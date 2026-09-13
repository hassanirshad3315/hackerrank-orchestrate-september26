"""
Pydantic schema for structured fact and amount extraction from images.csv / PNGs.
"""

from typing import Optional
from pydantic import BaseModel, Field


class ImageExtractedFacts(BaseModel):
    image_id: str = Field(..., description="Image identifier (e.g. image_01)")
    user_id: str = Field(..., description="User ID linked in images.csv")
    related_event_id: str = Field(..., description="Linked financial event ID from images.csv")
    extracted_amount: float = Field(
        ..., description="Exact net monetary figure extracted from document"
    )
    currency: str = Field(..., description="Stated currency on document")
    document_type: str = Field(..., description="payslip, utility_bill, rent_receipt, etc.")
    document_date: Optional[str] = Field(
        None, description="Date visible on document (YYYY-MM-DD)"
    )
    is_legible: bool = Field(
        ..., description="True if critical financial amount is clearly readable"
    )
    confidence_reasoning: str = Field(
        ..., description="Explanation of where amount was identified"
    )
