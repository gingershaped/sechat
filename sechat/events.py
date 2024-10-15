from typing import Annotated, Any, Literal, Optional
from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class EventType(IntEnum):
    MESSAGE = 1
    EDIT = 2
    JOIN = 3
    LEAVE = 4
    NAME_CHANGE = 5
    MESSAGE_STARRED = 6
    DEBUG = 7
    MENTION = 8
    FLAG = 9
    DELETE = 10
    FILE_UPLOAD = 11
    MODERATOR_FLAG = 12
    SETTINGS_CHANGED = 13
    GLOBAL_NOTIFICATION = 14
    ACCESS_CHANGED = 15
    USER_NOTIFICATION = 16
    INVITATION = 17
    REPLY = 18
    MESSAGE_MOVED_OUT = 19
    MESSAGE_MOVED_IN = 20
    TIME_BREAK = 21
    FEED_TICKER = 22
    USER_SUSPENSION = 29
    USER_MERGE = 30
    USER_NAME_OR_AVATAR_CHANGE = 34


class Event(BaseModel):
    id: int
    room_id: int
    room_name: str


class BaseMessageEvent(Event):
    message_id: int
    user_id: int
    user_name: str

    parent_id: Optional[int] = None
    show_parent: Any = None  # idfk what this is
    target_user_id: int = 0

    message_stars: int = 0
    message_owner_stars: int = 0
    message_edits: int = 0


class DeleteEvent(BaseMessageEvent):
    event_type: Literal[EventType.DELETE]


class MessageEvent(BaseMessageEvent):
    event_type: Literal[EventType.MESSAGE]
    content: str


class EditEvent(MessageEvent):
    event_type: Literal[EventType.EDIT]


class MentionEvent(MessageEvent):
    event_type: Literal[EventType.MENTION]


class ReplyEvent(MessageEvent):
    event_type: Literal[EventType.REPLY]


class UnknownEvent(Event):
    event_type: EventType
    model_config = ConfigDict(extra="allow")


Events = DeleteEvent | MessageEvent | EditEvent | MentionEvent | ReplyEvent
_EventAdapter = TypeAdapter[Event](
    Annotated[Events, Field(discriminator="event_type")] | UnknownEvent
)
