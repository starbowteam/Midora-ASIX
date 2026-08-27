"""Логирование: основной лог сервера и лог заявок в одном формате.

Лог заявок раньше был одной строкой текста в эмбеде. Теперь он собирается тем же
конструктором, что и основной лог: автор, заголовок, поля и подпись со временем MSK.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import discord

from botcore import bot, console_log
from config import *
from embeds import build_log_line_embed
from formatting import (
    format_channel_label,
    format_channel_type_label,
    format_datetime_msk,
    format_log_time_msk,
    format_roles_label,
    format_text_block,
    format_thread_parent_label,
    get_member_timeout_until,
    safe_asset_url,
)
from projects import get_project, is_allowed_guild_id, is_famq_activity_guild
from state import save_member_activity, update_member_activity_profile


# --- Каналы ---

async def _resolve_channel(guild: discord.Guild, project_key: str) -> discord.TextChannel | None:
    project = get_project(guild)
    if project is None or not project.get(project_key):
        return None
    channel_id = int(project[project_key])
    try:
        channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
    except Exception:
        return None
    return channel if isinstance(channel, discord.TextChannel) else None


async def resolve_activity_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    if not is_famq_activity_guild(guild.id):
        return None
    return await _resolve_channel(guild, "activity_log_channel_id")


async def resolve_application_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    return await _resolve_channel(guild, "application_log_channel_id")


# --- Журнал аудита ---

async def fetch_audit_executor(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    target_id: int | None = None,
) -> tuple[discord.Member | None, discord.AuditLogEntry | None]:
    try:
        async for entry in guild.audit_logs(limit=5, action=action):
            created_at = entry.created_at.replace(tzinfo=timezone.utc) if entry.created_at.tzinfo is None else entry.created_at
            if (datetime.now(timezone.utc) - created_at).total_seconds() > 15:
                continue
            if target_id is not None:
                entry_target_id = getattr(entry.target, "id", None)
                if entry_target_id != target_id:
                    continue
            user = entry.user
            if user is None:
                continue
            member = guild.get_member(user.id)
            if member is None:
                try:
                    member = await guild.fetch_member(user.id)
                except Exception:
                    member = None
            return member, entry
    except Exception:
        return None, None
    return None, None


async def fetch_optional_audit_executor(
    guild: discord.Guild,
    action_name: str,
    target_id: int | None = None,
) -> tuple[discord.Member | None, discord.AuditLogEntry | None]:
    action = getattr(discord.AuditLogAction, action_name, None)
    if action is None:
        return None, None
    return await fetch_audit_executor(guild, action, target_id=target_id)


async def detect_leave_reason(guild: discord.Guild, user_id: int) -> tuple[str, int | None, str | None]:
    actor, entry = await fetch_audit_executor(guild, discord.AuditLogAction.ban, target_id=user_id)
    if entry is not None:
        return "ban", actor.id if actor is not None else None, entry.reason
    actor, entry = await fetch_audit_executor(guild, discord.AuditLogAction.kick, target_id=user_id)
    if entry is not None:
        return "kick", actor.id if actor is not None else None, entry.reason
    return "left", None, None


# --- Очередь отправки ---
#
# Каждое событие сервера — это отдельный POST в Discord, и на активном сервере
# канал логов быстро упирается в rate limit (429). Поэтому записи копятся в
# очереди, а фоновая задача отправляет их пачками: Discord принимает до 10
# эмбедов в одном сообщении, то есть запросов становится почти в 10 раз меньше.

LOG_BATCH_DELAY_SECONDS = 1.5
LOG_EMBEDS_PER_MESSAGE = 10
LOG_QUEUE_MAX_SIZE = 2000

_log_queue: asyncio.Queue | None = None
_log_worker: asyncio.Task | None = None


def _ensure_log_worker() -> asyncio.Queue | None:
    global _log_queue, _log_worker
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return None

    if _log_queue is None:
        _log_queue = asyncio.Queue(maxsize=LOG_QUEUE_MAX_SIZE)
    if _log_worker is None or _log_worker.done():
        _log_worker = asyncio.create_task(_drain_log_queue())
    return _log_queue


def enqueue_log_embed(channel: discord.TextChannel, embed: discord.Embed) -> None:
    queue = _ensure_log_worker()
    if queue is None:
        return
    try:
        queue.put_nowait((channel, embed))
    except asyncio.QueueFull:
        # Переполнение возможно только при аварийном потоке событий; теряем
        # запись, но не блокируем обработчик события.
        console_log("Log queue is full, dropping a log entry")


async def _drain_log_queue() -> None:
    assert _log_queue is not None
    while True:
        try:
            channel, embed = await _log_queue.get()
            batches: dict[int, tuple[discord.TextChannel, list[discord.Embed]]] = {
                channel.id: (channel, [embed])
            }

            # Небольшая пауза даёт соседним событиям попасть в ту же пачку.
            await asyncio.sleep(LOG_BATCH_DELAY_SECONDS)
            while not _log_queue.empty():
                next_channel, next_embed = _log_queue.get_nowait()
                _, embeds = batches.setdefault(next_channel.id, (next_channel, []))
                embeds.append(next_embed)

            for target_channel, embeds in batches.values():
                for start in range(0, len(embeds), LOG_EMBEDS_PER_MESSAGE):
                    chunk = embeds[start : start + LOG_EMBEDS_PER_MESSAGE]
                    try:
                        await target_channel.send(embeds=chunk)
                    except Exception as error:
                        console_log(f"Failed to send log batch to {target_channel.id}: {error}")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            console_log(f"Log queue worker error: {error}")
            await asyncio.sleep(1)



# --- Отправка ---

async def send_activity_log(
    guild: discord.Guild,
    *,
    title: str,
    description: str,
    fields: list[tuple[str, str, bool]],
    color: int = COLOR_LOG,
    author_name: str | None = None,
    author_icon_url: str | None = None,
    footer_parts: list[str] | None = None,
    thumbnail_url: str | None = None,
    image_url: str | None = None,
) -> None:
    channel = await resolve_activity_log_channel(guild)
    if channel is None:
        return
    enqueue_log_embed(
        channel,
        build_log_line_embed(
            title=title,
            description=description,
            fields=fields,
            color=color,
            author_name=author_name,
            author_icon_url=author_icon_url,
            footer_parts=footer_parts,
            thumbnail_url=thumbnail_url,
            image_url=image_url,
        ),
    )


async def send_application_log(
    guild: discord.Guild,
    *,
    title: str,
    description: str,
    fields: list[tuple[str, str, bool]],
    actor: discord.abc.User | discord.Member | None = None,
    footer_parts: list[str] | None = None,
    color: int = COLOR_LOG_APPLICATION,
) -> None:
    """Запись в лог заявок в том же виде, что и основной лог сервера."""
    channel = await resolve_application_log_channel(guild)
    if channel is None:
        return
    author_name = f"{actor} ({actor.id})" if actor is not None else None
    author_icon_url = safe_asset_url(getattr(actor, "display_avatar", None)) if actor is not None else None
    enqueue_log_embed(
        channel,
        build_log_line_embed(
            title=title,
            description=description,
            fields=fields,
            color=color,
            author_name=author_name,
            author_icon_url=author_icon_url,
            footer_parts=footer_parts or [format_log_time_msk()],
        ),
    )


# --- Diff-хелперы ---

def get_channel_update_changes(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> list[str]:
    changes: list[str] = []
    if before.name != after.name:
        changes.append(f"Имя: `{before.name}` → `{after.name}`")
    if getattr(before, "category_id", None) != getattr(after, "category_id", None):
        changes.append(
            f"Категория: `{getattr(before.category, 'name', 'нет')}` → `{getattr(after.category, 'name', 'нет')}`"
        )
    for attr_name, label in (
        ("topic", "Тема"),
        ("slowmode_delay", "Слоумод"),
        ("nsfw", "NSFW"),
        ("bitrate", "Битрейт"),
        ("user_limit", "Лимит пользователей"),
        ("position", "Позиция"),
    ):
        if getattr(before, attr_name, None) != getattr(after, attr_name, None):
            changes.append(f"{label}: `{getattr(before, attr_name, None)}` → `{getattr(after, attr_name, None)}`")
    return changes


def get_role_update_changes(before: discord.Role, after: discord.Role) -> list[str]:
    changes: list[str] = []
    if before.name != after.name:
        changes.append(f"Имя: `{before.name}` → `{after.name}`")
    if before.colour != after.colour:
        changes.append(f"Цвет: `{before.colour}` → `{after.colour}`")
    if before.hoist != after.hoist:
        changes.append(f"Отдельно: `{before.hoist}` → `{after.hoist}`")
    if before.mentionable != after.mentionable:
        changes.append(f"Упоминание: `{before.mentionable}` → `{after.mentionable}`")
    if before.permissions.value != after.permissions.value:
        changes.append("Права роли были изменены.")
    return changes


def get_guild_update_changes(before: discord.Guild, after: discord.Guild) -> list[str]:
    changes: list[str] = []
    for attr_name, label in (
        ("name", "Название"),
        ("description", "Описание"),
        ("vanity_url_code", "Vanity"),
        ("preferred_locale", "Язык"),
        ("verification_level", "Верификация"),
        ("explicit_content_filter", "Фильтр контента"),
        ("default_notifications", "Уведомления"),
    ):
        if getattr(before, attr_name, None) != getattr(after, attr_name, None):
            changes.append(
                f"{label}: `{getattr(before, attr_name, None) or '—'}` → `{getattr(after, attr_name, None) or '—'}`"
            )
    if safe_asset_url(before.icon) != safe_asset_url(after.icon):
        changes.append("Иконка сервера была изменена.")
    if safe_asset_url(before.banner) != safe_asset_url(after.banner):
        changes.append("Баннер сервера был изменён.")
    if safe_asset_url(before.splash) != safe_asset_url(after.splash):
        changes.append("Splash-изображение было изменено.")
    if safe_asset_url(before.discovery_splash) != safe_asset_url(after.discovery_splash):
        changes.append("Discovery splash был изменён.")
    return changes


def actor_label(actor: discord.Member | None, fallback: str = "неизвестно") -> str:
    return actor.mention if actor is not None else fallback


# --- Слушатели событий основного лога ---

async def log_channel_create(channel: discord.abc.GuildChannel) -> None:
    guild = getattr(channel, "guild", None)
    if guild is None or not is_famq_activity_guild(guild.id):
        return

    actor, _entry = await fetch_optional_audit_executor(guild, "channel_create", target_id=channel.id)
    await send_activity_log(
        guild,
        title="Канал создан",
        description="└ канал был **создан**",
        fields=[
            ("Канал", format_channel_label(channel), True),
            ("Тип канала", f"`{format_channel_type_label(channel)}`", True),
            ("Создал", actor_label(actor), True),
        ],
        footer_parts=[f"ID канала: {channel.id}", format_log_time_msk()],
    )


async def log_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
    guild = getattr(after, "guild", None)
    if guild is None or not is_famq_activity_guild(guild.id):
        return
    changes = get_channel_update_changes(before, after)
    if not changes:
        return

    actor, _entry = await fetch_optional_audit_executor(guild, "channel_update", target_id=after.id)
    await send_activity_log(
        guild,
        title="Канал обновлён",
        description="└ канал был **изменён** на сервере",
        fields=[
            ("Канал", format_channel_label(after), True),
            ("Тип канала", f"`{format_channel_type_label(after)}`", True),
            ("Изменил", actor_label(actor), True),
            ("Изменения", "\n".join(f"• {line}" for line in changes[:12]), False),
        ],
        footer_parts=[f"ID канала: {after.id}", format_log_time_msk()],
    )


async def log_channel_delete(channel: discord.abc.GuildChannel) -> None:
    guild = getattr(channel, "guild", None)
    if guild is None or not is_famq_activity_guild(guild.id):
        return

    actor, _entry = await fetch_optional_audit_executor(guild, "channel_delete", target_id=channel.id)
    await send_activity_log(
        guild,
        title="Канал удалён",
        description="└ канал был **удалён**",
        fields=[
            ("Название", f"`{getattr(channel, 'name', 'неизвестно')}`", True),
            ("Тип канала", f"`{format_channel_type_label(channel)}`", True),
            ("Удалил", actor_label(actor), True),
        ],
        footer_parts=[f"ID канала: {channel.id}", format_log_time_msk()],
    )


async def log_role_create(role: discord.Role) -> None:
    if not is_famq_activity_guild(role.guild.id):
        return
    actor, _entry = await fetch_optional_audit_executor(role.guild, "role_create", target_id=role.id)
    await send_activity_log(
        role.guild,
        title="Роль создана",
        description="└ на сервере была **создана** роль",
        fields=[
            ("Роль", role.mention, True),
            ("Создал", actor_label(actor), True),
            ("Цвет", f"`{role.colour}`", True),
        ],
        footer_parts=[f"ID роли: {role.id}", format_log_time_msk()],
    )


async def log_role_update(before: discord.Role, after: discord.Role) -> None:
    if not is_famq_activity_guild(after.guild.id):
        return
    changes = get_role_update_changes(before, after)
    if not changes:
        return
    actor, _entry = await fetch_optional_audit_executor(after.guild, "role_update", target_id=after.id)
    await send_activity_log(
        after.guild,
        title="Роль обновлена",
        description="└ роль была **изменена**",
        fields=[
            ("Роль", after.mention, True),
            ("Изменил", actor_label(actor), True),
            ("Изменения", "\n".join(f"• {line}" for line in changes[:10]), False),
        ],
        footer_parts=[f"ID роли: {after.id}", format_log_time_msk()],
    )


async def log_role_delete(role: discord.Role) -> None:
    if not is_famq_activity_guild(role.guild.id):
        return
    actor, _entry = await fetch_optional_audit_executor(role.guild, "role_delete", target_id=role.id)
    await send_activity_log(
        role.guild,
        title="Роль удалена",
        description="└ роль была **удалена**",
        fields=[
            ("Роль", f"`{role.name}`", True),
            ("Удалил", actor_label(actor), True),
        ],
        footer_parts=[f"ID роли: {role.id}", format_log_time_msk()],
    )


async def log_thread_create(thread: discord.Thread) -> None:
    if not is_famq_activity_guild(thread.guild.id):
        return

    actor, _entry = await fetch_optional_audit_executor(thread.guild, "thread_create", target_id=thread.id)
    creator = actor.mention if actor is not None else (f"<@{thread.owner_id}>" if thread.owner_id else "неизвестно")
    await send_activity_log(
        thread.guild,
        title="Ветка создана",
        description="└ ветка была **создана**",
        fields=[
            ("Ветка", thread.mention if thread.mention else f"`{thread.name}`", True),
            ("Родительский канал", format_thread_parent_label(thread), True),
            ("Создал", creator, True),
        ],
        footer_parts=[f"ID ветки: {thread.id}", format_log_time_msk()],
    )


async def log_thread_update(before: discord.Thread, after: discord.Thread) -> None:
    if not is_famq_activity_guild(after.guild.id):
        return

    actor, _entry = await fetch_optional_audit_executor(after.guild, "thread_update", target_id=after.id)
    if before.archived != after.archived:
        await send_activity_log(
            after.guild,
            title="Архивация ветки",
            description=("└ ветка была **архивирована**" if after.archived else "└ ветка была **разархивирована**"),
            fields=[
                ("Ветка", after.mention if after.mention else f"`{after.name}`", True),
                ("Родительский канал", format_thread_parent_label(after), True),
                ("Изменил", actor_label(actor), True),
            ],
            footer_parts=[f"ID ветки: {after.id}", format_log_time_msk()],
        )
        return

    changes: list[str] = []
    if before.name != after.name:
        changes.append(f"Имя: `{before.name}` → `{after.name}`")
    if before.locked != after.locked:
        changes.append(f"Locked: `{before.locked}` → `{after.locked}`")
    if before.slowmode_delay != after.slowmode_delay:
        changes.append(f"Слоумод: `{before.slowmode_delay}` → `{after.slowmode_delay}`")
    if not changes:
        return
    await send_activity_log(
        after.guild,
        title="Ветка обновлена",
        description="└ ветка была **изменена**",
        fields=[
            ("Ветка", after.mention if after.mention else f"`{after.name}`", True),
            ("Родительский канал", format_thread_parent_label(after), True),
            ("Изменил", actor_label(actor), True),
            ("Изменения", "\n".join(f"• {line}" for line in changes), False),
        ],
        footer_parts=[f"ID ветки: {after.id}", format_log_time_msk()],
    )


async def log_thread_delete(thread: discord.Thread) -> None:
    if not is_famq_activity_guild(thread.guild.id):
        return

    actor, _entry = await fetch_optional_audit_executor(thread.guild, "thread_delete", target_id=thread.id)
    await send_activity_log(
        thread.guild,
        title="Ветка удалена",
        description="└ ветка была **удалена**",
        fields=[
            ("Ветка", f"`{thread.name}`", True),
            ("Родительский канал", format_thread_parent_label(thread), True),
            ("Удалил", actor_label(actor), True),
        ],
        footer_parts=[f"ID ветки: {thread.id}", format_log_time_msk()],
    )


async def log_message_edit(before: discord.Message, after: discord.Message) -> None:
    if before.author.bot or before.guild is None or not is_famq_activity_guild(before.guild.id):
        return
    if before.content == after.content:
        return

    await send_activity_log(
        before.guild,
        title="Сообщение изменено",
        description="└ сообщение было **изменено**",
        author_name=f"{before.author} ({before.author.id})",
        author_icon_url=before.author.display_avatar.url,
        fields=[
            ("Автор", before.author.mention, True),
            ("Канал", format_channel_label(before.channel), True),
            ("До изменения", format_text_block(before.content, fallback="Нет текста"), False),
            ("После изменения", format_text_block(after.content, fallback="Нет текста"), False),
        ],
        footer_parts=[
            f"ID пользователя: {before.author.id}",
            f"ID сообщения: {before.id}",
            format_log_time_msk(),
        ],
    )


async def log_message_delete(message: discord.Message) -> None:
    if message.author.bot or message.guild is None or not is_famq_activity_guild(message.guild.id):
        return

    await send_activity_log(
        message.guild,
        title="Сообщение удалено",
        description="└ сообщение было **удалено**",
        author_name=f"{message.author} ({message.author.id})",
        author_icon_url=message.author.display_avatar.url,
        fields=[
            ("Автор", message.author.mention, True),
            ("Канал", format_channel_label(message.channel), True),
            ("Содержимое удалённого сообщения", format_text_block(message.content, fallback="Нет текста"), False),
        ],
        footer_parts=[
            f"ID пользователя: {message.author.id}",
            f"ID сообщения: {message.id}",
            format_log_time_msk(),
        ],
    )


async def log_bulk_message_delete(messages: list[discord.Message]) -> None:
    if not messages:
        return
    sample = messages[0]
    if sample.guild is None or not is_famq_activity_guild(sample.guild.id):
        return
    actor, _entry = await fetch_optional_audit_executor(sample.guild, "message_bulk_delete", target_id=sample.channel.id)
    await send_activity_log(
        sample.guild,
        title="Массовое удаление сообщений",
        description="└ произошло **массовое удаление** сообщений",
        fields=[
            ("Канал", format_channel_label(sample.channel), True),
            ("Количество", f"`{len(messages)}`", True),
            ("Удалил", actor_label(actor), True),
        ],
        footer_parts=[format_log_time_msk()],
    )


async def log_pins_update(channel: discord.abc.GuildChannel, last_pin: datetime | None) -> None:
    guild = getattr(channel, "guild", None)
    if guild is None or not is_famq_activity_guild(guild.id):
        return

    await send_activity_log(
        guild,
        title="Обновлены закрепы",
        description="└ закреплённые сообщения в канале были **обновлены**",
        fields=[
            ("Канал", format_channel_label(channel), True),
            ("Последний закреп", format_datetime_msk(last_pin), True),
        ],
        footer_parts=[format_log_time_msk()],
    )


async def log_user_update(before: discord.User, after: discord.User) -> None:

    for guild in bot.guilds:
        if not is_famq_activity_guild(guild.id):
            continue
        member = guild.get_member(after.id)
        if member is None:
            continue

        if safe_asset_url(before.display_avatar) != safe_asset_url(after.display_avatar):
            await send_activity_log(
                guild,
                title="Смена аватара",
                description="└ пользователь сменил **аватар**",
                author_name=f"{after} ({after.id})",
                author_icon_url=after.display_avatar.url,
                fields=[("Пользователь", member.mention, True)],
                footer_parts=[f"ID пользователя: {after.id}", format_log_time_msk()],
                thumbnail_url=safe_asset_url(before.display_avatar),
                image_url=safe_asset_url(after.display_avatar),
            )

        if safe_asset_url(before.banner) != safe_asset_url(after.banner):
            await send_activity_log(
                guild,
                title="Смена баннера",
                description="└ пользователь сменил **баннер**",
                author_name=f"{after} ({after.id})",
                author_icon_url=after.display_avatar.url,
                fields=[("Пользователь", member.mention, True)],
                footer_parts=[f"ID пользователя: {after.id}", format_log_time_msk()],
                thumbnail_url=safe_asset_url(before.banner) or after.display_avatar.url,
                image_url=safe_asset_url(after.banner),
            )
        update_member_activity_profile(member)
    save_member_activity()


async def log_guild_update(before: discord.Guild, after: discord.Guild) -> None:
    if not is_famq_activity_guild(after.id):
        return
    changes = get_guild_update_changes(before, after)
    if not changes:
        return
    actor, _entry = await fetch_optional_audit_executor(after, "guild_update", target_id=after.id)
    await send_activity_log(
        after,
        title="Настройки сервера обновлены",
        description="└ настройки сервера были **изменены**",
        fields=[
            ("Сервер", f"`{after.name}`", True),
            ("Изменил", actor_label(actor), True),
            ("Изменения", "\n".join(f"• {line}" for line in changes[:12]), False),
        ],
        footer_parts=[f"ID сервера: {after.id}", format_log_time_msk()],
        thumbnail_url=safe_asset_url(before.icon) or safe_asset_url(after.icon),
        image_url=safe_asset_url(after.banner) or safe_asset_url(after.icon),
    )


async def log_member_update(before: discord.Member, after: discord.Member) -> None:

    if after.bot or not is_allowed_guild_id(after.guild.id):
        return

    if is_famq_activity_guild(after.guild.id):
        if before.nick != after.nick:
            actor, _entry = await fetch_optional_audit_executor(after.guild, "member_update", target_id=after.id)
            await send_activity_log(
                after.guild,
                title="Смена никнейма",
                description=f"{VOICE_EMOJI_RENAME_TEXT} пользователь сменил ник на сервере",
                author_name=f"{after} ({after.id})",
                author_icon_url=after.display_avatar.url,
                fields=[
                    ("Пользователь", after.mention, True),
                    ("Старый ник", f"`{before.display_name}`", True),
                    ("Новый ник", f"`{after.display_name}`", True),
                    ("Изменил", actor.mention if actor is not None else "сам пользователь", True),
                ],
                footer_parts=[f"ID пользователя: {after.id}", format_log_time_msk()],
            )

        before_role_ids = {role.id for role in before.roles if role != before.guild.default_role}
        after_role_ids = {role.id for role in after.roles if role != after.guild.default_role}
        if before_role_ids != after_role_ids:
            actor, _entry = await fetch_optional_audit_executor(after.guild, "member_role_update", target_id=after.id)
            added_roles = [
                role for role in after.roles if role.id not in before_role_ids and role != after.guild.default_role
            ]
            removed_roles = [
                role for role in before.roles if role.id not in after_role_ids and role != before.guild.default_role
            ]
            if added_roles:
                await send_activity_log(
                    after.guild,
                    title="Выдача ролей",
                    description="└ пользователю была **выдана** роль(и)",
                    author_name=f"{after} ({after.id})",
                    author_icon_url=after.display_avatar.url,
                    fields=[
                        ("Пользователь", after.mention, True),
                        ("Изменил", actor_label(actor), True),
                        ("Выданы", format_roles_label(added_roles), False),
                    ],
                    footer_parts=[f"ID пользователя: {after.id}", format_log_time_msk()],
                )
            if removed_roles:
                await send_activity_log(
                    after.guild,
                    title="Снятие ролей",
                    description="└ пользователю была **снята** роль(и)",
                    author_name=f"{after} ({after.id})",
                    author_icon_url=after.display_avatar.url,
                    fields=[
                        ("Пользователь", after.mention, True),
                        ("Изменил", actor_label(actor), True),
                        ("Сняты", format_roles_label(removed_roles), False),
                    ],
                    footer_parts=[f"ID пользователя: {after.id}", format_log_time_msk()],
                )

        before_timeout = get_member_timeout_until(before)
        after_timeout = get_member_timeout_until(after)
        if before_timeout != after_timeout:
            actor, _entry = await fetch_optional_audit_executor(after.guild, "member_update", target_id=after.id)
            if after_timeout is not None:
                duration_text = format_datetime_msk(after_timeout)
                description = f"{VOICE_EMOJI_LOCK_TEXT} пользователь был замучен"
            else:
                duration_text = "мут снят"
                description = f"{VOICE_EMOJI_ACCESS_TEXT} пользователю сняли мут"
            await send_activity_log(
                after.guild,
                title="Изменение timeout",
                description=description,
                author_name=f"{after} ({after.id})",
                author_icon_url=after.display_avatar.url,
                fields=[
                    ("Пользователь", after.mention, True),
                    ("Длительность", duration_text, True),
                    ("Изменил", actor_label(actor), True),
                ],
                footer_parts=[f"ID пользователя: {after.id}", format_log_time_msk()],
            )

        if safe_asset_url(getattr(before, "guild_avatar", None)) != safe_asset_url(getattr(after, "guild_avatar", None)):
            await send_activity_log(
                after.guild,
                title="Смена серверного аватара",
                description=f"{VOICE_EMOJI_ACCESS_TEXT} пользователь сменил серверный аватар",
                author_name=f"{after} ({after.id})",
                author_icon_url=after.display_avatar.url,
                fields=[("Пользователь", after.mention, True)],
                footer_parts=[f"ID пользователя: {after.id}", format_log_time_msk()],
                thumbnail_url=safe_asset_url(getattr(before, "guild_avatar", None)) or after.display_avatar.url,
                image_url=safe_asset_url(getattr(after, "guild_avatar", None)) or after.display_avatar.url,
            )

    update_member_activity_profile(after)
    save_member_activity()


def register_listeners() -> None:
    """Подписывает лог на события Discord.

    Используется add_listener, а не @bot.event: так на одно событие может быть
    несколько независимых подписчиков (лог, антинюк, приветствие).
    """
    bot.add_listener(log_channel_create, "on_guild_channel_create")
    bot.add_listener(log_channel_update, "on_guild_channel_update")
    bot.add_listener(log_channel_delete, "on_guild_channel_delete")
    bot.add_listener(log_role_create, "on_guild_role_create")
    bot.add_listener(log_role_update, "on_guild_role_update")
    bot.add_listener(log_role_delete, "on_guild_role_delete")
    bot.add_listener(log_thread_create, "on_thread_create")
    bot.add_listener(log_thread_update, "on_thread_update")
    bot.add_listener(log_thread_delete, "on_thread_delete")
    bot.add_listener(log_message_edit, "on_message_edit")
    bot.add_listener(log_message_delete, "on_message_delete")
    bot.add_listener(log_bulk_message_delete, "on_bulk_message_delete")
    bot.add_listener(log_pins_update, "on_guild_channel_pins_update")
    bot.add_listener(log_user_update, "on_user_update")
    bot.add_listener(log_guild_update, "on_guild_update")
    bot.add_listener(log_member_update, "on_member_update")
