#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Simple async conversation manager — no pyromod needed.
# Works with python-telegram-bot 20.x

import asyncio

_pending: dict = {}   # chat_id -> asyncio.Future


async def conv_ask(bot, chat_id: int, prompt: str, timeout: int = 120):
    """
    Send prompt to chat_id, then wait up to `timeout` seconds for user reply.
    Returns the telegram.Message object the user sent.
    Raises Exception("Timeout") on timeout.
    """
    await bot.send_message(chat_id, prompt, parse_mode="HTML")
    loop = asyncio.get_event_loop()
    fut  = loop.create_future()
    _pending[chat_id] = fut
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
    except asyncio.TimeoutError:
        raise Exception("Timeout")
    finally:
        _pending.pop(chat_id, None)


def conv_resolve(chat_id: int, message) -> bool:
    """
    Called by text_router for every incoming plain text message.
    If a conv_ask() is waiting for this chat, resolves it.
    Returns True if resolved, False otherwise.
    """
    fut = _pending.get(chat_id)
    if fut and not fut.done():
        fut.set_result(message)
        return True
    return False
