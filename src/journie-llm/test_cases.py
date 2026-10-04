"""Offline structural checks for draft annotations, not a human review."""

import json
import re
from collections import Counter
from datetime import date, time
from pathlib import Path

from schema import ANNOTATION_RULES, Extraction


def load_cases():
    return [
        json.loads(line)
        for line in Path(__file__).with_name("cases.jsonl").read_text(encoding="utf-8").splitlines()
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
        assert set(case) == {
            "id",
            "category",
            "user_input",
            "expected_output",
            "expected_constraints",
            "expected_missing_fields",
            "expected_conflicts",
        }
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
        for key, operator in (
            ("interests", "prefer"),
            ("must_visit", "include"),
            ("avoid", "exclude"),
            ("preferred_places", "prefer"),
        ):
            assert set(output[key]) == {
                atom["value"]
                for atom in atoms
                if atom["field"] == key and atom["operator"] == operator
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
            and not any(
                atom["field"] == "budget_vnd" and atom["operator"] == "eq" for atom in atoms
            )
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
                assert ("duration_disagreement" in conflicts) == (
                    output["duration_days"] != days
                ), case["id"]
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
    print("Validated dataset v2: 120 draft cases, 20 per category; all annotation checks passed.")
    print(json.dumps(coverage(), ensure_ascii=False, indent=2))
