"""Strict v2 dependency; not wired into legacy routes until the coordinated cutover."""
from fastapi import Header, HTTPException

from .errors import AuthError
from .service import authenticate


def get_bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not isinstance(authorization, str):
        raise HTTPException(401, detail='authentication_required', headers={'WWW-Authenticate': 'Bearer'})
    parts = authorization.split(' ')
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1]:
        raise HTTPException(401, detail='invalid_session', headers={'WWW-Authenticate': 'Bearer'})
    return parts[1]


def get_authenticated_user_id(authorization: str | None = Header(default=None)) -> int:
    try:
        return authenticate(get_bearer_token(authorization)).account_id
    except AuthError as exc:
        raise HTTPException(exc.status_code, detail=exc.code, headers={'WWW-Authenticate': 'Bearer'}) from None
