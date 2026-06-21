"""Plugin manifest — parses and validates plugin.yaml files."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)


class ManifestError(ValueError):
    """Raised when a plugin.yaml is missing, unreadable, or invalid."""


class PluginManifest(BaseModel):
    name: str
    version: str
    description: str = ""
    author: str = ""
    routes: Optional[str] = None   # relative path to routes module, e.g. "routes.py"
    tools: Optional[str] = None    # relative path to tools module, e.g. "tools.py"

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v.strip()

    @classmethod
    def from_path(cls, path: Path) -> "PluginManifest":
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ManifestError(f"Cannot read {path}: {e}") from e
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError as e:
            raise ManifestError(f"Invalid YAML in {path}: {e}") from e
        if not isinstance(data, dict):
            raise ManifestError(f"{path}: plugin.yaml must be a mapping")
        if "name" not in data:
            raise ManifestError(f"{path}: manifest missing required field 'name'")
        try:
            return cls(**data)
        except Exception as e:
            raise ManifestError(f"{path}: {e}") from e
