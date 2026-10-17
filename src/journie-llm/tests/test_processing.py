"""Offline checks of processing invariants and the immutable annotation contract."""

from typing import Any

import pytest
from processing import (
    DIAGNOSTICS_VERSION,
    PROCESSING_VERSION,
    consistency_issues,
    derive_missing_fields,
    process_extraction,
)
from schema import Extraction
from test_cases import load_cases


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


def test_versions_and_all_gold_outputs() -> None:
    assert PROCESSING_VERSION == "missing-fields-v1"
    assert DIAGNOSTICS_VERSION == "consistency-v1"
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
    assert {issue["code"] for issue in issues} == {
        "budget_bound_requires_review",
        "conflict_metadata_unverifiable",
    }
    assert all(issue["severity"] == "review" for issue in issues)
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
