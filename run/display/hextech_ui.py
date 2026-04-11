"""桌面 UI 主入口。

这个文件保留 Tk 界面结构、主状态对象和主要交互方法。
后台线程、Web 协同、LCU 查询和头像下载等运行时细节委托给 `display.ui_runtime`，
以便在保持热路径聚合的前提下，让后续需求变更有明确落点。
"""

import ctypes
import logging
import os
import sys
import threading

import tkinter as tk
from processing.runtime_store import CachedDataFrameLoader, detect_hero_id_column, get_latest_csv
from scraping.version_sync import (
    ASSET_DIR,
    CONFIG_DIR,
    get_advanced_session,
    load_champion_core_data,
)

from . import ui_runtime

WEB_PORT_FILE = os.path.join(CONFIG_DIR, "web_server_port.txt")

os.makedirs(ASSET_DIR, exist_ok=True)
logger = logging.getLogger(__name__)

try:
    from processing.orchestrator import refresh_backend_data
except ImportError:
    print("缺少核心依赖模块，请确认文件结构完整。")
    sys.exit(1)


class HextechUI:
    """桌面伴生主界面，负责持有 UI 状态并协调后台运行时任务。"""

    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            logger.debug("设置 DPI 感知失败。", exc_info=True)

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.threads = []
        self.web_port_file = WEB_PORT_FILE

        self.session = get_advanced_session()
        self.core_data = load_champion_core_data()
        self._data_loader = CachedDataFrameLoader(get_latest_csv)

        self.df = self.load_data()
        self.current_hero_ids = set()
        self.image_cache = {}
        self._lcu_port = None
        self._lcu_token = None

        self.last_click_time = 0
        self.img_write_lock = threading.Lock()
        self.downloading_imgs = set()
        self._df_lock = threading.Lock()
        self._window_topmost = False
        self._window_visible = False

        self.web_process = None
        self._start_web_server()

        self.root = tk.Tk()
        self.root.title("Hextech 伴生系统")
        self.root.geometry("320x600")
        self.root.configure(bg="#1e1e2e")
        self.root.attributes("-alpha", 0.85, "-topmost", False)
        self.root.overrideredirect(True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.withdraw()

        self._build_ui()
        self._init_core_engine()
        self.check_and_sync_data()
        self.start_background_scraper()

    def _start_web_server(self):
        """后台启动网页服务，避免阻塞界面线程。"""

        try:
            self.web_process = ui_runtime.start_web_server_process(self.web_port_file)
        except Exception as exc:
            print(f"\n启动网页服务失败: {exc}")

    def _init_core_engine(self):
        ui_runtime.initialize_core_threads(self)

    def _run_terminal(self):
        ui_runtime.run_terminal_loop(self)

    def _build_ui(self):
        self.title_frame = tk.Frame(self.root, bg="#11111b")
        self.title_frame.pack(fill=tk.X)

        self.title_bar = tk.Label(
            self.title_frame,
            text="备战席",
            bg="#11111b",
            fg="#cdd6f4",
            font=("Microsoft YaHei", 12, "bold"),
            pady=8,
        )
        self.title_bar.pack(side=tk.LEFT, padx=(10, 0))
        self.title_bar.bind("<ButtonPress-1>", self.start_move)
        self.title_bar.bind("<B1-Motion>", self.do_move)

        self.canvas = tk.Canvas(self.root, bg="#1e1e2e", highlightthickness=0)
        self.list_frame = tk.Frame(self.canvas, bg="#1e1e2e")
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)
        self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")

        self.root.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        self.list_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        self.status_label = tk.Label(
            self.root,
            text="系统初始化中...",
            bg="#1e1e2e",
            fg="#a6adc8",
            font=("Microsoft YaHei", 9),
        )
        self.status_label.pack(side=tk.BOTTOM, pady=5)

    def check_and_sync_data(self):
        threading.Thread(target=self._silent_sync, daemon=True).start()

    def _set_status(self, text, color):
        if hasattr(self, "status_label") and self.status_label.winfo_exists():
            self.status_label.config(text=text, fg=color)

    def _run_on_ui_thread(self, callback):
        root = getattr(self, "root", None)
        if root is None:
            return False
        try:
            root.after(0, callback)
            return True
        except tk.TclError:
            return False

    def _set_window_topmost(self, enabled: bool) -> None:
        if self._window_topmost == enabled:
            return
        try:
            self.root.attributes("-topmost", enabled)
            if enabled:
                self.root.lift()
            self._window_topmost = enabled
        except tk.TclError:
            logger.debug("切换窗口置顶状态失败。", exc_info=True)

    def _show_overlay(self, topmost: bool = True) -> None:
        try:
            self.root.deiconify()
            self._set_window_topmost(topmost)
            self.root.update_idletasks()
            self._window_visible = True
        except tk.TclError:
            logger.debug("显示悬浮窗失败。", exc_info=True)

    def _hide_overlay(self) -> None:
        try:
            self._set_window_topmost(False)
            self.root.withdraw()
            self._window_visible = False
        except tk.TclError:
            logger.debug("隐藏悬浮窗失败。", exc_info=True)

    def _reload_data_into_ui(self, status_text, status_color):
        new_df = self.load_data()

        def _update_on_main():
            with self._df_lock:
                self.df = new_df
            self._set_status(status_text, status_color)

        if not self._run_on_ui_thread(_update_on_main):
            with self._df_lock:
                self.df = new_df

    def _silent_sync(self):
        ui_runtime.run_silent_sync(self, refresh_backend_data)

    def load_data(self):
        return self._data_loader.get_df().copy()

    def on_hero_click(self, champ_id, hero_name):
        """处理英雄卡片点击，并触发终端输出与页面跳转。"""

        ui_runtime.handle_hero_click(self, champ_id, hero_name)

    def lcu_polling_loop(self):
        ui_runtime.lcu_polling_loop(self)

    def _load_and_set_img(self, champ_id, label):
        ui_runtime.load_and_set_img(self, champ_id, label)

    def update_ui(self, hero_ids):
        for widget in self.list_frame.winfo_children():
            widget.destroy()

        with self._df_lock:
            is_empty = self.df.empty

        if not hero_ids or is_empty:
            tk.Label(
                self.list_frame,
                text="当前没有可用英雄，或数据仍在同步中...",
                fg="#f9e2af",
                bg="#1e1e2e",
                font=("Microsoft YaHei", 10),
            ).pack(pady=20)
            return

        self.status_label.config(text="实时数据已挂载", fg="#a6e3a1")
        display_list = []

        with self._df_lock:
            current_df = self.df

        id_col = detect_hero_id_column(current_df)
        for hid in hero_ids:
            if id_col:
                h_data = current_df[current_df[id_col] == hid]
                if not h_data.empty:
                    row = h_data.iloc[0]
                    id_val = row.get(id_col, row.get("英雄 ID", row.get("ID", hid)))
                    name = row.get("英雄名称", row.get("英雄名", "未知"))
                    win = float(row.get("英雄胜率", row.get("胜率", 0.5)))
                    pick = float(row.get("英雄出场率", row.get("出场率", 0.1)))
                    tier = row.get("英雄评级", row.get("评级", "T?"))
                    display_list.append({"id": id_val, "name": name, "win": win, "pick": pick, "tier": tier})

        display_list = sorted(display_list, key=lambda item: item["win"], reverse=True)

        for item in display_list:
            card = tk.Frame(self.list_frame, bg="#313244", pady=5, padx=5, cursor="hand2")
            card.pack(fill=tk.X, pady=4, padx=(0, 10))

            img_label = tk.Label(card, bg="#313244")
            img_label.pack(side=tk.LEFT, padx=(0, 10))
            threading.Thread(target=lambda i=item["id"], l=img_label: self._load_and_set_img(i, l), daemon=True).start()

            info = tk.Frame(card, bg="#313244")
            info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            title = self.core_data.get(str(item["id"]), {}).get("title", "")
            full_name = f"{item['name']} {title}".strip() if title else item["name"]

            tk.Label(
                info,
                text=f"[{item['tier']}] {full_name}",
                font=("Microsoft YaHei", 10, "bold"),
                fg="#cdd6f4",
                bg="#313244",
            ).pack(anchor="w")
            tk.Label(
                info,
                text=f"胜率: {item['win']:.1%} | 出场: {item['pick']:.1%}",
                font=("Microsoft YaHei", 9),
                fg="#a6adc8",
                bg="#313244",
            ).pack(anchor="w", pady=(3, 0))

            bar_canvas = tk.Canvas(info, height=4, bg="#1e1e2e", highlightthickness=0)
            bar_canvas.pack(fill=tk.X, pady=(4, 0))
            bar_color = "#a6e3a1" if item["win"] >= 0.51 else ("#f9e2af" if item["win"] >= 0.48 else "#f38ba8")
            ratio = max(0, min(1, (item["win"] - 0.40) / 0.20))

            bar_canvas.bind(
                "<Configure>",
                lambda e, c=bar_canvas, r=ratio, col=bar_color: (
                    c.delete("all"),
                    c.create_rectangle(0, 0, int(r * e.width), 4, fill=col, outline=""),
                ),
            )

            def bind_click(widget, cid, name):
                widget.bind("<Button-1>", lambda e, c=cid, n=name: self.on_hero_click(c, n))
                for child in widget.winfo_children():
                    bind_click(child, cid, name)

            bind_click(card, item["id"], item["name"])

    def window_sync_loop(self):
        ui_runtime.window_sync_loop(self)

    def start_move(self, event):
        self.x, self.y = event.x, event.y

    def do_move(self, event):
        self.root.geometry(f"+{self.root.winfo_x() + (event.x - self.x)}+{self.root.winfo_y() + (event.y - self.y)}")

    def _restore_from_terminal(self):
        self.pause_event.clear()
        self._show_overlay(topmost=True)

    def start_background_scraper(self):
        """启动后台数据刷新循环。"""

        ui_runtime.start_background_scraper(self, refresh_backend_data)

    def on_close(self):
        print("\n[System] 收到退出信号，正在等待数据安全落盘...")
        self.stop_event.set()
        for thread in self.threads:
            if thread.is_alive():
                thread.join(timeout=2)
        if getattr(self, "web_process", None):
            try:
                self.web_process.terminate()
            except Exception:
                pass
        self.root.destroy()


def run_desktop():
    """启动桌面伴生窗口。"""

    HextechUI().root.mainloop()


if __name__ == "__main__":
    run_desktop()
