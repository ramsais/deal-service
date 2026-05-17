import json
import base64
from fastapi import Depends, Request
from app.exceptions import AppException


class AuthorizationException(AppException):
    def __init__(self, message: str = "Forbidden: insufficient permissions", details: dict | None = None):
        super().__init__(status_code=403, error="FORBIDDEN", message=message, details=details)


class AuthenticationException(AppException):
    def __init__(self, message: str = "Unauthorized: missing or invalid token claims", details: dict | None = None):
        super().__init__(status_code=401, error="UNAUTHORIZED", message=message, details=details)


def _extract_claims(request: Request) -> dict:
    """
    API Gateway (with Cognito authorizer) forwards the JWT claims as a base64-encoded
    JSON string in the 'x-amzn-oidc-data' header, or as individual claim headers.
    We support both patterns:
      1. x-amzn-oidc-data  — full JWT payload forwarded by ALB/API GW
      2. x-cognito-claims  — custom header set by API GW mapping template (JSON string)
    Falls back to decoding the Authorization Bearer token payload directly (for local dev).
    """
    # Pattern 1: API GW / ALB forwards encoded JWT payload
    oidc_data = request.headers.get("x-amzn-oidc-data")
    if oidc_data:
        try:
            # JWT is header.payload.signature — we only need the payload
            payload_b64 = oidc_data.split(".")[1]
            # Add padding if needed
            payload_b64 += "=" * (-len(payload_b64) % 4)
            return json.loads(base64.urlsafe_b64decode(payload_b64))
        except Exception:
            raise AuthenticationException(message="Failed to decode OIDC token claims")

    # Pattern 2: API GW mapping template injects claims as JSON header
    cognito_claims = request.headers.get("x-cognito-claims")
    if cognito_claims:
        try:
            return json.loads(cognito_claims)
        except Exception:
            raise AuthenticationException(message="Failed to parse Cognito claims header")

    # Fallback: decode Bearer token payload (no signature verification — API GW already did it)
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        try:
            parts = token.split(".")
            if len(parts) == 3:
                payload_b64 = parts[1]
                payload_b64 += "=" * (-len(payload_b64) % 4)
                return json.loads(base64.urlsafe_b64decode(payload_b64))
        except Exception:
            raise AuthenticationException(message="Failed to decode Bearer token claims")

    raise AuthenticationException(message="No authentication credentials found")


def _get_role(claims: dict) -> str:
    """
    Extract the user role from Cognito claims.
    Cognito groups are available under 'cognito:groups' as a list.
    We recognise 'WRITE_USER' and 'READ_USER'; WRITE_USER takes precedence.
    """
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):
        # Some serialisations send it as a comma-separated string
        groups = [g.strip() for g in groups.split(",")]

    if "WRITE_USER" in groups:
        return "WRITE_USER"
    if "READ_USER" in groups:
        return "READ_USER"

    raise AuthorizationException(message="User does not belong to a recognised role (WRITE_USER or READ_USER)")


# ---------------------------------------------------------------------------
# Reusable FastAPI dependencies
# ---------------------------------------------------------------------------

def require_admin(request: Request) -> str:
    """Dependency — allows only users in the 'WRITE_USER' group."""
    claims = _extract_claims(request)
    role = _get_role(claims)
    if role != "WRITE_USER":
        raise AuthorizationException(message="This action requires the 'WRITE_USER' role")
    return role


def require_user(request: Request) -> str:
    """Dependency — allows users in the 'READ_USER' OR 'WRITE_USER' group."""
    claims = _extract_claims(request)
    _get_role(claims)  # validates that the user belongs to at least one known role
    return "ok"
