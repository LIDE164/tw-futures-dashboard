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
- 回測暫不使用當日法人籌碼，避免用今天資料回填歷史
- 停損與停利會寫入模擬持倉，並在策略與回測中實際觸發
- 模擬帳本會保存到 `data/trading.db`，重啟 Streamlit 後仍可還原模擬部位

## 安全檔案

以下檔案不應提交到 GitHub：

- `.env`
- `.streamlit/secrets.toml`
- `data/trading.db`

可參考 `.env.example` 與 `.streamlit/secrets.toml.example` 建立自己的設定。
