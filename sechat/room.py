from time import time

from websockets.client import connect
from aiohttp import ClientSession


class Room:
    def __init__(self, session: ClientSession, fkey: str, userID: int, roomID: int):
        self.session = session
        self.fkey = fkey
        self.userID = userID
        self.roomID = roomID

    async def getSockets(self):
        while True:
            async with self.session.post(
                "https://chat.stackexchange.com/ws-auth",
                data={"fkey": self.fkey, "roomid": self.roomID},
            ) as r:
                yield connect((await r.json())["url"] + f"?l={int(time())}")

    async def loop(self):
        async for socket in self.getSockets():
            pass
