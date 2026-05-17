import json
import logging
import sys
import time
import uuid
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON — compatible with CloudWatch Logs Insights."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        # Merge any extra fields passed via `extra=` kwarg
        for key, value in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "id", "levelname", "levelno", "lineno", "module",
                "msecs", "message", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread", "threadName",
            ):
                log_obj[key] = value
        return json.dumps(log_obj, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Call once at application startup to set up JSON logging on the root logger."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Quieten noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


logger = logging.getLogger("deal_service")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request/response with a correlation ID (x-request-id)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        start = time.perf_counter()

        logger.info(
            "request started",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )

        response: Response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        response.headers["x-request-id"] = request_id
        return response
