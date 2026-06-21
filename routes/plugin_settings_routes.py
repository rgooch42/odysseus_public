"""Plugin enable/disable settings API."""
import logging
from fastapi import APIRouter, HTTPException
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

    return router
