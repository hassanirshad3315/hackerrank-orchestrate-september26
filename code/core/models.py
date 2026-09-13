"""
Core data models for financial reconstruction and balance forecasting.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Set


@dataclass
class UserProfile:
    user_id: str
    home_currency: str
    current_available_balance: float
    minimum_balance_to_keep: float
    financial_priorities: List[str] = field(default_factory=list)
    expense_categories_to_protect: Set[str] = field(default_factory=set)
    expense_categories_user_is_willing_to_reduce: Set[str] = field(default_factory=set)
    expense_categories_user_is_willing_to_stop: Set[str] = field(default_factory=set)
    payment_methods_user_will_consider: Set[str] = field(default_factory=set)
    max_installment_months: Optional[int] = None


@dataclass
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Optional[float]
    currency: str
    event_date: str  # YYYY-MM-DD
    settlement_date: str  # YYYY-MM-DD
    status: str
    linked_event_id: Optional[str] = None
    flexibility: str = "fixed"
    minimum_allowed_amount: Optional[float] = None


@dataclass
class RecurringPattern:
    category: str
    event_type: str
    frequency: str  # 'monthly_day', 'weekly', 'biweekly', 'custom_days'
    interval_days: int
    day_of_month: Optional[int] = None
    reference_date: date = date(2020, 1, 1)
    amount: float = 0.0
    currency: str = ""
    flexibility: str = "fixed"
    minimum_allowed_amount: Optional[float] = None
    representative_event_id: str = ""
    description: str = ""


@dataclass
class CashFlowEntry:
    entry_date: date
    event_id: str
    description: str
    category: str
    direction: str  # 'debit' or 'credit'
    amount: float  # In user's home currency
    is_recurring: bool = False
    flexibility: str = "fixed"
    minimum_allowed_amount: Optional[float] = None


@dataclass
class DailyBalance:
    balance_date: date
    start_balance: float
    credits: float
    debits: float
    end_balance: float
    entries: List[CashFlowEntry] = field(default_factory=list)


@dataclass
class ForecastResult:
    user_id: str
    start_date: date
    end_date: date
    start_balance: float
    minimum_balance_to_keep: float
    daily_balances: Dict[date, DailyBalance] = field(default_factory=dict)
    min_projected_balance: float = 0.0
    min_balance_date: Optional[date] = None
    all_entries: List[CashFlowEntry] = field(default_factory=list)
