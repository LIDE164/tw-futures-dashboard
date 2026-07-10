# tw-futures-dashboard

## 永豐 Shioaji 設定

本專案預設以模擬模式啟動，且不會把 API key 寫進程式碼。

部署到 Streamlit Cloud 時，請在 secrets 或環境變數設定：

```text
SJ_API_KEY=your_sinopac_api_key
SJ_SECRET_KEY=your_sinopac_secret_key
SJ_SIMULATION=true
```

## 系統定位

這不是自動交易系統，而是台指期策略研究、模擬交易與手動下單輔助系統。

- 永豐 API：只用於行情、K 線與帳務參考
- 策略系統：只產生訊號，不直接下單
- 模擬下單：寫入本機 Streamlit session 的模擬帳本
- 回測系統：用歷史 K 線檢查策略邏輯
- 實際下單：請自行在券商軟體操作
