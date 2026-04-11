from __future__ import annotations

"""打包白名单清单生成器。

文件职责：
- 枚举稳定配置、静态页面和图片资源
- 生成构建期与运行期共用的 bundle manifest

核心输入：
- `config/`
- `assets/`
- `display/static/`

核心输出：
- bundle manifest 字典
- `_bundle_runtime/` 目录结构

主要依赖：
- `shutil`
- `json`

维护提醒：
- 这里只白名单稳定资源，不应把高频运行态文件误打进包里
"""

import json
import shutil
from pathlib import Path
from typing import Iterable


STABLE_CONFIG_FILES = (
    "Champion_Core_Data.json",
    "Champion_Alias_Index.json",
    "Augment_Icon_Manifest.json",
    "Augment_Icon_Map.json",
    "Augment_Full_Map.json",
    "Augment_Apexlol_Map.json",
    "hero_version.txt",
)
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
BUNDLE_MANIFEST_NAME = "bundle_manifest.json"


def iter_stable_asset_files(asset_dir: Path) -> Iterable[Path]:
    if not asset_dir.exists():
        return []
    return sorted(
        path for path in asset_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in ASSET_SUFFIXES
    )


def build_bundle_manifest(base_dir: Path) -> dict:
    """基于当前目录结构生成稳定资源白名单 manifest。"""
    config_dir = base_dir / "config"
    asset_dir = base_dir / "assets"
    static_dir = base_dir / "display" / "static"

    config_files = [
        name for name in STABLE_CONFIG_FILES if (config_dir / name).exists()
    ]
    asset_files = [
        path.relative_to(asset_dir).as_posix()
        for path in iter_stable_asset_files(asset_dir)
    ]

    return {
        "config_files": config_files,
        "asset_files": asset_files,
        "static_dirs": ["static"] if static_dir.exists() else [],
    }


def prepare_bundle_runtime(base_dir: Path, build_dir: Path) -> Path:
    """把 manifest 对应的稳定资源复制到临时 bundle 目录。"""
    bundle_root = build_dir / "_bundle_runtime"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)

    config_dir = base_dir / "config"
    asset_dir = base_dir / "assets"
    static_dir = base_dir / "display" / "static"

    manifest = build_bundle_manifest(base_dir)
    (bundle_root / "config").mkdir(parents=True, exist_ok=True)
    (bundle_root / "assets").mkdir(parents=True, exist_ok=True)

    if static_dir.exists():
        shutil.copytree(static_dir, bundle_root / "static")

    for filename in manifest["config_files"]:
        shutil.copy2(config_dir / filename, bundle_root / "config" / filename)

    for relative_name in manifest["asset_files"]:
        source = asset_dir / Path(relative_name)
        target = bundle_root / "assets" / Path(relative_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    (bundle_root / BUNDLE_MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return bundle_root
