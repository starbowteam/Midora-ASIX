"""Доступ к конфигурации проектов, названиям серверов и ролям заявок."""

from __future__ import annotations

from typing import Any

import discord

from config import *


def get_project(guild_or_id: discord.Guild | int | None) -> dict[str, Any] | None:
    guild_id = guild_or_id.id if isinstance(guild_or_id, discord.Guild) else guild_or_id
    if guild_id is None:
        return None
    return PROJECT_CONFIGS.get(int(guild_id))


def get_project_name(guild_id: int | None) -> str:
    project = get_project(guild_id)
    return str(project.get("project_name")) if project else "ASIXEZ"


def is_allowed_guild_id(guild_id: int | None) -> bool:
    return guild_id is not None and int(guild_id) in PROJECT_CONFIGS


def is_famq_activity_guild(guild_id: int) -> bool:
    return guild_id == FAMQ_GUILD_ID


def get_project_panel_key(base_key: str, guild_id: int) -> str:
    return f"{base_key}:{guild_id}"


def get_project_channel_id(guild_id: int | None, key: str) -> int | None:
    project = get_project(guild_id)
    if project is None:
        return None
    raw_value = project.get(key)
    return int(raw_value) if raw_value else None


# --- Названия серверов ---

def get_server_label(server: str) -> str:
    if server == FAMQ_SERVER_FRIEND_VERIFICATION:
        return f"{EMOJI_FRIEND_TEXT} Верификация для друзей"
    if server == FEDRU_APPLICATION_SERVER:
        return f"{EMOJI_ACCEPT_TEXT} ASIXEZ RU"
    if server == FAMQ_SERVER_DENVER:
        return "🏔️ Denver"
    if server == FAMQ_SERVER_ORLANDO:
        return f"{EMOJI_ORLANDO_TEXT} Orlando"
    if server == FAMQ_SERVER_SF:
        return f"{EMOJI_SF_TEXT} San Francisco"
    return f"{EMOJI_DETROIT_TEXT} Detroit"


def get_server_plain_label(server: str) -> str:
    if server == FAMQ_SERVER_FRIEND_VERIFICATION:
        return "Верификация для друзей"
    if server == FEDRU_APPLICATION_SERVER:
        return "ASIXEZ RU"
    if server == FAMQ_SERVER_DENVER:
        return "Denver"
    if server == FAMQ_SERVER_ORLANDO:
        return "Orlando"
    return "San Francisco" if server == FAMQ_SERVER_SF else "Detroit"


def get_server_tag(server: str) -> str:
    if server == FAMQ_SERVER_FRIEND_VERIFICATION:
        return "friend"
    if server == FEDRU_APPLICATION_SERVER:
        return "ru"
    if server == FAMQ_SERVER_DENVER:
        return "den"
    if server == FAMQ_SERVER_ORLANDO:
        return "orl"
    return "sf" if server == FAMQ_SERVER_SF else "det"


def is_friend_verification_application(server: str) -> bool:
    return server == FAMQ_SERVER_FRIEND_VERIFICATION


# --- Опции заявок и роли ---

def get_project_application_option(server: str, guild_id: int | None = None) -> dict[str, Any] | None:
    if guild_id is not None:
        project = get_project(guild_id)
        if project is not None:
            for option in project.get("application_options", []):
                if option["key"] == server:
                    return option
    for project in PROJECT_CONFIGS.values():
        for option in project.get("application_options", []):
            if option["key"] == server:
                return option
    return None


def get_manageable_application_options(guild_id: int | None) -> list[dict[str, Any]]:
    project = get_project(guild_id)
    if project is None:
        return []
    return [
        option
        for option in project.get("application_options", [])
        if option["key"] != FAMQ_SERVER_FRIEND_VERIFICATION
    ]


def get_server_recruiter_roles(server: str, guild_id: int | None = None) -> list[int]:
    option = get_project_application_option(server, guild_id)
    if option is None:
        return []
    return list(option.get("recruiter_roles", []))


def get_server_accept_role_id(server: str, guild_id: int | None = None) -> int | None:
    option = get_project_application_option(server, guild_id)
    if option is None:
        return None
    return option.get("accept_role_id")


def get_server_manager_roles(server: str, guild_id: int | None = None) -> list[int]:
    option = get_project_application_option(server, guild_id)
    if option is None:
        return [APPLICATION_CONTROL_ROLE_ID]
    manager_roles = option.get("manager_roles")
    if isinstance(manager_roles, list) and manager_roles:
        return [int(role_id) for role_id in manager_roles if int(role_id)]
    return [APPLICATION_CONTROL_ROLE_ID]


def get_application_control_roles(guild_id: int | None) -> list[int]:
    role_ids = {APPLICATION_CONTROL_ROLE_ID}
    for option in get_manageable_application_options(guild_id):
        role_ids.update(int(role_id) for role_id in option.get("manager_roles", []) if int(role_id))
    return sorted(role_ids)


def member_has_any_role(member: discord.Member | None, role_ids: list[int]) -> bool:
    if member is None:
        return False
    wanted = {int(role_id) for role_id in role_ids if role_id}
    return any(role.id in wanted for role in getattr(member, "roles", []))


def can_manage_application(member: discord.Member | None, server: str, guild_id: int | None = None) -> bool:
    if member is None:
        return False
    return member_has_any_role(member, get_server_manager_roles(server, guild_id or member.guild.id))


def has_application_control_access(
    member: discord.Member | None,
    server: str | None = None,
    guild_id: int | None = None,
) -> bool:
    if member is None:
        return False
    role_ids = (
        get_server_manager_roles(server, guild_id or member.guild.id)
        if server is not None
        else get_application_control_roles(guild_id or member.guild.id)
    )
    return member_has_any_role(member, role_ids)


# --- Эмодзи сервера ---

def get_guild_emoji_text(guild: discord.Guild | None, emoji_id: int, fallback: str = "•") -> str:
    """Возвращает настоящий кастомный эмодзи с сервера по его ID, а не текстовый код."""
    emoji = guild.get_emoji(int(emoji_id)) if guild is not None else None
    return str(emoji) if emoji is not None else fallback


def get_custom_emoji_markup(guild: discord.Guild | None, emoji_id: int, fallback_name: str) -> str:
    """Возвращает markup кастомного Discord emoji, даже если он ещё не попал в кэш."""
    emoji = guild.get_emoji(int(emoji_id)) if guild is not None else None
    return str(emoji) if emoji is not None else f"<:{fallback_name}:{int(emoji_id)}>"
