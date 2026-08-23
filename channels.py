"""Общие операции с каналами и сообщениями Discord."""

from __future__ import annotations

import asyncio

import discord

from botcore import bot, console_log
from formatting import text_sendable
from projects import get_project


async def cleanup_bot_messages(channel: discord.TextChannel, limit: int = 200) -> None:
    try:
        if bot.user is None:
            return
        await channel.purge(limit=limit, check=lambda message: message.author.id == bot.user.id)
    except Exception:
        pass


async def delete_message_safely(message: discord.Message) -> None:
    try:
        await message.delete()
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def delete_channel_now(channel_id: int, reason: str) -> bool:
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            channel = None

    if channel is None:
        return True

    if isinstance(channel, discord.Thread) and channel.archived:
        try:
            await channel.edit(archived=False, reason=reason)
        except Exception:
            pass

    delete_method = getattr(channel, "delete", None)
    if not callable(delete_method):
        return True

    try:
        await delete_method(reason=reason)
        return True
    except Exception as error:
        console_log(f"Failed to delete channel {channel_id}: {error}")
        return False


async def delete_channel_later(channel_id: int, reason: str) -> None:
    await asyncio.sleep(4)
    for _attempt in range(8):
        if await delete_channel_now(channel_id, reason):
            return
        await asyncio.sleep(3)


async def ensure_guild_members_loaded(guild: discord.Guild) -> None:
    try:
        await asyncio.wait_for(guild.chunk(cache=True), timeout=15)
    except Exception as error:
        console_log(f"Member chunk skipped for guild {guild.id}: {error!r}")


async def send_dm_or_fallback(
    guild: discord.Guild,
    user_id: int,
    embed: discord.Embed,
    *,
    file: discord.File | None = None,
) -> None:
    """Пробует ЛС, а при закрытых личных сообщениях пишет в запасной канал."""
    member = guild.get_member(user_id)
    user: discord.abc.User = member if member is not None else await bot.fetch_user(user_id)

    try:
        if file is not None:
            await user.send(embed=embed, file=file)
        else:
            await user.send(embed=embed)
        return
    except Exception:
        pass

    project = get_project(guild)
    fallback_channel_id = project.get("dm_fallback_channel_id") if project else None
    if not fallback_channel_id:
        return
    try:
        fallback = guild.get_channel(int(fallback_channel_id)) or await guild.fetch_channel(int(fallback_channel_id))
    except Exception:
        return

    if not text_sendable(fallback):
        return

    # discord.File — одноразовый поток, для второй отправки нужен новый объект.
    retry_file = discord.File(file.fp.name, filename=file.filename) if file is not None and hasattr(file.fp, "name") else None
    try:
        await fallback.send(
            content=f"<@{user_id}>",
            embed=embed,
            file=retry_file,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
    except Exception:
        pass
