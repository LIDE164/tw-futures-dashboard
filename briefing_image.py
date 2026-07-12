from pathlib import Path

import pandas as pd

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None


WIDTH = 1400
HEIGHT = 920
BG = "#080a0d"
PANEL = "#101318"
GRID = "#343943"
TEXT = "#e6e9ef"
MUTED = "#9aa2af"
GOLD = "#e3b341"
UP = "#ef4444"
DOWN = "#22c55e"
BLUE = "#55b6d9"


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


def _fmt(value):
    number = _num(value)
    return f"{number:,.0f}" if number else "--"


def _panel(draw, box, outline=GRID):
    draw.rectangle(box, fill=PANEL, outline=outline, width=2)


def _text(draw, xy, value, size=24, fill=TEXT, bold=False, anchor=None):
    draw.text(xy, str(value), font=_font(size, bold), fill=fill, anchor=anchor)


def _wrap(draw, text, width_px, font):
    words = list(str(text))
    lines = []
    current = ""
    for char in words:
        candidate = current + char
        if draw.textlength(candidate, font=font) > width_px and current:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _daily_bars(bars, days=28):
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
    return daily.tail(days).reset_index()


def _draw_chart(draw, box, daily):
    x0, y0, x1, y1 = box
    _panel(draw, box)
    _text(draw, (x0 + 18, y0 + 12), "日 K 趨勢與均線", 24, GOLD, True)
    if daily.empty:
        _text(draw, ((x0 + x1) // 2, (y0 + y1) // 2), "K 線資料不足", 28, MUTED, anchor="mm")
        return

    chart_top = y0 + 52
    volume_top = y1 - 92
    chart_bottom = volume_top - 14
    price_min = float(daily["Low"].min())
    price_max = float(daily["High"].max())
    margin = max(10.0, (price_max - price_min) * 0.08)
    price_min -= margin
    price_max += margin
    span = max(1.0, price_max - price_min)
    count = len(daily)
    step = (x1 - x0 - 82) / max(1, count)
    candle_w = max(4, min(14, step * 0.58))

    def price_y(value):
        return chart_bottom - (float(value) - price_min) / span * (chart_bottom - chart_top)

    for index in range(5):
        y = chart_top + (chart_bottom - chart_top) * index / 4
        draw.line((x0 + 52, y, x1 - 14, y), fill="#252a32", width=1)
        level = price_max - span * index / 4
        _text(draw, (x0 + 8, y), f"{level:,.0f}", 14, MUTED, anchor="lm")

    xs = []
    for index, row in daily.iterrows():
        x = x0 + 58 + step * (index + 0.5)
        xs.append(x)
        color = UP if row["Close"] >= row["Open"] else DOWN
        draw.line((x, price_y(row["High"]), x, price_y(row["Low"])), fill=color, width=2)
        top = min(price_y(row["Open"]), price_y(row["Close"]))
        bottom = max(price_y(row["Open"]), price_y(row["Close"]))
        draw.rectangle((x - candle_w / 2, top, x + candle_w / 2, max(top + 2, bottom)), fill=color)

    for column, color in (("MA5", GOLD), ("MA10", "#df7b39"), ("MA20", BLUE)):
        points = []
        for x, value in zip(xs, daily[column]):
            if not pd.isna(value):
                points.append((x, price_y(value)))
        if len(points) >= 2:
            draw.line(points, fill=color, width=3)

    volume_max = max(1.0, float(daily["Volume"].max()))
    for x, (_, row) in zip(xs, daily.iterrows()):
        height = float(row["Volume"]) / volume_max * 62
        color = UP if row["Close"] >= row["Open"] else DOWN
        draw.rectangle((x - candle_w / 2, y1 - 16 - height, x + candle_w / 2, y1 - 16), fill=color)
    _text(draw, (x0 + 55, y1 - 84), "VOL", 15, MUTED, True)

    latest = daily.iloc[-1]
    legends = [
        (f"MA5 {_fmt(latest.get('MA5'))}", GOLD),
        (f"MA10 {_fmt(latest.get('MA10'))}", "#df7b39"),
        (f"MA20 {_fmt(latest.get('MA20'))}", BLUE),
    ]
    lx = x0 + 255
    for label, color in legends:
        _text(draw, (lx, y0 + 17), label, 16, color, True)
        lx += 155


def _draw_levels(draw, box, briefing, daily):
    x0, y0, x1, y1 = box
    _panel(draw, box)
    _text(draw, ((x0 + x1) // 2, y0 + 18), "下一交易時段關鍵價位", 24, GOLD, True, "ma")
    latest = daily.iloc[-1] if not daily.empty else {}
    current = _num(briefing.get("last_price"))
    def near_market(value):
        number = _num(value)
        return number if current > 0 and current * 0.85 <= number <= current * 1.15 else 0.0

    levels = [
        (near_market(briefing.get("call_pressure")), "Call 壓力", UP),
        (latest.get("MA10") if len(latest) else 0, "MA10 多空分界", GOLD),
        (current, "最近價格", TEXT),
        (latest.get("MA20") if len(latest) else 0, "MA20 第一支撐", BLUE),
        (near_market(briefing.get("put_support")), "Put 支撐", DOWN),
        (briefing.get("stop_loss_price"), "策略停損", DOWN),
    ]
    valid = []
    seen = set()
    for value, label, color in levels:
        number = int(round(_num(value)))
        if number > 0 and number not in seen:
            seen.add(number)
            valid.append((number, label, color))
    valid.sort(reverse=True)
    row_height = max(42, (y1 - y0 - 76) // max(1, len(valid)))
    for index, (value, label, color) in enumerate(valid):
        y = y0 + 58 + index * row_height
        draw.line((x0 + 18, y + 24, x1 - 18, y + 24), fill=GRID, width=1)
        _text(draw, (x0 + 24, y), f"{value:,.0f}", 25, color, True)
        _text(draw, (x0 + 150, y + 3), label, 18, TEXT)


def _scenario_specs(briefing):
    score = int(briefing.get("score") or 50)
    current = _num(briefing.get("last_price"))
    stop = _num(briefing.get("stop_loss_price"))
    target = _num(briefing.get("take_profit_price"))
    gap = abs(current - stop) or max(20, current * 0.002)
    return [
        ("劇本一｜偏多延續", max(20, min(70, score)), DOWN, f"站穩 {_fmt(current)}，挑戰 {_fmt(target or current + gap * 2)}", "回踩守穩再偏多，不追高。"),
        ("劇本二｜區間震盪", max(20, 70 - abs(score - 50)), GOLD, f"約 {_fmt(current-gap)}～{_fmt(current+gap)} 整理", "靠近支撐觀察，區間中央不交易。"),
        ("劇本三｜轉弱下跌", max(10, min(60, 100-score)), UP, f"跌破 {_fmt(stop or current-gap)} 且無法站回", "多單退出；空方仍需 60 分趨勢確認。"),
    ]


def _draw_scenarios(draw, top, briefing):
    gap = 14
    card_w = (WIDTH - 40 - gap * 2) / 3
    for index, (title, chance, color, condition, plan) in enumerate(_scenario_specs(briefing)):
        x0 = 20 + index * (card_w + gap)
        box = (x0, top, x0 + card_w, top + 170)
        _panel(draw, box, color)
        _text(draw, (x0 + 16, top + 14), title, 22, color, True)
        _text(draw, (x0 + card_w - 16, top + 16), f"{chance}%", 20, TEXT, True, "ra")
        _text(draw, (x0 + 16, top + 58), condition, 18, TEXT, True)
        font = _font(17)
        for line_index, line in enumerate(_wrap(draw, plan, card_w - 32, font)[:3]):
            draw.text((x0 + 16, top + 96 + line_index * 24), line, font=font, fill=MUTED)


def render_preopen_briefing_image(briefing, bars, output_path="data/preopen_briefing_latest.png"):
    if Image is None:
        raise RuntimeError("Pillow 尚未安裝，無法產生 Telegram 圖片。")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    daily = _daily_bars(bars)

    _text(draw, (24, 18), f"微型臺指（近月）{briefing.get('session_label')}開盤前交易地圖", 34, GOLD, True)
    _text(
        draw,
        (24, 64),
        f"契約 {briefing.get('contract_code') or '--'}｜最近 {_fmt(briefing.get('last_price'))}｜"
        f"評分 {briefing.get('score')} {briefing.get('label')}｜{briefing.get('direction')}",
        22,
        TEXT,
        True,
    )
    _text(draw, (WIDTH - 24, 24), briefing.get("last_bar_time", ""), 18, MUTED, False, "ra")
    _draw_chart(draw, (20, 105, 900, 545), daily)
    _draw_levels(draw, (916, 105, 1380, 545), briefing, daily)
    _draw_scenarios(draw, 560, briefing)

    _panel(draw, (20, 746, 900, 892))
    _text(draw, (38, 760), "交易重點", 23, GOLD, True)
    focus = [
        f"15 分 {briefing.get('trend_15m')}｜60 分 {briefing.get('trend_60m')}｜ADX {briefing.get('adx', 0):.1f}",
        f"小台法人多空比 {_num(briefing.get('mtx_long_short_ratio')):.2f}%｜選擇權 OI P/C {_num(briefing.get('pc_oi_ratio')):.2f}%",
        f"參考進場 {_fmt(briefing.get('entry_price'))}｜停損 {_fmt(briefing.get('stop_loss_price'))}｜停利 {_fmt(briefing.get('take_profit_price'))}",
        f"每口預估風險 NT$ {_num(briefing.get('estimated_risk')):,.0f}；開盤跳空時價位作廢。",
    ]
    for index, line in enumerate(focus):
        _text(draw, (40, 800 + index * 23), f"- {line}", 17, TEXT)

    _panel(draw, (916, 746, 1380, 892), GOLD)
    _text(draw, (934, 760), "建議操作方向", 23, GOLD, True)
    _text(draw, (934, 801), briefing.get("direction", "觀望"), 30, TEXT, True)
    _text(draw, (934, 843), "等待開盤後第一根完整 15 分 K 確認", 17, MUTED)
    _text(draw, (WIDTH - 22, HEIGHT - 12), "技術分析與模擬策略參考，非投資建議；系統不會自動送出真實委託。", 15, MUTED, False, "rb")

    image.save(output_path, format="PNG", optimize=True)
    return str(output_path)
