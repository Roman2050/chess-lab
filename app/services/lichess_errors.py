class LichessError(Exception):
    """Base error for failures handled by the Lichess API boundary."""


class LichessConfigurationError(LichessError):
    """The server-side Lichess integration is misconfigured or unauthorized."""


class LichessCoordinationError(LichessError):
    """Redis could not safely coordinate the deployment-wide Lichess client."""


class LichessBusyError(LichessError):
    """Another deployment-wide Lichess import is already active."""

    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__("Lichess import is already in progress")
        self.retry_after = retry_after


class LichessRateLimitedError(LichessError):
    """Lichess has rate-limited the integration."""

    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__("Lichess rate limit is active")
        self.retry_after = retry_after


class LichessUserNotFoundError(LichessError):
    """The requested Lichess account does not exist."""


class LichessUnavailableError(LichessError):
    """Lichess could not be reached or is temporarily unavailable."""


class LichessProtocolError(LichessError):
    """Lichess returned a response outside the supported contract."""
