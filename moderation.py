"""Префиксные команды модерации и досье участника (`!usi`)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands

from applications_flow import ApplicationToggleView
from botcore import bot
from channels import delete_message_safely
from config import *
from embeds import build_usi_embed, make_embed
from formatting import format_datetime_msk, parse_iso, split_long_message, trim_embed_text
from nicknames import publish_staff_and_nickname_panels
from projects import (
    get_manageable_application_options,
    get_server_plain_label,
    has_application_control_access,
    member_has_any_role,
)
from state import (
    application_store,
    format_member_event_line,
    get_member_activity_record,
    reload_applications,
    reload_member_activity,
    save_member_activity,
    update_member_activity_profile,
)


def has_usi_access(member: discord.Member | None) -> bool:
    return member_has_any_role(member, USI_ALLOWED_ROLE_IDS)


def format_roles_for_usi(guild: discord.Guild, member: discord.Member | None, record: dict[str, Any]) -> str:
    if member is not None:
        role_mentions = [role.mention for role in reversed(member.roles) if role != guild.default_role]
        if role_mentions:
            return ", ".join(role_mentions)
    known_role_names = [str(name) for name in record.get("knownRoleNames", []) if str(name).strip()]
    if known_role_names:
        return trim_embed_text(", ".join(f"`{name}`" for name in known_role_names))
    return "Роли ещё не зафиксированы."


def build_customization_lines(member: discord.Member | None, user: discord.abc.User) -> str:
    lines = [f"[Аватар]({user.display_avatar.url})"]
    guild_avatar = getattr(member, "guild_avatar", None) if member is not None else None
    if guild_avatar is not None:
        lines.append(f"[Серверный аватар]({guild_avatar.url})")
    banner = getattr(user, "banner", None)
    if banner is not None:
        lines.append(f"[Баннер]({banner.url})")
    avatar_decoration = getattr(user, "avatar_decoration", None)
    avatar_decoration_sku_id = getattr(user, "avatar_decoration_sku_id", None)
    if avatar_decoration is not None:
        decoration_url = getattr(avatar_decoration, "url", None)
        if decoration_url:
            lines.append(f"[Украшение]({decoration_url})")
    elif avatar_decoration_sku_id:
        lines.append(f"Украшение: `{avatar_decoration_sku_id}`")
    if getattr(user, "accent_color", None):
        lines.append(f"Акцент: `{str(user.accent_color)}`")
    if getattr(user, "global_name", None):
        lines.append(f"Неймплейс: `{user.global_name}`")
    return trim_embed_text(" • ".join(lines))


def build_join_history_text(record: dict[str, Any]) -> str:
    join_events = list(record.get("joinEvents", []))
    leave_events = list(record.get("leaveEvents", []))
    summary = [
        f"Вступлений: **{len(join_events)}**",
        f"Выходов: **{len(leave_events)}**",
    ]
    recent_events = leave_events[-3:]
    if recent_events:
        summary.append("")
        summary.append("Последние выходы:")
        summary.extend([f"• {format_member_event_line(event)}" for event in reversed(recent_events)])
    elif join_events:
        summary.append("")
        summary.append(f"Последний вход: {format_member_event_line(join_events[-1])}")
    else:
        summary.append("")
        summary.append("История входов пока не зафиксирована.")
    return trim_embed_text("\n".join(summary))


class ApplicationHistoryView(discord.ui.View):
    def __init__(self, guild_id: int, target_user_id: int, requester_id: int):
        super().__init__(timeout=900)
        self.guild_id = guild_id
        self.target_user_id = target_user_id
        self.requester_id = requester_id

    @discord.ui.button(label="История по заявкам", style=discord.ButtonStyle.secondary)
    async def history_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if interaction.user.id != self.requester_id and not has_usi_access(member):
            await interaction.response.send_message("Недостаточно прав для просмотра истории заявок.", ephemeral=True)
            return

        reload_applications()
        items = [
            app
            for app in application_store.get("items", {}).values()
            if int(app.get("guildId", 0)) == self.guild_id and int(app.get("applicantId", 0)) == self.target_user_id
        ]
        if not items:
            await interaction.response.send_message("Пользователь не подал ещё ни одной заявки.", ephemeral=True)
            return

        items.sort(key=lambda app: parse_iso(str(app.get("submittedAt", ""))), reverse=True)
        lines: list[str] = []
        for app in items[:15]:
            submitted = format_datetime_msk(parse_iso(str(app.get("submittedAt", ""))))
            decided_at = parse_iso(str(app.get("decidedAt", ""))) if app.get("decidedAt") else None
            decided_text = format_datetime_msk(decided_at) if decided_at is not None else "ещё не обработана"
            lines.append(
                "\n".join(
                    [
                        f"**#{app['id']}** — {get_server_plain_label(str(app.get('server', '')))}",
                        f"Статус: `{app.get('status', 'unknown')}`",
                        f"Подана: {submitted}",
                        f"Решение: {decided_text}",
                    ]
                )
            )

        embed = make_embed(
            title=f"{EMOJI_REVIEW_TEXT} История заявок пользователя",
            description="\n\n".join(lines),
            color=COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def clone_role_with_overwrites(
    guild: discord.Guild,
    source_role: discord.Role,
    new_name: str,
    reason: str,
) -> tuple[discord.Role, int]:
    created_role = await guild.create_role(
        name=new_name,
        permissions=source_role.permissions,
        colour=source_role.colour,
        hoist=source_role.hoist,
        mentionable=source_role.mentionable,
        reason=reason,
    )

    try:
        bot_member = guild.me or await guild.fetch_member(bot.user.id if bot.user else 0)
    except Exception:
        bot_member = guild.me

    if bot_member is not None:
        try:
            target_position = min(source_role.position, max(bot_member.top_role.position - 1, 1))
            if target_position > 0:
                await created_role.edit(position=target_position, reason=reason)
        except Exception:
            pass

    copied_overwrites = 0
    for channel in guild.channels:
        overwrite = channel.overwrites_for(source_role)
        if overwrite.is_empty():
            continue
        try:
            await channel.set_permissions(created_role, overwrite=overwrite, reason=reason)
            copied_overwrites += 1
        except Exception:
            continue

    return created_role, copied_overwrites


@bot.command(name="say")
@commands.has_permissions(manage_messages=True)
async def say_command(ctx: commands.Context, *, content: str) -> None:
    if not content.strip():
        await ctx.reply("Укажите сообщение для отправки.", mention_author=False, delete_after=5)
        return

    await delete_message_safely(ctx.message)
    for chunk in split_long_message(content):
        await ctx.send(chunk)


@bot.command(name="clsapp")
async def close_application_command(ctx: commands.Context) -> None:
    await _toggle_application_command(ctx, "close")


@bot.command(name="opnapp")
async def open_application_command(ctx: commands.Context) -> None:
    await _toggle_application_command(ctx, "open")


async def _toggle_application_command(ctx: commands.Context, action: str) -> None:
    if ctx.guild is None:
        await ctx.reply("Команда доступна только на сервере.", mention_author=False, delete_after=5)
        return
    member = ctx.author if isinstance(ctx.author, discord.Member) else None
    if not has_application_control_access(member, guild_id=ctx.guild.id):
        await ctx.reply("Недостаточно прав для управления заявками.", mention_author=False, delete_after=5)
        return

    if not get_manageable_application_options(ctx.guild.id):
        await ctx.reply("Для этого сервера нет доступных направлений заявок.", mention_author=False, delete_after=5)
        return

    verb = "закрыть" if action == "close" else "открыть"
    await ctx.send(
        f"Выберите сервер, для которого нужно {verb} подачу заявок.",
        view=ApplicationToggleView(ctx.guild.id, action, ctx.author.id),
    )


@bot.command(name="staffpanels")
async def staff_panels_command(ctx: commands.Context) -> None:
    if ctx.guild is None or ctx.guild.id != FAMQ_GUILD_ID:
        await ctx.reply("Команда доступна только на основном сервере ASIXEZ.", mention_author=False, delete_after=5)
        return
    member = ctx.author if isinstance(ctx.author, discord.Member) else None
    if not has_application_control_access(member, guild_id=ctx.guild.id) and not ctx.author.guild_permissions.manage_guild:
        await ctx.reply("Недостаточно прав для обновления панелей состава.", mention_author=False, delete_after=5)
        return

    notice = await ctx.reply("Обновляю панель состава и отчёт по никам...", mention_author=False)
    issues = await publish_staff_and_nickname_panels(ctx.guild)
    if issues:
        await notice.edit(content="Не всё получилось:\n" + "\n".join(f"• {issue}" for issue in issues))
        return
    await notice.edit(content="Готово: панель состава и отчёт по никам обновлены.")


@bot.command(name="roleclone")
@commands.has_permissions(manage_roles=True, manage_channels=True)
async def roleclone_command(ctx: commands.Context, source_role: discord.Role, *, new_name: str) -> None:
    if ctx.guild is None:
        await ctx.reply("Команда доступна только на сервере.", mention_author=False, delete_after=5)
        return

    bot_member = ctx.guild.me
    if bot_member is None:
        try:
            bot_member = await ctx.guild.fetch_member(bot.user.id if bot.user else 0)
        except Exception:
            bot_member = None

    if bot_member is None:
        await ctx.reply("Не удалось определить бота на сервере.", mention_author=False, delete_after=5)
        return

    if source_role >= bot_member.top_role:
        await ctx.reply(
            "Я не могу клонировать роль, которая выше или равна моей верхней роли.",
            mention_author=False,
            delete_after=5,
        )
        return

    await delete_message_safely(ctx.message)

    status_message = await ctx.send(f"Клонирую роль **{discord.utils.escape_markdown(source_role.name)}**...")
    try:
        created_role, copied_overwrites = await clone_role_with_overwrites(
            ctx.guild,
            source_role,
            new_name.strip(),
            reason=f"Role cloned by {ctx.author}",
        )
    except Exception:
        await status_message.edit(
            content="Не удалось клонировать роль. Проверь мои права `Manage Roles` и `Manage Channels`."
        )
        return

    await status_message.edit(
        content=(
            f"Роль успешно клонирована: {created_role.mention}\n"
            f"Источник: **{discord.utils.escape_markdown(source_role.name)}**\n"
            f"Перенесено channel overwrites: **{copied_overwrites}**"
        )
    )


@bot.command(name="clear")
@commands.has_permissions(manage_messages=True)
async def clear_command(ctx: commands.Context, amount: int) -> None:
    if amount <= 0:
        await ctx.reply("Укажите число больше 0.", mention_author=False, delete_after=5)
        return

    amount = min(amount, 100)
    if not isinstance(ctx.channel, discord.TextChannel):
        await ctx.reply("Команда доступна только в текстовом канале.", mention_author=False, delete_after=5)
        return

    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
        confirmation = await ctx.send(f"Удалено сообщений: {max(len(deleted) - 1, 0)}")
        await confirmation.delete(delay=5)
    except Exception:
        await ctx.reply("Не удалось удалить сообщения. Проверь права бота.", mention_author=False, delete_after=5)


@bot.command(name="usi")
async def usi_command(ctx: commands.Context, user_id: str) -> None:
    if ctx.guild is None:
        await ctx.reply("Команда доступна только на сервере.", mention_author=False, delete_after=5)
        return

    member = ctx.author if isinstance(ctx.author, discord.Member) else None
    if not has_usi_access(member):
        await ctx.reply("Недостаточно прав для выполнения команды.", mention_author=False, delete_after=5)
        return

    cleaned_user_id = re.sub(r"[^\d]", "", user_id)
    if not cleaned_user_id:
        await ctx.reply("Укажите корректный Discord ID пользователя.", mention_author=False, delete_after=5)
        return

    target_user_id = int(cleaned_user_id)
    target_member = ctx.guild.get_member(target_user_id)
    if target_member is None:
        try:
            target_member = await ctx.guild.fetch_member(target_user_id)
        except Exception:
            target_member = None

    try:
        target_user = await bot.fetch_user(target_user_id)
    except Exception:
        if target_member is None:
            await ctx.reply("Не удалось найти пользователя по этому Discord ID.", mention_author=False, delete_after=5)
            return
        target_user = target_member

    reload_member_activity()
    if target_member is not None:
        update_member_activity_profile(target_member)
        save_member_activity()
    record = get_member_activity_record(ctx.guild.id, target_user_id)
    embed = build_usi_embed(
        ctx.guild,
        target_member,
        target_user,
        record,
        roles_text=format_roles_for_usi(ctx.guild, target_member, record),
        customization_text=build_customization_lines(target_member, target_user),
        join_history_text=build_join_history_text(record),
    )
    await ctx.send(embed=embed, view=ApplicationHistoryView(ctx.guild.id, target_user_id, ctx.author.id))


@say_command.error
@clear_command.error
@roleclone_command.error
@usi_command.error
async def moderation_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("Недостаточно прав для выполнения команды.", mention_author=False, delete_after=5)
    elif isinstance(error, commands.MissingRequiredArgument):
        if ctx.command and ctx.command.name == "usi":
            await ctx.reply("Использование: `!usi <discord_id>`", mention_author=False, delete_after=5)
        else:
            await ctx.reply("Использование: `!roleclone <роль> <новое имя>`", mention_author=False, delete_after=5)
    elif isinstance(error, commands.BadArgument):
        await ctx.reply("Неверный формат команды.", mention_author=False, delete_after=5)
