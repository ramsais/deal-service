import base64
import json
import logging

from fastapi import Request

from app.exceptions import AppException
from app.services.config import settings

logger = logging.getLogger("deal_service.auth")


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
    logger.debug(
        "auth headers received",
        extra={
            "has_x_amzn_oidc_data": bool(request.headers.get("x-amzn-oidc-data")),
            "has_x_cognito_claims": bool(request.headers.get("x-cognito-claims")),
            "has_authorization": bool(request.headers.get("authorization")),
            "has_x_internal_api_key": bool(request.headers.get("x-internal-api-key")),
            "path": request.url.path,
            "method": request.method,
        },
    )

    # Pattern 1: API GW / ALB forwards encoded JWT payload
    oidc_data = request.headers.get("x-amzn-oidc-data")
    if oidc_data:
        try:
            # JWT is header.payload.signature — we only need the payload
            payload_b64 = oidc_data.split(".")[1]
            # Add padding if needed
            payload_b64 += "=" * (-len(payload_b64) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload_b64))
            logger.info(
                "claims extracted from x-amzn-oidc-data",
                extra={
                    "source": "x-amzn-oidc-data",
                    "sub": claims.get("sub"),
                    "cognito_groups": claims.get("cognito:groups"),
                    "email": claims.get("email"),
                    "username": claims.get("cognito:username"),
                },
            )
            return claims
        except Exception:
            logger.warning("failed to decode x-amzn-oidc-data token claims", extra={"path": request.url.path})
            raise AuthenticationException(message="Failed to decode OIDC token claims")

    # Pattern 2: API GW mapping template injects claims as JSON header
    cognito_claims = request.headers.get("x-cognito-claims")
    if cognito_claims:
        try:
            claims = json.loads(cognito_claims)
            logger.info(
                "claims extracted from x-cognito-claims",
                extra={
                    "source": "x-cognito-claims",
                    "sub": claims.get("sub"),
                    "cognito_groups": claims.get("cognito:groups"),
                    "email": claims.get("email"),
                    "username": claims.get("cognito:username"),
                },
            )
            return claims
        except Exception:
            logger.warning("failed to parse x-cognito-claims header", extra={"path": request.url.path})
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
                claims = json.loads(base64.urlsafe_b64decode(payload_b64))
                logger.info(
                    "claims extracted from Bearer token",
                    extra={
                        "source": "authorization_bearer",
                        "sub": claims.get("sub"),
                        "cognito_groups": claims.get("cognito:groups"),
                        "email": claims.get("email"),
                        "username": claims.get("cognito:username"),
                    },
                )
                return claims
        except Exception:
            logger.warning("failed to decode Bearer token claims", extra={"path": request.url.path})
            raise AuthenticationException(message="Failed to decode Bearer token claims")

    logger.warning(
        "no authentication credentials found",
        extra={"path": request.url.path, "method": request.method},
    )
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
    if settings.LOCAL_DEV:
        logger.info(
            "auth bypassed (LOCAL_DEV=True)",
            extra={
                "required_role": "WRITE_USER",
                "path": request.url.path,
                "method": request.method,
            },
        )
        return "WRITE_USER"

    claims = _extract_claims(request)
    role = _get_role(claims)
    if role != "WRITE_USER":
        raise AuthorizationException(message="This action requires the 'WRITE_USER' role")
    logger.info(
        "request authorised",
        extra={
            "required_role": "WRITE_USER",
            "resolved_role": role,
            "sub": claims.get("sub"),
            "path": request.url.path,
            "method": request.method,
        },
    )
    return role


def require_user(request: Request) -> str:
    """Dependency — allows users in the 'READ_USER' OR 'WRITE_USER' group."""
    if settings.LOCAL_DEV:
        logger.info(
            "auth bypassed (LOCAL_DEV=True)",
            extra={
                "required_role": "READ_USER|WRITE_USER",
                "path": request.url.path,
                "method": request.method,
            },
        )
        return "ok"

    claims = _extract_claims(request)
    role = _get_role(claims)
    logger.info(
        "request authorised",
        extra={
            "required_role": "READ_USER|WRITE_USER",
            "resolved_role": role,
            "sub": claims.get("sub"),
            "path": request.url.path,
            "method": request.method,
        },
    )
    return "ok"
