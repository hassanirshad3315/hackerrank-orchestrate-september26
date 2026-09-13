"""
Deterministic financial state reconciler and event deduplicator.
Separates recurring commitments from one-time events, applies conflict resolution,
reserves pending debits, ignores pending credits / unrealized gains, and converts foreign currencies.
Integrates message facts (payroll/contract updates) and image facts (missing amounts).
"""

import csv
from datetime import date, datetime
from typing import Dict, List, Optional, Set, Tuple

from code.constants import (
    DIRECTION_CREDIT,
    DIRECTION_DEBIT,
    DIRECTION_NON_CASH,
    EVENT_STATUS_CANCELLED,
    EVENT_STATUS_FAILED,
    EVENT_STATUS_PENDING,
    EVENT_STATUS_SCHEDULED,
    EVENT_STATUS_SETTLED,
    EVENT_STATUS_UNREALIZED,
    FLEXIBILITY_FIXED,
    FLEXIBILITY_REDUCIBLE,
    FLEXIBILITY_REDUCIBLE_OR_STOPPABLE,
    FLEXIBILITY_STOPPABLE,
    MIN_RECURRENCE_OCCURRENCES,
)
from code.core.currency import CurrencyConverter
from code.core.models import FinancialEvent, RecurringPattern, UserProfile
from code.llm.image_extractor import ImageFactExtractor
from code.llm.message_extractor import MessageFactExtractor


class FinancialReconciler:
    def __init__(
        self,
        profiles_path: str = "dataset/financial_profiles.csv",
        events_path: str = "dataset/financial_events.csv",
        currency_converter: Optional[CurrencyConverter] = None,
        message_extractor: Optional[MessageFactExtractor] = None,
        image_extractor: Optional[ImageFactExtractor] = None,
    ):
        self.converter = currency_converter or CurrencyConverter()
        self.msg_extractor = message_extractor or MessageFactExtractor()
        self.img_extractor = image_extractor or ImageFactExtractor()
        self.profiles: Dict[str, UserProfile] = {}
        self.events_by_user: Dict[str, List[FinancialEvent]] = {}
        self.load_profiles(profiles_path)
        self.load_events(events_path)

    def load_profiles(self, path: str) -> None:
        with open(path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row["user_id"].strip()
                max_inst = (
                    int(row["max_installment_months"].strip())
                    if row.get("max_installment_months") and row["max_installment_months"].strip()
                    else None
                )

                def parse_set(val: Optional[str]) -> Set[str]:
                    if not val or not val.strip():
                        return set()
                    return {x.strip() for x in val.strip().split("|") if x.strip()}

                def parse_list(val: Optional[str]) -> List[str]:
                    if not val or not val.strip():
                        return []
                    return [x.strip() for x in val.strip().split("|") if x.strip()]

                profile = UserProfile(
                    user_id=uid,
                    home_currency=row["home_currency"].strip(),
                    current_available_balance=float(row["current_available_balance"].strip()),
                    minimum_balance_to_keep=float(row["minimum_balance_to_keep"].strip()),
                    financial_priorities=parse_list(row.get("financial_priorities")),
                    expense_categories_to_protect=parse_set(row.get("expense_categories_to_protect")),
                    expense_categories_user_is_willing_to_reduce=parse_set(
                        row.get("expense_categories_user_is_willing_to_reduce")
                    ),
                    expense_categories_user_is_willing_to_stop=parse_set(
                        row.get("expense_categories_user_is_willing_to_stop")
                    ),
                    payment_methods_user_will_consider=parse_set(
                        row.get("payment_methods_user_will_consider")
                    ),
                    max_installment_months=max_inst,
                )
                self.profiles[uid] = profile

    def load_events(self, path: str) -> None:
        with open(path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row["user_id"].strip()
                ev_id = row["event_id"].strip()
                amt_str = row["amount"].strip() if row.get("amount") else ""
                amt = float(amt_str) if amt_str else None

                # Check if amount is missing and extract from linked image
                if amt is None:
                    img_fact = self.img_extractor.get_event_fact(ev_id)
                    if img_fact and img_fact.is_legible:
                        amt = img_fact.extracted_amount

                min_amt_str = (
                    row["minimum_allowed_amount"].strip()
                    if row.get("minimum_allowed_amount")
                    else ""
                )
                min_amt = float(min_amt_str) if min_amt_str else None

                linked = row["linked_event_id"].strip() if row.get("linked_event_id") else None
                if linked == "":
                    linked = None

                event = FinancialEvent(
                    event_id=ev_id,
                    user_id=uid,
                    event_type=row["event_type"].strip(),
                    description=row["description"].strip(),
                    category=row["category"].strip(),
                    direction=row["direction"].strip(),
                    amount=amt,
                    currency=row["currency"].strip(),
                    event_date=row["event_date"].strip(),
                    settlement_date=row["settlement_date"].strip(),
                    status=row["status"].strip(),
                    linked_event_id=linked,
                    flexibility=row.get("flexibility", FLEXIBILITY_FIXED).strip() or FLEXIBILITY_FIXED,
                    minimum_allowed_amount=min_amt,
                )
                if uid not in self.events_by_user:
                    self.events_by_user[uid] = []
                self.events_by_user[uid].append(event)

    def reconcile_user_events(
        self, user_id: str, request_date: date
    ) -> Tuple[List[FinancialEvent], List[RecurringPattern]]:
        """
        Reconciles events for a user as of request_date, applying conflict resolution and message facts.
        """
        all_events = self.events_by_user.get(user_id, [])
        profile = self.profiles[user_id]
        home_curr = profile.home_currency

        # Index events by event_id
        events_by_id: Dict[str, FinancialEvent] = {e.event_id: e for e in all_events}

        # Track cancelled or superseded parent events
        suppressed_event_ids: Set[str] = set()
        for e in all_events:
            if e.linked_event_id and e.linked_event_id in events_by_id:
                parent = events_by_id[e.linked_event_id]
                if e.status == EVENT_STATUS_SETTLED and parent.status in (
                    EVENT_STATUS_CANCELLED,
                    EVENT_STATUS_PENDING,
                    EVENT_STATUS_SETTLED,
                ):
                    suppressed_event_ids.add(parent.event_id)
                if parent.status == EVENT_STATUS_FAILED:
                    suppressed_event_ids.add(parent.event_id)

        # Apply message extracted updates (e.g. salary change, date change, contract end)
        msg_facts = self.msg_extractor.extract_user_facts(user_id)
        msg_salary_amt: Optional[float] = None
        msg_salary_date: Optional[str] = None
        contract_ended = False

        for mf in msg_facts:
            if mf.fact_type == "contract_ended_no_income":
                contract_ended = True
            elif mf.fact_type == "salary_amount_update" and mf.updated_amount is not None:
                msg_salary_amt = mf.updated_amount
            elif mf.fact_type == "salary_date_update" and mf.effective_date:
                msg_salary_date = mf.effective_date

        historical_events: List[FinancialEvent] = []
        future_or_pending_events: List[FinancialEvent] = []

        for e in all_events:
            if e.event_id in suppressed_event_ids:
                continue
            if e.status in (EVENT_STATUS_CANCELLED, EVENT_STATUS_FAILED, EVENT_STATUS_UNREALIZED):
                continue
            if e.direction == DIRECTION_NON_CASH:
                continue

            e_settle = (
                datetime.strptime(e.settlement_date, "%Y-%m-%d").date()
                if e.settlement_date
                else None
            )

            # Pending credits must be ignored until settled
            if e.direction == DIRECTION_CREDIT and e.status == EVENT_STATUS_PENDING:
                continue

            # If salary was updated by message, update scheduled salary event
            if e.category == "salary":
                if contract_ended:
                    continue
                if msg_salary_amt is not None and e.status == EVENT_STATUS_SCHEDULED:
                    e.amount = msg_salary_amt
                if msg_salary_date is not None and e.status == EVENT_STATUS_SCHEDULED:
                    e.settlement_date = msg_salary_date
                    e_settle = datetime.strptime(msg_salary_date, "%Y-%m-%d").date()

            # Historical settled event
            if e.status == EVENT_STATUS_SETTLED and e_settle and e_settle < request_date:
                historical_events.append(e)
            # Pending debits or scheduled future events on or after request_date
            elif (
                (e.status == EVENT_STATUS_PENDING and e.direction == DIRECTION_DEBIT)
                or (e.status == EVENT_STATUS_SCHEDULED and e_settle and e_settle >= request_date)
                or (e.status == EVENT_STATUS_SETTLED and e_settle and e_settle >= request_date)
            ):
                future_or_pending_events.append(e)

        # Detect recurring patterns from historical events and scheduled income
        recurring_patterns = self._detect_recurrence_patterns(
            historical_events, future_or_pending_events, home_curr, contract_ended, msg_salary_amt
        )

        return future_or_pending_events, recurring_patterns

    def _detect_recurrence_patterns(
        self,
        historical_events: List[FinancialEvent],
        future_events: List[FinancialEvent],
        home_currency: str,
        contract_ended: bool,
        msg_salary_amt: Optional[float],
    ) -> List[RecurringPattern]:
        groups: Dict[Tuple[str, str], List[FinancialEvent]] = {}
        for e in historical_events:
            if e.amount is None or e.amount <= 0:
                continue
            key = (e.category, e.event_type)
            if key not in groups:
                groups[key] = []
            groups[key].append(e)

        patterns: List[RecurringPattern] = []

        # Check for regular salary recurring monthly
        if not contract_ended:
            salary_hist = groups.get(("salary", "income"), [])
            salary_fut = [e for e in future_events if e.category == "salary"]
            if salary_hist or salary_fut:
                all_sal = salary_hist + salary_fut
                sal_sorted = sorted(
                    all_sal, key=lambda x: datetime.strptime(x.event_date, "%Y-%m-%d").date()
                )
                latest_sal = sal_sorted[-1]
                sal_day = datetime.strptime(latest_sal.settlement_date, "%Y-%m-%d").date().day
                sal_amt = (
                    msg_salary_amt
                    if msg_salary_amt is not None
                    else latest_sal.amount
                )
                home_sal = self.converter.convert(
                    sal_amt, latest_sal.currency, home_currency, latest_sal.settlement_date
                )
                patterns.append(
                    RecurringPattern(
                        category="salary",
                        event_type="income",
                        frequency="monthly_day",
                        interval_days=30,
                        day_of_month=sal_day,
                        reference_date=datetime.strptime(latest_sal.settlement_date, "%Y-%m-%d").date(),
                        amount=home_sal,
                        currency=home_currency,
                        flexibility=FLEXIBILITY_FIXED,
                        minimum_allowed_amount=None,
                        representative_event_id=latest_sal.event_id,
                        description=latest_sal.description,
                    )
                )

        for (cat, etype), evs in groups.items():
            if cat == "salary":
                continue  # Handled above
            if len(evs) < MIN_RECURRENCE_OCCURRENCES:
                continue

            evs_sorted = sorted(
                evs, key=lambda x: datetime.strptime(x.event_date, "%Y-%m-%d").date()
            )
            dates = [datetime.strptime(x.event_date, "%Y-%m-%d").date() for x in evs_sorted]

            intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
            if not intervals:
                continue

            median_interval = sorted(intervals)[len(intervals) // 2]
            home_amounts = [
                self.converter.convert(x.amount, x.currency, home_currency, x.settlement_date)
                for x in evs_sorted
            ]

            latest_ev = evs_sorted[-1]
            flex = latest_ev.flexibility
            min_allowed = (
                self.converter.convert(
                    latest_ev.minimum_allowed_amount,
                    latest_ev.currency,
                    home_currency,
                    latest_ev.settlement_date,
                )
                if latest_ev.minimum_allowed_amount is not None
                else None
            )

            if 27 <= median_interval <= 32:
                days_of_month = [d.day for d in dates]
                target_day = days_of_month[-1]
                amount = home_amounts[-1]
                patterns.append(
                    RecurringPattern(
                        category=cat,
                        event_type=etype,
                        frequency="monthly_day",
                        interval_days=median_interval,
                        day_of_month=target_day,
                        reference_date=dates[-1],
                        amount=amount,
                        currency=home_currency,
                        flexibility=flex,
                        minimum_allowed_amount=min_allowed,
                        representative_event_id=latest_ev.event_id,
                        description=latest_ev.description,
                    )
                )
            elif 4 <= median_interval <= 25:
                recent_amounts = home_amounts[-4:] if len(home_amounts) >= 4 else home_amounts
                conservative_amount = (
                    max(recent_amounts) if cat in ("groceries", "dining", "transport") else home_amounts[-1]
                )
                patterns.append(
                    RecurringPattern(
                        category=cat,
                        event_type=etype,
                        frequency="custom_days",
                        interval_days=median_interval,
                        reference_date=dates[-1],
                        amount=conservative_amount,
                        currency=home_currency,
                        flexibility=flex,
                        minimum_allowed_amount=min_allowed,
                        representative_event_id=latest_ev.event_id,
                        description=latest_ev.description,
                    )
                )

        return patterns
