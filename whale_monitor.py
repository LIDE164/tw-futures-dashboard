from collections import OrderedDict
from datetime import datetime, timedelta
from threading import RLock

import pandas as pd


def _number(value, default=0.0):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return float(default)


def _first(values):
    try:
        return _number(values[0]) if values else 0.0
    except (TypeError, IndexError):
        return 0.0


def _round_tick(value, tick=5):
    return round(float(value or 0) / tick) * tick


def _session_key(now):
    now = now or datetime.now()
    minutes = now.hour * 60 + now.minute
    if 8 * 60 + 45 <= minutes <= 13 * 60 + 45:
        return f"{now.date().isoformat()}:day"
    if minutes >= 15 * 60:
        return f"{now.date().isoformat()}:night"
    if minutes <= 5 * 60:
        return f"{(now.date() - timedelta(days=1)).isoformat()}:night"
    return f"{now.date().isoformat()}:closed"


class WhaleFlowMonitor:
    """Collect futures ticks without placing orders and expose order-flow snapshots."""

    def __init__(
        self,
        delta_ratio_threshold=-0.08,
        sell_streak_minutes=4,
        min_tx_volume=100,
        level1_delta_ratio_threshold=-0.04,
        level1_sell_streak_minutes=2,
        level1_min_tx_volume=50,
        min_completeness_ratio=0.95,
        min_classification_ratio=0.80,
        burst_window_minutes=3,
        burst_delta_ratio_threshold=-0.12,
        burst_small_delta_ratio_threshold=-0.08,
        burst_min_tx_volume=300,
    ):
        self.delta_ratio_threshold = float(delta_ratio_threshold)
        self.sell_streak_minutes = int(sell_streak_minutes)
        self.min_tx_volume = int(min_tx_volume)
        self.level1_delta_ratio_threshold = float(level1_delta_ratio_threshold)
        self.level1_sell_streak_minutes = int(level1_sell_streak_minutes)
        self.level1_min_tx_volume = int(level1_min_tx_volume)
        self.min_completeness_ratio = float(min_completeness_ratio)
        self.min_classification_ratio = float(min_classification_ratio)
        self.burst_window_minutes = max(2, int(burst_window_minutes))
        self.burst_delta_ratio_threshold = float(burst_delta_ratio_threshold)
        self.burst_small_delta_ratio_threshold = float(burst_small_delta_ratio_threshold)
        self.burst_min_tx_volume = float(burst_min_tx_volume)
        self._lock = RLock()
        self._code_to_product = {}
        self._session_key = ""
        self._products = {}
        self._active_alert_level = 0
        self._alert_episode = 1
        self._last_positive_at = ""
        self._events = []

    @staticmethod
    def _empty_product():
        return {
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "neutral_volume": 0.0,
            "amount": 0.0,
            "volume": 0.0,
            "last_price": 0.0,
            "last_tick_at": "",
            "exchange_baseline_set": False,
            "exchange_volume_baseline": 0.0,
            "exchange_total_volume": 0.0,
            "bid_price": 0.0,
            "ask_price": 0.0,
            "bid_volume": 0.0,
            "ask_volume": 0.0,
            "minutes": OrderedDict(),
        }

    def register_contract(self, product_root, contract):
        product_root = str(product_root or "").upper()
        aliases = {
            str(getattr(contract, name, "") or "").upper()
            for name in ("code", "symbol", "target_code")
        }
        with self._lock:
            for alias in aliases:
                if alias:
                    self._code_to_product[alias] = product_root
            self._products.setdefault(product_root, self._empty_product())

    def _roll_session(self, now):
        key = _session_key(now)
        if key == self._session_key:
            return
        self._session_key = key
        for product in list(self._products):
            self._products[product] = self._empty_product()
        self._active_alert_level = 0
        self._alert_episode = 1
        self._last_positive_at = ""
        self._events = []

    def _product(self, code):
        code = str(code or "").upper()
        product = self._code_to_product.get(code)
        if product:
            return product
        for root in ("TXF", "MXF", "TMF"):
            if code.startswith(root):
                return root
        return ""

    @staticmethod
    def _tick_time(tick):
        value = getattr(tick, "datetime", None)
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        return datetime.now()

    @staticmethod
    def _tick_side(tick, price, product):
        tick_type = getattr(tick, "tick_type", 0)
        raw_type = str(getattr(tick_type, "value", tick_type)).lower()
        if raw_type in {"1", "buy", "b"} or "buy" in raw_type:
            return "buy"
        if raw_type in {"2", "sell", "s"} or "sell" in raw_type:
            return "sell"

        ask = _number(getattr(tick, "ask_price", 0)) or _number(product.get("ask_price"))
        bid = _number(getattr(tick, "bid_price", 0)) or _number(product.get("bid_price"))
        if ask and price >= ask:
            return "buy"
        if bid and price <= bid:
            return "sell"
        previous = _number(product.get("last_price"))
        if previous and price > previous:
            return "buy"
        if previous and price < previous:
            return "sell"
        return "neutral"

    def on_tick(self, exchange, tick):
        product_root = self._product(getattr(tick, "code", ""))
        if not product_root or bool(getattr(tick, "simtrade", False)):
            return
        now = self._tick_time(tick)
        price = _number(getattr(tick, "close", 0))
        volume = _number(getattr(tick, "volume", 0))
        if price <= 0 or volume <= 0:
            return

        with self._lock:
            self._roll_session(now)
            product = self._products.setdefault(product_root, self._empty_product())
            side = self._tick_side(tick, price, product)
            exchange_total = _number(getattr(tick, "total_volume", 0))
            if exchange_total > 0:
                baseline = _number(product.get("exchange_volume_baseline"))
                if not product.get("exchange_baseline_set") or exchange_total < baseline:
                    previously_received = _number(product.get("volume"))
                    product["exchange_volume_baseline"] = max(
                        0.0,
                        exchange_total - previously_received - volume,
                    )
                    product["exchange_baseline_set"] = True
                product["exchange_total_volume"] = exchange_total
            product[f"{side}_volume"] += volume
            product["amount"] += price * volume
            product["volume"] += volume
            product["last_price"] = price
            product["last_tick_at"] = now.isoformat(timespec="seconds")

            minute_key = now.replace(second=0, microsecond=0).isoformat(timespec="minutes")
            minute = product["minutes"].setdefault(
                minute_key,
                {
                    "buy": 0.0,
                    "sell": 0.0,
                    "neutral": 0.0,
                    "close": price,
                    "received": 0.0,
                    "exchange_baseline": max(0.0, exchange_total - volume) if exchange_total else 0.0,
                    "exchange_baseline_set": bool(exchange_total),
                    "exchange_total": exchange_total,
                },
            )
            minute[side] += volume
            minute["received"] = _number(minute.get("received")) + volume
            if exchange_total > 0:
                if not minute.get("exchange_baseline_set"):
                    minute["exchange_baseline"] = max(0.0, exchange_total - volume)
                    minute["exchange_baseline_set"] = True
                minute["exchange_total"] = exchange_total
            minute["close"] = price
            while len(product["minutes"]) > 90:
                product["minutes"].popitem(last=False)

    def on_bidask(self, exchange, quote):
        product_root = self._product(getattr(quote, "code", ""))
        if not product_root or bool(getattr(quote, "simtrade", False)):
            return
        with self._lock:
            self._roll_session(datetime.now())
            product = self._products.setdefault(product_root, self._empty_product())
            product["bid_price"] = _first(getattr(quote, "bid_price", []))
            product["ask_price"] = _first(getattr(quote, "ask_price", []))
            product["bid_volume"] = _first(getattr(quote, "bid_volume", []))
            product["ask_volume"] = _first(getattr(quote, "ask_volume", []))

    @staticmethod
    def _sell_streak(product, now):
        current_minute = now.replace(second=0, microsecond=0).isoformat(timespec="minutes")
        completed = [
            value
            for key, value in product.get("minutes", {}).items()
            if key < current_minute
        ]
        streak = 0
        for minute in reversed(completed):
            if _number(minute.get("sell")) > _number(minute.get("buy")):
                streak += 1
            else:
                break
        return streak, len(completed)

    @staticmethod
    def _product_quality(product):
        received = _number(product.get("volume"))
        classified = _number(product.get("buy_volume")) + _number(product.get("sell_volume"))
        baseline = _number(product.get("exchange_volume_baseline"))
        exchange_total = _number(product.get("exchange_total_volume"))
        expected = max(0.0, exchange_total - baseline) if product.get("exchange_baseline_set") else 0.0
        completeness = min(received / expected, 1.0) if expected > 0 else None
        classification = min(classified / received, 1.0) if received > 0 else None
        return {
            "received_volume": received,
            "expected_volume": expected,
            "missing_volume": max(0.0, expected - received),
            "completeness_ratio": completeness,
            "classified_volume": classified,
            "classification_ratio": classification,
        }

    def snapshot(self, now=None):
        now = (now or datetime.now()).replace(tzinfo=None)

        def product_copy(product):
            copied = dict(product or self._empty_product())
            copied["minutes"] = OrderedDict(
                (key, dict(values or {}))
                for key, values in (copied.get("minutes") or {}).items()
            )
            return copied

        with self._lock:
            self._roll_session(now)
            tx = product_copy(self._products.get("TXF"))
            small_root = "MXF" if self._products.get("MXF", {}).get("volume", 0) else "TMF"
            small = product_copy(self._products.get(small_root))
            micro = product_copy(self._products.get("TMF"))

        tx_buy = _number(tx.get("buy_volume"))
        tx_sell = _number(tx.get("sell_volume"))
        tx_total = tx_buy + tx_sell
        tx_delta = tx_buy - tx_sell
        tx_delta_ratio = tx_delta / tx_total if tx_total else 0.0
        streak, completed_minutes = self._sell_streak(small, now)
        burst_end = now.replace(second=0, microsecond=0)
        burst_start = burst_end - timedelta(minutes=self.burst_window_minutes)
        tx_burst = self._aggregate_minutes(tx.get("minutes"), burst_start, burst_end)
        small_burst = self._aggregate_minutes(small.get("minutes"), burst_start, burst_end)
        vwap_source = micro if _number(micro.get("volume")) else small
        vwap = _number(vwap_source.get("amount")) / _number(vwap_source.get("volume")) if _number(vwap_source.get("volume")) else 0.0
        product_quality = {
            "TXF": self._product_quality(tx),
            small_root: self._product_quality(small),
            "TMF": self._product_quality(micro),
        }
        tx_quality = product_quality["TXF"]
        small_quality = product_quality[small_root]

        def quality_passes(quality):
            completeness = quality.get("completeness_ratio")
            classification = quality.get("classification_ratio")
            return bool(
                completeness is not None
                and completeness >= self.min_completeness_ratio
                and classification is not None
                and classification >= self.min_classification_ratio
            )

        data_quality_ready = quality_passes(tx_quality) and quality_passes(small_quality)

        def burst_quality_passes(values):
            completeness = values.get("completeness_ratio")
            classification = values.get("classification_ratio")
            return bool(
                completeness is not None
                and completeness >= self.min_completeness_ratio
                and classification is not None
                and classification >= self.min_classification_ratio
            )

        burst_data_quality_ready = bool(
            tx_burst.get("minute_count") >= self.burst_window_minutes
            and small_burst.get("minute_count") >= self.burst_window_minutes
            and burst_quality_passes(tx_burst)
            and burst_quality_passes(small_burst)
        )
        burst_ready = bool(
            burst_data_quality_ready
            and _number(tx_burst.get("classified_volume")) >= self.burst_min_tx_volume
            and _number(tx_burst.get("delta_ratio")) <= self.burst_delta_ratio_threshold
            and _number(small_burst.get("delta_ratio")) <= self.burst_small_delta_ratio_threshold
            and int(small_burst.get("sell_dominant_minutes") or 0) >= 2
        )

        timestamps = [
            pd.to_datetime(item.get("last_tick_at"), errors="coerce")
            for item in (tx, small, micro)
            if item.get("last_tick_at")
        ]
        timestamps = [item.to_pydatetime() for item in timestamps if not pd.isna(item)]
        latest_tick = max(timestamps) if timestamps else None
        age_seconds = (now - latest_tick).total_seconds() if latest_tick else None
        ready = bool(
            tx_total >= self.min_tx_volume
            and completed_minutes >= self.sell_streak_minutes
            and age_seconds is not None
            and age_seconds <= 90
        )
        return {
            "session_key": self._session_key,
            "tx_delta": tx_delta,
            "tx_delta_ratio": tx_delta_ratio,
            "tx_buy_volume": tx_buy,
            "tx_sell_volume": tx_sell,
            "small_product": small_root,
            "small_sell_streak": streak,
            "small_buy_volume": _number(small.get("buy_volume")),
            "small_sell_volume": _number(small.get("sell_volume")),
            "current_price": _number(micro.get("last_price")) or _number(small.get("last_price")),
            "session_vwap": vwap,
            "last_tick_at": latest_tick.isoformat(timespec="seconds") if latest_tick else "",
            "stream_age_seconds": age_seconds,
            "stream_ready": bool(age_seconds is not None and age_seconds <= 90),
            "data_quality_ready": data_quality_ready,
            "product_quality": product_quality,
            "tx_completeness_ratio": tx_quality.get("completeness_ratio"),
            "tx_classification_ratio": tx_quality.get("classification_ratio"),
            "small_completeness_ratio": small_quality.get("completeness_ratio"),
            "small_classification_ratio": small_quality.get("classification_ratio"),
            "min_completeness_ratio": self.min_completeness_ratio,
            "min_classification_ratio": self.min_classification_ratio,
            "tx_total_volume": tx_total,
            "completed_minutes": completed_minutes,
            "ready": ready,
            "bearish_delta": tx_delta_ratio <= self.delta_ratio_threshold,
            "sell_streak_confirmed": streak >= self.sell_streak_minutes,
            "delta_ratio_threshold": self.delta_ratio_threshold,
            "sell_streak_minutes": self.sell_streak_minutes,
            "min_tx_volume": self.min_tx_volume,
            "level1_delta_ratio_threshold": self.level1_delta_ratio_threshold,
            "level1_sell_streak_minutes": self.level1_sell_streak_minutes,
            "level1_min_tx_volume": self.level1_min_tx_volume,
            "burst_window_minutes": self.burst_window_minutes,
            "burst_ready": burst_ready,
            "burst_data_quality_ready": burst_data_quality_ready,
            "burst_tx_volume": _number(tx_burst.get("classified_volume")),
            "burst_tx_delta": _number(tx_burst.get("delta")),
            "burst_tx_delta_ratio": _number(tx_burst.get("delta_ratio")),
            "burst_small_delta": _number(small_burst.get("delta")),
            "burst_small_delta_ratio": _number(small_burst.get("delta_ratio")),
            "burst_small_sell_minutes": int(small_burst.get("sell_dominant_minutes") or 0),
            "burst_tx_completeness_ratio": tx_burst.get("completeness_ratio"),
            "burst_tx_classification_ratio": tx_burst.get("classification_ratio"),
            "burst_small_completeness_ratio": small_burst.get("completeness_ratio"),
            "burst_small_classification_ratio": small_burst.get("classification_ratio"),
            "burst_delta_ratio_threshold": self.burst_delta_ratio_threshold,
            "burst_small_delta_ratio_threshold": self.burst_small_delta_ratio_threshold,
            "burst_min_tx_volume": self.burst_min_tx_volume,
        }

    def transition_level(self, level, now=None, reset_after_minutes=5):
        """Return True only for a new alert episode or a level escalation."""
        now = (now or datetime.now()).replace(tzinfo=None)
        level = int(level or 0)
        with self._lock:
            if level <= 0:
                last_positive = pd.to_datetime(self._last_positive_at, errors="coerce")
                if (
                    self._active_alert_level > 0
                    and not pd.isna(last_positive)
                    and (now - last_positive.to_pydatetime()).total_seconds()
                    >= reset_after_minutes * 60
                ):
                    self._active_alert_level = 0
                    self._alert_episode += 1
                return False

            self._last_positive_at = now.isoformat(timespec="seconds")
            if level <= self._active_alert_level:
                return False
            self._active_alert_level = level
            return True

    def record_event(self, event):
        event = dict(event or {})
        if not event:
            return
        with self._lock:
            event["episode"] = self._alert_episode
            self._events.append(event)
            self._events = self._events[-500:]

    def alert_episode(self):
        with self._lock:
            return self._alert_episode

    @staticmethod
    def _aggregate_minutes(minutes, start, end):
        selected = []
        for key, values in (minutes or {}).items():
            minute_at = pd.to_datetime(key, errors="coerce")
            if pd.isna(minute_at):
                continue
            minute_at = minute_at.to_pydatetime()
            if start <= minute_at < end:
                selected.append((minute_at, dict(values or {})))
        selected.sort(key=lambda item: item[0])

        buy = sum(_number(values.get("buy")) for _, values in selected)
        sell = sum(_number(values.get("sell")) for _, values in selected)
        neutral = sum(_number(values.get("neutral")) for _, values in selected)
        received = sum(
            _number(values.get("received"))
            or _number(values.get("buy")) + _number(values.get("sell")) + _number(values.get("neutral"))
            for _, values in selected
        )
        expected = sum(
            max(
                0.0,
                _number(values.get("exchange_total"))
                - _number(values.get("exchange_baseline")),
            )
            for _, values in selected
            if values.get("exchange_baseline_set")
        )
        classified = buy + sell
        delta = buy - sell
        ratio = delta / classified if classified else 0.0
        sell_minutes = sum(
            1 for _, values in selected if _number(values.get("sell")) > _number(values.get("buy"))
        )
        buy_minutes = sum(
            1 for _, values in selected if _number(values.get("buy")) > _number(values.get("sell"))
        )
        max_sell_streak = 0
        current_streak = 0
        for _, values in selected:
            if _number(values.get("sell")) > _number(values.get("buy")):
                current_streak += 1
                max_sell_streak = max(max_sell_streak, current_streak)
            else:
                current_streak = 0
        return {
            "buy_volume": buy,
            "sell_volume": sell,
            "neutral_volume": neutral,
            "classified_volume": classified,
            "received_volume": received,
            "expected_volume": expected,
            "completeness_ratio": min(received / expected, 1.0) if expected else None,
            "classification_ratio": min(classified / received, 1.0) if received else None,
            "delta": delta,
            "delta_ratio": ratio,
            "minute_count": len(selected),
            "sell_dominant_minutes": sell_minutes,
            "buy_dominant_minutes": buy_minutes,
            "max_sell_streak": max_sell_streak,
        }

    def hourly_summary(self, now=None):
        """Aggregate the last 60 completed one-minute buckets and alert counts."""
        now = (now or datetime.now()).replace(tzinfo=None)
        end = now.replace(second=0, microsecond=0)
        start = end - timedelta(minutes=60)
        with self._lock:
            products = {
                root: self._aggregate_minutes(product.get("minutes"), start, end)
                for root, product in self._products.items()
                if root in {"TXF", "MXF", "TMF"}
            }
            events = [dict(item) for item in self._events]

        for root in ("TXF", "MXF", "TMF"):
            products.setdefault(root, self._aggregate_minutes({}, start, end))
        event_counts = {1: 0, 2: 0, 3: 0}
        for event in events:
            created_at = pd.to_datetime(event.get("created_at"), errors="coerce")
            if pd.isna(created_at):
                continue
            created_at = created_at.to_pydatetime()
            if start <= created_at < end:
                level = int(event.get("level") or 0)
                if level in event_counts:
                    event_counts[level] += 1
        coverage = max((item["minute_count"] for item in products.values()), default=0)
        return {
            "session_key": self._session_key,
            "period_start": start.isoformat(timespec="minutes"),
            "period_end": end.isoformat(timespec="minutes"),
            "coverage_minutes": coverage,
            "products": products,
            "event_counts": event_counts,
            "snapshot": self.snapshot(now),
        }

    def minute_records(self, last_minutes=3):
        """Return recent minute buckets for durable true order-flow storage."""
        cutoff = datetime.now().replace(second=0, microsecond=0) - timedelta(
            minutes=max(1, int(last_minutes or 1))
        )
        rows = []
        with self._lock:
            session_key = self._session_key
            for root, product in self._products.items():
                for minute_key, values in product.get("minutes", {}).items():
                    minute_at = pd.to_datetime(minute_key, errors="coerce")
                    if pd.isna(minute_at) or minute_at.to_pydatetime() < cutoff:
                        continue
                    received = _number(values.get("received")) or (
                        _number(values.get("buy"))
                        + _number(values.get("sell"))
                        + _number(values.get("neutral"))
                    )
                    expected = max(
                        0.0,
                        _number(values.get("exchange_total"))
                        - _number(values.get("exchange_baseline")),
                    )
                    classified = _number(values.get("buy")) + _number(values.get("sell"))
                    rows.append(
                        {
                            "product_root": root,
                            "session_key": session_key,
                            "ts": minute_at.to_pydatetime(),
                            "buy_volume": _number(values.get("buy")),
                            "sell_volume": _number(values.get("sell")),
                            "neutral_volume": _number(values.get("neutral")),
                            "received_volume": received,
                            "expected_volume": expected,
                            "completeness_ratio": min(received / expected, 1.0) if expected else None,
                            "classification_ratio": min(classified / received, 1.0) if received else None,
                            "close": _number(values.get("close")),
                        }
                    )
        return rows

    def export_state(self):
        with self._lock:
            products = {}
            for root, product in self._products.items():
                products[root] = {
                    **{key: value for key, value in product.items() if key != "minutes"},
                    "minutes": dict(product.get("minutes", {})),
                }
            return {
                "session_key": self._session_key,
                "products": products,
                "active_alert_level": self._active_alert_level,
                "alert_episode": self._alert_episode,
                "last_positive_at": self._last_positive_at,
                "events": self._events[-500:],
            }

    def restore_state(self, state):
        state = dict(state or {})
        if state.get("session_key") != _session_key(datetime.now()):
            return
        with self._lock:
            self._session_key = state.get("session_key", "")
            self._active_alert_level = int(state.get("active_alert_level") or 0)
            self._alert_episode = int(state.get("alert_episode") or 1)
            self._last_positive_at = str(state.get("last_positive_at") or "")
            self._events = list(state.get("events") or [])[-500:]
            for root, saved in (state.get("products") or {}).items():
                product = self._empty_product()
                product.update(saved or {})
                product["minutes"] = OrderedDict((saved or {}).get("minutes") or {})
                self._products[str(root).upper()] = product


def derive_downside_levels(bars, current_price, atr_points=0):
    current_price = float(current_price or 0)
    atr_points = float(atr_points or 0)
    gap = _round_tick(max(30.0, atr_points * 0.5))
    minimum_distance = max(10.0, atr_points * 0.12)
    candidates = []
    if bars is not None and not bars.empty and "Low" in bars.columns:
        lows = pd.to_numeric(bars["Low"], errors="coerce").dropna().tail(64).reset_index(drop=True)
        for index in range(1, len(lows) - 1):
            if lows.iloc[index] <= lows.iloc[index - 1] and lows.iloc[index] <= lows.iloc[index + 1]:
                candidates.append(_round_tick(lows.iloc[index]))
        for window in (4, 12, 32):
            if len(lows) >= window:
                candidates.append(_round_tick(lows.tail(window).min()))

    candidates = sorted(
        {
            value
            for value in candidates
            if value > 0 and value <= current_price - minimum_distance
        },
        reverse=True,
    )
    first = candidates[0] if candidates else _round_tick(current_price - gap)
    second_options = [value for value in candidates[1:] if value <= first - max(20.0, gap * 0.6)]
    second = second_options[0] if second_options else _round_tick(first - gap)
    target_one = _round_tick(second - gap)
    target_two = _round_tick(target_one - gap)
    return {
        "first_support": first,
        "second_support": second,
        "pullback_targets": [target_one, target_two],
        "level_gap": gap,
    }


def build_distribution_event(flow, realtime, bars, tech_data=None, now=None):
    flow = dict(flow or {})
    realtime = dict(realtime or {})
    tech_data = dict(tech_data or {})
    if not flow.get("stream_ready") or not flow.get("data_quality_ready"):
        return None

    current_price = _number(realtime.get("current_price"))
    vwap = _number(flow.get("session_vwap")) or _number(realtime.get("vwap"))
    if current_price <= 0:
        return None

    tx_total = _number(flow.get("tx_total_volume"))
    delta_ratio = _number(flow.get("tx_delta_ratio"))
    sell_streak = int(flow.get("small_sell_streak") or 0)
    level1_min_volume = float(flow.get("level1_min_tx_volume", 50) or 50)
    level1_delta_threshold = float(flow.get("level1_delta_ratio_threshold", -0.04) or -0.04)
    level1_streak = int(flow.get("level1_sell_streak_minutes", 2) or 2)
    cumulative_level1_ready = bool(
        tx_total >= level1_min_volume
        and delta_ratio <= level1_delta_threshold
        and sell_streak >= level1_streak
    )
    burst_ready = bool(flow.get("burst_ready"))
    level1_ready = cumulative_level1_ready or burst_ready
    if not level1_ready:
        return None

    reference_price = max(current_price, vwap)
    level_bars = bars.iloc[:-1] if bars is not None and len(bars) > 1 else bars
    levels = derive_downside_levels(level_bars, reference_price, tech_data.get("ATR") or 0)
    first = levels["first_support"]
    second = levels["second_support"]
    latest_close = _number(bars["Close"].iloc[-1]) if bars is not None and not bars.empty else current_price
    level2_min_volume = float(flow.get("min_tx_volume", 100) or 100)
    level2_delta_threshold = float(flow.get("delta_ratio_threshold", -0.08) or -0.08)
    level2_streak = int(flow.get("sell_streak_minutes", 4) or 4)
    cumulative_level2_ready = bool(
        tx_total >= level2_min_volume
        and delta_ratio <= level2_delta_threshold
        and sell_streak >= level2_streak
        and vwap > 0
        and current_price < vwap
    )
    burst_window = int(flow.get("burst_window_minutes") or 3)
    burst_tx_ratio = _number(flow.get("burst_tx_delta_ratio"))
    burst_small_ratio = _number(flow.get("burst_small_delta_ratio"))
    burst_sell_minutes = int(flow.get("burst_small_sell_minutes") or 0)
    burst_level2_ready = bool(
        burst_ready
        and burst_tx_ratio <= -0.18
        and burst_small_ratio <= -0.12
        and burst_sell_minutes >= burst_window
        and vwap > 0
        and current_price < vwap
    )
    level2_ready = cumulative_level2_ready or burst_level2_ready
    if burst_ready and cumulative_level1_ready:
        trigger_mode = "累積 Delta＋短線急殺"
    elif burst_ready:
        trigger_mode = f"最近 {burst_window} 分鐘急殺"
    else:
        trigger_mode = "交易時段累積 Delta"
    level = 1
    if level2_ready:
        level = 2
    if level2_ready and (latest_close < first or current_price < second):
        level = 3

    if level == 1:
        status_key = "early_distribution"
        judgement = "大台主動賣量開始增加，小台賣量連續占優，屬於早期疑似派發。"
        suggestion = f"先停止追價做多，觀察日盤 VWAP 與第一關 {first:,.0f}；尚未構成做空確認。"
    elif level == 2 and current_price >= first:
        status_key = "watch"
        judgement = "賣壓增強，但尚未跌破第一關。"
        suggestion = f"停止追多，跌破 {first:,.0f} 並且下一根完整 15 分 K 站不回時，再確認空方。"
    elif level == 2:
        status_key = "testing_first"
        judgement = "盤中已測試第一關下方，但完整 15 分 K 尚未確認跌破。"
        suggestion = f"停止追多，等待收盤確認；下一關觀察 {second:,.0f}。"
    elif current_price >= second:
        status_key = "first_broken"
        judgement = "第一關已跌破，且完整 15 分 K 尚未站回，空方動能提高。"
        suggestion = f"避免搶反彈；第二關 {second:,.0f} 失守後，留意下方回測區。"
    else:
        status_key = "second_broken"
        judgement = "第二關也已失守，賣壓進入延伸階段。"
        targets = levels["pullback_targets"]
        suggestion = f"避免追多，留意 {targets[0]:,.0f}、{targets[1]:,.0f} 回測區與急跌後反抽。"

    return {
        "event_type": f"WHALE_DISTRIBUTION_L{level}",
        "level": level,
        "session_key": flow.get("session_key", ""),
        "status_key": status_key,
        "trigger_mode": trigger_mode,
        "tx_delta": _number(flow.get("tx_delta")),
        "tx_delta_ratio": delta_ratio,
        "tx_buy_volume": _number(flow.get("tx_buy_volume")),
        "tx_sell_volume": _number(flow.get("tx_sell_volume")),
        "small_product": flow.get("small_product", "MXF"),
        "small_sell_streak": int(flow.get("small_sell_streak") or 0),
        "burst_window_minutes": burst_window,
        "burst_tx_delta": _number(flow.get("burst_tx_delta")),
        "burst_tx_delta_ratio": burst_tx_ratio,
        "burst_small_delta": _number(flow.get("burst_small_delta")),
        "burst_small_delta_ratio": burst_small_ratio,
        "burst_small_sell_minutes": burst_sell_minutes,
        "current_price": current_price,
        "session_vwap": vwap,
        "first_support": first,
        "second_support": second,
        "pullback_targets": levels["pullback_targets"],
        "judgement": judgement,
        "suggestion": suggestion,
        "last_tick_at": flow.get("last_tick_at", ""),
        "tx_completeness_ratio": flow.get("tx_completeness_ratio"),
        "tx_classification_ratio": flow.get("tx_classification_ratio"),
        "small_completeness_ratio": flow.get("small_completeness_ratio"),
        "small_classification_ratio": flow.get("small_classification_ratio"),
        "created_at": (now or datetime.now()).isoformat(timespec="seconds"),
    }


def build_test_distribution_event(price=45520, level=2):
    price = float(price or 45520)
    level = max(1, min(3, int(level or 2)))
    first = _round_tick(price - 20)
    second = _round_tick(price - 80)
    judgements = {
        1: "大台主動賣量開始增加，小台賣量連續占優，屬於早期疑似派發。",
        2: "賣壓增強，但尚未跌破第一關。",
        3: "第一關已跌破，且完整 15 分 K 尚未站回，空方動能提高。",
    }
    suggestions = {
        1: f"先停止追價做多，觀察日盤 VWAP 與第一關 {first:,.0f}；尚未構成做空確認。",
        2: f"停止追多，跌破 {first:,.0f} 並且下一根完整 15 分 K 站不回時，再確認空方。",
        3: f"避免搶反彈；第二關 {second:,.0f} 失守後，留意下方回測區。",
    }
    return {
        "event_type": f"WHALE_DISTRIBUTION_L{level}",
        "level": level,
        "session_key": f"{datetime.now().date().isoformat()}:test",
        "status_key": {1: "early_distribution", 2: "watch", 3: "first_broken"}[level],
        "trigger_mode": "最近 3 分鐘急殺（測試）",
        "tx_delta": {1: -420, 2: -1280, 3: -2380}[level],
        "tx_delta_ratio": {1: -0.052, 2: -0.126, 3: -0.214}[level],
        "tx_buy_volume": 4200,
        "tx_sell_volume": {1: 4620, 2: 5480, 3: 6580}[level],
        "small_product": "MXF",
        "small_sell_streak": {1: 2, 2: 4, 3: 7}[level],
        "burst_window_minutes": 3,
        "burst_tx_delta": {1: -360, 2: -820, 3: -1280}[level],
        "burst_tx_delta_ratio": {1: -0.13, 2: -0.21, 3: -0.28}[level],
        "burst_small_delta": {1: -520, 2: -1250, 3: -1880}[level],
        "burst_small_delta_ratio": {1: -0.10, 2: -0.18, 3: -0.25}[level],
        "burst_small_sell_minutes": 3,
        "current_price": price,
        "session_vwap": price + 55,
        "first_support": first,
        "second_support": second,
        "pullback_targets": [_round_tick(price - 140), _round_tick(price - 190)],
        "judgement": judgements[level],
        "suggestion": suggestions[level],
        "last_tick_at": datetime.now().isoformat(timespec="seconds"),
        "tx_completeness_ratio": 0.998,
        "tx_classification_ratio": 0.972,
        "small_completeness_ratio": 0.991,
        "small_classification_ratio": 0.948,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
