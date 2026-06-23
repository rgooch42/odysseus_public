"""Plugin settings storage backend."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class SettingsProvider(Protocol):
    """Minimal storage interface for plugin settings.

    PR 2 will add OpenBaoProvider implementing this same protocol.
    """
    def load(self, plugin_name: str) -> dict: ...
    def save(self, plugin_name: str, data: dict) -> None: ...


class LocalJSONProvider:
    """Stores per-plugin settings as JSON files: {settings_dir}/{name}.json"""

    def __init__(self, settings_dir: Path) -> None:
        self._dir = Path(settings_dir)

    def _path(self, plugin_name: str) -> Path:
        return self._dir / f"{plugin_name}.json"

    def load(self, plugin_name: str) -> dict:
        p = self._path(plugin_name)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not read settings for plugin %s — returning empty", plugin_name)
            return {}

    def save(self, plugin_name: str, data: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(plugin_name).write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
