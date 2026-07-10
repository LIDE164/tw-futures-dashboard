import argparse
import time
from datetime import datetime

import pandas as pd

import sinopac_api
from alert_manager import dispatch_alert
from indicators import build_tech_data
from market_session import TAIPEI, get_market_status
from paper_broker import PaperBroker
from risk_manager import evaluate_entry_risk
from scoring import get_decision_score
from storage import restore_paper_broker_state, save_signal, update_heartbeat
from strategy import StrategyManager


SIGNAL_TIMEFRAME = "15min"
PRODUCT_ROOT = getattr(sinopac_api, "DEFAULT_FUTURES_ROOT", "TMF")
WORKER_NAME = "signal_worker"


def _resample_completed_bars(df, rule=SIGNAL_TIMEFRAME):
    if df is None or df.empty or "ts" not in df.columns:
        return pd.DataFrame()

    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in df.columns for column in required):
        return pd.DataFrame()

    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
    out = (
        out.dropna(subset=["ts"])
        .set_index("ts")
        .resample(rule)
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
        .reset_index()
    )
    current_bucket = pd.Timestamp.now(tz=TAIPEI).tz_localize(None).floor(rule)
    out = out[out["ts"] < current_bucket].copy()
    out.attrs.update(getattr(df, "attrs", {}))
    return out


def _entry_plan(action, realtime, stop_loss_points, take_profit_points):
    current_price = float(realtime.get("current_price") or 0)
    bid_price = float(realtime.get("bid_price") or 0)
    ask_price = float(realtime.get("ask_price") or 0)

    if action == "BUY_LONG":
        entry = ask_price or current_price
        return entry, entry - stop_loss_points, entry + take_profit_points

    if action == "SELL_SHORT":
        entry = bid_price or current_price
        return entry, entry + stop_loss_points, entry - take_profit_points

    return current_price, 0.0, 0.0


def _event_type(action, message):
    if action == "BUY_LONG":
        return "ENTRY_LONG"
    if action == "SELL_SHORT":
        return "ENTRY_SHORT"
    if action in {"CLOSE_LONG", "CLOSE_SHORT"}:
        if "停損" in message:
            return "EXIT_STOP"
        if "獲利目標" in message:
            return "EXIT_TARGET"
        return "EXIT_SCORE"
    return "HOLD"


def evaluate_once(api, args):
    market_status = get_market_status()
    realtime = sinopac_api.get_realtime_data_from_sinopac(api, product_root=PRODUCT_ROOT)
    raw_kbars, kbars_error = sinopac_api.get_recent_micro_txf_kbars(api, days=args.days)
    if kbars_error:
        update_heartbeat(WORKER_NAME, "warning", kbars_error)

    bars = _resample_completed_bars(raw_kbars)
    if bars.empty:
        update_heartbeat(WORKER_NAME, "warning", "尚無完整 15 分 K 可計算訊號")
        return None

    broker = restore_paper_broker_state(PaperBroker(multiplier=10))
    strategy = StrategyManager(
        long_entry_score=args.long_entry_score,
        short_entry_score=args.short_entry_score,
        stop_loss_points=args.stop_loss_points,
        take_profit_points=args.take_profit_points,
    )
    strategy.sync_position(
        broker.position,
        broker.entry_price,
        broker.stop_loss_price,
        broker.take_profit_price,
    )

    tech_data = build_tech_data(bars, realtime)
    score, label, reasons, feature = get_decision_score(tech_data, inst_data={}, with_reason=True)
    current_price = float(realtime.get("current_price") or 0)
    action, message = strategy.decide_action(score, current_price)
    event_type = _event_type(action, message)

    if action in {"BUY_LONG", "SELL_SHORT"}:
        risk = evaluate_entry_risk(action, broker, market_status)
        if not risk.allowed:
            update_heartbeat(WORKER_NAME, "ok", f"訊號 {action} 被風控擋下：{' / '.join(risk.reasons)}")
            return None

    if action == "HOLD":
        update_heartbeat(WORKER_NAME, "ok", message)
        return None

    entry_price, stop_price, take_price = _entry_plan(
        action,
        realtime,
        float(args.stop_loss_points),
        float(args.take_profit_points),
    )
    if action in {"CLOSE_LONG", "CLOSE_SHORT"}:
        entry_price = broker.entry_price or current_price
        stop_price = broker.stop_loss_price
        take_price = broker.take_profit_price

    bar_time = pd.to_datetime(bars["ts"].iloc[-1]).strftime("%Y/%m/%d %H:%M")
    contract_code = realtime.get("contract_code") or bars.attrs.get("contract_code", "")
    signal = {
        "signal_key": f"{contract_code}:{bar_time}:{action}:{entry_price:.0f}",
        "contract_code": contract_code,
        "bar_time": bar_time,
        "action": action,
        "score": score,
        "label": label,
        "feature": feature,
        "price": current_price,
        "entry_price": entry_price,
        "stop_loss_price": stop_price,
        "take_profit_price": take_price,
        "reasons": reasons,
        "message": message,
    }
    save_signal(signal)
    sent, detail = dispatch_alert(signal, event_type)
    update_heartbeat(WORKER_NAME, "ok", f"{event_type} {action}: {detail}")
    return signal


def run_worker(args):
    api, api_error = sinopac_api.get_api(simulation=args.simulation)
    if api_error:
        update_heartbeat(WORKER_NAME, "error", api_error)
        print(api_error)
        return 1

    while True:
        try:
            signal = evaluate_once(api, args)
            if signal:
                print(f"{datetime.now(TAIPEI):%Y-%m-%d %H:%M:%S} {signal['action']} {signal['score']}")
        except Exception as exc:
            update_heartbeat(WORKER_NAME, "error", str(exc))
            print(f"signal_worker error: {exc}")

        if args.once:
            return 0
        time.sleep(args.interval)


def parse_args():
    parser = argparse.ArgumentParser(description="微型臺指背景訊號與警報服務")
    parser.add_argument("--once", action="store_true", help="只執行一次，供測試用")
    parser.add_argument("--interval", type=int, default=30, help="輪詢秒數，預設 30 秒")
    parser.add_argument("--days", type=int, default=90, help="K 線回看天數，預設 90")
    parser.add_argument("--simulation", action=argparse.BooleanOptionalAction, default=sinopac_api.get_simulation_default())
    parser.add_argument("--long-entry-score", type=int, default=60)
    parser.add_argument("--short-entry-score", type=int, default=40)
    parser.add_argument("--stop-loss-points", type=float, default=50)
    parser.add_argument("--take-profit-points", type=float, default=100)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run_worker(parse_args()))
