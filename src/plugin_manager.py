"""Discovers, loads, and manages the enable/disable lifecycle of Odysseus plugins."""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from src.plugin_manifest import PluginManifest, ManifestError
from src.plugin_registry import PluginRegistry, get_registry
from src.plugin_settings_provider import LocalJSONProvider, SettingsProvider

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
        provider: Optional[Any] = None,
    ) -> None:
        self._plugins_dir = Path(plugins_dir)
        self._state_file = Path(state_file)
        self._registry = registry or get_registry()
        self._discovered: List[Plugin] = []
        self._mcp_manager: Optional[Any] = None
        settings_dir = Path(state_file).parent / "plugin-settings"
        self._provider = provider or LocalJSONProvider(settings_dir)

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
        self._migrate_legacy_state()
        return self._discovered

    # ── Migration ─────────────────────────────────────────────────────────────

    def _migrate_legacy_state(self) -> None:
        """One-time migration from monolithic data/plugins.json to per-plugin files."""
        if not self._state_file.exists():
            return
        try:
            old = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        enabled_names = set(old.get("enabled", []))
        old_settings = old.get("settings", {})
        migrated: set = set()
        for name in enabled_names:
            if not self._provider.load(name):
                self._provider.save(name, {
                    "enabled": True,
                    "services": {},
                    "settings": old_settings.get(name, {}),
                })
            migrated.add(name)
        for name, plugin_settings in old_settings.items():
            if name not in migrated and not self._provider.load(name):
                self._provider.save(name, {
                    "enabled": False,
                    "services": {},
                    "settings": plugin_settings,
                })
        migrated_path = self._state_file.with_suffix(".json.migrated")
        self._state_file.rename(migrated_path)
        logger.info("Migrated legacy plugins.json → per-plugin settings files (%s)", migrated_path)

    # ── Enable / disable state ────────────────────────────────────────────────

    def is_enabled(self, plugin_name: str) -> bool:
        return self._provider.load(plugin_name).get("enabled", False)

    def set_enabled(self, plugin_name: str, enabled: bool) -> None:
        data = self._provider.load(plugin_name)
        data["enabled"] = enabled
        self._provider.save(plugin_name, data)

    def is_service_enabled(self, plugin_name: str, service_name: str) -> bool:
        return self._provider.load(plugin_name).get("services", {}).get(service_name, {}).get("enabled", True)

    def set_service_enabled(self, plugin_name: str, service_name: str, enabled: bool) -> None:
        data = self._provider.load(plugin_name)
        data.setdefault("services", {}).setdefault(service_name, {})["enabled"] = enabled
        self._provider.save(plugin_name, data)

    def get_service_setting(self, plugin_name: str, service_name: str, key: str):
        return self._provider.load(plugin_name).get("services", {}).get(service_name, {}).get(key)

    def save_service_settings(self, plugin_name: str, service_name: str, values: dict) -> None:
        data = self._provider.load(plugin_name)
        data.setdefault("services", {}).setdefault(service_name, {}).update(values)
        self._provider.save(plugin_name, data)

    def get_all_plugin_settings(self, plugin_name: str) -> dict:
        return self._provider.load(plugin_name).get("settings", {})

    def save_plugin_settings(self, plugin_name: str, values: dict) -> None:
        """Persist settings for a plugin. Merges into existing values."""
        data = self._provider.load(plugin_name)
        data.setdefault("settings", {}).update(values)
        self._provider.save(plugin_name, data)

    def get_plugin_setting(self, plugin_name: str, key: str):
        """Return the stored value for a plugin setting key, or None."""
        return self._provider.load(plugin_name).get("settings", {}).get(key)

    # ── URL / Header resolution ───────────────────────────────────────────────

    def resolve_mcp_url(self, plugin_name: str, server) -> tuple:
        """Returns (url, source) where source is 'env' | 'local' | 'unset'."""
        if server.url_env:
            env_val = os.environ.get(server.url_env, "").strip()
            if env_val:
                return env_val, "env"
        local_val = (self.get_service_setting(plugin_name, server.name, "url") or "").strip()
        if local_val:
            return local_val, "local"
        return None, "unset"

    def resolve_mcp_headers(self, plugin_name: str, server) -> tuple:
        """Returns (headers_dict, source) where source is 'env' | 'local' | 'unset'."""
        if server.headers_env:
            env_val = os.environ.get(server.headers_env, "").strip()
            if env_val:
                try:
                    return json.loads(env_val), "env"
                except json.JSONDecodeError:
                    logger.warning("Plugin %s: invalid JSON in env %s", plugin_name, server.headers_env)
        local_val = (self.get_service_setting(plugin_name, server.name, "headers") or "").strip()
        if local_val:
            try:
                return json.loads(local_val), "local"
            except json.JSONDecodeError:
                logger.warning("Plugin %s: invalid JSON in local headers for %s", plugin_name, server.name)
        return None, "unset"

    # ── Validation ────────────────────────────────────────────────────────────

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

    def set_mcp_manager(self, mcp_manager) -> None:
        """Store a reference to the MCP manager for live server status lookups."""
        self._mcp_manager = mcp_manager

    def _get_services(self, plugin: "Plugin") -> list:
        """Build services list with live MCP connection status and local settings."""
        services = []
        plugin_name = plugin.manifest.name
        plugin_name_normalized = plugin_name.replace("-", "_")
        for srv in (plugin.manifest.mcp_servers or []):
            mcp_name = srv.name
            safe_mcp_name = mcp_name.lower().replace("-", "_").replace(" ", "_")
            server_id = f"builtin_http_plugin_{plugin_name_normalized}_{safe_mcp_name}"
            url, url_source = self.resolve_mcp_url(plugin_name, srv)

            if self._mcp_manager is not None:
                conn = self._mcp_manager.get_server_status(server_id)
                status = conn.get("status", "disconnected")
                tool_count = conn.get("tool_count", 0)
                enabled_tool_count = conn.get("enabled_tool_count", tool_count)
            else:
                status = "unconfigured" if url_source == "unset" else "disconnected"
                tool_count = 0
                enabled_tool_count = 0

            svc_settings_values = {}
            for s in (srv.settings or []):
                stored = self.get_service_setting(plugin_name, mcp_name, s.key)
                svc_settings_values[s.key] = stored if stored is not None else (s.default or "")

            services.append({
                "name": mcp_name,
                "description": srv.description,
                "status": status,
                "tool_count": tool_count,
                "enabled_tool_count": enabled_tool_count,
                "url_configured": url_source != "unset",
                "url_source": url_source,
                "enabled": self.is_service_enabled(plugin_name, mcp_name),
                "settings_schema": [s.model_dump() for s in (srv.settings or [])],
                "settings_values": svc_settings_values,
            })
        return services

    def list_plugins(self) -> List[dict]:
        """Return serializable info for all discovered plugins, including live MCP status."""
        result = []
        for p in self._discovered:
            name = p.manifest.name
            validation_status, validation_issues = self.validate_requirements(p)
            result.append({
                "name": name,
                "guid": p.manifest.guid,
                "version": p.manifest.version,
                "addon_type": p.manifest.addon_type,
                "description": p.manifest.description,
                "author": p.manifest.author,
                "enabled": self.is_enabled(name),
                "validation_status": validation_status,
                "validation_issues": validation_issues,
                "services": self._get_services(p),
                "settings_schema": [s.model_dump() for s in (p.manifest.settings or [])],
                "settings_values": self.get_all_plugin_settings(name),
            })
        return result

    # ── Loading ───────────────────────────────────────────────────────────────

    def load_enabled(self) -> None:
        """Import and register tools for all enabled plugins."""
        for plugin in self._discovered:
            if self.is_enabled(plugin.manifest.name):
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
        for plugin in self._discovered:
            if not self.is_enabled(plugin.manifest.name):
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
        for plugin in self._discovered:
            name = plugin.manifest.name
            if not self.is_enabled(name):
                continue
            if not plugin.manifest.mcp_servers:
                continue
            for mcp_cfg in plugin.manifest.mcp_servers:
                if not self.is_service_enabled(name, mcp_cfg.name):
                    logger.debug("Plugin %s: MCP server %s skipped (disabled)", name, mcp_cfg.name)
                    continue
                url, url_source = self.resolve_mcp_url(name, mcp_cfg)
                if not url:
                    logger.warning(
                        "Plugin %s: MCP server %s skipped (no URL in env or local settings)",
                        name, mcp_cfg.name,
                    )
                    continue
                headers, _ = self.resolve_mcp_headers(name, mcp_cfg)
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
                        logger.info("Plugin %s: MCP server %s connected (source: %s)", name, mcp_cfg.name, url_source)
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
    from src.plugin_settings_provider import LocalJSONProvider
    settings_dir = Path(state_file).parent / "plugin-settings"
    provider = LocalJSONProvider(settings_dir)
    _manager = PluginManager(plugins_dir=plugins_dir, state_file=state_file, provider=provider)
    _manager.discover()
    _manager.load_enabled()
    return _manager
