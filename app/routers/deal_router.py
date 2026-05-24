from fastapi import APIRouter, Depends, status

from app.auth import require_admin, require_user
from app.schemas.deal import Deal, DealCreate, DealUpdate
from app.services import deal_service
from app.services.config import settings

router = APIRouter(prefix="/deals", tags=["deals"])


@router.get("/health", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok", "service": settings.SERVICE_NAME, "version": settings.SERVICE_VERSION}


@router.get("", response_model=list[Deal], dependencies=[Depends(require_user)])
async def list_deals(company_id: str | None = None) -> list[Deal]:
    return await deal_service.get_all_deals(company_id=company_id)


@router.get("/{deal_id}", response_model=Deal, dependencies=[Depends(require_user)])
async def get_deal(deal_id: str) -> Deal:
    return await deal_service.get_deal_by_id(deal_id)


@router.post("", response_model=Deal, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_user)])
async def create_deal(payload: DealCreate) -> Deal:
    return await deal_service.create_deal(payload)


@router.put("/{deal_id}", response_model=Deal, dependencies=[Depends(require_user)])
async def update_deal(deal_id: str, payload: DealUpdate) -> Deal:
    return await deal_service.update_deal(deal_id, payload)


@router.delete("/{deal_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_deal(deal_id: str) -> None:
    await deal_service.delete_deal(deal_id)
