import pandas as pd
import numpy as np
import logging
import hashlib
import time
from typing import List, Dict, Any, Optional, Tuple

from hero_sync import load_champion_core_data
from icon_resolver import build_local_augment_icon_url

# 全局缓存。
_hextech_cache_pool: Dict[Tuple[str, str], Dict[str, List[Dict[str, Any]]]] = {}
_champion_cache_pool: Dict[str, List[Dict[str, Any]]] = {}
_cache_metadata: Dict[str, Dict[str, Any]] = {}
_champion_core_cache: Optional[Dict[str, Any]] = None

# 缓存配置。
MAX_CACHE_SIZE = 100
CACHE_TTL = 300.0

def _get_champion_maps():
    # 构建英雄名称到 ID/英文名的映射。
    global _champion_core_cache
    if _champion_core_cache is None:
        try:
            _champion_core_cache = load_champion_core_data()
        except Exception as e:
            logging.warning(f"加载英雄核心数据失败：{e}")
            _champion_core_cache = {}

    name_to_id = {}
    name_to_en = {}
    for key, value in _champion_core_cache.items():
        name = value.get('name', '')
        en_name = value.get('en_name', '')
        if name:
            name_to_id[name] = key
            name_to_en[name] = en_name
    return name_to_id, name_to_en


def _compute_df_hash(df: pd.DataFrame) -> str:
    # 基于行数、列名和边界数据计算轻量哈希。
    try:
        row_count = len(df)
        col_hash = hashlib.md5(str(tuple(df.columns)).encode()).hexdigest()[:8]

        sample_data = ""
        if row_count > 0:
            sample_data = str(df.iloc[0].tolist()) + str(df.iloc[-1].tolist())

        hash_input = f"{row_count}|{col_hash}|{sample_data}"
        return hashlib.md5(hash_input.encode()).hexdigest()[:16]
    except Exception as e:
        logging.warning(f"计算 DataFrame 哈希失败：{e}")
        return str(id(df))


def _get_from_cache(cache_pool: dict, key) -> Optional[Any]:
    if key in cache_pool:
        meta = _cache_metadata.get(key, {})
        if CACHE_TTL <= 0 or (time.time() - meta.get('timestamp', 0)) < CACHE_TTL:
            return cache_pool[key]
        cache_pool.pop(key, None)
        _cache_metadata.pop(key, None)
    return None


def _set_to_cache(cache_pool: dict, key, value: Any, df: pd.DataFrame) -> None:
    # 写入缓存并按容量淘汰最早条目。
    if len(cache_pool) >= MAX_CACHE_SIZE:
        oldest_key = next(iter(cache_pool))
        cache_pool.pop(oldest_key, None)
        _cache_metadata.pop(oldest_key, None)

    cache_pool[key] = value
    _cache_metadata[key] = {
        'row_count': len(df),
        'timestamp': time.time()
    }


def _invalidate_stale_caches(df: pd.DataFrame) -> None:
    # 当数据行数变化时，清理相关缓存。
    current_rows = len(df)
    stale_keys = []

    for key, meta in _cache_metadata.items():
        if meta.get('row_count', 0) != current_rows:
            stale_keys.append(key)

    for key in stale_keys:
        _hextech_cache_pool.pop(key, None)
        _champion_cache_pool.pop(key, None)
        _cache_metadata.pop(key, None)


def process_champions_data(df: pd.DataFrame) -> List[Dict[str, Any]]:
    # 计算全英雄榜单，按贝叶斯平滑和标准分综合排序。
    if df.empty:
        return []

    logging.info(f"Processing columns: {df.columns.tolist()}")

    df_hash = _compute_df_hash(df)
    cached_result = _get_from_cache(_champion_cache_pool, df_hash)
    if cached_result is not None:
        logging.debug(f"命中英雄大盘缓存，哈希={df_hash}")
        return cached_result

    # 获取英雄映射数据
    name_to_id, name_to_en = _get_champion_maps()

    try:
        data = df.copy()
        data = data[['英雄名称', '英雄胜率', '英雄出场率']].drop_duplicates(subset=['英雄名称']).copy()
        required_cols = ['英雄名称', '英雄胜率', '英雄出场率']
        if not all(col in data.columns for col in required_cols):
            logging.warning(f"缺少必要列，当前列：{data.columns.tolist()}")
            return []

        min_pick_rate = 0.005
        avg_winrate = data['英雄胜率'].mean()

        data['贝叶斯胜率'] = (
            data['英雄胜率'] * data['英雄出场率'] + avg_winrate * min_pick_rate
        ) / (data['英雄出场率'] + min_pick_rate)

        bayes_mean = data['贝叶斯胜率'].mean()
        bayes_std = data['贝叶斯胜率'].std()
        if bayes_std == 0 or np.isnan(bayes_std):
            bayes_std = 1
        data['Z_贝叶斯胜率'] = (data['贝叶斯胜率'] - bayes_mean) / bayes_std

        pick_mean = data['英雄出场率'].mean()
        pick_std = data['英雄出场率'].std()
        if pick_std == 0 or np.isnan(pick_std):
            pick_std = 1
        data['Z_出场率'] = (data['英雄出场率'] - pick_mean) / pick_std

        data['综合分数'] = data['Z_贝叶斯胜率'] * 0.80 + data['Z_出场率'] * 0.20

        data = data.sort_values(by='综合分数', ascending=False)

        result = []
        for _, row in data.iterrows():
            result.append({
                '英雄名称': str(row['英雄名称']),
                '英雄 ID': name_to_id.get(str(row['英雄名称']), ''),
                '英文名': name_to_en.get(str(row['英雄名称']), ''),
                '英雄胜率': float(row['英雄胜率']) if pd.notna(row['英雄胜率']) else 0.0,
                '英雄出场率': float(row['英雄出场率']) if pd.notna(row['英雄出场率']) else 0.0,
                '贝叶斯胜率': float(row['贝叶斯胜率']) if pd.notna(row['贝叶斯胜率']) else 0.0,
                '综合分数': float(row['综合分数']) if pd.notna(row['综合分数']) else 0.0,
                'Z_贝叶斯胜率': float(row['Z_贝叶斯胜率']) if pd.notna(row['Z_贝叶斯胜率']) else 0.0,
                'Z_出场率': float(row['Z_出场率']) if pd.notna(row['Z_出场率']) else 0.0
            })

        return result

    except Exception as e:
        logging.error(f"处理英雄大盘数据异常：{e}")
        return []


def _clear_champion_cache():
    # 手动清空英雄大盘缓存。
    global _champion_cache_pool, _cache_metadata
    _champion_cache_pool.clear()
    _cache_metadata.clear()


def _has_column_variant(df: pd.DataFrame, variants: List[str]) -> bool:
    # 检查 DataFrame 中是否存在给定的列名变体
    return any(var in df.columns for var in variants)


def process_hextechs_data(df: pd.DataFrame, name: str) -> Dict[str, List[Dict[str, Any]]]:
    # 计算单英雄海克斯结果，返回总榜、综合榜、纯胜率榜和分阶级榜单
    # 计算时会引入置信度衰减，避免低样本数据干扰排序
    logging.info(f"Processing columns: {df.columns.tolist()}")

    if df.empty:
        return {
            'top_10_overall': [],
            'comprehensive': [],
            'winrate_only': [],
            'Prismatic': [],
            'Gold': [],
            'Silver': []
        }

    # ========== 缓存检查 ==========
    df_hash = _compute_df_hash(df)
    cache_key = (name, df_hash)
    cached_result = _get_from_cache(_hextech_cache_pool, cache_key)
    if cached_result is not None:
        logging.debug(f"命中海克斯缓存，英雄={name}, 哈希={df_hash}")
        return cached_result

    try:
        # 创建副本避免修改原始数据
        data = df.copy()

        # 确保必要列存在
        required_cols = ['英雄名称', '海克斯名称', '海克斯胜率', '海克斯出场率', '胜率差']
        missing_cols = [col for col in required_cols if col not in data.columns]

        # 特殊兼容性处理：对不同编号字段变体进行不敏感匹配
        # 这些列不是必填项，但如果存在任何变体就可用
        id_variants = ['英雄ID', '英雄 ID', '英雄 id']
        has_id_column = _has_column_variant(data, id_variants)

        if missing_cols:
            logging.warning(f"缺少必要列 {missing_cols}，当前列：{data.columns.tolist()}")
            return {
                'top_10_overall': [],
                'comprehensive': [],
                'winrate_only': [],
                'Prismatic': [],
                'Gold': [],
                'Silver': []
            }

        # ========== 过滤指定英雄 ==========
        hero_data = data[data['英雄名称'] == name].copy()
        if hero_data.empty:
            logging.warning(f"英雄 '{name}' 无数据")
            return {
                'top_10_overall': [],
                'comprehensive': [],
                'winrate_only': [],
                'Prismatic': [],
                'Gold': [],
                'Silver': []
            }

        # ========== 动态置信度衰减惩罚（替代硬编码过滤） ==========
        # 采用平滑衰减因子，让低样本高胜率数据自然降权
        confidence_threshold = 0.001  # 置信度阈值（0.1%）

        def apply_confidence_penalty(row):
            # 计算动态置信度惩罚因子
            pick_rate = row['海克斯出场率']
            if pick_rate <= 0:
                return 0.0
            # 使用平滑衰减曲线
            penalty = 1.0 / (1.0 + (confidence_threshold / pick_rate) ** 2)
            return penalty

        hero_data['置信度因子'] = hero_data.apply(apply_confidence_penalty, axis=1)

        # ========== 标准分计算 ==========
        # 胜率差标准分
        wr_diff_mean = hero_data['胜率差'].mean()
        wr_diff_std = hero_data['胜率差'].std()
        if wr_diff_std == 0 or np.isnan(wr_diff_std):
            wr_diff_std = 1
        hero_data['Z_胜率差'] = (hero_data['胜率差'] - wr_diff_mean) / wr_diff_std

        # 出场率标准分
        pick_mean = hero_data['海克斯出场率'].mean()
        pick_std = hero_data['海克斯出场率'].std()
        if pick_std == 0 or np.isnan(pick_std):
            pick_std = 1
        hero_data['Z_出场率'] = (hero_data['海克斯出场率'] - pick_mean) / pick_std

        # ========== 综合得分计算（带置信度衰减） ==========
        # 基于胜率差和出场率计算综合得分，再乘以置信度因子
        def calc_comprehensive_score(row):
            z_wr = row['Z_胜率差']
            z_pr = row['Z_出场率']
            wr_diff = row['胜率差']
            confidence = row['置信度因子']

            if wr_diff >= 0:
                base_score = z_wr * 0.85 + z_pr * 0.15
            else:
                base_score = z_wr * 0.85 - z_pr * 0.15

            # 应用置信度衰减
            return base_score * confidence

        hero_data['综合得分'] = hero_data.apply(calc_comprehensive_score, axis=1)

        # ========== 综合得分容错处理（空值填充） ==========
        hero_data['综合得分'] = hero_data['综合得分'].fillna(0.0)

        # ========== 辅助函数：生成海克斯卡片 ==========
        def build_hextech_card(row, include_score=True):
            card = {
                '海克斯名称': str(row['海克斯名称']),
                '海克斯阶级': str(row.get('海克斯阶级', '棱彩')),
                '海克斯胜率': float(row['海克斯胜率']) if pd.notna(row['海克斯胜率']) else 0.0,
                '海克斯出场率': float(row['海克斯出场率']) if pd.notna(row['海克斯出场率']) else 0.0,
                '胜率差': float(row['胜率差']) if pd.notna(row['胜率差']) else 0.0,
                'icon': build_local_augment_icon_url(row['海克斯名称'])
            }
            if include_score:
                card['综合得分'] = float(row['综合得分']) if pd.notna(row['综合得分']) else 0.0
            return card

        # ========== 前十总榜：不计阶级，按综合得分取前 10 ==========
        top_10_data = hero_data.sort_values(by='综合得分', ascending=False).head(10)
        top_10_overall = []
        for _, row in top_10_data.iterrows():
            top_10_overall.append(build_hextech_card(row, include_score=True))

        # ========== 综合榜单（向后兼容） ==========
        comp_data = hero_data.sort_values(by='综合得分', ascending=False)
        comprehensive_list = []
        for _, row in comp_data.iterrows():
            comprehensive_list.append(build_hextech_card(row, include_score=True))

        # ========== 纯胜率榜单（向后兼容） ==========
        winrate_data = hero_data.sort_values(by='海克斯胜率', ascending=False)
        winrate_list = []
        for _, row in winrate_data.iterrows():
            winrate_list.append(build_hextech_card(row, include_score=False))

        # ========== 分阶级数组 ==========
        def build_tier_array(tier_name, limit=None):
            # 为指定阶级生成数组
            tier_variants = {
                'Prismatic': ['棱彩', '彩色'],
                'Gold': ['金色', '黄金'],
                'Silver': ['银色', '白银']
            }

            variants = tier_variants.get(tier_name, [])
            tier_data = hero_data[hero_data['海克斯阶级'].isin(variants)].copy()

            # 按综合得分排序并截取
            tier_data_by_score = tier_data.sort_values(by='综合得分', ascending=False)
            if limit is not None:
                tier_data_by_score = tier_data_by_score.head(limit)
            result = []
            for _, row in tier_data_by_score.iterrows():
                result.append(build_hextech_card(row, include_score=True))

            return result

        prismatic_list = build_tier_array('Prismatic')
        gold_list = build_tier_array('Gold')
        silver_list = build_tier_array('Silver')

        result = {
            'top_10_overall': top_10_overall,
            'comprehensive': comprehensive_list,
            'winrate_only': winrate_list,
            'Prismatic': prismatic_list,
            'Gold': gold_list,
            'Silver': silver_list
        }

        # ========== 缓存结果 ==========
        _set_to_cache(_hextech_cache_pool, cache_key, result, df)
        return result

    except Exception as e:
        logging.error(f"处理海克斯数据异常: {e}")
        # 安全降级，返回空列表
        return {
            'top_10_overall': [],
            'comprehensive': [],
            'winrate_only': [],
            'Prismatic': [],
            'Gold': [],
            'Silver': []
        }

def clear_hextech_cache():
    # 手动清空海克斯缓存（用于强制刷新）
    global _hextech_cache_pool, _cache_metadata
    _hextech_cache_pool.clear()
    _cache_metadata.clear()
