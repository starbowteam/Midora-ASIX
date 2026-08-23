from __future__ import annotations


import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


class JsonStore(dict[str, Any]):
    """A JSON-backed mapping that keeps its object identity when reloaded."""

    def __init__(self, path: Path, default_factory: Callable[[], dict[str, Any]]) -> None:
        self.path = path
        self.default_factory = default_factory
        super().__init__()
        self.reload()

    def normalize(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else self.default_factory()

    def reload(self) -> None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = self.default_factory()
        self.clear()
        self.update(self.normalize(value))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self, ensure_ascii=False, indent=2)
        temporary_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary_path.write_text(payload, encoding="utf-8")
        temporary_path.replace(self.path)


class BotStorage:
    """Owns every persisted state document used by the bot."""

    def __init__(
        self,
        *,
        applications_file: Path,
        panels_file: Path,
        giveaways_file: Path,
        voice_rooms_file: Path,
        member_activity_file: Path,
        legacy_applications_file: Path,
    ) -> None:
        self._migrate_legacy_applications(applications_file, legacy_applications_file)
        self.applications = JsonStore(applications_file, lambda: {"nextId": 1, "items": {}})
        self.panels = JsonStore(panels_file, dict)
        self.giveaways = JsonStore(giveaways_file, lambda: {"nextId": 1, "items": {}})
        self.voice_rooms = JsonStore(voice_rooms_file, lambda: {"rooms": {}})
        self.member_activity = JsonStore(member_activity_file, lambda: {"guilds": {}})
        self._stores = {
            "applications": self.applications,
            "panels": self.panels,
            "giveaways": self.giveaways,
            "voice_rooms": self.voice_rooms,
            "member_activity": self.member_activity,
        }
        self._pending_saves: dict[str, asyncio.TimerHandle] = {}
        self._dirty_stores: set[str] = set()
        self._ensure_shapes()

    @staticmethod
    def _migrate_legacy_applications(applications_file: Path, legacy_file: Path) -> None:
        if applications_file.exists() or not legacy_file.exists():
            return
        applications_file.parent.mkdir(parents=True, exist_ok=True)
        applications_file.write_text(legacy_file.read_text(encoding="utf-8"), encoding="utf-8")

    def _ensure_shapes(self) -> None:
        self.applications.setdefault("nextId", 1)
        if not isinstance(self.applications.get("items"), dict):
            self.applications["items"] = {}
        self.giveaways.setdefault("nextId", 1)
        if not isinstance(self.giveaways.get("items"), dict):
            self.giveaways["items"] = {}
        if not isinstance(self.voice_rooms.get("rooms"), dict):
            self.voice_rooms["rooms"] = {}
        if not isinstance(self.member_activity.get("guilds"), dict):
            self.member_activity["guilds"] = {}

    def schedule_save(self, store_name: str, delay_seconds: float = 0.35) -> None:
        """Coalesce nearby writes so Discord events do not block on disk I/O."""
        if store_name not in self._stores:
            raise KeyError(f"Unknown storage document: {store_name}")
        self._dirty_stores.add(store_name)
        if store_name in self._pending_saves:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.flush(store_name)
            return
        self._pending_saves[store_name] = loop.call_later(delay_seconds, self._save_scheduled, store_name)

    def _save_scheduled(self, store_name: str) -> None:
        self._pending_saves.pop(store_name, None)
        if store_name not in self._dirty_stores:
            return
        self._stores[store_name].save()
        self._dirty_stores.discard(store_name)

    def flush(self, store_name: str | None = None) -> None:
        store_names = (store_name,) if store_name is not None else tuple(self._dirty_stores)
        for name in store_names:
            pending = self._pending_saves.pop(name, None)
            if pending is not None:
                pending.cancel()
            if name in self._dirty_stores:
                self._stores[name].save()
                self._dirty_stores.discard(name)

    def reload(self, store_name: str) -> None:
        if store_name not in self._stores:
            raise KeyError(f"Unknown storage document: {store_name}")
        self.flush(store_name)
        self._stores[store_name].reload()
        self._ensure_shapes()
