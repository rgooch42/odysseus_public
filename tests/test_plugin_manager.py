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


from src.tool_execution import _execute_tool_block_impl
from src.agent_tools import ToolBlock
from src.plugin_registry import get_registry


async def test_plugin_tool_dispatches_via_tool_execution():
    """A plugin-registered tool should be callable via execute_tool_block."""
    registry = get_registry()
    registry.clear()

    async def do_greet(content, owner=None):
        return {"greeting": f"hello {content}"}

    registry.register_tool_implementation("greet", do_greet)

    block = ToolBlock(tool_type="greet", content="world")
    desc, result = await _execute_tool_block_impl(block)
    assert result.get("greeting") == "hello world"
    registry.clear()
