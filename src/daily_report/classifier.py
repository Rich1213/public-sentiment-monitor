from typing import Dict


SECTION_LABELS = {
    "food_safety": "食安 / 品質異常",
    "product_feedback": "商品反饋",
    "service_feedback": "服務體驗",
    "price_value": "價格 / CP 值",
    "promotion_campaign": "活動 / 聯名 / 行銷",
    "membership_payment": "會員 / 支付 / APP",
}


def _combined_text(title: str, content: str = "", theme: str = "") -> str:
    return " ".join([title or "", content or "", theme or ""]).lower()


def classify_daily_report_signal(
    title: str,
    content: str = "",
    theme: str = "",
    channel: str = "",
) -> Dict[str, str]:
    text = _combined_text(title, content, theme)
    title_lc = (title or "").lower()

    if any(token in text for token in ["活蟲", "異物", "食安", "發霉", "塑膠", "玻璃", "吃壞肚子", "寄生蟲"]):
        return {"section_key": "food_safety", "section_label": SECTION_LABELS["food_safety"], "reason": "food_safety_keyword"}
    if any(token in text for token in ["漲價", "太貴", "cp 值", "cp值", "縮水", "不值", "很貴", "企劃問題很大"]):
        return {"section_key": "price_value", "section_label": SECTION_LABELS["price_value"], "reason": "price_value_keyword"}
    if any(token in text for token in ["店員", "服務", "結帳", "態度", "排隊", "補貨", "等待", "流程"]):
        return {"section_key": "service_feedback", "section_label": SECTION_LABELS["service_feedback"], "reason": "service_feedback_keyword"}
    if any(token in text for token in ["會員", "app", "支付", "點數", "載具", "發票", "openpoint", "open point"]):
        return {"section_key": "membership_payment", "section_label": SECTION_LABELS["membership_payment"], "reason": "membership_payment_keyword"}
    if any(tag in title_lc for tag in ["[商品]", "[心得]", "[食記]", "[評價]", "[開箱]"]):
        return {"section_key": "product_feedback", "section_label": SECTION_LABELS["product_feedback"], "reason": "product_tag"}
    if any(token in text for token in ["聯名", "活動", "新品", "限定", "回歸", "復刻", "抽獎", "買一送一"]):
        return {"section_key": "promotion_campaign", "section_label": SECTION_LABELS["promotion_campaign"], "reason": "promotion_campaign_keyword"}
    return {"section_key": "product_feedback", "section_label": SECTION_LABELS["product_feedback"], "reason": "default_product_feedback"}
