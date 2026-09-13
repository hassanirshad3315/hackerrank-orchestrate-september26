"""
Deterministic 6-tier tie-breaker for ranking safe plan candidates.
"""

from datetime import date
from typing import List, Optional
from code.constants import METHOD_NOT_RECOMMENDED
from code.core.plan_generator import PlanCandidate


class PlanTieBreaker:
    @staticmethod
    def rank_candidates(candidates: List[PlanCandidate]) -> List[PlanCandidate]:
        """
        Ranks safe candidates using the 6 challenge tie-break rules:
        1. Complete full request by desired_completion_date (True before False)
        2. Require no spending changes (0 changes before 1, 2, 3 changes)
        3. Minimize total amount paid (including fees)
        4. Start payment earlier (earliest first_payment_date)
        5. Use fewer payments (lower number_of_payments)
        6. Lowest payment_option_id (lexicographical / numeric final tie-breaker)
        """
        # Filter only safe candidates, excluding fallback unless nothing else is safe
        safe_candidates = [c for c in candidates if c.is_safe]
        
        # If there are actionable safe plans (not 'not_recommended'), prioritize them over 'not_recommended'
        actionable_plans = [c for c in safe_candidates if c.method != METHOD_NOT_RECOMMENDED]

        pool = actionable_plans if actionable_plans else safe_candidates

        def sort_key(c: PlanCandidate):
            # Rule 1: Completes by deadline (True first -> 0, False -> 1)
            r1 = 0 if c.completes_by_deadline else 1
            # Rule 2: Spending changes count (0 first)
            r2 = len(c.spending_changes_list)
            # Rule 3: Total amount paid (min first)
            r3 = c.total_amount_paid
            # Rule 4: First payment date (earliest first)
            r4 = c.first_payment_date if c.first_payment_date is not None else date.max
            # Rule 5: Number of payments (min first)
            r5 = c.number_of_payments
            # Rule 6: Lowest payment_option_id
            r6 = c.payment_option_id if c.payment_option_id != "none" else "zzzz"
            return (r1, r2, r3, r4, r5, r6)

        return sorted(pool, key=sort_key)
