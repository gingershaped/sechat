import unittest
import os
import sechat
from time import sleep
from sechat.events import Events

EMAIL1, PASSWORD1 = os.environ["EMAIL1"], os.environ["PASSWORD1"]
try:
    EMAIL2, PASSWORD2 = os.environ["EMAIL2"], os.environ["PASSWORD2"]
except KeyError:
    EMAIL2 = None

bot = None
bot2 = None

def setUpModule():
    global bot, bot2
    bot = sechat.Bot()
    if EMAIL2:
        bot2 = sechat.Bot()

class T00LoginTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bot.login(EMAIL1, PASSWORD1)
    
    def testProps(self):
        self.assertIsNotNone(bot.fkey)
        self.assertIsNotNone(bot.userID)
    
class T01RoomTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.room = bot.joinRoom(1, False)
    @classmethod
    def tearDownClass(cls):
        cls.room.send("Stage 1 complete. DO NOT resume sending messages.")
        sleep(3)

    def test00ConnectionProcess(self):
        self.room.connect()
        self.assertIsNotNone(self.room.socket)
        sleep(1)
        self.assertTrue(self.room.thread.is_alive)
        self.assertTrue(self.room.running)
        self.room.send("Currently testing message functionality. Please DO NOT send any other messages in this room until I say the tests are completed. The test will begin in 3 seconds. Thank you.")
        sleep(3)
    def test01Messaging(self):
        self.room.send("Message 1")
        sleep(3)
        self.room.send("Cooldown check 1")
        self.room.send("Cooldown check 2")
        self.room.send("Cooldown check 3")
        sleep(4)
        self.room.send("Too fast check 1")
        with self.assertRaises(sechat.errors.TooFastError):
            self.room.send("Too fast check 2 (should not be seen)", False)
        sleep(5)
    def test02Editing(self):
        ident = self.room.send("Before edit")
        sleep(2)
        self.room.edit(ident, "After edit")
        sleep(2)
    def test03Deleting(self):
        ident = self.room.send("Going to be deleted")
        sleep(2)
        self.room.delete(ident)
        sleep(2)
        
    def test04Transcript(self):
        messages = [i["content"] if "content" in i else False for i in self.room.getRecentMessages()[-7:]]
        print(messages)
        self.assertEqual(messages[0], "Message 1")
        self.assertEqual(messages[3], "Cooldown check 3")
        self.assertEqual(messages[5], "After edit")
        self.assertFalse(messages[6])
        

@unittest.skipIf(EMAIL2 is None, "Don't have two bots to test this with")
class T02MultiUserTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bot2.login(EMAIL2, PASSWORD2)
        cls.room = bot.joinRoom(1)
        cls.room2 = bot2.joinRoom(1)
        cls.gotMessage = False
        cls.gotReply = False
    @classmethod
    def tearDownClass(cls):
        bot2.leaveAllRooms()
        cls.room.send("Stage 2 complete. DO NOT resume sending messages.")
        sleep(2)

    def onMessage(self, event):
        if event.content == "Test message":
            self.gotMessage = True
    def onReply(self, event):
        self.gotReply = True
        print(event)
    
    def test00Starring(self):
        ident = self.room.send("This message will be starred")
        sleep(2)
        self.room2.star(ident)
        sleep(2)
        self.room2.send("Unstarring message")
        self.room2.star(ident)
        sleep(4)
    def test01MessageEvents(self):
        self.room.on(Events.MESSAGE, self.onMessage)
        self.room2.send("Test message")
        sleep(2)
        self.room.off(self.onMessage)
        self.assertTrue(self.gotMessage)
    def test02ReplyEvents(self):
        self.room.on(Events.REPLY, self.onReply)
        ident = self.room.send("Test message")
        self.room2.send(self.room2.buildReply(ident, "Test reply"))
        sleep(2)
        self.room.off(self.onReply)
        self.assertTrue(self.gotReply)
        

class T03ROTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.room = bot.joinRoom(1, False)
        cls.ident = None
    @classmethod
    def tearDownClass(cls):
        sleep(2)
    def test00PinMessages(self):
        self.ident = self.room.send("This message will be pinned")
        sleep(2)
        self.room.pin(self.ident)
        sleep(2)
        self.room.unpin(self.ident)
        sleep(2)
        self.room.send("Clearing stars on message")
        self.room.clearStars(self.ident)
    @unittest.skip("No privs yet")
    def test02MoveMessages(self):
        ident = self.room.send("This message will be moved to https://chat.stackexchange.com/rooms/120733/osp-testing")
        sleep(2)
        self.room.move([ident], 120733)
        

class T04LeaveTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.room = bot.joinRoom(1)
        cls.room.send("Testing complete. You may now resume sending messages.")
    def setUp(self):
        self.room = bot.joinRoom(1)
        sleep(2)
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
