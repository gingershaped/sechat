from typing import Optional, cast, DefaultDict
from collections.abc import Mapping
from logging import Logger, getLogger
from pathlib import Path
from os import PathLike, makedirs
from asyncio import create_task, Task, wait_for, CancelledError
from http.cookies import Morsel
from functools import partial
from traceback import format_exception

import pickle


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
        useCookies: bool = True,
        logger: Optional[Logger] = None,
        cachePath: Optional[PathLike] = None,
    ):
        self.useCookies = useCookies
        if logger:
            self.logger = logger
        else:
            self.logger = getLogger("Bot")
        if cachePath:
            self.cachePath = Path(cachePath)
        else:
            self.cachePath = user_cache_path("sechat", None, __version__)
        makedirs(self.cachePath, exist_ok = True)

        self.cookieJar = CookieJar()
        self.session = ClientSession(cookie_jar = self.cookieJar)
        self.session.headers.update(
            {
                "User-Agent": f"Mozilla/5.0 (compatible; automated;) sechat/{__version__} (unauthenticated; +http://pypi.org/project/sechat)"
            }
        )

        self.roomTasks: dict[Room, Task] = {}
        self.rooms: dict[int, Room] = {}

    def loadCookies(self, email: str, cookies: CookieJar):
        cookiePath = self.cachePath / f"sechat_cookies_{md5(email.encode('utf-8')).hexdigest()}.dat"
        try:
            cookies.load(cookiePath)
        except FileNotFoundError:
            self.logger.debug("No cookies found :(")
        else:
            return True

    def dumpCookies(self, email: str, cookies: CookieJar):
        cookiePath = self.cachePath / f"sechat_cookies_{md5(email.encode('utf-8')).hexdigest()}.dat"
        cookies.save(cookiePath)
        self.logger.debug(f"Dumped cookies to {cookiePath}")

    async def getChatFkey(self) -> Optional[str]:
        async with self.session.get(
            "https://chat.stackexchange.com/chats/join/favorite"
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

    async def getChatUserId(self) -> Optional[int]:
        async with self.session.get(
            "https://chat.stackexchange.com/chats/join/favorite"
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

    async def scrapeFkey(self) -> Optional[str]:
        async with self.session.get(
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

    async def doSELogin(self, host: str, email: str, password: str, fkey: str) -> str:
        async with self.session.post(
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

    async def loadProfile(self, host: str, fkey: str, email: str, password: str):
        async with self.session.post(
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

    async def universalLogin(self, host: str):
        return await self.session.post(f"{host}/users/login/universal/request")

    def needsToLogin(self, email: str) -> bool:
        if self.useCookies:
            if self.loadCookies(email, self.cookieJar):
                self.cookieJar._do_expiration()
                return "acct" not in self.cookieJar._cookies.get(("stackexchange.com", "/"), {})
        return True

    async def authenticate(self, email: str, password: Optional[str], host: str):
        if self.useCookies:
            if self.loadCookies(email, self.cookieJar):
                self.logger.debug("Loaded cookies")
        self.cookieJar._do_expiration()
        if "acct" not in self.cookieJar._cookies.get(("stackexchange.com", "/"), {}):
            assert password is not None, "Cookie expired, must supply password!"
            self.logger.debug("Logging into SE...")
            self.logger.debug("Acquiring fkey...")
            fkey = await self.scrapeFkey()
            if not fkey:
                raise LoginError("Failed to scrape site fkey.")
            self.logger.debug(f"Acquired fkey: {fkey}")
            self.logger.info(f"Logging into {host}...")
            result = await self.doSELogin(host, fkey, email, password)
            if result != "Login-OK":
                raise LoginError(f"Site login failed!", result)
            self.logger.debug(f"Logged into {host}!")
            self.logger.debug("Loading profile...")
            await self.loadProfile(host, fkey, email, password)
            self.logger.debug("Loaded SE profile!")
            self.logger.debug("Logging into the rest of the network...")
            await self.universalLogin(host)
            if self.useCookies:
                self.logger.debug("Dumping cookies...")
                self.dumpCookies(email, self.cookieJar)

        self.fkey = await self.getChatFkey()
        self.userID = await self.getChatUserId()
        if not self.fkey or not self.userID:
            raise LoginError("Login failed. Bad email/password?")
        self.logger.debug(f"Chat fkey is {self.fkey}, user ID is {self.userID}")
        self.logger.info(f"Logged into {host}!")
        self.session.headers.update(
            {
                "User-Agent": f"Mozilla/5.0 (compatible; automated;) sechat/{__version__} (logged in as user {self.userID}; +http://pypi.org/project/sechat)"
            }
        )

    def _rejoinRoom(self, room: Room, task: Task):
        try:
            if task.exception() != None:
                self.logger.warning(f"Task for room {room.roomID} exited abnormally. Restarting it.")
                self.logger.warning("Error:")
                [self.logger.warning(l) for l in format_exception(task.exception())]
                task = create_task(room.loop(), name=room.logger.name)
                task.add_done_callback(partial(self._rejoinRoom, room))
                self.roomTasks[room] = task
        except CancelledError:
            pass


    async def joinRoom(self, roomID: int, logger: Optional[Logger] = None) -> Room:
        self.logger.info(f"Joining room {roomID}")
        if not self.fkey or not self.userID:
            raise RuntimeError("Not logged in")
        room = Room(self.cookieJar, self.fkey, self.userID, roomID, logger)
        task = create_task(room.loop(), name=room.logger.name)
        task.add_done_callback(partial(self._rejoinRoom, room))
        self.rooms[roomID] = room
        self.roomTasks[room] = task
        await room._connectedEvent.wait()
        return room

    def leaveRoom(self, roomID: int):
        self.logger.info(f"Leaving room {roomID}")
        room = self.rooms[roomID]
        self.rooms.pop(roomID)
        task = self.roomTasks.pop(room)
        task.cancel()

    def leaveAllRooms(self):
        [self.leaveRoom(room) for room in list(self.rooms.keys())]

    async def shutdown(self):
        self.logger.info("Shutting down...")
        wait_for(self.session.close(), 3)
        self.logger.debug("Shutdown completed!")
