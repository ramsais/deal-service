# ---------------------------------------------------------------------------
# 1. OTel MUST be configured before FastAPI is imported / instantiated
# ---------------------------------------------------------------------------
from app.telemetry import configure_telemetry, instrument_app

configure_telemetry(service_name="deal-service", service_version="1.0.2")

# ---------------------------------------------------------------------------
# 2. Logging setup — after OTel so JsonFormatter can read OTel span context
# ---------------------------------------------------------------------------
from app.services.config import settings
from app.logging_config import configure_logging, RequestLoggingMiddleware

configure_logging(level=settings.LOG_LEVEL)

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions import AppException
from app.routers.deal_router import router as deal_router

logger = logging.getLogger("deal_service")

app = FastAPI(title="Deal Service", version="1.0.0")

# Must be the first middleware added
app.add_middleware(RequestLoggingMiddleware)

# ---------------------------------------------------------------------------
# 3. Wire OTel FastAPI instrumentation AFTER app + middleware are registered
# ---------------------------------------------------------------------------
instrument_app(app, excluded_urls="health")


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "message": exc.message, "details": exc.details},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"message": "An unexpected internal server error occurred."},
    )


app.include_router(deal_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """
    Health endpoint for ECS cluster service / ALB target group health checks.
    Returns service name, version, and status so the load balancer can verify
    the container is ready to serve traffic.
    """
    return {
        "status": "ok",
        "service": "deal-service",
        "version": "1.0.0",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)