import os
import sys
sys.path.insert(0, os.path.abspath("."))

import pandas as pd
from datetime import datetime

from code.core.currency import CurrencyConverter
from code.core.reconciler import FinancialReconciler
from code.core.forecaster import CashFlowForecaster
from code.core.plan_generator import PlanGenerator
from code.core.tie_breaker import PlanTieBreaker
from code.core.explainer import DecisionExplainer
from code.llm.message_extractor import MessageFactExtractor
from code.llm.image_extractor import ImageFactExtractor

def run_trace():
    converter = CurrencyConverter()
    reconciler = FinancialReconciler(currency_converter=converter)
    forecaster = CashFlowForecaster(reconciler)
    plan_gen = PlanGenerator(reconciler, forecaster)
    msg_extractor = MessageFactExtractor()
    img_extractor = ImageFactExtractor()

    # Load payment options
    rpo_df = pd.read_csv('dataset/request_payment_options.csv')
    options_by_req = {}
    for _, row in rpo_df.iterrows():
        r_id = row['request_id']
        if r_id not in options_by_req:
            options_by_req[r_id] = []
        options_by_req[r_id].append(row.to_dict())

    sample_reqs = pd.read_csv('dataset/sample_requests.csv').head(3)

    print('=' * 88)
    print('END-TO-END PIPELINE TRACE FOR SAMPLE REQUESTS')
    print('=' * 88)

    for _, s_row in sample_reqs.iterrows():
        req_id = s_row['request_id']
        uid = s_row['user_id']
        rdate = datetime.strptime(s_row['request_date'], '%Y-%m-%d').date()
        cdate = datetime.strptime(s_row['desired_completion_date'], '%Y-%m-%d').date()
        req_amt = float(s_row['requested_amount'])
        allows_part = str(s_row['allows_partial_payment']).lower() == 'true'
        opts = options_by_req.get(req_id, [])

        # Extract LLM message and image facts
        msg_facts = msg_extractor.extract_user_facts(uid)
        ev_msg_ids = [m.message_id for m in msg_facts]
        
        # Image facts linked to user events
        ev_img_ids = []
        for e in reconciler.events_by_user.get(uid, []):
            img_fact = img_extractor.get_event_fact(e.event_id)
            if img_fact:
                ev_img_ids.append(img_fact.image_id)

        # Generate plan candidates
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

        # Tie break ranking
        ranked = PlanTieBreaker.rank_candidates(candidates)
        best = ranked[0] if ranked else None

        # Synthesize explanation
        prof = reconciler.profiles[uid]
        ev_desc_map = {e.event_id: e.description for e in reconciler.events_by_user.get(uid, [])}
        explanation = DecisionExplainer.generate_explanation(
            candidate=best,
            profile=prof,
            requested_amount=req_amt,
            desired_completion_date=cdate,
            evidence_descriptions=ev_desc_map,
        )

        print(f"\n>>> Request ID: {req_id} | User ID: {uid} | Type: {s_row['request_type']} | Amount: {req_amt:,.2f} {prof.home_currency}")
        print(f"Evidence Set:")
        print(f"  - Messages Pulled: {ev_msg_ids if ev_msg_ids else 'None'}")
        print(f"  - Images Pulled: {ev_img_ids if ev_img_ids else 'None'}")
        print(f"  - Stored Profile: Bal={prof.current_available_balance:,.2f}, MinBal={prof.minimum_balance_to_keep:,.2f}, Methods={prof.payment_methods_user_will_consider}")
        print(f"Pipeline Output Row:")
        print(f"  request_id: {req_id}")
        print(f"  amount_safe_to_pay: {best.amount_safe_to_pay:g}")
        print(f"  affordability_status: {best.status}")
        print(f"  recommended_payment_method: {best.method}")
        print(f"  payment_plan: {best.payment_plan_str}")
        print(f"  earliest_date_for_full_payment: {best.earliest_date_for_full_payment}")
        print(f"  spending_changes_needed: {best.spending_changes_str}")
        print(f"  decision_explanation: \"{explanation}\"")
        print(f"Ground Truth in sample_requests.csv:")
        print(f"  amount_safe_to_pay: {s_row['amount_safe_to_pay']}")
        print(f"  affordability_status: {s_row['affordability_status']}")
        print(f"  recommended_payment_method: {s_row['recommended_payment_method']}")
        print(f"  payment_plan: {s_row['payment_plan']}")
        print(f"  earliest_date_for_full_payment: {s_row['earliest_date_for_full_payment']}")
        print(f"  spending_changes_needed: {s_row['spending_changes_needed']}")
        print(f"  decision_explanation: \"{s_row['decision_explanation']}\"")

if __name__ == '__main__':
    run_trace()
