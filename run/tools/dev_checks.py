from __future__ import annotations

"""开发自检工具。

这个模块替代原先散落在 `run/tests/` 下的临时测试文件，用来做开发阶段的收口验证：
- 根目录薄入口收敛检查
- 手工别名索引只读检查
- 自愈调度检查
- 日志级别与落盘检查
- 打包配置检查

它不是正式测试框架的一部分，但可以作为本地开发工具或 CI 前置校验。
"""

import io
import json
import logging
import os
import sys
from pathlib import Path
from tempfile import mkstemp

RUN_DIR = Path(__file__).resolve().parents[1]
if str(RUN_DIR) not in sys.path:
    sys.path.insert(0, str(RUN_DIR))

import scraping.heal_worker as heal_worker
from processing.alias_search import load_manual_alias_index
from tools.log_utils import install_summary_logging


def check_root_entrypoints() -> None:
    root_scripts = {
        path.name
        for path in RUN_DIR.iterdir()
        if path.is_file() and path.suffix == ".py"
    }

    assert {"build.py", "hextech_ui.py", "web_server.py"}.issubset(root_scripts)
    assert (RUN_DIR / "display").exists()
    assert (RUN_DIR / "processing").exists()
    assert (RUN_DIR / "tools").exists()


def check_manual_alias_index() -> None:
    alias_file = RUN_DIR / "config" / "Champion_Alias_Index.json"
    payload = json.loads(alias_file.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert payload, "Champion_Alias_Index.json 应至少包含一条手工索引"
    first = payload[0]
    assert isinstance(first, dict)
    assert "heroName" in first
    assert load_manual_alias_index()


def check_heal_worker_contract() -> None:
    assert hasattr(heal_worker, "heal_missing_artifacts")
    assert hasattr(heal_worker, "detect_missing_artifacts")


def check_logging_contract() -> None:
    fd, tmp_name = mkstemp(prefix="hextech-dev-", suffix=".log")
    os.close(fd)
    try:
        file_handler = logging.FileHandler(tmp_name, encoding="utf-8")
        stream_buffer = io.StringIO()
        stream_handler = logging.StreamHandler(stream_buffer)

        install_summary_logging(handlers=[file_handler, stream_handler])

        assert file_handler.level == logging.ERROR
        assert stream_handler.level == logging.WARNING
        file_handler.close()
    finally:
        try:
            os.remove(tmp_name)
        except OSError:
            pass


def check_packaging_config() -> None:
    build_script = (RUN_DIR / "tools" / "build_bundle.py").read_text(encoding="utf-8")
    spec_text = (RUN_DIR / "Hextech伴生终端.spec").read_text(encoding="utf-8")

    assert "--hidden-import\", \"filelock\"" in build_script
    assert "filelock" in spec_text
    assert "display" in (RUN_DIR / "tools" / "bundle_manifest.py").read_text(encoding="utf-8")


def check_no_legacy_imports() -> None:
    legacy_hits = []
    for path in RUN_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "dev_checks.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "from app." in text or "from services." in text or "import app." in text or "import services." in text:
            legacy_hits.append(path)
    assert not legacy_hits, f"仍存在旧导入: {legacy_hits}"


def main() -> None:
    check_root_entrypoints()
    check_manual_alias_index()
    check_heal_worker_contract()
    check_logging_contract()
    check_packaging_config()
    check_no_legacy_imports()
    print("所有开发自检通过。")


if __name__ == "__main__":
    main()
