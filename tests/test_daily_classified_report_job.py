import os
import tempfile
import unittest

from src.jobs.daily_classified_report_job import run_daily_classified_report_capture
from src.utils.db_manager import SentimentDB


class DailyReportSchemaTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.db = SentimentDB(db_path=path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_save_and_fetch_daily_report_with_sections(self):
        self.db.save_intel_daily_report(
            {
                "report_date": "2026-05-26",
                "scope_type": "brand",
                "scope_key": "7-ELEVEN",
                "snapshot_at": "2026-05-27T08:00:00+08:00",
                "headline_summary": "昨日總結",
                "payload_json": "{\"section_order\": [\"product_feedback\"]}",
            }
        )
        self.db.save_intel_daily_report_section(
            {
                "report_date": "2026-05-26",
                "scope_key": "7-ELEVEN",
                "section_key": "product_feedback",
                "section_label": "商品反饋",
                "signal_count": 2,
                "pos_count": 1,
                "neu_count": 0,
                "neg_count": 1,
                "high_risk_count": 0,
                "summary_text": "商品討論偏向口味與配料變化。",
                "top_threads_json": "[]",
                "evidence_quotes_json": "[]",
                "payload_json": "{}",
            }
        )

        report = self.db.get_intel_daily_report("2026-05-26", "brand", "7-ELEVEN")
        sections = self.db.get_intel_daily_report_sections("2026-05-26", "7-ELEVEN")
        self.assertEqual(report["headline_summary"], "昨日總結")
        self.assertEqual(sections[0]["section_key"], "product_feedback")

    def test_capture_builds_brand_daily_report_sections(self):
        run_id = self.db.create_run("7-ELEVEN")
        thread_id = self.db.save_thread(
            url="https://www.ptt.cc/bbs/CVS/M.1.html",
            source_name="PTT",
            channel="ptt",
            title="[商品] 7-11 日東紅茶 皇家奶茶",
            keyword="7-ELEVEN",
            board="CVS",
            published_at="2026-05-26T09:00:00+08:00",
        )
        self.db.save_thread_item(
            thread_id=thread_id,
            content="香氣不錯 但有點甜",
            item_type="main",
            sequence=0,
            platform_item_id="main-1",
            published_at="2026-05-26T09:00:00+08:00",
        )
        self.db.save_thread_item(
            thread_id=thread_id,
            content="沒有筍乾沒有蔥",
            item_type="push",
            author="tester",
            sequence=1,
            platform_item_id="push-1",
            published_at="2026-05-26T10:00:00+08:00",
        )
        self.db.save_analysis(
            thread_id,
            run_id,
            {
                "sentiment": "中立",
                "score": 3,
                "theme": "商品反饋",
                "reason": "口味與甜度討論",
                "voice_source": "ptt",
                "analyzed_with": "title",
                "model_used": "test",
            },
            analyzed_content="香氣不錯 但有點甜",
        )
        item_id = self.db.get_thread_item_id_by_platform_item_id("push-1")
        self.db.save_item_analysis(
            thread_item_id=item_id,
            run_id=run_id,
            analysis={
                "sentiment": "負面",
                "score": 3,
                "theme": "商品反饋",
                "reason": "配料變少",
                "voice_source": "ptt",
                "analyzed_with": "push",
                "model_used": "test",
            },
            analyzed_content="沒有筍乾沒有蔥",
        )
        self.db.close_run(run_id, articles_found=1, articles_new=1)

        result = run_daily_classified_report_capture(
            db=self.db,
            report_date="2026-05-26",
            scope_key="7-ELEVEN",
        )

        self.assertEqual(result["written_sections"], 1)
        report = self.db.get_intel_daily_report("2026-05-26", "brand", "7-ELEVEN")
        sections = self.db.get_intel_daily_report_sections("2026-05-26", "7-ELEVEN")
        self.assertIsNotNone(report)
        self.assertEqual(sections[0]["section_key"], "product_feedback")
        self.assertIn("沒有筍乾沒有蔥", sections[0]["evidence_quotes_json"])


if __name__ == "__main__":
    unittest.main()
