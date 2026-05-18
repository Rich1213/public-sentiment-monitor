"""
brands.py — 品牌搜尋設定中心

定義各便利超商品牌的：
  - 各渠道精確搜尋詞（避免語意模糊誤抓）
  - 內容相關性驗證關鍵字（過濾非品牌文章）
  - 品牌顯示名稱與渠道類型對應

渠道架構（三層）：
  media  層：Google News（品牌敘事訊號）
  forum  層：PTT（真實民意）
  social 層：Dcard（年輕族群）
"""

from typing import Dict, List, Optional


# ─────────────────────────────────────────────────────────────────
# 品牌主設定
# ─────────────────────────────────────────────────────────────────

# Dcard 通用排除詞：徵才/招募貼文（所有品牌共用）
_DCARD_EXCLUDE = [
    "徵才", "徵人", "誠徵", "招募", "徵店員", "徵工讀",
    "面試通知", "錄取", "應徵", "工讀生", "職缺",
]

BRANDS: Dict[str, dict] = {

    "7-ELEVEN": {
        "display_name": "7-ELEVEN（統一超商）",
        "search_queries": {
            "google_news": '7-ELEVEN OR 統一超商 OR "7-11" OR 小七 OR (統一超商 食安) OR (7-11 食品) OR (小七 食安)',
            "ptt":         "7-11 OR 統一超商 OR 小七 OR 7ELEVEN",
            "dcard":       "7-ELEVEN OR 統一超商 OR 小七 OR 711 OR 7-11",
            "youtube":     "7-ELEVEN 統一超商 OR 小七 OR 7-11超商",
        },
        "validation_keywords": [
            "7-eleven", "7-11", "7eleven", "711",
            "統一超商", "小七", "seven eleven",
        ],
        "crisis_bypass": True,
        "exclude_keywords": _DCARD_EXCLUDE,
    },

    "全家": {
        "display_name": "全家 FamilyMart",
        "search_queries": {
            "google_news": '全家便利商店 OR FamilyMart台灣 OR 全家超商 OR (全家 食安) OR (全家 食品安全)',
            "ptt":         "全家便利 OR FamilyMart OR 全家超商 OR 全家超市",
            "dcard":       "全家便利 OR FamilyMart OR 全家超商 OR 全家便利店",
            "youtube":     "全家便利商店 OR FamilyMart台灣",
        },
        "validation_keywords": [
            "全家便利", "familymart", "全家超商",
            "全家店", "fmc", "全家fp", "全家超市",
        ],
        "crisis_bypass": True,
        "exclude_keywords": _DCARD_EXCLUDE,
    },

    "萊爾富": {
        "display_name": "萊爾富 Hi-Life",
        "search_queries": {
            "google_news": '萊爾富 OR Hi-Life便利商店 OR (萊爾富 食安)',
            "ptt":         "萊爾富 OR Hi-Life OR hilife",
            "dcard":       "萊爾富 OR hilife OR hi-life",
            "youtube":     "萊爾富 Hi-Life便利商店",
        },
        "validation_keywords": [
            "萊爾富", "hi-life", "hilife", "hi life",
        ],
        "crisis_bypass": True,
        "exclude_keywords": _DCARD_EXCLUDE,
    },

    "OK mart": {
        "display_name": "OK mart",
        "search_queries": {
            "google_news": 'OK mart OR OK超商 台灣 OR (OK超商 食安)',
            "ptt":         "OK超商 OR OKmart OR OK mart OR OK便利",
            "dcard":       "OK mart OR OK超商 OR okmart",
            "youtube":     "OK mart OK超商 台灣",
        },
        "validation_keywords": [
            "ok mart", "ok超商", "okmart", "ok便利", "來來超商",
        ],
        "crisis_bypass": True,
        "exclude_keywords": _DCARD_EXCLUDE,
    },

    # ── 跨品牌食安危機監控（不限定特定品牌）────────────────────
    "超商食安": {
        "display_name": "超商食安危機監控",
        "search_queries": {
            "google_news": "超商 食安 OR 超商 異物 OR 超商 食品安全 OR 超商 活蟲 OR 超商 食品問題 OR 超商 下架 OR 超商 食物中毒",
            "ptt":         "超商食安 OR 超商異物 OR 超商蟲",
            "dcard":       "超商食安 OR 超商異物 OR 超商食品問題",
            "youtube":     "超商食安 OR 超商異物 OR 便利商店食品安全",
        },
        "validation_keywords": [
            "超商", "便利商店", "7-11", "全家", "萊爾富", "ok mart",
            "統一超商", "小七", "familymart",
        ],
        "crisis_bypass": True,
        "exclude_keywords": _DCARD_EXCLUDE,
    },
}


# ─────────────────────────────────────────────────────────────────
# 渠道類型對應
# ─────────────────────────────────────────────────────────────────

CHANNEL_LAYER: Dict[str, str] = {
    "google_news": "media",   # 媒體層（品牌敘事訊號）
    "ptt":         "forum",   # 論壇層（真實民意）
    "dcard":       "social",  # 社群層（年輕族群）
}

CHANNEL_DISPLAY: Dict[str, str] = {
    "google_news": "Google News",
    "ptt":         "PTT",
    "dcard":       "Dcard",
}

LAYER_DISPLAY: Dict[str, str] = {
    "media":  "媒體層（品牌敘事/新聞）",
    "forum":  "論壇層（PTT 真實民意）",
    "social": "社群層（Dcard 年輕族群）",
}


# ─────────────────────────────────────────────────────────────────
# 查詢工具函式
# ─────────────────────────────────────────────────────────────────

def get_brand_config(keyword: str) -> dict:
    if keyword in BRANDS:
        return BRANDS[keyword]
    for brand_key, config in BRANDS.items():
        if brand_key.lower() in keyword.lower() or keyword.lower() in brand_key.lower():
            return config
    return {
        "display_name": keyword,
        "search_queries": {ch: keyword for ch in ["google_news", "ptt", "dcard"]},
        "validation_keywords": [keyword.lower()],
        "exclude_keywords": [],
    }


def get_search_query(keyword: str, channel: str) -> str:
    """取得指定品牌在指定渠道的搜尋詞。channel: google_news | ptt | dcard"""
    config = get_brand_config(keyword)
    return config["search_queries"].get(channel, keyword)


def is_brand_relevant(keyword: str, title: str, content: str = "") -> bool:
    """判斷文章是否真的與該品牌相關。"""
    config = get_brand_config(keyword)
    validation_kws = config.get("validation_keywords", [keyword.lower()])
    exclude_kws    = config.get("exclude_keywords", [])
    combined = (title + " " + content[:500]).lower()
    if any(ex.lower() in combined for ex in exclude_kws):
        return False
    return any(vk.lower() in combined for vk in validation_kws)


def get_channel_layer(channel: str) -> str:
    return CHANNEL_LAYER.get(channel, "media")
