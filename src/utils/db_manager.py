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
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from src.utils.score_utils import normalize_score, is_legacy_score

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 預設渠道種子資料
# ─────────────────────────────────────────────────────────────
DEFAULT_SOURCES = [
    # (name, type, is_active, weight, fetch_limit)
    ("Google News", "news",  1, 1.2, 10),
    ("PTT",         "forum", 1, 1.0, 10),
    ("Dcard",       "forum", 1, 0.9, 10),
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
    def __init__(self, db_path: str = None):
        self._adapter = DBAdapter()

        # 向後相容：允許傳入 db_path 覆寫 SQLite 路徑
        if db_path is not None and not self._adapter.is_postgres:
            self._adapter._sqlite_path = db_path

        self._init_db()
        self._ensure_schema_migrations()
        self._seed_sources()

    @property
    def adapter(self) -> DBAdapter:
        return self._adapter

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
            else:
                c.execute("PRAGMA table_info(pr_reports)")
                cols = [row["name"] for row in c.fetchall()]
                if "dashboard_summary" not in cols:
                    c.execute("ALTER TABLE pr_reports ADD COLUMN dashboard_summary TEXT")
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

            CREATE TABLE IF NOT EXISTS threads (
                id           TEXT PRIMARY KEY,
                source_id    INTEGER REFERENCES sources(id),
                channel      TEXT    NOT NULL,
                title        TEXT    NOT NULL,
                url          TEXT    NOT NULL,
                author       TEXT,
                board        TEXT,
                keyword      TEXT,
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
                content   TEXT    NOT NULL,
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

            CREATE TABLE IF NOT EXISTS pr_reports (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     INTEGER REFERENCES monitoring_runs(id),
                keyword    TEXT,
                track      TEXT,
                dashboard_summary TEXT,
                report     TEXT,
                created_at TEXT   DEFAULT (datetime('now'))
            );
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
            """CREATE TABLE IF NOT EXISTS threads (
                id           TEXT PRIMARY KEY,
                source_id    INTEGER REFERENCES sources(id),
                channel      TEXT    NOT NULL,
                title        TEXT    NOT NULL,
                url          TEXT    NOT NULL,
                author       TEXT,
                board        TEXT,
                keyword      TEXT,
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
                content   TEXT    NOT NULL,
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
            """CREATE TABLE IF NOT EXISTS pr_reports (
                id         SERIAL PRIMARY KEY,
                run_id     INTEGER REFERENCES monitoring_runs(id),
                keyword    TEXT,
                track      TEXT,
                dashboard_summary TEXT,
                report     TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )""",
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

    # ── Monitoring Run ────────────────────────────────────────
    def create_run(self, keyword: str, fresh_mode: bool = False) -> int:
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            now = datetime.now().isoformat()
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
                (datetime.now().isoformat(), articles_found, articles_new, run_id)
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

            now = datetime.now()
            to_close = []
            keyword_set = set(keywords or [])
            for row in rows:
                if keyword_set and row.get("keyword") not in keyword_set:
                    continue
                if older_than_minutes is not None:
                    started_at = row.get("started_at")
                    try:
                        started_dt = datetime.fromisoformat(started_at)
                    except Exception:
                        started_dt = now - timedelta(days=1)
                    if (now - started_dt) < timedelta(minutes=older_than_minutes):
                        continue
                to_close.append(int(row["id"]))

            for run_id in to_close:
                c.execute(
                    f"UPDATE monitoring_runs SET ended_at={ph}, articles_found=COALESCE(articles_found, 0), articles_new=COALESCE(articles_new, 0) WHERE id={ph}",
                    (now.isoformat(), run_id)
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

    # ── 存入討論串（Layer 2）─────────────────────────────────
    def save_thread(
        self,
        url: str,
        source_name: str,
        channel: str,
        title: str,
        author: str = None,
        board: str = None,
        keyword: str = None,
        published_at: str = None,
        push_count: int = None,
        boo_count: int = None,
        neutral_count: int = None,
        comment_count: int = None,
    ) -> str:
        thread_id = hashlib.md5(url.encode()).hexdigest()
        source_id = self.get_source_id(source_name)
        ph = self._ph()
        now = datetime.now().isoformat()

        cols = ["id","source_id","channel","title","url","author","board","keyword",
                "published_at","fetched_at","push_count","boo_count","neutral_count","comment_count"]
        vals = (thread_id, source_id, channel, title, url, author, board, keyword,
                published_at, now, push_count, boo_count, neutral_count, comment_count)
        ph_list = ", ".join([ph] * len(cols))
        cols_str = ", ".join(cols)

        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            if self._adapter.is_postgres:
                update_cols = ["source_id","channel","title","url","author","board",
                               "keyword","published_at","fetched_at",
                               "push_count","boo_count","neutral_count","comment_count"]
                updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in update_cols)
                sql = (
                    f"INSERT INTO threads ({cols_str}) VALUES ({ph_list}) "
                    f"ON CONFLICT (id) DO UPDATE SET {updates}"
                )
            else:
                sql = f"INSERT OR REPLACE INTO threads ({cols_str}) VALUES ({ph_list})"
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
    ):
        if not content or not content.strip():
            return
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                f"INSERT INTO thread_items (thread_id, item_type, author, content, sequence) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph})",
                (thread_id, item_type, author, content.strip(), sequence)
            )
            conn.commit()
        finally:
            conn.close()

    def save_thread_items_bulk(self, thread_id: str, items: List[Dict]):
        rows = [
            (thread_id, it.get("item_type", "main"),
             it.get("author"), it.get("content", ""), it.get("sequence", 0))
            for it in items if it.get("content", "").strip()
        ]
        if not rows:
            return
        ph = self._ph()
        conn = self._adapter.get_connection()
        try:
            c = conn.cursor()
            sql = (
                f"INSERT INTO thread_items (thread_id, item_type, author, content, sequence) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph})"
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

    # ── 向後相容 ─────────────────────────────────────────────
    def save_article(self, article: Dict, analysis: Dict, run_id: int = None):
        """舊介面相容層：將 article dict 拆解後存入新 schema。"""
        channel = article.get("channel", "news")
        if channel == "ptt":
            source_name = "PTT"
        elif channel == "dcard":
            source_name = "Dcard"
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
