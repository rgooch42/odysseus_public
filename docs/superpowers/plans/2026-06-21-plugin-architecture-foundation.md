# Plugin Architecture Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a plugin system to Odysseus that auto-discovers plugins in a `plugins/` directory, registers their routes and tools at startup, and exposes per-plugin on/off toggles in Settings.

**Architecture:** Each plugin lives in `plugins/<name>/` with a `plugin.yaml` manifest and optional `routes.py` / `tools.py`. A singleton `PluginManager` (initialized in `app.py`) discovers enabled plugins, wires their FastAPI routers into the app, and registers their tool schemas and implementations into a shared `PluginRegistry`. `agent_loop.py` appends plugin schemas to `all_tool_schemas`; `tool_execution.py` falls through to the registry when a tool name is unknown to the built-in dispatch. Plugin enabled/disabled state persists in `data/plugins.json`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, PyYAML (already a dep via requirements.txt), vanilla JS for the settings panel.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `core/constants.py` | Modify | Add `PLUGINS_DIR`, `PLUGINS_STATE_FILE` |
| `src/plugin_manifest.py` | Create | Pydantic model for `plugin.yaml` |
| `src/plugin_registry.py` | Create | Runtime store: tool schemas + implementations, routes |
| `src/plugin_manager.py` | Create | Discover, load, enable/disable plugins |
| `routes/plugin_settings_routes.py` | Create | `/api/plugins` list/enable/disable endpoints |
| `src/tool_execution.py` | Modify (line ~581) | Fallback dispatch to plugin registry |
| `src/agent_loop.py` | Modify (line ~2329) | Append plugin schemas to `all_tool_schemas` |
| `app.py` | Modify (end of router registration block) | Init `PluginManager`, register routes |
| `static/js/plugin-settings.js` | Create | Settings toggle UI |
| `plugins/__init__.py` | Create | Empty — marks directory |
| `plugins/example/plugin.yaml` | Create | Reference plugin manifest |
| `plugins/example/tools.py` | Create | Reference plugin tools |
| `tests/test_plugin_manifest.py` | Create | Manifest parsing tests |
| `tests/test_plugin_manager.py` | Create | Manager discover/load/enable tests |
| `tests/test_plugin_settings_routes.py` | Create | API endpoint tests |

---

## Task 1: Constants

**Files:**
- Modify: `core/constants.py:24` (after `FEATURES_FILE` line)

- [ ] **Step 1.1: Add constants**

Open `core/constants.py`. After the `FEATURES_FILE` line, add:

```python
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")
PLUGINS_STATE_FILE = os.path.join(DATA_DIR, "plugins.json")
```

- [ ] **Step 1.2: Verify import**

```bash
python -c "from core.constants import PLUGINS_DIR, PLUGINS_STATE_FILE; print(PLUGINS_DIR, PLUGINS_STATE_FILE)"
```

Expected: two paths printed, no error.

- [ ] **Step 1.3: Commit**

```bash
git add core/constants.py
git commit -m "feat(plugins): add PLUGINS_DIR and PLUGINS_STATE_FILE constants"
```

---

## Task 2: Plugin Manifest Model

**Files:**
- Create: `src/plugin_manifest.py`
- Create: `tests/test_plugin_manifest.py`

- [ ] **Step 2.1: Write the failing tests**

Create `tests/test_plugin_manifest.py`:

```python
"""Tests for plugin manifest parsing."""
import textwrap
import pytest
from src.plugin_manifest import PluginManifest, ManifestError


def test_minimal_valid_manifest(tmp_path):
    yaml_text = textwrap.dedent("""\
        name: my-plugin
        version: 1.0.0
        description: A test plugin
    """)
    (tmp_path / "plugin.yaml").write_text(yaml_text)
    manifest = PluginManifest.from_path(tmp_path / "plugin.yaml")
    assert manifest.name == "my-plugin"
    assert manifest.version == "1.0.0"
    assert manifest.routes is None
    assert manifest.tools is None


def test_full_manifest(tmp_path):
    yaml_text = textwrap.dedent("""\
        name: full-plugin
        version: 2.1.0
        description: Full featured plugin
        author: rgooch42
        routes: routes.py
        tools: tools.py
    """)
    (tmp_path / "plugin.yaml").write_text(yaml_text)
    manifest = PluginManifest.from_path(tmp_path / "plugin.yaml")
    assert manifest.routes == "routes.py"
    assert manifest.tools == "tools.py"
    assert manifest.author == "rgooch42"


def test_missing_name_raises(tmp_path):
    yaml_text = "version: 1.0.0\ndescription: Missing name\n"
    (tmp_path / "plugin.yaml").write_text(yaml_text)
    with pytest.raises(ManifestError, match="name"):
        PluginManifest.from_path(tmp_path / "plugin.yaml")


def test_invalid_yaml_raises(tmp_path):
    (tmp_path / "plugin.yaml").write_text(":\nbroken: [yaml\n")
    with pytest.raises(ManifestError):
        PluginManifest.from_path(tmp_path / "plugin.yaml")
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_plugin_manifest.py -v
```

Expected: 4 errors — `ModuleNotFoundError: No module named 'src.plugin_manifest'`

- [ ] **Step 2.3: Write the implementation**

Create `src/plugin_manifest.py`:

```python
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
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_plugin_manifest.py -v
```

Expected: 4 PASSED.

- [ ] **Step 2.5: Commit**

```bash
git add src/plugin_manifest.py tests/test_plugin_manifest.py
git commit -m "feat(plugins): add PluginManifest model with YAML parsing"
```

---

## Task 3: Plugin Registry

**Files:**
- Create: `src/plugin_registry.py`
- Create: `tests/test_plugin_registry.py`

- [ ] **Step 3.1: Write the failing tests**

Create `tests/test_plugin_registry.py`:

```python
"""Tests for the plugin tool/route registry."""
import pytest
from src.plugin_registry import PluginRegistry


@pytest.fixture()
def registry():
    r = PluginRegistry()
    yield r
    r.clear()


def test_register_and_retrieve_tool_schemas(registry):
    schema = {"type": "function", "function": {"name": "my_tool", "description": "test"}}
    registry.register_tool_schemas("my-plugin", [schema])
    schemas = registry.get_all_tool_schemas()
    assert any(s["function"]["name"] == "my_tool" for s in schemas)


def test_register_and_dispatch_tool_impl(registry):
    async def do_my_tool(content, owner=None):
        return {"result": "ok"}

    registry.register_tool_implementation("my_tool", do_my_tool)
    impl = registry.get_tool_implementation("my_tool")
    assert impl is do_my_tool


def test_unknown_tool_returns_none(registry):
    assert registry.get_tool_implementation("nonexistent") is None


def test_clear_removes_all(registry):
    schema = {"type": "function", "function": {"name": "temp_tool", "description": "x"}}
    registry.register_tool_schemas("p", [schema])
    registry.clear()
    assert registry.get_all_tool_schemas() == []


def test_duplicate_plugin_registration_replaces(registry):
    s1 = {"type": "function", "function": {"name": "tool_a", "description": "v1"}}
    s2 = {"type": "function", "function": {"name": "tool_a", "description": "v2"}}
    registry.register_tool_schemas("p", [s1])
    registry.register_tool_schemas("p", [s2])
    schemas = registry.get_all_tool_schemas()
    descs = [s["function"]["description"] for s in schemas if s["function"]["name"] == "tool_a"]
    assert descs == ["v2"]
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_plugin_registry.py -v
```

Expected: 5 errors — `ModuleNotFoundError: No module named 'src.plugin_registry'`

- [ ] **Step 3.3: Write the implementation**

Create `src/plugin_registry.py`:

```python
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
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_plugin_registry.py -v
```

Expected: 5 PASSED.

- [ ] **Step 3.5: Commit**

```bash
git add src/plugin_registry.py tests/test_plugin_registry.py
git commit -m "feat(plugins): add PluginRegistry for tool schemas and implementations"
```

---

## Task 4: Plugin Manager

**Files:**
- Create: `src/plugin_manager.py`
- Create: `tests/test_plugin_manager.py`
- Create: `plugins/__init__.py`

- [ ] **Step 4.1: Write the failing tests**

Create `tests/test_plugin_manager.py`:

```python
"""Tests for PluginManager discovery and lifecycle."""
import json
import textwrap
from pathlib import Path
import pytest
from src.plugin_manager import PluginManager
from src.plugin_registry import PluginRegistry


def _make_plugin(tmp_path: Path, name: str, has_tools: bool = False) -> Path:
    plugin_dir = tmp_path / name
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        textwrap.dedent(f"""\
            name: {name}
            version: 1.0.0
            description: Test plugin {name}
            {"tools: tools.py" if has_tools else ""}
        """)
    )
    if has_tools:
        (plugin_dir / "tools.py").write_text(textwrap.dedent("""\
            TOOL_SCHEMAS = [{"type": "function", "function": {"name": "test_tool", "description": "x", "parameters": {"type": "object", "properties": {}}}}]
            TOOL_IMPLEMENTATIONS = {}
        """))
    return plugin_dir


def test_discover_finds_plugins(tmp_path):
    state_file = tmp_path / "plugins.json"
    _make_plugin(tmp_path, "plugin-a")
    _make_plugin(tmp_path, "plugin-b")
    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file, registry=registry)
    names = [p.manifest.name for p in mgr.discover()]
    assert "plugin-a" in names
    assert "plugin-b" in names


def test_plugin_disabled_by_default(tmp_path):
    state_file = tmp_path / "plugins.json"
    _make_plugin(tmp_path, "plugin-a")
    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file, registry=registry)
    mgr.discover()
    assert not mgr.is_enabled("plugin-a")


def test_enable_and_disable(tmp_path):
    state_file = tmp_path / "plugins.json"
    _make_plugin(tmp_path, "plugin-a")
    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file, registry=registry)
    mgr.discover()
    mgr.set_enabled("plugin-a", True)
    assert mgr.is_enabled("plugin-a")
    mgr.set_enabled("plugin-a", False)
    assert not mgr.is_enabled("plugin-a")


def test_enable_persists_to_state_file(tmp_path):
    state_file = tmp_path / "plugins.json"
    _make_plugin(tmp_path, "plugin-a")
    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file, registry=registry)
    mgr.discover()
    mgr.set_enabled("plugin-a", True)
    saved = json.loads(state_file.read_text())
    assert "plugin-a" in saved.get("enabled", [])


def test_load_enabled_registers_tools(tmp_path):
    state_file = tmp_path / "plugins.json"
    state_file.write_text(json.dumps({"enabled": ["plugin-a"]}))
    _make_plugin(tmp_path, "plugin-a", has_tools=True)
    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file, registry=registry)
    mgr.discover()
    mgr.load_enabled()
    schemas = registry.get_all_tool_schemas()
    assert any(s["function"]["name"] == "test_tool" for s in schemas)


def test_directory_without_manifest_is_skipped(tmp_path):
    state_file = tmp_path / "plugins.json"
    (tmp_path / "not-a-plugin").mkdir()
    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file, registry=registry)
    plugins = mgr.discover()
    assert len(plugins) == 0
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_plugin_manager.py -v
```

Expected: 6 errors — `ModuleNotFoundError: No module named 'src.plugin_manager'`

- [ ] **Step 4.3: Write the implementation**

Create `src/plugin_manager.py`:

```python
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
```

- [ ] **Step 4.4: Create `plugins/__init__.py`**

```bash
mkdir -p plugins
touch plugins/__init__.py
```

- [ ] **Step 4.5: Run tests to verify they pass**

```bash
python -m pytest tests/test_plugin_manager.py -v
```

Expected: 6 PASSED.

- [ ] **Step 4.6: Commit**

```bash
git add src/plugin_manager.py plugins/__init__.py tests/test_plugin_manager.py
git commit -m "feat(plugins): add PluginManager with discover/load/enable lifecycle"
```

---

## Task 5: Plugin Settings Routes

**Files:**
- Create: `routes/plugin_settings_routes.py`
- Create: `tests/test_plugin_settings_routes.py`

- [ ] **Step 5.1: Write the failing tests**

Create `tests/test_plugin_settings_routes.py`:

```python
"""Tests for the plugin settings API endpoints."""
import json
import textwrap
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from routes.plugin_settings_routes import setup_plugin_settings_routes
from src.plugin_manager import PluginManager
from src.plugin_registry import PluginRegistry


def _make_app(tmp_path: Path) -> tuple[TestClient, PluginManager]:
    """Build a minimal FastAPI app with plugin settings routes."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    state_file = tmp_path / "plugins.json"

    # Create one test plugin
    plugin_dir = plugins_dir / "test-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(textwrap.dedent("""\
        name: test-plugin
        version: 1.0.0
        description: A test plugin
    """))

    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=plugins_dir, state_file=state_file, registry=registry)
    mgr.discover()

    app = FastAPI()
    app.include_router(setup_plugin_settings_routes(mgr))
    return TestClient(app), mgr


def test_list_plugins(tmp_path):
    client, _ = _make_app(tmp_path)
    resp = client.get("/api/plugins")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(p["name"] == "test-plugin" for p in data)


def test_plugin_disabled_by_default(tmp_path):
    client, _ = _make_app(tmp_path)
    resp = client.get("/api/plugins")
    plugin = next(p for p in resp.json() if p["name"] == "test-plugin")
    assert plugin["enabled"] is False


def test_enable_plugin(tmp_path):
    client, _ = _make_app(tmp_path)
    resp = client.post("/api/plugins/test-plugin/enable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_disable_plugin(tmp_path):
    client, mgr = _make_app(tmp_path)
    mgr.set_enabled("test-plugin", True)
    resp = client.post("/api/plugins/test-plugin/disable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_enable_unknown_plugin_404(tmp_path):
    client, _ = _make_app(tmp_path)
    resp = client.post("/api/plugins/nonexistent/enable")
    assert resp.status_code == 404
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
python -m pytest tests/test_plugin_settings_routes.py -v
```

Expected: 5 errors — `ModuleNotFoundError: No module named 'routes.plugin_settings_routes'`

- [ ] **Step 5.3: Write the implementation**

Create `routes/plugin_settings_routes.py`:

```python
"""Plugin enable/disable settings API."""
import logging
from fastapi import APIRouter, HTTPException
from src.plugin_manager import PluginManager

logger = logging.getLogger(__name__)


def setup_plugin_settings_routes(manager: PluginManager) -> APIRouter:
    router = APIRouter(prefix="/api/plugins", tags=["plugins"])

    @router.get("")
    async def list_plugins():
        return manager.list_plugins()

    @router.post("/{name}/enable")
    async def enable_plugin(name: str):
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        manager.set_enabled(name, True)
        return {"name": name, "enabled": True}

    @router.post("/{name}/disable")
    async def disable_plugin(name: str):
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        manager.set_enabled(name, False)
        return {"name": name, "enabled": False}

    return router
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_plugin_settings_routes.py -v
```

Expected: 5 PASSED.

- [ ] **Step 5.5: Commit**

```bash
git add routes/plugin_settings_routes.py tests/test_plugin_settings_routes.py
git commit -m "feat(plugins): add plugin settings API (list/enable/disable)"
```

---

## Task 6: Wire into app.py

**Files:**
- Modify: `app.py`

- [ ] **Step 6.1: Import and initialize PluginManager at startup**

In `app.py`, find the line `load_dotenv(encoding="utf-8-sig")` near the top and the lifespan/startup block. The plugin manager must be initialized after the app is created but before routes are registered.

Find the section where route registration begins (around line 574, where `include_router` calls start). Add **before** that block:

```python
# ── Plugin system ────────────────────────────────────────────────────────────
from pathlib import Path as _Path
from src.plugin_manager import init_manager as _init_plugin_manager
from core.constants import PLUGINS_DIR, PLUGINS_STATE_FILE
from routes.plugin_settings_routes import setup_plugin_settings_routes

_plugin_manager = _init_plugin_manager(
    plugins_dir=_Path(PLUGINS_DIR),
    state_file=_Path(PLUGINS_STATE_FILE),
)
app.include_router(setup_plugin_settings_routes(_plugin_manager))
```

Then at the **end** of the router registration block (after all other `include_router` calls), add:

```python
# Register routes from enabled plugins
_plugin_manager.register_routes(app)
```

- [ ] **Step 6.2: Verify app starts cleanly**

```bash
timeout 8 python app.py 2>&1 | head -20
```

Expected: Server startup messages, no `ImportError` or `AttributeError`. Plugin discovery message in logs (may show "0 plugins discovered" if `plugins/` is empty — that's correct).

- [ ] **Step 6.3: Commit**

```bash
git add app.py
git commit -m "feat(plugins): initialize PluginManager and register plugin routes at startup"
```

---

## Task 7: Wire plugin schemas into agent_loop.py

**Files:**
- Modify: `src/agent_loop.py` (around line 2329)

- [ ] **Step 7.1: Add plugin schema injection**

In `src/agent_loop.py`, find the two places that build `all_tool_schemas` (lines ~2329 and ~2335). Both branches assign:

```python
all_tool_schemas = base_schemas + _mcp_filtered
# or
all_tool_schemas = base_schemas + mcp_schemas
```

After each of those assignments, the list is filtered by `disabled_tools` at line ~2337. Import the registry and inject plugin schemas **before** that filter block.

Find the import block at the top of `agent_loop.py` (around line 33 where `FUNCTION_TOOL_SCHEMAS` is imported) and add:

```python
from src.plugin_registry import get_registry as _get_plugin_registry
```

Then find the `if disabled_tools:` block (around line 2336) and add plugin schemas **above** it:

```python
# Append plugin-contributed tool schemas
_plugin_schemas = _get_plugin_registry().get_all_tool_schemas()
if _plugin_schemas:
    all_tool_schemas = all_tool_schemas + _plugin_schemas
```

So the section reads:

```python
            else:
                base_schemas = FUNCTION_TOOL_SCHEMAS if _needs_admin else [
                    s for s in FUNCTION_TOOL_SCHEMAS
                    if s.get("function", {}).get("name") not in _ADMIN_SCHEMA_NAMES
                ]
                all_tool_schemas = base_schemas + mcp_schemas
            # Append plugin-contributed tool schemas
            _plugin_schemas = _get_plugin_registry().get_all_tool_schemas()
            if _plugin_schemas:
                all_tool_schemas = all_tool_schemas + _plugin_schemas
            if disabled_tools:
                all_tool_schemas = [
```

- [ ] **Step 7.2: Run existing agent_loop tests**

```bash
python -m pytest tests/test_agent_loop.py -v -x 2>/dev/null || python -m pytest tests/ -k "agent_loop" -v -x
```

Expected: All previously-passing tests still pass.

- [ ] **Step 7.3: Commit**

```bash
git add src/agent_loop.py
git commit -m "feat(plugins): inject plugin tool schemas into agent tool assembly"
```

---

## Task 8: Wire plugin dispatch into tool_execution.py

**Files:**
- Modify: `src/tool_execution.py`

- [ ] **Step 8.1: Add plugin fallback at end of _execute_tool_block_impl**

In `src/tool_execution.py`, find the end of `_execute_tool_block_impl` — the final `else` clause that returns an "unknown tool" error. It will look something like:

```python
    else:
        desc = f"{tool}: unknown"
        result = {"error": f"Unknown tool: {tool}", "exit_code": 1}
    return desc, result
```

Replace that final `else` block with a plugin registry lookup:

```python
    else:
        # Check plugin-registered tools before returning unknown
        from src.plugin_registry import get_registry as _get_plugin_registry
        _plugin_impl = _get_plugin_registry().get_tool_implementation(tool)
        if _plugin_impl is not None:
            try:
                result = await _plugin_impl(content, owner=owner)
                desc = f"{tool}: plugin"
            except Exception as e:
                desc = f"{tool}: plugin error"
                result = {"error": str(e), "exit_code": 1}
        else:
            desc = f"{tool}: unknown"
            result = {"error": f"Unknown tool: {tool}", "exit_code": 1}
    return desc, result
```

- [ ] **Step 8.2: Write a targeted test for plugin dispatch**

Add to `tests/test_plugin_manager.py` (append to the file):

```python
import asyncio
from src.tool_execution import _execute_tool_block_impl
from src.agent_tools import ToolBlock
from src.plugin_registry import get_registry


def test_plugin_tool_dispatches_via_tool_execution(tmp_path):
    """A plugin-registered tool should be callable via execute_tool_block."""
    registry = get_registry()
    registry.clear()

    async def do_greet(content, owner=None):
        return {"greeting": f"hello {content}"}

    registry.register_tool_implementation("greet", do_greet)

    block = ToolBlock(tool_type="greet", content="world")
    desc, result = asyncio.get_event_loop().run_until_complete(
        _execute_tool_block_impl(block)
    )
    assert result.get("greeting") == "hello world"
    registry.clear()
```

- [ ] **Step 8.3: Run the new test**

```bash
python -m pytest tests/test_plugin_manager.py::test_plugin_tool_dispatches_via_tool_execution -v
```

Expected: PASSED.

- [ ] **Step 8.4: Run full test suite to check for regressions**

```bash
python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20
```

Expected: Pass rate matches the pre-task baseline (no new failures).

- [ ] **Step 8.5: Commit**

```bash
git add src/tool_execution.py tests/test_plugin_manager.py
git commit -m "feat(plugins): dispatch unknown tools to plugin registry in tool_execution"
```

---

## Task 9: Settings UI

**Files:**
- Create: `static/js/plugin-settings.js`

- [ ] **Step 9.1: Create the plugin toggle panel**

Create `static/js/plugin-settings.js`:

```javascript
// Plugin management panel — loaded by the Settings page.
(function () {
  "use strict";

  async function fetchPlugins() {
    const resp = await fetch("/api/plugins");
    if (!resp.ok) return [];
    return resp.json();
  }

  async function setPluginEnabled(name, enabled) {
    const action = enabled ? "enable" : "disable";
    await fetch(`/api/plugins/${encodeURIComponent(name)}/${action}`, {
      method: "POST",
    });
  }

  function buildPluginRow(plugin) {
    const row = document.createElement("div");
    row.className = "plugin-row";
    row.style.cssText =
      "display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border,#333)";

    const info = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = plugin.name + " v" + plugin.version;
    const desc = document.createElement("div");
    desc.style.cssText = "font-size:0.85em;opacity:0.7;margin-top:2px";
    desc.textContent = plugin.description || "";
    if (plugin.author) {
      desc.textContent += " — by " + plugin.author;
    }
    info.appendChild(title);
    info.appendChild(desc);

    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = plugin.enabled;
    toggle.style.cssText = "width:20px;height:20px;cursor:pointer;margin-left:16px";
    toggle.addEventListener("change", async () => {
      await setPluginEnabled(plugin.name, toggle.checked);
    });

    row.appendChild(info);
    row.appendChild(toggle);
    return row;
  }

  async function renderPluginPanel(container) {
    container.innerHTML = "";
    const heading = document.createElement("h3");
    heading.textContent = "Plugins";
    heading.style.marginBottom = "12px";
    container.appendChild(heading);

    const plugins = await fetchPlugins();
    if (plugins.length === 0) {
      const empty = document.createElement("p");
      empty.style.opacity = "0.6";
      empty.textContent = "No plugins installed. Add a plugin directory under plugins/.";
      container.appendChild(empty);
      return;
    }
    plugins.forEach((p) => container.appendChild(buildPluginRow(p)));
  }

  // Mount when the settings panel is opened. Odysseus fires a custom event
  // "settings:open" or renders a section with data-section="plugins".
  // Fall back to injecting after DOMContentLoaded if neither is detected.
  function mount() {
    const existing = document.querySelector('[data-section="plugins"]');
    if (existing) {
      renderPluginPanel(existing);
      return;
    }
    // Fallback: append a section to the settings container if present.
    const settingsContainer = document.getElementById("settings-content");
    if (!settingsContainer) return;
    const section = document.createElement("div");
    section.setAttribute("data-section", "plugins");
    section.style.cssText = "margin-top:24px;padding-top:16px;border-top:1px solid var(--border,#333)";
    settingsContainer.appendChild(section);
    renderPluginPanel(section);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }

  // Re-render when settings panel reopens (Odysseus uses a custom event).
  document.addEventListener("settings:open", mount);
})();
```

- [ ] **Step 9.2: Verify the file is syntactically valid**

```bash
node --check static/js/plugin-settings.js
```

Expected: No output (no errors).

- [ ] **Step 9.3: Commit**

```bash
git add static/js/plugin-settings.js
git commit -m "feat(plugins): add plugin settings toggle panel (plugin-settings.js)"
```

---

## Task 10: Reference Plugin

**Files:**
- Create: `plugins/example/plugin.yaml`
- Create: `plugins/example/tools.py`
- Create: `plugins/example/__init__.py`

- [ ] **Step 10.1: Create the example plugin**

```bash
mkdir -p plugins/example
touch plugins/example/__init__.py
```

Create `plugins/example/plugin.yaml`:

```yaml
name: example-plugin
version: 1.0.0
description: Reference plugin showing the minimal structure for an Odysseus plugin.
author: community
tools: tools.py
```

Create `plugins/example/tools.py`:

```python
"""Example plugin tools — reference implementation.

TOOL_SCHEMAS: list of OpenAI-compatible function tool schema dicts.
TOOL_IMPLEMENTATIONS: dict mapping tool name -> async callable(content, owner=None) -> dict.
"""
from typing import Optional


async def do_plugin_ping(content: str, owner: Optional[str] = None) -> dict:
    """Echo content back — proves the plugin tool dispatch is working."""
    return {"pong": content or "hello from example-plugin"}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "plugin_ping",
            "description": "Example plugin tool. Echoes the input back as a pong. Remove this plugin when you no longer need the reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Any text to echo back"
                    }
                },
                "required": []
            }
        }
    }
]

TOOL_IMPLEMENTATIONS = {
    "plugin_ping": do_plugin_ping,
}
```

- [ ] **Step 10.2: Verify it is discoverable**

```bash
python -c "
from pathlib import Path
from src.plugin_manager import PluginManager
from src.plugin_registry import PluginRegistry
from core.constants import PLUGINS_DIR, PLUGINS_STATE_FILE
mgr = PluginManager(Path(PLUGINS_DIR), Path(PLUGINS_STATE_FILE), PluginRegistry())
plugins = mgr.discover()
print([p.manifest.name for p in plugins])
"
```

Expected: `['example-plugin']`

- [ ] **Step 10.3: Commit**

```bash
git add plugins/example/
git commit -m "feat(plugins): add example plugin as reference implementation"
```

---

## Task 11: Full Suite Regression Check and Push

- [ ] **Step 11.1: Run full test suite**

```bash
python -m pytest tests/ -x -q --tb=short 2>&1 | tail -30
```

Expected: Pass rate matches or exceeds baseline before this plan was started.

- [ ] **Step 11.2: Smoke-test app startup**

```bash
timeout 8 python app.py 2>&1 | grep -E "INFO|ERROR|Plugin" | head -20
```

Expected: App starts, plugin discovery logged, no errors.

- [ ] **Step 11.3: Push to odysseus_local and odysseus_public**

```bash
git push origin feat/http-mcp-builtin-headers-reconnect
git push public feat/http-mcp-builtin-headers-reconnect
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Plugin manifest format (YAML) → Task 2
- [x] Plugin loader/registry — discover at startup → Task 4
- [x] Route injection → Task 4 (`register_routes`) + Task 6 (app.py wiring)
- [x] Tool registration hook → Task 3 (registry) + Task 4 (load_enabled) + Task 8 (dispatch)
- [x] Plugin schemas in agent → Task 7
- [x] On/off toggle in Settings → Task 5 (API) + Task 9 (UI)
- [x] DB model registration → **intentionally deferred** (avoids touching core/database.py, the highest-risk file per architecture-runtime-inventory.md §6.1). Add in a follow-up plan.
- [x] UI panel injection (beyond toggle) → **deferred to Phase 2** per spec §5.1

**Deferred items (note for next plan):**
1. DB model registration for plugins (plugins declaring their own SQLAlchemy models)
2. Full UI panel injection (sidebar panels, dashboard widgets from plugins)
3. Settings namespace extension (per-plugin prefs)
4. `plugin-settings.js` integration point in `settings.js` or the settings HTML page — the JS file is created but not yet `<script>`-included. This needs a one-line addition to the settings HTML template (find the template and add `<script src="/static/js/plugin-settings.js"></script>`). Flagged here because the template path needs to be verified against the actual Odysseus settings page structure before adding.
