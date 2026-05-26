import os
import unittest
from unittest.mock import patch

import worker.runner as runner


class RunnerRuntimeTuningTest(unittest.TestCase):
    def test_inter_article_delay_defaults_to_zero_for_paid_openai_compat(self):
        with patch.dict(
            os.environ,
            {"MODEL_SENTIMENT_PROVIDER": "openai_compat"},
            clear=True,
        ):
            self.assertEqual(runner._resolve_inter_article_delay(), 0.0)

    def test_inter_article_delay_keeps_explicit_override(self):
        with patch.dict(
            os.environ,
            {
                "MODEL_SENTIMENT_PROVIDER": "openai_compat",
                "INTER_ARTICLE_DELAY": "0.5",
            },
            clear=True,
        ):
            self.assertEqual(runner._resolve_inter_article_delay(), 0.5)

    def test_inter_brand_cooldown_defaults_to_zero_for_paid_openai_compat(self):
        with patch.dict(
            os.environ,
            {"MODEL_SENTIMENT_PROVIDER": "openai_compat"},
            clear=True,
        ):
            self.assertEqual(runner._resolve_inter_brand_cooldown(), 0)

    def test_inter_brand_cooldown_keeps_explicit_override(self):
        with patch.dict(
            os.environ,
            {
                "MODEL_SENTIMENT_PROVIDER": "openai_compat",
                "INTER_BRAND_COOLDOWN": "5",
            },
            clear=True,
        ):
            self.assertEqual(runner._resolve_inter_brand_cooldown(), 5)

    def test_nvidia_defaults_remain_conservative(self):
        with patch.dict(
            os.environ,
            {"MODEL_SENTIMENT_PROVIDER": "nvidia"},
            clear=True,
        ):
            self.assertEqual(runner._resolve_inter_article_delay(), 1.5)
            self.assertEqual(runner._resolve_inter_brand_cooldown(), 60)


if __name__ == "__main__":
    unittest.main()
