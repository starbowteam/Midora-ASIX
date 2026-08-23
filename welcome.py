"""Приветствие и прощание.

В самом welcome-канале остаются только короткие строки «зашёл» и «вышел».
Вся информация о семье, ссылки и картинка уходят участнику в личные сообщения.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from botcore import bot
from components import REGISTER_URL, large_separator, welcome_gallery, welcome_banner_file
from config import *
from embeds import make_embed
from formatting import format_log_time_msk, text_sendable
from projects import get_guild_emoji_text, get_project


class WelcomeDirectMessageCard(discord.ui.LayoutView):
    """Личное приветствие: одна тёмная карточка с картинкой и кнопкой регистрации."""

    def __init__(self, guild: discord.Guild) -> None:
        super().__init__(timeout=None)
        title_emoji = get_guild_emoji_text(guild, EMOJI_WELCOME_TITLE_ID, "✦")
        line_emoji = get_guild_emoji_text(guild, EMOJI_WELCOME_LINE_ID, "•")
        applications_url = f"https://discord.com/channels/{guild.id}/{FAMQ_PANEL_CHANNEL_ID}"

        register_button = discord.ui.Button(
            label="Регистрация",
            emoji="🔗",
            style=discord.ButtonStyle.link,
            url=REGISTER_URL,
        )

        card = discord.ui.Container(
            discord.ui.TextDisplay(f"# {title_emoji} Добро пожаловать в ASIXEZ"),
            large_separator(),
            discord.ui.TextDisplay(
                f"{line_emoji} Заявку на вступление можно оставить здесь: [канал заявок]({applications_url})\n"
                f"{line_emoji} Не вводил промокод? Напиши в игровой чат `/promo ASIX`\n"
                f"{line_emoji} Наш YouTube: https://www.youtube.com/@asixezzz"
            ),
            large_separator(),
            discord.ui.TextDisplay(
                "## Промокод семьи `/promo ASIX`\n"
                "> Получай **$50.000** и **7 дней Majestic Premium**"
            ),
            large_separator(),
            welcome_gallery(),
            discord.ui.ActionRow(register_button),
        )
        self.add_item(card)


async def resolve_welcome_channel(guild: discord.Guild) -> discord.TextChannel | None:
    project = get_project(guild)
    channel_id = project.get("welcome_channel_id") if project else None
    if not channel_id:
        return None
    try:
        channel = guild.get_channel(int(channel_id)) or await guild.fetch_channel(int(channel_id))
    except Exception:
        return None
    return channel if isinstance(channel, discord.TextChannel) else None


async def send_welcome_dm(member: discord.Member) -> None:
    # Без файла карточка всё равно отправляется: welcome_gallery() сам подставит
    # фирменный баннер. file=None передавать нельзя — discord.py примет его за вложение.
    banner_file = welcome_banner_file()
    extra = {"file": banner_file} if banner_file is not None else {}
    try:
        await member.send(view=WelcomeDirectMessageCard(member.guild), **extra)
    except Exception:
        # Личные сообщения могут быть закрыты — вход на сервер из-за этого падать не должен.
        pass


async def send_join_notice(member: discord.Member) -> None:
    """Короткая строка о входе в welcome-канале, без картинок и описаний."""
    channel = await resolve_welcome_channel(member.guild)
    if channel is None or not text_sendable(channel):
        return

    member_count = member.guild.member_count or len(member.guild.members)
    embed = make_embed(
        description=f"**→ {member.mention} зашёл на сервер**\nТеперь нас: **{member_count}**",
        color=COLOR_SOFT,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name=str(member), icon_url=member.display_avatar.url)
    embed.set_footer(text=f"ID: {member.id} • {format_log_time_msk()}")
    try:
        # Упоминание идёт в content: внутри эмбеда оно не даёт пинга новичку.
        await channel.send(
            content=member.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
    except Exception:
        pass


async def send_leave_notice(member: discord.Member) -> None:
    """Короткая строка о выходе в welcome-канале."""
    channel = await resolve_welcome_channel(member.guild)
    if channel is None or not text_sendable(channel):
        return

    member_count = member.guild.member_count or len(member.guild.members)
    embed = make_embed(
        description=f"**← {member.mention} покинул сервер**\nТеперь нас: **{member_count}**",
        color=COLOR_MUTED,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name=str(member), icon_url=member.display_avatar.url)
    embed.set_footer(text=f"ID: {member.id} • {format_log_time_msk()}")
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


async def on_member_join_welcome(member: discord.Member) -> None:
    if member.bot or member.guild.id != FAMQ_GUILD_ID:
        return
    await send_join_notice(member)
    await send_welcome_dm(member)


async def on_member_remove_welcome(member: discord.Member) -> None:
    if member.bot or member.guild.id != FAMQ_GUILD_ID:
        return
    await send_leave_notice(member)


def register_listeners() -> None:
    bot.add_listener(on_member_join_welcome, "on_member_join")
    bot.add_listener(on_member_remove_welcome, "on_member_remove")
