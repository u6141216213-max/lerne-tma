class AuthError(Exception):
    """Stable, non-personal error code for the future HTTP boundary."""

    def __init__(self, code: str, status_code: int = 401):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
