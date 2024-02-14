from typing import Any, Optional
from enum import Enum
from dataclasses import dataclass, field, InitVar
from datetime import datetime
from collections import defaultdict
from html import unescape

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
    USER_SUSPENSION = 29
    USER_MERGE = 30
    USER_NAME_OR_AVATAR_CHANGE = 34


class EventBase:
    pass


class UnknownEvent(EventBase):
    def __init__(self, **kwargs):
        self.eventType = EventType(kwargs["event_type"])
        self.args = kwargs


@dataclass
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
    parent_id: Optional[int] = None
    show_parent: Any = None  # idfk what this is
    target_user_id: int = 0
    message_stars: int = 0
    message_owner_stars: int = 0
    message_edits: int = 0

    def __post_init__(self, event_type, time_stamp):
        super().__post_init__(event_type, time_stamp)
        self.content = unescape(self.content)


@dataclass
class EditEvent(MessageEvent):
    pass

@dataclass
class MentionEvent(MessageEvent):
    pass
    
@dataclass
class ReplyEvent(MessageEvent):
    pass
    
@dataclass
class DeleteEvent(RoomEvent):
    user_id: int
    user_name: int
    message_id: int
    message_edits: int = 0
    target_user_id: Optional[int] = None
    parent_id: Optional[int] = None
    show_parent: bool = False

EVENT_CLASSES = defaultdict(
    lambda: UnknownEvent,
    {
        EventType.MESSAGE: MessageEvent,
        EventType.MENTION: MentionEvent,
        EventType.REPLY: ReplyEvent,
        EventType.EDIT: EditEvent,
        EventType.DELETE: DeleteEvent,
    },
)
