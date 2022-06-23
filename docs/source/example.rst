Example Bot
===========

Whenever someone sends "hello" this bot will reply with "Hello, <username>!"

.. code-block:: python

	import sechat
	from sechat.events import Events

	def messageHandler(event):
	    if event.content == "hello":
	        r.send(r.buildReply(event.message_id, "Hello, " + event.user_name + "!"))

	bot = sechat.Bot()
	bot.login("<email>", "<password>")
	r = bot.joinRoom(1)
	r.on(Events.MESSAGE, messageHandler)

	try:
	    while True:
		    pass
	finally:
	    bot.leaveAllRooms()


