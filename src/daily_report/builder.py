from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from src.daily_report.classifier import classify_daily_report_signal, SECTION_LABELS
from src.utils.db_manager import SentimentDB, APP_TIMEZONE


class DailyReportBuilder:
    def __init__(self, db: SentimentDB):
        self.db = db

    def build_report(self, report_date: str, scope_key: str) -> Dict[str, Any]:
        rows = self.db.get_daily_report_signal_rows(report_date=report_date, scope_key=scope_key)
        evidence_rows = self.db.get_daily_report_evidence_rows(report_date=report_date, scope_key=scope_key)
        sections = self._group_sections(rows, evidence_rows)
        headline_summary = self._build_headline_summary(scope_key, sections)
        return {
            "report": {
                "report_date": report_date,
                "scope_type": "brand",
                "scope_key": scope_key,
                "snapshot_at": datetime.now(ZoneInfo(APP_TIMEZONE)).isoformat(),
                "headline_summary": headline_summary,
                "payload_json": self.db._json_dumps({"section_order": [s["section_key"] for s in sections]}),
            },
            "sections": sections,
        }

    def _group_sections(self, rows: List[Dict[str, Any]], evidence_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        evidence_by_thread: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in evidence_rows:
            evidence_by_thread[str(row.get("thread_id"))].append(row)

        for row in rows:
            classified = classify_daily_report_signal(
                title=row.get("title", ""),
                content=f"{row.get('reason', '')} {row.get('theme', '')}",
                theme=row.get("theme", ""),
                channel=row.get("channel", ""),
            )
            section_key = classified["section_key"]
            bucket = grouped.setdefault(
                section_key,
                {
                    "section_key": section_key,
                    "section_label": classified["section_label"],
                    "signal_count": 0,
                    "pos_count": 0,
                    "neu_count": 0,
                    "neg_count": 0,
                    "high_risk_count": 0,
                    "summary_text": "",
                    "top_threads": [],
                    "evidence_quotes": [],
                    "payload_json": "{}",
                    "_rows": [],
                },
            )
            bucket["signal_count"] += 1
            sentiment = row.get("sentiment")
            if sentiment == "正面":
                bucket["pos_count"] += 1
            elif sentiment == "負面":
                bucket["neg_count"] += 1
            else:
                bucket["neu_count"] += 1
            if int(round(row.get("score") or 0)) >= 4:
                bucket["high_risk_count"] += 1
            bucket["_rows"].append(row)

        sections: List[Dict[str, Any]] = []
        for bucket in grouped.values():
            bucket["_rows"].sort(
                key=lambda row: (int(round(row.get("score") or 0)), row.get("published_at") or row.get("first_seen_at") or row.get("analyzed_at") or ""),
                reverse=True,
            )
            top_rows = bucket["_rows"][:3]
            bucket["top_threads"] = [
                {
                    "thread_id": row.get("thread_id"),
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "channel": row.get("channel"),
                    "sentiment": row.get("sentiment"),
                    "score": int(round(row.get("score") or 0)),
                    "theme": row.get("theme"),
                }
                for row in top_rows
            ]
            bucket["evidence_quotes"] = self._pick_evidence_quotes(top_rows, evidence_by_thread)
            bucket["summary_text"] = self._build_section_summary(bucket)
            bucket["payload_json"] = self.db._json_dumps({"classification_count": len(bucket["_rows"])})
            bucket["top_threads_json"] = self.db._json_dumps(bucket["top_threads"])
            bucket["evidence_quotes_json"] = self.db._json_dumps(bucket["evidence_quotes"])
            del bucket["_rows"]
            sections.append(bucket)

        sections.sort(key=lambda item: (item["high_risk_count"], item["signal_count"], -list(SECTION_LABELS.keys()).index(item["section_key"])), reverse=True)
        return sections

    def _pick_evidence_quotes(self, top_rows: List[Dict[str, Any]], evidence_by_thread: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        quotes: List[Dict[str, Any]] = []
        seen = set()
        for row in top_rows:
            for evidence in evidence_by_thread.get(str(row.get("thread_id")), []):
                quote = (evidence.get("content") or "").strip()
                if not quote or quote in seen:
                    continue
                seen.add(quote)
                quotes.append(
                    {
                        "quote": quote,
                        "author": evidence.get("author"),
                        "sentiment": evidence.get("sentiment"),
                        "score": int(round(evidence.get("score") or 0)),
                    }
                )
                if len(quotes) >= 3:
                    return quotes
        return quotes

    def _build_section_summary(self, bucket: Dict[str, Any]) -> str:
        threads = bucket.get("top_threads", [])
        top_themes = [thread.get("theme") for thread in threads if thread.get("theme")]
        theme_text = "、".join(list(dict.fromkeys(top_themes))[:2]) if top_themes else bucket["section_label"]
        if bucket["neg_count"] > max(bucket["pos_count"], bucket["neu_count"]):
            tone = "負面討論為主"
        elif bucket["pos_count"] > max(bucket["neg_count"], bucket["neu_count"]):
            tone = "正面反饋較多"
        else:
            tone = "正負意見交錯"
        evidence = bucket.get("evidence_quotes", [])
        if evidence:
            return f"昨日此類以{theme_text}為主，{tone}；代表留言顯示消費者最在意的是「{evidence[0]['quote'][:28]}」。"
        return f"昨日此類以{theme_text}為主，{tone}。"

    def _build_headline_summary(self, scope_key: str, sections: List[Dict[str, Any]]) -> str:
        if not sections:
            return f"昨日 {scope_key} 尚無足夠輿情資料可供分類匯報。"
        primary = sections[0]
        secondary = sections[1] if len(sections) > 1 else None
        primary_text = primary["section_label"]
        secondary_text = f"與{secondary['section_label']}" if secondary else ""
        risk_text = "，未見明顯高風險食安擴散" if primary.get("high_risk_count", 0) == 0 else "，其中已有高風險訊號需優先關注"
        return f"昨日 {scope_key} 輿情以{primary_text}{secondary_text}為主{risk_text}。"
