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
from storage import restore_paper_broker_state, save_paper_broker_state, save_signal, update_heartbeat
from strategy import StrategyManager


SIGNAL_TIMEFRAME = "15min"
PRODUCT_ROOT = getattr(sinopac_api, "DEFAULT_FUTURES_ROOT", "TMF")
WORKER_NAME = "signal_worker"
TEST_SIGNAL_ACTIONS = {"BUY_LONG", "SELL_SHORT", "CLOSE_LONG", "CLOSE_SHORT"}
TEST_EXIT_EVENTS = {"STOP", "TARGET"}


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


def _event_type(action, message, broker=None, current_price=0):
    if action == "BUY_LONG":
        return "ENTRY_LONG"
    if action == "SELL_SHORT":
        return "ENTRY_SHORT"
    if action in {"CLOSE_LONG", "CLOSE_SHORT"}:
        current_price = float(current_price or 0)
        if broker is not None and broker.position > 0:
            if broker.stop_loss_price and current_price <= broker.stop_loss_price:
                return "EXIT_STOP"
            if broker.take_profit_price and current_price >= broker.take_profit_price:
                return "EXIT_TARGET"
        if broker is not None and broker.position < 0:
            if broker.stop_loss_price and current_price >= broker.stop_loss_price:
                return "EXIT_STOP"
            if broker.take_profit_price and current_price <= broker.take_profit_price:
                return "EXIT_TARGET"
        if "停損" in message:
            return "EXIT_STOP"
        if "獲利目標" in message:
            return "EXIT_TARGET"
        return "EXIT_SCORE"
    return "HOLD"


def _sync_paper_tracking(broker, action, current_price, entry_price, stop_price, take_price, message, args):
    if not args.auto_paper_fill:
        return False, "paper tracking disabled"

    if action in {"BUY_LONG", "SELL_SHORT"}:
        filled, fill_msg = broker.execute(
            action,
            entry_price,
            quantity=args.paper_quantity,
            note=f"worker alert: {message}",
            stop_loss_price=stop_price,
            take_profit_price=take_price,
        )
    elif action in {"CLOSE_LONG", "CLOSE_SHORT"}:
        filled, fill_msg = broker.execute(
            action,
            current_price,
            quantity=abs(broker.position) or args.paper_quantity,
            note=f"worker alert: {message}",
        )
    else:
        return False, "no paper tracking action"

    if filled:
        save_paper_broker_state(broker)
    return filled, fill_msg


def _round_to_tick(value, tick=5):
    if value <= 0:
        return 0.0
    return round(float(value) / tick) * tick


def _effective_risk_points(args, tech_data):
    fixed_stop = float(args.stop_loss_points)
    fixed_take = float(args.take_profit_points)
    if not args.adaptive_risk:
        return fixed_stop, fixed_take

    atr_points = float(tech_data.get("ATR") or 0)
    if atr_points <= 0:
        return fixed_stop, fixed_take

    stop_points = _round_to_tick(max(20, min(180, atr_points * float(args.atr_stop_multiplier))))
    take_points = _round_to_tick(max(stop_points, min(360, stop_points * float(args.reward_risk_ratio))))
    return stop_points, take_points


def _build_test_signal(action, args):
    now = datetime.now(TAIPEI)
    price = float(args.test_price)
    if price <= 0:
        price = 25000.0

    realtime = {
        "current_price": price,
        "bid_price": price - 1,
        "ask_price": price + 1,
    }
    effective_stop, effective_take = float(args.stop_loss_points), float(args.take_profit_points)
    entry_price, stop_price, take_price = _entry_plan(
        action,
        realtime,
        effective_stop,
        effective_take,
    )

    if action == "CLOSE_LONG":
        if args.test_exit == "TARGET":
            entry_price = price - effective_take
            stop_price = entry_price - effective_stop
            take_price = price
            event_type = "EXIT_TARGET"
            message = "測試：多單到達停利，提醒平倉"
            reasons = ["測試多單停利通知", "確認 Telegram 平倉訊息可送達"]
        else:
            entry_price = price + effective_stop
            stop_price = price
            take_price = entry_price + effective_take
            event_type = "EXIT_STOP"
            message = "測試：多單跌破停損，提醒平倉"
            reasons = ["測試多單停損通知", "確認 Telegram 平倉訊息可送達"]
    elif action == "CLOSE_SHORT":
        if args.test_exit == "TARGET":
            entry_price = price + effective_take
            stop_price = entry_price + effective_stop
            take_price = price
            event_type = "EXIT_TARGET"
            message = "測試：空單到達停利，提醒回補"
            reasons = ["測試空單停利通知", "確認 Telegram 平倉訊息可送達"]
        else:
            entry_price = price - effective_stop
            stop_price = price
            take_price = entry_price - effective_take
            event_type = "EXIT_STOP"
            message = "測試：空單突破停損，提醒回補"
            reasons = ["測試空單停損通知", "確認 Telegram 平倉訊息可送達"]
    elif action == "BUY_LONG":
        event_type = "ENTRY_LONG"
        message = "測試：策略分數達到做多門檻"
        reasons = ["測試做多通知", "確認 Telegram 進場訊息可送達"]
    elif action == "SELL_SHORT":
        event_type = "ENTRY_SHORT"
        message = "測試：策略分數達到做空門檻"
        reasons = ["測試做空通知", "確認 Telegram 進場訊息可送達"]
    else:
        raise ValueError(f"unsupported test signal action: {action}")

    signal = {
        "signal_key": f"TEST:{now:%Y%m%d%H%M%S}:{action}:{entry_price:.0f}",
        "contract_code": "TEST-TMF",
        "bar_time": now.strftime("%Y/%m/%d %H:%M:%S"),
        "action": action,
        "score": 70 if action in {"BUY_LONG", "CLOSE_SHORT"} else 30,
        "label": "測試訊號",
        "feature": "test",
        "price": price,
        "entry_price": entry_price,
        "stop_loss_price": stop_price,
        "take_profit_price": take_price,
        "reasons": reasons,
        "message": message,
    }
    return signal, event_type


def send_test_signal(args):
    action = args.test_signal
    if action not in TEST_SIGNAL_ACTIONS:
        raise ValueError(f"--test-signal must be one of: {', '.join(sorted(TEST_SIGNAL_ACTIONS))}")

    signal, event_type = _build_test_signal(action, args)
    save_signal(signal)
    sent, detail = dispatch_alert(signal, event_type)
    update_heartbeat(WORKER_NAME, "test", f"{event_type} {action}: {detail}")
    print(f"{event_type} {action}: {detail}")
    return 0 if sent and detail in {"telegram sent", "webhook sent", "duplicate alert skipped"} else 1


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

    broker = restore_paper_broker_state(
        PaperBroker(
            multiplier=10,
            commission_per_side=args.commission_per_side,
            slippage_points=args.slippage_points,
        )
    )
    broker.commission_per_side = args.commission_per_side
    broker.slippage_points = args.slippage_points
    tech_data = build_tech_data(bars, realtime)
    effective_stop_loss_points, effective_take_profit_points = _effective_risk_points(args, tech_data)
    strategy = StrategyManager(
        long_entry_score=args.long_entry_score,
        short_entry_score=args.short_entry_score,
        stop_loss_points=effective_stop_loss_points,
        take_profit_points=effective_take_profit_points,
    )
    strategy.sync_position(
        broker.position,
        broker.entry_price,
        broker.stop_loss_price,
        broker.take_profit_price,
    )

    score, label, reasons, feature = get_decision_score(tech_data, inst_data={}, with_reason=True)
    current_price = float(realtime.get("current_price") or 0)
    action, message = strategy.decide_action(score, current_price)
    event_type = _event_type(action, message, broker, current_price)

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
        float(effective_stop_loss_points),
        float(effective_take_profit_points),
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
    tracking_detail = ""
    delivered = detail in {"telegram sent", "webhook sent"}
    if sent and delivered:
        tracked, tracking_detail = _sync_paper_tracking(
            broker,
            action,
            current_price,
            entry_price,
            stop_price,
            take_price,
            message,
            args,
        )
        tracking_detail = f" | paper: {tracking_detail}" if tracking_detail else ""
        if tracked:
            strategy.apply_fill(
                action,
                entry_price,
                args.paper_quantity,
                stop_price,
                take_price,
            )
    elif sent:
        tracking_detail = " | paper: alert not delivered, tracking skipped"

    update_heartbeat(WORKER_NAME, "ok", f"{event_type} {action}: {detail}{tracking_detail}")
    return signal


def run_worker(args):
    if args.test_signal:
        return send_test_signal(args)

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
    parser.add_argument("--adaptive-risk", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--atr-stop-multiplier", type=float, default=1.2)
    parser.add_argument("--reward-risk-ratio", type=float, default=2.0)
    parser.add_argument("--paper-quantity", type=int, default=1)
    parser.add_argument("--commission-per-side", type=float, default=0.0)
    parser.add_argument("--slippage-points", type=float, default=1.0)
    parser.add_argument("--auto-paper-fill", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--test-signal", choices=sorted(TEST_SIGNAL_ACTIONS), help="Send a test strategy alert")
    parser.add_argument("--test-exit", choices=sorted(TEST_EXIT_EVENTS), default="STOP", help="Exit event for close test signals")
    parser.add_argument("--test-price", type=float, default=25000.0, help="Reference price for --test-signal")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run_worker(parse_args()))
