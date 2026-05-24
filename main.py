# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
from app.logging_config import configure_logging, RequestLoggingMiddleware
from app.services.config import settings

configure_logging(level=settings.LOG_LEVEL)
import logging
import os
from fastapi import FastAPI
from app.exceptions import GlobalExceptionHandlers
from app.routers.deal_router import router as deal_router
# ---------------------------------------------------------------------------
# OpenTelemetry setup (AWS X-Ray compatible)
# ---------------------------------------------------------------------------
from opentelemetry import trace, propagate
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator
from opentelemetry.propagators.aws import AwsXRayPropagator
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
# from opentelemetry.instrumentation.requests import RequestsInstrumentor

try:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # optional
except Exception:  # pragma: no cover - instrumentation may be optional at runtime
    HTTPXClientInstrumentor = None


def init_telemetry():
    """
    Initialize OpenTelemetry tracing with AWS X-Ray ID generator and OTLP exporter.
    The exporter endpoint can be provided via settings or OTEL_EXPORTER_OTLP_ENDPOINT env var.
    """
    service_name = settings.OTEL_SERVICE_NAME or settings.SERVICE_NAME
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": settings.SERVICE_VERSION,
            "deployment.environment": settings.ENV,
        }
    )
    provider = TracerProvider(resource=resource, id_generator=AwsXRayIdGenerator())
    endpoint = (
            settings.OTEL_EXPORTER_OTLP_ENDPOINT
            or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            or "http://127.0.0.1:4318"
    )
    span_exporter = OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(provider)
    # Use a composite propagator so incoming context from either W3C traceparent or AWS X-Ray is respected
    propagate.set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), AwsXRayPropagator()])
    )
    return provider


# Initialize telemetry once at import time
_tracer_provider = init_telemetry()
logger = logging.getLogger("deal_service")
# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Deal Service",
    description="A microservice for managing deal data with local JSON storage.",
    version=settings.SERVICE_VERSION,
)
# RequestLoggingMiddleware must be added first so correlation_id is set
# before any other middleware or handler runs.
app.add_middleware(RequestLoggingMiddleware)
# Centralized exception handling via exceptions.handlers.GlobalExceptionHandlers
GlobalExceptionHandlers.register(app)

# Routers
app.include_router(deal_router)
# Instrument FastAPI and outbound HTTP clients
FastAPIInstrumentor.instrument_app(app, tracer_provider=trace.get_tracer_provider())
# HTTPXClientInstrumentor().instrument()
if HTTPXClientInstrumentor:
    try:
        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint for ECS cluster service — no authentication required.
    Returns 200 only if the service is ready to serve traffic.
    """
    logger.info("Health check called")
    try:
        from app.services.storage_service import DealStorage
        DealStorage()
        logger.info(
            "Health check passed",
            extra={"service": settings.SERVICE_NAME, "version": settings.SERVICE_VERSION},
        )
        return {
            "status": "healthy",
            "service": settings.SERVICE_NAME,
            "version": settings.SERVICE_VERSION,
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)},
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=9000,
        log_config=None,  # Disable Uvicorn's default logging config to use our custom JSON logging
        access_log=True,
    )
