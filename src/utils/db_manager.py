"""
SentimentDB — 三層式渠道區隔資料庫管理器

支援雙後端：
  - SQLite（預設，本地開發）
  - PostgreSQL（有 DATABASE_URL 環境變數時自動切換）

Layer 1  Sources / Keywords / Runs
         sources         渠道登錄表（PTT / Google News / Dcard）
         keywords        關鍵字管理
         monitoring_runs 每次執行紀錄

Layer 2  Thread-level（文章 / 討論串）
         threads         主文 metadata + 前置信號（push/boo/comment 數）

Layer 3  Item-level（內容原子）
         thread_items    主文內容、推文、噓文、回覆

Analysis  LLM 結果
         analyses        情感分析結果，對應 thread + run
         pr_reports      公關策略報告，對應 run
"""

import os
import json
import hashlib
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from zoneinfo import ZoneInfo

from src.utils.score_utils import normalize_score, is_legacy_score
from src.utils.alert_engine import (
    is_alert_eligible, is_thread_alert_by_items,
    build_alert_from_row, build_alert_from_items,
    sort_alerts,
)

logger = logging.getLogger(__name__)
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Taipei")

# ─────────────────────────────────────────────────────────────
# 預設渠道種子資料
# ─────────────────────────────────────────────────────────────
DEFAULT_SOURCES = [
    # (name, type, is_active, weight, fetch_limit)
    ("Google News", "news",  1, 1.2, 10),
    ("PTT",         "forum", 1, 1.0, 10),
    ("Dcard",       "forum", 1, 0.9, 10),
    ("Threads",     "social", 1, 0.9, 10),
]


# ─────────────────────────────────────────────────────────────
# DBAdapter — 處理 SQLite vs PostgreSQL 語法差異
# ─────────────────────────────────────────────────────────────
class DBAdapter:
    """
    統一 SQLite 和 PostgreSQL 的操作介面。

    主要差異處理：
    1. Placeholder：SQLite 用 `?`，Postgres 用 `%s`
    2. AUTOINCREMENT：SQLite 用 INTEGER PRIMARY KEY AUTOINCREMENT，
                      Postgres 用 SERIAL PRIMARY KEY
    3. 日期函數：SQLite 的 datetime('now') → Postgres 的NOW()
    4. INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    5. INSERT OR REPLACE → INSERT ... ON CONFLICT ... DO UPDATE
    """

    def __init__(self):
        self._database_url = os.getenv("DATABASE_URL", "")
        self._is_postgres = bool(self._database_url)
        self._sqlite_path = os.getenv("SQLITE_PATH", "sentiment_monitor.db")

    @property
    def is_postgres(self) -> bool:
        return self._is_postgres

    @property
    def placeholder(self) -> str:
        """SQL 參數佔位符。"""
        return "%s" if self._is_postgres else "?"

    def get_connection(self):
        """取得資料庫連線。"""
        if self._is_postgres:
            try:
                import psycopg2
                import psycopg2.extras
                conn = psycopg2.connect(self._database_url)
                conn.autocommit = False
                return conn
            except ImportError:
                raise ImportError("請安裝 psycopg2-binary：pip install psycopg2-binary>=2.9.9")
        else:
            import sqlite3
            conn = sqlite3.connect(self._sqlite_path)
            conn.row_factory = sqlite3.Row
            return conn

    def fetchone_dict(self, cursor, row) -> Optional[Dict]:
        """將 cursor row 轉為 dict（相容兩種 DB）。"""
        if row is None:
            return None
        if self._is_postgres:
            cols = [desc[0] for desc in cursor.description]
            return dict(zip(cols, row))
        else:
            return dict(row)

    def fetchall_dict(self, cursor) -> List[Dict]:
        """將所有 rows 轉為 list of dict。"""
        rows = cursor.fetchall()
        if self._is_postgres:
            cols = [desc[0] for desc in cursor.description]
            return [dict(zip(cols, row)) for row in rows]
        else:
            return [dict(row) for row in rows]

    def adapt_schema(self, sqlite_sql: str) -> str:
        """
        將 SQLite DDL 轉換為 PostgreSQL 相容語法。
        """
        if not self._is_postgres:
            return sqlite_sql

        sql = sqlite_sql
        # AUTOINCREMENT
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        # TEXT PRIMARY KEY (threads.id) → 保留 TEXT
        # datetime('now') → NOW()
        sql = sql.replace("datetime('now')", "NOW()")
        # PRAGMA → 跳過
        if sql.strip().upper().startswith("PRAGMA"):
            return ""
        # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        sql = sql.replace("INSERT OR REPLACE INTO", "INSERT INTO")
        # WAL journal mode → 跳過
        if "journal_mode" in sql:
            return ""
        return sql

    def insert_ignore(self, table: str, columns: List[str], values: Tuple,
                      conflict_col: str = None) -> str:
        """
        生成 INSERT ... ON CONFLICT DO NOTHING 語句。
        回傳 SQL 字串（placeholder 已套用）。
        """
        ph = self.placeholder
        cols_str = ", ".join(columns)
        vals_str = ", ".join([ph] * len(columns))

        if self._is_postgres:
            conflict = f"ON CONFLICT ({conflict_col}) DO NOTHING" if conflict_col else "ON CONFLICT DO NOTHING"
            return f"INSERT INTO {table} ({cols_str}) VALUES ({vals_str}) {conflict}"
        else:
            return f"INSERT OR IGNORE INTO {table} ({cols_str}) VALUES ({vals_str})"

    def insert_replace(self, table: str, columns: List[str],
                       conflict_col: str, update_cols: List[str]) -> str:
        """
        生成 INSERT ... ON CONFLICT ... DO UPDATE 語句（upsert）。
        """
        ph = self.placeholder
        cols_str = ", ".join(columns)
        vals_str = ", ".join([ph] * len(columns))

        if self._is_postgres:
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
            return (
                f"INSERT INTO {table} ({cols_str}) VALUES ({vals_str}) "
                f"ON CONFLICT ({conflict_col}) DO UPDATE SET {updates}"
            )
        else:
            return f"INSERT OR REPLACE INTO {table} ({cols_str}) VALUES ({vals_str})"

    def last_insert_id(self, cursor, sequence_name: str = None) -> int:
        """取得最後插入的 rowid / serial id。"""
        if self._is_postgres:
            if sequence_name:
                cursor.execute(f"SELECT currval('{sequence_name}')")
            else:
                cursor.execute("SELECT lastval()")
            return cursor.fetchone()[0]
        else:
            return cursor.lastrowid


# ─────────────────────────────────────────────────────────────
# SentimentDB
# ─────────────────────────────────────────────────────────────
class SentimentDB:
    _initialized_targets = set()

    def __init__(self, db_path: str = None):
        self._adapter = DBAdapter()

        # 向後相容：允許傳入 db_path 覆寫 SQLite 路徑
        if db_path is not None and not self._adapter.is_postgres:
            self._adapter._sqlite_path = db_path

        target_key = self._target_key()
        if target_key not in self.__class__._initialized_targets:
            self._init_db()
            self._ensure_schema_migrations()
            self._seed_sources()
            self.__class__._initialized_targets.add(target_key)

    @property
    def adapter(self) -> DBAdapter:
        return self._adapter

    def _target_key(self) -> Tuple[str, str]:
        if self._adapter.is_postgres:
            return ("postgres", self._adapter._database_url)
        return ("sqlite", self._adapter._sqlite_path)

    # ── Schema 建立 ──────────────────────────────────────────
    def _init_db(self):
        """建立所有資料表（SQLite 和 Postgres 共用）。"""
        if self._adapter.is_postgres:
            self._init_postgres()
        else:
            self._init_sqlite()

    def _init_sqlite(self):
        import sqlite3
        with sqlite3.connect(self._adapter._sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.executescript(self._sqlite_schema())
            conn.commit()

    def _init_postgres(self):
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            for stmt in self._postgres_schema_statements():
                if stmt.strip():
                    c.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema_migrations(self):
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            if self._adapter.is_postgres:
                c.execute("ALTER TABLE pr_reports ADD COLUMN IF NOT EXISTS dashboard_summary TEXT")
                c.execute("ALTER TABLE threads ADD COLUMN IF NOT EXISTS platform_id TEXT")
                c.execute("ALTER TABLE threads ADD COLUMN IF NOT EXISTS first_seen_at TEXT")
                c.execute("ALTER TABLE thread_items ADD COLUMN IF NOT EXISTS platform_item_id TEXT")
                c.execute("ALTER TABLE thread_items ADD COLUMN IF NOT EXISTS like_count INTEGER")
                c.execute("ALTER TABLE thread_items ADD COLUMN IF NOT EXISTS published_at TEXT")
                c.execute("UPDATE threads SET first_seen_at = COALESCE(first_seen_at, published_at, fetched_at)")
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_threads_platform_id ON threads(platform_id)")
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_thread_items_platform_item_id ON thread_items(platform_item_id)")
                c.execute("""CREATE TABLE IF NOT EXISTS monitor_batches (
                    id         SERIAL PRIMARY KEY,
                    batch_key  TEXT    NOT NULL,
                    keywords   TEXT    NOT NULL,
                    fresh_mode INTEGER DEFAULT 0,
                    started_at TEXT    NOT NULL,
                    ended_at   TEXT,
                    status     TEXT    DEFAULT 'running'
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_monitor_batches_open ON monitor_batches(batch_key, ended_at)")
                c.execute("""CREATE TABLE IF NOT EXISTS daily_snapshots (
                    id                SERIAL PRIMARY KEY,
                    snapshot_date     TEXT    NOT NULL,
                    keyword           TEXT    NOT NULL,
                    snapshot_at       TEXT    NOT NULL,
                    risk_score        INTEGER DEFAULT 0,
                    article_count     INTEGER DEFAULT 0,
                    pos_count         INTEGER DEFAULT 0,
                    neu_count         INTEGER DEFAULT 0,
                    neg_count         INTEGER DEFAULT 0,
                    high_risk_count   INTEGER DEFAULT 0,
                    avg_score         REAL    DEFAULT 0,
                    channel_breakdown TEXT,
                    top_themes        TEXT,
                    dashboard_summary TEXT,
                    payload_json      TEXT,
                    UNIQUE(snapshot_date, keyword)
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_daily_snapshots_date ON daily_snapshots(snapshot_date)")
                c.execute("""CREATE TABLE IF NOT EXISTS intel_event_cases (
                    id                 TEXT PRIMARY KEY,
                    keyword            TEXT NOT NULL,
                    canonical_theme    TEXT NOT NULL,
                    label              TEXT NOT NULL,
                    status             TEXT NOT NULL DEFAULT 'active',
                    severity           INTEGER DEFAULT 0,
                    first_seen_at      TEXT NOT NULL,
                    last_seen_at       TEXT NOT NULL,
                    evidence_count     INTEGER DEFAULT 0,
                    source_mix_json    TEXT,
                    sentiment_mix_json TEXT,
                    metadata_json      TEXT,
                    created_at         TEXT DEFAULT NOW(),
                    updated_at         TEXT DEFAULT NOW()
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_event_cases_keyword ON intel_event_cases(keyword)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_event_cases_last_seen ON intel_event_cases(last_seen_at)")
                c.execute("""CREATE TABLE IF NOT EXISTS intel_event_case_threads (
                    id                 SERIAL PRIMARY KEY,
                    event_case_id      TEXT NOT NULL REFERENCES intel_event_cases(id) ON DELETE CASCADE,
                    thread_id          TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                    latest_analysis_id INTEGER REFERENCES analyses(id),
                    first_bound_at     TEXT NOT NULL,
                    last_bound_at      TEXT NOT NULL,
                    UNIQUE(event_case_id, thread_id)
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_event_case_threads_case ON intel_event_case_threads(event_case_id)")
                c.execute("""CREATE TABLE IF NOT EXISTS intel_topics (
                    id                 TEXT PRIMARY KEY,
                    scope_key          TEXT NOT NULL,
                    canonical_theme    TEXT NOT NULL,
                    label              TEXT NOT NULL,
                    first_seen_at      TEXT NOT NULL,
                    last_seen_at       TEXT NOT NULL,
                    event_count        INTEGER DEFAULT 0,
                    signal_count       INTEGER DEFAULT 0,
                    sentiment_mix_json TEXT,
                    source_mix_json    TEXT,
                    metadata_json      TEXT,
                    created_at         TEXT DEFAULT NOW(),
                    updated_at         TEXT DEFAULT NOW()
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_topics_scope ON intel_topics(scope_key)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_topics_last_seen ON intel_topics(last_seen_at)")
                c.execute("""CREATE TABLE IF NOT EXISTS intel_topic_events (
                    id             SERIAL PRIMARY KEY,
                    topic_id       TEXT NOT NULL REFERENCES intel_topics(id) ON DELETE CASCADE,
                    event_case_id  TEXT NOT NULL REFERENCES intel_event_cases(id) ON DELETE CASCADE,
                    first_bound_at TEXT NOT NULL,
                    last_bound_at  TEXT NOT NULL,
                    UNIQUE(topic_id, event_case_id)
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_topic_events_topic ON intel_topic_events(topic_id)")
                c.execute("""CREATE TABLE IF NOT EXISTS intel_monthly_snapshots (
                    id                      SERIAL PRIMARY KEY,
                    snapshot_month          TEXT NOT NULL,
                    scope_type              TEXT NOT NULL,
                    scope_key               TEXT NOT NULL,
                    snapshot_at             TEXT NOT NULL,
                    active_risks_json       TEXT,
                    opportunity_topics_json TEXT,
                    top_topics_json         TEXT,
                    competitive_matrix_json TEXT,
                    narrative_summary       TEXT,
                    payload_json            TEXT,
                    UNIQUE(snapshot_month, scope_type, scope_key)
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_monthly_snapshots_month ON intel_monthly_snapshots(snapshot_month)")
                c.execute("""CREATE TABLE IF NOT EXISTS collector_cache (
                    cache_key    TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    expires_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_collector_cache_expires ON collector_cache(expires_at)")
            else:
                c.execute("PRAGMA table_info(pr_reports)")
                cols = [row["name"] for row in c.fetchall()]
                if "dashboard_summary" not in cols:
                    c.execute("ALTER TABLE pr_reports ADD COLUMN dashboard_summary TEXT")
                c.execute("PRAGMA table_info(threads)")
                thread_cols = [row["name"] for row in c.fetchall()]
                if "platform_id" not in thread_cols:
                    c.execute("ALTER TABLE threads ADD COLUMN platform_id TEXT")
                if "first_seen_at" not in thread_cols:
                    c.execute("ALTER TABLE threads ADD COLUMN first_seen_at TEXT")
                c.execute("UPDATE threads SET first_seen_at = COALESCE(first_seen_at, published_at, fetched_at)")
                c.execute("PRAGMA table_info(thread_items)")
                item_cols = [row["name"] for row in c.fetchall()]
                if "platform_item_id" not in item_cols:
                    c.execute("ALTER TABLE thread_items ADD COLUMN platform_item_id TEXT")
                if "like_count" not in item_cols:
                    c.execute("ALTER TABLE thread_items ADD COLUMN like_count INTEGER")
                if "published_at" not in item_cols:
                    c.execute("ALTER TABLE thread_items ADD COLUMN published_at TEXT")
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_threads_platform_id ON threads(platform_id)")
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_thread_items_platform_item_id ON thread_items(platform_item_id)")
                c.execute("""CREATE TABLE IF NOT EXISTS monitor_batches (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_key  TEXT    NOT NULL,
                    keywords   TEXT    NOT NULL,
                    fresh_mode INTEGER DEFAULT 0,
                    started_at TEXT    NOT NULL,
                    ended_at   TEXT,
                    status     TEXT    DEFAULT 'running'
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_monitor_batches_open ON monitor_batches(batch_key, ended_at)")
                c.execute("""CREATE TABLE IF NOT EXISTS daily_snapshots (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date     TEXT    NOT NULL,
                    keyword           TEXT    NOT NULL,
                    snapshot_at       TEXT    NOT NULL,
                    risk_score        INTEGER DEFAULT 0,
                    article_count     INTEGER DEFAULT 0,
                    pos_count         INTEGER DEFAULT 0,
                    neu_count         INTEGER DEFAULT 0,
                    neg_count         INTEGER DEFAULT 0,
                    high_risk_count   INTEGER DEFAULT 0,
                    avg_score         REAL    DEFAULT 0,
                    channel_breakdown TEXT,
                    top_themes        TEXT,
                    dashboard_summary TEXT,
                    payload_json      TEXT,
                    UNIQUE(snapshot_date, keyword)
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_daily_snapshots_date ON daily_snapshots(snapshot_date)")
                c.execute("""CREATE TABLE IF NOT EXISTS intel_event_cases (
                    id                 TEXT PRIMARY KEY,
                    keyword            TEXT NOT NULL,
                    canonical_theme    TEXT NOT NULL,
                    label              TEXT NOT NULL,
                    status             TEXT NOT NULL DEFAULT 'active',
                    severity           INTEGER DEFAULT 0,
                    first_seen_at      TEXT NOT NULL,
                    last_seen_at       TEXT NOT NULL,
                    evidence_count     INTEGER DEFAULT 0,
                    source_mix_json    TEXT,
                    sentiment_mix_json TEXT,
                    metadata_json      TEXT,
                    created_at         TEXT DEFAULT (datetime('now')),
                    updated_at         TEXT DEFAULT (datetime('now'))
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_event_cases_keyword ON intel_event_cases(keyword)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_event_cases_last_seen ON intel_event_cases(last_seen_at)")
                c.execute("""CREATE TABLE IF NOT EXISTS intel_event_case_threads (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_case_id      TEXT NOT NULL REFERENCES intel_event_cases(id) ON DELETE CASCADE,
                    thread_id          TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                    latest_analysis_id INTEGER REFERENCES analyses(id),
                    first_bound_at     TEXT NOT NULL,
                    last_bound_at      TEXT NOT NULL,
                    UNIQUE(event_case_id, thread_id)
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_event_case_threads_case ON intel_event_case_threads(event_case_id)")
                c.execute("""CREATE TABLE IF NOT EXISTS intel_topics (
                    id                 TEXT PRIMARY KEY,
                    scope_key          TEXT NOT NULL,
                    canonical_theme    TEXT NOT NULL,
                    label              TEXT NOT NULL,
                    first_seen_at      TEXT NOT NULL,
                    last_seen_at       TEXT NOT NULL,
                    event_count        INTEGER DEFAULT 0,
                    signal_count       INTEGER DEFAULT 0,
                    sentiment_mix_json TEXT,
                    source_mix_json    TEXT,
                    metadata_json      TEXT,
                    created_at         TEXT DEFAULT (datetime('now')),
                    updated_at         TEXT DEFAULT (datetime('now'))
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_topics_scope ON intel_topics(scope_key)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_topics_last_seen ON intel_topics(last_seen_at)")
                c.execute("""CREATE TABLE IF NOT EXISTS intel_topic_events (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id       TEXT NOT NULL REFERENCES intel_topics(id) ON DELETE CASCADE,
                    event_case_id  TEXT NOT NULL REFERENCES intel_event_cases(id) ON DELETE CASCADE,
                    first_bound_at TEXT NOT NULL,
                    last_bound_at  TEXT NOT NULL,
                    UNIQUE(topic_id, event_case_id)
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_topic_events_topic ON intel_topic_events(topic_id)")
                c.execute("""CREATE TABLE IF NOT EXISTS intel_monthly_snapshots (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_month          TEXT NOT NULL,
                    scope_type              TEXT NOT NULL,
                    scope_key               TEXT NOT NULL,
                    snapshot_at             TEXT NOT NULL,
                    active_risks_json       TEXT,
                    opportunity_topics_json TEXT,
                    top_topics_json         TEXT,
                    competitive_matrix_json TEXT,
                    narrative_summary       TEXT,
                    payload_json            TEXT,
                    UNIQUE(snapshot_month, scope_type, scope_key)
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_intel_monthly_snapshots_month ON intel_monthly_snapshots(snapshot_month)")
                c.execute("""CREATE TABLE IF NOT EXISTS collector_cache (
                    cache_key    TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    expires_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                )""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_collector_cache_expires ON collector_cache(expires_at)")
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()

    def _sqlite_schema(self) -> str:
        return """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS sources (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL UNIQUE,
                type         TEXT    NOT NULL,
                is_active    INTEGER NOT NULL DEFAULT 1,
                weight       REAL    NOT NULL DEFAULT 1.0,
                fetch_limit  INTEGER NOT NULL DEFAULT 10,
                created_at   TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS keywords (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword    TEXT    NOT NULL UNIQUE,
                is_active  INTEGER NOT NULL DEFAULT 1,
                created_at TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS monitoring_runs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword        TEXT    NOT NULL,
                started_at     TEXT    NOT NULL,
                ended_at       TEXT,
                articles_found INTEGER DEFAULT 0,
                articles_new   INTEGER DEFAULT 0,
                fresh_mode     INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS monitor_batches (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_key  TEXT    NOT NULL,
                keywords   TEXT    NOT NULL,
                fresh_mode INTEGER DEFAULT 0,
                started_at TEXT    NOT NULL,
                ended_at   TEXT,
                status     TEXT    DEFAULT 'running'
            );
            CREATE INDEX IF NOT EXISTS idx_monitor_batches_open ON monitor_batches(batch_key, ended_at);

            CREATE TABLE IF NOT EXISTS threads (
                id           TEXT PRIMARY KEY,
                source_id    INTEGER REFERENCES sources(id),
                channel      TEXT    NOT NULL,
                title        TEXT    NOT NULL,
                url          TEXT    NOT NULL,
                author       TEXT,
                board        TEXT,
                platform_id  TEXT UNIQUE,
                keyword      TEXT,
                first_seen_at TEXT,
                published_at TEXT,
                fetched_at   TEXT    DEFAULT (datetime('now')),
                push_count    INTEGER,
                boo_count     INTEGER,
                neutral_count INTEGER,
                comment_count INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_threads_channel   ON threads(channel);
            CREATE INDEX IF NOT EXISTS idx_threads_keyword   ON threads(keyword);
            CREATE INDEX IF NOT EXISTS idx_threads_published ON threads(published_at);

            CREATE TABLE IF NOT EXISTS thread_items (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT    NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                item_type TEXT    NOT NULL,
                author    TEXT,
                platform_item_id TEXT UNIQUE,
                content   TEXT    NOT NULL,
                like_count INTEGER,
                published_at TEXT,
                sequence  INTEGER DEFAULT 0,
                created_at TEXT   DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_items_thread ON thread_items(thread_id);
            CREATE INDEX IF NOT EXISTS idx_items_type   ON thread_items(item_type);

            CREATE TABLE IF NOT EXISTS analyses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id       TEXT    NOT NULL REFERENCES threads(id),
                run_id          INTEGER REFERENCES monitoring_runs(id),
                analyzed_content TEXT,
                sentiment       TEXT,
                score           REAL,
                theme           TEXT,
                reason          TEXT,
                voice_source    TEXT,
                analyzed_with   TEXT,
                model_used      TEXT,
                source_weight   REAL    NOT NULL DEFAULT 1.0,
                analyzed_at     TEXT    DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_analyses_run    ON analyses(run_id);
            CREATE INDEX IF NOT EXISTS idx_analyses_thread ON analyses(thread_id);

            CREATE TABLE IF NOT EXISTS item_analyses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_item_id  INTEGER NOT NULL REFERENCES thread_items(id),
                run_id          INTEGER REFERENCES monitoring_runs(id),
                analyzed_content TEXT,
                sentiment       TEXT,
                score           REAL,
                theme           TEXT,
                reason          TEXT,
                voice_source    TEXT,
                analyzed_with   TEXT,
                model_used      TEXT,
                source_weight   REAL    NOT NULL DEFAULT 1.0,
                analyzed_at     TEXT    DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_item_analyses_run ON item_analyses(run_id);
            CREATE INDEX IF NOT EXISTS idx_item_analyses_item ON item_analyses(thread_item_id);

            CREATE TABLE IF NOT EXISTS pr_reports (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     INTEGER REFERENCES monitoring_runs(id),
                keyword    TEXT,
                track      TEXT,
                dashboard_summary TEXT,
                report     TEXT,
                created_at TEXT   DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS daily_snapshots (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date     TEXT    NOT NULL,
                keyword           TEXT    NOT NULL,
                snapshot_at       TEXT    NOT NULL,
                risk_score        INTEGER DEFAULT 0,
                article_count     INTEGER DEFAULT 0,
                pos_count         INTEGER DEFAULT 0,
                neu_count         INTEGER DEFAULT 0,
                neg_count         INTEGER DEFAULT 0,
                high_risk_count   INTEGER DEFAULT 0,
                avg_score         REAL    DEFAULT 0,
                channel_breakdown TEXT,
                top_themes        TEXT,
                dashboard_summary TEXT,
                payload_json      TEXT,
                UNIQUE(snapshot_date, keyword)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_snapshots_date ON daily_snapshots(snapshot_date);

            CREATE TABLE IF NOT EXISTS intel_event_cases (
                id                 TEXT PRIMARY KEY,
                keyword            TEXT NOT NULL,
                canonical_theme    TEXT NOT NULL,
                label              TEXT NOT NULL,
                status             TEXT NOT NULL DEFAULT 'active',
                severity           INTEGER DEFAULT 0,
                first_seen_at      TEXT NOT NULL,
                last_seen_at       TEXT NOT NULL,
                evidence_count     INTEGER DEFAULT 0,
                source_mix_json    TEXT,
                sentiment_mix_json TEXT,
                metadata_json      TEXT,
                created_at         TEXT DEFAULT (datetime('now')),
                updated_at         TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_intel_event_cases_keyword ON intel_event_cases(keyword);
            CREATE INDEX IF NOT EXISTS idx_intel_event_cases_last_seen ON intel_event_cases(last_seen_at);

            CREATE TABLE IF NOT EXISTS intel_event_case_threads (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                event_case_id      TEXT NOT NULL REFERENCES intel_event_cases(id) ON DELETE CASCADE,
                thread_id          TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                latest_analysis_id INTEGER REFERENCES analyses(id),
                first_bound_at     TEXT NOT NULL,
                last_bound_at      TEXT NOT NULL,
                UNIQUE(event_case_id, thread_id)
            );
            CREATE INDEX IF NOT EXISTS idx_intel_event_case_threads_case ON intel_event_case_threads(event_case_id);

            CREATE TABLE IF NOT EXISTS intel_topics (
                id                 TEXT PRIMARY KEY,
                scope_key          TEXT NOT NULL,
                canonical_theme    TEXT NOT NULL,
                label              TEXT NOT NULL,
                first_seen_at      TEXT NOT NULL,
                last_seen_at       TEXT NOT NULL,
                event_count        INTEGER DEFAULT 0,
                signal_count       INTEGER DEFAULT 0,
                sentiment_mix_json TEXT,
                source_mix_json    TEXT,
                metadata_json      TEXT,
                created_at         TEXT DEFAULT (datetime('now')),
                updated_at         TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_intel_topics_scope ON intel_topics(scope_key);
            CREATE INDEX IF NOT EXISTS idx_intel_topics_last_seen ON intel_topics(last_seen_at);

            CREATE TABLE IF NOT EXISTS intel_topic_events (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id       TEXT NOT NULL REFERENCES intel_topics(id) ON DELETE CASCADE,
                event_case_id  TEXT NOT NULL REFERENCES intel_event_cases(id) ON DELETE CASCADE,
                first_bound_at TEXT NOT NULL,
                last_bound_at  TEXT NOT NULL,
                UNIQUE(topic_id, event_case_id)
            );
            CREATE INDEX IF NOT EXISTS idx_intel_topic_events_topic ON intel_topic_events(topic_id);

            CREATE TABLE IF NOT EXISTS intel_monthly_snapshots (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_month          TEXT NOT NULL,
                scope_type              TEXT NOT NULL,
                scope_key               TEXT NOT NULL,
                snapshot_at             TEXT NOT NULL,
                active_risks_json       TEXT,
                opportunity_topics_json TEXT,
                top_topics_json         TEXT,
                competitive_matrix_json TEXT,
                narrative_summary       TEXT,
                payload_json            TEXT,
                UNIQUE(snapshot_month, scope_type, scope_key)
            );
            CREATE INDEX IF NOT EXISTS idx_intel_monthly_snapshots_month ON intel_monthly_snapshots(snapshot_month);

            CREATE TABLE IF NOT EXISTS collector_cache (
                cache_key    TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_collector_cache_expires ON collector_cache(expires_at);
        """

    def _postgres_schema_statements(self) -> List[str]:
        return [
            """CREATE TABLE IF NOT EXISTS sources (
                id           SERIAL PRIMARY KEY,
                name         TEXT    NOT NULL UNIQUE,
                type         TEXT    NOT NULL,
                is_active    INTEGER NOT NULL DEFAULT 1,
                weight       REAL    NOT NULL DEFAULT 1.0,
                fetch_limit  INTEGER NOT NULL DEFAULT 10,
                created_at   TIMESTAMPTZ DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS keywords (
                id         SERIAL PRIMARY KEY,
                keyword    TEXT    NOT NULL UNIQUE,
                is_active  INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS monitoring_runs (
                id             SERIAL PRIMARY KEY,
                keyword        TEXT    NOT NULL,
                started_at     TEXT    NOT NULL,
                ended_at       TEXT,
                articles_found INTEGER DEFAULT 0,
                articles_new   INTEGER DEFAULT 0,
                fresh_mode     INTEGER DEFAULT 0
            )""",
            """CREATE TABLE IF NOT EXISTS monitor_batches (
                id         SERIAL PRIMARY KEY,
                batch_key  TEXT    NOT NULL,
                keywords   TEXT    NOT NULL,
                fresh_mode INTEGER DEFAULT 0,
                started_at TEXT    NOT NULL,
                ended_at   TEXT,
                status     TEXT    DEFAULT 'running'
            )""",
            "CREATE INDEX IF NOT EXISTS idx_monitor_batches_open ON monitor_batches(batch_key, ended_at)",
            """CREATE TABLE IF NOT EXISTS threads (
                id           TEXT PRIMARY KEY,
                source_id    INTEGER REFERENCES sources(id),
                channel      TEXT    NOT NULL,
                title        TEXT    NOT NULL,
                url          TEXT    NOT NULL,
                author       TEXT,
                board        TEXT,
                platform_id  TEXT UNIQUE,
                keyword      TEXT,
                first_seen_at TEXT,
                published_at TEXT,
                fetched_at   TIMESTAMPTZ DEFAULT NOW(),
                push_count    INTEGER,
                boo_count     INTEGER,
                neutral_count INTEGER,
                comment_count INTEGER
            )""",
            "CREATE INDEX IF NOT EXISTS idx_threads_channel   ON threads(channel)",
            "CREATE INDEX IF NOT EXISTS idx_threads_keyword   ON threads(keyword)",
            "CREATE INDEX IF NOT EXISTS idx_threads_published ON threads(published_at)",
            """CREATE TABLE IF NOT EXISTS thread_items (
                id        SERIAL PRIMARY KEY,
                thread_id TEXT    NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                item_type TEXT    NOT NULL,
                author    TEXT,
                platform_item_id TEXT UNIQUE,
                content   TEXT    NOT NULL,
                like_count INTEGER,
                published_at TEXT,
                sequence  INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_items_thread ON thread_items(thread_id)",
            "CREATE INDEX IF NOT EXISTS idx_items_type   ON thread_items(item_type)",
            """CREATE TABLE IF NOT EXISTS analyses (
                id              SERIAL PRIMARY KEY,
                thread_id       TEXT    NOT NULL REFERENCES threads(id),
                run_id          INTEGER REFERENCES monitoring_runs(id),
                analyzed_content TEXT,
                sentiment       TEXT,
                score           REAL,
                theme           TEXT,
                reason          TEXT,
                voice_source    TEXT,
                analyzed_with   TEXT,
                model_used      TEXT,
                source_weight   REAL    NOT NULL DEFAULT 1.0,
                analyzed_at     TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_analyses_run    ON analyses(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_analyses_thread ON analyses(thread_id)",
            """CREATE TABLE IF NOT EXISTS item_analyses (
                id              SERIAL PRIMARY KEY,
                thread_item_id  INTEGER NOT NULL REFERENCES thread_items(id),
                run_id          INTEGER REFERENCES monitoring_runs(id),
                analyzed_content TEXT,
                sentiment       TEXT,
                score           REAL,
                theme           TEXT,
                reason          TEXT,
                voice_source    TEXT,
                analyzed_with   TEXT,
                model_used      TEXT,
                source_weight   REAL    NOT NULL DEFAULT 1.0,
                analyzed_at     TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_item_analyses_run ON item_analyses(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_item_analyses_item ON item_analyses(thread_item_id)",
            """CREATE TABLE IF NOT EXISTS pr_reports (
                id         SERIAL PRIMARY KEY,
                run_id     INTEGER REFERENCES monitoring_runs(id),
                keyword    TEXT,
                track      TEXT,
                dashboard_summary TEXT,
                report     TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS daily_snapshots (
                id                SERIAL PRIMARY KEY,
                snapshot_date     TEXT    NOT NULL,
                keyword           TEXT    NOT NULL,
                snapshot_at       TEXT    NOT NULL,
                risk_score        INTEGER DEFAULT 0,
                article_count     INTEGER DEFAULT 0,
                pos_count         INTEGER DEFAULT 0,
                neu_count         INTEGER DEFAULT 0,
                neg_count         INTEGER DEFAULT 0,
                high_risk_count   INTEGER DEFAULT 0,
                avg_score         REAL    DEFAULT 0,
                channel_breakdown TEXT,
                top_themes        TEXT,
                dashboard_summary TEXT,
                payload_json      TEXT,
                UNIQUE(snapshot_date, keyword)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_daily_snapshots_date ON daily_snapshots(snapshot_date)",
            """CREATE TABLE IF NOT EXISTS intel_event_cases (
                id                 TEXT PRIMARY KEY,
                keyword            TEXT NOT NULL,
                canonical_theme    TEXT NOT NULL,
                label              TEXT NOT NULL,
                status             TEXT NOT NULL DEFAULT 'active',
                severity           INTEGER DEFAULT 0,
                first_seen_at      TEXT NOT NULL,
                last_seen_at       TEXT NOT NULL,
                evidence_count     INTEGER DEFAULT 0,
                source_mix_json    TEXT,
                sentiment_mix_json TEXT,
                metadata_json      TEXT,
                created_at         TIMESTAMPTZ DEFAULT NOW(),
                updated_at         TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_intel_event_cases_keyword ON intel_event_cases(keyword)",
            "CREATE INDEX IF NOT EXISTS idx_intel_event_cases_last_seen ON intel_event_cases(last_seen_at)",
            """CREATE TABLE IF NOT EXISTS intel_event_case_threads (
                id                 SERIAL PRIMARY KEY,
                event_case_id      TEXT NOT NULL REFERENCES intel_event_cases(id) ON DELETE CASCADE,
                thread_id          TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                latest_analysis_id INTEGER REFERENCES analyses(id),
                first_bound_at     TEXT NOT NULL,
                last_bound_at      TEXT NOT NULL,
                UNIQUE(event_case_id, thread_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_intel_event_case_threads_case ON intel_event_case_threads(event_case_id)",
            """CREATE TABLE IF NOT EXISTS intel_topics (
                id                 TEXT PRIMARY KEY,
                scope_key          TEXT NOT NULL,
                canonical_theme    TEXT NOT NULL,
                label              TEXT NOT NULL,
                first_seen_at      TEXT NOT NULL,
                last_seen_at       TEXT NOT NULL,
                event_count        INTEGER DEFAULT 0,
                signal_count       INTEGER DEFAULT 0,
                sentiment_mix_json TEXT,
                source_mix_json    TEXT,
                metadata_json      TEXT,
                created_at         TIMESTAMPTZ DEFAULT NOW(),
                updated_at         TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_intel_topics_scope ON intel_topics(scope_key)",
            "CREATE INDEX IF NOT EXISTS idx_intel_topics_last_seen ON intel_topics(last_seen_at)",
            """CREATE TABLE IF NOT EXISTS intel_topic_events (
                id             SERIAL PRIMARY KEY,
                topic_id       TEXT NOT NULL REFERENCES intel_topics(id) ON DELETE CASCADE,
                event_case_id  TEXT NOT NULL REFERENCES intel_event_cases(id) ON DELETE CASCADE,
                first_bound_at TEXT NOT NULL,
                last_bound_at  TEXT NOT NULL,
                UNIQUE(topic_id, event_case_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_intel_topic_events_topic ON intel_topic_events(topic_id)",
            """CREATE TABLE IF NOT EXISTS intel_monthly_snapshots (
                id                      SERIAL PRIMARY KEY,
                snapshot_month          TEXT NOT NULL,
                scope_type              TEXT NOT NULL,
                scope_key               TEXT NOT NULL,
                snapshot_at             TEXT NOT NULL,
                active_risks_json       TEXT,
                opportunity_topics_json TEXT,
                top_topics_json         TEXT,
                competitive_matrix_json TEXT,
                narrative_summary       TEXT,
                payload_json            TEXT,
                UNIQUE(snapshot_month, scope_type, scope_key)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_intel_monthly_snapshots_month ON intel_monthly_snapshots(snapshot_month)",
            """CREATE TABLE IF NOT EXISTS collector_cache (
                cache_key    TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_collector_cache_expires ON collector_cache(expires_at)",
        ]

    # ── 共用 SQL helper ──────────────────────────────────────
    def _ph(self) -> str:
        return self._adapter.placeholder

    def _execute(self, conn, sql: str, params: tuple = ()):
        """執行 SQL，自動補 commit（SQLite context manager 不 commit on execute）。"""
        c = conn.cursor()
        c.execute(sql, params)
        return c

    # ── 種子資料 ─────────────────────────────────────────────
    def _seed_sources(self):
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            for name, stype, is_active, weight, fetch_limit in DEFAULT_SOURCES:
                if self._adapter.is_postgres:
                    c.execute(
                        "INSERT INTO sources (name, type, is_active, weight, fetch_limit) "
                        f"VALUES ({ph},{ph},{ph},{ph},{ph}) ON CONFLICT (name) DO NOTHING",
                        (name, stype, is_active, weight, fetch_limit)
                    )
                else:
                    c.execute(
                        "INSERT OR IGNORE INTO sources (name, type, is_active, weight, fetch_limit) "
                        f"VALUES ({ph},{ph},{ph},{ph},{ph})",
                        (name, stype, is_active, weight, fetch_limit)
                    )
            conn.commit()
        finally:
            conn.close()

    # ── 關鍵字管理 ───────────────────────────────────────────
    def ensure_keyword(self, keyword: str) -> int:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            if self._adapter.is_postgres:
                c.execute(
                    f"INSERT INTO keywords (keyword) VALUES ({ph}) ON CONFLICT (keyword) DO NOTHING",
                    (keyword,)
                )
            else:
                c.execute(
                    f"INSERT OR IGNORE INTO keywords (keyword) VALUES ({ph})",
                    (keyword,)
                )
            conn.commit()
            c.execute(f"SELECT id FROM keywords WHERE keyword = {ph}", (keyword,))
            row = c.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def get_active_keywords(self) -> List[str]:
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute("SELECT keyword FROM keywords WHERE is_active = 1")
            return [r[0] for r in c.fetchall()]
        finally:
            conn.close()

    # ── 渠道查詢 ─────────────────────────────────────────────
    def get_source_id(self, name: str) -> Optional[int]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(f"SELECT id FROM sources WHERE name = {ph}", (name,))
            row = c.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def get_active_sources(self) -> List[Dict]:
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM sources WHERE is_active = 1")
            return self._adapter.fetchall_dict(c)
        finally:
            conn.close()

    def _today_str(self) -> str:
        return self._local_now().strftime("%Y-%m-%d")

    def _local_now(self) -> datetime:
        return datetime.now(ZoneInfo(APP_TIMEZONE))

    def _now_iso(self) -> str:
        return self._local_now().isoformat(timespec="seconds")

    def _parse_local_datetime(self, raw: Any) -> Optional[datetime]:
        if raw in (None, ""):
            return None
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None
        if dt.tzinfo is None:
            fallback_tz = timezone.utc if self._adapter.is_postgres else ZoneInfo(APP_TIMEZONE)
            dt = dt.replace(tzinfo=fallback_tz)
        return dt.astimezone(ZoneInfo(APP_TIMEZONE))

    def _serialize_local_timestamp(self, raw: Any) -> Optional[str]:
        dt = self._parse_local_datetime(raw)
        if not dt:
            return raw
        return dt.isoformat(timespec="seconds")

    def _google_news_fallback_url(self, title: str) -> str:
        query = urllib.parse.quote(title or "")
        return (
            "https://news.google.com/search"
            f"?q={query}&hl=zh-TW&gl=TW&ceid=TW%3Azh-Hant"
        )

    def _public_thread_url(self, channel: str, url: Any, title: str) -> str:
        raw_url = str(url or "").strip()
        if raw_url.startswith(("http://", "https://")):
            return raw_url
        if channel == "google_news" and raw_url.startswith("gnews_title:"):
            return self._google_news_fallback_url(title)
        return "#"

    def _day_start_str(self, day: str) -> str:
        return f"{day}T00:00:00"

    def get_collector_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        ph = self._ph()
        now_dt = self._local_now()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT payload_json, expires_at FROM collector_cache WHERE cache_key = {ph}",
                (cache_key,),
            )
            row = c.fetchone()
            if not row:
                return None
            payload_json = row[0] if self._adapter.is_postgres else row["payload_json"]
            expires_at = row[1] if self._adapter.is_postgres else row["expires_at"]
            expires_dt = self._parse_local_datetime(expires_at)
            if not expires_dt or expires_dt <= now_dt:
                c.execute(f"DELETE FROM collector_cache WHERE cache_key = {ph}", (cache_key,))
                conn.commit()
                return None
            return json.loads(payload_json)
        finally:
            conn.close()

    def set_collector_cache(self, cache_key: str, payload: Dict[str, Any], ttl_minutes: int) -> None:
        ph = self._ph()
        now = self._local_now().isoformat(timespec="seconds")
        expires_at = (self._local_now() + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds")
        payload_json = self._json_dumps(payload)
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            if self._adapter.is_postgres:
                c.execute(
                    f"""INSERT INTO collector_cache (cache_key, payload_json, expires_at, updated_at)
                        VALUES ({ph},{ph},{ph},{ph})
                        ON CONFLICT (cache_key) DO UPDATE
                        SET payload_json = EXCLUDED.payload_json,
                            expires_at = EXCLUDED.expires_at,
                            updated_at = EXCLUDED.updated_at""",
                    (cache_key, payload_json, expires_at, now),
                )
            else:
                c.execute(
                    f"""INSERT INTO collector_cache (cache_key, payload_json, expires_at, updated_at)
                        VALUES ({ph},{ph},{ph},{ph})
                        ON CONFLICT(cache_key) DO UPDATE SET
                            payload_json = excluded.payload_json,
                            expires_at = excluded.expires_at,
                            updated_at = excluded.updated_at""",
                    (cache_key, payload_json, expires_at, now),
                )
            conn.commit()
        finally:
            conn.close()

    def _days_since(self, start_at: Optional[str], end_date: str) -> int:
        if not start_at:
            return 0
        try:
            start_dt = self._parse_local_datetime(start_at)
            if not start_dt:
                return 0
            start_date = start_dt.date()
            end_day = datetime.fromisoformat(end_date).date()
        except Exception:
            return 0
        return max((end_day - start_date).days, 0)

    def _json_dumps(self, value: Any) -> str:
        def _default(obj: Any):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            return str(obj)

        return json.dumps(value, ensure_ascii=False, default=_default)

    def _upsert_sql(self, table: str, columns: List[str], conflict_cols: List[str], update_cols: List[str]) -> str:
        ph = self._ph()
        cols_str = ", ".join(columns)
        vals_str = ", ".join([ph] * len(columns))
        if self._adapter.is_postgres:
            updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in update_cols)
            conflicts = ", ".join(conflict_cols)
            return (
                f"INSERT INTO {table} ({cols_str}) VALUES ({vals_str}) "
                f"ON CONFLICT ({conflicts}) DO UPDATE SET {updates}"
            )
        return f"INSERT OR REPLACE INTO {table} ({cols_str}) VALUES ({vals_str})"

    # ── Monitor Batch ────────────────────────────────────────
    def create_monitor_batch(self, keywords: List[str], fresh_mode: bool = False, batch_key: str = "default") -> int:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            now = self._now_iso()
            c.execute(
                f"INSERT INTO monitor_batches (batch_key, keywords, fresh_mode, started_at, status) VALUES ({ph},{ph},{ph},{ph},{ph})",
                (batch_key, self._json_dumps(keywords), int(fresh_mode), now, "running"),
            )
            batch_id = self._adapter.last_insert_id(c)
            conn.commit()
            return batch_id
        finally:
            conn.close()

    def close_monitor_batch(self, batch_id: int, status: str = "completed") -> None:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"UPDATE monitor_batches SET ended_at={ph}, status={ph} WHERE id={ph}",
                (self._now_iso(), status, batch_id),
            )
            conn.commit()
        finally:
            conn.close()

    def close_stale_monitor_batches(self, batch_key: str = "default", older_than_minutes: int = 180) -> int:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT id, started_at FROM monitor_batches WHERE batch_key = {ph} AND ended_at IS NULL",
                (batch_key,),
            )
            rows = self._adapter.fetchall_dict(c)
            if not rows:
                return 0

            now = self._local_now()
            closed = 0
            for row in rows:
                started_at = self._parse_local_datetime(row["started_at"]) or (now - timedelta(days=1))
                if (now - started_at) < timedelta(minutes=older_than_minutes):
                    continue
                c.execute(
                    f"UPDATE monitor_batches SET ended_at={ph}, status={ph} WHERE id={ph}",
                    (now.isoformat(timespec="seconds"), "stale", row["id"]),
                )
                closed += 1
            conn.commit()
            return closed
        finally:
            conn.close()

    def get_active_monitor_batch(self, batch_key: str = "default", max_age_minutes: int = 180) -> Optional[Dict]:
        self.close_stale_monitor_batches(batch_key=batch_key, older_than_minutes=max_age_minutes)
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT * FROM monitor_batches WHERE batch_key = {ph} AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
                (batch_key,),
            )
            row = c.fetchone()
            data = self._adapter.fetchone_dict(c, row)
            if not data:
                return None
            try:
                data["keywords"] = json.loads(data.get("keywords") or "[]")
            except Exception:
                data["keywords"] = []
            return data
        finally:
            conn.close()

    # ── Monitoring Run ────────────────────────────────────────
    def create_run(self, keyword: str, fresh_mode: bool = False) -> int:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            now = self._now_iso()
            c.execute(
                f"INSERT INTO monitoring_runs (keyword, started_at, fresh_mode) VALUES ({ph},{ph},{ph})",
                (keyword, now, int(fresh_mode))
            )
            if self._adapter.is_postgres:
                c.execute("SELECT lastval()")
                run_id = c.fetchone()[0]
            else:
                run_id = c.lastrowid
            conn.commit()
            return run_id
        finally:
            conn.close()

    def close_run(self, run_id: int, articles_found: int, articles_new: int):
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"UPDATE monitoring_runs SET ended_at={ph}, articles_found={ph}, articles_new={ph} WHERE id={ph}",
                (self._now_iso(), articles_found, articles_new, run_id)
            )
            conn.commit()
        finally:
            conn.close()

    def close_open_runs(self, keywords: Optional[List[str]] = None, older_than_minutes: Optional[int] = None) -> int:
        """關閉 orphan / stale open runs，回傳關閉筆數。"""
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute("SELECT id, keyword, started_at FROM monitoring_runs WHERE ended_at IS NULL")
            rows = self._adapter.fetchall_dict(c)
            if not rows:
                return 0

            now = self._local_now()
            to_close = []
            keyword_set = set(keywords or [])
            for row in rows:
                if keyword_set and row.get("keyword") not in keyword_set:
                    continue
                if older_than_minutes is not None:
                    started_at = row.get("started_at")
                    started_dt = self._parse_local_datetime(started_at) or (now - timedelta(days=1))
                    if (now - started_dt) < timedelta(minutes=older_than_minutes):
                        continue
                to_close.append(int(row["id"]))

            for run_id in to_close:
                c.execute(
                    f"UPDATE monitoring_runs SET ended_at={ph}, articles_found=COALESCE(articles_found, 0), articles_new=COALESCE(articles_new, 0) WHERE id={ph}",
                    (now.isoformat(timespec="seconds"), run_id)
                )
            conn.commit()
            return len(to_close)
        finally:
            conn.close()

    def get_recent_runs(self, limit: int = 10, keyword: str = None) -> List[Dict]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            if keyword:
                c.execute(
                    f"SELECT * FROM monitoring_runs WHERE keyword = {ph} "
                    f"ORDER BY started_at DESC LIMIT {ph}",
                    (keyword, limit)
                )
            else:
                c.execute(
                    "SELECT * FROM monitoring_runs ORDER BY started_at DESC LIMIT " + ph,
                    (limit,)
                )
            return self._adapter.fetchall_dict(c)
        finally:
            conn.close()

    def get_run(self, run_id: int) -> Optional[Dict]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(f"SELECT * FROM monitoring_runs WHERE id = {ph}", (run_id,))
            row = c.fetchone()
            return self._adapter.fetchone_dict(c, row)
        finally:
            conn.close()

    def get_run_analyses(self, run_id: int) -> List[Dict]:
        """回傳指定 run 的所有情感分析結果（含文章 metadata）。"""
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"""SELECT a.sentiment, a.score, a.theme, a.reason,
                       a.voice_source, a.analyzed_with, a.model_used,
                       a.analyzed_at, a.source_weight,
                       t.title, t.url, t.channel, t.board,
                       t.push_count, t.boo_count, t.neutral_count, t.comment_count
                FROM analyses a
                JOIN threads t ON a.thread_id = t.id
                WHERE a.run_id = {ph}
                ORDER BY a.analyzed_at""",
                (run_id,)
            )
            return [self._normalize_analysis_row(row) for row in self._adapter.fetchall_dict(c)]
        finally:
            conn.close()

    def get_run_item_analyses(self, run_id: int) -> List[Dict]:
        """回傳指定 run 的留言級分析結果。"""
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"""SELECT ia.sentiment, ia.score, ia.theme, ia.reason,
                       ia.voice_source, ia.analyzed_with, ia.model_used,
                       ia.analyzed_at, ia.source_weight,
                       ti.content, ti.author, ti.platform_item_id,
                       t.title, t.url, t.channel, t.board
                FROM item_analyses ia
                JOIN thread_items ti ON ia.thread_item_id = ti.id
                JOIN threads t ON ti.thread_id = t.id
                WHERE ia.run_id = {ph}
                ORDER BY ia.analyzed_at""",
                (run_id,)
            )
            return [self._normalize_analysis_row(row) for row in self._adapter.fetchall_dict(c)]
        finally:
            conn.close()

    def get_run_pr_report(self, run_id: int) -> Optional[Dict]:
        """回傳指定 run 的 PR 策略報告（若不存在回傳 None）。"""
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT * FROM pr_reports WHERE run_id = {ph} ORDER BY id DESC LIMIT 1",
                (run_id,)
            )
            row = c.fetchone()
            return self._adapter.fetchone_dict(c, row)
        finally:
            conn.close()

    def _latest_completed_run_at(self, keywords: Optional[List[str]] = None, before_started_at: Optional[str] = None) -> Optional[str]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            sql = f"SELECT ended_at, started_at FROM monitoring_runs WHERE ended_at IS NOT NULL"
            params: List[Any] = []
            if before_started_at:
                sql += f" AND ended_at < {ph}"
                params.append(before_started_at)
            if keywords:
                placeholders = ",".join([ph] * len(keywords))
                sql += f" AND keyword IN ({placeholders})"
                params.extend(keywords)
            sql += " ORDER BY ended_at DESC, started_at DESC LIMIT 1"
            c.execute(sql, tuple(params))
            row = c.fetchone()
            if not row:
                return None
            data = self._adapter.fetchone_dict(c, row)
            return self._serialize_local_timestamp(data.get("ended_at") or data.get("started_at"))
        finally:
            conn.close()

    def _completed_run_ids_for_date(
        self,
        snapshot_date: str,
        keywords: Optional[List[str]] = None,
        before_started_at: Optional[str] = None,
    ) -> List[int]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            sql = (
                "SELECT id, keyword, ended_at, started_at "
                f"FROM monitoring_runs WHERE ended_at IS NOT NULL AND substr(COALESCE(ended_at, started_at, ''), 1, 10) = {ph}"
            )
            params: List[Any] = [snapshot_date]
            if before_started_at:
                sql += f" AND ended_at < {ph}"
                params.append(before_started_at)
            if keywords:
                placeholders = ",".join([ph] * len(keywords))
                sql += f" AND keyword IN ({placeholders})"
                params.extend(keywords)
            sql += " ORDER BY ended_at DESC, started_at DESC, id DESC"
            c.execute(sql, tuple(params))
            rows = self._adapter.fetchall_dict(c)
            latest_by_keyword: Dict[str, int] = {}
            for row in rows:
                keyword = row.get("keyword")
                if keyword and keyword not in latest_by_keyword:
                    latest_by_keyword[keyword] = row["id"]
            return list(latest_by_keyword.values())
        finally:
            conn.close()

    def _recent_snapshot_overview(self, snapshot_date: str, keywords: Optional[List[str]] = None) -> Dict[str, Any]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            sql = (
                "SELECT snapshot_date, keyword, snapshot_at, risk_score, article_count, pos_count, neu_count, neg_count, avg_score "
                f"FROM daily_snapshots WHERE snapshot_date = {ph}"
            )
            params: List[Any] = [snapshot_date]
            if keywords:
                placeholders = ",".join([ph] * len(keywords))
                sql += f" AND keyword IN ({placeholders})"
                params.extend(keywords)
            sql += " ORDER BY keyword ASC"
            c.execute(sql, tuple(params))
            rows = self._adapter.fetchall_dict(c)
        finally:
            conn.close()

        return {
            "snapshot_date": snapshot_date,
            "rows": rows,
            "total_articles": sum(int(row.get("article_count") or 0) for row in rows),
            "max_risk_score": max([int(row.get("risk_score") or 0) for row in rows], default=0),
        }

    def _active_threads_for_date(self, snapshot_date: str, keywords: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            params: List[Any] = [snapshot_date, snapshot_date, snapshot_date, snapshot_date]
            keyword_sql = ""
            if keywords:
                placeholders = ",".join([ph] * len(keywords))
                keyword_sql = f" AND t.keyword IN ({placeholders})"
                params.extend(keywords)
            c.execute(
                f"""
                SELECT
                    t.id,
                    t.keyword,
                    t.channel,
                    t.title,
                    t.url,
                    t.board,
                    t.published_at,
                    t.first_seen_at,
                    MAX(CASE WHEN substr(COALESCE(ti.published_at, ''), 1, 10) <= {ph} THEN ti.published_at END) AS latest_item_published_at
                FROM threads t
                LEFT JOIN thread_items ti ON ti.thread_id = t.id
                WHERE (
                    (
                        (
                            substr(COALESCE(t.published_at, ''), 1, 10) = {ph}
                        )
                        OR (
                            t.published_at IS NULL
                            AND substr(COALESCE(t.first_seen_at, t.fetched_at, ''), 1, 10) = {ph}
                        )
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM thread_items ti2
                        WHERE ti2.thread_id = t.id
                          AND ti2.item_type <> 'main'
                          AND substr(COALESCE(ti2.published_at, ''), 1, 10) = {ph}
                    )
                )
                {keyword_sql}
                GROUP BY t.id, t.keyword, t.channel, t.title, t.url, t.board, t.published_at, t.first_seen_at
                """,
                tuple(params),
            )
            rows = self._adapter.fetchall_dict(c)
        finally:
            conn.close()

        active_threads: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            data = dict(row)
            first_seen_at = data.get("first_seen_at") or data.get("published_at")
            recent_activity_at = max(
                [value for value in [data.get("latest_item_published_at"), first_seen_at, data.get("published_at")] if value],
                default="",
            )
            data["first_seen_at"] = first_seen_at
            data["recent_activity_at"] = recent_activity_at
            data["ongoing_days"] = self._days_since(first_seen_at, snapshot_date) if first_seen_at and str(first_seen_at)[:10] < snapshot_date else 0
            active_threads[data["id"]] = data
        return active_threads

    def get_dashboard_day_summary(self, snapshot_date: str = None, keywords: Optional[List[str]] = None) -> Dict[str, Any]:
        snapshot_date = snapshot_date or self._today_str()
        active_batch = self.get_active_monitor_batch()
        cutoff_started_at = active_batch.get("started_at") if active_batch else None
        latest_completed_at = self._latest_completed_run_at(keywords=keywords, before_started_at=cutoff_started_at)
        day_run_ids = self._completed_run_ids_for_date(snapshot_date, keywords=keywords, before_started_at=cutoff_started_at)
        active_threads = self._active_threads_for_date(snapshot_date, keywords=keywords)
        yesterday = (datetime.fromisoformat(snapshot_date) - timedelta(days=1)).strftime("%Y-%m-%d")
        empty_snapshot = self._recent_snapshot_overview(yesterday, keywords=keywords)

        if not day_run_ids and not active_threads:
            return {
                "snapshot_date": snapshot_date,
                "updated_at": latest_completed_at,
                "active_batch": active_batch,
                "brand_map": {},
                "channel_counts": {"google_news": 0, "ptt": 0, "dcard": 0, "youtube": 0, "threads": 0},
                "all_alerts": [],
                "total_articles": 0,
                "latest_run_at": latest_completed_at,
                "empty_snapshot": empty_snapshot,
            }

        ph = self._ph()
        run_placeholders = ",".join([ph] * len(day_run_ids)) if day_run_ids else None
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            analysis_rows: List[Dict[str, Any]] = []
            item_analysis_rows: List[Dict[str, Any]] = []
            pr_rows: List[Dict[str, Any]] = []

            if day_run_ids:
                c.execute(
                    f"""
                    SELECT a.thread_id, a.run_id, a.sentiment, a.score, a.theme, a.reason,
                           a.voice_source, a.analyzed_with, a.model_used, a.analyzed_at,
                           t.title, t.url, t.channel, t.board, t.published_at, t.first_seen_at,
                           t.push_count, t.boo_count, t.neutral_count, t.comment_count,
                           t.keyword
                    FROM analyses a
                    JOIN threads t ON a.thread_id = t.id
                    WHERE a.run_id IN ({run_placeholders})
                    """,
                    tuple(day_run_ids),
                )
                analysis_rows = [self._normalize_analysis_row(row) for row in self._adapter.fetchall_dict(c)]

                c.execute(
                    f"""
                    SELECT ia.thread_item_id, ia.run_id, ia.sentiment, ia.score, ia.theme, ia.reason,
                           ia.voice_source, ia.analyzed_with, ia.model_used, ia.analyzed_at,
                           ti.content, ti.author, ti.platform_item_id, ti.published_at,
                           t.channel, t.title, t.url, t.keyword, ti.thread_id
                    FROM item_analyses ia
                    JOIN thread_items ti ON ia.thread_item_id = ti.id
                    JOIN threads t ON ti.thread_id = t.id
                    WHERE ia.run_id IN ({run_placeholders})
                    """,
                    tuple(day_run_ids),
                )
                item_analysis_rows = [self._normalize_analysis_row(row) for row in self._adapter.fetchall_dict(c)]

                c.execute(
                    f"""
                    SELECT pr.*
                    FROM pr_reports pr
                    WHERE pr.run_id IN ({run_placeholders})
                    ORDER BY pr.run_id DESC, pr.created_at DESC
                    """,
                    tuple(day_run_ids),
                )
                pr_rows = self._adapter.fetchall_dict(c)
        finally:
            conn.close()

        analyses_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in analysis_rows:
            key = (row["keyword"], row["thread_id"])
            prev = analyses_by_key.get(key)
            if prev is None or (row.get("analyzed_at") or "") >= (prev.get("analyzed_at") or ""):
                analyses_by_key[key] = row

        items_by_key: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for row in item_analysis_rows:
            key = (row["keyword"], row["thread_item_id"])
            prev = items_by_key.get(key)
            if prev is None or (row.get("analyzed_at") or "") >= (prev.get("analyzed_at") or ""):
                items_by_key[key] = row

        pr_by_keyword: Dict[str, Dict[str, Any]] = {}
        for row in pr_rows:
            keyword = row.get("keyword")
            if keyword and keyword not in pr_by_keyword:
                pr_by_keyword[keyword] = row

        brand_map: Dict[str, Dict[str, Any]] = {}
        channel_counts = {"google_news": 0, "ptt": 0, "dcard": 0, "youtube": 0, "threads": 0}
        seen_global_alerts = set()
        all_alerts: List[Dict[str, Any]] = []

        for row in analyses_by_key.values():
            kw = row["keyword"]
            brand = brand_map.setdefault(
                kw,
                {
                    "keyword": kw,
                    "pos": 0,
                    "neu": 0,
                    "neg": 0,
                    "total": 0,
                    "scores": [],
                    "analyses": [],
                    "itemAnalyses": [],
                    "alerts": [],
                    "pr": None,
                    "dashboardSummary": None,
                    "lastRunId": row.get("run_id"),
                },
            )
            brand["total"] += 1
            sentiment = row.get("sentiment")
            if sentiment == "正面":
                brand["pos"] += 1
            elif sentiment == "負面":
                brand["neg"] += 1
            else:
                brand["neu"] += 1
            brand["scores"].append(row["score"])
            brand["analyses"].append(row)

            thread_id = row["thread_id"]
            channel = row.get("channel")
            if channel in channel_counts:
                channel_counts[channel] += 1

            if is_alert_eligible(row.get("sentiment", ""), row.get("score", 0)):
                # 補上 url 轉換後再交給 alert_engine 建構
                row_with_url = dict(row)
                row_with_url["url"] = self._public_thread_url(
                    row.get("channel") or "",
                    row.get("url"),
                    row.get("title") or "",
                )
                row_with_url["keyword"] = kw
                alert = build_alert_from_row(row_with_url, active_threads, snapshot_date)
                brand["alerts"].append(alert)
                if thread_id not in seen_global_alerts:
                    seen_global_alerts.add(thread_id)
                    all_alerts.append(alert)

        # items_by_thread：同時按 thread_id 分組，供後續留言警報判斷
        items_by_thread: Dict[str, List[Dict[str, Any]]] = {}

        for row in items_by_key.values():
            kw = row["keyword"]
            brand = brand_map.setdefault(
                kw,
                {
                    "keyword": kw,
                    "pos": 0,
                    "neu": 0,
                    "neg": 0,
                    "total": 0,
                    "scores": [],
                    "analyses": [],
                    "itemAnalyses": [],
                    "alerts": [],
                    "pr": None,
                    "dashboardSummary": None,
                    "lastRunId": row.get("run_id"),
                },
            )
            brand["itemAnalyses"].append(row)

            tid = row.get("thread_id")
            if tid:
                items_by_thread.setdefault(str(tid), []).append(row)

        # Scenario 2：留言聚合警報
        # 貼文層已觸發的 thread_id 跳過（seen_global_alerts 已記錄）
        for tid_str, item_rows in items_by_thread.items():
            if tid_str in seen_global_alerts:
                continue
            if not is_thread_alert_by_items(item_rows):
                continue

            kw = item_rows[0].get("keyword", "")
            brand = brand_map.get(kw)
            if not brand:
                continue

            alert = build_alert_from_items(kw, item_rows, active_threads, snapshot_date)
            # url 轉換（與 Scenario 1 一致）
            alert["url"] = self._public_thread_url(
                alert.get("channel") or "",
                alert.get("url"),
                alert.get("title") or "",
            )
            brand["alerts"].append(alert)
            seen_global_alerts.add(tid_str)
            all_alerts.append(alert)

        for kw, brand in brand_map.items():
            pr_row = pr_by_keyword.get(kw)
            if pr_row:
                brand["pr"] = pr_row.get("report")
                brand["dashboardSummary"] = pr_row.get("dashboard_summary")

        return {
            "snapshot_date": snapshot_date,
            "updated_at": latest_completed_at,
            "active_batch": active_batch,
            "brand_map": brand_map,
            "channel_counts": channel_counts,
            "all_alerts": sort_alerts(all_alerts),
            "total_articles": sum(brand.get("total", 0) for brand in brand_map.values()),
            "latest_run_at": latest_completed_at,
            "empty_snapshot": empty_snapshot,
        }

    def save_daily_snapshots(self, snapshot_date: str = None, keywords: Optional[List[str]] = None) -> int:
        snapshot_date = snapshot_date or self._today_str()
        summary = self.get_dashboard_day_summary(snapshot_date=snapshot_date, keywords=keywords)
        brand_map = summary.get("brand_map") or {}
        if not brand_map:
            return 0

        columns = [
            "snapshot_date", "keyword", "snapshot_at", "risk_score", "article_count",
            "pos_count", "neu_count", "neg_count", "high_risk_count", "avg_score",
            "channel_breakdown", "top_themes", "dashboard_summary", "payload_json",
        ]
        update_cols = columns[2:]
        sql = self._upsert_sql("daily_snapshots", columns, ["snapshot_date", "keyword"], update_cols)

        today = self._today_str()
        freeze_mode = snapshot_date < today

        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            written = 0
            for keyword, brand in brand_map.items():
                if freeze_mode:
                    c.execute(
                        f"SELECT 1 FROM daily_snapshots WHERE snapshot_date = {self._ph()} AND keyword = {self._ph()} LIMIT 1",
                        (snapshot_date, keyword),
                    )
                    if c.fetchone():
                        continue
                total = max(int(brand.get("total", 0)), 1)
                negative_count = int(brand.get("neg", 0))
                scores = list(brand.get("scores", []))
                channel_breakdown: Dict[str, int] = {}
                for item in brand.get("analyses", []):
                    channel = item.get("channel")
                    if channel:
                        channel_breakdown[channel] = channel_breakdown.get(channel, 0) + 1
                top_themes = []
                theme_counts: Dict[str, int] = {}
                for item in brand.get("analyses", []):
                    theme = item.get("theme")
                    if theme:
                        theme_counts[theme] = theme_counts.get(theme, 0) + 1
                top_themes = [theme for theme, _ in sorted(theme_counts.items(), key=lambda pair: pair[1], reverse=True)[:3]]
                avg_score = round(sum(scores) / max(len(scores), 1), 2) if scores else 0
                high_risk_count = len([a for a in brand.get("alerts", []) if a.get("score", 0) >= 4])
                negative_ratio = negative_count / total
                volume_score = min(35, negative_ratio * 35 + min(negative_count, 8) * 4)
                severity_score = min(45, high_risk_count * 12 + len([a for a in brand.get("alerts", []) if a.get("score", 0) == 3]) * 6 + avg_score * 5)
                spread_score = min(20, len(channel_breakdown) * 7 + (8 if {"youtube", "ptt"}.issubset(set(channel_breakdown.keys())) else 0))
                risk_score = int(round(min(100, volume_score + severity_score + spread_score)))
                payload = {
                    "keyword": keyword,
                    "snapshot_date": snapshot_date,
                    "updated_at": summary.get("updated_at"),
                    "brand": brand,
                }
                row_values = (
                    snapshot_date,
                    keyword,
                    datetime.now().isoformat(),
                    risk_score,
                    brand.get("total", 0),
                    brand.get("pos", 0),
                    brand.get("neu", 0),
                    brand.get("neg", 0),
                    high_risk_count,
                    avg_score,
                    self._json_dumps(channel_breakdown),
                    self._json_dumps(top_themes),
                    brand.get("dashboardSummary"),
                    self._json_dumps(payload),
                )
                c.execute(sql, row_values)
                written += 1
            conn.commit()
            return written
        finally:
            conn.close()

    def get_daily_snapshots(self, limit: int = 31, keyword: Optional[str] = None) -> List[Dict]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            if keyword:
                c.execute(
                    f"SELECT * FROM daily_snapshots WHERE keyword = {ph} ORDER BY snapshot_date DESC LIMIT {ph}",
                    (keyword, limit),
                )
            else:
                c.execute(
                    "SELECT * FROM daily_snapshots ORDER BY snapshot_date DESC, keyword ASC LIMIT " + ph,
                    (limit,),
                )
            rows = self._adapter.fetchall_dict(c)
            for row in rows:
                for field in ("channel_breakdown", "top_themes", "payload_json"):
                    if row.get(field):
                        try:
                            row[field] = json.loads(row[field])
                        except Exception:
                            pass
            return rows
        finally:
            conn.close()

    def get_dashboard_trend(self, days: int = 7, keywords: Optional[List[str]] = None, today: Optional[str] = None) -> Dict[str, Dict[str, Optional[float]]]:
        today = today or self._today_str()
        end_day = datetime.fromisoformat(today).date()
        start_day = end_day - timedelta(days=days - 1)
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            sql = f"""
                SELECT snapshot_date, keyword, article_count, neg_count
                FROM daily_snapshots
                WHERE snapshot_date >= {ph} AND snapshot_date < {ph}
            """
            params: List[Any] = [start_day.strftime("%Y-%m-%d"), today]
            if keywords:
                placeholders = ",".join([ph] * len(keywords))
                sql += f" AND keyword IN ({placeholders})"
                params.extend(keywords)
            sql += " ORDER BY snapshot_date ASC"
            c.execute(sql, tuple(params))
            snapshot_rows = self._adapter.fetchall_dict(c)
        finally:
            conn.close()

        trend: Dict[str, Dict[str, Optional[float]]] = {}
        for row in snapshot_rows:
            total = int(row.get("article_count") or 0)
            negative = int(row.get("neg_count") or 0)
            value = round((negative / total) * 100, 2) if total > 0 else None
            trend.setdefault(row["keyword"], {})[row["snapshot_date"]] = value

        today_summary = self.get_dashboard_day_summary(snapshot_date=today, keywords=keywords)
        for keyword, brand in (today_summary.get("brand_map") or {}).items():
            total = int(brand.get("total") or 0)
            negative = int(brand.get("neg") or 0)
            trend.setdefault(keyword, {})[today] = round((negative / total) * 100, 2) if total > 0 else None

        return trend

    def get_thread_item_id_by_platform_item_id(self, platform_item_id: str) -> Optional[int]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(f"SELECT id FROM thread_items WHERE platform_item_id = {ph}", (platform_item_id,))
            row = c.fetchone()
            if row is None:
                return None
            return row[0] if self._adapter.is_postgres else row["id"]
        finally:
            conn.close()

    # ── 去重 ─────────────────────────────────────────────────
    def is_duplicate(self, url: str) -> bool:
        thread_id = hashlib.md5(url.encode()).hexdigest()
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(f"SELECT 1 FROM threads WHERE id = {ph}", (thread_id,))
            return c.fetchone() is not None
        finally:
            conn.close()

    def get_existing_threads(self, urls: List[str]) -> set:
        """
        批次查詢已存在的 thread key（URL 或 title_key）。
        回傳原始輸入字串集合，供採集器快速過濾。
        """
        if not urls:
            return set()

        url_to_id = {url: hashlib.md5(url.encode()).hexdigest() for url in urls}
        ids = list(url_to_id.values())
        ph = self._ph()
        placeholders = ",".join([ph] * len(ids))
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT id FROM threads WHERE id IN ({placeholders})",
                tuple(ids)
            )
            existing_ids = {
                row[0] if self._adapter.is_postgres else row["id"]
                for row in c.fetchall()
            }
            return {url for url, thread_id in url_to_id.items() if thread_id in existing_ids}
        finally:
            conn.close()

    def get_thread_analysis_status(self, urls: List[str]) -> Dict[str, Dict[str, bool]]:
        """
        批次查詢 thread 是否存在，以及是否已有至少一筆文章級分析。

        回傳格式：
          {
            original_url_or_key: {
              "thread_exists": bool,
              "has_analysis": bool,
            }
          }
        """
        if not urls:
            return {}

        url_to_id = {url: hashlib.md5(url.encode()).hexdigest() for url in urls}
        ids = list(url_to_id.values())
        ph = self._ph()
        placeholders = ",".join([ph] * len(ids))
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"""SELECT t.id,
                           CASE WHEN COUNT(a.id) > 0 THEN 1 ELSE 0 END AS has_analysis
                    FROM threads t
                    LEFT JOIN analyses a ON a.thread_id = t.id
                    WHERE t.id IN ({placeholders})
                    GROUP BY t.id""",
                tuple(ids),
            )
            status_by_id = {}
            for row in self._adapter.fetchall_dict(c):
                thread_id = row["id"]
                status_by_id[thread_id] = {
                    "thread_exists": True,
                    "has_analysis": bool(row["has_analysis"]),
                }
            return {
                url: status_by_id.get(
                    thread_id,
                    {"thread_exists": False, "has_analysis": False},
                )
                for url, thread_id in url_to_id.items()
            }
        finally:
            conn.close()

    # ── 存入討論串（Layer 2）─────────────────────────────────
    def save_thread(
        self,
        url: str,
        source_name: str,
        channel: str,
        title: str,
        author: str = None,
        board: str = None,
        platform_id: str = None,
        thread_key: str = None,
        keyword: str = None,
        published_at: str = None,
        push_count: int = None,
        boo_count: int = None,
        neutral_count: int = None,
        comment_count: int = None,
    ) -> str:
        identity_key = thread_key or url
        thread_id = hashlib.md5(identity_key.encode()).hexdigest()
        source_id = self.get_source_id(source_name)
        ph = self._ph()
        now = self._now_iso()
        first_seen_at = now

        cols = ["id","source_id","channel","title","url","author","board","platform_id","keyword",
                "first_seen_at","published_at","fetched_at","push_count","boo_count","neutral_count","comment_count"]
        vals = (thread_id, source_id, channel, title, url, author, board, platform_id, keyword,
                first_seen_at, published_at, now, push_count, boo_count, neutral_count, comment_count)
        ph_list = ", ".join([ph] * len(cols))
        cols_str = ", ".join(cols)

        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            if self._adapter.is_postgres:
                update_cols = ["source_id","channel","title","url","author","board","platform_id",
                               "keyword","published_at","fetched_at",
                               "push_count","boo_count","neutral_count","comment_count"]
                updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in update_cols)
                updates += ", first_seen_at = COALESCE(threads.first_seen_at, EXCLUDED.first_seen_at)"
                sql = (
                    f"INSERT INTO threads ({cols_str}) VALUES ({ph_list}) "
                    f"ON CONFLICT (id) DO UPDATE SET {updates}"
                )
            else:
                updates = ", ".join(
                    f"{col} = excluded.{col}" for col in [
                        "source_id", "channel", "title", "url", "author", "board", "platform_id",
                        "keyword", "published_at", "fetched_at", "push_count", "boo_count", "neutral_count", "comment_count"
                    ]
                )
                sql = (
                    f"INSERT INTO threads ({cols_str}) VALUES ({ph_list}) "
                    f"ON CONFLICT(id) DO UPDATE SET {updates}, "
                    f"first_seen_at = COALESCE(threads.first_seen_at, excluded.first_seen_at)"
                )
            c.execute(sql, vals)
            conn.commit()
        finally:
            conn.close()
        return thread_id

    # ── 存入內容原子（Layer 3）──────────────────────────────
    def save_thread_item(
        self,
        thread_id: str,
        content: str,
        item_type: str = "main",
        author: str = None,
        sequence: int = 0,
        platform_item_id: str = None,
        like_count: int = None,
        published_at: str = None,
    ):
        if not content or not content.strip():
            return
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            if self._adapter.is_postgres:
                c.execute(
                    f"INSERT INTO thread_items (thread_id, item_type, author, platform_item_id, content, like_count, published_at, sequence) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph}) "
                    f"ON CONFLICT (platform_item_id) DO NOTHING",
                    (thread_id, item_type, author, platform_item_id, content.strip(), like_count, published_at, sequence)
                )
            else:
                c.execute(
                    f"INSERT OR IGNORE INTO thread_items (thread_id, item_type, author, platform_item_id, content, like_count, published_at, sequence) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                    (thread_id, item_type, author, platform_item_id, content.strip(), like_count, published_at, sequence)
                )
            conn.commit()
        finally:
            conn.close()

    def save_thread_items_bulk(self, thread_id: str, items: List[Dict]):
        rows = [
            (
                thread_id,
                it.get("item_type", "main"),
                it.get("author"),
                it.get("platform_item_id"),
                it.get("content", ""),
                it.get("like_count"),
                it.get("published_at"),
                it.get("sequence", 0),
            )
            for it in items if it.get("content", "").strip()
        ]
        if not rows:
            return
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            if self._adapter.is_postgres:
                sql = (
                    f"INSERT INTO thread_items (thread_id, item_type, author, platform_item_id, content, like_count, published_at, sequence) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph}) "
                    f"ON CONFLICT (platform_item_id) DO NOTHING"
                )
            else:
                sql = (
                    f"INSERT OR IGNORE INTO thread_items (thread_id, item_type, author, platform_item_id, content, like_count, published_at, sequence) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})"
                )
            c.executemany(sql, rows)
            conn.commit()
        finally:
            conn.close()

    # ── 存入分析結果 ─────────────────────────────────────────
    def save_analysis(
        self,
        thread_id: str,
        run_id: int,
        analysis: Dict,
        analyzed_content: str = "",
    ):
        ph = self._ph()
        normalized_score = normalize_score(analysis.get("score"))
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT s.weight FROM threads t JOIN sources s ON t.source_id = s.id WHERE t.id = {ph}",
                (thread_id,)
            )
            row = c.fetchone()
            source_weight = row[0] if row else 1.0

            c.execute(
                f"""INSERT INTO analyses
                  (thread_id, run_id, analyzed_content,
                   sentiment, score, theme, reason,
                   voice_source, analyzed_with, model_used, source_weight, analyzed_at)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                (
                    thread_id, run_id, analyzed_content,
                    analysis.get("sentiment"),
                    normalized_score,
                    analysis.get("theme"),
                    analysis.get("reason"),
                    analysis.get("voice_source"),
                    analysis.get("analyzed_with"),
                    analysis.get("model_used"),
                    source_weight,
                    datetime.now().isoformat(),
                )
            )
            conn.commit()
        finally:
            conn.close()

    def save_item_analysis(
        self,
        thread_item_id: int,
        run_id: int,
        analysis: Dict,
        analyzed_content: str = "",
    ):
        ph = self._ph()
        normalized_score = normalize_score(analysis.get("score"))
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"""INSERT INTO item_analyses
                  (thread_item_id, run_id, analyzed_content,
                   sentiment, score, theme, reason,
                   voice_source, analyzed_with, model_used, source_weight, analyzed_at)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                (
                    thread_item_id, run_id, analyzed_content,
                    analysis.get("sentiment"),
                    normalized_score,
                    analysis.get("theme"),
                    analysis.get("reason"),
                    analysis.get("voice_source"),
                    analysis.get("analyzed_with"),
                    analysis.get("model_used"),
                    1.0,
                    datetime.now().isoformat(),
                )
            )
            conn.commit()
        finally:
            conn.close()

    # ── 存入 PR 報告 ─────────────────────────────────────────
    def save_pr_report(
        self,
        run_id: int,
        keyword: str,
        track: str,
        report: str,
        dashboard_summary: str = None,
    ):
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"INSERT INTO pr_reports (run_id, keyword, track, dashboard_summary, report) VALUES ({ph},{ph},{ph},{ph},{ph})",
                (run_id, keyword, track, dashboard_summary, report)
            )
            conn.commit()
        finally:
            conn.close()

    # ── Intelligence Layer ──────────────────────────────────
    def save_intel_event_case(self, payload: Dict[str, Any]) -> str:
        columns = [
            "id", "keyword", "canonical_theme", "label", "status", "severity",
            "first_seen_at", "last_seen_at", "evidence_count", "source_mix_json",
            "sentiment_mix_json", "metadata_json", "updated_at",
        ]
        row = {**payload, "updated_at": self._now_iso()}
        sql = self._upsert_sql(
            "intel_event_cases",
            columns,
            ["id"],
            [col for col in columns if col != "id"],
        )
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(sql, tuple(row.get(col) for col in columns))
            conn.commit()
            return str(payload["id"])
        finally:
            conn.close()

    def get_intel_event_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(f"SELECT * FROM intel_event_cases WHERE id = {ph}", (case_id,))
            return self._adapter.fetchone_dict(c, c.fetchone())
        finally:
            conn.close()

    def get_intel_event_cases(self, since_date: str) -> List[Dict[str, Any]]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT * FROM intel_event_cases WHERE last_seen_at >= {ph} ORDER BY last_seen_at DESC, severity DESC",
                (since_date,),
            )
            return self._adapter.fetchall_dict(c)
        finally:
            conn.close()

    def bind_thread_to_intel_event_case(
        self,
        event_case_id: str,
        thread_id: str,
        latest_analysis_id: int,
        first_bound_at: str,
        last_bound_at: str,
    ) -> int:
        columns = [
            "event_case_id", "thread_id", "latest_analysis_id", "first_bound_at", "last_bound_at"
        ]
        sql = self._upsert_sql(
            "intel_event_case_threads",
            columns,
            ["event_case_id", "thread_id"],
            ["latest_analysis_id", "first_bound_at", "last_bound_at"],
        )
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(sql, (event_case_id, thread_id, latest_analysis_id, first_bound_at, last_bound_at))
            conn.commit()
            return self._adapter.last_insert_id(c) if not self._adapter.is_postgres else 1
        finally:
            conn.close()

    def get_intel_event_case_threads(self, event_case_id: str) -> List[Dict[str, Any]]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT * FROM intel_event_case_threads WHERE event_case_id = {ph} ORDER BY thread_id ASC",
                (event_case_id,),
            )
            return self._adapter.fetchall_dict(c)
        finally:
            conn.close()

    def save_intel_topic(self, payload: Dict[str, Any]) -> str:
        columns = [
            "id", "scope_key", "canonical_theme", "label",
            "first_seen_at", "last_seen_at", "event_count", "signal_count",
            "sentiment_mix_json", "source_mix_json", "metadata_json", "updated_at",
        ]
        row = {**payload, "updated_at": self._now_iso()}
        sql = self._upsert_sql(
            "intel_topics",
            columns,
            ["id"],
            [col for col in columns if col != "id"],
        )
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(sql, tuple(row.get(col) for col in columns))
            conn.commit()
            return str(payload["id"])
        finally:
            conn.close()

    def get_intel_topic(self, topic_id: str) -> Optional[Dict[str, Any]]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(f"SELECT * FROM intel_topics WHERE id = {ph}", (topic_id,))
            return self._adapter.fetchone_dict(c, c.fetchone())
        finally:
            conn.close()

    def get_intel_topics(self, scope_key: Optional[str] = None, days: int = 30) -> List[Dict[str, Any]]:
        cutoff = (
            datetime.now(ZoneInfo(APP_TIMEZONE)) - timedelta(days=days)
        ).isoformat()
        ph = self._ph()
        sql = f"SELECT * FROM intel_topics WHERE last_seen_at >= {ph}"
        params: List[Any] = [cutoff]
        if scope_key:
            sql += f" AND scope_key = {ph}"
            params.append(scope_key)
        sql += " ORDER BY signal_count DESC, last_seen_at DESC"
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(sql, tuple(params))
            return self._adapter.fetchall_dict(c)
        finally:
            conn.close()

    def bind_event_case_to_intel_topic(
        self,
        topic_id: str,
        event_case_id: str,
        first_bound_at: str,
        last_bound_at: str,
    ) -> int:
        columns = ["topic_id", "event_case_id", "first_bound_at", "last_bound_at"]
        sql = self._upsert_sql(
            "intel_topic_events",
            columns,
            ["topic_id", "event_case_id"],
            ["first_bound_at", "last_bound_at"],
        )
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(sql, (topic_id, event_case_id, first_bound_at, last_bound_at))
            conn.commit()
            return self._adapter.last_insert_id(c) if not self._adapter.is_postgres else 1
        finally:
            conn.close()

    def get_intel_topic_events(self, topic_id: str) -> List[Dict[str, Any]]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT * FROM intel_topic_events WHERE topic_id = {ph} ORDER BY event_case_id ASC",
                (topic_id,),
            )
            return self._adapter.fetchall_dict(c)
        finally:
            conn.close()

    def get_intel_topics_for_month(self, snapshot_month: str, scope_key: str) -> List[Dict[str, Any]]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            month_expr = "substr(last_seen_at, 1, 7)" if not self._adapter.is_postgres else "substr(last_seen_at, 1, 7)"
            c.execute(
                f"SELECT * FROM intel_topics WHERE {month_expr} = {ph} AND scope_key = {ph} ORDER BY signal_count DESC, last_seen_at DESC",
                (snapshot_month, scope_key),
            )
            return self._adapter.fetchall_dict(c)
        finally:
            conn.close()

    def get_intel_monthly_competitive_rows(self, snapshot_month: str) -> List[Dict[str, Any]]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            month_expr = "substr(last_seen_at, 1, 7)" if not self._adapter.is_postgres else "substr(last_seen_at, 1, 7)"
            c.execute(
                f"SELECT scope_key, canonical_theme, signal_count FROM intel_topics WHERE {month_expr} = {ph} ORDER BY canonical_theme ASC, signal_count DESC",
                (snapshot_month,),
            )
            return self._adapter.fetchall_dict(c)
        finally:
            conn.close()

    def save_intel_monthly_snapshot(self, payload: Dict[str, Any]) -> int:
        columns = [
            "snapshot_month", "scope_type", "scope_key", "snapshot_at",
            "active_risks_json", "opportunity_topics_json", "top_topics_json",
            "competitive_matrix_json", "narrative_summary", "payload_json",
        ]
        sql = self._upsert_sql(
            "intel_monthly_snapshots",
            columns,
            ["snapshot_month", "scope_type", "scope_key"],
            [col for col in columns if col not in {"snapshot_month", "scope_type", "scope_key"}],
        )
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(sql, tuple(payload.get(col) for col in columns))
            conn.commit()
            if self._adapter.is_postgres:
                c.execute(
                    f"SELECT id FROM intel_monthly_snapshots WHERE snapshot_month = {self._ph()} AND scope_type = {self._ph()} AND scope_key = {self._ph()}",
                    (payload["snapshot_month"], payload["scope_type"], payload["scope_key"]),
                )
                row = c.fetchone()
                return int(row[0]) if row else 0
            c.execute(
                "SELECT id FROM intel_monthly_snapshots WHERE snapshot_month = ? AND scope_type = ? AND scope_key = ?",
                (payload["snapshot_month"], payload["scope_type"], payload["scope_key"]),
            )
            row = c.fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def get_intel_monthly_snapshot(self, snapshot_month: str, scope_type: str, scope_key: str) -> Optional[Dict[str, Any]]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT * FROM intel_monthly_snapshots WHERE snapshot_month = {ph} AND scope_type = {ph} AND scope_key = {ph}",
                (snapshot_month, scope_type, scope_key),
            )
            return self._adapter.fetchone_dict(c, c.fetchone())
        finally:
            conn.close()

    def get_intelligence_signal_rows(self, since_date: str) -> List[Dict[str, Any]]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"""
                SELECT
                    a.id AS analysis_id,
                    a.thread_id,
                    t.keyword,
                    t.channel,
                    a.sentiment,
                    a.score,
                    a.theme,
                    COALESCE(t.published_at, a.analyzed_at) AS published_at
                FROM analyses a
                JOIN threads t ON t.id = a.thread_id
                WHERE COALESCE(t.published_at, a.analyzed_at) >= {ph}
                ORDER BY COALESCE(t.published_at, a.analyzed_at) ASC
                """,
                (since_date,),
            )
            return self._adapter.fetchall_dict(c)
        finally:
            conn.close()

    # ── 向後相容 ─────────────────────────────────────────────
    def save_article(self, article: Dict, analysis: Dict, run_id: int = None):
        """舊介面相容層：將 article dict 拆解後存入新 schema。"""
        channel = article.get("channel", "news")
        if channel == "ptt":
            source_name = "PTT"
        elif channel == "dcard":
            source_name = "Dcard"
        elif channel == "threads":
            source_name = "Threads"
        elif channel == "youtube":
            source_name = "YouTube"
        else:
            source_name = "Google News"

        board = None
        src_field = article.get("source", "")
        if "/" in src_field and src_field.startswith("PTT"):
            board = src_field.split("/", 1)[1]

        thread_id = self.save_thread(
            url=article["link"],
            source_name=source_name,
            channel=channel,
            title=article["title"],
            board=board,
            platform_id=article.get("platform_id"),
            thread_key=article.get("storage_key") or article["link"],
            keyword=article.get("keyword"),
            published_at=article.get("published"),
            push_count=article.get("push_count"),
            boo_count=article.get("boo_count"),
            neutral_count=article.get("neutral_count"),
            comment_count=article.get("comment_count"),
        )

        content = article.get("content", "")
        if content:
            self.save_thread_item(thread_id, content, item_type="main")

        for item in article.get("push_items", []):
            self.save_thread_item(
                thread_id,
                item.get("content", ""),
                item_type=item.get("item_type", "push"),
                author=item.get("author"),
                sequence=item.get("sequence", 0),
                platform_item_id=item.get("platform_item_id"),
                like_count=item.get("like_count"),
                published_at=item.get("published_at"),
            )

        if run_id:
            self.save_analysis(thread_id, run_id, analysis, analyzed_content=content[:500])

    # ── 歷史趨勢查詢 ─────────────────────────────────────────
    def get_recent_sentiment_trend(self, limit: int = 100) -> List[tuple]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT a.sentiment, a.score FROM analyses a ORDER BY a.analyzed_at DESC LIMIT {ph}",
                (limit,)
            )
            rows = c.fetchall()
            return [(row[0], normalize_score(row[1])) for row in rows]
        finally:
            conn.close()

    def get_thread_analyses(self, run_id: int) -> List[Dict]:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"""SELECT a.*, t.title, t.url, t.channel, t.board,
                       t.push_count, t.boo_count, t.neutral_count
                FROM analyses a
                JOIN threads t ON a.thread_id = t.id
                WHERE a.run_id = {ph}
                ORDER BY a.analyzed_at""",
                (run_id,)
            )
            return [self._normalize_analysis_row(row) for row in self._adapter.fetchall_dict(c)]
        finally:
            conn.close()

    def backfill_legacy_scores(self) -> int:
        """
        將 analyses 內舊版 0.x 分數回填成 1–5 整數。
        回傳更新筆數。
        """
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(f"SELECT id, score FROM analyses WHERE score > 0 AND score < 1")
            rows = c.fetchall()
            updates = []
            for row in rows:
                analysis_id = row[0] if self._adapter.is_postgres else row["id"]
                score = row[1] if self._adapter.is_postgres else row["score"]
                if is_legacy_score(score):
                    updates.append((normalize_score(score), analysis_id))

            if not updates:
                return 0

            c.executemany(
                f"UPDATE analyses SET score = {ph} WHERE id = {ph}",
                updates,
            )
            conn.commit()
            return len(updates)
        finally:
            conn.close()

    @staticmethod
    def _normalize_analysis_row(row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["raw_score"] = data.get("score")
        data["score"] = normalize_score(data.get("score"))
        return data


# ── 快速測試 ─────────────────────────────────────────────────
if __name__ == "__main__":
    import sqlite3
    db = SentimentDB(db_path=":memory:")
    print("Schema 建立成功")

    sources = db.get_active_sources()
    print(f"活躍渠道：{[s['name'] for s in sources]}")

    run_id = db.create_run("7-ELEVEN")
    print(f"Run ID: {run_id}")

    article = {
        "title": "7-ELEVEN 推出新服務獲好評", "link": "https://example.com/1",
        "source": "聯合新聞網", "published": "2026-05-16 10:00",
        "content": "統一超商推出新型態服務，深獲消費者好評...",
        "channel": "google_news", "keyword": "7-ELEVEN",
    }
    analysis = {"sentiment": "正面", "score": 0.85, "theme": "新服務",
                 "reason": "正面報導", "voice_source": "媒體",
                 "analyzed_with": "標題+內文", "model_used": "llama-3.3-70b"}
    db.save_article(article, analysis, run_id=run_id)
    print("save_article 相容層 OK")

    db.close_run(run_id, articles_found=1, articles_new=1)
    rows = db.get_thread_analyses(run_id)
    print(f"查回 {len(rows)} 筆分析，sentiment={rows[0]['sentiment']}")
    print("\nDB 測試通過")
