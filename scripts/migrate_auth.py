"""Explicit auth migration tool. Defaults to read-only inspection of an explicit DSN.

AUTH_MIGRATION_DATABASE_URL is intentionally separate from production app config.
The DSN is captured before legacy model imports load dotenv. No API startup,
bot operations or implicit SUPABASE_DB_URL fallback.
"""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from peewee import PostgresqlDatabase
from playhouse.db_url import connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Apply auth migrations 76–79 to the explicitly configured DB')
    args = parser.parse_args()
    # Read this BEFORE importing models (the legacy database module loads dotenv).
    url = os.environ.get('AUTH_MIGRATION_DATABASE_URL')
    if not url:
        parser.error('Set AUTH_MIGRATION_DATABASE_URL explicitly; no app-config fallback is used')
    database = None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname or not parsed.path.strip('/'):
            raise ValueError('Invalid migration DSN')
        database = connect(url, connect_timeout=10)
        if not isinstance(database, PostgresqlDatabase):
            raise ValueError('PostgreSQL is required')
        from api.models import AUTH_MODELS
        from api.migrations import run_auth_migrations, AUTH_FOUNDATION_MIGRATION_ID
        with database.connection_context():
            if not database.table_exists('tma_user'):
                raise RuntimeError('Legacy schema missing')
            counts = database.execute_sql('''SELECT COUNT(*),
                SUM(CASE WHEN is_guest = true THEN 1 ELSE 0 END),
                SUM(CASE WHEN user_id > 0 AND is_guest = false THEN 1 ELSE 0 END)
                FROM tma_user''').fetchone()
            applied = False
            if database.table_exists('tma_migration_history'):
                applied = bool(database.execute_sql(
                    'SELECT migration_id FROM tma_migration_history WHERE migration_id = %s',
                    (AUTH_FOUNDATION_MIGRATION_ID,),
                ).fetchone())
            report = {'mode': 'apply' if args.apply else 'inspect',
                      'legacy_users': counts[0], 'legacy_guests': counts[1] or 0,
                      'telegram_candidates': counts[2] or 0, 'migration_recorded': applied,
                      'auth_tables_present': [m._meta.table_name for m in AUTH_MODELS
                                              if database.table_exists(m._meta.table_name)]}
            report['recorded_auth_migrations'] = [row[0] for row in database.execute_sql(
                'SELECT migration_id FROM tma_migration_history WHERE migration_id BETWEEN 76 AND 79 ORDER BY migration_id'
            ).fetchall()] if database.table_exists('tma_migration_history') else []
            if args.apply:
                report['migration'] = run_auth_migrations(database)
            print(json.dumps(report))
        return 0
    except Exception:
        # DB exception messages may contain credentials/hostnames. Operators can
        # inspect the DB privately; do not echo the DSN or raw exception here.
        print('Auth migration inspection/apply failed. Check the explicit target, schema and DB access; '
              'no failed migration is recorded as successful.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
