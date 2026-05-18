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
        "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": correlation_id_var.get("-"),
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
    - Reads x-request-id from incoming request (set by API Gateway / ALB).
    - Generates a new UUID if header is absent.
    - Stores it in correlation_id_var so ALL downstream log lines include it.
    - Logs request started + request completed with method, path, status, duration.
    - Echoes x-request-id back in the response header.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        correlation_id = request.headers.get("x-request-id") or str(uuid.uuid4())
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

        response.headers["x-request-id"] = correlation_id
        correlation_id_var.reset(token)
        return response
