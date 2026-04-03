import requests
import json
import time
import pandas as pd
from datetime import datetime
import os
import glob
import re
import urllib3
import logging
import threading
import random
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from hero_sync import get_advanced_session, CONFIG_DIR, load_augment_map, load_champion_core_data

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
FRESHNESS_THRESHOLD = 0.0005

# 请求标识池。
USER_AGENT_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

def get_random_ua():
    # 随机选择请求标识。
    return random.choice(USER_AGENT_POOL)


def fetch_with_retry(session, url, max_retries=3, timeout=10):
    # 指数退避重试。
    last_exception = None
    for attempt in range(max_retries):
        try:
            headers = {"User-Agent": get_random_ua()}
            response = session.get(url, headers=headers, timeout=timeout, verify=True)
            response.raise_for_status()
            return response
        except (requests.exceptions.HTTPError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_exception = e
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                logging.warning(f"请求 {url} 失败 (尝试 {attempt + 1}/{max_retries}): {e}，{wait_time}秒后重试...")
                time.sleep(wait_time)
            else:
                logging.warning(f"请求 {url} 失败，已达最大重试次数 {max_retries}: {e}")
    return None

def check_execution_permission():
    status_file = os.path.join(CONFIG_DIR, "scraper_status.json")
    now = time.time()
    if not os.path.exists(status_file):
        return True, "首次运行，启动抓取..."
    try:
        with open(status_file, "r") as f:
            last_run = json.load(f).get("last_success_time", 0)
            if datetime.fromtimestamp(now).date() > datetime.fromtimestamp(last_run).date():
                return True, "跨天自动同步..."
            if (now - last_run) / 3600 >= 4:
                return True, "数据过时，执行同步..."
            return False, "数据尚在有效期内，跳过抓取。"
    except Exception:
        return True, "状态文件异常，强制刷新..."

def update_status_file():
    with open(os.path.join(CONFIG_DIR, "scraper_status.json"), "w") as f:
        json.dump({"last_success_time": time.time()}, f)

def cleanup_old_csvs():
    # 清理过期数据和残留临时文件。
    files = glob.glob(os.path.join(CONFIG_DIR, "Hextech_Data_*.csv"))
    tmp_files = glob.glob(os.path.join(CONFIG_DIR, "Hextech_Data_*.csv.tmp"))
    now = datetime.now()

    for f in files + tmp_files:
        try:
            m = re.search(r"Hextech_Data_(\d{4}-\d{2}-\d{2})", os.path.basename(f))
            if not m: continue
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d")

            is_stale_csv = f.endswith('.csv') and (now - file_date).days > 3
            is_stale_tmp = f.endswith('.tmp') and (now - file_date).days > 1

            if is_stale_csv or is_stale_tmp:
                os.remove(f)
                logging.info(f"已清理过期/残留文件：{os.path.basename(f)}")
        except Exception as e:
            logging.error(f"清理文件异常 {f}: {e}")
def extract_champion_stats(html: str, aug_id_map: dict, truth_dict: dict, champ_id: str, champ_name: str, champ_data: dict) -> list:
    # 扫描页面并用内存字典完成匹配。
    rows = []

    cleaned_html = html.replace('\\"', '"').replace('\\\\', '\\')

    universal_pattern = re.compile(
        r'"(\d{4})"\s*:\s*\{[^{}]*?"(?:winRate|win_rate)"\s*:\s*"?([\d.]+)"?[^{}]*?"(?:pickRate|pick_rate)"\s*:\s*"?([\d.]+)"?',
        re.DOTALL
    )

    for match in universal_pattern.finditer(cleaned_html):
        mid = match.group(1)
        if mid in aug_id_map:
            try:
                win = float(match.group(2))
                pick = float(match.group(3))

                if pick > 1.0:
                    pick = pick / 100.0
                    logging.debug(f"[量纲转换] 海克斯 ID={mid}，出场率从百分数转换为小数：{pick*100:.1f}% -> {pick:.4f}")

                pick = min(1.0, pick)

                if win > 0 and pick >= FRESHNESS_THRESHOLD:
                    web_name = aug_id_map.get(mid, "")
                    local_tier = truth_dict.get(web_name)
                    if web_name and local_tier:
                        rows.append({
                            "英雄 ID": champ_id,
                            "英雄名称": champ_name,
                            "英雄评级": champ_data.get('tier', 'T3'),
                            "英雄胜率": float(champ_data.get('winRate', 0)),
                            "英雄出场率": float(champ_data.get('pickRate', 0)),
                            "海克斯阶级": local_tier,
                            "海克斯名称": web_name,
                            "海克斯胜率": win,
                            "海克斯出场率": pick
                        })
            except (ValueError, IndexError, AttributeError) as e:
                chunk_start = max(0, cleaned_html.find(mid) - 50)
                chunk_end = min(len(cleaned_html), cleaned_html.find(mid) + len(mid) + 150)
                chunk_snapshot = cleaned_html[chunk_start:chunk_end].replace('\n', '\\n')[:200]
                logging.warning(
                    f"[{champ_name}] 海克斯 ID={mid} 解析失败：{e} | "
                    f"上下文快照：{chunk_snapshot} | "
                    f"堆栈：{traceback.format_exc().strip()}"
                )
                continue

    return rows

def main_scraper(stop_event=None):
    current_date = datetime.now().strftime('%Y-%m-%d')
    output_csv = os.path.join(CONFIG_DIR, f"Hextech_Data_{current_date}.csv")

    can_run, msg = check_execution_permission()
    if not can_run:
        logging.info(f"数据尚在有效期内，跳过抓取：{msg}")
        return False

    logging.info(f"启动抓取任务：{msg}")
    truth_dict = load_augment_map()
    core_data = load_champion_core_data()
    if not truth_dict or not core_data:
        logging.error("基础数据加载失败，终止抓取。")
        return False

    session = get_advanced_session()

    try:
        aug_response = fetch_with_retry(session, "https://hextech.dtodo.cn/data/aram-mayhem-augments.zh_cn.json")
        if aug_response is None:
            logging.error("获取海克斯配置数据失败")
            return False
        aug_data = aug_response.json()

        aug_id_map = {
            str(k): v.get('displayName', '').strip()
            for k, v in aug_data.items()
        }

        stats_response = fetch_with_retry(session, "https://hextech.dtodo.cn/data/champions-stats.json")
        if stats_response is None:
            logging.error("获取英雄统计数据失败")
            return False
        stats_list = stats_response.json()
    except Exception as e:
        logging.error(f"抓取端握手异常：{e}")
        return False

    all_rows = []
    lock = threading.Lock()

    def fetch_champ(champ):
        c_id = str(champ.get('championId', ''))
        c_name = core_data.get(c_id, {}).get("name", c_id)
        url = f"https://hextech.dtodo.cn/zh-CN/champion-stats/{c_id}"
        champ_rows = []
        try:
            time.sleep(random.uniform(0.5, 1.5))

            res = fetch_with_retry(session, url)

            if res is not None and res.status_code == 200 and len(res.text) > 0:
                try:
                    champ_rows = extract_champion_stats(res.text, aug_id_map, truth_dict, c_id, c_name, champ)
                except ValueError as e:
                    logging.warning(f"[{c_name}] aug 解析失败：{e} | URL={url} | 响应长度={len(res.text)}")
        except Exception as e:
            logging.error(f"[{c_name}] HTTP 获取失败：{e} | URL={url} | 堆栈={traceback.format_exc().strip()}")

        return c_name, champ_rows

    logging.info(f"启动 16 线程超频抓取池，共 {len(stats_list)} 名英雄...")
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(fetch_champ, c) for c in stats_list]
        for f in as_completed(futures):
            if stop_event and stop_event.is_set():
                logging.info("收到用户强制退出信号，正在销毁爬虫线程池...")
                for fut in futures:
                    fut.cancel()
                executor.shutdown(wait=False)
                return False

            try:
                _, rows = f.result()
                with lock:
                    if rows:
                        all_rows.extend(rows)
            except Exception as e:
                logging.error(f"线程结果收集失败：{e}")

    if all_rows:
        df = pd.DataFrame(all_rows)
        df['胜率差'] = df['海克斯胜率'] - df['英雄胜率']

        wr_std = df['胜率差'].std()
        pr_std = df['海克斯出场率'].std()
        if wr_std == 0:
            wr_std = 1
        if pr_std == 0:
            pr_std = 1

        z_wr = (df['胜率差'] - df['胜率差'].mean()) / wr_std
        z_pr = (df['海克斯出场率'] - df['海克斯出场率'].mean()) / pr_std

        # 胜率差为正时增加出场率加成，为负时则扣减
        sign_mask = df['胜率差'].apply(lambda x: 1 if x >= 0 else -1)
        df['综合得分'] = z_wr * 0.85 + z_pr * 0.15 * sign_mask

        df.sort_values(
            by=['英雄名称', '海克斯阶级', '综合得分'],
            ascending=[True, True, False],
            inplace=True
        )

        # 数据量过低时直接拒绝覆盖结果
        if len(df) < 300:
            logging.error(f"数据熔断：有效行数 {len(df)} < 300，拒绝覆盖 CSV")
            return False

        # --- 原子化写入开始 ---
        tmp_csv = output_csv + ".tmp"
        df.to_csv(tmp_csv, index=False, encoding='utf-8-sig')
        # 使用操作系统级原子替换
        os.replace(tmp_csv, output_csv)
        # --- 原子化写入结束 ---

        update_status_file()
        cleanup_old_csvs()
        logging.info(f"抓取结束，固化至：{output_csv}")
        return True
    else:
        logging.error("抓取任务未能生成有效数据，请检查网络或数据源。")
        return False

if __name__ == "__main__":
    main_scraper()
