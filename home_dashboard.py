import pandas as pd
import streamlit as st

import charting


def _price(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:,.0f}" if number and not pd.isna(number) else "無資料"


def _money(value):
    try:
        return f"NT$ {float(value or 0):,.0f}"
    except (TypeError, ValueError):
        return "無資料"


def _valid_levels(items):
    result = []
    seen = set()
    for label, value, role in items:
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            continue
        if pd.isna(number):
            continue
        rounded = int(round(number))
        if rounded <= 0 or rounded in seen:
            continue
        seen.add(rounded)
        result.append({"價位": rounded, "定位": label, "區域": role})
    return sorted(result, key=lambda item: item["價位"], reverse=True)


def _near_market(value, current_price, tolerance=0.15):
    try:
        number = float(value or 0)
        current = float(current_price or 0)
    except (TypeError, ValueError):
        return 0.0
    if current <= 0 or not current * (1 - tolerance) <= number <= current * (1 + tolerance):
        return 0.0
    return number


def _daily_mas(raw_kbars):
    daily = charting.prepare_daily_chart(raw_kbars, days=45)
    if daily is None or daily.empty:
        return {}, pd.DataFrame()
    latest = daily.iloc[-1]
    values = {}
    for name in ("MA5", "MA10", "MA20"):
        value = latest.get(name)
        values[name] = 0.0 if value is None or pd.isna(value) else float(value)
    return values, daily


def _scenario_data(current_price, tech_data, public_data, score):
    resistance = float(tech_data.get("上方壓力") or 0)
    support = float(tech_data.get("下方支撐") or 0)
    atr = max(10.0, float(tech_data.get("ATR") or 50))
    option_levels = public_data.get("option_levels", {})
    call_pressure = float(option_levels.get("call_pressure") or 0)
    put_support = float(option_levels.get("put_support") or 0)
    upper = min([value for value in (resistance, call_pressure) if value > current_price] or [current_price + atr * 2])
    lower = max([value for value in (support, put_support) if 0 < value < current_price] or [current_price - atr * 2])
    middle_low = max(lower, current_price - atr * 0.8)
    middle_high = min(upper, current_price + atr * 0.8)
    return [
        {
            "title": "劇本一｜偏多延續",
            "chance": max(20, min(70, int(score))),
            "tone": "bull",
            "condition": f"站穩 {_price(current_price)} 並突破 {_price(upper)}",
            "plan": f"回踩 {_price(current_price)} 附近守穩再偏多，跌破 {_price(middle_low)} 取消。",
        },
        {
            "title": "劇本二｜區間震盪",
            "chance": max(20, min(60, 70 - abs(int(score) - 50))),
            "tone": "range",
            "condition": f"價格維持 {_price(middle_low)}～{_price(middle_high)}",
            "plan": "靠近支撐觀察、靠近壓力減碼；區間中央不追價。",
        },
        {
            "title": "劇本三｜轉弱下跌",
            "chance": max(10, min(60, 100 - int(score))),
            "tone": "bear",
            "condition": f"跌破 {_price(lower)} 且反彈無法站回",
            "plan": f"多單先退出；空方仍須確認 60 分趨勢，下一支撐看 {_price(lower - atr)}。",
        },
    ]


def _scenario_card(item):
    tone_class = item["tone"]
    st.markdown(
        f"""
        <div class="scenario-card {tone_class}">
          <div class="scenario-title">{item['title']} <span>{item['chance']}%</span></div>
          <div class="scenario-condition">{item['condition']}</div>
          <div class="scenario-plan">{item['plan']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_home_dashboard(context):
    realtime = context["realtime"]
    tech_data = context["tech_data"]
    public_data = context["public_data"]
    trade_plan = context["trade_plan"]
    raw_kbars = context["raw_kbars"]
    current_price = float(context.get("current_price") or 0)
    score = int(context.get("score") or 50)
    market_status = context["market_status"]
    mas, _ = _daily_mas(raw_kbars)

    st.markdown(
        """
        <style>
        .block-container {max-width: 1380px; padding-top: 1.1rem;}
        .map-kicker {color:#d4a72c; font-weight:700; letter-spacing:0; font-size:1.05rem;}
        .map-title {font-size:1.85rem; font-weight:800; margin:.15rem 0 .2rem 0;}
        .map-meta {color:#9ca3af; font-size:.9rem; margin-bottom:.65rem;}
        .scenario-card {border:1px solid #3f4652; padding:14px; min-height:142px; background:#111318;}
        .scenario-card.bull {border-color:#1f7a4d;}
        .scenario-card.range {border-color:#9a6b18;}
        .scenario-card.bear {border-color:#9b2f3d;}
        .scenario-title {font-weight:800; font-size:1.02rem; margin-bottom:9px;}
        .scenario-title span {float:right; color:#d1d5db;}
        .scenario-card.bull .scenario-title {color:#3ddc84;}
        .scenario-card.range .scenario-title {color:#f2c14e;}
        .scenario-card.bear .scenario-title {color:#ff5d73;}
        .scenario-condition {font-weight:650; margin-bottom:8px;}
        .scenario-plan {color:#c4c9d1; line-height:1.5; font-size:.9rem;}
        div[data-testid="stMetric"] {background:#101217; border:1px solid #2d323b; padding:10px 12px;}
        div[data-testid="stMetricLabel"] {color:#aeb4bf;}
        @media (max-width: 700px) {
          .map-title {font-size:1.45rem;}
          .scenario-card {min-height:0; margin-bottom:8px;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    contract = context.get("contract_text") or "無資料"
    delivery = context.get("delivery_text") or "無資料"
    st.markdown('<div class="map-kicker">微型臺指（近月）交易地圖</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="map-title">{market_status.label}｜{context.get("execution_status")}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="map-meta">契約 {contract}｜到期 {delivery}｜訊號 {context.get("latest_bar_text")}｜僅供策略研究與手動下單參考</div>',
        unsafe_allow_html=True,
    )

    q1, q2, q3, q4, q5 = st.columns([1.25, 1, 1, 1, 1])
    q1.metric("最近價格", _price(current_price), f"{context.get('price_delta', 0):+,.0f} 點" if context.get("price_delta") else None)
    q2.metric("買一", _price(realtime.get("bid_price")), f"{int(realtime.get('bid_volume') or 0)} 口")
    q3.metric("賣一", _price(realtime.get("ask_price")), f"{int(realtime.get('ask_volume') or 0)} 口")
    q4.metric("策略評分", score, context.get("label"))
    q5.metric("方向", trade_plan.get("title", "觀望"))

    chart_col, level_col = st.columns([1.75, 1], gap="medium")
    with chart_col:
        st.subheader("日 K 趨勢")
        fig = charting.build_daily_chart(raw_kbars, days=35)
        if fig is not None:
            st.plotly_chart(fig, width="stretch", config=charting.PLOT_CONFIG)
        else:
            st.info("日 K 資料不足。")

    option_levels = public_data.get("option_levels", {})
    levels = _valid_levels(
        [
            ("Call 壓力", _near_market(option_levels.get("call_pressure"), current_price), "壓力"),
            ("近期上方壓力", tech_data.get("上方壓力"), "壓力"),
            ("MA10 多空分界", mas.get("MA10"), "分界"),
            ("目前價格", current_price, "現價"),
            ("MA20 第一支撐", mas.get("MA20"), "支撐"),
            ("Put 支撐", _near_market(option_levels.get("put_support"), current_price), "支撐"),
            ("近期下方支撐", tech_data.get("下方支撐"), "支撐"),
        ]
    )
    with level_col:
        st.subheader("關鍵價位")
        if levels:
            st.dataframe(pd.DataFrame(levels), hide_index=True, width="stretch")
        else:
            st.info("關鍵價位資料不足。")
        st.subheader("建議操作方向")
        st.write(trade_plan.get("summary", "先觀望。"))
        p1, p2, p3 = st.columns(3)
        p1.metric("進場", _price(trade_plan.get("entry_price")))
        p2.metric("停損", _price(trade_plan.get("stop_loss")))
        p3.metric("停利", _price(trade_plan.get("take_profit")))
        st.caption(trade_plan.get("close_rule", "等待完整 15 分 K 確認。"))

    st.subheader("下一交易時段可能走勢劇本")
    scenarios = _scenario_data(current_price, tech_data, public_data, score)
    scenario_cols = st.columns(3, gap="medium")
    for column, scenario in zip(scenario_cols, scenarios):
        with column:
            _scenario_card(scenario)

    focus_col, strategy_col = st.columns([1.25, 1], gap="medium")
    with focus_col:
        st.subheader("交易重點")
        checklist = [
            f"15 分趨勢：{tech_data.get('15分趨勢文字', '未知')}；60 分趨勢：{tech_data.get('60分趨勢文字', '未知')}",
            f"站上 MA10 {_price(mas.get('MA10'))} 才有利短線轉強",
            f"上方壓力 {_price(tech_data.get('上方壓力'))}；下方支撐 {_price(tech_data.get('下方支撐'))}",
            f"每口最大預估風險 {_money(context.get('max_loss_per_contract'))}；來回成本 {_money(context.get('estimated_cost'))}",
        ]
        for item in checklist:
            st.write(f"✓ {item}")
        for item in context.get("risk_reasons", [])[:3]:
            st.warning(item)

    with strategy_col:
        st.subheader("主要判斷")
        for item in context.get("plain_reasons", [])[:3]:
            st.write(f"• {item}")
        mtx = public_data.get("mtx_net", {})
        pc = public_data.get("pc_ratio", {})
        st.caption(
            f"小台法人多空比：{float(mtx.get('long_short_ratio') or 0):.2f}%｜"
            f"選擇權未平倉 P/C：{float(pc.get('oi_ratio') or 0):.2f}%｜"
            f"風險環境：{tech_data.get('風險環境', '未知')}"
        )
        confirmation = context.get("five_minute_confirmation", {})
        if context.get("require_5m_confirmation"):
            if confirmation.get("confirmed"):
                st.success(
                    f"5 分確認通過｜{confirmation.get('bar_time') or '無時間'}｜"
                    f"評分 {confirmation.get('score', 50)}"
                )
            else:
                st.warning(
                    f"5 分尚未確認｜{confirmation.get('status', '等待資料')}｜"
                    + "；".join(confirmation.get("reasons") or [])
                )
        st.info(
            f"綜合判斷：{context.get('label')}（{score} 分）。"
            "開盤跳空時原價位可能失效，請等待第一根完整 15 分 K 確認。"
        )

    st.caption(
        f"今日風控：{context.get('risk_daily_trades', 0)}/3 筆｜"
        f"已實現 {_money(context.get('risk_daily_pnl'))}｜"
        f"連虧 {context.get('risk_consecutive_losses', 0)}/2｜"
        f"API 更新：{context.get('freshness_label')}"
    )
