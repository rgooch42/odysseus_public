"""Tests for the plugin settings API endpoints."""
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


# ── Task 5: /settings and /refresh routes ────────────────────────────────────

YAML_WITH_SETTINGS = """\
name: my-plugin
version: 1.0.0
settings:
  - key: my_key
    label: My Key
    type: text
"""

YAML_NO_SETTINGS = """\
name: my-plugin
version: 1.0.0
"""


def _make_app_with_yaml(tmp_path: Path, yaml_content: str):
    """Build a minimal app with a single plugin defined by raw YAML."""
    from src.plugin_registry import PluginRegistry
    plugin_dir = tmp_path / "my-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(yaml_content)
    state_file = tmp_path / "state.json"
    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=tmp_path, state_file=state_file, registry=registry)
    mgr.discover()
    app = FastAPI()
    app.include_router(setup_plugin_settings_routes(mgr))
    return TestClient(app), mgr


def test_save_settings_ok(tmp_path):
    client, mgr = _make_app_with_yaml(tmp_path, YAML_WITH_SETTINGS)
    resp = client.post("/api/plugins/my-plugin/settings", json={"my_key": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "my-plugin"
    assert body["settings"]["my_key"] == "hello"
    assert mgr.get_plugin_setting("my-plugin", "my_key") == "hello"


def test_save_settings_unknown_key_returns_400(tmp_path):
    client, _ = _make_app_with_yaml(tmp_path, YAML_WITH_SETTINGS)
    resp = client.post("/api/plugins/my-plugin/settings", json={"bad_key": "x"})
    assert resp.status_code == 400
    assert "bad_key" in resp.json()["detail"]


def test_save_settings_plugin_not_found(tmp_path):
    client, _ = _make_app_with_yaml(tmp_path, YAML_WITH_SETTINGS)
    resp = client.post("/api/plugins/nonexistent/settings", json={})
    assert resp.status_code == 404


def test_save_settings_no_schema_rejects_body(tmp_path):
    client, _ = _make_app_with_yaml(tmp_path, YAML_NO_SETTINGS)
    resp = client.post("/api/plugins/my-plugin/settings", json={"some_key": "v"})
    assert resp.status_code == 400


def test_save_settings_no_schema_empty_body_ok(tmp_path):
    client, _ = _make_app_with_yaml(tmp_path, YAML_NO_SETTINGS)
    resp = client.post("/api/plugins/my-plugin/settings", json={})
    assert resp.status_code == 200


def test_refresh_plugin_ok(tmp_path):
    client, _ = _make_app_with_yaml(tmp_path, YAML_WITH_SETTINGS)
    resp = client.post("/api/plugins/my-plugin/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "my-plugin"
    assert "validation_status" in body
    assert "services" in body
    assert "settings_schema" in body
    assert "settings_values" in body


def test_refresh_plugin_not_found(tmp_path):
    client, _ = _make_app_with_yaml(tmp_path, YAML_WITH_SETTINGS)
    resp = client.post("/api/plugins/nonexistent/refresh")
    assert resp.status_code == 404


def _make_mcp_app(tmp_path: Path) -> tuple[TestClient, PluginManager]:
    """Build test app with a plugin that has an MCP server."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    state_file = tmp_path / "plugins.json"

    plugin_dir = plugins_dir / "mcp-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(textwrap.dedent("""\
        name: mcp-plugin
        version: 1.0.0
        description: A test plugin with MCP server
        mcp_servers:
          - name: SB
            url_env: SB_URL
            headers_env: SB_HEADERS
            description: Test MCP
            settings:
              - key: url
                label: Server URL
                type: url
                placeholder: http://change-me/mcp
              - key: headers
                label: Auth Headers
                type: text
                secret: true
        settings:
          - key: sink
            label: Sink
            type: text
    """))

    registry = PluginRegistry()
    mgr = PluginManager(plugins_dir=plugins_dir, state_file=state_file, registry=registry)
    mgr.discover()

    app = FastAPI()
    app.include_router(setup_plugin_settings_routes(mgr))
    return TestClient(app), mgr


# ── Task 6: New routes ────────────────────────────────────────────────────────

def test_enable_service(tmp_path):
    client, _ = _make_mcp_app(tmp_path)
    resp = client.post("/api/plugins/mcp-plugin/services/SB/enable")
    assert resp.status_code == 200
    assert resp.json() == {"name": "mcp-plugin", "service": "SB", "enabled": True}


def test_disable_service(tmp_path):
    client, _ = _make_mcp_app(tmp_path)
    resp = client.post("/api/plugins/mcp-plugin/services/SB/disable")
    assert resp.status_code == 200
    assert resp.json() == {"name": "mcp-plugin", "service": "SB", "enabled": False}


def test_enable_service_unknown_plugin_404(tmp_path):
    client, _ = _make_mcp_app(tmp_path)
    resp = client.post("/api/plugins/nope/services/SB/enable")
    assert resp.status_code == 404


def test_enable_service_unknown_service_404(tmp_path):
    client, _ = _make_mcp_app(tmp_path)
    resp = client.post("/api/plugins/mcp-plugin/services/NOPE/enable")
    assert resp.status_code == 404


def test_save_service_settings(tmp_path):
    client, mgr = _make_mcp_app(tmp_path)
    resp = client.post(
        "/api/plugins/mcp-plugin/services/SB/settings",
        json={"url": "http://docker.local/mcp"},
    )
    assert resp.status_code == 200
    assert resp.json()["settings"]["url"] == "http://docker.local/mcp"
    # Verify persisted
    assert mgr.get_service_setting("mcp-plugin", "SB", "url") == "http://docker.local/mcp"


def test_save_service_settings_unknown_key_400(tmp_path):
    client, _ = _make_mcp_app(tmp_path)
    resp = client.post(
        "/api/plugins/mcp-plugin/services/SB/settings",
        json={"unknown_key": "bad"},
    )
    assert resp.status_code == 400


def test_export_settings(tmp_path):
    client, mgr = _make_mcp_app(tmp_path)
    mgr.save_service_settings("mcp-plugin", "SB", {"url": "http://local/mcp"})
    resp = client.get("/api/plugins/mcp-plugin/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["services"]["SB"]["url"] == "http://local/mcp"


def test_export_strips_env_governed_url(tmp_path, monkeypatch):
    monkeypatch.setenv("SB_URL", "http://from-env/mcp")
    client, mgr = _make_mcp_app(tmp_path)
    mgr.save_service_settings("mcp-plugin", "SB", {"url": "http://local/mcp"})
    resp = client.get("/api/plugins/mcp-plugin/export")
    assert resp.status_code == 200
    svc = resp.json().get("services", {}).get("SB", {})
    assert "url" not in svc


def test_import_settings_merges(tmp_path):
    client, mgr = _make_mcp_app(tmp_path)
    resp = client.post(
        "/api/plugins/mcp-plugin/import-settings",
        json={
            "settings": {"sink": "local"},
            "services": {"SB": {"url": "http://imported/mcp"}},
        },
    )
    assert resp.status_code == 200
    assert mgr.get_service_setting("mcp-plugin", "SB", "url") == "http://imported/mcp"
    assert mgr.get_all_plugin_settings("mcp-plugin") == {"sink": "local"}


def test_import_settings_rejects_unknown_service(tmp_path):
    client, _ = _make_mcp_app(tmp_path)
    resp = client.post(
        "/api/plugins/mcp-plugin/import-settings",
        json={"services": {"NOPE": {"url": "x"}}},
    )
    assert resp.status_code == 400


def test_plugin_import_preview(tmp_path):
    client, _ = _make_mcp_app(tmp_path)
    yaml_content = textwrap.dedent("""\
        name: new-plugin
        version: 1.0.0
        addon_type: mcp
        description: Imported plugin
        author: tester
    """)
    resp = client.post(
        "/api/plugins/import",
        content=yaml_content.encode(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["manifest"]["name"] == "new-plugin"
    assert data["already_installed"] is False
    # Preview should NOT write to disk
    assert not (tmp_path / "plugins" / "new-plugin").exists()


def test_plugin_import_confirm_writes_to_disk(tmp_path):
    client, mgr = _make_mcp_app(tmp_path)
    yaml_content = textwrap.dedent("""\
        name: new-plugin
        version: 1.0.0
        addon_type: mcp
        description: Imported plugin
        author: tester
    """)
    resp = client.post(
        "/api/plugins/import?confirm=true",
        content=yaml_content.encode(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200
    plugin_dir = tmp_path / "plugins" / "new-plugin"
    assert plugin_dir.exists()
    assert (plugin_dir / "plugin.yaml").exists()
    data = resp.json()
    assert data["name"] == "new-plugin"
