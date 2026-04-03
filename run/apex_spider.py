# 海克斯信息爬虫。
# 使用请求库和解析库抓取页面，再用线程池并发处理。

import logging
import json
import os
import random
import time
import tempfile
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CONFIG_PATH = Path(CONFIG_DIR)
ALLOWED_CONFIG_FILES = {
    "Champion_Core_Data.json",
    "hero_aliases.json",
    "Champion_Synergy.json",
}
MAX_CONFIG_FILE_SIZE = 10 * 1024 * 1024
MAX_FETCH_RETRIES = 3
REQUEST_TIMEOUT_SECONDS = 10
RETRY_BACKOFF_FACTOR = 0.5
THREAD_POOL_WORKERS = 8
THREAD_POOL_TIMEOUT_SECONDS = 300
OUTPUT_LOCK_TIMEOUT_SECONDS = 30
OUTPUT_LOCK_POLL_INTERVAL_SECONDS = 0.2

# 日志配置。
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger(__name__)

# 常见桌面浏览器请求标识池。
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
]


def get_random_user_agent() -> str:
    # 随机选择请求标识。
    return random.choice(USER_AGENTS)


def normalize_name(name_str: str) -> str:
    # 规范化英雄名称。
    if not name_str:
        return ""
    return name_str.replace(" ", "").replace("-", "").replace("'", "").replace(".", "").lower()


def _sanitize_url_for_log(url: str) -> str:
    # 日志中仅保留到路径级别，隐藏查询参数和 fragment。
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url[:200]

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            "",
            "",
        )
    )


def _resolve_config_path(filename: str) -> Path:
    # 将配置文件限制在固定白名单内，避免路径穿越。
    if filename not in ALLOWED_CONFIG_FILES:
        raise ValueError(f"不允许访问的配置文件：{filename}")

    resolved = (CONFIG_PATH / filename).resolve()
    if CONFIG_PATH.resolve() not in resolved.parents:
        raise ValueError(f"配置文件路径越界：{filename}")
    return resolved


def _load_json_file(filename: str, expected_kind: str) -> dict:
    # 读取受限配置文件并做基本结构校验。
    file_path = _resolve_config_path(filename)
    if not file_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{filename}")

    if file_path.stat().st_size > MAX_CONFIG_FILE_SIZE:
        raise ValueError(f"配置文件过大：{filename}")

    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"{expected_kind} 配置格式错误：{filename}")

    if expected_kind == "core_data":
        for champ_id, champ_info in data.items():
            if not isinstance(champ_id, str) or not isinstance(champ_info, dict):
                raise ValueError(f"{expected_kind} 配置内容格式错误：{filename}")
    elif expected_kind == "aliases":
        for alias_name, alias_values in data.items():
            if not isinstance(alias_name, str) or not isinstance(alias_values, list):
                raise ValueError(f"{expected_kind} 配置内容格式错误：{filename}")

    return data


@contextmanager
def _output_file_lock(lock_path: Path, timeout_seconds: int = OUTPUT_LOCK_TIMEOUT_SECONDS):
    # 用锁文件串行化输出，避免并发实例同时写入。
    deadline = time.monotonic() + timeout_seconds
    lock_fd = None

    try:
        while True:
            try:
                lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                try:
                    stale_age = time.time() - lock_path.stat().st_mtime
                    if stale_age > timeout_seconds * 4:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"等待输出锁超时：{lock_path.name}")
                time.sleep(OUTPUT_LOCK_POLL_INTERVAL_SECONDS)

        yield
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_json(output_path: Path, payload: dict) -> None:
    # 先写临时文件，再原子替换，避免中断导致 JSON 损坏。
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(output_path.parent),
            delete=False,
            suffix=".tmp",
        ) as f:
            temp_path = Path(f.name)
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, output_path)
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _safe_exception_label(exc: Exception) -> str:
    return exc.__class__.__name__


class ApexSpider:
    # 轻量级爬虫类，支持线程池并发抓取静态页面。

    def __init__(self):
        # 初始化爬虫会话和重试机制。
        self.base_url = os.environ.get("APEX_BASE_URL", "https://apexlol.info/zh").rstrip("/")
        parsed_base = urlparse(self.base_url)
        if parsed_base.scheme != "https" or not parsed_base.netloc:
            raise ValueError("APEX_BASE_URL 必须是有效的 https URL")
        self.allowed_netloc = parsed_base.netloc
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": get_random_user_agent()
        })

        logger.info(f"ApexSpider 初始化完成，User-Agent: {self.session.headers['User-Agent'][:50]}...，单层重试已启用")

    def _sanitize_log_url(self, url: str) -> str:
        return _sanitize_url_for_log(url)

    def _is_allowed_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and parsed.netloc == self.allowed_netloc
        )

    def _build_allowed_detail_url(self, href: str) -> Optional[str]:
        candidate = urljoin(f"{self.base_url}/", href.strip())
        if not self._is_allowed_url(candidate):
            logger.warning(f"跳过非白名单链接：{self._sanitize_log_url(candidate)}")
            return None
        parsed = urlparse(candidate)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    def fetch_page(self, url: str) -> Optional[str]:
        # 获取页面内容，失败返回 None。
        retryable_status_codes = {429, 500, 502, 503, 504}

        if not self._is_allowed_url(url):
            logger.warning(f"拒绝非白名单请求：{self._sanitize_log_url(url)}")
            return None

        for attempt in range(MAX_FETCH_RETRIES + 1):
            try:
                logger.info(
                    f"正在加载页面：{self._sanitize_log_url(url)} "
                    f"(尝试 {attempt + 1}/{MAX_FETCH_RETRIES + 1})"
                )
                response = self.session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
                response.encoding = 'utf-8'

                if response.status_code == 200:
                    return response.text
                elif response.status_code in retryable_status_codes and attempt < MAX_FETCH_RETRIES:
                    delay = RETRY_BACKOFF_FACTOR * (2 ** attempt)
                    logger.warning(
                        f"页面返回可重试状态码：{response.status_code}, "
                        f"URL: {self._sanitize_log_url(url)}, 将在 {delay:.2f} 秒后重试..."
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"页面返回状态码异常：{response.status_code}, "
                        f"URL: {self._sanitize_log_url(url)}"
                    )
                    return None

            except requests.Timeout:
                if attempt < MAX_FETCH_RETRIES:
                    delay = RETRY_BACKOFF_FACTOR * (2 ** attempt)
                    logger.warning(
                        f"页面加载超时 - URL: {self._sanitize_log_url(url)}, "
                        f"将在 {delay:.2f} 秒后重试..."
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"页面加载超时 - URL: {self._sanitize_log_url(url)}")
                    return None
            except requests.RequestException as e:
                if attempt < MAX_FETCH_RETRIES:
                    delay = RETRY_BACKOFF_FACTOR * (2 ** attempt)
                    logger.warning(
                        f"页面加载失败 - URL: {self._sanitize_log_url(url)}, "
                        f"错误：{_safe_exception_label(e)}, 将在 {delay:.2f} 秒后重试..."
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"页面加载失败 - URL: {self._sanitize_log_url(url)}, "
                        f"错误：{_safe_exception_label(e)}"
                    )
                    return None
            except Exception as e:
                logger.error(
                    f"页面加载异常 - URL: {self._sanitize_log_url(url)}, "
                    f"错误：{_safe_exception_label(e)}"
                )
                return None

    def crawl_champion_list(self) -> dict:
        # 爬取英雄列表并返回名称和详情页地址。
        url = f"{self.base_url}/champions"
        logger.info(f"开始爬取英雄列表：{self._sanitize_log_url(url)}")

        result = {
            "success": False,
            "url": url,
            "champions": [],
            "error": None
        }

        try:
            html = self.fetch_page(url)
            if html is None:
                result["error"] = "页面加载失败"
                return result

            soup = BeautifulSoup(html, 'html.parser')

            champ_cards = soup.select('.champ-card')
            logger.info(f"找到 {len(champ_cards)} 个英雄卡片")

            champions = []
            for card in champ_cards:
                try:
                    name_elem = card.select_one('.name')
                    if not name_elem:
                        continue

                    name = name_elem.get_text(strip=True)

                    href = card.get('href')
                    if href:
                        full_url = self._build_allowed_detail_url(href)
                        if full_url:
                            champions.append({"name": name, "url": full_url})
                            logger.info(f"提取英雄：{name} -> {self._sanitize_log_url(full_url)}")
                except Exception as e:
                    logger.warning(f"单个英雄卡片提取失败：{_safe_exception_label(e)}")
                    continue

            if champions:
                logger.info(f"成功提取 {len(champions)} 个英雄（含 URL）")
                result["champions"] = champions
                result["success"] = True
            else:
                logger.warning("未找到匹配的英雄元素")

        except Exception as e:
            logger.error(
                f"爬虫执行异常 - URL: {self._sanitize_log_url(url)}, "
                f"错误：{_safe_exception_label(e)}"
            )
            result["error"] = "英雄列表解析异常"

        return result

    def extract_hextech_synergies(self, detail_url: str) -> list:
        # 提取英雄详情页中的海克斯协同方案。
        logger.info(f"开始提取海克斯协同方案：{self._sanitize_log_url(detail_url)}")
        result = []

        try:
            html = self.fetch_page(detail_url)
            if html is None:
                logger.error(f"详情页加载失败：{self._sanitize_log_url(detail_url)}")
                return result

            soup = BeautifulSoup(html, 'html.parser')

            cards = soup.select('.interaction-card')
            logger.info(f"找到 {len(cards)} 个交互卡片")

            for card in cards:
                try:
                    has_synergy_tag = False

                    tag_elements = card.select('span.tag-badge')
                    for tag_elem in tag_elements:
                        classes = tag_elem.get('class', [])
                        # 检查是否有协同方案相关的类名
                        if 'tag-synergy' in classes or 'tag-trap' in classes or 'tag-fun' in classes:
                            has_synergy_tag = True
                            break

                    if has_synergy_tag:
                        # 使用文本提取函数，并以“ | ”分隔多行
                        text = card.get_text(separator=' | ', strip=True)
                        if text:
                            result.append(text)
                            logger.info(f"提取到协同方案：{text[:50]}...")
                except Exception as e:
                    logger.warning(f"单个卡片提取失败：{_safe_exception_label(e)}")
                    continue

            logger.info(f"成功提取 {len(result)} 个海克斯协同方案")
            return result

        except Exception as e:
            logger.error(
                f"提取异常 - URL: {self._sanitize_log_url(detail_url)}, "
                f"错误：{_safe_exception_label(e)}"
            )
            return result


def main():
    # 主函数入口
    logger.info("=" * 50)
    logger.info("ApexLoL 超频并发爬虫启动")
    logger.info("=" * 50)

    # 创建爬虫实例
    spider = ApexSpider()

    # 爬取英雄列表
    logger.info("-" * 30)
    logger.info("（任务 1）爬取英雄列表")
    champion_result = spider.crawl_champion_list()

    if champion_result["success"]:
        logger.info(f"英雄列表爬取成功，共 {len(champion_result['champions'])} 条数据")
        for champ in champion_result["champions"][:3]:
            logger.info(
                f"  - {champ['name']} -> {_sanitize_url_for_log(champ['url'])}"
            )
    else:
        logger.error(f"英雄列表爬取失败：{champion_result.get('error')}")
        return

    logger.info("=" * 50)

    # 加载本地配置文件
    logger.info("-" * 30)
    logger.info("（任务 2）加载本地英雄配置")

    try:
        core_data = _load_json_file("Champion_Core_Data.json", "core_data")
        logger.info(f"核心数据加载成功：{len(core_data)} 个英雄")
    except (FileNotFoundError, PermissionError, ValueError, json.JSONDecodeError) as e:
        logger.error(f"核心数据加载失败：{_safe_exception_label(e)}")
        return
    except Exception as e:
        logger.error(f"核心数据加载失败：{_safe_exception_label(e)}")
        return

    # 构建输出用的完整数据字典，包含核心字段和别名列表
    core_info_dict = {}
    for champ_id, champ_info in core_data.items():
        name = champ_info.get("name")
        if name:
            core_info_dict[champ_id] = {
                "id": champ_id,
                "name": name,
                "title": champ_info.get("title", ""),
                "en_name": champ_info.get("en_name", ""),
                "aliases": champ_info.get("aliases", [])
            }

    # 构建网页名称匹配索引，统一纳入名称、称号、英文名和别名
    search_index = {}
    for champ_id, champ_info in core_info_dict.items():
        # 将名称、称号、英文名和别名加入搜索索引
        names_to_index = [
            champ_info["name"],
            champ_info["title"],
            champ_info["en_name"],
            *champ_info.get("aliases", [])
        ]

        for name_field in names_to_index:
            if name_field:
                normalized = normalize_name(name_field)
                if normalized:
                    search_index[normalized] = champ_id

    logger.info(f"构建核心数据字典：{len(core_info_dict)} 个英雄")
    logger.info(f"构建搜索索引：{len(search_index)} 个关键词")

    # 全量遍历英雄列表并提取海克斯协同方案，使用线程池并发执行
    logger.info("-" * 30)
    logger.info(f"（任务 3）全量提取海克斯协同方案（{THREAD_POOL_WORKERS} 线程并发）")

    # 初始化最终数据字典
    final_data = {}

    # 获取英雄列表（全量，移除之前的[:3]限制）
    champions = champion_result.get("champions", [])
    if champions:
        logger.info(f"开始遍历 {len(champions)} 个英雄的海克斯协同方案（并发处理）...")

        # 构建任务字典：地址对应英雄信息
        task_map = {}
        skipped_names = []

        for champ in champions:
            champ_name = champ["name"]
            champ_url = champ["url"]

            # 对网页提取的英雄名做同样清洗，再去搜索索引中查找编号
            normalized_champ_name = normalize_name(champ_name)
            champ_id = search_index.get(normalized_champ_name)

            if not champ_id:
                skipped_names.append(champ_name)
                continue

            # 从核心信息字典中取出完整信息并组装任务
            core_info = core_info_dict[champ_id]
            task_map[champ_url] = {
                "name": core_info["name"],
                "id": champ_id,
                "title": core_info["title"],
                "en_name": core_info["en_name"],
                "aliases": core_info["aliases"]
            }

        # 调试信息
        if skipped_names:
            logger.warning(f"未匹配的英雄名称数: {len(skipped_names)}")
            if len(skipped_names) <= 10:
                for name in skipped_names[:5]:
                    logger.warning(f"  示例: {repr(name)}")

        logger.info(f"成功匹配 {len(task_map)} 个英雄用于并发抓取")

        # 使用线程池进行并发抓取（将工作线程数从 16 调整为 8）
        executor = ThreadPoolExecutor(max_workers=THREAD_POOL_WORKERS)
        try:
            # 提交所有任务
            future_to_url = {
                executor.submit(spider.extract_hextech_synergies, url): url
                for url in task_map.keys()
            }

            # 收集完成的任务结果
            try:
                for future in as_completed(future_to_url, timeout=THREAD_POOL_TIMEOUT_SECONDS):
                    champ_url = future_to_url[future]
                    try:
                        synergies = future.result()
                        champ_info = task_map[champ_url]
                        champ_id = champ_info["id"]
                        champ_name = champ_info["name"]

                        # 合并数据结构
                        final_data[champ_id] = {
                            "id": champ_id,
                            "name": champ_name,
                            "title": champ_info["title"],
                            "en_name": champ_info["en_name"],
                            "aliases": champ_info["aliases"],
                            "synergies": synergies
                        }

                        logger.info(f"[{champ_name}] 提取完成，共 {len(synergies)} 个协同方案")

                    except Exception as e:
                        logger.error(
                            f"并发任务异常 - URL: {_sanitize_url_for_log(champ_url)}, "
                            f"错误：{_safe_exception_label(e)}"
                        )
                        continue
            except TimeoutError:
                logger.error("并发抓取超时：已取消未完成任务")
                for future in future_to_url:
                    future.cancel()
            except Exception as e:
                logger.error(f"并发抓取异常：{_safe_exception_label(e)}")
                for future in future_to_url:
                    future.cancel()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        # 持久化到数据文件
        output_path = _resolve_config_path("Champion_Synergy.json")
        lock_path = output_path.with_suffix(output_path.suffix + ".lock")
        with _output_file_lock(lock_path):
            _atomic_write_json(output_path, final_data)

        logger.info(f"数据已保存到：{output_path}")
        logger.info(f"Total heroes captured: {len(final_data)}")
    else:
        logger.error("英雄列表为空，无法提取协同方案")

    logger.info("=" * 50)
    logger.info("爬虫执行完成")
    logger.info("=" * 50)

    return {
        "champions": champion_result,
        "hextech_data": final_data
    }


if __name__ == "__main__":
    main()
