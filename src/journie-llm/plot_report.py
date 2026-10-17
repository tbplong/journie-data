"""Export the six selected supervisor-report figures without calling any model."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from processing import PROCESSING_VERSION, process_extraction
from schema import Extraction, score

CATEGORIES = ("basic", "multiple", "missing", "conflicting", "noisy", "adversarial")
LABELS = {
    "gemini": "Gemini\n3.5 Flash Lite",
    "gemma": "Cloud Gemma\n4 26B A4B",
    "lmstudio-gemma": "LM Studio Gemma\n4 E4B Q4",
}
COLORS = ("#2463A0", "#C56535", "#168477", "#745C96")
OUTCOMES = (
    "Request failure",
    "Invalid JSON",
    "Invalid schema",
    "Valid, not exact",
    "Fully correct",
)
OUTCOME_COLORS = ("#9B3443", "#DB7948", "#EBC76B", "#A7B9CC", "#218575")


def aggregate(rows: list[dict], metrics_key: str = "metrics") -> dict:
    """Use all attempts for quality; condition hallucination only on valid outputs."""
    if not rows:
        raise ValueError("Cannot aggregate an empty group")
    outcomes: Counter[str] = Counter(dict.fromkeys(OUTCOMES, 0))
    for row in rows:
        if row["failure"] is not None:
            outcome = OUTCOMES[0]
        elif not row["json_parse_valid"]:
            outcome = OUTCOMES[1]
        elif not row["schema_valid"]:
            outcome = OUTCOMES[2]
        elif not row[metrics_key]["complete_case_accuracy"]:
            outcome = OUTCOMES[3]
        else:
            outcome = OUTCOMES[4]
        outcomes[outcome] += 1
    valid = [row for row in rows if row["schema_valid"]]
    latencies = [row["latency_seconds"] for row in rows]
    if not all(math.isfinite(value) and value > 0 for value in latencies):
        raise ValueError("Latency must be finite and positive for logarithmic plots")
    result = {
        "requests": len(rows),
        "unique_cases": len({r["test_case_id"] for r in rows}),
        "outcomes": dict(outcomes),
        "schema_valid_count": len(valid),
        "schema_valid_rate": len(valid) / len(rows),
        "complete_case_accuracy": outcomes[OUTCOMES[4]] / len(rows),
        "hallucinated_count": sum(row[metrics_key]["hallucinated_case"] for row in valid),
        "hallucination_rate": (
            sum(row[metrics_key]["hallucinated_case"] for row in valid) / len(valid)
        )
        if valid
        else None,
        "p50_seconds": statistics.median(latencies),
        "p95_seconds": statistics.quantiles(latencies, n=100, method="inclusive")[94]
        if len(latencies) > 1
        else latencies[0],
        "sorted_latency_seconds": sorted(latencies),
    }
    for group in ("populated_field", "empty_field"):
        correct = sum(row[metrics_key][f"{group}_correct"] for row in rows)
        total = sum(row[metrics_key][f"{group}_total"] for row in rows)
        result[f"{group}_correct"] = correct
        result[f"{group}_total"] = total
        result[f"{group}_accuracy"] = correct / total if total else None
    missing = [row[metrics_key]["missing_fields"] for row in rows]
    correct_missing = sum(
        row["schema_valid"] and item["fp"] == item["fn"] == 0
        for row, item in zip(rows, missing, strict=True)
    )
    tp, fp, fn = (sum(item[key] for item in missing) for key in ("tp", "fp", "fn"))
    result["missing_fields_correct_count"] = correct_missing
    result["missing_fields_exact_accuracy"] = correct_missing / len(rows)
    result["missing_fields_f1"] = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return result


def load_run(run: Path, processed_run: Path | None = None, cases: Path | None = None) -> dict:
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if not manifest["complete"]:
        raise ValueError("Report requires a completed run")
    rows = [
        json.loads(line)
        for line in (run / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows or not manifest["providers"]:
        raise ValueError("Report requires measured attempts and configured models")
    if any(row["warmup"] for row in rows):
        raise ValueError("Measured results unexpectedly contain warm-ups")
    keys = [(r["provider"], r["test_case_id"], r["pass"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate provider/case/pass observations")
    sources = {
        name: hashlib.sha256((run / name).read_bytes()).hexdigest()
        for name in ("manifest.json", "results.jsonl", "summary.json")
    }
    cases = cases or Path(__file__).with_name("cases.jsonl")
    cases_hash = hashlib.sha256(cases.read_bytes()).hexdigest()
    if cases_hash != manifest["cases_sha256"]:
        raise ValueError("Gold case-file hash differs from run; supply the matching --cases file")
    case_records = [
        json.loads(line) for line in cases.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    try:
        gold = {
            case["id"]: Extraction.model_validate(case["expected_output"]) for case in case_records
        }
        if len(gold) != len(case_records):
            raise ValueError("Duplicate gold case IDs")
        gold_categories = {case["id"]: case["category"] for case in case_records}
        for row in rows:
            if (
                type(row["schema_valid"]) is not bool
                or type(row["json_parse_valid"]) is not bool
                or (
                    row["schema_valid"]
                    and (not row["json_parse_valid"] or row["failure"] is not None)
                )
                or (row["failure"] is not None and row["json_parse_valid"])
                or row["category"] != gold_categories[row["test_case_id"]]
            ):
                raise ValueError("Source flags or case category are inconsistent")
            if row["schema_valid"] and json.dumps(
                json.loads(row["raw_response"]), sort_keys=True
            ) != (json.dumps(row["parsed_output"], sort_keys=True)):
                raise ValueError("Raw response and parsed output disagree")
            actual = (
                Extraction.model_validate(row["parsed_output"]) if row["schema_valid"] else None
            )
            if row["metrics"] != score(actual, gold[row["test_case_id"]]):
                raise ValueError("Raw row metrics differ from gold rescoring")
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            "Raw results or matching gold annotations failed scoring validation"
        ) from exc
    warnings = []
    if manifest.get("measured_requests") != len(rows):
        warnings.append(
            f"Manifest reports {manifest.get('measured_requests')} measured requests; "
            f"results contain {len(rows)}. Figures use recorded, complete-grid attempts."
        )
    inline = ["processed_metrics" in row for row in rows]
    if any(inline) and not all(inline):
        raise ValueError("Only some measured rows contain processed metrics")
    if processed_run is not None and any(inline):
        raise ValueError("Run already contains processed metrics; omit --processed-run")
    processed_rows = rows if rows and all(inline) else None
    processed_sources: dict[str, str] = {}
    processing_origin = None
    processed_summary = None
    if processed_run is not None:
        processed_manifest = json.loads(
            (processed_run / "manifest.json").read_text(encoding="utf-8")
        )
        if processed_manifest.get("complete") is not True:
            raise ValueError("Processed replay must be complete")
        for name, source_hash in sources.items():
            field = f"source_{name.removesuffix('.jsonl').removesuffix('.json')}_sha256"
            if processed_manifest.get(field) != source_hash:
                raise ValueError(f"Processed replay does not belong to this source {name}")
        for field in ("cases_sha256", "schema_sha256", "prompt_sha256"):
            if processed_manifest.get(field) != manifest.get(field):
                raise ValueError(f"Raw and processed {field} differ")
        processed_rows = [
            json.loads(line)
            for line in (processed_run / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        paired = {
            (row["provider"], row["test_case_id"], row["pass"]): row for row in processed_rows
        }
        if len(paired) != len(processed_rows) or set(paired) != set(keys):
            raise ValueError("Raw and processed provider/case/pass grids differ")
        processed_rows = [paired[key] for key in keys]
        for raw, processed in zip(rows, processed_rows, strict=True):
            original = {key: processed[key] for key in raw}
            if json.dumps(raw, sort_keys=True) != json.dumps(original, sort_keys=True):
                raise ValueError("Processed replay changed an original raw field")
        processing_origin = "offline replay"
        processing_version = processed_manifest.get("processing_version")
        processed_sources = {
            name: hashlib.sha256((processed_run / name).read_bytes()).hexdigest()
            for name in ("manifest.json", "results.jsonl", "processed_summary.json")
        }
        processed_summary = json.loads(
            (processed_run / "processed_summary.json").read_text(encoding="utf-8")
        )
    elif processed_rows is not None:
        processing_origin = "recorded inline"
        processing_version = manifest.get("processing_version")
        processed_summary = json.loads((run / "processed_summary.json").read_text(encoding="utf-8"))
        sources["processed_summary.json"] = hashlib.sha256(
            (run / "processed_summary.json").read_bytes()
        ).hexdigest()
    if processed_rows is not None:
        if processing_version != PROCESSING_VERSION:
            raise ValueError(
                "Unsupported processing version: cannot assume missing-fields-only changes"
            )
        for raw, processed in zip(rows, processed_rows, strict=True):
            if not raw["schema_valid"]:
                if (
                    processed["processed_output"] is not None
                    or processed["processed_metrics"] != raw["metrics"]
                ):
                    raise ValueError("Processing must not repair or credit invalid outputs")
                continue
            try:
                extraction = Extraction.model_validate(raw["parsed_output"])
            except ValueError:
                raise ValueError("Schema-valid source row has invalid parsed_output") from None
            expected = process_extraction(extraction).model_dump()
            if processed["processed_output"] != expected:
                raise ValueError(
                    "Processed output differs from deterministic missing-field processing"
                )
            before, after = raw["metrics"], processed["processed_metrics"]
            if {
                key: value
                for key, value in before.items()
                if key
                not in (
                    "missing_fields",
                    "complete_case_accuracy",
                )
            } != {
                key: value
                for key, value in after.items()
                if key
                not in (
                    "missing_fields",
                    "complete_case_accuracy",
                )
            }:
                raise ValueError(
                    "Missing-field processing unexpectedly changed other quality metrics"
                )
            exact = all(after["field_matches"].values()) and all(
                after[key]["fp"] == after[key]["fn"] == 0
                for key in (
                    "classified_constraints",
                    "missing_fields",
                    "possible_conflicts",
                )
            )
            if after["complete_case_accuracy"] != int(exact):
                raise ValueError("Processed complete-case score disagrees with component metrics")
            if after != score(Extraction.model_validate(expected), gold[raw["test_case_id"]]):
                raise ValueError("Processed row metrics differ from gold rescoring")
    models = []
    reference = None
    for config in manifest["providers"]:
        name = config["name"]
        selected = [row for row in rows if row["provider"] == name]
        processed_selected = (
            [row for row in processed_rows if row["provider"] == name]
            if processed_rows is not None
            else None
        )
        combinations = {(r["test_case_id"], r["pass"]) for r in selected}
        if (
            len(selected) != manifest["case_count"] * manifest["passes"]
            or len({r["test_case_id"] for r in selected}) != manifest["case_count"]
            or {r["pass"] for r in selected} != set(range(1, manifest["passes"] + 1))
        ):
            raise ValueError(f"Incomplete observations for {name}")
        if reference is not None and reference != combinations:
            raise ValueError("Models did not receive the same case/pass combinations")
        reference = combinations
        if {r["model"] for r in selected} != {config["model"]}:
            raise ValueError(f"Model IDs disagree with manifest for {name}")
        models.append(
            {
                "provider": name,
                "label": LABELS.get(name, name),
                "model": config["model"],
                "resolved_models": sorted(
                    {r["resolved_model"] for r in selected if r["resolved_model"]}
                ),
                "pacing_seconds": config.get("pacing_seconds"),
                "overall": aggregate(selected),
                "categories": {
                    category: aggregate([r for r in selected if r["category"] == category])
                    for category in CATEGORIES
                },
                "processed_overall": aggregate(processed_selected, "processed_metrics")
                if processed_selected is not None
                else None,
                "processed_categories": {
                    category: aggregate(
                        [r for r in processed_selected if r["category"] == category],
                        "processed_metrics",
                    )
                    for category in CATEGORIES
                }
                if processed_selected is not None
                else None,
            }
        )
    if len(rows) != sum(model["overall"]["requests"] for model in models):
        raise ValueError("Results contain unconfigured providers")
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    for model in models:
        comparisons = [(summary, model["overall"], model["categories"])]
        if processed_summary is not None:
            comparisons.append(
                (processed_summary, model["processed_overall"], model["processed_categories"])
            )
        for saved_rows, overall, categories in comparisons:
            for category, calculated in [("overall", overall), *categories.items()]:
                saved = next(
                    s
                    for s in saved_rows
                    if s["provider"] == model["provider"] and s["category"] == category
                )
                if saved["requests"] != calculated["requests"] or saved["model"] != model["model"]:
                    raise ValueError("Saved summary model/count differs from measured rows")
                pairs = {
                    "schema_valid_rate": "schema_valid_rate",
                    "complete_case_accuracy": "complete_case_accuracy",
                    "hallucination_rate": "hallucination_rate_valid_outputs",
                    "populated_field_accuracy": "populated_field_accuracy",
                    "empty_field_accuracy": "empty_field_accuracy",
                    "p50_seconds": "all_attempts_p50_seconds",
                    "p95_seconds": "all_attempts_p95_seconds",
                    "missing_fields_f1": "missing_fields_f1",
                }
                for key, saved_key in pairs.items():
                    value, expected = calculated[key], saved[saved_key]
                    if value != expected and (
                        value is None
                        or expected is None
                        or not math.isclose(
                            value,
                            expected,
                            rel_tol=1e-10,
                            abs_tol=1e-10,
                        )
                    ):
                        raise ValueError(
                            f"Recomputed {key} differs from summary: {model['provider']}"
                        )
    return {
        "case_count": manifest["case_count"],
        "passes": manifest["passes"],
        "cases_sha256": manifest["cases_sha256"],
        "scoring_validation": "raw and processed rows rescored against hash-matching gold",
        "models": models,
        "sources_sha256": sources,
        "processed_sources_sha256": processed_sources,
        "processing_origin": processing_origin,
        "processing_version": PROCESSING_VERSION if processed_rows is not None else None,
        "prompt_variant": manifest.get("prompt_variant", "not recorded"),
        "warnings": warnings,
    }


def render(data: dict, output: Path) -> None:
    # Imported only for rendering; aggregation tests need no plotting dependency.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.ticker import (
        FuncFormatter,
        LogLocator,
        MaxNLocator,
        NullFormatter,
        PercentFormatter,
    )

    data["rendering"] = {
        "matplotlib_version": matplotlib.__version__,
        "png_dpi": 300,
        "formats": ["png", "svg"],
    }
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#BEC7CF",
            "axes.labelcolor": "#263747",
            "text.color": "#263747",
            "xtick.color": "#526170",
            "ytick.color": "#526170",
            "svg.fonttype": "none",
            "savefig.facecolor": "white",
        }
    )
    models = data["models"]
    labels = [model["label"] for model in models]
    colors = [COLORS[i % len(COLORS)] for i in range(len(models))]
    stats = [model["overall"] for model in models]
    has_processed = all(model["processed_overall"] is not None for model in models)
    stages = [("Raw", "overall", "categories")]
    if has_processed:
        stages.append(("Post-processed", "processed_overall", "processed_categories"))
    footer = (
        f"{data['case_count']} unique cases/model | {data['passes']} repeated passes | "
        f"Warm-ups excluded | Draft gold | Prompt: {data['prompt_variant']}"
    )

    def figure(title, subtitle, rect=(0.10, 0.24, 0.85, 0.52)):
        fig = plt.figure(figsize=(12, 6.75))
        fig.text(0.07, 0.92, title, fontsize=21, fontweight="bold")
        fig.text(0.07, 0.865, subtitle, fontsize=11, color="#526170")
        fig.text(0.07, 0.05, footer, fontsize=9, color="#647383")
        if data["warnings"]:
            fig.text(
                0.07,
                0.015,
                "Manifest count discrepancy: using recorded attempts. See captions.",
                fontsize=8,
                color="#9B3443",
            )
        return fig, fig.add_axes(rect)

    def percent_axis(ax):
        ax.set_ylim(0, 114)
        ax.set_yticks(range(0, 101, 20))
        ax.yaxis.set_major_formatter(PercentFormatter(100))
        ax.grid(axis="y", color="#E4E9EE", linewidth=0.7)
        ax.set_axisbelow(True)

    def save(fig, name):
        for extension in ("png", "svg"):
            fig.savefig(output / f"{name}.{extension}", dpi=300)
        plt.close(fig)

    fig, ax = figure(
        "From API Response To Correct Extraction",
        "Same attempts scored twice; only missing_fields changes, not failures or validity."
        if has_processed
        else "Raw output only; post-processing data unavailable.",
        (0.24, 0.28, 0.64, 0.50),
    )
    outcome_rows = [
        (model["label"] + "\n" + stage, model[key]) for model in models for stage, key, _ in stages
    ]
    for i, (_, stat) in enumerate(outcome_rows):
        left = 0.0
        for outcome, color in zip(OUTCOMES, OUTCOME_COLORS, strict=True):
            width = 100 * stat["outcomes"][outcome] / stat["requests"]
            ax.barh(i, width, left=left, height=0.57, color=color, edgecolor="white")
            if width >= 5:
                ax.text(
                    left + width / 2,
                    i,
                    f"{width:.1f}%",
                    ha="center",
                    va="center",
                    fontsize=9 if width < 10 else 11,
                    color="white" if outcome in (OUTCOMES[0], OUTCOMES[4]) else "#263747",
                    fontweight="bold",
                )
            left += width
        ax.text(
            102,
            i,
            f"{100 * stat['complete_case_accuracy']:.1f}% exact\n"
            f"{stat['outcomes'][OUTCOMES[4]]}/{stat['requests']}",
            va="center",
            fontsize=9,
        )
    ax.set_yticks(range(len(outcome_rows)), [label for label, _ in outcome_rows], fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, 119)
    ax.set_xticks(range(0, 101, 20))
    ax.xaxis.set_major_formatter(PercentFormatter(100))
    ax.set_xlabel("Share of measured attempts")
    ax.legend(
        handles=[
            Patch(facecolor=c, label=s) for c, s in zip(OUTCOME_COLORS, OUTCOMES, strict=True)
        ],
        loc="upper center",
        bbox_to_anchor=(0.48, -0.26),
        ncol=3,
        frameon=False,
        fontsize=10,
    )
    save(fig, "01_outcome_breakdown")

    fig, ax = figure(
        "Exact Extraction Across Request Categories",
        "Separate raw and post-processed rows; denominator remains all attempts in each category."
        if has_processed
        else "Raw complete-case accuracy; post-processing data unavailable.",
        (0.24, 0.25, 0.63, 0.49),
    )
    category_rows = [
        (model["label"] + "\n" + stage, model[key]) for model in models for stage, _, key in stages
    ]
    matrix = [
        [100 * group[category]["complete_case_accuracy"] for category in CATEGORIES]
        for _, group in category_rows
    ]
    image = ax.imshow(matrix, vmin=0, vmax=100, cmap="YlGnBu", aspect="auto")
    for i, (_, group) in enumerate(category_rows):
        for j, category in enumerate(CATEGORIES):
            cell = group[category]
            ink = "white" if matrix[i][j] >= 60 else "#263747"
            ax.text(
                j,
                i - 0.09,
                f"{matrix[i][j]:.1f}%",
                ha="center",
                va="center",
                fontsize=13,
                fontweight="bold",
                color=ink,
            )
            ax.text(
                j,
                i + 0.19,
                f"Valid: {100 * cell['schema_valid_rate']:.0f}%",
                ha="center",
                va="center",
                fontsize=9,
                color=ink,
            )
    ax.set_xticks(range(len(CATEGORIES)), [category.title() for category in CATEGORIES])
    ax.set_yticks(range(len(category_rows)), [label for label, _ in category_rows], fontsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.colorbar(
        image,
        cax=fig.add_axes((0.90, 0.25, 0.018, 0.49)),
        format=PercentFormatter(100),
        label="Complete-case accuracy",
    )
    category_sizes = {cell["requests"] for _, group in category_rows for cell in group.values()}
    category_count = str(next(iter(category_sizes))) if len(category_sizes) == 1 else "variable"
    fig.text(
        0.24,
        0.145,
        f"Each cell: {category_count} attempts; paired stages are not new samples.",
        fontsize=10,
    )
    save(fig, "02_category_accuracy")

    fig, ax = figure(
        "Observed Request Latency Distribution",
        "All attempts, including invalid and failed outputs. Logarithmic time axis.",
        (0.10, 0.24, 0.61, 0.52),
    )
    for model, stat, color in zip(models, stats, colors, strict=True):
        values = stat["sorted_latency_seconds"]
        cumulative = [100 * (i + 1) / len(values) for i in range(len(values))]
        ax.step(
            [values[0], *values],
            [0, *cumulative],
            where="post",
            color=color,
            linewidth=2,
            label=model["label"].replace("\n", " "),
        )
        ax.scatter(
            [stat["p50_seconds"], stat["p95_seconds"]], [50, 95], color=color, s=35, zorder=3
        )
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    ax.set_xlabel("Recorded request duration (seconds)")
    ax.set_ylabel("Attempts finished by this duration")
    ax.set_ylim(0, 102)
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.grid(which="major", color="#E4E9EE", linewidth=0.7)
    ax.legend(loc="lower right", frameon=True, fontsize=9)
    fig.text(0.75, 0.73, "MODEL                  P50       P95", fontsize=9, fontweight="bold")
    for i, (model, stat, color) in enumerate(zip(models, stats, colors, strict=True)):
        y = 0.65 - 0.105 * i
        fig.text(0.75, y, model["label"], color=color, fontsize=10)
        fig.text(0.885, y, f"{stat['p50_seconds']:.2f}s", fontsize=10)
        fig.text(0.945, y, f"{stat['p95_seconds']:.2f}s", fontsize=10)
    fig.text(0.75, 0.26, "Dots mark P50 and P95.\nNot conditioned on correctness.", fontsize=10)
    save(fig, "04_latency_distribution")

    fig, ax = figure(
        "Extraction Quality Versus Response Time",
        "Circles: raw. Diamonds: post-processed. Shared HTTP timing excludes processing overhead."
        if has_processed
        else "Raw quality; spans show HTTP P50 to P95, not confidence intervals.",
    )
    for model, stat, color in zip(models, stats, colors, strict=True):
        accuracy = 100 * stat["complete_case_accuracy"]
        ax.hlines(accuracy, stat["p50_seconds"], stat["p95_seconds"], color=color, linewidth=2)
        ax.scatter(stat["p50_seconds"], accuracy, s=120, color=color, zorder=3)
        ax.plot(stat["p95_seconds"], accuracy, marker="|", color=color, markersize=12)
        label = f"{model['label']}\nRaw {accuracy:.1f}%"
        if has_processed:
            processed_accuracy = 100 * model["processed_overall"]["complete_case_accuracy"]
            ax.vlines(
                stat["p50_seconds"],
                min(accuracy, processed_accuracy),
                max(accuracy, processed_accuracy),
                color=color,
                linestyle=":",
                linewidth=2,
            )
            ax.scatter(
                stat["p50_seconds"],
                processed_accuracy,
                marker="D",
                s=90,
                facecolor="white",
                edgecolor=color,
                linewidth=2,
                zorder=3,
            )
            label += f" -> post {processed_accuracy:.1f}%"
        ax.annotate(
            label + f" | P50 {stat['p50_seconds']:.2f}s",
            (stat["p50_seconds"], max(accuracy, processed_accuracy) if has_processed else accuracy),
            xytext=(9, 12),
            textcoords="offset points",
            fontsize=10,
            color=color,
        )
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    ax.set_xlim(
        min(s["p50_seconds"] for s in stats) * 0.7, max(s["p95_seconds"] for s in stats) * 1.8
    )
    ax.xaxis.set_major_locator(
        MaxNLocator(nbins=5) if len(models) == 1 else LogLocator(base=10, subs=(1, 2, 5))
    )
    ax.xaxis.set_minor_formatter(NullFormatter())
    highest = max(
        100 * model[key]["complete_case_accuracy"] for model in models for _, key, _ in stages
    )
    ax.set_ylim(0, min(105, highest + 23))
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.set_xlabel("Recorded request duration (seconds, logarithmic scale)")
    ax.set_ylabel("Fully correct / all measured attempts")
    ax.grid(color="#E4E9EE", linewidth=0.7)
    fig.text(
        0.10,
        0.125,
        "Spans: HTTP P50 to P95, not confidence intervals. Processing adds no new model request."
        if has_processed
        else "Better trade-off: higher accuracy and lower latency. No threshold.",
        fontsize=10,
    )
    save(fig, "05_quality_vs_latency")

    fig, first = figure(
        "Hallucination Rates Need Validity Context",
        "Raw = post-processed: missing-field processing changes neither hallucination nor validity."
        if has_processed
        else "Unsupported-fact measure; invalid outputs are not hallucination-free.",
        (0.09, 0.26, 0.38, 0.47),
    )
    second = fig.add_axes((0.56, 0.26, 0.38, 0.47))
    for ax, metric, title in (
        (first, "hallucination_rate", "Among schema-valid outputs"),
        (second, "schema_valid_rate", "Schema validity among all attempts"),
    ):
        for i, (stat, color) in enumerate(zip(stats, colors, strict=True)):
            rate = stat[metric]
            if rate is None:
                ax.text(i, 6, "N/A\nNo valid output", ha="center", fontsize=10)
                continue
            ax.bar(i, 100 * rate, color=color, width=0.55)
            numerator, denominator = (
                (stat["hallucinated_count"], stat["schema_valid_count"])
                if (metric == "hallucination_rate")
                else (stat["schema_valid_count"], stat["requests"])
            )
            ax.text(
                i,
                100 * rate + 2,
                f"{100 * rate:.1f}%\n{numerator}/{denominator}",
                ha="center",
                va="bottom",
                fontsize=10,
                fontweight="bold",
            )
        ax.set_xticks(range(len(models)), labels, fontsize=10)
        ax.set_title(title, fontsize=12, pad=14)
        percent_axis(ax)
    first.set_ylabel("Share of schema-valid outputs")
    fig.text(
        0.09,
        0.12,
        "Different valid subsets: compare hallucination jointly with validity, not alone.",
        fontsize=10,
    )
    save(fig, "07_hallucination_diagnostic")

    fig, ax = figure(
        "Field Accuracy And Missing-Field Processing" if has_processed else "Raw Field Accuracy",
        "Ordinary field scores stay unchanged; only missing-field metadata is recomputed."
        if has_processed
        else "Raw field accuracy; no processed metrics supplied.",
        (0.09, 0.27, 0.39 if has_processed else 0.85, 0.43),
    )
    for i, (stat, color) in enumerate(zip(stats, colors, strict=True)):
        for offset, prefix, hatch, alpha in (
            (-0.18, "populated_field", None, 1.0),
            (0.18, "empty_field", "///", 0.55),
        ):
            rate = stat[f"{prefix}_accuracy"]
            if rate is None:
                ax.text(i + offset, 5, "N/A", ha="center")
                continue
            ax.bar(
                i + offset,
                100 * rate,
                width=0.32,
                color=color,
                hatch=hatch,
                alpha=alpha,
                edgecolor=color,
            )
            ax.text(i + offset, 100 * rate + 2, f"{100 * rate:.1f}%", ha="center", fontsize=9)
    ax.set_xticks(range(len(models)), labels, fontsize=9)
    ax.set_ylabel("Correct / evaluated field comparisons", fontsize=10)
    ax.set_title(
        "Populated vs empty/null (raw = post)" if has_processed else "Raw field scores",
        fontsize=11,
        pad=40,
    )
    percent_axis(ax)
    ax.legend(
        handles=[
            Patch(facecolor="#657B91", label="Populated fields"),
            Patch(facecolor="#BECAD5", hatch="///", label="Empty / null fields"),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, 1.15),
        ncol=2,
        frameon=False,
        fontsize=9,
    )
    if has_processed:
        missing_ax = fig.add_axes((0.58, 0.27, 0.36, 0.43))
        for i, (model, color) in enumerate(zip(models, colors, strict=True)):
            for offset, (_, key, _) in zip((-0.18, 0.18), stages, strict=True):
                stat = model[key]
                rate = 100 * stat["missing_fields_exact_accuracy"]
                missing_ax.bar(
                    i + offset, rate, width=0.32, color=color, alpha=0.5 if key == "overall" else 1
                )
                missing_ax.text(i + offset, rate + 2, f"{rate:.1f}%", ha="center", fontsize=9)
        missing_ax.set_xticks(range(len(models)), labels, fontsize=9)
        missing_ax.set_title("Whole missing-field list accuracy", fontsize=11, pad=40)
        missing_ax.set_ylabel("Correct lists / all measured attempts", fontsize=10)
        percent_axis(missing_ax)
        missing_ax.legend(
            handles=[
                Patch(facecolor="#A7B9CC", label="Raw"),
                Patch(facecolor="#526B83", label="Post-processed"),
            ],
            loc="upper center",
            bbox_to_anchor=(0.5, 1.15),
            ncol=2,
            frameon=False,
            fontsize=9,
        )
    groups = {(s["populated_field_total"], s["empty_field_total"]) for s in stats}
    if len(groups) == 1:
        populated, empty = next(iter(groups))
        fig.text(
            0.10,
            0.13,
            f"Per model: {populated:,} populated and {empty:,} empty/null comparisons "
            f"({100 * empty / (populated + empty):.1f}% empty/null).",
            fontsize=10,
        )
    fig.text(
        0.10,
        0.09,
        "Populated group includes the clarification boolean, following the benchmark scorer.",
        fontsize=9,
        color="#647383",
    )
    save(fig, "08_field_accuracy_diagnostic")


def captions(data: dict) -> str:
    models = data["models"]
    has_processed = all(model["processed_overall"] is not None for model in models)
    lines = [
        "# Supervisor Report Figures",
        "",
        f"Source: completed run; {data['case_count']} unique cases/model, {data['passes']} passes.",
        "Warm-ups excluded. PNG files are 300 DPI; SVG files retain vector graphics and text.",
        f"Dataset SHA-256: `{data['cases_sha256']}`.",
        f"Prompt variant: `{data['prompt_variant']}`.",
        "",
    ]
    if data["warnings"]:
        lines += ["## Integrity Warnings", "", *data["warnings"], ""]
    lines += ["## Scoring Stages", ""]
    if has_processed:
        lines += [
            f"Post-processing source: **{data['processing_origin']}**, "
            f"version `{data['processing_version']}`. Raw and processed views use the same "
            "provider/case/pass attempts; they are not additional requests or independent samples.",
            "Only missing_fields is changed. Invalid outputs remain invalid. All original raw "
            "fields are preserved for an external replay, whose source hashes must match this run.",
            "Populated/empty field scores, hallucination, and schema validity are unchanged by "
            "this processing. Complete-case and missing-field-list accuracy can improve.",
            "",
        ]
    else:
        lines += [
            "Post-processing is unavailable, not zero. Supply `--processed-run` for a "
            "matching historical replay, or use a run with inline processed metrics.",
            "",
        ]
    lines += [
        "## Models",
        "",
        "| Label | Configured model | Returned model |",
        "| --- | --- | --- |",
    ]
    for model in models:
        lines.append(
            f"| {model['label'].replace(chr(10), ' ')} | `{model['model']}` | "
            f"`{', '.join(model['resolved_models'])}` |"
        )
    lines += ["", "## Figures", ""]
    descriptions = (
        (
            "01_outcome_breakdown",
            "Request outcomes",
            "Each attempt belongs to one outcome. Failures take precedence over "
            "JSON/schema errors, "
            "so empty responses are not double-counted. "
            + (
                "Paired rows compare raw versus post-processed scoring of the same attempts; "
                "do not sum their denominators. "
                if has_processed
                else "Only raw rows are shown. "
            )
            + "Fully correct means all gold fields and atoms match, not merely valid JSON.",
        ),
        (
            "02_category_accuracy",
            "Category accuracy",
            "Color and large numbers show complete-case accuracy; smaller numbers show "
            "unchanged schema validity. "
            + (
                "Each model has raw and post-processed rows. "
                if has_processed
                else "Only raw rows are shown. "
            )
            + "Category attempt counts are annotated; repeated passes are not independent cases.",
        ),
        (
            "04_latency_distribution",
            "Observed latency",
            "Empirical cumulative distributions include all attempts, regardless of "
            "validity/correctness. P50/P95 use the runner's inclusive linear interpolation. "
            "The logarithmic axis exposes slow tails without hiding failures. "
            "Latency includes network and server queueing, not deliberate inter-request "
            "pacing. It is HTTP timing only, not post-processed pipeline timing.",
        ),
        (
            "05_quality_vs_latency",
            "Quality-speed trade-off",
            "Circles show raw accuracy. "
            + (
                "Hollow diamonds show processed accuracy. "
                if has_processed
                else "No processed markers are shown. "
            )
            + (
                "Both use the same HTTP median on the x axis; processing overhead is NOT shown. "
                if has_processed
                else "Raw quality uses the recorded HTTP median. "
            )
            + "Horizontal spans show HTTP P50 to P95, not confidence intervals. Do not interpret "
            "the shared x position as proof that processing is free or has measured equal latency. "
            "Historical end-to-end pipeline latency can be unavailable. "
            "No threshold is established.",
        ),
        (
            "07_hallucination_diagnostic",
            "Unsupported facts with validity context",
            "Hallucination is conditional on schema-valid outputs. Numerator/denominator labels "
            "are shown alongside validity among all attempts. It includes nongold atoms or "
            "unsupported scalar/list facts, including incorrect extracted values, "
            "not a diagnosis of model intent. "
            "Different valid subsets prevent interpreting this rate independently of validity. "
            + (
                "Missing-field processing leaves both rates unchanged; no redundant bars are added."
                if has_processed
                else "Post-processed rates are unavailable."
            ),
        ),
        (
            "08_field_accuracy_diagnostic",
            "Field-accuracy partition",
            "Populated and empty/null accuracy each pool the scorer's correct/total counts across "
            "all attempts. The populated group includes needs_clarification. "
            "Empty/null fields dominate the comparisons; high aggregate field accuracy "
            "is not equivalent to correct constraint extraction. Those field scores exclude "
            "missing_fields. "
            + (
                "These scores are unchanged after processing. The second panel compares exact "
                "missing-field-list accuracy before and after processing, divided by ALL attempts. "
                "Invalid outputs get no credit, even when the gold missing-field list is empty."
                if has_processed
                else "Only the raw field-score panel is shown."
            ),
        ),
    )
    for filename, title, description in descriptions:
        lines += [f"### {title}", f"Files: `{filename}.png` and `{filename}.svg`.", description, ""]
    lines += [
        "## Key Numbers",
        "",
        "| Model | Raw exact | Processed exact | Change (pp) | "
        "Missing-list accuracy, raw -> post | "
        "Schema valid | HTTP P50 / P95 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model in models:
        stat = model["overall"]
        processed = model["processed_overall"]
        processed_exact = (
            f"{100 * processed['complete_case_accuracy']:.1f}%" if processed else "N/A"
        )
        delta = (
            f"{100 * (processed['complete_case_accuracy'] - stat['complete_case_accuracy']):+.1f}"
            if processed
            else "N/A"
        )
        processed_missing = (
            f"{100 * processed['missing_fields_exact_accuracy']:.1f}%" if processed else "N/A"
        )
        lines.append(
            f"| {model['label'].replace(chr(10), ' ')} | "
            f"{100 * stat['complete_case_accuracy']:.1f}% | "
            f"{processed_exact} | {delta} | "
            f"{100 * stat['missing_fields_exact_accuracy']:.1f}% -> {processed_missing} | "
            f"{100 * stat['schema_valid_rate']:.1f}% | "
            f"{stat['p50_seconds']:.2f}s / {stat['p95_seconds']:.2f}s |"
        )
    lines += [
        "",
        "## Interpretation And Limits",
        "",
        "Raw exact accuracy describes the model's first-pass response. Processed accuracy "
        "describes that response plus deterministic application logic, not better model weights "
        "or a second inference. Report them separately.",
        "",
        "When multiple Gemma variants are present, this is not a controlled same-model "
        "comparison of local versus cloud hosting. Gold annotations "
        "are draft and exact-match wording matters. Providers ran in sequential blocks; "
        "no confidence intervals or population-generalization claims are made. Repeated passes "
        "observe the same cases, not independent new cases. Hosting resources, billing and cold "
        "starts were not measured. Pacing may differ, so intrinsic throughput is not plotted.",
        "",
        "Annotation conventions can affect exact-match scores; consult the dataset's applicable "
        "changelog, including any missing-destination ambiguity. No cases, prompts, scores "
        "or original run artifacts were changed.",
        "",
        "## Auditable Plot Data",
        "",
        "`chart_data.json` contains aggregate counts, curves and source hashes. "
        "Rows were rescored against hash-matching gold, then aggregated and checked against "
        "raw/processed summaries. Exact missing-field-list accuracy is derived from item error "
        "counts and schema flags. Raw prompts, responses, credentials, endpoints and config notes "
        "are not copied into this report.",
        "",
    ]
    return "\n".join(lines)


def export_report(data: dict, output: Path) -> None:
    """Render figures and their matching captions/data into the caller's output directory."""
    render(data, output)
    (output / "chart_data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (output / "captions.md").write_text(captions(data), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=Path("data/run1"))
    parser.add_argument("--output", type=Path, help="New directory; defaults to RUN/figures")
    parser.add_argument(
        "--processed-run",
        type=Path,
        help="Matching offline replay for an older run without processed metrics",
    )
    parser.add_argument(
        "--cases", type=Path, help="Matching gold JSONL; defaults to benchmark cases.jsonl"
    )
    args = parser.parse_args()
    output = args.output or args.run / "figures"
    try:
        data = load_run(args.run, args.processed_run, args.cases)
        for warning in data["warnings"]:
            print(f"Warning: {warning}")
        output.mkdir(parents=True, exist_ok=False)
        export_report(data, output)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        parser.error(str(exc))
    print(f"Generated six PNG/SVG pairs, captions and plot data in {output}")


if __name__ == "__main__":
    main()
