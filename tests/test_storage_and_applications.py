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


if __name__ == "__main__":
    unittest.main()
