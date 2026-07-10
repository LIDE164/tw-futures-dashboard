import pandas as pd

from indicators import build_tech_data
from paper_broker import PaperBroker
from scoring import get_decision_score
from strategy import StrategyManager


def _normalise_kbars(df):
    df = df.copy()

    rename_map = {}
    for column in df.columns:
        lower = str(column).lower()
        if lower == "open":
            rename_map[column] = "Open"
        elif lower == "high":
            rename_map[column] = "High"
        elif lower == "low":
            rename_map[column] = "Low"
        elif lower == "close":
            rename_map[column] = "Close"
        elif lower == "volume":
            rename_map[column] = "Volume"

    df = df.rename(columns=rename_map)
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"K 線缺少欄位：{', '.join(missing)}")

    for column in required:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    return df.dropna(subset=required).reset_index(drop=True)


def run_backtest(
    df,
    inst_data=None,
    quantity=1,
    multiplier=200,
    commission_per_side=0.0,
    slippage_points=1.0,
    long_entry_score=60,
    short_entry_score=40,
    stop_loss_points=50,
):
    if df is None or df.empty or len(df) < 60:
        return pd.DataFrame(), pd.DataFrame(), {"error": "K 線資料不足，至少需要 60 根以上資料。"}

    try:
        df = _normalise_kbars(df)
    except Exception as exc:
        return pd.DataFrame(), pd.DataFrame(), {"error": str(exc)}

    strategy = StrategyManager(
        long_entry_score=long_entry_score,
        short_entry_score=short_entry_score,
        stop_loss_points=stop_loss_points,
    )
    broker = PaperBroker(
        multiplier=multiplier,
        commission_per_side=commission_per_side,
        slippage_points=slippage_points,
    )
    records = []

    for i in range(60, len(df) - 1):
        history = df.iloc[: i + 1].copy()
        current_price = float(history["Close"].iloc[-1])
        next_open = float(df["Open"].iloc[i + 1])

        realtime = {
            "current_price": current_price,
            "volume": float(history["Volume"].iloc[-1]),
            "vwap": current_price,
        }

        strategy.sync_position(broker.position, broker.entry_price)
        tech_data = build_tech_data(history, realtime)
        score, label, reasons, feature = get_decision_score(
            tech_data,
            inst_data=inst_data or {},
            with_reason=True,
        )
        action, message = strategy.decide_action(score, current_price)

        filled = False
        fill_message = ""
        if action != "HOLD":
            filled, fill_message = broker.execute(action, next_open, quantity=quantity, note=message)
            if filled:
                strategy.apply_fill(action, next_open, quantity)

        unrealized = broker.unrealized_pnl(current_price)
        equity = broker.realized_pnl + unrealized

        records.append(
            {
                "bar": i,
                "price": current_price,
                "next_open": next_open,
                "score": score,
                "label": label,
                "action": action,
                "filled": filled,
                "message": message,
                "fill_message": fill_message,
                "position": broker.position,
                "realized_pnl": broker.realized_pnl,
                "unrealized_pnl": unrealized,
                "equity": equity,
            }
        )

    trades = broker.trades_df()
    equity_curve = pd.DataFrame(records)
    summary = summarize_backtest(trades, equity_curve)
    return trades, equity_curve, summary


def summarize_backtest(trades, equity_curve):
    if trades.empty:
        return {
            "交易次數": 0,
            "總損益": 0,
            "勝率": 0,
            "平均獲利": 0,
            "平均虧損": 0,
            "盈虧比": 0,
            "最大回撤": 0,
            "最大連虧次數": 0,
            "平均持倉K棒數": 0,
            "多單損益": 0,
            "空單損益": 0,
        }

    close_trades = trades[trades["pnl"] != 0].copy()
    if close_trades.empty:
        return {
            "交易次數": 0,
            "總損益": 0,
            "勝率": 0,
            "平均獲利": 0,
            "平均虧損": 0,
            "盈虧比": 0,
            "最大回撤": 0,
            "最大連虧次數": 0,
            "平均持倉K棒數": 0,
            "多單損益": 0,
            "空單損益": 0,
        }

    total_pnl = close_trades["pnl"].sum()
    winners = close_trades[close_trades["pnl"] > 0]
    losers = close_trades[close_trades["pnl"] < 0]
    avg_win = winners["pnl"].mean() if not winners.empty else 0
    avg_loss = losers["pnl"].mean() if not losers.empty else 0
    payoff_ratio = abs(avg_win / avg_loss) if avg_loss else 0

    if not equity_curve.empty:
        peak = equity_curve["equity"].cummax()
        max_drawdown = (equity_curve["equity"] - peak).min()
    else:
        max_drawdown = 0

    max_losing_streak = 0
    current_streak = 0
    for pnl in close_trades["pnl"]:
        if pnl < 0:
            current_streak += 1
            max_losing_streak = max(max_losing_streak, current_streak)
        else:
            current_streak = 0

    close_trades = close_trades.reset_index(drop=True)
    entries = trades[trades["pnl"] == 0].reset_index(drop=True)
    holding_bars = 0
    if not entries.empty and len(entries) == len(close_trades):
        holding_bars = 1

    long_pnl = close_trades[close_trades["action"].eq("CLOSE_LONG")]["pnl"].sum()
    short_pnl = close_trades[close_trades["action"].eq("CLOSE_SHORT")]["pnl"].sum()

    return {
        "交易次數": int(len(close_trades)),
        "總損益": round(float(total_pnl), 0),
        "勝率": round(float((close_trades["pnl"] > 0).mean() * 100), 2),
        "平均獲利": round(float(avg_win), 0),
        "平均虧損": round(float(avg_loss), 0),
        "盈虧比": round(float(payoff_ratio), 2),
        "最大回撤": round(float(max_drawdown), 0),
        "最大連虧次數": int(max_losing_streak),
        "平均持倉K棒數": holding_bars,
        "多單損益": round(float(long_pnl), 0),
        "空單損益": round(float(short_pnl), 0),
    }
