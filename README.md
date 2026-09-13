# Buy or Wait? — AI-Powered Financial Decision Agent

Submission for **HackerRank Orchestrate (September 2026)**.

## Architecture Overview

1. **Deterministic Core Engine (`code/core/`):**
   - **`reconciler.py`**: State reconstruction from historical transactions, de-duplicating lifecycle records, reserving pending debits, and ignoring unconfirmed credits and unrealized gains.
   - **`currency.py`**: Exact dated exchange rate conversions from `dataset/exchange_rates.csv`.
   - **`forecaster.py`**: Conservative 90-day cash flow simulation checking intraday balance troughs against `minimum_balance_to_keep`.
   - **`plan_generator.py`**: Suffix-margin optimized candidate evaluator for `full_payment`, `partial_payment`, provider `installments`, and permitted spending reductions.
   - **`tie_breaker.py`**: Exact 6-rule hierarchical tie-breaker prioritizing deadlines, 0 spending changes, minimal cost, early start, and fewer payments.
   - **`validator.py`**: Strict consistency verifier for numeric safety bounds, partial payment sums, option IDs, and status/method pairings.
   - **`explainer.py`**: Grounded explanation synthesizer bounded strictly to referenced evidence IDs.

2. **Schema-Constrained LLM Extraction (`code/llm/` & `code/schemas/`):**
   - **`message_extractor.py`**: Extracts structured payroll changes, contract dates, and unapproved bonuses with Pydantic schema validation.
   - **`image_extractor.py`**: Extracts missing transaction figures from payslips, bills, and receipts in `dataset/media/images/`.

## Running the Pipeline

To run the complete evaluation pipeline on `dataset/requests.csv` and generate `dataset/output.csv`:

```bash
python code/main.py
```

To run sample evaluation benchmarks:

```bash
python evaluation/eval_samples.py
```
