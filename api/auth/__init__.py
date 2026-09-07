"""Auth v2 foundation. No HTTP routes, DB initialization or network calls on import.

Only trusted server-side provider adapters may construct VerifiedIdentity.
See project_docs/ARCHITECTURE/AUTH_V2.md before connecting a client or router.
"""
