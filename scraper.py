import requests
from datetime import datetime, timedelta

def get_taifex_institutional_oi(api_token=""):
    try:
        start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesInstitutionalInvestors&data_id=TXF&start_date={start_date}"
        
        # 如果使用者有在網頁側邊欄輸入 Token，則自動帶入
        if api_token:
            url += f"&token={api_token}"
        
        res = requests.get(url, timeout=10)
        data = res.json()
        
        if data.get('status') != 200 or not data.get('data'):
            return {"外資": 0, "投信": 0, "自營商": 0, "error": "FinMind API 未傳回資料 (請確認 Token 是否正確或目前是否為盤後)"}
            
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
