"""Хранилище состояния бота: чтение/запись JSON-файлов с отложенным сохранением."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional


class BotStorage:
    """Управляет сохранением и загрузкой данных в JSON-файлы."""

    def __init__(
        self,
        applications_file: Path,
        panels_file: Path,
        giveaways_file: Path,
        voice_rooms_file: Path,
        member_activity_file: Path,
        legacy_applications_file: Optional[Path] = None,
        save_delay: float = 2.0,
    ):
        self.applications_file = applications_file
        self.panels_file = panels_file
        self.giveaways_file = giveaways_file
        self.voice_rooms_file = voice_rooms_file
        self.member_activity_file = member_activity_file
        self.legacy_applications_file = legacy_applications_file

        self.save_delay = save_delay
        self._pending_saves: dict[str, bool] = {}
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

        # Загружаем все данные при инициализации
        self._data: dict[str, dict[str, Any]] = {}
        self._load_all()

    def _load_all(self) -> None:
        """Загружает все JSON-файлы в память."""
        self._data["applications"] = self._load_json(self.applications_file) or {"items": {}, "nextId": 1}
        self._data["panels"] = self._load_json(self.panels_file) or {}
        self._data["giveaways"] = self._load_json(self.giveaways_file) or {"items": {}, "nextId": 1}
        self._data["voice_rooms"] = self._load_json(self.voice_rooms_file) or {"rooms": {}}
        self._data["member_activity"] = self._load_json(self.member_activity_file) or {"guilds": {}}

        # Если есть legacy-файл, пробуем загрузить из него заявки (при первом запуске)
        if self.legacy_applications_file and self.legacy_applications_file.exists():
            legacy = self._load_json(self.legacy_applications_file)
            if legacy and "items" in legacy:
                # Объединяем с существующими заявками (новые перезаписывают старые)
                current_items = self._data["applications"].get("items", {})
                current_next = self._data["applications"].get("nextId", 1)
                legacy_items = legacy.get("items", {})
                # Если нет текущих заявок, используем legacy целиком
                if not current_items:
                    self._data["applications"] = legacy
                else:
                    # Обновляем id и дополняем
                    merged = {**legacy_items, **current_items}
                    self._data["applications"]["items"] = merged
                    self._data["applications"]["nextId"] = max(current_next, legacy.get("nextId", 1))
                # После загрузки legacy удаляем файл, чтобы не загружать его повторно
                try:
                    self.legacy_applications_file.unlink()
                except Exception:
                    pass

        self._schedule_save("applications")
        self._schedule_save("panels")
        self._schedule_save("giveaways")
        self._schedule_save("voice_rooms")
        self._schedule_save("member_activity")

    @staticmethod
    def _load_json(path: Path) -> Optional[dict[str, Any]]:
        """Загружает JSON из файла, возвращает None при ошибке."""
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _save_json(path: Path, data: dict[str, Any]) -> None:
        """Сохраняет данные в JSON-файл атомарно."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_path.replace(path)
        except Exception:
            # Если запись не удалась, пытаемся удалить временный файл
            try:
                temp_path.unlink()
            except Exception:
                pass
            raise

    def _schedule_save(self, key: str) -> None:
        """Планирует отложенное сохранение указанного раздела."""
        with self._lock:
            self._pending_saves[key] = True
            self._reset_timer()

    def _reset_timer(self) -> None:
        """Сбрасывает таймер: если он уже был, отменяет и создаёт новый."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._timer = threading.Timer(self.save_delay, self._flush_pending)
        self._timer.daemon = True
        self._timer.start()

    def _flush_pending(self) -> None:
        """Сохраняет все накопившиеся изменения."""
        with self._lock:
            pending = list(self._pending_saves.keys())
            self._pending_saves.clear()
            self._timer = None

        for key in pending:
            if key in self._data:
                path = self._file_for_key(key)
                if path is not None:
                    try:
                        self._save_json(path, self._data[key])
                    except Exception as e:
                        # Логируем ошибку, но не прерываем остальные сохранения
                        print(f"Failed to save {key}: {e}")

    def _file_for_key(self, key: str) -> Optional[Path]:
        """Возвращает путь к файлу для данного ключа."""
        mapping = {
            "applications": self.applications_file,
            "panels": self.panels_file,
            "giveaways": self.giveaways_file,
            "voice_rooms": self.voice_rooms_file,
            "member_activity": self.member_activity_file,
        }
        return mapping.get(key)

    # --- Публичные методы для доступа к данным ---

    @property
    def applications(self) -> dict[str, Any]:
        return self._data["applications"]

    @property
    def panels(self) -> dict[str, Any]:
        return self._data["panels"]

    @property
    def giveaways(self) -> dict[str, Any]:
        return self._data["giveaways"]

    @property
    def voice_rooms(self) -> dict[str, Any]:
        return self._data["voice_rooms"]

    @property
    def member_activity(self) -> dict[str, Any]:
        return self._data["member_activity"]

    def schedule_save(self, key: str) -> None:
        """Вызывается извне для планирования сохранения раздела."""
        self._schedule_save(key)

    def flush(self) -> None:
        """Принудительно сохраняет все данные (используется при перезапуске)."""
        with self._lock:
            # Сохраняем все, что есть, даже если не запланировано
            for key, data in self._data.items():
                path = self._file_for_key(key)
                if path is not None:
                    try:
                        self._save_json(path, data)
                    except Exception as e:
                        print(f"Failed to flush {key}: {e}")
            self._pending_saves.clear()
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def reload(self, key: str) -> None:
        """Перезагружает указанный раздел из файла (для обновления извне)."""
        path = self._file_for_key(key)
        if path is None:
            return
        data = self._load_json(path)
        if data is not None:
            self._data[key] = data
