# About

**sechat** (usually referred to in monospace to distinguish it from the platform it's designed for, e.g. `sechat`) is an asynchronous Python client for the [Stack Exchange](https://stackexchange.net) Q&A website network's [custom-built chat service](https://chat.stackexchange.com), variously referred to as "Stack Exchange chat", "SEchat", and "Bonfire". It is designed for creating automated chatbots to assist with moderation and other rote tasks.

!!! warning
    **`sechat` is by nature unstable**, as Stack Exchange chat has no public API and no guarantee of consistency, and the library may need to be updated at any time to accomodate for changes to chat's internals. No part of its API should be assumed to be stable between any two commits.

## Installation
!!! warning
    The copy of `sechat` on PyPI is no longer updated due to `sechat`'s always-unstable nature. Do not use it; instead, install directly from GitHub.


To install `sechat`, use your module manager's system for installing from Git repositories. `sechat` is available on GitHub at <https://github.com/gingershaped/sechat>, and the `v3` branch should be used.

## Example
```py
import asyncio

from sechat import Credentials, Room

async def main():
    credentials = await Credentials.load_or_authenticate("credentials.dat", "<your email address>", "<your password>")
    async with Room.join(credentials, 1) as room:
        await room.send("Hello World!")

asyncio.run(main())
```