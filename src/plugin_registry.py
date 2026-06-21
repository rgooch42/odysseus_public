"""Runtime registry for plugin-contributed tools and routers.

A single module-level instance (_registry) is used as the default.
Tests create their own PluginRegistry() instances with .clear() between runs.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self) -> None:
        # plugin_name -> list of OpenAI-style tool schema dicts
        self._schemas: Dict[str, List[dict]] = {}
        # tool_name -> async callable
        self._impls: Dict[str, Callable] = {}

    def register_tool_schemas(self, plugin_name: str, schemas: List[dict]) -> None:
        self._schemas[plugin_name] = schemas
        for s in schemas:
            name = s.get("function", {}).get("name", "?")
            logger.debug("Plugin %s registered tool schema: %s", plugin_name, name)

    def register_tool_implementation(self, tool_name: str, impl: Callable) -> None:
        self._impls[tool_name] = impl

    def get_all_tool_schemas(self) -> List[dict]:
        result = []
        for schemas in self._schemas.values():
            result.extend(schemas)
        return result

    def get_tool_implementation(self, tool_name: str) -> Optional[Callable]:
        return self._impls.get(tool_name)

    def clear(self) -> None:
        self._schemas.clear()
        self._impls.clear()


# Module-level singleton used by the rest of the app.
_registry = PluginRegistry()


def get_registry() -> PluginRegistry:
    return _registry
