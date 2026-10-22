# Offline Processing Replay

Retrospective deterministic processing of saved outputs, not first-pass accuracy or a prompt improvement. No network requests, new tokens, or new model cost.

Original HTTP latency/tokens are retained. Historical pipeline latency is unknown; postprocessing time was measured now. Recorded validation time is retained where present.

Missing destination follows the null scalar even for destination ambiguity; diagnostics do not resolve intent, repair facts, or establish grounding. Gold is used only for scoring.

| Provider / Model | Requests | Original exact | Processed exact | Changed outputs | Gained exact | Lost exact | Diagnostics checked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini / gemini-3.5-flash-lite | 438 | 302 | 366 | 84 | 64 | 0 | 438 |

## Repaired Outputs

Every output changed beyond missing_fields; gold is used only for the exact columns.

| Provider | Case | Pass | Repairs | Exact before | Exact after |
| --- | --- | ---: | --- | ---: | ---: |
| gemini | offtopic-18 | 1 | canonical_name | 0 | 1 |
| gemini | conflicting-18 | 1 | canonical_name, missing_fields | 0 | 1 |
| gemini | conflicting-12 | 1 | scalar_from_constraint, missing_fields | 0 | 1 |
| gemini | multiple-19 | 1 | canonical_name, missing_fields | 0 | 1 |
| gemini | noisy-10 | 1 | canonical_name, missing_fields | 0 | 0 |
| gemini | noisy-18 | 1 | conflict_from_observation | 0 | 0 |
| gemini | multiple-15 | 1 | canonical_name, missing_fields | 0 | 1 |
| gemini | basic-20 | 1 | canonical_name | 0 | 1 |
| gemini | multiple-12 | 1 | conflict_from_observation | 0 | 0 |
| gemini | conflicting-12 | 2 | scalar_from_constraint, missing_fields | 0 | 1 |
| gemini | multiple-07 | 2 | scalar_from_constraint, missing_fields | 0 | 0 |
| gemini | conflicting-04 | 2 | clarification_from_conflict | 0 | 0 |
| gemini | offtopic-18 | 2 | canonical_name | 0 | 0 |
| gemini | noisy-10 | 2 | canonical_name, missing_fields | 0 | 0 |
| gemini | basic-20 | 2 | canonical_name | 0 | 1 |
| gemini | noisy-07 | 2 | dedupe_constraint | 0 | 1 |
| gemini | multiple-12 | 2 | conflict_from_observation | 0 | 0 |
| gemini | multiple-15 | 2 | canonical_name, missing_fields | 0 | 1 |
| gemini | conflicting-06 | 2 | conflict_from_observation | 0 | 0 |
| gemini | conflicting-18 | 2 | canonical_name, missing_fields | 0 | 1 |
| gemini | multiple-16 | 3 | list_from_constraint | 0 | 0 |
| gemini | conflicting-12 | 3 | scalar_from_constraint, missing_fields | 0 | 1 |
| gemini | multiple-12 | 3 | conflict_from_observation | 0 | 0 |
| gemini | conflicting-04 | 3 | clarification_from_conflict | 0 | 0 |
| gemini | noisy-10 | 3 | canonical_name, missing_fields | 0 | 0 |
| gemini | offtopic-18 | 3 | canonical_name | 0 | 1 |
| gemini | basic-20 | 3 | canonical_name | 0 | 1 |
| gemini | multiple-15 | 3 | canonical_name, missing_fields | 0 | 1 |
| gemini | conflicting-18 | 3 | canonical_name, missing_fields | 0 | 1 |
| gemini | conflicting-06 | 3 | conflict_from_observation, missing_fields | 0 | 0 |
