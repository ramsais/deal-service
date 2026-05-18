"""
HTTP client for the company-service.

All public functions accept an optional `correlation_id` string that is
forwarded as the `x-request-id` header so distributed traces can be joined
across services.
"""
import logging
from typing import Optional

import httpx

from app.logging_config import correlation_id_var
from app.schemas.deal import Company
from app.services.config import settings

logger = logging.getLogger("deal_service.company_client")


def _headers() -> dict:
    cid = correlation_id_var.get("")
    headers = {"Accept": "application/json"}
    if cid:
        headers["x-request-id"] = cid
    if settings.INTERNAL_API_KEY:
        headers["x-internal-api-key"] = settings.INTERNAL_API_KEY
    return headers


async def get_company(company_id: str) -> Optional[Company]:
    """
    Fetch a single company by ID from the company-service.

    Returns None if the company is not found (404) or if the company-service
    is unreachable, so that deal endpoints degrade gracefully.
    """
    url = f"{settings.COMPANY_SERVICE_URL}/companies/{company_id}"
    correlation_id = correlation_id_var.get("")
    logger.info(
        "fetching company from company-service",
        extra={"company_id": company_id, "url": url, "correlation_id": correlation_id},
    )
    try:
        async with httpx.AsyncClient(timeout=settings.COMPANY_SERVICE_TIMEOUT) as client:
            response = await client.get(url, headers=_headers())
        if response.status_code == 404:
            logger.warning(
                "company not found in company-service",
                extra={"company_id": company_id, "correlation_id": correlation_id},
            )
            return None
        response.raise_for_status()
        data = response.json()
        company = Company.model_validate(data)
        logger.info(
            "company fetched successfully",
            extra={"company_id": company_id, "company_name": company.name, "correlation_id": correlation_id},
        )
        return company
    except httpx.HTTPStatusError as exc:
        logger.error(
            "company-service returned error status",
            extra={
                "company_id": company_id,
                "status_code": exc.response.status_code,
                "correlation_id": correlation_id,
            },
        )
        return None
    except httpx.RequestError as exc:
        logger.error(
            "failed to reach company-service",
            extra={"company_id": company_id, "error": str(exc), "correlation_id": correlation_id},
        )
        return None


async def get_companies(company_ids: list[str]) -> dict[str, Company]:
    """
    Fetch multiple companies by their IDs concurrently.

    Returns a mapping of company_id -> Company for all IDs that were found.
    Missing / unreachable companies are silently omitted so callers can still
    return partial results.
    """
    import asyncio

    correlation_id = correlation_id_var.get("")
    logger.info(
        "fetching multiple companies from company-service",
        extra={"company_ids": company_ids, "count": len(company_ids), "correlation_id": correlation_id},
    )
    tasks = {cid: get_company(cid) for cid in set(company_ids)}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    mapping: dict[str, Company] = {}
    for cid, result in zip(tasks.keys(), results):
        if isinstance(result, Company):
            mapping[cid] = result
        elif isinstance(result, Exception):
            logger.warning(
                "unexpected error fetching company",
                extra={"company_id": cid, "error": str(result), "correlation_id": correlation_id},
            )
    logger.info(
        "companies fetched",
        extra={
            "requested": len(company_ids),
            "resolved": len(mapping),
            "correlation_id": correlation_id,
        },
    )
    return mapping
