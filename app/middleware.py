# Re-export from the canonical logging config so any existing imports keep working.
from app.logging_config import (
    RequestLoggingMiddleware,
    correlation_id_var,
    CORRELATION_ID_HEADER,
)  # noqa: F401

# Alias used by company_service.py client for header injection
_correlation_ctx = correlation_id_var