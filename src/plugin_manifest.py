"""Plugin manifest — parses and validates plugin.yaml files."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Literal, Optional

import yaml
from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)


class ManifestError(ValueError):
    """Raised when a plugin.yaml is missing, unreadable, or invalid."""


class PluginMcpServer(BaseModel):
    """Declares an HTTP MCP server that this plugin requires.

    url_env: name of the env var holding the server URL.
    headers_env: optional env var holding a JSON dict of static request headers.
    transport: "http" (default) or "sse".
    """
    name: str
    url_env: str
    headers_env: Optional[str] = None
    description: str = ""
    transport: str = "http"
    settings: Optional[List["PluginSetting"]] = None


class PluginRequirement(BaseModel):
    """One entry in the plugin's requires: list."""
    env: Optional[str] = None
    integration: Optional[str] = None

    @model_validator(mode='after')
    def at_least_one_set(self) -> 'PluginRequirement':
        if self.env is None and self.integration is None:
            raise ValueError("PluginRequirement must specify 'env' or 'integration'")
        return self


class _SettingOption(BaseModel):
    value: str
    label: str


class PluginSetting(BaseModel):
    """One entry in the plugin's settings: list."""
    key: str
    label: str
    type: Literal["select", "url", "text", "toggle"]
    options: Optional[List[_SettingOption]] = None
    default: Optional[Any] = None
    placeholder: Optional[str] = None
    env_hint: Optional[str] = None
    secret: bool = False


# Rebuild PluginMcpServer now that PluginSetting is defined (forward ref resolution)
PluginMcpServer.model_rebuild()


class PluginManifest(BaseModel):
    name: str
    guid: Optional[str] = None
    version: str
    addon_type: str = "mcp"
    min_odysseus_version: Optional[str] = None
    description: str = ""
    author: str = ""
    routes: Optional[str] = None   # relative path to routes module, e.g. "routes.py"
    tools: Optional[str] = None    # relative path to tools module, e.g. "tools.py"
    models: Optional[str] = None   # relative path to SQLAlchemy models module, e.g. "models.py"
    mcp_servers: Optional[List[PluginMcpServer]] = None  # HTTP MCP endpoints managed by this plugin
    requires: Optional[List[PluginRequirement]] = None
    settings: Optional[List[PluginSetting]] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v.strip()

    @field_validator("addon_type")
    @classmethod
    def addon_type_known(cls, v: str) -> str:
        known = {"mcp", "secrets_backend"}
        if v not in known:
            logger.warning("Unknown addon_type %r — plugin will load but may not function", v)
        return v

    @classmethod
    def from_path(cls, path: Path) -> "PluginManifest":
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ManifestError(f"Cannot read {path}: {e}") from e
        return cls.from_yaml_str(raw, source=str(path))

    @classmethod
    def from_yaml_str(cls, content: str, source: str = "<string>") -> "PluginManifest":
        """Parse a manifest from a YAML string (used for plugin import)."""
        try:
            data = yaml.safe_load(content) or {}
        except yaml.YAMLError as e:
            raise ManifestError(f"Invalid YAML in {source}: {e}") from e
        if not isinstance(data, dict):
            raise ManifestError(f"{source}: plugin.yaml must be a mapping")
        if "name" not in data:
            raise ManifestError(f"{source}: manifest missing required field 'name'")
        try:
            return cls(**data)
        except Exception as e:
            raise ManifestError(f"{source}: {e}") from e
