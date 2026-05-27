import hashlib
import json
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Dict, List
from zoneinfo import ZoneInfo

from src.utils.db_manager import APP_TIMEZONE, SentimentDB


class EventCaseBuilder:
    WINDOW_DAYS = 7

    def __init__(self, db: SentimentDB):
        self.db = db

    def build_cases(self, signals: List[Dict]) -> List[Dict]:
        grouped: Dict[tuple, List[Dict]] = {}
        for signal in signals:
            theme = (signal.get("theme") or "未分類議題").strip()
            key = (signal["keyword"], theme)
            grouped.setdefault(key, []).append(signal)

        cases: List[Dict] = []
        for (keyword, theme), rows in grouped.items():
            rows = sorted(rows, key=lambda row: self._parse_dt(row["published_at"]))
            cluster: List[Dict] = []
            cluster_start: datetime | None = None

            for row in rows:
                published_at = self._parse_dt(row["published_at"])
                if not cluster:
                    cluster = [row]
                    cluster_start = published_at
                    continue
                if published_at - cluster_start <= timedelta(days=self.WINDOW_DAYS):
                    cluster.append(row)
                    continue

                cases.append(self._materialize_case(keyword, theme, cluster_start, cluster))
                cluster = [row]
                cluster_start = published_at

            if cluster and cluster_start is not None:
                cases.append(self._materialize_case(keyword, theme, cluster_start, cluster))
        return cases

    def project_recent_cases(self, since_date: str) -> int:
        signals = self.db.get_intelligence_signal_rows(since_date=since_date)
        cases = self.build_cases(signals)
        for case in cases:
            self.db.save_intel_event_case(case)
            for member in case["members"]:
                self.db.bind_thread_to_intel_event_case(
                    event_case_id=case["id"],
                    thread_id=member["thread_id"],
                    latest_analysis_id=member["analysis_id"],
                    first_bound_at=case["first_seen_at"],
                    last_bound_at=case["last_seen_at"],
                )
        return len(cases)

    def _parse_dt(self, value: str) -> datetime:
        text = str(value).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(APP_TIMEZONE))
        return dt.astimezone(ZoneInfo(APP_TIMEZONE))

    def _materialize_case(self, keyword: str, theme: str, cluster_start: datetime, rows: List[Dict]) -> Dict:
        first_seen_at = rows[0]["published_at"]
        last_seen_at = rows[-1]["published_at"]
        source_mix: Dict[str, int] = {}
        sentiment_mix: Dict[str, int] = {}
        for row in rows:
            source_mix[row["channel"]] = source_mix.get(row["channel"], 0) + 1
            sentiment_mix[row["sentiment"]] = sentiment_mix.get(row["sentiment"], 0) + 1
        return {
            "id": hashlib.md5(f"{keyword}:{theme}:{cluster_start.isoformat()}".encode()).hexdigest(),
            "keyword": keyword,
            "canonical_theme": theme,
            "label": theme,
            "status": "active",
            "severity": max(int(row.get("score") or 0) for row in rows),
            "first_seen_at": first_seen_at,
            "last_seen_at": last_seen_at,
            "evidence_count": len(rows),
            "source_mix_json": json.dumps(source_mix, ensure_ascii=False),
            "sentiment_mix_json": json.dumps(sentiment_mix, ensure_ascii=False),
            "metadata_json": json.dumps({"window_start": cluster_start.date().isoformat()}, ensure_ascii=False),
            "members": rows,
        }
