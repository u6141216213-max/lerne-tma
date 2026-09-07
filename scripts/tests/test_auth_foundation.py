"""Isolated auth regressions: SQLite temp files + local signed provider fixtures.

Run: python -m unittest discover -s scripts/tests -p test_auth_foundation.py -v
Never imports api.main, initializes production DBs or calls a provider network.
"""
from concurrent.futures import ThreadPoolExecutor
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sys
import threading
import unittest
import uuid
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from peewee import IntegrityError, OperationalError, SqliteDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from api import models
from api.auth import service, providers
from api.auth.dependencies import get_authenticated_user_id
from api.auth.errors import AuthError
from api.auth.providers import VerifiedIdentity
from api.migrations import run_auth_migrations


def proof(provider='telegram', subject='101', when=None):
    now = when or models.auth_utcnow()
    return VerifiedIdentity(provider, subject, now, now + timedelta(minutes=5), secrets.token_hex(32))


class AuthDatabaseTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / 'scratch'
        scratch.mkdir(exist_ok=True)
        # Avoid Python 3.14's private temporary-directory ACL on Windows restricted
        # tokens; each test still gets a separate, unpredictable disposable file.
        self.database_path = scratch / f'lerne-auth-test-{uuid.uuid4().hex}.sqlite3'
        self.database = SqliteDatabase(str(self.database_path),
                                       pragmas={'foreign_keys': 1}, timeout=10)
        self.previous = models.tma_db.obj
        models.tma_db.initialize(self.database)
        self.database.connect()
        self.legacy_models = [models.TMAUser, models.TMA_Folder, models.TMA_Deck, models.TMA_Card,
                              models.TMAProgress, models.TMAReviewHistory, models.TMA_Collaborator,
                              models.TMAOfflineBatch, models.TMAAuthCode]
        self.database.create_tables(self.legacy_models)
        self.database.execute_sql('CREATE TABLE tma_migration_history (migration_id INT PRIMARY KEY)')
        self.database.execute_sql('INSERT INTO tma_migration_history VALUES (75)')
        models.TMAUser.create(user_id=101, is_guest=False, first_name='Synthetic user')
        models.TMAUser.create(user_id=202, is_guest=True)
        folder = models.TMA_Folder.create(user_id=101, name='Synthetic folder')
        self.deck = models.TMA_Deck.create(user_id=101, name='Synthetic deck', folder=folder,
                                           share_id='fixture-share')
        self.card = models.TMA_Card.create(deck=self.deck, front_text='A', back_text='B', creator_id=101)
        models.TMAProgress.create(user_id=101, card_id=self.card.id, queue='review', interval=17)
        models.TMAReviewHistory.create(user_id=101, card_id=self.card.id, rating=3)
        models.TMA_Collaborator.create(user_id=202, target_type='deck', target_id=self.deck.id,
                                      role='viewer', added_by=101)
        models.TMAOfflineBatch.create(key='101:fixture-batch', payload_hash='0' * 64, response='{}')

    def tearDown(self):
        self.database.close()
        models.tma_db.initialize(self.previous)
        self.database_path.unlink(missing_ok=True)

    def migrate(self):
        return run_auth_migrations(self.database)

    def snapshot(self, include_users=True):
        return {m._meta.table_name: list(m.select().dicts()) for m in self.legacy_models
                if include_users or m is not models.TMAUser}

    def assert_auth_error(self, code, fn, *args, **kwargs):
        with self.assertRaises(AuthError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_migration_preserves_every_legacy_row_and_is_repeatable(self):
        before = self.snapshot()
        result = self.migrate()
        self.assertEqual(result['accounts'], 2)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(models.TMAAuthIdentity.select().count(), 0)
        self.assertEqual(models.TMAAuthSession.select().count(), 0)
        self.assertFalse(self.migrate()['applied'])
        self.assertEqual(models.TMAAuthAccount.select().count(), 2)
        self.assertEqual(self.snapshot(), before)

    def test_empty_database_migration_and_missing_legacy_schema(self):
        self.database.drop_tables(self.legacy_models)
        with self.assertRaisesRegex(RuntimeError, 'Legacy tma_user'):
            self.migrate()
        models.TMAUser.create_table()
        self.assertEqual(self.migrate()['accounts'], 0)

    def test_migration_failure_rolls_back_ddl_backfill_and_marker(self):
        before = self.snapshot()
        execute = self.database.execute_sql

        def fail_marker(sql, *args, **kwargs):
            if 'INSERT INTO tma_migration_history (migration_id)' in sql:
                raise OperationalError('Synthetic failure')
            return execute(sql, *args, **kwargs)

        with patch.object(self.database, 'execute_sql', side_effect=fail_marker):
            with self.assertRaises(OperationalError):
                self.migrate()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.database.table_exists('tma_auth_account'))
        self.assertEqual(self.database.execute_sql('SELECT migration_id FROM tma_migration_history').fetchall(), [(75,)])
        self.assertTrue(self.migrate()['applied'])

    def test_untracked_tables_and_missing_tracked_table_fail_closed(self):
        models.TMAAuthAccount.create_table()
        with self.assertRaisesRegex(RuntimeError, 'Untracked auth tables'):
            self.migrate()
        models.TMAAuthAccount.drop_table()
        self.migrate()
        models.TMAAuthProof.drop_table()
        with self.assertRaisesRegex(RuntimeError, 'history/schema mismatch'):
            self.migrate()

    def test_database_enforces_identity_uniqueness_provider_and_foreign_keys(self):
        self.migrate()
        models.TMAAuthIdentity.create(account=101, provider='google', subject='google-sub')
        for values in (
            dict(account=202, provider='google', subject='google-sub'),
            dict(account=101, provider='google', subject='another-sub'),
            dict(account=999, provider='google', subject='nonexistent-account'),
            dict(account=202, provider='whatsapp', subject='unsupported'),
        ):
            with self.subTest(values=values), self.assertRaises(IntegrityError):
                models.TMAAuthIdentity.create(**values)

    def test_telegram_android_login_and_linked_google_keep_progress_and_account_id(self):
        self.migrate()
        before = self.snapshot(include_users=False)
        telegram = service.login_verified(proof())
        self.assertEqual(telegram.account_id, 101)
        service.link_verified(telegram.access_token, proof('google', 'google-sub'))
        google = service.login_verified(proof('google', 'google-sub'))
        self.assertEqual(google.account_id, telegram.account_id)
        self.assertEqual(service.authenticate(google.access_token).account_id, 101)
        self.assertEqual(service.linked_providers(telegram.access_token), ['google', 'telegram'])
        self.assertEqual(self.snapshot(include_users=False), before)
        self.assertEqual(models.TMAUser.select().count(), 2)

    def test_google_first_uses_js_safe_non_telegram_id(self):
        self.migrate()
        tokens = service.login_verified(proof('google', 'new-google-sub'))
        self.assertTrue(-(2**52) < tokens.account_id <= -(10**12))
        self.assertEqual(service.login_verified(proof('google', 'new-google-sub')).account_id, tokens.account_id)
        self.assertFalse(models.TMAUser.get_by_id(tokens.account_id).is_guest)

    def test_real_new_telegram_user_also_gets_provider_independent_id(self):
        self.migrate()
        tokens = service.login_verified(proof('telegram', '303'))
        self.assertLess(tokens.account_id, 0)
        self.assertEqual(service.login_verified(proof('telegram', '303')).account_id, tokens.account_id)

    def test_google_first_cannot_capture_unclaimed_legacy_telegram_account(self):
        self.migrate()
        google = service.login_verified(proof('google', 'new-google-sub'))
        before = self.snapshot()
        error = self.assert_auth_error('identity_conflict', service.link_verified, google.access_token, proof())
        self.assertEqual(error.status_code, 409)
        self.assertNotIn('101', str(error))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(service.login_verified(proof()).account_id, 101)

    def test_linked_identity_cannot_be_moved_or_replaced(self):
        self.migrate()
        first = service.login_verified(proof())
        second = service.login_verified(proof('telegram', '404'))
        service.link_verified(first.access_token, proof('google', 'first-google'))
        self.assert_auth_error('identity_conflict', service.link_verified, second.access_token,
                               proof('google', 'first-google'))
        self.assert_auth_error('provider_already_linked', service.link_verified, first.access_token,
                               proof('google', 'different-google'))
        # Linking the same identity again is idempotent with fresh provider proof.
        service.link_verified(first.access_token, proof('google', 'first-google'))
        self.assertEqual(models.TMAAuthIdentity.select().count(), 3)

    def test_ambiguous_guest_id_is_preserved_and_cannot_be_claimed(self):
        self.migrate()
        self.assertIsNone(models.TMAAuthAccount.get_by_id(202).legacy_telegram_subject)
        self.assert_auth_error('legacy_account_review_required', service.login_verified, proof('telegram', '202'))
        self.assertTrue(models.TMAUser.get_by_id(202).is_guest)
        self.assertEqual(models.TMAAuthSession.select().count(), 0)

    def test_unverified_profile_and_guest_flag_grant_no_session(self):
        self.migrate()
        self.assert_auth_error('invalid_provider_proof', service.login_verified,
                               {'provider': 'telegram', 'subject': '101'})
        self.assertEqual(models.TMAAuthIdentity.select().count(), 0)
        self.assertEqual(models.TMAAuthSession.select().count(), 0)

    def test_provider_proof_is_one_time_and_failure_creates_no_orphan_account(self):
        self.migrate()
        verified = proof('google', 'new-google')
        service.login_verified(verified)
        count = models.TMAUser.select().count()
        self.assert_auth_error('provider_proof_replayed', service.login_verified, verified)
        stale = proof('google', 'stale', models.auth_utcnow() - timedelta(minutes=6))
        self.assert_auth_error('invalid_provider_proof', service.login_verified, stale)
        self.assertEqual(models.TMAUser.select().count(), count)
        self.assertEqual(models.TMAAuthSession.select().count(), 1)

    def test_tokens_are_hashed_and_cannot_be_used_for_wrong_purpose(self):
        self.migrate()
        tokens = service.login_verified(proof())
        for record in models.TMAAuthToken.select():
            self.assertEqual(len(record.token_hash), 64)
            self.assertNotIn(record.token_hash, (tokens.access_token, tokens.refresh_token))
        self.assertNotIn(tokens.refresh_token, repr(tokens))
        self.assert_auth_error('invalid_session', service.authenticate, tokens.refresh_token)
        self.assert_auth_error('invalid_session', service.refresh_session, tokens.access_token)
        self.assert_auth_error('invalid_session', service.authenticate, tokens.access_token[:-1] + '!')

    def test_refresh_rotation_replay_commits_revocation_and_preserves_other_device(self):
        self.migrate()
        first = service.login_verified(proof())
        other = service.login_verified(proof())
        rotated = service.refresh_session(first.refresh_token)
        self.assertEqual(rotated.session_id, first.session_id)
        self.assertNotEqual(rotated.refresh_token, first.refresh_token)
        self.assertEqual(rotated.session_expires_at, first.session_expires_at)
        self.assertEqual(service.authenticate(rotated.access_token).account_id, 101)
        self.assert_auth_error('refresh_reuse_detected', service.refresh_session, first.refresh_token)
        self.assert_auth_error('invalid_session', service.authenticate, rotated.access_token)
        self.assert_auth_error('invalid_session', service.refresh_session, rotated.refresh_token)
        self.assertEqual(service.authenticate(other.access_token).account_id, 101)
        self.assertIsNotNone(models.TMAAuthSession.get_by_id(first.session_id).revoked_at)

    def test_expiry_logout_disable_and_account_deletion(self):
        self.migrate()
        first = service.login_verified(proof())
        with patch.object(service, 'utcnow', return_value=first.access_expires_at):
            self.assert_auth_error('invalid_session', service.authenticate, first.access_token)
            refreshed = service.refresh_session(first.refresh_token)
        service.revoke_session(refreshed.access_token)
        self.assert_auth_error('invalid_session', service.authenticate, refreshed.access_token)
        second = service.login_verified(proof())
        with patch.object(service, 'utcnow', return_value=second.session_expires_at):
            self.assert_auth_error('invalid_session', service.refresh_session, second.refresh_token)
        models.TMAAuthAccount.update(disabled_at=models.auth_utcnow()).where(models.TMAAuthAccount.user == 101).execute()
        self.assert_auth_error('invalid_session', service.authenticate, second.access_token)
        self.assert_auth_error('invalid_session', service.login_verified, proof())
        models.TMAUser.delete().where(models.TMAUser.user_id == 101).execute()
        self.assertEqual(models.TMAAuthIdentity.select().count(), 0)
        self.assertEqual(models.TMAAuthSession.select().count(), 0)
        self.assertEqual(models.TMAAuthToken.select().count(), 0)

    def test_logout_all_and_reauthentication_for_link(self):
        self.migrate()
        first = service.login_verified(proof())
        second = service.login_verified(proof())
        later = models.auth_utcnow() + timedelta(minutes=6)
        with patch.object(service, 'utcnow', return_value=later):
            refreshed = service.refresh_session(first.refresh_token)
            self.assert_auth_error('reauthentication_required', service.link_verified,
                                   refreshed.access_token, proof('google', 'g', later))
        service.revoke_session(second.access_token, all_sessions=True)
        self.assert_auth_error('invalid_session', service.authenticate, first.access_token)
        self.assert_auth_error('invalid_session', service.authenticate, second.access_token)

    def test_failure_issuing_tokens_rolls_back_identity_account_and_proof(self):
        self.migrate()
        verified = proof('google', 'rollback-google')
        with patch.object(service, '_mint_tokens', side_effect=OperationalError('Synthetic failure')):
            with self.assertRaises(OperationalError):
                service.login_verified(verified)
        self.assertEqual(models.TMAUser.select().count(), 2)
        self.assertEqual(models.TMAAuthSession.select().count(), 0)
        self.assertEqual(models.TMAAuthIdentity.select().count(), 0)
        self.assertEqual(models.TMAAuthProof.select().count(), 0)
        self.assertLess(service.login_verified(verified).account_id, 0)

    def test_service_rejects_outer_transaction_and_owns_worker_connection(self):
        self.migrate()
        with self.database.atomic():
            with self.assertRaisesRegex(RuntimeError, 'own their transaction'):
                service.login_verified(proof())
        self.database.close()
        service.login_verified(proof())
        self.assertTrue(self.database.is_closed())

    def test_concurrent_refresh_has_one_winner_and_replay_revokes_family(self):
        self.migrate()
        tokens = service.login_verified(proof())
        barrier = threading.Barrier(2)

        def rotate():
            barrier.wait(timeout=5)
            try:
                return service.refresh_session(tokens.refresh_token)
            except AuthError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: rotate(), range(2)))
        successes = [r for r in results if isinstance(r, service.SessionTokens)]
        self.assertEqual(len(successes), 1)
        self.assertIn('refresh_reuse_detected', results)
        self.assert_auth_error('invalid_session', service.authenticate, successes[0].access_token)

    def test_concurrent_first_logins_resolve_to_one_account(self):
        self.migrate()
        barrier = threading.Barrier(2)

        def login():
            barrier.wait(timeout=5)
            return service.login_verified(proof('google', 'parallel-google')).account_id

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: login(), range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(models.TMAUser.select().count(), 3)

    def test_strict_http_dependency_ignores_forged_user_header(self):
        self.migrate()
        tokens = service.login_verified(proof())
        app = FastAPI()

        @app.get('/private')
        def private(user_id: int = Depends(get_authenticated_user_id)):
            return {'user_id': user_id}

        with TestClient(app) as client:
            self.assertEqual(client.get('/private', headers={'X-User-ID': '101'}).status_code, 401)
            self.assertEqual(client.get('/private', headers={'Authorization': 'Bearer invalid'}).status_code, 401)
            response = client.get('/private', headers={
                'X-User-ID': '202', 'Authorization': 'Bearer ' + tokens.access_token,
            })
            self.assertEqual(response.json(), {'user_id': 101})

    def test_legacy_header_and_public_code_endpoints_are_retired(self):
        from api.dependencies.auth import get_user_id
        from api.routers.auth import CodeGenerateSchema, generate_auth_code
        with self.assertRaises(TypeError):
            get_user_id(x_user_id='101')
        with self.assertRaises(HTTPException) as raised:
            generate_auth_code(CodeGenerateSchema(user_id=101))
        self.assertEqual(raised.exception.status_code, 410)

    def test_one_time_challenge_requires_server_secret_and_preserves_account(self):
        self.migrate()
        challenge = service.create_challenge('telegram', 'login')
        pending = service.get_pending_challenge(challenge.state, provider='telegram')
        self.assertIsNone(pending.nonce)
        verified = proof('telegram', '101')
        service.confirm_challenge(challenge.state, verified)
        with self.assertRaises(AuthError):
            service.exchange_challenge(challenge.id, 'wrong-secret')
        result = service.exchange_challenge(challenge.id, challenge.secret)
        self.assertFalse(result['linked'])
        account_id = result['tokens'].account_id
        with self.assertRaises(AuthError):
            service.exchange_challenge(challenge.id, challenge.secret)
        tokens = service.login_verified(proof('telegram', '101'))
        self.assertEqual(tokens.account_id, account_id)

    def test_email_password_register_login_and_durable_throttle(self):
        self.migrate()
        password = 'correct horse battery staple'
        created = service.register_email_password(' User@Example.COM ', password)
        logged_in = service.login_email_password('user@example.com', password)
        self.assertEqual(created.account_id, logged_in.account_id)
        for _ in range(5):
            with self.assertRaises(AuthError) as raised:
                service.login_email_password('user@example.com', 'incorrect password')
            self.assertEqual(raised.exception.code, 'invalid_credentials')
        with self.assertRaises(AuthError) as raised:
            service.login_email_password('user@example.com', password)
        self.assertEqual(raised.exception.code, 'too_many_attempts')

    def test_email_password_link_keeps_existing_telegram_account(self):
        self.migrate()
        telegram = service.login_verified(proof('telegram', '101'))
        service.link_email_password(telegram.access_token, 'person@example.com', 'correct horse battery staple')
        by_email = service.login_email_password('person@example.com', 'correct horse battery staple')
        self.assertEqual(by_email.account_id, telegram.account_id)
        self.assertIn('email_password', service.linked_providers(telegram.access_token))
        other = service.login_verified(proof('telegram', '987654321'))
        with self.assertRaises(AuthError) as raised:
            service.link_email_password(other.access_token, 'person@example.com', 'another correct password')
        self.assertEqual(raised.exception.code, 'identity_conflict')


    def test_password_recovery_preserves_data_revokes_other_sessions_and_clears_lockout(self):
        self.migrate()
        social = service.login_verified(proof())
        service.link_email_password(social.access_token, 'recover@example.com', 'old correct password')
        old = service.login_email_password('recover@example.com', 'old correct password')
        before = self.snapshot()
        for _ in range(5):
            self.assert_auth_error('invalid_credentials', service.login_email_password,
                                   'recover@example.com', 'incorrect password')
        service.set_email_password(social.access_token, 'recover@example.com', 'new correct password')
        self.assertEqual(before, self.snapshot())
        self.assertEqual(service.authenticate(social.access_token).account_id, 101)
        self.assert_auth_error('invalid_session', service.authenticate, old.access_token)
        self.assert_auth_error('invalid_session', service.refresh_session, old.refresh_token)
        self.assert_auth_error('invalid_credentials', service.login_email_password,
                               'recover@example.com', 'old correct password')
        self.assertEqual(service.login_email_password('recover@example.com', 'new correct password').account_id, 101)

    def test_recovery_rejects_password_unknown_method_and_stale_sessions(self):
        self.migrate()
        email = service.register_email_password('recover@example.com', 'old correct password')
        service.link_verified(email.access_token, proof('telegram', '555'))
        for method in ('email_password', None, 'telegram'):
            models.TMAAuthSession.update(authentication_method=method,
                authenticated_at=models.auth_utcnow() - timedelta(minutes=6) if method == 'telegram'
                else models.auth_utcnow()).where(models.TMAAuthSession.id == email.session_id).execute()
            self.assert_auth_error('reauthentication_required', service.set_email_password,
                                   email.access_token, 'recover@example.com', 'new correct password')
        self.assertEqual(service.login_email_password('recover@example.com', 'old correct password').account_id,
                         email.account_id)

    def test_recovery_does_not_change_email_or_touch_another_account(self):
        self.migrate()
        email = service.register_email_password('recover@example.com', 'old correct password')
        other = service.login_verified(proof())
        self.assert_auth_error('password_not_linked', service.set_email_password,
                               other.access_token, 'recover@example.com', 'new correct password')
        service.link_verified(email.access_token, proof('google', 'recovery-google'))
        social = service.login_verified(proof('google', 'recovery-google'))
        self.assertTrue(service.password_settings(social.access_token)['can_reset_password'])
        self.assert_auth_error('email_change_not_allowed', service.set_email_password,
                               social.access_token, 'different@example.com', 'new correct password')
        service.set_email_password(social.access_token, 'recover@example.com', 'new correct password')
        self.assertEqual(service.authenticate(other.access_token).account_id, 101)

    def test_recovery_failure_rolls_back_password_and_revocations(self):
        self.migrate()
        social = service.login_verified(proof())
        service.link_email_password(social.access_token, 'recover@example.com', 'old correct password')
        old = service.login_email_password('recover@example.com', 'old correct password')
        with patch.object(models.TMAAuthPasswordThrottle, 'delete', side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):
                service.set_email_password(social.access_token, 'recover@example.com', 'new correct password')
        self.assertEqual(service.authenticate(old.access_token).account_id, 101)
        self.assertEqual(service.login_email_password('recover@example.com', 'old correct password').account_id, 101)

    def test_session_method_upgrade_leaves_old_sessions_untrusted(self):
        self.migrate()
        social = service.login_verified(proof())
        self.database.execute_sql('ALTER TABLE tma_auth_session DROP COLUMN authentication_method')
        self.database.execute_sql('DELETE FROM tma_migration_history WHERE migration_id = 79')
        self.assertTrue(self.migrate()['session_method_migration_applied'])
        self.assertIsNone(models.TMAAuthSession.get_by_id(social.session_id).authentication_method)
        self.assertFalse(service.password_settings(social.access_token)['can_reset_password'])
        self.assertFalse(self.migrate().get('session_method_migration_applied', False))

    def test_password_http_contract_redacts_validation_and_requires_social_login(self):
        from api.routers.auth_v2 import router
        self.migrate()
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        secret = 'secret!'
        response = client.post('/auth/v2/email-password/register', json={'email': 'bad', 'password': secret})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)
        created = client.post('/auth/v2/email-password/register', json={
            'email': 'recover@example.com', 'password': 'old correct password'})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.headers['cache-control'], 'no-store')
        headers = {'Authorization': 'Bearer ' + created.json()['access_token']}
        response = client.post('/auth/v2/email-password/set', headers=headers, json={
            'email': 'recover@example.com', 'password': 'new correct password'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['detail'], 'reauthentication_required')
        self.assertEqual(client.get('/auth/v2/password-settings').status_code, 401)

    def test_login_rereads_hash_after_account_lock(self):
        self.migrate()
        created = service.register_email_password('recover@example.com', 'old correct password')
        lock_account = service._account
        new_hash = service._password_hasher().hash('new correct password')

        def reset_while_acquiring_lock(account_id):
            account = lock_account(account_id)
            models.TMAAuthPassword.update(password_hash=new_hash).where(
                models.TMAAuthPassword.account == account_id).execute()
            return account

        with patch.object(service, '_account', side_effect=reset_while_acquiring_lock):
            self.assert_auth_error('invalid_credentials', service.login_email_password,
                                   'recover@example.com', 'old correct password')
        self.assertEqual(models.TMAAuthSession.select().where(
            models.TMAAuthSession.account == created.account_id).count(), 1)

    def test_reminders_use_verified_telegram_subject_not_canonical_account_id(self):
        from api.services import reminder_service as reminders
        self.migrate()
        created = service.register_email_password('recover@example.com', 'old correct password')
        send = AsyncMock()
        bot = SimpleNamespace(bot=SimpleNamespace(send_message=send))
        self.assertEqual(asyncio.run(reminders.send_reminder_to_user(bot, created.account_id))['status'], 'skipped')
        send.assert_not_awaited()
        service.link_verified(created.access_token, proof('telegram', '555'))
        with patch.object(reminders, 'get_user_reminder_settings', return_value={'enabled': True}), \
             patch.object(reminders, 'get_user_due_summary', return_value={'total_due': 1, 'total_new': 0}), \
             patch.object(reminders, 'format_reminder_message', return_value='Synthetic reminder'), \
             patch.object(reminders, 'InlineKeyboardMarkup', return_value=None), \
             patch.object(reminders, 'InlineKeyboardButton', return_value=None):
            result = asyncio.run(reminders.send_reminder_to_user(bot, created.account_id))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(send.await_args.kwargs['chat_id'], 555)
        with patch.object(reminders, 'get_user_reminder_settings', return_value={'enabled': False}):
            self.assertEqual(asyncio.run(reminders.send_reminder_to_user(bot, created.account_id))['status'], 'skipped')
        self.assertEqual(send.await_count, 1)


class TelegramProofTests(unittest.TestCase):
    def signed_data(self, **overrides):
        values = {'user': json.dumps({'id': 101}),
                  'auth_date': str(int(datetime.now(timezone.utc).timestamp())), **overrides}
        check = '\n'.join(f'{key}={value}' for key, value in sorted(values.items()))
        key = hmac.new(b'WebAppData', b'synthetic-bot-token', hashlib.sha256).digest()
        values['hash'] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
        return urlencode(values)

    def test_valid_signed_proof_and_canonical_fingerprint(self):
        raw = self.signed_data(signature='signed-additional-field')
        first = providers.verify_telegram_init_data(raw, 'synthetic-bot-token')
        second = providers.verify_telegram_init_data('&'.join(reversed(raw.split('&'))), 'synthetic-bot-token')
        self.assertEqual(first.subject, '101')
        self.assertEqual(first.proof_hash, second.proof_hash)

    def test_invalid_signature_duplicates_expiry_future_and_subject(self):
        valid = self.signed_data()
        old = int(datetime.now(timezone.utc).timestamp()) - 301
        future = int(datetime.now(timezone.utc).timestamp()) + 60
        for raw, token in (
            (valid, 'wrong-bot-token'), (valid + '&auth_date=1', 'synthetic-bot-token'),
            (self.signed_data(auth_date=str(old)), 'synthetic-bot-token'),
            (self.signed_data(auth_date=str(future)), 'synthetic-bot-token'),
            (self.signed_data(user=json.dumps({'id': True})), 'synthetic-bot-token'),
            (self.signed_data(user=json.dumps({'id': -1})), 'synthetic-bot-token'),
            (self.signed_data(user='{}'), 'synthetic-bot-token'),
            (self.signed_data(user='null'), 'synthetic-bot-token'),
            ('hash=x', 'synthetic-bot-token'),
        ):
            with self.subTest(raw=raw), self.assertRaises(AuthError):
                providers.verify_telegram_init_data(raw, token)


class GoogleProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from google.auth import crypt
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        cls.public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        cls.signer = crypt.RSASigner.from_string(private, key_id='fixture')

    def signed_token(self, **overrides):
        from google.auth import jwt
        now = int(datetime.now(timezone.utc).timestamp())
        claims = dict(iss='https://accounts.google.com', aud='fixture-client', sub='google-101',
                      iat=now, exp=now + 3600, nonce='n' * 43)
        claims.update(overrides)
        return jwt.encode(self.signer, claims).decode()

    def verify(self, token):
        # Real RSA signature/issuer/audience/time verification, only certificate
        # retrieval is replaced with a local fixture; no external requests.
        with patch('google.oauth2.id_token._fetch_certs', return_value={'fixture': self.public}):
            return providers.verify_google_id_token(token, client_id='fixture-client', expected_nonce='n' * 43)

    def test_real_signed_google_token_and_subject_not_email(self):
        result = self.verify(self.signed_token(email='same@example.test'))
        self.assertEqual(result.provider, 'google')
        self.assertEqual(result.subject, 'google-101')

    def test_invalid_issuer_audience_nonce_presenter_and_expired_token(self):
        now = int(datetime.now(timezone.utc).timestamp())
        for override in (dict(iss='https://attacker.invalid'), dict(aud='other-client'),
                         dict(nonce='wrong'), dict(azp='other-client'), dict(exp=now - 60),
                         dict(iat=now - 301), dict(sub=''), dict(iat=now + 60)):
            with self.subTest(override=override), self.assertRaises(AuthError):
                self.verify(self.signed_token(**override))

    def test_bad_signature_and_transport_failure(self):
        token = self.signed_token()
        parts = token.split('.')
        parts[2] = 'A' * len(parts[2])
        with self.assertRaises(AuthError):
            self.verify('.'.join(parts))
        from google.auth.exceptions import TransportError
        with patch('google.oauth2.id_token._fetch_certs', side_effect=TransportError('private transport details')):
            with self.assertRaises(AuthError) as caught:
                providers.verify_google_id_token(token, client_id='fixture-client', expected_nonce='n' * 43)
        self.assertEqual(caught.exception.code, 'provider_unavailable')
        self.assertNotIn('private', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
