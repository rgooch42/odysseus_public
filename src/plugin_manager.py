"""Discovers, loads, and manages the enable/disable lifecycle of Odysseus plugins."""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from src.plugin_manifest import PluginManifest, ManifestError
from src.plugin_registry import PluginRegistry, get_registry

logger = logging.getLogger(__name__)


@dataclass
class Plugin:
    directory: Path
    manifest: PluginManifest


class PluginManager:
    def __init__(
        self,
        plugins_dir: Path,
        state_file: Path,
        registry: Optional[PluginRegistry] = None,
    ) -> None:
        self._plugins_dir = Path(plugins_dir)
        self._state_file = Path(state_file)
        self._registry = registry or get_registry()
        self._discovered: List[Plugin] = []

    # ── Discovery ────────────────────────────────────────────────────────────

    def discover(self) -> List[Plugin]:
        """Scan plugins_dir for valid plugin subdirectories. Returns all found."""
        self._discovered = []
        if not self._plugins_dir.exists():
            return self._discovered
        for entry in sorted(self._plugins_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "plugin.yaml"
            if not manifest_path.exists():
                continue
            try:
                manifest = PluginManifest.from_path(manifest_path)
                self._discovered.append(Plugin(directory=entry, manifest=manifest))
                logger.debug("Plugin discovered: %s @ %s", manifest.name, entry)
            except ManifestError as e:
                logger.warning("Skipping plugin in %s: %s", entry.name, e)
        return self._discovered

    # ── Enable / disable state ────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"enabled": []}

    def _save_state(self, state: dict) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )

    def is_enabled(self, plugin_name: str) -> bool:
        return plugin_name in self._load_state().get("enabled", [])

    def set_enabled(self, plugin_name: str, enabled: bool) -> None:
        state = self._load_state()
        names: list = state.setdefault("enabled", [])
        if enabled and plugin_name not in names:
            names.append(plugin_name)
        elif not enabled and plugin_name in names:
            names.remove(plugin_name)
        self._save_state(state)

    def list_plugins(self) -> List[dict]:
        """Return serializable info for all discovered plugins."""
        return [
            {
                "name": p.manifest.name,
                "version": p.manifest.version,
                "description": p.manifest.description,
                "author": p.manifest.author,
                "enabled": self.is_enabled(p.manifest.name),
            }
            for p in self._discovered
        ]

    # ── Loading ───────────────────────────────────────────────────────────────

    def load_enabled(self) -> None:
        """Import and register tools for all enabled plugins."""
        enabled_names = set(self._load_state().get("enabled", []))
        for plugin in self._discovered:
            if plugin.manifest.name not in enabled_names:
                continue
            self._load_plugin(plugin)

    def _load_plugin(self, plugin: Plugin) -> None:
        name = plugin.manifest.name
        if plugin.manifest.tools:
            tools_path = plugin.directory / plugin.manifest.tools
            if tools_path.exists():
                try:
                    mod = self._import_module(f"_plugin_{name}_tools", tools_path)
                    schemas = getattr(mod, "TOOL_SCHEMAS", [])
                    impls = getattr(mod, "TOOL_IMPLEMENTATIONS", {})
                    self._registry.register_tool_schemas(name, schemas)
                    for tool_name, impl in impls.items():
                        self._registry.register_tool_implementation(tool_name, impl)
                    logger.info("Plugin %s: loaded %d tool(s)", name, len(schemas))
                except Exception as e:
                    logger.error("Plugin %s: failed to load tools: %s", name, e)

    def register_routes(self, app) -> None:
        """Register FastAPI routers for all enabled plugins that declare routes."""
        enabled_names = set(self._load_state().get("enabled", []))
        for plugin in self._discovered:
            if plugin.manifest.name not in enabled_names:
                continue
            if not plugin.manifest.routes:
                continue
            routes_path = plugin.directory / plugin.manifest.routes
            if not routes_path.exists():
                logger.warning("Plugin %s: routes file not found: %s", plugin.manifest.name, routes_path)
                continue
            try:
                mod = self._import_module(
                    f"_plugin_{plugin.manifest.name}_routes", routes_path
                )
                if hasattr(mod, "setup_routes"):
                    router = mod.setup_routes()
                    app.include_router(router)
                    logger.info("Plugin %s: routes registered", plugin.manifest.name)
                elif hasattr(mod, "router"):
                    app.include_router(mod.router)
                    logger.info("Plugin %s: routes registered", plugin.manifest.name)
                else:
                    logger.warning(
                        "Plugin %s: routes.py must expose setup_routes() or router",
                        plugin.manifest.name,
                    )
            except Exception as e:
                logger.error("Plugin %s: failed to register routes: %s", plugin.manifest.name, e)

    @staticmethod
    def _import_module(module_name: str, path: Path):
        spec = importlib.util.spec_from_file_location(module_name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
        return mod


# Module-level singleton — initialized by app.py at startup.
_manager: Optional[PluginManager] = None


def get_manager() -> Optional[PluginManager]:
    return _manager


def init_manager(plugins_dir: Path, state_file: Path) -> PluginManager:
    global _manager
    _manager = PluginManager(plugins_dir=plugins_dir, state_file=state_file)
    _manager.discover()
    _manager.load_enabled()
    return _manager
