import uuid
from datetime import datetime, timezone

from app.exceptions import ResourceNotFoundException
from app.schemas.deal import Deal, DealCreate, DealUpdate
from app.services import storage_service


async def get_all_deals(company_id: str | None = None) -> list[Deal]:
    raw = await storage_service.read_deals()
    deals = [Deal.model_validate(d) for d in raw]
    if company_id is not None:
        deals = [d for d in deals if d.company_id == company_id]
    return deals


async def get_deal_by_id(deal_id: str) -> Deal:
    raw = await storage_service.read_deals()
    for d in raw:
        if d["id"] == deal_id:
            return Deal.model_validate(d)
    raise ResourceNotFoundException(message=f"Deal with id '{deal_id}' not found")


async def create_deal(payload: DealCreate) -> Deal:
    raw = await storage_service.read_deals()
    now = datetime.now(timezone.utc)
    deal = Deal(
        id=str(uuid.uuid4()),
        title=payload.title,
        amount=payload.amount,
        status=payload.status,
        company_id=payload.company_id,
        created_at=now,
        updated_at=now,
    )
    raw.append(deal.model_dump(mode="json"))
    await storage_service.write_deals(raw)
    return deal


async def update_deal(deal_id: str, payload: DealUpdate) -> Deal:
    raw = await storage_service.read_deals()
    for i, d in enumerate(raw):
        if d["id"] == deal_id:
            existing = Deal.model_validate(d)
            updated_data = existing.model_dump()
            patch = payload.model_dump(exclude_unset=True)
            updated_data.update(patch)
            updated_data["updated_at"] = datetime.now(timezone.utc)
            updated = Deal(**updated_data)
            raw[i] = updated.model_dump(mode="json")
            await storage_service.write_deals(raw)
            return updated
    raise ResourceNotFoundException(message=f"Deal with id '{deal_id}' not found")


async def delete_deal(deal_id: str) -> None:
    raw = await storage_service.read_deals()
    new_raw = [d for d in raw if d["id"] != deal_id]
    if len(new_raw) == len(raw):
        raise ResourceNotFoundException(message=f"Deal with id '{deal_id}' not found")
    await storage_service.write_deals(new_raw)
