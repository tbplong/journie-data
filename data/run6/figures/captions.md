# Supervisor Report Figures

Source: completed run; 146 unique cases/model, 3 passes.
Warm-ups excluded. PNG files are 300 DPI; SVG files retain vector graphics and text.
Dataset SHA-256: `f58b495b87551a718cb5d01cdebbc1723b419ffb491d36c087f3f7230b3bd27b`.
Prompt variant: `semantic-v3`.

## Scoring Stages

Post-processing source: **recorded inline**, version `repair-v2`. Raw and processed views use the same provider/case/pass attempts; they are not additional requests or independent samples.
Repairs resolve contradictions inside each output using only its own values; needs_clarification is never lowered. Invalid outputs remain invalid. Field scores and hallucination can change; schema validity cannot.
All original raw fields are preserved for an external replay, whose source hashes must match this run.

## Models

| Label | Configured model | Returned model |
| --- | --- | --- |
| Gemini 3.5 Flash Lite | `gemini-3.5-flash-lite` | `gemini-3.5-flash-lite` |

## Figures

### Request outcomes
Files: `01_outcome_breakdown.png` and `01_outcome_breakdown.svg`.
Each attempt belongs to one outcome. Failures take precedence over JSON/schema errors, so empty responses are not double-counted. Paired rows compare raw versus post-processed scoring of the same attempts; do not sum their denominators. Fully correct means all gold fields and atoms match, not merely valid JSON.

### Category accuracy
Files: `02_category_accuracy.png` and `02_category_accuracy.svg`.
Color and large numbers show complete-case accuracy; smaller numbers show unchanged schema validity. Each model has raw and post-processed rows. Each cell shows its attempt count n; repeated passes are not independent cases. Trait columns (v3) overlap the primary categories and must not be summed with them.

### Observed latency
Files: `04_latency_distribution.png` and `04_latency_distribution.svg`.
Empirical cumulative distributions include all attempts, regardless of validity/correctness. P50/P95 use the runner's inclusive linear interpolation. The logarithmic axis exposes slow tails without hiding failures. Latency includes network and server queueing, not deliberate inter-request pacing. It is HTTP timing only, not post-processed pipeline timing.

### Quality-speed trade-off
Files: `05_quality_vs_latency.png` and `05_quality_vs_latency.svg`.
Circles show raw accuracy. Hollow diamonds show processed accuracy. Both use the same HTTP median on the x axis; processing overhead is NOT shown. Horizontal spans show HTTP P50 to P95, not confidence intervals. Do not interpret the shared x position as proof that processing is free or has measured equal latency. Historical end-to-end pipeline latency can be unavailable. No threshold is established.

### Unsupported facts with validity context
Files: `07_hallucination_diagnostic.png` and `07_hallucination_diagnostic.svg`.
Hallucination is conditional on schema-valid outputs. Numerator/denominator labels are shown alongside validity among all attempts. It includes nongold atoms or unsupported scalar/list facts, including incorrect extracted values, not a diagnosis of model intent. Different valid subsets prevent interpreting this rate independently of validity. Faded bars are raw hallucination, solid bars post-processed; validity is unchanged.

### Field-accuracy partition
Files: `08_field_accuracy_diagnostic.png` and `08_field_accuracy_diagnostic.svg`.
Populated and empty/null accuracy each pool the scorer's correct/total counts across all attempts. The populated group includes needs_clarification. Empty/null fields dominate the comparisons; high aggregate field accuracy is not equivalent to correct constraint extraction. Those field scores exclude missing_fields. Faded bars are raw, solid bars post-processed field scores. The second panel compares exact missing-field-list accuracy before and after processing, divided by ALL attempts. Invalid outputs get no credit, even when the gold missing-field list is empty.

## Key Numbers

| Model | Raw exact | Processed exact | Change (pp) | Missing-list accuracy, raw -> post | Schema valid | HTTP P50 / P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Gemini 3.5 Flash Lite | 68.9% | 80.8% | +11.9 | 82.2% -> 97.9% | 100.0% | 2.29s / 6.65s |

## Interpretation And Limits

Raw exact accuracy describes the model's first-pass response. Processed accuracy describes that response plus deterministic application logic, not better model weights or a second inference. Report them separately.

When multiple Gemma variants are present, this is not a controlled same-model comparison of local versus cloud hosting. Gold annotations are draft and exact-match wording matters. Providers ran in sequential blocks; no confidence intervals or population-generalization claims are made. Repeated passes observe the same cases, not independent new cases. Hosting resources, billing and cold starts were not measured. Pacing may differ, so intrinsic throughput is not plotted.

Annotation conventions can affect exact-match scores; consult the dataset's applicable changelog, including any missing-destination ambiguity. No cases, prompts, scores or original run artifacts were changed.

## Auditable Plot Data

`chart_data.json` contains aggregate counts, curves and source hashes. Rows were rescored against hash-matching gold, then aggregated and checked against raw/processed summaries. Exact missing-field-list accuracy is derived from item error counts and schema flags. Raw prompts, responses, credentials, endpoints and config notes are not copied into this report.
