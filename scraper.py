import requests
from datetime import datetime, timedelta

def get_taifex_institutional_oi(api_token=""):
    """
    使用 FinMind API 獲取期交所三大法人未平倉淨額。
    為防止被雲端主機 IP 拒絕，本模組支援傳入 Token 進行正式管道請求。
    """
    try:
        start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesInstitutionalInvestors&data_id=TXF&start_date={start_date}"
        
        # 如果使用者有在側邊欄輸入 Token，則自動帶入
        if api_token:
            url += f"&token={api_token}"
        
        res = requests.get(url, timeout=10)
        data = res.json()
        
        if data.get('status') != 200 or not data.get('data'):
            return {"外資": 0, "投信": 0, "自營商": 0, "error": "FinMind API 未傳回資料 (請至側邊欄輸入有效的 Token / 或是目前為非交易日盤後)"}
            
        df = data['data']
        latest_date = df[-1]['date']
        latest_data = [item for item in df if item['date'] == latest_date]
        
        oi_dict = {"外資": 0, "投信": 0, "自營商": 0}
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
        return {"外資": 0, "投信": 0, "自營商": 0, "error": f"籌碼 API 連線異常: {str(e)}"}
