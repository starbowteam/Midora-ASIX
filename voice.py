"""Временные голосовые комнаты и панель управления ими."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import discord

from botcore import bot
from components import brand_gallery, large_separator, small_separator
from config import *
from formatting import extract_user_id, format_channel_label, format_log_time_msk
from projects import get_project, is_famq_activity_guild
from state import (
    get_owned_voice_room,
    get_voice_room,
    get_voice_rooms,
    reload_voice_rooms,
    remove_voice_room,
    set_voice_room,
)


VOICE_CONTROLS = (
    (VOICE_EMOJI_ADD_SLOT_TEXT, "Добавить 1 слот"),
    (VOICE_EMOJI_REMOVE_SLOT_TEXT, "Убрать 1 слот"),
    (VOICE_EMOJI_LOCK_TEXT, "Открыть или закрыть комнату"),
    (VOICE_EMOJI_SPEAK_TEXT, "Выдать или забрать право говорить"),
    (VOICE_EMOJI_KICK_TEXT, "Исключить пользователя"),
    (VOICE_EMOJI_BITRATE_TEXT, "Изменить битрейт"),
    (VOICE_EMOJI_SET_SLOTS_TEXT, "Выставить лимит слотов"),
    (VOICE_EMOJI_TRANSFER_TEXT, "Передать владение"),
    (VOICE_EMOJI_RENAME_TEXT, "Переименовать комнату"),
    (VOICE_EMOJI_ACCESS_TEXT, "Выдать или забрать доступ"),
)


def build_voice_owner_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=True,
        stream=True,
        use_voice_activation=True,
        priority_speaker=True,
        move_members=True,
        mute_members=True,
        deafen_members=True,
        manage_channels=True,
    )


async def resolve_owned_voice_channel(
    interaction: discord.Interaction,
) -> tuple[discord.VoiceChannel, dict[str, Any]] | tuple[None, None]:
    if interaction.guild is None:
        return None, None

    reload_voice_rooms()
    owned_room = get_owned_voice_room(interaction.user.id, interaction.guild.id)
    if owned_room is None:
        await interaction.response.send_message(
            "У вас нет активной голосовой комнаты. Зайдите в канал создания комнаты и попробуйте снова.",
            ephemeral=True,
        )
        return None, None

    channel_id, room = owned_room
    channel = interaction.guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await interaction.guild.fetch_channel(channel_id)
        except Exception:
            channel = None

    if not isinstance(channel, discord.VoiceChannel):
        remove_voice_room(channel_id)
        await interaction.response.send_message(
            "Ваша голосовая комната не найдена. Зайдите в канал создания комнаты ещё раз.",
            ephemeral=True,
        )
        return None, None

    return channel, room


async def fetch_target_member(guild: discord.Guild, raw_value: str) -> discord.Member | None:
    user_id = extract_user_id(raw_value)
    if user_id is None:
        return None

    member = guild.get_member(user_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None


async def create_temporary_voice_room(member: discord.Member) -> discord.VoiceChannel | None:
    guild = member.guild
    project = get_project(guild)
    if project is None:
        return None
    existing_room = get_owned_voice_room(member.id, guild.id)
    if existing_room is not None:
        existing_channel = guild.get_channel(existing_room[0])
        if existing_channel is None:
            try:
                existing_channel = await guild.fetch_channel(existing_room[0])
            except Exception:
                existing_channel = None
        if isinstance(existing_channel, discord.VoiceChannel):
            return existing_channel
        remove_voice_room(existing_room[0])

    trigger_channel_id = int(project["voice_trigger_channel_id"])
    trigger_channel = guild.get_channel(trigger_channel_id)
    if trigger_channel is None:
        try:
            trigger_channel = await guild.fetch_channel(trigger_channel_id)
        except Exception:
            return None

    if not isinstance(trigger_channel, discord.VoiceChannel):
        return None

    room_name = f"Комната {member.display_name}"
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        member: build_voice_owner_overwrite(),
    }

    try:
        channel = await guild.create_voice_channel(
            name=room_name,
            category=trigger_channel.category,
            overwrites=overwrites,
            user_limit=2,
            bitrate=min(trigger_channel.bitrate, guild.bitrate_limit),
            reason=f"Временная голосовая комната для {member}",
        )
    except Exception:
        return None

    set_voice_room(
        channel.id,
        {
            "channelId": channel.id,
            "guildId": guild.id,
            "ownerId": member.id,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "name": channel.name,
        },
    )
    return channel


async def cleanup_voice_room_if_empty(channel: discord.abc.GuildChannel | None) -> None:
    if not isinstance(channel, discord.VoiceChannel):
        return
    if not get_voice_room(channel.id):
        return
    if channel.members:
        return

    remove_voice_room(channel.id)
    try:
        await channel.delete(reason="Пустая временная голосовая комната")
    except Exception:
        pass


async def cleanup_stale_voice_rooms(guild: discord.Guild) -> None:
    reload_voice_rooms()
    stale_ids: list[int] = []

    for channel_id in list(get_voice_rooms().keys()):
        try:
            voice_channel = guild.get_channel(int(channel_id)) or await guild.fetch_channel(int(channel_id))
        except Exception:
            voice_channel = None

        if not isinstance(voice_channel, discord.VoiceChannel):
            stale_ids.append(int(channel_id))
            continue

        if not voice_channel.members:
            try:
                await voice_channel.delete(reason="Очистка пустой временной комнаты после рестарта")
            except Exception:
                pass
            stale_ids.append(int(channel_id))

    for channel_id in stale_ids:
        remove_voice_room(channel_id)


# --- Модалки ---

class VoiceRoomUserModal(discord.ui.Modal):
    def __init__(self, action: str):
        titles = {
            "speak": "Управление голосом",
            "kick": "Исключить пользователя",
            "transfer": "Передать владение",
            "access": "Управление доступом",
        }
        super().__init__(title=titles.get(action, "Управление комнатой"), timeout=None)
        self.action = action
        self.user_value = discord.ui.TextInput(
            label="Укажите @пользователя или ID",
            placeholder="@user или 123456789012345678",
            max_length=64,
            required=True,
        )
        self.add_item(self.user_value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Сервер не найден.", ephemeral=True)
            return

        channel, room = await resolve_owned_voice_channel(interaction)
        if channel is None or room is None:
            return

        target = await fetch_target_member(interaction.guild, str(self.user_value).strip())
        if target is None:
            await interaction.response.send_message("Пользователь не найден.", ephemeral=True)
            return

        if self.action == "speak":
            overwrite = channel.overwrites_for(target)
            is_blocked = overwrite.speak is False
            overwrite.speak = None if is_blocked else False
            overwrite.view_channel = True if overwrite.view_channel is None else overwrite.view_channel
            overwrite.connect = True if overwrite.connect is None else overwrite.connect
            await channel.set_permissions(target, overwrite=overwrite, reason=f"Voice speak toggle by {interaction.user}")
            await interaction.response.send_message(
                f"{'Разрешил' if is_blocked else 'Запретил'} говорить пользователю {target.mention}.",
                ephemeral=True,
            )
            return

        if self.action == "kick":
            if target not in channel.members:
                await interaction.response.send_message("Этот пользователь сейчас не находится в вашей комнате.", ephemeral=True)
                return
            await target.move_to(None, reason=f"Voice room kick by {interaction.user}")
            await interaction.response.send_message(f"Пользователь {target.mention} исключён из комнаты.", ephemeral=True)
            return

        if self.action == "transfer":
            if target.id == interaction.user.id:
                await interaction.response.send_message("Вы уже являетесь владельцем этой комнаты.", ephemeral=True)
                return
            if target not in channel.members:
                await interaction.response.send_message(
                    "Передать владение можно только пользователю, который находится в вашей комнате.",
                    ephemeral=True,
                )
                return

            old_owner = interaction.guild.get_member(int(room.get("ownerId", interaction.user.id)))
            if old_owner is not None:
                await channel.set_permissions(
                    old_owner,
                    overwrite=discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
                    reason="Смена владельца временной комнаты",
                )
            await channel.set_permissions(
                target,
                overwrite=build_voice_owner_overwrite(),
                reason="Новый владелец временной комнаты",
            )
            room["ownerId"] = target.id
            room["name"] = channel.name
            set_voice_room(channel.id, room)
            await interaction.response.send_message(f"Права владельца переданы пользователю {target.mention}.", ephemeral=True)
            return

        if self.action == "access":
            if target.id == int(room.get("ownerId", 0)):
                await interaction.response.send_message("У владельца комнаты доступ нельзя забрать.", ephemeral=True)
                return
            overwrite = channel.overwrites_for(target)
            has_explicit_access = overwrite.connect is True
            overwrite.view_channel = True
            overwrite.connect = False if has_explicit_access else True
            await channel.set_permissions(target, overwrite=overwrite, reason=f"Voice access toggle by {interaction.user}")
            await interaction.response.send_message(
                f"{'Забрал' if has_explicit_access else 'Выдал'} доступ пользователю {target.mention}.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Неизвестное действие.", ephemeral=True)


class VoiceRoomRenameModal(discord.ui.Modal, title="Сменить название комнаты"):
    def __init__(self):
        super().__init__(timeout=None)
        self.room_name = discord.ui.TextInput(
            label="Новое название комнаты",
            placeholder="Введите новое название",
            max_length=80,
            required=True,
        )
        self.add_item(self.room_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel, room = await resolve_owned_voice_channel(interaction)
        if channel is None or room is None:
            return

        new_name = str(self.room_name).strip()
        if not new_name:
            await interaction.response.send_message("Название не может быть пустым.", ephemeral=True)
            return

        await channel.edit(name=new_name, reason=f"Voice room rename by {interaction.user}")
        room["name"] = new_name
        set_voice_room(channel.id, room)
        await interaction.response.send_message(
            f"Название комнаты изменено на **{discord.utils.escape_markdown(new_name)}**.",
            ephemeral=True,
        )


class VoiceRoomBitrateModal(discord.ui.Modal, title="Изменить битрейт комнаты"):
    def __init__(self):
        super().__init__(timeout=None)
        self.bitrate = discord.ui.TextInput(
            label="Введите битрейт (например 96)",
            placeholder="Значение в kbps",
            max_length=8,
            required=True,
        )
        self.add_item(self.bitrate)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Сервер не найден.", ephemeral=True)
            return

        channel, _room = await resolve_owned_voice_channel(interaction)
        if channel is None:
            return

        raw_value = re.sub(r"[^\d]", "", str(self.bitrate))
        if not raw_value:
            await interaction.response.send_message("Укажите числовое значение битрейта.", ephemeral=True)
            return

        bitrate_value = int(raw_value)
        if bitrate_value <= 384:
            bitrate_value *= 1000
        bitrate_value = max(8000, min(bitrate_value, interaction.guild.bitrate_limit))

        await channel.edit(bitrate=bitrate_value, reason=f"Voice bitrate updated by {interaction.user}")
        await interaction.response.send_message(
            f"Битрейт комнаты изменён на **{bitrate_value // 1000} kbps**.",
            ephemeral=True,
        )


class VoiceRoomSlotsModal(discord.ui.Modal, title="Установить количество слотов"):
    def __init__(self):
        super().__init__(timeout=None)
        self.slots = discord.ui.TextInput(
            label="Введите количество слотов",
            placeholder="От 0 до 99 (0 = без лимита)",
            max_length=3,
            required=True,
        )
        self.add_item(self.slots)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel, _room = await resolve_owned_voice_channel(interaction)
        if channel is None:
            return

        raw_value = re.sub(r"[^\d]", "", str(self.slots))
        if not raw_value:
            await interaction.response.send_message("Укажите число от 0 до 99.", ephemeral=True)
            return

        slots = int(raw_value)
        if slots < 0 or slots > 99:
            await interaction.response.send_message("Количество слотов должно быть от 0 до 99.", ephemeral=True)
            return
        if slots != 0 and slots < len(channel.members):
            await interaction.response.send_message(
                f"Сейчас в комнате {len(channel.members)} участников. Установите лимит не меньше этого числа.",
                ephemeral=True,
            )
            return

        await channel.edit(user_limit=slots, reason=f"Voice slots updated by {interaction.user}")
        label = "без лимита" if slots == 0 else str(slots)
        await interaction.response.send_message(f"Количество слотов обновлено: **{label}**.", ephemeral=True)


# --- Панель управления ---

class VoiceControlCard(discord.ui.LayoutView):
    """Панель голосовых комнат: баннер сверху, затем текст и ряды кнопок.

    Собрана Components V2-контейнером — у классического эмбеда картинка всегда
    уезжает под текст, а здесь порядок элементов задаётся явно.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

        controls_text = "\n".join(f"{emoji} {label}" for emoji, label in VOICE_CONTROLS)
        card = discord.ui.Container(
            brand_gallery(),
            discord.ui.TextDisplay("## Управление временной комнатой"),
            large_separator(),
            discord.ui.TextDisplay(controls_text),
            small_separator(),
            discord.ui.TextDisplay("> Создание временных комнат доступно для всех участников."),
            large_separator(),
            discord.ui.ActionRow(*self._build_row(0)),
            discord.ui.ActionRow(*self._build_row(1)),
            accent_colour=COLOR_PANEL,
        )
        self.add_item(card)

    def _build_row(self, row_index: int) -> list[discord.ui.Button]:
        specs = (
            (VOICE_EMOJI_ADD_SLOT, "voice_room_add_slot", self.add_slot),
            (VOICE_EMOJI_REMOVE_SLOT, "voice_room_remove_slot", self.remove_slot),
            (VOICE_EMOJI_LOCK, "voice_room_lock", self.toggle_lock),
            (VOICE_EMOJI_SPEAK, "voice_room_speak", self.toggle_speak),
            (VOICE_EMOJI_KICK, "voice_room_kick", self.kick_user),
            (VOICE_EMOJI_BITRATE, "voice_room_bitrate", self.change_bitrate),
            (VOICE_EMOJI_SET_SLOTS, "voice_room_set_slots", self.set_slots),
            (VOICE_EMOJI_TRANSFER, "voice_room_transfer", self.transfer_owner),
            (VOICE_EMOJI_RENAME, "voice_room_rename", self.rename_room),
            (VOICE_EMOJI_ACCESS, "voice_room_access", self.toggle_access),
        )
        buttons: list[discord.ui.Button] = []
        for emoji, custom_id, callback in specs[row_index * 5 : row_index * 5 + 5]:
            button = discord.ui.Button(emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=custom_id)
            button.callback = callback
            buttons.append(button)
        return buttons

    async def _owned_room(
        self, interaction: discord.Interaction
    ) -> tuple[discord.VoiceChannel, dict[str, Any]] | tuple[None, None]:
        return await resolve_owned_voice_channel(interaction)

    async def add_slot(self, interaction: discord.Interaction) -> None:
        channel, _room = await self._owned_room(interaction)
        if channel is None:
            return
        if channel.user_limit == 0:
            await interaction.response.send_message(
                "В комнате уже установлен безлимит. Сначала задайте конкретное количество слотов.",
                ephemeral=True,
            )
            return

        new_limit = min(channel.user_limit + 1, 99)
        if new_limit == channel.user_limit:
            await interaction.response.send_message("Достигнут максимальный лимит слотов.", ephemeral=True)
            return

        await channel.edit(user_limit=new_limit, reason=f"Voice slot add by {interaction.user}")
        await interaction.response.send_message(f"Добавил 1 слот. Теперь лимит комнаты: **{new_limit}**.", ephemeral=True)

    async def remove_slot(self, interaction: discord.Interaction) -> None:
        channel, _room = await self._owned_room(interaction)
        if channel is None:
            return
        if channel.user_limit == 0:
            await interaction.response.send_message(
                "Сейчас у комнаты нет лимита. Установите количество слотов вручную.",
                ephemeral=True,
            )
            return

        min_limit = max(1, len(channel.members))
        new_limit = max(min_limit, channel.user_limit - 1)
        if new_limit == channel.user_limit:
            await interaction.response.send_message(
                "Сейчас нельзя уменьшить лимит ниже текущего числа участников.",
                ephemeral=True,
            )
            return

        await channel.edit(user_limit=new_limit, reason=f"Voice slot remove by {interaction.user}")
        await interaction.response.send_message(f"Убрал 1 слот. Теперь лимит комнаты: **{new_limit}**.", ephemeral=True)

    async def toggle_lock(self, interaction: discord.Interaction) -> None:
        channel, _room = await self._owned_room(interaction)
        if channel is None or interaction.guild is None:
            return

        overwrite = channel.overwrites_for(interaction.guild.default_role)
        is_locked = overwrite.connect is False
        overwrite.view_channel = True
        overwrite.connect = None if is_locked else False
        await channel.set_permissions(
            interaction.guild.default_role,
            overwrite=overwrite,
            reason=f"Voice lock toggle by {interaction.user}",
        )
        await interaction.response.send_message(
            "Вход в комнату разрешён для всех."
            if is_locked
            else "Вход в комнату запрещён для всех, кроме тех, у кого есть доступ.",
            ephemeral=True,
        )

    async def toggle_speak(self, interaction: discord.Interaction) -> None:
        channel, _room = await self._owned_room(interaction)
        if channel is None:
            return
        await interaction.response.send_modal(VoiceRoomUserModal("speak"))

    async def kick_user(self, interaction: discord.Interaction) -> None:
        channel, _room = await self._owned_room(interaction)
        if channel is None:
            return
        await interaction.response.send_modal(VoiceRoomUserModal("kick"))

    async def change_bitrate(self, interaction: discord.Interaction) -> None:
        channel, _room = await self._owned_room(interaction)
        if channel is None:
            return
        await interaction.response.send_modal(VoiceRoomBitrateModal())

    async def set_slots(self, interaction: discord.Interaction) -> None:
        channel, _room = await self._owned_room(interaction)
        if channel is None:
            return
        await interaction.response.send_modal(VoiceRoomSlotsModal())

    async def transfer_owner(self, interaction: discord.Interaction) -> None:
        channel, _room = await self._owned_room(interaction)
        if channel is None:
            return
        await interaction.response.send_modal(VoiceRoomUserModal("transfer"))

    async def rename_room(self, interaction: discord.Interaction) -> None:
        channel, _room = await self._owned_room(interaction)
        if channel is None:
            return
        await interaction.response.send_modal(VoiceRoomRenameModal())

    async def toggle_access(self, interaction: discord.Interaction) -> None:
        channel, _room = await self._owned_room(interaction)
        if channel is None:
            return
        await interaction.response.send_modal(VoiceRoomUserModal("access"))


# --- События ---

async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    project = get_project(member.guild)
    if member.bot or project is None:
        return

    if is_famq_activity_guild(member.guild.id):
        await log_voice_activity(member, before, after)

    if before.channel is not None and before.channel != after.channel:
        await cleanup_voice_room_if_empty(before.channel)

    if after.channel is None or after.channel.id != int(project["voice_trigger_channel_id"]):
        return

    target_channel = await create_temporary_voice_room(member)
    if target_channel is None:
        return

    try:
        await member.move_to(target_channel, reason="Создание временной голосовой комнаты")
    except Exception:
        pass


async def log_voice_activity(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    from logs import fetch_optional_audit_executor, send_activity_log

    footer = [f"ID пользователя: {member.id}", format_log_time_msk()]
    author_name = f"{member} ({member.id})"

    if before.channel != after.channel:
        if before.channel is None and after.channel is not None:
            await send_activity_log(
                member.guild,
                title="Голосовая активность",
                description=f"{VOICE_EMOJI_ADD_SLOT_TEXT} пользователь подключился к голосовому каналу",
                author_name=author_name,
                author_icon_url=member.display_avatar.url,
                fields=[
                    ("Пользователь", member.mention, True),
                    ("Канал", format_channel_label(after.channel), True),
                ],
                footer_parts=footer,
            )
        elif before.channel is not None and after.channel is None:
            actor, _entry = await fetch_optional_audit_executor(member.guild, "member_disconnect", target_id=member.id)
            description = (
                f"{VOICE_EMOJI_KICK_TEXT} пользователь был исключён из голосового канала"
                if actor is not None
                else f"{VOICE_EMOJI_REMOVE_SLOT_TEXT} пользователь покинул голосовой канал"
            )
            fields = [
                ("Пользователь", member.mention, True),
                ("Канал", format_channel_label(before.channel), True),
            ]
            if actor is not None:
                fields.append(("Изменил", actor.mention, True))
            await send_activity_log(
                member.guild,
                title="Голосовая активность",
                description=description,
                author_name=author_name,
                author_icon_url=member.display_avatar.url,
                fields=fields,
                footer_parts=footer,
            )
        elif before.channel is not None and after.channel is not None:
            await send_activity_log(
                member.guild,
                title="Голосовая активность",
                description=f"{VOICE_EMOJI_TRANSFER_TEXT} пользователь перешёл в другой голосовой канал",
                author_name=author_name,
                author_icon_url=member.display_avatar.url,
                fields=[
                    ("Пользователь", member.mention, True),
                    ("Из", format_channel_label(before.channel), True),
                    ("В", format_channel_label(after.channel), True),
                ],
                footer_parts=footer,
            )

    if before.self_stream != after.self_stream:
        await send_activity_log(
            member.guild,
            title="Стрим в голосовом канале",
            description=(
                f"{VOICE_EMOJI_BITRATE_TEXT} пользователь начал стрим"
                if after.self_stream
                else f"{VOICE_EMOJI_BITRATE_TEXT} пользователь закончил стрим"
            ),
            author_name=author_name,
            author_icon_url=member.display_avatar.url,
            fields=[
                ("Пользователь", member.mention, True),
                ("Канал", format_channel_label(after.channel or before.channel), True),
            ],
            footer_parts=footer,
        )

    if before.self_video != after.self_video:
        await send_activity_log(
            member.guild,
            title="Камера в голосовом канале",
            description=(
                f"{VOICE_EMOJI_ACCESS_TEXT} пользователь включил камеру"
                if after.self_video
                else f"{VOICE_EMOJI_ACCESS_TEXT} пользователь выключил камеру"
            ),
            author_name=author_name,
            author_icon_url=member.display_avatar.url,
            fields=[
                ("Пользователь", member.mention, True),
                ("Канал", format_channel_label(after.channel or before.channel), True),
            ],
            footer_parts=footer,
        )


def register_listeners() -> None:
    bot.add_listener(on_voice_state_update, "on_voice_state_update")
