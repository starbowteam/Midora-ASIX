"""Общая обвязка над взаимодействиями Discord.

Discord отводит на ответ 3 секунды. Если не уложиться, пользователь видит
«Приложение не ответило вовремя» (кнопка) или «Что-то пошло не так» (форма),
а причина при этом нигде не видна. Здесь ответ подтверждается сразу, время
подтверждения замеряется, а любая ошибка обработчика попадает в лог и
доходит до нажавшего.
"""

from __future__ import annotations

import time
import traceback

import discord

from botcore import console_log


# Порог, после которого подтверждение считается медленным. Discord обрывает
# взаимодействие на 3 секундах, поэтому предупреждаем заранее.
SLOW_ACK_SECONDS = 1.5


async def ack_interaction(interaction: discord.Interaction, *, label: str) -> bool:
    """Подтверждает взаимодействие. Возвращает False, если Discord его уже закрыл."""
    if interaction.response.is_done():
        return True

    started = time.monotonic()
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.NotFound:
        # Прошло больше трёх секунд: токен взаимодействия уже недействителен.
        console_log(
            f"Interaction '{label}' expired before it could be acknowledged "
            f"({time.monotonic() - started:.1f}s). Скорее всего бот упёрся в лимит запросов Discord."
        )
        return False
    except Exception as error:
        console_log(f"Interaction '{label}' could not be acknowledged: {error!r}")
        return False

    elapsed = time.monotonic() - started
    if elapsed >= SLOW_ACK_SECONDS:
        console_log(
            f"Interaction '{label}' acknowledged slowly: {elapsed:.1f}s. "
            "Бот близок к лимиту Discord — проверьте объём исходящих запросов."
        )
    return True


async def report_interaction_error(
    interaction: discord.Interaction,
    error: BaseException,
    *,
    label: str,
) -> None:
    """Пишет ошибку в лог и сообщает о ней нажавшему."""
    # Форма с тремя аргументами работает на всех версиях Python, в отличие от
    # однопараметрического варианта, появившегося только в 3.10.
    details = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    console_log(f"Interaction '{label}' failed: {error!r}\n{details}")
    message = "Не удалось выполнить действие. Ошибка записана в лог, попробуйте ещё раз."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        # Взаимодействие могло уже истечь — тогда остаётся только запись в логе.
        pass


class LoggedErrorsMixin:
    """Подмешивается к View/Modal, чтобы ошибки не пропадали молча."""

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: object = None,
    ) -> None:
        label = f"{type(self).__name__}:{getattr(item, 'custom_id', '') or 'submit'}"
        await report_interaction_error(interaction, error, label=label)
