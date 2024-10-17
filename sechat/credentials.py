import pickle
from dataclasses import dataclass
from http.cookies import Morsel
from logging import getLogger
from os.path import exists
from typing import TYPE_CHECKING, cast

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
    server: Server
    acct: Morsel[str]
    prov: Morsel[str]
    chatusr: Morsel[str]
    user_id: int

    @property
    def cookies(self):
        return {
            "acct": self.acct,
            "prov": self.prov,
            (
                "sechatusr" if self.server == Server.STACK_EXCHANGE else "chatusr"
            ): self.chatusr,
        }

    @property
    def headers(self):
        return {"User-Agent": USER_AGENT, "Referer": self.server}

    def session(self):
        return ClientSession(self.server, cookies=self.cookies, headers=self.headers)

    @staticmethod
    async def scrape_fkey(session: ClientSession, server: Server):
        async with session.get("/chats/join/favorite") as response:
            soup = BeautifulSoup(await response.read(), "lxml")
            assert isinstance(fkey_input := soup.find(id="fkey"), Tag)
            assert isinstance(fkey := fkey_input.attrs["value"], str)
        return fkey

    @staticmethod
    async def authenticate(
        email: str, password: str, *, server: Server = Server.STACK_EXCHANGE
    ) -> "Credentials":
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
                    raise LoginError(
                        "Failed to login!", response.status, await response.text()
                    )
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

    @staticmethod
    async def load(path: "FileDescriptorOrPath"):
        logger.info(f"Reading credentials from {path}")
        with open(path, "rb") as file:
            credentials = pickle.load(file)
        assert isinstance(credentials, Credentials)
        async with credentials.session() as session, session.get("/") as response:
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
    ):
        if exists(path):
            try:
                credentials = await Credentials.load(path)
            except Exception as e:
                logger.error(f"Failed to load credentials from {path}", exc_info=e)
            else:
                if credentials is not None:
                    return credentials
        credentials = await Credentials.authenticate(email, password, server=server)
        with open(path, "wb") as file:
            pickle.dump(credentials, file)
        logger.info(f"Saved credentials to {file}")
        return credentials
