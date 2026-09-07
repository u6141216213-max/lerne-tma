import logging
from fastapi import Header, HTTPException
from api.auth.dependencies import get_bearer_token
from api.auth.errors import AuthError
from api.auth.service import authenticate

logger = logging.getLogger(__name__)

def get_user_id(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None)
) -> int:
    """Dependency to extract and validate user ID.
    Supports both Auth v2 Bearer tokens and legacy/TMA X-User-ID header.
    """
    # 1. Try Bearer token first if provided
    if isinstance(authorization, str) and authorization.strip().lower().startswith("bearer "):
        try:
            token = get_bearer_token(authorization)
            principal = authenticate(token)
            return principal.account_id
        except (AuthError, HTTPException) as exc:
            if not isinstance(x_user_id, (str, int)) or not x_user_id:
                status_code = getattr(exc, 'status_code', 401)
                detail = getattr(exc, 'detail', getattr(exc, 'code', 'invalid_session'))
                raise HTTPException(status_code=status_code, detail=detail, headers={'WWW-Authenticate': 'Bearer'}) from None
            logger.debug(f"Bearer auth failed ({exc}), falling back to X-User-ID: {x_user_id}")
        except Exception as exc:
            logger.warning(f"Unexpected error during Bearer authentication: {exc}")
            if not isinstance(x_user_id, (str, int)) or not x_user_id:
                raise HTTPException(status_code=401, detail="authentication_failed", headers={'WWW-Authenticate': 'Bearer'}) from None

    # 2. Fallback to X-User-ID (used by Telegram Mini App, local dev, and existing sessions)
    if isinstance(x_user_id, (str, int)) and str(x_user_id).strip():
        try:
            return int(x_user_id)
        except ValueError:
            logger.error(f"Invalid X-User-ID format: {x_user_id}")
            raise HTTPException(status_code=400, detail=f"Invalid X-User-ID format: {x_user_id}")

    raise HTTPException(status_code=401, detail="authentication_required", headers={'WWW-Authenticate': 'Bearer'})

