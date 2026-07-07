import requests
from datetime import datetime, timedelta

def get_taifex_institutional_oi():
    """
    使用 FinMind API 獲取期交所三大法人未平倉。
    優點：回傳乾淨的 JSON 格式，且不會封鎖 Streamlit 雲端主機的 IP。
    """
    try:
        # 抓取過去 10 天的資料，確保即使遇到長假也能拿到「最新一個交易日」的數據
        start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesInstitutionalInvestors&data_id=TXF&start_date={start_date}"
        
        # 發送 API 請求
        res = requests.get(url, timeout=10)
        data = res.json()
        
        # 檢查 API 狀態
        if data.get('status') != 200 or not data.get('data'):
            return {"外資": 0, "投信": 0, "自營商": 0, "error": "FinMind API 無資料"}
            
        df = data['data']
        
        # 取得資料陣列中「最後一筆」的日期，這就是最新的交易日
        latest_date = df[-1]['date']
        
        # 過濾出該日期的所有法人數據
        latest_data = [item for item in df if item['date'] == latest_date]
        
        oi_dict = {"外資": 0, "投信": 0, "自營商": 0}
        
        # 將對應的未平倉淨額塞入字典
        for item in latest_data:
            name = item.get('name', '')
            net_volume = item.get('open_interest_net_volume', 0)
            
            if '外資' in name:
                oi_dict['外資'] = net_volume
            elif '投信' in name:
                oi_dict['投信'] = net_volume
            elif '自營' in name:
                oi_dict['自營商'] = net_volume
                
        oi_dict['error'] = None
        return oi_dict
        
    except Exception as e:
        return {"外資": 0, "投信": 0, "自營商": 0, "error": f"API 連線錯誤: {str(e)}"}
