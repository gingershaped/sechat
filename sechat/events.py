from typing import Annotated, Any, Literal, Optional
from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class EventType(IntEnum):
    """The possible events that chat can send, as listed in `master-chat.js`.
    
    Attributes:
        MessagePosted: A message was sent.
        MessageEdited: A message was edited.
        UserEntered: A user joined the room.
        UserLeft: A user left the room.
        RoomNameChanged: The room's visibility, name, tags, or description were changed.
        MessageStarred: A user starred a message.
        DebugMessage: Unknown. `master-chat.js` does not send or handle DEBUG events, and there is no known way to send
            them manually; however, they have appeared in GDPR datadumps containing information about room edits.
            Further research is needed.
        UserMentioned: A message was sent which mentions the current account by username.
        MessageFlagged: A spam flag was raised. Only recieved by users with 10k or more reputation.
        MessageDeleted: A message was deleted.
        FileAdded: Unknown. There is unused code in `master-chat.js` that seems to suggest it was possible at
            one point to upload arbitrary files to chat; this may be a relic from that feature.
        ModeratorFlag: A moderator flag was raised. Details unknown since normal users don't recieve this.
        UserSettingsChanged: This account's chat settings (such as muted users) were changed.
        GlobalNotification: Unknown.
        AccessLevelChanged: This account's access level was changed.
        UserNotification: Unknown.
        Invitation: Someone invited this account to a room.
        MessageReply: Someone replied to a message sent by this account.
        MessageMovedOut: A message was moved out of this room.
        MessageMovedIn: A message was moved into this room.
        TimeBreak: This room was placed in timeout by a room owner or moderator.
        FeedTicker: An RSS feed in ticker mode recieved a new event.
        UserSuspended: A user was suspended? Details unknown.
        UserMerged: User accounts were merged? Details unknown.
        UserNameOrAvatarChanged: A user's name or avatar was changed.
    """
    MessagePosted = 1
    MessageEdited = 2
    UserEntered = 3
    UserLeft = 4
    RoomNameChanged = 5
    MessageStarred = 6
    DebugMessage = 7
    UserMentioned = 8
    MessageFlagged = 9
    MessageDeleted = 10
    FileAdded = 11
    ModeratorFlag = 12
    UserSettingsChanged = 13
    GlobalNotification = 14
    AccessLevelChanged = 15
    UserNotification = 16
    Invitation = 17
    MessageReply = 18
    MessageMovedOut = 19
    MessageMovedIn = 20
    TimeBreak = 21
    FeedTicker = 22
    UserSuspended = 29
    UserMerged = 30
    UserNameOrAvatarChanged = 34


class Event(BaseModel):
    """An event recieved from chat.

    Attributes:
        id: The unique id of this event.
    """
    id: int

class RoomEvent(Event):
    """An event pertaining to a specific room.
    
    Attributes:
        room_id: The id of the room this event was recieved from.
        room_name: The name of the room this event was recieved from.
    """
    room_id: int
    room_name: str


class BaseMessageEvent(RoomEvent):
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

class MessageEvent(BaseMessageEvent):
    """A message was sent.
    
    Attributes:
        content: The content of the message, as a snippet of HTML.
    """
    event_type: Literal[EventType.MessagePosted]
    content: str


class EditEvent(MessageEvent):
    """A message was edited."""
    event_type: Literal[EventType.MessageEdited]


class MentionEvent(MessageEvent):
    """The bot was mentioned in a message.
    
    This event will be sent along with a MessageEvent if someone mentioned the bot in a message.
    """
    event_type: Literal[EventType.UserMentioned]

class DeleteEvent(BaseMessageEvent):
    """A message was deleted."""
    event_type: Literal[EventType.MessageDeleted]


class ReplyEvent(MessageEvent):
    """The bot was replied to.
    
    This event will be sent along with a MessageEvent if someone replied to a message sent by the bot.
    """
    event_type: Literal[EventType.MessageReply]


class UnknownEvent(Event):
    """
    An undocumented event.
    
    Instances of this class will have additional properties matching the JSON recieved by the library.

    Attributes:
        event_type: The type of the event.
    """

    event_type: EventType
    model_config = ConfigDict(extra="allow")


Events = MessageEvent | EditEvent | MentionEvent | DeleteEvent | ReplyEvent
EventAdapter = TypeAdapter[Event](
    Annotated[Events, Field(discriminator="event_type")] | UnknownEvent
)
