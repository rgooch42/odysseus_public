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


def test_mcp_servers_parsed(tmp_path):
    yaml_text = textwrap.dedent("""\
        name: mcp-plugin
        version: 1.0.0
        description: Plugin with MCP servers
        mcp_servers:
          - name: MY_SB
            url_env: MY_SB_URL
            headers_env: MY_SB_HEADERS
            description: SecondBrain
          - name: MY_SHELL
            url_env: MY_SHELL_URL
            transport: sse
    """)
    (tmp_path / "plugin.yaml").write_text(yaml_text)
    manifest = PluginManifest.from_path(tmp_path / "plugin.yaml")
    assert manifest.mcp_servers is not None
    assert len(manifest.mcp_servers) == 2
    sb = manifest.mcp_servers[0]
    assert sb.name == "MY_SB"
    assert sb.url_env == "MY_SB_URL"
    assert sb.headers_env == "MY_SB_HEADERS"
    assert sb.transport == "http"
    shell = manifest.mcp_servers[1]
    assert shell.name == "MY_SHELL"
    assert shell.transport == "sse"
    assert shell.headers_env is None


def test_mcp_servers_absent_is_none(tmp_path):
    yaml_text = "name: plain\nversion: 1.0.0\n"
    (tmp_path / "plugin.yaml").write_text(yaml_text)
    manifest = PluginManifest.from_path(tmp_path / "plugin.yaml")
    assert manifest.mcp_servers is None


def test_requires_env_parsed(tmp_path):
    yaml_text = textwrap.dedent("""\
        name: my-plugin
        version: 1.0.0
        requires:
          - env: MY_URL
          - env: MY_HEADERS
          - integration: vault
    """)
    (tmp_path / "plugin.yaml").write_text(yaml_text)
    manifest = PluginManifest.from_path(tmp_path / "plugin.yaml")
    assert manifest.requires is not None
    assert len(manifest.requires) == 3
    assert manifest.requires[0].env == "MY_URL"
    assert manifest.requires[0].integration is None
    assert manifest.requires[2].integration == "vault"
    assert manifest.requires[2].env is None


def test_settings_parsed(tmp_path):
    yaml_text = textwrap.dedent("""\
        name: my-plugin
        version: 1.0.0
        settings:
          - key: research_sink
            label: Research storage
            type: select
            options:
              - value: local
                label: Local
              - value: sb_mcp
                label: SecondBrain vault
            default: sb_mcp
          - key: chroma_url
            label: Vector DB endpoint
            type: url
            placeholder: http://docker.local:8000
            env_hint: CHROMADB_HOST
    """)
    (tmp_path / "plugin.yaml").write_text(yaml_text)
    manifest = PluginManifest.from_path(tmp_path / "plugin.yaml")
    assert manifest.settings is not None
    assert len(manifest.settings) == 2
    s = manifest.settings[0]
    assert s.key == "research_sink"
    assert s.type == "select"
    assert s.default == "sb_mcp"
    assert s.options is not None
    assert len(s.options) == 2
    u = manifest.settings[1]
    assert u.key == "chroma_url"
    assert u.env_hint == "CHROMADB_HOST"
    assert u.options is None


def test_requires_absent_is_none(tmp_path):
    (tmp_path / "plugin.yaml").write_text("name: p\nversion: 1.0.0\n")
    manifest = PluginManifest.from_path(tmp_path / "plugin.yaml")
    assert manifest.requires is None
    assert manifest.settings is None


def test_requires_empty_entry_raises(tmp_path):
    yaml_text = textwrap.dedent("""\
        name: my-plugin
        version: 1.0.0
        requires:
          - {}
    """)
    (tmp_path / "plugin.yaml").write_text(yaml_text)
    with pytest.raises(ManifestError):
        PluginManifest.from_path(tmp_path / "plugin.yaml")
