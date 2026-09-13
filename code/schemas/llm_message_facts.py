"""
Pydantic schema for structured fact extraction from messages.csv.
"""

from typing import Optional
from pydantic import BaseModel, Field


class MessageExtractedFacts(BaseModel):
    message_id: str = Field(..., description="ID of the message processed")
    user_id: str = Field(..., description="User ID associated with message")
    fact_type: str = Field(
        ...,
        description="Category of financial update extracted",
    )
    target_event_id: Optional[str] = Field(
        None, description="Specific related_event_id if linked or described in message"
    )
    updated_amount: Optional[float] = Field(
        None, description="Exact confirmed amount stated in message"
    )
    currency: Optional[str] = Field(None, description="Currency of the stated amount")
    effective_date: Optional[str] = Field(
        None, description="Effective settlement/payment date (YYYY-MM-DD)"
    )
    is_confirmed: bool = Field(
        ...,
        description="True only if the financial fact/income is final and confirmed",
    )
    is_cancelled: bool = Field(
        ..., description="True if an event or contract is explicitly terminated"
    )
    is_delayed: bool = Field(
        ..., description="True if payment date was pushed back"
    )
    confidence_reasoning: str = Field(
        ..., description="Exact citation from message justifying extraction"
    )
