from pathlib import Path

import pandas as pd

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None


WIDTH = 1200
HEIGHT = 1680
BG = "#07101a"
PANEL = "#f7f4ea"
PANEL_ALT = "#eef2f4"
INK = "#14202b"
MUTED = "#5d6873"
LINE = "#273748"
GOLD = "#c79526"
UP = "#d9363e"
DOWN = "#16884c"
BLUE = "#2385a8"
ORANGE = "#dd7f24"


def _font(size, bold=False):
    candidates = [
        "C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _num(value, default=0.0):
    try:
        number = float(value or default)
        return float(default) if pd.isna(number) else number
    except (TypeError, ValueError):
        return float(default)


def _fmt(value, suffix=""):
    number = _num(value)
    return f"{number:,.0f}{suffix}" if number else "--"


def _signed(value):
    number = _num(value)
    return f"{number:+,.0f}" if number else "--"


def _panel(draw, box, fill=PANEL, outline=LINE, width=2):
    draw.rounded_rectangle(box, radius=5, fill=fill, outline=outline, width=width)


def _text(draw, xy, value, size=22, fill=INK, bold=False, anchor=None):
    draw.text(xy, str(value), font=_font(size, bold), fill=fill, anchor=anchor)


def _wrap(draw, text, width_px, font, max_lines=None):
    lines = []
    current = ""
    for char in str(text):
        candidate = current + char
        if draw.textlength(candidate, font=font) > width_px and current:
            lines.append(current)
            current = char
            if max_lines and len(lines) >= max_lines:
                break
        else:
            current = candidate
    if current and (not max_lines or len(lines) < max_lines):
        lines.append(current)
    return lines


def _daily_bars(bars, days=35):
    if bars is None or bars.empty or "ts" not in bars.columns:
        return pd.DataFrame()
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(bars.columns):
        return pd.DataFrame()
    frame = bars.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce")
    daily = (
        frame.dropna(subset=["ts"])
        .set_index("ts")
        .resample("1D")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna()
    )
    daily["MA5"] = daily["Close"].rolling(5).mean()
    daily["MA10"] = daily["Close"].rolling(10).mean()
    daily["MA20"] = daily["Close"].rolling(20).mean()
    low9 = daily["Low"].rolling(9).min()
    high9 = daily["High"].rolling(9).max()
    rsv = (daily["Close"] - low9) / (high9 - low9).replace(0, pd.NA) * 100
    daily["K"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    daily["D"] = daily["K"].ewm(alpha=1 / 3, adjust=False).mean()
    return daily.tail(days).reset_index()


def _draw_title(draw, briefing):
    title = briefing.get("report_title") or f"微型臺指 盤前整合分析｜{briefing.get('session_label')}"
    _text(draw, (18, 18), title, 34, "#f4f6f8", True)
    _text(draw, (WIDTH - 18, 24), f"資料截止 {briefing.get('last_bar_time') or '--'}", 18, "#cbd3da", False, "ra")
    _text(draw, (WIDTH - 18, 53), "歷史相似日模型每次盤前重新計算", 16, "#e2b84c", True, "ra")


def _summary_tiles(draw, briefing):
    items = [
        ("最近成交", _fmt(briefing.get("last_price")), f"買 {_fmt(briefing.get('bid_price'))}｜賣 {_fmt(briefing.get('ask_price'))}"),
        ("成交量", _fmt(briefing.get("total_volume")), "永豐 snapshot"),
        ("法人多空比", f"{_num(briefing.get('mtx_long_short_ratio')):.2f}%" if briefing.get("mtx_long_short_ratio") is not None else "--", "小台三大法人"),
        ("小台淨部位", _signed(briefing.get("mtx_net_oi")), "口數"),
        ("外資未平倉", _signed(briefing.get("foreign_oi")), "口數"),
        ("投信未平倉", _signed(briefing.get("investment_trust_oi")), "口數"),
        ("選擇權 P/C", f"{_num(briefing.get('pc_oi_ratio')):.2f}%" if briefing.get("pc_oi_ratio") is not None else "--", "未平倉比"),
    ]
    left = 12
    top = 78
    gap = 5
    width = (WIDTH - 24 - gap * (len(items) - 1)) / len(items)
    for index, (label, value, note) in enumerate(items):
        x0 = left + index * (width + gap)
        _panel(draw, (x0, top, x0 + width, 178))
        _text(draw, (x0 + width / 2, top + 12), label, 16, INK, True, "ma")
        value_color = DOWN if str(value).startswith("-") else UP if str(value).startswith("+") else INK
        _text(draw, (x0 + width / 2, top + 43), value, 25, value_color, True, "ma")
        _text(draw, (x0 + width / 2, top + 77), note, 13, MUTED, False, "ma")


def _draw_candle_chart(draw, box, daily):
    x0, y0, x1, y1 = box
    _panel(draw, box)
    _text(draw, ((x0 + x1) / 2, y0 + 15), "微型臺指日 K｜均線與成交量", 22, INK, True, "ma")
    if daily.empty:
        _text(draw, ((x0 + x1) / 2, (y0 + y1) / 2), "K 線資料不足", 25, MUTED, True, "mm")
        return
    top = y0 + 58
    volume_top = y1 - 100
    bottom = volume_top - 12
    price_min = float(daily["Low"].min())
    price_max = float(daily["High"].max())
    margin = max(10.0, (price_max - price_min) * 0.08)
    price_min -= margin
    price_max += margin
    span = max(1.0, price_max - price_min)
    step = (x1 - x0 - 72) / max(1, len(daily))
    candle_width = max(3, min(12, step * 0.58))

    def py(value):
        return bottom - (float(value) - price_min) / span * (bottom - top)

    for index in range(5):
        y = top + (bottom - top) * index / 4
        draw.line((x0 + 48, y, x1 - 12, y), fill="#ccd2d5", width=1)
        _text(draw, (x0 + 7, y), f"{price_max - span * index / 4:,.0f}", 12, MUTED, anchor="lm")
    xs = []
    for index, row in daily.iterrows():
        x = x0 + 52 + step * (index + 0.5)
        xs.append(x)
        color = UP if row["Close"] >= row["Open"] else DOWN
        draw.line((x, py(row["High"]), x, py(row["Low"])), fill=color, width=2)
        body_top = min(py(row["Open"]), py(row["Close"]))
        body_bottom = max(py(row["Open"]), py(row["Close"]))
        draw.rectangle((x - candle_width / 2, body_top, x + candle_width / 2, max(body_top + 2, body_bottom)), fill=color)
    for column, color in (("MA5", GOLD), ("MA10", ORANGE), ("MA20", BLUE)):
        points = [(x, py(value)) for x, value in zip(xs, daily[column]) if not pd.isna(value)]
        if len(points) >= 2:
            draw.line(points, fill=color, width=3)
    max_volume = max(1.0, float(daily["Volume"].max()))
    for x, (_, row) in zip(xs, daily.iterrows()):
        height = float(row["Volume"]) / max_volume * 67
        color = UP if row["Close"] >= row["Open"] else DOWN
        draw.rectangle((x - candle_width / 2, y1 - 14 - height, x + candle_width / 2, y1 - 14), fill=color)
    latest = daily.iloc[-1]
    legends = (("MA5", GOLD), ("MA10", ORANGE), ("MA20", BLUE))
    x = x0 + 195
    for name, color in legends:
        arrow = "↑" if len(daily) > 1 and latest[name] >= daily[name].iloc[-2] else "↓"
        _text(draw, (x, y0 + 42), f"{name} {_fmt(latest[name])}{arrow}", 15, color, True)
        x += 145


def _draw_model_panel(draw, box, briefing, daily):
    x0, y0, x1, y1 = box
    _panel(draw, box)
    model = briefing.get("scenario_model") or {}
    probabilities = model.get("probabilities") or {}
    _text(draw, ((x0 + x1) / 2, y0 + 15), "三劇本歷史學習模型", 22, INK, True, "ma")
    _text(draw, (x0 + 18, y0 + 48), model.get("method") or "--", 15, MUTED)
    rows = (("偏多延續", probabilities.get("bull", 0), UP), ("區間震盪", probabilities.get("range", 0), GOLD), ("轉弱下跌", probabilities.get("bear", 0), DOWN))
    for index, (label, probability, color) in enumerate(rows):
        y = y0 + 83 + index * 48
        _text(draw, (x0 + 18, y), label, 17, INK, True)
        draw.rounded_rectangle((x0 + 145, y + 2, x1 - 62, y + 22), radius=9, fill="#dce2e4")
        bar_width = max(2, (x1 - x0 - 225) * float(probability) / 100)
        draw.rounded_rectangle((x0 + 145, y + 2, x0 + 145 + bar_width, y + 22), radius=9, fill=color)
        _text(draw, (x1 - 18, y), f"{probability}%", 18, color, True, "ra")
    accuracy = _num(model.get("walk_forward_accuracy"))
    _text(draw, (x0 + 18, y0 + 235), f"歷史樣本 {model.get('sample_size', 0)} 日｜相似日 {model.get('neighbour_count', 0)} 日", 16, INK, True)
    _text(draw, (x0 + 18, y0 + 263), f"Walk-forward 命中 {accuracy:.1f}%（{model.get('walk_forward_tests', 0)} 次）", 16, INK)
    _text(draw, (x0 + 18, y0 + 291), f"模型信心：{model.get('confidence', '低')}｜命中率只供校準，不代表獲利率", 15, MUTED)
    if not daily.empty:
        latest = daily.iloc[-1]
        k_value = _num(latest.get("K"))
        d_value = _num(latest.get("D"))
        kd_label = "黃金交叉" if k_value > d_value else "死亡交叉"
        kd_color = UP if k_value > d_value else DOWN
        _text(draw, (x0 + 18, y0 + 326), f"日 KD：K {k_value:.1f}｜D {d_value:.1f}｜{kd_label}", 17, kd_color, True)
        plot = daily.dropna(subset=["K", "D"]).tail(12)
        if len(plot) >= 2:
            left, top, right, bottom = x0 + 18, y0 + 358, x1 - 18, y1 - 18
            for level in (20, 50, 80):
                y = bottom - level / 100 * (bottom - top)
                draw.line((left, y, right, y), fill="#d2d8da", width=1)
            for column, color in (("K", GOLD), ("D", BLUE)):
                points = []
                for idx, value in enumerate(plot[column]):
                    px = left + idx / (len(plot) - 1) * (right - left)
                    py = bottom - float(value) / 100 * (bottom - top)
                    points.append((px, py))
                draw.line(points, fill=color, width=3)


def _draw_institutional(draw, box, briefing):
    x0, y0, x1, y1 = box
    _panel(draw, box)
    _text(draw, ((x0 + x1) / 2, y0 + 14), "法人與選擇權籌碼", 21, INK, True, "ma")
    rows = [
        ("小台三法人淨部位", briefing.get("mtx_net_oi"), "口"),
        ("小台法人多空比", briefing.get("mtx_long_short_ratio"), "%"),
        ("外資臺指期淨部位", briefing.get("foreign_oi"), "口"),
        ("投信臺指期淨部位", briefing.get("investment_trust_oi"), "口"),
        ("自營商臺指期淨部位", briefing.get("dealer_oi"), "口"),
        ("選擇權未平倉 P/C", briefing.get("pc_oi_ratio"), "%"),
    ]
    for index, (label, value, suffix) in enumerate(rows):
        y = y0 + 49 + index * 34
        draw.line((x0 + 14, y + 25, x1 - 14, y + 25), fill="#d4d9dc", width=1)
        _text(draw, (x0 + 20, y), label, 15, INK)
        if value is None:
            rendered = "--"
            color = MUTED
        elif suffix == "%":
            rendered = f"{_num(value):.2f}%"
            color = INK
        else:
            rendered = f"{_num(value):+,.0f} 口"
            color = UP if _num(value) > 0 else DOWN if _num(value) < 0 else INK
        _text(draw, (x1 - 18, y), rendered, 16, color, True, "ra")
    errors = briefing.get("public_errors") or []
    note = "資料來源：TAIFEX 公開資料" if not errors else "部分公開資料未接回，缺值以 -- 顯示"
    _text(draw, (x0 + 18, y1 - 25), note, 13, MUTED)


def _draw_factor_row(draw, box, briefing, daily):
    x0, y0, x1, y1 = box
    _panel(draw, box)
    _text(draw, (x0 + 18, y0 + 12), "多空力量評估", 20, INK, True)
    latest = daily.iloc[-1] if not daily.empty else None
    price = _num(briefing.get("last_price"))
    factors = [
        ("15分趨勢", briefing.get("trend_15m", "--")),
        ("60分趨勢", briefing.get("trend_60m", "--")),
        ("ADX", f"{_num(briefing.get('adx')):.1f}"),
        ("量比", f"{_num(briefing.get('volume_ratio')):.2f}"),
        ("MA5", "上方" if latest is not None and price >= _num(latest.get("MA5")) else "下方"),
        ("法人比", f"{_num(briefing.get('mtx_long_short_ratio')):.1f}%" if briefing.get("mtx_long_short_ratio") is not None else "--"),
        ("策略分數", str(briefing.get("score", "--"))),
    ]
    cell_width = (x1 - x0 - 30) / len(factors)
    for index, (label, value) in enumerate(factors):
        cx = x0 + 15 + cell_width * (index + 0.5)
        if index:
            draw.line((x0 + 15 + cell_width * index, y0 + 44, x0 + 15 + cell_width * index, y1 - 12), fill="#c9d0d3", width=1)
        _text(draw, (cx, y0 + 48), label, 14, MUTED, True, "ma")
        _text(draw, (cx, y0 + 79), value, 18, INK, True, "ma")


def _scenario_specs(briefing, daily):
    model = briefing.get("scenario_model") or {}
    probabilities = model.get("probabilities") or {}
    current = _num(briefing.get("last_price"))
    latest = daily.iloc[-1] if not daily.empty else None
    ma20 = _num(latest.get("MA20")) if latest is not None else 0
    typical_up = _num(model.get("typical_up_move")) or max(40, current * 0.002)
    typical_down = _num(model.get("typical_down_move")) or max(40, current * 0.002)
    typical_range = _num(model.get("typical_range")) or min(typical_up, typical_down) * 0.55
    bull_trigger = max(current, _num(latest.get("MA5")) if latest is not None else current)
    bear_trigger = min(current - typical_range * 0.45, ma20 or current)
    return [
        {
            "title": "劇本一｜偏多延續",
            "probability": probabilities.get("bull", 0),
            "color": UP,
            "kind": "bull",
            "condition": f"站穩 {_fmt(bull_trigger)} 且 15 分量價續強",
            "target": f"相似日盤中上緣距離約 +{_fmt(typical_up)} 點",
            "plan": "回踩不破支撐再偏多；開高過遠不追價。",
        },
        {
            "title": "劇本二｜區間震盪",
            "probability": probabilities.get("range", 0),
            "color": GOLD,
            "kind": "range",
            "condition": f"約 {_fmt(current-typical_range)}～{_fmt(current+typical_range)} 往返",
            "target": "量縮、ADX 未擴張，方向訊號容易失效",
            "plan": "區間中央不交易；只在邊界等確認。",
        },
        {
            "title": "劇本三｜轉弱下跌",
            "probability": probabilities.get("bear", 0),
            "color": DOWN,
            "kind": "bear",
            "condition": f"跌破 {_fmt(bear_trigger)} 且反彈無法站回",
            "target": f"相似日盤中下緣距離約 -{_fmt(typical_down)} 點",
            "plan": "多單先退；放空仍須 60 分趨勢同向。",
        },
    ]


def _draw_path(draw, box, kind, color):
    x0, y0, x1, y1 = box
    draw.line((x0, (y0 + y1) / 2, x1, (y0 + y1) / 2), fill="#b9c2c6", width=1)
    if kind == "bull":
        ratios = (0.72, 0.48, 0.60, 0.38, 0.45, 0.18)
    elif kind == "bear":
        ratios = (0.28, 0.47, 0.39, 0.64, 0.55, 0.83)
    else:
        ratios = (0.50, 0.34, 0.62, 0.39, 0.58, 0.46)
    points = []
    for index, ratio in enumerate(ratios):
        x = x0 + index / (len(ratios) - 1) * (x1 - x0)
        y = y0 + ratio * (y1 - y0)
        points.append((x, y))
    draw.line(points, fill=color, width=4)


def _draw_scenarios(draw, box, briefing, daily):
    x0, y0, x1, y1 = box
    gap = 8
    width = (x1 - x0 - gap * 2) / 3
    for index, spec in enumerate(_scenario_specs(briefing, daily)):
        left = x0 + index * (width + gap)
        _panel(draw, (left, y0, left + width, y1), outline=spec["color"], width=3)
        _text(draw, (left + 14, y0 + 13), spec["title"], 18, spec["color"], True)
        _text(draw, (left + width - 14, y0 + 13), f"{spec['probability']}%", 21, spec["color"], True, "ra")
        _draw_path(draw, (left + 16, y0 + 51, left + width - 16, y0 + 124), spec["kind"], spec["color"])
        _text(draw, (left + 16, y0 + 130), "路徑示意，非價格預測", 12, MUTED)
        font = _font(15)
        lines = [spec["condition"], spec["target"], spec["plan"]]
        y = y0 + 158
        for line in lines:
            for wrapped in _wrap(draw, line, width - 30, font, max_lines=2):
                draw.text((left + 15, y), f"• {wrapped}", font=font, fill=INK)
                y += 22
            y += 2


def _draw_levels(draw, box, briefing, daily):
    x0, y0, x1, y1 = box
    _panel(draw, box)
    _text(draw, ((x0 + x1) / 2, y0 + 14), "關鍵價位（由高到低）", 20, INK, True, "ma")
    latest = daily.iloc[-1] if not daily.empty else None
    current = _num(briefing.get("last_price"))

    def near(value):
        number = _num(value)
        return number if current and current * 0.85 <= number <= current * 1.15 else 0

    values = [
        (near(briefing.get("call_pressure")), "Call 壓力", UP),
        (_num(latest.get("MA5")) if latest is not None else 0, "MA5", GOLD),
        (_num(latest.get("MA10")) if latest is not None else 0, "MA10", ORANGE),
        (current, "最近成交", INK),
        (_num(latest.get("MA20")) if latest is not None else 0, "MA20", BLUE),
        (near(briefing.get("put_support")), "Put 支撐", DOWN),
    ]
    seen = set()
    rows = []
    for value, label, color in sorted(values, key=lambda item: item[0], reverse=True):
        rounded = int(round(value))
        if rounded > 0 and rounded not in seen:
            seen.add(rounded)
            rows.append((rounded, label, color))
    for index, (value, label, color) in enumerate(rows[:6]):
        y = y0 + 48 + index * 32
        draw.line((x0 + 12, y + 24, x1 - 12, y + 24), fill="#d3d8da", width=1)
        _text(draw, (x0 + 18, y), f"{value:,.0f}", 17, color, True)
        _text(draw, (x0 + 145, y), label, 15, INK)


def _draw_open_checks(draw, box, briefing):
    x0, y0, x1, y1 = box
    _panel(draw, box)
    _text(draw, ((x0 + x1) / 2, y0 + 14), "開盤後確認順序", 20, INK, True, "ma")
    checks = [
        "1｜先確認是否跳空超過相似日常態，過遠則原價位作廢",
        "2｜等待第一根完整 15 分 K，不用開盤瞬間追價",
        f"3｜15 分 {briefing.get('trend_15m')}、60 分 {briefing.get('trend_60m')}，方向一致才提高權重",
        "4｜成交量與 ADX 同步擴張才視為突破，不一致視為假突破",
        "5｜進場前先算每口最大損失；不符合風控就維持觀望",
    ]
    font = _font(15)
    y = y0 + 53
    for check in checks:
        for line in _wrap(draw, check, x1 - x0 - 34, font, max_lines=2):
            draw.text((x0 + 18, y), line, font=font, fill=INK)
            y += 23
        y += 5


def _draw_bottom(draw, box, briefing):
    x0, y0, x1, y1 = box
    left_end = x0 + (x1 - x0) * 0.58
    _panel(draw, (x0, y0, left_end - 5, y1))
    _panel(draw, (left_end + 5, y0, x1, y1), outline=GOLD, width=3)
    model = briefing.get("scenario_model") or {}
    probabilities = model.get("probabilities") or {}
    dominant = max(probabilities, key=probabilities.get) if probabilities else "range"
    dominant_label = {"bull": "偏多延續", "range": "區間震盪", "bear": "轉弱下跌"}.get(dominant, "觀望")
    _text(draw, (x0 + 18, y0 + 13), "綜合結論", 21, INK, True)
    _text(draw, (x0 + 18, y0 + 48), f"主劇本：{dominant_label} {probabilities.get(dominant, 0)}%｜模型信心 {model.get('confidence', '低')}", 20, UP if dominant == "bull" else DOWN if dominant == "bear" else GOLD, True)
    explanations = [
        f"策略分數 {briefing.get('score')}｜{briefing.get('label')}｜風險環境 {briefing.get('risk_environment')}",
        f"歷史相似日 {model.get('sample_size', 0)} 日；Walk-forward 命中 {_num(model.get('walk_forward_accuracy')):.1f}%",
        "機率會隨新增 K 線每日重算，不沿用昨日固定比例。",
    ]
    for index, line in enumerate(explanations):
        _text(draw, (x0 + 20, y0 + 84 + index * 29), f"- {line}", 16, INK)

    hourly = briefing.get("report_mode") == "hourly"
    _text(draw, (left_end + 22, y0 + 13), "操作卡（每小時更新）" if hourly else "操作卡（盤前預備）", 21, INK, True)
    _text(draw, (left_end + 22, y0 + 49), briefing.get("direction") or "觀望", 27, GOLD, True)
    if briefing.get("entry_price"):
        details = (
            f"參考 {_fmt(briefing.get('entry_price'))}｜停損 {_fmt(briefing.get('stop_loss_price'))}｜"
            f"停利 {_fmt(briefing.get('take_profit_price'))}"
        )
        _text(draw, (left_end + 22, y0 + 87), details, 16, INK)
        _text(draw, (left_end + 22, y0 + 117), f"1 口預估風險約 NT$ {_num(briefing.get('estimated_risk')):,.0f}", 17, DOWN, True)
    else:
        _text(draw, (left_end + 22, y0 + 91), "開盤後等待完整 15 分 K 再更新進出價", 17, INK)
    disclaimer = "盤中分析，不是直接下單指令" if hourly else "盤前計畫，不是直接下單指令"
    _text(draw, (left_end + 22, y1 - 31), disclaimer, 15, MUTED, True)


def render_preopen_briefing_image(briefing, bars, output_path="data/preopen_briefing_latest.png"):
    if Image is None:
        raise RuntimeError("Pillow 尚未安裝，無法產生 Telegram 圖卡。")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    daily = _daily_bars(bars)

    _draw_title(draw, briefing)
    _summary_tiles(draw, briefing)
    _draw_candle_chart(draw, (12, 190, 720, 650), daily)
    _draw_model_panel(draw, (728, 190, 1188, 650), briefing, daily)
    _draw_institutional(draw, (12, 662, 460, 1040), briefing)
    _draw_factor_row(draw, (468, 662, 1188, 790), briefing, daily)
    _draw_levels(draw, (468, 798, 790, 1040), briefing, daily)
    _draw_open_checks(draw, (798, 798, 1188, 1040), briefing)
    _draw_scenarios(draw, (12, 1052, 1188, 1355), briefing, daily)
    _draw_bottom(draw, (12, 1367, 1188, 1638), briefing)
    _text(draw, (WIDTH - 14, HEIGHT - 13), "資料來源：Sinopac Shioaji、TAIFEX｜僅供策略研究與手動交易參考", 14, "#cbd3da", False, "rb")

    image.save(output_path, format="PNG", optimize=True)
    return str(output_path)
