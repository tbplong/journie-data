# Offline Processing Replay

Retrospective deterministic processing of saved outputs, not first-pass accuracy or a prompt improvement. No network requests, new tokens, or new model cost.

Original HTTP latency/tokens are retained. Historical pipeline latency is unknown; postprocessing time was measured now. Recorded validation time is retained where present.

Missing destination follows the null scalar even for destination ambiguity; diagnostics do not resolve intent, repair facts, or establish grounding. Gold is used only for scoring.

| Provider / Model | Requests | Original exact | Processed exact | Changed outputs | Gained exact | Lost exact | Diagnostics checked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini / gemini-3.5-flash-lite | 360 | 198 | 245 | 83 | 47 | 0 | 354 |

## Repaired Outputs

Every output changed beyond missing_fields; gold is used only for the exact columns.

| Provider | Case | Pass | Repairs | Exact before | Exact after |
| --- | --- | ---: | --- | ---: | ---: |
| gemini | noisy-12 | 1 | canonical_name | 0 | 1 |
| gemini | adversarial-15 | 1 | scalar_from_constraint | 0 | 0 |
| gemini | noisy-07 | 1 | dedupe_constraint | 0 | 1 |
| gemini | multiple-12 | 1 | conflict_from_observation | 0 | 0 |
| gemini | multiple-19 | 1 | canonical_name, missing_fields | 0 | 0 |
| gemini | multiple-15 | 1 | canonical_name, missing_fields | 0 | 1 |
| gemini | adversarial-08 | 1 | conflict_from_observation | 0 | 0 |
| gemini | basic-20 | 1 | canonical_name | 0 | 1 |
| gemini | noisy-10 | 1 | canonical_name, missing_fields | 0 | 0 |
| gemini | conflicting-11 | 2 | scalar_from_constraint | 0 | 1 |
| gemini | noisy-18 | 2 | conflict_from_observation | 0 | 0 |
| gemini | basic-20 | 2 | canonical_name | 0 | 1 |
| gemini | multiple-16 | 2 | list_from_constraint | 0 | 0 |
| gemini | conflicting-03 | 2 | scalar_from_constraint, missing_fields | 0 | 1 |
| gemini | conflicting-12 | 2 | scalar_from_constraint, missing_fields | 0 | 1 |
| gemini | multiple-12 | 2 | conflict_from_observation | 0 | 0 |
| gemini | noisy-01 | 2 | dedupe_constraint | 0 | 0 |
| gemini | multiple-15 | 2 | canonical_name, missing_fields | 0 | 1 |
| gemini | multiple-19 | 2 | canonical_name | 0 | 0 |
| gemini | adversarial-15 | 2 | scalar_from_constraint | 0 | 0 |
| gemini | noisy-10 | 2 | canonical_name, missing_fields | 0 | 0 |
| gemini | noisy-07 | 3 | dedupe_constraint | 0 | 1 |
| gemini | conflicting-11 | 3 | scalar_from_constraint | 0 | 1 |
| gemini | noisy-06 | 3 | canonical_name, dedupe_constraint | 0 | 1 |
| gemini | adversarial-08 | 3 | conflict_from_observation | 0 | 0 |
| gemini | noisy-01 | 3 | dedupe_constraint | 0 | 0 |
| gemini | noisy-18 | 3 | conflict_from_observation | 0 | 0 |
| gemini | conflicting-12 | 3 | scalar_from_constraint, missing_fields | 0 | 1 |
| gemini | multiple-12 | 3 | conflict_from_observation | 0 | 0 |
| gemini | noisy-10 | 3 | canonical_name, missing_fields | 0 | 0 |
| gemini | noisy-12 | 3 | canonical_name | 0 | 1 |
| gemini | basic-20 | 3 | canonical_name | 0 | 1 |
| gemini | multiple-15 | 3 | canonical_name, missing_fields | 0 | 1 |
