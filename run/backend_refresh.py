import logging
import os
import threading
import time
from typing import Optional

from apex_spider import main as run_apex_spider
from hero_sync import CONFIG_DIR, sync_hero_data
from hextech_query import get_latest_csv
from hextech_scraper import main_scraper

logger = logging.getLogger(__name__)

_refresh_lock = threading.Lock()
_refresh_lock_file = os.path.join(CONFIG_DIR, "backend_refresh.lock")
_synergy_file = os.path.join(CONFIG_DIR, "Champion_Synergy.json")
_synergy_stale_after = 24 * 3600
_stale_lock_after = 6 * 3600


def _acquire_file_lock() -> Optional[int]:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    while True:
        try:
            fd = os.open(_refresh_lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, f"{os.getpid()} {time.time()}".encode("utf-8"))
            return fd
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(_refresh_lock_file) > _stale_lock_after:
                    os.remove(_refresh_lock_file)
                    continue
            except OSError:
                pass
            return None


def _release_file_lock(fd: int) -> None:
    try:
        os.close(fd)
    finally:
        try:
            os.remove(_refresh_lock_file)
        except OSError:
            pass


def _should_refresh_synergy(force: bool) -> bool:
    if force or not os.path.exists(_synergy_file):
        return True
    try:
        return (time.time() - os.path.getmtime(_synergy_file)) > _synergy_stale_after
    except OSError:
        return True


def _stop_requested(stop_event) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def refresh_backend_data(force: bool = False, stop_event=None) -> bool:
    # 刷新桌面界面和网页层共享的运行数据。
    #
    # 先刷新英雄基础数据，再在后台线程中启动协同数据抓取，
    # 这样不会阻塞海克斯数据的刷新流程。
    with _refresh_lock:
        lock_fd = _acquire_file_lock()
        if lock_fd is None:
            logger.info("后台刷新已在进行中，跳过本次请求。")
            return False

        try:
            logger.info("开始刷新后端运行数据。")
            hero_ok = sync_hero_data()
            if _stop_requested(stop_event):
                logger.info("刷新在英雄基础数据完成后被中止。")
                return False

            synergy_needed = _should_refresh_synergy(force)
            synergy_started = False
            if synergy_needed:
                logger.info("触发 Champion_Synergy.json 后台刷新。")

                def _synergy_worker() -> None:
                    try:
                        run_apex_spider()
                        if os.path.exists(_synergy_file):
                            logger.info("Champion_Synergy.json 刷新完成。")
                        else:
                            logger.warning("协同刷新线程结束，但 Champion_Synergy.json 未生成。")
                    except Exception:
                        logger.exception("Champion_Synergy.json 刷新失败。")

                threading.Thread(
                    target=_synergy_worker,
                    daemon=True,
                    name="apex-spider-refresh",
                ).start()
                synergy_started = True
            else:
                logger.info("Champion_Synergy.json 仍在有效期内，跳过刷新。")

            if _stop_requested(stop_event):
                logger.info("刷新在协同数据阶段后被中止。")
                return False

            hextech_result = bool(main_scraper(stop_event))
            if not hextech_result:
                latest_csv = get_latest_csv()
                hextech_result = bool(latest_csv and os.path.exists(latest_csv))

            logger.info(
                "后端刷新完成：hero_sync=%s, synergy_started=%s, hextech=%s",
                hero_ok,
                synergy_started or not synergy_needed,
                hextech_result,
            )
            return bool(hero_ok and hextech_result)
        finally:
            _release_file_lock(lock_fd)
