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
    # Provider-backed: per-plugin JSON file holds the enabled flag
    settings_file = tmp_path / "plugin-settings" / "plugin-a.json"
    assert settings_file.exists()
    saved = json.loads(settings_file.read_text())
    assert saved.get("enabled") is True


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
    assert p["settings_schema"] == [{"key": "my_key", "label": "My Key", "type": "text", "options": None, "default": None, "placeholder": None, "env_hint": None, "secret": False}]
    assert p["settings_values"] == {"my_key": "hello"}


def test_list_plugins_services_no_mcp_manager(tmp_path, monkeypatch):
    monkeypatch.delenv("SB_URL", raising=False)
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
    svc = p["services"][0]
    assert svc["name"] == "SB"
    assert svc["description"] == "SecondBrain vault"
    assert svc["status"] == "unconfigured"
    assert svc["tool_count"] == 0
    assert svc["url_configured"] is False
    assert svc["url_source"] == "unset"
    assert svc["enabled"] is True


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
    svc = p["services"][0]
    assert svc["name"] == "SB"
    assert svc["description"] == "SecondBrain vault"
    assert svc["status"] == "connected"
    assert svc["tool_count"] == 12
    assert svc["url_configured"] is True
    assert svc["url_source"] == "env"
    assert svc["enabled"] is True


def test_list_plugins_services_hyphenated_mcp_name(tmp_path, monkeypatch):
    """server_id must normalize hyphens/spaces in MCP server names, matching register_mcp_servers."""
    monkeypatch.setenv("MY_URL", "http://example.com")
    state_file = tmp_path / "plugins.json"
    plugin_dir = tmp_path / "my-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: my-plugin\nversion: 1.0.0\nmcp_servers:\n  - name: my-server\n    url_env: MY_URL\n    description: hyphen test\n"
    )
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file)
    mgr.discover()

    class FakeMCPManager:
        def get_server_status(self, server_id):
            # register_mcp_servers would produce builtin_http_plugin_my_plugin_my_server
            if server_id == "builtin_http_plugin_my_plugin_my_server":
                return {"status": "connected", "tool_count": 5}
            return {"status": "disconnected"}

    mgr.set_mcp_manager(FakeMCPManager())
    plugins = mgr.list_plugins()
    assert plugins[0]["services"][0]["status"] == "connected"


# ── Task 4: LocalJSONProvider, per-service enable/disable, URL resolution, migration ──

from src.plugin_settings_provider import LocalJSONProvider


def _make_mcp_plugin_full(tmp_path: Path, name: str, url_env: str = "SB_URL") -> Path:
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(textwrap.dedent(f"""\
        name: {name}
        version: 1.0.0
        guid: test-guid-{name}
        addon_type: mcp
        mcp_servers:
          - name: SB
            url_env: {url_env}
            headers_env: SB_HEADERS
            settings:
              - key: url
                label: Server URL
                type: url
                placeholder: http://change-me/mcp
              - key: headers
                label: Auth Headers
                type: text
                secret: true
    """))
    return plugin_dir


def _make_mgr(tmp_path: Path, name: str = "test-plugin") -> "PluginManager":
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    settings_dir = tmp_path / "plugin-settings"
    provider = LocalJSONProvider(settings_dir)
    state_file = tmp_path / "plugins.json"
    _make_mcp_plugin_full(plugins_dir, name)
    registry = PluginRegistry()
    mgr = PluginManager(
        plugins_dir=plugins_dir,
        state_file=state_file,
        registry=registry,
        provider=provider,
    )
    mgr.discover()
    return mgr


def test_service_enabled_by_default(tmp_path):
    mgr = _make_mgr(tmp_path)
    assert mgr.is_service_enabled("test-plugin", "SB") is True


def test_set_service_enabled_persists(tmp_path):
    mgr = _make_mgr(tmp_path)
    mgr.set_service_enabled("test-plugin", "SB", False)
    assert mgr.is_service_enabled("test-plugin", "SB") is False


def test_save_service_settings_and_read_back(tmp_path):
    mgr = _make_mgr(tmp_path)
    mgr.save_service_settings("test-plugin", "SB", {"url": "http://docker.local/mcp"})
    assert mgr.get_service_setting("test-plugin", "SB", "url") == "http://docker.local/mcp"


def test_resolve_mcp_url_prefers_env_over_local(tmp_path, monkeypatch):
    mgr = _make_mgr(tmp_path)
    monkeypatch.setenv("SB_URL", "http://from-env/mcp")
    mgr.save_service_settings("test-plugin", "SB", {"url": "http://local/mcp"})
    plugin = next(p for p in mgr._discovered if p.manifest.name == "test-plugin")
    srv = plugin.manifest.mcp_servers[0]
    url, source = mgr.resolve_mcp_url("test-plugin", srv)
    assert url == "http://from-env/mcp"
    assert source == "env"


def test_resolve_mcp_url_falls_back_to_local(tmp_path, monkeypatch):
    monkeypatch.delenv("SB_URL", raising=False)
    mgr = _make_mgr(tmp_path)
    mgr.save_service_settings("test-plugin", "SB", {"url": "http://local/mcp"})
    plugin = next(p for p in mgr._discovered if p.manifest.name == "test-plugin")
    srv = plugin.manifest.mcp_servers[0]
    url, source = mgr.resolve_mcp_url("test-plugin", srv)
    assert url == "http://local/mcp"
    assert source == "local"


def test_resolve_mcp_url_returns_unset_when_neither(tmp_path, monkeypatch):
    monkeypatch.delenv("SB_URL", raising=False)
    mgr = _make_mgr(tmp_path)
    plugin = next(p for p in mgr._discovered if p.manifest.name == "test-plugin")
    srv = plugin.manifest.mcp_servers[0]
    url, source = mgr.resolve_mcp_url("test-plugin", srv)
    assert url is None
    assert source == "unset"


def test_resolve_mcp_headers_parses_json_from_env(tmp_path, monkeypatch):
    mgr = _make_mgr(tmp_path)
    monkeypatch.setenv("SB_HEADERS", '{"Authorization": "Bearer tok"}')
    plugin = next(p for p in mgr._discovered if p.manifest.name == "test-plugin")
    srv = plugin.manifest.mcp_servers[0]
    headers, source = mgr.resolve_mcp_headers("test-plugin", srv)
    assert headers == {"Authorization": "Bearer tok"}
    assert source == "env"


def test_migration_from_legacy_plugins_json(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _make_mcp_plugin_full(plugins_dir, "alpha")
    state_file = tmp_path / "plugins.json"
    state_file.write_text(json.dumps({
        "enabled": ["alpha"],
        "settings": {"alpha": {"research_sink": "local"}},
    }))
    settings_dir = tmp_path / "plugin-settings"
    provider = LocalJSONProvider(settings_dir)
    mgr = PluginManager(
        plugins_dir=plugins_dir,
        state_file=state_file,
        registry=PluginRegistry(),
        provider=provider,
    )
    mgr.discover()

    assert not state_file.exists()
    assert (tmp_path / "plugins.json.migrated").exists()
    loaded = provider.load("alpha")
    assert loaded["enabled"] is True
    assert loaded["settings"]["research_sink"] == "local"


def test_list_plugins_includes_guid_and_addon_type(tmp_path):
    mgr = _make_mgr(tmp_path)
    plugins = mgr.list_plugins()
    p = next(x for x in plugins if x["name"] == "test-plugin")
    assert p["guid"] == "test-guid-test-plugin"
    assert p["addon_type"] == "mcp"


def test_list_plugins_service_url_source_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("SB_URL", raising=False)
    mgr = _make_mgr(tmp_path)
    plugins = mgr.list_plugins()
    svc = next(x for x in plugins if x["name"] == "test-plugin")["services"][0]
    assert svc["url_source"] == "unset"
    assert svc["enabled"] is True


def test_list_plugins_service_url_source_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SB_URL", "http://env/mcp")
    mgr = _make_mgr(tmp_path)
    plugins = mgr.list_plugins()
    svc = next(x for x in plugins if x["name"] == "test-plugin")["services"][0]
    assert svc["url_source"] == "env"
