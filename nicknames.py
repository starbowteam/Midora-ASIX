"""Система ников по форме и панель состава семьи."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import discord

from applications import build_formatted_nickname
from botcore import bot, console_log
from channels import cleanup_bot_messages, ensure_guild_members_loaded
from config import *
from embeds import build_nickname_report_embed, build_staff_panel_embed, make_embed
from projects import get_guild_emoji_text
from state import panel_store, save_panels


STAFF_ROLE_GROUPS = (
    ("Leaders", FAMQ_FRIEND_VERIFY_ROLE_1_ID, EMOJI_STAFF_LEADERS_ID),
    ("Dep Leaders", FAMQ_DEP_LEADER_ROLE_ID, EMOJI_STAFF_DEP_LEADERS_ID),
    ("Chief Recruit", CHIEF_RECRUIT_ROLE_ID, EMOJI_STAFF_CHIEF_RECRUIT_ID),
    ("Curators", FAMQ_CURATOR_ROLE_ID, EMOJI_STAFF_CURATORS_ID),
    ("Dep Chief Recruit", DEP_CHIEF_RECRUIT_ROLE_ID, EMOJI_STAFF_DEP_CHIEF_RECRUIT_ID),
    ("Boss", FAMQ_BOSS_ROLE_ID, EMOJI_STAFF_BOSS_ID),
    ("High", FAMQ_HIGH_ROLE_ID, EMOJI_STAFF_HIGH_ID),
    ("Recruits", FAMQ_RECRUITER_ROLE_ID, EMOJI_STAFF_RECRUITS_ID),
)

# Первый подходящий префикс — приоритетный. Это предотвращает неверный формат
# у участников, у которых одновременно несколько ролей.
# Ограничители рассылки напоминаний о никах: она идёт в фоне и не должна
# съедать лимит запросов, от которого зависит отклик кнопок.
NICKNAME_DM_DELAY_SECONDS = 1.5
NICKNAME_DM_LIMIT_PER_RUN = 25

NICKNAME_RULES: tuple[tuple[int, str], ...] = (
    (CHIEF_RECRUIT_ROLE_ID, "Chief Rec"),
    (DEP_CHIEF_RECRUIT_ROLE_ID, "Dcr"),
    (FAMQ_DEP_LEADER_ROLE_ID, "Dep"),
    (FAMQ_CURATOR_ROLE_ID, "Curator"),
    (FAMQ_BOSS_ROLE_ID, "Boss"),
    (FAMQ_HIGH_ROLE_ID, "High"),
    (FAMQ_RECRUITER_ROLE_ID, "Rec"),
    (APPLICATION_EXTRA_ACCEPT_ROLE_ID, "ASX"),
)


def get_required_nickname_prefix(member: discord.Member) -> str | None:
    role_ids = {role.id for role in member.roles}
    for role_id, prefix in NICKNAME_RULES:
        if int(role_id) in role_ids:
            return prefix
    return None


def nickname_matches_required_format(member: discord.Member, prefix: str) -> bool:
    pattern = rf"^{re.escape(prefix)}\s*\|\s*.+?\s*\|\s*\d{{1,12}}$"
    return re.match(pattern, member.display_name.strip(), flags=re.IGNORECASE) is not None


def get_members_with_bad_nicknames(guild: discord.Guild) -> list[tuple[discord.Member, str]]:
    bad_members: list[tuple[discord.Member, str]] = []
    for member in guild.members:
        if member.bot:
            continue
        prefix = get_required_nickname_prefix(member)
        if prefix is None:
            continue
        if not nickname_matches_required_format(member, prefix):
            bad_members.append((member, prefix))
    bad_members.sort(key=lambda item: (item[1], item[0].display_name.lower()))
    return bad_members


def build_nickname_dm_embed(guild: discord.Guild, prefix: str) -> discord.Embed:
    line_emoji = get_guild_emoji_text(guild, EMOJI_WELCOME_LINE_ID, "•")
    return make_embed(
        title="Проверьте никнейм",
        description="\n".join(
            [
                f"{line_emoji} Ваш никнейм на сервере ASIXEZ сейчас не соответствует форме.",
                f"{line_emoji} Нужный формат: **`{prefix} | Имя РЛ | Статик`**",
                "",
                "Нажмите кнопку ниже, укажите имя РЛ и Static-ID — бот сам поставит правильный никнейм.",
            ]
        ),
        color=COLOR_MUTED,
        timestamp=datetime.now(timezone.utc),
    )


async def get_famq_member_for_interaction(interaction: discord.Interaction) -> discord.Member | None:
    guild = interaction.guild or bot.get_guild(FAMQ_GUILD_ID)
    if guild is None:
        return None
    member = guild.get_member(interaction.user.id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(interaction.user.id)
    except Exception:
        return None


class NicknameSelfFixModal(discord.ui.Modal):
    def __init__(self, prefix: str):
        super().__init__(title="Изменить себе никнейм", timeout=180)
        self.prefix = prefix
        self.irl_name = discord.ui.TextInput(label="Ваше имя в РЛ", max_length=40, required=True)
        self.static_id = discord.ui.TextInput(label="Ваш Static-ID", max_length=20, required=True)
        self.add_item(self.irl_name)
        self.add_item(self.static_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        member = await get_famq_member_for_interaction(interaction)
        if member is None:
            await interaction.response.send_message("Не удалось найти вас на сервере ASIXEZ.", ephemeral=True)
            return

        prefix = get_required_nickname_prefix(member)
        if prefix is None:
            await interaction.response.send_message("Для ваших ролей форма ника не требуется.", ephemeral=True)
            return

        static_id = re.sub(r"\D+", "", str(self.static_id))
        if not static_id:
            await interaction.response.send_message("Static-ID должен содержать цифры.", ephemeral=True)
            return

        new_nick = build_formatted_nickname(prefix, str(self.irl_name), static_id)
        try:
            await member.edit(nick=new_nick, reason="Nickname self-fix form")
        except Exception:
            await interaction.response.send_message(
                "Не удалось изменить ник. Проверьте, что роль бота выше вашей роли, или обратитесь к старшему.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(f"Готово, поставил ник: **{new_nick}**", ephemeral=True)


class NicknameSelfFixView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Изменить себе никнейм",
        style=discord.ButtonStyle.secondary,
        custom_id="famq_self_fix_nickname",
    )
    async def fix_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        member = await get_famq_member_for_interaction(interaction)
        if member is None:
            await interaction.response.send_message("Не удалось найти вас на сервере ASIXEZ.", ephemeral=True)
            return
        prefix = get_required_nickname_prefix(member)
        if prefix is None:
            await interaction.response.send_message("Для ваших ролей форма ника не требуется.", ephemeral=True)
            return
        await interaction.response.send_modal(NicknameSelfFixModal(prefix))


class StaffPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Обновить", style=discord.ButtonStyle.secondary, custom_id="famq_staff_panel_refresh")
    async def refresh(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if interaction.guild is None or interaction.guild.id != FAMQ_GUILD_ID:
            await interaction.response.send_message("Эта кнопка доступна только на сервере ASIXEZ.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await ensure_guild_members_loaded(interaction.guild)
        if interaction.message is not None:
            await interaction.message.edit(
                embed=build_staff_panel_embed(interaction.guild, STAFF_ROLE_GROUPS, get_guild_emoji_text),
                view=StaffPanelView(),
            )
        await interaction.followup.send("Состав обновлён.", ephemeral=True)


async def send_nickname_fix_dms(guild: discord.Guild, bad_members: list[tuple[discord.Member, str]]) -> int:
    """Уведомляет каждого участника с неправильным ником один раз для текущей формы."""
    state = panel_store.setdefault("famq_nickname_notice_state", {})
    if not isinstance(state, dict):
        state = {}
        panel_store["famq_nickname_notice_state"] = state

    active_user_ids = {str(member.id) for member, _prefix in bad_members}
    changed = False
    for user_id in list(state):
        if user_id not in active_user_ids:
            state.pop(user_id, None)
            changed = True

    sent_count = 0
    for member, prefix in bad_members:
        if sent_count >= NICKNAME_DM_LIMIT_PER_RUN:
            console_log(
                f"Nickname DM limit reached ({NICKNAME_DM_LIMIT_PER_RUN}); "
                f"остальные {len(bad_members) - sent_count} участников получат напоминание позже"
            )
            break

        user_id = str(member.id)
        if state.get(user_id) == prefix:
            continue
        try:
            await member.send(embed=build_nickname_dm_embed(guild, prefix), view=NicknameSelfFixView())
        except Exception:
            # Если ЛС закрыты, оставляем участника без отметки — при следующем
            # обновлении отчёта бот повторит попытку.
            continue
        state[user_id] = prefix
        changed = True
        sent_count += 1
        # Создание личного канала — один из самых жёстко ограниченных запросов
        # Discord. Рассылка идёт в фоне, поэтому спокойно растягиваем её во
        # времени: иначе очередь запросов упирается в общий лимит и нажатия
        # кнопок перестают укладываться в отведённые три секунды.
        await asyncio.sleep(NICKNAME_DM_DELAY_SECONDS)

    if changed:
        save_panels()
    return sent_count


async def publish_staff_panel(guild: discord.Guild) -> bool:
    if guild.id != FAMQ_GUILD_ID:
        return False
    try:
        channel = guild.get_channel(FAMQ_STAFF_PANEL_CHANNEL_ID) or await guild.fetch_channel(FAMQ_STAFF_PANEL_CHANNEL_ID)
    except Exception as error:
        console_log(f"Staff panel channel fetch failed: {error}")
        return False
    if not isinstance(channel, discord.TextChannel):
        console_log(f"Staff panel channel is not text channel: {type(channel)!r}")
        return False

    try:
        await cleanup_bot_messages(channel)
        await channel.send(
            embed=build_staff_panel_embed(guild, STAFF_ROLE_GROUPS, get_guild_emoji_text),
            view=StaffPanelView(),
        )
        console_log(f"Staff panel published to {channel.id}")
        return True
    except Exception as error:
        console_log(f"Staff panel publish failed: {error}")
        return False


async def publish_nickname_report(guild: discord.Guild) -> bool:
    if guild.id != FAMQ_GUILD_ID:
        return False
    try:
        channel = guild.get_channel(FAMQ_NICKNAME_REPORT_CHANNEL_ID) or await guild.fetch_channel(
            FAMQ_NICKNAME_REPORT_CHANNEL_ID
        )
    except Exception as error:
        console_log(f"Nickname report channel fetch failed: {error}")
        return False
    if not isinstance(channel, discord.TextChannel):
        console_log(f"Nickname report channel is not text channel: {type(channel)!r}")
        return False

    try:
        bad_members = get_members_with_bad_nicknames(guild)
        await cleanup_bot_messages(channel)
        content = " ".join(member.mention for member, _prefix in bad_members[:35]) if bad_members else None
        await channel.send(
            content=content,
            embed=build_nickname_report_embed(bad_members),
            view=NicknameSelfFixView() if bad_members else None,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        dm_count = await send_nickname_fix_dms(guild, bad_members)
        console_log(
            f"Nickname report published to {channel.id}: {len(bad_members)} bad nicknames; sent {dm_count} DM notices"
        )
        return True
    except Exception as error:
        console_log(f"Nickname report publish failed: {error}")
        return False


async def publish_staff_and_nickname_panels(guild: discord.Guild) -> list[str]:
    issues: list[str] = []
    if guild.id != FAMQ_GUILD_ID:
        return issues
    await ensure_guild_members_loaded(guild)
    if not await publish_staff_panel(guild):
        issues.append(
            f"панель состава не была опубликована: проверьте канал {FAMQ_STAFF_PANEL_CHANNEL_ID} и права бота"
        )
    if not await publish_nickname_report(guild):
        issues.append(
            f"отчёт по никнеймам не был опубликован: проверьте канал {FAMQ_NICKNAME_REPORT_CHANNEL_ID} и права бота"
        )
    return issues


async def publish_staff_and_nickname_panels_later(guild: discord.Guild, delay_seconds: float = 3.0) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        issues = await publish_staff_and_nickname_panels(guild)
        if issues:
            console_log("Staff/nickname publication issues: " + " | ".join(issues))
    except Exception as error:
        console_log(f"Staff/nickname background publication failed: {error}")
