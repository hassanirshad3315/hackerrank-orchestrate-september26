"""
Deterministic plan generator for evaluating payment options and spending change candidates.
Generates full payment, partial payment, installment, wait, and fallback candidates.
Optimized with suffix-margin analysis for instant O(1) safety evaluations.
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
        min_balance = profile.minimum_balance_to_keep

        # 1. Baseline forecast with NO spending changes and NO payments
        base_res = self.forecaster.forecast_user_balance(user_id, request_date)
        sorted_dates = sorted(base_res.daily_balances.keys())
        date_to_idx = {d: i for i, d in enumerate(sorted_dates)}
        
        # Suffix min margins: margin[i] = min balance from date i to end_date - min_balance_to_keep
        end_balances = [base_res.daily_balances[d].end_balance for d in sorted_dates]
        start_balances = [base_res.daily_balances[d].start_balance for d in sorted_dates]
        debits = [base_res.daily_balances[d].debits for d in sorted_dates]
        
        # Daily min margin accounting for intraday dips
        daily_min_margins = [
            min(start_balances[i] - debits[i], end_balances[i]) - min_balance
            for i in range(len(sorted_dates))
        ]
        
        suffix_min_margins = list(daily_min_margins)
        for i in range(len(daily_min_margins) - 2, -1, -1):
            suffix_min_margins[i] = min(suffix_min_margins[i], suffix_min_margins[i + 1])

        # 2. Determine amount_safe_to_pay on request_date
        margin_today = suffix_min_margins[0] if suffix_min_margins else 0.0
        amount_safe_to_pay = max(0.0, min(requested_amount, round(margin_today, 2)))

        # 3. Determine earliest_date_for_full_payment
        earliest_full_date: Optional[date] = None
        for i, d in enumerate(sorted_dates):
            if suffix_min_margins[i] >= requested_amount - 1e-4:
                earliest_full_date = d
                break

        earliest_full_str = earliest_full_date.strftime("%Y-%m-%d") if earliest_full_date else ""

        candidates: List[PlanCandidate] = []
        ev_msg_ids = evidence_message_ids or []
        ev_img_ids = evidence_image_ids or []

        # 4. Evaluate Full Payment on request_date
        if METHOD_FULL_PAYMENT in considered_methods:
            is_safe = (margin_today >= requested_amount - 1e-4)
            if is_safe:
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

                # Test safety of installments schedule against baseline
                is_safe = self._test_schedule_safety(schedule, sorted_dates, daily_min_margins)
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
            if self._test_schedule_safety(part_schedule, sorted_dates, daily_min_margins):
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

    def _test_schedule_safety(
        self,
        schedule: List[Tuple[date, float]],
        sorted_dates: List[date],
        daily_min_margins: List[float],
    ) -> bool:
        """
        Fast safety check for a multi-payment schedule against daily baseline margins.
        """
        date_to_idx = {d: i for i, d in enumerate(sorted_dates)}
        cum_impact = [0.0] * len(sorted_dates)
        
        for p_date, p_amt in schedule:
            if p_date in date_to_idx:
                idx = date_to_idx[p_date]
                cum_impact[idx] += p_amt
            elif p_date < sorted_dates[0]:
                cum_impact[0] += p_amt

        # Propagate cumulative deductions forward
        running_deduction = 0.0
        for i in range(len(sorted_dates)):
            running_deduction += cum_impact[i]
            if daily_min_margins[i] - running_deduction < -1e-4:
                return False
        return True

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
                res = self.forecaster.forecast_user_balance(
                    user_id=user_id,
                    start_date=request_date,
                    spending_changes=sc,
                    additional_payments=[(request_date, requested_amount)],
                )
                if res.min_projected_balance >= res.minimum_balance_to_keep:
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
                res = self.forecaster.forecast_user_balance(
                    user_id=user_id,
                    start_date=request_date,
                    spending_changes=sc,
                    additional_payments=[(request_date, requested_amount)],
                )
                if res.min_projected_balance >= res.minimum_balance_to_keep:
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
