from logging import getLogger
from aiohttp import ClientSession
from sechat import Credentials
from sechat.client import ChatClient
from sechat.servers import Server


class Profile(ChatClient):
    """A utility class for modifying a user's profile details."""

    @staticmethod
    def open(credentials: Credentials):
        """Create a new context manager for a Profile object."""
        return ChatClient.ContextManager(Profile, credentials)

    def __init__(self, session: ClientSession, fkey: str, user_id: int, server: Server):
        super().__init__(session, fkey, user_id, server)

    async def edit_bio(self, bio: str) -> None:
        """Update the account's bio.

        The maximum length is 200 characters, anything longer will be truncated.

        Parameters:
            message: The new bio text.
        """
        await self._request(f"/users/usermessage/{self.user_id}", {"message": bio})
