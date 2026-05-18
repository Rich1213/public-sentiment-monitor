"""
logger.py — 集中式 logging 設定

所有模組統一使用此 logger，避免 print() 散落各處。

使用方式：
    from src.utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("採集完成：%d 篇", count)

Log level 由 .env 的 LOG_LEVEL 控制（預設 INFO）。
Log 同時輸出到：
  - 終端機（StreamHandler）
  - logs/monitor.log（RotatingFileHandler，單檔 5MB，保留 5 份）
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    """
    取得具名 logger。多次呼叫相同 name 回傳同一個實例（Python logging 保證）。
    """
    logger = logging.getLogger(name)

    # 只在 root logger 未設定時初始化（避免重複加 handler）
    if not logging.root.handlers:
        _setup_root_logger()

    return logger


def _setup_root_logger():
    level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    level     = getattr(logging, level_str, logging.INFO)

    fmt     = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── 終端機輸出 ───────────────────────────────────────────────
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    # ── 檔案輸出（logs/ 目錄）────────────────────────────────────
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "monitor.log",
        maxBytes=5 * 1024 * 1024,   # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    logging.root.setLevel(level)
    logging.root.addHandler(stream_handler)
    logging.root.addHandler(file_handler)
