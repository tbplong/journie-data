# Offline Processing Replay

Retrospective deterministic processing of saved outputs, not first-pass accuracy or a prompt improvement. No network requests, new tokens, or new model cost.

Original HTTP latency/tokens are retained. Historical pipeline latency is unknown; postprocessing time was measured now. Recorded validation time is retained where present.

Missing destination follows the null scalar even for destination ambiguity; diagnostics do not resolve intent, repair facts, or establish grounding. Gold is used only for scoring.

| Provider / Model | Requests | Original exact | Processed exact | Changed outputs | Gained exact | Lost exact | Diagnostics checked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini / gemini-3.5-flash-lite | 360 | 196 | 229 | 64 | 33 | 0 | 360 |
| gemma / gemma-4-26b-a4b-it | 360 | 6 | 18 | 27 | 12 | 0 | 108 |
| lmstudio-gemma / google/gemma-4-E4B-it-qat-q4_0-gguf | 360 | 67 | 106 | 113 | 39 | 0 | 360 |
