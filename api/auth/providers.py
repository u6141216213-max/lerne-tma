"""Trusted provider boundary. Never deserialize VerifiedIdentity from HTTP JSON."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import re
from urllib.parse import parse_qsl

from api.models import auth_utcnow
from .errors import AuthError

PROOF_TTL = timedelta(minutes=5)
CLOCK_SKEW = timedelta(seconds=30)


@dataclass(frozen=True)
class VerifiedIdentity:
    # Internal capability produced only AFTER provider cryptographic verification.
    # Google verification below checks signature/iss/aud/azp/exp/nonce first.
    provider: str
    subject: str = field(repr=False)
    authenticated_at: datetime
    expires_at: datetime
    proof_hash: str = field(repr=False)


def verify_telegram_init_data(init_data: str, bot_token: str) -> VerifiedIdentity:
    """Validate raw Mini App initData, not initDataUnsafe or Telegram Login data."""
    if not bot_token:
        raise AuthError('provider_not_configured', 503)
    if not isinstance(init_data, str) or not 1 <= len(init_data) <= 16384:
        raise AuthError('invalid_provider_proof')
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=32, errors='strict')
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError('Duplicate field')
        supplied_hash = data.pop('hash')
        if not re.fullmatch(r'[0-9a-f]{64}', supplied_hash):
            raise ValueError('Invalid hash')
        check_string = '\n'.join(f'{key}={value}' for key, value in sorted(data.items()))
        secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied_hash):
            raise ValueError('Invalid signature')
        if not re.fullmatch(r'[0-9]{1,12}', data['auth_date']):
            raise ValueError('Invalid auth date')
        # UTC naive timestamps match the PostgreSQL TIMESTAMP columns.
        issued = datetime(1970, 1, 1) + timedelta(seconds=int(data['auth_date']))
        now = auth_utcnow()
        if issued > now + CLOCK_SKEW or issued + PROOF_TTL <= now:
            raise ValueError('Expired proof')
        user = json.loads(data['user'])
        user_id = user['id']
        if type(user_id) is not int or not 0 < user_id < 2**52:
            raise ValueError('Invalid Telegram subject')
        fingerprint = hashlib.sha256(
            ('telegram\n' + check_string + '\n' + supplied_hash).encode()).hexdigest()
        return VerifiedIdentity('telegram', str(user_id), issued, issued + PROOF_TTL, fingerprint)
    except (KeyError, ValueError, TypeError, OverflowError, UnicodeError):
        # Never include initData, profile contents or the bot token in an error.
        raise AuthError('invalid_provider_proof') from None


def verify_google_id_token(encoded_token: str, *, client_id: str,
                           expected_nonce: str, authorized_presenters: tuple[str, ...] = ()) -> VerifiedIdentity:
    """Verify with Google's library; expected_nonce MUST come from server state.

    The future HTTP adapter must bind and atomically consume the login/link
    challenge for this nonce and originating browser/device session. Neither
    client_id nor expected_nonce may be accepted as trusted request parameters.
    """
    if not client_id or not expected_nonce or len(expected_nonce) < 32:
        raise AuthError('provider_not_configured', 503)
    if not isinstance(encoded_token, str) or not 1 <= len(encoded_token) <= 16384:
        raise AuthError('invalid_provider_proof')
    # Lazy imports keep migrations independent from the provider/network runtime.
    from google.auth.exceptions import GoogleAuthError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token
    import requests

    class BoundedRequest(Request):
        def __call__(self, *args, **kwargs):
            kwargs['timeout'] = 10
            return super().__call__(*args, **kwargs)

    try:
        with requests.Session() as http:
            claims = id_token.verify_oauth2_token(
                encoded_token, BoundedRequest(session=http), audience=client_id,
                clock_skew_in_seconds=int(CLOCK_SKEW.total_seconds()),
            )
        if claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
            raise ValueError('Invalid issuer')
        if claims.get('aud') != client_id:
            raise ValueError('Invalid audience')
        if 'azp' in claims and claims['azp'] not in (client_id, *authorized_presenters):
            raise ValueError('Invalid authorized presenter')
        nonce = claims.get('nonce')
        if not isinstance(nonce, str) or not hmac.compare_digest(nonce.encode(), expected_nonce.encode()):
            raise ValueError('Invalid nonce')
        subject = claims['sub']
        if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            raise ValueError('Invalid subject')
        if type(claims['iat']) is not int or type(claims['exp']) is not int:
            raise ValueError('Invalid timestamps')
        issued = datetime(1970, 1, 1) + timedelta(seconds=claims['iat'])
        expires = datetime(1970, 1, 1) + timedelta(seconds=claims['exp'])
        expires = min(expires, issued + PROOF_TTL)
        now = auth_utcnow()
        if issued > now + CLOCK_SKEW or expires <= now:
            raise ValueError('Expired proof')
        fingerprint = hashlib.sha256(('google\n' + encoded_token).encode()).hexdigest()
        return VerifiedIdentity('google', subject, issued, expires, fingerprint)
    except TransportError:
        raise AuthError('provider_unavailable', 503) from None
    except (GoogleAuthError, ValueError, KeyError, TypeError, OverflowError, UnicodeError):
        raise AuthError('invalid_provider_proof') from None
