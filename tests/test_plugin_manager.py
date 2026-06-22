"""Tests for PluginManager discovery and lifecycle."""
import json
import os
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


def _make_plugin_with_requires(tmp_path, name, requires_yaml):
    plugin_dir = tmp_path / name
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        f"""\
name: {name}
version: 1.0.0
{requires_yaml}
"""
    )
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


import asyncio
from unittest.mock import AsyncMock, MagicMock, call
from src.tool_execution import _execute_tool_block_impl
from src.agent_tools import ToolBlock
from src.plugin_registry import get_registry


def _make_mcp_plugin(tmp_path: Path, name: str, extra_yaml: str = "") -> Path:
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(exist_ok=True)
    content = f"name: {name}\nversion: 1.0.0\ndescription: MCP test plugin\n"
    if extra_yaml:
        content += extra_yaml
    (plugin_dir / "plugin.yaml").write_text(content)
    return plugin_dir


@pytest.mark.asyncio
async def test_register_mcp_servers_calls_connect(tmp_path, monkeypatch):
    state_file = tmp_path / "plugins.json"
    state_file.write_text('{"enabled": ["mcp-plugin"]}')
    _make_mcp_plugin(tmp_path, "mcp-plugin",
        "mcp_servers:\n  - name: TEST_SB\n    url_env: TEST_SB_URL\n    headers_env: TEST_SB_HEADERS\n")
    monkeypatch.setenv("TEST_SB_URL", "http://test.local:9999/mcp")
    monkeypatch.setenv("TEST_SB_HEADERS", '{"Authorization": "Bearer tok"}')

    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file, registry=registry)
    mgr.discover()

    mock_mcp = AsyncMock()
    mock_mcp.connect_server = AsyncMock(return_value=True)
    await mgr.register_mcp_servers(mock_mcp)

    mock_mcp.connect_server.assert_called_once()
    kwargs = mock_mcp.connect_server.call_args.kwargs
    assert kwargs["url"] == "http://test.local:9999/mcp"
    assert kwargs["headers"] == {"Authorization": "Bearer tok"}
    assert kwargs["transport"] == "http"
    assert "builtin_http_plugin_mcp_plugin_test_sb" == kwargs["server_id"]


@pytest.mark.asyncio
async def test_register_mcp_servers_skips_missing_url(tmp_path, monkeypatch):
    state_file = tmp_path / "plugins.json"
    state_file.write_text('{"enabled": ["mcp-plugin"]}')
    _make_mcp_plugin(tmp_path, "mcp-plugin",
        "mcp_servers:\n  - name: MISSING_SERVER\n    url_env: UNSET_ENV_VAR_12345\n")
    monkeypatch.delenv("UNSET_ENV_VAR_12345", raising=False)

    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file, registry=registry)
    mgr.discover()

    mock_mcp = AsyncMock()
    mock_mcp.connect_server = AsyncMock(return_value=True)
    await mgr.register_mcp_servers(mock_mcp)

    mock_mcp.connect_server.assert_not_called()


@pytest.mark.asyncio
async def test_register_mcp_servers_skips_disabled_plugins(tmp_path, monkeypatch):
    state_file = tmp_path / "plugins.json"
    state_file.write_text('{"enabled": []}')
    _make_mcp_plugin(tmp_path, "mcp-plugin",
        "mcp_servers:\n  - name: SHOULD_NOT_CONNECT\n    url_env: TEST_SHOULD_NOT_CONNECT_URL\n")
    monkeypatch.setenv("TEST_SHOULD_NOT_CONNECT_URL", "http://should-not-connect.local/mcp")

    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file, registry=registry)
    mgr.discover()

    mock_mcp = AsyncMock()
    mock_mcp.connect_server = AsyncMock(return_value=True)
    await mgr.register_mcp_servers(mock_mcp)

    mock_mcp.connect_server.assert_not_called()


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


def test_get_plugin_setting_default(tmp_path):
    state_file = tmp_path / "plugins.json"
    _make_plugin(tmp_path, "plugin-a")
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    # Key not set — returns None
    assert mgr.get_plugin_setting("plugin-a", "research_sink") is None


def test_save_and_get_plugin_setting(tmp_path):
    state_file = tmp_path / "plugins.json"
    _make_plugin(tmp_path, "plugin-a")
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    mgr.save_plugin_settings("plugin-a", {"research_sink": "sb_mcp", "chroma_url": "http://host:8000"})
    assert mgr.get_plugin_setting("plugin-a", "research_sink") == "sb_mcp"
    assert mgr.get_plugin_setting("plugin-a", "chroma_url") == "http://host:8000"


def test_save_plugin_settings_persisted(tmp_path):
    state_file = tmp_path / "plugins.json"
    _make_plugin(tmp_path, "plugin-a")
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    mgr.save_plugin_settings("plugin-a", {"research_sink": "local"})
    # Fresh instance reads from disk
    mgr2 = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    assert mgr2.get_plugin_setting("plugin-a", "research_sink") == "local"


def test_save_plugin_settings_does_not_clobber_enabled(tmp_path):
    state_file = tmp_path / "plugins.json"
    _make_plugin(tmp_path, "plugin-a")
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    mgr.set_enabled("plugin-a", True)
    mgr.save_plugin_settings("plugin-a", {"key": "val"})
    # enabled list unchanged
    assert mgr.is_enabled("plugin-a")


def test_validate_requirements_all_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_URL", "http://example.com")
    state_file = tmp_path / "plugins.json"
    _make_plugin_with_requires(tmp_path, "req-plugin", "requires:\n  - env: MY_URL")
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    status, issues = mgr.validate_requirements(mgr._discovered[0])
    assert status == "ok"
    assert issues == []


def test_validate_requirements_missing_env_is_error(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    state_file = tmp_path / "plugins.json"
    _make_plugin_with_requires(tmp_path, "req-plugin", "requires:\n  - env: MISSING_VAR")
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    status, issues = mgr.validate_requirements(mgr._discovered[0])
    assert status == "error"
    assert any(i["type"] == "env_missing" and i["env"] == "MISSING_VAR" for i in issues)
    assert issues[0]["severity"] == "error"


def test_validate_requirements_missing_integration_is_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    state_file = tmp_path / "plugins.json"
    _make_plugin_with_requires(tmp_path, "req-plugin", "requires:\n  - integration: vault")
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    status, issues = mgr.validate_requirements(mgr._discovered[0])
    assert status == "warning"
    assert any(i["type"] == "integration_missing" and i["integration"] == "vault" for i in issues)
    assert issues[0]["severity"] == "warning"


def test_validate_requirements_no_requires_is_ok(tmp_path):
    state_file = tmp_path / "plugins.json"
    _make_plugin(tmp_path, "plain")
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    status, issues = mgr.validate_requirements(mgr._discovered[0])
    assert status == "ok"
    assert issues == []


def test_validate_worst_severity_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOD_VAR", "set")
    monkeypatch.delenv("BAD_VAR", raising=False)
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    state_file = tmp_path / "plugins.json"
    _make_plugin_with_requires(
        tmp_path, "req-plugin",
        "requires:\n  - env: GOOD_VAR\n  - env: BAD_VAR\n  - integration: vault"
    )
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    status, issues = mgr.validate_requirements(mgr._discovered[0])
    assert status == "error"   # error beats warning
    assert len(issues) == 2    # BAD_VAR (error) + vault (warning)


def test_list_plugins_includes_validation(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    state_file = tmp_path / "plugins.json"
    plugin_dir = tmp_path / "req-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: req-plugin\nversion: 1.0.0\nrequires:\n  - env: MISSING_VAR\n"
    )
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    plugins = mgr.list_plugins()
    assert len(plugins) == 1
    p = plugins[0]
    assert p["validation_status"] == "error"
    assert any(i["env"] == "MISSING_VAR" for i in p["validation_issues"])


def test_list_plugins_includes_settings_schema_and_values(tmp_path):
    state_file = tmp_path / "plugins.json"
    plugin_dir = tmp_path / "cfg-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: cfg-plugin\nversion: 1.0.0\nsettings:\n  - key: my_key\n    label: My Key\n    type: text\n"
    )
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    mgr.save_plugin_settings("cfg-plugin", {"my_key": "hello"})
    plugins = mgr.list_plugins()
    p = plugins[0]
    assert p["settings_schema"] == [{"key": "my_key", "label": "My Key", "type": "text", "options": None, "default": None, "placeholder": None, "env_hint": None}]
    assert p["settings_values"] == {"my_key": "hello"}


def test_list_plugins_services_no_mcp_manager(tmp_path):
    state_file = tmp_path / "plugins.json"
    plugin_dir = tmp_path / "svc-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: svc-plugin\nversion: 1.0.0\nmcp_servers:\n  - name: SB\n    url_env: SB_URL\n    description: SecondBrain vault\n"
    )
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()
    plugins = mgr.list_plugins()
    p = plugins[0]
    assert p["services"] == [
        {"name": "SB", "description": "SecondBrain vault", "status": "unconfigured", "tool_count": 0, "url_configured": False}
    ]


def test_list_plugins_services_with_mcp_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("SB_URL", "http://example.com")
    state_file = tmp_path / "plugins.json"
    plugin_dir = tmp_path / "svc-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: svc-plugin\nversion: 1.0.0\nmcp_servers:\n  - name: SB\n    url_env: SB_URL\n    description: SecondBrain vault\n"
    )
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()

    class FakeMCPManager:
        def get_server_status(self, server_id):
            if server_id == "builtin_http_plugin_svc_plugin_sb":
                return {"status": "connected", "tool_count": 12}
            return {"status": "disconnected"}

    mgr.set_mcp_manager(FakeMCPManager())
    plugins = mgr.list_plugins()
    p = plugins[0]
    assert p["services"] == [
        {"name": "SB", "description": "SecondBrain vault", "status": "connected", "tool_count": 12, "url_configured": True}
    ]
