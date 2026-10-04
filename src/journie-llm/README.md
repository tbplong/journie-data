# Journie LLM Extraction Benchmark

Research question: can a remotely self-hosted small/medium LLM extract travel
constraints as accurately as a free cloud LLM, and what are the trade-offs in
client-observed latency, token usage, and reliability?

The pipeline is **request -> LLM -> JSON -> Pydantic validation -> gold scoring**.
There is no solver, itinerary generation, retrieval, web access, POI lookup, or
LLM judge. All endpoints are called from this machine. No new dependencies are
required; HTTPX and Pydantic already belong to this repository.

## Configure

The configured `providers.json` includes two Hugging Face Inference Providers
baselines using the existing OpenAI-compatible adapter:

| Config name | Pinned model and inference provider |
| --- | --- |
| `huggingface-qwen` | `Qwen/Qwen3-Next-80B-A3B-Instruct:deepinfra` |
| `huggingface-deepseek` | `deepseek-ai/DeepSeek-V3-0324:deepinfra` |

Both are general non-thinking instruction/chat variants. DeepInfra currently
advertises structured-output support for both, so the configurations request the
same strict schema, temperature, and output cap. The `:deepinfra` suffix pins the
inference provider instead of selecting the fastest provider dynamically. No
provider-specific request extensions or automatic model-selection experiments
are configured. Smoke-test schema compatibility before a full run; advertised
support does not establish compatibility with every JSON Schema feature.

Create a [fine-grained Hugging Face token](https://huggingface.co/settings/tokens)
with **Make calls to Inference Providers** permission and set `HF_TOKEN`:

```bash
export HF_TOKEN='YOUR_HUGGING_FACE_TOKEN'
```

Both configs use `https://router.huggingface.co/v1`; Hugging Face routes the calls
to DeepInfra, and the benchmark records the returned model ID when available.

```bash
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json \
  --provider huggingface-qwen --provider huggingface-deepseek \
  --limit 5 --passes 1 --output data/huggingface-comparison-smoke
```

This smoke comparison uses 16 requests including warm-ups, assuming the default
three warm-ups per configuration. No live generation requests have been made to
verify it or spend credits.

**These are paid inference models accessible using limited free credits, not
zero-priced models.** Hugging Face currently documents **$0.10 monthly credits
for free accounts**, subject to change. Both configurations share this allowance;
it may not cover a full benchmark. Once exhausted, further requests can consume
purchased credits if available. The runner neither checks the remaining balance
nor enforces a spending cap. Check [HF pricing](https://huggingface.co/docs/inference-providers/pricing)
and your [billing settings](https://huggingface.co/settings/billing) before running.
Avoid purchased credits/custom provider keys if the experiment must incur no
charges; custom provider keys are billed directly and do not use HF free credits.
Stop testing when the allowance is exhausted rather than treating payment errors
as model-quality evidence.

`providers.json` also retains your Gemini and remote LM Studio Gemma settings.
Configure their base URLs and exact model IDs before running; endpoint/model
availability changes, so the benchmark does not silently select another model.

Credentials are read from the environment variables named by `api_key_env`.
Never put credentials in the JSON, URLs, notes, or `extra_body`: configuration
is saved in the run manifest. Configure optional LM Studio authentication with
its own `api_key_env` if needed. Missing configured keys stop the run before
creating output files. HTTP redirect following is disabled.

On the other machine, start LM Studio's OpenAI-compatible server and allow LAN
access only from trusted clients. The URL ends in `/v1`, not `/chat/completions`.
An unauthenticated HTTP server must not be exposed publicly; use a trusted LAN,
VPN, or authenticated HTTPS gateway. Obtain the exact loaded model IDs from LM
Studio's UI or `/v1/models`. Load the intended model before each measurement;
running the two local models separately avoids model swapping and GPU contention.

Use the same temperature and output cap across configurations. Structured output
uses Gemini's `responseJsonSchema` or OpenAI's strict `json_schema` response
format. If an endpoint cannot enforce the schema, explicitly set
`structured_output: false`; the same schema remains in the shared prompt. Record
that difference and do not claim identical enforcement or silently retry without
it. Unknown/unsupported generation settings produce visible request failures.

`extra_body` supports endpoint-specific extensions but cannot override shared
request fields or enable tools. Record reasoning mode, quantization, context
length, LM Studio version, and CPU/GPU/offload settings in `notes`. There is no
attempt to assume identical tokenizers, reasoning behavior, or server defaults.

## Run

From the repository root, after configuring the example or another JSON array:

```bash
uv run python src/journie-llm/benchmark.py --help
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json \
  --provider lmstudio-gemma --limit 3 --passes 1 \
  --output data/llm-smoke-gemma
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json \
  --provider lmstudio-gemma --output data/llm-gemma
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json \
  --provider gemini --provider huggingface-qwen --provider huggingface-deepseek \
  --output data/llm-cloud
```

Output directories must not exist; older measurements are never overwritten.
No `--provider` selection means all configured models run sequentially.

Defaults: **3 warm-up requests, 3 measured passes, concurrency 1, seed 42**.
Each model receives the same seeded shuffle in each pass. A full run makes 363
calls per model. Pacing is configurable per endpoint to respect free-tier quotas;
15 seconds is an example, not a guarantee of quota compliance. Daily quotas may
require measuring separate passes on different days. Do not hammer rate-limited
services: use the smoke run first and adjust pacing before a full run.

Requests are non-streaming. Latency measures the request through complete
response reception, including network and provider queueing, but excludes local
validation/scoring. Warm-ups are separately recorded and excluded from measured
results. There are no retries, JSON repairs, model fallbacks, or automatic cold
starts. A warm-up failure is visible but does not abort subsequent requests.

## Cases And Annotations

`cases.jsonl` is **dataset v2**, with 120 manually authored **draft** cases, 20 per category:
basic, multiple, missing, conflicting, noisy, and adversarial. These annotations
have structural/consistency checks, **not independent human review**. Review and
freeze them before presenting the benchmark as research evidence; do not tune
the prompt against measured results without labeling a new benchmark version.
See [the dataset changelog](cases.changelog.md) for revised IDs, annotation
conventions, version hashes, coverage counts, and a separately reported contract
wording caveat. The schema and shared prompt are unchanged. Reproduce dataset
validation and coverage with `uv run python src/journie-llm/test_cases.py`.

Each case has `id`, `category`, `user_input`, `expected_output`,
`expected_constraints`, `expected_missing_fields`, and `expected_conflicts`.
Redundant annotations are checked against the full gold output at load time.

`schema.py` defines the authoritative shared prompt and extraction contract:

- All keys are required, extra keys are forbidden, and scalar types are strict.
- Unknown scalar values stay `null`; unknown lists stay empty. No planning defaults.
- Dates use ISO format, times use `HH:MM`, and explicitly denominated budgets use
  integer VND. Foreign currency amounts are not converted.
- Relative dates use **2026-10-03**, timezone **Asia/Ho_Chi_Minh**, never run time.
- Canonical destination/interest aliases are documented in the shared prompt.
  Named places retain their stated wording; there is no POI identity resolution.
- Constraints are atoms with `field`, `operator`, and a canonical string `value`.
  Facts, obligations, limits, and exclusions are hard; interests and preferences
  are soft. Visit duration is represented by a place-specific atom.
- `missing_fields` tracks absence of destination, dates/duration, usable budget,
  and exact available time. It is informational, not a solver-required-field list.
- Omission alone does not set `needs_clarification`. Explicit numeric ambiguity
  or logical contradictions do. Detect only the supported contradiction codes;
  do not guess whether a trip is feasible from unstated POI knowledge.

Pydantic checks types, allowed values, actual calendar dates, and time formats.
It does not reject contradictory requests: faithful extraction must preserve
their conflicting constraints. Schema validity is not business-rule validity.

## Outputs And Metrics

Every run writes:

- `manifest.json`: model/config/settings, full prompt/schema, hashes, reference
  policy, prompt variant/version, processing/diagnostic versions, selected case
  count, timestamps, and completion status.
- `results.jsonl`: one row per measured attempt with provider/model/case/pass,
  raw model text, parsed output, validation errors, finish reason, latency,
  reported token counts, returned model/version when available, failures, and
  deterministic quality scores. Check returned IDs for unintended routing changes.
- `warmups.jsonl`: excluded warm-up attempts, including their failures and tokens.
- `summary.json` and `summary.csv`: overall and per-category comparisons.
- `processed_summary.json` and `processed_summary.csv`: separately scored outputs
  after deterministic missing-field processing, plus diagnostic and timing metrics.

Responses are flushed after each request; interrupted runs retain completed
measurements and set `complete: false`. Do not compare partial runs as full runs.
Result files may contain personal information if you supply private cases.
HTTP/network errors store sanitized failure types/status codes, not secret-bearing
request URLs or headers. Remote resource usage is `null`, not zero.

Quality denominators include failed requests: they receive zero field/case
accuracy and all gold atoms become false negatives. Metrics are:

- JSON parse rate and strict Pydantic/schema-valid rate, separately.
- Exact field accuracy, plus populated-field and empty/null-field accuracy.
- Constraint micro precision/recall/F1, ignoring hard/soft labels initially.
- Labeled constraint micro precision/recall/F1 and hard/soft accuracy conditional
  on correctly extracted atoms. Misclassification is not a hallucinated atom.
- Missing-field and explicit-conflict micro precision/recall/F1.
- Complete-case accuracy: every field and atom must match. List order does not
  matter, but duplicates do; arbitrary fuzzy wording is not accepted.
- Hallucination case rate **among schema-valid outputs**: any nongold atom or
  unsupported scalar/list value counts. This is an operational unsupported-fact
  measure and includes incorrect extracted values, not an independent diagnosis
  of the model's internal cause. Invalid outputs cannot be reliably scored for
  hallucination and are instead captured by validity and failed quality scores.

This is gold-based exact scoring, not an LLM judge. Original wording in `other`
atoms and canonicalization choices can influence scores; review mismatches
qualitatively before interpreting them as semantic failures.

Performance includes P50/P95 complete-response latency for successful HTTP
responses (even invalid extraction), P50/P95 for all attempts, request failure,
timeout and rate-limit rates, and observed sequential throughput. Throughput
includes pacing and local result processing but excludes warm-up. Category
throughput is unavailable because categories are interleaved. Percentiles use
linear interpolation; repeated passes are repeated observations, not independent
new cases or statistical confidence intervals.

Token means/totals use **reported counts only**, with coverage counts. Missing
usage stays `null`; no estimation or cross-tokenizer equivalence is assumed.
Gemini candidate/output tokens and OpenAI completion tokens can account for
reasoning differently; reported reasoning tokens are saved separately, and
reported totals are not reconstructed or double-counted. Warm-up usage is in
`warmups.jsonl`, not the measured summary. Neither API billing nor local power/
hardware cost is inferred from token counts. Free availability must be verified
at run time; this tool does not guarantee zero charges.

## Extraction Improvements

The runner now records **both raw and processed results**. No model weights,
schema, gold labels, or existing run files are changed. There is still no retry,
JSON repair, extra model call, or automatic correction of semantic facts.

### Missing Fields

`processing.py` implements `missing-fields-v1`. On a schema-valid extraction it
deep-copies the object and changes only `missing_fields`, in the original order:
null destination; no start/end date or trip duration; null VND budget; missing
either exact availability endpoint. Zero VND counts as populated. Foreign or
minimum-only budgets do not become scalar caps. Existing conflicts and
`needs_clarification` remain unchanged; missing information is informational.

This retains the dataset's current null-destination convention, including
conflicting destinations. The existing wording ambiguity is documented in
`cases.changelog.md`; this stage does not redefine the contract.

### Prompt Variants

`--prompt-variant baseline` is the default and reproduces the original system
prompt byte-for-byte. `--prompt-variant reinforced` appends a clause-to-atom
checklist and independently authored atom illustrations. Both preserve the full
authoritative prompt, schema, fixed reference date, and generation settings.
Illustrations are not copied from benchmark rows and are labeled as partial atom
examples, not complete outputs. All providers receive the same chosen prompt.
The added prompt may increase input tokens/latency; improvement is not guaranteed.

Example live smoke experiment (requires a configured endpoint; may consume quota):

```bash
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json --provider lmstudio-gemma \
  --prompt-variant reinforced --limit 6 --passes 1 \
  --output data/reinforced-smoke
```

### Diagnostic Checks

`consistency-v1` checks duplicates, scalar/list-to-atom correspondence,
unambiguous field/operator/hard-soft rules, and mechanically observable
date/duration, budget-bound, and exact must-visit/avoid contradictions. Properly
preserved contradictory requests are not errors merely because they conflict.
The checks never modify atoms, values, conflicts, or clarification flags.

Diagnostics use `error` for an internal representation mismatch and `review`
when evidence is insufficient. They cannot prove grounding in the user's text,
detect every omitted clause, or infer itinerary feasibility. Reversed clock
endpoints always require review: no day offset is represented, and no naive
overnight-keyword or negation heuristic is used. Multiple destination names alone
do not establish single-destination disagreement. A true clarification flag is
not cleared just because conflict codes are absent.

### Result Separation

`raw_response`, `parsed_output`, `metrics`, schema flags, HTTP latency and reported
tokens retain their original meanings. Added row fields are `processed_output`,
`processed_metrics`, `missing_fields_changed`, `consistency_issues`,
`validation_seconds`, `postprocessing_seconds`, and
`extraction_pipeline_latency_seconds`. Invalid/failed outputs are not processed:
their processed output and diagnostics are null and their processed quality
scores remain failed. Warm-ups are excluded from both summaries.

Live pipeline latency sums HTTP, JSON/schema validation, and processing/diagnostic
durations, excluding both benchmark gold-scoring operations. Diagnostics run on
the processed object. Their rates use checked schema-valid outputs, not failed
attempts; review rates are separate from error rates. Accuracy still uses all
attempts, and hallucination still uses schema-valid outputs. Existing figures
continue to read **raw** summaries; no graph is silently relabeled as processed.

### Offline Replay

Reprocess saved first-pass results into a new directory, with no credentials or
API calls required:

```bash
uv run python src/journie-llm/replay_processing.py \
  --run data/run1 --output data/run1-processing
```

Replay requires matching case/schema hashes, consistent saved raw/parsed results
and scores, and a complete provider/case/pass grid. It refuses existing outputs
and source-run descendants. It writes enhanced rows, separate raw/processed
summaries, a safe provenance manifest, and `report.md` with gained/lost exact cases.
Raw latency/tokens remain historical. Postprocessing time is measured now;
historical end-to-end pipeline latency remains unknown, not manufactured by
adding today's processing to an old HTTP measurement. No new tokens are used.

This is a **retrospective pipeline experiment**, not improved first-pass model
accuracy or evidence that the reinforced prompt works. The inspected v2 corpus
is development data. Compare baseline/raw, baseline/processed, reinforced/raw,
and reinforced/processed on fresh independently reviewed held-out cases before
claiming general improvement. Split by case/paraphrase family, not repeated pass.

There is **no winner threshold**. Compare quality, latency, tokens, failure rates,
and category weaknesses. RAM/VRAM, controlled cold-start measurements, concurrent
load testing, actual billing, and solver integration are outside this version.

## Supervisor Report Figures

Generate the six selected report figures from a completed run without calling
models or adding a project dependency:

```bash
uv run --with 'matplotlib>=3.10,<4' python src/journie-llm/plot_report.py --run data/run1
```

The default output is a new `data/run1/figures/` directory containing 300-DPI PNG
and vector SVG pairs, `captions.md`, and auditable `chart_data.json`. Existing
output directories are not overwritten; use `--output` for another destination.
Matplotlib's version is recorded in the chart data. The script recomputes selected
metrics from measured requests and checks them against saved summaries. No raw
responses, credentials, endpoint addresses, or configuration notes are exported.
Outcome categories are exclusive; latency includes invalid/failed attempts;
hallucination is conditioned on schema validity. Warm-ups are excluded everywhere.

## Offline Verification

```bash
uv run --extra dev pytest src/journie-llm
uv run --extra dev ruff check src/journie-llm
uv run --extra dev mypy src/journie-llm
```

Tests use mocked HTTP responses, including valid output, refusal, malformed
JSON, invalid schema, timeouts, network errors, and quota failures. No API keys,
network calls, or LM Studio installation are required for these tests.
