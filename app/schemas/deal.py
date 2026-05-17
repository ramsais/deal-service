from datetime import datetime
from pydantic import BaseModel, ConfigDict


class Deal(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str
    title: str
    amount: float
    status: str  # "Open", "Closed", "Won", "Lost"
    company_id: str
    created_at: datetime
    updated_at: datetime


class DealCreate(BaseModel):
    model_config = ConfigDict(strict=True)

    title: str
    amount: float
    status: str
    company_id: str


class DealUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    title: str | None = None
    amount: float | None = None
    status: str | None = None
    company_id: str | None = None
