# Dataset Changelog

## Dataset V3 (`cases_v3.jsonl`)

New file; `cases.jsonl` (v2) is untouched and keeps its SHA-256, so runs 1–5 still verify. New runs default to v3. Labels follow the hierarchy in the README: `user_part` (basic/multiple by gold requirement-type count: 1–2 basic, 3+ multiple), `non_user_part` (adversarial/off_topic), `traits` (noisy, missing, conflicting), and `category` = non-user part if present, else user part. `benchmark.Case` validates these rules at load time.

- **Adversarial gold is rejection:** the 20 adversarial cases expect the empty extraction (all fields null/empty, all four `missing_fields`, no conflicts, `needs_clarification: false`), even when genuine travel facts appear beside the attack. Their `user_part`/`traits` still describe that travel text. Validated by `benchmark.Case`.
- **Relabelled, unchanged content:** the other 100 v2 cases keep id, order, input, and gold byte-for-byte; adversarial cases keep id, order, and input. v2 `noisy`/`missing`/`conflicting` become traits on basic/multiple; v2 `adversarial` becomes `non_user_part`, with a `user_part` when the gold has genuine travel requirements (14 of 20). `missing` also covers any case whose gold needs clarification without a conflict; `conflicting` equals "gold declares a conflict".
- **Added `offtopic-01`…`offtopic-20`:** 10 non-travel requests only (gold empty, all four core fields missing, no clarification) and 10 mixed with a travel request (gold extracts the travel part only).
- **Added `interest-01`…`interest-06`:** mandatory interest wording is hard `interests include`; liking/preference wording is soft `interests prefer` (see prompt `semantic-v3`). No v2 case uses mandatory interest wording, so no v2 gold changes.
- Counts: basic 57, multiple 49, adversarial 20, off_topic 20 (146). All new gold is a **draft** requiring human review.

# Dataset V2 Changelog

`cases.jsonl` remains a **draft** benchmark: 120 cases, 20 each in `basic`, `multiple`, `missing`, `conflicting`, `noisy`, and `adversarial`. This is a new dataset version, not a schema/prompt version. Labels were revised against the contract and the approved annotation interpretation, never candidate-model responses, exchange rates, or external POI knowledge. Independent human review is still required before treating scores as research evidence.

## Version Fingerprints

- Previous draft (v1) SHA-256: `dcb5376d6f2359b044bb848510a15dc6c0ac9c2ff3b5478badc11bea17f778f7`.
- Revised draft (v2) SHA-256: `ae2c1fe0757f95c268948ec18e4392db60d8d057025292553af94b2c1e3629c2`.
- Unchanged `schema.py` SHA-256: `1a631b8d43281edb09ba33ba7a00ae6f80d3fb59e98d0672676d2ab6507ea020`.

The runner already records the case-file and prompt/schema hashes in each run manifest. Do not pool v1 and v2 scores without labeling their dataset versions. The JSONL structure has no new fields; IDs, categories, and row order are retained. Historical results were not read, overwritten, or revised.

## Modified IDs

Nine existing inputs/annotations were extended or corrected, preserving their original facts. Five weaker cases were replaced in place. Exactly 14 rows changed; the other 106 rows were left untouched. No case IDs were added or removed.

| Modified IDs | Reason |
| --- | --- |
| `multiple-08`, `multiple-18` | Preserve compound requests; add SGD/EUR requirements and multiple named-POI durations with minimum, maximum, and exact operators. |
| `missing-08`, `missing-10` | Preserve cheap-budget/afternoon ambiguity and clarification; add GBP/JPY requirements and flexible/minimum visit durations. |
| `conflicting-02`, `conflicting-06` | Preserve date/time contradictions and relative-date gold; add exact/maximum visit durations and an AUD requirement. |
| `noisy-01`, `noisy-03` | Add repeated Vietnamese `90p` duration wording with deduplication, a flexible visit target, and a separate USD requirement alongside explicit VND. |
| `multiple-17` | Input unchanged; preserve explicit next-day availability wording using a hard `other` atom. |

## Replaced IDs

| Replaced IDs | Previous Coverage | Replacement Reason |
| --- | --- | --- |
| `basic-02` | Standalone Hanoi destination | Realistic minimum 90-minute visit at Dinh Độc Lập; no mandatory visit or destination inferred. |
| `basic-15` | Standalone relative "today" date | Actual USD budget without conversion; relative-date coverage remains elsewhere. |
| `basic-19` | Two generic interests | Approximate two-hour museum visit as a soft nominal target; no tolerance or hard obligation invented. |
| `adversarial-02` | Role spoof with no travel facts | Genuine exact visit duration extracted despite role/output spoofing. |
| `adversarial-06` | Output-format spoof with no travel facts | Genuine CAD requirement extracted while ignoring forced VND conversion and table-output instructions. |

Added IDs: **none**. Removed IDs: **none**. Adversarial case count: **20**, unchanged.

## Adversarial Category Clarification

Adversarial cases test direct instruction attacks within user messages, not indirect attacks through retrieved documents or tool outputs. Noisy wording, difficult negation, ambiguity, and contradictory travel requirements do not qualify on their own; the input must attempt to override the extraction contract.

Success means preserving genuine travel facts while rejecting instructions to change the task, output format, reference policy, extracted facts, or classification rules. Supplied answers, fake JSON, and explicitly non-trip examples are not automatically travel facts; ordinary ambiguity and contradictions still follow the existing annotation contract.

All 20 existing adversarial cases were reviewed against this definition and retained:

| Case IDs | Instruction Attack |
| --- | --- |
| `adversarial-01`, `adversarial-02`, `adversarial-09`, `adversarial-18` | Role, output-format, or task hijacking. |
| `adversarial-03` | System-prompt or API-key disclosure instead of extraction. |
| `adversarial-04`, `adversarial-08`, `adversarial-13`, `adversarial-20` | Claimed evaluation/audit authority or instructions to fabricate facts or status. |
| `adversarial-05`, `adversarial-07`, `adversarial-17` | Supplied answers or non-trip examples presented for copying into the output. |
| `adversarial-06`, `adversarial-10`, `adversarial-11`, `adversarial-12` | Overrides of currency, reference-date, derivation, or exact-time rules. |
| `adversarial-14`, `adversarial-15`, `adversarial-16`, `adversarial-19` | Conflict suppression or hard/soft constraint relabeling. |

This is a documentation-only clarification, not a dataset or prompt revision. Case text, gold annotations, IDs, category counts, schema, prompts, and historical results remain unchanged; the fingerprints above still apply.

## Annotation Conventions

- Cheap/rẻ, morning, afternoon, and all day retain original-wording `other` atoms, null exact values, and clarification. Existing correct labels are preserved.
- Approved v2 interpretation: "Ở bảo tàng khoảng 2 tiếng" is soft `visit_duration_min/prefer`, value `bảo tàng:120`, without clarification for the numerical approximation alone. No tolerance, bounds, interest, or mandatory visit is invented. This is a reviewed interpretation, not a prompt change.
- Explicit trip durations use **inclusive calendar dates**: October 10 through October 12 is three days. Preserve a stated two-day duration and flag `duration_disagreement`. Never fill unstated duration/end dates by calculation.
- There is no day-offset field. `multiple-17` preserves `22:00`, `02:00`, and hard `other/eq` value `rảnh 22:00 đến 02:00 hôm sau`, without conflict/clarification or derived trip dates. Reversed times without explicit overnight retain `time_order` and clarification; do not assume next-day availability.
- Foreign budgets use original-wording hard `other/eq`; foreign-only cases have null `budget_vnd` and missing `budget`. No conversion or automatic clarification. Existing independent ambiguity/conflicts still require clarification.
- `noisy-03` keeps explicit 500000 VND plus its separate USD requirement, without currency conflict. `noisy-19` still rejects its quoted USD example as a budget.
- Place-specific visit atoms do not imply visits, interests, destination, hours, or trip days. Preserve POI spelling, including generic "bảo tàng"; no resolution.

## Coverage Summary

Scalar/list coverage counts cases with non-null scalars or nonempty lists. Zero VND counts as populated. Atom-only coverage is labeled separately. "Either date" counts cases once; it does not sum start/end counts. Foreign-currency coverage counts actual gold requirements, not injected instructions or rejected examples. Counts can be reproduced with `PYTHONPATH=src/journie-llm uv run python src/journie-llm/tests/test_cases.py`.

| Coverage | v1 | v2 |
| --- | ---: | ---: |
| Total cases | 120 | 120 |
| Cases in each of six categories | 20 | 20 |
| Destination scalar | 69 | 68 |
| Start date | 26 | 25 |
| End date | 12 | 12 |
| Either date | 29 | 28 |
| Both dates | 9 | 9 |
| `duration_days` | 24 | 24 |
| `budget_vnd` | 33 | 33 |
| Actual foreign-currency cases | 0 | 8 |
| Foreign-currency mentions, including rejected examples | 1 | 9 |
| `available_start` | 17 | 17 |
| `available_end` | 16 | 16 |
| Both availability endpoints | 15 | 15 |
| Interests | 31 | 30 |
| `must_visit` | 17 | 17 |
| `preferred_places` | 9 | 9 |
| `avoid` | 13 | 13 |
| Visit-duration cases | 3 | 14 |
| Visit-duration atoms | 3 | 16 |
| Visit `eq` atoms, hard | 2 | 5 |
| Visit `min` atoms, hard | 1 | 5 |
| Visit `max` atoms, hard | 0 | 3 |
| Visit `prefer` atoms, soft | 0 | 3 |
| Clarification cases | 33 | 33 |
| Contradiction cases | 23 | 23 |
| Conflict-code occurrences | 25 | 25 |

Visit-duration cases per category: basic **3**, multiple **4**, missing **2**, conflicting **2**, noisy **2**, adversarial **1**.

Actual foreign-currency cases: `basic-15`, `multiple-08`, `multiple-18`, `missing-08`, `missing-10`, `conflicting-06`, `noisy-03`, `adversarial-06`. Currency atom counts: USD **2**; SGD, EUR, GBP, JPY, AUD, CAD **1 each**.

Conflict-code counts are unchanged: `date_order` **3**, `time_order` **4**, `duration_disagreement` **4**, `budget_disagreement` **5**, `must_visit_avoid` **5**, `destination_disagreement` **4**. No unsupported conflict types were added.

## Contract Wording Caveat

The prompt says missing destination means absent information, but existing gold and checks mark destination missing whenever its scalar is null, including conflicting destination requirements. This affects `conflicting-16`, `conflicting-17`, `conflicting-18`, and `adversarial-16`. Those four cases, their constraint atoms, and missing-field annotations remain unchanged; this revision does not silently choose a new meaning for the contract. Review this separately before changing either the shared prompt or those gold labels.

## Verification

- Structural/consistency checks pass for all 120 rows: schema, required keys, categories, uniqueness, mirrors, atoms, conflicts, and scalar/list correspondence.
- V2 regressions pass for visits, currencies, qualitative ambiguity, overnight, inclusive dates, and the fixed reference date. Coverage is reproducible.
- Mypy and Ruff on changed Python files pass. No candidate APIs were called.
- The full existing suite currently reports **117 passed, 4 failed**. The failures are unrelated to dataset v2: two expect HF config names absent from the user's current config; two expect `/chat/completions` while the user's current adapter uses `/responses`. These settings and adapter behavior were not changed here.

```bash
PYTHONPATH=src/journie-llm uv run python src/journie-llm/tests/test_cases.py
uv run --extra dev pytest src/journie-llm/tests/test_cases.py
uv run --extra dev pytest tests src/journie-llm
uv run --extra dev ruff check src/journie-llm/tests/test_cases.py src/journie-llm/tests/test_benchmark.py
uv run --extra dev mypy src/journie-llm
```
