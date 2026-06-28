"""Traditional Chinese display names for MLB team and player fields."""

from __future__ import annotations

import re
import unicodedata


TEAM_ZH = {
    "Arizona Diamondbacks": "亞利桑那響尾蛇",
    "Athletics": "運動家",
    "Atlanta Braves": "亞特蘭大勇士",
    "Baltimore Orioles": "巴爾的摩金鶯",
    "Boston Red Sox": "波士頓紅襪",
    "Chicago Cubs": "芝加哥小熊",
    "Chicago White Sox": "芝加哥白襪",
    "Cincinnati Reds": "辛辛那提紅人",
    "Cleveland Guardians": "克里夫蘭守護者",
    "Colorado Rockies": "科羅拉多洛磯",
    "Detroit Tigers": "底特律老虎",
    "Houston Astros": "休士頓太空人",
    "Kansas City Royals": "堪薩斯市皇家",
    "Los Angeles Angels": "洛杉磯天使",
    "Los Angeles Dodgers": "洛杉磯道奇",
    "Miami Marlins": "邁阿密馬林魚",
    "Milwaukee Brewers": "密爾瓦基釀酒人",
    "Minnesota Twins": "明尼蘇達雙城",
    "New York Mets": "紐約大都會",
    "New York Yankees": "紐約洋基",
    "Philadelphia Phillies": "費城費城人",
    "Pittsburgh Pirates": "匹茲堡海盜",
    "San Diego Padres": "聖地牙哥教士",
    "San Francisco Giants": "舊金山巨人",
    "Seattle Mariners": "西雅圖水手",
    "St. Louis Cardinals": "聖路易紅雀",
    "Tampa Bay Rays": "坦帕灣光芒",
    "Texas Rangers": "德州遊騎兵",
    "Toronto Blue Jays": "多倫多藍鳥",
    "Washington Nationals": "華盛頓國民",
}


GIVEN_NAME_ZH = {
    "aaron": "亞倫",
    "adrian": "艾德里安",
    "alec": "亞歷克",
    "alex": "亞歷克斯",
    "andrew": "安德魯",
    "anthony": "安東尼",
    "bailey": "貝利",
    "blake": "布雷克",
    "brady": "布雷迪",
    "brandon": "布蘭登",
    "bryan": "布萊恩",
    "bryce": "布萊斯",
    "carlos": "卡洛斯",
    "chris": "克里斯",
    "christopher": "克里斯多福",
    "clarke": "克拉克",
    "clayton": "克萊頓",
    "cole": "柯爾",
    "corbin": "柯賓",
    "dylan": "迪倫",
    "eduardo": "愛德華多",
    "framber": "弗蘭伯",
    "freddy": "佛萊迪",
    "gerrit": "格里特",
    "griffin": "葛里芬",
    "hunter": "杭特",
    "jack": "傑克",
    "jacob": "雅各布",
    "jake": "傑克",
    "james": "詹姆斯",
    "jesus": "赫蘇斯",
    "joe": "喬",
    "jose": "荷西",
    "juan": "胡安",
    "justin": "賈斯汀",
    "kevin": "凱文",
    "kyle": "凱爾",
    "lance": "蘭斯",
    "logan": "羅根",
    "luis": "路易斯",
    "marcus": "馬庫斯",
    "max": "麥斯",
    "michael": "麥可",
    "mike": "麥克",
    "miles": "麥爾斯",
    "nathan": "奈森",
    "nick": "尼克",
    "paul": "保羅",
    "pablo": "帕布羅",
    "reid": "里德",
    "ryan": "萊恩",
    "ryne": "萊恩",
    "sandy": "桑迪",
    "sean": "西恩",
    "seth": "賽斯",
    "shane": "謝恩",
    "sonny": "桑尼",
    "spencer": "史賓塞",
    "tarik": "塔里克",
    "tanner": "坦納",
    "tyler": "泰勒",
    "walker": "沃克",
    "will": "威爾",
    "yusei": "菊池雄星",
    "yoshinobu": "山本由伸",
    "shohei": "大谷翔平",
}


SURNAME_ZH = {
    "alcantara": "阿爾坎塔拉",
    "bassitt": "巴希特",
    "berrios": "貝里歐斯",
    "brown": "布朗",
    "burnes": "伯恩斯",
    "cease": "希斯",
    "cole": "柯爾",
    "darvish": "達比修",
    "degrom": "迪葛隆",
    "eovaldi": "伊瓦迪",
    "fried": "弗里德",
    "gilbert": "吉爾伯特",
    "glasnow": "葛拉斯諾",
    "gausman": "高斯曼",
    "gore": "戈爾",
    "gray": "葛雷",
    "greene": "葛林",
    "imanaga": "今永昇太",
    "irvin": "厄文",
    "keller": "凱勒",
    "king": "金恩",
    "kirby": "柯比",
    "kikuchi": "菊池雄星",
    "lodolo": "羅多羅",
    "lopez": "羅培茲",
    "lugo": "盧戈",
    "manaea": "曼奈亞",
    "mcgreevy": "麥格里維",
    "miller": "米勒",
    "nelson": "尼爾森",
    "nola": "諾拉",
    "ober": "歐伯",
    "ragans": "雷根斯",
    "sale": "塞爾",
    "scherzer": "薛澤",
    "severino": "塞維里諾",
    "schlittler": "史利特勒",
    "skubal": "史庫柏",
    "skenes": "史基恩斯",
    "snell": "史奈爾",
    "steele": "史提爾",
    "strider": "史崔德",
    "valdez": "瓦德茲",
    "webb": "韋伯",
    "woodruff": "伍德拉夫",
    "wheeler": "惠勒",
    "yamamoto": "山本由伸",
}

PLAYER_ZH = {
    "shohei ohtani": "大谷翔平",
    "yoshinobu yamamoto": "山本由伸",
    "yusei kikuchi": "菊池雄星",
    "shota imanaga": "今永昇太",
    "yu darvish": "達比修有",
    "kodai senga": "千賀滉大",
    "kenta maeda": "前田健太",
}

LETTER_ZH = {
    "a": "阿",
    "b": "布",
    "c": "克",
    "d": "德",
    "e": "伊",
    "f": "夫",
    "g": "格",
    "h": "赫",
    "i": "伊",
    "j": "傑",
    "k": "克",
    "l": "爾",
    "m": "姆",
    "n": "恩",
    "o": "歐",
    "p": "普",
    "q": "丘",
    "r": "爾",
    "s": "斯",
    "t": "特",
    "u": "優",
    "v": "維",
    "w": "沃",
    "x": "克斯",
    "y": "伊",
    "z": "茲",
}


SYLLABLE_ZH = {
    "a": "阿",
    "ba": "巴",
    "be": "貝",
    "bi": "比",
    "bo": "波",
    "br": "布",
    "ca": "卡",
    "ce": "塞",
    "ch": "查",
    "ci": "西",
    "co": "柯",
    "cu": "庫",
    "da": "達",
    "de": "德",
    "di": "迪",
    "do": "多",
    "du": "杜",
    "e": "艾",
    "fa": "法",
    "fe": "菲",
    "fi": "費",
    "fo": "佛",
    "ga": "加",
    "ge": "傑",
    "gi": "吉",
    "go": "戈",
    "gr": "葛",
    "ha": "哈",
    "he": "赫",
    "hi": "希",
    "ho": "霍",
    "ja": "賈",
    "je": "傑",
    "ji": "吉",
    "jo": "喬",
    "ka": "卡",
    "ke": "凱",
    "ki": "基",
    "ko": "柯",
    "la": "拉",
    "le": "雷",
    "li": "里",
    "lo": "羅",
    "lu": "路",
    "ma": "馬",
    "me": "梅",
    "mi": "米",
    "mo": "莫",
    "na": "納",
    "ne": "內",
    "ni": "尼",
    "no": "諾",
    "o": "歐",
    "pa": "帕",
    "pe": "佩",
    "pi": "皮",
    "po": "波",
    "ra": "拉",
    "re": "雷",
    "ri": "里",
    "ro": "羅",
    "ru": "魯",
    "sa": "薩",
    "se": "塞",
    "si": "西",
    "so": "索",
    "st": "史",
    "ta": "塔",
    "te": "特",
    "ti": "提",
    "to": "托",
    "tr": "崔",
    "va": "瓦",
    "ve": "維",
    "vi": "維",
    "wa": "瓦",
    "we": "韋",
    "wi": "威",
    "ya": "亞",
    "yo": "尤",
    "za": "札",
}


def team_zh(name: str) -> str:
    return TEAM_ZH.get(name, name)


def normalize_ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def token_to_zh(token: str) -> str:
    key = normalize_ascii(token).lower().strip(".' -")
    if not key:
        return ""
    if key in GIVEN_NAME_ZH:
        return GIVEN_NAME_ZH[key]
    if key in SURNAME_ZH:
        return SURNAME_ZH[key]
    parts = re.findall(r"[bcdfghjklmnpqrstvwxyz]*[aeiouy]+|[bcdfghjklmnpqrstvwxyz]+$", key)
    rendered = []
    for part in parts[:4]:
        if part in SYLLABLE_ZH:
            rendered.append(SYLLABLE_ZH[part])
        elif part[:2] in SYLLABLE_ZH:
            rendered.append(SYLLABLE_ZH[part[:2]])
        elif part[:1] in SYLLABLE_ZH:
            rendered.append(SYLLABLE_ZH[part[:1]])
    if rendered:
        return "".join(rendered)
    return "".join(LETTER_ZH.get(ch, "") for ch in key) or "未譯名"


def player_zh(name: str) -> str:
    if not name:
        return ""
    cleaned = normalize_ascii(name).replace(".", " ")
    full_key = " ".join(cleaned.lower().split())
    if full_key in PLAYER_ZH:
        return PLAYER_ZH[full_key]
    tokens = [t for t in re.split(r"[\s-]+", cleaned) if t]
    if not tokens:
        return ""
    if len(tokens) == 1:
        return token_to_zh(tokens[0])
    given = token_to_zh(tokens[0])
    surname = token_to_zh(tokens[-1])
    middle = [token_to_zh(t) for t in tokens[1:-1]]
    rendered = [given, *middle, surname]
    return " ".join(part for part in rendered if part)
