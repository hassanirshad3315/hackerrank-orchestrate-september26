"""
Deterministic 90-day cash flow simulation and minimum balance forecaster.
Simulates daily balance trajectories from request_date forward using reconciled
one-time events, reserved pending debits, confirmed scheduled salary, and recurring commitments.
"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from code.constants import (
    DIRECTION_CREDIT,
    DIRECTION_DEBIT,
    FORECAST_HORIZON_DAYS,
    SPENDING_ACTION_REDUCE_TO,
    SPENDING_ACTION_STOP,
)
from code.core.currency import CurrencyConverter
from code.core.models import (
    CashFlowEntry,
    DailyBalance,
    FinancialEvent,
    ForecastResult,
    RecurringPattern,
    UserProfile,
)
from code.core.reconciler import FinancialReconciler


class CashFlowForecaster:
    def __init__(self, reconciler: FinancialReconciler):
        self.reconciler = reconciler
        self.converter = reconciler.converter

    def forecast_user_balance(
        self,
        user_id: str,
        start_date: date,
        horizon_days: int = FORECAST_HORIZON_DAYS,
        spending_changes: Optional[List[str]] = None,
        additional_payments: Optional[List[Tuple[date, float]]] = None,
    ) -> ForecastResult:
        """
        Runs a deterministic day-by-day cash flow simulation over the horizon_days.
        Returns the ForecastResult containing daily balances, minimum balance reached, and all entries.
        """
        profile = self.reconciler.profiles[user_id]
        home_curr = profile.home_currency
        start_balance = profile.current_available_balance
        min_balance_to_keep = profile.minimum_balance_to_keep

        # Parse spending changes
        active_changes: Dict[str, Tuple[str, float]] = {}
        if spending_changes:
            for sc in spending_changes:
                if not sc or sc == "none":
                    continue
                parts = sc.split(":")
                action = parts[0].strip()
                ev_id = parts[1].strip()
                if action == SPENDING_ACTION_STOP:
                    active_changes[ev_id] = (SPENDING_ACTION_STOP, 0.0)
                elif action == SPENDING_ACTION_REDUCE_TO:
                    target_amt = float(parts[2].strip())
                    active_changes[ev_id] = (SPENDING_ACTION_REDUCE_TO, target_amt)

        future_events, recurring_patterns = self.reconciler.reconcile_user_events(
            user_id, start_date
        )

        entries_by_date: Dict[date, List[CashFlowEntry]] = {}
        end_date = start_date + timedelta(days=horizon_days)

        # Track scheduled event categories so recurring patterns don't duplicate them on the same date
        scheduled_dates_by_cat: Set[Tuple[str, date]] = set()

        # 1. Schedule one-time/scheduled future events and pending debits
        for e in future_events:
            e_date = (
                datetime.strptime(e.settlement_date, "%Y-%m-%d").date()
                if e.settlement_date
                else datetime.strptime(e.event_date, "%Y-%m-%d").date()
            )
            if e_date < start_date:
                e_date = start_date  # Process immediate pending debit today
            if e_date > end_date:
                continue

            if e.amount is None:
                continue

            amt = self.converter.convert(e.amount, e.currency, home_curr, e.settlement_date)

            if e.event_id in active_changes:
                action, new_amt = active_changes[e.event_id]
                if action == SPENDING_ACTION_STOP:
                    amt = 0.0
                elif action == SPENDING_ACTION_REDUCE_TO:
                    amt = new_amt

            if amt > 0:
                scheduled_dates_by_cat.add((e.category, e_date))
                entry = CashFlowEntry(
                    entry_date=e_date,
                    event_id=e.event_id,
                    description=e.description,
                    category=e.category,
                    direction=e.direction,
                    amount=amt,
                    is_recurring=False,
                    flexibility=e.flexibility,
                    minimum_allowed_amount=e.minimum_allowed_amount,
                )
                if e_date not in entries_by_date:
                    entries_by_date[e_date] = []
                entries_by_date[e_date].append(entry)

        # 2. Project recurring expense and income patterns across the horizon
        for pat in recurring_patterns:
            pat_amt = pat.amount
            if pat.representative_event_id in active_changes:
                action, target_amt = active_changes[pat.representative_event_id]
                if action == SPENDING_ACTION_STOP:
                    pat_amt = 0.0
                elif action == SPENDING_ACTION_REDUCE_TO:
                    min_floor = pat.minimum_allowed_amount if pat.minimum_allowed_amount is not None else 0.0
                    pat_amt = max(target_amt, min_floor)

            if pat_amt <= 0:
                continue

            direction = DIRECTION_CREDIT if pat.event_type == "income" else DIRECTION_DEBIT

            proj_dates: List[date] = []
            if pat.frequency == "monthly_day" and pat.day_of_month is not None:
                curr_y = start_date.year
                curr_m = start_date.month
                for _ in range(5):
                    import calendar
                    max_day = calendar.monthrange(curr_y, curr_m)[1]
                    d_day = min(pat.day_of_month, max_day)
                    d_val = date(curr_y, curr_m, d_day)
                    if start_date <= d_val <= end_date:
                        # Only add if not already scheduled as a one-time event on that date
                        if (pat.category, d_val) not in scheduled_dates_by_cat:
                            proj_dates.append(d_val)
                    curr_m += 1
                    if curr_m > 12:
                        curr_m = 1
                        curr_y += 1
            else:
                interval = pat.interval_days if pat.interval_days > 0 else 7
                cur_d = pat.reference_date
                while cur_d < start_date:
                    cur_d += timedelta(days=interval)
                while cur_d <= end_date:
                    if (pat.category, cur_d) not in scheduled_dates_by_cat:
                        proj_dates.append(cur_d)
                    cur_d += timedelta(days=interval)

            for d_val in proj_dates:
                entry = CashFlowEntry(
                    entry_date=d_val,
                    event_id=pat.representative_event_id,
                    description=f"{pat.description} (recurring)",
                    category=pat.category,
                    direction=direction,
                    amount=pat_amt,
                    is_recurring=True,
                    flexibility=pat.flexibility,
                    minimum_allowed_amount=pat.minimum_allowed_amount,
                )
                if d_val not in entries_by_date:
                    entries_by_date[d_val] = []
                entries_by_date[d_val].append(entry)

        # 3. Add any proposed payment plan debits (for safety testing)
        if additional_payments:
            for p_date, p_amt in additional_payments:
                if p_amt > 0 and start_date <= p_date <= end_date:
                    entry = CashFlowEntry(
                        entry_date=p_date,
                        event_id="plan_payment",
                        description="Proposed plan payment",
                        category="proposed_plan",
                        direction=DIRECTION_DEBIT,
                        amount=p_amt,
                        is_recurring=False,
                        flexibility="fixed",
                    )
                    if p_date not in entries_by_date:
                        entries_by_date[p_date] = []
                    entries_by_date[p_date].append(entry)

        # 4. Step day-by-day and calculate running balance
        daily_balances: Dict[date, DailyBalance] = {}
        all_entries: List[CashFlowEntry] = []
        current_bal = start_balance
        min_proj_bal = start_balance
        min_proj_date = start_date

        cur_day = start_date
        while cur_day <= end_date:
            day_entries = entries_by_date.get(cur_day, [])
            all_entries.extend(day_entries)

            day_credits = sum(e.amount for e in day_entries if e.direction == DIRECTION_CREDIT)
            day_debits = sum(e.amount for e in day_entries if e.direction == DIRECTION_DEBIT)

            day_start_bal = current_bal
            intraday_low = day_start_bal - day_debits
            if intraday_low < min_proj_bal:
                min_proj_bal = intraday_low
                min_proj_date = cur_day

            day_end_bal = day_start_bal + day_credits - day_debits
            if day_end_bal < min_proj_bal:
                min_proj_bal = day_end_bal
                min_proj_date = cur_day

            daily_balances[cur_day] = DailyBalance(
                balance_date=cur_day,
                start_balance=round(day_start_bal, 2),
                credits=round(day_credits, 2),
                debits=round(day_debits, 2),
                end_balance=round(day_end_bal, 2),
                entries=day_entries,
            )

            current_bal = day_end_bal
            cur_day += timedelta(days=1)

        return ForecastResult(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            start_balance=round(start_balance, 2),
            minimum_balance_to_keep=round(min_balance_to_keep, 2),
            daily_balances=daily_balances,
            min_projected_balance=round(min_proj_bal, 2),
            min_balance_date=min_proj_date,
            all_entries=all_entries,
        )
