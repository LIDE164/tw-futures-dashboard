# tw-futures-dashboard

## 永豐 Shioaji 設定

本專案預設以模擬模式啟動，且不會把 API key 寫進程式碼。

部署到 Streamlit Cloud 時，請在 secrets 或環境變數設定：

```text
SJ_API_KEY=your_sinopac_api_key
SJ_SECRET_KEY=your_sinopac_secret_key
SJ_SIMULATION=true
```

若要送出正式委託，還需要設定 CA 憑證：

```text
SJ_CA_PATH=
SJ_CA_PASSWORD=
SJ_PERSON_ID=
```

策略模組只產生建議訊號；永豐下單功能需要在側邊欄同時勾選「啟用永豐送單」與「我確認允許本次送單」才會送出。
