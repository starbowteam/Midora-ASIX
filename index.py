"""Точка сборки бота: панели, события, команды и запуск.

Предметная логика живёт в отдельных модулях (заявки, голосовые комнаты,
розыгрыши, логи, безопасность, ники, приветствие); здесь только их соединение.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands

import logs
import moderation  # noqa: F401  — регистрирует префиксные команды
import security
import voice
import welcome
from applications_flow import (
    ApplicationCard,
    ApplicationPanelCard,
    FamqPanelView,
    apply_application_state,
    cleanup_resolved_application_channels,
    create_or_update_main_panel,
    refresh_pending_application_messages,
)
from botcore import INSTANCE_ID, bot, console_log
from channels import cleanup_bot_messages, delete_message_safely
from components import FamilyInfoCard, welcome_banner_file
from config import *
from embeds import build_restart_report_embed, build_restart_status_embed
from formatting import format_log_time_msk, text_sendable
from giveaways import GiveawayJoinView, GiveawayModal, ensure_giveaway_task
from nicknames import (
    NicknameSelfFixView,
    StaffPanelView,
    publish_staff_and_nickname_panels_later,
)
from projects import (
    get_manageable_application_options,
    get_project,
    get_project_panel_key,
    get_server_plain_label,
    has_application_control_access,
    is_allowed_guild_id,
    is_famq_activity_guild,
    member_has_any_role,
)
from state import (
    application_store,
    apply_default_closed_application_servers,
    giveaway_store,
    is_application_open,
    panel_store,
    reload_applications,
    reload_giveaways,
    reload_member_activity,
    reload_voice_rooms,
    record_member_join_activity,
    record_member_leave_activity,
    get_member_role_names,
    save_panels,
    storage,
)
from voice import VoiceControlCard, cleanup_stale_voice_rooms


startup_done = False
views_restored = False
command_sync_done = False
restart_task: asyncio.Task | None = None


# --- Перезапуск по расписанию ---

def get_next_restart_datetime(now: datetime | None = None) -> datetime:
    current = now if now is not None else datetime.now(MSK_TZ)
    current = current.replace(tzinfo=MSK_TZ) if current.tzinfo is None else current.astimezone(MSK_TZ)
    candidates = []
    for hour in RESTART_HOURS_MSK:
        candidate = current.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= current:
            candidate += timedelta(days=1)
        candidates.append(candidate)
    return min(candidates)


def restart_process_now() -> None:
    """Сбрасывает состояние на диск и перезапускает процесс."""
    storage.flush()
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *sys.argv])


async def restart_process_later() -> None:
    while True:
        try:
            now = datetime.now(MSK_TZ)
            next_restart = get_next_restart_datetime(now)
            delay_seconds = max((next_restart - now).total_seconds(), 1)
            console_log(f"Next bot restart scheduled at {next_restart.isoformat()}")
            await asyncio.sleep(delay_seconds)
            restart_process_now()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            console_log(f"Restart task failed: {error}")
            await asyncio.sleep(60)


def ensure_restart_task() -> None:
    global restart_task
    if restart_task is not None and not restart_task.done():
        return
    if restart_task is not None:
        try:
            exception = restart_task.exception()
        except Exception:
            exception = None
        if exception is not None:
            console_log(f"Restart task stopped with error: {exception}")
    restart_task = asyncio.create_task(restart_process_later())


# --- Проверки при старте ---

async def collect_restart_issues(guild: discord.Guild, setup_issues: list[str] | None = None) -> list[str]:
    project = get_project(guild)
    issues = list(setup_issues or [])
    if project is None:
        return issues

    me = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    permissions = me.guild_permissions if me is not None else None
    required_permissions = {
        "view_audit_log": "нет доступа к журналу аудита, анти-рейд не сможет видеть нарушителя",
        "manage_roles": "нет права управлять ролями, анти-рейд не сможет снять опасные роли",
        "moderate_members": "нет права выдавать timeout, анти-спам/анти-рейд не сможет мутить",
        "manage_channels": "нет права управлять каналами, заявки и голосовые комнаты могут тормозить или падать",
    }
    if permissions is None:
        issues.append("не удалось получить права бота на сервере")
    else:
        for permission_name, message in required_permissions.items():
            if not getattr(permissions, permission_name, False):
                issues.append(message)

    channel_checks = [
        ("канал отчётности перезапуска", project.get("restart_status_channel_id")),
        ("канал логов безопасности", project.get("security_log_channel_id")),
        ("канал логов заявок", project.get("application_log_channel_id")),
        ("канал панели заявок", project.get("panel_channel_id")),
    ]
    for label, channel_id in channel_checks:
        if not channel_id:
            issues.append(f"{label}: ID не настроен")
            continue
        try:
            channel = guild.get_channel(int(channel_id)) or await guild.fetch_channel(int(channel_id))
        except Exception:
            issues.append(f"{label}: не найден или нет доступа")
            continue
        if not text_sendable(channel):
            issues.append(f"{label}: канал не текстовый")

    category_id = project.get("application_category_id")
    if category_id:
        category = guild.get_channel(int(category_id))
        if category is None:
            issues.append(f"категория заявок {category_id} не найдена в кеше сервера")
        elif not isinstance(category, discord.CategoryChannel):
            issues.append(f"канал {category_id} не является категорией — заявки будут создаваться без категории")

    if not WELCOME_BANNER_PATH.exists():
        issues.append(f"нет файла картинки приветствия: {WELCOME_BANNER_PATH}")

    return issues


async def send_restart_status(guild: discord.Guild, setup_issues: list[str] | None = None) -> None:
    project = get_project(guild)
    if project is None:
        return
    try:
        channel = guild.get_channel(int(project["restart_status_channel_id"])) or await guild.fetch_channel(
            int(project["restart_status_channel_id"])
        )
    except Exception:
        return

    if not text_sendable(channel):
        return

    issues = await collect_restart_issues(guild, setup_issues)
    message = await channel.send(
        embed=build_restart_status_embed(
            project["project_name"],
            issues,
            instance_id=INSTANCE_ID,
            bot_user_id=bot.user.id if bot.user else None,
        )
    )
    if not issues or not isinstance(channel, discord.TextChannel):
        return

    report_embed = build_restart_report_embed(issues)
    try:
        thread = await channel.create_thread(
            name=f"restart-report-{datetime.now(MSK_TZ).strftime('%d-%m-%H-%M')}",
            message=message,
            auto_archive_duration=1440,
        )
        await thread.send(embed=report_embed)
    except Exception:
        try:
            await channel.send(embed=report_embed)
        except Exception:
            pass


# --- Панели ---

async def publish_family_info_panel(guild: discord.Guild) -> bool:
    """Публикует Components V2-карточку «Информация о семье».

    Карточка каждый раз пересоздаётся: её изображение приходит вложением, а при
    редактировании сообщения Discord теряет ранее прикреплённые файлы.
    """
    project = get_project(guild)
    channel_id = project.get("family_info_channel_id") if project else None
    if not channel_id:
        return False
    try:
        channel = guild.get_channel(int(channel_id)) or await guild.fetch_channel(int(channel_id))
    except Exception as error:
        console_log(f"Family info channel fetch failed: {error}")
        return False
    if not isinstance(channel, discord.TextChannel):
        console_log(f"Family info channel is not text channel: {type(channel)!r}")
        return False

    # Файла картинки может не быть (например, папку assets не задеплоили) —
    # тогда карточка публикуется с фирменным баннером вместо вложения.
    banner_file = welcome_banner_file()
    if banner_file is None:
        console_log(f"Family info panel: banner file missing at {WELCOME_BANNER_PATH}, using brand banner")

    # file=None discord.py трактует как настоящее вложение, поэтому параметр
    # передаётся только когда файл есть.
    extra = {"file": banner_file} if banner_file is not None else {}
    try:
        await cleanup_bot_messages(channel)
        message = await channel.send(view=FamilyInfoCard(), **extra)
        panel_store[get_project_panel_key(FAMILY_INFO_PANEL_KEY, guild.id)] = {
            "messageId": message.id,
            "channelId": channel.id,
        }
        save_panels()
        console_log(f"Family info panel published to {channel.id}")
        return True
    except Exception as error:
        console_log(f"Family info panel publish failed: {error}")
        return False


async def disable_legacy_info_panel(guild: discord.Guild) -> None:
    """Старая текстовая инфо-панель больше не публикуется — чистим её канал."""
    project = get_project(guild)
    if project is None or not project.get("info_channel_id"):
        return
    try:
        channel = guild.get_channel(int(project["info_channel_id"])) or await guild.fetch_channel(
            int(project["info_channel_id"])
        )
    except Exception:
        channel = None

    if isinstance(channel, discord.TextChannel):
        await cleanup_bot_messages(channel)
    panel_store.pop(get_project_panel_key(INFO_PANEL_KEY, guild.id), None)
    save_panels()


async def create_or_update_voice_panel(guild: discord.Guild, force_recreate: bool = False) -> bool:
    project = get_project(guild)
    if project is None:
        return False
    try:
        channel = guild.get_channel(int(project["voice_panel_channel_id"])) or await guild.fetch_channel(
            int(project["voice_panel_channel_id"])
        )
    except Exception:
        return False
    if not isinstance(channel, discord.TextChannel):
        return False

    if force_recreate:
        await cleanup_bot_messages(channel)

    panel_key = get_project_panel_key(VOICE_PANEL_KEY, guild.id)
    stored = panel_store.get(panel_key, {})
    message_id = stored.get("messageId")

    if not force_recreate and message_id:
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(content=None, embed=None, view=VoiceControlCard())
            return False
        except Exception:
            pass

    if message_id:
        try:
            old_message = await channel.fetch_message(int(message_id))
            await delete_message_safely(old_message)
        except Exception:
            pass

    message = await channel.send(view=VoiceControlCard())
    panel_store[panel_key] = {"messageId": message.id, "channelId": channel.id}
    save_panels()
    return True


async def notify_unwhitelisted_guild(guild: discord.Guild) -> None:
    notice = (
        "Этот сервер отсутствует в whitelist бота.\n"
        f"Чтобы получить доступ, свяжитесь с: `{WHITELIST_CONTACT_ID}`"
    )
    candidate_channels: list[discord.abc.MessageableChannel] = []
    if isinstance(guild.system_channel, discord.TextChannel):
        candidate_channels.append(guild.system_channel)
    for channel in guild.text_channels:
        if channel not in candidate_channels:
            candidate_channels.append(channel)

    for channel in candidate_channels:
        try:
            await channel.send(notice)
            return
        except Exception:
            continue


async def restore_persistent_views() -> None:
    global views_restored
    if views_restored:
        return

    for guild_id in PROJECT_GUILD_IDS:
        stored = panel_store.get(get_project_panel_key(PANEL_KEY, guild_id), {})
        message_id = stored.get("messageId")
        if message_id:
            if guild_id == FAMQ_GUILD_ID:
                bot.add_view(ApplicationPanelCard(guild_id), message_id=int(message_id))
            else:
                bot.add_view(FamqPanelView(guild_id), message_id=int(message_id))

    bot.add_view(StaffPanelView())
    bot.add_view(VoiceControlCard())
    bot.add_view(NicknameSelfFixView())

    reload_applications()
    reload_giveaways()
    reload_voice_rooms()
    reload_member_activity()

    for app in application_store.get("items", {}).values():
        if app.get("status") != "pending":
            continue
        message_id = app.get("applicationMessageId")
        if message_id:
            bot.add_view(ApplicationCard(app), message_id=int(message_id))

    for giveaway in giveaway_store.get("items", {}).values():
        if giveaway.get("status") != "active":
            continue
        message_id = giveaway.get("messageId")
        if message_id:
            bot.add_view(GiveawayJoinView(int(giveaway["id"])), message_id=int(message_id))
        ensure_giveaway_task(int(giveaway["id"]))

    views_restored = True


# --- Слэш-команды ---

@bot.tree.command(
    name="giveaway",
    description="Создать розыгрыш. Время указывается в часах, через столько часов бот завершит розыгрыш.",
)
@app_commands.guilds(*GUILD_SCOPES)
@app_commands.default_permissions(manage_messages=True)
async def giveaway_command(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.guild.id != FAMQ_GUILD_ID:
        await interaction.response.send_message("На этом сервере розыгрыши пока не настроены.", ephemeral=True)
        return
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if member is None or not member.guild_permissions.manage_messages:
        await interaction.response.send_message("Недостаточно прав для создания розыгрыша.", ephemeral=True)
        return
    await interaction.response.send_modal(GiveawayModal())


@bot.tree.command(name="restart", description="Перезапустить бота.")
@app_commands.guilds(*GUILD_SCOPES)
async def restart_command(interaction: discord.Interaction) -> None:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if member is None or not member_has_any_role(member, [RESTART_COMMAND_ROLE_ID]):
        await interaction.response.send_message(
            f"Команда доступна только участникам с ролью <@&{RESTART_COMMAND_ROLE_ID}>.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    await interaction.response.send_message("Перезапускаю бота...", ephemeral=True)
    console_log(f"Manual restart requested by {interaction.user} ({interaction.user.id})")
    if interaction.guild is not None:
        await logs.send_application_log(
            interaction.guild,
            title="Перезапуск бота",
            description="└ бот перезапущен командой `/restart`",
            fields=[("Инициатор", interaction.user.mention, True)],
            actor=interaction.user,
        )
    # Небольшая пауза, чтобы Discord успел доставить ответ до подмены процесса.
    await asyncio.sleep(1.5)
    restart_process_now()


class ApplicationsGroup(app_commands.Group):
    """Управление приёмом заявок: открыть, закрыть, статус, панель."""

    def __init__(self) -> None:
        super().__init__(name="applications", description="Управление приёмом заявок семьи")

    @staticmethod
    async def _resolve(interaction: discord.Interaction, server: Optional[str]) -> tuple[bool, str]:
        """Проверяет права и возвращает (ok, направление)."""
        if interaction.guild is None:
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return False, ""

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not has_application_control_access(member, guild_id=interaction.guild.id):
            await interaction.response.send_message(
                "У вас нет прав для управления заявками.", ephemeral=True
            )
            return False, ""

        options = get_manageable_application_options(interaction.guild.id)
        if not options:
            await interaction.response.send_message(
                "Для этого сервера нет доступных направлений заявок.", ephemeral=True
            )
            return False, ""

        available_keys = [option["key"] for option in options]
        selected = server or available_keys[0]
        if selected not in available_keys:
            await interaction.response.send_message(
                "Неизвестное направление. Доступно: " + ", ".join(f"`{key}`" for key in available_keys),
                ephemeral=True,
            )
            return False, ""
        return True, selected

    async def server_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild_id = interaction.guild.id if interaction.guild else None
        return [
            app_commands.Choice(name=option["label"], value=option["key"])
            for option in get_manageable_application_options(guild_id)
            if current.lower() in option["label"].lower()
        ][:25]

    @app_commands.command(name="open", description="Открыть приём заявок")
    @app_commands.describe(
        server="Направление заявок (по умолчанию первое доступное)",
        announce="Объявить об открытии в канале анонсов",
    )
    async def open_applications(
        self,
        interaction: discord.Interaction,
        server: Optional[str] = None,
        announce: bool = True,
    ) -> None:
        ok, selected = await self._resolve(interaction, server)
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)
        await apply_application_state(interaction.guild, selected, True, announce=announce)
        await interaction.followup.send(
            f"Набор на «{get_server_plain_label(selected)}» открыт"
            + (" и объявлен." if announce else " (без анонса)."),
            ephemeral=True,
        )

    @app_commands.command(name="close", description="Закрыть приём заявок")
    @app_commands.describe(
        server="Направление заявок (по умолчанию первое доступное)",
        announce="Объявить о закрытии в канале анонсов",
    )
    async def close_applications(
        self,
        interaction: discord.Interaction,
        server: Optional[str] = None,
        announce: bool = True,
    ) -> None:
        ok, selected = await self._resolve(interaction, server)
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)
        await apply_application_state(interaction.guild, selected, False, announce=announce)
        await interaction.followup.send(
            f"Набор на «{get_server_plain_label(selected)}» закрыт"
            + (" и объявлен." if announce else " (без анонса)."),
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Показать статус приёма заявок и активные заявки")
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not has_application_control_access(member, guild_id=interaction.guild.id):
            await interaction.response.send_message("У вас нет прав для управления заявками.", ephemeral=True)
            return

        reload_applications()
        pending = [
            app
            for app in application_store.get("items", {}).values()
            if int(app.get("guildId", 0)) == interaction.guild.id and app.get("status") == "pending"
        ]
        lines = [
            f"{'🟢' if is_application_open(option['key'], interaction.guild.id) else '🔴'} "
            f"**{option['label']}** — {'открыт' if is_application_open(option['key'], interaction.guild.id) else 'закрыт'}"
            for option in get_manageable_application_options(interaction.guild.id)
        ]
        claimed = sum(1 for app in pending if int(app.get("claimedBy") or 0))
        lines.append("")
        lines.append(f"Заявок в работе: **{len(pending)}** (закреплено за рекрутерами: **{claimed}**)")
        await interaction.response.send_message("\n".join(lines) or "Нет направлений.", ephemeral=True)

    @app_commands.command(name="panel", description="Пересоздать панель подачи заявок")
    async def panel(self, interaction: discord.Interaction) -> None:
        ok, _selected = await self._resolve(interaction, None)
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)
        await create_or_update_main_panel(interaction.guild, force_recreate=True)
        await interaction.followup.send("Панель заявок пересоздана.", ephemeral=True)


applications_group = ApplicationsGroup()
applications_group.open_applications.autocomplete("server")(applications_group.server_autocomplete)
applications_group.close_applications.autocomplete("server")(applications_group.server_autocomplete)
bot.tree.add_command(applications_group, guild=discord.Object(id=FAMQ_GUILD_ID))


# --- События ---

async def on_member_join_main(member: discord.Member) -> None:
    if member.bot or not is_allowed_guild_id(member.guild.id):
        return

    record_member_join_activity(member)
    await security.check_join_raid(member)

    if is_famq_activity_guild(member.guild.id):
        from formatting import format_datetime_msk

        await logs.send_activity_log(
            member.guild,
            title="Участник присоединился",
            description=f"{EMOJI_ACCEPT_TEXT} пользователь зашёл на сервер",
            author_name=f"{member} ({member.id})",
            author_icon_url=member.display_avatar.url,
            fields=[
                ("Пользователь", member.mention, True),
                ("Аккаунт создан", format_datetime_msk(member.created_at), True),
            ],
            footer_parts=[f"ID пользователя: {member.id}", format_log_time_msk()],
            thumbnail_url=member.display_avatar.url,
        )


async def on_member_remove_main(member: discord.Member) -> None:
    guild = member.guild
    if not is_allowed_guild_id(guild.id):
        return

    reason, actor_id, audit_reason = await logs.detect_leave_reason(guild, member.id)
    record_member_leave_activity(
        guild.id,
        member.id,
        reason=reason,
        actor_id=actor_id,
        audit_reason=audit_reason,
        role_names=get_member_role_names(member),
        nickname=member.display_name,
        username=member.name,
        global_name=member.global_name or "",
    )
    if is_famq_activity_guild(guild.id) and reason != "ban":
        description = "└ пользователь покинул сервер"
        if reason == "kick":
            description = "└ пользователь был **кикнут** с сервера"
        fields = [("Пользователь", member.mention, True)]
        if actor_id:
            fields.append(("Изменил", f"<@{actor_id}>", True))
        if audit_reason:
            fields.append(("Причина", audit_reason, False))
        await logs.send_activity_log(
            guild,
            title="Выход с сервера",
            description=description,
            author_name=f"{member} ({member.id})",
            author_icon_url=member.display_avatar.url,
            fields=fields,
            footer_parts=[f"ID пользователя: {member.id}", format_log_time_msk()],
        )
    await security.maybe_restrict_kick_actor(guild, member.id)


async def on_member_ban_main(guild: discord.Guild, user: discord.User | discord.Member) -> None:
    if not is_allowed_guild_id(guild.id):
        return
    record_member_leave_activity(
        guild.id,
        user.id,
        reason="ban",
        role_names=get_member_role_names(user) if isinstance(user, discord.Member) else None,
        nickname=user.display_name if isinstance(user, discord.Member) else "",
        username=user.name,
        global_name=getattr(user, "global_name", "") or "",
    )
    if is_famq_activity_guild(guild.id):
        actor, entry = await logs.fetch_optional_audit_executor(guild, "ban", target_id=user.id)
        fields = [("Пользователь", f"<@{user.id}>", True)]
        if actor is not None:
            fields.append(("Изменил", actor.mention, True))
        if entry is not None and entry.reason:
            fields.append(("Причина", entry.reason, False))
        await logs.send_activity_log(
            guild,
            title="Бан участника",
            description="└ пользователь был **забанен**",
            author_name=f"{user} ({user.id})",
            author_icon_url=getattr(user.display_avatar, "url", None),
            fields=fields,
            footer_parts=[f"ID пользователя: {user.id}", format_log_time_msk()],
        )


async def on_message_main(message: discord.Message) -> None:
    if message.author.bot or message.guild is None:
        return

    if not is_allowed_guild_id(message.guild.id):
        if message.content.startswith(str(bot.command_prefix)):
            try:
                await message.channel.send(
                    "Этот сервер отсутствует в whitelist бота. "
                    f"Чтобы получить доступ, свяжитесь с: `{WHITELIST_CONTACT_ID}`"
                )
            except Exception:
                pass
        return

    await security.handle_message_spam(message)


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    if is_allowed_guild_id(guild.id):
        return
    await notify_unwhitelisted_guild(guild)


@bot.event
async def on_ready() -> None:
    global startup_done, command_sync_done
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="discord.gg/diamondshop")
    )
    ensure_restart_task()
    await restore_persistent_views()

    if startup_done:
        console_log(f"Reconnected as {bot.user}")
        return

    apply_default_closed_application_servers()

    if not command_sync_done:
        total_synced = 0
        for guild_scope in GUILD_SCOPES:
            synced = await bot.tree.sync(guild=guild_scope)
            total_synced += len(synced)
        console_log(f"Slash commands synced: {total_synced}")
        command_sync_done = True

    for guild in list(bot.guilds):
        if not is_allowed_guild_id(guild.id):
            await notify_unwhitelisted_guild(guild)

    for guild_id in PROJECT_GUILD_IDS:
        guild = bot.get_guild(guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(guild_id)
            except Exception:
                console_log(f"Project setup skipped: guild {guild_id} not found.")
                continue

        if guild.id == FAMQ_GUILD_ID:
            asyncio.create_task(publish_staff_and_nickname_panels_later(guild))

        setup_issues: list[str] = []
        created_main = await create_or_update_main_panel(guild, force_recreate=True)
        await disable_legacy_info_panel(guild)
        created_voice = await create_or_update_voice_panel(guild, force_recreate=True)
        if guild.id == FAMQ_GUILD_ID and not await publish_family_info_panel(guild):
            setup_issues.append(
                f"карточка «Информация о семье» не опубликована: проверьте канал {FAMQ_FAMILY_INFO_CHANNEL_ID}"
            )
        await cleanup_stale_voice_rooms(guild)
        cleaned_resolved = await cleanup_resolved_application_channels(guild)
        refreshed_applications = await refresh_pending_application_messages(guild)
        await send_restart_status(guild, setup_issues)

        project = get_project(guild)
        project_name = project.get("project_name", str(guild.id)) if project else str(guild.id)
        console_log(f"{project_name} main panel " + ("created" if created_main else "updated"))
        console_log(f"{project_name} voice panel " + ("created" if created_voice else "updated"))
        console_log(f"{project_name} resolved application channels cleaned: {cleaned_resolved}")
        console_log(f"{project_name} pending applications refreshed: {refreshed_applications}")

    # Строка намеренно подробная: если в логах хостинга она встречается дважды
    # с разными instance, значит запущены две копии бота — отсюда дубли сообщений.
    console_log(
        f"Logged in as {bot.user} (id {bot.user.id if bot.user else '?'}) | "
        f"build {BUILD_TAG} | instance {INSTANCE_ID} | pid {os.getpid()}"
    )
    startup_done = True


listeners_registered = False


def register_listeners() -> None:
    """Подписывает модули на события Discord — ровно один раз.

    Повторный вызов раньше добавлял вторую копию каждого обработчика, и бот
    отправлял каждое сообщение о входе или выходе дважды.
    """
    global listeners_registered
    if listeners_registered:
        console_log("Listeners are already registered, skipping duplicate registration")
        return

    logs.register_listeners()
    security.register_listeners()
    voice.register_listeners()
    welcome.register_listeners()
    bot.add_listener(on_member_join_main, "on_member_join")
    bot.add_listener(on_member_remove_main, "on_member_remove")
    bot.add_listener(on_member_ban_main, "on_member_ban")
    bot.add_listener(on_message_main, "on_message")
    listeners_registered = True


register_listeners()


def main() -> None:
    console_log(f"Starting bot: build {BUILD_TAG} | instance {INSTANCE_ID} | pid {os.getpid()}")
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN или FAMQ_BOT_TOKEN не задан в .env")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
