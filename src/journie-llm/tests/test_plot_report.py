"""Check report stages and replay integrity without plotting or model requests."""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from benchmark import summarize
from plot_report import OUTCOMES, aggregate, captions
from plot_report import load_run as load_report
from processing import PROCESSING_VERSION, PROCESSORS, process_extraction
from schema import Extraction, score

# Dataset v2 category order, as in cases.jsonl.
CATEGORIES = ("basic", "multiple", "missing", "conflicting", "noisy", "adversarial")


def test_report_outcomes_and_denominators():
    rows = []
    for i in range(5):
        rows.append(
            {
                "test_case_id": f"case-{i}",
                "failure": "empty_response" if i == 0 else None,
                "json_parse_valid": i >= 2,
                "schema_valid": i >= 3,
                "latency_seconds": float(i + 1),
                "metrics": {
                    "complete_case_accuracy": int(i == 4),
                    "hallucinated_case": int(i == 3),
                    "populated_field_correct": int(i == 4),
                    "populated_field_total": 1,
                    "empty_field_correct": int(i >= 3),
                    "empty_field_total": 2,
                    "missing_fields": {"tp": int(i == 4), "fp": 0, "fn": int(i != 4)},
                },
            }
        )
    stats = aggregate(rows)
    assert stats["outcomes"] == dict.fromkeys(OUTCOMES, 1)
    assert sum(stats["outcomes"].values()) == stats["requests"] == 5
    assert stats["schema_valid_rate"] == 2 / 5
    assert stats["complete_case_accuracy"] == 1 / 5
    assert stats["hallucination_rate"] == 1 / 2
    assert stats["p50_seconds"] == 3 and stats["p95_seconds"] == 4.8
    assert stats["populated_field_accuracy"] == 1 / 5
    assert stats["empty_field_accuracy"] == 2 / 10
    assert aggregate(rows[:3])["hallucination_rate"] is None


def fixture_gold() -> Extraction:
    return Extraction.model_validate(
        {
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
            "missing_fields": [
                "destination",
                "travel_dates_or_duration",
                "budget",
                "available_time",
            ],
            "possible_conflicts": [],
            "needs_clarification": False,
        }
    )


def load_run(run: Path, processed_run: Path | None = None) -> dict:
    return load_report(run, processed_run, cases=run.parent / "cases.jsonl")


def fixture_rows() -> list[dict[str, Any]]:
    gold = fixture_gold()
    rows = []
    for index, category in enumerate(CATEGORIES):
        actual = (
            None
            if category == "adversarial"
            else gold
            if category == "basic"
            else gold.model_copy(deep=True, update={"missing_fields": []})
        )
        processed = process_extraction(actual) if actual is not None else None
        rows.append(
            {
                "provider": "synthetic",
                "model": "offline-test-model",
                "resolved_model": "offline-test-model-v1",
                "test_case_id": f"case-{category}",
                "category": category,
                "pass": 1,
                "warmup": False,
                "failure": None,
                "json_parse_valid": True,
                "schema_valid": actual is not None,
                "parsed_output": actual.model_dump() if actual is not None else {},
                "raw_response": actual.model_dump_json() if actual is not None else "{}",
                "latency_seconds": float(index + 1),
                "http_status": 200,
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "reasoning_tokens": None,
                "metrics": score(actual, gold),
                "processed_output": processed.model_dump() if processed is not None else None,
                "processed_metrics": score(processed, gold),
            }
        )
    return rows


def write_run(
    run: Path,
    rows: list[dict[str, Any]],
    *,
    inline: bool = False,
    source: Path | None = None,
    traits: dict[str, list[str]] | None = None,
    processing: str = PROCESSING_VERSION,
) -> None:
    run.mkdir()
    cases = run.parent / "cases.jsonl"
    cases.write_text(
        "".join(
            json.dumps(
                {
                    "id": f"case-{category}",
                    "category": category,
                    **({"traits": traits.get(f"case-{category}", [])} if traits else {}),
                    "expected_output": fixture_gold().model_dump(),
                }
            )
            + "\n"
            for category in CATEGORIES
        ),
        encoding="utf-8",
    )
    manifest: dict[str, Any] = {
        "complete": True,
        "case_count": 6,
        "passes": 1,
        "measured_requests": 6,
        "providers": [{"name": "synthetic", "model": "offline-test-model", "pacing_seconds": 0}],
        "cases_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(b"synthetic-schema").hexdigest(),
        "prompt_sha256": hashlib.sha256(b"synthetic-prompt").hexdigest(),
        "prompt_variant": "synthetic",
    }
    if inline or source is not None:
        manifest["processing_version"] = processing
    if source is not None:
        for name in ("manifest.json", "results.jsonl", "summary.json"):
            stem = name.removesuffix(".jsonl").removesuffix(".json")
            manifest[f"source_{stem}_sha256"] = hashlib.sha256(
                (source / name).read_bytes()
            ).hexdigest()
    saved = copy.deepcopy(rows)
    if not inline and source is None:
        for row in saved:
            del row["processed_output"], row["processed_metrics"]
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in saved), encoding="utf-8"
    )
    for key, filename in (
        ("metrics", "summary.json"),
        ("processed_metrics", "processed_summary.json"),
    ):
        if key == "processed_metrics" and not (inline or source is not None):
            continue
        summaries = []
        trait_groups = sorted({f"trait:{t}" for values in (traits or {}).values() for t in values})
        for category in ("overall", *CATEGORIES, *trait_groups):
            selected = [
                {**row, "metrics": row[key]}
                for row in rows
                if category in ("overall", row["category"])
                or (
                    category.startswith("trait:")
                    and category.removeprefix("trait:")
                    in (traits or {}).get(row["test_case_id"], [])
                )
            ]
            summaries.append(
                {
                    "provider": "synthetic",
                    "model": "offline-test-model",
                    "category": category,
                    **summarize(selected),
                }
            )
        (run / filename).write_text(json.dumps(summaries), encoding="utf-8")


def test_stage_aggregate_changes_only_exact_and_missing_list_scores() -> None:
    rows = fixture_rows()
    raw, processed = aggregate(rows), aggregate(rows, "processed_metrics")
    assert raw["complete_case_accuracy"] == raw["missing_fields_exact_accuracy"] == 1 / 6
    assert (
        processed["complete_case_accuracy"] == processed["missing_fields_exact_accuracy"] == 5 / 6
    )
    assert raw["missing_fields_correct_count"] == 1
    assert processed["missing_fields_correct_count"] == 5
    assert raw["missing_fields_f1"] == 2 / 7
    assert processed["missing_fields_f1"] == 10 / 11
    changed = {key for key in raw if raw[key] != processed[key]}
    assert changed == {
        "outcomes",
        "complete_case_accuracy",
        "missing_fields_correct_count",
        "missing_fields_exact_accuracy",
        "missing_fields_f1",
    }


def test_invalid_output_gets_no_exact_missing_list_credit_even_with_empty_gold() -> None:
    row = fixture_rows()[-1]
    gold = Extraction.model_validate(fixture_rows()[0]["parsed_output"]).model_copy(
        update={"missing_fields": []}
    )
    row["metrics"] = row["processed_metrics"] = score(None, gold)
    assert row["metrics"]["missing_fields"] == {"tp": 0, "fp": 0, "fn": 0}
    for key in ("metrics", "processed_metrics"):
        stats = aggregate([row], key)
        assert stats["missing_fields_correct_count"] == 0
        assert stats["missing_fields_exact_accuracy"] == stats["complete_case_accuracy"] == 0
        assert stats["outcomes"]["Invalid schema"] == 1


@pytest.mark.parametrize("inline", [False, True])
def test_load_run_separates_raw_and_inline_stages(tmp_path: Path, inline: bool) -> None:
    run = tmp_path / "run"
    write_run(run, fixture_rows(), inline=inline)
    data = load_run(run)
    model = data["models"][0]
    assert model["overall"] == aggregate(fixture_rows())
    assert set(model["categories"]) == set(CATEGORIES)
    assert all(stat["requests"] == 1 for stat in model["categories"].values())
    assert data["warnings"] == []
    if inline:
        assert model["processed_overall"] == aggregate(fixture_rows(), "processed_metrics")
        assert set(model["processed_categories"]) == set(CATEGORIES)
        assert data["processing_origin"] == "recorded inline"
        assert "processed_summary.json" in data["sources_sha256"]
    else:
        assert model["processed_overall"] is model["processed_categories"] is None
        assert data["processing_origin"] is data["processing_version"] is None
        assert "unavailable, not zero" in captions(data)
        assert "| synthetic | 16.7% | N/A | N/A | 16.7% -> N/A |" in captions(data)
        assert "Paired rows" not in captions(data)
        assert "The second panel" not in captions(data)


def test_matching_gold_is_required(tmp_path: Path) -> None:
    run = tmp_path / "run"
    write_run(run, fixture_rows(), inline=True)
    with pytest.raises(ValueError, match="No dataset file matches"):
        load_report(run)
    with pytest.raises(ValueError, match="Gold case-file hash differs"):
        load_report(run, cases=Path(__file__).resolve().parents[1] / "cases_v3.jsonl")


@pytest.mark.parametrize(
    "field,value", [("json_parse_valid", False), ("failure", "empty_response")]
)
def test_schema_valid_rows_require_parsed_json_and_no_failure(tmp_path: Path, field, value) -> None:
    run = tmp_path / "run"
    rows = fixture_rows()
    rows[1][field] = value
    write_run(run, rows, inline=True)
    with pytest.raises(ValueError, match="failed scoring validation"):
        load_run(run)


def test_pooled_f1_cannot_hide_corrupted_per_row_missing_scores(tmp_path: Path) -> None:
    run = tmp_path / "run"
    rows = fixture_rows()
    rows[0]["processed_metrics"]["missing_fields"]["tp"] -= 1
    rows[1]["processed_metrics"]["missing_fields"]["tp"] += 1
    # Pooled F1 and exact rates stay unchanged, but individual item counts no longer match gold.
    write_run(run, rows, inline=True)
    with pytest.raises(ValueError, match="Processed row metrics differ from gold rescoring"):
        load_run(run)


def test_external_replay_pairs_reordered_rows_and_preserves_raw(tmp_path: Path) -> None:
    run, replay = tmp_path / "raw", tmp_path / "replay"
    rows = fixture_rows()
    write_run(run, rows)
    write_run(replay, rows[::-1], source=run)
    data = load_run(run, replay)
    assert data["models"][0]["processed_overall"] == aggregate(rows, "processed_metrics")
    assert data["processing_origin"] == "offline replay"
    for directory, field in ((run, "sources_sha256"), (replay, "processed_sources_sha256")):
        for name, digest in data[field].items():
            assert digest == hashlib.sha256((directory / name).read_bytes()).hexdigest()
    raw_rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    replay_rows = [json.loads(line) for line in (replay / "results.jsonl").read_text().splitlines()]
    assert [row["test_case_id"] for row in replay_rows] == [
        row["test_case_id"] for row in reversed(raw_rows)
    ]
    for raw in raw_rows:
        # Pair by identity, not by file position.
        paired = next(row for row in replay_rows if row["test_case_id"] == raw["test_case_id"])
        assert {key: paired[key] for key in raw} == raw
        if raw["schema_valid"]:
            expected = process_extraction(Extraction.model_validate(raw["parsed_output"]))
            assert paired["processed_output"] == expected.model_dump()
            assert {
                key: value
                for key, value in paired["processed_output"].items()
                if key != "missing_fields"
            } == {
                key: value for key, value in raw["parsed_output"].items() if key != "missing_fields"
            }


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("dataset", "cases_sha256 differ"),
        ("provenance", "does not belong"),
        ("raw_mutation", "original raw field"),
        ("partial_inline", "Only some"),
        ("repair", "must not repair"),
        ("invalid_credit", "must not repair"),
        ("output_change", "deterministic missing-field"),
        ("missing_rule", "deterministic missing-field"),
        ("metric_change", "other quality metrics"),
        ("summary", "differs from summary"),
        ("complete_score", "disagrees with component"),
    ],
)
def test_rejects_untrustworthy_stages(tmp_path: Path, corruption: str, message: str) -> None:
    run, replay = tmp_path / "raw", tmp_path / "replay"
    rows = fixture_rows()
    inline = corruption == "partial_inline"
    write_run(run, rows, inline=inline)
    if not inline:
        write_run(replay, rows[::-1], source=run)
    target = run if inline else replay
    manifest = json.loads((target / "manifest.json").read_text())
    saved = [json.loads(line) for line in (target / "results.jsonl").read_text().splitlines()]
    valid = next(row for row in saved if row["category"] == "multiple")
    invalid = next(row for row in saved if not row["schema_valid"])
    if corruption == "dataset":
        manifest["cases_sha256"] = "wrong-dataset"
    elif corruption == "provenance":
        manifest["source_results_sha256"] = "wrong-source"
    elif corruption == "raw_mutation":
        valid["raw_response"] = "changed"
    elif corruption == "partial_inline":
        del valid["processed_metrics"]
    elif corruption == "repair":
        invalid["processed_output"] = rows[0]["processed_output"]
    elif corruption == "invalid_credit":
        invalid["processed_metrics"]["complete_case_accuracy"] = 1
    elif corruption == "output_change":
        valid["processed_output"]["destination"] = "invented"
    elif corruption == "missing_rule":
        valid["processed_output"]["missing_fields"] = []
    elif corruption == "metric_change":
        valid["processed_metrics"]["empty_field_correct"] = 0
    elif corruption == "complete_score":
        valid["processed_metrics"]["complete_case_accuracy"] = 0
    elif corruption == "summary":
        path = target / "processed_summary.json"
        summary = json.loads(path.read_text())
        summary[0]["complete_case_accuracy"] = 0
        path.write_text(json.dumps(summary), encoding="utf-8")
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (target / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in saved), encoding="utf-8"
    )
    with pytest.raises(ValueError, match=message):
        load_run(run, None if inline else replay)


@pytest.mark.parametrize(
    ("inline", "filename"),
    [(False, "summary.json"), (True, "summary.json"), (True, "processed_summary.json")],
)
def test_saved_stage_summaries_are_independently_validated(
    tmp_path: Path, inline: bool, filename: str
) -> None:
    run = tmp_path / "run"
    write_run(run, fixture_rows(), inline=inline)
    path = run / filename
    summary = json.loads(path.read_text())
    summary[0]["complete_case_accuracy"] = 0
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from summary"):
        load_run(run)


def test_count_warning_and_captions_use_actual_single_model_grid(tmp_path: Path) -> None:
    run = tmp_path / "run"
    write_run(run, fixture_rows(), inline=True)
    path = run / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["measured_requests"] = 999
    path.write_text(json.dumps(manifest), encoding="utf-8")
    data = load_run(run)
    assert len(data["warnings"]) == 1
    assert "999" in data["warnings"][0] and "results contain 6" in data["warnings"][0]
    model = data["models"][0]
    assert model["overall"]["requests"] == model["processed_overall"]["requests"] == 6
    assert model["overall"]["complete_case_accuracy"] == 1 / 6
    assert model["processed_overall"]["complete_case_accuracy"] == 5 / 6
    text = captions(data)
    assert "6 unique cases/model, 1 passes" in text
    assert "Integrity Warnings" in text and data["warnings"][0] in text
    assert "Raw exact | Processed exact | Change (pp)" in text
    assert "| synthetic | 16.7% | 83.3% | +66.7 | 16.7% -> 83.3% |" in text
    assert "recorded inline" in text and "not additional requests" in text
    assert "offline-test-model" in text
    for unsupported_claim in ("Gemini", "Cloud Gemma", "LM Studio", "Three passes", "four cases"):
        assert unsupported_claim not in text


def test_v3_traits_add_overlapping_columns(tmp_path: Path) -> None:
    run = tmp_path / "run"
    traits = {"case-basic": ["missing"], "case-missing": ["missing"], "case-noisy": ["noisy"]}
    write_run(run, fixture_rows(), inline=True, traits=traits)
    data = load_run(run)
    model = data["models"][0]
    assert list(model["categories"]) == [*CATEGORIES, "trait:missing", "trait:noisy"]
    assert list(model["processed_categories"]) == list(model["categories"])
    assert model["categories"]["trait:missing"]["requests"] == 2
    assert model["categories"]["trait:noisy"] == model["categories"]["noisy"]
    assert "Trait columns (v3) overlap" in captions(data)


def test_repair_v2_inline_is_verified_and_captioned(tmp_path: Path) -> None:
    run = tmp_path / "run"
    rows = fixture_rows()
    for row in rows:
        if row["schema_valid"]:
            parsed = Extraction.model_validate(row["parsed_output"])
            row["processed_output"] = PROCESSORS["repair-v2"](parsed)[0].model_dump()
    write_run(run, rows, inline=True, processing="repair-v2")
    data = load_run(run)
    assert data["processing_version"] == "repair-v2"
    assert "Repairs resolve contradictions" in captions(data)
    saved = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    next(row for row in saved if row["schema_valid"])["processed_output"]["destination"] = "x"
    (run / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in saved))
    with pytest.raises(ValueError, match="deterministic repair-v2 processing"):
        load_run(run)


def test_replay_supersedes_inline_processing(tmp_path: Path) -> None:
    run, replay = tmp_path / "raw", tmp_path / "replay"
    rows = fixture_rows()
    write_run(run, rows, inline=True)
    write_run(replay, rows[::-1], source=run)
    data = load_run(run, replay)
    assert data["processing_origin"] == "offline replay"
    assert data["models"][0]["processed_overall"] == aggregate(rows, "processed_metrics")
