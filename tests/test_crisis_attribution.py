import unittest

from src.config.brands import (
    get_search_terms,
    is_relevant_with_two_stage_attribution,
)


class CrisisAttributionTest(unittest.TestCase):
    def test_crisis_keyword_has_generic_search_terms(self):
        terms = get_search_terms("超商食安", "youtube")
        self.assertIn("超商 活蟲", terms)
        self.assertIn("便利商店 食物中毒", terms)

    def test_crisis_keyword_accepts_generic_crisis_article(self):
        matched, reason = is_relevant_with_two_stage_attribution(
            "超商食安",
            title="超商鮪魚沙拉驚見活蟲蠕動",
            content="便利商店義大利麵出現小蟲，民眾擔憂食品安全。",
        )
        self.assertTrue(matched)
        self.assertEqual(reason, "generic_crisis")

    def test_brand_keyword_rejects_generic_crisis_without_brand_signal(self):
        matched, reason = is_relevant_with_two_stage_attribution(
            "7-ELEVEN",
            title="超商鮪魚沙拉驚見活蟲蠕動",
            content="便利商店義大利麵出現小蟲，民眾擔憂食品安全。",
        )
        self.assertFalse(matched)
        self.assertEqual(reason, "generic_only")

    def test_brand_keyword_accepts_crisis_when_content_has_brand_signal(self):
        matched, reason = is_relevant_with_two_stage_attribution(
            "7-ELEVEN",
            title="超商鮪魚沙拉驚見活蟲蠕動",
            content="統一超商門市商品被爆料有活蟲，網友點名 7-11 品管。",
        )
        self.assertTrue(matched)
        self.assertEqual(reason, "brand_signal")


if __name__ == "__main__":
    unittest.main()
