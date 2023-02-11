from typing import Optional, Any, TypeVar, Generic
from collections.abc import Callable, Coroutine
from time import time
from logging import Logger, getLogger
from asyncio import gather
from functools import partial

import json

from websockets.client import connect
from aiohttp import ClientSession

from sechat.events import EventBase, MentionEvent, EventType, EventTypeMember, EVENT_CLASSES




ET = TypeVar("ET", bound = EventBase, contravariant=True)
EventHandler = Callable[[ET], Coroutine]

class Room:
    def __init__(
        self,
        session: ClientSession,
        fkey: str,
        userID: int,
        roomID: int,
        logger: Optional[Logger] = None,
    ):
        if logger:
            self.logger = logger
        else:
            self.logger = getLogger(f"Room-{roomID}")
        self.session = session
        self.fkey = fkey
        self.userID = userID
        self.roomID = roomID
        self.lastPing = time()
        self.handlers: dict[EventType, set[EventHandler]] = {
            eventType: set() for eventType in EventType
        }
        self.register(self._mentionHandler, EventType.MENTION)

    async def _mentionHandler(self, event: MentionEvent):
        await self.session.post(
            "https://chat.stackexchange.com/messages/ack",
            data={"id": event.message_id, "fkey": self.fkey},
        )

    async def getSockets(self):
        while True:
            async with self.session.post(
                "https://chat.stackexchange.com/ws-auth",
                data={"fkey": self.fkey, "roomid": self.roomID},
            ) as r:
                yield connect((await r.json())["url"] + f"?l={int(time())}")

    async def loop(self):
        async for connection in self.getSockets():
            async with connection as socket:
                data = await socket.recv()
            if data is not None and data != "":
                try:
                    data = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    self.logger.warning(f"Recieved malformed packet: {data}")
                    continue
                self.lastPing = time.time()
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
                handler(EVENT_CLASSES[eventType](**eventData))
                for handler in self.handlers[eventType]
            )
        )

    T = TypeVar("T", bound = EventTypeMember)
    def register(self, handler: EventHandler[EventBase[T]], eventType: EventType):
        self.handlers[eventType].add(handler)

    def unregister(self, handler: EventHandler, eventType: EventType):
        self.handlers[eventType].remove(handler)

    def on(self, eventType: EventType) -> Callable[[EventHandler], EventHandler]:
        def _on(handler: EventHandler):
            self.register(handler, eventType)
            return handler
        return _on