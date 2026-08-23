"""Сборка эмбедов.

Баннер семьи подключается только там, где он осмыслен: личные сообщения по
заявкам и приветствие. Панели заявок, информации и голосовых комнат собираются
Components V2-карточками в :mod:`components`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

from applications import build_answer_block, extract_legacy_irl_name
from config import *
from formatting import (
    format_datetime_msk,
    format_log_time_msk,
    format_relative_time,
    parse_iso,
    trim_embed_text,
)
from projects import (
    get_project,
    get_project_name,
    get_server_label,
    get_server_plain_label,
    is_friend_verification_application,
)


def normalize_theme_color(color: int | None = None) -> int:
    """Приводит «сигнальные» цвета Discord к монохромной палитре бота.

    Раньше функция схлопывала в ``COLOR`` вообще любой цвет, поэтому все эмбеды
    выходили одинаково почти чёрными. Теперь переопределяются только «сигнальные»
    значения, а осознанно выбранные цвета палитры доходят до Discord как есть.
    """
    if color in {0x2ECC71, 0x1F8B4C, 0x0F7B53}:
        return COLOR_SOFT
    if color in {0xE74C3C, 0xF1C40F}:
        return COLOR_MUTED
    return COLOR if color is None else color


def make_embed(
    *,
    title: str | None = None,
    description: str | None = None,
    color: int | None = None,
    timestamp: datetime | None = None,
    banner: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=normalize_theme_color(color),
        timestamp=timestamp,
    )
    if banner and FAMILY_BRAND_BANNER_URL:
        embed.set_image(url=FAMILY_BRAND_BANNER_URL)
    return embed


# --- Заявки ---

def build_application_fields(application: dict[str, Any]) -> list[tuple[str, str]]:
    """Пары «вопрос анкеты → ответ» в порядке подачи."""
    if is_friend_verification_application(str(application.get("server", ""))):
        return [
            ("01. Ваше имя, фамилия в игре", application.get("friendNameGame") or "—"),
            ("02. В какой семье вы состоите?", application.get("friendFamily") or "—"),
        ]
    return [
        ("01. Имя IRL", application.get("irlName") or extract_legacy_irl_name(application.get("nameAge", "")) or "—"),
        ("02. Возраст IRL", application.get("ageIrl") or application.get("nameAge") or "—"),
        ("03. Левел, онлайн и часовой пояс", application.get("levelOnline") or "—"),
        ("04. Фракция", application.get("fraction") or "—"),
        ("05. Ник и Static-ID", application.get("nameStatic") or "—"),
    ]


def build_application_embed(application: dict[str, Any], applicant_tag: str) -> discord.Embed:
    """Резервное представление заявки эмбедом (используется при сбое карточки)."""
    is_friend = is_friend_verification_application(str(application.get("server", "")))
    header = (
        f"{EMOJI_FRIEND_TEXT} **Тип:** {get_server_label(application['server'])}"
        if is_friend
        else f"{get_server_label(application['server'])} **Сервер:** {get_server_plain_label(application['server'])}"
    )
    blocks = [f"{EMOJI_REVIEW_TEXT} **Заявитель:** {applicant_tag}", header]
    blocks.extend(build_answer_block(label, value) for label, value in build_application_fields(application))
    description = "\n\n".join(blocks)
    if application.get("claimedBy"):
        description += f"\n\n**Заявка закреплена за:** <@{application['claimedBy']}>"

    title_prefix = "Верификация для друзей" if is_friend else "Заявка"
    embed = make_embed(
        title=f"{title_prefix} #{application['id']}",
        description=description,
        color=COLOR_PANEL,
        timestamp=parse_iso(application.get("submittedAt")),
    )
    embed.set_footer(text=f"ID заявителя: {application['applicantId']}")
    return embed


def build_dm_embed(title: str, description: str) -> discord.Embed:
    """Личное сообщение участнику: всегда с фирменным баннером."""
    return make_embed(
        title=title,
        description=description,
        color=COLOR_SOFT,
        timestamp=datetime.now(timezone.utc),
        banner=True,
    )


def build_result_embed(
    app: dict[str, Any],
    recruiter_user_id: int,
    verdict: str,
    reject_reason: str | None = None,
) -> discord.Embed:
    is_accepted = verdict == "accepted"
    is_friend = is_friend_verification_application(app["server"])
    embed = make_embed(
        color=COLOR_SOFT if is_accepted else COLOR_MUTED,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"{get_project_name(int(app.get('guildId', 0)))} • {get_server_plain_label(app['server'])}")

    if is_accepted:
        embed.title = f"{EMOJI_ACCEPT_TEXT} {'Верификация одобрена' if is_friend else 'Заявка принята'}"
        embed.description = "\n".join(
            [
                f"Заявка от пользователя <@{app['applicantId']}>",
                "",
                (
                    f"Верификация друга семьи была **одобрена**. {EMOJI_REVIEW_TEXT}"
                    if is_friend
                    else f"На вступление в семью была **принята**. {EMOJI_REVIEW_TEXT}"
                ),
                "",
                f"**Рассматривал заявку:** <@{recruiter_user_id}>",
                "",
                (
                    "> После выдачи роли поставьте ник по форме, указанной в личных сообщениях."
                    if is_friend
                    else "> Никнейм на сервере: ASIXEZ | Ник | Статик."
                ),
            ]
        )
    else:
        embed.title = f"{EMOJI_REJECT_TEXT} {'Верификация отклонена' if is_friend else 'Заявка отклонена'}"
        embed.description = "\n".join(
            [
                f"Заявка от пользователя <@{app['applicantId']}>",
                "",
                (
                    "Верификация друга семьи была **отклонена**. ❌"
                    if is_friend
                    else "На вступление в семью была **отклонена**. ❌"
                ),
                "",
                f"**Причина:** {reject_reason or 'не указана'}",
                f"**Рассматривал заявку:** <@{recruiter_user_id}>",
            ]
        )
    return embed


def build_panel_embed(guild_id: int, guild: discord.Guild | None = None) -> discord.Embed:
    """Панель заявок для проектов без Components V2-карточки (ASIXEZ RU)."""
    from state import get_visible_application_options

    project = get_project(guild_id)
    visible_options = get_visible_application_options(guild_id)
    open_lines = [f"{option.get('emoji_text', '•')} **{option['label']}**" for option in visible_options]
    open_block = "\n".join(open_lines) if open_lines else f"{EMOJI_REJECT_TEXT} `набор временно закрыт`"

    description = "\n".join(
        [
            f"## {EMOJI_ACCEPT_TEXT} Заявки в {get_project_name(guild_id)}",
            "",
            f"{EMOJI_REVIEW_TEXT} **Доступно сейчас**",
            open_block,
            "",
            f"{EMOJI_CALL_TEXT} Ответ и приглашение на обзвон придут прямо в ваш канал заявки.",
            "",
            f"{EMOJI_REJECT_TEXT} Если кнопка ниже недоступна, набор временно закрыт.",
        ]
    )
    embed = make_embed(description=description, color=COLOR_PANEL)
    embed.set_author(name=project.get("project_name", "ASIXEZ") if project else "ASIXEZ")
    embed.set_footer(text="ASIXEZ • Application Center • обычно 20-60 минут")
    return embed


# --- Логи ---

def build_log_line_embed(
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
) -> discord.Embed:
    """Единый формат для основного лога и лога заявок."""
    embed = make_embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    if author_name:
        if author_icon_url:
            embed.set_author(name=author_name, icon_url=author_icon_url)
        else:
            embed.set_author(name=author_name)
    for name, value, inline in fields:
        if not value:
            continue
        embed.add_field(name=name, value=trim_embed_text(value), inline=inline)
    if footer_parts:
        embed.set_footer(text=" • ".join(part for part in footer_parts if part))
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    if image_url:
        embed.set_image(url=image_url)
    return embed


def build_security_embed(
    *,
    title: str,
    color: int,
    user_id: int | None,
    action_label: str,
    count_label: str,
    result_label: str,
    extra_lines: list[str] | None = None,
    source_message_id: int | None = None,
) -> discord.Embed:
    embed = make_embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    embed.add_field(
        name="👤 User",
        value=(f"<@{user_id}> (`{user_id}`)" if user_id else "Не определён"),
        inline=False,
    )
    embed.add_field(name="📌 Action", value=action_label, inline=True)
    embed.add_field(name="📊 Count", value=count_label, inline=True)
    embed.add_field(name="⚡ Result", value=result_label, inline=False)
    if extra_lines:
        embed.add_field(name="🧾 Details", value="\n".join(extra_lines)[:1024], inline=False)
    if source_message_id:
        embed.add_field(name="🆔 Message ID", value=str(source_message_id), inline=False)
    return embed


# --- Розыгрыши ---

def build_giveaway_embed(giveaway: dict[str, Any], creator_mention: str) -> discord.Embed:
    participants = len(giveaway.get("participants", []))
    ends_at = parse_iso(giveaway.get("endsAt"))
    embed = make_embed(
        title=f"{EMOJI_ACCEPT_TEXT} Розыгрыш {giveaway.get('prize', 'Приз')}",
        description="\n".join(
            [
                f"**Организатор:** {creator_mention}",
                f"**Приз:** {giveaway.get('prize', 'Не указан')}",
                "",
                f"{EMOJI_REVIEW_TEXT} **Условия участия:**",
                giveaway.get("conditions", "Не указаны"),
                "",
                f"{EMOJI_CALL_TEXT} **Участников:** {participants}",
                f"{EMOJI_DETROIT_TEXT} **Завершение:** <t:{int(ends_at.timestamp())}:R>",
            ]
        ),
        color=COLOR_SOFT,
        timestamp=ends_at,
    )
    embed.set_footer(text=f"Giveaway #{giveaway['id']}")
    return embed


def build_giveaway_closed_embed(
    giveaway: dict[str, Any],
    creator_mention: str,
    winner_mention: str | None,
) -> discord.Embed:
    embed = build_giveaway_embed(giveaway, creator_mention)
    embed.color = COLOR_SOFT if winner_mention else COLOR_MUTED
    if winner_mention:
        embed.description += f"\n\n{EMOJI_ACCEPT_TEXT} **Победитель:** {winner_mention}"
    else:
        embed.description += f"\n\n{EMOJI_REJECT_TEXT} Розыгрыш завершён без участников."
    return embed


# --- Состав семьи и никнеймы ---

def build_staff_panel_embed(guild: discord.Guild, staff_groups: tuple, emoji_resolver) -> discord.Embed:
    """Состав семьи: каждый участник показывается только в высшей группе."""
    members_by_role: dict[int, list[discord.Member]] = {role_id: [] for _title, role_id, _emoji in staff_groups}
    for member in guild.members:
        if member.bot:
            continue
        member_role_ids = {role.id for role in member.roles}
        # Порядок групп — утверждённая иерархия, а не позиция роли в Discord.
        for _title, role_id, _emoji in staff_groups:
            if role_id in member_role_ids:
                members_by_role[role_id].append(member)
                break

    embed = make_embed(color=COLOR_PANEL)
    for title, role_id, emoji_id in staff_groups:
        members = sorted(members_by_role[role_id], key=lambda member: member.display_name.casefold())
        value = "\n".join(member.mention for member in members) if members else "—"
        embed.add_field(
            name=f"{emoji_resolver(guild, emoji_id, '•')} {title}",
            value=trim_embed_text(value, limit=1024),
            inline=True,
        )
    return embed


def build_nickname_report_embed(bad_members: list[tuple[discord.Member, str]]) -> discord.Embed:
    if not bad_members:
        embed = make_embed(
            title=f"{EMOJI_ACCEPT_TEXT} Проверка никнеймов",
            description="Все проверенные участники стоят по форме. Красота, порядок, дышим ровно.",
            color=COLOR_SOFT,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"ASIXEZ • {format_log_time_msk()}")
        return embed

    lines = [f"- {member.mention} → `{prefix} | Имя РЛ | Static-ID`" for member, prefix in bad_members[:35]]
    if len(bad_members) > 35:
        lines.append(f"...и ещё {len(bad_members) - 35} участник(ов).")
    embed = make_embed(
        title=f"{EMOJI_REVIEW_TEXT} Проверка никнеймов",
        description=(
            "Ниже участники, у которых ник не по форме.\n"
            "Пожалуйста, поставьте ник по указанному шаблону. Если не получается, нажмите кнопку ниже, бот поможет сам.\n\n"
            + "\n".join(lines)
        ),
        color=COLOR_MUTED,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"ASIXEZ • найдено: {len(bad_members)} • {format_log_time_msk()}")
    return embed


# --- Досье участника (/usi) ---

def build_usi_embed(
    guild: discord.Guild,
    member: discord.Member | None,
    user: discord.abc.User,
    record: dict[str, Any],
    *,
    roles_text: str,
    customization_text: str,
    join_history_text: str,
) -> discord.Embed:
    created_at = getattr(user, "created_at", None)
    joined_at = member.joined_at if member is not None else None
    if joined_at is None and record.get("joinEvents"):
        joined_at = parse_iso(str(record["joinEvents"][-1].get("at")))
    premium_since = member.premium_since if member is not None else None
    nickname = (
        member.display_name
        if member is not None
        else (record.get("lastNickname") or record.get("lastUsername") or user.name)
    )

    embed = make_embed(color=COLOR, timestamp=datetime.now(timezone.utc))
    embed.description = f"{EMOJI_REVIEW_TEXT} {nickname} ({user.name} • {user.id})"
    embed.add_field(
        name="Создан",
        value=f"{format_datetime_msk(created_at)}\n{format_relative_time(created_at)}",
        inline=True,
    )
    embed.add_field(name="Заходы", value=join_history_text, inline=True)
    embed.add_field(
        name="Буст",
        value=(
            f"{format_datetime_msk(premium_since)}\n{format_relative_time(premium_since)}"
            if premium_since is not None
            else "Не бустит сервер."
        ),
        inline=True,
    )
    known_role_count = len([name for name in record.get("knownRoleNames", []) if str(name).strip()])
    embed.add_field(name=f"Роли ({known_role_count})", value=roles_text, inline=False)
    embed.add_field(name="Кастомизация", value=customization_text, inline=False)
    last_join_text = format_datetime_msk(joined_at) if joined_at is not None else "—"
    embed.set_footer(text=f"{guild.name} • ID: {user.id} • Последний вход: {last_join_text}")
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed


# --- Перезапуск ---

def build_restart_status_embed(project_name: str, issues: list[str]) -> discord.Embed:
    embed = make_embed(
        title=f"{EMOJI_ACCEPT_TEXT} {project_name} Bot Restart",
        description=(
            "✅ Бот перезапущен и работает стабильно."
            if not issues
            else f"⚠️ Бот перезапущен, но найдено проблем: **{len(issues)}**. Отчёт ниже в ветке."
        ),
        color=COLOR_SOFT if not issues else COLOR_MUTED,
        timestamp=datetime.now(timezone.utc),
    )
    # Метка сборки сразу отвечает на вопрос «доехал ли деплой».
    embed.set_footer(text=f"Сборка {BUILD_TAG} • {format_log_time_msk()}")
    return embed


def build_restart_report_embed(issues: list[str]) -> discord.Embed:
    embed = make_embed(
        title="🧾 Отчёт после перезапуска",
        description="\n".join(f"• {issue}" for issue in issues[:20]),
        color=COLOR_MUTED,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Проверьте эти пункты, чтобы анти-рейд и заявки работали без задержек.")
    return embed
