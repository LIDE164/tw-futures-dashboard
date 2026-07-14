from pathlib import Path

import pandas as pd

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = None


WIDTH, HEIGHT = 1200, 1740
NAVY = "#071a35"
NAVY_2 = "#102b4e"
PAPER = "#f8f7f0"
PAPER_2 = "#eef3f5"
INK = "#17212b"
MUTED = "#66717d"
GRID = "#cbd4da"
UP = "#d8343a"
DOWN = "#16864e"
GOLD = "#c49320"
BLUE = "#2383a5"
ORANGE = "#dc7b25"


def _font(size, bold=False):
    candidates = [
        "C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _n(value, default=0.0):
    try:
        number = float(value)
        return default if pd.isna(number) else number
    except (TypeError, ValueError):
        return default


def _fmt(value, digits=0, missing="--"):
    if value is None or value == "":
        return missing
    return f"{_n(value):,.{digits}f}"


def _signed(value, suffix=""):
    if value is None:
        return "--"
    return f"{_n(value):+,.0f}{suffix}"


def _text(draw, xy, text, size=18, fill=INK, bold=False, anchor=None):
    draw.text(xy, str(text), font=_font(size, bold), fill=fill, anchor=anchor)


def _panel(draw, box, fill=PAPER, width=3):
    draw.rectangle(box, fill=fill, outline=NAVY, width=width)


def _title(draw, box, text, size=20):
    x0, y0, x1, _ = box
    _text(draw, ((x0 + x1) / 2, y0 + 8), text, size, NAVY, True, "ma")


def _wrap(draw, text, width, size=15, max_lines=3):
    font = _font(size)
    lines, current = [], ""
    for char in str(text):
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines


def _daily(bars, days=35):
    if bars is None or bars.empty or "ts" not in bars.columns:
        return pd.DataFrame()
    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(bars.columns):
        return pd.DataFrame()
    frame = bars.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce")
    daily = frame.dropna(subset=["ts"]).set_index("ts").resample("1D").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    for period in (5, 10, 20):
        daily[f"MA{period}"] = daily["Close"].rolling(period).mean()
    low9, high9 = daily["Low"].rolling(9).min(), daily["High"].rolling(9).max()
    rsv = (daily["Close"] - low9) / (high9 - low9).replace(0, pd.NA) * 100
    daily["K"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    daily["D"] = daily["K"].ewm(alpha=1 / 3, adjust=False).mean()
    return daily.tail(days).reset_index()


def _header(draw, briefing):
    draw.rectangle((0, 0, WIDTH, 68), fill=NAVY)
    session = briefing.get("session_label") or "盤前"
    date = str(briefing.get("session_key") or "").split(":")[0].replace("-", "/")
    _text(draw, (18, 14), f"微型臺指 {session}盤前整合分析｜{date}", 30, "white", True)
    _text(draw, (815, 17), "綜合資料分析", 21, "#e4bf55", True)
    _text(draw, (1182, 19), f"資料截止：{briefing.get('last_bar_time') or '--'}", 15, "white", False, "ra")


def _summary(draw, briefing):
    items = [
        ("微指最近價", _fmt(briefing.get("last_price")), f"{_signed(briefing.get('price_change'))} ({_n(briefing.get('price_change_pct')):+.2f}%)"),
        ("成交量", _fmt(briefing.get("total_volume")), "永豐即時 snapshot"),
        ("小台法人多空比", f"{_fmt(briefing.get('mtx_long_short_ratio'), 2)}%", "非散戶身分資料"),
        ("三法人期貨淨部位", _signed(briefing.get("institutional_total_oi"), "口"), briefing.get("institutional_date") or "--"),
        ("外資未平倉", _signed(briefing.get("foreign_oi"), "口"), "TAIFEX 盤後"),
        ("投信未平倉", _signed(briefing.get("investment_trust_oi"), "口"), "TAIFEX 盤後"),
        ("前十大交易人", _signed(briefing.get("large_trader_net_oi"), "口"), "TX等值近月"),
    ]
    y0, y1, gap = 76, 188, 3
    width = (WIDTH - 24 - gap * 6) / 7
    for index, (label, value, note) in enumerate(items):
        x0 = 12 + index * (width + gap)
        _panel(draw, (x0, y0, x0 + width, y1))
        _text(draw, (x0 + width / 2, y0 + 13), label, 15, NAVY, True, "ma")
        color = DOWN if str(value).startswith("-") else UP if str(value).startswith("+") else INK
        _text(draw, (x0 + width / 2, y0 + 47), value, 24, color, True, "ma")
        _text(draw, (x0 + width / 2, y0 + 82), note, 12, MUTED, False, "ma")


def _candles(draw, box, daily):
    _panel(draw, box)
    _title(draw, box, "微型臺指日 K 線圖")
    x0, y0, x1, y1 = box
    if daily.empty:
        _text(draw, ((x0 + x1) / 2, (y0 + y1) / 2), "永豐 K 線資料不足", 22, MUTED, True, "mm")
        return
    latest = daily.iloc[-1]
    legend = [("MA5", GOLD), ("MA10", ORANGE), ("MA20", BLUE)]
    lx = x0 + 55
    for name, color in legend:
        arrow = "↑" if len(daily) > 1 and _n(latest[name]) >= _n(daily[name].iloc[-2]) else "↓"
        _text(draw, (lx, y0 + 38), f"{name} {_fmt(latest[name])}{arrow}", 14, color, True)
        lx += 130
    top, bottom, volume_top = y0 + 73, y1 - 90, y1 - 76
    pmin, pmax = float(daily.Low.min()), float(daily.High.max())
    margin = max(10, (pmax - pmin) * .08)
    pmin, pmax = pmin - margin, pmax + margin
    span = max(1, pmax - pmin)
    step = (x1 - x0 - 64) / len(daily)
    body = max(3, min(11, step * .55))
    py = lambda value: bottom - (float(value) - pmin) / span * (bottom - top)
    for index in range(5):
        y = top + index * (bottom - top) / 4
        draw.line((x0 + 45, y, x1 - 10, y), fill=GRID, width=1)
        _text(draw, (x0 + 5, y), f"{pmax - span * index / 4:,.0f}", 11, MUTED, anchor="lm")
    xs = []
    for index, row in daily.iterrows():
        x = x0 + 48 + step * (index + .5)
        xs.append(x)
        color = UP if row.Close >= row.Open else DOWN
        draw.line((x, py(row.High), x, py(row.Low)), fill=color, width=2)
        a, b = sorted((py(row.Open), py(row.Close)))
        draw.rectangle((x - body / 2, a, x + body / 2, max(a + 2, b)), fill=color)
    for name, color in legend:
        points = [(x, py(value)) for x, value in zip(xs, daily[name]) if not pd.isna(value)]
        if len(points) > 1:
            draw.line(points, fill=color, width=2)
    _text(draw, (x0 + 10, volume_top - 16), "成交量", 14, MUTED, True)
    vmax = max(1, float(daily.Volume.max()))
    for x, (_, row) in zip(xs, daily.iterrows()):
        height = float(row.Volume) / vmax * 58
        color = UP if row.Close >= row.Open else DOWN
        draw.rectangle((x - body / 2, y1 - 12 - height, x + body / 2, y1 - 12), fill=color)


def _kd_ma(draw, box, daily):
    _panel(draw, box)
    _title(draw, box, "KD 指標")
    x0, y0, x1, y1 = box
    latest = daily.iloc[-1] if not daily.empty else None
    if latest is None:
        _text(draw, ((x0 + x1) / 2, y0 + 100), "資料不足", 18, MUTED, True, "mm")
        return
    _text(draw, (x0 + 14, y0 + 39), f"K {_n(latest.K):.1f}｜D {_n(latest.D):.1f}", 15, NAVY, True)
    plot = daily.dropna(subset=["K", "D"]).tail(14)
    left, top, right, bottom = x0 + 18, y0 + 69, x1 - 18, y0 + 205
    for level in (20, 50, 80):
        y = bottom - level / 100 * (bottom - top)
        draw.line((left, y, right, y), fill=GRID, width=1)
    if len(plot) > 1:
        for column, color in (("K", GOLD), ("D", BLUE)):
            points = [(left + i / (len(plot) - 1) * (right - left), bottom - _n(v) / 100 * (bottom - top)) for i, v in enumerate(plot[column])]
            draw.line(points, fill=color, width=3)
    label = "KD 黃金交叉" if _n(latest.K) >= _n(latest.D) else "KD 死亡交叉"
    _text(draw, ((x0 + x1) / 2, y0 + 225), label, 17, UP if _n(latest.K) >= _n(latest.D) else DOWN, True, "ma")
    draw.line((x0 + 8, y0 + 258, x1 - 8, y0 + 258), fill=NAVY, width=2)
    _text(draw, ((x0 + x1) / 2, y0 + 271), "均線結構", 19, NAVY, True, "ma")
    price = _n(latest.Close)
    for i, name in enumerate(("MA5", "MA10", "MA20")):
        direction = "上彎" if len(daily) > 1 and _n(latest[name]) >= _n(daily[name].iloc[-2]) else "下彎"
        _text(draw, (x0 + 28, y0 + 309 + i * 31), name, 16, INK, True)
        _text(draw, (x1 - 25, y0 + 309 + i * 31), f"{direction}｜價在{'上' if price >= _n(latest[name]) else '下'}", 15, UP if direction == "上彎" else DOWN, True, "ra")
    structure = "多頭排列" if _n(latest.MA5) > _n(latest.MA10) > _n(latest.MA20) else "空頭排列" if _n(latest.MA5) < _n(latest.MA10) < _n(latest.MA20) else "均線糾結"
    _text(draw, ((x0 + x1) / 2, y1 - 30), structure, 18, GOLD, True, "ma")


def _ratio_panel(draw, box, briefing, daily):
    _panel(draw, box)
    _title(draw, box, "小台法人多空比｜盤後資料")
    x0, y0, x1, y1 = box
    history = briefing.get("public_history") or []
    chart = [item for item in history if item.get("mtx_ratio") is not None][-12:]
    left, top, right, bottom = x0 + 28, y0 + 55, x1 - 24, y0 + 225
    if chart:
        values = [_n(item.get("mtx_ratio")) for item in chart]
        vmin, vmax = min(values + [90]), max(values + [110])
        span = max(1, vmax - vmin)
        width = (right - left) / max(1, len(chart))
        for i, (item, value) in enumerate(zip(chart, values)):
            x = left + i * width + 4
            y = bottom - (value - vmin) / span * (bottom - top)
            draw.rectangle((x, y, x + max(4, width - 8), bottom), fill=UP if value >= 100 else DOWN)
        _text(draw, (left, top - 18), f"最新 {_fmt(values[-1], 2)}%", 15, NAVY, True)
    else:
        _text(draw, ((left + right) / 2, (top + bottom) / 2), "真實歷史自本版起每日累積", 16, MUTED, True, "mm")
    draw.line((x0 + 10, y0 + 244, x1 - 10, y0 + 244), fill=NAVY, width=2)
    rows = history[-5:] if history else [{"date": briefing.get("institutional_date"), "mtx_ratio": briefing.get("mtx_long_short_ratio")}]
    _text(draw, (x0 + 25, y0 + 257), "日期", 15, NAVY, True)
    _text(draw, (x0 + 165, y0 + 257), "法人多空比", 15, NAVY, True)
    for i, row in enumerate(reversed(rows[-5:])):
        y = y0 + 287 + i * 27
        _text(draw, (x0 + 25, y), row.get("date") or "--", 14, INK)
        _text(draw, (x0 + 170, y), f"{_fmt(row.get('mtx_ratio'), 2)}%", 14, UP if _n(row.get("mtx_ratio")) >= 100 else DOWN, True)
    _text(draw, (x0 + 305, y0 + 265), "解讀", 16, NAVY, True)
    notes = ["僅代表三法人部位比", "不等於散戶真實身分", "需搭配 K 線與量能"]
    for i, note in enumerate(notes):
        _text(draw, (x0 + 300, y0 + 299 + i * 35), f"• {note}", 14, INK)


def _volume_panel(draw, box, briefing):
    _panel(draw, box)
    _title(draw, box, "成交量變化")
    x0, y0, x1, y1 = box
    rows = (briefing.get("daily_volume_history") or [])[-3:]
    vmax = max([_n(row.get("volume")) for row in rows] + [1])
    if not rows:
        _text(draw, ((x0 + x1) / 2, (y0 + y1) / 2), "日成交量資料不足", 18, MUTED, True, "mm")
        return
    bar_w = 68
    for i, row in enumerate(rows):
        cx = x0 + 72 + i * 112
        h = _n(row.get("volume")) / vmax * 145
        draw.rectangle((cx, y1 - 45 - h, cx + bar_w, y1 - 45), fill=UP if i % 2 == 0 else DOWN)
        _text(draw, (cx + bar_w / 2, y1 - 70 - h), _fmt(row.get("volume")), 14, NAVY, True, "ma")
        _text(draw, (cx + bar_w / 2, y1 - 35), row.get("date") or "--", 14, INK, True, "ma")
    latest = _n(rows[-1].get("volume"))
    previous = _n(rows[-2].get("volume")) if len(rows) > 1 else latest
    note = "量能增加" if latest > previous else "量能縮減"
    _text(draw, (x1 - 20, y0 + 53), note, 16, UP if latest > previous else DOWN, True, "ra")
    _text(draw, (x1 - 20, y0 + 84), "量能只確認強弱，不單獨決定方向", 13, MUTED, anchor="ra")


def _line_chart(draw, box, title, values, color=BLUE):
    _panel(draw, box)
    _title(draw, box, title)
    x0, y0, x1, y1 = box
    values = [float(value) for value in values if value is not None]
    if len(values) < 2:
        _text(draw, ((x0 + x1) / 2, (y0 + y1) / 2), "外部行情暫未取得", 18, MUTED, True, "mm")
        return
    left, top, right, bottom = x0 + 40, y0 + 55, x1 - 20, y1 - 35
    vmin, vmax = min(values), max(values)
    margin = max(1, (vmax - vmin) * .08)
    vmin, vmax = vmin - margin, vmax + margin
    points = [(left + i / (len(values) - 1) * (right - left), bottom - (value - vmin) / (vmax - vmin) * (bottom - top)) for i, value in enumerate(values)]
    for i in range(4):
        y = top + i * (bottom - top) / 3
        draw.line((left, y, right, y), fill=GRID, width=1)
    draw.line(points, fill=color, width=3)
    _text(draw, (left, top - 20), f"最新 {_fmt(values[-1], 2)}", 14, NAVY, True)


def _institutional(draw, box, briefing):
    _panel(draw, box)
    _title(draw, box, "法人期貨籌碼（口數）")
    x0, y0, x1, y1 = box
    history = briefing.get("public_history") or []
    previous = history[-2] if len(history) > 1 else {}
    rows = [
        ("外資", briefing.get("foreign_oi"), previous.get("foreign")),
        ("投信", briefing.get("investment_trust_oi"), previous.get("trust")),
        ("自營商", briefing.get("dealer_oi"), previous.get("dealer")),
        ("三法人合計", briefing.get("institutional_total_oi"), previous.get("institutional_total")),
    ]
    _text(draw, (x0 + 18, y0 + 48), "身分", 15, NAVY, True)
    _text(draw, (x0 + 155, y0 + 48), "淨部位", 15, NAVY, True)
    _text(draw, (x1 - 18, y0 + 48), "較前次", 15, NAVY, True, "ra")
    for i, (label, value, prior) in enumerate(rows):
        y = y0 + 82 + i * 42
        draw.line((x0 + 12, y + 27, x1 - 12, y + 27), fill=GRID, width=1)
        _text(draw, (x0 + 18, y), label, 16, INK, True)
        _text(draw, (x0 + 155, y), _signed(value), 16, UP if _n(value) > 0 else DOWN, True)
        delta = None if prior is None or value is None else _n(value) - _n(prior)
        _text(draw, (x1 - 18, y), _signed(delta), 15, MUTED if delta is None else UP if delta > 0 else DOWN, True, "ra")
    _text(draw, (x0 + 16, y1 - 28), f"資料日：{briefing.get('institutional_date') or '--'}｜TAIFEX 盤後", 13, MUTED)


def _factor_row(draw, box, briefing, daily):
    _panel(draw, box)
    _title(draw, box, "多空力量評估")
    x0, y0, x1, y1 = box
    latest = daily.iloc[-1] if not daily.empty else None
    kd = "偏多" if latest is not None and _n(latest.K) >= _n(latest.D) else "偏空"
    values = [
        ("技術面", briefing.get("trend_15m") or "--"),
        ("KD 指標", kd),
        ("成交量", "增" if _n(briefing.get("volume_ratio")) >= 1 else "減"),
        ("60分趨勢", briefing.get("trend_60m") or "--"),
        ("小台法人", "偏多" if _n(briefing.get("mtx_net_oi")) > 0 else "偏空"),
        ("外資籌碼", "偏多" if _n(briefing.get("foreign_oi")) > 0 else "偏空"),
        ("投信籌碼", "偏多" if _n(briefing.get("investment_trust_oi")) > 0 else "偏空"),
        ("整體評分", str(briefing.get("score", "--"))),
    ]
    width = (x1 - x0 - 20) / len(values)
    for i, (label, value) in enumerate(values):
        cx = x0 + 10 + width * (i + .5)
        if i:
            draw.line((x0 + 10 + width * i, y0 + 42, x0 + 10 + width * i, y1 - 10), fill=GRID, width=1)
        _text(draw, (cx, y0 + 52), label, 14, MUTED, True, "ma")
        color = UP if "多" in str(value) or str(value) in ("增", "上") else DOWN if "空" in str(value) or str(value) in ("減", "下") else GOLD
        _text(draw, (cx, y0 + 92), value, 17, color, True, "ma")


def _large_trader(draw, box, briefing):
    _panel(draw, box)
    _title(draw, box, "大額交易人（近月前十大）")
    x0, y0, x1, y1 = box
    rows = [
        ("多方部位", briefing.get("large_trader_long_oi")),
        ("空方部位", briefing.get("large_trader_short_oi")),
        ("多空淨額", briefing.get("large_trader_net_oi")),
        ("全市場 OI", briefing.get("large_trader_market_oi")),
    ]
    for i, (label, value) in enumerate(rows):
        y = y0 + 49 + i * 31
        _text(draw, (x0 + 18, y), label, 15, INK)
        _text(draw, (x1 - 18, y), _fmt(value), 16, UP if label == "多方部位" else DOWN if label == "空方部位" else INK, True, "ra")
    _text(draw, (x0 + 16, y1 - 25), "TX+MTX/4+TMF/20 等值｜TAIFEX", 12, MUTED)


def _scenario_specs(briefing, daily):
    model = briefing.get("scenario_model") or {}
    p = model.get("probabilities") or {}
    price = _n(briefing.get("last_price"))
    up = _n(model.get("typical_up_move"), max(40, price * .002))
    down = _n(model.get("typical_down_move"), max(40, price * .002))
    span = _n(model.get("typical_range"), min(up, down) * .55)
    return [
        ("劇本一｜偏多延續", p.get("bull", 0), UP, "bull", f"站穩 {_fmt(price + span * .2)}，量價同步", f"上緣約 {_fmt(price + up)}"),
        ("劇本二｜區間震盪", p.get("range", 0), GOLD, "range", f"約 {_fmt(price-span)}～{_fmt(price+span)}", "區間中央不追單"),
        ("劇本三｜轉弱下跌", p.get("bear", 0), DOWN, "bear", f"跌破 {_fmt(price-span*.4)} 且站不回", f"下緣約 {_fmt(price-down)}"),
    ]


def _path(draw, box, kind, color):
    x0, y0, x1, y1 = box
    ratios = {"bull": (.72, .45, .60, .34, .42, .18), "range": (.48, .33, .62, .38, .58, .46), "bear": (.28, .45, .38, .62, .54, .82)}[kind]
    points = [(x0 + i / 5 * (x1 - x0), y0 + value * (y1 - y0)) for i, value in enumerate(ratios)]
    draw.line((x0, (y0+y1)/2, x1, (y0+y1)/2), fill=GRID, width=1)
    draw.line(points, fill=color, width=3)


def _scenarios(draw, box, briefing, daily):
    _panel(draw, box)
    _title(draw, box, "明日走勢劇本推演")
    x0, y0, x1, y1 = box
    specs = _scenario_specs(briefing, daily)
    height = (y1 - y0 - 42) / 3
    for i, (title, probability, color, kind, condition, target) in enumerate(specs):
        top = y0 + 39 + i * height
        if i:
            draw.line((x0 + 8, top, x1 - 8, top), fill=NAVY, width=2)
        _text(draw, (x0 + 14, top + 9), title, 15, color, True)
        _text(draw, (x1 - 15, top + 9), f"{probability}%", 17, color, True, "ra")
        _path(draw, (x0 + 14, top + 39, x0 + 195, top + height - 11), kind, color)
        _text(draw, (x0 + 211, top + 42), condition, 13, INK)
        _text(draw, (x0 + 211, top + 67), target, 13, MUTED)


def _timeline(draw, box, briefing):
    _panel(draw, box)
    _title(draw, box, "盤中分時路徑估計（模型）", 18)
    x0, y0, x1, y1 = box
    model = briefing.get("scenario_model") or {}
    p = model.get("probabilities") or {}
    dominant = max(p, key=p.get) if p else "range"
    price = _n(briefing.get("last_price"))
    span = max(20, _n(model.get("typical_range"), 40))
    slots = ["09:00-09:20", "09:20-10:00", "10:00-10:30", "10:30-11:30", "11:30-12:30", "13:00-13:45"]
    labels = ["開盤確認", "回測支撐", "整理選邊", "趨勢延續", "高檔整理", "尾盤確認"]
    _text(draw, (x0 + 12, y0 + 45), "時間", 13, NAVY, True)
    _text(draw, (x0 + 125, y0 + 45), "預估狀態", 13, NAVY, True)
    _text(draw, (x1 - 12, y0 + 45), "參考區間", 13, NAVY, True, "ra")
    bias = 1 if dominant == "bull" else -1 if dominant == "bear" else 0
    for i, (slot, label) in enumerate(zip(slots, labels)):
        y = y0 + 75 + i * 34
        low = price + bias * span * i / 10 - span * .45
        high = price + bias * span * i / 10 + span * .45
        _text(draw, (x0 + 12, y), slot, 12, INK)
        _text(draw, (x0 + 125, y), label, 12, INK)
        _text(draw, (x1 - 12, y), f"{low:,.0f}～{high:,.0f}", 12, UP if bias > 0 else DOWN if bias < 0 else GOLD, True, "ra")
    _text(draw, (x0 + 12, y1 - 23), "模型區間非報價；開盤後以完成 K 棒重算", 12, MUTED)


def _levels(draw, box, briefing, daily):
    _panel(draw, box)
    _title(draw, box, "關鍵價位區（高至低）")
    x0, y0, x1, y1 = box
    latest = daily.iloc[-1] if not daily.empty else None
    current = _n(briefing.get("last_price"))
    candidates = [
        (briefing.get("call_pressure"), "Call 壓力", UP),
        (_n(latest.MA5) if latest is not None else None, "MA5", GOLD),
        (_n(latest.MA10) if latest is not None else None, "MA10", ORANGE),
        (current, "最近價", INK),
        (_n(latest.MA20) if latest is not None else None, "MA20", BLUE),
        (briefing.get("put_support"), "Put 支撐", DOWN),
    ]
    rows, seen = [], set()
    for value, label, color in sorted(candidates, key=lambda item: _n(item[0], -1), reverse=True):
        value = round(_n(value))
        if value > 0 and value not in seen and (not current or current * .8 < value < current * 1.2):
            rows.append((value, label, color)); seen.add(value)
    for i, (value, label, color) in enumerate(rows[:7]):
        y = y0 + 49 + i * 34
        draw.line((x0 + 10, y + 26, x1 - 10, y + 26), fill=GRID, width=1)
        _text(draw, (x0 + 18, y), f"{value:,.0f}", 17, color, True)
        _text(draw, (x0 + 145, y), label, 14, INK)


def _globals(draw, box, briefing):
    _panel(draw, box)
    _title(draw, box, "國際市場影響（最近收盤）")
    x0, y0, x1, y1 = box
    items = briefing.get("international_markets") or []
    if not items:
        _text(draw, ((x0+x1)/2, (y0+y1)/2), "Yahoo Finance 暫未取得", 17, MUTED, True, "mm")
        return
    width = (x1 - x0 - 22) / min(4, len(items))
    for i, item in enumerate(items[:4]):
        cx = x0 + 11 + width * (i + .5)
        change = item.get("change_pct")
        _text(draw, (cx, y0 + 58), item.get("label"), 15, NAVY, True, "ma")
        _text(draw, (cx, y0 + 94), f"{_n(change):+.2f}%" if change is not None else "--", 18, UP if _n(change) >= 0 else DOWN, True, "ma")
        _text(draw, (cx, y0 + 125), item.get("date") or "--", 11, MUTED, anchor="ma")
    changes = [_n(item.get("change_pct")) for item in items if item.get("change_pct") is not None]
    tone = "外部風險偏正向" if changes and sum(changes) > 0 else "外部風險偏保守"
    _text(draw, (x0 + 16, y1 - 29), f"• {tone}，仍以台指開盤後量價確認", 14, INK)


def _conclusion(draw, box, briefing):
    _panel(draw, box)
    _title(draw, box, "綜合結論")
    x0, y0, x1, y1 = box
    model = briefing.get("scenario_model") or {}
    p = model.get("probabilities") or {}
    dominant = max(p, key=p.get) if p else "range"
    labels = {"bull": "偏多延續", "range": "區間震盪", "bear": "轉弱下跌"}
    color = UP if dominant == "bull" else DOWN if dominant == "bear" else GOLD
    _text(draw, (x0 + 18, y0 + 51), f"主劇本：{labels[dominant]} {p.get(dominant, 0)}%", 22, color, True)
    notes = [
        f"Walk-forward {model.get('walk_forward_accuracy', 0):.1f}%／{model.get('walk_forward_tests', 0)} 次",
        f"前次歷史誤判已校準：{(model.get('last_walk_forward_result') or {}).get('date', '--')}",
        f"實際發報追蹤 {model.get('forecast_tracking_count', 0)} 次，持續累積",
        "機率代表相似情境，不等於勝率或保證獲利",
    ]
    for i, note in enumerate(notes):
        _text(draw, (x0 + 18, y0 + 91 + i * 29), f"✓ {note}", 14, INK)


def _advice(draw, box, briefing):
    _panel(draw, box)
    _title(draw, box, "操作建議（盤前／短線）")
    x0, y0, x1, y1 = box
    entry = briefing.get("entry_price")
    lines = [
        f"方向：{briefing.get('direction') or '觀望'}",
        f"參考：{_fmt(entry)}｜停損 {_fmt(briefing.get('stop_loss_price'))}",
        f"停利：{_fmt(briefing.get('take_profit_price'))}｜風險 NT$ {_fmt(briefing.get('estimated_risk'))}",
        "開盤後先等第一根完整 15 分 K",
        "跳空過遠、量價不一致則原計畫作廢",
    ]
    for i, line in enumerate(lines):
        _text(draw, (x0 + 16, y0 + 52 + i * 31), f"✓ {line}", 14, INK if i else GOLD, i == 0)
    _text(draw, (x0 + 16, y1 - 25), "系統只提醒，不會送出真實委託", 13, DOWN, True)


def render_preopen_briefing_image(briefing, bars, output_path="data/preopen_briefing_latest.png"):
    if Image is None:
        raise RuntimeError("Pillow 尚未安裝，無法產生 Telegram 圖卡。")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    draw = ImageDraw.Draw(image)
    daily = _daily(bars)

    _header(draw, briefing)
    _summary(draw, briefing)
    _candles(draw, (12, 198, 478, 650), daily)
    _kd_ma(draw, (486, 198, 724, 650), daily)
    _ratio_panel(draw, (732, 198, 1188, 650), briefing, daily)
    _volume_panel(draw, (12, 658, 396, 946), briefing)
    _line_chart(draw, (404, 658, 802, 946), "SOX 指數（日 K）", [item.get("close") for item in briefing.get("sox_history") or []])
    _institutional(draw, (810, 658, 1188, 946), briefing)
    _factor_row(draw, (12, 954, 802, 1125), briefing, daily)
    _large_trader(draw, (810, 954, 1188, 1125), briefing)
    _scenarios(draw, (12, 1133, 478, 1450), briefing, daily)
    _timeline(draw, (486, 1133, 802, 1450), briefing)
    _levels(draw, (810, 1133, 1188, 1450), briefing, daily)
    _globals(draw, (12, 1458, 396, 1700), briefing)
    _conclusion(draw, (404, 1458, 802, 1700), briefing)
    _advice(draw, (810, 1458, 1188, 1700), briefing)
    _text(draw, (14, 1722), "資料來源：Sinopac Shioaji、TAIFEX、Yahoo Finance｜模型欄位均標示為估計｜僅供策略研究", 13, "white")
    image.save(output_path, format="PNG", optimize=True)
    return str(output_path)
