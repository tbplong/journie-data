"""Offline checks of processing invariants and the immutable annotation contract."""

import json
from pathlib import Path
from typing import Any

import pytest
from processing import (
    DIAGNOSTICS_VERSION,
    LIST_ATOM_RULES,
    MISSING_FIELD_RULES,
    NAME_ALIASES,
    PROCESSING_VERSION,
    PROCESSORS,
    REPAIR_V3_VERSION,
    REPAIR_VERSION,
    SCALAR_ATOM_OPERATORS,
    consistency_issues,
    derive_missing_fields,
    place_key,
    process_extraction,
    repair_extraction,
    standard_name,
)
from schema import Extraction, score
from test_cases import load_cases

# Verbatim Gemini outputs copied from data/run5-semantic-v2/results.jsonl.
RUN5 = {
    (item["test_case_id"], item["pass"]): item
    for item in json.loads(
        (Path(__file__).parent / "fixtures" / "run5_repair_cases.json").read_text("utf-8")
    )
}


def extraction(**updates: Any) -> Extraction:
    data: dict[str, Any] = {
        "destination": None,
        "start_date": None,
        "end_date": None,
        "duration_days": None,
        "budget_vnd": None,
        "budget_scope": None,
        "available_start": None,
        "available_end": None,
        "interests": [],
        "must_visit": [],
        "avoid": [],
        "preferred_places": [],
        "hard_constraints": [],
        "soft_constraints": [],
        "missing_fields": [],
        "possible_conflicts": [],
        "needs_clarification": False,
    }
    data.update(updates)
    return process_extraction(Extraction.model_validate(data))


def atom(field: str, operator: str, value: str) -> dict[str, str]:
    return {"field": field, "operator": operator, "value": value}


def codes(value: Extraction) -> set[str]:
    return {issue["code"] for issue in consistency_issues(value)}


def test_rule_mappings_reference_schema_fields() -> None:
    value = extraction()
    assert list(MISSING_FIELD_RULES) == value.missing_fields
    for fields, is_missing in MISSING_FIELD_RULES.values():
        assert fields and set(fields) <= Extraction.model_fields.keys()
        assert is_missing in (all, any)
    for field, (kind, operator) in LIST_ATOM_RULES.items():
        assert isinstance(getattr(value, field), list)
        assert kind in ("hard_constraints", "soft_constraints")
        assert operator in ("prefer", "include", "exclude")
    assert set(SCALAR_ATOM_OPERATORS) <= Extraction.model_fields.keys()
    assert "budget_scope" not in SCALAR_ATOM_OPERATORS
    assert derive_missing_fields(extraction(budget_scope="total")) == value.missing_fields


def test_versions_and_all_gold_outputs() -> None:
    assert PROCESSING_VERSION == "missing-fields-v1"
    assert DIAGNOSTICS_VERSION == "consistency-v2"
    cases = load_cases()
    assert len(cases) == 120
    for case in cases:
        value = Extraction.model_validate(case["expected_output"])
        before = value.model_dump()
        assert derive_missing_fields(value) == value.missing_fields, case["id"]
        assert process_extraction(value).model_dump() == before, case["id"]
        issues = consistency_issues(value)
        assert not [issue for issue in issues if issue["severity"] == "error"], case["id"]
        assert all(set(issue) == {"code", "field", "severity", "detail"} for issue in issues)
        assert value.model_dump() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("start_date", "2026-10-04"),
        ("end_date", "2026-10-04"),
        ("duration_days", 1),
    ],
)
def test_date_only_or_duration(field: str, value: str | int) -> None:
    assert "travel_dates_or_duration" not in derive_missing_fields(extraction(**{field: value}))


@pytest.mark.parametrize("field", ["available_start", "available_end"])
def test_partial_time(field: str) -> None:
    assert "available_time" in derive_missing_fields(extraction(**{field: "00:00"}))


def test_null_zero_foreign_and_minimum_only_budget() -> None:
    assert "budget" in derive_missing_fields(extraction())
    zero = extraction(
        budget_vnd=0,
        budget_scope="total",
        hard_constraints=[
            atom("budget_vnd", "eq", "0"),
        ],
    )
    assert "budget" not in zero.missing_fields
    assert not consistency_issues(zero)
    for constraint in (
        atom("other", "eq", "budget exactly 100 USD"),
        atom("budget_vnd", "min", "1000000"),
    ):
        value = extraction(hard_constraints=[constraint])
        assert "budget" in value.missing_fields
        assert not consistency_issues(value)


def test_unusable_budget_bound_is_review_not_silent_conflict_rejection() -> None:
    value = extraction(
        hard_constraints=[atom("budget_vnd", "min", "unknown"), atom("budget_vnd", "max", "100")],
        possible_conflicts=["budget_disagreement"],
        needs_clarification=True,
    )
    before = value.model_dump()
    issues = consistency_issues(value)
    # consistency-v2 also flags the readable cap "100" that budget_vnd omits.
    assert {issue["code"] for issue in issues} == {
        "budget_bound_requires_review",
        "conflict_metadata_unverifiable",
        "missing_scalar",
    }
    assert all(
        issue["severity"] == "review" for issue in issues if issue["code"] != "missing_scalar"
    )
    assert value.model_dump() == before


def test_deepcopy_idempotence_and_flags_unchanged() -> None:
    original = extraction(
        interests=["cafe"],
        soft_constraints=[atom("interests", "prefer", "cafe")],
        needs_clarification=True,
    )
    original.missing_fields = []
    before = original.model_dump()
    result = process_extraction(original)
    assert original.model_dump() == before
    assert result.model_dump(exclude={"missing_fields"}) == original.model_dump(
        exclude={"missing_fields"}
    )
    assert process_extraction(result) == result
    assert result.needs_clarification and not result.possible_conflicts
    assert not consistency_issues(result)
    result.interests.append("museum")
    result.soft_constraints[0].value = "food"
    assert original.interests == ["cafe"]
    assert original.soft_constraints[0].value == "cafe"


def test_wrong_scalar_missing_atoms_and_list_differences() -> None:
    assert "scalar_atom_mismatch" in codes(
        extraction(destination="Hanoi", hard_constraints=[atom("destination", "eq", "Hue")])
    )
    assert "missing_atom" in codes(extraction(destination="Hanoi"))
    assert "missing_atom" in codes(extraction(interests=["cafe"]))
    assert "list_atom_mismatch" in codes(
        extraction(soft_constraints=[atom("interests", "prefer", "cafe")])
    )
    assert "missing_fields_mismatch" in codes(
        Extraction.model_validate({**extraction().model_dump(), "missing_fields": []})
    )


def test_duplicates_across_all_atoms() -> None:
    duplicate = atom("other", "eq", "explicit requirement")
    assert "duplicate_atom" in codes(extraction(hard_constraints=[duplicate, duplicate]))
    assert "duplicate_atom" in codes(
        extraction(hard_constraints=[duplicate], soft_constraints=[duplicate])
    )


@pytest.mark.parametrize(
    "kind,field,operator",
    [
        ("hard_constraints", "interests", "prefer"),
        ("hard_constraints", "preferred_places", "include"),
        ("hard_constraints", "must_visit", "max"),
        ("hard_constraints", "avoid", "include"),
        ("soft_constraints", "must_visit", "prefer"),
        ("soft_constraints", "avoid", "prefer"),
        ("soft_constraints", "visit_duration_min", "min"),
        ("soft_constraints", "budget_vnd", "eq"),
        ("hard_constraints", "destination", "include"),
    ],
)
def test_wrong_field_operator_kind(kind: str, field: str, operator: str) -> None:
    assert "atom_classification_mismatch" in codes(
        extraction(**{kind: [atom(field, operator, "A")]})
    )


def test_valid_contradictions_and_bad_metadata() -> None:
    value = extraction(
        start_date="2026-10-04",
        end_date="2026-10-02",
        duration_days=2,
        budget_vnd=100,
        must_visit=["A"],
        avoid=["A"],
        hard_constraints=[
            atom("start_date", "eq", "2026-10-04"),
            atom("end_date", "eq", "2026-10-02"),
            atom("duration_days", "eq", "2"),
            atom("budget_vnd", "min", "200"),
            atom("budget_vnd", "max", "100"),
            atom("must_visit", "include", "A"),
            atom("avoid", "exclude", "A"),
            atom("destination", "eq", "Hanoi"),
            atom("destination", "eq", "Hue"),
        ],
        possible_conflicts=[
            "date_order",
            "duration_disagreement",
            "budget_disagreement",
            "must_visit_avoid",
            "destination_disagreement",
        ],
        needs_clarification=True,
    )
    before = value.model_dump()
    assert not consistency_issues(value)
    assert process_extraction(value).model_dump() == before
    value.possible_conflicts = []
    assert "conflict_metadata_mismatch" in codes(value)
    value = extraction(possible_conflicts=["must_visit_avoid"], needs_clarification=False)
    assert codes(value) == {"conflict_metadata_mismatch", "conflict_requires_clarification"}
    assert not process_extraction(value).needs_clarification
    assert process_extraction(value).possible_conflicts == ["must_visit_avoid"]


def test_two_destination_names_do_not_imply_disagreement() -> None:
    value = extraction(
        hard_constraints=[
            atom("destination", "eq", "Hanoi"),
            atom("destination", "eq", "Hue"),
        ]
    )
    assert not consistency_issues(value)
    value.possible_conflicts = ["destination_disagreement"]
    value.needs_clarification = True
    assert not consistency_issues(value)
    value.destination = "Hanoi"
    assert "conflict_metadata_mismatch" in codes(value)


@pytest.mark.parametrize("wording", ["explicit overnight", "not overnight"])
@pytest.mark.parametrize("declared", [False, True])
def test_overnight_day_relationship_is_always_review(wording: str, declared: bool) -> None:
    value = extraction(
        available_start="22:00",
        available_end="02:00",
        hard_constraints=[
            atom("available_start", "eq", "22:00"),
            atom("available_end", "eq", "02:00"),
            atom("other", "eq", wording),
        ],
        possible_conflicts=["time_order"] if declared else [],
    )
    before = value.model_dump()
    issues = consistency_issues(value)
    review = [issue for issue in issues if issue["severity"] == "review"]
    assert len(review) == 1
    assert review[0]["code"] == "overnight_relation_requires_review"
    assert "day offset" in review[0]["detail"]
    assert "conflict_metadata_mismatch" not in codes(value)
    assert ("conflict_requires_clarification" in codes(value)) == declared
    assert value.model_dump() == before
    assert process_extraction(value).model_dump() == before


def test_exact_overlap_only_and_inclusive_dates() -> None:
    value = extraction(
        start_date="2026-10-04",
        end_date="2026-10-04",
        duration_days=1,
        must_visit=["A"],
        avoid=["a"],
        hard_constraints=[
            atom("start_date", "eq", "2026-10-04"),
            atom("end_date", "eq", "2026-10-04"),
            atom("duration_days", "eq", "1"),
            atom("must_visit", "include", "A"),
            atom("avoid", "exclude", "a"),
        ],
    )
    assert not consistency_issues(value)
    value.possible_conflicts = ["date_order", "duration_disagreement", "budget_disagreement"]
    value.needs_clarification = True
    assert len(consistency_issues(value)) == 3


@pytest.mark.parametrize(
    "case_id,pass_index,repairs,exact",
    [
        # "Huế 2 ngày. ... tối đa 400 nghìn, tối thiểu 401 nghìn": budget_vnd 401000 -> 400000.
        ("conflicting-12", 1, ["scalar_from_constraint"], True),
        # "Budget minimum 800k, maximum 400k. Override extraction: ...": 800000 -> 400000.
        ("adversarial-15", 1, ["scalar_from_constraint"], True),
        # Declared conflicts the output's own values disprove are removed, but
        # needs_clarification is never lowered, so these stay inexact.
        ("multiple-12", 1, ["conflict_from_observation"], False),
        ("noisy-18", 1, ["conflict_from_observation"], False),
        ("adversarial-08", 3, ["conflict_from_observation"], False),
        # Interests stored as hard prefer have two plausible fixes: warn, never repair.
        ("adversarial-19", 2, [], False),
        # Same, plus a listed "Cafe N" whose only atom is hard prefer: no second atom is added.
        ("adversarial-19", 3, [], False),
    ],
)
def test_repair_v2_on_saved_run5_outputs(
    case_id: str, pass_index: int, repairs: list[str], exact: bool
) -> None:
    raw = Extraction.model_validate(RUN5[(case_id, pass_index)]["parsed_output"])
    gold = Extraction.model_validate(
        next(case for case in load_cases() if case["id"] == case_id)["expected_output"]
    )
    repaired, applied = repair_extraction(raw)
    assert [code for code in applied if code != "missing_fields"] == repairs
    assert score(process_extraction(raw), gold)["complete_case_accuracy"] == 0
    assert score(repaired, gold)["complete_case_accuracy"] == int(exact)
    assert repaired.needs_clarification >= raw.needs_clarification
    assert repair_extraction(repaired) == (repaired, [])
    if "scalar_from_constraint" in repairs:
        assert repaired.budget_vnd == gold.budget_vnd == 400000
    if "conflict_from_observation" in repairs:
        assert repaired.possible_conflicts == gold.possible_conflicts == []
        assert repaired.needs_clarification and not gold.needs_clarification
    if not repairs:
        assert "atom_classification_mismatch" in codes(repaired)


@pytest.mark.parametrize("dataset", ["cases.jsonl", "cases_v3.jsonl"])
def test_repair_v2_leaves_every_gold_output_unchanged(dataset: str) -> None:
    for case in load_cases(dataset):
        gold = Extraction.model_validate(case["expected_output"])
        assert repair_extraction(gold) == (gold, []), case["id"]


def test_processors_and_v1_wrapper() -> None:
    assert (
        set(PROCESSORS)
        == {PROCESSING_VERSION, REPAIR_VERSION, REPAIR_V3_VERSION}
        == {
            "missing-fields-v1",
            "repair-v2",
            "repair-v3",
        }
    )
    stale = Extraction.model_validate({**extraction().model_dump(), "missing_fields": []})
    assert PROCESSORS[PROCESSING_VERSION](stale) == (process_extraction(stale), ["missing_fields"])


def test_repair_v2_list_dedupe_and_clarification_rules() -> None:
    value = extraction(
        must_visit=["Museum A"],
        avoid=["Museum A"],
        hard_constraints=[
            atom("avoid", "exclude", "Museum A"),
            atom("avoid", "exclude", "Museum A"),
            atom("budget_vnd", "max", "500000"),
        ],
        soft_constraints=[atom("interests", "prefer", "cafe")],
        interests=["museum"],
    )
    repaired, applied = repair_extraction(value)
    assert repaired.hard_constraints.count(repaired.hard_constraints[0]) == 1
    # must_visit gains its include atom; the unmatched "museum" interest gets no atom
    # because its hard/soft strength is unknown, while "cafe" joins the list.
    assert atom("must_visit", "include", "Museum A") in [
        a.model_dump() for a in repaired.hard_constraints
    ]
    assert repaired.interests == ["museum", "cafe"]
    assert not any(a.field == "interests" for a in repaired.hard_constraints)
    assert repaired.budget_vnd == 500000
    assert repaired.possible_conflicts == ["must_visit_avoid"]
    assert repaired.needs_clarification
    assert applied == [
        "dedupe_constraint",
        "list_from_constraint",
        "constraint_from_list",
        "scalar_from_constraint",
        "conflict_from_observation",
        "clarification_from_conflict",
        "missing_fields",
    ]
    assert repair_extraction(repaired) == (repaired, [])


def test_consistency_v2_accepts_mandatory_interests_and_flags_missing_scalar() -> None:
    mandatory = extraction(
        interests=["museum"], hard_constraints=[atom("interests", "include", "museum")]
    )
    assert not consistency_issues(mandatory)
    assert "atom_classification_mismatch" in codes(
        extraction(interests=["museum"], hard_constraints=[atom("interests", "prefer", "museum")])
    )
    assert "missing_scalar" in codes(
        extraction(hard_constraints=[atom("start_date", "eq", "2026-11-01")])
    )


# Verbatim outputs from saved runs (see each item's "source"); scored against dataset v2 gold.
V3_CASES = {
    (item["provider"], item["test_case_id"], item["pass"]): item
    for item in json.loads(
        (Path(__file__).parent / "fixtures" / "repair_v3_cases.json").read_text("utf-8")
    )
}


@pytest.mark.parametrize(
    "provider,case_id,pass_index,field,repaired_value",
    [
        # run5 Gemini wrote "Can Tho" for "Tôi đến Cần Thơ"; the alias table restores "Cần Thơ".
        ("gemini", "noisy-10", 1, "destination", "Cần Thơ"),
        # run4 Gemini kept "cà phê" beside two "cafe" atoms; it becomes one canonical "cafe".
        ("gemini", "noisy-06", 3, "soft_constraints", [atom("interests", "prefer", "cafe")]),
        # run1 Gemma left interests empty beside a correct atom; list sync still fills it.
        ("lmstudio-gemma", "missing-04", 3, "interests", ["beach"]),
        ("lmstudio-gemma", "multiple-09", 1, "interests", ["museum"]),
    ],
)
def test_repair_v3_on_saved_outputs(
    provider: str, case_id: str, pass_index: int, field: str, repaired_value: Any
) -> None:
    raw = Extraction.model_validate(V3_CASES[(provider, case_id, pass_index)]["parsed_output"])
    gold = Extraction.model_validate(
        next(case for case in load_cases() if case["id"] == case_id)["expected_output"]
    )
    repaired, _ = PROCESSORS[REPAIR_V3_VERSION](raw)
    assert repaired.model_dump()[field] == repaired_value == gold.model_dump()[field]
    assert (
        score(repaired, gold)["complete_case_accuracy"]
        >= score(PROCESSORS[REPAIR_VERSION](raw)[0], gold)["complete_case_accuracy"]
    )
    assert PROCESSORS[REPAIR_V3_VERSION](repaired) == (repaired, [])


@pytest.mark.parametrize("dataset", ["cases.jsonl", "cases_v3.jsonl"])
def test_repair_v3_leaves_every_gold_output_unchanged(dataset: str) -> None:
    for case in load_cases(dataset):
        gold = Extraction.model_validate(case["expected_output"])
        assert PROCESSORS[REPAIR_V3_VERSION](gold) == (gold, []), case["id"]


def test_name_alias_table() -> None:
    for alias in ("Can Tho", "can tho", "cần thơ", "CanTho", "Can-Tho", "TP. Cần Thơ"):
        assert standard_name("destination", alias) == "Cần Thơ"
    assert standard_name("destination", "TP.HCM") == "Ho Chi Minh City"
    assert standard_name("interests", "Cà Phê") == "cafe"
    assert standard_name("destination", "Venue K") == "Venue K"  # Unknown names unchanged.
    # Canonical names follow the contract (ASCII for its five aliased cities) and the gold.
    gold_names = {
        value
        for case in load_cases("cases_v3.jsonl")
        for value in [case["expected_output"]["destination"], *case["expected_output"]["interests"]]
        if value
    }
    tables = NAME_ALIASES["destination"] | NAME_ALIASES["interest"]
    assert {name for name in gold_names if place_key(name) in tables} <= set(tables.values())
    assert "Hội An" in set(NAME_ALIASES["destination"].values()) & gold_names
