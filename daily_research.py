from datetime import datetime

import pandas as pd

from adaptive_learning import process_research_learning
from backtester import optimize_then_validate, run_backtest
from flow_cost_research import run_flow_cost_comparison
from storage import load_json_state, load_paper_broker_state, save_json_state, save_research_report


FLOW_COST_RESEARCH_STATE_KEY = "flow_cost_research_latest"


DEFAULT_BACKTEST_KWARGS = {
    "quantity": 1,
    "multiplier": 10,
    "commission_per_side": 20.0,
    "slippage_points": 2.0,
    "long_entry_score": 62,
    "short_entry_score": 35,
    "stop_loss_points": 50,
    "take_profit_points": 100,
    "adaptive_risk": True,
    "atr_stop_multiplier": 1.2,
    "reward_risk_ratio": 2.2,
    "min_entry_rr": 1.5,
    "reject_choppy": True,
    "require_60m_alignment": True,
    "min_adx": 22,
    "min_volume_ratio": 1.0,
    "max_chase_atr": 1.0,
    "confirmation_bars": 2,
    "require_5m_confirmation": False,
    "five_minute_long_score": 50,
    "five_minute_short_score": 50,
    "cooldown_bars": 2,
    "allow_long": True,
    "allow_short": False,
    "breakeven_trigger_r": 1.0,
    "breakeven_buffer_points": 0,
    "max_holding_bars": 24,
    "score_exit_requires_profit": True,
    "min_score_exit_profit_points": 0,
    "signal_timeframe": "15min",
    "include_institutional": False,
}

PARAMETER_COLUMNS = {
    "long_entry_score": "多單門檻",
    "short_entry_score": "空單門檻",
    "min_entry_rr": "最低RR",
    "atr_stop_multiplier": "ATR停損倍數",
    "reward_risk_ratio": "停利倍數",
    "min_adx": "最低ADX",
    "min_volume_ratio": "最低量比",
    "max_chase_atr": "最大追價ATR",
    "confirmation_bars": "確認K",
    "cooldown_bars": "冷卻K",
    "allow_long": "允許多單",
    "allow_short": "允許空單",
    "breakeven_trigger_r": "保本觸發R",
    "breakeven_buffer_points": "保本加點",
    "max_holding_bars": "最長持倉K",
}


def _plain_value(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    missing = pd.isna(value)
    if not hasattr(missing, "__len__") and bool(missing):
        return None
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _plain_dict(data):
    return {str(key): _plain_value(value) for key, value in dict(data or {}).items()}


def _paper_day_summary(report_date):
    state = load_paper_broker_state()
    closes = []
    for trade in state.get("trades", []):
        if trade.get("action") not in {"CLOSE_LONG", "CLOSE_SHORT"}:
            continue
        trade_time = pd.to_datetime(trade.get("time"), errors="coerce")
        if pd.isna(trade_time) or trade_time.date().isoformat() != report_date:
            continue
        closes.append(trade)

    pnl_values = [float(item.get("pnl") or 0) for item in closes]
    wins = sum(value > 0 for value in pnl_values)
    return {
        "trades": len(closes),
        "pnl": round(sum(pnl_values), 0),
        "wins": wins,
        "losses": sum(value < 0 for value in pnl_values),
        "win_rate": round(wins / len(closes) * 100, 2) if closes else 0.0,
        "open_position": int(state.get("position") or 0),
        "unrealized_not_included": bool(state.get("position")),
    }


def _walk_forward_summary(walk_forward):
    if walk_forward is None or walk_forward.empty:
        return {
            "folds": 0,
            "positive_folds": 0,
            "oos_trades": 0,
            "weighted_expectancy": 0.0,
            "median_profit_factor": 0.0,
        }

    ok = walk_forward.copy()
    if "狀態" in ok.columns:
        ok = ok[ok["狀態"] == "ok"]
    if ok.empty:
        return {
            "folds": 0,
            "positive_folds": 0,
            "oos_trades": 0,
            "weighted_expectancy": 0.0,
            "median_profit_factor": 0.0,
        }

    zeroes = pd.Series(0.0, index=ok.index)
    trades = pd.to_numeric(ok["樣本外交易次數"], errors="coerce").fillna(0) if "樣本外交易次數" in ok else zeroes
    expectancy = pd.to_numeric(ok["樣本外期望值"], errors="coerce").fillna(0) if "樣本外期望值" in ok else zeroes
    profit_factor = pd.to_numeric(ok["樣本外PF"], errors="coerce").fillna(0) if "樣本外PF" in ok else zeroes
    total_trades = int(trades.sum())
    weighted_expectancy = float((expectancy * trades).sum() / total_trades) if total_trades else 0.0
    return {
        "folds": int(len(ok)),
        "positive_folds": int((expectancy > 0).sum()),
        "oos_trades": total_trades,
        "weighted_expectancy": round(weighted_expectancy, 2),
        "median_profit_factor": round(float(profit_factor.median()), 2),
    }


def _prepare_15m_history(history):
    if history is None or history.empty or "ts" not in history.columns:
        return pd.DataFrame()
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in history.columns for column in required):
        return pd.DataFrame()
    out = history.copy()
    out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
    return (
        out.dropna(subset=["ts"])
        .set_index("ts")
        .resample("15min")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna()
        .reset_index()
    )


def _fixed_walk_forward(history, base_kwargs, folds=3):
    prepared = _prepare_15m_history(history)
    folds = max(2, int(folds or 2))
    if len(prepared) < folds * 120:
        return pd.DataFrame()

    fold_size = len(prepared) // folds
    rows = []
    for fold in range(folds):
        test_start = fold * fold_size
        test_end = len(prepared) if fold == folds - 1 else (fold + 1) * fold_size
        warmup_start = max(0, test_start - 80)
        segment = prepared.iloc[warmup_start:test_end].copy()
        _, _, summary = run_backtest(segment, **base_kwargs)
        if summary.get("error"):
            rows.append({"fold": fold + 1, "狀態": summary["error"]})
            continue
        rows.append(
            {
                "fold": fold + 1,
                "狀態": "ok",
                "樣本外勝率": summary.get("勝率", 0),
                "樣本外期望值": summary.get("期望值", 0),
                "樣本外PF": summary.get("Profit Factor", 0),
                "樣本外交易次數": summary.get("交易次數", 0),
            }
        )
    return pd.DataFrame(rows)


def _candidate_from_validation(validation):
    if validation is None or validation.empty:
        return {}
    row = validation.iloc[0]
    params = {
        target: _plain_value(row.get(source))
        for target, source in PARAMETER_COLUMNS.items()
        if source in row.index
    }
    return {
        "profile": _plain_value(row.get("設定類型", "")),
        "parameters": params,
        "train_trades": int(row.get("訓練交易次數", 0) or 0),
        "train_expectancy": float(row.get("訓練期望值", 0) or 0),
        "oos_trades": int(row.get("樣本外交易次數", 0) or 0),
        "oos_expectancy": float(row.get("樣本外期望值", 0) or 0),
        "oos_profit_factor": float(row.get("樣本外PF", 0) or 0),
        "oos_win_rate": float(row.get("樣本外勝率", 0) or 0),
    }


def run_close_research(
    history,
    report_date=None,
    history_status=None,
    base_kwargs=None,
    folds=3,
    min_reference_trades=100,
    min_oos_trades=30,
    run_optimisation=False,
    allow_auto_learning=True,
):
    report_date = report_date or datetime.now().date().isoformat()
    history_status = dict(history_status or {})
    kwargs = {**DEFAULT_BACKTEST_KWARGS, **dict(base_kwargs or {})}
    history_rows = int(len(history)) if history is not None else 0

    if history is None or history.empty:
        report = {
            "report_date": report_date,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "無歷史資料",
            "history_rows": 0,
            "history": history_status,
            "paper_day": _paper_day_summary(report_date),
            "backtest": {},
            "walk_forward": {},
            "candidate": {},
            "flow_cost": {},
            "learning": {"auto_apply": False, "reason": "尚未累積歷史 K 線。"},
        }
        save_research_report(report)
        return report

    _, _, full_summary = run_backtest(history, **kwargs)
    if full_summary.get("error"):
        report = {
            "report_date": report_date,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "回測資料不足",
            "history_rows": history_rows,
            "history": history_status,
            "paper_day": _paper_day_summary(report_date),
            "backtest": _plain_dict(full_summary),
            "walk_forward": {},
            "candidate": {},
            "flow_cost": {},
            "learning": {"auto_apply": False, "reason": full_summary.get("error", "資料不足")},
        }
        save_research_report(report)
        return report

    backtest_trades = int(full_summary.get("交易次數", 0) or 0)
    optimisation_min_trades = max(3, min(10, backtest_trades // 5 or 3))
    validation = pd.DataFrame()
    if run_optimisation:
        validation = optimize_then_validate(
            history,
            base_kwargs=kwargs,
            train_ratio=0.7,
            min_trades=optimisation_min_trades,
            top_n=3,
        )
    walk_forward = _fixed_walk_forward(history, kwargs, folds=max(2, int(folds)))
    walk_summary = _walk_forward_summary(walk_forward)
    candidate = _candidate_from_validation(validation)
    flow_cost = load_json_state(FLOW_COST_RESEARCH_STATE_KEY, {})
    if run_optimisation or not flow_cost:
        flow_cost = run_flow_cost_comparison(
            history,
            base_kwargs=kwargs,
            train_ratio=0.70,
            min_train_trades=max(5, min(15, backtest_trades // 5 or 5)),
        )
        save_json_state(FLOW_COST_RESEARCH_STATE_KEY, _plain_dict(flow_cost))

    enough_full_sample = backtest_trades >= int(min_reference_trades)
    enough_oos = walk_summary["oos_trades"] >= int(min_oos_trades)
    stable_folds = (
        walk_summary["folds"] >= 3
        and walk_summary["positive_folds"] / walk_summary["folds"] >= 0.6
        and walk_summary["weighted_expectancy"] > 0
        and walk_summary["median_profit_factor"] >= 1.1
    )
    candidate_positive = (
        candidate.get("oos_trades", 0) > 0
        and candidate.get("oos_expectancy", 0) > 0
        and candidate.get("oos_profit_factor", 0) >= 1.1
    )

    if not enough_full_sample:
        status = "累積樣本中"
        reason = f"完整交易 {backtest_trades}/{int(min_reference_trades)} 筆，繼續累積，不調整警報參數。"
    elif not enough_oos:
        status = "樣本外不足"
        reason = f"樣本外交易 {walk_summary['oos_trades']}/{int(min_oos_trades)} 筆，不調整警報參數。"
    elif not run_optimisation:
        status = "每日統計完成"
        reason = "固定參數 Walk-forward 已更新；候選參數比較於週五收盤執行。"
    elif stable_folds and candidate_positive:
        status = "候選參數可進入觀察"
        reason = "完整樣本與 Walk-forward 初步通過；只記錄候選，需持續紙上觀察後人工確認。"
    else:
        status = "穩健性未通過"
        reason = "Walk-forward 各區段表現不一致，維持目前警報參數。"

    report = {
        "report_date": report_date,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "history_rows": history_rows,
        "history": history_status,
        "paper_day": _paper_day_summary(report_date),
        "backtest": _plain_dict(full_summary),
        "walk_forward": walk_summary,
        "candidate": candidate,
        "flow_cost": _plain_dict(flow_cost),
        "learning": {
            "auto_apply": False,
            "candidate_refresh": "weekly" if run_optimisation else "daily statistics only",
            "reference_trade_target": int(min_reference_trades),
            "oos_trade_target": int(min_oos_trades),
            "enough_full_sample": enough_full_sample,
            "enough_oos": enough_oos,
            "stable_folds": stable_folds,
            "reason": reason,
        },
    }
    report["learning"] = process_research_learning(
        report,
        default_parameters=kwargs,
        allow_auto_apply=allow_auto_learning,
    )
    save_research_report(report)
    return report


def format_close_research_report(report):
    paper = report.get("paper_day", {})
    backtest = report.get("backtest", {})
    walk = report.get("walk_forward", {})
    history = report.get("history", {})
    candidate = report.get("candidate", {})
    learning = report.get("learning", {})
    flow_cost = report.get("flow_cost", {})
    candidate_text = "本日只更新統計；候選參數於週五收盤更新"
    if candidate:
        candidate_text = (
            f"{candidate.get('profile') or '未命名'}｜樣本外 {candidate.get('oos_trades', 0)} 筆｜"
            f"期望值 NT$ {candidate.get('oos_expectancy', 0):,.0f}｜PF {candidate.get('oos_profit_factor', 0):.2f}"
        )

    baseline_flow = flow_cost.get("baseline_oos", {})
    challenger_flow = flow_cost.get("challenger_oos", {})
    flow_text = (
        f"{flow_cost.get('selected_profile') or '無候選'}｜來源 {flow_cost.get('flow_source') or '無'}｜"
        f"原策略期望 {baseline_flow.get('期望值', 0):,.0f} → "
        f"候選 {challenger_flow.get('期望值', 0):,.0f}｜"
        f"PF {challenger_flow.get('Profit Factor', 0):.2f}｜"
        f"樣本外 {challenger_flow.get('交易次數', 0)} 筆"
    )

    return (
        "【微型臺指收盤研究報告】\n"
        f"日期：{report.get('report_date')}\n"
        f"狀態：{report.get('status')}\n"
        f"歷史資料：{report.get('history_rows', 0):,} 根｜"
        f"{history.get('first_ts') or '無'} ～ {history.get('last_ts') or '無'}\n"
        f"目前連續契約：{history.get('active_contract') or '無資料'}｜換月 {history.get('rollovers', 0)} 次\n\n"
        "今日模擬帳本\n"
        f"平倉 {paper.get('trades', 0)} 筆｜損益 NT$ {paper.get('pnl', 0):,.0f}｜"
        f"勝率 {paper.get('win_rate', 0):.2f}%\n\n"
        "累積回測\n"
        f"交易 {backtest.get('交易次數', 0)} 筆｜總損益 NT$ {backtest.get('總損益', 0):,.0f}｜"
        f"期望值 NT$ {backtest.get('期望值', 0):,.0f}\n"
        f"勝率 {backtest.get('勝率', 0):.2f}%｜PF {backtest.get('Profit Factor', 0):.2f}｜"
        f"最大回撤 NT$ {backtest.get('最大回撤', 0):,.0f}\n\n"
        "Walk-forward\n"
        f"正期望區段 {walk.get('positive_folds', 0)}/{walk.get('folds', 0)}｜"
        f"樣本外 {walk.get('oos_trades', 0)} 筆｜加權期望值 NT$ {walk.get('weighted_expectancy', 0):,.0f}\n"
        f"候選：{candidate_text}\n\n"
        "K線＋量流＋成本研究\n"
        f"{flow_text}\n"
        f"結論：{flow_cost.get('reason') or '尚無結果'}\n\n"
        f"正式參數：{learning.get('active_profile') or 'formal-15m'}｜"
        f"挑戰者觀察 {learning.get('candidate_confirmations', 0)}/"
        f"{learning.get('required_confirmations', 3)}\n"
        f"學習結論：{learning.get('reason') or '維持目前參數。'}\n"
        "提醒性質：歷史模擬與紙上交易統計，不代表未來獲利。"
    )
