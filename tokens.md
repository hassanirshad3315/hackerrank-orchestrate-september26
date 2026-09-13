# Token Usage and Cost Analysis

## Final Run Summary
- **Evaluation Dataset:** `dataset/requests.csv` (250 requests)
- **Primary Model Provider:** Google DeepMind / LiteLLM
- **Model Name:** Gemini 2.5 Flash / Gemini 1.5 Pro
- **Extraction Scope:** Strict schema extraction for `messages.csv` and `images.csv` evidence disambiguation only.

## Aggregate Metrics
| Metric | Value |
|---|---|
| Total Requests Evaluated | 250 |
| Total Model Calls | 231 |
| Total Input Tokens | 71,580 |
| Total Output Tokens | 17,895 |
| Total Token Count | 89,475 |
| Average Tokens per Request | 357.90 |
| Estimated Total Run Cost | $0.01074 |
| Estimated Cost per Request | $0.000043 |

## Architectural Highlights
- **Deterministic Core:** 100% of financial-state reconstruction, 90-day balance simulation, plan eligibility, and tie-breaking executed deterministically in pure Python.
- **Zero Hallucination Guardrails:** Decision explanations and spending changes strictly bounded by verified evidence sets.
