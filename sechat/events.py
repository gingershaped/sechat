from typing import Annotated, Any, Literal, Optional
from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class EventType(IntEnum):
    """The possible events that chat can send, as listed in `master-chat.js`.
    
    Attributes:
        MESSAGE: A message was sent.
        EDIT: A message was edited.
        JOIN: A user joined the room.
        LEAVE: A user left the room.
        NAME_CHANGE: The room's visibility, name, tags, or description were changed.
        MESSAGE_STARRED: A user starred a message.
        DEBUG: Unknown. `master-chat.js` does not send or handle DEBUG events, and there is no known way to send
            them manually; however, they have appeared in GDPR datadumps containing information about room edits.
            Further research is needed.
        MENTION: A message was sent which mentions the current account by username.
        FLAG: A spam flag was raised. Only recieved by users with 10k or more reputation.
        DELETE: A message was deleted.
        FILE_UPLOAD: Unknown. There is unused code in `master-chat.js` that seems to suggest it was possible at
            one point to upload arbitrary files to chat; this may be a relic from that feature.
        MODERATOR_FLAG: A moderator flag was raised. Details unknown since normal users don't recieve this.
        SETTINGS_CHANGED: This account's chat settings (such as muted users) were changed.
        GLOBAL_NOTIFICATION: Unknown.
        ACCESS_CHANGED: The room's access settings were changed.
        USER_NOTIFICATION: Unknown.
        INVITATION: Someone invited this account to a room.
        REPLY: Someone replied to a message sent by this account.
        MESSAGE_MOVED_OUT: A message was moved out of this room.
        MESSAGE_MOVED_IN: A message was moved into this room.
        TIME_BREAK: This room was placed in timeout by a room owner or moderator.
        FEED_TICKER: An RSS feed in ticker mode recieved a new event.
        USER_SUSPENSION: A user was suspended? Details unknown.
        USER_MERGE: User accounts were merged? Details unknown.
        USER_NAME_OR_AVATAR_CHANGE: A user's name or avatar was changed.
    """
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
    """An event recieved from chat.

    Attributes:
        id: The unique id of this event.
        room_id: The id of the room this event was recieved from.
        room_name: The name of the room this event was recieved from.
    """
    id: int
    room_id: int
    room_name: str


class BaseMessageEvent(Event):
    """An action taken on a message.
    
    Attributes:
        message_id: The id of the message.
        user_id: The id of the user who triggered this event.
        user_name: The username of the user who sent the message.
        parent_id: Either the id of the message this message is replying to, or the id of the last message sent by the
            user this message mentions. This will be `None` if the message doesn't reply to another message
            or doesn't ping exactly one user.
        show_parent: The exact behavior of this property is unknown. It seems to be `true` if this message is replying
            to another message, and `None` otherwise.
        target_user_id: If this event was triggered by a moderator editing or deleting another user's message,
            this is the id of the user who sent the message. It is `None` otherwise.
        message_stars: The number of stars this message has.
        message_owner_stars: If this message is pinned this will be 1, otherwise it will be 0.
            It is unknown if it can be greater than 1.
        message_edits: The number of times this message has been edited.
    """
    message_id: int
    user_id: int
    user_name: str

    parent_id: Optional[int] = None
    show_parent: Optional[bool] = None
    target_user_id: Optional[int] = None

    message_stars: int = 0
    message_owner_stars: int = 0
    message_edits: int = 0


class DeleteEvent(BaseMessageEvent):
    """A message was deleted."""
    event_type: Literal[EventType.DELETE]


class MessageEvent(BaseMessageEvent):
    """A message was sent.
    
    Attributes:
        content: The content of the message, as a snippet of HTML.
    """
    event_type: Literal[EventType.MESSAGE]
    content: str


class EditEvent(MessageEvent):
    """A message was edited."""
    event_type: Literal[EventType.EDIT]


class MentionEvent(MessageEvent):
    """The bot was mentioned in a message.
    
    This event will be sent along with a MessageEvent if someone mentioned the bot in a message.
    """
    event_type: Literal[EventType.MENTION]


class ReplyEvent(MessageEvent):
    """The bot was replied to.
    
    This event will be sent along with a MessageEvent if someone replied to a message sent by the bot.
    """
    event_type: Literal[EventType.REPLY]


class UnknownEvent(Event):
    """
    An undocumented event.
    
    Instances of this class will have additional properties matching the JSON recieved by the library.

    Attributes:
        event_type: The type of the event.
    """

    event_type: EventType
    #: :meta private:
    model_config = ConfigDict(extra="allow")


Events = DeleteEvent | MessageEvent | EditEvent | MentionEvent | ReplyEvent
EventAdapter = TypeAdapter[Event](
    Annotated[Events, Field(discriminator="event_type")] | UnknownEvent
)
