import json
import re
from typing import Any, Callable, Optional
from aiohttp import ClientSession
from backoff import on_exception, runtime

from sechat import credentials
from sechat.credentials import Credentials
from sechat.errors import OperationFailedError, RatelimitError
from sechat.servers import Server

BACKOFF_RESPONSE = re.compile(r"You can perform this action again in (\d+) seconds?\.")


class ChatClient:
    """A base class for objects which interact with chat.

    Attributes:
        user_id: The unique id of the bot user.
        server: The chat instance this room belongs to.
    """

    class ContextManager[C: ChatClient]:
        def __init__(self, cls: type[C], credentials: Credentials, *args):
            self.cls = cls
            self.credentials = credentials
            self.args = args
            self.instance: Optional[C] = None

        async def __aenter__(self) -> C:
            session = self.credentials._session()
            fkey = await self.credentials._scrape_fkey(session)
            self.instance = self.cls(
                session,
                fkey,
                self.credentials.user_id,
                self.credentials.server,
                *self.args,
            )
            return self.instance

        async def __aexit__(self, *_):
            assert self.instance is not None
            await self.instance.close()

    def __init__(self, session: ClientSession, fkey: str, user_id: int, server: Server):
        self._session = session
        self._fkey = fkey
        self.user_id = user_id
        self.server = server

    async def close(self):
        await self._session.close()

    @on_exception(runtime, RatelimitError, value=lambda e: e.retry_after, jitter=None)
    async def _request(self, url: str, data: dict[str, Any] = {}):
        async with self._session.post(
            url, data=data | {"fkey": self._fkey}
        ) as response:
            text = await response.text()
            match response.status:
                case 409:
                    if (match := BACKOFF_RESPONSE.fullmatch(text)) is None:
                        raise RatelimitError(1)
                    raise RatelimitError(int(match.group(1)))
                case 200:
                    return text
                case _:
                    raise OperationFailedError(
                        f"Got non-ok status code {response.status} ({response.reason})",
                        text,
                    )

    async def _json_request(self, url: str, data: dict[str, Any] = {}):
        response = await self._request(url, data)
        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            raise OperationFailedError("Failed to decode response", response) from e

    async def _ok_request(self, url: str, data: dict[str, Any] = {}):
        if (response := await self._json_request(url, data)) != "ok":
            raise OperationFailedError(f"received non-ok response", response)
