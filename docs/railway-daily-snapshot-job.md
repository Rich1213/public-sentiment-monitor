# Railway Daily Snapshot Job

目標：每天台北時間 `00:05` 凍結前一天的 `daily_snapshots`，避免昨日趨勢值被今天補跑資料覆蓋。

## Repo 內已補好的檔案

- `src/jobs/daily_snapshot_job.py`
- `scripts/capture_daily_snapshot.py`
- `railway.snapshot.toml`

`railway.snapshot.toml` 會把 cron service 設成：

- `startCommand = "python3 scripts/capture_daily_snapshot.py"`
- `cronSchedule = "5 16 * * *"`

說明：

- Railway cron 以 `UTC` 計時
- `16:05 UTC` = 台北時間隔天 `00:05`
- job 內部用 `Asia/Taipei` 計算 `snapshot_date = yesterday`

## Railway 一次性設定

1. 在同一個 Railway project 新增一個 service
2. Source 指向同一個 GitHub repo 與 `main` branch
3. 到這個 service 的 Settings
4. 將 Config as Code file path 設成 `/railway.snapshot.toml`
5. 確認這個 service 也能讀到：
   - `DATABASE_URL`
   - `MONITOR_KEYWORDS`（可選）
   - `SNAPSHOT_TIMEZONE=Asia/Taipei`（若不設，程式預設也是 `Asia/Taipei`）
6. Deploy 該 service

## 手動驗證

可以先把 cron service 的 Start Command 手動執行一次，或把 schedule 暫時改成接近現在的 UTC 時間。

預期 log：

```text
[daily_snapshot_job] snapshot_date=2026-05-19 written=4 timezone=Asia/Taipei
```

`written=0` 不一定是錯，代表：

- 那一天沒有符合「今日新訊號」的資料
- 或該日 snapshot 已經凍結存在，job 選擇跳過覆寫
