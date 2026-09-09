from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from applications import (  # noqa: E402
    build_application_channel_name,
    build_asx_member_nickname,
    build_formatted_nickname,
    extract_nickname_and_static,
    sanitize_channel_name_component,
)
from storage import BotStorage  # noqa: E402


def make_storage(directory: Path) -> BotStorage:
    return BotStorage(
        applications_file=directory / "applications.json",
        panels_file=directory / "panels.json",
        giveaways_file=directory / "giveaways.json",
        voice_rooms_file=directory / "voice.json",
        member_activity_file=directory / "activity.json",
        legacy_applications_file=directory / "legacy.json",
    )


class ApplicationLogicTests(unittest.TestCase):
    def test_nickname_uses_irl_name_and_static_id(self) -> None:
        nickname = build_asx_member_nickname({"irlName": "Александр Иванов", "nameStatic": "Player 7654321"})
        self.assertEqual(nickname, "ASX | Александр Иванов | 7654321")

    def test_nickname_never_exceeds_discord_limit(self) -> None:
        nickname = build_asx_member_nickname(
            {"irlName": "Максимилиан Александрович Пржевальский", "nameStatic": "Nick 12345678"}
        )
        self.assertLessEqual(len(nickname), 32)
        self.assertTrue(nickname.startswith("ASX | "))
        self.assertTrue(nickname.endswith("| 12345678"))

    def test_legacy_nickname_parser(self) -> None:
        self.assertEqual(extract_nickname_and_static("John Doe | 12345"), ("John", "12345"))

    def test_channel_name_is_safe_and_limited(self) -> None:
        self.assertEqual(sanitize_channel_name_component("  Name / Test!  "), "name-test")

    def test_formatted_nickname_keeps_prefix_and_static(self) -> None:
        self.assertEqual(build_formatted_nickname("Rec", "Иван", "40798"), "Rec | Иван | 40798")
        self.assertLessEqual(len(build_formatted_nickname("Chief Rec", "Очень Длинное Имя", "123456789012")), 32)


class ApplicationChannelNameTests(unittest.TestCase):
    def test_channel_name_matches_requested_format(self) -> None:
        self.assertEqual(build_application_channel_name("iZiTuller", 12), "application-izituller-№12")

    def test_channel_name_sanitizes_and_truncates(self) -> None:
        name = build_application_channel_name("!!! Странный / Ник !!!", 3)
        self.assertTrue(name.startswith("application-"))
        self.assertTrue(name.endswith("-№3"))
        self.assertLessEqual(len(name), 100)

    def test_channel_name_falls_back_when_nickname_is_empty(self) -> None:
        self.assertEqual(build_application_channel_name("###", 5), "application-user-№5")


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_is_reloaded_without_replacing_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            storage = make_storage(Path(raw_directory))
            applications = storage.applications
            applications["items"]["1"] = {"status": "pending"}
            applications.save()
            applications.reload()

            self.assertIs(applications, storage.applications)
            self.assertEqual(applications["items"]["1"]["status"], "pending")

    async def test_nearby_saves_are_coalesced_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            storage = make_storage(directory)
            storage.applications["items"]["2"] = {"status": "accepted"}
            storage.schedule_save("applications", delay_seconds=0.01)
            storage.schedule_save("applications", delay_seconds=0.01)
            await asyncio.sleep(0.03)

            self.assertEqual(make_storage(directory).applications["items"]["2"]["status"], "accepted")


class ProjectConfigTests(unittest.TestCase):
    """Проверяет ID и цвета, от которых зависят панели и логи."""

    def test_application_category_points_to_new_category(self) -> None:
        import config

        self.assertEqual(config.FAMQ_APPLICATION_CATEGORY_ID, 1540665998057676840)
        self.assertEqual(config.FAMQ_FAMILY_INFO_CHANNEL_ID, 1540664873925746810)
        self.assertEqual(config.RESTART_COMMAND_ROLE_ID, 1534265635813589122)

    def test_log_colors_are_lighter_than_before(self) -> None:
        import config

        # Раньше все эмбеды схлопывались в 0x050505 и сливались с тёмной темой.
        self.assertGreater(config.COLOR_LOG, 0x303030)
        self.assertGreater(config.COLOR, 0x101010)

    def test_theme_color_no_longer_flattens_palette(self) -> None:
        import config
        from embeds import normalize_theme_color

        self.assertEqual(normalize_theme_color(config.COLOR_SOFT), config.COLOR_SOFT)
        self.assertEqual(normalize_theme_color(config.COLOR_LOG), config.COLOR_LOG)
        # «Сигнальные» цвета Discord по-прежнему приводятся к палитре бота.
        self.assertEqual(normalize_theme_color(0xE74C3C), config.COLOR_MUTED)

    def test_only_one_family_banner_url_is_configured(self) -> None:
        import config

        self.assertEqual(config.PANEL_BANNER_URL, config.FAMILY_BRAND_BANNER_URL)
        self.assertIn("banners/1466147160763666472", config.FAMILY_BRAND_BANNER_URL)


class CardStructureTests(unittest.TestCase):
    """Карточки Components V2 должны собираться и держать заданный порядок блоков."""

    @staticmethod
    def component_types(view) -> list[int]:
        container = view.to_components()[0]
        return [child["type"] for child in container["components"]]

    def test_family_info_card_has_banner_before_button(self) -> None:
        from components import FamilyInfoCard

        types = self.component_types(FamilyInfoCard())
        self.assertIn(12, types, "нет MediaGallery с баннером")
        self.assertIn(1, types, "нет ActionRow с кнопкой")
        self.assertLess(types.index(12), types.index(1), "баннер должен идти перед кнопкой")

    def test_family_info_card_has_no_accent_stripe(self) -> None:
        from components import FamilyInfoCard

        self.assertIsNone(FamilyInfoCard().to_components()[0]["accent_color"])

    def test_voice_card_starts_with_banner(self) -> None:
        from voice import VoiceControlCard

        types = self.component_types(VoiceControlCard())
        self.assertEqual(types[0], 12, "баннер голосовой панели должен быть первым блоком")

    def test_voice_card_exposes_all_ten_controls(self) -> None:
        import json

        from voice import VoiceControlCard

        payload = json.dumps(VoiceControlCard().to_components())
        self.assertEqual(payload.count('"type": 2'), 10)

    def test_application_card_starts_with_banner_and_has_actions(self) -> None:
        import json

        from applications_flow import ApplicationCard

        application = {
            "id": 42,
            "server": "denver",
            "applicantId": 1,
            "status": "pending",
            "claimedBy": 0,
            "submittedAt": "2026-08-22T10:00:00+00:00",
            "irlName": "Иван",
            "ageIrl": "21",
            "levelOnline": "15",
            "fraction": "Нет",
            "nameStatic": "Ivan 40798",
        }
        card = ApplicationCard(application, "ivan")
        self.assertEqual(self.component_types(card)[0], 12)
        payload = json.dumps(card.to_components(), ensure_ascii=False)
        for custom_id in ("famq_review_42", "famq_call_42", "famq_accept_42", "famq_reject_42"):
            self.assertIn(custom_id, payload)

    def test_application_card_buttons_disable_together(self) -> None:
        import json

        from applications_flow import ApplicationCard

        application = {
            "id": 9,
            "server": "denver",
            "applicantId": 1,
            "status": "accepted",
            "claimedBy": 5,
            "submittedAt": "2026-08-22T10:00:00+00:00",
        }
        payload = json.dumps(ApplicationCard(application, "x", disabled=True).to_components())
        self.assertEqual(payload.count('"disabled": true'), 4)


class BannerFallbackTests(unittest.TestCase):
    """Карточки должны публиковаться даже если файла картинки нет на диске."""

    @staticmethod
    def gallery_url(view) -> str:
        for child in view.to_components()[0]["components"]:
            if child["type"] == 12:
                return child["items"][0]["media"]["url"]
        raise AssertionError("в карточке нет MediaGallery")

    def test_welcome_gallery_uses_attachment_when_file_exists(self) -> None:
        import config
        from components import FamilyInfoCard

        if not config.WELCOME_BANNER_PATH.exists():
            self.skipTest("файл картинки отсутствует локально")
        self.assertEqual(self.gallery_url(FamilyInfoCard()), config.WELCOME_BANNER_ATTACHMENT_URL)

    def test_welcome_gallery_falls_back_to_brand_banner(self) -> None:
        import config
        import components
        from components import FamilyInfoCard

        original = config.WELCOME_BANNER_PATH
        missing = original.parent / "__no_such_banner__.jpg"
        components.WELCOME_BANNER_PATH = missing
        try:
            url = self.gallery_url(FamilyInfoCard())
        finally:
            components.WELCOME_BANNER_PATH = original
        self.assertEqual(url, config.FAMILY_BRAND_BANNER_URL)
        self.assertFalse(url.startswith("attachment://"))

    def test_banner_file_is_none_when_missing(self) -> None:
        import components

        original = components.WELCOME_BANNER_PATH
        components.WELCOME_BANNER_PATH = original.parent / "__no_such_banner__.jpg"
        try:
            self.assertIsNone(components.welcome_banner_file())
        finally:
            components.WELCOME_BANNER_PATH = original


class WelcomeNoticeTests(unittest.TestCase):
    """Сообщения о входе/выходе: без дублей и с картинкой справа."""

    def setUp(self) -> None:
        import welcome

        welcome._recent_notices.clear()

    def test_repeated_event_is_skipped(self) -> None:
        import welcome

        self.assertTrue(welcome.mark_notice_sent(1, 100, "join"))
        self.assertFalse(welcome.mark_notice_sent(1, 100, "join"), "повтор должен отсеиваться")

    def test_join_and_leave_are_tracked_separately(self) -> None:
        import welcome

        self.assertTrue(welcome.mark_notice_sent(1, 100, "join"))
        self.assertTrue(welcome.mark_notice_sent(1, 100, "leave"))
        self.assertTrue(welcome.mark_notice_sent(1, 101, "join"))

    def test_old_entries_stop_blocking(self) -> None:
        import welcome

        self.assertTrue(welcome.mark_notice_sent(1, 100, "join"))
        # Сдвигаем отметку в прошлое за пределы окна дедупликации.
        key = (1, 100, "join")
        welcome._recent_notices[key] -= welcome.NOTICE_DEDUP_WINDOW_SECONDS + 1
        self.assertTrue(welcome.mark_notice_sent(1, 100, "join"))

    def test_notice_embed_has_thumbnail_on_the_right(self) -> None:
        import config
        import welcome

        embed = welcome.build_member_notice_embed(FakeMember(), joined=True)
        self.assertTrue(embed.thumbnail.url, "у сообщения должна быть картинка справа")
        if config.WELCOME_ICON_PATH.exists():
            self.assertEqual(embed.thumbnail.url, config.WELCOME_ICON_ATTACHMENT_URL)

    def test_leave_embed_differs_from_join(self) -> None:
        import welcome

        join = welcome.build_member_notice_embed(FakeMember(), joined=True)
        leave = welcome.build_member_notice_embed(FakeMember(), joined=False)
        self.assertIn("зашёл на сервер", join.description)
        self.assertIn("покинул сервер", leave.description)
        self.assertNotEqual(join.colour, leave.colour)


class FakeAsset:
    url = "https://example.invalid/avatar.png"


class FakeGuild:
    id = 1466147160763666472
    member_count = 217
    members: list = []


class FakeMember:
    """Минимальная замена discord.Member для проверки сборки эмбеда."""

    id = 1039133630036975616
    bot = False
    guild = FakeGuild()
    mention = "<@1039133630036975616>"
    display_avatar = FakeAsset()

    def __str__(self) -> str:
        return "versize52"


class LogBatchingTests(unittest.IsolatedAsyncioTestCase):
    """Логи уходят пачками, иначе активный сервер упирается в rate limit Discord."""

    async def test_many_events_collapse_into_few_requests(self) -> None:
        import discord

        import logs

        requests: list[int] = []

        class FakeChannel(discord.TextChannel):
            def __init__(self, channel_id: int) -> None:
                self.id = channel_id

            async def send(self, **kwargs) -> None:
                requests.append(len(kwargs.get("embeds", [])))

        channel = FakeChannel(1466322279440060572)
        for index in range(25):
            logs.enqueue_log_embed(channel, discord.Embed(description=f"событие {index}"))
        await asyncio.sleep(3.0)

        self.assertEqual(sum(requests), 25, "ни одна запись не должна потеряться")
        self.assertLessEqual(len(requests), 4, "25 событий должны уложиться в несколько запросов")
        self.assertTrue(all(count <= logs.LOG_EMBEDS_PER_MESSAGE for count in requests))


class InteractionTimingTests(unittest.IsolatedAsyncioTestCase):
    """Discord ждёт ответа на нажатие 3 секунды.

    Если сначала ходить в API, а отвечать в конце, пользователь видит
    «Взаимодействие не удалось», хотя действие потом всё-таки выполняется.
    """

    def setUp(self) -> None:
        import applications_flow

        self.module = applications_flow
        self.calls: list[str] = []
        self._patch(
            "lock_application_channel_to_recruiter",
            "refresh_application_message",
            "disable_buttons",
            "send_dm_or_fallback",
            "post_result",
            "log_application_event",
            "can_manage_application",
            "schedule_channel_deletion",
            "text_sendable",
        )

    def _patch(self, *names: str) -> None:
        """Возвращает подменённые атрибуты модуля после теста."""
        for name in names:
            original = getattr(self.module, name)
            self.addCleanup(setattr, self.module, name, original)

    def make_interaction(self):
        calls = self.calls

        class Response:
            def is_done(self) -> bool:
                return "defer" in calls

            async def defer(self, **_kwargs) -> None:
                calls.append("defer")

            async def send_message(self, *_a, **_k) -> None:
                calls.append("send_message")

            async def send_modal(self, *_a, **_k) -> None:
                calls.append("send_modal")

        class Followup:
            async def send(self, *_a, **_k) -> None:
                calls.append("followup")

        class Guild:
            id = 1466147160763666472

            def get_member(self, _user_id):
                return None

            def get_role(self, _role_id):
                return None

        class User:
            id = 555
            roles: list = []

        class Channel:
            id = 4242

            async def send(self, *_a, **_k) -> None:
                calls.append("channel_message")

        class Interaction:
            id = 1
            guild = Guild()
            user = User()
            channel = Channel()
            response = Response()
            followup = Followup()

        return Interaction()

    async def test_accept_defers_before_touching_discord_api(self) -> None:
        calls = self.calls
        application = {
            "id": 77,
            "server": "denver",
            "applicantId": 1,
            "guildId": 1466147160763666472,
            "channelId": 4242,
            "status": "pending",
            "claimedBy": 0,
            "submittedAt": "2026-08-22T10:00:00+00:00",
        }
        self.module.application_store.setdefault("items", {})["77"] = application

        async def fake_lock(*_a, **_k):
            calls.append("lock_channel")

        async def noop(*_a, **_k):
            calls.append("api_call")

        self.module.lock_application_channel_to_recruiter = fake_lock
        self.module.disable_buttons = noop
        self.module.send_dm_or_fallback = noop
        self.module.post_result = noop
        self.module.log_application_event = noop
        self.module.can_manage_application = lambda *_a, **_k: True
        self.module.schedule_channel_deletion = lambda *_a, **_k: calls.append("schedule_delete")

        card = self.module.ApplicationCard(application, "tester")
        await card.accept_callback(self.make_interaction())

        self.assertIn("defer", calls, "ответ должен быть отложен")
        self.assertEqual(calls[0], "defer", "defer обязан быть самым первым действием")
        self.assertLess(
            calls.index("defer"),
            calls.index("lock_channel"),
            "обращения к API должны идти только после defer",
        )
        self.assertIn("schedule_delete", calls, "канал заявки должен ставиться на удаление")

    async def test_review_defers_before_touching_discord_api(self) -> None:
        calls = self.calls
        application = {
            "id": 78,
            "server": "denver",
            "applicantId": 1,
            "guildId": 1466147160763666472,
            "channelId": 4243,
            "status": "pending",
            "claimedBy": 0,
            "submittedAt": "2026-08-22T10:00:00+00:00",
        }
        self.module.application_store.setdefault("items", {})["78"] = application

        async def fake_lock(*_a, **_k):
            calls.append("lock_channel")

        async def noop(*_a, **_k):
            calls.append("api_call")

        self.module.lock_application_channel_to_recruiter = fake_lock
        self.module.refresh_application_message = noop
        self.module.log_application_event = noop
        self.module.can_manage_application = lambda *_a, **_k: True
        self.module.text_sendable = lambda _channel: True

        card = self.module.ApplicationCard(application, "tester")
        await card.review_callback(self.make_interaction())

        self.assertEqual(calls[0], "defer")
        self.assertLess(calls.index("defer"), calls.index("lock_channel"))


class ChannelLockingTests(unittest.IsolatedAsyncioTestCase):
    """Закрытие канала заявки не должно масштабироваться числом рекрутеров."""

    async def test_locking_uses_role_overwrites_not_per_member(self) -> None:
        import applications_flow

        permission_calls: list[str] = []

        class Role:
            def __init__(self, role_id: int) -> None:
                self.id = role_id

        class Member:
            def __init__(self, member_id: int) -> None:
                self.id = member_id
                self.bot = False
                self.roles = [Role(1485562379512315996)]

        class Channel:
            id = 4242
            # Двадцать рекрутеров в канале: раньше это означало двадцать запросов.
            members = [Member(1000 + index) for index in range(20)]

            async def set_permissions(self, target, **_kwargs) -> None:
                permission_calls.append(type(target).__name__)

        channel = Channel()

        class Guild:
            id = 1466147160763666472

            def get_channel(self, _channel_id):
                return channel

            def get_member(self, member_id):
                return Member(member_id)

            def get_role(self, role_id):
                return Role(role_id)

        original_text_sendable = applications_flow.text_sendable
        self.addCleanup(setattr, applications_flow, "text_sendable", original_text_sendable)
        applications_flow.text_sendable = lambda _channel: True
        await applications_flow.lock_application_channel_to_recruiter(
            Guild(),
            {"id": 5, "channelId": 4242, "claimedBy": 777, "applicantId": 1, "server": "denver", "guildId": 1},
        )

        self.assertLessEqual(
            len(permission_calls),
            8,
            f"ожидались единицы запросов на роли, а не по одному на участника: {len(permission_calls)}",
        )
        self.assertIn("Member", permission_calls, "закрепивший рекрутер должен сохранить доступ")


class InteractionDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    """Просроченные и упавшие взаимодействия должны быть видны в логе."""

    async def test_expired_interaction_is_reported_and_stops_handler(self) -> None:
        import discord

        import interactions

        messages: list[str] = []
        original_log = interactions.console_log
        self.addCleanup(setattr, interactions, "console_log", original_log)
        interactions.console_log = messages.append

        class Response:
            def is_done(self) -> bool:
                return False

            async def defer(self, **_kwargs):
                raise discord.NotFound(_FakeResponse(), "Unknown interaction")

        class Interaction:
            response = Response()

        acknowledged = await interactions.ack_interaction(Interaction(), label="review#1")

        self.assertFalse(acknowledged, "обработчик обязан остановиться на истёкшем взаимодействии")
        self.assertTrue(any("expired" in text for text in messages), messages)

    async def test_slow_acknowledgement_is_flagged(self) -> None:
        import interactions

        messages: list[str] = []
        original_log = interactions.console_log
        self.addCleanup(setattr, interactions, "console_log", original_log)
        interactions.console_log = messages.append

        class Response:
            def is_done(self) -> bool:
                return False

            async def defer(self, **_kwargs):
                await asyncio.sleep(interactions.SLOW_ACK_SECONDS + 0.05)

        class Interaction:
            response = Response()

        self.assertTrue(await interactions.ack_interaction(Interaction(), label="accept#1"))
        self.assertTrue(any("slowly" in text for text in messages), messages)


class _FakeResponse:
    status = 404
    reason = "Not Found"


if __name__ == "__main__":
    unittest.main()
