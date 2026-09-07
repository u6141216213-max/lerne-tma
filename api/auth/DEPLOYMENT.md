# Auth v2 deployment

Run `python scripts/migrate_auth.py --apply` only in a planned maintenance
window, with `AUTH_MIGRATION_DATABASE_URL` set to the chosen PostgreSQL target.
This applies migrations 76–79; API startup never applies them implicitly.
Migration 79 adds nullable session authentication provenance. Existing sessions
remain untrusted for password recovery until the user signs in again through a
social provider. Tested on disposable SQLite only; validate PostgreSQL DDL and
concurrent reset/login on a staging copy before deployment. Forward recovery:
rerun the explicit runner after correcting a failed migration; do not delete
credentials or migration history to roll back. Snapshot the chosen DB first.

Set these server secrets before enabling traffic: `BOT_TOKEN`,
`TELEGRAM_WEBHOOK_SECRET`, `BOT_SETUP_TOKEN`, `REMINDER_CRON_TOKEN`,
`PUBLIC_API_BASE_URL`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
`GOOGLE_OAUTH_REDIRECT_URI`, and `TELEGRAM_BOT_USERNAME`. The two URLs must use
HTTPS, and the Google redirect must exactly match its registered URI.

`/api/bot_setup` requires `X-Internal-Setup-Token`; Telegram webhook requests
must carry Telegram's configured secret header. Test login in browser, Android,
and Telegram Mini App before opening traffic.

The current web/Capacitor compatibility layer stores a refresh token locally.
Before public release, replace it with Android Keystore-backed storage and a
separate web HttpOnly-cookie/CSRF contract. Do not treat it as XSS-resistant.

Email/password credentials use Argon2id and a durable per-email lockout after
five failed attempts. Email delivery is not configured in this repository:
email ownership is not verified and there are no email recovery messages.
Password recovery requires a fresh (under five minutes) Telegram/Google login
to the same, previously linked account. An email/password session or an old
session cannot reset passwords. Reset preserves the sign-in email, revokes all
other sessions and clears the email lockout atomically. Without a linked social
provider, password recovery is intentionally unavailable in this MVP.

Registration offers Telegram linking without making it mandatory. Linking does
not merge two existing accounts or trust matching email addresses. Reminder
recipients are resolved from the verified Telegram identity, never the canonical
account ID; existing reminder preferences are preserved.

Outstanding release checks: browser/Android/Telegram end-to-end provider flows,
automatic token refresh on the client, secure token storage, account-switch
isolation of offline data and pending sync operations, rate limiting for public
registration/challenge endpoints, and explicit Telegram sign-in confirmation.
This local implementation is not a production-readiness sign-off.
