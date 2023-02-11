from typing import Any, Generic, TypeVar, Literal, get_args
from collections.abc import Mapping
from enum import Enum
from dataclasses import dataclass, field, InitVar
from datetime import datetime
from collections import defaultdict

T = TypeVar("T", bound = "EventBase")
class EventType(Enum):
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
    USER_SUSPENSION = 23
    USER_MERGE = 24
    USER_NAME_OR_AVATAR_CHANGE = 25

EventTypeMember = Literal[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25] # >:(
assert set(get_args(EventTypeMember)) == {member.value for member in EventType}

V = TypeVar("V", bound = EventTypeMember)
@dataclass
class EventBase(Generic[V]):
    eventType: V = field(init = False)

class UnknownEvent(EventBase):
    def __init__(self, **kwargs):
        self.eventType = EventType(kwargs["event_type"])
        self.args = kwargs

class Event(EventBase):
    event_type: InitVar[int]
    time_stamp: InitVar[int]
    id: int
    timestamp: datetime = field(init=False)

    def __post_init__(self, event_type, time_stamp):
        self.eventType = EventType(event_type)
        self.timestamp = datetime.fromtimestamp(time_stamp)


@dataclass
class RoomEvent(Event):
    room_id: int
    room_name: int


@dataclass
class MessageEvent(RoomEvent):
    content: str
    message_id: int
    user_id: int
    user_name: str


@dataclass
class MentionEvent(MessageEvent):
    parent_id: int
    target_user_id: int


EVENT_CLASSES = defaultdict(lambda: UnknownEvent, {
    EventType.MESSAGE: MessageEvent,
    EventType.MENTION: MentionEvent
})
