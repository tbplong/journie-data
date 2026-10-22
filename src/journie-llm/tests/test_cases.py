"""Offline structural checks for draft annotations, not a human review."""

import json
import re
from collections import Counter
from datetime import date, time
from pathlib import Path

import pytest
from benchmark import REJECTED, Case, summary_groups
from benchmark import load_cases as load_dataset
from schema import ANNOTATION_RULES, Extraction

V2_KEYS = {
    "id",
    "category",
    "user_input",
    "expected_output",
    "expected_constraints",
    "expected_missing_fields",
    "expected_conflicts",
}
V3_KEYS = V2_KEYS | {"user_part", "non_user_part", "traits"}
V3 = Path(__file__).resolve().parents[1] / "cases_v3.jsonl"


def load_cases(name="cases.jsonl"):
    return [
        json.loads(line)
        for line in (Path(__file__).resolve().parents[1] / name)
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def test_cases():
    cases = load_cases()
    assert len(cases) == 120
    assert Counter(case["category"] for case in cases) == dict.fromkeys(
        ("basic", "multiple", "missing", "conflicting", "noisy", "adversarial"), 20
    )
    assert len({case["id"] for case in cases}) == 120
    assert len({case["user_input"] for case in cases}) == 120
    for case in cases:
        assert set(case) == V2_KEYS
        assert not any(
            atom["field"] == "interests" for atom in case["expected_output"]["hard_constraints"]
        ), case["id"]
        check_gold(case)


def check_gold(case):
    """Contract checks shared by every dataset version."""
    assert isinstance(case["user_input"], str) and case["user_input"].strip()
    output = case["expected_output"]
    assert set(output) == set(Extraction.model_fields), case["id"]
    Extraction.model_validate(output)
    assert case["expected_constraints"] == {
        key: output[key] for key in ("hard_constraints", "soft_constraints")
    }, case["id"]
    assert case["expected_missing_fields"] == output["missing_fields"], case["id"]
    assert case["expected_conflicts"] == output["possible_conflicts"], case["id"]
    missing = []
    if output["destination"] is None:
        missing.append("destination")
    if all(output[key] is None for key in ("start_date", "end_date", "duration_days")):
        missing.append("travel_dates_or_duration")
    if output["budget_vnd"] is None:
        missing.append("budget")
    if output["available_start"] is None or output["available_end"] is None:
        missing.append("available_time")
    assert output["missing_fields"] == missing, case["id"]
    atoms = output["hard_constraints"] + output["soft_constraints"]
    assert len({tuple(sorted(atom.items())) for atom in atoms}) == len(atoms), case["id"]
    for key in (
        "destination",
        "start_date",
        "end_date",
        "duration_days",
        "budget_vnd",
        "available_start",
        "available_end",
    ):
        if output[key] is not None:
            operators = ("eq", "max") if key == "budget_vnd" else ("eq",)
            assert any(
                atom["field"] == key
                and atom["operator"] in operators
                and atom["value"] == str(output[key])
                for atom in atoms
            ), case["id"]
    # Interests are soft prefer, or hard include when the user states them as mandatory.
    assert set(output["interests"]) == {
        atom["value"]
        for atom in atoms
        if atom["field"] == "interests" and atom["operator"] in ("prefer", "include")
    }, case["id"]
    assert all(
        atom["operator"] == "include"
        for atom in output["hard_constraints"]
        if atom["field"] == "interests"
    ), case["id"]
    for key, operator in (
        ("must_visit", "include"),
        ("avoid", "exclude"),
        ("preferred_places", "prefer"),
    ):
        assert set(output[key]) == {
            atom["value"] for atom in atoms if atom["field"] == key and atom["operator"] == operator
        }, case["id"]
    assert all(atom["operator"] == "prefer" for atom in output["soft_constraints"])
    conflicts = output["possible_conflicts"]
    assert ("must_visit_avoid" in conflicts) == bool(
        set(output["must_visit"]) & set(output["avoid"])
    ), case["id"]
    minimums = [
        int(atom["value"])
        for atom in atoms
        if atom["field"] == "budget_vnd" and atom["operator"] == "min"
    ]
    caps = [
        int(atom["value"])
        for atom in atoms
        if atom["field"] == "budget_vnd" and atom["operator"] == "max"
    ]
    assert ("budget_disagreement" in conflicts) == any(
        minimum > cap for minimum in minimums for cap in caps
    ), case["id"]
    if (
        minimums
        and not caps
        and not any(atom["field"] == "budget_vnd" and atom["operator"] == "eq" for atom in atoms)
    ):
        assert output["budget_vnd"] is None, case["id"]
    if "budget_disagreement" in conflicts:
        assert output["budget_vnd"] == min(caps), case["id"]
    if "destination_disagreement" in conflicts:
        assert output["destination"] is None, case["id"]
        assert len({atom["value"] for atom in atoms if atom["field"] == "destination"}) > 1
    if output["start_date"] and output["end_date"]:
        days = (
            date.fromisoformat(output["end_date"]) - date.fromisoformat(output["start_date"])
        ).days + 1
        assert ("date_order" in conflicts) == (days < 1), case["id"]
        if output["duration_days"] is not None:
            assert ("duration_disagreement" in conflicts) == (output["duration_days"] != days), (
                case["id"]
            )
    if "time_order" in conflicts:
        assert output["available_end"] < output["available_start"], case["id"]
    if output["possible_conflicts"]:
        assert output["needs_clarification"], case["id"]
    for key in ("start_date", "end_date"):
        if output[key] is not None:
            date.fromisoformat(output[key])
    for key in ("available_start", "available_end"):
        if output[key] is not None:
            time.fromisoformat(output[key])


def test_v3_hierarchy():
    v2, v3 = load_cases(), load_cases("cases_v3.jsonl")
    assert len(v3) == 146
    assert len({case["id"] for case in v3}) == len({case["user_input"] for case in v3}) == 146
    # The 120 v2 cases keep id, order, and input. Gold is unchanged except for adversarial
    # cases, which v3 rejects outright: nothing extracted, no clarification requested.
    rejected = REJECTED.model_dump()
    for old, new in zip(v2, v3, strict=False):
        assert (old["id"], old["user_input"]) == (new["id"], new["user_input"])
        if old["category"] == "adversarial":
            assert new["expected_output"] == rejected, old["id"]
        else:
            assert {k: old[k] for k in V2_KEYS - {"category"}} == {
                k: new[k] for k in V2_KEYS - {"category"}
            }
        if old["category"] in ("basic", "multiple"):
            assert new["category"] == old["category"], old["id"]
        if old["category"] in ("noisy", "missing", "conflicting"):
            assert old["category"] in new["traits"], old["id"]
        assert (new["non_user_part"] == "adversarial") == (old["category"] == "adversarial")
    assert Counter(case["category"] for case in v3) == {
        "basic": 57,
        "multiple": 49,
        "adversarial": 20,
        "off_topic": 20,
    }
    for case in v3:
        assert set(case) == V3_KEYS, case["id"]
        check_gold(case)
        Case.model_validate(case)
    groups = {name: len(ids) for name, ids in summary_groups(load_dataset(V3)).items()}
    assert groups == {
        "adversarial": 20,
        "basic": 57,
        "multiple": 49,
        "off_topic": 20,
        "non_user_input": 40,
        "non_user_input:adversarial": 20,
        "non_user_input:off_topic": 20,
        "trait:conflicting": 23,
        "trait:missing": 24,
        "trait:noisy": 20,
        "user_input": 130,
        "user_input:basic": 75,
        "user_input:multiple": 55,
    }
    new_cases = {case["id"]: case for case in v3[120:]}
    for index in range(1, 11):
        gold = new_cases[f"offtopic-{index:02}"]["expected_output"]
        assert gold["missing_fields"] == [
            "destination",
            "travel_dates_or_duration",
            "budget",
            "available_time",
        ]
        assert not gold["hard_constraints"] and not gold["soft_constraints"]
        assert not gold["needs_clarification"]
    for index in range(11, 21):
        assert new_cases[f"offtopic-{index:02}"]["user_part"] is not None
    interests = {
        case_id: sorted(
            (kind, atom["value"])
            for kind in ("hard", "soft")
            for atom in new_cases[case_id]["expected_output"][f"{kind}_constraints"]
            if atom["field"] == "interests"
        )
        for case_id in new_cases
        if case_id.startswith("interest-")
    }
    assert interests == {
        "interest-01": [("hard", "museum")],
        "interest-02": [("soft", "museum")],
        "interest-03": [("hard", "beach"), ("soft", "cafe")],
        "interest-04": [("hard", "culture"), ("hard", "food")],
        "interest-05": [("soft", "park"), ("soft", "shopping")],
        "interest-06": [("hard", "museum"), ("soft", "shopping")],
    }


@pytest.mark.parametrize(
    "case_id,change,message",
    [
        # offtopic-11: "Đi Huế 2 ngày" (basic user part) plus an off-topic request.
        ("offtopic-11", {"category": "basic"}, "category must be"),
        ("offtopic-11", {"user_part": "multiple"}, "user_part must be basic"),
        ("offtopic-11", {"traits": ["conflicting"]}, "conflicting trait"),
        ("offtopic-11", {"user_part": None}, "gold requirements need a user_part"),
        # adversarial-15: labels describe the attack's travel text; gold must stay rejected.
        ("adversarial-15", {"category": "basic"}, "category must be"),
        ("adversarial-15", {"user_part": None}, "traits describe the user part"),
    ],
)
def test_v3_labels_are_validated(case_id, change, message):
    case = {**next(c for c in load_cases("cases_v3.jsonl") if c["id"] == case_id), **change}
    with pytest.raises(ValueError, match=message):
        Case.model_validate(case)


def test_v3_adversarial_gold_must_be_rejected():
    v2_gold = next(c for c in load_cases() if c["id"] == "adversarial-15")["expected_output"]
    case = next(c for c in load_cases("cases_v3.jsonl") if c["id"] == "adversarial-15")
    case = {
        **case,
        "expected_output": v2_gold,
        "expected_constraints": {
            key: v2_gold[key] for key in ("hard_constraints", "soft_constraints")
        },
        "expected_missing_fields": v2_gold["missing_fields"],
        "expected_conflicts": v2_gold["possible_conflicts"],
    }
    with pytest.raises(ValueError, match="adversarial gold must be"):
        Case.model_validate(case)


def test_v2_visit_and_currency_annotations():
    cases = {case["id"]: case for case in load_cases()}
    visits = {
        "basic-02": [("hard", "min", "Dinh Độc Lập:90")],
        "basic-18": [("hard", "eq", "Museum A:45")],
        "basic-19": [("soft", "prefer", "bảo tàng:120")],
        "multiple-08": [("hard", "min", "Museum A:90"), ("hard", "max", "Garden B:60")],
        "multiple-09": [("hard", "eq", "Gallery D:90")],
        "multiple-16": [("hard", "min", "Beach H:30")],
        "multiple-18": [("hard", "max", "Landmark 81:60"), ("hard", "eq", "Cafe N:120")],
        "missing-08": [("soft", "prefer", "Cafe N:30")],
        "missing-10": [("hard", "min", "Garden E:20")],
        "conflicting-02": [("hard", "eq", "Museum A:60")],
        "conflicting-06": [("hard", "max", "Beach H:45")],
        "noisy-01": [("hard", "min", "Dinh Độc Lập:90")],
        "noisy-03": [("soft", "prefer", "Museum A:60")],
        "adversarial-02": [("hard", "eq", "Museum A:60")],
    }
    for case_id, case in cases.items():
        gold = case["expected_output"]
        actual = [
            (kind, atom["operator"], atom["value"])
            for kind in ("hard", "soft")
            for atom in gold[f"{kind}_constraints"]
            if atom["field"] == "visit_duration_min"
        ]
        assert sorted(actual) == sorted(visits.get(case_id, [])), case_id
    nominal = cases["basic-19"]["expected_output"]
    assert not nominal["needs_clarification"]
    assert nominal["duration_days"] is None
    assert (
        not nominal["interests"] and not nominal["must_visit"] and not nominal["preferred_places"]
    )
    assert not cases["basic-02"]["expected_output"]["must_visit"]

    foreign = {
        "basic-15": "Ngân sách đúng 100 USD",
        "multiple-08": "budget exactly 200 SGD",
        "multiple-18": "budget exactly 100 EUR",
        "missing-08": "budget exactly 100 GBP",
        "missing-10": "budget exactly 10000 JPY",
        "conflicting-06": "budget exactly 100 AUD",
        "noisy-03": "ngân sách ngoại tệ riêng đúng 100 USD",
        "adversarial-06": "budget exactly 100 CAD",
    }
    for case_id, wording in foreign.items():
        gold = cases[case_id]["expected_output"]
        assert {"field": "other", "operator": "eq", "value": wording} in gold["hard_constraints"]
        assert gold["budget_scope"] is None
        if case_id == "noisy-03":
            assert gold["budget_vnd"] == 500000
            assert "budget" not in gold["missing_fields"]
        else:
            assert gold["budget_vnd"] is None and "budget" in gold["missing_fields"]
            assert not any(atom["field"] == "budget_vnd" for atom in gold["hard_constraints"])
        assert gold["needs_clarification"] == (
            case_id
            in {
                "missing-08",
                "missing-10",
                "conflicting-06",
            }
        )


def test_v2_ambiguity_overnight_and_reference_date():
    cases = {case["id"]: case["expected_output"] for case in load_cases()}
    for case_id in ("missing-06", "missing-07", "missing-08", "noisy-17", "adversarial-20"):
        assert cases[case_id]["budget_vnd"] is None, case_id
        assert cases[case_id]["needs_clarification"], case_id
    for case_id in ("missing-09", "missing-10", "missing-11", "noisy-16", "adversarial-12"):
        assert cases[case_id]["available_start"] is None, case_id
        assert cases[case_id]["available_end"] is None, case_id
        assert cases[case_id]["needs_clarification"], case_id
    overnight = cases["multiple-17"]
    assert (overnight["available_start"], overnight["available_end"]) == ("22:00", "02:00")
    assert {"field": "other", "operator": "eq", "value": "rảnh 22:00 đến 02:00 hôm sau"} in (
        overnight["hard_constraints"]
    )
    assert not overnight["possible_conflicts"] and not overnight["needs_clarification"]
    assert all(overnight[key] is None for key in ("start_date", "end_date", "duration_days"))
    for case_id in ("conflicting-04", "conflicting-05", "conflicting-06", "conflicting-19"):
        assert "time_order" in cases[case_id]["possible_conflicts"], case_id
        assert cases[case_id]["needs_clarification"], case_id
    assert "Reference date: 2026-10-03. Timezone: Asia/Ho_Chi_Minh." in ANNOTATION_RULES
    assert cases["basic-14"]["start_date"] == "2026-10-04"
    assert cases["conflicting-02"]["start_date"] == "2026-10-04"
    assert cases["conflicting-02"]["end_date"] == "2026-10-02"
    assert cases["multiple-02"]["duration_days"] is None
    assert cases["multiple-12"]["duration_days"] == 3
    assert cases["conflicting-07"]["duration_days"] == 2
    assert "duration_disagreement" in cases["conflicting-07"]["possible_conflicts"]


def coverage():
    """Count gold facts, not currency mentions in rejected examples or injections."""
    cases = load_cases()
    outputs = [case["expected_output"] for case in cases]
    counts = {
        key: sum(output[key] not in (None, []) for output in outputs)
        for key in (
            "destination",
            "start_date",
            "end_date",
            "duration_days",
            "budget_vnd",
            "available_start",
            "available_end",
            "interests",
            "must_visit",
            "preferred_places",
            "avoid",
        )
    }
    atoms = [output["hard_constraints"] + output["soft_constraints"] for output in outputs]
    visits = [atom for group in atoms for atom in group if atom["field"] == "visit_duration_min"]
    currency = re.compile(r"\b(?:USD|EUR|GBP|JPY|AUD|CAD|SGD)\b")
    foreign = [
        any(atom["field"] == "other" and currency.search(atom["value"]) for atom in group)
        for group in atoms
    ]
    return {
        "cases": len(cases),
        "categories": dict(Counter(case["category"] for case in cases)),
        **counts,
        "either_date": sum(
            o["start_date"] is not None or o["end_date"] is not None for o in outputs
        ),
        "both_dates": sum(
            o["start_date"] is not None and o["end_date"] is not None for o in outputs
        ),
        "both_available_endpoints": sum(
            o["available_start"] is not None and o["available_end"] is not None for o in outputs
        ),
        "visit_duration_cases": sum(
            any(a["field"] == "visit_duration_min" for a in g) for g in atoms
        ),
        "visit_duration_atoms": len(visits),
        "visit_duration_operators": dict(Counter(atom["operator"] for atom in visits)),
        "foreign_currency_cases": sum(foreign),
        "foreign_currency_ids": [
            case["id"] for case, present in zip(cases, foreign, strict=True) if present
        ],
        "foreign_currency_codes": dict(
            Counter(
                code
                for group in atoms
                for atom in group
                if atom["field"] == "other"
                for code in currency.findall(atom["value"])
            )
        ),
        "foreign_currency_mention_cases": sum(
            bool(currency.search(c["user_input"])) for c in cases
        ),
        "clarification_cases": sum(o["needs_clarification"] for o in outputs),
        "contradiction_cases": sum(bool(o["possible_conflicts"]) for o in outputs),
        "conflict_codes": dict(Counter(code for o in outputs for code in o["possible_conflicts"])),
    }


if __name__ == "__main__":
    test_cases()
    test_v2_visit_and_currency_annotations()
    test_v2_ambiguity_overnight_and_reference_date()
    test_v3_hierarchy()
    print("Validated dataset v2 (120 cases) and v3 (146 cases); all annotation checks passed.")
    print(json.dumps(coverage(), ensure_ascii=False, indent=2))
