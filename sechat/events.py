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


class UserEvent(RoomEvent):
    """An event with user information.

    Attributes:
        user_id: The id of the user who triggered this event.
        user_name: The username of the user who sent the message.
        target_user_id: The id of the user targeted by this event.
    """

    user_id: int
    user_name: str
    target_user_id: Optional[int] = None


class BaseMessageEvent(RoomEvent):
    """An action taken on a message.

    Attributes:
        message_id: The id of the message.
        parent_id: Either the id of the message this message is replying to, or the id of the last message sent by the
            user this message mentions. This will be `None` if the message doesn't reply to another message
            or doesn't ping exactly one user.
        show_parent: The exact behavior of this property is unknown. It seems to be `True` if this message is replying
            to another message, and `None` otherwise.
        message_stars: The number of stars this message has. This will be 0 if the message is not starred.
        message_owner_stars: The number of pins this message has. This will be 0 if the message is not pinned.
        message_edits: The number of times this message has been edited.
    """

    message_id: int
    parent_id: Optional[int] = None
    show_parent: Optional[bool] = None

    message_stars: int = 0
    message_owner_stars: int = 0
    message_edits: int = 0


class MessageEvent(BaseMessageEvent, UserEvent):
    """A message was sent.

    Attributes:
        content: The content of the message, as a snippet of HTML.
    """

    event_type: Literal[EventType.MessagePosted]
    content: str


class EditEvent(MessageEvent):
    """A message was edited."""

    event_type: Literal[EventType.MessageEdited]


class UserEnteredEvent(UserEvent):
    """A user joined this room."""

    event_type: Literal[EventType.UserEntered]


class UserLeftEvent(UserEvent):
    """A user left this room."""

    event_type: Literal[EventType.UserLeft]


class MessageStarredEvent(BaseMessageEvent):
    """Someone starred or pinned a message.

    [`message_stars`][sechat.events.BaseMessageEvent.message_stars] and
    [`message_owner_stars`][sechat.events.BaseMessageEvent.message_owner_stars] will reflect the new star count
    and pin state of this message. This event no longer includes information about who starred or pinned the message;
    see https://meta.stackexchange.com/q/229913/1116284.
    """

    event_type: Literal[EventType.MessageStarred]


class MentionEvent(MessageEvent):
    """The bot was mentioned in a message.

    This event will be sent along with a MessageEvent if someone mentioned the bot in a message.
    """

    event_type: Literal[EventType.UserMentioned]


class DeleteEvent(BaseMessageEvent, UserEvent):
    """A message was deleted."""

    event_type: Literal[EventType.MessageDeleted]


class AccessLevelChangedEvent(UserEvent):
    """A user's access level was changed.

    [`user_id`][sechat.events.UserEvent.user_id] and [`user_name`][sechat.events.UserEvent.user_name] will be the
    user id and username of the user which performed the change; [`target_user_id`][sechat.events.UserEvent.target_user_id]
    will be the user id of the user whose access level was changed.


    Attributes:
        content: A short string describing what change occured, which appears under certain conditions in
            chat's UI. TODO: Investigate what those conditions are, see also https://meta.stackexchange.com/q/402787/1116284
    """

    event_type: Literal[EventType.AccessLevelChanged]
    content: str


class InvitationEvent(UserEvent):
    """Someone invited this account to a room.

    [`room_id`][sechat.events.RoomEvent.room_id] and [`room_name`][sechat.events.RoomEvent.room_name] will be the
    id and name of the room this account was invited to join. [`user_id`][sechat.events.UserEvent.user_id]
    and [`user_name`][sechat.events.UserEvent.user_name] will be the user id and username of the user which
    sent the invite; [`target_user_id`][sechat.events.UserEvent.target_user_id] will be the user id of this account.

    Attributes:
        content: A snippet of HTML containing the human-readable invite message which would be shown as a notification
            in the chat client.
    """

    event_type: Literal[EventType.Invitation]
    content: str


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


Events = (
    MessageEvent
    | EditEvent
    | UserEnteredEvent
    | UserLeftEvent
    | MentionEvent
    | DeleteEvent
    | AccessLevelChangedEvent
    | InvitationEvent
    | ReplyEvent
)
EventAdapter = TypeAdapter[Event](
    Annotated[Events, Field(discriminator="event_type")] | UnknownEvent
)
