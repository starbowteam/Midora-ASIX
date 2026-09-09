"""Система семейных заявок: подача, карточка, решения рекрутеров и панели."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import discord

from applications import (
    build_application_channel_name,
    build_asx_member_nickname,
)
from botcore import bot, console_log
from channels import (
    cleanup_bot_messages,
    delete_channel_now,
    delete_message_safely,
    schedule_channel_deletion,
    send_dm_or_fallback,
    spawn_background_task,
)
from components import brand_gallery, large_separator, small_separator
from config import *
from embeds import (
    build_application_fields,
    build_dm_embed,
    build_panel_embed,
    build_result_embed,
)
from formatting import format_log_time_msk, parse_iso, text_sendable, trim_text
from interactions import LoggedErrorsMixin, ack_interaction
from logs import send_application_log
from projects import (
    can_manage_application,
    get_custom_emoji_markup,
    get_guild_emoji_text,
    get_manageable_application_options,
    get_project,
    get_project_name,
    get_project_panel_key,
    get_server_accept_role_id,
    get_server_plain_label,
    get_server_recruiter_roles,
    has_application_control_access,
    is_friend_verification_application,
    member_has_any_role,
)
from state import (
    application_store,
    get_visible_application_options,
    is_application_open,
    next_application_id,
    panel_store,
    reload_applications,
    save_applications,
    save_panels,
    set_application_open,
)


application_action_locks: dict[int, asyncio.Lock] = {}


def get_application_lock(app_id: int) -> asyncio.Lock:
    lock = application_action_locks.get(app_id)
    if lock is None:
        lock = asyncio.Lock()
        application_action_locks[app_id] = lock
    return lock


def build_application_ping_content(applicant_id: int, role_ids: list[int] | None = None) -> str:
    mentions = [f"<@{applicant_id}>"]
    target_role_ids = GLOBAL_APPLICATION_PING_ROLE_IDS if role_ids is None else role_ids
    mentions.extend(f"<@&{role_id}>" for role_id in target_role_ids if role_id)
    return " ".join(dict.fromkeys(mentions))


def is_privileged_recruiter(member: discord.Member | None) -> bool:
    return member_has_any_role(member, [CHIEF_RECRUIT_ROLE_ID, DEP_CHIEF_RECRUIT_ROLE_ID])


# --- Карточки Components V2 ---

class ApplicationPanelCard(LoggedErrorsMixin, discord.ui.LayoutView):
    """Цельная карточка подачи заявок в канале панели."""

    def __init__(self, guild_id: int, guild: discord.Guild | None = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        actual_guild = guild or bot.get_guild(guild_id)
        intro_emoji = get_guild_emoji_text(actual_guild, EMOJI_APPLICATION_INTRO_ID, "✦")
        denver_emoji = get_custom_emoji_markup(actual_guild, DENVER_EMOJI_ID, "Denver")
        is_open = bool(get_visible_application_options(guild_id))

        apply_button = discord.ui.Button(
            custom_id=f"famq_apply_{guild_id}",
            label="Подать заявку" if is_open else "Набор временно закрыт",
            style=discord.ButtonStyle.secondary,
            disabled=not is_open,
        )
        apply_button.callback = self.apply_callback

        card = discord.ui.Container(
            brand_gallery(),
            discord.ui.TextDisplay(f"## {intro_emoji} Путь в семью начинается здесь!"),
            large_separator(),
            discord.ui.TextDisplay(
                "Заполни анкету — после проверки рекрутеры свяжутся с тобой.\n\n"
                f"**Заявки принимаются на сервере {denver_emoji} Denver.**"
            ),
            small_separator(),
            discord.ui.TextDisplay(
                "**Рассмотрение заявки**\n"
                "• Уведомление о приглашении на обзвон обычно отправляется в личные сообщения.\n"
                "> Обычно заявки обрабатываются в течение 1–2 дней — всё зависит от загруженности рекрутеров."
            ),
            discord.ui.TextDisplay(
                "**Требования**\n"
                "> Укажи все обязательные данные анкеты: имя и возраст IRL, левел, онлайн и часовой пояс, "
                "фракцию, игровой ник и Static-ID."
            ),
            large_separator(),
            discord.ui.ActionRow(apply_button),
            accent_colour=COLOR_PANEL,
        )
        self.add_item(card)

    async def apply_callback(self, interaction: discord.Interaction) -> None:
        options = get_visible_application_options(self.guild_id)
        if not options:
            await interaction.response.send_message("Подача заявок сейчас недоступна.", ephemeral=True)
            return
        await show_application_modal(interaction, options[0]["key"], self.guild_id)


class ApplicationCard(LoggedErrorsMixin, discord.ui.LayoutView):
    """Карточка заявки в её канале — в том же стиле, что и панель подачи.

    Components V2-сообщение не может нести ``content``, поэтому упоминания
    заявителя и ролей живут первым текстовым блоком внутри контейнера.
    """

    def __init__(
        self,
        application: dict[str, Any],
        applicant_tag: str = "",
        *,
        disabled: bool = False,
        ping_line: str = "",
    ):
        super().__init__(timeout=None)
        self.app_id = int(application["id"])
        server = str(application.get("server", ""))
        is_friend = is_friend_verification_application(server)

        blocks: list[discord.ui.Item] = [brand_gallery()]
        if ping_line:
            blocks.append(discord.ui.TextDisplay(ping_line))

        title = ("Верификация для друзей" if is_friend else "Заявка") + f" #{self.app_id}"
        applicant_line = f"**Заявитель:** <@{application['applicantId']}>"
        if applicant_tag:
            applicant_line += f" · `{applicant_tag}`"
        blocks.append(
            discord.ui.TextDisplay(
                f"## {title}\n{applicant_line}\n**Направление:** {get_server_plain_label(server)}"
            )
        )
        blocks.append(large_separator())

        blocks.append(
            discord.ui.TextDisplay(
                "\n\n".join(
                    f"**{label}**\n> {trim_text(str(value).strip() or '—', 400)}"
                    for label, value in build_application_fields(application)
                )
            )
        )
        blocks.append(large_separator())

        claimed_by = int(application.get("claimedBy") or 0)
        status = str(application.get("status", "pending"))
        footer_lines: list[str] = []
        if claimed_by:
            footer_lines.append(f"**Закреплена за:** <@{claimed_by}>")
        if status == "accepted":
            footer_lines.append("**Статус:** заявка принята")
        elif status == "rejected":
            reason = str(application.get("rejectReason", "")).strip()
            footer_lines.append("**Статус:** заявка отклонена" + (f" — {reason}" if reason else ""))
        footer_lines.append(
            f"-# ID заявителя: {application['applicantId']} • "
            f"подана {format_log_time_msk(parse_iso(application.get('submittedAt')))}"
        )
        blocks.append(discord.ui.TextDisplay("\n".join(footer_lines)))
        blocks.append(discord.ui.ActionRow(*self._build_buttons(disabled)))

        self.add_item(discord.ui.Container(*blocks, accent_colour=COLOR_PANEL))

    def _build_buttons(self, disabled: bool) -> list[discord.ui.Button]:
        specs = (
            (f"{BTN_REVIEW_PREFIX}{self.app_id}", "Взять на рассмотрение", self.review_callback),
            (f"{BTN_CALL_PREFIX}{self.app_id}", "Вызвать на обзвон", self.call_callback),
            (f"{BTN_ACCEPT_PREFIX}{self.app_id}", "Принять заявку", self.accept_callback),
            (f"{BTN_REJECT_PREFIX}{self.app_id}", "Отклонить заявку", self.reject_callback),
        )
        buttons: list[discord.ui.Button] = []
        for custom_id, label, callback in specs:
            button = discord.ui.Button(
                custom_id=custom_id,
                label=label,
                style=discord.ButtonStyle.secondary,
                disabled=disabled,
            )
            button.callback = callback
            buttons.append(button)
        return buttons

    @staticmethod
    async def _reply(interaction: discord.Interaction, text: str) -> None:
        """Отвечает независимо от того, было ли взаимодействие уже отложено."""
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception as error:
            console_log(f"Failed to answer interaction for application #{interaction.id}: {error!r}")

    async def _guard(self, interaction: discord.Interaction) -> dict[str, Any] | None:
        # Состояние заявок живёт в памяти процесса, поэтому перечитывать файл
        # на каждое нажатие не нужно: это лишний блокирующий ввод-вывод в цикле
        # событий, из-за которого ответ не успевал уложиться в лимит Discord.
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        app = application_store.get("items", {}).get(str(self.app_id))
        if not app:
            await self._reply(interaction, "Заявка не найдена.")
            return None
        if interaction.guild is None:
            await self._reply(interaction, "Сервер не найден.")
            return None

        if not can_manage_application(member, app.get("server", FAMQ_SERVER_DENVER), interaction.guild.id):
            await self._reply(interaction, "Эта кнопка доступна только назначенным ролям этого сервера.")
            return None

        claimed_by = int(app.get("claimedBy") or 0)
        if claimed_by not in {0, interaction.user.id} and not is_privileged_recruiter(member):
            await self._reply(interaction, f"Эта заявка уже закреплена за рекрутером <@{claimed_by}>.")
            return None

        return app

    async def _claim(self, interaction: discord.Interaction) -> dict[str, Any] | None:
        """Общая проверка для действий, меняющих статус заявки."""
        app = application_store.get("items", {}).get(str(self.app_id))
        if not app:
            await self._reply(interaction, "Заявка не найдена.")
            return None
        if app.get("status") in {"accepted", "rejected"}:
            await self._reply(interaction, "Заявка уже обработана.")
            return None
        claimed_by = int(app.get("claimedBy") or 0)
        if claimed_by not in {0, interaction.user.id} and not is_privileged_recruiter(interaction.user):
            await self._reply(interaction, f"Эта заявка уже закреплена за рекрутером <@{claimed_by}>.")
            return None
        app["claimedBy"] = interaction.user.id
        app["claimedAt"] = datetime.now(timezone.utc).isoformat()
        return app

    async def review_callback(self, interaction: discord.Interaction) -> None:
        # Discord ждёт ответа на нажатие 3 секунды, иначе показывает
        # «Взаимодействие не удалось». Дальше идут запросы к API, которые в
        # этот лимит не укладываются, поэтому ответ откладываем сразу.
        if not await ack_interaction(interaction, label=f"review#{self.app_id}"):
            return
        app = await self._guard(interaction)
        if app is None:
            return
        async with get_application_lock(self.app_id):
            app = await self._claim(interaction)
            if app is None:
                return
            application_store["items"][str(self.app_id)] = app
            save_applications()

        await lock_application_channel_to_recruiter(interaction.guild, app)
        await refresh_application_message(interaction.guild, app)
        if text_sendable(interaction.channel):
            await interaction.channel.send(
                content=f"<@{app['applicantId']}> Ваша заявка взята на рассмотрение рекрутером <@{interaction.user.id}>.",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        await log_application_event(
            interaction.guild,
            title="Заявка взята на рассмотрение",
            description="└ рекрутер **закрепил** за собой заявку",
            application=app,
            actor=interaction.user,
        )
        await interaction.followup.send(f"Заявка #{self.app_id} закреплена за вами.", ephemeral=True)

    async def call_callback(self, interaction: discord.Interaction) -> None:
        if not await ack_interaction(interaction, label=f"call#{self.app_id}"):
            return
        app = await self._guard(interaction)
        if app is None:
            return

        if not int(app.get("claimedBy") or 0):
            app["claimedBy"] = interaction.user.id
            app["claimedAt"] = datetime.now(timezone.utc).isoformat()
            application_store["items"][str(self.app_id)] = app
            save_applications()
            await lock_application_channel_to_recruiter(interaction.guild, app)
            await refresh_application_message(interaction.guild, app)

        project = get_project(int(app.get("guildId", interaction.guild.id if interaction.guild else 0)))
        interview_channel_ids = list(project.get("interview_channel_ids", [])) if project else []
        if not interview_channel_ids:
            await interaction.followup.send("Для этого проекта каналы обзвона пока не настроены.", ephemeral=True)
            return
        await interaction.followup.send(
            "Выберите канал для обзвона:",
            view=CallSelectView(self.app_id, interview_channel_ids),
            ephemeral=True,
        )

    async def accept_callback(self, interaction: discord.Interaction) -> None:
        # Ответ откладывается первым же действием: раньше defer стоял после
        # проверок и записи на диск, и при малейшей загрузке процесса не успевал
        # в трёхсекундный лимит — взаимодействие падало, а вместе с ним
        # пропадали выдача ролей и удаление канала заявки.
        if not await ack_interaction(interaction, label=f"accept#{self.app_id}"):
            return
        app = await self._guard(interaction)
        if app is None:
            return
        async with get_application_lock(self.app_id):
            app = await self._claim(interaction)
            if app is None:
                return
            app["status"] = "accepted"
            app["decidedBy"] = interaction.user.id
            app["decidedAt"] = datetime.now(timezone.utc).isoformat()
            application_store["items"][str(self.app_id)] = app
            save_applications()

        await lock_application_channel_to_recruiter(interaction.guild, app)
        accept_role_id = get_server_accept_role_id(
            app.get("server", FAMQ_SERVER_DENVER), int(app.get("guildId", interaction.guild.id))
        )
        member = interaction.guild.get_member(int(app["applicantId"]))
        if member is None:
            try:
                member = await interaction.guild.fetch_member(int(app["applicantId"]))
            except Exception:
                member = None

        is_friend_verification = is_friend_verification_application(app.get("server", ""))
        if member is not None:
            roles_to_add: list[discord.Role] = []
            if is_friend_verification:
                role = interaction.guild.get_role(int(accept_role_id)) if accept_role_id else None
                if role is not None:
                    roles_to_add.append(role)
            else:
                for role_id in (APPLICATION_ACADEMY_ROLE_ID, FAMQ_ACCEPT_ROLE_ID):
                    role = interaction.guild.get_role(int(role_id))
                    if role is not None:
                        roles_to_add.append(role)
            if roles_to_add:
                try:
                    await member.add_roles(*list(dict.fromkeys(roles_to_add)), reason="FAMQ application accepted")
                except Exception:
                    pass

            if not is_friend_verification:
                try:
                    await member.edit(
                        nick=build_asx_member_nickname(app),
                        reason="Accepted FAMQ application nickname format",
                    )
                except Exception:
                    pass

        await disable_buttons(interaction.guild, app)
        await send_dm_or_fallback(
            interaction.guild,
            int(app["applicantId"]),
            build_dm_embed(
                "Верификация одобрена" if is_friend_verification else "Заявка принята",
                (
                    "Вы успешно прошли верификацию на друга, в семье ASIXEZ. "
                    "Просьба поставить ник по форме на нашем сервере: Имя (IRL) | Имя/Ник(В игре)"
                    if is_friend_verification
                    else "\n".join(
                        [
                            f"Ваша заявка была успешно принята рекрутером: **<@{interaction.user.id}>**.",
                            "- Для получения инвайта обратитесь к любому старшему в игре.",
                            "- На данный момент Вы в академии семьи, чтобы получить ранг смените фамилию в игре на ASIXEZ, и обратитесь к любому старшему для повышения.",
                            "- Если Вы не вводили промокод, то введите в чат `/promo ASIX`, и получите 50.000$ при достижении 3 уровня, и дополнительные 50.000 от нашей семьи за ввод промокода.",
                            "- Регистрация по промокоду: https://majestic-rp.ru/register?utm_campaign=ASIX",
                        ]
                    )
                ),
            ),
        )
        await post_result(interaction.guild, app, interaction.user.id, "accepted")
        await log_application_event(
            interaction.guild,
            title="Заявка принята",
            description=f"{EMOJI_ACCEPT_TEXT} рекрутер **принял** заявку",
            application=app,
            actor=interaction.user,
        )
        await interaction.followup.send(f"Заявка #{self.app_id} принята. Канал будет удалён.", ephemeral=True)
        application_action_locks.pop(self.app_id, None)
        schedule_channel_deletion(int(app.get("channelId") or 0), "Заявка FAMQ принята")

    async def reject_callback(self, interaction: discord.Interaction) -> None:
        app = await self._guard(interaction)
        if app is None:
            return
        if app.get("status") in {"accepted", "rejected"}:
            await interaction.response.send_message("Заявка уже обработана.", ephemeral=True)
            return
        await interaction.response.send_modal(RejectModal(self.app_id))


class FamqPanelView(discord.ui.View):
    """Классическая панель заявок для проектов без Components V2-карточки."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        if get_project(guild_id) is None:
            return

        visible_options = get_visible_application_options(guild_id)
        if not visible_options:
            self.add_item(
                discord.ui.Button(
                    custom_id=f"famq_apply_closed_{guild_id}",
                    label="Набор временно закрыт",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                )
            )
            return

        if len(visible_options) <= 1:
            apply_button = discord.ui.Button(
                custom_id=f"famq_apply_{guild_id}",
                label="Подать заявку",
                style=discord.ButtonStyle.secondary,
            )
            apply_button.callback = self.apply_callback
            self.add_item(apply_button)
            return

        select = discord.ui.Select(
            custom_id=f"{PANEL_SELECT_ID}_{guild_id}",
            placeholder="Выберите сервер или верификацию",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=option["label"], value=option["key"], description="Открытая подача анкеты")
                for option in visible_options
            ],
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def apply_callback(self, interaction: discord.Interaction) -> None:
        options = get_visible_application_options(self.guild_id)
        if not options:
            await interaction.response.send_message("Подача заявок сейчас недоступна.", ephemeral=True)
            return
        await show_application_modal(interaction, options[0]["key"], self.guild_id)

    async def select_callback(self, interaction: discord.Interaction) -> None:
        selected = self.children[0]
        if not isinstance(selected, discord.ui.Select) or not selected.values:
            await interaction.response.send_message("Не удалось определить сервер.", ephemeral=True)
            return
        await show_application_modal(interaction, selected.values[0], self.guild_id)


# --- Модалки ---

async def show_application_modal(interaction: discord.Interaction, server: str, guild_id: int) -> None:
    if not is_application_open(server, guild_id):
        await interaction.response.send_message("Подача заявок на этот сервер сейчас закрыта.", ephemeral=True)
        return
    if server == FAMQ_SERVER_FRIEND_VERIFICATION:
        await interaction.response.send_modal(FriendVerificationModal())
        return
    await interaction.response.send_modal(ApplicationModal(server))


class ApplicationModal(LoggedErrorsMixin, discord.ui.Modal):
    def __init__(self, server: str):
        super().__init__(title=f"Заявка — {get_server_plain_label(server)}", timeout=None)
        self.server = server

        self.irl_name = discord.ui.TextInput(label="Ваше имя IRL", max_length=40, required=True)
        self.age_irl = discord.ui.TextInput(label="Ваш возраст IRL", max_length=20, required=True)
        self.level_online = discord.ui.TextInput(
            label="Левел в игре & Онлайн и часовой пояс", max_length=150, required=True
        )
        self.fraction = discord.ui.TextInput(
            label="Состоите во фракции? Если да — в какой?", max_length=150, required=True
        )
        self.name_static = discord.ui.TextInput(label="Ник в игре & Static-ID", max_length=150, required=True)

        for item in (self.irl_name, self.age_irl, self.level_online, self.fraction, self.name_static):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await submit_application(
            interaction,
            self.server,
            {
                "irlName": str(self.irl_name).strip(),
                "ageIrl": str(self.age_irl).strip(),
                "nameAge": f"{str(self.irl_name).strip()} | {str(self.age_irl).strip()}",
                "levelOnline": str(self.level_online).strip(),
                "fraction": str(self.fraction).strip(),
                "nameStatic": str(self.name_static).strip(),
            },
        )


class FriendVerificationModal(LoggedErrorsMixin, discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Верификация для друзей", timeout=None)
        self.friend_name_game = discord.ui.TextInput(
            label="Ваше имя, фамилия в игре", max_length=60, required=True
        )
        self.friend_family = discord.ui.TextInput(
            label="В какой семье вы состоите?", max_length=100, required=True
        )
        self.add_item(self.friend_name_game)
        self.add_item(self.friend_family)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await submit_application(
            interaction,
            FAMQ_SERVER_FRIEND_VERIFICATION,
            {
                "friendNameGame": str(self.friend_name_game).strip(),
                "friendFamily": str(self.friend_family).strip(),
            },
        )


class RejectModal(LoggedErrorsMixin, discord.ui.Modal):
    def __init__(self, app_id: int):
        super().__init__(title="Отклонить заявку", timeout=None)
        self.app_id = app_id
        self.reason = discord.ui.TextInput(
            label="Укажите причину отклонения",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Отклонение делает столько же обращений к API, сколько и принятие,
        # поэтому ответ откладывается до всей остальной работы.
        if not await ack_interaction(interaction, label=f"reject#{self.app_id}"):
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        app = application_store.get("items", {}).get(str(self.app_id))
        if not app:
            await interaction.followup.send("Заявка не найдена.", ephemeral=True)
            return
        if interaction.guild is None or not can_manage_application(
            member, app.get("server", FAMQ_SERVER_DENVER), int(app.get("guildId", interaction.guild.id))
        ):
            await interaction.followup.send("Недостаточно прав.", ephemeral=True)
            return

        reject_reason = str(self.reason).strip()
        async with get_application_lock(self.app_id):
            app = application_store.get("items", {}).get(str(self.app_id))
            if not app:
                await interaction.followup.send("Заявка не найдена.", ephemeral=True)
                return
            if app.get("status") in {"accepted", "rejected"}:
                await interaction.followup.send("Заявка уже обработана.", ephemeral=True)
                return
            claimed_by = int(app.get("claimedBy") or 0)
            if claimed_by not in {0, interaction.user.id} and not is_privileged_recruiter(member):
                await interaction.followup.send(
                    f"Эта заявка уже закреплена за рекрутером <@{claimed_by}>.", ephemeral=True
                )
                return

            app["claimedBy"] = interaction.user.id
            app["claimedAt"] = datetime.now(timezone.utc).isoformat()
            app["status"] = "rejected"
            app["decidedBy"] = interaction.user.id
            app["decidedAt"] = datetime.now(timezone.utc).isoformat()
            app["rejectReason"] = reject_reason
            application_store["items"][str(self.app_id)] = app
            save_applications()

        await lock_application_channel_to_recruiter(interaction.guild, app)
        await disable_buttons(interaction.guild, app)
        is_friend_verification = is_friend_verification_application(app.get("server", ""))
        project_name = get_project_name(int(app.get("guildId", interaction.guild.id)))
        await send_dm_or_fallback(
            interaction.guild,
            int(app["applicantId"]),
            build_dm_embed(
                "Верификация отклонена" if is_friend_verification else "Заявка отклонена",
                "\n".join(
                    [
                        "Ваше заявление на верификацию для друзей было отклонено.",
                        f"Проект: **{project_name}**",
                        f"Рассматривал: <@{interaction.user.id}>",
                        f"**Причина:** {reject_reason}",
                    ]
                    if is_friend_verification
                    else [
                        f"Ваша заявка в ASIXEZ была отклонена рекрутером: <@{interaction.user.id}>.",
                        f"Проект: **{project_name}**",
                        f"**Причина:** {reject_reason}",
                        "Вы можете подать заявку повторно исправив свою ошибку.",
                    ]
                ),
            ),
        )
        await post_result(interaction.guild, app, interaction.user.id, "rejected", reject_reason)
        await log_application_event(
            interaction.guild,
            title="Заявка отклонена",
            description=f"{EMOJI_REJECT_TEXT} рекрутер **отклонил** заявку",
            application=app,
            actor=interaction.user,
            extra_fields=[("Причина", reject_reason, False)],
        )
        await interaction.followup.send(f"Заявка #{self.app_id} отклонена. Канал будет удалён.", ephemeral=True)
        application_action_locks.pop(self.app_id, None)
        schedule_channel_deletion(int(app.get("channelId") or 0), "Заявка FAMQ отклонена")


class CallSelectView(LoggedErrorsMixin, discord.ui.View):
    def __init__(self, app_id: int, interview_channel_ids: list[int]):
        super().__init__(timeout=180)
        self.app_id = app_id
        select = discord.ui.Select(
            custom_id=f"{SELECT_CALL_PREFIX}{app_id}",
            placeholder="Выберите канал для обзвона",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"Канал для обзвона #{index + 1}",
                    value=str(channel_id),
                    description=f"ID: {channel_id}",
                )
                for index, channel_id in enumerate(interview_channel_ids)
            ],
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        selected = self.children[0]
        if not isinstance(selected, discord.ui.Select) or not selected.values:
            await interaction.response.send_message("Канал не выбран.", ephemeral=True)
            return

        app = application_store.get("items", {}).get(str(self.app_id))
        if not app:
            await interaction.response.send_message("Заявка не найдена.", ephemeral=True)
            return
        if interaction.guild is None or not can_manage_application(
            member, app.get("server", FAMQ_SERVER_DENVER), int(app.get("guildId", interaction.guild.id))
        ):
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return
        claimed_by = int(app.get("claimedBy") or 0)
        if claimed_by not in {0, interaction.user.id} and not is_privileged_recruiter(member):
            await interaction.response.send_message(
                f"Эта заявка закреплена за рекрутером <@{claimed_by}>.", ephemeral=True
            )
            return

        selected_channel_id = int(selected.values[0])
        project = get_project(int(app.get("guildId", interaction.guild.id)))
        waiting_channel_id = int(project["waiting_channel_id"]) if project and project.get("waiting_channel_id") else None

        # Сначала закрываем меню, и только потом пишем в канал и в лог: иначе
        # ответ уходил после двух обращений к API и не всегда успевал вовремя.
        await interaction.response.edit_message(
            content=f"Заявитель <@{app['applicantId']}> вызван на обзвон в <#{selected_channel_id}>.",
            view=None,
        )

        if text_sendable(interaction.channel):
            await interaction.channel.send(
                content=(
                    f"<@{app['applicantId']}> Вы были вызваны на обзвон рекрутером <@{interaction.user.id}> "
                    + (
                        f"в канал <#{selected_channel_id}>."
                        if waiting_channel_id is None
                        else f"в канал <#{selected_channel_id}>. Для прохождения зайдите в канал ожидания: <#{waiting_channel_id}>."
                    )
                ),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        await log_application_event(
            interaction.guild,
            title="Вызов на обзвон",
            description=f"{EMOJI_CALL_TEXT} рекрутер **вызвал** заявителя на обзвон",
            application=app,
            actor=interaction.user,
            extra_fields=[("Канал обзвона", f"<#{selected_channel_id}>", True)],
        )


# --- Логирование заявок ---

async def log_application_event(
    guild: discord.Guild,
    *,
    title: str,
    description: str,
    application: dict[str, Any],
    actor: discord.abc.User | discord.Member | None = None,
    extra_fields: list[tuple[str, str, bool]] | None = None,
) -> None:
    fields: list[tuple[str, str, bool]] = [
        ("Заявка", f"`#{application['id']}`", True),
        ("Направление", get_server_plain_label(str(application.get("server", ""))), True),
        ("Заявитель", f"<@{application['applicantId']}>", True),
    ]
    if actor is not None:
        fields.append(("Рекрутер", f"<@{actor.id}>", True))
    channel_id = int(application.get("channelId") or 0)
    if channel_id:
        fields.append(("Канал заявки", f"<#{channel_id}>", True))
    fields.extend(extra_fields or [])

    await send_application_log(
        guild,
        title=title,
        description=description,
        fields=fields,
        actor=actor,
        footer_parts=[f"ID заявителя: {application['applicantId']}", format_log_time_msk()],
    )


# --- Каналы заявок ---

def add_global_application_observer_overwrites(
    guild: discord.Guild,
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
) -> None:
    read_only_overwrite = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
    for role_id in GLOBAL_APPLICATION_PING_ROLE_IDS:
        role = guild.get_role(int(role_id)) if role_id else None
        if role is None or role in overwrites:
            continue
        overwrites[role] = read_only_overwrite


async def create_application_channel(
    guild: discord.Guild,
    project: dict[str, Any],
    *,
    name: str,
    applicant: discord.abc.User,
    recruiter_role_ids: list[int],
    reason: str,
) -> discord.TextChannel | None:
    """Создаёт приватный текстовый канал заявки в категории заявок."""
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        applicant: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
    }
    for role_id in recruiter_role_ids:
        role = guild.get_role(int(role_id)) if role_id else None
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
    add_global_application_observer_overwrites(guild, overwrites)

    category = None
    category_id = int(project.get("application_category_id") or 0)
    if category_id:
        category = guild.get_channel(category_id)
        if category is None:
            try:
                category = await guild.fetch_channel(category_id)
            except Exception:
                category = None
    if not isinstance(category, discord.CategoryChannel):
        console_log(f"Application category {category_id} not found, creating channel without category")
        category = None

    try:
        return await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            reason=reason,
        )
    except Exception as error:
        console_log(f"Failed to create application channel: {error}")
        return None


async def ensure_application_channel_observers(guild: discord.Guild, channel: discord.TextChannel) -> None:
    read_only_overwrite = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
    current_overwrites = channel.overwrites
    for role_id in GLOBAL_APPLICATION_PING_ROLE_IDS:
        role = guild.get_role(int(role_id)) if role_id else None
        if role is None or role in current_overwrites:
            continue
        try:
            await channel.set_permissions(
                role, overwrite=read_only_overwrite, reason="Global application observer access"
            )
        except Exception:
            continue


async def lock_application_channel_to_recruiter(guild: discord.Guild, application: dict[str, Any]) -> None:
    channel_id = application.get("channelId")
    claimed_by = int(application.get("claimedBy") or 0)
    if not channel_id or claimed_by <= 0:
        return

    try:
        channel = guild.get_channel(int(channel_id)) or await guild.fetch_channel(int(channel_id))
    except Exception:
        return

    claimant = guild.get_member(claimed_by)
    if claimant is None:
        try:
            claimant = await guild.fetch_member(claimed_by)
        except Exception:
            claimant = None

    # Заявки прошлых версий бота жили в приватных ветках.
    if isinstance(channel, discord.Thread):
        if claimant is not None:
            try:
                await channel.add_user(claimant)
            except Exception:
                pass
        return

    if not text_sendable(channel):
        return

    recruiter_role_ids = set(
        get_server_recruiter_roles(
            application.get("server", FAMQ_SERVER_DENVER), int(application.get("guildId", guild.id))
        )
    )
    recruiter_role_ids.update(int(role_id) for role_id in GLOBAL_APPLICATION_PING_ROLE_IDS if int(role_id))
    if not recruiter_role_ids:
        return

    denied_overwrite = discord.PermissionOverwrite(
        view_channel=False, send_messages=False, read_message_history=False
    )
    allow_overwrite = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True
    )
    reason = f"Application #{application.get('id')} locked to recruiter {claimed_by}"

    # Доступ закрывается на уровне ролей, а не каждого участника отдельно.
    # Прежний вариант делал по запросу к API на каждого рекрутера в канале:
    # десяток рекрутеров превращался в десяток последовательных запросов,
    # нажатие кнопки отвечало через десяток секунд и Discord успевал показать
    # «Взаимодействие не удалось». Ролевых перезаписей всегда единицы, и они
    # вдобавок действуют на тех, кто получит роль позже.
    for role_id in sorted(recruiter_role_ids):
        role = guild.get_role(int(role_id))
        if role is None:
            continue
        try:
            await channel.set_permissions(role, overwrite=denied_overwrite, reason=reason)
        except Exception as error:
            console_log(f"Failed to restrict role {role_id} on channel {channel.id}: {error!r}")

    # Персональное разрешение перекрывает запрет роли, поэтому закрепивший
    # заявку рекрутер сохраняет доступ.
    if claimant is not None:
        try:
            await channel.set_permissions(
                claimant,
                overwrite=allow_overwrite,
                reason=f"Application #{application.get('id')} claimed by recruiter",
            )
        except Exception as error:
            console_log(f"Failed to grant claimant {claimed_by} on channel {channel.id}: {error!r}")


# --- Подача заявки ---

async def submit_application(
    interaction: discord.Interaction,
    server: str,
    answers: dict[str, Any],
) -> None:
    project = get_project(interaction.guild) if interaction.guild is not None else None
    if interaction.guild is None or project is None:
        await interaction.response.send_message(
            "Это действие доступно только на разрешённых серверах проекта.", ephemeral=True
        )
        return
    if not is_application_open(server, interaction.guild.id):
        await interaction.response.send_message("Подача заявок на этот сервер сейчас закрыта.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    reload_applications()
    app_id = next_application_id()

    channel_name = build_application_channel_name(interaction.user.display_name or interaction.user.name, app_id)
    recruiter_roles = get_server_recruiter_roles(server, interaction.guild.id)
    application_channel = await create_application_channel(
        interaction.guild,
        project,
        name=channel_name,
        applicant=interaction.user,
        recruiter_role_ids=recruiter_roles,
        reason=f"{project['project_name']} application #{app_id}",
    )
    if application_channel is None:
        await interaction.followup.send(
            "Не удалось создать канал для заявки. Проверьте права бота и категорию заявок.", ephemeral=True
        )
        return

    application = {
        "id": app_id,
        "server": server,
        "applicantId": interaction.user.id,
        "guildId": interaction.guild.id,
        "channelId": application_channel.id,
        "status": "pending",
        "claimedBy": 0,
        "claimedAt": "",
        "submittedAt": datetime.now(timezone.utc).isoformat(),
        "decidedAt": "",
        "decidedBy": "",
        "applicationMessageId": "",
        **answers,
    }

    ping_line = build_application_ping_content(interaction.user.id, recruiter_roles)
    app_message = await application_channel.send(
        view=ApplicationCard(application, interaction.user.display_name, ping_line=ping_line),
        allowed_mentions=discord.AllowedMentions(roles=True, users=True),
    )

    application["applicationMessageId"] = app_message.id
    application_store.setdefault("items", {})[str(app_id)] = application
    save_applications()
    bot.add_view(ApplicationCard(application), message_id=app_message.id)

    server_label = get_server_plain_label(server)
    await interaction.followup.send(
        embed=build_dm_embed(
            "✅ Заявка принята",
            f"Ваша заявка **#{app_id}** ({server_label}) успешно подана!\n"
            f"Ваш канал: <#{application_channel.id}>\n\nОжидайте ответа от рекрутера.",
        ),
        ephemeral=True,
    )
    spawn_background_task(
        log_application_event(
            interaction.guild,
            title="Новая заявка",
            description=f"{EMOJI_REVIEW_TEXT} участник **подал** заявку",
            application=application,
            actor=interaction.user,
        )
    )


# --- Обновление сообщений заявок ---

async def _fetch_application_message(
    guild: discord.Guild, application: dict[str, Any]
) -> discord.Message | None:
    channel_id = application.get("channelId")
    message_id = application.get("applicationMessageId")
    if not channel_id or not message_id:
        return None
    try:
        channel = guild.get_channel(int(channel_id)) or await guild.fetch_channel(int(channel_id))
    except Exception:
        return None
    if not text_sendable(channel):
        return None
    try:
        return await channel.fetch_message(int(message_id))
    except Exception:
        return None


async def resolve_applicant_tag(guild: discord.Guild, application: dict[str, Any]) -> str:
    applicant_id = int(application["applicantId"])
    try:
        user = guild.get_member(applicant_id) or await bot.fetch_user(applicant_id)
        return getattr(user, "display_name", None) or getattr(user, "name", f"user-{applicant_id}")
    except Exception:
        return f"user-{applicant_id}"


async def disable_buttons(guild: discord.Guild, application: dict[str, Any]) -> None:
    message = await _fetch_application_message(guild, application)
    if message is None:
        return
    applicant_tag = await resolve_applicant_tag(guild, application)
    try:
        await message.edit(view=ApplicationCard(application, applicant_tag, disabled=True))
    except Exception:
        pass


async def refresh_application_message(guild: discord.Guild, application: dict[str, Any]) -> None:
    message = await _fetch_application_message(guild, application)
    if message is None:
        return
    applicant_tag = await resolve_applicant_tag(guild, application)
    try:
        await message.edit(
            view=ApplicationCard(
                application,
                applicant_tag,
                disabled=application.get("status") != "pending",
            )
        )
    except Exception:
        pass


async def refresh_pending_application_messages(guild: discord.Guild) -> int:
    reload_applications()
    refreshed = 0

    for app in list(application_store.get("items", {}).values()):
        if int(app.get("guildId", 0)) != guild.id or app.get("status") != "pending":
            continue

        try:
            channel = guild.get_channel(int(app["channelId"])) or await guild.fetch_channel(int(app["channelId"]))
        except Exception:
            continue
        if not text_sendable(channel):
            continue
        if isinstance(channel, discord.TextChannel):
            await ensure_application_channel_observers(guild, channel)

        ping_role_ids = (
            get_server_recruiter_roles(FAMQ_SERVER_FRIEND_VERIFICATION, guild.id)
            if is_friend_verification_application(str(app.get("server", "")))
            else None
        )
        ping_line = build_application_ping_content(int(app["applicantId"]), ping_role_ids)
        applicant_tag = await resolve_applicant_tag(guild, app)

        message = None
        old_message_id = app.get("applicationMessageId")
        if old_message_id:
            try:
                message = await channel.fetch_message(int(old_message_id))
                await message.edit(view=ApplicationCard(app, applicant_tag, ping_line=ping_line))
            except Exception:
                message = None
        if message is None:
            message = await channel.send(
                view=ApplicationCard(app, applicant_tag, ping_line=ping_line),
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )
            app["applicationMessageId"] = message.id
            application_store["items"][str(app["id"])] = app

        bot.add_view(ApplicationCard(app), message_id=message.id)
        if int(app.get("claimedBy") or 0):
            await lock_application_channel_to_recruiter(guild, app)
        refreshed += 1

    if refreshed:
        save_applications()
    return refreshed


async def cleanup_resolved_application_channels(guild: discord.Guild) -> int:
    reload_applications()
    deleted = 0
    for app in application_store.get("items", {}).values():
        if int(app.get("guildId", 0)) != guild.id:
            continue
        if app.get("status") not in {"accepted", "rejected"}:
            continue
        channel_id = int(app.get("channelId") or 0)
        if channel_id <= 0:
            continue
        if await delete_channel_now(channel_id, f"Cleanup resolved application #{app.get('id')}"):
            deleted += 1
    return deleted


async def post_result(
    guild: discord.Guild,
    app: dict[str, Any],
    recruiter_user_id: int,
    verdict: str,
    reject_reason: str | None = None,
) -> None:
    project = get_project(guild)
    if project is None:
        return
    try:
        channel = guild.get_channel(int(project["results_channel_id"])) or await guild.fetch_channel(
            int(project["results_channel_id"])
        )
    except Exception:
        return

    if not text_sendable(channel):
        return

    try:
        await channel.send(
            content=f"<@{app['applicantId']}>",
            embed=build_result_embed(app, recruiter_user_id, verdict, reject_reason),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
    except Exception as error:
        # Канал итогов не должен обрывать выдачу ролей и удаление канала заявки.
        console_log(f"Failed to post application result #{app.get('id')}: {error!r}")


# --- Управление набором ---

async def announce_application_state_change(guild: discord.Guild, server: str, is_open: bool) -> None:
    try:
        channel = guild.get_channel(APPLICATION_ANNOUNCE_CHANNEL_ID) or await guild.fetch_channel(
            APPLICATION_ANNOUNCE_CHANNEL_ID
        )
    except Exception:
        return

    if not text_sendable(channel):
        return

    server_name = get_server_plain_label(server)
    project = get_project(guild)
    application_link = (
        f"https://discord.com/channels/{guild.id}/{int(project['panel_channel_id'])}"
        if project and project.get("panel_channel_id")
        else f"https://discord.com/channels/{FAMQ_GUILD_ID}/{FAMQ_PANEL_CHANNEL_ID}"
    )
    if is_open:
        content = (
            "Приветствую, @everyone \n\n"
            f'Заявки на сервер "{server_name}" вновь открыты! Подать заявку можно тут: {application_link}.'
        )
    else:
        content = (
            "Приветствую, @everyone \n\n"
            f'Заявки на сервер "{server_name}" временно закрыты. '
            "О открытии команда семьи сообщит вам позже.\n\n"
            "Хорошего дня."
        )

    await channel.send(content, allowed_mentions=discord.AllowedMentions(everyone=True))


async def apply_application_state(
    guild: discord.Guild,
    server: str,
    is_open: bool,
    *,
    announce: bool = True,
) -> None:
    set_application_open(server, guild.id, is_open)
    await create_or_update_main_panel(guild, force_recreate=False)
    if announce:
        await announce_application_state_change(guild, server, is_open)


class ApplicationToggleView(discord.ui.View):
    def __init__(self, guild_id: int, action: str, author_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.action = action
        self.author_id = author_id
        select = discord.ui.Select(
            placeholder="Выберите сервер",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=option["label"],
                    value=option["key"],
                    description="Открыть набор" if action == "open" else "Закрыть набор",
                )
                for option in get_manageable_application_options(guild_id)
            ],
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Сервер не найден.", ephemeral=True)
            return
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Этим меню может пользоваться только автор команды.", ephemeral=True)
            return
        select = self.children[0]
        if not isinstance(select, discord.ui.Select) or not select.values:
            await interaction.response.send_message("Сервер не выбран.", ephemeral=True)
            return

        server = select.values[0]
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not has_application_control_access(member, server, interaction.guild.id):
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return

        is_open = self.action == "open"
        await apply_application_state(interaction.guild, server, is_open)

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=(
                f'Подача заявок на сервер "{get_server_plain_label(server)}" '
                + ("открыта." if is_open else "закрыта.")
            ),
            view=self,
        )


# --- Панели ---

async def create_or_update_main_panel(guild: discord.Guild, force_recreate: bool = False) -> bool:
    project = get_project(guild)
    if project is None:
        return False
    try:
        channel = guild.get_channel(int(project["panel_channel_id"])) or await guild.fetch_channel(
            int(project["panel_channel_id"])
        )
    except Exception:
        return False
    if not isinstance(channel, discord.TextChannel):
        return False

    if force_recreate:
        await cleanup_bot_messages(channel)

    panel_key = get_project_panel_key(PANEL_KEY, guild.id)
    stored = panel_store.get(panel_key, {})
    uses_card = guild.id == FAMQ_GUILD_ID

    if not force_recreate and stored.get("messageId"):
        try:
            # Отдельное сообщение-баннер осталось от прежней версии панели:
            # теперь баннер — первый элемент самой карточки.
            if stored.get("imageMessageId"):
                try:
                    banner_message = await channel.fetch_message(int(stored["imageMessageId"]))
                    await delete_message_safely(banner_message)
                except Exception:
                    pass
                stored.pop("imageMessageId", None)
                panel_store[panel_key] = stored
                save_panels()
            panel_message = await channel.fetch_message(int(stored["messageId"]))
            if uses_card:
                await panel_message.edit(content=None, embed=None, view=ApplicationPanelCard(guild.id, guild))
            else:
                await panel_message.edit(embed=build_panel_embed(guild.id, guild), view=FamqPanelView(guild.id))
            return False
        except Exception:
            pass

    for key in ("imageMessageId", "messageId"):
        message_id = stored.get(key)
        if not message_id:
            continue
        try:
            message = await channel.fetch_message(int(message_id))
            await delete_message_safely(message)
        except Exception:
            pass

    if uses_card:
        panel_message = await channel.send(view=ApplicationPanelCard(guild.id, guild))
    else:
        panel_message = await channel.send(embed=build_panel_embed(guild.id, guild), view=FamqPanelView(guild.id))
    panel_store[panel_key] = {"messageId": panel_message.id, "channelId": channel.id}
    save_panels()
    return True
