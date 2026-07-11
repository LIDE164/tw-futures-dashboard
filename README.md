# tw-futures-dashboard

## 永豐 Shioaji 設定

本專案預設以模擬模式啟動，且不會把 API key 寫進程式碼。

部署到 Streamlit Cloud 時，請在 secrets 或環境變數設定：

```text
SJ_API_KEY=your_sinopac_api_key
SJ_SECRET_KEY=your_sinopac_secret_key
SJ_SIMULATION=true
```

本機可使用 `.env`；Streamlit Cloud 請到網站後台 Secrets 設定，不要把真實 key 推到 GitHub。
一般使用時不需要在側邊欄手動輸入 API Key，畫面會自動讀取 `.env`、環境變數或 Streamlit Secrets。

## 系統定位

這不是自動交易系統，而是微型臺指策略研究、模擬交易與手動下單輔助系統。

- 永豐 API：只用於微型臺指行情、K 線與帳務參考
- 策略系統：只產生訊號，不直接下單
- 模擬下單：寫入本機 Streamlit session 的模擬帳本
- 回測系統：用歷史 K 線檢查策略邏輯
- 實際下單：請自行在券商軟體操作

## 微型臺指新手模式

- 商品根代碼預設為 `TMF`
- 契約乘數固定為 10 元/點
- 模擬口數固定為 1 口
- 訊號週期固定為 15 分鐘 K
- 首頁會顯示每口最大預估虧損與預估來回成本
- 首頁會顯示買一、賣一、價差與最近 15 分 K 線圖
- 手機首頁以行情列、單一操作卡、圖表切換與風控摘要為主，進階資訊預設收合
- 圖表支援近一月日 K 與 15 分交易圖切換，採台灣習慣紅漲綠跌配色
- 圖表會鎖定拖曳縮放並隱藏工具列，避免手機誤觸
- 進場參考價會優先使用賣一/買一，沒有買賣價時才用滑價估算
- 新手風控會限制每日最多 3 筆、連虧 2 筆停止、日虧 1000 元停止、收盤前 15 分鐘不開新倉
- 首頁會分開顯示契約代碼、契約到期日、市場狀態與最後有效訊號時間
- 休市或盤間時不提供可直接成交的進場價，只顯示下次開盤預備計畫
- 最新一根尚未完成的 15 分鐘 K 不會拿來產生策略訊號
- 回測暫不使用當日法人籌碼，避免用今天資料回填歷史
- 停損與停利會寫入模擬持倉，並在策略與回測中實際觸發
- 模擬帳本會保存到 `data/trading.db`，重啟 Streamlit 後仍可還原模擬部位

目前仍不是常駐警報服務；若沒有人開啟或刷新 Streamlit，系統不會保證背景持續監控行情。

## 背景警報服務

第一版背景服務使用 `signal_worker.py`，會定期輪詢永豐 snapshot 與最近 K 線：

```bash
python signal_worker.py --interval 30
```

測試單次執行：

```bash
python signal_worker.py --once
```

測試 Telegram 策略訊號發報：

```bash
python signal_worker.py --test-signal BUY_LONG
python signal_worker.py --test-signal SELL_SHORT
python signal_worker.py --test-signal CLOSE_LONG
python signal_worker.py --test-signal CLOSE_SHORT
python signal_worker.py --test-signal CLOSE_LONG --test-exit TARGET
python signal_worker.py --test-signal CLOSE_SHORT --test-exit TARGET
```

這些測試指令只會送出測試通知與寫入本機紀錄，不會送出真實委託。

Windows 本機建議使用專案附的 PowerShell 腳本，它會自動建立 `.venv` 並安裝 `requirements.txt`：

```powershell
.\run_worker.ps1 -Once
```

測試策略訊號：

```powershell
.\run_worker.ps1 -TestSignal BUY_LONG
.\run_worker.ps1 -TestSignal SELL_SHORT
.\run_worker.ps1 -TestSignal CLOSE_LONG
.\run_worker.ps1 -TestSignal CLOSE_LONG -TestExit TARGET
```

常駐執行：

```powershell
.\run_worker.ps1 -Interval 30
```

背景啟動、查看與停止：

```powershell
.\start_worker.ps1 -Interval 30
.\worker_status.ps1
.\stop_worker.ps1
```

如果 PowerShell 不允許執行腳本，可先在同一個 Terminal 執行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

目前功能：

- 只使用完整 15 分 K 產生進場 / 平倉訊號
- 讀取 SQLite 中的模擬部位，用來監控停損與停利
- 寫入 `signals`、`alerts`、`worker_heartbeats`
- 以 alert key 去重，避免同一根 K 重複提醒
- 若設定 `TELEGRAM_BOT_TOKEN` 與 `TELEGRAM_CHAT_ID`，會嘗試推送 Telegram
- 若設定 `ALERT_WEBHOOK_URL`，會嘗試推送 webhook
- 未設定通知時，警報仍會保存到 SQLite 並輸出到 console

注意：Streamlit Cloud 不保證背景常駐行程穩定執行；真正全自動提醒建議部署在 VPS、NAS、雲端排程或其他常駐 worker 環境。

## 安全檔案

以下檔案不應提交到 GitHub：

- `.env`
- `.streamlit/secrets.toml`
- `data/trading.db`

可參考 `.env.example` 與 `.streamlit/secrets.toml.example` 建立自己的設定。
