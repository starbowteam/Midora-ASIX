"""Чистые помощники форматирования текста, времени и подписей Discord."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from config import MSK_TZ


def parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(timezone.utc)


def trim_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def trim_embed_text(value: str, limit: int = 1024) -> str:
    return trim_text(value, limit)


def format_text_block(value: str, *, fallback: str = "Нет", limit: int = 1000) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return fallback
    return trim_embed_text(cleaned, limit=limit)


def build_before_after_value(before_value: str, after_value: str) -> str:
    return f"**До:** {format_text_block(before_value)}\n**После:** {format_text_block(after_value)}"


def split_long_message(content: str, limit: int = 2000) -> list[str]:
    if len(content) <= limit:
        return [content]

    chunks: list[str] = []
    remaining = content
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    return [chunk for chunk in chunks if chunk]


def parse_hours_input(raw_value: str) -> int | None:
    try:
        hours = int(raw_value.strip())
    except (AttributeError, ValueError):
        return None
    if hours <= 0:
        return None
    return hours


def extract_user_id(raw_value: str) -> int | None:
    match = re.search(r"\d{15,22}", raw_value or "")
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def text_sendable(channel: Any) -> bool:
    return isinstance(channel, (discord.TextChannel, discord.Thread))


# --- Время ---

MONTH_NAMES_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def format_datetime_msk(value: datetime | None) -> str:
    if value is None:
        return "—"
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    localized = current.astimezone(MSK_TZ)
    month_name = MONTH_NAMES_RU[localized.month - 1]
    return f"{localized.day} {month_name} {localized.year} г. • {localized.strftime('%H:%M')} MSK"


def format_log_time_msk(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    localized = current.astimezone(MSK_TZ)
    today = datetime.now(MSK_TZ).date()
    target_date = localized.date()
    if target_date == today:
        prefix = "Сегодня"
    elif target_date == today - timedelta(days=1):
        prefix = "Вчера"
    else:
        prefix = localized.strftime("%d.%m.%Y")
    return f"{prefix}, в {localized.strftime('%H:%M')} MSK"


def format_relative_time(value: datetime | None) -> str:
    if value is None:
        return "—"
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - current.astimezone(timezone.utc)
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return "только что"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин. назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч. назад"
    days = hours // 24
    if days < 30:
        return f"{days} дн. назад"
    months = days // 30
    if months < 12:
        return f"{months} мес. назад"
    years = months // 12
    return f"{years} г. назад"


# --- Подписи объектов Discord ---

def safe_asset_url(asset: Any) -> str:
    if asset is None:
        return ""
    return str(getattr(asset, "url", "") or "")


def format_member_label(member: discord.abc.User | discord.Member | None) -> str:
    if member is None:
        return "неизвестно"
    if isinstance(member, discord.Member):
        return member.mention
    return f"<@{member.id}>"


def format_role_label(role: discord.Role | None) -> str:
    if role is None:
        return "неизвестно"
    return role.mention


def format_roles_label(roles: list[discord.Role]) -> str:
    if not roles:
        return "—"
    return trim_embed_text(" ".join(role.mention for role in roles))


def format_channel_label(channel: Any) -> str:
    if channel is None:
        return "# неизвестно"
    mention = getattr(channel, "mention", None)
    if mention:
        return mention
    name = getattr(channel, "name", "неизвестно")
    return f"# {name}"


def format_thread_parent_label(thread: discord.Thread | None) -> str:
    parent = thread.parent if thread is not None else None
    if parent is None:
        return "неизвестно"
    return format_channel_label(parent)


def format_channel_type_label(channel: Any) -> str:
    if channel is None:
        return "unknown"
    if isinstance(channel, discord.Thread):
        return "thread"
    channel_type = getattr(channel, "type", None)
    if channel_type is None:
        return "unknown"
    return str(channel_type).replace("_", " ")


def get_member_timeout_until(member: discord.Member) -> datetime | None:
    return (
        getattr(member, "communication_disabled_until", None)
        or getattr(member, "timed_out_until", None)
        or getattr(member, "timeout_until", None)
    )
