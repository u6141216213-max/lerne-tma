"""HTTP boundary for the auth-v2 service.

The provider proof is verified here; the service receives only a
VerifiedIdentity. Tokens never appear in redirect URLs or HTML responses.
"""
import base64
import hashlib
import html
import os
from typing import Literal
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from api.auth.dependencies import get_bearer_token, get_authenticated_user_id
from api.auth.errors import AuthError
from api.auth import providers, service
from api.models import TMAUser

class AuthRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                response = await handler(request)
            except RequestValidationError:
                # Validation inputs may contain plaintext passwords or tokens.
                raise HTTPException(422, detail='invalid_auth_request') from None
            response.headers['Cache-Control'] = 'no-store'
            return response

        return safe_handler


router = APIRouter(prefix='/auth/v2', tags=['auth-v2'], route_class=AuthRoute)


class ChallengeRequest(BaseModel):
    provider: Literal['telegram', 'google']
    purpose: Literal['login', 'link'] = 'login'


class ChallengeExchangeRequest(BaseModel):
    secret: str = Field(min_length=40, max_length=128)


class TelegramMiniAppRequest(BaseModel):
    init_data: str = Field(min_length=1, max_length=16384)
    purpose: Literal['login', 'link'] = 'login'


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=40, max_length=128)


class LogoutRequest(BaseModel):
    all_sessions: bool = False


class EmailPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)


def _raise_auth(error: AuthError):
    raise HTTPException(error.status_code, detail=error.code,
                        headers={'WWW-Authenticate': 'Bearer'} if error.status_code == 401 else None) from None


def _tokens_response(tokens):
    return {
        'access_token': tokens.access_token,
        'refresh_token': tokens.refresh_token,
        'token_type': 'Bearer',
        'access_expires_at': tokens.access_expires_at.isoformat() + 'Z',
        'session_expires_at': tokens.session_expires_at.isoformat() + 'Z',
    }


def _google_configuration():
    client_id = os.getenv('GOOGLE_OAUTH_CLIENT_ID', '').strip()
    client_secret = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET', '').strip()
    redirect_uri = os.getenv('GOOGLE_OAUTH_REDIRECT_URI', '').strip()
    if not client_id or not client_secret or not redirect_uri.startswith('https://'):
        raise AuthError('provider_not_configured', 503)
    return client_id, client_secret, redirect_uri


@router.post('/challenges')
def create_auth_challenge(data: ChallengeRequest, authorization: str | None = Header(default=None)):
    try:
        token = get_bearer_token(authorization) if data.purpose == 'link' else None
        if data.provider == 'google':
            client_id, _, redirect_uri = _google_configuration()
        challenge = service.create_challenge(data.provider, data.purpose, access_token=token)
        response = {
            'challenge_id': challenge.id,
            'secret': challenge.secret,
            'expires_at': challenge.expires_at.isoformat() + 'Z',
        }
        if data.provider == 'telegram':
            bot_username = os.getenv('TELEGRAM_BOT_USERNAME', 'LerneDeutsch287_bot').lstrip('@')
            response['authorization_url'] = f'https://t.me/{bot_username}?start=auth_{challenge.state}'
        else:
            verifier_digest = hashlib.sha256(challenge.pkce_verifier.encode()).digest()
            code_challenge = base64.urlsafe_b64encode(verifier_digest).decode().rstrip('=')
            response['authorization_url'] = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'response_type': 'code',
                'scope': 'openid email profile',
                'state': challenge.state,
                'nonce': challenge.nonce,
                'code_challenge': code_challenge,
                'code_challenge_method': 'S256',
                'prompt': 'select_account',
            })
        return response
    except AuthError as exc:
        _raise_auth(exc)


@router.post('/telegram/mini-app')
def telegram_mini_app_login(data: TelegramMiniAppRequest, authorization: str | None = Header(default=None)):
    try:
        proof = providers.verify_telegram_init_data(data.init_data, os.getenv('BOT_TOKEN', ''))
        if data.purpose == 'link':
            service.link_verified(get_bearer_token(authorization), proof)
            return {'linked': True, 'providers': service.linked_providers(get_bearer_token(authorization))}
        return _tokens_response(service.login_verified(proof))
    except AuthError as exc:
        _raise_auth(exc)


@router.post('/email-password/register')
def register_email_password(data: EmailPasswordRequest):
    try:
        return _tokens_response(service.register_email_password(data.email, data.password))
    except AuthError as exc:
        _raise_auth(exc)


@router.post('/email-password/login')
def login_email_password(data: EmailPasswordRequest):
    try:
        return _tokens_response(service.login_email_password(data.email, data.password))
    except AuthError as exc:
        _raise_auth(exc)


@router.post('/email-password/link', status_code=204)
def link_email_password(data: EmailPasswordRequest, authorization: str = Depends(get_bearer_token)):
    try:
        service.link_email_password(authorization, data.email, data.password)
    except AuthError as exc:
        _raise_auth(exc)


@router.post('/email-password/set', status_code=204)
def set_email_password(data: EmailPasswordRequest, authorization: str = Depends(get_bearer_token)):
    try:
        service.set_email_password(authorization, data.email, data.password)
    except AuthError as exc:
        _raise_auth(exc)


@router.get('/google/callback', response_class=HTMLResponse, include_in_schema=False)
def google_callback(
    code: str | None = Query(default=None, max_length=4096),
    state: str | None = Query(default=None, max_length=128),
    error: str | None = Query(default=None, max_length=128),
):
    message = 'Вход не был завершён. Вернитесь в Lerne и попробуйте ещё раз.'
    status = 400
    try:
        if error or not code or not state:
            raise AuthError('provider_cancelled', 400)
        # We must obtain the server-stored nonce/verifier after locating the
        # challenge by state. The state itself is high entropy and is never
        # accepted as a completed login credential.
        from api.auth.service import get_pending_challenge
        challenge = get_pending_challenge(state, provider='google')
        client_id, client_secret, redirect_uri = _google_configuration()
        exchange = requests.post('https://oauth2.googleapis.com/token', data={
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
            'code_verifier': challenge.pkce_verifier,
        }, timeout=10)
        if exchange.status_code != 200:
            raise AuthError('invalid_provider_proof')
        encoded_token = exchange.json().get('id_token')
        proof = providers.verify_google_id_token(encoded_token, client_id=client_id,
                                                 expected_nonce=challenge.nonce)
        service.confirm_challenge(state, proof)
        message = 'Google подтвердил вход. Вернитесь в Lerne — вход будет завершён автоматически.'
        status = 200
    except (AuthError, ValueError, requests.RequestException):
        pass
    return HTMLResponse(
        '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Lerne</title><body style="font-family:system-ui;background:#101827;color:#fff;display:grid;min-height:100vh;place-items:center;margin:0">'
        f'<main style="max-width:360px;padding:24px;text-align:center;line-height:1.5">{html.escape(message)}</main></body></html>',
        status_code=status,
        headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'"},
    )


@router.post('/challenges/{challenge_id}/exchange')
def exchange_auth_challenge(challenge_id: str, data: ChallengeExchangeRequest,
                            authorization: str | None = Header(default=None)):
    try:
        # Supplying a bearer token is required only for a link challenge; the
        # service rejects a token from a different initiating session.
        token = get_bearer_token(authorization) if authorization else None
        result = service.exchange_challenge(challenge_id, data.secret, access_token=token)
        if result['linked']:
            return result
        return _tokens_response(result['tokens'])
    except AuthError as exc:
        _raise_auth(exc)


@router.post('/refresh')
def refresh_auth_session(data: RefreshRequest):
    try:
        return _tokens_response(service.refresh_session(data.refresh_token))
    except AuthError as exc:
        _raise_auth(exc)


@router.post('/logout', status_code=204)
def logout_auth_session(data: LogoutRequest, authorization: str = Depends(get_bearer_token)):
    try:
        service.revoke_session(authorization, all_sessions=data.all_sessions)
    except AuthError as exc:
        _raise_auth(exc)


@router.get('/identities')
def get_auth_identities(authorization: str = Depends(get_bearer_token)):
    try:
        return {'providers': service.linked_providers(authorization)}
    except AuthError as exc:
        _raise_auth(exc)


@router.get('/me')
def get_authenticated_profile(user_id: int = Depends(get_authenticated_user_id)):
    user = TMAUser.get_or_none(TMAUser.user_id == user_id)
    if user is None:
        raise HTTPException(401, detail='invalid_session', headers={'WWW-Authenticate': 'Bearer'})
    return {
        'user_id': user.user_id,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'username': user.username,
        'photo_url': user.photo_url,
        'active_language': user.active_language or 'de',
        'native_language': user.native_language or 'uk',
        'has_selected_language': bool(user.has_selected_language),
        'is_guest': False,
    }


@router.get('/password-settings')
def get_password_settings(authorization: str = Depends(get_bearer_token)):
    try:
        return service.password_settings(authorization)
    except AuthError as exc:
        _raise_auth(exc)
