class ChatException(Exception):
    """Base exception type"""
    pass

class LoginError(ChatException):
    """Used for errors while logging in"""

    pass

class RatelimitError(ChatException):
    """Used when a ratelimit is hit"""

    def __init__(self, retry_after: int):
        super().__init__()
        self.retry_after = retry_after


class OperationFailedError(ChatException):
    """Used when an operation fails"""
    pass
