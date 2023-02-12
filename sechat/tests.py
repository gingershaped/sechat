import asyncio
import os
import unittest

from sechat import Bot, Room, EventType, MentionEvent, MessageEvent

bot = Bot()
bot2 = Bot()
EMAIL1, PASSWORD1, EMAIL2, PASSWORD2 = os.environ["EMAIL1"], os.environ["PASSWORD1"], None, None

def setUpModule():
    global EMAIL2, PASSWORD2
    try:
        EMAIL2, PASSWORD2 = os.environ["EMAIL2"], os.environ["PASSWORD2"]
    except KeyError:
        EMAIL2 = None

class T00LoginTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    async def asyncSetUp(cls):
        await bot.authenticate(EMAIL1, PASSWORD1, "https://codegolf.stackexchange.com")
    
    def testProps(self):
        self.assertIsNotNone(bot.fkey)
        self.assertIsNotNone(bot.userID)
    
class T01RoomTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUp(cls):
        cls.room = bot.joinRoom(1)
    @classmethod
    async def asyncTearDown(cls):
        await cls.room.send("Stage 1 complete. DO NOT resume sending messages.")
        await asyncio.sleep(3)

    async def test00ConnectionProcess(self):
        self.assertIsNotNone(self.room.fkey)
        await asyncio.sleep(1)
        await self.room.send("Currently testing message functionality. Please DO NOT send any other messages in this room until I say the tests are completed. The test will begin in 3 seconds. Thank you.")
        await asyncio.sleep(3)
    async def test01Messaging(self):
        await self.room.send("Message 1")
        await asyncio.sleep(3)
        await self.room.send("Cooldown check 1")
        await self.room.send("Cooldown check 2")
        await self.room.send("Cooldown check 3")
        await asyncio.sleep(3)
    async def test02Editing(self):
        ident = await self.room.send("Before edit")
        await asyncio.sleep(2)
        await self.room.edit(ident, "After edit")
        await asyncio.sleep(2)
    async def test03Deleting(self):
        ident = await self.room.send("Going to be deleted")
        await asyncio.sleep(2)
        await self.room.delete(ident)
        await asyncio.sleep(2)
    '''
    def test04Transcript(self):
        messages = [i["content"] if "content" in i else False for i in self.room.getRecentMessages()[-7:]]
        print(messages)
        self.assertEqual(messages[0], "Message 1")
        self.assertEqual(messages[3], "Cooldown check 3")
        self.assertEqual(messages[5], "After edit")
        self.assertFalse(messages[6])
    '''
        

@unittest.skipIf(EMAIL2 is None, "Don't have two bots to test this with")
class T02MultiUserTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    async def asyncSetUp(cls):
        await bot2.authenticate(EMAIL2, PASSWORD2, "https://codegolf.stackexchange.com") # type: ignore Pylance doesn't realise skipIf is a typeguard
        cls.room = bot.joinRoom(1)
        cls.room2 = bot2.joinRoom(1)
        cls.gotMessage = False
        cls.gotReply = False
    @classmethod
    async def asyncTearDown(cls):
        bot2.leaveAllRooms()
        await cls.room.send("Stage 2 complete. DO NOT resume sending messages.")
        await asyncio.sleep(2)

    async def onMessage(self, room: Room, event: MessageEvent):
        if event.content == "Test message":
            self.gotMessage = True
    async def onMention(self, room, event):
        self.gotReply = True
        print(event)
    
    async def test00Starring(self):
        ident = await self.room.send("This message will be starred")
        await asyncio.sleep(2)
        await self.room2.star(ident)
        await asyncio.sleep(2)
        await self.room2.send("Unstarring message")
        await self.room2.star(ident)
        await asyncio.sleep(4)
    async def test01MessageEvents(self):
        self.room.register(self.onMessage, EventType.MESSAGE)
        await self.room2.send("Test message")
        await asyncio.sleep(2)
        self.room.unregister(self.onMessage, EventType.MESSAGE)
        self.assertTrue(self.gotMessage)
    async def test02ReplyEvents(self):
        self.room.register(self.onMention, EventType.MENTION)
        ident = await self.room.send("Test message")
        await self.room2.reply(ident, "Test reply")
        await asyncio.sleep(2)
        self.room.unregister(self.onMention, EventType.MENTION)
        self.assertTrue(self.gotReply)
        

class T03ROTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.room = bot.joinRoom(1)
        cls.ident = None
    @classmethod
    async def asyncTearDown(cls):
        await asyncio.sleep(2)
    async def test00PinMessages(self):
        self.ident = await self.room.send("This message will be pinned")
        await asyncio.sleep(2)
        await self.room.pin(self.ident)
        await asyncio.sleep(2)
        await self.room.unpin(self.ident)
        await asyncio.sleep(2)
        await self.room.send("Clearing stars on message")
        await self.room.clearStars(self.ident)
    @unittest.skip("No privs yet")
    async def test02MoveMessages(self):
        ident = await self.room.send("This message will be moved to https://chat.stackexchange.com/rooms/120733/osp-testing")
        await asyncio.sleep(2)
        await self.room.moveMessages([ident], 120733)
        

class T04LeaveTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    async def asyncSetUp(cls):
        cls.room = bot.joinRoom(1)
        await cls.room.send("Testing complete. You may now resume sending messages.")
    def setUp(self):
        self.room = bot.joinRoom(1)
        await asyncio.sleep(2)
    def test01LeaveRoom(self):
        bot.leaveRoom(1, True)
        with self.assertRaises(ValueError):
            bot.leaveRoom(1)
    def test02LeaveAllRooms(self):
        bot.leaveAllRooms(True)

    @unittest.skip("Don't want to trigger the captcha")
    def test03Logout(self):
        bot.logout()
        self.assertIsNone(bot.fkey)
        self.assertIsNone(bot.userID)
