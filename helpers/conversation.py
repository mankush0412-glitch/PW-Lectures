#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Simple conversation manager — replaces pyromod
# Usage:
#   msg = await conv_ask(bot, chat_id, "Enter phone:", timeout=120)
#   conv_resolve(chat_id, incoming_message)  ← call from text_router handler

import asyncio

_pending: dict = {}   # chat_id -> asyncio.Future


async def conv_ask(client, chat_id: int, prompt: str, timeout: int = 120):
    """
    Send `prompt` to `chat_id`, then wait up to `timeout` seconds
    for the user to send any text message back.
    Returns the Message object the user sent.
    Raises Exception("Timeout") on timeout.
    """
    await client.send_message(chat_id, prompt)
    loop = asyncio.get_event_loop()
    fut  = loop.create_future()
    _pending[chat_id] = fut
    try:
        result = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        return result
    except asyncio.TimeoutError:
        raise Exception("Timeout")
    finally:
        _pending.pop(chat_id, None)


def conv_resolve(chat_id: int, message) -> bool:
    """
    Called by the text_router handler for every incoming private message.
    If a conv_ask() is waiting for this chat_id, resolves it with the message.
    Returns True if a conversation was waiting, False otherwise.
    """
    fut = _pending.get(chat_id)
    if fut and not fut.done():
        fut.set_result(message)
        return True
    return False
