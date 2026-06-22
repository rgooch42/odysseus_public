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
