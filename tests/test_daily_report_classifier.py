import unittest

from src.daily_report.classifier import classify_daily_report_signal


class DailyReportClassifierTest(unittest.TestCase):
    def test_food_safety_has_highest_priority(self):
        result = classify_daily_report_signal(
            title="[商品] 7-11 沙拉裡有活蟲",
            content="吃到一半發現有蟲",
            theme="食安危機",
            channel="ptt",
        )
        self.assertEqual(result["section_key"], "food_safety")

    def test_product_feedback_accepts_cvs_product_post(self):
        result = classify_daily_report_signal(
            title="[商品] 7-11 日東紅茶 皇家奶茶",
            content="口感偏甜 但回購意願高",
            theme="新品討論",
            channel="ptt",
        )
        self.assertEqual(result["section_key"], "product_feedback")

    def test_price_value_beats_generic_promotion_when_complaining_about_price(self):
        result = classify_daily_report_signal(
            title="[情報] 7-11 聯名新品",
            content="太貴了 CP 值很低",
            theme="聯名商品",
            channel="dcard",
        )
        self.assertEqual(result["section_key"], "price_value")


if __name__ == "__main__":
    unittest.main()
