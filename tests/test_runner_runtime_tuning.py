import os
import unittest
from unittest.mock import MagicMock
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

    def test_run_all_brands_triggers_intelligence_refresh_after_success(self):
        db = MagicMock()
        db.create_monitor_batch.return_value = 123

        with patch("src.utils.db_manager.SentimentDB", return_value=db), \
             patch("src.analyzers.sentiment_analyzer.SentimentAnalyzer", return_value=MagicMock()), \
             patch("src.analyzers.pr_advisor.PRAdvisor", return_value=MagicMock()), \
             patch("src.notifiers.telegram_notifier.TelegramNotifier.is_enabled", return_value=False), \
             patch.object(runner, "run_monitor") as run_monitor_mock, \
             patch.object(runner, "_run_intelligence_refresh") as refresh_mock:
            runner.run_all_brands(keywords=["7-ELEVEN"], fresh_mode=False, print_banner=False, batch_id=456)

        run_monitor_mock.assert_called_once()
        refresh_mock.assert_called_once_with(db, ["7-ELEVEN"])
        db.close_monitor_batch.assert_called_once_with(456, status="completed")

    def test_run_all_brands_still_refreshes_intelligence_after_per_brand_failure(self):
        db = MagicMock()
        db.create_monitor_batch.return_value = 123

        with patch("src.utils.db_manager.SentimentDB", return_value=db), \
             patch("src.analyzers.sentiment_analyzer.SentimentAnalyzer", return_value=MagicMock()), \
             patch("src.analyzers.pr_advisor.PRAdvisor", return_value=MagicMock()), \
             patch("src.notifiers.telegram_notifier.TelegramNotifier.is_enabled", return_value=False), \
             patch.object(runner, "run_monitor", side_effect=RuntimeError("boom")), \
             patch.object(runner, "_run_intelligence_refresh") as refresh_mock:
            runner.run_all_brands(keywords=["7-ELEVEN"], fresh_mode=False, print_banner=False, batch_id=456)

        refresh_mock.assert_called_once_with(db, ["7-ELEVEN"])
        db.close_monitor_batch.assert_called_once_with(456, status="completed")


if __name__ == "__main__":
    unittest.main()
