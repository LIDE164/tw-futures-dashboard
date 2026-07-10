import pandas as pd


PLOT_CONFIG = {
    "displayModeBar": False,
    "scrollZoom": False,
    "doubleClick": False,
    "responsive": True,
}


def _load_plotly():
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception:
        return None, None
    return go, make_subplots


def _add_trade_lines(fig, trade_plan=None):
    line_specs = [
        ("entry_price", "進場", "#2563eb", "dash"),
        ("stop_loss", "停損", "#22c55e", "dot"),
        ("take_profit", "停利", "#ef4444", "dot"),
    ]
    trade_plan = trade_plan or {}
    for key, label, color, dash in line_specs:
        value = trade_plan.get(key)
        if value:
            fig.add_hline(
                y=float(value),
                line_color=color,
                line_dash=dash,
                annotation_text=f"{label} {float(value):,.0f}",
                annotation_position="top left",
                row=1,
                col=1,
            )


def _style_mobile_chart(fig, height=390):
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=18, b=8),
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        template="plotly_dark",
    )
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    return fig


def build_signal_chart(kbars, trade_plan=None, rows=80):
    if kbars is None or kbars.empty:
        return None

    go, make_subplots = _load_plotly()
    if go is None or make_subplots is None:
        return None

    required = {"ts", "Open", "High", "Low", "Close"}
    if not required.issubset(set(kbars.columns)):
        return None

    df = kbars.tail(rows).copy()
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df = df.dropna(subset=["ts"])
    if df.empty:
        return None

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.03,
    )
    fig.add_trace(
        go.Candlestick(
            x=df["ts"],
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            increasing_line_color="#ef4444",
            decreasing_line_color="#22c55e",
            name="15 分 K",
        ),
        row=1,
        col=1,
    )
    if "Volume" in df.columns:
        colors = ["#ef4444" if close >= open_ else "#22c55e" for open_, close in zip(df["Open"], df["Close"])]
        fig.add_trace(
            go.Bar(x=df["ts"], y=df["Volume"], marker_color=colors, name="成交量"),
            row=2,
            col=1,
        )

    _add_trade_lines(fig, trade_plan)
    return _style_mobile_chart(fig)


def prepare_daily_chart(kbars, days=35):
    if kbars is None or kbars.empty or "ts" not in kbars.columns:
        return pd.DataFrame()

    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(kbars.columns)):
        return pd.DataFrame()

    df = kbars.copy()
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df = df.dropna(subset=["ts"]).set_index("ts")
    daily = (
        df.resample("1D")
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        .dropna()
    )
    daily["MA5"] = daily["Close"].rolling(5).mean()
    daily["MA10"] = daily["Close"].rolling(10).mean()
    daily["MA20"] = daily["Close"].rolling(20).mean()
    return daily.tail(days).reset_index()


def build_daily_chart(kbars, days=35):
    daily = prepare_daily_chart(kbars, days)
    if daily.empty:
        return None

    go, make_subplots = _load_plotly()
    if go is None or make_subplots is None:
        return None

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.03,
    )
    fig.add_trace(
        go.Candlestick(
            x=daily["ts"],
            open=daily["Open"],
            high=daily["High"],
            low=daily["Low"],
            close=daily["Close"],
            increasing_line_color="#ef4444",
            decreasing_line_color="#22c55e",
            name="日 K",
        ),
        row=1,
        col=1,
    )
    for column, color in (("MA5", "#facc15"), ("MA10", "#60a5fa"), ("MA20", "#c084fc")):
        fig.add_trace(
            go.Scatter(x=daily["ts"], y=daily[column], mode="lines", name=column, line=dict(color=color, width=1.6)),
            row=1,
            col=1,
        )

    colors = ["#ef4444" if close >= open_ else "#22c55e" for open_, close in zip(daily["Open"], daily["Close"])]
    fig.add_trace(
        go.Bar(x=daily["ts"], y=daily["Volume"], marker_color=colors, name="成交量"),
        row=2,
        col=1,
    )
    return _style_mobile_chart(fig)
