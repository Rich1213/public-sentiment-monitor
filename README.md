# 台灣便利超商輿情監控系統
## Taiwan CVS Public Sentiment Monitor

自動監控 7-ELEVEN、全家、萊爾富、OK mart 四大品牌在 Google News、Bing News、PTT、Dcard 上的輿論動態，以 AI 進行情感分析，並產出雙軌公關策略建議。

---

## 系統架構

```
資料採集層               分析層                    輸出層
─────────────────────   ─────────────────────   ─────────────────────
Google News (RSS)   ──►                         Telegram 警報
Bing News (RSS)     ──► LLM 情感分析          ──► SQLite 資料庫
PTT (全站搜尋)      ──► (NVIDIA / Llama-3.3)  ──► PR 策略報告
Dcard (API)         ──►                         Console 輸出
```

**雙軌公關策略（PR Advisor）**
- **Track A — 危機應對軌**：負面聲量為主時啟動，提供 SCCT 框架的止血、溝通、修復三段策略
- **Track B — 品牌進攻軌**：正面聲量為主時啟動，提供內容資產化、SEO 承接、社群放大建議

---

## 快速開始

```bash
# 1. 初始化環境（首次使用）
bash setup.sh

# 2. 填寫 API 金鑰
nano .env

# 3. 啟動監控
source .venv/bin/activate
python main.py                     # 監控所有四大品牌
python main.py -k 7-ELEVEN 全家    # 指定品牌
python main.py --fresh             # 強制重新採集
```

---

## 環境變數設定（.env）

| 變數名稱 | 必填 | 說明 |
|---|---|---|
| `NVIDIA_API_KEY` | ✅ | NVIDIA NIM API 金鑰（用於 Llama-3.3-70B） |
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | ✅ | 接收警報的 Chat ID |
| `MONITOR_KEYWORDS` | — | 監控品牌（逗號分隔，預設監控四大品牌） |
| `FETCH_LIMIT` | — | 每渠道採集上限（預設 10）|
| `ALERT_THRESHOLD` | — | 負面警報閾值，0.0～1.0（預設 0.7）|

---

## 專案結構

```
public-sentiment-monitor/
├── main.py                         # 主程式（多品牌監控入口）
├── setup.sh                        # 初始化腳本
├── requirements.txt                # Python 依賴套件
├── .env.example                    # 環境變數範本
├── dcard_session.json              # Dcard Cookie（需自行取得）
│
└── src/
    ├── collectors/
    │   ├── google_news_collector.py   # Google News RSS + 全文提取
    │   ├── news_collector.py          # Bing News RSS + 全文提取
    │   ├── ptt_collector.py           # PTT 全站搜尋 + 推/噓解析
    │   └── dcard_collector.py         # Dcard API（JA3 指紋偽裝）
    │
    ├── analyzers/
    │   ├── sentiment_analyzer.py      # LLM 情感分析（正面/中立/負面）
    │   └── pr_advisor.py              # 雙軌公關策略生成
    │
    ├── notifiers/
    │   └── telegram_notifier.py       # Telegram 警報通知
    │
    └── utils/
        ├── db_manager.py              # SQLite 三層式資料管理
        └── content_extractor.py       # 自適應全文提取（trafilatura + bs4）
```

---

## 資料庫結構

系統使用 SQLite 三層式架構（`sentiment_monitor.db`）自動建立：

- **Layer 1**：`sources`（渠道登錄）、`keywords`（品牌管理）、`monitoring_runs`（執行紀錄）
- **Layer 2**：`threads`（文章主文 metadata，含 PTT 推/噓計數）
- **Layer 3**：`thread_items`（內文、推文、噓文、Dcard 留言原子）
- **Analysis**：`analyses`（LLM 分析結果）、`pr_reports`（PR 策略報告）

---

## Dcard Session 取得方式

Dcard 採集器依賴瀏覽器登入狀態。請依以下步驟取得 Cookie：

1. 以 Chrome 登入 [dcard.tw](https://www.dcard.tw)
2. 安裝瀏覽器擴充套件（如 Cookie-Editor）
3. 匯出 Dcard 相關 Cookie，存為專案根目錄的 `dcard_session.json`

格式範例：
```json
{
  "cookies": [
    {"name": "...", "value": "...", "domain": ".dcard.tw"}
  ]
}
```

---

## 技術規格

- **語言**：Python 3.10+
- **AI 模型**：meta/llama-3.3-70b-instruct（via NVIDIA NIM API）
- **資料庫**：SQLite（WAL 模式）
- **通知**：Telegram Bot API
