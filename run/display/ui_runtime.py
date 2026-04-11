"""桌面 UI 运行时辅助层。

文件职责：
- 承载桌面端后台线程、窗口联动和资源加载等非纯界面逻辑

核心输入：
- `HextechUI` 主类持有的状态、控件和会话对象
- Web live_state、LCU 本地接口和本地图片资源

核心输出：
- 桌面端后台刷新、英雄联动、图片缓存和窗口状态同步

主要依赖：
- `processing.query_terminal`
- `scraping.version_sync`

维护提醒：
- Tk 组件结构仍应留在 `display.hextech_ui`
- 新增后台线程、轮询或资源下载逻辑优先集中在本文件
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from io import BytesIO
from typing import TYPE_CHECKING
from urllib.parse import quote

import psutil
import requests
import win32gui
from PIL import Image, ImageTk

from processing.query_terminal import display_hero_hextech, main_query, set_last_hero
from scraping.version_sync import ASSET_DIR, BASE_DIR

if TYPE_CHECKING:
    from .hextech_ui import HextechUI


logger = logging.getLogger(__name__)
SERVER_PORT = int(os.getenv("HEXTECH_PORT", "8000"))


def resolve_web_base(web_port_file: str, timeout: float = 5.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(web_port_file, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            port = int(raw)
            if 1 <= port <= 65535:
                return f"http://127.0.0.1:{port}"
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    return f"http://127.0.0.1:{SERVER_PORT}"


def disable_lcu_https_warning() -> None:
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass


def scan_lcu_process() -> tuple:
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if proc.info["name"] == "LeagueClientUx.exe":
                port, token = None, None
                for arg in proc.info["cmdline"] or []:
                    if arg.startswith("--app-port="):
                        port = arg.split("=", 1)[1]
                    if arg.startswith("--remoting-auth-token="):
                        token = arg.split("=", 1)[1]
                if port and token:
                    return port, token
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None, None


def poll_lcu_live_ids(ui: "HextechUI"):
    if not ui._lcu_port or not ui._lcu_token:
        port, token = scan_lcu_process()
        if not port or not token:
            ui._lcu_port = None
            ui._lcu_token = None
            return None
        ui._lcu_port = port
        ui._lcu_token = token

    auth = base64.b64encode(f"riot:{ui._lcu_token}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
    url = f"https://127.0.0.1:{ui._lcu_port}/lol-champ-select/v1/session"

    try:
        res = ui.session.get(url, headers=headers, verify=False, timeout=2.5)
    except requests.exceptions.RequestException:
        ui._lcu_port = None
        ui._lcu_token = None
        return None

    if res.status_code == 404:
        return set()
    if res.status_code in (401, 403):
        ui._lcu_port = None
        ui._lcu_token = None
        return None
    if res.status_code != 200:
        ui._lcu_port = None
        return None

    try:
        payload = res.json()
    except ValueError:
        return None

    available_ids = {str(c.get("championId")) for c in payload.get("benchChampions", [])}
    local_cell_id = payload.get("localPlayerCellId")
    for player in payload.get("myTeam", []):
        if player.get("cellId") == local_cell_id and player.get("championId"):
            available_ids.add(str(player.get("championId")))
    return {champ_id for champ_id in available_ids if champ_id and champ_id != "0"}


def start_web_server_process(web_port_file: str):
    startupinfo = None
    child_env = os.environ.copy()
    child_env["HEXTECH_BASE_DIR"] = BASE_DIR
    child_env["HEXTECH_OPEN_BROWSER"] = "0"
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--web-server"]
        cwd = BASE_DIR
    else:
        web_script = os.path.join(BASE_DIR, "web_server.py")
        command = [sys.executable, web_script]
        cwd = BASE_DIR
    web_process = subprocess.Popen(
        command,
        startupinfo=startupinfo,
        cwd=cwd,
        env=child_env,
    )
    resolve_web_base(web_port_file, timeout=5.0)
    return web_process


def initialize_core_threads(ui: "HextechUI") -> None:
    threads = [
        threading.Thread(target=lcu_polling_loop, args=(ui,), daemon=True),
        threading.Thread(target=window_sync_loop, args=(ui,), daemon=True),
        threading.Thread(target=run_terminal_loop, args=(ui,), daemon=True),
    ]
    ui.threads.extend(threads)
    for thread in threads:
        thread.start()


def run_terminal_loop(ui: "HextechUI") -> None:
    while not ui.stop_event.is_set():
        with ui._df_lock:
            is_empty = ui.df.empty
        if not is_empty:
            break
        time.sleep(0.5)
    if not ui.stop_event.is_set():
        with ui._df_lock:
            df_snapshot = ui.df
        main_query(shared_df=df_snapshot, ui_instance=ui)


def run_silent_sync(ui: "HextechUI", refresh_backend_data) -> None:
    """启动阶段执行一次静默刷新，并在完成后把结果回灌到 UI。"""
    try:
        refresh_backend_data(force=False, stop_event=ui.stop_event)
        if ui.stop_event.is_set():
            return
        ui._reload_data_into_ui("数据同步完成", "#a6e3a1")
    except Exception:
        logger.exception("启动阶段后台刷新失败。")
        ui._run_on_ui_thread(lambda: ui._set_status("数据同步失败", "#f38ba8"))


def handle_hero_click(ui: "HextechUI", champ_id, hero_name) -> None:
    try:
        set_last_hero(hero_name)
    except Exception:
        logger.debug("记录最近一次英雄选择失败。", exc_info=True)

    def terminal_task():
        try:
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.flush()
            with ui._df_lock:
                df_snapshot = ui.df
            display_hero_hextech(df_snapshot, hero_name, is_from_ui=True)
        except Exception as exc:
            print(f"\n输出错误: {exc}")

    threading.Thread(target=terminal_task, daemon=True).start()

    def redirect_task():
        en_name = ui.core_data.get(str(champ_id), {}).get("en_name", "")
        for _ in range(3):
            web_base = resolve_web_base(ui.web_port_file, timeout=1.0)
            try:
                resp = requests.post(
                    f"{web_base}/api/redirect",
                    json={"hero_id": str(champ_id), "hero_name": hero_name},
                    timeout=1.5,
                )
                if resp.status_code == 200:
                    return
            except Exception:
                logger.debug("请求 /api/redirect 失败，准备重试。", exc_info=True)
            time.sleep(0.4)
        logger.debug("请求 /api/redirect 多次失败，回退到本地浏览器打开。")
        url = (
            f"{web_base}/detail.html"
            f"?hero={quote(hero_name)}"
            f"&id={champ_id}"
            f"&en={quote(en_name)}"
            f"&auto=1"
        )
        webbrowser.open(url)

    threading.Thread(target=redirect_task, daemon=True).start()


def lcu_polling_loop(ui: "HextechUI") -> None:
    """优先读取 Web live_state，失败时回退本地 LCU，持续同步可用英雄集合。"""
    disable_lcu_https_warning()
    while not ui.stop_event.is_set():
        if ui.pause_event.is_set():
            time.sleep(1)
            continue

        available_ids = None
        try:
            web_base = resolve_web_base(ui.web_port_file, timeout=1.0)
            res = ui.session.get(f"{web_base}/api/live_state", timeout=2)
            if res.status_code == 200:
                data = res.json()
                available_ids = {str(champ_id) for champ_id in data.get("champion_ids", []) if str(champ_id).strip()}
        except Exception:
            available_ids = None

        if available_ids is None:
            available_ids = poll_lcu_live_ids(ui)
        if available_ids is None:
            available_ids = set()

        if available_ids != ui.current_hero_ids:
            ui.current_hero_ids = available_ids.copy()
            ui.root.after(0, ui.update_ui, available_ids)
        time.sleep(1.5)


def load_and_set_img(ui: "HextechUI", champ_id, label) -> None:
    """加载英雄头像，优先命中本地缓存，缺失时远端下载后回写到本地。"""
    try:
        if not label.winfo_exists():
            return
        if champ_id in ui.image_cache:
            label.config(image=ui.image_cache[champ_id])
            return

        img_path = os.path.join(ASSET_DIR, f"{champ_id}.png")
        if os.path.exists(img_path):
            with Image.open(img_path) as raw_img:
                img = raw_img.resize((48, 48), Image.Resampling.LANCZOS)
        else:
            if champ_id in ui.downloading_imgs:
                return
            ui.downloading_imgs.add(champ_id)
            url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/champion-icons/{champ_id}.png"
            try:
                res = ui.session.get(url, verify=True, timeout=10)
                if res.status_code != 200:
                    return
                with ui.img_write_lock:
                    with open(img_path, "wb") as f:
                        f.write(res.content)
                with Image.open(BytesIO(res.content)) as raw_img:
                    img = raw_img.resize((48, 48), Image.Resampling.LANCZOS)
            finally:
                ui.downloading_imgs.discard(champ_id)

        photo = ImageTk.PhotoImage(img)
        ui.image_cache[champ_id] = photo
        if label.winfo_exists():
            label.config(image=photo)
    except Exception:
        logger.exception("加载英雄头像失败：champ_id=%s", champ_id)


def window_sync_loop(ui: "HextechUI") -> None:
    """根据客户端和游戏窗口前后台状态控制伴生窗口显隐与吸附。"""
    while not ui.stop_event.is_set():
        if ui.pause_event.is_set():
            time.sleep(1)
            continue
        try:
            hwnd_client = win32gui.FindWindow(None, "League of Legends")
            hwnd_game = win32gui.FindWindow(None, "League of Legends (TM) Client")

            if hwnd_game:
                ui._hide_overlay()
            elif hwnd_client:
                fg_window = win32gui.GetForegroundWindow()
                is_client_fg = fg_window == hwnd_client
                is_self_fg = "Hextech" in win32gui.GetWindowText(fg_window)

                if is_client_fg or is_self_fg:
                    ui._show_overlay(topmost=True)
                    if not getattr(ui, "_init_pos", False) and is_client_fg:
                        rect = win32gui.GetWindowRect(hwnd_client)
                        ui.root.geometry(f"320x600+{rect[2]}+{rect[1]}")
                        ui._init_pos = True
                else:
                    ui._hide_overlay()
            else:
                ui._hide_overlay()
        except Exception:
            logger.exception("窗口同步循环异常。")
        time.sleep(0.5)


def start_background_scraper(ui: "HextechUI", refresh_backend_data) -> None:
    """启动桌面端后台刷新线程，按固定周期执行自愈和数据同步。"""
    def scraper_loop():
        while not ui.stop_event.is_set():
            try:
                refresh_backend_data(force=False, stop_event=ui.stop_event)
                if ui.stop_event.is_set():
                    return
                ui._reload_data_into_ui("数据同步完成", "#a6e3a1")
            except Exception:
                logger.exception("定时后台刷新失败。")
                ui._run_on_ui_thread(lambda: ui._set_status("后台刷新失败", "#f38ba8"))

            for _ in range(14400):
                if ui.stop_event.is_set():
                    break
                time.sleep(1)

    threading.Thread(target=scraper_loop, daemon=True).start()
