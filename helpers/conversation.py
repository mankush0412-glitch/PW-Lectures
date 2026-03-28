#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Conversation helper for pyromod-style ask()
# (pyromod provides bot.ask() natively — this file just re-exports it cleanly)

import asyncio


async def ask(bot, chat_id: int, text: str, timeout: int = 300):
    """
    Wrapper around pyromod's bot.ask().
    Returns Message or raises asyncio.TimeoutError.
    """
    return await bot.ask(chat_id, text, timeout=timeout)
