"""Анти-спам, анти-рейд и анти-нюк.

Все счётчики — скользящие окна на deque: держим только события за последние
10 секунд, поэтому память не растёт даже при длительном рейде.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import timedelta
from typing import Any

import discord

from botcore import bot
from config import *
from embeds import build_security_embed
from logs import fetch_audit_executor
from projects import get_project, is_allowed_guild_id


join_cache: dict[int, deque[float]] = {}
user_message_cache: dict[tuple[int, int], deque[float]] = {}
admin_action_cache: dict[tuple[int, int], dict[str, deque[float]]] = {}
spam_action_cache: dict[tuple[int, int], float] = {}
kick_restriction_cache: dict[tuple[int, int], dict[str, Any]] = {}
join_alert_state: dict[int, dict[str, float]] = {}


def prune_deque(values: deque[float], window: float, now: float | None = None) -> None:
    current = now if now is not None else time.time()
    while values and current - values[0] > window:
        values.popleft()


def prune_security_caches(now: float | None = None) -> None:
    current = now if now is not None else time.time()
    for guild_id in list(join_cache.keys()):
        prune_deque(join_cache[guild_id], 10, current)
        if not join_cache[guild_id]:
            join_cache.pop(guild_id, None)

    for cache_key in list(user_message_cache.keys()):
        prune_deque(user_message_cache[cache_key], 10, current)
        if not user_message_cache[cache_key]:
            user_message_cache.pop(cache_key, None)

    for cache_key in list(admin_action_cache.keys()):
        action_map = admin_action_cache[cache_key]
        for action_name in list(action_map.keys()):
            prune_deque(action_map[action_name], 10, current)
            if not action_map[action_name]:
                action_map.pop(action_name, None)
        if not action_map:
            admin_action_cache.pop(cache_key, None)

    for cache_key in list(spam_action_cache.keys()):
        if current - spam_action_cache[cache_key] > 10:
            spam_action_cache.pop(cache_key, None)


def is_protected_target(guild: discord.Guild, user_id: int) -> bool:
    if bot.user is not None and user_id == bot.user.id:
        return True
    return guild.owner_id == user_id


def register_admin_action(guild_id: int, user_id: int, action_name: str, now: float | None = None) -> int:
    current = now if now is not None else time.time()
    prune_security_caches(current)
    action_map = admin_action_cache.setdefault((guild_id, user_id), {})
    action_queue = action_map.setdefault(action_name, deque())
    action_queue.append(current)
    prune_deque(action_queue, 10, current)
    return len(action_queue)


def register_user_message(guild_id: int, user_id: int, now: float | None = None) -> int:
    current = now if now is not None else time.time()
    prune_security_caches(current)
    message_queue = user_message_cache.setdefault((guild_id, user_id), deque())
    message_queue.append(current)
    prune_deque(message_queue, 10, current)
    return sum(1 for timestamp in message_queue if current - timestamp <= 5)


async def apply_timeout_to_member(member: discord.Member | None, duration_seconds: int, reason: str) -> bool:
    if member is None or member.guild is None or is_protected_target(member.guild, member.id):
        return False
    try:
        await member.timeout(timedelta(seconds=duration_seconds), reason=reason)
        return True
    except Exception:
        return False


async def resolve_security_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    project = get_project(guild)
    if project is None:
        return None
    try:
        channel = guild.get_channel(int(project["security_log_channel_id"])) or await guild.fetch_channel(
            int(project["security_log_channel_id"])
        )
    except Exception:
        return None
    return channel if isinstance(channel, discord.TextChannel) else None


async def send_security_log(
    guild: discord.Guild,
    *,
    title: str,
    color: int,
    user_id: int | None,
    action_label: str,
    count_label: str,
    result_label: str,
    extra_lines: list[str] | None = None,
    source_message_id: int | None = None,
    ping_alert_role: bool = False,
    view: discord.ui.View | None = None,
) -> None:
    channel = await resolve_security_log_channel(guild)
    if channel is None:
        return

    project = get_project(guild)
    alert_role_id = int(project["alert_role_id"]) if project and project.get("alert_role_id") else ALERT_ROLE_ID
    content = f"<@&{alert_role_id}>" if ping_alert_role else None
    log_view = view if view is not None else (
        SecurityActionView(user_id) if user_id and not is_protected_target(guild, user_id) else None
    )
    try:
        await channel.send(
            content=content,
            embed=build_security_embed(
                title=title,
                color=color,
                user_id=user_id,
                action_label=action_label,
                count_label=count_label,
                result_label=result_label,
                extra_lines=extra_lines,
                source_message_id=source_message_id,
            ),
            view=log_view,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
    except Exception:
        pass


# --- Ограничение модераторов ---

def role_has_moderation_power(role: discord.Role) -> bool:
    permissions = role.permissions
    return any(
        [
            permissions.administrator,
            permissions.kick_members,
            permissions.ban_members,
            permissions.manage_roles,
            getattr(permissions, "moderate_members", False),
            getattr(permissions, "mute_members", False),
        ]
    )


def get_removable_moderation_roles(member: discord.Member) -> list[discord.Role]:
    guild_me = member.guild.me
    if guild_me is None:
        return []

    removable_roles: list[discord.Role] = []
    for role in member.roles:
        if role == member.guild.default_role or role.managed:
            continue
        if not role_has_moderation_power(role):
            continue
        if role >= guild_me.top_role:
            continue
        removable_roles.append(role)
    return removable_roles


async def restrict_kick_actor(member: discord.Member) -> list[int]:
    if is_protected_target(member.guild, member.id):
        return []
    cache_key = (member.guild.id, member.id)
    if cache_key in kick_restriction_cache:
        return list(kick_restriction_cache[cache_key].get("roleIds", []))

    roles_to_remove = get_removable_moderation_roles(member)
    if not roles_to_remove:
        return []

    try:
        await member.remove_roles(*roles_to_remove, reason="Anti-nuke mass kick restriction")
    except Exception:
        return []

    role_ids = [role.id for role in roles_to_remove]
    kick_restriction_cache[cache_key] = {"roleIds": role_ids, "createdAt": time.time()}
    return role_ids


async def restore_kick_restriction(guild: discord.Guild, user_id: int, reason: str) -> bool:
    record = kick_restriction_cache.pop((guild.id, user_id), None)
    if not record:
        return False

    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            member = None
    if member is None or is_protected_target(guild, user_id):
        return False

    roles = [role for role_id in record.get("roleIds", []) if (role := guild.get_role(int(role_id))) is not None]
    if not roles:
        return False

    try:
        await member.add_roles(*roles, reason=reason)
        return True
    except Exception:
        return False


# --- Кнопки в логе безопасности ---

class SecurityActionView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id

    async def _guard(self, interaction: discord.Interaction) -> discord.Member | None:
        if interaction.guild is None:
            await interaction.response.send_message("Сервер не найден.", ephemeral=True)
            return None
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or not member.guild_permissions.manage_guild:
            await interaction.response.send_message("Недостаточно прав для использования этих кнопок.", ephemeral=True)
            return None
        target = interaction.guild.get_member(self.target_user_id)
        if target is None:
            try:
                target = await interaction.guild.fetch_member(self.target_user_id)
            except Exception:
                target = None
        if target is None:
            await interaction.response.send_message("Пользователь не найден на сервере.", ephemeral=True)
            return None
        if is_protected_target(interaction.guild, target.id):
            await interaction.response.send_message("К этому пользователю нельзя применять действия.", ephemeral=True)
            return None
        return target

    @discord.ui.button(label="Mute", emoji="🔇", style=discord.ButtonStyle.secondary)
    async def mute_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        target = await self._guard(interaction)
        if target is None:
            return
        applied = await apply_timeout_to_member(target, 10 * 60, f"Security log mute by {interaction.user}")
        await interaction.response.send_message(
            "Пользователь отправлен в timeout на 10 минут." if applied else "Не удалось выдать timeout.",
            ephemeral=True,
        )

    @discord.ui.button(label="Kick", emoji="🔨", style=discord.ButtonStyle.secondary)
    async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        target = await self._guard(interaction)
        if target is None:
            return
        try:
            await target.kick(reason=f"Security log kick by {interaction.user}")
            await interaction.response.send_message("Пользователь кикнут.", ephemeral=True)
        except Exception:
            await interaction.response.send_message("Не удалось кикнуть пользователя.", ephemeral=True)

    @discord.ui.button(label="Ban", emoji="🚫", style=discord.ButtonStyle.secondary)
    async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        target = await self._guard(interaction)
        if target is None:
            return
        try:
            await interaction.guild.ban(target, reason=f"Security log ban by {interaction.user}", delete_message_days=0)
            await interaction.response.send_message("Пользователь забанен.", ephemeral=True)
        except Exception:
            await interaction.response.send_message("Не удалось забанить пользователя.", ephemeral=True)


class KickRestrictionReviewView(discord.ui.View):
    def __init__(self, actor_id: int):
        super().__init__(timeout=None)
        self.actor_id = actor_id

    async def _guard(self, interaction: discord.Interaction) -> bool:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if interaction.guild is None or member is None or not member.guild_permissions.manage_guild:
            await interaction.response.send_message("Недостаточно прав для проверки.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Да, вернуть", style=discord.ButtonStyle.secondary, custom_id="kick_restriction_restore")
    async def restore_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        restored = await restore_kick_restriction(interaction.guild, self.actor_id, f"Approved by {interaction.user}")
        await interaction.response.edit_message(
            content=None,
            view=None,
            embed=build_security_embed(
                title="✅ Kick restriction approved",
                color=COLOR_SOFT,
                user_id=self.actor_id,
                action_label="Mass Kick Review",
                count_label="Manual check completed",
                result_label="Moderation roles restored" if restored else "Nothing to restore or restore failed",
                extra_lines=[f"Checked by: <@{interaction.user.id}>"],
            ),
        )

    @discord.ui.button(label="Нет, не возвращать", style=discord.ButtonStyle.secondary, custom_id="kick_restriction_keep")
    async def keep_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        # Кэш ограничений хранится по паре (guild_id, user_id): раньше здесь чистился
        # ключ из одного actor_id, поэтому запись оставалась и повторный масс-кик
        # того же модератора уже не приводил к новому ограничению.
        kick_restriction_cache.pop((interaction.guild.id, self.actor_id), None)
        await interaction.response.edit_message(
            content=None,
            view=None,
            embed=build_security_embed(
                title="🚫 Kick restriction rejected",
                color=COLOR_MUTED,
                user_id=self.actor_id,
                action_label="Mass Kick Review",
                count_label="Manual check completed",
                result_label="Moderation roles were not restored",
                extra_lines=[f"Checked by: <@{interaction.user.id}>"],
            ),
        )


# --- Реакции на превышение лимитов ---

async def maybe_restrict_kick_actor(guild: discord.Guild, kicked_user_id: int) -> None:
    actor, _entry = await fetch_audit_executor(guild, discord.AuditLogAction.kick, target_id=kicked_user_id)
    if actor is None or is_protected_target(guild, actor.id):
        return

    count = register_admin_action(guild.id, actor.id, "kick")
    limit = ANTI_NUKE_LIMITS["kick"]
    if count < limit or (guild.id, actor.id) in kick_restriction_cache:
        return

    removed_role_ids = await restrict_kick_actor(actor)
    removed_roles_text = ", ".join(f"<@&{role_id}>" for role_id in removed_role_ids) if removed_role_ids else "Роли не удалось снять"
    await send_security_log(
        guild,
        title="🛡️ Mass Kick Review Required",
        color=COLOR_MUTED,
        user_id=actor.id,
        action_label="Mass Kick",
        count_label=f"{count} / 10 sec (limit: {limit})",
        result_label=(
            "Kick/Ban/Mute roles temporarily removed until review"
            if removed_role_ids
            else "Limit reached, but roles could not be removed"
        ),
        extra_lines=[
            "Была ли это запланированная акция?",
            "Нажмите **Да**, чтобы вернуть функционал.",
            "Нажмите **Нет**, чтобы не возвращать функционал.",
            f"Removed roles: {removed_roles_text}",
            f"Last kicked user ID: `{kicked_user_id}`",
        ],
        ping_alert_role=True,
        view=KickRestrictionReviewView(actor.id),
    )


async def maybe_timeout_admin_actor(
    guild: discord.Guild,
    action_name: str,
    action_label: str,
    target_id: int | None = None,
) -> None:
    action_map = {
        "channel_delete": discord.AuditLogAction.channel_delete,
        "role_delete": discord.AuditLogAction.role_delete,
        "ban": discord.AuditLogAction.ban,
    }
    audit_action = action_map.get(action_name)
    if audit_action is None:
        return

    actor, _entry = await fetch_audit_executor(guild, audit_action, target_id=target_id)
    if actor is None or is_protected_target(guild, actor.id):
        return

    count = register_admin_action(guild.id, actor.id, action_name)
    limit = ANTI_NUKE_LIMITS[action_name]
    if count < limit:
        return

    applied = await apply_timeout_to_member(actor, 10 * 60, f"Anti-nuke trigger: {action_name}")
    removed_role_ids: list[int] = []
    if not applied:
        removed_role_ids = await restrict_kick_actor(actor)
    removed_roles_text = ", ".join(f"<@&{role_id}>" for role_id in removed_role_ids) if removed_role_ids else ""
    await send_security_log(
        guild,
        title="🧨 Anti-Nuke Triggered",
        color=COLOR_MUTED,
        user_id=actor.id,
        action_label=action_label,
        count_label=f"{count} / 10 sec (limit: {limit})",
        result_label=(
            "Timeout applied for 10 minutes"
            if applied
            else (
                "Timeout failed, moderation roles removed"
                if removed_role_ids
                else "Limit reached, but timeout/role removal could not be applied"
            )
        ),
        extra_lines=[
            line
            for line in (
                f"Target ID: `{target_id}`" if target_id else "",
                f"Removed roles: {removed_roles_text}" if removed_roles_text else "",
            )
            if line
        ],
        ping_alert_role=True,
    )


async def check_join_raid(member: discord.Member) -> None:
    current = time.time()
    prune_security_caches(current)
    guild_join_cache = join_cache.setdefault(member.guild.id, deque())
    guild_join_cache.append(current)
    prune_deque(guild_join_cache, 10, current)
    join_count = len(guild_join_cache)
    guild_alert_state = join_alert_state.setdefault(member.guild.id, {"warning": 0.0, "alert": 0.0})

    if join_count >= JOIN_THRESHOLD_ALERT and current - guild_alert_state["alert"] >= 10:
        guild_alert_state["alert"] = current
        applied = await apply_timeout_to_member(member, 10 * 60, "Anti-raid join spike")
        await send_security_log(
            member.guild,
            title="🚨 Possible raid detected",
            color=COLOR_MUTED,
            user_id=member.id,
            action_label="Join Raid",
            count_label=f"{join_count} / 10 sec",
            result_label="Alert sent + newcomer timeout 10 min" if applied else "High risk alert sent, timeout failed",
            extra_lines=[
                f"👥 Joins: {join_count} / 10 sec",
                "📊 Status: High Risk",
                f"🛡️ Auto-timeout: {'applied' if applied else 'failed'}",
            ],
            ping_alert_role=True,
        )

    if join_count >= JOIN_THRESHOLD_WARNING and current - guild_alert_state["warning"] >= 10:
        guild_alert_state["warning"] = current
        await send_security_log(
            member.guild,
            title="⚠️ Possible raid detected",
            color=COLOR_MUTED,
            user_id=member.id,
            action_label="Join Raid",
            count_label=f"{join_count} / 10 sec",
            result_label="Suspicious activity warning sent",
            extra_lines=[f"👥 Joins: {join_count} / 10 sec", "📊 Status: Suspicious"],
            ping_alert_role=True,
        )


async def handle_message_spam(message: discord.Message) -> bool:
    """Возвращает True, если сообщение было обработано как спам."""
    current = time.time()
    prune_security_caches(current)

    if message.author.bot or not isinstance(message.author, discord.Member) or message.guild is None:
        return False
    if not is_allowed_guild_id(message.guild.id):
        return False
    if is_protected_target(message.guild, message.author.id):
        return False

    count = register_user_message(message.guild.id, message.author.id, current)
    if count < SPAM_LIMIT:
        return False

    cache_key = (message.guild.id, message.author.id)
    if current - spam_action_cache.get(cache_key, 0.0) < 5:
        return True
    spam_action_cache[cache_key] = current

    purged_count = 0
    try:
        deleted_messages = await message.channel.purge(
            limit=10,
            check=lambda item: (
                item.author.id == message.author.id
                and current - item.created_at.timestamp() <= 5
            ),
        )
        purged_count = len(deleted_messages)
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        purged_count = 1

    applied = await apply_timeout_to_member(message.author, 60, "Anti-spam trigger")
    await send_security_log(
        message.guild,
        title="💬 Anti-Spam Triggered",
        color=COLOR_MUTED,
        user_id=message.author.id,
        action_label="Message Spam",
        count_label=f"{count} / 5 sec",
        result_label=(
            f"Purged {purged_count} messages + timeout 60 sec"
            if applied
            else f"Purged {purged_count} messages, timeout could not be applied"
        ),
        source_message_id=message.id,
        ping_alert_role=False,
    )
    return True


# --- Слушатели ---

async def on_channel_delete_security(channel: discord.abc.GuildChannel) -> None:
    guild = getattr(channel, "guild", None)
    if guild is None or not is_allowed_guild_id(guild.id):
        return
    await maybe_timeout_admin_actor(guild, "channel_delete", "Channel Delete", target_id=channel.id)


async def on_role_delete_security(role: discord.Role) -> None:
    if not is_allowed_guild_id(role.guild.id):
        return
    await maybe_timeout_admin_actor(role.guild, "role_delete", "Role Delete", target_id=role.id)


async def on_member_ban_security(guild: discord.Guild, user: discord.User | discord.Member) -> None:
    if not is_allowed_guild_id(guild.id):
        return
    await maybe_timeout_admin_actor(guild, "ban", "Member Ban", target_id=user.id)


def register_listeners() -> None:
    bot.add_listener(on_channel_delete_security, "on_guild_channel_delete")
    bot.add_listener(on_role_delete_security, "on_guild_role_delete")
    bot.add_listener(on_member_ban_security, "on_member_ban")
