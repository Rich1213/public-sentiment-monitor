#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# setup.sh — 台灣便利超商輿情監控系統 初始化腳本
# ─────────────────────────────────────────────────────────────────

set -e

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     台灣便利超商輿情監控系統 — 環境初始化               ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── 1. 確認 Python 版本 ───────────────────────────────────────────
echo "▶ 確認 Python 版本..."
python3 --version || { echo "❌ 請先安裝 Python 3.10+"; exit 1; }

# ── 2. 建立虛擬環境 ──────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "▶ 建立虛擬環境 .venv ..."
    python3 -m venv .venv
else
    echo "▶ 虛擬環境已存在，跳過建立。"
fi

# ── 3. 啟動虛擬環境並安裝依賴 ────────────────────────────────────
echo "▶ 安裝依賴套件..."
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "   ✅ 套件安裝完成"

# ── 4. 建立 .env ──────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "▶ 已建立 .env（請填入您的 API Key 與 Telegram 設定）"
    echo ""
    echo "   必填項目："
    echo "   • NVIDIA_API_KEY   — NVIDIA NIM API 金鑰"
    echo "   • TELEGRAM_BOT_TOKEN — Telegram Bot Token"
    echo "   • TELEGRAM_CHAT_ID   — Telegram Chat ID"
else
    echo "▶ .env 已存在，跳過建立。"
fi

# ── 5. Dcard Session 提示 ────────────────────────────────────────
echo ""
echo "⚠️  Dcard 渠道說明："
echo "   Dcard 採集需要有效的瀏覽器 Session Cookie。"
echo "   請在瀏覽器登入 Dcard 後，匯出 cookies 存為 dcard_session.json"
echo "   格式：[{\"name\": \"...\", \"value\": \"...\", \"domain\": \".dcard.tw\"}, ...]"

# ── 6. 完成 ──────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✅ 初始化完成！請依下列步驟啟動系統："
echo ""
echo "  1. 編輯 .env 填入您的 API 金鑰"
echo "  2. source .venv/bin/activate"
echo "  3. python main.py              # 監控所有四大品牌"
echo "     python main.py -k 7-ELEVEN 全家  # 指定品牌"
echo "     python main.py --fresh     # 強制重新採集"
echo "════════════════════════════════════════════════════════════"
echo ""
