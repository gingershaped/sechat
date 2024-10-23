from pprint import pformat
from typing import Any, Optional

from aiohttp import ClientResponse


class ChatException(Exception):
    """Base exception type"""

    pass


class LoginError(ChatException):
    """Raised when an error occurs while logging in"""

    def __init__(self, message: str):
        super().__init__(message)


class RatelimitError(ChatException):
    """Raised when a ratelimit is hit.

    This error is automatically handled by the library and should never escape to user code.
    """

    def __init__(self, retry_after: int):
        super().__init__()
        self.retry_after = retry_after


class OperationFailedError(ChatException):
    """Raised when chat returns an unexpected response"""

    def __init__(self, message: str, payload: Optional[Any] = None):
        super().__init__(message)
        if payload is not None:
            self.add_note(f"Chat responded with the following payload:\n{pformat(payload)}")