from asyncio import sleep
import json
import re

from functools import partialmethod
from logging import getLogger
from time import monotonic, time
from typing import Any, AsyncGenerator, Optional, cast

from aiohttp import ClientSession
from backoff import on_exception, runtime
from bs4 import BeautifulSoup, Tag
from yarl import URL

from sechat.credentials import Credentials
from sechat.errors import OperationFailedError, RatelimitError
from sechat.events import Event, _EventAdapter, MentionEvent, ReplyEvent
from sechat.servers import Server

RESET_INTERVAL = 60 * 60 * 2
BACKOFF_RESPONSE = re.compile(r"You can perform this action again in (\d+) seconds?\.")


class Room:
    @staticmethod
    async def join(credentials: Credentials, room_id: int):
        session = credentials.session()
        fkey = await Credentials.scrape_fkey(session, credentials.server)
        return Room(room_id, credentials.user_id, session, fkey)

    @staticmethod
    async def anonymous(
        room_id: int, *, server: Server = Server.STACK_EXCHANGE, poll_interval=2
    ):
        async with ClientSession(server) as session:
            fkey = await Credentials.scrape_fkey(session, server)
            async with session.post(
                f"/chats/{room_id}/events",
                data={"since": 0, "mode": "Messages", "msgCount": 100, "fkey": fkey},
            ) as response:
                last_time = (await response.json())["time"]
            while True:
                async with session.post(
                    "/events", data={f"r{room_id}": last_time, "fkey": fkey}
                ) as response:
                    if (
                        payload := cast(dict, await response.json()).get(f"r{room_id}")
                    ) is None:
                        continue
                    if "t" in payload:
                        last_time = payload["t"]
                    if "e" in payload:
                        for event_data in payload["e"]:
                            yield _EventAdapter.validate_python(event_data)
                await sleep(poll_interval)

    def __init__(self, room_id: int, user_id: int, session: ClientSession, fkey: str):
        self.logger = getLogger(__name__).getChild(str(room_id))
        self.room_id = room_id
        self.user_id = user_id
        self.session = session
        self.fkey = fkey

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        await self._request(f"/chats/leave/{self.room_id}", {"quiet": "true"})
        await self.session.close()

    async def _socket_urls(self):
        while True:
            async with self.session.post(
                "/ws-auth", data={"fkey": self.fkey, "roomid": self.room_id}
            ) as response:
                response.raise_for_status()
                url = URL((await response.json())["url"]).with_query(l=int(time()))
            yield url

    async def events(self) -> AsyncGenerator[Event, None]:
        async with ClientSession(
            headers=self.session.headers, cookie_jar=self.session.cookie_jar
        ) as ws_session:
            async for url in self._socket_urls():
                async with ws_session.ws_connect(
                    url, origin=str(self.session._base_url)
                ) as connection:
                    self.logger.info(f"Connected to {url}, fkey is {self.fkey}")
                    connected_at = monotonic()
                    while True:
                        if monotonic() - connected_at >= RESET_INTERVAL:
                            self.logger.debug("Resetting socket after reset interval")
                            break
                        try:
                            message = cast(
                                dict, await connection.receive_json(timeout=45)
                            )
                        except Exception as e:
                            self.logger.warning(
                                "An exception occured while receiving data:", exc_info=e
                            )
                            break
                        if (
                            (body := message.get(f"r{self.room_id}")) is not None
                            and body != {}
                            and (events := body.get("e")) is not None
                        ):
                            for event_data in events:
                                self.logger.debug(
                                    f"Recieved event data: {event_data!r}"
                                )
                                event = _EventAdapter.validate_python(event_data)
                                if isinstance(event, (MentionEvent, ReplyEvent)):
                                    await self._request(
                                        "/messages/ack", {"id": str(event.message_id)}
                                    )
                                yield event

    @on_exception(runtime, RatelimitError, value=lambda e: e.retryAfter, jitter=None)
    async def _request(self, url: str, data: dict[str, Any] = {}):
        async with self.session.post(url, data=data | {"fkey": self.fkey}) as response:
            text = await response.text()
            match response.status:
                case 409:
                    if (match := BACKOFF_RESPONSE.fullmatch(text)) is None:
                        self.logger.warning(f"Got 409 with malformed response: {text}")
                        raise RatelimitError(1)
                    raise RatelimitError(int(match.group(1)))
                case 200:
                    return text
                case _:
                    raise OperationFailedError(response.status, text)

    async def _json_request(self, url: str, data: dict[str, Any] = {}):
        response = await self._request(url, data)
        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            raise OperationFailedError("Failed to decode response", response) from e

    async def _ok_request(self, url: str, data: dict[str, Any] = {}):
        if (response := await self._request(url, data)) != "ok":
            raise OperationFailedError(response)

    async def send(self, message: str, reply_to: Optional[int] = None):
        if not len(message):
            raise ValueError("Cannot send an empty message!")
        if reply_to is not None:
            message = f":{reply_to} " + message
        return (
            await self._json_request(
                f"/chats/{self.room_id}/messages/new", {"text": message}
            )
        )["id"]

    async def edit(self, message_id: int, new_body: str):
        await self._ok_request(f"/messages/{message_id}", {"text": new_body})

    async def _message_nilad_route(self, op: str, message_id: int):
        await self._ok_request(f"/messages/{message_id}/{op}")

    delete = partialmethod(_message_nilad_route, "delete")
    star = partialmethod(_message_nilad_route, "star")
    pin = partialmethod(_message_nilad_route, "owner-star")
    unpin = partialmethod(_message_nilad_route, "unowner-star")
    clear_stars = partialmethod(_message_nilad_route, "unstar")

    async def move_messages(self, message_ids: set[int], target_room: int):
        if (
            result := await self._request(
                f"/admin/movePosts/{self.room_id}",
                {"to": target_room, "ids": ",".join(map(str, message_ids))},
            )
        ) != str(len(message_ids)):
            raise OperationFailedError("Failed to move some messages", result)

    async def bookmark(self, start_message: int, end_message: int, bookmark_title: str):
        payload = {
            "roomId": self.room_id,
            "firstMessageId": start_message,
            "lastMessageId": end_message,
            "title": bookmark_title,
        }
        if not (result := await self._json_request("/conversation/new", payload)).get(
            "ok", False
        ):
            raise OperationFailedError("Failed to create bookmark", result)

    async def delete_bookmark(self, title: str):
        await self._ok_request(f"/conversation/delete/{self.room_id}/{title}")
