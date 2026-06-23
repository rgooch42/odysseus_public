"""Tests for LocalJSONProvider and SettingsProvider protocol."""
import json
from pathlib import Path
import pytest
from src.plugin_settings_provider import LocalJSONProvider, SettingsProvider


def test_load_returns_empty_when_file_missing(tmp_path):
    provider = LocalJSONProvider(tmp_path / "settings")
    assert provider.load("my-plugin") == {}


def test_save_creates_file_and_load_reads_it(tmp_path):
    provider = LocalJSONProvider(tmp_path / "settings")
    data = {"enabled": True, "services": {}, "settings": {"key": "val"}}
    provider.save("my-plugin", data)
    assert provider.load("my-plugin") == data


def test_save_creates_directory_if_missing(tmp_path):
    settings_dir = tmp_path / "deep" / "nested" / "settings"
    provider = LocalJSONProvider(settings_dir)
    provider.save("my-plugin", {"enabled": False})
    assert settings_dir.is_dir()


def test_save_writes_pretty_json(tmp_path):
    provider = LocalJSONProvider(tmp_path)
    provider.save("p", {"enabled": True})
    raw = (tmp_path / "p.json").read_text()
    assert "\n" in raw  # pretty-printed


def test_load_returns_empty_on_corrupt_json(tmp_path):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    (settings_dir / "my-plugin.json").write_text("not json !!")
    provider = LocalJSONProvider(settings_dir)
    assert provider.load("my-plugin") == {}


def test_save_overwrites_existing_file(tmp_path):
    provider = LocalJSONProvider(tmp_path)
    provider.save("p", {"enabled": False})
    provider.save("p", {"enabled": True})
    assert provider.load("p")["enabled"] is True


def test_separate_plugins_do_not_collide(tmp_path):
    provider = LocalJSONProvider(tmp_path)
    provider.save("alpha", {"x": 1})
    provider.save("beta", {"x": 2})
    assert provider.load("alpha")["x"] == 1
    assert provider.load("beta")["x"] == 2


def test_local_json_provider_implements_protocol(tmp_path):
    provider = LocalJSONProvider(tmp_path)
    assert isinstance(provider, SettingsProvider)
