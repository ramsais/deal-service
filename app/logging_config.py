import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Carries correlation_id across async tasks within a single request
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")

# Store OpenTelemetry context for the request
otel_context_var: ContextVar[dict] = ContextVar("otel_context", default={"trace_id": "", "span_id": ""})

# Header names — API Gateway injects X-Amzn-Trace-Id; clients may send X-Correlation-ID
CORRELATION_ID_HEADER = "x-request-id"
_AMZN_TRACE_HEADER = "x-amzn-trace-id"
_CORRELATION_HEADER = "x-correlation-id"


def _get_otel_trace_context() -> dict:
    """Return active OpenTelemetry trace/span IDs if available for log correlation."""
    try:
        from opentelemetry import trace  # type: ignore
        from opentelemetry.context import get_current
        from opentelemetry.trace import get_current_span

        # Method 1: Try to get span from current context
        span = trace.get_current_span()
        if span is not None and span.get_span_context().is_valid:
            ctx = span.get_span_context()
            return {
                "trace_id": f"{ctx.trace_id:032x}",
                "span_id": f"{ctx.span_id:016x}",
            }

        # Method 2: Try to get span from explicit current context
        current_context = get_current()
        if current_context:
            span_from_context = get_current_span(current_context)
            if span_from_context and span_from_context.get_span_context().is_valid:
                ctx = span_from_context.get_span_context()
                return {
                    "trace_id": f"{ctx.trace_id:032x}",
                    "span_id": f"{ctx.span_id:016x}",
                }

        # Method 3: Try to get context from the tracer provider directly
        from opentelemetry.trace import get_tracer_provider
        tracer_provider = get_tracer_provider()
        if hasattr(tracer_provider, 'get_tracer'):
            tracer = tracer_provider.get_tracer(__name__)
            # This is a last resort - check if there's an active span in the tracer
            span = trace.get_current_span()
            if span and span.get_span_context().is_valid:
                ctx = span.get_span_context()
                return {
                    "trace_id": f"{ctx.trace_id:032x}",
                    "span_id": f"{ctx.span_id:016x}",
                }

    except Exception as e:
        # Log the exception for debugging
        import logging
        logging.getLogger("otel_trace_context").debug(f"Failed to get OTel trace context: {e}")
        pass

    return {"trace_id": "", "span_id": ""}


class JsonFormatter(logging.Formatter):
    """
    Emits log records as single-line JSON.
    Compatible with CloudWatch Logs Insights — every field is queryable.
    Extra fields passed via `extra=` kwargs are merged into the JSON object.
    """

    # Fields that are standard LogRecord attributes — never copy these as extras
    _SKIP_FIELDS = frozenset({
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "id", "levelname", "levelno", "lineno", "module",
        "msecs", "message", "msg", "name", "pathname", "process",
        "processName", "relativeCreated", "stack_info", "thread", "threadName",
        "taskName", "trace_id", "span_id",  # Add these to prevent duplication
    })

    def format(self, record: logging.LogRecord) -> str:
        # Check if trace context was passed in the record's extra data first
        record_trace_id = getattr(record, 'trace_id', None)
        record_span_id = getattr(record, 'span_id', None)

        # If not in record, try to get from current OTel context
        if not record_trace_id or not record_span_id:
            otel_ctx = _get_otel_trace_context()
            trace_id = record_trace_id or otel_ctx["trace_id"]
            span_id = record_span_id or otel_ctx["span_id"]
        else:
            trace_id = record_trace_id
            span_id = record_span_id

        # If still empty, try to get from stored context var
        if not trace_id or not span_id:
            try:
                stored_ctx = otel_context_var.get()
                trace_id = trace_id or stored_ctx.get("trace_id", "")
                span_id = span_id or stored_ctx.get("span_id", "")
            except:
                pass

        log_obj = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": correlation_id_var.get("-"),
            # OTel trace context — links this log line to Application Signals trace
            "trace_id": trace_id,
            "span_id": span_id,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        # Merge any extra fields passed via extra={} kwarg
        for key, value in record.__dict__.items():
            if key not in self._SKIP_FIELDS and not key.startswith("_"):
                log_obj[key] = value
        return json.dumps(log_obj, default=str)


def configure_logging(level: str = "INFO") -> None:
    """
    Call ONCE at application startup (top of main.py) before anything else.
    Sets up JSON logging on the root logger — all loggers in the app inherit it.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Suppress noisy third-party access logs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Correlation-ID middleware — runs on every request.

    Priority order for correlation ID resolution:
      1. x-correlation-id  (explicit client / upstream header)
      2. x-amzn-trace-id   (injected by API Gateway on every request — always present)
      3. x-request-id      (legacy / ALB-injected)
      4. generated uuid4   (fallback for direct calls bypassing API Gateway)

    The resolved ID is:
      - Stored in `correlation_id_var` (ContextVar) for the request lifetime
      - Included in every JSON log line via JsonFormatter
      - Echoed back in the x-request-id response header
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Resolve correlation ID — prefer API Gateway injected header
        correlation_id = (
                request.headers.get(_CORRELATION_HEADER)
                or request.headers.get(_AMZN_TRACE_HEADER)  # API Gateway always sets this
                or request.headers.get(CORRELATION_ID_HEADER)
                or str(uuid.uuid4())
        )
        correlation_token = correlation_id_var.set(correlation_id)
        start = time.perf_counter()

        logger = logging.getLogger("request")

        # Initial log without trace context (FastAPI instrumentation hasn't run yet)
        logger.info(
            "request started",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
            },
        )

        # Process the request - FastAPI instrumentation will set up trace context
        response: Response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        # Now get trace context after request processing
        otel_ctx = _get_otel_trace_context()

        # Store trace context in ContextVar for use by other parts of the application
        otel_token = otel_context_var.set(otel_ctx)

        logger.info(
            "request completed",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "trace_id": otel_ctx["trace_id"],
                "span_id": otel_ctx["span_id"],
            },
        )

        # Echo correlation ID back to caller so they can log/trace it
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        response.headers["x-correlation-id"] = correlation_id

        # Reset context variables
        correlation_id_var.reset(correlation_token)
        try:
            otel_context_var.reset(otel_token)
        except:
            pass  # otel_token might not be set if context retrieval failed

        return response
