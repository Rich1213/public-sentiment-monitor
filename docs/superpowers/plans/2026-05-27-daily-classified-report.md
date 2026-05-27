# Daily Classified Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new `昨日分類日報` product layer that lets a main-brand marketing or PR user review yesterday's classified sentiment report in the web app, with category summaries, representative articles, and evidence quotes, without changing the semantics of the existing dashboard.

**Architecture:** Add a dedicated daily-report data layer on top of existing analyzed signals. A deterministic classifier maps yesterday's signals into six fixed sections, a builder job materializes daily report snapshots into new `intel_daily_*` tables, a new `/daily-report` API serves the page, and a new `daily-report.html` page renders the report with the same shell as dashboard/intelligence while remaining logically isolated from both.

**Tech Stack:** Python, FastAPI, SQLite/PostgreSQL via `SentimentDB`, plain HTML/CSS/JS frontend, pytest, node test runner.

---

## File Structure

### New files

- `src/daily_report/__init__.py`
  - Package marker for the daily report layer.
- `src/daily_report/classifier.py`
  - Deterministic signal-to-section classifier for the six fixed report sections.
- `src/daily_report/builder.py`
  - Reads yesterday's analyzed signals for one main brand, groups them into sections, builds headline summary, representative threads, and evidence quotes.
- `src/jobs/daily_classified_report_job.py`
  - Orchestrates a single day / single brand report capture into DB.
- `scripts/capture_daily_classified_report.py`
  - CLI entrypoint for scheduled runs.
- `daily-report.html`
  - New page for the yesterday report.
- `daily_report_summary.js`
  - Small frontend formatter helpers for the new page.
- `tests/test_daily_report_classifier.py`
  - Unit tests for section classification.
- `tests/test_daily_classified_report_job.py`
  - DB + job integration tests for report capture.
- `tests/test_daily_report_api.py`
  - API contract tests for `/daily-report`.
- `tests/daily_report_html.test.js`
  - Frontend smoke tests for navigation and rendering.

### Modified files

- `src/utils/db_manager.py`
  - Add new tables and CRUD methods for `intel_daily_reports` and `intel_daily_report_sections`.
- `api/app.py`
  - Add `/daily-report` endpoint and response schema.
- `dashboard.html`
  - Add navigation link to the new report page.
- `intelligence.html`
  - Add navigation link to the new report page.
- `README.md`
  - Document the new layer and capture script.

### Existing tests that must keep passing

- `tests/test_dashboard_today.py`
- `tests/test_daily_snapshot_job.py`
- `tests/test_score_compat.py`
- `tests/dashboard_html.test.js`
- `tests/intelligence_html.test.js`

## Task 1: Lock the daily-report contract with failing tests

**Files:**
- Create: `tests/test_daily_report_api.py`
- Create: `tests/daily_report_html.test.js`
- Modify: `tests/test_dashboard_today.py` (only if a regression guard is needed for nav or API coexistence)

- [ ] **Step 1: Write the failing API contract test**

```python
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import app
from src.utils.db_manager import SentimentDB


class DailyReportApiTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.original_sqlite_path = os.environ.get("SQLITE_PATH")
        os.environ["SQLITE_PATH"] = path
        self.db = SentimentDB(db_path=path)
        self.client = TestClient(app)

        self.db.save_intel_daily_report(
            {
                "report_date": "2026-05-26",
                "scope_type": "brand",
                "scope_key": "7-ELEVEN",
                "snapshot_at": "2026-05-27T08:00:00+08:00",
                "headline_summary": "昨日 7-ELEVEN 輿情以商品反饋與價格討論為主。",
                "payload_json": "{\"section_order\": [\"product_feedback\", \"price_value\"]}",
            }
        )
        self.db.save_intel_daily_report_section(
            {
                "report_date": "2026-05-26",
                "scope_key": "7-ELEVEN",
                "section_key": "product_feedback",
                "section_label": "商品反饋",
                "signal_count": 3,
                "pos_count": 1,
                "neu_count": 1,
                "neg_count": 1,
                "high_risk_count": 0,
                "summary_text": "鮮食與聯名商品的口味和配料調整討論較多。",
                "top_threads_json": "[{\"title\": \"[商品] 全家x屯京拉麵\", \"channel\": \"ptt\"}]",
                "evidence_quotes_json": "[{\"quote\": \"沒有筍乾沒有蔥\"}]",
                "payload_json": "{}",
            }
        )

    def tearDown(self):
        if self.original_sqlite_path is None:
            os.environ.pop("SQLITE_PATH", None)
        else:
            os.environ["SQLITE_PATH"] = self.original_sqlite_path
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_daily_report_endpoint_returns_headline_and_sections(self):
        resp = self.client.get("/daily-report?date=2026-05-26&scope_key=7-ELEVEN")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["report_date"], "2026-05-26")
        self.assertEqual(body["scope_key"], "7-ELEVEN")
        self.assertEqual(body["headline_summary"], "昨日 7-ELEVEN 輿情以商品反饋與價格討論為主。")
        self.assertEqual(len(body["sections"]), 1)
        self.assertEqual(body["sections"][0]["section_key"], "product_feedback")
```

- [ ] **Step 2: Write the failing HTML smoke test**

```javascript
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

test('daily-report.html includes navigation to dashboard and intelligence', () => {
  const html = fs.readFileSync('daily-report.html', 'utf8');
  assert.match(html, /dashboard\.html/);
  assert.match(html, /intelligence\.html/);
  assert.match(html, /昨日分類日報/);
});
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_daily_report_api.py -v
node --test tests/daily_report_html.test.js
```

Expected:

- `tests/test_daily_report_api.py` fails with missing DB methods or missing `/daily-report`
- `tests/daily_report_html.test.js` fails because `daily-report.html` does not exist yet

- [ ] **Step 4: Commit the failing-test checkpoint**

```bash
git add tests/test_daily_report_api.py tests/daily_report_html.test.js
git commit -m "test: lock daily report contract"
```

## Task 2: Add daily-report schema and DB methods

**Files:**
- Modify: `src/utils/db_manager.py`
- Create: `tests/test_daily_classified_report_job.py`

- [ ] **Step 1: Write the failing DB persistence test**

```python
import os
import tempfile
import unittest

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
        assert report["headline_summary"] == "昨日總結"
        assert sections[0]["section_key"] == "product_feedback"
```

- [ ] **Step 2: Run the DB test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_daily_classified_report_job.py::DailyReportSchemaTest::test_save_and_fetch_daily_report_with_sections -v
```

Expected:

- FAIL with missing `save_intel_daily_report`, missing `get_intel_daily_report`, or missing table

- [ ] **Step 3: Implement new schema and DB methods in `src/utils/db_manager.py`**

Add new table creation and CRUD methods shaped like the existing intelligence layer:

```python
def save_intel_daily_report(self, payload: Dict[str, Any]) -> int:
    columns = [
        "report_date", "scope_type", "scope_key", "snapshot_at",
        "headline_summary", "payload_json",
    ]
    sql = self._upsert_sql(
        "intel_daily_reports",
        columns,
        ["report_date", "scope_type", "scope_key"],
        ["snapshot_at", "headline_summary", "payload_json"],
    )
    values = tuple(payload.get(col) for col in columns)
    conn = self._adapter.get_connection()
    try:
        c = conn.cursor()
        c.execute(sql, values)
        conn.commit()
        return 1
    finally:
        conn.close()


def save_intel_daily_report_section(self, payload: Dict[str, Any]) -> int:
    columns = [
        "report_date", "scope_key", "section_key", "section_label",
        "signal_count", "pos_count", "neu_count", "neg_count", "high_risk_count",
        "summary_text", "top_threads_json", "evidence_quotes_json", "payload_json",
    ]
    sql = self._upsert_sql(
        "intel_daily_report_sections",
        columns,
        ["report_date", "scope_key", "section_key"],
        [col for col in columns if col not in {"report_date", "scope_key", "section_key"}],
    )
    values = tuple(payload.get(col) for col in columns)
    conn = self._adapter.get_connection()
    try:
        c = conn.cursor()
        c.execute(sql, values)
        conn.commit()
        return 1
    finally:
        conn.close()


def get_intel_daily_report(self, report_date: str, scope_type: str, scope_key: str) -> Optional[Dict[str, Any]]:
    ...


def get_intel_daily_report_sections(self, report_date: str, scope_key: str) -> List[Dict[str, Any]]:
    ...
```

Also add table creation for:

```sql
CREATE TABLE IF NOT EXISTS intel_daily_reports (
  report_date TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  snapshot_at TEXT NOT NULL,
  headline_summary TEXT,
  payload_json TEXT,
  PRIMARY KEY (report_date, scope_type, scope_key)
);

CREATE TABLE IF NOT EXISTS intel_daily_report_sections (
  report_date TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  section_key TEXT NOT NULL,
  section_label TEXT NOT NULL,
  signal_count INTEGER NOT NULL DEFAULT 0,
  pos_count INTEGER NOT NULL DEFAULT 0,
  neu_count INTEGER NOT NULL DEFAULT 0,
  neg_count INTEGER NOT NULL DEFAULT 0,
  high_risk_count INTEGER NOT NULL DEFAULT 0,
  summary_text TEXT,
  top_threads_json TEXT,
  evidence_quotes_json TEXT,
  payload_json TEXT,
  PRIMARY KEY (report_date, scope_key, section_key)
);
```

- [ ] **Step 4: Run the DB test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_daily_classified_report_job.py::DailyReportSchemaTest::test_save_and_fetch_daily_report_with_sections -v
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add src/utils/db_manager.py tests/test_daily_classified_report_job.py
git commit -m "feat: add daily report schema layer"
```

## Task 3: Build the deterministic six-section classifier

**Files:**
- Create: `src/daily_report/classifier.py`
- Create: `src/daily_report/__init__.py`
- Create: `tests/test_daily_report_classifier.py`

- [ ] **Step 1: Write the failing classifier tests**

```python
import unittest

from src.daily_report.classifier import classify_daily_report_signal


class DailyReportClassifierTest(unittest.TestCase):
    def test_food_safety_has_highest_priority(self):
        result = classify_daily_report_signal(
            title="[商品] 7-11 沙拉裡有活蟲",
            content="吃到一半發現有蟲",
            theme="食安危機",
            channel="ptt",
        )
        self.assertEqual(result["section_key"], "food_safety")

    def test_product_feedback_accepts_cvs_product_post(self):
        result = classify_daily_report_signal(
            title="[商品] 7-11 日東紅茶 皇家奶茶",
            content="口感偏甜 但回購意願高",
            theme="新品討論",
            channel="ptt",
        )
        self.assertEqual(result["section_key"], "product_feedback")

    def test_price_value_beats_generic_promotion_when_complaining_about_price(self):
        result = classify_daily_report_signal(
            title="[情報] 7-11 聯名新品",
            content="太貴了 CP 值很低",
            theme="聯名商品",
            channel="dcard",
        )
        self.assertEqual(result["section_key"], "price_value")
```

- [ ] **Step 2: Run the classifier tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_daily_report_classifier.py -v
```

Expected:

- FAIL with import error or function missing

- [ ] **Step 3: Implement `src/daily_report/classifier.py`**

Use a deterministic priority classifier:

```python
SECTION_MAP = [
    ("food_safety", "食安 / 品質異常"),
    ("product_feedback", "商品反饋"),
    ("service_feedback", "服務體驗"),
    ("price_value", "價格 / CP 值"),
    ("promotion_campaign", "活動 / 聯名 / 行銷"),
    ("membership_payment", "會員 / 支付 / APP"),
]


def classify_daily_report_signal(
    title: str,
    content: str = "",
    theme: str = "",
    channel: str = "",
) -> dict:
    text = " ".join([title or "", content or "", theme or ""]).lower()
    if any(token in text for token in ["活蟲", "異物", "食安", "發霉", "塑膠", "吃壞肚子"]):
        return {"section_key": "food_safety", "section_label": "食安 / 品質異常", "reason": "food_safety_keyword"}
    if any(token in text for token in ["漲價", "太貴", "cp值", "縮水", "不值"]):
        return {"section_key": "price_value", "section_label": "價格 / CP 值", "reason": "price_value_keyword"}
    if any(token in text for token in ["店員", "服務", "結帳", "態度", "排隊"]):
        return {"section_key": "service_feedback", "section_label": "服務體驗", "reason": "service_feedback_keyword"}
    if any(token in text for token in ["會員", "app", "支付", "點數", "載具", "發票"]):
        return {"section_key": "membership_payment", "section_label": "會員 / 支付 / APP", "reason": "membership_payment_keyword"}
    if any(token in text for token in ["聯名", "活動", "新品", "限定", "回歸", "復刻"]):
        return {"section_key": "promotion_campaign", "section_label": "活動 / 聯名 / 行銷", "reason": "promotion_campaign_keyword"}
    return {"section_key": "product_feedback", "section_label": "商品反饋", "reason": "default_product_feedback"}
```

- [ ] **Step 4: Run the classifier tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_daily_report_classifier.py -v
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add src/daily_report/__init__.py src/daily_report/classifier.py tests/test_daily_report_classifier.py
git commit -m "feat: add daily report classifier"
```

## Task 4: Build the daily report builder and capture job

**Files:**
- Create: `src/daily_report/builder.py`
- Create: `src/jobs/daily_classified_report_job.py`
- Create: `scripts/capture_daily_classified_report.py`
- Modify: `tests/test_daily_classified_report_job.py`

- [ ] **Step 1: Extend the job test with a failing end-to-end capture case**

```python
from src.jobs.daily_classified_report_job import run_daily_classified_report_capture


class DailyReportJobTest(unittest.TestCase):
    def test_capture_builds_brand_daily_report_sections(self):
        self.db.save_thread(
            title="[商品] 7-11 日東紅茶 皇家奶茶",
            url="https://www.ptt.cc/bbs/CVS/M.1.html",
            source="PTT",
            channel="ptt",
            keyword="7-ELEVEN",
            content="香氣不錯 但有點甜",
            published_at="2026-05-26T09:00:00+08:00",
            board="CVS",
            author="tester",
        )
        self.db.save_analysis(
            thread_id="...",
            keyword="7-ELEVEN",
            analysis={
                "sentiment": "中立",
                "score": 3,
                "theme": "商品反饋",
                "reason": "口味與甜度討論",
            },
        )

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
```

- [ ] **Step 2: Run the job test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_daily_classified_report_job.py::DailyReportJobTest::test_capture_builds_brand_daily_report_sections -v
```

Expected:

- FAIL with missing job or missing builder logic

- [ ] **Step 3: Implement `src/daily_report/builder.py`**

Create a focused builder with these responsibilities:

```python
class DailyReportBuilder:
    def __init__(self, db: SentimentDB):
        self.db = db

    def build_report(self, report_date: str, scope_key: str) -> dict:
        rows = self.db.get_daily_report_signal_rows(report_date=report_date, scope_key=scope_key)
        sections = self._group_sections(rows)
        headline_summary = self._build_headline_summary(scope_key, sections)
        return {
            "report": {
                "report_date": report_date,
                "scope_type": "brand",
                "scope_key": scope_key,
                "snapshot_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                "headline_summary": headline_summary,
                "payload_json": self.db._json_dumps({"section_order": [s["section_key"] for s in sections]}),
            },
            "sections": sections,
        }
```

Key implementation rules:

- Use only yesterday's analyzed rows for the chosen `scope_key`
- Compute `signal_count`, `pos_count`, `neu_count`, `neg_count`, `high_risk_count`
- Pick up to 3 representative threads sorted by score desc, then recency desc
- Extract up to 3 evidence quotes from `item analyses` or push rows if available
- Build `summary_text` deterministically from dominant sentiment + top concerns

- [ ] **Step 4: Implement the job and script**

```python
def run_daily_classified_report_capture(
    db: SentimentDB = None,
    report_date: str = None,
    scope_key: str = "7-ELEVEN",
) -> dict:
    db = db or SentimentDB()
    builder = DailyReportBuilder(db)
    payload = builder.build_report(report_date=report_date, scope_key=scope_key)
    db.save_intel_daily_report(payload["report"])
    written_sections = 0
    for section in payload["sections"]:
        db.save_intel_daily_report_section(section)
        written_sections += 1
    return {
        "report_date": report_date,
        "scope_key": scope_key,
        "written_sections": written_sections,
    }
```

Script:

```python
def main() -> int:
    report_date = resolve_snapshot_date(...)
    scope_key = os.getenv("DAILY_REPORT_SCOPE_KEY", "7-ELEVEN")
    result = run_daily_classified_report_capture(report_date=report_date, scope_key=scope_key)
    print(f"[daily_classified_report] report_date={result['report_date']} scope_key={result['scope_key']} written_sections={result['written_sections']}")
    return 0
```

- [ ] **Step 5: Run the job tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_daily_classified_report_job.py -v
```

Expected:

- PASS

- [ ] **Step 6: Commit**

```bash
git add src/daily_report/builder.py src/jobs/daily_classified_report_job.py scripts/capture_daily_classified_report.py tests/test_daily_classified_report_job.py
git commit -m "feat: add daily classified report builder"
```

## Task 5: Add the `/daily-report` API

**Files:**
- Modify: `api/app.py`
- Modify: `tests/test_daily_report_api.py`

- [ ] **Step 1: Extend the API test with expected response shape**

```python
def test_daily_report_endpoint_returns_expected_section_payload(self):
    resp = self.client.get("/daily-report?date=2026-05-26&scope_key=7-ELEVEN")
    self.assertEqual(resp.status_code, 200)
    body = resp.json()
    self.assertIn("sections", body)
    self.assertEqual(body["sections"][0]["section_label"], "商品反饋")
    self.assertIn("top_threads", body["sections"][0])
    self.assertIn("evidence_quotes", body["sections"][0])
```

- [ ] **Step 2: Run the API tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_daily_report_api.py -v
```

Expected:

- FAIL because endpoint or response schema is missing

- [ ] **Step 3: Implement new Pydantic schemas and endpoint in `api/app.py`**

Add:

```python
class DailyReportSectionResponse(BaseModel):
    section_key: str
    section_label: str
    signal_count: int
    pos_count: int
    neu_count: int
    neg_count: int
    high_risk_count: int
    summary_text: Optional[str] = None
    top_threads: list
    evidence_quotes: list


class DailyReportResponse(BaseModel):
    report_date: str
    scope_type: str
    scope_key: str
    headline_summary: Optional[str] = None
    sections: list


@app.get("/daily-report", response_model=DailyReportResponse, tags=["Daily Report"])
def daily_report(
    date: Optional[str] = Query(default=None, description="日期 YYYY-MM-DD，預設昨天"),
    scope_key: str = Query(default="7-ELEVEN", description="主品牌"),
):
    db = SentimentDB()
    report_date = date or resolve_snapshot_date()
    report = db.get_intel_daily_report(report_date, "brand", scope_key)
    if not report:
        raise HTTPException(status_code=404, detail=f"Daily report {report_date}/{scope_key} 不存在")
    sections = db.get_intel_daily_report_sections(report_date, scope_key)
    return {
        "report_date": report["report_date"],
        "scope_type": report["scope_type"],
        "scope_key": report["scope_key"],
        "headline_summary": report.get("headline_summary"),
        "sections": [
            {
                **row,
                "top_threads": json.loads(row.get("top_threads_json") or "[]"),
                "evidence_quotes": json.loads(row.get("evidence_quotes_json") or "[]"),
            }
            for row in sections
        ],
    }
```

- [ ] **Step 4: Run the API tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_daily_report_api.py -v
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add api/app.py tests/test_daily_report_api.py
git commit -m "feat: add daily report api"
```

## Task 6: Build the `daily-report.html` page

**Files:**
- Create: `daily-report.html`
- Create: `daily_report_summary.js`
- Modify: `dashboard.html`
- Modify: `intelligence.html`
- Modify: `tests/daily_report_html.test.js`

- [ ] **Step 1: Extend the HTML test with rendering targets**

```javascript
test('daily-report.html contains headline and section containers', () => {
  const html = fs.readFileSync('daily-report.html', 'utf8');
  assert.match(html, /id=\"daily-report-headline\"/);
  assert.match(html, /id=\"daily-report-sections\"/);
  assert.match(html, /昨日分類日報/);
});
```

- [ ] **Step 2: Run the HTML test to verify it fails**

Run:

```bash
node --test tests/daily_report_html.test.js
```

Expected:

- FAIL because `daily-report.html` is missing or lacks the required containers

- [ ] **Step 3: Implement `daily-report.html` using the existing visual shell**

Use the structure and aesthetic of `intelligence.html`, but tailor copy to the daily-report workflow:

```html
<div class="topbar">
  <div class="logo">
    <div class="logo-icon">◔</div>
    <div>
      <div class="logo-text">昨日分類日報</div>
      <div class="logo-sub">Yesterday Report · 按分類整理主品牌昨日輿情</div>
    </div>
  </div>
  <div class="topbar-right">
    <nav class="nav-tabs">
      <a class="nav-link" href="./dashboard.html">今日監控</a>
      <a class="nav-link active" href="./daily-report.html">昨日日報</a>
      <a class="nav-link" href="./intelligence.html">品牌決策</a>
    </nav>
  </div>
</div>

<div class="hero">
  <div class="card hero-card">
    <h1>昨日分類日報</h1>
    <p id="daily-report-headline">載入中…</p>
  </div>
  <div class="card">
    <div class="section-title">昨日重點分類數</div>
    <div class="kpi-big" id="daily-report-section-count">—</div>
    <div class="kpi-sub" id="daily-report-meta">等待日報 API</div>
  </div>
</div>

<div id="daily-report-sections" class="section-grid">
  <div class="empty">載入中…</div>
</div>
```

Implement rendering with `fetch(`${API}/daily-report?...`)`, and for each section render:

- counts
- summary text
- top threads
- evidence quotes

- [ ] **Step 4: Add nav links to the existing pages**

Update `dashboard.html` and `intelligence.html` top nav to include:

```html
<a class="nav-link" href="./daily-report.html">昨日日報</a>
```

- [ ] **Step 5: Run the HTML tests to verify they pass**

Run:

```bash
node --test tests/daily_report_html.test.js tests/dashboard_html.test.js tests/intelligence_html.test.js
```

Expected:

- PASS

- [ ] **Step 6: Commit**

```bash
git add daily-report.html daily_report_summary.js dashboard.html intelligence.html tests/daily_report_html.test.js
git commit -m "feat: add daily report page"
```

## Task 7: Document the capture flow and keep dashboard safe

**Files:**
- Modify: `README.md`
- Modify: `tests/test_dashboard_today.py` (only if a targeted no-regression assertion is missing)

- [ ] **Step 1: Add a no-regression dashboard assertion if needed**

If there is no assertion proving dashboard still uses `brand_map` and existing alert semantics, add one like:

```python
def test_dashboard_today_still_returns_existing_contract_keys(client):
    body = client.get("/dashboard/today").json()
    assert "brand_map" in body
    assert "all_alerts" in body
    assert "channel_counts" in body
```

- [ ] **Step 2: Run the targeted dashboard regression tests**

Run:

```bash
python3 -m pytest tests/test_dashboard_today.py tests/test_daily_snapshot_job.py tests/test_score_compat.py -v
```

Expected:

- PASS

- [ ] **Step 3: Update `README.md` with the new layer**

Add a short section:

```md
## 昨日分類日報

此頁面用於主品牌晨會前的昨天輿情匯報，按固定六大分類整理代表文章與推文舉證。

手動產生：

```bash
python3 scripts/capture_daily_classified_report.py
```

預設主品牌可由 `DAILY_REPORT_SCOPE_KEY` 指定。
```

- [ ] **Step 4: Run the full verification set**

Run:

```bash
python3 -m pytest tests/test_daily_report_classifier.py tests/test_daily_classified_report_job.py tests/test_daily_report_api.py tests/test_dashboard_today.py tests/test_daily_snapshot_job.py tests/test_score_compat.py -v
node --test tests/daily_report_html.test.js tests/dashboard_html.test.js tests/intelligence_html.test.js
```

Expected:

- All tests PASS

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_dashboard_today.py
git commit -m "docs: add daily report usage"
```

## Self-Review

### Spec coverage

- New product layer and purpose: covered by Tasks 2-6
- Six fixed sections: covered by Task 3
- Main-brand, yesterday-only workflow: covered by Tasks 4-5
- Evidence quotes integrated into section payload: covered by Tasks 4-6
- Independent data layer and API: covered by Tasks 2 and 5
- No dashboard semantic regression: covered by Tasks 1 and 7

### Placeholder scan

- No `TBD` or `TODO`
- All tasks name concrete files, commands, and test entry points
- Code steps include explicit function and endpoint shapes

### Type consistency

- Report table methods: `save_intel_daily_report`, `get_intel_daily_report`
- Section table methods: `save_intel_daily_report_section`, `get_intel_daily_report_sections`
- Job function: `run_daily_classified_report_capture`
- API endpoint: `GET /daily-report`
- Frontend page: `daily-report.html`

