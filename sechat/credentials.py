import pickle
from dataclasses import dataclass
from http.cookies import Morsel
from logging import getLogger
from os.path import exists
from typing import TYPE_CHECKING, Optional, cast

from aiohttp import ClientSession, CookieJar
from bs4 import BeautifulSoup, Tag
from yarl import URL

from sechat.errors import LoginError
from sechat.servers import Server

if TYPE_CHECKING:
    from _typeshed import FileDescriptorOrPath

LOGIN_HOST = URL("https://meta.stackexchange.com")
COOKIE_ROOT = "stackexchange.com"
USER_AGENT = "Mozilla/5.0 (compatible; sechat/2.0.0; +https://pypi.org/project/sechat)"
logger = getLogger(__name__)


@dataclass
class Credentials:
    """Cookies necessary to interact with Stack Exchange chat.
    
    It is strongly recommended to use [`load_or_authenticate`][sechat.Credentials.load_or_authenticate]
    instead of just [`authenticate`][sechat.Credentials.authenticate], because Stack Exchange will present
    you with a CAPTCHA if you make too many login attempts in too short of a time. `load_and_authenticate`
    will save your cookies to a file and try to reuse them instead of logging in again
    and risking tripping the CAPTCHA, and automatically handles expiration and fetching new credentials.

    Attributes:
        server: The chat instance these cookies are valid for.
        prov: The `prov` cookie returned by logging into Meta Stack Exchange.
        acct: The `acct` cookie returned by logging into Meta Stack Exchange.
        chatusr: The `sechatusr` (chat.stackexchange.com) or `chatusr` (the other two chat servers) cookie
            returned by logging into chat.
        user_id: The ID of the chat account (not main site account!) these cookies are for.
    """

    server: Server
    prov: Morsel[str]
    acct: Morsel[str]
    chatusr: Morsel[str]
    user_id: int

    @property
    def _cookies(self):
        return {
            "acct": self.acct,
            "prov": self.prov,
            (
                "sechatusr" if self.server == Server.STACK_EXCHANGE else "chatusr"
            ): self.chatusr,
        }

    @property
    def _headers(self):
        return {"User-Agent": USER_AGENT, "Referer": self.server}

    def _session(self):
        return ClientSession(self.server, cookies=self._cookies, headers=self._headers)

    @staticmethod
    async def _scrape_fkey(session: ClientSession):
        async with session.get("/chats/join/favorite") as response:
            soup = BeautifulSoup(await response.read(), "lxml")
            assert isinstance(fkey_input := soup.find(id="fkey"), Tag)
            assert isinstance(fkey := fkey_input.attrs["value"], str)
        return fkey

    @staticmethod
    async def authenticate(
        email: str, password: str, *, server: Server = Server.STACK_EXCHANGE
    ) -> "Credentials":
        """Log into a chat server.
        
        Every time this function is called it will perform the entire login process again, which will
        trigger a CAPTCHA if done too many times. Unless you need complete control over the login process,
        use [`load_or_authenticate`][sechat.Credentials.load_or_authenticate] instead.
        The account **must have 20 reputation to use chat**.

        Args:
            email: The email address of the account to log into.
            password: The password of the account to log into.
            server: The chat server to log into.
        
        Returns:
            A new `Credentials` instance that can be used to join chatrooms.

        Raises:
            LoginError: Something went wrong while logging in, most likely a CAPTCHA or incorrect credentials.
        """
        
        logger.info(f"Logging into {server}")
        chat_user_cookie = "sechatusr" if server == Server.STACK_EXCHANGE else "chatusr"

        async with ClientSession(
            LOGIN_HOST, headers={"User-Agent": USER_AGENT}
        ) as qa_session:
            async with qa_session.get("/users/login") as response:
                response.raise_for_status()
                soup = BeautifulSoup(await response.read(), "lxml")
                assert isinstance(login_form := soup.find(id="login-form"), Tag)
                assert isinstance(
                    fkey_input := login_form.find(attrs={"name": "fkey"}), Tag
                )
                assert isinstance(qa_fkey := fkey_input["value"], str)
                logger.debug(f"QA fkey is {qa_fkey}")
            async with qa_session.post(
                "/users/login-or-signup/validation/track",
                data={
                    "isSignup": "false",
                    "isLogin": "true",
                    "isPassword": "false",
                    "isAddLogin": "false",
                    "fkey": qa_fkey,
                    "ssrc": "head",
                    "email": email,
                    "password": password,
                    "oauthversion": "",
                    "oauthserver": "",
                },
            ) as response:
                if response.status != 200:
                    raise LoginError(f"MSE responded with a non-ok status code {response.status} {response.reason}")
            async with qa_session.post(
                "/users/login",
                data={
                    "fkey": qa_fkey,
                    "ssrc": "login",
                    "email": email,
                    "password": password,
                    "oauth_version": "",
                    "oauth_server": "",
                },
                allow_redirects=False,
            ) as response:
                if response.status != 302:
                    raise LoginError("Login failed! Incorrect username or password?")
                if (redirect_target := URL(response.headers["Location"])).path != "/":
                    raise LoginError(
                        f"Login failed! Redirected to {redirect_target}; caught by captcha?"
                    )
                logger.debug(f"Logged in to {LOGIN_HOST}")

        qa_cookies = cast(CookieJar, qa_session.cookie_jar)._cookies[(COOKIE_ROOT, "")]
        acct = qa_cookies["acct"]
        prov = qa_cookies["prov"]

        async with ClientSession(
            server, headers={"User-Agent": USER_AGENT}
        ) as chat_session:
            chat_session.cookie_jar.update_cookies(
                cookies={"acct": acct, "prov": prov},
                response_url=URL.build(scheme="https", host=COOKIE_ROOT),
            )
            async with chat_session.get("/") as response:
                response.raise_for_status()
                if chat_user_cookie not in response.cookies:
                    raise LoginError(
                        f"Login failed! {chat_user_cookie} not in cookies returned from {server}"
                    )
                chatusr = response.cookies[chat_user_cookie]

                soup = BeautifulSoup(await response.read(), "lxml")
                assert isinstance(
                    topbar_menu_links := soup.find(class_="topbar-menu-links"), Tag
                )
                assert isinstance(profile_link := topbar_menu_links.find("a"), Tag)
                assert isinstance(url := profile_link["href"], str)
                url = URL(url)
                if url.host == "stackexchange.com":
                    raise LoginError(
                        "The supplied credentials were not accepted by chat! Bad username or password?"
                    )
                user_id = int(url.parts[2])

        logger.info(f"Logged into QA and chat")
        return Credentials(
            server=server, acct=acct, prov=prov, chatusr=chatusr, user_id=user_id
        )

    def save(self, path: "FileDescriptorOrPath") -> None:
        """Save a `Credentials` instance to a file.
        
        Args:
            path: The file descriptor or path to save to.
        """
        with open(path, "wb") as file:
            pickle.dump(self, file)
        logger.info(f"Saved credentials to {path}")

    @staticmethod
    async def load(path: "FileDescriptorOrPath") -> Optional["Credentials"]:
        """Read and validate a `Credentials` instance from a file.
        
        Args:
            path: The file descriptor or path to load from.

        Returns:
            The loaded `Credentials` instance, or `None` if it was invalid.
        """
        logger.info(f"Reading credentials from {path}")
        with open(path, "rb") as file:
            credentials = pickle.load(file)
        assert isinstance(credentials, Credentials)
        async with credentials._session() as session, session.get("/") as response:
            soup = BeautifulSoup(await response.read(), "lxml")
            assert isinstance(
                topbar_menu_links := soup.find(class_="topbar-menu-links"), Tag
            )
            assert isinstance(profile_link := topbar_menu_links.find("a"), Tag)
            assert isinstance(url := profile_link["href"], str)
            url = URL(url)
            if url.host == "stackexchange.com":
                logger.info(f"Credentials in {path} are expired!")
                return None
            user_id = int(url.parts[2])
            if user_id != credentials.user_id:
                logger.warning(
                    f"Credentials in {path} have an incorrect user id! ({user_id} expected, {credentials.user_id} loaded)"
                )
                return None
        return credentials

    @staticmethod
    async def load_or_authenticate(
        path: "FileDescriptorOrPath",
        email: str,
        password: str,
        *,
        server: Server = Server.STACK_EXCHANGE,
    ) -> "Credentials":
        """Load and validate credentials from a file, or log in if they aren't valid.

        This function automatically handles preserving credentials to minimize logins and avoid a CAPTCHA,
        as well as renewing them when necessary. The account **must have 20 reputation to use chat**.

        Args:
            path: The file descriptor or path to load credentials from and save them to.
            email: The email address of the account to log into.
            password: The password of the account to log into.
            server: The chat server to log into.
        """
        if exists(path):
            try:
                credentials = await Credentials.load(path)
            except Exception as e:
                logger.error(f"Failed to load credentials from {path}", exc_info=e)
            else:
                if credentials is not None:
                    return credentials
        credentials = await Credentials.authenticate(email, password, server=server)
        credentials.save(path)
        return credentials
