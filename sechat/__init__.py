import websocket
import threading
import time
import requests
import pickle
import os
import logging
import json
import atexit
from tempfile import gettempdir
from bs4 import BeautifulSoup
from . import errors
from .events import Events
from collections import namedtuple
from hashlib import sha256

'''A BETTER Stack Exchange chat module.'''

class Room:
  def __init__(self, parent, roomID, logRequestErrors = False):
    '''A room which the bot is in. You should never create this classs by hand!'''
    self.session = parent.session
    self.fkey = parent.fkey
    self.userID = parent.userID
    self.roomID = roomID
    self.logRequestErrors = logRequestErrors
    self.cooldown = 2
    
    self.logger = logging.getLogger("Room-" + str(self.roomID))
    self.thread = None
    self.socket = None
    self.running = False
    self.handlers = {}
    self.internalHandlers = {
      Events.REPLY.value: self._replyHandler,
      Events.MENTION.value: self._replyHandler
    }
    self.lastPing = 0

    self.connect()

  def __del__(self):
    self.halt()

  def halt(self, join = True):
    '''Shut down the room.

      :param join: Wait until the room's thread has stopped.
      :type join: bool
    '''
    if self.running:
      self.running = False
      if join:
        self.thread.join()

  def connect(self):
    '''Connect to the SEChat remote room. Gets called automagically in __init__ so you shouldn't need to use it much.'''
    self.logger.info("Connecting...")
    try:
      r = self.session.post(
        "https://chat.stackexchange.com/ws-auth",
        data = {
          "fkey": self.fkey,
          "roomid": self.roomID
        }
      ).json()
    except Exception as e:
      raise errors.ConnectionError("Unable to authenticate") from e
    target = r["url"]+"?l={}".format(int(time.time()))
    try:
      self.socket = websocket.create_connection(target, origin="http://chat.stackexchange.com", timeout = 2)
    except Exception as e:
      raise errors.ConnectionError("Failed to connect to socket") from e
    self.logger.info("Connected!")
    self.lastPing = time.time()
    if not self.running:
      self.logger.debug("Starting thread...")
      self.thread = threading.Thread(target = self.run, daemon = True)
      self.thread.start()


  def getRecentMessages(self, since = 0, mode = "Messages", count = 100):
    '''Gets a list of recent messages.

      :param since: Message ID (?) to start getting messages from. Use 0 for as far back as possible.
      :type since: int
      :param count: Number of messages to get.
      :type count: int
      
      :return: A list of messsages
      :rtype: list
    '''
    try:
      r = self.session.post(
        "https://chat.stackexchange.com/chats/{}/events"
          .format(self.roomID),
        data = {
          "since": since,
          "mode": mode,
          "count": count,
          "fkey": self.fkey
        },
        headers = {
          'Referer': 'https://chat.stackexchange.com/rooms/{}'
              .format(self.roomID)
        }
      ).json()
    except Exception as e:
      self.logger.error("Error fetching recent messages:")
      return []
    else:
      return r["events"]


  def run(self):
    self.running = True
    self.logger.debug("Thread started!")
    while self.running:
      try:
        data = self.socket.recv()
      except websocket.WebSocketTimeoutException:
        continue
      except websocket.WebSocketConnectionClosedException:
        self.logger.warning("Connection closed, attempting to reconnect")
        self.connect()
      except Exception as e:
        self.logger.critical("Unexpected error")
        raise errors.ConnectionError from e
      if data is not None and data != "":
        try:
          data = json.loads(data)
        except json.JSONDecodeError:
          self.logger.warning("Recived corrupted data: " + data)
        else:
          self.lastPing = time.time()
          self.process(data)
      if time.time() - self.lastPing > 60:
        self.logger.warning("Connection likely dropped, reconnecting...")
        self.socket.close()
        self.connect()
    self.logger.info("Shutting down...")
    self.session.post(
      "https://chat.stackexchange.com/chats/leave/"
      + str(self.roomID),
      data = {
        "quiet": True,
        "fkey": self.fkey
      }
    )
    self.socket.close()

  def process(self, data):
    if "r" + str(self.roomID) in data:
      data = data["r" + str(self.roomID)]
      if data != {}:
        if "e" in data:
          for event in data["e"]:
            self.logger.debug("Got event: " + str(event))
            try:
              self.handle(event["event_type"], event)
            except:
              self.handle(Events.SECHAT_ERROR, event["event_type"], self._defaultOnHandlerErrorHandler)

  def on(self, event, callback):
    '''Add an event listener.

      :param event: The event to listen for.
      :type event: sechat.events.EventType
      :param callback: The callback function. Gets a namedtuple with all the event data as its only parameter.
      :type callback: function

      :raises ValueError: If there is an event handler already registered for that event type or the event type is unknown.
    '''
    if event in Events:
      if event.value not in self.handlers:
        self.handlers[event.value] = callback
      else:
        raise ValueError("Handler already registered for event " + Events(event).name)
    else:
      raise ValueError("Unknown event type: " + str(event))

  def off(self, event):
    '''Remove an event listener.

      :param event: The event type of the handler to remove.
      :type event: sechat.events.EventType
    '''
    if event in Events:
      if event.value in self.handlers:
        self.handlers.pop(event.value)
      else:
        raise ValueError("No handler for event " + Events(event).name)
    else:
      raise ValueError("Unknown event type: " + str(event))

  def handle(self, event, data, default = None):
    if event in self.internalHandlers:
      self.internalHandlers[event](data)
    if event in self.handlers:
      t = namedtuple("Event", data.keys())
      self.handlers[event](t(**data))
    elif default:
      default(data)

  def _defaultOnHandlerErrorHandler(self, event):
    self.logger.exception("An error occured in the handler for event " + Events(event).name)

  def _replyHandler(self, event):
    self.session.post(
      "https://chat.stackexchange.com/messages/ack",
      data = {
        "id": event["message_id"],
        "fkey": self.fkey
      }
    )


  def processTooFast(self, func, handleTooFast):
    try:
      r = func()
    except Exception as e:
      if self.logRequestErrors:
        self.logger.exception("An error occured in function " + repr(func))
      return
    if r.text.startswith("You can perform this action again"):
      if handleTooFast:
        time.sleep(self.cooldown)
        self.cooldown = self.cooldown ** 2
        return self.processTooFast(func, handleTooFast)
      else:
        raise errors.TooFastError
    elif r.text.startswith("It is too late"):
      return False
    else:
      self.cooldown = 2
      return r

  def bookmark(self, start, end, title):
    '''Bookmark a conversation.

      :param start: The ID of the first message in the conversation.
      :type start: int
      :param end: The ID of the last message in the conversation.
      :type end: int
      :param title: The title of the conversation.
      :type title: str
    '''
    self.logger.info(
      "Bookmarking conversation \"{0}\" (from {1} to {2})"
        .format(title, start, end)
    )
    self.session.post(
      "https://chat.stackexchange.com/conversation/new",
      data = {
        "roomId": self.roomID,
        "firstMessageId": start,
        "lastMessageId": end,
        "title": title,
        "fkey": self.fkey
      },
      headers = {
        'Referer': 'https://chat.stackexchange.com/rooms/{}'
          .format(self.roomID)
      }
    )

  def clearBookmark(self, title):
    '''Clear a bookmark.

      :param title: The title of the bookmark to delete.
      :type title: str
    '''
    self.logger.info(
      "Clearing bookmark {0}"
        .format(title)
    )
    self.session.post(
      "https://chat.stackexchange.com/conversation/delete/{0}/{1}"
      .format(self.roomID, title),
      data = {
        "fkey": self.fkey
      }
    )
      
  def send(self, message, handleTooFast = True):
    '''Send a message.

      :param message: The message to send.
      :type message: str
      :param handleTooFast: Whether or not to wait if the message cooldown is triggered.
      :type handleTooFast: bool

      :return: The ID of the message that was just sent.
      :rtype: int
    '''
    self.logger.info("Sending message: " + message)
    return self.processTooFast(
        lambda: self.session.post(
          "https://chat.stackexchange.com/chats/{}/messages/new"
            .format(self.roomID),
          data = {
            "fkey": self.fkey,
            "text": message
          },
          headers = {
            'Referer': 'https://chat.stackexchange.com/rooms/{}'
              .format(self.roomID),
            'Origin': 'https://chat.stackexchange.com'
          }  
      ),
      handleTooFast
    ).json()["id"]
  def buildReply(self, target, message):
    '''Convenience function for making a reply.

        :param target: The user ID of the person to reply to.
        :type target: int
        :param message: The message to send in reply.
        :type message: str

        :return: The message with reply (pass this to send)
        :rtype: str
    '''
    return ":" + str(target) + " " + message
  def edit(self, target, newMessage, handleTooFast = True):
    '''Edit a message.

        :param target: The message ID to edit.
        :type target: int
        :param newMessage: The text to replace the message with.
        :type newMessage: str
        :param handleTooFast: Whether or not to wait if the editing cooldown is triggered.
        :type handleTooFast: bool
    '''
    self.logger.info(
      "Editing message {0} to: {1}"
        .format(target, newMessage)
    )
    self.processTooFast(
      lambda: self.session.post(
        "https://chat.stackexchange.com/messages/{}"
          .format(target),
        data = {
          "text": newMessage,
          "fkey": self.fkey
        },
        headers = {
          'Referer': 'https://chat.stackexchange.com/rooms/{}'
            .format(self.roomID)
        }
      ),
      handleTooFast
    )
  def delete(self, id, handleTooFast = True):
    '''Delete a message.

        :param id: The message ID to delete.
        :type id: int
        :param handleTooFast: Whether or not to wait if the deleting cooldown is triggered.
        :type handleTooFast: bool
    '''
    self.logger.info(
      "Deleting message {}"
        .format(id)
    )
    self.processTooFast(
      lambda: self.session.post(
        "https://chat.stackexchange.com/messages/{}/delete"
          .format(id),
        data = {
          "fkey": self.fkey
        },
        headers = {
          'Referer': 'https://chat.stackexchange.com/rooms/{}'
            .format(self.roomID)
        }
      ),
      handleTooFast
    )
  def star(self, id, handleTooFast = True):
    '''Star a message.

        :param id: The message ID to star.
        :type id: int
        :param handleTooFast: Whether or not to wait if the starring cooldown is triggered.
        :type handleTooFast: bool
    '''
    self.logger.info(
      "Starring {0}"
        .format(id)
    )
    self.processTooFast(
      lambda: self.session.post(
        "https://chat.stackexchange.com/messages/{}/star"
          .format(id),
        data = {
          "fkey": self.fkey
        },
        headers = {
          'Referer': 'https://chat.stackexchange.com/rooms/{}'
            .format(self.roomID)
        }
      ),
      handleTooFast
    )
  def pin(self, id, handleTooFast = True):
    '''Pin a message.

        :param id: The message ID to pin.
        :type id: int
        :param handleTooFast: Whether or not to wait if the pinning cooldown is triggered.
        :type handleTooFast: bool
    '''
    self.logger.info(
      "Pinning {0}"
        .format(id)
    )
    self.processTooFast(
      lambda: self.session.post(
        "https://chat.stackexchange.com/messages/{}/owner-star"
          .format(id),
        data = {
          "fkey": self.fkey
        },
        headers = {
          'Referer': 'https://chat.stackexchange.com/rooms/{}'
            .format(self.roomID)
        }
      ),
      handleTooFast
    )
  def unpin(self, id, handleTooFast = True):
    '''Unpin a message.

        :param id: The message ID to unpin.
        :type id: int
        :param handleTooFast: Whether or not to wait if the unpinning cooldown is triggered.
        :type handleTooFast: bool
    '''
    self.logger.info(
      "Unpinning {0}"
        .format(id)
    )
    self.processTooFast(
      lambda: self.session.post(
        "https://chat.stackexchange.com/messages/{}/unowner-star"
          .format(id),
        data = {
          "fkey": self.fkey
        },
        headers = {
          'Referer': 'https://chat.stackexchange.com/rooms/{}'
            .format(self.roomID)
        }
      ),
      handleTooFast
    )
  def clearStars(self, id, handleTooFast = True):
    '''Clear stars on a message.

        :param id: The message ID to clear stars on.
        :type id: int
        :param handleTooFast: Whether or not to wait if the clearing-stars-on-messages cooldown is triggered.
        :type handleTooFast: bool
    '''
    self.logger.info(
      "Clearing stars on {0}"
        .format(id)
    )
    self.processTooFast(
      lambda: self.session.post(
        "https://chat.stackexchange.com/messages/{}/unstar"
          .format(id),
        data = {
          "fkey": self.fkey
        },
        headers = {
          'Referer': 'https://chat.stackexchange.com/rooms/{}'
            .format(self.roomID)
        }
      ),
      handleTooFast
    )
  def move(self, ids, target):
    '''Move a group of messages.

        :param ids: A list of message IDs to move.
        :type ids: list
        :param target: The room ID to move the messages to.
        :type target: int
    '''
    if type(ids) != list:
      ids = [ids]
    self.logger.info(
      "Moving messages {0} to {1}"
        .format(
          ", ".join([str(i) for i in ids]),
          target
        )
    )
    self.session.post(
      "https://chat.stackexchange.com/admin/movePosts/{}"
        .format(self.roomID),
      data = {
        "ids": ",".join([str(i) for i in ids]),
        "to": target,
        "fkey": self.fkey
      },
      headers = {
        'Referer': 'https://chat.stackexchange.com/rooms/{}'
          .format(self.roomID),
        'Origin': 'https://chat.stackexchange.com'
      }
    )

class Bot:
  def __init__(self, logger = None, useCookies = True):
    '''A Stack Exchange chat client/bot.

        :param logger: A custom logger to use (if None the bot will make its own logger)
        :type logger: logging.Logger
        :param useCookies: Whether to use cookies to keep the bot's login tokens stored. HIGHLY recommended (both to avoid ratelimiting and for quick startup).
        :type useCookies: bool
    '''
    self.useCookies = useCookies
    self.logger = logger if logger else logging.getLogger("Bot")
    
    self.session = requests.Session()
    self.fkey = None
    self.chatID = None
    self.userID = None
    self.rooms = {}

    atexit.register(self.leaveAllRooms, True)
    

  def login(self, email, password, host="codegolf.stackexchange.com"):
    '''Log in to Stack Exchange and SE Chat. Uses cookies if self.useCookies is True.

        :param email: The email address of the account to log in to.
        :type email: str
        :param password: The password of the account to log in to.
        :type password: str
        :param host: The host SE site to log in to. You should probably set this to the bot's "parent site" on SEChat. This MUST be a site that the bot has an account on, otherwise the login process will fail.
        :type host: str

        :raises sechat.errors.FutureError: If the bot can't get an fkey from the openID login page
        :raises sechat.errors.LoginError: If the login fails
    '''
    self._COOKIEPATH = gettempdir() + "/sechat_cookies_" + sha256(email.encode("utf-8")).hexdigest() + ".dat"
    if self.useCookies:
      l = logging.getLogger("CookieManager")
      l.debug("Loading cookies...")
      try:
        f = open(self._COOKIEPATH, "rb")
      except FileNotFoundError:
        l.debug("No cookies found")
      else:
        try:
          self.session.cookies.update(pickle.load(f))
        except Exception as e:
          l.warning("Error loading cookies: " + str(e))
        else:
          l.debug("Success!")
          self.session.cookies.clear_expired_cookies()
    if not "acct" in dict(self.session.cookies):
      self.logger.info("Logging in to " + host)
      self.logger.debug("Getting fkey...")
      fkey = BeautifulSoup(
        self.session.get(
          "https://openid.stackexchange.com/account/login"
        ).text,
        "html.parser"
      ).form.find(attrs={"name": "fkey"})["value"]
      if fkey == None:
        raise errors.FutureError(
          "Unable to extract fkey from login page, are you using this in the future?"
        )
      self.logger.debug("Got fkey: " + fkey)
      self.logger.debug("Logging in to Stack Exchange...")
      r = self.session.post(
        "https://{}/users/login-or-signup/validation/track".format(host),
        data = {
          "email": email,
          "password": password,
          "fkey": fkey,
          "isSignup": "false",
          "isLogin": "true",
          "isPassword": "false",
          "isAddLogin": "false",
          "hasCaptcha": "false",
          "ssrc": "head",
          "submitButton": "Log in"
        }
      )
      if r.text == "Login-OK":
        self.logger.debug("Logged in to Stack Exchange!")
      else:
        raise errors.LoginError(
          "Failed to log in to Stack Exchange"
        )
      self.logger.debug("Loading profile...")
      s = BeautifulSoup(
        self.session.post(
          "https://{0}/users/login?ssrc=head&returnurl=https%3a%2f%2f{0}%2f"
            .format(host),
          data = {
            "email": email,
            "password": password,
            "fkey": fkey,
            "ssrc": "head"
          }
        ).text,
        "html.parser"
      )
      if "Human verification" in s.head.title.string:
        raise errors.LoginError(
          "Failed to load SE profile: Caught by captcha. (It's almost like I'm not human!) Wait around 5min and try again."
        )
      self.logger.debug("Loaded SE profile!")
      self.logger.debug("Logging in to other sites...")
      self.session.post(
        "https://{}/users/login/universal/request"
          .format(host)
      )
      if self.useCookies:
        l = logging.getLogger("CookieManager")
        l.debug("Dumping cookies...")
        with open(self._COOKIEPATH, "wb") as f:
          pickle.dump(self.session.cookies, f)
        l.debug("Success!")
    self.logger.debug("Getting chat fkey...")
    r = BeautifulSoup(
      self.session.get(
        "https://chat.stackexchange.com/chats/join/favorite"
      ).text,
      "html.parser"
    )
    self.fkey = r.find(id="content").form.find("input", attrs={"name": "fkey"})["value"]
    print(r.find(class_="topbar-menu-links").find("a")["href"].split("/")[2])
    try:
      self.userID = int(r.find(class_="topbar-menu-links").find("a")["href"].split("/")[2])
    except ValueError:
      raise errors.LoginError(
        "Login failed. Bad email/password?"
      ) from None
    self.logger.debug("Got chat fkey: " + self.fkey)
    self.logger.info("Logged in to chat successfully!")

  def joinRoom(self, roomID):
    '''Join a room.

        :param roomID: The ID of the room to join.
        :type roomID: str

        :return: The room instance.
        :rtype: sechat.Room
    '''
    room = Room(self, roomID)
    self.rooms[roomID] = room
    return room

  def leaveRoom(self, roomID, wait = False):
    '''Leave a room. The behavior of the corresponding Room instance afer this message is called is undefined.

        :param roomID: The room ID to leave.
        :type roomID: str
        :param wait: If True, sechat guarantees that the room's thread will be stopped when the method returns.
        :type wait: bool

        :raises ValueError: If the bot is not in the room in question.
    '''
    self.logger.info(
      "Leaving room {}"
        .format(roomID)
    )
    try:
      self.rooms[roomID].halt(wait)
      self.rooms.pop(roomID)
    except KeyError:
      raise ValueError("Not in room " + roomID) from None

  def leaveAllRooms(self, wait = False):
    '''Leave all rooms. Works the same as leaveRoom but for all rooms.
        :param wait: See above.
        :type wait: bool
    '''
    self.logger.info("Leaving all rooms...")
    toDel = []
    for room in self.rooms:
      self.rooms[room].halt(False)
      toDel.append(room)
    if wait:
      for room in self.rooms:
        self.rooms[room].thread.join()
    for room in toDel:
      self.rooms.pop(room)
    self.session.post(
      "https://chat.stackexchange.com/chats/leave/all",
      data = {
        "fkey": self.fkey
      }
    )

  def logout(self):
    '''Log out from Stack Exchange. Clears cookies if self.useCookies is True. You probably DO NOT want to call this when your application exits, call leaveAllRooms instead. (Although the rooms should auto-shutdown, it's best to be sure.)'''
    self.logger.info("Logging out...")
    self.leaveAllRooms(True)
    self.session.post("https://openid.stackexchange.com/account/logout")
    if self.useCookies:
      l = logging.getLogger("CookieManager")
      l.debug("Clearing cookies...")
      os.remove(self._COOKIEPATH)
      l.debug("Done!")
    self.logger.info("Logged out successfully.")
