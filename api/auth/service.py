"""Provider-independent accounts, linking and revocable opaque sessions.

Public operations own their transaction and connection lifetime. Call them from
synchronous FastAPI routes (or offload the WHOLE operation to one worker thread).
No legacy X-User-ID, profile, auth code or pending guest session grants access here.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import re
import secrets
import uuid

from peewee import IntegrityError

from api.models import (
    TMAUser, TMAAuthAccount, TMAAuthIdentity, TMAAuthSession, TMAAuthToken,
    TMAAuthProof, TMAAuthChallenge, TMAAuthPassword, TMAAuthPasswordThrottle,
    tma_db, auth_utcnow as utcnow,
)
from .errors import AuthError
from .providers import VerifiedIdentity, PROOF_TTL, CLOCK_SKEW
from .transactions import auth_transaction, locked

ACCESS_TTL = timedelta(minutes=15)
SESSION_TTL = timedelta(days=30)
REAUTH_TTL = timedelta(minutes=5)
CHALLENGE_TTL = timedelta(minutes=5)
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
PASSWORD_WINDOW = timedelta(minutes=15)
PASSWORD_LOCKOUT = timedelta(minutes=15)
PASSWORD_MAX_FAILURES = 5


def _password_hasher():
    # Import lazily so explicit schema inspection remains independent of the
    # crypto runtime. Argon2id is deliberately never reimplemented here.
    from argon2 import PasswordHasher
    return PasswordHasher()


def _canonical_email(email: str) -> str:
    if not isinstance(email, str):
        raise AuthError('invalid_email', 400)
    value = email.strip().lower()
    if (not 3 <= len(value) <= 320 or not re.fullmatch(r"[^\s@]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value)
            or '..' in value):
        raise AuthError('invalid_email', 400)
    return value


def _validate_password(password: str):
    if not isinstance(password, str) or not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise AuthError('invalid_password', 400)


@dataclass(frozen=True)
class Principal:
    account_id: int
    session_id: str
    authenticated_at: datetime


@dataclass(frozen=True)
class SessionTokens:
    account_id: int
    session_id: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires_at: datetime
    session_expires_at: datetime


@dataclass(frozen=True)
class AuthChallenge:
    id: str
    secret: str = field(repr=False)
    state: str = field(repr=False)
    nonce: str | None = field(repr=False)
    pkce_verifier: str | None = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class PendingChallenge:
    """Server-only fields needed to finish a provider redirect."""
    nonce: str | None = field(repr=False)
    pkce_verifier: str | None = field(repr=False)


def _consume_proof(proof: VerifiedIdentity, now: datetime):
    if not isinstance(proof, VerifiedIdentity) or proof.provider not in ('telegram', 'google'):
        raise AuthError('invalid_provider_proof')
    if (not isinstance(proof.subject, str) or not 1 <= len(proof.subject) <= 255
            or not re.fullmatch(r'[0-9a-f]{64}', proof.proof_hash)
            or proof.authenticated_at > now + CLOCK_SKEW
            or proof.authenticated_at + PROOF_TTL <= now
            or proof.expires_at <= now
            or proof.expires_at > proof.authenticated_at + PROOF_TTL):
        raise AuthError('invalid_provider_proof')
    if proof.provider == 'telegram' and (
        not re.fullmatch(r'[1-9][0-9]{0,15}', proof.subject) or int(proof.subject) >= 2**52
    ):
        raise AuthError('invalid_provider_proof')
    try:
        with tma_db.atomic():
            TMAAuthProof.create(proof_hash=proof.proof_hash, expires_at=proof.expires_at)
    except IntegrityError:
        raise AuthError('provider_proof_replayed') from None


def _account(account_id: int):
    account = locked(TMAAuthAccount.select().where(TMAAuthAccount.user == account_id)).get_or_none()
    if account is None or account.disabled_at is not None:
        raise AuthError('invalid_session')
    return account


def _identity_owner(proof: VerifiedIdentity):
    identity = TMAAuthIdentity.get_or_none(
        (TMAAuthIdentity.provider == proof.provider) & (TMAAuthIdentity.subject == proof.subject))
    if identity:
        return identity.account_id
    if proof.provider == 'telegram':
        legacy = TMAAuthAccount.get_or_none(TMAAuthAccount.legacy_telegram_subject == proof.subject)
        if legacy:
            return legacy.user_id
        # Historical guests used random positive IDs in Telegram's namespace.
        # A real Telegram credential with that number does not prove guest-data
        # ownership. Preserve the rows and stop, rather than claim or replace them.
        if proof.subject.isascii() and proof.subject.isdigit() and len(proof.subject) <= 16:
            ambiguous = TMAAuthAccount.get_or_none(TMAAuthAccount.user == int(proof.subject))
            if ambiguous:
                raise AuthError('legacy_account_review_required', 409)
    return None


def _new_account():
    # Negative JS-safe user IDs cannot collide with Telegram's positive IDs.
    # Negative card/deck IDs in Dexie are a different identifier namespace.
    for _ in range(8):
        user_id = -(10**12 + secrets.randbelow(2**52 - 10**12))
        try:
            with tma_db.atomic():
                user = TMAUser.create(user_id=user_id, is_guest=False)
                return TMAAuthAccount.create(user=user)
        except IntegrityError:
            continue
    raise AuthError('account_creation_retry', 503)


def _attach(account, proof):
    existing = TMAAuthIdentity.get_or_none(
        (TMAAuthIdentity.account == account.user_id) & (TMAAuthIdentity.provider == proof.provider))
    if existing:
        if existing.subject != proof.subject:
            raise AuthError('provider_already_linked', 409)
        return
    try:
        with tma_db.atomic():
            TMAAuthIdentity.create(account=account.user_id, provider=proof.provider, subject=proof.subject)
    except IntegrityError:
        # DB uniqueness arbitrates concurrent first login/link across workers.
        # The outer transaction rolls back a losing account/session/proof as well.
        raise AuthError('identity_conflict', 409) from None
    if proof.provider == 'telegram':
        account.legacy_telegram_subject = None
        account.save(only=[TMAAuthAccount.legacy_telegram_subject])
    TMAUser.update(is_guest=False).where(TMAUser.user_id == account.user_id).execute()


def _mint_tokens(session, now):
    values = {}
    access_expiry = min(now + ACCESS_TTL, session.expires_at)
    for kind, prefix, expiry in (
        ('access', 'la_', access_expiry), ('refresh', 'lr_', session.expires_at),
    ):
        token = prefix + secrets.token_urlsafe(32)
        TMAAuthToken.create(
            token_hash=hashlib.sha256(token.encode()).hexdigest(), session=session.id,
            kind=kind, created_at=now, expires_at=expiry,
        )
        values[kind] = token
    return SessionTokens(session.account_id, session.id, values['access'], values['refresh'],
                         access_expiry, session.expires_at)


def _create_session(account, now: datetime, authenticated_at: datetime | None = None,
                    *, authentication_method: str = 'email_password'):
    session = TMAAuthSession.create(
        id=str(uuid.uuid4()), account=account.user_id,
        authenticated_at=authenticated_at or now, created_at=now, expires_at=now + SESSION_TTL,
        authentication_method=authentication_method,
    )
    return _mint_tokens(session, now)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _new_challenge_value(prefix: str = '') -> str:
    return prefix + secrets.token_urlsafe(32)


def _issue_verified(proof: VerifiedIdentity, now: datetime, *, link_account_id: int | None = None):
    if not isinstance(proof, VerifiedIdentity):
        raise AuthError('invalid_provider_proof')
    owner = _identity_owner(proof)
    if link_account_id is not None:
        account = _account(link_account_id)
        if owner is not None and owner != account.user_id:
            raise AuthError('identity_conflict', 409)
        _consume_proof(proof, now)
        _attach(account, proof)
        return None
    account = _account(owner) if owner is not None else _new_account()
    _consume_proof(proof, now)
    _attach(account, proof)
    return _create_session(account, now, proof.authenticated_at, authentication_method=proof.provider)


def _find_token(token: str, kind: str):
    prefix = 'la_' if kind == 'access' else 'lr_'
    if not isinstance(token, str) or not re.fullmatch(prefix + r'[A-Za-z0-9_-]{43}', token):
        raise AuthError('invalid_session')
    record = TMAAuthToken.get_or_none(
        (TMAAuthToken.token_hash == hashlib.sha256(token.encode()).hexdigest())
        & (TMAAuthToken.kind == kind))
    if record is None:
        raise AuthError('invalid_session')
    return record


def _session(record, now):
    candidate = TMAAuthSession.get_or_none(TMAAuthSession.id == record.session_id)
    if candidate is None:
        raise AuthError('invalid_session')
    # Consistent lock order: account -> session. Serializes disable, linking and
    # revoke-all against refresh/login for the same account on PostgreSQL.
    _account(candidate.account_id)
    session = locked(TMAAuthSession.select().where(TMAAuthSession.id == candidate.id)).get_or_none()
    if session is None or session.revoked_at or session.expires_at <= now or record.expires_at <= now:
        raise AuthError('invalid_session')
    return session


def _authenticated(access_token, now):
    record = _find_token(access_token, 'access')
    session = _session(record, now)
    if record.consumed_at is not None:
        raise AuthError('invalid_session')
    return session


def login_verified(proof: VerifiedIdentity) -> SessionTokens:
    """Only a trusted, verified provider adapter may call this entry point."""
    with auth_transaction():
        now = utcnow()
        return _issue_verified(proof, now)


def authenticate(access_token: str) -> Principal:
    with auth_transaction():
        session = _authenticated(access_token, utcnow())
        return Principal(session.account_id, session.id, session.authenticated_at)


def refresh_session(refresh_token: str) -> SessionTokens:
    replayed = False
    result = None
    with auth_transaction():
        now = utcnow()
        record = _find_token(refresh_token, 'refresh')
        session = _session(record, now)
        # Re-read after acquiring session lock: another worker may have rotated.
        record = TMAAuthToken.get_by_id(record.token_hash)
        if record.consumed_at is not None:
            TMAAuthSession.update(revoked_at=now).where(TMAAuthSession.id == session.id).execute()
            replayed = True
        else:
            record.consumed_at = now
            record.save(only=[TMAAuthToken.consumed_at])
            result = _mint_tokens(session, now)
    # Revocation must COMMIT before returning an error to the HTTP caller.
    if replayed:
        raise AuthError('refresh_reuse_detected')
    return result


def revoke_session(access_token: str, *, all_sessions: bool = False):
    with auth_transaction():
        now = utcnow()
        session = _authenticated(access_token, now)
        condition = (TMAAuthSession.account == session.account_id) if all_sessions else (
            TMAAuthSession.id == session.id)
        TMAAuthSession.update(revoked_at=now).where(condition & TMAAuthSession.revoked_at.is_null()).execute()


def link_verified(access_token: str, proof: VerifiedIdentity):
    """Link a NEW provider, never move an identity or merge another account."""
    with auth_transaction():
        now = utcnow()
        session = _authenticated(access_token, now)
        if session.authenticated_at + REAUTH_TTL <= now:
            raise AuthError('reauthentication_required')
        _issue_verified(proof, now, link_account_id=session.account_id)


def linked_providers(access_token: str) -> list[str]:
    with auth_transaction():
        session = _authenticated(access_token, utcnow())
        providers = [row.provider for row in TMAAuthIdentity.select(TMAAuthIdentity.provider)
                     .where(TMAAuthIdentity.account == session.account_id).order_by(TMAAuthIdentity.provider)]
        if TMAAuthPassword.get_or_none(TMAAuthPassword.account == session.account_id):
            providers.append('email_password')
        return providers


def password_settings(access_token: str) -> dict:
    with auth_transaction():
        now = utcnow()
        session = _authenticated(access_token, now)
        credential = TMAAuthPassword.get_or_none(TMAAuthPassword.account == session.account_id)
        return {'email': credential.email if credential else None,
                'can_reset_password': session.authentication_method in ('telegram', 'google')
                and session.authenticated_at + REAUTH_TTL > now}


def _password_throttle(email_hash: str, now: datetime):
    row = locked(TMAAuthPasswordThrottle.select().where(
        TMAAuthPasswordThrottle.email_hash == email_hash)).get_or_none()
    if row and row.locked_until and row.locked_until > now:
        raise AuthError('too_many_attempts', 429)
    if row and row.window_started_at + PASSWORD_WINDOW <= now:
        row.failures, row.window_started_at, row.locked_until, row.updated_at = 0, now, None, now
        row.save()
    return row


def _password_failed(email_hash: str, row, now: datetime):
    if row is None:
        try:
            with TMAAuthPasswordThrottle._meta.database.atomic():
                row = TMAAuthPasswordThrottle.create(email_hash=email_hash, failures=0, window_started_at=now,
                                                      updated_at=now)
        except IntegrityError:
            row = locked(TMAAuthPasswordThrottle.select().where(
                TMAAuthPasswordThrottle.email_hash == email_hash)).get()
    row.failures += 1
    row.updated_at = now
    if row.failures >= PASSWORD_MAX_FAILURES:
        row.locked_until = now + PASSWORD_LOCKOUT
    row.save()


def register_email_password(email: str, password: str) -> SessionTokens:
    """Create a standalone account without inferring identity from a profile."""
    email = _canonical_email(email)
    _validate_password(password)
    hashed = _password_hasher().hash(password)
    with auth_transaction():
        if TMAAuthPassword.get_or_none(TMAAuthPassword.email == email):
            raise AuthError('email_unavailable', 409)
        now = utcnow()
        account = _new_account()
        try:
            TMAAuthPassword.create(account=account.user_id, email=email, password_hash=hashed,
                                   created_at=now, changed_at=now)
        except IntegrityError:
            raise AuthError('email_unavailable', 409) from None
        return _create_session(account, now)


def login_email_password(email: str, password: str) -> SessionTokens:
    email = _canonical_email(email)
    _validate_password(password)
    email_hash = _hash('email-login\n' + email)
    result = None
    invalid = False
    with auth_transaction():
        now = utcnow()
        credential = TMAAuthPassword.get_or_none(TMAAuthPassword.email == email)
        if credential:
            # Same account-first lock order as password reset. Read the hash
            # again after waiting: an old password must not mint a new session.
            account = _account(credential.account_id)
            credential = TMAAuthPassword.get_by_id(credential.account_id)
        throttle = _password_throttle(email_hash, now)
        hasher = _password_hasher()
        try:
            # Keep an unknown email close to the verified-password timing path.
            candidate = credential.password_hash if credential else hasher.hash('not-used')
            valid = hasher.verify(candidate, password)
        except Exception:
            valid = False
        if not credential or not valid:
            _password_failed(email_hash, throttle, now)
            invalid = True
        else:
            if hasher.check_needs_rehash(credential.password_hash):
                credential.password_hash, credential.changed_at = hasher.hash(password), now
                credential.save(only=[TMAAuthPassword.password_hash, TMAAuthPassword.changed_at])
            if throttle:
                throttle.delete_instance()
            result = _create_session(account, now)
    # Raising within the transaction would roll back the durable failed-attempt
    # counter. Return the same public error only after the counter committed.
    if invalid:
        raise AuthError('invalid_credentials')
    return result


def link_email_password(access_token: str, email: str, password: str):
    email = _canonical_email(email)
    _validate_password(password)
    hashed = _password_hasher().hash(password)
    with auth_transaction():
        now = utcnow()
        session = _authenticated(access_token, now)
        if session.authenticated_at + REAUTH_TTL <= now:
            raise AuthError('reauthentication_required')
        account = _account(session.account_id)
        if TMAAuthPassword.get_or_none(TMAAuthPassword.account == account.user_id):
            raise AuthError('provider_already_linked', 409)
        try:
            TMAAuthPassword.create(account=account.user_id, email=email, password_hash=hashed,
                                   created_at=now, changed_at=now)
        except IntegrityError:
            raise AuthError('identity_conflict', 409) from None


def set_email_password(access_token: str, email: str, password: str):
    """Reset an existing password after a fresh social-provider login.

    This is the recovery path: a user who can freshly authenticate with
    Telegram or Google may choose a new password without email delivery.
    """
    email = _canonical_email(email)
    _validate_password(password)
    hashed = _password_hasher().hash(password)
    with auth_transaction():
        now = utcnow()
        session = _authenticated(access_token, now)
        if (session.authenticated_at + REAUTH_TTL <= now
                or session.authentication_method not in ('telegram', 'google')):
            raise AuthError('reauthentication_required')
        account = _account(session.account_id)
        credential = TMAAuthPassword.get_or_none(TMAAuthPassword.account == account.user_id)
        if credential is None:
            raise AuthError('password_not_linked', 409)
        if credential.email != email:
            raise AuthError('email_change_not_allowed', 409)
        credential.password_hash, credential.changed_at = hashed, now
        credential.save(only=[TMAAuthPassword.password_hash, TMAAuthPassword.changed_at])
        TMAAuthSession.update(revoked_at=now).where(
            (TMAAuthSession.account == account.user_id) & (TMAAuthSession.id != session.id)
            & TMAAuthSession.revoked_at.is_null()).execute()
        TMAAuthPasswordThrottle.delete().where(
            TMAAuthPasswordThrottle.email_hash == _hash('email-login\n' + email)).execute()


def create_challenge(provider: str, purpose: str, *, access_token: str | None = None) -> AuthChallenge:
    if provider not in ('telegram', 'google') or purpose not in ('login', 'link'):
        raise AuthError('invalid_challenge', 400)
    with auth_transaction():
        now = utcnow()
        account_id = None
        initiating_session_id = None
        if purpose == 'link':
            if not access_token:
                raise AuthError('authentication_required')
            session = _authenticated(access_token, now)
            if session.authenticated_at + REAUTH_TTL <= now:
                raise AuthError('reauthentication_required')
            account_id, initiating_session_id = session.account_id, session.id
        secret, state = _new_challenge_value('cs_'), _new_challenge_value('st_')
        nonce = _new_challenge_value('no_') if provider == 'google' else None
        verifier = _new_challenge_value('pv_') if provider == 'google' else None
        challenge = TMAAuthChallenge.create(
            id=str(uuid.uuid4()), provider=provider, purpose=purpose, account=account_id,
            initiating_session_id=initiating_session_id, secret_hash=_hash(secret),
            state_hash=_hash(state), nonce=nonce, pkce_verifier=verifier,
            expires_at=now + CHALLENGE_TTL,
        )
        return AuthChallenge(challenge.id, secret, state, nonce, verifier, challenge.expires_at)


def confirm_challenge(state: str, proof: VerifiedIdentity):
    if not isinstance(state, str) or not isinstance(proof, VerifiedIdentity):
        raise AuthError('invalid_challenge', 400)
    with auth_transaction():
        now = utcnow()
        challenge = locked(TMAAuthChallenge.select().where(TMAAuthChallenge.state_hash == _hash(state))).get_or_none()
        if (challenge is None or challenge.completed_at is not None or challenge.expires_at <= now
                or challenge.provider != proof.provider or proof.expires_at <= now):
            raise AuthError('invalid_challenge')
        if challenge.verified_subject is not None:
            if challenge.proof_hash == proof.proof_hash:
                return
            raise AuthError('challenge_already_confirmed', 409)
        challenge.verified_subject = proof.subject
        challenge.verified_at = proof.authenticated_at
        challenge.verified_expires_at = proof.expires_at
        challenge.proof_hash = proof.proof_hash
        challenge.save()


def get_pending_challenge(state: str, *, provider: str) -> PendingChallenge:
    """Return redirect verifier material without making a challenge reusable."""
    if provider not in ('telegram', 'google') or not isinstance(state, str):
        raise AuthError('invalid_challenge', 400)
    with auth_transaction():
        now = utcnow()
        challenge = TMAAuthChallenge.get_or_none(
            TMAAuthChallenge.state_hash == _hash(state))
        if (challenge is None or challenge.provider != provider
                or challenge.completed_at is not None or challenge.expires_at <= now):
            raise AuthError('invalid_challenge')
        if provider == 'google' and (not challenge.nonce or not challenge.pkce_verifier):
            raise AuthError('invalid_challenge')
        return PendingChallenge(challenge.nonce, challenge.pkce_verifier)


def exchange_challenge(challenge_id: str, secret: str, *, access_token: str | None = None):
    if not isinstance(challenge_id, str) or not isinstance(secret, str):
        raise AuthError('invalid_challenge', 400)
    with auth_transaction():
        now = utcnow()
        challenge = locked(TMAAuthChallenge.select().where(TMAAuthChallenge.id == challenge_id)).get_or_none()
        if (challenge is None or challenge.completed_at is not None or challenge.expires_at <= now
                or not secrets.compare_digest(challenge.secret_hash, _hash(secret))
                or not challenge.verified_subject or not challenge.proof_hash
                or not challenge.verified_at or not challenge.verified_expires_at):
            raise AuthError('invalid_challenge')
        proof = VerifiedIdentity(challenge.provider, challenge.verified_subject, challenge.verified_at,
                                 challenge.verified_expires_at, challenge.proof_hash)
        if challenge.purpose == 'link':
            if not access_token:
                raise AuthError('authentication_required')
            session = _authenticated(access_token, now)
            if session.id != challenge.initiating_session_id or session.account_id != challenge.account_id:
                raise AuthError('invalid_challenge')
            if session.authenticated_at + REAUTH_TTL <= now:
                raise AuthError('reauthentication_required')
            _issue_verified(proof, now, link_account_id=session.account_id)
            result = {'linked': True, 'providers': [row.provider for row in TMAAuthIdentity.select(TMAAuthIdentity.provider)
                      .where(TMAAuthIdentity.account == session.account_id).order_by(TMAAuthIdentity.provider)]}
        else:
            tokens = _issue_verified(proof, now)
            result = {'linked': False, 'tokens': tokens}
        challenge.completed_at = now
        challenge.save(only=[TMAAuthChallenge.completed_at])
        return result
