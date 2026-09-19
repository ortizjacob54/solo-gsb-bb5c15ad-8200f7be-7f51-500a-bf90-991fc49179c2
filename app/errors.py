"""Domain error type that maps to a structured HTTP error response."""


class DomainError(Exception):
    """A client-facing problem reported as a structured error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict | None = None,
        status_code: int = 422,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
