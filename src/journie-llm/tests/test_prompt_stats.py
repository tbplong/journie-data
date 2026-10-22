import json
from pathlib import Path
from typing import cast

import prompts
import pytest
from benchmark import fingerprint
from prompt_stats import fit_prompt_tokens, markdown_table, section_stats

CASES = Path(__file__).resolve().parents[1] / "cases.jsonl"


def test_sections_partition_every_prompt() -> None:
    rows = section_stats()
    for variant, version in prompts.PROMPT_VERSIONS.items():
        mine = [row for row in rows if row["prompt_version"] == version]
        prompt = prompts.build_prompt(cast(prompts.PromptVariant, variant))
        assert sum(row["chars"] for row in mine) == len(prompt)
        assert mine[0]["section"] == "contract"
        assert sum(row["share"] for row in mine) == pytest.approx(1, abs=1e-3)


def write_run(tmp_path, tokens):
    cases = tmp_path / "cases.jsonl"
    cases.write_text("\n".join(CASES.read_text("utf-8").splitlines()[:3]) + "\n", encoding="utf-8")
    ids = [json.loads(line)["id"] for line in cases.read_text("utf-8").splitlines()]
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "system_prompt": "PROMPT",
                "prompt_sha256": fingerprint("PROMPT"),
                "prompt_version": "baseline-v1",
                "cases_sha256": fingerprint(cases.read_bytes()),
            }
        )
    )
    records = [
        {"provider": "p", "model": "m", "test_case_id": "warm", "warmup": True, "input_tokens": 1},
        *(
            {"provider": "p", "model": "m", "test_case_id": case_id, "warmup": False}
            | {"input_tokens": tokens(case_id)}
            for case_id in ids
        ),
        {"provider": "p", "model": "m", "test_case_id": ids[0], "warmup": False},
    ]
    (run / "results.jsonl").write_text("\n".join(map(json.dumps, records)), encoding="utf-8")
    return run, cases


def test_fit_recovers_fixed_prompt_tokens(tmp_path) -> None:
    lengths: dict[str, int] = {}

    def tokens(case_id: str) -> int:
        return 1000 + 2 * lengths[case_id]

    for line in CASES.read_text("utf-8").splitlines()[:3]:
        case = json.loads(line)
        lengths[case["id"]] = len(case["user_input"])
    run, cases = write_run(tmp_path, tokens)
    [row] = fit_prompt_tokens(run, cases)
    assert row["requests"] == 3
    assert row["prompt_tokens"] == 1000
    assert row["tokens_per_input_char"] == 2
    assert row["max_abs_residual"] == 0


def test_fit_rejects_mismatched_cases(tmp_path) -> None:
    run, cases = write_run(tmp_path, lambda _: 1)
    cases.write_text(cases.read_text("utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cases_sha256"):
        fit_prompt_tokens(run, cases)


def test_markdown_table_aligns_columns() -> None:
    table = markdown_table([{"name": "a", "n": 5}, {"name": "long", "n": 12}])
    assert table.splitlines() == [
        "| name |  n |",
        "|------|---:|",
        "| a    |  5 |",
        "| long | 12 |",
    ]
