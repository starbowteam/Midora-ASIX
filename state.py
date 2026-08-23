"""Единая точка доступа к сохраняемому состоянию бота."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

from config import *
from formatting import format_datetime_msk, parse_iso
from projects import get_project, get_project_panel_key
from storage import BotStorage


storage = BotStorage(
    applications_file=APPLICATIONS_FILE,
    panels_file=PANELS_FILE,
    giveaways_file=GIVEAWAYS_FILE,
    voice_rooms_file=VOICE_ROOMS_FILE,
    member_activity_file=MEMBER_ACTIVITY_FILE,
    legacy_applications_file=LEGACY_APPLICATIONS_FILE,
)
application_store = storage.applications
panel_store = storage.panels
giveaway_store = storage.giveaways
voice_room_store = storage.voice_rooms
member_activity_store = storage.member_activity


def save_applications() -> None:
    storage.schedule_save("applications")


def save_panels() -> None:
    storage.schedule_save("panels")


def save_giveaways() -> None:
    storage.schedule_save("giveaways")


def save_voice_rooms() -> None:
    storage.schedule_save("voice_rooms")


def save_member_activity() -> None:
    storage.schedule_save("member_activity")


def reload_applications() -> None:
    storage.reload("applications")


def reload_giveaways() -> None:
    storage.reload("giveaways")


def reload_voice_rooms() -> None:
    storage.reload("voice_rooms")


def reload_member_activity() -> None:
    storage.reload("member_activity")


# --- Заявки ---

def next_application_id() -> int:
    app_id = int(application_store.get("nextId", 1))
    application_store["nextId"] = app_id + 1
    return app_id


def get_application(app_id: int) -> dict[str, Any] | None:
    return application_store.get("items", {}).get(str(app_id))


def put_application(application: dict[str, Any]) -> None:
    application_store.setdefault("items", {})[str(application["id"])] = application
    save_applications()


def iter_guild_applications(guild_id: int, status: str | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for application in application_store.get("items", {}).values():
        if int(application.get("guildId", 0)) != int(guild_id):
            continue
        if status is not None and application.get("status") != status:
            continue
        result.append(application)
    return result


# --- Розыгрыши ---

def next_giveaway_id() -> int:
    giveaway_id = int(giveaway_store.get("nextId", 1))
    giveaway_store["nextId"] = giveaway_id + 1
    return giveaway_id


# --- Состояние набора заявок ---

def get_application_state(guild_id: int) -> dict[str, Any]:
    record = panel_store.setdefault(
        get_project_panel_key(APPLICATION_STATE_KEY, guild_id),
        {"closedServers": []},
    )
    closed_servers = record.get("closedServers", [])
    if not isinstance(closed_servers, list):
        closed_servers = []
    record["closedServers"] = [str(server) for server in closed_servers if str(server).strip()]
    return record


def get_default_closed_servers(guild_id: int | None) -> list[str]:
    project = get_project(guild_id)
    if project is None:
        return []
    return [
        str(option["key"])
        for option in project.get("application_options", [])
        if not option.get("default_open", True)
    ]


def apply_default_closed_application_servers() -> bool:
    changed = False
    for guild_id in PROJECT_GUILD_IDS:
        default_closed = set(get_default_closed_servers(guild_id))
        if not default_closed:
            continue
        state = get_application_state(guild_id)
        closed_servers = set(state.get("closedServers", []))
        updated = sorted(closed_servers | default_closed)
        if updated == sorted(closed_servers):
            continue
        state["closedServers"] = updated
        changed = True
    if changed:
        save_panels()
    return changed


def is_application_open(server: str, guild_id: int | None) -> bool:
    if guild_id is None:
        return True
    state = get_application_state(int(guild_id))
    return server not in set(state.get("closedServers", []))


def set_application_open(server: str, guild_id: int, is_open: bool) -> None:
    state = get_application_state(guild_id)
    closed_servers = set(state.get("closedServers", []))
    if is_open:
        closed_servers.discard(server)
    else:
        closed_servers.add(server)
    state["closedServers"] = sorted(closed_servers)
    save_panels()


def get_visible_application_options(guild_id: int | None) -> list[dict[str, Any]]:
    project = get_project(guild_id)
    if project is None:
        return []
    return [
        option
        for option in project.get("application_options", [])
        if option.get("visible_in_select", True) and is_application_open(option["key"], guild_id)
    ]


# --- Голосовые комнаты ---

def get_voice_rooms() -> dict[str, dict[str, Any]]:
    rooms = voice_room_store.get("rooms", {})
    if not isinstance(rooms, dict):
        rooms = {}
        voice_room_store["rooms"] = rooms
    return rooms


def get_voice_room(channel_id: int) -> dict[str, Any] | None:
    return get_voice_rooms().get(str(channel_id))


def get_owned_voice_room(owner_id: int, guild_id: int | None = None) -> tuple[int, dict[str, Any]] | None:
    for channel_id, room in get_voice_rooms().items():
        try:
            if int(room.get("ownerId", 0)) != owner_id:
                continue
            if guild_id is not None and int(room.get("guildId", 0)) != guild_id:
                continue
            return int(channel_id), room
        except (TypeError, ValueError):
            continue
    return None


def set_voice_room(channel_id: int, payload: dict[str, Any]) -> None:
    get_voice_rooms()[str(channel_id)] = payload
    save_voice_rooms()


def remove_voice_room(channel_id: int) -> None:
    get_voice_rooms().pop(str(channel_id), None)
    save_voice_rooms()


# --- Активность участников ---

def get_guild_activity_store(guild_id: int) -> dict[str, Any]:
    guilds = member_activity_store.setdefault("guilds", {})
    guild_store = guilds.setdefault(str(guild_id), {"members": {}})
    guild_store.setdefault("members", {})
    return guild_store


def get_member_activity_record(guild_id: int, user_id: int) -> dict[str, Any]:
    guild_store = get_guild_activity_store(guild_id)
    members = guild_store.setdefault("members", {})
    record = members.setdefault(
        str(user_id),
        {
            "joinEvents": [],
            "leaveEvents": [],
            "knownRoleNames": [],
            "lastNickname": "",
            "lastUsername": "",
            "lastGlobalName": "",
            "boostEvents": [],
            "lastSeenAt": "",
            "lastAvatarUrl": "",
            "lastBannerUrl": "",
            "lastGuildAvatarUrl": "",
            "lastAvatarDecoration": "",
            "lastAccentColor": "",
        },
    )
    for key in ("joinEvents", "leaveEvents", "knownRoleNames", "boostEvents"):
        record.setdefault(key, [])
    for key in (
        "lastAvatarUrl",
        "lastBannerUrl",
        "lastGuildAvatarUrl",
        "lastAvatarDecoration",
        "lastAccentColor",
    ):
        record.setdefault(key, "")
    return record


def merge_known_role_names(record: dict[str, Any], role_names: list[str]) -> None:
    existing = {str(name) for name in record.get("knownRoleNames", []) if str(name).strip()}
    for role_name in role_names:
        cleaned = str(role_name).strip()
        if cleaned:
            existing.add(cleaned)
    record["knownRoleNames"] = sorted(existing, key=str.casefold)


def get_member_role_names(member: discord.Member) -> list[str]:
    return [role.name for role in member.roles if role != member.guild.default_role]


def update_member_activity_profile(member: discord.Member) -> None:
    record = get_member_activity_record(member.guild.id, member.id)
    record["lastNickname"] = member.display_name
    record["lastUsername"] = member.name
    record["lastGlobalName"] = member.global_name or ""
    record["lastSeenAt"] = datetime.now(timezone.utc).isoformat()
    record["lastAvatarUrl"] = str(member.display_avatar.url)
    guild_avatar = getattr(member, "guild_avatar", None)
    record["lastGuildAvatarUrl"] = str(guild_avatar.url) if guild_avatar is not None else ""
    banner = getattr(member, "banner", None)
    record["lastBannerUrl"] = str(banner.url) if banner is not None else ""
    avatar_decoration = getattr(member, "avatar_decoration", None)
    decoration_url = getattr(avatar_decoration, "url", None) if avatar_decoration is not None else None
    record["lastAvatarDecoration"] = str(decoration_url or getattr(member, "avatar_decoration_sku_id", "") or "")
    record["lastAccentColor"] = str(getattr(member, "accent_color", "") or "")
    merge_known_role_names(record, get_member_role_names(member))
    if member.premium_since is not None:
        premium_since_iso = member.premium_since.astimezone(timezone.utc).isoformat()
        boost_events = [str(item) for item in record.get("boostEvents", [])]
        if premium_since_iso not in boost_events:
            boost_events.append(premium_since_iso)
            record["boostEvents"] = boost_events


def record_member_join_activity(member: discord.Member) -> None:
    update_member_activity_profile(member)
    record = get_member_activity_record(member.guild.id, member.id)
    join_events = record.get("joinEvents", [])
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    if not join_events or abs((parse_iso(str(join_events[-1].get("at", ""))) - now).total_seconds()) > 5:
        join_events.append({"at": now_iso, "reason": "join"})
    record["joinEvents"] = join_events[-50:]
    save_member_activity()


def record_member_leave_activity(
    guild_id: int,
    user_id: int,
    *,
    reason: str,
    actor_id: int | None = None,
    audit_reason: str | None = None,
    role_names: list[str] | None = None,
    nickname: str = "",
    username: str = "",
    global_name: str = "",
) -> None:
    record = get_member_activity_record(guild_id, user_id)
    if role_names:
        merge_known_role_names(record, role_names)
    if nickname:
        record["lastNickname"] = nickname
    if username:
        record["lastUsername"] = username
    if global_name:
        record["lastGlobalName"] = global_name
    leave_events = record.get("leaveEvents", [])
    now = datetime.now(timezone.utc)
    payload = {
        "at": now.isoformat(),
        "reason": reason,
        "actorId": int(actor_id or 0),
        "auditReason": audit_reason or "",
    }
    if leave_events:
        last_event = leave_events[-1]
        last_at = parse_iso(str(last_event.get("at", "")))
        if (
            str(last_event.get("reason", "")) == reason
            and abs((now - last_at.astimezone(timezone.utc)).total_seconds()) <= 5
        ):
            leave_events[-1] = payload
            record["leaveEvents"] = leave_events[-50:]
            save_member_activity()
            return
    leave_events.append(payload)
    record["leaveEvents"] = leave_events[-50:]
    save_member_activity()


def format_member_event_line(event: dict[str, Any]) -> str:
    event_at = parse_iso(str(event.get("at", "")))
    reason = str(event.get("reason", "left"))
    reason_map = {
        "join": "вступил",
        "left": "вышел",
        "kick": "кикнут",
        "ban": "забанен",
    }
    actor_id = int(event.get("actorId", 0) or 0)
    audit_reason = str(event.get("auditReason", "")).strip()
    line = f"{format_datetime_msk(event_at)} — {reason_map.get(reason, reason)}"
    if actor_id:
        line += f" (<@{actor_id}>)"
    if audit_reason:
        line += f" — {audit_reason}"
    return line
