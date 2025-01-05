from importlib.metadata import version

from sechat.credentials import Credentials
from sechat.room import Room
from sechat.profile import Profile
from sechat.servers import Server
from sechat import events, errors

__version__ = version(__name__)
__all__ = ["Credentials", "Room", "Profile", "Server", "events", "errors"]
