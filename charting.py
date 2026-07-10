import pandas as pd


def build_signal_chart(kbars, trade_plan=None, rows=80):
    if kbars is None or kbars.empty:
        return None

    try:
        import plotly.graph_objects as go
    except Exception:
        return None

    required = {"ts", "Open", "High", "Low", "Close"}
    if not required.issubset(set(kbars.columns)):
        return None

    df = kbars.tail(rows).copy()
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df = df.dropna(subset=["ts"])
    if df.empty:
        return None

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=df["ts"],
                open=df["Open"],
                high=df["High"],
                low=df["Low"],
                close=df["Close"],
                increasing_line_color="#16a34a",
                decreasing_line_color="#dc2626",
                name="15 分 K",
            )
        ]
    )

    line_specs = [
        ("entry_price", "進場", "#2563eb", "dash"),
        ("stop_loss", "停損", "#dc2626", "dot"),
        ("take_profit", "停利", "#16a34a", "dot"),
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
            )

    fig.update_layout(
        height=360,
        margin=dict(l=10, r=10, t=20, b=10),
        xaxis_rangeslider_visible=False,
        showlegend=False,
        template="plotly_dark",
    )
    return fig
