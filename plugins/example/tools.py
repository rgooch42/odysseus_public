"""Example plugin tools — reference implementation.

TOOL_SCHEMAS: list of OpenAI-compatible function tool schema dicts.
TOOL_IMPLEMENTATIONS: dict mapping tool name -> async callable(content, owner=None) -> dict.
"""
from typing import Optional


async def do_plugin_ping(content: str, owner: Optional[str] = None) -> dict:
    """Echo content back — proves the plugin tool dispatch is working."""
    return {"pong": content or "hello from example-plugin"}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "plugin_ping",
            "description": "Example plugin tool. Echoes the input back as a pong. Remove this plugin when you no longer need the reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Any text to echo back"
                    }
                },
                "required": []
            }
        }
    }
]

TOOL_IMPLEMENTATIONS = {
    "plugin_ping": do_plugin_ping,
}
