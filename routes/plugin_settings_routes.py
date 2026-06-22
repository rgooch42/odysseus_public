"""Plugin enable/disable settings API."""
import logging
from fastapi import APIRouter, HTTPException, Request
from src.plugin_manager import PluginManager

logger = logging.getLogger(__name__)


def setup_plugin_settings_routes(manager: PluginManager) -> APIRouter:
    router = APIRouter(prefix="/api/plugins", tags=["plugins"])

    @router.get("")
    async def list_plugins():
        return manager.list_plugins()

    @router.post("/{name}/enable")
    async def enable_plugin(name: str):
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        manager.set_enabled(name, True)
        return {"name": name, "enabled": True}

    @router.post("/{name}/disable")
    async def disable_plugin(name: str):
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        manager.set_enabled(name, False)
        return {"name": name, "enabled": False}

    @router.post("/{name}/settings")
    async def save_settings(name: str, request: Request):
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        plugin = plugins[name]
        body = await request.json()
        valid_keys = {s.key for s in (plugin.manifest.settings or [])}
        unknown = set(body.keys()) - valid_keys
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown setting keys: {sorted(unknown)}")
        manager.save_plugin_settings(name, body)
        return {"name": name, "settings": manager.get_all_plugin_settings(name)}

    @router.post("/{name}/refresh")
    async def refresh_plugin(name: str):
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        all_plugins = manager.list_plugins()
        for p in all_plugins:
            if p["name"] == name:
                return p
        raise HTTPException(status_code=500, detail="Plugin disappeared during refresh")

    return router
