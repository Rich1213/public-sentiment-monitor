"""
api/app.py — FastAPI Web 服務

端點清單：
  GET  /health              服務健康檢查
  POST /runs/monitor        觸發一次監控採集（非同步背景執行）
  GET  /runs/recent         最近執行紀錄（預設最近 20 筆）
  GET  /runs/{run_id}       指定 run 的詳細資料（含分析結果）

啟動方式：
  uvicorn api.app:app --host 0.0.0.0 --port $PORT
  （Railway 自動設定 PORT 環境變數）
"""

import os
import logging
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.utils.db_manager import SentimentDB

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# FastAPI 應用
# ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="台灣便利超商輿情監控 API",
    description="Taiwan CVS Public Sentiment Monitor — REST API",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────
# Pydantic Schema
# ─────────────────────────────────────────────────────────────

class MonitorRequest(BaseModel):
    """POST /runs/monitor 請求 body。"""
    keywords: Optional[List[str]] = None
    fresh: bool = False


class MonitorResponse(BaseModel):
    status: str
    message: str
    keywords: List[str]
    triggered_at: str


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    db_backend: str
    version: str


class DashboardTodayResponse(BaseModel):
    snapshot_date: str
    updated_at: Optional[str] = None
    active_batch: Optional[dict] = None
    brand_map: dict
    channel_counts: dict
    all_alerts: list
    total_articles: int
    trend: Optional[dict] = None


# ─────────────────────────────────────────────────────────────
# 背景任務執行器
# ─────────────────────────────────────────────────────────────

def _run_monitor_bg(keywords: List[str], fresh: bool, batch_id: int) -> None:
    """在背景執行完整監控流程（供 BackgroundTasks 呼叫）。"""
    from worker.runner import run_all_brands
    db = SentimentDB()
    try:
        run_all_brands(keywords=keywords, fresh_mode=fresh, batch_id=batch_id)
    except Exception as e:
        logger.error("背景監控任務失敗：%s", e, exc_info=True)
        try:
            db.close_monitor_batch(batch_id, status="failed")
        except Exception:
            logger.error("關閉 monitor batch 失敗：%s", batch_id, exc_info=True)


# ─────────────────────────────────────────────────────────────
# 路由
# ─────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
def health_check():
    """服務健康檢查。"""
    db = SentimentDB()
    db_backend = "postgresql" if db._adapter.is_postgres else "sqlite"
    return HealthResponse(
        status="ok",
        timestamp=datetime.utcnow().isoformat() + "Z",
        db_backend=db_backend,
        version="2.0.0",
    )


@app.post("/runs/monitor", response_model=MonitorResponse, tags=["Monitor"])
def trigger_monitor(req: MonitorRequest, background_tasks: BackgroundTasks):
    """
    觸發一次監控採集。採集在背景執行，立即回傳 202 狀態。

    - **keywords**: 指定監控品牌，不填則監控全部四大品牌
    - **fresh**: true = 強制重新採集（忽略去重）
    """
    default_kws = ["7-ELEVEN", "全家", "萊爾富", "OK mart", "超商食安"]
    env_kws_str = os.getenv("MONITOR_KEYWORDS", "")
    env_kws = [k.strip() for k in env_kws_str.split(",") if k.strip()]

    keywords = req.keywords or env_kws or default_kws
    db = SentimentDB()
    active_batch = db.get_active_monitor_batch()
    if active_batch:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "已有輿情更新任務執行中",
                "batch_id": active_batch.get("id"),
                "started_at": active_batch.get("started_at"),
                "keywords": active_batch.get("keywords") or [],
            },
        )

    batch_id = db.create_monitor_batch(keywords=keywords, fresh_mode=req.fresh)
    background_tasks.add_task(_run_monitor_bg, keywords=keywords, fresh=req.fresh, batch_id=batch_id)

    return MonitorResponse(
        status="accepted",
        message="監控任務已排入背景執行",
        keywords=keywords,
        triggered_at=datetime.utcnow().isoformat() + "Z",
    )


@app.get("/dashboard/today", response_model=DashboardTodayResponse, tags=["Dashboard"])
def dashboard_today(
    date: Optional[str] = Query(default=None, description="日期 YYYY-MM-DD，預設今天"),
):
    db = SentimentDB()
    try:
        summary = db.get_dashboard_day_summary(snapshot_date=date)
        summary["trend"] = db.get_dashboard_trend(days=7, keywords=["7-ELEVEN", "全家", "萊爾富", "OK mart"])
        return summary
    except Exception as e:
        logger.error("查詢 dashboard today 失敗：%s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/snapshots/recent", tags=["Snapshots"])
def recent_snapshots(
    limit: int = Query(default=31, ge=1, le=90, description="回傳筆數"),
    keyword: Optional[str] = Query(default=None, description="指定品牌"),
):
    db = SentimentDB()
    try:
        rows = db.get_daily_snapshots(limit=limit, keyword=keyword)
        return {"snapshots": rows, "count": len(rows)}
    except Exception as e:
        logger.error("查詢 snapshots 失敗：%s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/snapshots/capture", tags=["Snapshots"])
def capture_snapshot(
    date: Optional[str] = Query(default=None, description="日期 YYYY-MM-DD，預設今天"),
):
    db = SentimentDB()
    try:
        written = db.save_daily_snapshots(snapshot_date=date)
        return {"status": "ok", "snapshot_date": date or datetime.now().strftime("%Y-%m-%d"), "written": written}
    except Exception as e:
        logger.error("建立 snapshot 失敗：%s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/runs/recent", tags=["Monitor"])
def recent_runs(
    limit: int = Query(default=20, ge=1, le=100, description="回傳筆數"),
    keyword: Optional[str] = Query(default=None, description="篩選品牌關鍵字"),
):
    """
    查詢最近執行紀錄。

    - **limit**: 最多回傳幾筆（預設 20，最大 100）
    - **keyword**: 指定品牌篩選（可選）
    """
    db = SentimentDB()
    try:
        rows = db.get_recent_runs(limit=limit, keyword=keyword)
        return {"runs": rows, "count": len(rows)}
    except Exception as e:
        logger.error("查詢 recent runs 失敗：%s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/runs/{run_id}", tags=["Monitor"])
def get_run_detail(run_id: int):
    """
    查詢指定 run 的詳細資料，含情感分析結果摘要與 PR 報告。
    """
    db = SentimentDB()
    try:
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} 不存在")

        analyses = db.get_run_analyses(run_id)
        item_analyses = db.get_run_item_analyses(run_id)
        pr_report = db.get_run_pr_report(run_id)

        return {
            "run": run,
            "analyses": analyses,
            "item_analyses": item_analyses,
            "pr_report": pr_report,
            "analyses_count": len(analyses),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("查詢 run %s 失敗：%s", run_id, e)
        raise HTTPException(status_code=500, detail=str(e))
