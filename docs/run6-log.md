# Run6 log

Run6: Gemini (`gemini-3.5-flash-lite`) only, dataset v3 (`cases_v3.jsonl`, 146 cases), prompt `semantic-v3`, processing `repair-v2`, 3 passes, 3 warm-ups, seed 42. LM Studio Gemma is excluded because its endpoint was unreachable in run4. Expected 441 requests (438 measured + 3 warm-ups) at 15 s pacing, about 2 hours.

Pre-run state: 258 tests pass; ruff, ruff format, mypy, `git diff --check` clean; `cases.jsonl` and `schema.py` SHA-256 unchanged. Repair-v2 was validated offline on saved runs 1–5 with zero lost exact cases.

Command (from README, Semantic-v2 example with the new options):

```bash
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json \
  --provider gemini \
  --prompt-variant semantic-v3 --processing repair-v2 \
  --passes 3 --warmups 3 --seed 42 \
  --output data/run6
```

Then:

```bash
uv run --with 'matplotlib>=3.10,<4' python src/journie-llm/plot_report.py \
  --run data/run6 --output data/run6/figures
```

## Attempts

### Attempt 1

- Started: 2026-10-21. Console: `logs/run6-attempt1.log`.
- Finished: exit 0, 438/438 measured requests + 3 warm-ups, **0 API failures, 0 invalid outputs** (schema-valid 100%). Manifest: `complete: true`, `prompt_version: semantic-v3`, `processing_version: repair-v2`, `diagnostics_version: consistency-v2`.
- `plot_report.py` succeeded on the first try: `data/run6/figures/` (six PNG/SVG pairs, `captions.md`, `chart_data.json`).
- No code fixes were needed, so `data/run6` was never deleted or rerun.

## Results (Gemini, dataset v3, 3 passes)

| group | attempts | raw exact | after repair-v2 |
|---|---:|---:|---:|
| overall | 438 | 302 (68.9%) | **354 (80.8%)** |
| user_input:basic | 225 | 170 (75.6%) | 190 (84.4%) |
| user_input:multiple | 165 | 84 (50.9%) | 116 (70.3%) |
| non_user_input:adversarial | 60 | 49 (81.7%) | 50 (83.3%) |
| non_user_input:off_topic | 60 | 48 (80.0%) | 51 (85.0%) |
| trait:missing | 72 | 43 (59.7%) | 53 (73.6%) |
| trait:noisy | 60 | 32 (53.3%) | 45 (75.0%) |
| trait:conflicting | 69 | 46 (66.7%) | 58 (84.1%) |

Hierarchy rows overlap (a case can be in several); do not sum them.

- **Processing:** 52 attempts gained exact, **0 lost**. Beyond `missing_fields`, 14 outputs were repaired (3.2% of valid outputs): `conflicting-12` budget cap ×3 (all became exact), `noisy-07` duplicate removed (became exact), disproved conflicts removed in `multiple-12` ×3, `noisy-18`, `conflicting-06` ×2 (still inexact because `needs_clarification` is never lowered, or other errors).
- **Hallucination** (schema-valid outputs): 17.6% raw → 16.7% after repair.
- **Diagnostics after repair:** error rate 0.7% (2 `atom_classification_mismatch`, 3 `missing_atom`); 15 `overnight_relation_requires_review` (review severity, by design).
- **New rules:**
  - `interest-*` (mandatory vs preferred interests): **18/18** exact.
  - Adversarial rejection: 50/60 exact. The model still extracted travel facts beside the attack in `adversarial-11`, `adversarial-12` (all 3 passes), `adversarial-02` (2 passes), and once each in `adversarial-15` and `adversarial-20`.
  - Off-topic: 51/60. Failures are mixed cases: `offtopic-15`, `offtopic-18` (all passes), `offtopic-20` (2), `offtopic-13` (1). Constraint lists differ from the draft gold, so the gold should be reviewed.
- **Cost/latency:** mean 2,649 input tokens per request; P50 latency 2.29 s.

## Comparison with run5 on the 100 shared cases (same gold)

Non-adversarial v2 cases, 300 attempts each:

| run | raw exact | after processing |
|---|---:|---:|
| run5 (semantic-v2) | 212 | 238 (v1), 241 (repair-v2 replay) |
| run6 (semantic-v3) | 190 | 235 (repair-v2) |

- Most raw drops are `missing_fields` only, which processing fixes (e.g. `multiple-05`, `noisy-02`, `noisy-04`).
- The real gap after processing is −6 attempts. New semantic errors: a computed `end_date` (`multiple-01`) and an unresolved "in one week" (`multiple-10`).
- This is a single run on development data, so it is not evidence that semantic-v3 is worse.

## Follow-up: repair-v3 replay (offline, 2026-10-22)

- Replay: `data/run6-repair-v3` (`--processing repair-v3`); figures in `data/run6-repair-v3/figures`.
- repair-v3 adds name canonicalization from `src/journie-llm/name_aliases.csv` (e.g. `Can Tho` → `Cần Thơ`, `cà phê` → `cafe`).
- Exact after processing: 354 (repair-v2) → **366/438 (83.6%)**, 0 lost.
- Fixes `offtopic-18` (all 3 passes), `conflicting-18`, `basic-20`, `multiple-15`, `multiple-19`.
