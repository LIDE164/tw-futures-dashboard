import pandas as pd

from backtester import run_backtest


FLOW_COST_PROFILES = [
    {
        "profile": "量流成本寬鬆",
        "use_flow_cost_filter": True,
        "flow_lookback_bars": 3,
        "min_flow_ratio": 0.02,
        "min_flow_volume_intensity": 0.70,
        "max_cost_distance_atr": 1.50,
        "require_cost_slope": False,
        "min_flow_close_location": 0.00,
    },
    {
        "profile": "量流成本平衡",
        "use_flow_cost_filter": True,
        "flow_lookback_bars": 4,
        "min_flow_ratio": 0.04,
        "min_flow_volume_intensity": 0.80,
        "max_cost_distance_atr": 1.20,
        "require_cost_slope": True,
        "min_flow_close_location": 0.00,
    },
    {
        "profile": "量流成本嚴格",
        "use_flow_cost_filter": True,
        "flow_lookback_bars": 4,
        "min_flow_ratio": 0.08,
        "min_flow_volume_intensity": 0.90,
        "max_cost_distance_atr": 1.00,
        "require_cost_slope": True,
        "min_flow_close_location": 0.10,
    },
]


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _quality_score(summary, min_trades=10):
    trades = int(summary.get("交易次數") or 0)
    if trades < int(min_trades):
        return -1_000_000 + trades
    expectancy = _number(summary.get("期望值"))
    avg_loss = abs(_number(summary.get("平均虧損")))
    expectancy_r = expectancy / avg_loss if avg_loss else 0.0
    profit_factor = min(_number(summary.get("Profit Factor")), 3.0)
    total_pnl = _number(summary.get("總損益"))
    drawdown = abs(_number(summary.get("最大回撤")))
    recovery = total_pnl / drawdown if drawdown else 0.0
    return expectancy_r * 100 + profit_factor * 20 + recovery * 10 + min(trades, 50) * 0.2


def run_flow_cost_comparison(history, base_kwargs=None, train_ratio=0.70, min_train_trades=10):
    """Select a flow/cost profile on training data, then compare once out of sample."""
    if history is None or history.empty or len(history) < 1000:
        return {"status": "資料不足", "reason": "至少需要 1,000 根原始 K 線。"}
    data = history.copy().sort_values("ts").reset_index(drop=True)
    split_at = int(len(data) * float(train_ratio))
    if split_at < 600 or len(data) - split_at < 300:
        return {"status": "資料不足", "reason": "訓練段或樣本外區間不足。"}
    train = data.iloc[:split_at].copy()
    test = data.iloc[split_at:].copy()
    base_kwargs = dict(base_kwargs or {})

    candidates = []
    for profile in FLOW_COST_PROFILES:
        kwargs = {**base_kwargs, **{key: value for key, value in profile.items() if key != "profile"}}
        _, _, summary = run_backtest(train, **kwargs)
        if summary.get("error"):
            continue
        candidates.append(
            {
                "profile": profile["profile"],
                "parameters": {key: value for key, value in profile.items() if key != "profile"},
                "summary": summary,
                "quality_score": _quality_score(summary, min_train_trades),
            }
        )
    if not candidates:
        return {"status": "無候選", "reason": "量流成本候選均無法完成訓練回測。"}
    selected = max(candidates, key=lambda item: item["quality_score"])

    baseline_kwargs = {**base_kwargs, "use_flow_cost_filter": False}
    challenger_kwargs = {**base_kwargs, **selected["parameters"]}
    _, _, baseline_oos = run_backtest(test, **baseline_kwargs)
    _, _, challenger_oos = run_backtest(test, **challenger_kwargs)
    if baseline_oos.get("error") or challenger_oos.get("error"):
        return {"status": "樣本外失敗", "reason": baseline_oos.get("error") or challenger_oos.get("error")}

    baseline_expectancy = _number(baseline_oos.get("期望值"))
    challenger_expectancy = _number(challenger_oos.get("期望值"))
    uplift = challenger_expectancy - baseline_expectancy
    uplift_ratio = uplift / abs(baseline_expectancy) if baseline_expectancy else 0.0
    enough_oos = int(challenger_oos.get("交易次數") or 0) >= 10
    passed = bool(
        enough_oos
        and challenger_expectancy > max(0.0, baseline_expectancy * 1.10)
        and _number(challenger_oos.get("Profit Factor")) >= 1.10
        and abs(_number(challenger_oos.get("最大回撤")))
        <= max(abs(_number(baseline_oos.get("最大回撤"))) * 1.25, 1.0)
    )
    true_flow_rows = 0
    if "FlowCompleteness" in data.columns and "FlowClassification" in data.columns:
        true_flow_rows = int(
            (
                pd.to_numeric(data["FlowCompleteness"], errors="coerce").ge(0.95)
                & pd.to_numeric(data["FlowClassification"], errors="coerce").ge(0.80)
            ).sum()
        )
    source = "mixed_true_tick_proxy" if true_flow_rows else "kbar_proxy"
    return {
        "status": "候選通過" if passed else "僅供觀察",
        "flow_source": source,
        "true_flow_rows": true_flow_rows,
        "selected_profile": selected["profile"],
        "selected_parameters": selected["parameters"],
        "train": selected["summary"],
        "baseline_oos": baseline_oos,
        "challenger_oos": challenger_oos,
        "expectancy_uplift": round(uplift, 2),
        "expectancy_uplift_ratio": round(uplift_ratio, 4),
        "passed": passed,
        "auto_apply": False,
        "reason": (
            "樣本外期望值、PF、交易筆數與回撤均通過，先進入紙上觀察。"
            if passed
            else "未同時通過樣本外期望值、PF、交易筆數與回撤門檻，不調整正式警報。"
        ),
    }


def comparison_table(result):
    if not result or "baseline_oos" not in result:
        return pd.DataFrame()
    rows = []
    for name, summary in (
        ("原策略樣本外", result.get("baseline_oos") or {}),
        (f"{result.get('selected_profile')}樣本外", result.get("challenger_oos") or {}),
    ):
        rows.append(
            {
                "模型": name,
                "交易次數": summary.get("交易次數", 0),
                "勝率": summary.get("勝率", 0),
                "期望值": summary.get("期望值", 0),
                "Profit Factor": summary.get("Profit Factor", 0),
                "最大回撤": summary.get("最大回撤", 0),
                "多單交易": summary.get("多單交易次數", 0),
                "空單交易": summary.get("空單交易次數", 0),
            }
        )
    return pd.DataFrame(rows)
