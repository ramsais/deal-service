"""
HTTP client for the company-service with retries and circuit breaker.

All public functions accept an optional `correlation_id` string that is
forwarded as the `x-request-id` header so distributed traces can be joined
across services.
"""
import asyncio
import logging
from datetime import timedelta
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from aiobreaker import CircuitBreaker, CircuitBreakerError

from app.logging_config import correlation_id_var
from app.schemas.deal import Company
from app.services.config import settings

logger = logging.getLogger("deal_service.company_client")

# Circuit Breaker configuration (fail-fast when remote service is unhealthy)
# aiobreaker expects `timeout_duration` as a datetime.timedelta. Use env-driven value from settings.
_company_cb = CircuitBreaker(
    fail_max=settings.COMPANY_SERVICE_BREAKER_MAX_FAILURES,
    timeout_duration=timedelta(seconds=settings.COMPANY_SERVICE_BREAKER_RESET_TIMEOUT),
    name="company-service-breaker",
)


def _headers() -> dict:
    cid = correlation_id_var.get("")
    headers = {"Accept": "application/json"}
    if cid:
        # Preserve legacy x-request-id and also forward x-correlation-id for log correlation
        headers["x-request-id"] = cid
        headers["x-correlation-id"] = cid
    if settings.INTERNAL_API_KEY:
        headers["x-internal-api-key"] = settings.INTERNAL_API_KEY

    # Add OpenTelemetry trace context to headers for manual propagation
    try:
        from opentelemetry import trace
        from opentelemetry.propagate import inject

        # Inject trace context into headers
        inject(headers)
    except Exception as e:
        # Log but don't fail if OTel propagation fails
        logger.debug(f"Failed to inject OTel context into headers: {e}")

    return headers


def _is_retryable_exception(exc: BaseException) -> bool:
    # Network errors, timeouts
    if isinstance(exc, httpx.RequestError):
        return True
    # Retry only on 5xx responses, not 4xx like 404/400
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return 500 <= exc.response.status_code < 600
    return False


def _extract_company_id_from_retry_state(retry_state) -> Optional[str]:
    # Decorated function signature: _fetch_company(company_id: str)
    try:
        if retry_state.kwargs and "company_id" in retry_state.kwargs:
            return retry_state.kwargs.get("company_id")
        if retry_state.args and len(retry_state.args) >= 1:
            return retry_state.args[0]
    except Exception:
        return None
    return None


def _log_before_sleep(retry_state) -> None:
    """Log detailed retry/backoff info before sleeping for next attempt."""
    company_id = _extract_company_id_from_retry_state(retry_state)
    attempt = getattr(retry_state, "attempt_number", None)
    # Tenacity >=8 provides next_action.sleep; fallback to idle_for if unavailable
    sleep_seconds = None
    try:
        next_action = getattr(retry_state, "next_action", None)
        if next_action is not None:
            sleep_seconds = getattr(next_action, "sleep", None)
        if sleep_seconds is None:
            sleep_seconds = getattr(retry_state, "idle_for", None)
    except Exception:
        sleep_seconds = None

    exc = retry_state.outcome.exception() if retry_state.outcome else None
    status = None
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        status = exc.response.status_code

    logger.warning(
        "retrying company-service call",
        extra={
            "company_id": company_id,
            "attempt": attempt,
            "max_attempts": settings.COMPANY_SERVICE_RETRY_MAX,
            "sleep_seconds": sleep_seconds,
            "timeout_seconds": settings.COMPANY_SERVICE_TIMEOUT,
            "error_type": type(exc).__name__ if exc else None,
            "status_code": status,
        },
    )


def _log_after_call(retry_state) -> None:
    """Log attempt result after each call (success or failure)."""
    company_id = _extract_company_id_from_retry_state(retry_state)
    attempt = getattr(retry_state, "attempt_number", None)
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if exc is None:
        logger.info(
            "company-service call succeeded",
            extra={
                "company_id": company_id,
                "attempt": attempt,
                "timeout_seconds": settings.COMPANY_SERVICE_TIMEOUT,
            },
        )
    else:
        status = None
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
            status = exc.response.status_code
        logger.debug(
            "company-service call failed on attempt",
            extra={
                "company_id": company_id,
                "attempt": attempt,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "status_code": status,
            },
        )


@_company_cb
@retry(
    reraise=True,
    stop=stop_after_attempt(settings.COMPANY_SERVICE_RETRY_MAX),
    wait=wait_exponential(
        multiplier=settings.COMPANY_SERVICE_RETRY_BACKOFF_MULTIPLIER,
        min=settings.COMPANY_SERVICE_RETRY_BACKOFF_MIN,
        max=settings.COMPANY_SERVICE_RETRY_BACKOFF_MAX,
    ),
    retry=retry_if_exception(_is_retryable_exception),
    before_sleep=_log_before_sleep,
    after=_log_after_call,
)
async def _fetch_company(company_id: str) -> Optional[Company]:
    url = f"{settings.COMPANY_SERVICE_URL}/companies/{company_id}"
    async with httpx.AsyncClient(timeout=settings.COMPANY_SERVICE_TIMEOUT) as client:
        response = await client.get(url, headers=_headers())
    # Do not retry or raise for 404: treat as not found
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    return Company.model_validate(data)


async def get_company(company_id: str) -> Optional[Company]:
    """
    Fetch a single company by ID from the company-service.

    Returns None if the company is not found (404), circuit is open, or if the
    company-service is unreachable after retries, so that deal endpoints
    degrade gracefully.
    """
    correlation_id = correlation_id_var.get("")
    url = f"{settings.COMPANY_SERVICE_URL}/companies/{company_id}"
    logger.info(
        "fetching company from company-service",
        extra={
            "company_id": company_id,
            "url": url,
            "timeout_seconds": settings.COMPANY_SERVICE_TIMEOUT,
            "breaker_state": str(_company_cb.current_state),
            "correlation_id": correlation_id,
        },
    )
    try:
        company = await _fetch_company(company_id)
        if company is None:
            logger.warning(
                "company not found in company-service",
                extra={"company_id": company_id, "correlation_id": correlation_id},
            )
            return None
        logger.info(
            "company fetched successfully",
            extra={"company_id": company_id, "company_name": company.name, "correlation_id": correlation_id},
        )
        return company
    except CircuitBreakerError:
        remaining = None
        opens_at = None
        try:
            td = _company_cb.time_until_open
            remaining = td.total_seconds() if td else None
            opens_at = _company_cb.opens_at
        except Exception:
            pass
        logger.error(
            "company-service circuit breaker open",
            extra={
                "company_id": company_id,
                "remaining_open_seconds": remaining,
                "opens_at": opens_at,
                "correlation_id": correlation_id,
            },
        )
        return None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else None
        logger.error(
            "company-service returned error status",
            extra={"company_id": company_id, "status_code": status, "correlation_id": correlation_id},
        )
        return None
    except httpx.RequestError as exc:
        logger.error(
            "failed to reach company-service",
            extra={"company_id": company_id, "error": str(exc), "correlation_id": correlation_id},
        )
        return None
    except Exception as exc:  # defensive: never fail caller due to unexpected error
        logger.error(
            "unexpected error fetching company",
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
