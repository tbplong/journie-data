"""Prompt length by section (offline) and fixed prompt tokens fitted from recorded usage."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import cast

from benchmark import fingerprint, load_cases, matching_cases
from prompts import PROMPT_VERSIONS, PromptVariant, prompt_sections


def section_stats() -> list[dict]:
    """Characters and whitespace-separated words per named section of every variant."""
    rows = []
    for variant in PROMPT_VERSIONS:
        sections = prompt_sections(cast(PromptVariant, variant))
        total = sum(len(text) for _, text in sections)
        for name, text in sections:
            rows.append(
                {
                    "prompt_version": PROMPT_VERSIONS[variant],
                    "section": name,
                    "chars": len(text),
                    "words": len(text.split()),
                    "share": round(len(text) / total, 4),
                }
            )
    return rows


def fit_prompt_tokens(run: Path, cases: Path | None = None) -> list[dict]:
    """Least squares input_tokens = prompt_tokens + per_char * len(user_input), per provider.

    The intercept is the provider-tokenized cost of everything fixed per request (system
    prompt plus message framing); it uses only usage the provider already reported.
    """
    manifest = json.loads((run / "manifest.json").read_bytes())
    cases = cases or matching_cases(manifest.get("cases_sha256"))
    prompt = manifest.get("system_prompt")
    if not isinstance(prompt, str) or fingerprint(prompt) != manifest.get("prompt_sha256"):
        raise ValueError("Source prompt content does not match prompt hash")
    if fingerprint(cases.read_bytes()) != manifest.get("cases_sha256"):
        raise ValueError("Cases hash does not match source cases_sha256")
    inputs = {case.id: case.user_input for case in load_cases(cases)}
    groups: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for line in (run / "results.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["warmup"] or not isinstance(record.get("input_tokens"), int):
            continue
        groups.setdefault((record["provider"], record["model"]), []).append(
            (len(inputs[record["test_case_id"]]), record["input_tokens"])
        )
    rows = []
    for (provider, model), points in sorted(groups.items()):
        lengths, tokens = zip(*points, strict=True)
        if len(set(lengths)) < 2:
            raise ValueError(f"{provider}: need at least two distinct input lengths")
        slope, intercept = statistics.linear_regression(lengths, tokens)
        rows.append(
            {
                "run": run.name,
                "provider": provider,
                "model": model,
                "prompt_version": manifest.get("prompt_version") or "unversioned",
                "prompt_chars": len(prompt),
                "requests": len(points),
                "prompt_tokens": round(intercept, 1),
                "tokens_per_input_char": round(slope, 4),
                "chars_per_prompt_token": round(len(prompt) / intercept, 2),
                "max_abs_residual": round(
                    max(abs(t - intercept - slope * n) for n, t in points), 1
                ),
            }
        )
    return rows


def markdown_table(rows: list[dict]) -> str:
    """Column-aligned Markdown table; numbers are right-aligned."""
    header = list(rows[0])
    cells = [header, *([str(row[key]) for key in header] for row in rows)]
    widths = [max(len(line[i]) for line in cells) for i in range(len(header))]
    numeric = [isinstance(rows[0][key], int | float) for key in header]

    def render(line: list[str]) -> str:
        return (
            "| "
            + " | ".join(
                cell.rjust(width) if right else cell.ljust(width)
                for cell, width, right in zip(line, widths, numeric, strict=True)
            )
            + " |"
        )

    rule = (
        "|"
        + "|".join(
            "-" * (width + 1) + (":" if right else "-")
            for width, right in zip(widths, numeric, strict=True)
        )
        + "|"
    )
    return "\n".join([render(header), rule, *map(render, cells[1:])])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        type=Path,
        action="append",
        help="Run directory to fit, e.g. data/run5-semantic-v2; repeatable. "
        "Without it, print offline section lengths.",
    )
    parser.add_argument(
        "--cases", type=Path, help="Gold JSONL; defaults to the dataset matching each run's hash"
    )
    parser.add_argument("--csv", action="store_true", help="Print CSV instead of a table")
    args = parser.parse_args()
    try:
        rows = (
            [row for run in args.run for row in fit_prompt_tokens(run, args.cases)]
            if args.run
            else section_stats()
        )
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    if not args.csv:
        print(markdown_table(rows))
        return
    writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)


if __name__ == "__main__":
    main()
