from typing import Any


class ChatException(Exception):
    """Base exception type"""
    pass

class LoginError(ChatException):
    """Raised when an error occurs while logging in"""

    pass

class RatelimitError(ChatException):
    """Raised when a ratelimit is hit.
    
    This error is automatically handled by the library and should never escape to user code.
    """

    def __init__(self, retry_after: int):
        super().__init__()
        self.retry_after = retry_after


class OperationFailedError(ChatException):
    """Raised when chat returns an unexpected response"""
    pass