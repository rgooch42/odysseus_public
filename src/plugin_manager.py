"""Discovers, loads, and manages the enable/disable lifecycle of Odysseus plugins."""
from __future__ import annotations

import importlib.util
import json
import logging
import os
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

    def get_plugin_setting(self, plugin_name: str, key: str):
        """Return the stored value for a plugin setting key, or None."""
        return self._load_state().get("settings", {}).get(plugin_name, {}).get(key)

    def save_plugin_settings(self, plugin_name: str, values: dict) -> None:
        """Persist settings for a plugin. Merges into existing values."""
        state = self._load_state()
        plugin_settings = state.setdefault("settings", {}).setdefault(plugin_name, {})
        plugin_settings.update(values)
        self._save_state(state)

    def get_all_plugin_settings(self, plugin_name: str) -> dict:
        """Return all stored settings for a plugin."""
        return self._load_state().get("settings", {}).get(plugin_name, {})

    def validate_requirements(self, plugin: "Plugin") -> tuple[str, list]:
        """Check all requires: entries for a plugin.

        Returns (validation_status, issues) where:
          - validation_status: "ok" | "warning" | "error"
          - issues: list of dicts with keys type, severity, and env or integration
        """
        if not plugin.manifest.requires:
            return "ok", []

        issues = []
        severity_rank = {"ok": 0, "warning": 1, "error": 2}
        worst = "ok"

        for req in plugin.manifest.requires:
            if req.env:
                if not os.environ.get(req.env, "").strip():
                    issues.append({"type": "env_missing", "env": req.env, "severity": "error"})
                    if severity_rank["error"] > severity_rank[worst]:
                        worst = "error"
            elif req.integration:
                # When both env and integration are set, env is checked and integration is ignored.
                if not self._integration_configured(req.integration):
                    issues.append({
                        "type": "integration_missing",
                        "integration": req.integration,
                        "severity": "warning",
                    })
                    if severity_rank["warning"] > severity_rank[worst]:
                        worst = "warning"

        return worst, issues

    def _integration_configured(self, integration_type: str) -> bool:
        """Return True if the named integration type appears to be set up."""
        if integration_type == "vault":
            # OpenBao / HashiCorp Vault: check for the standard address env var
            return bool(os.environ.get("VAULT_ADDR", "").strip())
        # Unknown integration types: treat as not configured (warning, not error)
        return False

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
        if plugin.manifest.models:
            models_path = plugin.directory / plugin.manifest.models
            if models_path.exists():
                try:
                    self._import_module(f"_plugin_{name}_models", models_path)
                    logger.info("Plugin %s: models imported", name)
                except Exception as e:
                    logger.error("Plugin %s: failed to import models: %s", name, e)
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

    async def register_mcp_servers(self, mcp_manager) -> None:
        """Connect HTTP MCP servers declared in enabled plugins' manifests.

        server_id format: "builtin_http_plugin_{plugin_name}_{mcp_name_lower}"
        The "builtin_http_" prefix makes McpManager.is_builtin() and
        _is_external_builtin() include these tools in the LLM prompt, matching
        the behaviour of hand-configured HTTP built-ins.
        """
        import json as _json

        enabled_names = set(self._load_state().get("enabled", []))
        for plugin in self._discovered:
            name = plugin.manifest.name
            if name not in enabled_names:
                continue
            if not plugin.manifest.mcp_servers:
                continue
            for mcp_cfg in plugin.manifest.mcp_servers:
                url = os.environ.get(mcp_cfg.url_env, "").strip()
                if not url:
                    logger.warning(
                        "Plugin %s: MCP server %s skipped (%s not set)",
                        name, mcp_cfg.name, mcp_cfg.url_env,
                    )
                    continue
                headers = None
                if mcp_cfg.headers_env:
                    raw = os.environ.get(mcp_cfg.headers_env, "").strip()
                    if raw:
                        try:
                            headers = _json.loads(raw)
                        except Exception:
                            logger.warning(
                                "Plugin %s: MCP server %s — invalid JSON in %s",
                                name, mcp_cfg.name, mcp_cfg.headers_env,
                            )
                safe_mcp_name = mcp_cfg.name.lower().replace("-", "_").replace(" ", "_")
                server_id = f"builtin_http_plugin_{name.replace('-', '_')}_{safe_mcp_name}"
                display_name = f"Plugin [{name}]: {mcp_cfg.name}"
                if mcp_cfg.description:
                    display_name += f" — {mcp_cfg.description}"
                try:
                    ok = await mcp_manager.connect_server(
                        server_id=server_id,
                        name=display_name,
                        transport=mcp_cfg.transport,
                        url=url,
                        headers=headers,
                    )
                    if ok:
                        logger.info("Plugin %s: MCP server %s connected", name, mcp_cfg.name)
                    else:
                        logger.warning("Plugin %s: MCP server %s failed to connect", name, mcp_cfg.name)
                except Exception as e:
                    logger.error("Plugin %s: MCP server %s error: %s", name, mcp_cfg.name, e)

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
