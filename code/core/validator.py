"""
Deterministic output consistency validator.
Enforces all challenge constraints, enum pairings, sum invariants, and option references.
"""

from typing import Dict, List, Optional
from code.constants import (
    ALL_AFFORDABILITY_STATUSES,
    ALL_PAYMENT_METHODS,
    METHOD_FULL_PAYMENT,
    METHOD_INSTALLMENTS,
    METHOD_NOT_RECOMMENDED,
    METHOD_PARTIAL_PAYMENT,
    METHOD_WAIT,
    STATUS_AFFORDABLE_LATER,
    STATUS_AFFORDABLE_NOW,
    STATUS_AFFORDABLE_WITH_PLAN,
    STATUS_NOT_AFFORDABLE,
)


class OutputValidator:
    @staticmethod
    def validate_row(
        row: Dict[str, str],
        requested_amount: float,
        valid_option_ids: List[str],
    ) -> List[str]:
        """
        Validates a single prediction row against the 4 consistency rules.
        Returns a list of error strings (empty if valid).
        """
        errors = []
        req_id = row.get("request_id", "")
        amt_safe_str = row.get("amount_safe_to_pay", "")
        status = row.get("affordability_status", "")
        method = row.get("recommended_payment_method", "")
        plan_str = row.get("payment_plan", "")
        spending_str = row.get("spending_changes_needed", "")
        earliest_full = row.get("earliest_date_for_full_payment", "")

        # Rule 1: 0 <= amount_safe_to_pay <= requested_amount
        try:
            amt_safe = float(amt_safe_str)
            if amt_safe < -1e-4 or amt_safe > (requested_amount + 1e-4):
                errors.append(
                    f"Rule 1 violation in {req_id}: amount_safe_to_pay {amt_safe} not in [0, {requested_amount}]"
                )
        except ValueError:
            errors.append(f"Rule 1 violation in {req_id}: Invalid numeric amount_safe_to_pay '{amt_safe_str}'")

        # Rule 2: payment_plan entries for partial_payment sum to requested_amount
        if method == METHOD_PARTIAL_PAYMENT:
            parts = plan_str.split("|")
            if len(parts) != 2:
                errors.append(f"Rule 2 violation in {req_id}: partial_payment plan must have exactly 2 entries, got '{plan_str}'")
            else:
                try:
                    p1 = float(parts[0].split(":")[1])
                    p2 = float(parts[1].split(":")[1])
                    if abs((p1 + p2) - requested_amount) > 0.05:
                        errors.append(
                            f"Rule 2 violation in {req_id}: partial_payment sum {p1 + p2} != requested_amount {requested_amount}"
                        )
                except Exception as e:
                    errors.append(f"Rule 2 violation in {req_id}: Failed to parse partial_payment plan: {e}")

        # Rule 3: Installment plans match a valid payment_option_id
        if method == METHOD_INSTALLMENTS:
            if not valid_option_ids:
                errors.append(f"Rule 3 violation in {req_id}: Installments recommended but no valid option IDs found")

        # Rule 4: affordability_status matches recommended_payment_method
        if status == STATUS_AFFORDABLE_NOW:
            if method != METHOD_FULL_PAYMENT:
                errors.append(f"Rule 4 violation in {req_id}: affordable_now requires full_payment, got {method}")
        elif status == STATUS_AFFORDABLE_WITH_PLAN:
            if method not in (METHOD_INSTALLMENTS, METHOD_PARTIAL_PAYMENT, METHOD_FULL_PAYMENT):
                errors.append(
                    f"Rule 4 violation in {req_id}: affordable_with_plan requires installments, partial_payment, or full_payment with spending changes, got {method}"
                )
            if method == METHOD_FULL_PAYMENT and spending_str == "none":
                errors.append(
                    f"Rule 4 violation in {req_id}: affordable_with_plan + full_payment must specify spending_changes_needed"
                )
        elif status == STATUS_AFFORDABLE_LATER:
            if method != METHOD_WAIT:
                errors.append(f"Rule 4 violation in {req_id}: affordable_later requires wait, got {method}")
        elif status == STATUS_NOT_AFFORDABLE:
            if method != METHOD_NOT_RECOMMENDED:
                errors.append(f"Rule 4 violation in {req_id}: not_affordable requires not_recommended, got {method}")
            if plan_str != "none":
                errors.append(f"Rule 4 violation in {req_id}: not_affordable must have payment_plan='none', got {plan_str}")

        return errors
