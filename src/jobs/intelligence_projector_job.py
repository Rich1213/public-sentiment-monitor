from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.intelligence.event_case_builder import EventCaseBuilder
from src.intelligence.topic_builder import TopicBuilder
from src.utils.db_manager import APP_TIMEZONE, SentimentDB


def run_intelligence_projector(db: SentimentDB = None, since_date: str = None) -> dict:
    db = db or SentimentDB()
    since_date = since_date or (
        datetime.now(ZoneInfo(APP_TIMEZONE)) - timedelta(days=90)
    ).date().isoformat()

    event_case_builder = EventCaseBuilder(db)
    topic_builder = TopicBuilder(db)

    event_cases = event_case_builder.project_recent_cases(since_date=since_date)
    topics = topic_builder.project_recent_topics(since_date=since_date)

    return {
        "since_date": since_date,
        "event_cases": event_cases,
        "topics": topics,
    }
