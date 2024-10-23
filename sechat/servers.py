from enum import StrEnum


class Server(StrEnum):
    """A URL for a chat instance."""

    STACK_EXCHANGE = "https://chat.stackexchange.com"
    STACK_OVERFLOW = "https://chat.stackoverflow.com"
    META_STACK_EXCHANGE = "https://chat.meta.stackexchange.com"
