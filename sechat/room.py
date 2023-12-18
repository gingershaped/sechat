from typing import Generator, Optional, Any, TypeVar
from collections.abc import Callable, Coroutine, Collection
from time import time
from logging import Logger, getLogger
from asyncio import gather, sleep, wait_for, CancelledError, Event

import json
import re

from websockets.client import connect
from websockets.exceptions import ConnectionClosed
from aiohttp import ClientConnectionError, ClientSession, CookieJar

from backoff import on_exception as backoff, expo, on_predicate, runtime

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
        self._connectedEvent = Event()
        self.cookies = cookies
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

    async def shutdown(self):
        self.logger.info("Shutting down...")
        try:
            await wait_for(self.request(f"https://chat.stackexchange.com/chats/leave/{self.roomID}"), 3)
            await wait_for(self.session.close(), 3)
        except TimeoutError:
            pass
        self.logger.debug("Shutdown completed!")

    async def _mentionHandler(self, _, event: MentionEvent):
        try:
            await self.session.post(
                "https://chat.stackexchange.com/messages/ack",
                data={"id": event.message_id, "fkey": self.fkey},
            )
        except:
            pass

    async def getSocketUrls(self):
        while True:
            try:
                async with self.session.post(
                    "https://chat.stackexchange.com/ws-auth",
                    data={"fkey": self.fkey, "roomid": self.roomID},
                ) as r:
                    url = (await r.json())["url"] + f"?l={int(time())}"
                    self.logger.info(f"Connecting to {url}")
                    yield url
            except ClientConnectionError:
                self.logger.warning("An error occured while fetching the socket, trying again in 3s")
                await sleep(3)

    async def loop(self):
        self.session = ClientSession(cookie_jar=self.cookies)
        try:
            async for url in self.getSocketUrls():
                async with connect(url, origin="http://chat.stackexchange.com", close_timeout=3, ping_interval=None) as socket:  # type: ignore It doesn't like the origin header for some reason
                    self._connectedEvent.set()
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
        finally:
            await self.shutdown()

    async def process(self, data: dict):
        if f"r{self.roomID}" in data:
            data = data[f"r{self.roomID}"]
            if data != {}:
                if "e" in data:
                    for event in data["e"]:
                        if not isinstance(event, dict):
                            continue
                        self.logger.debug(f"Got event data: {event}")
                        for result in await gather(*[i async for i in self.handle(EventType(event["event_type"]), event)], return_exceptions=True):
                            if isinstance(result, Exception):
                                self.logger.error(f"An exception occured in a handler:", exc_info=result)

    async def handle(self, eventType: EventType, eventData: dict):
        event = EVENT_CLASSES[eventType](**eventData)
        for handler in self.handlers[eventType]:
            yield handler(self, event)

    def register(self, handler: EventHandler, eventType: EventType):
        self.handlers[eventType].add(handler)

    def unregister(self, handler: EventHandler, eventType: EventType):
        self.handlers[eventType].remove(handler)

    def on(self, eventType: EventType) -> Callable[[EventHandler], EventHandler]:
        def _on(handler: EventHandler):
            self.register(handler, eventType)
            return handler

        return _on

            
    @backoff(
        runtime,
        RatelimitError,
        value=lambda e: e.retryAfter,
        jitter=None
    )
    async def request(self, uri: str, data: dict[str, Any] = {}):
        while True:
            try:
                response = await self.session.post(
                    uri,
                    data=data | {"fkey": self.fkey},
                    headers={"Referer": f"https://chat.stackexchange.com/rooms/{self.roomID}"},
                )
            except ClientConnectionError:
                self.logger.warning("Connection error, retrying in 3s")
                await sleep(3)
            else:
                break
        if response.status == 409:
            match = re.match(r"You can perform this action again in (\d+)", await response.text())
            if match is None:
                self.logger.warning(f"Unable to extract retry value from response: {await response.text()}")
                raise RatelimitError(1)
            raise RatelimitError(int(match.group(1)))
        elif response.status != 200:
            raise OperationFailedError(response.status, await response.text())
        return response

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

    async def send(self, message: str) -> int:
        assert len(message) >= 1, "Message cannot be empty!"
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

    async def edit(self, messageID: int, newMessage: str):
        assert len(newMessage) >= 1, "Message cannot be empty!"
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

    async def moveMessages(self, messageIDs: Collection[int], roomID: int):
        messageIDs = set(messageIDs)
        self.logger.info(f"Moving messages {messageIDs} to room {roomID}")
        if (result := (
            await (
                await self.request(
                    f"https://chat.stackexchange.com/admin/movePosts/{self.roomID}",
                    {"to": roomID, "ids": ",".join(map(str, messageIDs))},
                )
            ).text()
        )) != str(len(messageIDs)):
            raise OperationFailedError(result)
