# Journie LLM Extraction Benchmark

Research question: can a remotely self-hosted small/medium LLM extract travel constraints as accurately as a free cloud LLM, and what are the trade-offs in client-observed latency, token usage, and reliability?

The pipeline is **request -> LLM -> JSON -> Pydantic validation -> gold scoring**. There is no solver, itinerary generation, retrieval, web access, POI lookup, or LLM judge. All endpoints are called from this machine. No new dependencies are required; HTTPX and Pydantic already belong to this repository.

## Configure

The configured `providers.json` includes two Hugging Face Inference Providers baselines using the existing OpenAI-compatible adapter:

| Config name | Pinned model and inference provider |
| --- | --- |
| `huggingface-qwen` | `Qwen/Qwen3-Next-80B-A3B-Instruct:deepinfra` |
| `huggingface-deepseek` | `deepseek-ai/DeepSeek-V3-0324:deepinfra` |

Both are general non-thinking instruction/chat variants. DeepInfra currently advertises structured-output support for both, so the configurations request the same strict schema temperature, and output cap. The `:deepinfra` suffix pins the inference provider instead of selecting the fastest provider dynamically. No provider-specific request extensions or automatic model-selection experiments are configured. Smoke-test schema compatibility before a full run advertised support does not establish compatibility with every JSON Schema feature.

Create a [fine-grained Hugging Face token](https://huggingface.co/settings/tokens) with **Make calls to Inference Providers** permission and set `HF_TOKEN`:

```bash
export HF_TOKEN='YOUR_HUGGING_FACE_TOKEN'
```

Both configs use `https://router.huggingface.co/v1`; Hugging Face routes the calls to DeepInfra, and the benchmark records the returned model ID when available.

```bash
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json \
  --provider huggingface-qwen --provider huggingface-deepseek \
  --limit 5 --passes 1 --output data/huggingface-comparison-smoke
```

This smoke comparison uses 16 requests including warm-ups, assuming the default three warm-ups per configuration. No live generation requests have been made to verify it or spend credits.

**These are paid inference models accessible using limited free credits, not zero-priced models.** Hugging Face currently documents **$0.10 monthly credits for free accounts**, subject to change. Both configurations share this allowance; it may not cover a full benchmark. Once exhausted, further requests can consume purchased credits if available. The runner neither checks the remaining balance nor enforces a spending cap. Check [HF pricing](https://huggingface.co/docs/inference-providers pricing) and your [billing settings](https://huggingface.co/settings/billing) before running. Avoid purchased credits/custom provider keys if the experiment must incur no charges; custom provider keys are billed directly and do not use HF free credits. Stop testing when the allowance is exhausted rather than treating payment errors as model-quality evidence.

`providers.json` also retains your Gemini and remote LM Studio Gemma settings. Configure their base URLs and exact model IDs before running; endpoint/model availability changes, so the benchmark does not silently select another model.

Credentials are read from the environment variables named by `api_key_env`. Never put credentials in the JSON, URLs, notes, or `extra_body`: configuration is saved in the run manifest. Configure optional LM Studio authentication with its own `api_key_env` if needed. Missing configured keys stop the run before creating output files. HTTP redirect following is disabled.

On the other machine, start LM Studio's OpenAI-compatible server and allow LAN access only from trusted clients. The URL ends in `/v1`, not `/chat/completions`. An unauthenticated HTTP server must not be exposed publicly; use a trusted LAN, VPN, or authenticated HTTPS gateway. Obtain the exact loaded model IDs from LM Studio's UI or `/v1/models`. Load the intended model before each measurement; running the two local models separately avoids model swapping and GPU contention.

Use the same temperature and output cap across configurations. Structured output uses Gemini's `responseJsonSchema` or OpenAI's strict `json_schema` response format. If an endpoint cannot enforce the schema, explicitly set `structured_output: false`; the same schema remains in the shared prompt. Record that difference and do not claim identical enforcement or silently retry without it. Unknown/unsupported generation settings produce visible request failures.

`extra_body` supports endpoint-specific extensions but cannot override shared request fields or enable tools. Record reasoning mode, quantization, context length, LM Studio version, and CPU/GPU/offload settings in `notes`. There is no attempt to assume identical tokenizers, reasoning behavior, or server defaults.

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

Output directories must not exist; older measurements are never overwritten. No `--provider` selection means all configured models run sequentially.

Defaults: **3 warm-up requests, 3 measured passes, concurrency 1, seed 42**. Each model receives the same seeded shuffle in each pass. A full run makes 363 calls per model. Pacing is configurable per endpoint to respect free-tier quotas; 15 seconds is an example, not a guarantee of quota compliance. Daily quotas may require measuring separate passes on different days. Do not hammer rate-limited services: use the smoke run first and adjust pacing before a full run.

Requests are non-streaming. Latency measures the request through complete response reception, including network and provider queueing, but excludes local validation/scoring. Warm-ups are separately recorded and excluded from measured results. There are no retries, JSON repairs, model fallbacks, or automatic cold starts. A warm-up failure is visible but does not abort subsequent requests.

## Cases And Annotations

`cases_v3.jsonl` is **dataset v3** (146 draft cases, hierarchy below); `cases.jsonl` remains **dataset v2**, with 120 manually authored **draft** cases, 20 per category: basic, multiple, missing, conflicting, noisy, and adversarial. These annotations have structural/consistency checks, **not independent human review**. Review and freeze them before presenting the benchmark as research evidence; do not tune the prompt against measured results without labeling a new benchmark version. See [the dataset changelog](cases.changelog.md) for revised IDs, annotation conventions, version hashes, coverage counts, and a separately reported contract wording caveat. The schema and shared prompt are unchanged. Reproduce dataset validation and coverage with `PYTHONPATH=src/journie-llm uv run python src/journie-llm/tests/test_cases.py`.

Each case has `id`, `category`, (v3 only: `user_part`, `non_user_part`, `traits`), `user_input`, `expected_output`, `expected_constraints`, `expected_missing_fields`, and `expected_conflicts`. Redundant annotations are checked against the full gold output at load time.

`schema.py` defines the authoritative shared prompt and extraction contract:

- All keys are required, extra keys are forbidden, and scalar types are strict.
- Unknown scalar values stay `null`; unknown lists stay empty. No planning defaults.
- Dates use ISO format, times use `HH:MM`, and explicitly denominated budgets use integer VND. Foreign currency amounts are not converted.
- Relative dates use **2026-10-03**, timezone **Asia/Ho_Chi_Minh**, never run time.
- Canonical destination/interest aliases are documented in the shared prompt. Named places retain their stated wording; there is no POI identity resolution.
- Constraints are atoms with `field`, `operator`, and a canonical string `value`. Facts, obligations, limits, and exclusions are hard; interests and preferences are soft. Visit duration is represented by a place-specific atom.
- `missing_fields` tracks absence of destination, dates/duration, usable budget, and exact available time. It is informational, not a solver-required-field list.
- Omission alone does not set `needs_clarification`. Explicit numeric ambiguity or logical contradictions do. Detect only the supported contradiction codes; do not guess whether a trip is feasible from unstated POI knowledge.

Pydantic checks types, allowed values, actual calendar dates, and time formats. It does not reject contradictory requests: faithful extraction must preserve their conflicting constraints. Schema validity is not business-rule validity.

### Test Case Categories

**Dataset v3** (`cases_v3.jsonl`, 146 draft cases, default for new runs) labels each case with a hierarchy instead of six flat categories. `cases.jsonl` (v2) is unchanged so earlier runs still verify and replay; replay, figures, and `prompt_stats.py` pick the dataset whose hash the run recorded.

```
Input
├── user input: travel-related requirements
│   ├── basic      1–2 requirement types
│   └── multiple   3 or more requirement types
│      traits on either: noisy, missing, conflicting
└── non-user input: requests not about travel extraction
    ├── adversarial  instructions that try to change the extraction
    └── off_topic    other non-travel requests, without attacking the extractor
```

- A **requirement type** is a distinct constraint `field` in the gold output (destination, dates, duration, budget, availability endpoints, interests, must-visit, avoid, preferred places, visit duration, other). It is counted from gold, not hand-labelled.
- A case may contain both parts: `user_part` (`basic`/`multiple`/null) and `non_user_part` (`adversarial`/`off_topic`/null). `category` is the reporting label: the non-user part if present, otherwise the user part.
- **noisy**: the input contains text that is not a requirement for this trip (asides, quoted examples, logs, other people's trips, order numbers, apologies, repeated restatements). **Clean** input contains only requirements. Noisy text is extra information; `off_topic` is a request to do something else; `adversarial` is an instruction to change the extraction.
- **missing**: the user says information is absent, or gives a vague or partial value ("cheap", "morning", bare "500", a start time without an end). Assigned from v2 `missing` cases plus any case whose gold needs clarification without a conflict.
- **conflicting**: the gold output declares at least one conflict (checked automatically).
- **Adversarial gold is rejection, not extraction (v3).** Any instruction that tries to change the extractor's role, task, format, rules, values, conflicts, or classification makes the whole message rejected: every field null or empty, all four `missing_fields`, no conflicts, `needs_clarification: false`, even when genuine travel facts appear beside the attack. `user_part`/`traits` still describe the travel text inside the attack (e.g. `adversarial-15` is basic + conflicting). Rejection is indistinguishable in the output from an off-topic-only message; the category label separates them in reports. Off-topic mixed requests and noisy text are not attacks and are still extracted.

Summaries report the primary categories plus every hierarchy node (`user_input`, `user_input:basic`, `non_user_input:off_topic`, `trait:noisy`, …); one case can count in several nodes, so node rows must not be summed.

The 120 v2 cases keep id, order, and input; gold is unchanged except the 20 adversarial cases, which now expect rejection. New in v3: 20 `off_topic` cases (10 off-topic only, 10 mixed with a travel request) and 6 `interest-*` cases for mandatory versus preferred interests. See [the dataset changelog](cases.changelog.md).

## Outputs And Metrics

Every run writes:

- `manifest.json`: model/config/settings, full prompt/schema, hashes, reference policy, prompt variant/version, processing/diagnostic versions, selected case count, timestamps, and completion status.
- `results.jsonl`: one row per measured attempt with provider/model/case/pass, raw model text, parsed output, validation errors, finish reason, latency, reported token counts, returned model/version when available, failures, and deterministic quality scores. Check returned IDs for unintended routing changes.
- `warmups.jsonl`: excluded warm-up attempts, including their failures and tokens.
- `summary.json` and `summary.csv`: overall and per-category comparisons.
- `processed_summary.json` and `processed_summary.csv`: separately scored outputs after deterministic missing-field processing, plus diagnostic and timing metrics.

Responses are flushed after each request; interrupted runs retain completed measurements and set `complete: false`. Do not compare partial runs as full runs. Result files may contain personal information if you supply private cases. HTTP/network errors store sanitized failure types/status codes, not secret-bearing request URLs or headers. Remote resource usage is `null`, not zero.

Quality denominators include failed requests: they receive zero field/case accuracy and all gold atoms become false negatives. Metrics are:
- JSON parse rate and strict Pydantic/schema-valid rate, separately.
- Exact field accuracy, plus populated-field and empty/null-field accuracy.
- Constraint micro precision/recall/F1, ignoring hard/soft labels initially.
- Labeled constraint micro precision/recall/F1 and hard/soft accuracy conditional on correctly extracted atoms. Misclassification is not a hallucinated atom.
- Missing-field and explicit-conflict micro precision/recall/F1.
- Complete-case accuracy: every field and atom must match. List order does not matter, but duplicates do; arbitrary fuzzy wording is not accepted.
- Hallucination case rate **among schema-valid outputs**: any nongold atom or unsupported scalar/list value counts. This is an operational unsupported-fact measure and includes incorrect extracted values, not an independent diagnosis of the model's internal cause. Invalid outputs cannot be reliably scored for hallucination and are instead captured by validity and failed quality scores.

This is gold-based exact scoring, not an LLM judge. Original wording in `other` atoms and canonicalization choices can influence scores; review mismatches qualitatively before interpreting them as semantic failures.

Performance includes P50/P95 complete-response latency for successful HTTP responses (even invalid extraction), P50/P95 for all attempts, request failure, timeout and rate-limit rates, and observed sequential throughput. Throughput includes pacing and local result processing but excludes warm-up. Category throughput is unavailable because categories are interleaved. Percentiles use linear interpolation; repeated passes are repeated observations, not independent new cases or statistical confidence intervals.

Token means/totals use **reported counts only**, with coverage counts. Missing usage stays `null`; no estimation or cross-tokenizer equivalence is assumed. Gemini candidate/output tokens and OpenAI completion tokens can account for reasoning differently; reported reasoning tokens are saved separately, and reported totals are not reconstructed or double-counted. Warm-up usage is in `warmups.jsonl`, not the measured summary. Neither API billing nor local power/ hardware cost is inferred from token counts. Free availability must be verified at run time; this tool does not guarantee zero charges.

## Extraction Improvements

The runner now records **both raw and processed results**. No model weights, schema, gold labels, or existing run files are changed. There is still no retry, JSON repair, extra model call, or automatic correction of semantic facts.

### Missing Fields

`processing.py` implements `missing-fields-v1`. On a schema-valid extraction it deep-copies the object and changes only `missing_fields`, in the original order: null destination; no start/end date or trip duration; null VND budget; missing either exact availability endpoint. Zero VND counts as populated. Foreign or minimum-only budgets do not become scalar caps. Existing conflicts and `needs_clarification` remain unchanged; missing information is informational.

This retains the dataset's current null-destination convention, including conflicting destinations. The existing wording ambiguity is documented in `cases.changelog.md`; this stage does not redefine the contract.

### Prompt Variants

`--prompt-variant baseline` is the default and reproduces the original system prompt byte-for-byte. `--prompt-variant reinforced` appends a clause-to-atom checklist and independently authored atom illustrations. Both preserve the full authoritative prompt, schema, fixed reference date, and generation settings. Illustrations are not copied from benchmark rows and are labeled as partial atom examples, not complete outputs. All providers receive the same chosen prompt. The added prompt may increase input tokens/latency; improvement is not guaranteed.

Example live smoke experiment (requires a configured endpoint; may consume quota):

```bash
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json --provider lmstudio-gemma \
  --prompt-variant reinforced --limit 6 --passes 1 \
  --output data/reinforced-smoke
```

### Structured Prompt Experiment

`--prompt-variant structured` selects `structured-v1` for every selected provider. It preserves the authoritative base prompt and schema, then appends organized instructions, three full independently authored input/output examples, and a short silent verification list. The techniques are **Instruction Structuring**, **Few-shot Prompting**, **Contrastive Examples** (embedded distinctions, not explicit bad JSON), **Output Format Constraints**, and **Self-Verification** (a short silent check, not chain-of-thought). The extraction contract, schema, gold labels, reference policy, and generation settings are unchanged. `baseline` remains the default; `baseline-v1` and `reinforced-v1` remain byte-identical.

The shared [active configuration](providers.json) contains only `gemini` and `lmstudio-gemma`; both receive the same chosen prompt. Only the cloud entry named `gemma` was removed and preserved exactly in the [single-item provider archive](archive/gemma-cloud.providers.json). The local `lmstudio-gemma` entry is unchanged. Example command (not executed; running it calls both endpoints and may consume quota):

```bash
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json \
  --provider gemini --provider lmstudio-gemma \
  --prompt-variant structured --limit 3 --passes 1 \
  --output data/structured-smoke
```

See the [prompt changelog](prompts.changelog.md) and complete rendered snapshots including the schema: [baseline-v1](archive/prompts/baseline-v1.txt), [reinforced-v1](archive/prompts/reinforced-v1.txt), and [structured-v1](archive/prompts/structured-v1.txt). Archived prompts are immutable: future edits require a new version and snapshot; never overwrite or delete archived versions. This experiment is empirically unverified: no live API calls were executed for this change, no improvement is claimed, and added input tokens or latency remain possible.

### Semantic Prompt Experiment

`--prompt-variant semantic` selects `semantic-v1`: the unchanged extraction contract, **Semantic Examples**, and a silent **Self-Verification** checklist, without serialized JSON Schema or full JSON output examples in the prompt. Schema enforcement still uses the native API configuration, identically across variants. Gemini receives instructions through `systemInstruction`, travel input through `contents`, and schema through `responseJsonSchema`; the local endpoint uses its existing system/user messages and strict `json_schema` response format. HTTPX, raw response capture, strict validation, and semantic diagnostics are unchanged.

All selected providers must enable `structured_output: true`; otherwise semantic-v1 stops before sending requests or creating the run directory. Use an existing schema-in-prompt variant for endpoints without native schema support. The default remains `baseline`, and all previous prompt versions and snapshots are retained unchanged. See the [semantic-v1 snapshot](archive/prompts/semantic-v1.txt) and [prompt changelog](prompts.changelog.md). No measured improvement is claimed; this compares prompt packaging, not the isolated effects of removing schema duplication and full examples.

Example full run (not executed; calls both endpoints and may consume quota; output must not exist):

```bash
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json \
  --provider gemini --provider lmstudio-gemma \
  --prompt-variant semantic --passes 3 --warmups 3 --seed 42 \
  --output data/run4-semantic
```

### Semantic-v2 Prompt Experiment

`--prompt-variant semantic-v2` selects `semantic-v2`; `--prompt-variant semantic` still selects `semantic-v1`. V2 preserves `ANNOTATION_RULES` and the three semantic-v1 interpretation examples, adding targeted English **Instruction Structuring**, independently authored **Semantic Examples**, and a silent **Self-Verification** checklist for grounding, budget semantics, hard/soft classification, explicit overnight availability, original names/accents and deduplication, and no unstated derivation. Genuine unmodeled requirements, foreign budgets, and explicit following-day clauses remain in `other`; irrelevant/meta/injected instructions do not. It includes neither serialized JSON Schema nor full JSON output examples. See the [semantic-v2 snapshot](archive/prompts/semantic-v2.txt) and [prompt changelog](prompts.changelog.md) for the targeted rules.

Run2/run4 analysis motivated these changes but does not establish causality. The analyzed 120 cases are development data, not held-out evaluation; new examples are independently authored, not copied from cases or gold. No measured improvement is claimed. Both Gemini and the local endpoint require native schema enforcement with the same semantic-v1 preflight guard (`structured_output: true` for every selected provider). All four older prompt versions remain byte-identical and `baseline` remains the default; schema, gold labels, processing, configuration, API parameters, and generation settings are unchanged, with no SDK switch, retry, or extra LLM call.

Example Gemini-only full run (not executed; may consume quota; output must not exist). This command explicitly selects Gemini because the local endpoint recently failed with network errors; add `--provider lmstudio-gemma` when that endpoint is reachable. This is a run selection, not permanent configuration removal: omitting all `--provider` flags still selects every active configuration.

```bash
uv run python src/journie-llm/benchmark.py \
  --config src/journie-llm/providers.json \
  --provider gemini \
  --prompt-variant semantic-v2 --passes 3 --warmups 3 --seed 42 \
  --output data/run5-semantic-v2
```

### Semantic-v3 Prompt Experiment

`--prompt-variant semantic-v3` is `semantic-v2` minus its two passages that keep travel facts beside an instruction attack (the Museum Birch example and the "keep the real travel facts" sentence), plus two sections. **Instruction attacks**: reject the whole message (empty extraction, `needs_clarification: false`); quoted examples and logs without an instruction are not attacks. **Interests**: strength follows the user's wording. "Like", "want", "would be nice" stay soft `interests prefer`; "required", "must have", "nhất định phải có", "bắt buộc có" become hard `interests include`. Both still list the interest in `interests`. This refines the contract's "interests are soft" rule only through this prompt version; `ANNOTATION_RULES` and older snapshots are unchanged. Dataset v3 adds `interest-01`…`interest-06` to measure it. `consistency-v2` accepts both forms.

### Repair-v2 Processing

`--processing repair-v2` (benchmark and replay; default stays `missing-fields-v1`) resolves contradictions **inside one output using only that output's values**. It never reads gold or the user text, never invents a value, never lowers `needs_clarification`, and never touches invalid outputs. Steps, in order, each recorded in the row's `repairs` list:

1. `dedupe_constraint`: drop exact repeats within one constraint list (a hard/soft pair is left for diagnostics).
2. `list_from_constraint` / `constraint_from_list`: sync `must_visit`, `avoid`, `preferred_places` with their constraints; interests only gain list entries, never constraints, because their hard/soft strength depends on wording. A value that already has a misplaced constraint (e.g. hard `prefer`) is not given a second one.
3. `scalar_from_constraint`: set a scalar from its constraint when exactly one value is stated; for budget, the cap wins (contract: conflicting bounds keep the maximum).
4. `conflict_from_observation`: add or remove date/duration/budget/must-visit-avoid conflicts that the output's own values prove or disprove; unverifiable ones stay as declared. Two destinations are never treated as a conflict (multi-city trips).
5. `clarification_from_conflict`: any remaining conflict sets `needs_clarification`.
6. `missing_fields`: v1 derivation.

Measured on saved Gemini runs scored against dataset v2 gold (no new requests): exact cases after v1 → after v2 were run5 282 → 286, run4 +7, run3 +12, run2 +15, run1 +63 (Gemini and Gemma), with **zero** cases lost in every run; all 266 gold outputs (v2 and v3) are left unchanged. The run5 gains are `conflicting-12` ×3 and `adversarial-15` ×1 (`budget_vnd` taken from the stated cap). Removing a disproved conflict does not make `multiple-12`, `noisy-18`, or `adversarial-08` exact because `needs_clarification` stays true. Replay with `--processing repair-v2` accepts runs that already carry inline v1 processing (recomputed in the replay's copy only); its `report.md` lists every repaired output.

### Repair-v3 Processing And Name Table

`--processing repair-v3` is repair-v2 preceded by name canonicalization from [`name_aliases.csv`](name_aliases.csv), a plain `kind,canonical,alias` table (`kind` is `destination` or `interest`). Destination scalars and atoms, interest list entries, and interest atoms that match an alias become the canonical name; unknown names are left unchanged. Matching ignores case, Unicode composition, spaces, dots, hyphens, and underscores, but not accents, so unaccented spellings are listed explicitly (e.g. `Cần Thơ` ← `Can Tho`, `CanTho`, `TP. Cần Thơ`; `cafe` ← `cà phê`, `ca phe`). Canonical names follow the contract (ASCII `Hanoi`, `Ho Chi Minh City`, `Da Nang`, `Da Lat`, `Hue`) and the gold (`Cần Thơ`, `Nha Trang`, `Hội An`); interest aliases are limited to the contract's own pairs plus spelling and plural variants. A name that maps to two canonical names fails at import. Add rows to extend coverage; repair-v2 stays byte-stable so earlier repair-v2 runs (run6) still verify.

Offline on saved runs (no new requests), exact cases after repair-v2 → repair-v3: run3 251 → 262, run4 236 → 245, run5 286 → 296, run6 354 → 366 (83.6%), run2 +9, run1 +19. **Zero** exact cases lost in any run (Gemini, cloud Gemma, LM Studio Gemma), and all 266 gold outputs stay unchanged. Typical gains: `noisy-10`/`conflicting-18` (`Can Tho` → `Cần Thơ`), `noisy-06` (`cà phê` + `cafe` → one `cafe`), `basic-20`, `multiple-15`. Repairs never lose an exact case, but on an output that is already wrong they can make one more field consistently wrong (e.g. an ambiguous `budget_vnd eq "500"` atom copied into the scalar).

### Diagnostic Checks

`consistency-v2` (all new runs and replays) adds two rules to `consistency-v1`: hard `interests include` is valid (semantic-v3), and `missing_scalar` flags a stated scalar constraint whose field is null. It checks duplicates, scalar/list-to-atom correspondence, unambiguous field/operator/hard-soft rules, and mechanically observable date/duration, budget-bound, and exact must-visit/avoid contradictions. Properly preserved contradictory requests are not errors merely because they conflict. The checks never modify atoms, values, conflicts, or clarification flags.

Diagnostics use `error` for an internal representation mismatch and `review` when evidence is insufficient. They cannot prove grounding in the user's text, detect every omitted clause, or infer itinerary feasibility. Reversed clock endpoints always require review: no day offset is represented, and no naive overnight-keyword or negation heuristic is used. Multiple destination names alone do not establish single-destination disagreement. A true clarification flag is not cleared just because conflict codes are absent.

### Result Separation

`raw_response`, `parsed_output`, `metrics`, schema flags, HTTP latency and reported tokens retain their original meanings. Added row fields are `processed_output`, `processed_metrics`, `missing_fields_changed`, `consistency_issues`, `validation_seconds`, `postprocessing_seconds`, and `extraction_pipeline_latency_seconds`. Invalid/failed outputs are not processed: their processed output and diagnostics are null and their processed quality scores remain failed. Warm-ups are excluded from both summaries.

Live pipeline latency sums HTTP, JSON/schema validation, and processing/diagnostic durations, excluding both benchmark gold-scoring operations. Diagnostics run on the processed object. Their rates use checked schema-valid outputs, not failed attempts; review rates are separate from error rates. Accuracy still uses all attempts, and hallucination still uses schema-valid outputs. Existing figures continue to read **raw** summaries; no graph is silently relabeled as processed.

### Offline Replay

Reprocess saved first-pass results into a new directory, with no credentials or API calls required:

```bash
uv run python src/journie-llm/replay_processing.py \
  --run data/run1 --output data/run1-processing
```

Replay requires matching case/schema hashes, consistent saved raw/parsed results and scores, and a complete provider/case/pass grid. It refuses existing outputs and source-run descendants. It writes enhanced rows, separate raw/processed summaries, a safe provenance manifest, and `report.md` with gained/lost exact cases. Raw latency/tokens remain historical. Postprocessing time is measured now; historical end-to-end pipeline latency remains unknown, not manufactured by adding today's processing to an old HTTP measurement. No new tokens are used.

This is a **retrospective pipeline experiment**, not improved first-pass model accuracy or evidence that the reinforced prompt works. The inspected v2 corpus is development data. Compare baseline/raw, baseline/processed, reinforced/raw, and reinforced/processed on fresh independently reviewed held-out cases before claiming general improvement. Split by case/paraphrase family, not repeated pass.

There is **no winner threshold**. Compare quality, latency, tokens, failure rates, and category weaknesses. RAM/VRAM, controlled cold-start measurements, concurrent load testing, actual billing, and solver integration are outside this version.

### Prompt Length

`prompts.prompt_sections()` names the contiguous parts that `build_prompt()` joins (contract, embedded schema, variant guidance/examples/checklist). Print characters, whitespace words, and share per section for every variant, offline:

```bash
PYTHONPATH=src/journie-llm uv run python src/journie-llm/prompt_stats.py
```

Token counts are not estimated from characters and no tokenizer dependency is added. Instead, fit a saved run's provider-reported usage by least squares, `input_tokens = prompt_tokens + per_char × len(user_input)`, per provider/model over measured (non-warmup) rows:

```bash
PYTHONPATH=src/journie-llm uv run python src/journie-llm/prompt_stats.py \
  --run data/run2 --run data/run5-semantic-v2
```

Output is an aligned Markdown table; add `--csv` for CSV. The intercept `prompt_tokens` is the fixed per-request cost in that provider's tokenizer (system prompt plus message framing); `max_abs_residual` shows how well characters predict the variable part. The fit requires matching prompt and case hashes. For `semantic*`, the native response schema is sent separately and does not appear in Gemini's reported `input_tokens`.

## Report Figures

Generate the six selected report figures from a completed run without calling models or adding a project dependency:

```bash
uv run --with 'matplotlib>=3.10,<4' python src/journie-llm/plot_report.py \
  --run data/run1 --processed-run data/run1-processing \
  --output data/run1/figures-processed
uv run --with 'matplotlib>=3.10,<4' python src/journie-llm/plot_report.py \
  --run data/run2 --output data/run2/figures-processed
```

The default output is a new `data/run1/figures/` directory containing 300-DPI PNG and vector SVG pairs, `captions.md`, and auditable `chart_data.json`. Existing output directories are not overwritten; use `--output` for another destination. Runs with inline `processed_metrics` are detected automatically. Older runs can be paired with `--processed-run`: its manifest/results/summary provenance hashes, case/schema/prompt hashes, and original per-attempt fields must match the raw run. Both processing versions are verified by re-running the recorded version on every valid output; the field-metric invariance check applies only to `missing-fields-v1`. With `repair-v2`, figures 7 and 8 add post-processed bars beside faded raw bars. A replay may also pair with a run that already has inline v1 processing; the replay then supersedes it. No post-processed score may credit an invalid output. Missing processing data is reported as unavailable, not zero.

Outcomes and category accuracy show separate **Raw** and **Post-processed** rows over the same attempts. The quality/latency chart shows both accuracy values at the same original HTTP timing, explicitly excluding processing overhead; this does not claim equal end-to-end pipeline latency. Figure 8 retains the unchanged populated/empty field scores and adds whole missing-field-list accuracy for both stages. That accuracy uses all attempts, including invalid outputs. Hallucination and validity are unchanged by missing-field processing, so redundant bars are not added. Raw and post-processed samples must not be summed as new requests.

Matplotlib's version is recorded in the chart data. The script recomputes selected metrics against hash-matching gold and checks both raw and processed summaries. Gold defaults to the dataset file whose hash the run recorded; use `--cases` to override. Figure 2 adds v3 trait columns (overlapping the categories) and an attempt count per cell. Mismatched hashes or per-row scores fail before figures are generated. Exact missing-field-list accuracy is derived from item error counts and schema flags. Manifest request-count discrepancies are reported in CLI output, captions, and figure footers; figures use the complete recorded case/pass grid without changing the manifest. No raw responses, credentials, endpoint addresses, or configuration notes are exported. Outcome categories are exclusive; latency includes invalid/failed attempts; hallucination is conditioned on schema validity. Warm-ups are excluded everywhere.

## Offline Verification

```bash
uv run --extra dev pytest src/journie-llm
uv run --extra dev ruff check src/journie-llm
uv run --extra dev mypy src/journie-llm
```

Tests use mocked HTTP responses, including valid output, refusal, malformed JSON, invalid schema, timeouts, network errors, and quota failures. No API keys, network calls, or LM Studio installation are required for these tests.
