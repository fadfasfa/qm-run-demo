import os
import glob
import json
import sys
import pandas as pd
from hero_sync import BASE_DIR, CONFIG_DIR, CORE_DATA_FILE
from alias_utils import normalize_alias_token, unique_alias_tokens

if os.name == 'nt': os.system('')  # 启用 Windows 终端颜色输出。
RESET = "\033[0m"

# 延迟加载基础数据，降低启动耗时。
CORE_DATA = None
CHAMP_NAME_MAP = {}

def init_core_data():
    global CORE_DATA, CHAMP_NAME_MAP
    if CORE_DATA is None:
        from hero_sync import load_champion_core_data
        try:
            CORE_DATA = load_champion_core_data()
            CHAMP_NAME_MAP = {v["name"]: v["title"] for k, v in CORE_DATA.items()}
        except (json.JSONDecodeError, KeyError, ValueError):
            CORE_DATA = {}
            CHAMP_NAME_MAP = {}
        except Exception:
            CORE_DATA = {}
            CHAMP_NAME_MAP = {}

GLOBAL_LAST_HERO = None
_alias_cache = None

def set_last_hero(name):
    global GLOBAL_LAST_HERO
    GLOBAL_LAST_HERO = name

def _normalize_query_df(shared_df=None):
    if shared_df is None:
        latest_csv = get_latest_csv()
        if not latest_csv:
            return pd.DataFrame(), None
        df = pd.read_csv(latest_csv)
        source = latest_csv
    elif isinstance(shared_df, pd.DataFrame):
        df = shared_df.copy()
        source = "shared_df"
    else:
        df = pd.DataFrame(shared_df).copy()
        source = "shared_df"

    if not df.empty:
        df.columns = df.columns.str.replace(' ', '')
        id_col = None
        for col in df.columns:
            if '英雄ID' in col or 'ID' in col:
                id_col = col
                break
        if id_col:
            df[id_col] = df[id_col].astype(str).str.strip().str.replace('.0', '', regex=False)
    return df, source

def get_highlight_color(row):
    wr_diff = row['胜率差']
    if wr_diff < 0:
        diff_val = abs(wr_diff)
        if diff_val <= 0.03: return "\033[38;5;214m" 
        if diff_val <= 0.07: return "\033[38;5;196m" 
        if diff_val <= 0.12: return "\033[38;5;160m" 
        return "\033[38;5;129m"                      
    else:
        score = row['海克斯胜率'] + (row['海克斯出场率'] * 0.3)
        if score >= 0.56: return "\033[38;5;51m"   
        if score >= 0.53: return "\033[38;5;46m"   
        if score >= 0.505: return "\033[38;5;118m" 
        return ""

def get_latest_csv():
    files = glob.glob(os.path.join(CONFIG_DIR, "Hextech_Data_*.csv"))
    if not files: return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]

def get_char_width(char):
    # 全角和宽字符按 2 计算，其余按 1 计算。
    return 2 if unicodedata.east_asian_width(char) in ('F', 'W') else 1

def align_text(text, width):
    text = str(text)
    cur_len = 0
    res = ""
    for char in text:
        char_w = get_char_width(char)
        if cur_len + char_w > width: break
        res += char
        cur_len += char_w
    return res + ' ' * (width - cur_len)

def print_side_by_side_table(df_source, title, limit=None):
    df_all = df_source.copy()
    if limit: df_all = df_all[df_all['胜率差'] >= 0] 
    
    df_comp = df_all.sort_values(by='综合得分', ascending=False).reset_index(drop=True)
    df_win = df_all.sort_values(by=['海克斯胜率', '海克斯出场率'], ascending=[False, False]).reset_index(drop=True)
    if limit: df_comp, df_win = df_comp.head(limit), df_win.head(limit)
    
    NAME_W, VAL_W = 24, 8
    print("\n" + "="*110 + f"\n {title}\n" + "="*110)
    print(align_text("海克斯(综合推荐)", NAME_W) + align_text("胜率", VAL_W) + align_text("出场", VAL_W) + "  ||  " + 
          align_text("海克斯(纯胜率)", NAME_W) + align_text("胜率", VAL_W) + align_text("出场", VAL_W))
    print("-" * 110)
    
    for i in range(len(df_comp)):
        rc = df_comp.iloc[i] if i < len(df_comp) else None
        rw = df_win.iloc[i] if i < len(df_win) else None
        l_content, r_content = " "*NAME_W + " "*VAL_W*2, " "*NAME_W + " "*VAL_W*2
        l_color, r_color = "", ""
        
        if rc is not None:
            l_color = get_highlight_color(rc)
            tier_prefix = rc['海克斯阶级'][0] if isinstance(rc['海克斯阶级'], str) and rc['海克斯阶级'] else "?"
            l_content = align_text(f"{i+1}.[{tier_prefix}]{rc['海克斯名称']}", NAME_W) + align_text(f"{rc['海克斯胜率']:.1%}", VAL_W) + align_text(f"{rc['海克斯出场率']:.1%}", VAL_W)
        if rw is not None:
            r_color = get_highlight_color(rw)
            tier_prefix = rw['海克斯阶级'][0] if isinstance(rw['海克斯阶级'], str) and rw['海克斯阶级'] else "?"
            r_content = align_text(f"{i+1}.[{tier_prefix}]{rw['海克斯名称']}", NAME_W) + align_text(f"{rw['海克斯胜率']:.1%}", VAL_W) + align_text(f"{rw['海克斯出场率']:.1%}", VAL_W)
            
        print(f"{l_color}{l_content}{RESET if l_color else ''}  ||  {r_color}{r_content}{RESET if r_color else ''}")

def add_new_alias(new_alias, official_names):
    print(f"\n错误 未匹配到对应英雄: \"{new_alias}\"")
    print("请选择您的操作：\n [任意键] 只是打错了，重新输入\n [2] 我要将该词添加为某个英雄的新外号")
    try:
        choice = input("请 请选择 (2/任意键): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if choice != '2':
        return None

    target_input = input("请 请输入该英雄的官方名称或系统中已有的外号 (例如: 皇子): ").strip()
    target_hero = get_official_hero_name(target_input, official_names)

    if not target_hero:
        return None

    confirm = input(f"请 确认要将 \"{new_alias}\" 永久添加为（{target_hero}）的外号吗？(y/n): ").strip().lower()
    if confirm == 'y':
        global CORE_DATA, CHAMP_NAME_MAP, _alias_cache
        from hero_sync import load_champion_core_data

        try:
            core_data = load_champion_core_data()
        except Exception:
            return None

        target_key = None
        for champ_id, champ_info in core_data.items():
            if str(champ_info.get("name", "")).strip() == target_hero:
                target_key = champ_id
                break

        if not target_key:
            return None

        target_entry = dict(core_data.get(target_key, {}))
        aliases = target_entry.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = []
        if new_alias not in aliases:
            aliases.append(new_alias)
        target_entry["aliases"] = aliases
        core_data[target_key] = target_entry

        tmp_path = CORE_DATA_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(core_data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, CORE_DATA_FILE)

        CORE_DATA = None
        CHAMP_NAME_MAP = {}
        _alias_cache = None
        print("成功 添加成功！")
        return target_hero
    return None


def build_default_aliases():
    print("\n警告 正在重建英雄别名索引...")
    aliases = {}
    try:
        from hero_sync import load_champion_core_data
        core_data = load_champion_core_data()
        for _, v in core_data.items():
            name = v.get("name")
            if not name:
                continue
            title = v.get("title")
            en = v.get("en_name", "")
            aliases[name] = unique_alias_tokens(
                [name, title, en],
                v.get("aliases", []),
            )
    except Exception as e:
        print(f"警告 核心数据提取失败: {e}")

    hardcoded = {
        "诺克萨斯之手": ["ns", "nuoshou", "诺手", "大白腿"],
        "疾风剑豪": ["ys", "yasuo", "亚索", "快乐风男", "孤儿"],
        "德玛西亚皇子": ["hz", "huangzi", "皇子", "周杰伦"],
        "九尾妖狐": ["hl", "huli", "狐狸", "刮痧师傅"],
        "盲僧": ["ms", "mangseng", "瞎子", "xiazi"],
        "无极剑圣": ["js", "jiansheng", "剑圣", "易大师", "疯狗"],
        "蛮族之王": ["mw", "manwang", "蛮王", "蛮三刀"],
        "英勇投弹手": ["fj", "feiji", "飞机"],
        "瘟疫之源": ["ls", "laoshu", "老鼠", "图奇"],
        "迅捷斥候": ["tm", "timo", "提莫", "种蘑菇的"],
        "卡牌大师": ["kp", "kapai", "卡牌"],
        "探险家": ["ez", "txj", "小黄毛"],
        "暗夜猎手": ["vn", "vayne", "薇恩", "洗澡狗", "乌兹"],
        "诡术妖姬": ["yj", "yaoji", "妖姬", "一条链子"],
        "虚空恐惧": ["cg", "chogath", "大虫子"],
        "虚空掠夺者": ["kzx", "khazix", "螳螂"],
        "正义巨像": ["jl", "galio", "加里奥"],
        "狂野女猎手": ["bo", "nidalee", "豹女", "奶大力"],
        "牛头酋长": ["nt", "alistar", "牛头"],
        "邪恶小法师": ["xf", "veigar", "小法"],
        "雪原双子": ["nr", "nunu", "努努", "雪人"],
        "赏金猎人": ["hh", "mf", "好运姐", "女枪"],
        "寒冰射手": ["hb", "ashe", "寒冰", "刮痧女王"],
        "武器大师": ["wq", "jax", "武器"],
        "时光守护者": ["zl", "zilean", "时光老头"],
        "炼金术士": ["lj", "singed", "炼金", "搅屎棍"],
        "痛苦之拥": ["evelynn", "寡妇"],
        "死亡颂唱者": ["ks", "karthus", "死歌"],
        "披甲龙龟": ["lg", "rammus", "龙龟"],
        "冰晶凤凰": ["fh", "anivia", "冰鸟"],
        "恶魔小丑": ["xc", "shaco", "小丑"],
        "琴瑟仙女": ["qn", "sona", "琴女", "36d"],
        "刀锋舞者": ["dm", "irelia", "刀妹"],
        "风暴之怒": ["fn", "janna", "风女"],
        "海洋之灾": ["cp", "gangplank", "船长"],
        "沙漠死神": ["gt", "nasus", "狗头"],
        "大发明家": ["ht", "heimerdinger", "大头"],
        "傲之追猎者": ["sg", "rengar", "狮子狗"],
        "皮城女警": ["nj", "caitlyn", "女警"],
        "蒸汽机器人": ["jqr", "blitzcrank", "机器人"],
        "熔岩巨兽": ["str", "malphite", "石头人", "混子"],
        "不祥之刃": ["kt", "katarina", "卡特"],
        "狂暴之心": ["kn", "kennen", "电耗子"],
        "德玛西亚之力": ["gl", "garen", "盖伦", "大宝剑"],
        "曙光女神": ["rn", "leona", "日女"],
        "首领之傲": ["wj", "urgot", "螃蟹"],
        "放逐之刃": ["rw", "riven", "锐雯", "瑞文"],
        "深渊巨口": ["dm", "kogmaw", "大嘴"],
        "雷霆咆哮": ["gb", "volibear", "狗熊"],
        "潮汐海灵": ["xz", "fizz", "小鱼人"],
        "凛冬之怒": ["zj", "sejuani", "猪妹"],
        "爆破鬼才": ["zj", "ziggs", "炸弹人"],
        "仙灵女巫": ["ll", "lulu", "紫皮大蒜"],
        "荣耀行刑官": ["dw", "draven", "德莱文"],
        "皎月女神": ["jy", "diana", "皎月"],
        "无双剑姬": ["jj", "fiora", "剑姬"],
        "皮城执法官": ["w", "vi", "蔚"],
        "沙漠皇帝": ["sh", "azir", "沙皇", "黄鸡"],
        "海兽祭司": ["cm", "illaoi", "触手妈"],
        "戏命师": ["jh", "jhin", "瘸子"],
        "暴走萝莉": ["jks", "jinx", "金克丝"],
        "河流之王": ["hm", "tahmkench", "蛤蟆"],
        "复仇之矛": ["hlst", "kalista", "滑板鞋"],
        "虚空遁地兽": ["ks", "reksai", "挖掘机"],
        "虚空之眼": ["dk", "velkoz", "大眼"],
        "圣枪游侠": ["xao", "lucian", "奥巴马"],
        "冰霜女巫": ["ls", "lissandra", "冰女"],
        "暗黑元首": ["xdr", "syndra", "球女"],
        "龙血武姬": ["lvn", "shyvana", "龙女"],
        "青钢影": ["kmr", "camille", "剪刀腿"],
        "星籁歌姬": ["sfl", "seraphine", "轮椅女"],
        "破败之王": ["fyg", "viego", "王大爷"],
        "愁云使者": ["gx", "vex", "熬夜波比"],
        "百裂冥犬": ["nfl", "nafiri", "狗"],
        "炽炎雏龙": ["smd", "smolder", "小火龙"]
    }

    supplemental = {
        "远古恐惧": ["稻草人", "草人", "fiddlesticks"],
        "蒸汽机器人": ["机器人", "布里茨", "blitzcrank"],
        "弗雷尔卓德之心": ["布隆", "braum"],
        "蜘蛛女皇": ["蜘蛛", "elise"],
        "无双剑姬": ["剑姬", "fiora"],
        "潮汐海灵": ["小鱼人", "鱼人", "fizz"],
        "正义巨像": ["加里奥", "galio"],
        "海洋之灾": ["船长", "gp", "gangplank"],
        "灵罗娃娃": ["格温", "剪刀妹", "gwen"],
        "大发明家": ["大头", "黑默丁格", "heimerdinger"],
        "海兽祭司": ["触手妈", "俄洛伊", "illaoi"],
        "戏命师": ["烬", "四哥", "jhin"],
        "暴走萝莉": ["金克丝", "jinx"],
        "死亡颂唱者": ["死歌", "karthus"],
        "虚空行者": ["卡萨丁", "kassadin"],
        "不祥之刃": ["卡特", "katarina"],
        "审判天使": ["天使", "kayle"],
        "狂暴之心": ["凯南", "kennen"],
        "永猎双子": ["千珏", "kindred"],
        "暴怒骑士": ["克烈", "kled"],
        "诡术妖姬": ["妖姬", "leblanc"],
        "含羞蓓蕾": ["莉莉娅", "lillia"],
        "冰霜女巫": ["冰女", "lissandra"],
        "仙灵女巫": ["露露", "lulu"],
        "米利欧": ["米利欧", "milio"],
        "铁铠冥魂": ["铁男", "mordekaiser"],
        "万花通灵": ["妮蔻", "neeko"],
        "永恒梦魇": ["梦魇", "nocturne"],
        "不羁之悦": ["尼菈", "nilah"],
        "圣锤之毅": ["波比", "poppy"],
        "元素女皇": ["奇亚娜", "qiyana"],
        "德玛西亚之翼": ["奎因", "quinn"],
        "炼金男爵": ["烈娜塔", "renata", "renataglasc"],
        "镕铁少女": ["芮尔", "rell"],
        "机械公敌": ["兰博", "rumble"],
        "荒漠皇帝": ["沙皇", "azir", "阿兹尔"],
        "虚空女皇": ["卑尔维斯", "女皇", "belveth"],
        "生化魔人": ["扎克", "果冻", "zac"],
        "影流之主": ["劫", "zed"],
        "暮光星灵": ["佐伊", "zoe"],
        "青钢影": ["卡蜜尔", "camille"],
        "魔蛇之拥": ["蛇女", "cassiopeia"],
        "皎月女神": ["皎月", "diana"],
        "双界灵兔": ["阿萝拉", "aurora"],
        "安蓓萨": ["安蓓萨", "ambessa"],
        "梅尔": ["梅尔", "mel"],
        "贝蕾亚": ["贝蕾亚", "briar"],
        "纳祖芒荣耀": ["奎桑提", "ksante", "k'sante"],
        "疾风剑豪": ["风男"],
        "解脱者": ["蒜男"],
        "腕豪": ["劲夫"],
        "雪原双子": ["雪人"],
        "河流之王": ["塔姆"],
        "盲僧": ["李青"],
        "皮城女警": ["凯特琳"],
        "虚空之女": ["Kaisa", "Kai'Sa"],
        "复仇之矛": ["卡莉丝塔"],
        "德玛西亚皇子": ["嘉文"],
        "酒桶": ["古拉加斯"]
    }

    for official_title, nicks in supplemental.items():
        aliases.setdefault(official_title, [])
        aliases[official_title] = unique_alias_tokens(aliases[official_title], nicks)

    for official_title, nicks in hardcoded.items():
        aliases.setdefault(official_title, [])
        aliases[official_title] = unique_alias_tokens(aliases[official_title], nicks)
    return aliases


def load_hero_aliases():
    global _alias_cache
    if _alias_cache is not None:
        return _alias_cache
    _alias_cache = build_default_aliases()
    return _alias_cache


def get_official_hero_name(user_input, official_names):
    init_core_data()
    u_in = normalize_alias_token(user_input)
    hero_aliases = load_hero_aliases()
    potential = set()
    for title, aliases in hero_aliases.items():
        normalized_aliases = [normalize_alias_token(alias) for alias in aliases]
        if any(u_in == alias or u_in in alias or alias in u_in for alias in normalized_aliases if alias):
            for official_name in official_names:
                if title == official_name:
                    potential.add(official_name)
    for name in official_names:
        title = CHAMP_NAME_MAP.get(name, "")
        normalized_name = normalize_alias_token(name)
        normalized_title = normalize_alias_token(title)
        if (
            u_in in normalized_name
            or u_in in normalized_title
            or normalized_name in u_in
            or normalized_title in u_in
        ):
            potential.add(name)
    results = sorted(list(potential))
    if not results:
        return None
    if len(results) == 1:
        return results[0]
    print(f"\n[?] 匹配到多个英雄:")
    for i, res in enumerate(results, 1):
        print(f" [{i}] {res}")
    try:
        idx = int(input(f"请 请输入序号选择: ")) - 1
        return results[idx]
    except (ValueError, IndexError):
        return None

def display_hero_hextech(df, hero_name, target_tier=None, is_from_ui=False):
    global GLOBAL_LAST_HERO
    GLOBAL_LAST_HERO = hero_name
    
    hero_data = df[df['英雄名称'] == hero_name].copy()
    if hero_data.empty: 
        print(f"错误 未在最新战报中找到 {hero_name} 的数据。")
        return
        
    h_win = hero_data.iloc[0]['英雄胜率']
    h_tier = hero_data.iloc[0]['英雄评级']
    stats_str = f"[评级:{h_tier} | 胜率:{h_win:.1%}]"

    if target_tier:
        tier_map = {"1":"白银", "2":"黄金", "3":"棱彩", "白银":"白银", "黄金":"黄金", "棱彩":"棱彩"}
        t_name = tier_map.get(str(target_tier))
        if t_name:
            tier_data = hero_data[hero_data['海克斯阶级'] == t_name]
            if not tier_data.empty: print_side_by_side_table(tier_data, f"综合推荐 （{hero_name}）- {t_name}阶级战报")
    else:
        print_side_by_side_table(hero_data, f"尊享 （{hero_name}）{stats_str} 全阶级 Top 25", limit=25)

    if is_from_ui:
        prompt = "\n请 （输入）称号/别名"
        if GLOBAL_LAST_HERO: prompt += f" | 快捷: 1/2/3查（{GLOBAL_LAST_HERO}）"
        prompt += " (q退出, u悬浮窗): "
        print(prompt, end="", flush=True)

def main_query(shared_df=None, ui_instance=None):
    global GLOBAL_LAST_HERO
    df, source = _normalize_query_df(shared_df)
    payload = {
        "source": source,
        "row_count": int(len(df)),
        "column_names": list(df.columns),
        "last_hero": GLOBAL_LAST_HERO,
        "has_data": not df.empty,
    }

    if ui_instance is not None:
        try:
            setattr(ui_instance, "backend_query_snapshot", payload)
        except Exception:
            pass

    return payload

if __name__ == "__main__":
    sys.exit(0)
