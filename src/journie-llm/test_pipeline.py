"""Offline prompt and processing contracts, independent of live configs and gold files."""

import asyncio
import copy
import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import benchmark
import httpx
import prompts
import pytest
from processing import (
    DIAGNOSTICS_VERSION,
    PROCESSING_VERSION,
    consistency_issues,
    derive_missing_fields,
    process_extraction,
)
from providers import GeminiProvider, OpenAICompatibleProvider, ProviderConfig
from schema import ANNOTATION_RULES, Extraction, score


def make_case(name: str = "plain", **updates: Any) -> benchmark.Case:
    output: dict[str, Any] = {
        **dict.fromkeys(
            (
                "destination",
                "start_date",
                "end_date",
                "duration_days",
                "budget_vnd",
                "budget_scope",
                "available_start",
                "available_end",
            )
        ),
        **{
            key: []
            for key in (
                "interests",
                "must_visit",
                "avoid",
                "preferred_places",
                "hard_constraints",
                "soft_constraints",
                "possible_conflicts",
            )
        },
        "missing_fields": ["destination", "travel_dates_or_duration", "budget", "available_time"],
        "needs_clarification": False,
        **updates,
    }
    gold = Extraction.model_validate(output)
    return benchmark.Case(
        id=name,
        category="generated",
        user_input=name,
        expected_output=gold,
        expected_constraints={key: output[key] for key in ("hard_constraints", "soft_constraints")},
        expected_missing_fields=list(gold.missing_fields),
        expected_conflicts=list(gold.possible_conflicts),
    )


def config(provider: Literal["gemini", "openai"] = "openai") -> ProviderConfig:
    return ProviderConfig(
        name=provider,
        provider=provider,
        model="offline-model",
        base_url="https://offline.test/v1",
        api_key_env="PIPELINE_TEST_KEY",
        temperature=0.25,
        max_output_tokens=321,
        timeout_seconds=7,
        extra_body={"custom_option": True},
    )


def response(provider: str, text: str) -> httpx.Response:
    payload = (
        {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
        if provider == "gemini"
        else {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
    )
    return httpx.Response(200, json=payload)


def execute_text(case: benchmark.Case, text: str) -> dict:
    async def execute() -> dict:
        cfg = config()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: response("openai", text))
        ) as client:
            return await benchmark.execute(
                OpenAICompatibleProvider(client, cfg), cfg, case, 1, prompt_variant="reinforced"
            )

    return asyncio.run(execute())


def test_prompt_contract_without_gold_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("build_prompt must not load gold data")

    monkeypatch.setattr(benchmark, "load_cases", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    baseline = (
        ANNOTATION_RULES
        + "\nJSON Schema:\n"
        + json.dumps(Extraction.model_json_schema(), ensure_ascii=False)
    )
    assert prompts.build_prompt().encode() == baseline.encode()
    assert prompts.build_prompt("baseline") == benchmark.SYSTEM_PROMPT == baseline
    reinforced = prompts.build_prompt("reinforced")
    assert reinforced.startswith(baseline)
    addition = reinforced[len(baseline) :]
    assert "CHECKLIST" in addition
    assert "Independently authored" in addition
    for illustration in ("Gallery Cedar", "Garden Willow", "60 CHF", "2027-04-18"):
        assert illustration in addition and illustration not in baseline
    assert reinforced.count("JSON Schema:\n") == 1
    with pytest.raises(ValueError, match="Unknown prompt variant"):
        prompts.build_prompt(cast(prompts.PromptVariant, "unknown"))


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_execute_sends_selected_prompt_only_setting_changed(
    provider: Literal["gemini", "openai"], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPELINE_TEST_KEY", "offline-secret")
    cfg, case = config(provider), make_case()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(provider, case.expected_output.model_dump_json())

    async def execute() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = GeminiProvider if provider == "gemini" else OpenAICompatibleProvider
            instance = adapter(client, cfg)
            baseline = await benchmark.execute(instance, cfg, case, 1)
            reinforced = await benchmark.execute(
                instance, cfg, case, 1, prompt_variant="reinforced"
            )
            return [baseline, reinforced]

    rows = asyncio.run(execute())
    bodies = [json.loads(request.content) for request in requests]
    variants: tuple[prompts.PromptVariant, ...] = ("baseline", "reinforced")
    for body, variant in zip(bodies, variants, strict=True):
        if provider == "gemini":
            assert body.pop("systemInstruction") == {
                "parts": [{"text": prompts.build_prompt(variant)}]
            }
            assert body["generationConfig"] == {
                "temperature": 0.25,
                "maxOutputTokens": 321,
                "responseMimeType": "application/json",
                "responseJsonSchema": Extraction.model_json_schema(),
            }
        else:
            assert body["messages"].pop(0) == {
                "role": "system",
                "content": prompts.build_prompt(variant),
            }
            assert (body["temperature"], body["max_tokens"], body["stream"]) == (0.25, 321, False)
            assert body["response_format"]["json_schema"]["schema"] == (
                Extraction.model_json_schema()
            )
    assert bodies[0] == bodies[1]
    assert requests[0].url == requests[1].url
    headers = [dict(request.headers) for request in requests]
    for header in headers:
        header.pop("content-length")
    assert headers[0] == headers[1]
    assert requests[0].extensions == requests[1].extensions
    assert requests[0].extensions["timeout"]["read"] == 7
    assert [row["prompt_variant"] for row in rows] == ["baseline", "reinforced"]
    assert [row["prompt_version"] for row in rows] == [
        prompts.PROMPT_VERSIONS[variant] for variant in ("baseline", "reinforced")
    ]
    assert (
        rows[0]["metrics"]
        == rows[1]["metrics"]
        == score(case.expected_output, case.expected_output)
    )


@pytest.mark.parametrize("text", ["```json\n{}\n```", "{}", '{"budget_vnd":"0"}'])
def test_invalid_responses_never_repaired(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_TEST_KEY", "offline-secret")
    row = execute_text(make_case(), text)
    assert row["raw_response"] == text
    assert row["parsed_output"] == (None if text.startswith("```") else json.loads(text))
    assert row["json_parse_valid"] == (not text.startswith("```"))
    assert not row["schema_valid"] and not row["validation_result"]["valid"]
    assert row["processed_output"] is row["consistency_issues"] is None
    assert row["missing_fields_changed"] is False
    assert row["processed_metrics"] == row["metrics"] == score(None, make_case().expected_output)


def test_processing_changes_only_missing_fields_and_diagnostics_are_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPELINE_TEST_KEY", "offline-secret")
    case = make_case(
        destination="Unmapped Place",
        start_date="2027-04-18",
        budget_vnd=0,
        available_start="21:15",
        available_end="00:45",
        missing_fields=[],
        must_visit=["Cedar"],
        avoid=["Cedar"],
        possible_conflicts=["must_visit_avoid"],
        needs_clarification=False,
    )
    raw = case.expected_output.model_dump()
    raw["missing_fields"] = ["budget", "travel_dates_or_duration"]
    row = execute_text(case, json.dumps(raw))
    assert row["schema_valid"] and row["missing_fields_changed"]
    assert row["raw_response"] == json.dumps(raw) and row["parsed_output"] == raw
    assert row["metrics"] == score(Extraction.model_validate(raw), case.expected_output)
    assert row["metrics"]["complete_case_accuracy"] == 0
    assert row["processed_output"] == {**raw, "missing_fields": []}
    assert row["processed_metrics"]["complete_case_accuracy"] == 1
    assert {issue["code"] for issue in row["consistency_issues"]} >= {
        "missing_atom",
        "conflict_requires_clarification",
        "overnight_relation_requires_review",
    }
    extraction = Extraction.model_validate(raw)
    original = extraction.model_dump()
    processed = process_extraction(extraction)
    before = processed.model_dump()
    assert consistency_issues(processed)
    assert processed.model_dump() == before and extraction.model_dump() == original
    assert processed is not extraction


def test_valid_contradiction_accepted_and_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_TEST_KEY", "offline-secret")
    case = make_case(
        must_visit=["Cedar"],
        avoid=["Cedar"],
        needs_clarification=True,
        possible_conflicts=["must_visit_avoid"],
        hard_constraints=[
            {"field": "must_visit", "operator": "include", "value": "Cedar"},
            {"field": "avoid", "operator": "exclude", "value": "Cedar"},
        ],
    )
    row = execute_text(case, case.expected_output.model_dump_json())
    assert row["schema_valid"] and not row["missing_fields_changed"]
    assert row["processed_output"] == row["parsed_output"] == case.expected_output.model_dump()
    assert row["consistency_issues"] == []
    assert row["metrics"] == row["processed_metrics"]
    assert row["metrics"]["complete_case_accuracy"] == 1


@pytest.mark.parametrize(
    "updates,missing",
    [
        ({}, ["destination", "travel_dates_or_duration", "budget", "available_time"]),
        ({"start_date": "2027-04-18", "budget_vnd": 0}, ["destination", "available_time"]),
        (
            {"end_date": "2027-04-18", "available_start": "09:00"},
            ["destination", "budget", "available_time"],
        ),
        (
            {"duration_days": 1, "available_start": "09:00", "available_end": "10:00"},
            ["destination", "budget"],
        ),
    ],
)
def test_missing_fields_null_logic(updates: dict, missing: list[str]) -> None:
    assert derive_missing_fields(make_case(**updates).expected_output) == missing


def test_pipeline_timing_excludes_both_gold_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_TEST_KEY", "offline-secret")
    clock = [0.0]
    score_calls = []

    def tick() -> float:
        clock[0] += 1
        return clock[0]

    def slow_score(actual: Extraction | None, expected: Extraction) -> dict:
        score_calls.append(clock[0])
        clock[0] += 100
        return score(actual, expected)

    monkeypatch.setattr(benchmark, "time", SimpleNamespace(perf_counter=tick))
    monkeypatch.setattr(benchmark, "score", slow_score)
    case = make_case()
    row = execute_text(case, case.expected_output.model_dump_json())
    assert len(score_calls) == 2 and clock[0] >= 200
    for key in ("latency_seconds", "validation_seconds", "postprocessing_seconds"):
        assert row[key] == 1
    assert (
        row["extraction_pipeline_latency_seconds"]
        == sum(
            row[key] for key in ("latency_seconds", "validation_seconds", "postprocessing_seconds")
        )
        == 3
    )


def test_processed_summary_denominators_and_raw_summary_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPELINE_TEST_KEY", "offline-secret")
    case = make_case(
        available_start="21:15",
        available_end="00:45",
        missing_fields=["destination", "travel_dates_or_duration", "budget"],
    )
    raw = case.expected_output.model_dump()
    raw["missing_fields"] = []
    rows = [
        execute_text(case, json.dumps(raw)),
        execute_text(case, case.expected_output.model_dump_json()),
        execute_text(case, "{}"),
    ]
    before = copy.deepcopy(rows)
    raw_summary = benchmark.summarize(rows, 6)
    summary = benchmark.summarize_processed(rows, 6)
    assert summary["requests"] == 3 and summary["consistency_checked_requests"] == 2
    assert summary["schema_valid_rate"] == summary["field_accuracy"] == pytest.approx(2 / 3)
    assert summary["complete_case_accuracy"] == pytest.approx(2 / 3)
    assert raw_summary["complete_case_accuracy"] == pytest.approx(1 / 3)
    assert summary["missing_fields_recall"] == pytest.approx(2 / 3)
    assert summary["missing_fields_changed_rate_valid_outputs"] == 0.5
    assert summary["consistency_error_rate_checked_outputs"] == 1
    assert summary["consistency_review_rate_checked_outputs"] == 1
    assert summary["consistency_issue_counts"] == {
        "missing_atom": 4,
        "overnight_relation_requires_review": 2,
    }
    assert summary["attempts_per_second"] == 0.5
    assert rows == before and benchmark.summarize(rows, 6) == raw_summary
    empty = benchmark.summarize_processed([rows[-1]])
    assert empty["consistency_checked_requests"] == 0
    assert empty["missing_fields_changed_rate_valid_outputs"] is None
    assert empty["consistency_error_rate_checked_outputs"] is None
    assert empty["consistency_issue_counts"] == {}


def test_full_reinforced_run_separate_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPELINE_TEST_KEY", "offline-secret")
    cases = [make_case("good-a"), make_case("good-b"), make_case("invalid")]
    by_input = {case.user_input: case for case in cases}
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["messages"][0]["content"] == prompts.build_prompt("reinforced")
        case = by_input[body["messages"][1]["content"]]
        requests.append(case.id)
        output = case.expected_output.model_dump()
        output["missing_fields"] = []
        if case.id == "invalid":
            output.pop("needs_clarification")
        return response("openai", json.dumps(output))

    client_class = httpx.AsyncClient

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return client_class(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(benchmark.httpx, "AsyncClient", client_factory)
    output = tmp_path / "reinforced"
    asyncio.run(benchmark.run([config()], cases, output, 2, 1, 42, "generated-hash", "reinforced"))
    manifest = json.loads((output / "manifest.json").read_text())
    prompt = prompts.build_prompt("reinforced")
    assert manifest["complete"] and manifest["measured_requests"] == 6
    assert manifest["prompt_variant"] == "reinforced"
    assert manifest["prompt_version"] == prompts.PROMPT_VERSIONS["reinforced"]
    assert manifest["system_prompt"] == prompt
    assert manifest["prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
    assert manifest["schema"] == Extraction.model_json_schema()
    assert manifest["processing_version"] == PROCESSING_VERSION
    assert manifest["diagnostics_version"] == DIAGNOSTICS_VERSION
    assert benchmark.SYSTEM_PROMPT == prompts.build_prompt("baseline")
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    warmups = [json.loads(line) for line in (output / "warmups.jsonl").read_text().splitlines()]
    assert len(rows) == 6 and len(warmups) == 1 and len(requests) == 7
    assert warmups[0]["warmup"] and warmups[0]["pass"] == 0
    for row in rows:
        assert not row["warmup"] and row["prompt_variant"] == "reinforced"
        assert row["metrics"]["complete_case_accuracy"] == 0
        valid = row["test_case_id"] != "invalid"
        assert row["schema_valid"] == row["missing_fields_changed"] == valid
        assert row["processed_metrics"]["complete_case_accuracy"] == int(valid)
        assert row["parsed_output"]["missing_fields"] == []
        if not valid:
            assert row["processed_output"] is row["consistency_issues"] is None
    for stem, summarize in (
        ("summary", benchmark.summarize),
        ("processed_summary", benchmark.summarize_processed),
    ):
        summaries = json.loads((output / f"{stem}.json").read_text())
        assert len(summaries) == 2
        for summary in summaries:
            assert summary["requests"] == 6
            assert "prompt_variant" not in summary and "prompt_version" not in summary
            expected = summarize(rows, summary["measured_wall_seconds"])
            assert all(summary[key] == value for key, value in expected.items())
            assert summary["complete_case_accuracy"] == (
                pytest.approx(2 / 3) if stem == "processed_summary" else 0
            )
        with (output / f"{stem}.csv").open(newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        assert len(csv_rows) == len(summaries)
        assert [float(row["complete_case_accuracy"]) for row in csv_rows] == [
            summary["complete_case_accuracy"] for summary in summaries
        ]
        assert all(int(row["requests"]) == 6 for row in csv_rows)
