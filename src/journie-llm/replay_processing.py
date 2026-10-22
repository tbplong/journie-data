"""Offline retrospective processing; never calls providers or changes source artifacts."""

from __future__ import annotations

import argparse
import copy
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from benchmark import (
    add_processing,
    fingerprint,
    load_cases,
    matching_cases,
    summarize,
    summarize_processed,
    summary_groups,
    write_summaries,
)
from processing import DIAGNOSTICS_VERSION, PROCESSING_POLICIES, PROCESSING_VERSION, PROCESSORS
from prompts import PROMPT_VERSIONS, RESPONSE_SCHEMA
from pydantic import ValidationError
from schema import Extraction, score

PROCESSING_FIELDS = {
    "processed_output",
    "processed_metrics",
    "consistency_issues",
    "missing_fields_changed",
    "repairs",
    "postprocessing_seconds",
    "extraction_pipeline_latency_seconds",
}


def replay(
    run: Path,
    output: Path,
    cases: Path | None = None,
    processing: str = PROCESSING_VERSION,
) -> list[dict]:
    """Validate a complete source run, then write an exclusively created sibling report."""
    run, output = run.resolve(), output.resolve()
    if output == run or output.is_relative_to(run):
        raise ValueError("Output must not be the source run or a descendant of it")
    if output.exists():
        raise FileExistsError("Output directory already exists")

    manifest_bytes = (run / "manifest.json").read_bytes()
    results_bytes = (run / "results.jsonl").read_bytes()
    source = json.loads(manifest_bytes)
    cases = (cases or matching_cases(source.get("cases_sha256"))).resolve()
    if source.get("complete") is not True:
        raise ValueError("Source run must be complete")
    if not isinstance(source.get("system_prompt"), str) or fingerprint(
        source["system_prompt"]
    ) != source.get("prompt_sha256"):
        raise ValueError("Source prompt content does not match prompt hash")
    cases_hash = fingerprint(cases.read_bytes())
    if cases_hash != source.get("cases_sha256"):
        raise ValueError("Cases hash does not match source cases_sha256")
    schema_dump = json.dumps(RESPONSE_SCHEMA, sort_keys=True)
    schema_hash = fingerprint(schema_dump)
    if (
        schema_hash != source.get("schema_sha256")
        or json.dumps(source.get("schema"), sort_keys=True) != schema_dump
    ):
        raise ValueError("Current schema does not match source schema/hash; contract changed")
    gold = {case.id: case for case in load_cases(cases)}
    passes = source.get("passes")
    if type(passes) is not int or passes < 1 or source.get("case_count") != len(gold):
        raise ValueError("Source passes/case_count does not match supplied cases")
    configs = source.get("providers")
    if not isinstance(configs, list) or not configs:
        raise ValueError("Source providers must be a nonempty list")
    safe_configs = []
    for config in configs:
        if not isinstance(config, dict) or any(
            not isinstance(config.get(key), str) or not config[key]
            for key in ("name", "provider", "model")
        ):
            raise ValueError("Invalid source provider identity")
        safe_configs.append({key: config[key] for key in ("name", "provider", "model")})
    models = {config["name"]: config["model"] for config in safe_configs}
    if len(models) != len(safe_configs):
        raise ValueError("Source provider names must be unique")
    expected = {
        (provider, case_id, index)
        for provider in models
        for case_id in gold
        for index in range(1, passes + 1)
    }
    rows = [json.loads(line) for line in results_bytes.splitlines() if line.strip()]
    if len(rows) != len(expected) or source.get("measured_requests") != len(expected):
        raise ValueError("Incomplete source results: expected provider/case/pass count differs")
    inline = [isinstance(row, dict) and bool(PROCESSING_FIELDS.intersection(row)) for row in rows]
    if any(inline) and not all(inline):
        raise ValueError("Only some source results are already processed")
    # Inline processing is derived from raw fields; it is recomputed in the replay's copy only.
    rows = [
        {key: value for key, value in row.items() if key not in PROCESSING_FIELDS}
        if isinstance(row, dict)
        else row
        for row in rows
    ]
    seen = set()
    parsed_rows = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Source result must be an object")
        if row.get("warmup") is not False:
            raise ValueError("Warmup or missing warmup flag in measured results")
        provider, case_id, index = row.get("provider"), row.get("test_case_id"), row.get("pass")
        if not isinstance(provider, str) or provider not in models:
            raise ValueError("Unknown source result provider")
        if not isinstance(case_id, str) or case_id not in gold:
            raise ValueError("Unknown source result case")
        if type(index) is not int or (provider, case_id, index) not in expected:
            raise ValueError("Unexpected source pass")
        identity = (provider, case_id, index)
        if identity in seen:
            raise ValueError("Duplicate provider/case/pass in source results")
        seen.add(identity)
        if row.get("model") != models[provider]:
            raise ValueError("Source result model does not match provider config")
        case = gold[case_id]
        if row.get("category") != case.category:
            raise ValueError("Source result category differs from supplied case")
        if type(row.get("schema_valid")) is not bool:
            raise ValueError("Invalid source schema_valid flag")
        if type(row.get("json_parse_valid")) is not bool:
            raise ValueError("Invalid source json_parse_valid flag")
        for field in ("latency_seconds", "validation_seconds"):
            value = row.get(field)
            if field == "validation_seconds" and value is None:
                continue
            if (
                value is None
                or type(value) not in (int, float)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"Invalid source {field}: finite nonnegative time required")
        for field in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens"):
            if field not in row or (
                row[field] is not None and (type(row[field]) is not int or row[field] < 0)
            ):
                raise ValueError(f"Invalid source {field}: nonnegative integer or null required")
        status = row.get("http_status")
        if "http_status" not in row or (
            status is not None and (type(status) is not int or not 100 <= status <= 599)
        ):
            raise ValueError("Invalid source http_status")
        if row.get("failure") is not None and not isinstance(row["failure"], str):
            raise ValueError("Invalid source failure type")
        validation = row.get("validation_result")
        if isinstance(validation, dict) and validation.get("valid") is not row["schema_valid"]:
            raise ValueError("schema_valid flag disagrees with stored validation_result")
        parsed = None
        if row["schema_valid"]:
            if row.get("json_parse_valid") is not True or row.get("failure") is not None:
                raise ValueError("schema_valid flag disagrees with stored failure/JSON status")
            try:
                parsed = Extraction.model_validate(row.get("parsed_output"))
            except ValidationError:
                raise ValueError("schema_valid flag disagrees with stored parsed_output") from None
            raw_response = row.get("raw_response")
            if not isinstance(raw_response, str):
                raise ValueError("schema_valid flag disagrees with stored raw_response")
            try:
                decoded = json.loads(raw_response)
            except (ValueError, TypeError):
                raise ValueError("schema_valid flag disagrees with stored raw_response") from None
            if json.dumps(decoded, sort_keys=True) != json.dumps(
                row["parsed_output"], sort_keys=True
            ):
                raise ValueError("Saved raw_response and parsed_output disagree")
        # Invalid outputs stay invalid: no parsing, repair, or reclassification during replay.
        if json.dumps(score(parsed, case.expected_output), sort_keys=True) != json.dumps(
            row.get("metrics"),
            sort_keys=True,
        ):
            raise ValueError("Raw metrics differ from current scorer/gold; incompatible artifact")
        parsed_rows.append(parsed)
    if seen != expected:
        raise ValueError("Source providers do not have the same complete case/pass combinations")

    summary_bytes = (run / "summary.json").read_bytes()
    source_summaries = json.loads(summary_bytes)
    wall_times = {}
    for summary in source_summaries:
        if summary.get("category") == "overall":
            name = summary.get("provider")
            if name not in models or name in wall_times or summary.get("model") != models[name]:
                raise ValueError("Source overall summaries have incompatible provider/model IDs")
            wall = summary.get("measured_wall_seconds")
            if wall is not None and (
                type(wall) not in (int, float) or not math.isfinite(wall) or wall <= 0
            ):
                raise ValueError("Source wall time must be a finite positive value or null")
            wall_times[name] = wall
    if set(wall_times) != set(models):
        raise ValueError("Source overall summaries missing provider wall times")

    enhanced = copy.deepcopy(rows)
    for row, parsed in zip(enhanced, parsed_rows, strict=True):
        row.setdefault("validation_seconds", None)
        add_processing(row, parsed, gold[row["test_case_id"]].expected_output, processing)
        # Today's processing time cannot reconstruct the historical extraction pipeline.
        row["extraction_pipeline_latency_seconds"] = None

    raw_summaries, processed_summaries = [], []
    report = [
        "# Offline Processing Replay",
        "",
        "Retrospective deterministic processing of saved outputs, not first-pass accuracy or "
        "a prompt improvement. No network requests, new tokens, or new model cost.",
        "",
        "Original HTTP latency/tokens are retained. Historical pipeline latency is unknown; "
        "postprocessing time was measured now. Recorded validation time is retained where present.",
        "",
        "Missing destination follows the null scalar even for destination ambiguity; diagnostics "
        "do not resolve intent, repair facts, or establish grounding. "
        "Gold is used only for scoring.",
        "",
        "| Provider / Model | Requests | Original exact | Processed exact | Changed outputs | "
        "Gained exact | Lost exact | Diagnostics checked |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for config in safe_configs:
        name, model = config["name"], config["model"]
        raw_group = [row for row in rows if row["provider"] == name]
        group = [row for row in enhanced if row["provider"] == name]
        groups = {"overall": set(gold), **summary_groups(list(gold.values()))}
        for category, ids in groups.items():
            selected_raw = [row for row in raw_group if row["test_case_id"] in ids]
            selected = [row for row in group if row["test_case_id"] in ids]
            wall = wall_times[name] if category == "overall" else None
            summary_identity = {"provider": name, "model": model, "category": category}
            raw_summaries.append({**summary_identity, **summarize(selected_raw, wall)})
            processed_summaries.append(
                {
                    **summary_identity,
                    **summarize_processed(selected, wall),
                    "experiment": "offline_replay",
                    "processing_version": processing,
                    "diagnostics_version": DIAGNOSTICS_VERSION,
                }
            )
        original = [row["metrics"]["complete_case_accuracy"] for row in group]
        processed = [row["processed_metrics"]["complete_case_accuracy"] for row in group]
        changed = sum(
            row["processed_output"] is not None and row["processed_output"] != row["parsed_output"]
            for row in group
        )
        gained = sum(not old and bool(new) for old, new in zip(original, processed, strict=True))
        lost = sum(bool(old) and not new for old, new in zip(original, processed, strict=True))
        checked = sum(row["consistency_issues"] is not None for row in group)
        label = f"{name} / {model}".replace("|", "\\|").replace("\n", " ")
        report.append(
            f"| {label} | {len(group)} | {sum(original)} | {sum(processed)} | "
            f"{changed} | {gained} | {lost} | {checked} |"
        )
    repaired = [row for row in enhanced if set(row["repairs"] or []) - {"missing_fields"}]
    report += ["", "## Repaired Outputs", ""]
    report += (
        [
            "Every output changed beyond missing_fields; gold is used only for the exact columns.",
            "",
            "| Provider | Case | Pass | Repairs | Exact before | Exact after |",
            "| --- | --- | ---: | --- | ---: | ---: |",
        ]
        + [
            f"| {row['provider']} | {row['test_case_id']} | {row['pass']} | "
            f"{', '.join(dict.fromkeys(row['repairs']))} | "
            f"{row['metrics']['complete_case_accuracy']} | "
            f"{row['processed_metrics']['complete_case_accuracy']} |"
            for row in repaired
        ]
        if repaired
        else ["None."]
    )
    variant = source.get("prompt_variant", "legacy_baseline")
    if variant not in PROMPT_VERSIONS and variant != "legacy_baseline":
        raise ValueError("Unknown source prompt variant")
    prompt_hash = source.get("prompt_sha256")
    if (
        not isinstance(prompt_hash, str)
        or len(prompt_hash) != 64
        or any(char not in "0123456789abcdef" for char in prompt_hash)
    ):
        raise ValueError("Invalid source prompt hash")
    manifest = {
        "experiment": "offline_replay",
        "complete": True,
        "created_at": datetime.now(UTC).isoformat(),
        "source_manifest_sha256": fingerprint(manifest_bytes),
        "source_results_sha256": fingerprint(results_bytes),
        "cases_sha256": cases_hash,
        "source_summary_sha256": fingerprint(summary_bytes),
        "schema_sha256": schema_hash,
        "schema": RESPONSE_SCHEMA,
        "prompt_sha256": prompt_hash,
        "prompt_variant": variant,
        "prompt_version": PROMPT_VERSIONS.get(variant),
        "processing_version": processing,
        "diagnostics_version": DIAGNOSTICS_VERSION,
        "processing_policy": PROCESSING_POLICIES[processing],
        "annotation_status": source.get("annotation_status", "draft; human review required"),
        "providers": safe_configs,
        "passes": passes,
        "case_count": len(gold),
        "measured_requests": len(rows),
        "new_model_requests": 0,
        "new_tokens": 0,
        "validation_seconds": None,
        "extraction_pipeline_latency_seconds": None,
        "timing_policy": "historical validation retained if recorded; pipeline unknown; "
        "postprocessing measured during replay",
    }
    # All source validation and report computation precedes exclusive directory creation.
    output.mkdir(parents=True, exist_ok=False)
    with (output / "results.jsonl").open("x", encoding="utf-8") as handle:
        for row in enhanced:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_summaries(output, raw_summaries)
    write_summaries(output, processed_summaries, "processed_summary")
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return enhanced


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", type=Path, required=True, help="Complete source run, e.g. data/run1"
    )
    parser.add_argument(
        "--cases", type=Path, help="Gold JSONL; defaults to the dataset matching the run's hash"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New directory outside source run; e.g. data/run1-processing",
    )
    parser.add_argument("--processing", choices=tuple(PROCESSORS), default=PROCESSING_VERSION)
    args = parser.parse_args()
    try:
        replay(args.run, args.output, args.cases, args.processing)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
