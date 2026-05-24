from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class Company(BaseModel):
    """Pydantic model representing a company fetched from the company-service."""

    model_config = ConfigDict(strict=False)

    id: str
    name: str
    industry: Optional[str] = None
    website: Optional[str] = None
    location: Optional[str] = None  # Added: matches company-service response
    # phone: Optional[str] = None
    # email: Optional[str] = None
    # address: Optional[str] = None
    is_active: Optional[bool] = None
    created_at: Optional[datetime] = None  # Added: matches company-service response
    updated_at: Optional[datetime] = None  # Added: matches company-service response


class Deal(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str
    title: str
    amount: float
    status: str  # "Open", "Closed", "Won", "Lost"
    company_id: str
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
    company: Optional[Company] = None


class DealCreate(BaseModel):
    model_config = ConfigDict(strict=True)

    title: str
    amount: float
    status: str
    company_id: str


class DealUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    title: Optional[str] = None
    amount: Optional[float] = None
    status: Optional[str] = None
    company_id: Optional[str] = None