"""Экземпляр бота и логирование.

Модуль намеренно не импортирует ничего из доменных модулей: благодаря этому
любой модуль проекта может сделать ``from botcore import bot`` без циклов.
"""

from __future__ import annotations

import sys
import uuid

import discord
from discord.ext import commands

from config import BASE_DIR
from logging_setup import setup_logging


logger = setup_logging(BASE_DIR)

# Короткий идентификатор запуска. Каждый процесс получает свой, поэтому по
# сообщениям в Discord сразу видно, сколько копий бота реально работает:
# два сообщения о перезапуске с разными идентификаторами — два процесса.
INSTANCE_ID = uuid.uuid4().hex[:6]


def console_log(message: str) -> None:
    level = logger.error if any(word in message.lower() for word in ("failed", "error", "skipped")) else logger.info
    level(message)
    sys.stdout.write(f"{message}\n")


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True
    intents.messages = True
    intents.message_content = True
    intents.voice_states = True
    if hasattr(intents, "moderation"):
        intents.moderation = True
    if hasattr(intents, "bans"):
        intents.bans = True
    return intents


bot = commands.Bot(command_prefix="!", intents=build_intents())
