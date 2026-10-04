"""Export the six selected supervisor-report figures without calling any model."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

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


def aggregate(rows: list[dict]) -> dict:
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
        elif not row["metrics"]["complete_case_accuracy"]:
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
        "hallucinated_count": sum(row["metrics"]["hallucinated_case"] for row in valid),
        "hallucination_rate": (
            sum(row["metrics"]["hallucinated_case"] for row in valid) / len(valid)
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
        correct = sum(row["metrics"][f"{group}_correct"] for row in rows)
        total = sum(row["metrics"][f"{group}_total"] for row in rows)
        result[f"{group}_correct"] = correct
        result[f"{group}_total"] = total
        result[f"{group}_accuracy"] = correct / total if total else None
    return result


def load_run(run: Path) -> dict:
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if not manifest["complete"]:
        raise ValueError("Report requires a completed run")
    rows = [
        json.loads(line)
        for line in (run / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any(row["warmup"] for row in rows):
        raise ValueError("Measured results unexpectedly contain warm-ups")
    keys = [(r["provider"], r["test_case_id"], r["pass"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate provider/case/pass observations")
    models = []
    reference = None
    for config in manifest["providers"]:
        name = config["name"]
        selected = [row for row in rows if row["provider"] == name]
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
                "pacing_seconds": config["pacing_seconds"],
                "overall": aggregate(selected),
                "categories": {
                    category: aggregate([r for r in selected if r["category"] == category])
                    for category in CATEGORIES
                },
            }
        )
    if len(rows) != sum(model["overall"]["requests"] for model in models):
        raise ValueError("Results contain unconfigured providers")
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    for model in models:
        for category, calculated in [("overall", model["overall"]), *model["categories"].items()]:
            saved = next(
                s
                for s in summary
                if s["provider"] == model["provider"] and s["category"] == category
            )
            pairs = {
                "schema_valid_rate": "schema_valid_rate",
                "complete_case_accuracy": "complete_case_accuracy",
                "hallucination_rate": "hallucination_rate_valid_outputs",
                "populated_field_accuracy": "populated_field_accuracy",
                "empty_field_accuracy": "empty_field_accuracy",
                "p50_seconds": "all_attempts_p50_seconds",
                "p95_seconds": "all_attempts_p95_seconds",
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
                    raise ValueError(f"Recomputed {key} differs from summary: {model['provider']}")
    return {
        "case_count": manifest["case_count"],
        "passes": manifest["passes"],
        "cases_sha256": manifest["cases_sha256"],
        "models": models,
        "sources_sha256": {
            name: hashlib.sha256((run / name).read_bytes()).hexdigest()
            for name in ("manifest.json", "results.jsonl", "summary.json")
        },
    }


def render(data: dict, output: Path) -> None:
    # Imported only for rendering; aggregation tests need no plotting dependency.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter, PercentFormatter

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
    footer = (
        f"{data['case_count']} unique cases/model | {data['passes']} repeated passes | "
        "Warm-ups excluded | Draft gold annotations"
    )

    def figure(title, subtitle, rect=(0.10, 0.24, 0.85, 0.52)):
        fig = plt.figure(figsize=(12, 6.75))
        fig.text(0.07, 0.92, title, fontsize=21, fontweight="bold")
        fig.text(0.07, 0.865, subtitle, fontsize=11, color="#526170")
        fig.text(0.07, 0.05, footer, fontsize=9, color="#647383")
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
        "Mutually exclusive outcomes; denominator: all measured attempts per model.",
        (0.24, 0.30, 0.64, 0.44),
    )
    for i, stat in enumerate(stats):
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
            f"Exact\n{stat['outcomes'][OUTCOMES[4]]}/{stat['requests']}",
            va="center",
            fontsize=10,
        )
    ax.set_yticks(range(len(models)), labels)
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
        "Large number: fully correct / all attempts. Smaller number: schema-valid share.",
        (0.24, 0.25, 0.63, 0.49),
    )
    matrix = [
        [100 * model["categories"][category]["complete_case_accuracy"] for category in CATEGORIES]
        for model in models
    ]
    image = ax.imshow(matrix, vmin=0, vmax=100, cmap="YlGnBu", aspect="auto")
    for i, model in enumerate(models):
        for j, category in enumerate(CATEGORIES):
            cell = model["categories"][category]
            ink = "white" if matrix[i][j] >= 60 else "#263747"
            ax.text(
                j,
                i - 0.09,
                f"{matrix[i][j]:.1f}%",
                ha="center",
                va="center",
                fontsize=16,
                fontweight="bold",
                color=ink,
            )
            ax.text(
                j,
                i + 0.19,
                f"Valid: {100 * cell['schema_valid_rate']:.0f}%",
                ha="center",
                va="center",
                fontsize=10,
                color=ink,
            )
    ax.set_xticks(range(len(CATEGORIES)), [category.title() for category in CATEGORIES])
    ax.set_yticks(range(len(models)), labels)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.colorbar(
        image,
        cax=fig.add_axes((0.90, 0.25, 0.018, 0.49)),
        format=PercentFormatter(100),
        label="Complete-case accuracy",
    )
    fig.text(0.24, 0.145, "Each cell: 20 unique cases x 3 passes = 60 attempts.", fontsize=10)
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
        "Dots: median latency. Spans: median to P95 latency, not confidence intervals.",
    )
    for model, stat, color in zip(models, stats, colors, strict=True):
        accuracy = 100 * stat["complete_case_accuracy"]
        ax.hlines(accuracy, stat["p50_seconds"], stat["p95_seconds"], color=color, linewidth=2)
        ax.scatter(stat["p50_seconds"], accuracy, s=120, color=color, zorder=3)
        ax.plot(stat["p95_seconds"], accuracy, marker="|", color=color, markersize=12)
        ax.annotate(
            f"{model['label']}\n{accuracy:.1f}% | P50 {stat['p50_seconds']:.2f}s",
            (stat["p50_seconds"], accuracy),
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
    ax.set_ylim(0, min(100, max(100 * s["complete_case_accuracy"] for s in stats) + 23))
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.set_xlabel("Recorded request duration (seconds, logarithmic scale)")
    ax.set_ylabel("Fully correct / all measured attempts")
    ax.grid(color="#E4E9EE", linewidth=0.7)
    fig.text(
        0.10,
        0.125,
        "Better trade-off: higher accuracy and lower latency. No acceptance threshold.",
        fontsize=10,
    )
    save(fig, "05_quality_vs_latency")

    fig, first = figure(
        "Hallucination Rates Need Validity Context",
        "Unsupported-fact measure; invalid outputs are not treated as hallucination-free.",
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
        "High Field Accuracy Can Hide Missing Facts",
        "Accuracy is computed across all attempts; invalid outputs receive zero correct fields.",
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
            ax.text(i + offset, 100 * rate + 2, f"{100 * rate:.1f}%", ha="center", fontsize=11)
    ax.set_xticks(range(len(models)), labels)
    ax.set_ylabel("Correct / evaluated field comparisons")
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
    lines = [
        "# Supervisor Report Figures",
        "",
        f"Source: completed run; {data['case_count']} unique cases/model, {data['passes']} passes.",
        "Warm-ups excluded. PNG files are 300 DPI; SVG files retain vector graphics and text.",
        f"Dataset SHA-256: `{data['cases_sha256']}`.",
        "",
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
            "so empty responses are not double-counted. Fully correct means all scored gold fields "
            "and atoms match, not merely valid JSON.",
        ),
        (
            "02_category_accuracy",
            "Category accuracy",
            "Color and large numbers show complete-case accuracy; smaller numbers show "
            "schema validity. "
            "Each cell has 20 unique cases repeated three times, not 60 independent cases.",
        ),
        (
            "04_latency_distribution",
            "Observed latency",
            "Empirical cumulative distributions include all attempts, regardless of "
            "validity/correctness. P50/P95 use the runner's inclusive linear interpolation. "
            "The logarithmic axis exposes the cloud Gemma tail without hiding failures. "
            "Latency includes network and server queueing, not deliberate inter-request "
            "pacing; there are no recorded timeouts here.",
        ),
        (
            "05_quality_vs_latency",
            "Quality-speed trade-off",
            "Dots use all-attempt median latency and complete-case accuracy. "
            "Horizontal spans extend "
            "to P95; they are latency tails, not uncertainty intervals. Upper-left is preferable. "
            "The plot establishes no quality or latency acceptance threshold.",
        ),
        (
            "07_hallucination_diagnostic",
            "Unsupported facts with validity context",
            "Hallucination is conditional on schema-valid outputs. Numerator/denominator labels "
            "are shown alongside validity among all attempts. It includes nongold atoms or "
            "unsupported scalar/list facts, including incorrect extracted values, "
            "not a diagnosis of model intent. "
            "Different valid subsets prevent interpreting this rate independently of validity.",
        ),
        (
            "08_field_accuracy_diagnostic",
            "Field-accuracy partition",
            "Populated and empty/null accuracy each pool the scorer's correct/total counts across "
            "all attempts. The populated group includes needs_clarification. "
            "Empty/null fields dominate the comparisons; high aggregate field accuracy "
            "is not equivalent to correct constraint extraction.",
        ),
    )
    for filename, title, description in descriptions:
        lines += [f"### {title}", f"Files: `{filename}.png` and `{filename}.svg`.", description, ""]
    lines += [
        "## Key Numbers",
        "",
        "| Model | Exact | Schema valid | P50 / P95 | Hallucinated / valid |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for model in models:
        stat = model["overall"]
        lines.append(
            f"| {model['label'].replace(chr(10), ' ')} | "
            f"{100 * stat['complete_case_accuracy']:.1f}% | "
            f"{100 * stat['schema_valid_rate']:.1f}% | "
            f"{stat['p50_seconds']:.2f}s / {stat['p95_seconds']:.2f}s | "
            f"{stat['hallucinated_count']}/{stat['schema_valid_count']} |"
        )
    lines += [
        "",
        "## Interpretation And Limits",
        "",
        "Gemini has the highest exact accuracy and lowest median latency in this run. "
        "LM Studio Gemma returns valid output but trails in complete-case accuracy. "
        "Cloud Gemma loses substantial end-to-end utility to output-validity failures.",
        "",
        "These are different Gemma model variants, including a quantized local artifact: "
        "not a controlled same-model comparison of local versus cloud hosting. Gold annotations "
        "are draft and exact-match wording matters. Providers ran in sequential blocks; "
        "no confidence intervals or population-generalization claims are made. Three passes "
        "are repeated observations of the same 120 cases. Hosting resources, billing and cold "
        "starts were not measured. Pacing differs, so intrinsic throughput is not plotted.",
        "",
        "The dataset v2 changelog documents a missing-destination wording ambiguity affecting "
        "four cases. No cases, prompts, scores or original run artifacts were changed.",
        "",
        "## Auditable Plot Data",
        "",
        "`chart_data.json` contains aggregate counts, curves and source hashes. "
        "All plotted metrics were recomputed from measured rows and checked against the saved "
        "summary. Raw prompts, responses, credentials, endpoint addresses and config notes "
        "are not copied into this report.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=Path("data/run1"))
    parser.add_argument("--output", type=Path, help="New directory; defaults to RUN/figures")
    args = parser.parse_args()
    output = args.output or args.run / "figures"
    try:
        data = load_run(args.run)
        output.mkdir(parents=True, exist_ok=False)
        render(data, output)
        (output / "chart_data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        (output / "captions.md").write_text(captions(data), encoding="utf-8")
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        parser.error(str(exc))
    print(f"Generated six PNG/SVG pairs, captions and plot data in {output}")


if __name__ == "__main__":
    main()
