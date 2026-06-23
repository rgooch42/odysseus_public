"""Plugin enable/disable settings API."""
import logging
import os
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

    @router.post("/{name}/services/{svc}/enable")
    async def enable_service(name: str, svc: str):
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        plugin = plugins[name]
        svc_names = {s.name for s in (plugin.manifest.mcp_servers or [])}
        if svc not in svc_names:
            raise HTTPException(status_code=404, detail=f"Service '{svc}' not found on plugin '{name}'")
        manager.set_service_enabled(name, svc, True)
        return {"name": name, "service": svc, "enabled": True}

    @router.post("/{name}/services/{svc}/disable")
    async def disable_service(name: str, svc: str):
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        plugin = plugins[name]
        svc_names = {s.name for s in (plugin.manifest.mcp_servers or [])}
        if svc not in svc_names:
            raise HTTPException(status_code=404, detail=f"Service '{svc}' not found on plugin '{name}'")
        manager.set_service_enabled(name, svc, False)
        return {"name": name, "service": svc, "enabled": False}

    @router.post("/{name}/services/{svc}/settings")
    async def save_service_settings(name: str, svc: str, request: Request):
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        plugin = plugins[name]
        svc_map = {s.name: s for s in (plugin.manifest.mcp_servers or [])}
        if svc not in svc_map:
            raise HTTPException(status_code=404, detail=f"Service '{svc}' not found on plugin '{name}'")
        body = await request.json()
        server = svc_map[svc]
        valid_keys = {s.key for s in (server.settings or [])}
        if valid_keys:
            unknown = set(body.keys()) - valid_keys
            if unknown:
                raise HTTPException(status_code=400, detail=f"Unknown service setting keys: {sorted(unknown)}")
        manager.save_service_settings(name, svc, body)
        return {
            "name": name,
            "service": svc,
            "settings": {k: manager.get_service_setting(name, svc, k) for k in (body.keys())},
        }

    @router.get("/{name}/export")
    async def export_settings(name: str):
        from fastapi.responses import JSONResponse
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        data = manager._provider.load(name)
        # Strip env-governed URL/header values so they don't appear in the export
        plugin = plugins[name]
        sanitized = {**data}
        svcs = sanitized.get("services", {})
        for srv in (plugin.manifest.mcp_servers or []):
            if srv.name in svcs:
                svc_data = dict(svcs[srv.name])
                if srv.url_env and os.environ.get(srv.url_env, "").strip():
                    svc_data.pop("url", None)
                if srv.headers_env and os.environ.get(srv.headers_env, "").strip():
                    svc_data.pop("headers", None)
                svcs = {**svcs, srv.name: svc_data}
        sanitized["services"] = svcs
        safe_name = name.replace('"', "").replace(";", "").replace("/", "")
        return JSONResponse(
            content=sanitized,
            headers={"Content-Disposition": f'attachment; filename="{safe_name}-settings.json"'},
        )

    @router.post("/{name}/import-settings")
    async def import_settings(name: str, request: Request):
        plugins = {p.manifest.name: p for p in manager._discovered}
        if name not in plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
        plugin = plugins[name]
        body = await request.json()

        # Validate plugin-level settings keys
        valid_plugin_keys = {s.key for s in (plugin.manifest.settings or [])}
        if "settings" in body and valid_plugin_keys:
            unknown = set(body["settings"].keys()) - valid_plugin_keys
            if unknown:
                raise HTTPException(status_code=400, detail=f"Unknown plugin setting keys: {sorted(unknown)}")

        # Validate per-service setting keys
        svc_map = {s.name: s for s in (plugin.manifest.mcp_servers or [])}
        if "services" in body:
            for svc_name, svc_vals in body["services"].items():
                if svc_name not in svc_map:
                    raise HTTPException(status_code=400, detail=f"Unknown service: {svc_name}")
                valid_svc_keys = {s.key for s in (svc_map[svc_name].settings or [])}
                if valid_svc_keys:
                    # Allow "enabled" through (it's structural, not a schema setting)
                    unknown_svc = set(svc_vals.keys()) - valid_svc_keys - {"enabled"}
                    if unknown_svc:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Unknown keys for service '{svc_name}': {sorted(unknown_svc)}",
                        )

        # Merge (don't overwrite env-governed fields)
        current = manager._provider.load(name)
        if "enabled" in body:
            current["enabled"] = body["enabled"]
        if "settings" in body:
            current.setdefault("settings", {}).update(body["settings"])
        if "services" in body:
            for svc_name, svc_vals in body["services"].items():
                srv = svc_map.get(svc_name)
                merged_svc = dict(current.get("services", {}).get(svc_name, {}))
                for k, v in svc_vals.items():
                    if k == "url" and srv and srv.url_env and os.environ.get(srv.url_env, "").strip():
                        continue  # env governs, skip
                    if k == "headers" and srv and srv.headers_env and os.environ.get(srv.headers_env, "").strip():
                        continue  # env governs, skip
                    merged_svc[k] = v
                current.setdefault("services", {})[svc_name] = merged_svc
        manager._provider.save(name, current)
        return {"name": name, "imported": True}

    @router.post("/import")
    async def import_plugin(request: Request, confirm: bool = False):
        import zipfile, io
        from src.plugin_manifest import PluginManifest, ManifestError

        content_type = request.headers.get("content-type", "")
        if "multipart" in content_type:
            form = await request.form()
            file_field = form.get("file")
            if file_field is None:
                raise HTTPException(status_code=400, detail="No file field in form")
            raw = await file_field.read()
            filename = getattr(file_field, "filename", "upload")
        elif "application/json" in content_type:
            body = await request.json()
            raise HTTPException(status_code=422, detail="URL-based import not yet supported")
        else:
            raw = await request.body()
            filename = "upload.yaml"

        # Parse manifest
        if filename.endswith(".zip"):
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
                yaml_names = [n for n in zf.namelist() if n.endswith("plugin.yaml")]
                if not yaml_names:
                    raise HTTPException(status_code=400, detail="No plugin.yaml found in zip")
                yaml_content = zf.read(yaml_names[0]).decode("utf-8")
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="Invalid zip file")
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="plugin.yaml is not valid UTF-8")
        else:
            try:
                yaml_content = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="Upload is not valid UTF-8")

        try:
            manifest = PluginManifest.from_yaml_str(yaml_content, source="<upload>")
        except ManifestError as e:
            raise HTTPException(status_code=400, detail=str(e))

        already_installed = any(
            p.manifest.name == manifest.name for p in manager._discovered
        )
        version_delta = None
        if already_installed:
            existing = next(p for p in manager._discovered if p.manifest.name == manifest.name)
            version_delta = {
                "current": existing.manifest.version,
                "incoming": manifest.version,
            }

        if not confirm:
            return {
                "manifest": manifest.model_dump(),
                "already_installed": already_installed,
                "version_delta": version_delta,
                "min_version_ok": True,  # version gate is future work
            }

        # Step 2: write to disk
        if filename.endswith(".zip"):
            plugin_dir = manager._plugins_dir / manifest.name
            plugin_dir.mkdir(parents=True, exist_ok=True)
            zf = zipfile.ZipFile(io.BytesIO(raw))
            resolved_root = plugin_dir.resolve()
            for member in zf.namelist():
                parts = member.split("/", 1)
                dest_rel = parts[1] if len(parts) > 1 else parts[0]
                if not dest_rel:
                    continue
                dest = plugin_dir / dest_rel
                if not dest.resolve().is_relative_to(resolved_root):
                    logger.warning("Blocked zip-slip path in import: %s", member)
                    continue
                if member.endswith("/"):
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(member))
        else:
            plugin_dir = manager._plugins_dir / manifest.name
            plugin_dir.mkdir(parents=True, exist_ok=True)
            (plugin_dir / "plugin.yaml").write_text(yaml_content, encoding="utf-8")

        # Init empty settings file
        if not manager._provider.load(manifest.name):
            manager._provider.save(manifest.name, {"enabled": False, "services": {}, "settings": {}})

        # Re-discover so list_plugins reflects the new plugin
        manager.discover()

        all_plugins = manager.list_plugins()
        for p in all_plugins:
            if p["name"] == manifest.name:
                return p
        raise HTTPException(status_code=500, detail="Plugin installed but not found after rediscover")

    return router
