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
