"""Offline benchmark contract tests; no provider credentials or live HTTP required."""

import asyncio
import csv
import json
import random
from pathlib import Path

import benchmark
import httpx
import pytest
from providers import GeminiProvider, OpenAICompatibleProvider, ProviderConfig
from pydantic import ValidationError
from schema import Extraction, score


@pytest.fixture(scope="module")
def cases():
    return {
        case.id: case
        for case in benchmark.load_cases(Path(__file__).resolve().parents[1] / "cases.jsonl")
    }


def config(provider="openai", **overrides):
    return ProviderConfig.model_validate(
        {
            "name": provider,
            "provider": provider,
            "model": "test-model",
            "base_url": "https://provider.test/v1",
            **overrides,
        }
    )


def test_provider_config_file():
    configs = [
        ProviderConfig.model_validate(item)
        for item in json.loads(
            (Path(__file__).resolve().parents[1] / "providers.json").read_text(encoding="utf-8")
        )
    ]
    assert len({cfg.name for cfg in configs}) == len(configs)


@pytest.mark.parametrize("name", ["huggingface-qwen", "huggingface-deepseek"])
def test_huggingface_config_uses_openai_protocol(name, cases, monkeypatch):
    # Protocol tests must not depend on the user's mutable live model configuration.
    model = (
        "Qwen/Qwen3-Next-80B-A3B-Instruct:deepinfra"
        if name == "huggingface-qwen"
        else "deepseek-ai/DeepSeek-V3-0324:deepinfra"
    )
    cfg = config(
        name=name, model=model, base_url="https://router.huggingface.co/v1", api_key_env="HF_TOKEN"
    )
    monkeypatch.setenv("HF_TOKEN", "hf_test_not_a_real_token")
    case = cases["multiple-01"]

    def handler(request):
        assert str(request.url) == "https://router.huggingface.co/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer hf_test_not_a_real_token"
        body = json.loads(request.content)
        assert body["model"] == cfg.model
        assert body["model"].endswith(":deepinfra")
        assert body["response_format"]["json_schema"]["schema"] == benchmark.RESPONSE_SCHEMA
        assert body["response_format"]["json_schema"]["strict"] is True
        assert "provider" not in body and "reasoning" not in body
        response = payload("openai", case.expected_output.model_dump_json())
        response["model"] = cfg.model.split(":")[0]
        return httpx.Response(200, json=response)

    row = execute_mock(cfg, case, handler)
    assert row["provider"] == name and row["model"] == cfg.model
    assert row["resolved_model"] == cfg.model.split(":")[0]
    assert row["schema_valid"] and row["metrics"]["complete_case_accuracy"] == 1


def payload(provider, text, usage=True):
    if provider == "gemini":
        result = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "private reasoning", "thought": True},
                            {"text": text[: len(text) // 2]},
                            {"text": text[len(text) // 2 :]},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        if usage:
            result["usageMetadata"] = {
                "promptTokenCount": 11,
                "candidatesTokenCount": 7,
                "totalTokenCount": 21,
                "thoughtsTokenCount": 3,
            }
    else:
        result = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
        if usage:
            result["usage"] = {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 21,
                "completion_tokens_details": {"reasoning_tokens": 3},
            }
    result["modelVersion" if provider == "gemini" else "model"] = "test-resolved-model"
    return result


def execute_mock(cfg, case, handler):
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = GeminiProvider if cfg.provider == "gemini" else OpenAICompatibleProvider
            return await benchmark.execute(adapter(client, cfg), cfg, case, 1)

    return asyncio.run(execute())


@pytest.mark.parametrize("provider", ["gemini", "openai"])
@pytest.mark.parametrize("structured", [True, False])
def test_success_payload_auth_schema_settings_tokens(provider, structured, cases, monkeypatch):
    monkeypatch.setenv("BENCHMARK_TEST_KEY", "test-secret")
    cfg = config(
        provider,
        api_key_env="BENCHMARK_TEST_KEY",
        temperature=0.25,
        max_output_tokens=123,
        timeout_seconds=4.0,
        structured_output=structured,
        extra_body={"custom_option": True},
        model="models/test-model" if provider == "gemini" else "test-model",
    )
    case = cases["multiple-01"]
    text = case.expected_output.model_dump_json()
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.method == "POST"
        assert request.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"),
            4.0,
        )
        assert body["custom_option"] is True
        assert "test-secret" not in str(request.url) + request.content.decode()
        if provider == "gemini":
            assert str(request.url) == "https://provider.test/v1/models/test-model:generateContent"
            assert request.headers["x-goog-api-key"] == "test-secret"
            assert "authorization" not in request.headers
            assert body["systemInstruction"] == {"parts": [{"text": benchmark.SYSTEM_PROMPT}]}
            assert body["contents"] == [{"role": "user", "parts": [{"text": case.user_input}]}]
            settings = {"temperature": 0.25, "maxOutputTokens": 123}
            if structured:
                settings.update(
                    responseMimeType="application/json",
                    responseJsonSchema=benchmark.RESPONSE_SCHEMA,
                )
            assert body["generationConfig"] == settings
        else:
            assert str(request.url) == "https://provider.test/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer test-secret"
            assert body["model"] == "test-model"
            assert body["messages"] == [
                {"role": "system", "content": benchmark.SYSTEM_PROMPT},
                {"role": "user", "content": case.user_input},
            ]
            assert (body["temperature"], body["max_tokens"], body["stream"]) == (0.25, 123, False)
            if structured:
                assert body["response_format"] == {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "travel_constraints",
                        "strict": True,
                        "schema": benchmark.RESPONSE_SCHEMA,
                    },
                }
            else:
                assert "response_format" not in body
        return httpx.Response(200, json=payload(provider, text))

    row = execute_mock(cfg, case, handler)
    assert len(requests) == 1
    assert row["failure"] is None
    assert row["json_parse_valid"] and row["schema_valid"]
    assert row["raw_response"] == text
    assert row["parsed_output"] == case.expected_output.model_dump()
    assert row["validation_result"] == {"valid": True, "errors": []}
    assert [
        row[key]
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "reasoning_tokens",
        )
    ] == [11, 7, 21, 3]
    assert row["finish_reason"] == ("STOP" if provider == "gemini" else "stop")
    assert row["resolved_model"] == "test-resolved-model"
    assert row["metrics"]["complete_case_accuracy"] == 1


@pytest.mark.parametrize("provider", ["gemini", "openai"])
@pytest.mark.parametrize("problem", ["json", "schema", "timeout", "network", "429", "refusal"])
def test_invalid_and_failed_responses(provider, problem, cases, monkeypatch):
    monkeypatch.setenv("BENCHMARK_TEST_KEY", "test-secret")
    cfg = config(provider, api_key_env="BENCHMARK_TEST_KEY")

    def handler(request):
        if problem == "timeout":
            raise httpx.ReadTimeout("mock timeout", request=request)
        if problem == "network":
            raise httpx.ConnectError("mock network error", request=request)
        if problem == "429":
            return httpx.Response(429, json={"error": "rate limited"})
        if problem == "refusal":
            response = (
                {"candidates": [{"finishReason": "SAFETY"}]}
                if provider == "gemini"
                else {"choices": [{"message": {"content": None, "refusal": "Cannot comply"}}]}
            )
        else:
            response = payload(provider, "not JSON" if problem == "json" else "{}")
        return httpx.Response(200, json=response)

    row = execute_mock(cfg, cases["multiple-01"], handler)
    assert (
        row["failure"]
        == {
            "json": None,
            "schema": None,
            "timeout": "timeout",
            "network": "network_error",
            "429": "rate_limit",
            "refusal": "empty_response",
        }[problem]
    )
    assert row["http_status"] == (
        429 if problem == "429" else None if problem in ("timeout", "network") else 200
    )
    assert row["json_parse_valid"] == (problem == "schema")
    assert row["schema_valid"] is False
    assert row["metrics"]["field_accuracy"] == 0
    assert row["metrics"]["constraints"] == {"tp": 0, "fp": 0, "fn": 5}
    assert row["latency_seconds"] >= 0
    if problem in ("json", "schema"):
        assert row["validation_result"]["valid"] is False
        assert row["validation_result"]["errors"]
        assert row["input_tokens"] == 11
    elif problem == "refusal":
        assert row["raw_response"] == ""
        assert row["input_tokens"] is None
    else:
        assert row["raw_response"] is None
        assert row["input_tokens"] is None


def test_score_exact_omission_and_hard_soft_swap(cases):
    gold = cases["multiple-01"].expected_output
    exact = score(gold, gold)
    assert exact["field_accuracy"] == exact["complete_case_accuracy"] == 1
    assert exact["constraints"] == exact["classified_constraints"] == {"tp": 5, "fp": 0, "fn": 0}
    assert exact["hallucinated_case"] == 0
    output = gold.model_dump()
    atom = output["hard_constraints"].pop()
    omitted = score(Extraction.model_validate(output), gold)
    assert omitted["field_accuracy"] == 1
    assert omitted["complete_case_accuracy"] == 0
    assert omitted["constraints"] == {"tp": 4, "fp": 0, "fn": 1}
    output["soft_constraints"].append(atom)
    swapped = score(Extraction.model_validate(output), gold)
    assert swapped["constraints"] == {"tp": 5, "fp": 0, "fn": 0}
    assert swapped["classified_constraints"] == {"tp": 4, "fp": 1, "fn": 1}
    assert swapped["hallucinated_case"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("destination", "Invented City"),
        ("budget_vnd", 999),
        ("interests", ["invented"]),
        ("must_visit", ["Invented POI"]),
        ("avoid", ["Invented POI"]),
        ("preferred_places", ["Invented POI"]),
    ],
)
def test_hallucinated_fields_without_atoms(field, value, cases):
    gold = cases["missing-01"].expected_output
    output = gold.model_dump()
    output[field] = value
    metrics = score(Extraction.model_validate(output), gold)
    assert metrics["constraints"] == {"tp": 0, "fp": 0, "fn": 0}
    assert metrics["unsupported_fields"] == [field]
    assert metrics["hallucinated_case"] == 1
    assert metrics["complete_case_accuracy"] == 0


def test_list_reordering_is_not_an_error(cases):
    gold = cases["multiple-08"].expected_output
    output = gold.model_dump()
    for value in output.values():
        if isinstance(value, list):
            value.reverse()
    metrics = score(Extraction.model_validate(output), gold)
    assert metrics["field_accuracy"] == 1
    assert metrics["hallucinated_case"] == 0
    assert (
        metrics["constraints"]
        == metrics["classified_constraints"]
        == {
            "tp": len(gold.hard_constraints) + len(gold.soft_constraints),
            "fp": 0,
            "fn": 0,
        }
    )
    assert metrics["complete_case_accuracy"] == 1


def test_summary_failure_denominators_and_unknown_tokens(cases):
    case = cases["multiple-01"]
    cfg = config()
    good = execute_mock(
        cfg,
        case,
        lambda _: httpx.Response(
            200,
            json=payload("openai", case.expected_output.model_dump_json(), usage=False),
        ),
    )
    bad = execute_mock(cfg, case, lambda _: httpx.Response(429))
    good["latency_seconds"], bad["latency_seconds"] = 2.0, 8.0
    summary = benchmark.summarize([good, bad], wall_seconds=10.0)
    for key in (
        "json_parse_valid_rate",
        "schema_valid_rate",
        "field_accuracy",
        "complete_case_accuracy",
        "populated_field_accuracy",
        "empty_field_accuracy",
        "request_failure_rate",
        "rate_limit_rate",
        "constraints_recall",
    ):
        assert summary[key] == 0.5, key
    assert summary["constraints_precision"] == 1
    assert summary["constraints_f1"] == pytest.approx(2 / 3)
    assert summary["hallucination_rate_valid_outputs"] == 0
    assert summary["successful_response_p50_seconds"] == 2
    assert summary["all_attempts_p50_seconds"] == 5
    assert summary["all_attempts_p95_seconds"] == pytest.approx(7.7)
    assert summary["attempts_per_second"] == 0.2
    assert summary["successful_responses_per_second"] == 0.1
    for key in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens"):
        assert good[key] is None
        assert summary[f"{key}_reported_requests"] == 0
        assert summary[f"{key}_mean"] is None
        assert summary[f"{key}_sum_reported"] is None
    assert benchmark.summarize([bad])["hallucination_rate_valid_outputs"] is None
    good["metrics"]["hallucinated_case"] = 1
    assert benchmark.summarize([good, bad])["hallucination_rate_valid_outputs"] == 1
    good["input_tokens"] = 0
    mixed = benchmark.summarize([good, bad])
    assert mixed["input_tokens_reported_requests"] == 1
    assert mixed["input_tokens_mean"] == mixed["input_tokens_sum_reported"] == 0
    assert benchmark.summarize([])["schema_valid_rate"] is None


@pytest.mark.parametrize(
    "override",
    [
        {"extra_body": {key: "override"}}
        for key in (
            "model",
            "models",
            "route",
            "messages",
            "contents",
            "systemInstruction",
            "generationConfig",
            "temperature",
            "max_tokens",
            "max_completion_tokens",
            "stream",
            "response_format",
            "tools",
            "tool_choice",
            "functions",
            "function_call",
        )
    ]
    + [
        {"base_url": url}
        for url in (
            "ftp://provider.test",
            "relative/path",
            "https://user:secret@provider.test",
            "https://provider.test?key=secret",
            "https://provider.test#secret",
        )
    ]
    + [{"api_key": "secret"}, {"headers": {"Authorization": "secret"}}],
)
def test_config_rejects_overrides_unsafe_urls_and_inline_secrets(override):
    with pytest.raises(ValidationError):
        config(**override)


def test_missing_credentials(cases, monkeypatch):
    monkeypatch.delenv("BENCHMARK_TEST_MISSING_KEY", raising=False)
    for cfg in (config("gemini"), config(api_key_env="BENCHMARK_TEST_MISSING_KEY")):
        with pytest.raises(ValueError, match="api_key_env|Missing API key"):
            execute_mock(cfg, cases["basic-01"], lambda _: pytest.fail("must not send HTTP"))


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_empty_response_retains_usage_and_http_latency(provider, cases, monkeypatch):
    monkeypatch.setenv("BENCHMARK_TEST_KEY", "test-secret")
    cfg = config(provider, api_key_env="BENCHMARK_TEST_KEY")
    response = payload(provider, "")
    if provider == "gemini":
        response.pop("candidates")
        response["promptFeedback"] = {"blockReason": "SAFETY"}
    else:
        response["choices"][0]["message"] = {"content": None, "refusal": "Cannot comply"}
    row = execute_mock(cfg, cases["basic-01"], lambda _: httpx.Response(200, json=response))
    assert row["failure"] == "empty_response"
    assert row["http_status"] == 200
    assert row["input_tokens"] == 11 and row["total_tokens"] == 21
    summary = benchmark.summarize([row], wall_seconds=2.0)
    assert summary["request_failure_rate"] == 1
    assert summary["successful_response_p50_seconds"] == row["latency_seconds"]
    assert summary["successful_responses_per_second"] == 0.5


@pytest.mark.parametrize("provider", ["gemini", "openai"])
@pytest.mark.parametrize("broken_part", ["content", "usage", "tokens"])
def test_malformed_envelopes_are_recorded_not_raised(provider, broken_part, cases, monkeypatch):
    monkeypatch.setenv("BENCHMARK_TEST_KEY", "test-secret")
    cfg = config(provider, api_key_env="BENCHMARK_TEST_KEY")
    response = payload(provider, cases["basic-01"].expected_output.model_dump_json())
    if broken_part == "content":
        if provider == "openai":
            response["choices"][0]["message"] = None
        else:
            response["candidates"][0]["content"] = None
    elif broken_part == "usage":
        response["usage" if provider == "openai" else "usageMetadata"] = [1]
    else:
        if provider == "openai":
            response["usage"]["prompt_tokens"] = "11"
        else:
            response["usageMetadata"]["promptTokenCount"] = -1
    row = execute_mock(cfg, cases["basic-01"], lambda _: httpx.Response(200, json=response))
    assert row["failure"] == "provider_response_error"
    assert row["schema_valid"] is False
    assert row["http_status"] == 200


def test_cli_validation_errors_do_not_print_credentials(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.json"
    sentinel = "DO_NOT_LEAK_THIS_CREDENTIAL"
    config_path.write_text(
        json.dumps(
            [
                {
                    "name": "test",
                    "provider": "openai",
                    "model": "test",
                    "base_url": f"https://user:{sentinel}@provider.test",
                    "api_key": sentinel,
                }
            ]
        )
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "benchmark.py",
            "--config",
            str(config_path),
            "--output",
            str(tmp_path / "out"),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        benchmark.main()
    assert exc.value.code == 2
    assert sentinel not in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_full_runner_outputs_warmups_seeded_passes_and_exclusivity(tmp_path, cases, monkeypatch):
    selected = [cases[key] for key in ("basic-01", "multiple-01", "missing-01")]
    by_input = {case.user_input: case for case in selected}
    monkeypatch.setenv("BENCHMARK_TEST_KEY", "test-secret")
    configs = [config(), config("gemini", api_key_env="BENCHMARK_TEST_KEY")]
    requests = []

    def handler(request):
        body = json.loads(request.content)
        provider = "openai" if "messages" in body else "gemini"
        text = (
            body["messages"][1]["content"]
            if provider == "openai"
            else body["contents"][0]["parts"][0]["text"]
        )
        requests.append((provider, by_input[text].id))
        if len(requests) in (1, 9):
            return httpx.Response(429)
        return httpx.Response(
            200,
            json=payload(
                provider,
                by_input[text].expected_output.model_dump_json(),
                usage=False,
            ),
        )

    client_class = httpx.AsyncClient

    def client_factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        return client_class(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(benchmark.httpx, "AsyncClient", client_factory)
    orders = []
    for name in ("first", "repeat"):
        output = tmp_path / name
        requests.clear()
        asyncio.run(benchmark.run(configs, selected, output, 2, 2, 42, "test-cases-hash"))
        manifest = json.loads((output / "manifest.json").read_text())
        rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
        warmups = [json.loads(line) for line in (output / "warmups.jsonl").read_text().splitlines()]
        summaries = json.loads((output / "summary.json").read_text())
        assert manifest["complete"] is True
        assert manifest["measured_requests"] == len(rows) == 12
        assert manifest["case_count"] == 3
        assert (manifest["passes"], manifest["warmups"], manifest["seed"]) == (2, 2, 42)
        assert manifest["concurrency"] == 1
        assert manifest["cases_sha256"] == "test-cases-hash"
        assert manifest["schema_sha256"] == benchmark.fingerprint(
            json.dumps(benchmark.RESPONSE_SCHEMA, sort_keys=True),
        )
        assert manifest["prompt_sha256"] == benchmark.fingerprint(benchmark.SYSTEM_PROMPT)
        assert manifest["providers"] == [cfg.model_dump() for cfg in configs]
        assert "test-secret" not in (output / "manifest.json").read_text()
        assert len(warmups) == 4
        assert all(row["warmup"] and row["pass"] == 0 for row in warmups)
        assert sum(row["failure"] == "rate_limit" for row in warmups) == 2
        assert all(not row["warmup"] and row["schema_valid"] for row in rows)
        order = [(row["provider"], row["pass"], row["test_case_id"]) for row in rows]
        orders.append(order)
        for cfg in configs:
            for pass_index in (1, 2):
                expected = list(selected)
                random.Random(42 + pass_index).shuffle(expected)
                assert [
                    r["test_case_id"]
                    for r in rows
                    if r["provider"] == cfg.name and r["pass"] == pass_index
                ] == [case.id for case in expected]
        assert len(summaries) == 8
        for summary in summaries:
            assert summary["requests"] == (6 if summary["category"] == "overall" else 2)
            assert summary["complete_case_accuracy"] == summary["schema_valid_rate"] == 1
            assert summary["request_failure_rate"] == 0
            assert summary["total_tokens_mean"] is None
        with (output / "summary.csv").open(newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        assert len(csv_rows) == len(summaries)
        assert [r["category"] for r in csv_rows] == [r["category"] for r in summaries]
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        with pytest.raises(FileExistsError):
            asyncio.run(benchmark.run(configs, selected, output, 2, 2, 42, "changed"))
        assert {path.name: path.read_bytes() for path in output.iterdir()} == before
        assert len(requests) == 16
    assert orders[0] == orders[1]
