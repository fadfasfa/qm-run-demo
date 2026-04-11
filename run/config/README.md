# 配置文件说明

这个目录存放运行时会读取的静态数据和缓存文件。这里的文本文件统一使用 UTF-8 无 BOM，避免不同编辑器或运行环境把中文键名写坏。

## 文件用途

- `Augment_Apexlol_Map.json`
  - 海克斯/强化符文名称到 ApexLoL 图标 slug 的映射缓存。
  - 由 `run/web_server.py` 在启动时预热，并在需要时自动刷新。
- `Augment_Full_Map.json`
  - 强化符文名称到品质/阶级的完整映射。
- `Augment_Icon_Map.json`
  - 强化符文名称到本地图标文件名的映射。
- `Augment_Icon_Manifest.json`
  - 海克斯统一目录，包含名称、品质、图标文件名、tooltip、纯文本 tooltip、数值映射等运行时字段。
  - 由海克斯图像下载/目录刷新逻辑自动生成，作为批量预缓存、接口输出和展示层合并的单一数据源。
- `Champion_Core_Data.json`
  - 英雄核心资料，包括名称、别称和英文名。
- `Champion_Synergy.json`
  - 英雄协同数据，供推荐和展示逻辑使用。
- `augment_icon_source.txt`
  - 当前图标来源标记。`apexlol` 表示图标映射来源于 ApexLoL。
- `hero_version.txt`
  - 英雄数据版本标记。
- `scraper_status.json`
  - 抓取器运行状态的轻量缓存。
- `user_settings.json`
  - 用户偏好或本地设置缓存。
- `web_server_port.txt`
  - Web 服务端口记录。

## 约定

- 新增或更新这类文件时，写入请保持 UTF-8 无 BOM。
- 如果文件是运行时缓存，优先由代码写入，不要手工维护业务含义。
- `Augment_Full_Map.json` 与 `Augment_Icon_Map.json` 仍可作为冷启动输入，但运行时主链路应优先读取 `Augment_Icon_Manifest.json`。
- 如果文件内容看起来像乱码，先检查是不是编码问题，再判断是不是数据源本身缺失。
