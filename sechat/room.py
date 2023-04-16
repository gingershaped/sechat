from typing import Optional, Any, TypeVar
from collections.abc import Callable, Coroutine, Mapping, Collection
from time import time
from logging import Logger, getLogger
from asyncio import gather, wait_for, CancelledError

import json

from websockets.client import connect
from websockets.exceptions import ConnectionClosed
from aiohttp import ClientSession, CookieJar

from backoff import on_exception as backoff, expo

from sechat.events import EventBase, MentionEvent, EventType, EVENT_CLASSES
from sechat.errors import RatelimitError, OperationFailedError

T = TypeVar("T", bound=EventBase)
EventHandler = Callable[["Room", T], Coroutine]


class Room:
    def __init__(
        self,
        cookies: CookieJar,
        fkey: str,
        userID: int,
        roomID: int,
        logger: Optional[Logger] = None,
    ):
        if logger:
            self.logger = logger
        else:
            self.logger = getLogger(f"Room-{roomID}")
        self.session = ClientSession(cookie_jar=cookies)
        self.fkey = fkey
        self.userID = userID
        self.roomID = roomID
        self.lastPing = time()
        self.handlers: dict[EventType, set[EventHandler]] = {
            eventType: set() for eventType in EventType
        }
        self.register(self._mentionHandler, EventType.MENTION)

    def __hash__(self):
        return hash(self.roomID)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.logger.info("Shutting down...")
        try:
            await wait_for(self.request(f"https://chat.stackexchange.com/chats/leave/{self.roomID}"), 3)
            await wait_for(self.session.close(), 3)
        except TimeoutError:
            pass
        self.logger.debug("Shutdown completed!")

    async def _mentionHandler(self, _, event: MentionEvent):
        await self.session.post(
            "https://chat.stackexchange.com/messages/ack",
            data={"id": event.message_id, "fkey": self.fkey},
        )

    async def getSocketUrls(self):
        while True:
            async with self.session.post(
                "https://chat.stackexchange.com/ws-auth",
                data={"fkey": self.fkey, "roomid": self.roomID},
            ) as r:
                url = (await r.json())["url"] + f"?l={int(time())}"
                self.logger.info(f"Connecting to {url}")
                yield url

    async def loop(self):
        async with self:
            async for url in self.getSocketUrls():
                async with connect(url, origin="http://chat.stackexchange.com", close_timeout=3, ping_interval=None) as socket:  # type: ignore It doesn't like the origin header for some reason
                    self.logger.info("Connected!")
                    while True:
                        try:
                            data = await wait_for(socket.recv(), timeout=60)
                        except ConnectionClosed:
                            self.logger.warning(
                                "Connection was closed. Attempting to reconnect..."
                            )
                            break
                        except TimeoutError:
                            self.logger.warning(
                                "No data recieved in a while, the connection may have dropped. Attempting to reconnect..."
                            )
                            break
                        except CancelledError:
                            raise
                        except Exception:
                            self.logger.critical(
                                "An error occurred while recieving data!"
                            )
                            raise
                        if data is not None and data != "":
                            try:
                                data = json.loads(data)
                            except (json.JSONDecodeError, TypeError):
                                self.logger.warning(
                                    f"Recieved malformed packet: {data}"
                                )
                                continue
                            await self.process(data)

    async def process(self, data: dict):
        if f"r{self.roomID}" in data:
            data = data[f"r{self.roomID}"]
            if data != {}:
                if "e" in data:
                    for event in data["e"]:
                        if not isinstance(event, dict):
                            continue
                        self.logger.debug(f"Got event data: {event}")
                        await self.handle(EventType(event["event_type"]), event)

    async def handle(self, eventType: EventType, eventData: dict):
        return await gather(
            *(
                handler(self, EVENT_CLASSES[eventType](**eventData))
                for handler in self.handlers[eventType]
            )
        )

    def register(self, handler: EventHandler, eventType: EventType):
        self.handlers[eventType].add(handler)

    def unregister(self, handler: EventHandler, eventType: EventType):
        self.handlers[eventType].remove(handler)

    def on(self, eventType: EventType) -> Callable[[EventHandler], EventHandler]:
        def _on(handler: EventHandler):
            self.register(handler, eventType)
            return handler

        return _on

    async def request(self, uri: str, data: Mapping[str, Any] = {}):
        response = await self.session.post(
            uri,
            data=data | {"fkey": self.fkey},
            headers={"Referer": f"https://chat.stackexchange.com/rooms/{self.roomID}"},
        )
        if response.status == 409:
            raise RatelimitError()
        elif response.status != 200:
            raise OperationFailedError(response.status, await response.text())
        return response

    @backoff(expo, RatelimitError)
    async def bookmark(self, start: int, end: int, title: str):
        result = await (
            await self.request(
                "https://chat.stackexchange.com/conversation/new",
                {
                    "roomId": self.roomID,
                    "firstMessageId": start,
                    "lastMessageId": end,
                    "title": title,
                },
            )
        ).text()
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            raise OperationFailedError(result)
        else:
            if not result.get("ok", False):
                raise OperationFailedError(result)
            return True

    @backoff(expo, RatelimitError)
    async def removeBookmark(self, title: str):
        self.logger.info(f"Removing bookmark {title}")
        if not (
            result := (
                await (
                    await self.request(
                        f"https://chat.stackexchange.com/conversation/delete/{self.roomID}/{title}"
                    )
                ).text()
            )
            != "ok"
        ):
            raise OperationFailedError(result)

    @backoff(expo, RatelimitError)
    async def send(self, message: str) -> int:
        self.logger.info(f'Sending message "{message}"')
        result = await (
            await self.request(
                f"https://chat.stackexchange.com/chats/{self.roomID}/messages/new",
                {"text": message},
            )
        ).text()
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            raise OperationFailedError(result)
        return result["id"]

    async def reply(self, target: int, message: str) -> int:
        return await self.send(f":{target} {message}")

    @backoff(expo, RatelimitError)
    async def edit(self, messageID: int, newMessage: str):
        self.logger.info(f'Editing message {messageID} to "{newMessage}"')
        if not (
            result := (
                await (
                    await self.request(
                        f"https://chat.stackexchange.com/messages/{messageID}",
                        {"text": newMessage},
                    )
                ).text()
            )
            != "ok"
        ):
            raise OperationFailedError(result)

    @backoff(expo, RatelimitError)
    async def delete(self, messageID: int):
        self.logger.info(f"Deleting message {messageID}")
        if not (
            result := (
                await (
                    await self.request(
                        f"https://chat.stackexchange.com/messages/{messageID}/delete"
                    )
                ).text()
            )
            != "ok"
        ):
            raise OperationFailedError(result)

    @backoff(expo, RatelimitError)
    async def star(self, messageID: int):
        self.logger.info(f"Starring message {messageID}")
        if not (
            result := (
                await (
                    await self.request(
                        f"https://chat.stackexchange.com/messages/{messageID}/star"
                    )
                ).text()
            )
            != "ok"
        ):
            raise OperationFailedError(result)

    @backoff(expo, RatelimitError)
    async def pin(self, messageID: int):
        self.logger.info(f"Pinning message {messageID}")
        if not (
            result := (
                await (
                    await self.request(
                        f"https://chat.stackexchange.com/messages/{messageID}/owner-star"
                    )
                ).text()
            )
            != "ok"
        ):
            raise OperationFailedError(result)

    @backoff(expo, RatelimitError)
    async def unpin(self, messageID: int):
        self.logger.info(f"Unpinning message {messageID}")
        if not (
            result := (
                await (
                    await self.request(
                        f"https://chat.stackexchange.com/messages/{messageID}/unowner-star"
                    )
                ).text()
            )
            != "ok"
        ):
            raise OperationFailedError(result)

    @backoff(expo, RatelimitError)
    async def clearStars(self, messageID: int):
        self.logger.info(f"Clearing stars on message {messageID}")
        if not (
            result := (
                await (
                    await self.request(
                        f"https://chat.stackexchange.com/messages/{messageID}/unstar"
                    )
                ).text()
            )
            != "ok"
        ):
            raise OperationFailedError(result)

    @backoff(expo, RatelimitError)
    async def moveMessages(self, messageIDs: Collection[int], roomID: int):
        messageIDs = set(messageIDs)
        self.logger.info(f"Moving messages {messageIDs} to room {roomID}")
        if result := (
            await (
                await self.request(
                    f"https://chat.stackexchange.com/admin/movePosts/{self.roomID}",
                    {"to": roomID, "ids": ",".join(map(str, messageIDs))},
                )
            ).text()
        ) != len(messageIDs):
            raise OperationFailedError(result)
