"""Run from the repository: python src/journie-llm/benchmark.py --help."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import random
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from processing import (
    DIAGNOSTICS_VERSION,
    PROCESSING_VERSION,
    consistency_issues,
    process_extraction,
)
from prompts import PROMPT_VERSIONS, RESPONSE_SCHEMA, PromptVariant, build_prompt
from providers import GeminiProvider, LLMProvider, OpenAICompatibleProvider, ProviderConfig
from pydantic import ValidationError, model_validator
from schema import Extraction, StrictModel, canonical, score

HERE = Path(__file__).resolve().parent
SYSTEM_PROMPT = build_prompt()


class Case(StrictModel):
    id: str
    category: str
    user_input: str
    expected_output: Extraction
    expected_constraints: dict[str, list[dict[str, str]]]
    expected_missing_fields: list[str]
    expected_conflicts: list[str]

    @model_validator(mode="after")
    def consistent_annotations(self) -> Case:
        gold = self.expected_output.model_dump()
        expected = {k: gold[k] for k in ("hard_constraints", "soft_constraints")}
        if canonical(self.expected_constraints) != canonical(expected):
            raise ValueError("expected_constraints differs from expected_output")
        if self.expected_missing_fields != gold["missing_fields"]:
            raise ValueError("expected_missing_fields differs from expected_output")
        if self.expected_conflicts != gold["possible_conflicts"]:
            raise ValueError("expected_conflicts differs from expected_output")
        return self


def load_cases(path: Path) -> list[Case]:
    cases = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(Case.model_validate_json(line))
        except (ValueError, ValidationError) as exc:
            raise ValueError(f"Invalid case at line {number}: {exc}") from exc
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("Cases must be nonempty with unique IDs")
    return cases


def fingerprint(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


async def execute(
    provider: LLMProvider,
    config: ProviderConfig,
    case: Case,
    pass_index: int,
    warmup: bool = False,
    prompt_variant: PromptVariant = "baseline",
) -> dict:
    system_prompt = build_prompt(prompt_variant, structured_output=config.structured_output)
    row: dict[str, Any] = {
        "provider": config.name,
        "model": config.model,
        "test_case_id": case.id,
        "category": case.category,
        "pass": pass_index,
        "warmup": warmup,
        "started_at": datetime.now(UTC).isoformat(),
        "raw_response": None,
        "parsed_output": None,
        "json_parse_valid": False,
        "schema_valid": False,
        "validation_result": None,
        "failure": None,
        "http_status": None,
        "resolved_model": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "reasoning_tokens": None,
        "finish_reason": None,
        "resource_usage": None,
        "prompt_variant": prompt_variant,
        "prompt_version": PROMPT_VERSIONS[prompt_variant],
    }
    parsed = None
    started = time.perf_counter()
    try:
        result = await provider.generate(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": case.user_input},
            ],
            RESPONSE_SCHEMA,
        )
        row["raw_response"] = result.text
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "reasoning_tokens",
            "finish_reason",
            "http_status",
            "resolved_model",
        ):
            row[key] = getattr(result, key)
        if not result.text:
            row["failure"] = "empty_response"
    except httpx.TimeoutException:
        row["failure"] = "timeout"
    except httpx.HTTPStatusError as exc:
        row["http_status"] = exc.response.status_code
        row["failure"] = "rate_limit" if exc.response.status_code == 429 else "http_error"
    except httpx.RequestError:
        row["failure"] = "network_error"
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        row["failure"] = "provider_response_error"
        row["http_status"] = 200
    # Stop the clock before JSON parsing and scoring: this is complete-response HTTP latency.
    row["latency_seconds"] = time.perf_counter() - started
    validation_started = time.perf_counter()
    if row["failure"] is None:
        try:
            raw = json.loads(row["raw_response"])
            row["json_parse_valid"] = True
            row["parsed_output"] = raw
            parsed = Extraction.model_validate(raw)
            row["schema_valid"] = True
            row["validation_result"] = {"valid": True, "errors": []}
        except json.JSONDecodeError as exc:
            row["validation_result"] = {"valid": False, "errors": [str(exc)]}
        except ValidationError as exc:
            row["validation_result"] = {
                "valid": False,
                "errors": exc.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                ),
            }
    row["validation_seconds"] = time.perf_counter() - validation_started
    row["metrics"] = score(parsed, case.expected_output)
    add_processing(row, parsed, case.expected_output)
    return row


def add_processing(row: dict, parsed: Extraction | None, expected: Extraction) -> None:
    """Keep every raw result field intact; processing never receives the gold output."""
    started = time.perf_counter()
    processed = process_extraction(parsed) if parsed is not None else None
    row["processed_output"] = processed.model_dump() if processed is not None else None
    row["consistency_issues"] = consistency_issues(processed) if processed is not None else None
    row["missing_fields_changed"] = (
        parsed is not None
        and processed is not None
        and (parsed.missing_fields != processed.missing_fields)
    )
    row["postprocessing_seconds"] = time.perf_counter() - started
    # Historical replay cannot reconstruct the original JSON/schema-validation duration.
    row["extraction_pipeline_latency_seconds"] = (
        row["latency_seconds"] + row["validation_seconds"] + row["postprocessing_seconds"]
        if row.get("validation_seconds") is not None
        else None
    )
    # Both gold-scoring operations are outside the extraction-pipeline latency measure.
    row["processed_metrics"] = score(processed, expected)


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (
        index - lower
    )


def summarize(rows: list[dict], wall_seconds: float | None = None) -> dict:
    n = len(rows)
    valid = [row for row in rows if row["schema_valid"]]
    successful = [
        row["latency_seconds"]
        for row in rows
        if row["http_status"] is not None and 200 <= row["http_status"] < 300
    ]
    summary: dict[str, Any] = {
        "requests": n,
        "json_parse_valid_rate": ratio(sum(r["json_parse_valid"] for r in rows), n),
        "schema_valid_rate": ratio(len(valid), n),
        "field_accuracy": ratio(sum(r["metrics"]["field_accuracy"] for r in rows), n),
        "complete_case_accuracy": ratio(
            sum(r["metrics"]["complete_case_accuracy"] for r in rows), n
        ),
        "hallucination_rate_valid_outputs": ratio(
            sum(r["metrics"]["hallucinated_case"] for r in valid), len(valid)
        ),
        "request_failure_rate": ratio(sum(r["failure"] is not None for r in rows), n),
        "timeout_rate": ratio(sum(r["failure"] == "timeout" for r in rows), n),
        "rate_limit_rate": ratio(sum(r["failure"] == "rate_limit" for r in rows), n),
        "successful_response_p50_seconds": percentile(successful, 0.50),
        "successful_response_p95_seconds": percentile(successful, 0.95),
        "all_attempts_p50_seconds": percentile([r["latency_seconds"] for r in rows], 0.50),
        "all_attempts_p95_seconds": percentile([r["latency_seconds"] for r in rows], 0.95),
        "measured_wall_seconds": wall_seconds,
        "attempts_per_second": ratio(n, wall_seconds) if wall_seconds is not None else None,
        "successful_responses_per_second": ratio(len(successful), wall_seconds)
        if wall_seconds is not None
        else None,
    }
    for prefix in ("populated_field", "empty_field"):
        summary[f"{prefix}_accuracy"] = ratio(
            sum(r["metrics"][f"{prefix}_correct"] for r in rows),
            sum(r["metrics"][f"{prefix}_total"] for r in rows),
        )
    for metric in ("constraints", "classified_constraints", "missing_fields", "possible_conflicts"):
        tp, fp, fn = (sum(r["metrics"][metric][key] for r in rows) for key in ("tp", "fp", "fn"))
        summary.update(
            {
                f"{metric}_tp": tp,
                f"{metric}_fp": fp,
                f"{metric}_fn": fn,
                f"{metric}_precision": ratio(tp, tp + fp),
                f"{metric}_recall": ratio(tp, tp + fn),
                f"{metric}_f1": ratio(2 * tp, 2 * tp + fp + fn),
            }
        )
    summary["hard_soft_accuracy_matched_constraints"] = ratio(
        summary["classified_constraints_tp"],
        summary["constraints_tp"],
    )
    for key in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens"):
        reported = [r[key] for r in rows if r[key] is not None]
        summary[f"{key}_reported_requests"] = len(reported)
        summary[f"{key}_mean"] = statistics.mean(reported) if reported else None
        summary[f"{key}_sum_reported"] = sum(reported) if reported else None
    return summary


def summarize_processed(rows: list[dict], wall_seconds: float | None = None) -> dict:
    summary = summarize(
        [{**row, "metrics": row["processed_metrics"]} for row in rows], wall_seconds
    )
    checked = [row for row in rows if row["consistency_issues"] is not None]
    summary["consistency_checked_requests"] = len(checked)
    summary["missing_fields_changed_rate_valid_outputs"] = ratio(
        sum(row["missing_fields_changed"] for row in checked),
        len(checked),
    )
    for severity in ("error", "review"):
        summary[f"consistency_{severity}_rate_checked_outputs"] = ratio(
            sum(
                any(issue["severity"] == severity for issue in row["consistency_issues"])
                for row in checked
            ),
            len(checked),
        )
    summary["consistency_issue_counts"] = dict(
        Counter(issue["code"] for row in checked for issue in row["consistency_issues"])
    )
    for key in ("postprocessing_seconds", "extraction_pipeline_latency_seconds"):
        values = [row[key] for row in rows if row[key] is not None]
        summary[f"{key}_reported_requests"] = len(values)
        summary[f"{key}_p50"] = percentile(values, 0.50)
        summary[f"{key}_p95"] = percentile(values, 0.95)
    return summary


def write_summaries(output: Path, summaries: list[dict], stem: str = "summary") -> None:
    (output / f"{stem}.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    if summaries:
        with (output / f"{stem}.csv").open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
            writer.writeheader()
            writer.writerows(
                {
                    key: json.dumps(value) if isinstance(value, dict) else value
                    for key, value in summary.items()
                }
                for summary in summaries
            )


async def run(
    configs: list[ProviderConfig],
    cases: list[Case],
    output: Path,
    passes: int,
    warmups: int,
    seed: int,
    cases_hash: str,
    prompt_variant: PromptVariant = "baseline",
) -> None:
    system_prompt = build_prompt(
        prompt_variant, structured_output=all(config.structured_output for config in configs)
    )
    # Exclusive directory creation prevents accidental destruction of earlier measurements.
    async with httpx.AsyncClient(follow_redirects=False) as client:
        providers = [
            (
                config,
                GeminiProvider(client, config)
                if config.provider == "gemini"
                else OpenAICompatibleProvider(client, config),
            )
            for config in configs
        ]
        output.mkdir(parents=True, exist_ok=False)
        manifest = {
            "started_at": datetime.now(UTC).isoformat(),
            "passes": passes,
            "warmups": warmups,
            "seed": seed,
            "concurrency": 1,
            "case_count": len(cases),
            "cases_sha256": cases_hash,
            "schema_sha256": fingerprint(json.dumps(RESPONSE_SCHEMA, sort_keys=True)),
            "prompt_sha256": fingerprint(system_prompt),
            "system_prompt": system_prompt,
            "prompt_variant": prompt_variant,
            "prompt_version": PROMPT_VERSIONS[prompt_variant],
            "processing_version": PROCESSING_VERSION,
            "diagnostics_version": DIAGNOSTICS_VERSION,
            "processing_policy": "only missing_fields changed; diagnostics never repair",
            "schema": RESPONSE_SCHEMA,
            "providers": [c.model_dump() for c in configs],
            "annotation_status": "draft; human review required",
            "resource_usage": "not measured remotely",
            "complete": False,
        }
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        collected: list[dict] = []
        summaries: list[dict] = []
        processed_summaries: list[dict] = []
        completed = False
        try:
            with (
                (output / "results.jsonl").open("x", encoding="utf-8") as results,
                (output / "warmups.jsonl").open("x", encoding="utf-8") as warming,
            ):
                for config, provider in providers:
                    print(
                        f"{config.name}: {config.model}; warming up ({warmups} requests)",
                        flush=True,
                    )
                    for index in range(warmups):
                        row = await execute(
                            provider, config, cases[index % len(cases)], 0, True, prompt_variant
                        )
                        warming.write(json.dumps(row, ensure_ascii=False) + "\n")
                        warming.flush()
                        if row["failure"]:
                            print(f"  warm-up failure: {row['failure']}", flush=True)
                        await asyncio.sleep(config.pacing_seconds)
                    started = time.perf_counter()
                    provider_rows = []
                    try:
                        for pass_index in range(1, passes + 1):
                            ordered = list(cases)
                            random.Random(seed + pass_index).shuffle(ordered)
                            for index, case in enumerate(ordered):
                                row = await execute(
                                    provider,
                                    config,
                                    case,
                                    pass_index,
                                    prompt_variant=prompt_variant,
                                )
                                collected.append(row)
                                provider_rows.append(row)
                                results.write(json.dumps(row, ensure_ascii=False) + "\n")
                                results.flush()
                                status = row["failure"] or (
                                    "valid" if row["schema_valid"] else "invalid"
                                )
                                print(
                                    f"{config.name} pass {pass_index}/{passes} "
                                    f"case {index + 1}/{len(cases)}: "
                                    f"{status} "
                                    f"{row['latency_seconds']:.2f}s",
                                    flush=True,
                                )
                                if index + 1 < len(cases) or pass_index < passes:
                                    await asyncio.sleep(config.pacing_seconds)
                    finally:
                        wall_seconds = time.perf_counter() - started
                        summaries.append(
                            {
                                "provider": config.name,
                                "model": config.model,
                                "category": "overall",
                                **summarize(provider_rows, wall_seconds),
                            }
                        )
                        processed_summaries.append(
                            {
                                "provider": config.name,
                                "model": config.model,
                                "category": "overall",
                                **summarize_processed(provider_rows, wall_seconds),
                            }
                        )
                    for category in sorted({case.category for case in cases}):
                        summaries.append(
                            {
                                "provider": config.name,
                                "model": config.model,
                                "category": category,
                                **summarize(
                                    [r for r in provider_rows if r["category"] == category]
                                ),
                            }
                        )
                        processed_summaries.append(
                            {
                                "provider": config.name,
                                "model": config.model,
                                "category": category,
                                **summarize_processed(
                                    [r for r in provider_rows if r["category"] == category]
                                ),
                            }
                        )
            completed = True
        finally:
            # Persist summaries even if the user interrupts; completed=false marks partial runs.
            write_summaries(output, summaries)
            write_summaries(output, processed_summaries, "processed_summary")
            manifest.update(
                complete=completed,
                finished_at=datetime.now(UTC).isoformat(),
                measured_requests=len(collected),
            )
            (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> None:
    load_dotenv(HERE.parents[1] / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="JSON array of provider configs")
    parser.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    parser.add_argument("--output", type=Path, required=True, help="New directory; must not exist")
    parser.add_argument(
        "--provider", action="append", help="Select config name; repeat to select more"
    )
    parser.add_argument("--passes", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--limit", type=int, help="Smoke-test only the first N cases")
    parser.add_argument("--prompt-variant", choices=tuple(PROMPT_VERSIONS), default="baseline")
    args = parser.parse_args()
    if args.passes < 1 or args.warmups < 0 or (args.limit is not None and args.limit < 1):
        parser.error("passes and limit must be positive; warmups must be nonnegative")
    try:
        raw_config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(raw_config, list):
            raise ValueError("config must be a JSON array")
        configs = [ProviderConfig.model_validate(item) for item in raw_config]
        if len({c.name for c in configs}) != len(configs):
            raise ValueError("Provider names must be unique")
        if args.provider:
            if set(args.provider) - {c.name for c in configs}:
                raise ValueError("Unknown --provider name")
            configs = [c for c in configs if c.name in args.provider]
        if not configs:
            raise ValueError("No providers selected")
        cases = load_cases(args.cases)
        if args.limit is not None:
            cases = cases[: args.limit]
        asyncio.run(
            run(
                configs,
                cases,
                args.output,
                args.passes,
                args.warmups,
                args.seed,
                fingerprint(args.cases.read_bytes()),
                args.prompt_variant,
            )
        )
    except ValidationError as exc:
        parser.error(
            json.dumps(
                exc.errors(
                    include_input=False,
                    include_context=False,
                    include_url=False,
                )
            )
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
