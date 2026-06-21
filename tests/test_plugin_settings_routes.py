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
