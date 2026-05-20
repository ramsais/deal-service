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

# Header names — API Gateway injects X-Amzn-Trace-Id; clients may send X-Correlation-ID
CORRELATION_ID_HEADER = "x-request-id"
_AMZN_TRACE_HEADER = "x-amzn-trace-id"
_CORRELATION_HEADER = "x-correlation-id"


def _get_otel_trace_context() -> dict:
    """
    Extract the active OTel trace_id and span_id from the current span context.
    Returns empty strings when no active span exists (e.g. during startup).
    These values are injected into every JSON log line so CloudWatch Logs
    Insights can correlate log entries with X-Ray / Application Signals traces.
    """
    try:
        from opentelemetry import trace as otel_trace
        span = otel_trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return {
                "trace_id": format(ctx.trace_id, "032x"),
                "span_id": format(ctx.span_id, "016x"),
            }
    except Exception:
        pass
    return {"trace_id": "", "span_id": ""}


class JsonFormatter(logging.Formatter):
    """
    Emits log records as single-line JSON.
    Compatible with CloudWatch Logs Insights — every field is queryable.
    Includes OTel trace_id and span_id so logs are linkable to
    CloudWatch Application Signals / X-Ray traces.
    Extra fields passed via `extra=` kwargs are merged into the JSON object.
    """

    # Fields that are standard LogRecord attributes — never copy these as extras
    _SKIP_FIELDS = frozenset({
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "id", "levelname", "levelno", "lineno", "module",
        "msecs", "message", "msg", "name", "pathname", "process",
        "processName", "relativeCreated", "stack_info", "thread", "threadName",
        "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        otel_ctx = _get_otel_trace_context()
        log_obj = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": correlation_id_var.get("-"),
            # OTel trace context — links this log line to Application Signals trace
            "trace_id": otel_ctx["trace_id"],
            "span_id": otel_ctx["span_id"],
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
        token = correlation_id_var.set(correlation_id)
        start = time.perf_counter()

        logger = logging.getLogger("request")
        logger.info(
            "request started",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
            },
        )

        response: Response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        logger.info(
            "request completed",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        # Echo correlation ID back to caller so they can log/trace it
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        response.headers["x-correlation-id"] = correlation_id
        correlation_id_var.reset(token)
        return response