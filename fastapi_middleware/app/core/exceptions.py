"""Custom exceptions for Guardian API integration."""


class GuardianBaseException(Exception):
    """Base exception for all Guardian-related errors."""

    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationError(GuardianBaseException):
    """Raised when authentication fails."""
    pass


class TokenExpiredError(AuthenticationError):
    """Raised when access token has expired."""
    pass


class TokenRefreshError(AuthenticationError):
    """Raised when token refresh fails."""
    pass


class APIConnectionError(GuardianBaseException):
    """Raised when connection to Intelbras API fails."""
    pass


class DeviceNotFoundError(GuardianBaseException):
    """Raised when requested device is not found."""
    pass


class PartitionNotFoundError(GuardianBaseException):
    """Raised when requested partition is not found."""
    pass


class AlarmOperationError(GuardianBaseException):
    """Raised when alarm arm/disarm operation fails."""
    pass


class InvalidSessionError(GuardianBaseException):
    """Raised when session_id is invalid or expired."""
    pass


class RateLimitError(GuardianBaseException):
    """Raised when API rate limit is exceeded."""
    pass
