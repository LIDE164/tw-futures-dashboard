def get_decision_score(data, fund_data, inst_data=None, with_reason=False):
    sc, rs = 0, []

    adx = data.get('ADX', 0)
    roc_20 = data.get('ROC_20', 0)
    is_trending = adx >= 25 

    def add(score, text=None):
        nonlocal sc
        sc += score
        if with_reason and text:
            rs.append(text)

    if data.get('訊號', False): 
        add(3 if is_trending else 1, f"訊號成立 ADX={adx:.1f} +{3 if is_trending else 1}")

    if data.get('收盤價', 0) <= data.get('BB_DN', 0) * 1.02:
        add(2, "布林下軌支撐 +2")

    if data.get('MACD柱', 0) > data.get('前日MACD柱', -999):
        add(2, "MACD好轉 +2")
    else:
        add(-3, "MACD轉弱 -3")
        
    if data.get('成交量', 0) > data.get('5日均量', 0) * 1.1:
        add(2, "量增 +2")

    if data.get('回測有撐', False):
        add(2, "回測支撐 +2")

    # ... 其他技術條件判斷 ...

    final_score = max(5, min(99, int(50 + sc * 3)))

    if final_score >= 60:
        label = "🟢 強勢買進"
    elif final_score >= 45:
        label = "🟡 偏多觀察"
    else:
        label = "⚪ 忽略"

    feature = "一般狀態"
    if data.get('紅吞', False):
        feature = "🔥 紅吞表態"
    elif data.get('回測有撐', False):
        feature = "💪 回檔有撐"

    return (final_score, label, rs, feature) if with_reason else (final_score, label, feature)
