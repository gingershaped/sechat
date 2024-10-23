import json
from pprint import pformat
import re
from asyncio import sleep
from functools import partialmethod
from logging import getLogger
from time import monotonic, time
from typing import Any, AsyncGenerator, Optional, cast

from aiohttp import ClientSession
from backoff import on_exception, runtime
from bs4 import BeautifulSoup, Tag
from pydantic import ValidationError
from yarl import URL

from sechat.credentials import Credentials
from sechat.errors import OperationFailedError, RatelimitError
from sechat.events import Event, EventAdapter, MentionEvent, ReplyEvent
from sechat.servers import Server

RESET_INTERVAL = 60 * 60 * 2
BACKOFF_RESPONSE = re.compile(r"You can perform this action again in (\d+) seconds?\.")


class Room:
    """A chatroom that a bot is in.

    This class should ideally be used as a context manager; if it is not, ensure that
    [`close`][sechat.Room.close] is called before your application exits so the bot account will leave
    the room and the underlying [aiohttp.ClientSession][] gets closed.

    Warning:
        Do not directly construct this class; use [`join`][sechat.Room.join] instead.
    Attributes:
        room_id: The unique id of this room.
        user_id: The unique id of the bot user. See [sechat.Credentials.user_id][].
    """

    @staticmethod
    async def join(credentials: Credentials, room_id: int) -> "Room":
        """Join a room.

        This function returns a `Room` instance, and is designed for use in a context manager.

        Parameters:
            credentials: The credentials for the account to join the room with.
            room_id: The id of the room to join.
        Returns:
            A new `Room` instance which may be used to interact with the room.
        """
        session = credentials._session()
        fkey = await Credentials._scrape_fkey(session)
        return Room(room_id, credentials.user_id, session, fkey)

    @staticmethod
    async def anonymous(
        room_id: int, *, server: Server = Server.STACK_EXCHANGE, poll_interval: int = 2
    ) -> AsyncGenerator[Event, None]:
        """Anonymously poll for events in a room. This method does not require any authentication.

        Parameters:
            room_id: The id of the room to fetch events for.
            server: The chat server of the room. Room ids are only unique to a single chat server.
            poll_interval: The interval at which new events should be checked, in seconds.
                It is not recommended to change this value.
        Yields:
            A sequence of [sechat.events.Event][]s which occur in the room.
        """
        async with ClientSession(server) as session:
            fkey = await Credentials._scrape_fkey(session)
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
                            yield EventAdapter.validate_python(event_data)
                await sleep(poll_interval)

    def __init__(self, room_id: int, user_id: int, session: ClientSession, fkey: str):
        self.room_id = room_id
        self.user_id = user_id
        self._logger = getLogger(__name__).getChild(str(room_id))
        self._session = session
        self._fkey = fkey

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self) -> None:
        """Close this room, releasing the underlying [aiohttp.ClientSession][] and visually leaving the room for other users."""
        await self._request(f"/chats/leave/{self.room_id}", {"quiet": "true"})
        await self._session.close()

    async def _socket_urls(self):
        while True:
            async with self._session.post(
                "/ws-auth", data={"fkey": self._fkey, "roomid": self.room_id}
            ) as response:
                response.raise_for_status()
                url = URL((await response.json())["url"]).with_query(l=int(time()))
            yield url

    async def events(self) -> AsyncGenerator[Event, None]:
        """Listen for events in the room.

        This function may be called multiple times to open several websockets; however, this is not advised
        as chat may react unpredictably.

        Warning:
            Unless this function is called, bot accounts will not appear in the room's user list until they
            send a message and may sporadically disappear after some time. If your bot does not consume events,
            it is advised to consume the events in a background task so the bot will stay in the user list:
            ```py
            async for event in room.events():
                pass
            ```
        Yields:
            A sequence of [sechat.events.Event][]s which occur in the room.
        """
        async with ClientSession(
            headers=self._session.headers, cookie_jar=self._session.cookie_jar
        ) as ws_session:
            async for url in self._socket_urls():
                async with ws_session.ws_connect(
                    url, origin=str(self._session._base_url)
                ) as connection:
                    self._logger.info(f"Connected to {url}, fkey is {self._fkey}")
                    connected_at = monotonic()
                    while True:
                        if monotonic() - connected_at >= RESET_INTERVAL:
                            self._logger.info("Resetting socket after reset interval")
                            break
                        try:
                            message = cast(
                                dict, await connection.receive_json(timeout=45)
                            )
                        except Exception as e:
                            self._logger.warning(
                                "An exception occured while receiving data:", exc_info=e
                            )
                            break
                        if (
                            (body := message.get(f"r{self.room_id}")) is not None
                            and body != {}
                            and (events := body.get("e")) is not None
                        ):
                            for event_data in events:
                                self._logger.debug(
                                    f"Recieved event data: {event_data!r}"
                                )
                                try:
                                    event = EventAdapter.validate_python(event_data)
                                except ValidationError as e:
                                    e.add_note(
                                        f"Recieved event data:\n{pformat(event_data)}"
                                    )
                                    raise
                                if isinstance(event, (MentionEvent, ReplyEvent)):
                                    await self._request(
                                        "/messages/ack", {"id": str(event.message_id)}
                                    )
                                yield event

    @on_exception(runtime, RatelimitError, value=lambda e: e.retry_after, jitter=None)
    async def _request(self, url: str, data: dict[str, Any] = {}):
        async with self._session.post(
            url, data=data | {"fkey": self._fkey}
        ) as response:
            text = await response.text()
            match response.status:
                case 409:
                    if (match := BACKOFF_RESPONSE.fullmatch(text)) is None:
                        self._logger.warning(f"Got 409 with malformed response: {text}")
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
            raise OperationFailedError(f"Recieved non-ok response", response)

    async def send(self, message: str, reply_to: Optional[int] = None) -> int:
        """Send a message.

        Parameters:
            message: The message to send, formatted using chat-compatible Markdown.
            reply_to: A message id to reply to.
        Returns:
            The id of the newly-sent message.
        """
        if not len(message):
            raise ValueError("Cannot send an empty message!")
        if reply_to is not None:
            message = f":{reply_to} " + message
        return (
            await self._json_request(
                f"/chats/{self.room_id}/messages/new", {"text": message}
            )
        )["id"]

    async def edit(self, message_id: int, new_body: str) -> None:
        """Edit a message.

        Parameters:
            message_id: The id of the message to edit. The message must have been sent by this account less than
                two and a half minutes ago.
            new_body: The new body of the message.
        """
        await self._ok_request(f"/messages/{message_id}", {"text": new_body})

    async def _message_nilad_route(self, op: str, message_id: int):
        await self._ok_request(f"/messages/{message_id}/{op}")

    async def delete(self, message_id: int) -> None:
        """Delete a message.

        Parameters:
            message_id: The id of the message to delete. The message must have been sent by this account less than
                two and a half minutes ago.
        """
        await self._message_nilad_route("delete", message_id)

    async def star(self, message_id: int) -> None:
        """Star or unstar a message. This function will _toggle_ the starred state.

        Parameters:
            message_id: The id of the message to star or unstar. An account can only star twenty messages every 24 hours, resetting
                at UTC midnight; after some amount of time (exact duration unknown), the star cannot be removed.
        """
        await self._message_nilad_route("star", message_id)

    async def pin(self, message_id: int) -> None:
        """Pin a message.

        The account must be an owner of the room the message was sent in.

        Parameters:
            message_id: The id of the message to pin.
        """
        await self._message_nilad_route("owner-star", message_id)

    async def unpin(self, message_id: int) -> None:
        """Unpin a message.

        The account must be an owner of the room the message was sent in.

        Parameters:
            message_id: The id of the message to unpin.
        """
        await self._message_nilad_route("unowner-star", message_id)

    async def clear_stars(self, message_id: int) -> None:
        """Clear stars on a message, removing it from the starboard.

        The account must be an owner of the room the message was sent in.

        Parameters:
            message_id: The id of the message to clear stars against.
        """
        await self._message_nilad_route("unstar", message_id)

    async def move_messages(self, message_ids: set[int], target_room: int) -> None:
        """Move messages from one room to another.

        All messages must be in the same room, and the account must be an owner of that room. The account
        does _not_ have to be an owner of the destination room or of any of the messages.
        Starred and pinned messages which are moved will appear on the destination room's starboard.

        Parameters:
            message_ids: A set of message ids to move to the target room.
            target_room: The id of the room to move the messages to.
        """
        if (
            result := await self._json_request(
                f"/admin/movePosts/{self.room_id}",
                {"to": target_room, "ids": ",".join(map(str, message_ids))},
            )
        ) != len(message_ids):
            raise OperationFailedError(
                f"Failed to move some messages; {len(message_ids)} provided, {result} moved"
            )

    async def bookmark(self, start_message: int, end_message: int, title: str) -> str:
        """Bookmark a conversation.

        The start message and end message must not be the same, and must be in the same room.
        Which one is first chronologically does not matter. The slug of the conversation will
        be the supplied title, converted to lowercase, with spaces replaced with dashes and
        non-alphanumeric characters filtered out.

        Parameters:
            start_message: The id of the first message in the conversation.
            end_message: The id of the last message in the conversation.
            title: The title of the bookmark.
        Returns:
            The slug of the conversation, which may be used to delete it.
        """
        payload = {
            "roomId": self.room_id,
            "firstMessageId": start_message,
            "lastMessageId": end_message,
            "title": title,
        }
        result = await self._json_request("/conversation/new", payload)
        if not (isinstance(result, dict) and result.get("ok", False)):
            raise OperationFailedError("Failed to create bookmark", result)
        return "".join(
            filter(lambda char: char.isalnum(), title.lower().replace(" ", "-"))
        )

    async def delete_bookmark(self, slug: str) -> None:
        """Delete a conversation.

        The account must have created the conversation in order to delete it.

        Parameters:
            slug: The slug of the conversation to delete.
        """
        await self._ok_request(f"/conversation/delete/{self.room_id}/{slug}")
