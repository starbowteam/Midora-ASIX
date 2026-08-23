"""Components V2-карточки: цельные тёмные панели вместо классических эмбедов."""

from __future__ import annotations

import discord

from config import (
    FAMILY_BRAND_BANNER_URL,
    FAMQ_MEDIA_CHANNEL_ID,
    FAMQ_PANEL_CHANNEL_ID,
    FAMQ_RESULTS_CHANNEL_ID,
    WELCOME_BANNER_ATTACHMENT_URL,
    WELCOME_BANNER_FILENAME,
    WELCOME_BANNER_PATH,
)


REGISTER_URL = "https://majestic-rp.ru/register?utm_campaign=ASIX"


def welcome_banner_file() -> discord.File | None:
    """Свежий File с картинкой приветствия.

    Картинка отправляется вложением, а не ссылкой: подписанные ссылки Discord CDN
    (`?ex=...&hm=...`) перестают отвечать примерно через сутки после выдачи.
    """
    if not WELCOME_BANNER_PATH.exists():
        return None
    return discord.File(WELCOME_BANNER_PATH, filename=WELCOME_BANNER_FILENAME)


def large_separator() -> discord.ui.Separator:
    return discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large)


def small_separator() -> discord.ui.Separator:
    return discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small)


def brand_gallery() -> discord.ui.MediaGallery:
    return discord.ui.MediaGallery(discord.MediaGalleryItem(FAMILY_BRAND_BANNER_URL))


def welcome_gallery() -> discord.ui.MediaGallery:
    """Картинка приветствия: вложением, а при его отсутствии — баннером семьи.

    Ссылка на вложение работает только вместе с самим файлом. Если файла нет
    (например, папку assets забыли задеплоить), карточка всё равно должна
    публиковаться — просто с фирменным баннером вместо welcome-картинки.
    """
    url = WELCOME_BANNER_ATTACHMENT_URL if WELCOME_BANNER_PATH.exists() else FAMILY_BRAND_BANNER_URL
    return discord.ui.MediaGallery(discord.MediaGalleryItem(url))


class FamilyInfoCard(discord.ui.LayoutView):
    """Карточка «Информация о семье».

    Порядок элементов задан продуктом: заголовок → приветствие → промокод →
    каналы → баннер во всю ширину → кнопка регистрации. Всё внутри одного
    Container, поэтому Discord рисует это цельной тёмной панелью без цветной
    полосы слева (accent_colour намеренно не задан).
    """

    def __init__(
        self,
        *,
        applications_channel_id: int = FAMQ_PANEL_CHANNEL_ID,
        results_channel_id: int = FAMQ_RESULTS_CHANNEL_ID,
        media_channel_id: int = FAMQ_MEDIA_CHANNEL_ID,
    ) -> None:
        super().__init__(timeout=None)

        register_button = discord.ui.Button(
            label="Регистрация",
            emoji="🔗",
            style=discord.ButtonStyle.link,
            url=REGISTER_URL,
        )

        card = discord.ui.Container(
            discord.ui.TextDisplay("# Информация о семье"),
            large_separator(),
            discord.ui.TextDisplay("## Добро пожаловать в нашу семью на Majestic Denver!"),
            large_separator(),
            discord.ui.TextDisplay(
                "## Наш семейный промокод `/promo ASIX`\n"
                "> Получай **$50.000** и **7 дней Majestic Premium**"
            ),
            large_separator(),
            discord.ui.TextDisplay(
                "## Каналы семьи\n"
                f"• **Заявки:** <#{applications_channel_id}>\n"
                f"• **Итог заявок:** <#{results_channel_id}>\n"
                f"• **MEDIA:** <#{media_channel_id}>"
            ),
            large_separator(),
            welcome_gallery(),
            discord.ui.ActionRow(register_button),
        )
        self.add_item(card)
