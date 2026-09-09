"""Единственный источник правды по ID, баннерам, цветам и настройкам проектов."""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "env")
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
APPLICATIONS_FILE = DATA_DIR / "famq_applications.json"
PANELS_FILE = DATA_DIR / "panels.json"
GIVEAWAYS_FILE = DATA_DIR / "giveaways.json"
VOICE_ROOMS_FILE = DATA_DIR / "voice_rooms.json"
MEMBER_ACTIVITY_FILE = DATA_DIR / "member_activity.json"
LEGACY_APPLICATIONS_FILE = BASE_DIR.parent / "data" / "famq_applications.json"


def env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        return int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a numeric Discord ID") from error


def env_int_list(name: str, default: str) -> list[int]:
    values = os.getenv(name, default).split(",")
    try:
        return [int(value.strip()) for value in values if value.strip()]
    except ValueError as error:
        raise RuntimeError(f"{name} must contain comma-separated numeric Discord IDs") from error


# --- Основные ID серверов и каналов ---
FAMQ_GUILD_ID = env_int("FAMQ_GUILD_ID", 1466147160763666472)
FAMQ_PANEL_CHANNEL_ID = env_int("FAMQ_PANEL_CHANNEL_ID", 1466147735873786200)
FAMQ_LOG_CHANNEL_ID = env_int("FAMQ_LOG_CHANNEL_ID", 1466322324184895510)
FAMQ_RESULTS_CHANNEL_ID = env_int("FAMQ_RESULTS_CHANNEL_ID", 1494984412637429881)
FAMQ_DM_FALLBACK_CHANNEL_ID = env_int("FAMQ_DM_FALLBACK_CHANNEL_ID", 1488477593589125171)
FAMQ_INTERVIEW_CHANNEL_IDS = env_int_list("FAMQ_INTERVIEW_CHANNEL_IDS", "1488482479227797615,1488482493073068153")
FAMQ_WAITING_CHANNEL_ID = env_int("FAMQ_WAITING_CHANNEL_ID", 1494985163178770432)
FAMQ_INFO_CHANNEL_ID = env_int("FAMQ_INFO_CHANNEL_ID", 1466307109296668868)
# Категория, в которой создаются текстовые каналы заявок.
FAMQ_APPLICATION_CATEGORY_ID = env_int("FAMQ_APPLICATION_CATEGORY_ID", 1540665998057676840)
FAMQ_WELCOME_CHANNEL_ID = env_int("FAMQ_WELCOME_CHANNEL_ID", 1504058190298353795)
FAMQ_ACTIVITY_LOG_CHANNEL_ID = env_int("FAMQ_ACTIVITY_LOG_CHANNEL_ID", 1466322279440060572)
FAMQ_STAFF_PANEL_CHANNEL_ID = env_int("FAMQ_STAFF_PANEL_CHANNEL_ID", 1533085586880200744)
FAMQ_NICKNAME_REPORT_CHANNEL_ID = env_int("FAMQ_NICKNAME_REPORT_CHANNEL_ID", 1533219952591376384)
# Канал с Components V2-карточкой «Информация о семье».
FAMQ_FAMILY_INFO_CHANNEL_ID = env_int("FAMQ_FAMILY_INFO_CHANNEL_ID", 1540664873925746810)
FAMQ_MEDIA_CHANNEL_ID = env_int("FAMQ_MEDIA_CHANNEL_ID", 1466319626181939291)
GIVEAWAY_CHANNEL_ID = env_int("FAMQ_GIVEAWAY_CHANNEL_ID", 1494988017016897718)
VOICE_PANEL_CHANNEL_ID = env_int("FAMQ_VOICE_PANEL_CHANNEL_ID", 1495067186237145188)
VOICE_TRIGGER_CHANNEL_ID = env_int("FAMQ_VOICE_TRIGGER_CHANNEL_ID", 1495067302880608306)

# --- Роли ---
FAMQ_RECRUITER_ROLE_ID = env_int("FAMQ_RECRUITER_ROLE_ID", 1485562379512315996)
GLOBAL_APPLICATION_PING_ROLE_ID = env_int("GLOBAL_APPLICATION_PING_ROLE_ID", 1485562379512315996)
APPLICATION_EXTRA_ACCEPT_ROLE_ID = env_int("APPLICATION_EXTRA_ACCEPT_ROLE_ID", 1508315652849401968)
APPLICATION_ACADEMY_ROLE_ID = env_int("APPLICATION_ACADEMY_ROLE_ID", 1508316765409775626)
APPLICATION_CONTROL_ROLE_ID = env_int("APPLICATION_CONTROL_ROLE_ID", 1467068877572800720)
FAMQ_DETROIT_TAG_ROLE_ID = env_int("FAMQ_DETROIT_TAG_ROLE_ID", 1486284234145665114)
FAMQ_ACCEPT_ROLE_ID = env_int("FAMQ_ACCEPT_ROLE_ID", 1485370909148844154)
FAMQ_DENVER_RECRUITER_ROLE_ID = env_int("FAMQ_DENVER_RECRUITER_ROLE_ID", 1485562379512315996)
FAMQ_DENVER_ACCEPT_ROLE_ID = env_int("FAMQ_DENVER_ACCEPT_ROLE_ID", 0)
FAMQ_SF_ROLE_1_ID = env_int("FAMQ_SF_ROLE_1_ID", 1486284234145665114)
FAMQ_SF_ROLE_2_ID = env_int("FAMQ_SF_ROLE_2_ID", 1491839604402294814)
FAMQ_SF_ACCEPT_ROLE_ID = env_int("FAMQ_SF_ACCEPT_ROLE_ID", 1493801356631347300)
FAMQ_ORLANDO_ROLE_ID = env_int("FAMQ_ORLANDO_ROLE_ID", 1496179273273311232)
FAMQ_ORLANDO_ACCEPT_ROLE_ID = env_int("FAMQ_ORLANDO_ACCEPT_ROLE_ID", 1496180200277409934)
FAMQ_FRIEND_VERIFY_ROLE_1_ID = env_int("FAMQ_FRIEND_VERIFY_ROLE_1_ID", 1467068877572800720)
FAMQ_FRIEND_VERIFY_ROLE_2_ID = env_int("FAMQ_FRIEND_VERIFY_ROLE_2_ID", 1501994072364027954)
FAMQ_FRIEND_VERIFY_ACCEPT_ROLE_ID = env_int("FAMQ_FRIEND_VERIFY_ACCEPT_ROLE_ID", 1503996404828213258)
FAMQ_DEP_LEADER_ROLE_ID = env_int("FAMQ_DEP_LEADER_ROLE_ID", 1501994072364027954)
FAMQ_CURATOR_ROLE_ID = env_int("FAMQ_CURATOR_ROLE_ID", 1488568512489459752)
FAMQ_BOSS_ROLE_ID = env_int("FAMQ_BOSS_ROLE_ID", 1466148246010335232)
FAMQ_HIGH_ROLE_ID = env_int("FAMQ_HIGH_ROLE_ID", 1485371224593797150)

# --- Новые роли для рекрутеров (Chief Recruit и Dep Chief Recruit) ---
CHIEF_RECRUIT_ROLE_ID = env_int("CHIEF_RECRUIT_ROLE_ID", 1537855558101504071)
DEP_CHIEF_RECRUIT_ROLE_ID = env_int("DEP_CHIEF_RECRUIT_ROLE_ID", 1537856131597082734)

# Роль, которой разрешена команда /restart.
RESTART_COMMAND_ROLE_ID = env_int("FAMQ_RESTART_COMMAND_ROLE_ID", 1534265635813589122)

# --- FEDRU ---
FEDRU_GUILD_ID = env_int("FEDRU_GUILD_ID", 1495718321340026942)
FEDRU_INFO_CHANNEL_ID = env_int("FEDRU_INFO_CHANNEL_ID", 1495718322484940903)
FEDRU_PANEL_CHANNEL_ID = env_int("FEDRU_PANEL_CHANNEL_ID", 1495718322929406064)
FEDRU_RESULTS_CHANNEL_ID = env_int("FEDRU_RESULTS_CHANNEL_ID", 1495718322929406065)
FEDRU_VOICE_PANEL_CHANNEL_ID = env_int("FEDRU_VOICE_PANEL_CHANNEL_ID", 1495718322929406071)
FEDRU_VOICE_TRIGGER_CHANNEL_ID = env_int("FEDRU_VOICE_TRIGGER_CHANNEL_ID", 1495718322929406072)
FEDRU_RESTART_STATUS_CHANNEL_ID = env_int("FEDRU_RESTART_STATUS_CHANNEL_ID", 1495718325391589390)
FEDRU_APPLICATION_LOG_CHANNEL_ID = env_int("FEDRU_APPLICATION_LOG_CHANNEL_ID", 1495718325391589388)
FEDRU_SECURITY_LOG_CHANNEL_ID = env_int("FEDRU_SECURITY_LOG_CHANNEL_ID", 1495718325391589389)
FEDRU_APPLICATION_CATEGORY_ID = env_int("FEDRU_APPLICATION_CATEGORY_ID", 1495718322484940908)
FEDRU_RECRUITER_ROLE_ID = env_int("FEDRU_RECRUITER_ROLE_ID", 1495718321557999645)

# --- Настройки бота ---
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("FAMQ_BOT_TOKEN")
WHITELIST_CONTACT_ID = os.getenv("FAMQ_WHITELIST_CONTACT_ID", "asixez").strip() or "asixez"
APPLICATION_ANNOUNCE_CHANNEL_ID = env_int("APPLICATION_ANNOUNCE_CHANNEL_ID", 1466147547193278568)
RESTART_STATUS_CHANNEL_ID = env_int("FAMQ_RESTART_STATUS_CHANNEL_ID", 1494333628489011220)
LOG_CHANNEL_ID = env_int("FAMQ_SECURITY_LOG_CHANNEL_ID", 1495406454985724035)
ALERT_ROLE_ID = env_int("FAMQ_SECURITY_ALERT_ROLE_ID", 1467068877572800720)
JOIN_THRESHOLD_WARNING = env_int("FAMQ_JOIN_THRESHOLD_WARNING", 5)
JOIN_THRESHOLD_ALERT = env_int("FAMQ_JOIN_THRESHOLD_ALERT", 10)
ANTI_NUKE_LIMITS = {
    "channel_delete": env_int("FAMQ_ANTI_NUKE_CHANNEL_DELETE_LIMIT", 2),
    "role_delete": env_int("FAMQ_ANTI_NUKE_ROLE_DELETE_LIMIT", 2),
    "ban": env_int("FAMQ_ANTI_NUKE_BAN_LIMIT", 3),
    "kick": env_int("FAMQ_ANTI_NUKE_KICK_LIMIT", 5),
}
SPAM_LIMIT = env_int("FAMQ_SPAM_LIMIT", 5)
MSK_TZ = timezone(timedelta(hours=3), name="MSK")
RESTART_HOURS_MSK = (8, 20)

# --- Баннеры ---
# Единственный фирменный баннер семьи. Используется точечно: карточка панели заявок,
# карточка самой заявки, личные сообщения по заявкам и панель голосовых комнат.
FAMILY_BRAND_BANNER_URL = "https://cdn.discordapp.com/banners/1466147160763666472/6dd2eab1023614a0ecb833de6bda25b3.png?size=1024"
PANEL_BANNER_URL = FAMILY_BRAND_BANNER_URL

# Картинка welcome/информации лежит рядом с кодом: подписанные ссылки Discord CDN
# (`?ex=...&hm=...`) протухают примерно через сутки, поэтому файл отправляется вложением.
WELCOME_BANNER_FILENAME = "welcome_banner.jpg"
WELCOME_BANNER_PATH = ASSETS_DIR / WELCOME_BANNER_FILENAME
WELCOME_BANNER_ATTACHMENT_URL = f"attachment://{WELCOME_BANNER_FILENAME}"

# Квадратная картинка в углу сообщений о входе и выходе.
WELCOME_ICON_FILENAME = "welcome_icon.jpg"
WELCOME_ICON_PATH = ASSETS_DIR / WELCOME_ICON_FILENAME
WELCOME_ICON_ATTACHMENT_URL = f"attachment://{WELCOME_ICON_FILENAME}"

# --- Константы серверов ---
FAMQ_SERVER_DETROIT = "detroit"
FAMQ_SERVER_DENVER = "denver"
# Кастомный эмодзи Denver с сервера семьи. Его имя берётся из кэша сервера при публикации.
DENVER_EMOJI_ID = 1518856005113610370
FAMQ_SERVER_SF = "sf"
FAMQ_SERVER_ORLANDO = "orlando"
FAMQ_SERVER_FRIEND_VERIFICATION = "friend_verification"
FEDRU_APPLICATION_SERVER = "federal_ru"

# --- Ключи панелей ---
PANEL_KEY = "famq_panel"
INFO_PANEL_KEY = "famq_info_panel"
FAMILY_INFO_PANEL_KEY = "famq_family_info_panel"
VOICE_PANEL_KEY = "famq_voice_panel"
APPLICATION_STATE_KEY = "famq_application_state"
PANEL_SELECT_ID = "famq_server_select"
BTN_REVIEW_PREFIX = "famq_review_"
BTN_ACCEPT_PREFIX = "famq_accept_"
BTN_REJECT_PREFIX = "famq_reject_"
BTN_CALL_PREFIX = "famq_call_"
SELECT_CALL_PREFIX = "famq_callsel_"

# --- Цвета ---
# Палитра осознанно монохромная, но заметно светлее прежней «почти чёрной»:
# на тёмной теме Discord полоса эмбеда теперь читается, а не сливается с фоном.
COLOR = 0x2B2D31
COLOR_SOFT = 0x3F4248
COLOR_MUTED = 0x53575F
COLOR_PANEL = 0x1E1F22
# Логи: основной канал логов и лог заявок.
COLOR_LOG = 0x4A4E57
COLOR_LOG_APPLICATION = 0x41454D

# --- Метка сборки ---
# Показывается в сообщении о перезапуске. Нужна, чтобы одним взглядом понять,
# какой код реально крутится на хосте, а не гадать, доехал ли деплой.
# Обновляется при каждом изменении кода.
BUILD_TAG = "2026-09-09.1"

# --- Список гильдий ---
PROJECT_GUILD_IDS = (FAMQ_GUILD_ID, FEDRU_GUILD_ID)
GUILD_SCOPES = [discord.Object(id=guild_id) for guild_id in PROJECT_GUILD_IDS]
USI_ALLOWED_ROLE_IDS = [1487148388549132462, 1467068877572800720, 1485562379512315996, 1488568512489459752, 1466148166368760053]

# --- Эмодзи (текст) ---
EMOJI_DETROIT_TEXT = "<:detroit:1495013594587332668>"
EMOJI_SF_TEXT = "<:san_francisco:1495013800426868878>"
EMOJI_ORLANDO_TEXT = "<:orlando:1496182029895860426>"
EMOJI_REVIEW_TEXT = "<a:1468592290141307137:1495013592200777838>"
EMOJI_ACCEPT_TEXT = "<a:1468592320512528415:1495013589583401083>"
EMOJI_CALL_TEXT = "<a:1468592240099070015:1495013587373002782>"
EMOJI_REJECT_TEXT = "<a:1468592303940829214:1495013583451324487>"
EMOJI_FRIEND_TEXT = "🤝"

# --- ID кастомных эмодзи ---
# Имя эмодзи берётся из Discord во время работы бота: это исключает отображение
# кода вида <:название:id> вместо самого эмодзи.
EMOJI_STAFF_LEADERS_ID = 1534826361913610424
EMOJI_STAFF_DEP_LEADERS_ID = 1509145222691553351
EMOJI_STAFF_CHIEF_RECRUIT_ID = 1466306802927931515
EMOJI_STAFF_CURATORS_ID = 1509145530276646933
EMOJI_STAFF_DEP_CHIEF_RECRUIT_ID = 1468591891166793865
EMOJI_STAFF_BOSS_ID = 1509145398537883760
EMOJI_STAFF_HIGH_ID = 1466431600559394879
EMOJI_STAFF_RECRUITS_ID = 1467062953647014043
EMOJI_APPLICATION_INTRO_ID = 1534826371107262574
EMOJI_WELCOME_TITLE_ID = 1509145548270075954
EMOJI_WELCOME_LINE_ID = 1468592121203392643

# --- Эмодзи (объекты) ---
EMOJI_DETROIT = discord.PartialEmoji(name="detroit", id=1495013594587332668)
EMOJI_SF = discord.PartialEmoji(name="san_francisco", id=1495013800426868878)
EMOJI_ORLANDO = discord.PartialEmoji(name="orlando", id=1495013800426868878)
EMOJI_REVIEW = discord.PartialEmoji(name="review", id=1495013592200777838, animated=True)
EMOJI_ACCEPT = discord.PartialEmoji(name="accept", id=1495013589583401083, animated=True)
EMOJI_CALL = discord.PartialEmoji(name="call", id=1495013587373002782, animated=True)
EMOJI_REJECT = discord.PartialEmoji(name="reject", id=1495013583451324487, animated=True)

# --- Голосовые эмодзи (текст) ---
VOICE_EMOJI_ADD_SLOT_TEXT = "<:slot_add:1495066734518861834>"
VOICE_EMOJI_REMOVE_SLOT_TEXT = "<:slot_remove:1495066731880648794>"
VOICE_EMOJI_LOCK_TEXT = "<:room_lock:1495066729875902524>"
VOICE_EMOJI_SPEAK_TEXT = "<:voice_toggle:1495066727858311289>"
VOICE_EMOJI_KICK_TEXT = "<:kick_user:1495066725379739799>"
VOICE_EMOJI_BITRATE_TEXT = "<:bitrate:1495066723332784341>"
VOICE_EMOJI_SET_SLOTS_TEXT = "<:set_slots:1495066720665342092>"
VOICE_EMOJI_TRANSFER_TEXT = "<:transfer_owner:1495066718370922768>"
VOICE_EMOJI_RENAME_TEXT = "<:rename_room:1495066715728642129>"
VOICE_EMOJI_ACCESS_TEXT = "<:room_access:1495066712679125095>"

# --- Голосовые эмодзи (объекты) ---
VOICE_EMOJI_ADD_SLOT = discord.PartialEmoji(name="slot_add", id=1495066734518861834)
VOICE_EMOJI_REMOVE_SLOT = discord.PartialEmoji(name="slot_remove", id=1495066731880648794)
VOICE_EMOJI_LOCK = discord.PartialEmoji(name="room_lock", id=1495066729875902524)
VOICE_EMOJI_SPEAK = discord.PartialEmoji(name="voice_toggle", id=1495066727858311289)
VOICE_EMOJI_KICK = discord.PartialEmoji(name="kick_user", id=1495066725379739799)
VOICE_EMOJI_BITRATE = discord.PartialEmoji(name="bitrate", id=1495066723332784341)
VOICE_EMOJI_SET_SLOTS = discord.PartialEmoji(name="set_slots", id=1495066720665342092)
VOICE_EMOJI_TRANSFER = discord.PartialEmoji(name="transfer_owner", id=1495066718370922768)
VOICE_EMOJI_RENAME = discord.PartialEmoji(name="rename_room", id=1495066715728642129)
VOICE_EMOJI_ACCESS = discord.PartialEmoji(name="room_access", id=1495066712679125095)

# --- Список ролей для пинга в заявках (глобальные наблюдатели) ---
GLOBAL_APPLICATION_PING_ROLE_IDS = [
    APPLICATION_CONTROL_ROLE_ID,
    GLOBAL_APPLICATION_PING_ROLE_ID,
    CHIEF_RECRUIT_ROLE_ID,
    DEP_CHIEF_RECRUIT_ROLE_ID,
]

# --- Конфигурации проектов ---
PROJECT_CONFIGS: dict[int, dict[str, Any]] = {
    FAMQ_GUILD_ID: {
        "project_name": "ASIXEZ",
        "application_name": "ASIXEZ",
        "panel_mode": "single_server",
        "panel_channel_id": FAMQ_PANEL_CHANNEL_ID,
        "info_channel_id": FAMQ_INFO_CHANNEL_ID,
        "family_info_channel_id": FAMQ_FAMILY_INFO_CHANNEL_ID,
        "media_channel_id": FAMQ_MEDIA_CHANNEL_ID,
        "results_channel_id": FAMQ_RESULTS_CHANNEL_ID,
        "voice_panel_channel_id": VOICE_PANEL_CHANNEL_ID,
        "voice_trigger_channel_id": VOICE_TRIGGER_CHANNEL_ID,
        "restart_status_channel_id": RESTART_STATUS_CHANNEL_ID,
        "application_log_channel_id": FAMQ_LOG_CHANNEL_ID,
        "security_log_channel_id": LOG_CHANNEL_ID,
        "activity_log_channel_id": FAMQ_ACTIVITY_LOG_CHANNEL_ID,
        "welcome_channel_id": FAMQ_WELCOME_CHANNEL_ID,
        "alert_role_id": ALERT_ROLE_ID,
        "application_category_id": FAMQ_APPLICATION_CATEGORY_ID,
        "dm_fallback_channel_id": FAMQ_DM_FALLBACK_CHANNEL_ID,
        "interview_channel_ids": list(FAMQ_INTERVIEW_CHANNEL_IDS),
        "waiting_channel_id": FAMQ_WAITING_CHANNEL_ID,
        "promo_code": "ASIX",
        "promo_register_url": "https://majestic-rp.ru/register?utm_campaign=ASIX",
        # В панели семьи доступна только заявка на Denver.
        "application_options": [
            {
                "key": FAMQ_SERVER_DENVER,
                "label": "Denver",
                "emoji": "🏔️",
                "emoji_text": "🏔️",
                "recruiter_roles": [
                    FAMQ_DENVER_RECRUITER_ROLE_ID,
                    CHIEF_RECRUIT_ROLE_ID,
                    DEP_CHIEF_RECRUIT_ROLE_ID,
                ],
                "manager_roles": [
                    APPLICATION_CONTROL_ROLE_ID,
                    FAMQ_DENVER_RECRUITER_ROLE_ID,
                    CHIEF_RECRUIT_ROLE_ID,
                    DEP_CHIEF_RECRUIT_ROLE_ID,
                ],
                "accept_role_id": FAMQ_ACCEPT_ROLE_ID,
                "default_open": True,
            }
        ],
    },
    FEDRU_GUILD_ID: {
        "project_name": "ASIXEZ RU",
        "application_name": "ASIXEZ RU",
        "panel_mode": "single_server",
        "panel_channel_id": FEDRU_PANEL_CHANNEL_ID,
        "info_channel_id": FEDRU_INFO_CHANNEL_ID,
        "family_info_channel_id": None,
        "media_channel_id": None,
        "results_channel_id": FEDRU_RESULTS_CHANNEL_ID,
        "voice_panel_channel_id": FEDRU_VOICE_PANEL_CHANNEL_ID,
        "voice_trigger_channel_id": FEDRU_VOICE_TRIGGER_CHANNEL_ID,
        "restart_status_channel_id": FEDRU_RESTART_STATUS_CHANNEL_ID,
        "application_log_channel_id": FEDRU_APPLICATION_LOG_CHANNEL_ID,
        "security_log_channel_id": FEDRU_SECURITY_LOG_CHANNEL_ID,
        "activity_log_channel_id": None,
        "welcome_channel_id": None,
        "alert_role_id": FEDRU_RECRUITER_ROLE_ID,
        "application_category_id": FEDRU_APPLICATION_CATEGORY_ID,
        "dm_fallback_channel_id": None,
        "interview_channel_ids": [],
        "waiting_channel_id": None,
        "promo_code": "ASIX",
        "promo_register_url": "https://majestic-rp.ru/register?utm_campaign=ASIX",
        "application_options": [
            {
                "key": FEDRU_APPLICATION_SERVER,
                "label": "ASIXEZ RU",
                "emoji": EMOJI_ACCEPT,
                "emoji_text": EMOJI_ACCEPT_TEXT,
                "recruiter_roles": [FEDRU_RECRUITER_ROLE_ID],
                "accept_role_id": None,
            }
        ],
    },
}


__all__ = [name for name in globals() if name.isupper()]
