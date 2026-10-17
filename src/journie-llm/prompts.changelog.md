# Prompt Changelog

## Version Policy

`baseline-v1` and `reinforced-v1` are retained byte-identical. Each [prompt snapshot](archive/prompts/) is the complete rendered system prompt, including the authoritative base and JSON Schema: [baseline-v1](archive/prompts/baseline-v1.txt), [reinforced-v1](archive/prompts/reinforced-v1.txt), and [structured-v1](archive/prompts/structured-v1.txt). Snapshots are immutable; later prompt edits require a new version and snapshot. Never overwrite or delete archived versions.

## structured-v1

CLI variant: `--prompt-variant structured`. Preserves the authoritative base and schema, then appends organized instructions, three full independently authored input/output examples, and a short silent verification list. Examples are not copied from benchmark cases.

- **Instruction Structuring:** organizes the extraction workflow without replacing the authoritative contract.
- **Few-shot Prompting:** supplies three complete input/output examples rather than partial atom illustrations.
- **Contrastive Examples:** embeds distinctions between easily confused extraction choices within valid examples and their instructions, not explicit bad JSON examples.
- **Output Format Constraints:** reinforces complete schema-conforming JSON only, with no commentary or reasoning.
- **Self-Verification:** adds a short silent final check, not a chain-of-thought request or another model call.

Rationale: make clause coverage, normalization, hard/soft distinctions, and output completeness more explicit. The extraction contract, schema, gold labels, fixed reference policy, and generation settings are unchanged; `baseline` remains the default. The shared variant applies to both active configurations, `gemini` and `lmstudio-gemma`. Only the cloud entry named `gemma` was removed from active configuration and preserved unchanged in [its single-item provider archive](archive/gemma-cloud.providers.json); local `lmstudio-gemma` is unchanged.

This is an empirically unverified prompt experiment, not a measured improvement. No live API calls were executed and no benchmark results are claimed. Additional input tokens or latency are possible.

## Retained Versions

- **baseline-v1** (`baseline`, default): original authoritative prompt plus JSON Schema, unchanged byte-for-byte.
- **reinforced-v1** (`reinforced`): authoritative prompt plus schema and the existing appended clause-to-atom checklist with independently authored partial atom illustrations, unchanged byte-for-byte.
