import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, unquote
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

import pandas as pd
import psutil
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from alias_utils import dedupe_alias_texts
from icon_resolver import (
    ensure_augment_icon_cached,
    find_augment_icon_filename,
    find_existing_augment_asset_filename,
    load_apexlol_hextech_map,
    load_augment_icon_map,
)
from data_processor import process_champions_data, process_hextechs_data
from hextech_query import get_latest_csv, load_hero_aliases
from hero_sync import BASE_DIR, CONFIG_DIR, RESOURCE_DIR, load_champion_core_data
from backend_refresh import refresh_backend_data

# 模块日志。
logger = logging.getLogger(__name__)

# 网页服务默认端口，可通过环境变量覆盖。
SERVER_PORT = int(os.getenv("HEXTECH_PORT", "8000"))
WEB_PORT_FILE = os.path.join(CONFIG_DIR, "web_server_port.txt")
ACTIVE_WEB_PORT = SERVER_PORT
VERSION_FILE = os.path.join(CONFIG_DIR, "hero_version.txt")
AUGMENT_ICON_SOURCE_FILE = os.path.join(CONFIG_DIR, "augment_icon_source.txt")
AUGMENT_ICON_AUDIT_FILE = os.path.join(CONFIG_DIR, "augment_icon_audit.jsonl")
BROWSER_PROFILE_DIR = os.path.join(CONFIG_DIR, "browser_profile")
# 图标来源标记。
AUGMENT_ICON_SOURCE_ID = "apexlol"

_managed_browser_process: Optional[subprocess.Popen] = None
_managed_browser_lock = threading.Lock()


def _write_active_web_port(port: int) -> None:
    tmp_path = WEB_PORT_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(str(port))
    os.replace(tmp_path, WEB_PORT_FILE)


def _iter_browser_candidates() -> List[str]:
    configured = os.getenv("HEXTECH_BROWSER")
    candidates = []
    if configured:
        candidates.append(configured)
    candidates.extend(["msedge", "chrome", "brave"])
    resolved: List[str] = []
    for candidate in candidates:
        path = shutil.which(candidate)
        if path and path not in resolved:
            resolved.append(path)
    return resolved


def _terminate_managed_browser() -> bool:
    global _managed_browser_process

    proc = _managed_browser_process
    if proc is None:
        return False

    _managed_browser_process = None
    if proc.poll() is not None:
        return True

    try:
        parent = psutil.Process(proc.pid)
    except psutil.Error:
        return False

    try:
        children = parent.children(recursive=True)
    except psutil.Error:
        children = []

    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    psutil.wait_procs(children, timeout=2)

    try:
        parent.terminate()
        parent.wait(timeout=2)
    except psutil.TimeoutExpired:
        try:
            parent.kill()
        except psutil.Error:
            pass
    except psutil.Error:
        return False

    return True


def _open_managed_browser(url: str, replace_existing: bool = False) -> bool:
    global _managed_browser_process

    os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)

    with _managed_browser_lock:
        existing = _managed_browser_process
        if existing is not None and existing.poll() is not None:
            _managed_browser_process = None
            existing = None

        if replace_existing and existing is not None:
            _terminate_managed_browser()

        for browser_path in _iter_browser_candidates():
            cmd = [
                browser_path,
                f"--app={url}",
                "--new-window",
                f"--user-data-dir={BROWSER_PROFILE_DIR}",
            ]
            try:
                _managed_browser_process = subprocess.Popen(cmd)
                logger.info("已启动受管浏览器窗口：%s", url)
                return True
            except OSError as e:
                logger.debug("启动浏览器 %s 失败：%s", browser_path, e)

    try:
        webbrowser.open(url)
        logger.info("已通过系统默认浏览器打开：%s", url)
        return True
    except Exception as e:
        logger.warning("打开浏览器失败：%s", e)
        return False


def _build_detail_url(hero_id: str, hero_name: str, en_name: str) -> str:
    return (
        f"http://127.0.0.1:{ACTIVE_WEB_PORT}/detail.html"
        f"?hero={quote(hero_name or '', safe='')}"
        f"&id={quote(str(hero_id), safe='')}"
        f"&en={quote(en_name or '', safe='')}"
        f"&auto=1"
    )

_champion_core_cache: Optional[dict] = None


def _ensure_champion_cache() -> dict:
    # 懒加载英雄核心数据，减少重复读取。
    global _champion_core_cache
    if _champion_core_cache is None:
        try:
            _champion_core_cache = load_champion_core_data()
        except Exception as e:
            logger.warning("英雄核心数据加载失败：%s", e)
            _champion_core_cache = {}
    return _champion_core_cache


def get_champion_name(champ_id: str) -> str:
    # 根据英雄 ID 读取中文名。
    cache = _ensure_champion_cache()
    champ_id_str = str(champ_id)
    if champ_id_str in cache:
        return cache[champ_id_str].get('name', '')
    return ''


def get_champion_info(champ_id: str) -> Tuple[str, str]:
    # 返回中文名和英文名。
    cache = _ensure_champion_cache()
    champ_id_str = str(champ_id)
    if champ_id_str in cache:
        data = cache[champ_id_str]
        return data.get('name', ''), data.get('en_name', '')
    return '', ''


def _get_ddragon_version() -> str:
    # 读取本地版本号，失败时使用默认值。
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            version = f.read().strip()
            if version:
                return version
    except (OSError, IOError):
        logger.debug("无法读取 hero_version.txt，改用内置版本。")
    return "14.3.1"


_augment_prefetch_lock = threading.Lock()
_augment_prefetch_mtime = 0.0


def _read_augment_icon_source_marker() -> str:
    try:
        with open(AUGMENT_ICON_SOURCE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, IOError):
        return ""


def _write_augment_icon_source_marker(source_id: str) -> None:
    tmp_path = AUGMENT_ICON_SOURCE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(source_id)
    os.replace(tmp_path, AUGMENT_ICON_SOURCE_FILE)


def _prefetch_augment_icons(force_refresh: bool = False) -> None:
    global _augment_prefetch_mtime

    with _augment_prefetch_lock:
        if not force_refresh and _augment_prefetch_mtime:
            return
        _augment_prefetch_mtime = time.time()

    try:
        icon_map = load_apexlol_hextech_map(CONFIG_DIR, force_refresh=force_refresh)
        logger.debug("已预热 apexlol 海克斯图标映射，共 %s 项", len(icon_map))
    except Exception as e:
        logger.debug("预热 apexlol 海克斯图标映射失败：%s", e)

    if force_refresh:
        try:
            _write_augment_icon_source_marker(AUGMENT_ICON_SOURCE_ID)
        except Exception as e:
            logger.debug("记录强化符文图标来源标记失败：%s", e)


def _append_augment_icon_audit(record: dict) -> None:
    # 审计记录只追加到 JSONL，方便后续排查每次启动的缺图和修复结果。
    os.makedirs(CONFIG_DIR, exist_ok=True)
    payload = dict(record)
    payload.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()))
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with open(AUGMENT_ICON_AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _audit_and_repair_augment_icons(force_map_refresh: bool = False) -> dict:
    # 启动时扫描海克斯图标映射，缺失则自动补抓并汇总写入审计文件。
    start_time = time.time()
    icon_map = {}
    repaired_items = []
    failed_items = []

    try:
        icon_map = load_augment_icon_map(CONFIG_DIR, force_refresh=force_map_refresh)
    except Exception as e:
        record = {
            "kind": "augment_icon_audit",
            "status": "error",
            "force_map_refresh": force_map_refresh,
            "error": str(e),
            "duration_ms": round((time.time() - start_time) * 1000, 2),
        }
        _append_augment_icon_audit(record)
        logger.error("海克斯图标自检失败：%s", e)
        return record

    missing_before = []
    checked = 0
    for raw_name, raw_filename in sorted(icon_map.items(), key=lambda item: item[0]):
        checked += 1
        filename = find_augment_icon_filename(icon_map, raw_name)
        if not filename:
            filename = find_augment_icon_filename(icon_map, raw_filename) or raw_filename
        filename = str(filename or "").strip()
        if not filename:
            failed_items.append({
                "name": raw_name,
                "reason": "无法解析图标文件名",
            })
            continue

        asset_path = os.path.join(_assets_dir, filename)
        if os.path.exists(asset_path) and os.path.getsize(asset_path) > 0:
            continue

        missing_before.append({
            "name": raw_name,
            "filename": filename,
        })
        try:
            cached_path = ensure_augment_icon_cached(filename, asset_dir=_assets_dir)
            if cached_path and os.path.exists(cached_path) and os.path.getsize(cached_path) > 0:
                repaired_items.append({
                    "name": raw_name,
                    "filename": filename,
                    "path": os.path.basename(cached_path),
                })
            else:
                failed_items.append({
                    "name": raw_name,
                    "filename": filename,
                    "reason": "远程抓取未返回可用文件",
                })
        except Exception as e:
            failed_items.append({
                "name": raw_name,
                "filename": filename,
                "reason": str(e),
            })

    record = {
        "kind": "augment_icon_audit",
        "status": "ok" if not failed_items else "partial_failure",
        "force_map_refresh": force_map_refresh,
        "map_entries": len(icon_map),
        "checked": checked,
        "missing_before_count": len(missing_before),
        "missing_before_sample": missing_before[:20],
        "repaired_count": len(repaired_items),
        "repaired_sample": repaired_items[:20],
        "failed_count": len(failed_items),
        "failed_items": failed_items,
        "duration_ms": round((time.time() - start_time) * 1000, 2),
    }
    _append_augment_icon_audit(record)

    if failed_items:
        logger.error(
            "海克斯图标自检存在失败：缺失=%s，修复=%s，失败=%s",
            len(missing_before),
            len(repaired_items),
            len(failed_items),
        )
    return record


def _startup_augment_icon_maintenance(force: bool = False) -> None:
    # 先预热图标映射，再执行缺图自检，保证页面开启前尽量完成修复。
    _prefetch_augment_icons(force_refresh=force)
    _audit_and_repair_augment_icons(force_map_refresh=force)



# 请求体：前端点击英雄后发送到跳转接口。

class RedirectRequest(BaseModel):
    hero_id: str
    hero_name: str


# 打包环境兼容的资源路径解析。

def get_resource_path(relative_path: str) -> str:
    # 返回打包环境兼容的资源路径。
    candidates = [
        os.path.join(RESOURCE_DIR, relative_path),
        os.path.join(BASE_DIR, relative_path),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def _html_file_response(filename: str) -> FileResponse:
    # 返回网页文件并显式声明中文编码，避免浏览器乱码。
    return FileResponse(
        os.path.join(_static_dir, filename),
        media_type="text/html; charset=utf-8",
    )


# 数据表文件缓存，减少重复读盘。

@dataclass
class CSVCache:
    path: str = ""
    mtime: float = 0.0
    df: pd.DataFrame = field(default_factory=pd.DataFrame)

_csv_cache = CSVCache()


def get_df() -> pd.DataFrame:
    # 返回最新的英雄数据表。
    latest = get_latest_csv()
    if not latest:
        return pd.DataFrame()
    try:
        current_mtime = os.path.getmtime(latest)
    except OSError:
        return _csv_cache.df

    if latest != _csv_cache.path or current_mtime != _csv_cache.mtime:
        try:
            # 直接读取数据表，让解析库自行推断列类型。
            df = pd.read_csv(latest)
            # 清理列名中的空格，兼容不同来源的数据表。

            # 动态定位英雄编号列。
            id_column = None
            for col_name in ["英雄ID", "英雄 ID"]:
                if col_name in df.columns:
                    id_column = col_name
                    break

            # 将“123.0”这类编号还原为纯文本。
            if id_column is not None:
                df[id_column] = df[id_column].astype(str).str.strip().str.replace('.0', '', regex=False)

            _csv_cache.path = latest
            _csv_cache.mtime = current_mtime
            _csv_cache.df = df
            logger.info("CSV refreshed: %s", os.path.basename(latest))
        except Exception as e:
            logger.error("CSV 刷新失败：%s", e)
            # 读取失败时复用旧缓存，避免页面抖动。
            return _csv_cache.df
    return _csv_cache.df


# 结构化数据缓存。

@dataclass
class JSONFileCache:
    # 记录数据文件的路径、修改时间和解析结果。
    path: str = ""
    mtime: float = 0.0
    data: dict = field(default_factory=dict)

_synergy_cache = JSONFileCache()


def _get_synergy_data() -> dict:
    # 读取并缓存协同数据文件。
    json_path = os.path.join(CONFIG_DIR, "Champion_Synergy.json")
    try:
        current_mtime = os.path.getmtime(json_path)
    except OSError:
        return _synergy_cache.data

    if json_path != _synergy_cache.path or current_mtime != _synergy_cache.mtime:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            _synergy_cache.path = json_path
            _synergy_cache.mtime = current_mtime
            _synergy_cache.data = data
            logger.info("Champion_Synergy.json cache refreshed")
        except Exception as e:
            logger.error("协同数据文件加载失败：%s", e)
            return _synergy_cache.data
    return _synergy_cache.data


# 网页套接字连接管理。

class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active.append(ws)

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, message: dict):
    # 先拷贝连接列表，再广播，避免遍历时被并发修改。
        async with self._lock:
            snapshot = list(self.active)
        dead = []
        for ws in snapshot:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self.active:
                        self.active.remove(ws)

manager = ConnectionManager()


# 本地客户端轮询状态与连接管理。

# 是否在检测到本地锁定英雄时自动跳转详情页。
AUTO_JUMP_ENABLED = True


@dataclass
class LCUState:
    # 保存当前连接、会话和英雄选择状态。
    port: Optional[str] = None
    token: Optional[str] = None
    current_ids: Set[str] = field(default_factory=set)
    local_champ_id: Optional[int] = None
    local_champ_name: Optional[str] = None
    consecutive_404_count: int = 0

_lcu_state = LCUState()


def _create_lcu_session() -> requests.Session:
    # 复用带重试的会话，降低临时失败带来的抖动。
    session = requests.Session()
    retry_strategy = Retry(
        total=2,
        backoff_factor=0.3,
        status_forcelist=[502, 503],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# 本地客户端请求复用会话。
_lcu_session = _create_lcu_session()


def _scan_lcu_process() -> tuple:
    # 扫描本地客户端进程并提取端口和令牌。
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if proc.info["name"] == "LeagueClientUx.exe":
                port, token = None, None
                for arg in proc.info["cmdline"] or []:
                    if arg.startswith("--app-port="):
                        port = arg.split("=")[1]
                    if arg.startswith("--remoting-auth-token="):
                        token = arg.split("=")[1]
                if port and token:
                    return port, token
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None, None


def _urllib3_disable_warnings():
    # 忽略本地客户端自签名证书告警。
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass


async def lcu_polling_loop():
    # 持续轮询本地客户端会话。
    #
    # - 读取当前可选英雄列表。
    # - 找到本地玩家的英雄编号。
    # - 向前端广播选角变化。
    # - 连续异常时自动重置连接状态。
    #
    # 轮询失败时会继续重试，不会让服务退出。
    #
    #
    _urllib3_disable_warnings()
    while True:
        try:
            if not _lcu_state.port:
                port, token = await asyncio.to_thread(_scan_lcu_process)
                if port:
                    _lcu_state.port = port
                    _lcu_state.token = token
                    logger.info("已检测到 LCU 连接，端口=%s", port)
                else:
                    await asyncio.sleep(2)
                    continue

            auth = base64.b64encode(
                f"riot:{_lcu_state.token}".encode()
            ).decode()
            headers = {
                "Authorization": f"Basic {auth}",
                "Accept": "application/json",
            }
            url = f"https://127.0.0.1:{_lcu_state.port}/lol-champ-select/v1/session"

            res = await asyncio.to_thread(
                _lcu_session.get, url, headers=headers, verify=False, timeout=3
            )

            if res.status_code == 200:
                data = res.json()
                # 成功响应后重置 404 计数。
                _lcu_state.consecutive_404_count = 0

                # 收集当前可选英雄编号。
                available_ids = {
                    str(c["championId"])
                    for c in data.get("benchChampions", [])
                }
                for p in data.get("myTeam", []):
                    if (
                        p.get("cellId") == data.get("localPlayerCellId")
                        and p.get("championId") != 0
                    ):
                        available_ids.add(str(p["championId"]))

                if available_ids != _lcu_state.current_ids:
                    _lcu_state.current_ids = available_ids.copy()
                    await manager.broadcast({
                        "type": "champion_update",
                        "champion_ids": list(available_ids),
                        "timestamp": time.time(),
                    })

                # 找到本地玩家所在的格子。
                local_cell_id = data.get("localPlayerCellId")
                local_champion_id = None

                # 在队伍列表中按格子编号找到本地玩家。
                for p in data.get("myTeam", []):
                    if p.get("cellId") == local_cell_id:
                        local_champion_id = p.get("championId")
                        break

                # 英雄编号大于 0 才表示已经锁定英雄。
                if local_champion_id and local_champion_id > 0:
                    prev_champ_id = _lcu_state.local_champ_id

                    if prev_champ_id != local_champion_id:
                        _lcu_state.local_champ_id = local_champion_id

                        # 读取英雄中英文名，供广播和日志使用。
                        hero_name, en_name = get_champion_info(str(local_champion_id))
                        _lcu_state.local_champ_name = hero_name

                        logger.info("LCU 已锁定英雄：%s (ID=%s)", hero_name, local_champion_id)

                        # 首次锁定时通知前端页面。
                        if AUTO_JUMP_ENABLED:
                            await manager.broadcast({
                                "type": "local_player_locked",
                                "champion_id": local_champion_id,
                                "hero_name": hero_name,
                                "en_name": en_name,
                            })
                        else:
                            logger.debug("AUTO_JUMP_ENABLED = False; skipping automatic jump broadcast")

            elif res.status_code == 404:
                # 返回 404，说明会话暂时不存在或已切换。
                _lcu_state.consecutive_404_count += 1

                # 清空本地英雄状态，等待下一次会话恢复。
                if _lcu_state.local_champ_id is not None:
                    _lcu_state.local_champ_id = None
                    _lcu_state.local_champ_name = None
                    _lcu_state.current_ids = set()

                # 连续 5 次 404 后，主动重置连接信息。
                if _lcu_state.consecutive_404_count >= 5:
                    logger.warning("LCU 连续返回 404 五次，重置连接状态（count=%s）", _lcu_state.consecutive_404_count)
                    _lcu_state.port = None
                    _lcu_state.token = None
                    _lcu_state.consecutive_404_count = 0

            elif res.status_code in (401, 403):
                # 令牌失效或未授权，重新扫描进程并获取新会话。
                logger.warning("LCU token 失效或未授权（401/403），重置连接状态。")
                _lcu_state.port = None
                _lcu_state.token = None
            else:
                logger.warning("LCU 响应异常状态码=%s，重置连接状态。", res.status_code)
                _lcu_state.port = None

        except requests.exceptions.ConnectionError as e:
            logger.warning("LCU 连接错误：%s", e)
            _lcu_state.port = None
            _lcu_state.token = None
        except Exception as e:
            logger.warning("LCU 轮询失败：%s", e)

        await asyncio.sleep(1.5)


# 数据表变更轮询。

async def csv_watcher_loop():
    # 每 3 秒检查一次数据表是否更新。
    #
    # 如果文件发生变化，则向前端广播 `data_updated`。
    #

    prev_mtime = 0.0
    while True:
        try:
            # 触发数据刷新，更新缓存时间。
            get_df()
            current_mtime = _csv_cache.mtime
            if current_mtime > prev_mtime and prev_mtime != 0.0:
                logger.info("CSV 已更新：%s", os.path.basename(_csv_cache.path))
                await manager.broadcast({'type': 'data_updated'})
            prev_mtime = current_mtime
        except (OSError, IOError) as e:
            logger.warning("CSV 监视器错误：%s", e)
        await asyncio.sleep(3)


# 网页服务生命周期管理。

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时并行执行后台刷新、图标预取和轮询任务。
    scraper_thread = threading.Thread(
        target=refresh_backend_data,
        kwargs={"force": False},
        daemon=True,
        name="backend-refresh-startup",
    )
    scraper_thread.start()
    needs_augment_refresh = _read_augment_icon_source_marker() != AUGMENT_ICON_SOURCE_ID
    augment_thread = threading.Thread(
        target=_startup_augment_icon_maintenance,
        kwargs={"force": needs_augment_refresh},
        daemon=True,
        name="augment-icon-maintenance",
    )
    augment_thread.start()
    task1 = asyncio.create_task(lcu_polling_loop())
    task2 = asyncio.create_task(csv_watcher_loop())
    yield
    task1.cancel()
    task2.cancel()
    try:
        await task1
    except asyncio.CancelledError:
        pass
    try:
        await task2
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)

# 挂载静态资源目录，提供前端文件。
_static_dir = get_resource_path("static")
os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# 运行时下载的图片和兜底资源都放在资源目录。
_assets_dir = get_resource_path("assets")
os.makedirs(_assets_dir, exist_ok=True)
# 这里的资源既包含英雄头像，也包含增强器图标缓存。


# 页面与接口路由。

@app.get("/")
async def read_index():
    # 返回首页文件。
    return _html_file_response("index.html")

@app.get("/index.html")
async def read_index_explicit():
    # 显式访问首页路径时也返回首页。
    return _html_file_response("index.html")

@app.get("/detail.html")
async def read_detail():
    # 返回详情页文件。
    return _html_file_response("detail.html")

@app.get("/canvas_fallback.js")
async def read_canvas_fallback():
    # 返回画布降级脚本，供网页中的图表降级使用。
    js_path = os.path.join(_static_dir, "canvas_fallback.js")
    if os.path.exists(js_path):
        return FileResponse(js_path, media_type="application/javascript")
    return JSONResponse(content={"error": "未找到"}, status_code=404)

@app.get("/favicon.ico")
async def favicon():
    # 返回空的站点图标响应，避免 404 噪音。
    return Response(status_code=204)

@app.get("/assets/{filename}")
async def get_asset(filename: str):
    # 按文件名返回资源；本地不存在时尝试增强器映射或官方资源回退。
    local_path = os.path.join(_assets_dir, filename)
    # 增强器图标优先走映射表并缓存到本地。
    real_requested = os.path.normcase(os.path.realpath(local_path))
    real_assets_dir = os.path.normcase(os.path.realpath(_assets_dir))
    if not real_requested.startswith(real_assets_dir + os.sep) and real_requested != real_assets_dir:
        logger.warning("已阻止目录遍历：%s -> %s", filename, real_requested)
        return JSONResponse(content={"error": "禁止访问"}, status_code=403)
    if os.path.exists(local_path):
        return FileResponse(local_path)
    if filename.endswith('.png') and not filename[:-4].isdigit():
        try:
            file_stem = unquote(filename[:-4])
            icon_map = load_augment_icon_map(CONFIG_DIR)
            mapped_filename = find_augment_icon_filename(icon_map, file_stem, asset_dir=_assets_dir)
            if mapped_filename:
                cached_path = ensure_augment_icon_cached(mapped_filename, asset_dir=_assets_dir)
                if cached_path and os.path.exists(cached_path):
                    return FileResponse(cached_path)
            local_fallback = find_existing_augment_asset_filename(_assets_dir, filename)
            if local_fallback:
                return FileResponse(os.path.join(_assets_dir, local_fallback))
            elif re.fullmatch(r"[A-Za-z0-9._-]+", file_stem):
                cached_path = ensure_augment_icon_cached(filename, asset_dir=_assets_dir)
                if cached_path and os.path.exists(cached_path):
                    return FileResponse(cached_path)
        except Exception as e:
            logger.debug("远程资源缓存失败：%s", e)

    # 普通英雄头像尝试从官方资源源回退。
    if filename.endswith('.png'):
        file_stem = filename[:-4]  # 这里的文件名形如“123.png”。
        hero_name = get_champion_name(file_stem)
        if hero_name:
            _, en_name = get_champion_info(file_stem)
            if en_name:
                version = _get_ddragon_version()
                ddragon_url = f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{en_name}.png"
                return RedirectResponse(url=ddragon_url, status_code=307)
        logger.debug("资源本地不存在，DDragon 回退也失败：%s", filename)

    return JSONResponse(content={"error": "资源未找到"}, status_code=404)

@app.get("/api/champions")
async def api_champions():
    df = get_df()
    return JSONResponse(content=process_champions_data(df))


@app.get("/api/champion_aliases")
async def api_champion_aliases():
    # 返回英雄别名索引，供前端搜索与终端保持一致。
    try:
        core_data = load_champion_core_data()
        alias_map = load_hero_aliases()
        payload = []

        for value in core_data.values():
            hero_name = value.get("name", "")
            title = value.get("title", "")
            en_name = value.get("en_name", "")
            aliases = dedupe_alias_texts(
                [hero_name, en_name, title],
                value.get("aliases", []),
                alias_map.get(hero_name, []),
                alias_map.get(title, []),
            )

            payload.append({
                "heroName": hero_name,
                "title": title,
                "enName": en_name,
                "aliases": aliases,
            })

        payload.sort(key=lambda item: item.get("heroName", ""))
        return JSONResponse(content=payload)
    except Exception as e:
        logger.warning("英雄别名索引读取失败：%s", e)
        return JSONResponse(content=[])

@app.get("/api/champion/{name}/hextechs")
async def api_champion_hextechs(name: str):
    df = get_df()
    return JSONResponse(content=process_hextechs_data(df, name))

@app.get("/api/augment_icon_map")
async def api_augment_icon_map():
    # 返回海克斯图标映射，供前端或调试页查找。
    try:
        raw_map = load_apexlol_hextech_map(CONFIG_DIR)
        data = {
            name: f"https://apexlol.info/images/hextech/{slug}.webp"
            for name, slug in raw_map.items()
        }
        return JSONResponse(content=data)
    except Exception as e:
        logger.warning("apexlol 海克斯图标映射读取失败：%s", e)
        return JSONResponse(content={})

@app.get("/api/synergies/{champ_id}")
async def api_synergies(champ_id: str):
    # 返回英雄协同数据。



    try:
        data = _get_synergy_data()
        if not data:
            return JSONResponse(content={"synergies": []})

        # 先按英雄编号精确匹配。
        synergy_data = data.get(champ_id, {})

        # 再尝试通过别名匹配。
        if not synergy_data:
            for key, value in data.items():
                # 先检查别名列表。
                aliases = value.get("aliases", [])
                if champ_id in aliases or champ_id.lower() in [a.lower() for a in aliases]:
                    synergy_data = value
                    break
                # 再检查键本身。
                if champ_id.lower() == key.lower():
                    synergy_data = value
                    break

        synergies = synergy_data.get("synergies", []) if synergy_data else []
        return JSONResponse(content={"synergies": synergies})
    except Exception as e:
        logger.warning("协同数据查询失败：%s", e)
        return JSONResponse(content={"synergies": []})

@app.post("/api/redirect")
async def api_redirect(req: RedirectRequest):
    # 处理前端点击后的重定向。

    # 先尝试从英雄编号还原中英文名。
    try:
        hero_name, en_name = get_champion_info(req.hero_id)
    except (ValueError, TypeError):
        # 编号不是合法文本时，回退为空字符串。
        hero_name, en_name = '', ''

    # 如果中文名缺失，退回前端传来的名称。
    if not hero_name:
        hero_name = req.hero_name

    # 当前没有前端连接时，直接由服务端打开详情页。
    if len(manager.active) == 0:
        url = _build_detail_url(req.hero_id, hero_name or req.hero_name, en_name)
        if _open_managed_browser(url, replace_existing=True):
            return JSONResponse(content={"status": "opened_browser"})
        return JSONResponse(content={"status": "浏览器打开失败"}, status_code=500)
    else:
        # 有前端在线时，直接广播给页面处理。
        await manager.broadcast({
            "type": "local_player_locked",
            "champion_id": req.hero_id,
            "hero_name": req.hero_name,
            "en_name": en_name
        })
        return JSONResponse(content={"status": "broadcast_sent"})

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws)


# 端口探测与浏览器启动。

def find_available_port(start_port=8000, max_attempts=50):
    # 从起始端口开始查找可用端口。
    import socket

    for port_offset in range(max_attempts):
        port = start_port + port_offset
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"未能在端口范围 {start_port}-{start_port + max_attempts - 1} 找到可用端口")

def _open_chrome(port: int):
    # 打开浏览器访问当前网页服务。
    url = f"http://127.0.0.1:{port}"
    _open_managed_browser(url, replace_existing=True)


def run_web_server() -> None:
    global ACTIVE_WEB_PORT

    # 启动时先找可用端口，避免端口占用导致服务直接失败。
    actual_port = find_available_port(SERVER_PORT)
    if actual_port != SERVER_PORT:
        logger.info("端口 %s 已被占用，改用端口 %s", SERVER_PORT, actual_port)

    # 将实际端口写回配置，供界面程序动态读取。
    ACTIVE_WEB_PORT = actual_port
    _write_active_web_port(ACTIVE_WEB_PORT)
    _open_chrome(actual_port)
    uvicorn.run("web_server:app", host="127.0.0.1", port=actual_port, reload=False)


if __name__ == "__main__":
    run_web_server()
