import yfinance as yf

def get_realtime_data():
    """
    取得即時的大盤相關報價與 VIX
    （台指期即時報價建議後續改用券商 API 如 Shioaji 獲取最準確的 Tick 與 VWAP）
    """
    try:
        # 抓取台灣加權指數 (^TWII) 作為參考
        twii = yf.Ticker("^TWII")
        twii_data = twii.history(period="1d")
        current_price = twii_data['Close'].iloc[-1]
        volume = twii_data['Volume'].iloc[-1]
        
        return {
            "current_price": current_price,
            "volume": volume,
            "vix": 18.5, # VIX 可替換為真實代碼或另外爬取
            "vwap": current_price * 0.998 # 模擬均價線計算
        }
    except:
        return {"current_price": 23000, "volume": 0, "vix": 0, "vwap": 23000}
