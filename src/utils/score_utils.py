"""
score_utils.py — 公關風險分數相容工具

支援兩種歷史分數格式：
  - 舊版：0.0–1.0 浮點數
  - 新版：1–5 整數
"""

from typing import Any

ALERT_THRESHOLD = 3


def normalize_score(raw_score: Any) -> int:
    """
    將舊版 0.0–1.0 與新版 1–5 分數統一轉成 1–5 整數。

    保留 0 作為「未知/缺值」哨兵值，避免把空值誤判成 1 分。
    """
    if raw_score is None:
        return 0

    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return 0

    if score <= 0:
        return 0

    if score >= 1:
        return max(1, min(5, round(score)))

    if score >= 0.85:
        return 5
    if score >= 0.70:
        return 4
    if score >= 0.50:
        return 3
    if score >= 0.30:
        return 2
    return 1


def is_legacy_score(raw_score: Any) -> bool:
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return False
    return 0 < score < 1
