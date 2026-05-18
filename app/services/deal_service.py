import logging
import uuid
from datetime import datetime, timezone

from app.exceptions import ResourceNotFoundException
from app.logging_config import correlation_id_var
from app.schemas.deal import Deal, DealCreate, DealUpdate
from app.services import company_service, storage_service

logger = logging.getLogger("deal_service.service")


def _cid() -> str:
    return correlation_id_var.get("")


async def get_all_deals(company_id: str | None = None) -> list[Deal]:
    correlation_id = _cid()
    logger.info(
        "get_all_deals called",
        extra={"company_id_filter": company_id, "correlation_id": correlation_id},
    )

    raw = await storage_service.read_deals()
    # Only return active deals
    deals = [Deal.model_validate(d) for d in raw if d.get("is_active", True)]

    if company_id is not None:
        deals = [d for d in deals if d.company_id == company_id]

    logger.info(
        "deals loaded from storage",
        extra={"count": len(deals), "company_id_filter": company_id, "correlation_id": correlation_id},
    )

    # Enrich with company details — fetch all unique company IDs in one batch
    unique_ids = list({d.company_id for d in deals})
    companies = await company_service.get_companies(unique_ids)

    logger.info(
        "company enrichment completed",
        extra={"requested": len(unique_ids), "resolved": len(companies), "correlation_id": correlation_id},
    )

    enriched = []
    for deal in deals:
        deal.company = companies.get(deal.company_id)
        enriched.append(deal)

    return enriched


async def get_deal_by_id(deal_id: str) -> Deal:
    correlation_id = _cid()
    logger.info(
        "get_deal_by_id called",
        extra={"deal_id": deal_id, "correlation_id": correlation_id},
    )

    raw = await storage_service.read_deals()
    for d in raw:
        if d["id"] == deal_id and d.get("is_active", True):
            deal = Deal.model_validate(d)
            logger.info(
                "deal found in storage",
                extra={"deal_id": deal_id, "company_id": deal.company_id, "correlation_id": correlation_id},
            )
            # Enrich with company details
            deal.company = await company_service.get_company(deal.company_id)
            logger.info(
                "deal enriched with company",
                extra={
                    "deal_id": deal_id,
                    "company_id": deal.company_id,
                    "company_found": deal.company is not None,
                    "correlation_id": correlation_id,
                },
            )
            return deal

    logger.warning(
        "deal not found",
        extra={"deal_id": deal_id, "correlation_id": correlation_id},
    )
    raise ResourceNotFoundException(message=f"Deal with id '{deal_id}' not found")


async def create_deal(payload: DealCreate) -> Deal:
    correlation_id = _cid()
    logger.info(
        "create_deal called",
        extra={
            "title": payload.title,
            "company_id": payload.company_id,
            "amount": payload.amount,
            "status": payload.status,
            "correlation_id": correlation_id,
        },
    )

    raw = await storage_service.read_deals()
    now = datetime.now(timezone.utc)
    deal = Deal(
        id=str(uuid.uuid4()),
        title=payload.title,
        amount=payload.amount,
        status=payload.status,
        company_id=payload.company_id,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    raw.append(deal.model_dump(mode="json", exclude={"company"}))
    await storage_service.write_deals(raw)

    logger.info(
        "deal created",
        extra={"deal_id": deal.id, "company_id": deal.company_id, "correlation_id": correlation_id},
    )

    # Enrich with company details
    deal.company = await company_service.get_company(deal.company_id)
    logger.info(
        "new deal enriched with company",
        extra={
            "deal_id": deal.id,
            "company_id": deal.company_id,
            "company_found": deal.company is not None,
            "correlation_id": correlation_id,
        },
    )
    return deal


async def update_deal(deal_id: str, payload: DealUpdate) -> Deal:
    correlation_id = _cid()
    logger.info(
        "update_deal called",
        extra={"deal_id": deal_id, "patch": payload.model_dump(exclude_unset=True), "correlation_id": correlation_id},
    )

    raw = await storage_service.read_deals()
    for i, d in enumerate(raw):
        if d["id"] == deal_id and d.get("is_active", True):
            existing = Deal.model_validate(d)
            updated_data = existing.model_dump(exclude={"company"})
            patch = payload.model_dump(exclude_unset=True)
            updated_data.update(patch)
            updated_data["updated_at"] = datetime.now(timezone.utc)
            updated = Deal(**updated_data)
            raw[i] = updated.model_dump(mode="json", exclude={"company"})
            await storage_service.write_deals(raw)

            logger.info(
                "deal updated",
                extra={"deal_id": deal_id, "patch": patch, "correlation_id": correlation_id},
            )

            # Enrich with company details
            updated.company = await company_service.get_company(updated.company_id)
            logger.info(
                "updated deal enriched with company",
                extra={
                    "deal_id": deal_id,
                    "company_id": updated.company_id,
                    "company_found": updated.company is not None,
                    "correlation_id": correlation_id,
                },
            )
            return updated

    logger.warning(
        "deal not found for update",
        extra={"deal_id": deal_id, "correlation_id": correlation_id},
    )
    raise ResourceNotFoundException(message=f"Deal with id '{deal_id}' not found")


async def delete_deal(deal_id: str) -> None:
    """Soft-delete: sets is_active=False instead of removing the record."""
    correlation_id = _cid()
    logger.info(
        "delete_deal called (soft-delete)",
        extra={"deal_id": deal_id, "correlation_id": correlation_id},
    )

    raw = await storage_service.read_deals()
    for i, d in enumerate(raw):
        if d["id"] == deal_id and d.get("is_active", True):
            raw[i]["is_active"] = False
            raw[i]["updated_at"] = datetime.now(timezone.utc).isoformat()
            await storage_service.write_deals(raw)
            logger.info(
                "deal soft-deleted",
                extra={"deal_id": deal_id, "correlation_id": correlation_id},
            )
            return

    logger.warning(
        "deal not found for deletion",
        extra={"deal_id": deal_id, "correlation_id": correlation_id},
    )
    raise ResourceNotFoundException(message=f"Deal with id '{deal_id}' not found")
