import json
from typing import Dict, List

from src.intelligence.topic_builder import TopicBuilder
from src.utils.db_manager import SentimentDB


class MonthlySnapshotBuilder:
    def __init__(self, db: SentimentDB):
        self.db = db
        self.topic_builder = TopicBuilder(db)

    def build_snapshot(self, snapshot_month: str, scope_type: str, scope_key: str) -> Dict:
        topics = self.db.get_intel_topics_for_month(snapshot_month=snapshot_month, scope_key=scope_key)
        top_topics = self._top_topics(topics)
        active_risks = self._active_risks(topics)
        opportunities = self._opportunities(topics)
        return {
            "snapshot_month": snapshot_month,
            "scope_type": scope_type,
            "scope_key": scope_key,
            "snapshot_at": self.db._now_iso(),
            "active_risks_json": json.dumps(active_risks, ensure_ascii=False),
            "opportunity_topics_json": json.dumps(opportunities, ensure_ascii=False),
            "top_topics_json": json.dumps(top_topics, ensure_ascii=False),
            "competitive_matrix_json": json.dumps(self._competitive_matrix(snapshot_month), ensure_ascii=False),
            "narrative_summary": self._narrative_summary(scope_key, top_topics),
            "payload_json": json.dumps({"topics": topics}, ensure_ascii=False),
        }

    def _top_topics(self, topics: List[Dict]) -> List[Dict]:
        return sorted(topics, key=lambda row: int(row.get("signal_count", 0)), reverse=True)[:10]

    def _active_risks(self, topics: List[Dict]) -> List[Dict]:
        return [topic for topic in self._top_topics(topics) if json.loads(topic.get("sentiment_mix_json") or "{}").get("負面", 0) >= 1][:5]

    def _opportunities(self, topics: List[Dict]) -> List[Dict]:
        return [
            topic for topic in self._top_topics(topics)
            if json.loads(topic.get("sentiment_mix_json") or "{}").get("正面", 0) >= 1
            or json.loads(topic.get("sentiment_mix_json") or "{}").get("中立", 0) >= 1
        ][:5]

    def _competitive_matrix(self, snapshot_month: str) -> Dict:
        return self.topic_builder.build_competitive_matrix(
            self.db.get_intel_monthly_competitive_rows(snapshot_month=snapshot_month)
        )

    def _narrative_summary(self, scope_key: str, top_topics: List[Dict]) -> str:
        labels = [topic.get("label", "未分類議題") for topic in top_topics[:3]]
        if not labels:
            return f"{scope_key} 本月尚無足夠 intelligence 議題資料。"
        return f"{scope_key} 本月主要由 {'、'.join(labels)} 定義，建議同時檢查風險延燒與可承接的內容機會。"
