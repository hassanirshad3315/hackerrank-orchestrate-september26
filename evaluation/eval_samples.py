"""
Evaluation script for benchmarking pipeline accuracy on dataset/sample_requests.csv.
Computes per-column accuracy and prints a detailed mismatch table.
"""

import os
import sys
sys.path.insert(0, os.path.abspath("."))

import pandas as pd
from datetime import datetime
from typing import Any, Dict, List, Tuple

from code.core.currency import CurrencyConverter
from code.core.reconciler import FinancialReconciler
from code.core.forecaster import CashFlowForecaster
from code.core.plan_generator import PlanGenerator, format_amount
from code.core.tie_breaker import PlanTieBreaker
from code.core.explainer import DecisionExplainer
from code.llm.message_extractor import MessageFactExtractor
from code.llm.image_extractor import ImageFactExtractor


def normalize_val(val: Any) -> str:
    if val is None or pd.isna(val):
        return ""
    s = str(val).strip()
    return s


def compare_amounts(expected_str: str, actual_str: str, rel_tol: float = 0.05) -> bool:
    try:
        e = float(expected_str)
        a = float(actual_str)
        if abs(e - a) < 1.0:
            return True
        if e > 0:
            return abs(e - a) / e <= rel_tol
        return False
    except ValueError:
        return expected_str == actual_str


def evaluate_sample_dataset(sample_path: str = "dataset/sample_requests.csv"):
    converter = CurrencyConverter()
    msg_extractor = MessageFactExtractor()
    img_extractor = ImageFactExtractor()
    reconciler = FinancialReconciler(
        currency_converter=converter,
        message_extractor=msg_extractor,
        image_extractor=img_extractor,
    )
    forecaster = CashFlowForecaster(reconciler)
    plan_gen = PlanGenerator(reconciler, forecaster)

    # Load payment options
    rpo_df = pd.read_csv("dataset/request_payment_options.csv")
    options_by_req: Dict[str, List[dict]] = {}
    for _, row in rpo_df.iterrows():
        r_id = row["request_id"].strip()
        if r_id not in options_by_req:
            options_by_req[r_id] = []
        options_by_req[r_id].append(row.to_dict())

    sample_df = pd.read_csv(sample_path)
    total_rows = len(sample_df)

    eval_columns = [
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
    ]

    correct_counts = {col: 0 for col in eval_columns}
    mismatches: List[Dict[str, str]] = []
    results: List[Dict[str, Any]] = []

    for _, s_row in sample_df.iterrows():
        req_id = s_row["request_id"].strip()
        uid = s_row["user_id"].strip()
        rdate = datetime.strptime(s_row["request_date"].strip(), "%Y-%m-%d").date()
        cdate = datetime.strptime(s_row["desired_completion_date"].strip(), "%Y-%m-%d").date()
        req_amt = float(s_row["requested_amount"])
        allows_part = str(s_row["allows_partial_payment"]).lower() == "true"
        opts = options_by_req.get(req_id, [])

        msg_facts = msg_extractor.extract_user_facts(uid)
        ev_msg_ids = [m.message_id for m in msg_facts]
        ev_img_ids = [
            img_extractor.get_event_fact(e.event_id).image_id
            for e in reconciler.events_by_user.get(uid, [])
            if img_extractor.get_event_fact(e.event_id)
        ]

        amt_safe, earliest_full, candidates = plan_gen.generate_candidates(
            request_id=req_id,
            user_id=uid,
            request_date=rdate,
            requested_amount=req_amt,
            desired_completion_date=cdate,
            allows_partial_payment=allows_part,
            payment_options=opts,
            evidence_message_ids=ev_msg_ids,
            evidence_image_ids=ev_img_ids,
        )

        ranked = PlanTieBreaker.rank_candidates(candidates)
        best = ranked[0] if ranked else None

        prof = reconciler.profiles[uid]
        ev_desc_map = {e.event_id: e.description for e in reconciler.events_by_user.get(uid, [])}
        explanation = DecisionExplainer.generate_explanation(
            candidate=best,
            profile=prof,
            requested_amount=req_amt,
            desired_completion_date=cdate,
            evidence_descriptions=ev_desc_map,
        )

        actual_row = {
            "request_id": req_id,
            "amount_safe_to_pay": format_amount(best.amount_safe_to_pay),
            "affordability_status": best.status,
            "recommended_payment_method": best.method,
            "payment_plan": best.payment_plan_str,
            "earliest_date_for_full_payment": normalize_val(best.earliest_date_for_full_payment),
            "spending_changes_needed": best.spending_changes_str,
            "decision_explanation": explanation,
        }
        results.append(actual_row)

        for col in eval_columns:
            exp_val = normalize_val(s_row[col])
            act_val = normalize_val(actual_row[col])

            match = False
            if col == "amount_safe_to_pay":
                match = compare_amounts(exp_val, act_val)
            else:
                match = (exp_val == act_val)

            if match:
                correct_counts[col] += 1
            else:
                mismatches.append({
                    "request_id": req_id,
                    "column": col,
                    "expected": exp_val,
                    "actual": act_val,
                })

    print("=" * 90)
    print(f"EVALUATION RESULTS ON {sample_path} ({total_rows} Total Rows)")
    print("=" * 90)
    print(f"{'Column Name':<35} | {'Accuracy':<10} | {'Correct / Total':<15}")
    print("-" * 90)
    for col in eval_columns:
        acc = (correct_counts[col] / total_rows) * 100.0
        print(f"{col:<35} | {acc:>7.1f}%  | {correct_counts[col]}/{total_rows}")
    print("-" * 90)

    if mismatches:
        print(f"\nMISMATCHED ROWS ({len(mismatches)} discrepancies found):")
        print(f"{'Request ID':<12} | {'Column':<32} | {'Expected':<22} | {'Actual'}")
        print("-" * 90)
        for m in mismatches:
            print(f"{m['request_id']:<12} | {m['column']:<32} | {m['expected']:<22} | {m['actual']}")
    else:
        print("\nAll sample requests matched ground truth perfectly across all columns!")


if __name__ == "__main__":
    evaluate_sample_dataset()
