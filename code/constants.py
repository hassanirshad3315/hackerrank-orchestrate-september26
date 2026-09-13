"""
Named constants for HackerRank Orchestrate - Buy or Wait?
Contains all deterministic parameters, status enums, conflict hierarchies, and ranking keys.
"""

from typing import Tuple

# Forecast & Projection Parameters
FORECAST_HORIZON_DAYS: int = 90
MAX_SPENDING_CHANGES: int = 3
MIN_RECURRENCE_OCCURRENCES: int = 2

# Affordability Status Enums
STATUS_AFFORDABLE_NOW: str = "affordable_now"
STATUS_AFFORDABLE_WITH_PLAN: str = "affordable_with_plan"
STATUS_AFFORDABLE_LATER: str = "affordable_later"
STATUS_NOT_AFFORDABLE: str = "not_affordable"

ALL_AFFORDABILITY_STATUSES: Tuple[str, ...] = (
    STATUS_AFFORDABLE_NOW,
    STATUS_AFFORDABLE_WITH_PLAN,
    STATUS_AFFORDABLE_LATER,
    STATUS_NOT_AFFORDABLE,
)

# Recommended Payment Method Enums
METHOD_FULL_PAYMENT: str = "full_payment"
METHOD_PARTIAL_PAYMENT: str = "partial_payment"
METHOD_INSTALLMENTS: str = "installments"
METHOD_WAIT: str = "wait"
METHOD_NOT_RECOMMENDED: str = "not_recommended"

ALL_PAYMENT_METHODS: Tuple[str, ...] = (
    METHOD_FULL_PAYMENT,
    METHOD_PARTIAL_PAYMENT,
    METHOD_INSTALLMENTS,
    METHOD_WAIT,
    METHOD_NOT_RECOMMENDED,
)

# Event Direction Enums
DIRECTION_DEBIT: str = "debit"
DIRECTION_CREDIT: str = "credit"
DIRECTION_NON_CASH: str = "non_cash"

# Event Status Enums
EVENT_STATUS_SETTLED: str = "settled"
EVENT_STATUS_PENDING: str = "pending"
EVENT_STATUS_SCHEDULED: str = "scheduled"
EVENT_STATUS_CANCELLED: str = "cancelled"
EVENT_STATUS_FAILED: str = "failed"
EVENT_STATUS_UNREALIZED: str = "unrealized"

# Flexibility Enums
FLEXIBILITY_FIXED: str = "fixed"
FLEXIBILITY_STOPPABLE: str = "stoppable"
FLEXIBILITY_REDUCIBLE: str = "reducible"
FLEXIBILITY_REDUCIBLE_OR_STOPPABLE: str = "reducible_or_stoppable"

# Spending Change Action Types
SPENDING_ACTION_STOP: str = "stop"
SPENDING_ACTION_REDUCE_TO: str = "reduce_to"

# Conflict Resolution Priorities (Higher value = Higher precedence)
CONFLICT_PRIORITY_EXPLICIT_CANCEL_OR_SETTLE: int = 40
CONFLICT_PRIORITY_NEWER_SAME_SOURCE: int = 30
CONFLICT_PRIORITY_SETTLED_EVENT: int = 20
CONFLICT_PRIORITY_FINANCIALLY_SAFER_FALLBACK: int = 10

# Tie-break ranking order
TIE_BREAK_KEYS: Tuple[str, ...] = (
    "completes_by_deadline",
    "requires_no_spending_changes",
    "total_amount_paid",
    "first_payment_date",
    "number_of_payments",
    "payment_option_id",
)
