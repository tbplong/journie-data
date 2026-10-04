# Supervisor Report Figures

Source: completed run; 120 unique cases/model, 3 passes.
Warm-ups excluded. PNG files are 300 DPI; SVG files retain vector graphics and text.
Dataset SHA-256: `ae2c1fe0757f95c268948ec18e4392db60d8d057025292553af94b2c1e3629c2`.

## Models

| Label | Configured model | Returned model |
| --- | --- | --- |
| Gemini 3.5 Flash Lite | `gemini-3.5-flash-lite` | `gemini-3.5-flash-lite` |
| Cloud Gemma 4 26B A4B | `gemma-4-26b-a4b-it` | `gemma-4-26b-a4b-it` |
| LM Studio Gemma 4 E4B Q4 | `google/gemma-4-E4B-it-qat-q4_0-gguf` | `gemma-4-e4b-it-qat` |

## Figures

### Request outcomes
Files: `01_outcome_breakdown.png` and `01_outcome_breakdown.svg`.
Each attempt belongs to one outcome. Failures take precedence over JSON/schema errors, so empty responses are not double-counted. Fully correct means all scored gold fields and atoms match, not merely valid JSON.

### Category accuracy
Files: `02_category_accuracy.png` and `02_category_accuracy.svg`.
Color and large numbers show complete-case accuracy; smaller numbers show schema validity. Each cell has 20 unique cases repeated three times, not 60 independent cases.

### Observed latency
Files: `04_latency_distribution.png` and `04_latency_distribution.svg`.
Empirical cumulative distributions include all attempts, regardless of validity/correctness. P50/P95 use the runner's inclusive linear interpolation. The logarithmic axis exposes the cloud Gemma tail without hiding failures. Latency includes network and server queueing, not deliberate inter-request pacing; there are no recorded timeouts here.

### Quality-speed trade-off
Files: `05_quality_vs_latency.png` and `05_quality_vs_latency.svg`.
Dots use all-attempt median latency and complete-case accuracy. Horizontal spans extend to P95; they are latency tails, not uncertainty intervals. Upper-left is preferable. The plot establishes no quality or latency acceptance threshold.

### Unsupported facts with validity context
Files: `07_hallucination_diagnostic.png` and `07_hallucination_diagnostic.svg`.
Hallucination is conditional on schema-valid outputs. Numerator/denominator labels are shown alongside validity among all attempts. It includes nongold atoms or unsupported scalar/list facts, including incorrect extracted values, not a diagnosis of model intent. Different valid subsets prevent interpreting this rate independently of validity.

### Field-accuracy partition
Files: `08_field_accuracy_diagnostic.png` and `08_field_accuracy_diagnostic.svg`.
Populated and empty/null accuracy each pool the scorer's correct/total counts across all attempts. The populated group includes needs_clarification. Empty/null fields dominate the comparisons; high aggregate field accuracy is not equivalent to correct constraint extraction.

## Key Numbers

| Model | Exact | Schema valid | P50 / P95 | Hallucinated / valid |
| --- | ---: | ---: | ---: | ---: |
| Gemini 3.5 Flash Lite | 54.4% | 100.0% | 1.68s / 2.16s | 99/360 |
| Cloud Gemma 4 26B A4B | 1.7% | 30.0% | 6.52s / 88.43s | 66/108 |
| LM Studio Gemma 4 E4B Q4 | 18.6% | 100.0% | 24.91s / 39.12s | 131/360 |

## Interpretation And Limits

Gemini has the highest exact accuracy and lowest median latency in this run. LM Studio Gemma returns valid output but trails in complete-case accuracy. Cloud Gemma loses substantial end-to-end utility to output-validity failures.

These are different Gemma model variants, including a quantized local artifact: not a controlled same-model comparison of local versus cloud hosting. Gold annotations are draft and exact-match wording matters. Providers ran in sequential blocks; no confidence intervals or population-generalization claims are made. Three passes are repeated observations of the same 120 cases. Hosting resources, billing and cold starts were not measured. Pacing differs, so intrinsic throughput is not plotted.

The dataset v2 changelog documents a missing-destination wording ambiguity affecting four cases. No cases, prompts, scores or original run artifacts were changed.

## Auditable Plot Data

`chart_data.json` contains aggregate counts, curves and source hashes. All plotted metrics were recomputed from measured rows and checked against the saved summary. Raw prompts, responses, credentials, endpoint addresses and config notes are not copied into this report.
