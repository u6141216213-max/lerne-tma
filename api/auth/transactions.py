"""One connection and transaction per synchronous auth service operation."""
from contextlib import contextmanager

from peewee import SqliteDatabase

from api.models import tma_db


@contextmanager
def auth_transaction():
    database = tma_db.obj
    if database is None:
        raise RuntimeError('Auth database is not initialized')
    # Especially important for refresh-reuse revocation: a caller must not roll
    # back a successful security mutation by catching AuthError in its transaction.
    if database.in_transaction():
        raise RuntimeError('Auth services must own their transaction')
    owned_connection = database.is_closed()
    try:
        database.connect(reuse_if_open=True)
        options = {'lock_type': 'IMMEDIATE'} if isinstance(database, SqliteDatabase) else {}
        with database.atomic(**options):
            yield database
    finally:
        if owned_connection and not database.is_closed():
            database.close()


def locked(query):
    """SQLite serializes writers via BEGIN IMMEDIATE; PostgreSQL locks rows."""
    return query if isinstance(tma_db.obj, SqliteDatabase) else query.for_update()
