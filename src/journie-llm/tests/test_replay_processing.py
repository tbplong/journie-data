"""Synthetic offline replay checks. No candidate models or real run artifacts are used."""

import copy
import json
import sys
from pathlib import Path

import benchmark
import httpx
import pytest
import replay_processing as replay
from prompts import RESPONSE_SCHEMA
from schema import Extraction, score


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Replay must not use network or instantiate providers")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(benchmark, "GeminiProvider", forbidden)
    monkeypatch.setattr(benchmark, "OpenAICompatibleProvider", forbidden)


@pytest.fixture
def source(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    cases_path = tmp_path / "cases.jsonl"
    gold_data = benchmark.load_cases(Path(__file__).resolve().parents[1] / "cases.jsonl")[
        0
    ].model_dump()
    output = gold_data["expected_output"]
    output.update(
        destination=None,
        start_date=None,
        end_date=None,
        duration_days=None,
        budget_vnd=0,
        budget_scope=None,
        available_start=None,
        available_end=None,
        interests=[],
        must_visit=[],
        avoid=[],
        preferred_places=[],
        soft_constraints=[],
        hard_constraints=[{"field": "budget_vnd", "operator": "eq", "value": "0"}],
        missing_fields=["destination", "travel_dates_or_duration", "available_time"],
        possible_conflicts=[],
        needs_clarification=False,
    )
    gold_data.update(
        id="zero",
        category="synthetic",
        user_input="My budget is exactly 0 VND.",
        expected_constraints={key: output[key] for key in ("hard_constraints", "soft_constraints")},
        expected_missing_fields=output["missing_fields"],
        expected_conflicts=[],
    )
    bad_case = copy.deepcopy(gold_data)
    bad_case["id"] = "invalid"
    cases_path.write_text("\n".join(json.dumps(case) for case in [gold_data, bad_case]) + "\n")
    gold = Extraction.model_validate(output)
    parsed = gold.model_copy(update={"missing_fields": ["budget"]})
    configs = [
        {
            "name": name,
            "provider": "openai",
            "model": f"model-{name}",
            "api_key_env": "SECRET_ENV",
            "token": "SECRET_TOKEN",
            "notes": "SECRET_NOTE",
            "base_url": "https://secret.invalid",
        }
        for name in ("a", "b")
    ]
    rows = []
    for config in configs:
        for index in (1, 2):
            for case_id in ("zero", "invalid"):
                valid = case_id == "zero"
                rows.append(
                    {
                        "provider": config["name"],
                        "model": config["model"],
                        "resolved_model": "actual-model-revision",
                        "test_case_id": case_id,
                        "category": "synthetic",
                        "pass": index,
                        "warmup": False,
                        "schema_valid": valid,
                        "json_parse_valid": True,
                        "parsed_output": parsed.model_dump() if valid else {"destination": 123},
                        "raw_response": parsed.model_dump_json()
                        if valid
                        else '{"destination":123}',
                        "validation_result": {
                            "valid": valid,
                            "errors": [] if valid else ["invalid"],
                        },
                        "failure": None,
                        "http_status": 200,
                        "latency_seconds": 1.25,
                        "input_tokens": 11,
                        "output_tokens": 7,
                        "total_tokens": 18,
                        "reasoning_tokens": None,
                        "custom_original": {"keep": [1, 2]},
                        "metrics": score(parsed if valid else None, gold),
                    }
                )
    manifest = {
        "complete": True,
        "passes": 2,
        "case_count": 2,
        "measured_requests": 8,
        "cases_sha256": benchmark.fingerprint(cases_path.read_bytes()),
        "schema": RESPONSE_SCHEMA,
        "schema_sha256": benchmark.fingerprint(json.dumps(RESPONSE_SCHEMA, sort_keys=True)),
        "prompt_sha256": benchmark.fingerprint(benchmark.SYSTEM_PROMPT),
        "system_prompt": benchmark.SYSTEM_PROMPT,
        "providers": configs,
        "notes": "SECRET_TOP_LEVEL",
    }
    (run / "manifest.json").write_text(json.dumps(manifest))
    (run / "results.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    summaries = [
        {
            "provider": cfg["name"],
            "model": cfg["model"],
            "category": "overall",
            **benchmark.summarize([r for r in rows if r["provider"] == cfg["name"]], 9.5),
        }
        for cfg in configs
    ]
    (run / "summary.json").write_text(json.dumps(summaries))
    return run, cases_path, tmp_path / "replayed", rows, manifest


def test_replay_preserves_raw_and_writes_separate_summaries(source):
    run, cases, output, original, manifest = source
    before = {path.name: path.read_bytes() for path in run.iterdir()}
    enhanced = replay.replay(run, output, cases)
    assert {path.name: path.read_bytes() for path in run.iterdir()} == before
    for old, new in zip(original, enhanced, strict=True):
        assert {key: new[key] for key in old} == old
        assert new["validation_seconds"] is None
        assert new["extraction_pipeline_latency_seconds"] is None
        assert new["postprocessing_seconds"] >= 0
        if old["schema_valid"]:
            assert new["processed_output"]["budget_vnd"] == 0
            assert "budget" not in new["processed_output"]["missing_fields"]
            assert "destination" in new["processed_output"]["missing_fields"]
            assert new["processed_metrics"]["complete_case_accuracy"] == 1
            assert new["consistency_issues"] == []
        else:
            assert new["processed_output"] is None
            assert new["consistency_issues"] is None
            assert new["processed_metrics"] == old["metrics"]
    raw = json.loads((output / "summary.json").read_text())
    saved = json.loads((run / "summary.json").read_text())
    assert [row for row in raw if row["category"] == "overall"] == saved
    processed = json.loads((output / "processed_summary.json").read_text())
    assert processed[0]["complete_case_accuracy"] == 0.5
    assert processed[0]["consistency_checked_requests"] == 2
    assert processed[0]["extraction_pipeline_latency_seconds_reported_requests"] == 0
    assert processed[0]["processing_version"] == replay.PROCESSING_VERSION
    new_manifest = json.loads((output / "manifest.json").read_text())
    assert new_manifest["prompt_variant"] == "legacy_baseline"
    assert new_manifest["prompt_sha256"] == manifest["prompt_sha256"]
    assert new_manifest["source_results_sha256"] == benchmark.fingerprint(before["results.jsonl"])
    assert new_manifest["source_manifest_sha256"] == benchmark.fingerprint(before["manifest.json"])
    assert new_manifest["source_summary_sha256"] == benchmark.fingerprint(before["summary.json"])
    assert new_manifest["new_tokens"] == new_manifest["new_model_requests"] == 0
    assert "SECRET" not in (output / "manifest.json").read_text()
    assert all(set(config) == {"name", "provider", "model"} for config in new_manifest["providers"])
    report = (output / "report.md").read_text()
    assert "| a / model-a | 4 | 0 | 2 | 2 | 2 | 0 | 2 |" in report
    assert "ambiguity" in report and "not first-pass" in report
    assert (output / "summary.csv").exists() and (output / "processed_summary.csv").exists()


@pytest.mark.parametrize(
    "problem,match",
    [
        ("cases_hash", "Cases hash"),
        ("schema_hash", "schema"),
        ("schema_dump", "schema"),
        ("prompt_hash", "prompt"),
        ("raw_mismatch", "raw_response"),
        ("bad_json_flag", "json_parse_valid"),
        ("negative_latency", "latency_seconds"),
        ("invalid_tokens", "output_tokens"),
        ("invalid_status", "http_status"),
        ("incomplete", "complete"),
        ("count", "count"),
        ("missing_row", "count"),
        ("duplicate", "Duplicate"),
        ("unknown_provider", "provider"),
        ("unknown_case", "case"),
        ("wrong_model", "model"),
        ("category", "category"),
        ("pass", "pass"),
        ("warmup", "Warmup"),
        ("metrics", "Raw metrics"),
        ("bad_valid", "parsed_output"),
        ("validation_flag", "validation_result"),
        ("already_processed", "Only some source results are already processed"),
        ("gold_mirror", "expected_missing_fields"),
    ],
)
def test_rejects_incompatible_artifacts_without_output(source, problem, match):
    run, cases, output, rows, manifest = source
    if problem == "cases_hash":
        manifest["cases_sha256"] = "b" * 64
    elif problem == "schema_hash":
        manifest["schema_sha256"] = "b" * 64
    elif problem == "schema_dump":
        manifest["schema"] = {}
    elif problem == "prompt_hash":
        manifest["prompt_sha256"] = "b" * 64
    elif problem == "incomplete":
        manifest["complete"] = False
    elif problem == "count":
        manifest["measured_requests"] = 7
    elif problem == "missing_row":
        rows.pop()
    elif problem == "duplicate":
        rows[-1] = rows[0]
    elif problem == "gold_mirror":
        data = [json.loads(line) for line in cases.read_text().splitlines()]
        data[0]["expected_missing_fields"] = []
        cases.write_text("\n".join(json.dumps(case) for case in data))
        manifest["cases_sha256"] = benchmark.fingerprint(cases.read_bytes())
    else:
        key, value = {
            "unknown_provider": ("provider", "unknown"),
            "unknown_case": ("test_case_id", "unknown"),
            "wrong_model": ("model", "different"),
            "category": ("category", "wrong"),
            "pass": ("pass", 3),
            "warmup": ("warmup", True),
            "metrics": ("metrics", {}),
            "bad_valid": ("parsed_output", {}),
            "validation_flag": ("validation_result", {"valid": False}),
            "already_processed": ("processed_output", None),
            "raw_mismatch": ("raw_response", "{}"),
            "bad_json_flag": ("json_parse_valid", 2),
            "negative_latency": ("latency_seconds", -1),
            "invalid_tokens": ("output_tokens", True),
            "invalid_status": ("http_status", "200"),
        }[problem]
        rows[0][key] = value
    (run / "manifest.json").write_text(json.dumps(manifest))
    (run / "results.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match=match):
        replay.replay(run, output, cases)
    assert not output.exists()


def test_invalid_source_wall_time_is_rejected(source):
    run, cases, output, _, _ = source
    summaries = json.loads((run / "summary.json").read_text())
    summaries[0]["measured_wall_seconds"] = -2
    (run / "summary.json").write_text(json.dumps(summaries))
    with pytest.raises(ValueError, match="wall time"):
        replay.replay(run, output, cases)
    assert not output.exists()


def test_destination_safety_including_resolved_symlink(source):
    run, cases, output, _, _ = source
    output.mkdir()
    with pytest.raises(FileExistsError):
        replay.replay(run, output, cases)
    alias = run.parent / "alias"
    alias.symlink_to(run, target_is_directory=True)
    for destination in (run, run / "nested", alias / "nested", run / ".." / "run" / "nested"):
        with pytest.raises(ValueError, match="descendant"):
            replay.replay(run, destination, cases)
    assert not (run / "nested").exists()


def test_cli_and_recorded_validation_time(source, monkeypatch):
    run, cases, output, rows, _ = source
    rows[0]["validation_seconds"] = 0.031
    (run / "results.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    monkeypatch.setattr(
        sys,
        "argv",
        ["replay_processing.py", "--run", str(run), "--cases", str(cases), "--output", str(output)],
    )
    replay.main()
    enhanced = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert enhanced[0]["validation_seconds"] == 0.031
    assert enhanced[0]["extraction_pipeline_latency_seconds"] is None
    with pytest.raises(SystemExit) as exc:
        replay.main()
    assert exc.value.code == 2


def test_cli_default_hash_matched_cases_and_required_output(source, monkeypatch):
    run, cases, output, _, _ = source
    monkeypatch.setattr(benchmark, "HERE", cases.parent)
    monkeypatch.setattr(
        sys, "argv", ["replay_processing.py", "--run", str(run), "--output", str(output)]
    )
    replay.main()
    assert (output / "manifest.json").exists()
    monkeypatch.setattr(sys, "argv", ["replay_processing.py", "--run", str(run)])
    with pytest.raises(SystemExit) as exc:
        replay.main()
    assert exc.value.code == 2


def test_repair_v2_replays_an_inline_processed_source(source):
    run, cases, output, original, _ = source
    gold = benchmark.load_cases(cases)[0].expected_output
    inline = copy.deepcopy(original)
    for row in inline:
        row.setdefault("validation_seconds", None)
        parsed = Extraction.model_validate(row["parsed_output"]) if row["schema_valid"] else None
        benchmark.add_processing(row, parsed, gold)
    (run / "results.jsonl").write_text("\n".join(json.dumps(row) for row in inline))
    before = (run / "results.jsonl").read_bytes()
    enhanced = replay.replay(run, output, cases, "repair-v2")
    assert (run / "results.jsonl").read_bytes() == before
    for old, new in zip(original, enhanced, strict=True):
        assert {key: new[key] for key in old} == old
        # The fixture's valid output has a stale missing_fields list and nothing else to repair.
        assert new["repairs"] == (["missing_fields"] if old["schema_valid"] else None)
    new_manifest = json.loads((output / "manifest.json").read_text())
    assert new_manifest["processing_version"] == "repair-v2"
    assert "never lowered" in new_manifest["processing_policy"]
    assert "## Repaired Outputs\n\nNone." in (output / "report.md").read_text()
