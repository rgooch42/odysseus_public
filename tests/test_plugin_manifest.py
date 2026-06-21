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
