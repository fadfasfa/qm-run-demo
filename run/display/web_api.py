"""Web 服务路由层。

这个模块只负责定义页面路由、API 路由和 WebSocket 入口。
所有与端口、LCU、缓存、资源定位、浏览器托管相关的细节都委托给 `web_runtime`，
从而让接口层保持稳定且方便后续扩展。
"""

from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import quote, unquote

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from processing.alias_search import load_manual_alias_index
from processing.view_adapter import process_champions_data, process_hextechs_data
from scraping.augment_catalog import load_augment_icon_manifest
from scraping.version_sync import CONFIG_DIR
from . import web_runtime


class RedirectRequest(BaseModel):
    """前端点击英雄后发送的跳转请求体。"""

    hero_id: str
    hero_name: str


def _html_file_response(filename: str) -> FileResponse:
    return FileResponse(
        os.path.join(web_runtime.get_static_dir(), filename),
        media_type="text/html; charset=utf-8",
    )


def register_routes(app: FastAPI) -> None:
    @app.get("/")
    async def read_index():
        return _html_file_response("index.html")

    @app.get("/index.html")
    async def read_index_explicit():
        return _html_file_response("index.html")

    @app.get("/detail.html")
    async def read_detail():
        return _html_file_response("detail.html")

    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204)

    @app.get("/assets/{filename}")
    async def get_asset(filename: str):
        assets_dir = web_runtime.get_assets_dir()
        local_path = os.path.join(assets_dir, filename)
        real_requested = os.path.normcase(os.path.realpath(local_path))
        real_assets_dir = os.path.normcase(os.path.realpath(assets_dir))
        if not real_requested.startswith(real_assets_dir + os.sep) and real_requested != real_assets_dir:
            web_runtime.logger.warning("已阻止目录遍历：%s -> %s", filename, real_requested)
            return JSONResponse(content={"error": "禁止访问"}, status_code=403)
        if os.path.exists(local_path):
            return FileResponse(local_path)

        if filename.endswith(".png") and not filename[:-4].isdigit():
            catalog_entry = None
            try:
                file_stem = unquote(filename[:-4])
                catalog_entry = web_runtime.find_augment_catalog_entry(file_stem, CONFIG_DIR)
                if catalog_entry:
                    augment_name = str(catalog_entry.get("name", "")).strip() or file_stem
                    mapped_filename = str(catalog_entry.get("filename", "")).strip()
                    if mapped_filename:
                        local_mapped = web_runtime.find_existing_augment_asset_filename(assets_dir, mapped_filename)
                        if local_mapped:
                            return FileResponse(os.path.join(assets_dir, local_mapped))
                        web_runtime.queue_augment_icon_cache(mapped_filename, augment_name)

                    remote_icon_url = web_runtime.resolve_remote_augment_icon_url(catalog_entry, augment_name)
                    if remote_icon_url:
                        return RedirectResponse(url=remote_icon_url, status_code=307)

                local_fallback = web_runtime.find_existing_augment_asset_filename(assets_dir, filename)
                if local_fallback:
                    return FileResponse(os.path.join(assets_dir, local_fallback))
                if re.fullmatch(r"[A-Za-z0-9._-]+", file_stem):
                    web_runtime.queue_augment_icon_cache(filename, file_stem)
                remote_icon_url = web_runtime.resolve_remote_augment_icon_url(catalog_entry, file_stem)
                if remote_icon_url:
                    return RedirectResponse(url=remote_icon_url, status_code=307)
            except Exception as exc:
                web_runtime.logger.debug("远程资源缓存失败：%s", exc)

        if filename.endswith(".png"):
            file_stem = filename[:-4]
            hero_name = web_runtime.get_champion_name(file_stem)
            if hero_name:
                _, en_name = web_runtime.get_champion_info(file_stem)
                if en_name:
                    version = web_runtime.get_ddragon_version()
                    ddragon_url = f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{en_name}.png"
                    return RedirectResponse(url=ddragon_url, status_code=307)
            web_runtime.logger.debug("资源本地不存在，DDragon 回退也失败：%s", filename)

        return JSONResponse(content={"error": "资源未找到"}, status_code=404)

    @app.get("/api/champions")
    async def api_champions():
        df = web_runtime.get_df()
        if not df.empty:
            return JSONResponse(content=process_champions_data(df))

        web_runtime.request_background_refresh(force=False)

        snapshot_df = await asyncio.to_thread(web_runtime.get_live_champion_snapshot_df)
        if not snapshot_df.empty:
            return JSONResponse(content=process_champions_data(snapshot_df))

        df = await web_runtime.get_df_with_refresh()
        return JSONResponse(content=process_champions_data(df))

    @app.get("/api/startup_status")
    async def api_startup_status():
        return JSONResponse(content=web_runtime.get_startup_status())

    @app.get("/api/live_state")
    async def api_live_state():
        return JSONResponse(content=web_runtime.get_live_state_payload())

    @app.get("/api/champion_aliases")
    async def api_champion_aliases():
        try:
            payload = load_manual_alias_index()
            payload.sort(key=lambda item: item.get("heroName", ""))
            return JSONResponse(content=payload)
        except Exception as exc:
            web_runtime.logger.warning("英雄别名索引读取失败：%s", exc)
            return JSONResponse(content=[])

    @app.get("/api/champion/{name}/hextechs")
    async def api_champion_hextechs(name: str):
        canonical_name = web_runtime.resolve_canonical_hero_name(name)
        df = await web_runtime.get_df_with_refresh()
        if df.empty:
            live_df = await asyncio.to_thread(web_runtime.get_live_hextech_snapshot_df, canonical_name)
            if not live_df.empty:
                return JSONResponse(content=process_hextechs_data(live_df, canonical_name))
        return JSONResponse(content=process_hextechs_data(df, canonical_name))

    @app.get("/api/augment_icon_map")
    async def api_augment_icon_map():
        try:
            manifest = load_augment_icon_manifest()
            data = {}
            for item in manifest:
                name = str(item.get("name", "")).strip()
                filename = str(item.get("filename", "")).strip()
                remote_icon_url = str(item.get("icon_url", "")).strip()
                if not name:
                    continue
                if filename:
                    data[name] = f"/assets/{quote(filename, safe='')}"
                elif remote_icon_url:
                    data[name] = remote_icon_url
            return JSONResponse(content=data)
        except Exception as exc:
            web_runtime.logger.warning("统一海克斯目录图标映射读取失败：%s", exc)
            return JSONResponse(content={})

    @app.get("/api/synergies/{champ_id}")
    async def api_synergies(champ_id: str):
        try:
            data = web_runtime.get_synergy_data()
            if not data:
                return JSONResponse(content={"synergies": []})

            resolved_champ_id = web_runtime.resolve_champion_id(champ_id)
            canonical_name = web_runtime.resolve_canonical_hero_name(champ_id).lower()

            synergy_data = data.get(resolved_champ_id or champ_id, {})
            if not synergy_data:
                for key, value in data.items():
                    if (
                        str(champ_id).lower() == key.lower()
                        or str(resolved_champ_id).lower() == key.lower()
                        or (canonical_name and canonical_name == key.lower())
                    ):
                        synergy_data = value
                        break

            synergies = synergy_data.get("synergies", []) if synergy_data else []
            return JSONResponse(content={"synergies": synergies})
        except Exception as exc:
            web_runtime.logger.warning("协同数据查询失败：%s", exc)
            return JSONResponse(content={"synergies": []})

    @app.post("/api/redirect")
    async def api_redirect(req: RedirectRequest, request: Request):
        origin = request.headers.get("origin")
        if not web_runtime.is_allowed_local_origin(origin):
            web_runtime.logger.warning("已拒绝非本地来源的 redirect 请求：origin=%s", origin)
            return JSONResponse(content={"error": "forbidden_origin"}, status_code=status.HTTP_403_FORBIDDEN)

        try:
            hero_name, en_name = web_runtime.get_champion_info(req.hero_id)
        except (ValueError, TypeError):
            hero_name, en_name = "", ""

        if not hero_name:
            hero_name = req.hero_name

        if len(web_runtime.manager.active) == 0:
            url = web_runtime.build_detail_url(req.hero_id, hero_name or req.hero_name, en_name)
            if web_runtime.open_managed_browser(url, replace_existing=True):
                return JSONResponse(content={"status": "opened_browser"})
            return JSONResponse(content={"status": "浏览器打开失败"}, status_code=500)

        await web_runtime.manager.broadcast(
            {
                "type": "local_player_locked",
                "champion_id": req.hero_id,
                "hero_name": req.hero_name,
                "en_name": en_name,
            }
        )
        return JSONResponse(content={"status": "broadcast_sent"})

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        origin = ws.headers.get("origin")
        if not web_runtime.is_allowed_local_origin(origin):
            web_runtime.logger.warning("已拒绝非本地来源的 WebSocket 连接：origin=%s", origin)
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await web_runtime.manager.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            await web_runtime.manager.disconnect(ws)
