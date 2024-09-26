from typing import Optional, cast
from logging import Logger, getLogger
from pathlib import Path
from os import PathLike, makedirs
from asyncio import create_task, Task
from functools import partial

from platformdirs import user_cache_path
from aiohttp import ClientSession, CookieJar
from bs4 import BeautifulSoup, Tag
from hashlib import md5

from sechat.room import Room
from sechat.errors import LoginError
from sechat.version import __version__


class Bot:
    def __init__(
        self,
        server: str = 'chat.stackexchange.com',
        useCookies: bool = True,
        logger: Optional[Logger] = None,
        cachePath: Optional[PathLike] = None,
    ):
        self.server = server
        self.useCookies = useCookies
        if logger:
            self.logger = logger
        else:
            self.logger = getLogger("Bot")
        if cachePath:
            self.cachePath = Path(cachePath)
        else:
            self.cachePath = user_cache_path("sechat", None, __version__)
        makedirs(self.cachePath, exist_ok=True)

        self.cookieJar = CookieJar()
        self.roomTasks: dict[Room, Task] = {}
        self.rooms: dict[int, Room] = {}

    def loadCookies(self, email: str, cookies: CookieJar):
        cookiePath = (
            self.cachePath
            / f"sechat_cookies_{md5(email.encode('utf-8')).hexdigest()}.dat"
        )
        try:
            cookies.load(cookiePath)
        except FileNotFoundError:
            self.logger.debug("No cookies found :(")
        else:
            return True

    def dumpCookies(self, email: str, cookies: CookieJar):
        cookiePath = (
            self.cachePath
            / f"sechat_cookies_{md5(email.encode('utf-8')).hexdigest()}.dat"
        )
        cookies.save(cookiePath)
        self.logger.debug(f"Dumped cookies to {cookiePath}")

    async def getChatFkey(self, session: ClientSession) -> Optional[str]:
        async with session.get(
            f"https://{self.server}/chats/join/favorite"
        ) as response:
            soup = BeautifulSoup(
                await response.text(),
                "html.parser",
            )
            if content := soup.find(id="content"):
                if form := cast(Tag, content).form:
                    if fkeyInput := form.find("input", attrs={"name": "fkey"}):
                        if fkey := cast(Tag, fkeyInput).get("value"):
                            if isinstance(fkey, list):
                                return "".join(fkey)
                            return fkey

    async def getChatUserId(self, session: ClientSession) -> Optional[int]:
        async with session.get(
            f"https://{self.server}/chats/join/favorite"
        ) as response:
            soup = BeautifulSoup(
                await response.text(),
                "html.parser",
            )
            if links := soup.find(class_="topbar-menu-links"):
                if link := cast(Tag, links).find("a"):
                    if href := cast(Tag, link).get("href"):
                        if isinstance(href, list):
                            href = "".join(href)
                        return int(href.split("/")[2])

    async def scrapeFkey(self, session: ClientSession) -> Optional[str]:
        async with session.get(
            "https://meta.stackexchange.com/users/login"
        ) as response:
            soup = BeautifulSoup(
                await response.text(),
                "html.parser",
            )
            if fkeyTag := soup.find(attrs={"name": "fkey"}):
                fkey = cast(Tag, fkeyTag)["value"]
                if isinstance(fkey, list):
                    return "".join(fkey)
                return fkey

    async def doSELogin(
        self, session: ClientSession, host: str, email: str, password: str, fkey: str
    ) -> str:
        async with session.post(
            f"{host}/users/login-or-signup/validation/track",
            data={
                "email": email,
                "password": password,
                "fkey": fkey,
                "isSignup": "false",
                "isLogin": "true",
                "isPassword": "false",
                "isAddLogin": "false",
                "hasCaptcha": "false",
                "ssrc": "head",
                "submitButton": "Log in",
            },
        ) as response:
            return await response.text()

    async def loadProfile(
        self, session: ClientSession, host: str, fkey: str, email: str, password: str
    ):
        async with session.post(
            f"{host}/users/login",
            params={"ssrc": "head", "returnurl": f"{host}"},
            data={
                "email": email,
                "password": password,
                "fkey": fkey,
                "ssrc": "head",
            },
        ) as response:
            soup = BeautifulSoup(
                await response.text(),
                "html.parser",
            )
            if head := soup.head:
                if title := head.title:
                    if titleString := title.string:
                        if "Human verification" in titleString:
                            raise LoginError(
                                "Failed to load SE profile: Caught by captcha. (It's almost like I'm not human!) Wait around 5min and try again."
                            )
                        else:
                            return
            raise LoginError(
                "Failed to load SE profile: Unable to ascertain success state."
            )

    async def universalLogin(self, session: ClientSession, host: str):
        return await session.post(f"{host}/users/login/universal/request")

    def needsToLogin(self, email: str) -> bool:
        if self.useCookies:
            if self.loadCookies(email, self.cookieJar):
                self.cookieJar._do_expiration()
                return "acct" not in self.cookieJar._cookies.get(
                    ("stackexchange.com", "/"), {}
                )
        return True

    async def authenticate(self, email: str, password: Optional[str], host: str):
        if self.useCookies:
            if self.loadCookies(email, self.cookieJar):
                self.logger.debug("Loaded cookies")
        self.cookieJar._do_expiration()
        async with ClientSession() as session:
            session.headers.update(
                {
                    "User-Agent": f"Mozilla/5.0 (compatible; sechat/{__version__}; +http://pypi.org/project/sechat)"
                }
            )
            if "acct" not in self.cookieJar._cookies.get(("stackexchange.com", "/"), {}):
                assert password is not None, "Cookie expired, must supply password!"
                self.logger.debug("Logging into SE...")
                self.logger.debug("Acquiring fkey...")
                fkey = await self.scrapeFkey(session)
                if not fkey:
                    raise LoginError("Failed to scrape site fkey.")
                self.logger.debug(f"Acquired fkey: {fkey}")
                self.logger.info(f"Logging into {host}...")
                result = await self.doSELogin(session, host, fkey, email, password)
                if result != "Login-OK":
                    raise LoginError(f"Site login failed!", result)
                self.logger.debug(f"Logged into {host}!")
                self.logger.debug("Loading profile...")
                await self.loadProfile(session, host, fkey, email, password)
                self.logger.debug("Loaded SE profile!")
                self.logger.debug("Logging into the rest of the network...")
                await self.universalLogin(session, host)
                if self.useCookies:
                    self.logger.debug("Dumping cookies...")
                    self.dumpCookies(email, self.cookieJar)

            self.fkey = await self.getChatFkey(session)
            self.userID = await self.getChatUserId(session)
            if not self.fkey or not self.userID:
                raise LoginError("Login failed. Bad email/password?")
            self.logger.debug(f"Chat fkey is {self.fkey}, user ID is {self.userID}")
            self.logger.info(f"Logged into {host}!")

    def _roomExited(self, room: Room, task: Task):
        if (e := task.exception()) != None:
            print(e)
            self.logger.critical(
                f"An exception occured in the task of room {room.roomID}"
            )

    async def joinRoom(self, roomID: int, logger: Optional[Logger] = None) -> Room:
        self.logger.info(f"Joining room {roomID}")
        if not self.fkey or not self.userID:
            raise RuntimeError("Not logged in")
        assert roomID not in self.rooms, "Already in room"
        room = Room(self.server, self.cookieJar, self.fkey, self.userID, roomID, logger)
        task = create_task(room.loop(), name=room.logger.name)
        task.add_done_callback(partial(self._roomExited, room))
        self.rooms[roomID] = room
        self.roomTasks[room] = task
        await room._connectedEvent.wait()
        return room

    async def closeRoom(self, roomID: int):
        self.logger.info(f"Leaving room {roomID}")
        task = self.roomTasks.pop(self.rooms.pop(roomID))
        task.cancel()
        await task

    async def closeAllRooms(self):
        [await self.closeRoom(room) for room in list(self.rooms.keys())]
