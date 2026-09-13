"""
Deterministic plan generator for evaluating payment options and spending change candidates.
Generates full payment, partial payment, installment, wait, and fallback candidates.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from code.constants import (
    ALL_PAYMENT_METHODS,
    FLEXIBILITY_FIXED,
    FLEXIBILITY_REDUCIBLE,
    FLEXIBILITY_REDUCIBLE_OR_STOPPABLE,
    FLEXIBILITY_STOPPABLE,
    FORECAST_HORIZON_DAYS,
    MAX_SPENDING_CHANGES,
    METHOD_FULL_PAYMENT,
    METHOD_INSTALLMENTS,
    METHOD_NOT_RECOMMENDED,
    METHOD_PARTIAL_PAYMENT,
    METHOD_WAIT,
    SPENDING_ACTION_REDUCE_TO,
    SPENDING_ACTION_STOP,
    STATUS_AFFORDABLE_LATER,
    STATUS_AFFORDABLE_NOW,
    STATUS_AFFORDABLE_WITH_PLAN,
    STATUS_NOT_AFFORDABLE,
)
from code.core.forecaster import CashFlowForecaster
from code.core.models import FinancialEvent, ForecastResult, RecurringPattern, UserProfile
from code.core.reconciler import FinancialReconciler


@dataclass
class PlanCandidate:
    request_id: str
    user_id: str
    method: str  # full_payment, partial_payment, installments, wait, not_recommended
    status: str  # affordable_now, affordable_with_plan, affordable_later, not_affordable
    payment_plan_str: str  # YYYY-MM-DD:amt|... or none
    earliest_date_for_full_payment: Optional[str]  # YYYY-MM-DD or empty
    spending_changes_str: str  # stop:ev|... or none
    spending_changes_list: List[str] = field(default_factory=list)
    total_amount_paid: float = 0.0
    first_payment_date: Optional[date] = None
    last_payment_date: Optional[date] = None
    number_of_payments: int = 0
    payment_option_id: str = "none"
    completes_by_deadline: bool = True
    is_safe: bool = False
    amount_safe_to_pay: float = 0.0
    evidence_event_ids: List[str] = field(default_factory=list)
    evidence_message_ids: List[str] = field(default_factory=list)
    evidence_image_ids: List[str] = field(default_factory=list)


def format_amount(val: float) -> str:
    if abs(val - round(val)) < 1e-6:
        return str(int(round(val)))
    return f"{val:.2f}"


class PlanGenerator:
    def __init__(self, reconciler: FinancialReconciler, forecaster: CashFlowForecaster):
        self.reconciler = reconciler
        self.forecaster = forecaster
        self.converter = reconciler.converter

    def generate_candidates(
        self,
        request_id: str,
        user_id: str,
        request_date: date,
        requested_amount: float,
        desired_completion_date: date,
        allows_partial_payment: bool,
        payment_options: List[dict],
        evidence_message_ids: Optional[List[str]] = None,
        evidence_image_ids: Optional[List[str]] = None,
    ) -> Tuple[float, Optional[date], List[PlanCandidate]]:
        """
        Evaluates all candidate payment plans for a request.
        Returns:
          - amount_safe_to_pay on request_date
          - earliest_date_for_full_payment
          - list of eligible, evaluated PlanCandidates
        """
        profile = self.reconciler.profiles[user_id]
        considered_methods = profile.payment_methods_user_will_consider
        max_inst_months = profile.max_installment_months

        # 1. Baseline forecast with NO spending changes and NO payments
        base_forecast = self.forecaster.forecast_user_balance(user_id, request_date)
        
        # 2. Determine amount_safe_to_pay on request_date
        amount_safe_to_pay = self._compute_amount_safe_to_pay(
            user_id, request_date, requested_amount, profile.minimum_balance_to_keep
        )

        # 3. Determine earliest_date_for_full_payment
        earliest_full_date = self._find_earliest_date_for_full_payment(
            user_id, request_date, requested_amount, profile.minimum_balance_to_keep
        )

        earliest_full_str = earliest_full_date.strftime("%Y-%m-%d") if earliest_full_date else ""

        candidates: List[PlanCandidate] = []
        ev_msg_ids = evidence_message_ids or []
        ev_img_ids = evidence_image_ids or []

        # 4. Evaluate Full Payment on request_date
        if METHOD_FULL_PAYMENT in considered_methods:
            full_safe = self._test_plan_safety(
                user_id, request_date, [(request_date, requested_amount)], []
            )
            if full_safe:
                candidates.append(
                    PlanCandidate(
                        request_id=request_id,
                        user_id=user_id,
                        method=METHOD_FULL_PAYMENT,
                        status=STATUS_AFFORDABLE_NOW,
                        payment_plan_str=f"{request_date.strftime('%Y-%m-%d')}:{format_amount(requested_amount)}",
                        earliest_date_for_full_payment=request_date.strftime("%Y-%m-%d"),
                        spending_changes_str="none",
                        spending_changes_list=[],
                        total_amount_paid=requested_amount,
                        first_payment_date=request_date,
                        last_payment_date=request_date,
                        number_of_payments=1,
                        payment_option_id="payment_option_01",
                        completes_by_deadline=(request_date <= desired_completion_date),
                        is_safe=True,
                        amount_safe_to_pay=amount_safe_to_pay,
                        evidence_message_ids=ev_msg_ids,
                        evidence_image_ids=ev_img_ids,
                    )
                )

        # 5. Evaluate Installments from request_payment_options.csv
        if METHOD_INSTALLMENTS in considered_methods:
            for opt in payment_options:
                if opt.get("payment_method") != "installments":
                    continue
                
                num_payments = int(opt["number_of_payments"])
                if max_inst_months is not None and num_payments > max_inst_months:
                    continue

                p_amt = float(opt["payment_amount"])
                first_d = datetime.strptime(opt["first_payment_date"], "%Y-%m-%d").date()
                freq_days = int(float(opt.get("payment_frequency_days", 30)))
                tot_amt = float(opt.get("total_payable_amount", p_amt * num_payments))
                opt_id = opt.get("payment_option_id", "none")

                # Generate schedule
                schedule: List[Tuple[date, float]] = []
                cur_d = first_d
                for i in range(num_payments):
                    schedule.append((cur_d, p_amt))
                    cur_d += timedelta(days=freq_days)

                last_d = schedule[-1][0]
                completes = (last_d <= desired_completion_date)

                is_safe = self._test_plan_safety(user_id, request_date, schedule, [])
                if is_safe:
                    plan_str = "|".join(f"{d.strftime('%Y-%m-%d')}:{format_amount(amt)}" for d, amt in schedule)
                    candidates.append(
                        PlanCandidate(
                            request_id=request_id,
                            user_id=user_id,
                            method=METHOD_INSTALLMENTS,
                            status=STATUS_AFFORDABLE_WITH_PLAN,
                            payment_plan_str=plan_str,
                            earliest_date_for_full_payment=earliest_full_str,
                            spending_changes_str="none",
                            spending_changes_list=[],
                            total_amount_paid=tot_amt,
                            first_payment_date=first_d,
                            last_payment_date=last_d,
                            number_of_payments=num_payments,
                            payment_option_id=opt_id,
                            completes_by_deadline=completes,
                            is_safe=True,
                            amount_safe_to_pay=amount_safe_to_pay,
                            evidence_message_ids=ev_msg_ids,
                            evidence_image_ids=ev_img_ids,
                        )
                    )

        # 6. Evaluate Partial Payment
        if (
            allows_partial_payment
            and METHOD_PARTIAL_PAYMENT in considered_methods
            and 0 < amount_safe_to_pay < requested_amount
            and earliest_full_date is not None
            and earliest_full_date <= desired_completion_date
        ):
            remaining_amt = round(requested_amount - amount_safe_to_pay, 2)
            part_schedule = [(request_date, amount_safe_to_pay), (earliest_full_date, remaining_amt)]
            if self._test_plan_safety(user_id, request_date, part_schedule, []):
                plan_str = f"{request_date.strftime('%Y-%m-%d')}:{format_amount(amount_safe_to_pay)}|{earliest_full_date.strftime('%Y-%m-%d')}:{format_amount(remaining_amt)}"
                candidates.append(
                    PlanCandidate(
                        request_id=request_id,
                        user_id=user_id,
                        method=METHOD_PARTIAL_PAYMENT,
                        status=STATUS_AFFORDABLE_WITH_PLAN,
                        payment_plan_str=plan_str,
                        earliest_date_for_full_payment=earliest_full_str,
                        spending_changes_str="none",
                        spending_changes_list=[],
                        total_amount_paid=requested_amount,
                        first_payment_date=request_date,
                        last_payment_date=earliest_full_date,
                        number_of_payments=2,
                        payment_option_id="none",
                        completes_by_deadline=(earliest_full_date <= desired_completion_date),
                        is_safe=True,
                        amount_safe_to_pay=amount_safe_to_pay,
                        evidence_message_ids=ev_msg_ids,
                        evidence_image_ids=ev_img_ids,
                    )
                )

        # 7. Evaluate Spending Changes if immediate methods aren't directly safe
        spending_candidates = self._evaluate_spending_reduction_plans(
            request_id,
            user_id,
            request_date,
            requested_amount,
            desired_completion_date,
            amount_safe_to_pay,
            earliest_full_str,
            considered_methods,
            ev_msg_ids,
            ev_img_ids,
        )
        candidates.extend(spending_candidates)

        # 8. Evaluate Wait (if full payment safe later)
        if METHOD_FULL_PAYMENT in considered_methods and earliest_full_date is not None and earliest_full_date > request_date:
            candidates.append(
                PlanCandidate(
                    request_id=request_id,
                    user_id=user_id,
                    method=METHOD_WAIT,
                    status=STATUS_AFFORDABLE_LATER,
                    payment_plan_str=f"{earliest_full_str}:{format_amount(requested_amount)}",
                    earliest_date_for_full_payment=earliest_full_str,
                    spending_changes_str="none",
                    spending_changes_list=[],
                    total_amount_paid=requested_amount,
                    first_payment_date=earliest_full_date,
                    last_payment_date=earliest_full_date,
                    number_of_payments=1,
                    payment_option_id="none",
                    completes_by_deadline=(earliest_full_date <= desired_completion_date),
                    is_safe=True,
                    amount_safe_to_pay=amount_safe_to_pay,
                    evidence_message_ids=ev_msg_ids,
                    evidence_image_ids=ev_img_ids,
                )
            )

        # 9. Fallback: Not Recommended
        candidates.append(
            PlanCandidate(
                request_id=request_id,
                user_id=user_id,
                method=METHOD_NOT_RECOMMENDED,
                status=STATUS_NOT_AFFORDABLE,
                payment_plan_str="none",
                earliest_date_for_full_payment=earliest_full_str,
                spending_changes_str="none",
                spending_changes_list=[],
                total_amount_paid=0.0,
                first_payment_date=None,
                last_payment_date=None,
                number_of_payments=0,
                payment_option_id="none",
                completes_by_deadline=False,
                is_safe=True,
                amount_safe_to_pay=amount_safe_to_pay,
                evidence_message_ids=ev_msg_ids,
                evidence_image_ids=ev_img_ids,
            )
        )

        return amount_safe_to_pay, earliest_full_date, candidates

    def _test_plan_safety(
        self,
        user_id: str,
        start_date: date,
        payments: List[Tuple[date, float]],
        spending_changes: List[str],
    ) -> bool:
        res = self.forecaster.forecast_user_balance(
            user_id=user_id,
            start_date=start_date,
            spending_changes=spending_changes,
            additional_payments=payments,
        )
        return res.min_projected_balance >= res.minimum_balance_to_keep

    def _compute_amount_safe_to_pay(
        self, user_id: str, request_date: date, requested_amount: float, min_balance_to_keep: float
    ) -> float:
        # Binary search for maximum safe payment today
        low = 0.0
        high = requested_amount
        best_safe = 0.0

        # Step check
        for test_amt in [requested_amount, 0.0]:
            if test_amt > 0:
                if self._test_plan_safety(user_id, request_date, [(request_date, test_amt)], []):
                    return test_amt

        # Binary search with 0.01 precision
        for _ in range(25):
            mid = (low + high) / 2.0
            if self._test_plan_safety(user_id, request_date, [(request_date, mid)], []):
                best_safe = mid
                low = mid
            else:
                high = mid

        return round(best_safe, 2)

    def _find_earliest_date_for_full_payment(
        self, user_id: str, request_date: date, requested_amount: float, min_balance_to_keep: float
    ) -> Optional[date]:
        for day_offset in range(FORECAST_HORIZON_DAYS + 1):
            check_d = request_date + timedelta(days=day_offset)
            if self._test_plan_safety(user_id, request_date, [(check_d, requested_amount)], []):
                return check_d
        return None

    def _evaluate_spending_reduction_plans(
        self,
        request_id: str,
        user_id: str,
        request_date: date,
        requested_amount: float,
        desired_completion_date: date,
        amount_safe_to_pay: float,
        earliest_full_str: str,
        considered_methods: Set[str],
        ev_msg_ids: List[str],
        ev_img_ids: List[str],
    ) -> List[PlanCandidate]:
        profile = self.reconciler.profiles[user_id]
        future_events, patterns = self.reconciler.reconcile_user_events(user_id, request_date)
        candidates: List[PlanCandidate] = []

        # Find adjustable flexible events
        stoppable_events: List[RecurringPattern] = []
        reducible_events: List[RecurringPattern] = []

        for p in patterns:
            cat = p.category
            if cat in profile.expense_categories_to_protect:
                continue
            if p.flexibility in (FLEXIBILITY_STOPPABLE, FLEXIBILITY_REDUCIBLE_OR_STOPPABLE) and cat in profile.expense_categories_user_is_willing_to_stop:
                stoppable_events.append(p)
            if p.flexibility in (FLEXIBILITY_REDUCIBLE, FLEXIBILITY_REDUCIBLE_OR_STOPPABLE) and cat in profile.expense_categories_user_is_willing_to_reduce:
                reducible_events.append(p)

        # Test single stop/reduce
        if METHOD_FULL_PAYMENT in considered_methods:
            for p in stoppable_events:
                sc = [f"stop:{p.representative_event_id}"]
                if self._test_plan_safety(user_id, request_date, [(request_date, requested_amount)], sc):
                    candidates.append(
                        PlanCandidate(
                            request_id=request_id,
                            user_id=user_id,
                            method=METHOD_FULL_PAYMENT,
                            status=STATUS_AFFORDABLE_WITH_PLAN,
                            payment_plan_str=f"{request_date.strftime('%Y-%m-%d')}:{format_amount(requested_amount)}",
                            earliest_date_for_full_payment=earliest_full_str or request_date.strftime("%Y-%m-%d"),
                            spending_changes_str=sc[0],
                            spending_changes_list=sc,
                            total_amount_paid=requested_amount,
                            first_payment_date=request_date,
                            last_payment_date=request_date,
                            number_of_payments=1,
                            payment_option_id="none",
                            completes_by_deadline=(request_date <= desired_completion_date),
                            is_safe=True,
                            amount_safe_to_pay=amount_safe_to_pay,
                            evidence_event_ids=[p.representative_event_id],
                            evidence_message_ids=ev_msg_ids,
                            evidence_image_ids=ev_img_ids,
                        )
                    )

            for p in reducible_events:
                min_floor = p.minimum_allowed_amount if p.minimum_allowed_amount is not None else 0.0
                sc = [f"reduce_to:{p.representative_event_id}:{format_amount(min_floor)}"]
                if self._test_plan_safety(user_id, request_date, [(request_date, requested_amount)], sc):
                    candidates.append(
                        PlanCandidate(
                            request_id=request_id,
                            user_id=user_id,
                            method=METHOD_FULL_PAYMENT,
                            status=STATUS_AFFORDABLE_WITH_PLAN,
                            payment_plan_str=f"{request_date.strftime('%Y-%m-%d')}:{format_amount(requested_amount)}",
                            earliest_date_for_full_payment=earliest_full_str or request_date.strftime("%Y-%m-%d"),
                            spending_changes_str=sc[0],
                            spending_changes_list=sc,
                            total_amount_paid=requested_amount,
                            first_payment_date=request_date,
                            last_payment_date=request_date,
                            number_of_payments=1,
                            payment_option_id="none",
                            completes_by_deadline=(request_date <= desired_completion_date),
                            is_safe=True,
                            amount_safe_to_pay=amount_safe_to_pay,
                            evidence_event_ids=[p.representative_event_id],
                            evidence_message_ids=ev_msg_ids,
                            evidence_image_ids=ev_img_ids,
                        )
                    )

        return candidates
