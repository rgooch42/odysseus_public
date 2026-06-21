"""
builtin_mcp.py

Auto-registration of built-in MCP servers on startup.
Each server runs as a stdio subprocess managed by McpManager.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from typing import Dict

from core.platform_compat import IS_WINDOWS, which_tool
from src.runtime_paths import get_app_root

logger = logging.getLogger(__name__)


def _find_npx() -> str:
    """Find the npx binary, checking common locations if not on PATH.

    On Windows the shim is `npx.cmd`, which `which_tool` resolves via PATHEXT.
    """
    npx = which_tool("npx")
    if npx:
        return npx
    if IS_WINDOWS:
        # Minimal-PATH fallbacks: npm's global bin lives under %APPDATA%\npm,
        # and node's installer dir carries npx.cmd alongside node.exe.
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        for candidate in (
            os.path.join(appdata, "npm", "npx.cmd"),
            r"C:\Program Files\nodejs\npx.cmd",
        ):
            if os.path.isfile(candidate):
                return candidate
        node = which_tool("node")
        if node:
            cand = os.path.join(os.path.dirname(node), "npx.cmd")
            if os.path.isfile(cand):
                return cand
        return "npx.cmd"  # fallback, will fail with a clear error
    # Common POSIX locations when PATH is minimal (e.g. systemd)
    for candidate in [
        os.path.expanduser("~/.npm-global/bin/npx"),
        os.path.expanduser("~/.local/bin/npx"),
        "/usr/local/bin/npx",
        "/usr/bin/npx",
    ]:
        if os.path.isfile(candidate):
            return candidate
    # Try to find node and use npx from same dir
    node = shutil.which("node")
    if node:
        npx_candidate = os.path.join(os.path.dirname(node), "npx")
        if os.path.isfile(npx_candidate):
            return npx_candidate
    return "npx"  # fallback, will fail with a clear error

# Server definitions: id -> (script path relative to project root, display name)
#
# bash / python / filesystem / web_search were folded into native in-process
# execution (src/tool_execution.py:_direct_fallback). Those trivial subprocess
# wrappers are gone.
#
# image_gen / memory / rag / email still run as stdio MCP servers — each
# carries hundreds of LOC of unique IMAP / HTTP / manager logic not worth
# duplicating into the native path right now.
_BUILTIN_SERVERS = {
    "image_gen":  ("mcp_servers/image_gen_server.py",  "Built-in: Image Generation"),
    "memory":     ("mcp_servers/memory_server.py",     "Built-in: Memory"),
    "rag":        ("mcp_servers/rag_server.py",        "Built-in: RAG"),
    "email":      ("mcp_servers/email_server.py",      "Built-in: Email"),
}

# NPX-based built-in servers (run via npx, not Python)
_BUILTIN_NPX_SERVERS = {
    "builtin_browser": {
        "name": "Built-in: Browser",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest", "--headless", "--caps", "vision"],
    }
}

# HTTP MCP servers auto-connected at startup when their URL env var is set.
# Each entry maps a stable server_id to the env var that holds the URL and an
# optional env var that holds a static bearer token.
#
# server_id MUST start with "builtin_http_" so McpManager.is_builtin() recognises
# them and McpManager._is_external_builtin() includes their tools in the LLM prompt.
#
# Add an entry here and the matching ODYSSEUS_MCP_*_URL var to .env to wire up a
# new HTTP MCP endpoint with zero code beyond this dict.
_BUILTIN_HTTP_SERVERS: Dict[str, Dict[str, str]] = {
    "builtin_http_sb": {
        "name": "Built-in: SecondBrain (sb)",
        "url_env": "ODYSSEUS_MCP_SB_URL",
        "token_env": "ODYSSEUS_MCP_SB_TOKEN",
    },
    "builtin_http_research": {
        "name": "Built-in: Research MCP",
        "url_env": "ODYSSEUS_MCP_RESEARCH_URL",
        "token_env": "ODYSSEUS_MCP_RESEARCH_TOKEN",
    },
    "builtin_http_shell": {
        "name": "Built-in: Shell MCP",
        "url_env": "ODYSSEUS_MCP_SHELL_URL",
        "token_env": "ODYSSEUS_MCP_SHELL_TOKEN",
    },
    "builtin_http_workspace": {
        "name": "Built-in: Workspace MCP",
        "url_env": "ODYSSEUS_MCP_WORKSPACE_URL",
        "token_env": "ODYSSEUS_MCP_WORKSPACE_TOKEN",
    },
    "builtin_http_model_router": {
        "name": "Built-in: Model Router MCP",
        "url_env": "ODYSSEUS_MCP_MODEL_ROUTER_URL",
        "token_env": "ODYSSEUS_MCP_MODEL_ROUTER_TOKEN",
    },
}

# Global flag to disable MCP if there are compatibility issues
MCP_DISABLED = os.environ.get("ODYSSEUS_DISABLE_MCP", "").lower() in ("1", "true", "yes")


async def register_builtin_servers(mcp_manager):
    """Connect all built-in MCP servers to the manager."""
    if MCP_DISABLED:
        logger.info("Built-in MCP servers disabled via ODYSSEUS_DISABLE_MCP")
        return

    base_dir = get_app_root()
    python = sys.executable

    async def _connect_python_server(server_id: str, script_path: str, name: str):
        try:
            ok = await mcp_manager.connect_server(
                server_id=server_id,
                name=name,
                transport="stdio",
                command=python,
                args=[script_path],
                env={"PYTHONPATH": base_dir},
            )
            if ok:
                logger.info(f"Built-in MCP server registered: {name}")
            else:
                logger.warning(f"Built-in MCP server failed to connect: {name}")
        except asyncio.CancelledError:
            logger.warning(f"Built-in MCP server {name} cancelled")
            raise
        except BaseException as e:
            logger.warning(f"Built-in MCP server {name} error: {type(e).__name__}: {e}")

    for server_id, (script, name) in _BUILTIN_SERVERS.items():
        script_path = os.path.join(base_dir, script)
        if not os.path.exists(script_path):
            logger.warning(f"Built-in MCP server script not found: {script_path}")
            continue
        asyncio.create_task(_connect_python_server(server_id, script_path, name))

    # Register NPX-based servers in the background (they take longer to start)
    npx_path = _find_npx()
    logger.info(f"NPX binary resolved to: {npx_path}")

    async def _start_npx_servers():
        await asyncio.sleep(3)  # let Python servers finish first
        for server_id, cfg in _BUILTIN_NPX_SERVERS.items():
            # Skip the server if its npx package isn't cached. Without this
            # check, npx would try to download/install the package on first
            # use, which can take minutes (or hang) on fresh installs without
            # Playwright system deps. Wrapping that in asyncio.wait_for to
            # bound the wait sounds reasonable, but mcp.client.stdio uses an
            # internal anyio task group that can't survive the resulting
            # cross-task cancellation: it raises "Attempted to exit cancel
            # scope in a different task than it was entered in" in a sibling
            # task, which cascades cancellations into the rest of the event
            # loop and downs the app. Detecting installed-state up-front lets
            # us bail with a useful warning before we ever touch stdio_client.
            args = cfg["args"]
            pkg_spec = _npx_package_from_args(args)
            if pkg_spec and not await _is_npx_package_cached(npx_path, pkg_spec):
                logger.warning(
                    f"{cfg['name']} is not available.\n"
                    f"  Reason: npm package {pkg_spec!r} is not installed in the npx cache.\n"
                    f"  Impact: tools provided by this MCP server will be unavailable.\n"
                    f"  Fix:    {os.path.basename(npx_path)} -y {pkg_spec} --version\n"
                    f"          (run once, then restart Odysseus)\n"
                    f"  Notes:  this server is optional; see README.md "
                    f"'Built-in MCP servers' for details."
                )
                continue

            logger.info(f"Starting NPX server: {cfg['name']} ({npx_path} {' '.join(args)})")
            try:
                ok = await mcp_manager.connect_server(
                    server_id=server_id,
                    name=cfg["name"],
                    transport="stdio",
                    command=npx_path,
                    args=args,
                )
                if ok:
                    logger.info(f"Built-in NPX server registered: {cfg['name']}")
                else:
                    logger.warning(f"Built-in NPX server failed to connect: {cfg['name']}")
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                logger.warning(f"Built-in NPX server {cfg['name']} error: {type(e).__name__}: {e}")

    asyncio.create_task(_start_npx_servers())

    # ── HTTP built-in servers ────────────────────────────────────────────────
    # Each entry in _BUILTIN_HTTP_SERVERS is attempted only when its URL env
    # var is set.  A missing or unreachable server logs a warning and is
    # skipped — the app starts normally and the reconnect loop retries later.
    async def _start_http_servers():
        await asyncio.sleep(5)  # let Python + NPX servers stabilise first
        for server_id, cfg in _BUILTIN_HTTP_SERVERS.items():
            url = os.environ.get(cfg["url_env"], "").strip()
            if not url:
                logger.debug(
                    "HTTP built-in %s skipped (%s not set)",
                    cfg["name"], cfg["url_env"],
                )
                continue
            token_env = cfg.get("token_env", "")
            token = os.environ.get(token_env, "").strip() if token_env else ""
            headers = {"Authorization": f"Bearer {token}"} if token else None
            try:
                ok = await mcp_manager.connect_server(
                    server_id=server_id,
                    name=cfg["name"],
                    transport="http",
                    url=url,
                    headers=headers,
                )
                if ok:
                    logger.info("HTTP built-in MCP registered: %s", cfg["name"])
                else:
                    logger.warning(
                        "HTTP built-in MCP unavailable at startup: %s (%s) — "
                        "reconnect loop will retry every 30 s",
                        cfg["name"], url,
                    )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                logger.warning(
                    "HTTP built-in MCP %s error: %s: %s",
                    cfg["name"], type(exc).__name__, exc,
                )

    asyncio.create_task(_start_http_servers())


def _npx_package_from_args(args):
    """Pick the package spec out of an npx args list shaped like
    ['-y', '<package@version>', ...flags]. Returns None if the
    convention doesn't match (we then skip the cache check and just
    try the connect)."""
    if not args:
        return None
    if "-y" in args:
        idx = args.index("-y") + 1
        if idx < len(args) and not args[idx].startswith("-"):
            return args[idx]
    # No -y prefix: first non-flag arg is the package
    for a in args:
        if not a.startswith("-"):
            return a
    return None


async def _is_npx_package_cached(npx_path, package_spec, timeout_s=5):
    """Probe whether an npx package is already in the local cache.

    First checks the local `_npx` cache for an installed package. If the
    package is not found there, falls back to `npx --no-install <pkg>
    --version` so older npm layouts still work without downloading.
    """
    if _is_package_in_npx_cache(package_spec):
        return True

    try:
        proc = await asyncio.create_subprocess_exec(
            npx_path, "--no-install", package_spec, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError:
        try:
            result = subprocess.run(
                [npx_path, "--no-install", package_spec, "--version"],
                capture_output=True,
                timeout=timeout_s,
            )
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return False
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, ValueError):
        return False
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return False
    return proc.returncode == 0 and bool(stdout.strip())


def _is_package_in_npx_cache(package_spec):
    """Return True when npm's `_npx` cache already contains package_spec."""
    package_name = _npx_package_name(package_spec)
    if not package_name:
        return False

    for cache_root in _npm_cache_roots():
        npx_root = os.path.join(cache_root, "_npx")
        if _npx_cache_contains_package(npx_root, package_name):
            return True
    return False


def _npx_package_name(package_spec):
    """Strip a version/range suffix from an npm package spec."""
    if not package_spec:
        return ""
    if package_spec.startswith("@"):
        parts = package_spec.split("@", 2)
        if len(parts) >= 3:
            return f"@{parts[1]}"
        return package_spec
    return package_spec.split("@", 1)[0]


def _npm_cache_roots():
    roots = []
    configured = os.environ.get("npm_config_cache")
    if configured:
        roots.append(os.path.expanduser(configured))
    roots.append(os.path.join(os.path.expanduser("~"), ".npm"))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(os.path.join(local_app_data, "npm-cache"))
    return list(dict.fromkeys(roots))


def _npx_cache_contains_package(npx_root, package_name):
    if not os.path.isdir(npx_root):
        return False
    package_path = os.path.join("node_modules", *package_name.split("/"), "package.json")
    try:
        entries = list(os.scandir(npx_root))
    except OSError:
        return False
    for entry in entries:
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        cached_name = _cached_package_name(os.path.join(entry.path, package_path))
        if is_dir and cached_name == package_name:
            return True
    return False


def _cached_package_name(package_json_path):
    try:
        with open(package_json_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return ""
    return str(data.get("name", "")).strip()
