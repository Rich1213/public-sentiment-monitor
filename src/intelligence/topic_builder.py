import hashlib
import json
from typing import Dict, List

from src.utils.db_manager import SentimentDB


class TopicBuilder:
    def __init__(self, db: SentimentDB):
        self.db = db

    def build_topics(self, event_cases: List[Dict]) -> List[Dict]:
        grouped: Dict[tuple, List[Dict]] = {}
        for case in event_cases:
            key = (case["keyword"], case["canonical_theme"])
            grouped.setdefault(key, []).append(case)

        topics: List[Dict] = []
        for (scope_key, theme), rows in grouped.items():
            first_seen_at = min(row["first_seen_at"] for row in rows)
            last_seen_at = max(row["last_seen_at"] for row in rows)
            signal_count = sum(int(row["evidence_count"]) for row in rows)
            source_mix: Dict[str, int] = {}
            sentiment_mix: Dict[str, int] = {}
            for row in rows:
                for channel, count in json.loads(row.get("source_mix_json") or "{}").items():
                    source_mix[channel] = source_mix.get(channel, 0) + int(count)
                for sentiment, count in json.loads(row.get("sentiment_mix_json") or "{}").items():
                    sentiment_mix[sentiment] = sentiment_mix.get(sentiment, 0) + int(count)
            topics.append(
                {
                    "id": hashlib.md5(f"{scope_key}:{theme}".encode()).hexdigest(),
                    "scope_key": scope_key,
                    "canonical_theme": theme,
                    "label": theme,
                    "first_seen_at": first_seen_at,
                    "last_seen_at": last_seen_at,
                    "event_count": len(rows),
                    "signal_count": signal_count,
                    "sentiment_mix_json": json.dumps(sentiment_mix, ensure_ascii=False),
                    "source_mix_json": json.dumps(source_mix, ensure_ascii=False),
                    "metadata_json": json.dumps({"event_case_ids": [row["id"] for row in rows]}, ensure_ascii=False),
                    "event_case_ids": [row["id"] for row in rows],
                }
            )
        return topics

    def build_competitive_matrix(self, rows: List[Dict]) -> Dict[str, Dict[str, int]]:
        matrix: Dict[str, Dict[str, int]] = {}
        for row in rows:
            matrix.setdefault(row["canonical_theme"], {})[row["scope_key"]] = int(row["signal_count"])
        return matrix

    def save_topics(self, topics: List[Dict]) -> int:
        for topic in topics:
            self.db.save_intel_topic(topic)
            for event_case_id in topic["event_case_ids"]:
                self.db.bind_event_case_to_intel_topic(
                    topic_id=topic["id"],
                    event_case_id=event_case_id,
                    first_bound_at=topic["first_seen_at"],
                    last_bound_at=topic["last_seen_at"],
                )
        return len(topics)

    def project_recent_topics(self, since_date: str) -> int:
        topics = self.build_topics(self.db.get_intel_event_cases(since_date=since_date))
        return self.save_topics(topics)
