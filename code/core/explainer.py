"""
Grounded decision explanation generator.
Strict guardrail: may only reference event/message/image IDs and facts that are
actually present in the row's evidence set.
"""

from datetime import date
from typing import Dict, List, Optional
from code.constants import (
    METHOD_FULL_PAYMENT,
    METHOD_INSTALLMENTS,
    METHOD_NOT_RECOMMENDED,
    METHOD_PARTIAL_PAYMENT,
    METHOD_WAIT,
    STATUS_AFFORDABLE_NOW,
    STATUS_AFFORDABLE_WITH_PLAN,
)
from code.core.models import UserProfile
from code.core.plan_generator import PlanCandidate


class DecisionExplainer:
    @staticmethod
    def generate_explanation(
        candidate: PlanCandidate,
        profile: UserProfile,
        requested_amount: float,
        desired_completion_date: date,
        evidence_descriptions: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Synthesizes a grounded explanation adhering strictly to the user's evidence set.
        """
        curr = profile.home_currency
        min_bal = profile.minimum_balance_to_keep
        min_bal_str = f"{curr} {min_bal:,.2f}".rstrip("0").rstrip(".") if min_bal % 1 != 0 else f"{curr} {int(min_bal):,}"

        # Helper format date: e.g. "8 August 2025" or "15 November 2019"
        def format_d(d: Optional[date]) -> str:
            if not d:
                return ""
            # e.g. 8 August 2025
            return f"{d.day} {d.strftime('%B')} {d.year}"

        # 1. Affordable Now / Full Payment
        if candidate.status == STATUS_AFFORDABLE_NOW and candidate.method == METHOD_FULL_PAYMENT:
            req_str = f"{curr} {requested_amount:,.2f}".rstrip("0").rstrip(".") if requested_amount % 1 != 0 else f"{curr} {int(requested_amount):,}"
            return f"Pay {req_str} today. This leaves at least {min_bal_str} available over the next 90 days."

        # 2. Affordable with Plan: Installments
        if candidate.method == METHOD_INSTALLMENTS:
            p_parts = candidate.payment_plan_str.split("|")
            num_inst = len(p_parts)
            first_inst_amt = float(p_parts[0].split(":")[1])
            inst_str = f"{curr} {first_inst_amt:,.2f}".rstrip("0").rstrip(".") if first_inst_amt % 1 != 0 else f"{curr} {int(first_inst_amt):,}"
            first_d_str = format_d(candidate.first_payment_date)
            return f"Use {num_inst} installments of {inst_str}, starting {first_d_str}. This leaves at least {min_bal_str} available."

        # 3. Affordable with Plan: Spending Reduction / Stop + Full Payment
        if candidate.status == STATUS_AFFORDABLE_WITH_PLAN and candidate.spending_changes_list:
            changes_desc = []
            for sc in candidate.spending_changes_list:
                parts = sc.split(":")
                action = parts[0]
                ev_id = parts[1]
                ev_name = (evidence_descriptions or {}).get(ev_id, f"expense {ev_id}")
                if action == "stop":
                    changes_desc.append(f"Stop {ev_name}")
                elif action == "reduce_to":
                    target_amt = float(parts[2])
                    amt_str = f"{curr} {target_amt:,.2f}".rstrip("0").rstrip(".") if target_amt % 1 != 0 else f"{curr} {int(target_amt):,}"
                    changes_desc.append(f"Reduce {ev_name} to {amt_str}")

            changes_str = ", ".join(changes_desc)
            req_str = f"{curr} {requested_amount:,.2f}".rstrip("0").rstrip(".") if requested_amount % 1 != 0 else f"{curr} {int(requested_amount):,}"
            return f"{changes_str}, then pay {req_str} today. This leaves at least {min_bal_str} available."

        # 4. Affordable with Plan: Partial Payment
        if candidate.method == METHOD_PARTIAL_PAYMENT:
            p_parts = candidate.payment_plan_str.split("|")
            p1_amt = float(p_parts[0].split(":")[1])
            p2_amt = float(p_parts[1].split(":")[1])
            p2_date_str = format_d(candidate.last_payment_date)
            p1_str = f"{curr} {p1_amt:,.2f}".rstrip("0").rstrip(".") if p1_amt % 1 != 0 else f"{curr} {int(p1_amt):,}"
            p2_str = f"{curr} {p2_amt:,.2f}".rstrip("0").rstrip(".") if p2_amt % 1 != 0 else f"{curr} {int(p2_amt):,}"
            return f"Pay {p1_str} today, then pay {p2_str} on {p2_date_str}. This keeps the {min_bal_str} minimum protected."

        # 5. Affordable Later / Wait
        if candidate.method == METHOD_WAIT:
            req_str = f"{curr} {requested_amount:,.2f}".rstrip("0").rstrip(".") if requested_amount % 1 != 0 else f"{curr} {int(requested_amount):,}"
            full_d_str = format_d(candidate.first_payment_date)
            return f"Wait until {full_d_str}, then pay {req_str} in full. Paying earlier would take the balance below the {min_bal_str} minimum."

        # 6. Not Affordable / Not Recommended
        deadline_str = format_d(desired_completion_date)
        return f"Do not make this payment by {deadline_str}. None of the available options keeps the {min_bal_str} minimum protected."
