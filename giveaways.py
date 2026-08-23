"""Розыгрыши: создание, участие и автоматическое подведение итогов."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

import discord

from botcore import bot
from config import *
from embeds import build_giveaway_closed_embed, build_giveaway_embed, make_embed
from formatting import parse_hours_input, parse_iso
from state import (
    giveaway_store,
    next_giveaway_id,
    reload_giveaways,
    save_giveaways,
)


giveaway_tasks: dict[int, asyncio.Task] = {}


async def finish_giveaway(giveaway_id: int) -> None:
    try:
        reload_giveaways()
        giveaway = giveaway_store["items"].get(str(giveaway_id))
        if not giveaway or giveaway.get("status") != "active":
            return

        delay = (parse_iso(giveaway.get("endsAt")) - datetime.now(timezone.utc)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        reload_giveaways()
        giveaway = giveaway_store["items"].get(str(giveaway_id))
        if not giveaway or giveaway.get("status") != "active":
            return

        try:
            guild = bot.get_guild(FAMQ_GUILD_ID) or await bot.fetch_guild(FAMQ_GUILD_ID)
            channel = guild.get_channel(int(giveaway["channelId"])) or await guild.fetch_channel(int(giveaway["channelId"]))
        except Exception:
            return

        if not isinstance(channel, discord.TextChannel):
            return

        participants = list(dict.fromkeys(int(user_id) for user_id in giveaway.get("participants", [])))
        creator_id = int(giveaway["creatorId"])
        winner_id = random.choice(participants) if participants else None

        giveaway["status"] = "finished"
        giveaway["finishedAt"] = datetime.now(timezone.utc).isoformat()
        giveaway["winnerId"] = winner_id or 0
        giveaway_store["items"][str(giveaway_id)] = giveaway
        save_giveaways()

        message = None
        try:
            message = await channel.fetch_message(int(giveaway["messageId"]))
        except Exception:
            message = None

        creator_mention = f"<@{creator_id}>"
        winner_mention = f"<@{winner_id}>" if winner_id else None
        closed_view = GiveawayJoinView(giveaway_id)
        for child in closed_view.children:
            child.disabled = True

        if message is not None:
            await message.edit(
                content="@everyone",
                embed=build_giveaway_closed_embed(giveaway, creator_mention, winner_mention),
                view=closed_view,
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )

        if winner_id:
            await channel.send(
                content=f"{winner_mention} победил в розыгрыше от {creator_mention}.",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            try:
                winner_user = guild.get_member(winner_id) or await bot.fetch_user(winner_id)
                creator_user = guild.get_member(creator_id) or await bot.fetch_user(creator_id)
                await winner_user.send(
                    embed=make_embed(
                        title=f"{EMOJI_ACCEPT_TEXT} Вы победили в розыгрыше!",
                        description="\n".join(
                            [
                                f"Вы выиграли: **{giveaway.get('prize', 'Приз')}**",
                                f"Организатор: {creator_user.mention if hasattr(creator_user, 'mention') else creator_mention}",
                                "Отправьте организатору в личные сообщения доказательства выполнения условий.",
                                f"Discord организатора: `{getattr(creator_user, 'name', str(creator_id))}`",
                            ]
                        ),
                        color=COLOR_SOFT,
                        timestamp=datetime.now(timezone.utc),
                    )
                )
            except Exception:
                pass
        else:
            await channel.send("Розыгрыш завершён без участников.")
    finally:
        giveaway_tasks.pop(giveaway_id, None)


def ensure_giveaway_task(giveaway_id: int) -> None:
    existing = giveaway_tasks.get(giveaway_id)
    if existing and not existing.done():
        return
    giveaway_tasks[giveaway_id] = asyncio.create_task(finish_giveaway(giveaway_id))


class GiveawayConfirmView(discord.ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=180)
        self.giveaway_id = giveaway_id

    @discord.ui.button(label="Да", style=discord.ButtonStyle.secondary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        reload_giveaways()
        giveaway = giveaway_store["items"].get(str(self.giveaway_id))
        if not giveaway or giveaway.get("status") != "active":
            await interaction.response.edit_message(content="Этот розыгрыш уже завершён.", view=None)
            return

        user_id = interaction.user.id
        participants = list(dict.fromkeys(int(user) for user in giveaway.get("participants", [])))
        if user_id not in participants:
            participants.append(user_id)
            giveaway["participants"] = participants
            giveaway_store["items"][str(self.giveaway_id)] = giveaway
            save_giveaways()

            try:
                guild = interaction.guild or bot.get_guild(FAMQ_GUILD_ID)
                if guild is not None:
                    channel = guild.get_channel(int(giveaway["channelId"])) or await guild.fetch_channel(int(giveaway["channelId"]))
                    if isinstance(channel, discord.TextChannel):
                        message = await channel.fetch_message(int(giveaway["messageId"]))
                        await message.edit(
                            content="@everyone",
                            embed=build_giveaway_embed(giveaway, f"<@{giveaway['creatorId']}>"),
                            view=GiveawayJoinView(self.giveaway_id),
                            allowed_mentions=discord.AllowedMentions(everyone=True),
                        )
                reload_giveaways()
            except Exception:
                pass

        await interaction.response.edit_message(content="Вы успешно приняли участие в розыгрыше.", view=None)

    @discord.ui.button(label="Нет", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Участие отменено.", view=None)


class GiveawayJoinView(discord.ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        reload_giveaways()
        giveaway = giveaway_store["items"].get(str(giveaway_id), {})
        participant_count = len(giveaway.get("participants", []))
        is_active = giveaway.get("status") == "active"

        join_button = discord.ui.Button(
            custom_id=f"giveaway_join_{giveaway_id}",
            label=f"Принять участие ({participant_count})",
            emoji=EMOJI_ACCEPT,
            style=discord.ButtonStyle.secondary,
            disabled=not is_active,
        )
        join_button.callback = self.join_callback
        self.add_item(join_button)

    async def join_callback(self, interaction: discord.Interaction) -> None:
        reload_giveaways()
        giveaway = giveaway_store["items"].get(str(self.giveaway_id))
        if not giveaway or giveaway.get("status") != "active":
            await interaction.response.send_message("Этот розыгрыш уже завершён.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Нажимая кнопку вы подтверждаете выполнение условий, в случае если условия не выполнены при победе вы ничего не получите.",
            view=GiveawayConfirmView(self.giveaway_id),
            ephemeral=True,
        )


class GiveawayModal(discord.ui.Modal, title="Создание розыгрыша"):
    conditions = discord.ui.TextInput(
        label="Введите условия розыгрыша",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
        placeholder="Опишите подробно, что нужно сделать для участия.",
    )
    prize = discord.ui.TextInput(
        label="Введите сумму розыгрыша (с валютой)",
        max_length=120,
        required=True,
        placeholder="Например: 5.000.000$",
    )
    duration_hours = discord.ui.TextInput(
        label="Введите время розыгрыша в часах",
        max_length=6,
        required=True,
        placeholder="Например: 24",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        hours = parse_hours_input(str(self.duration_hours))
        if hours is None:
            await interaction.response.send_message("Укажите время розыгрыша целым числом часов.", ephemeral=True)
            return

        if interaction.guild is None or interaction.guild.id != FAMQ_GUILD_ID:
            await interaction.response.send_message("Команда доступна только на сервере ASIXEZ.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        reload_giveaways()
        giveaway_id = next_giveaway_id()
        save_giveaways()

        try:
            channel = interaction.guild.get_channel(GIVEAWAY_CHANNEL_ID) or await interaction.guild.fetch_channel(GIVEAWAY_CHANNEL_ID)
        except Exception:
            await interaction.followup.send("Канал розыгрышей не найден.", ephemeral=True)
            return

        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("Канал розыгрышей недоступен.", ephemeral=True)
            return

        ends_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        giveaway = {
            "id": giveaway_id,
            "creatorId": interaction.user.id,
            "channelId": channel.id,
            "messageId": 0,
            "conditions": str(self.conditions).strip(),
            "prize": str(self.prize).strip(),
            "durationHours": hours,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "endsAt": ends_at.isoformat(),
            "status": "active",
            "participants": [],
            "winnerId": 0,
        }
        giveaway_store["items"][str(giveaway_id)] = giveaway
        save_giveaways()

        message = await channel.send(
            content="@everyone",
            embed=build_giveaway_embed(giveaway, interaction.user.mention),
            view=GiveawayJoinView(giveaway_id),
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )
        giveaway["messageId"] = message.id
        giveaway_store["items"][str(giveaway_id)] = giveaway
        save_giveaways()
        bot.add_view(GiveawayJoinView(giveaway_id), message_id=message.id)
        ensure_giveaway_task(giveaway_id)

        await interaction.followup.send(f"Розыгрыш #{giveaway_id} опубликован в <#{channel.id}>.", ephemeral=True)

