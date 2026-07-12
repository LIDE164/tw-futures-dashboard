import json
import sqlite3
from pathlib import Path

from paper_broker import PaperTrade


DB_PATH = Path("data/trading.db")


def _connect(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            contract_code TEXT,
            bar_time TEXT,
            action TEXT,
            score INTEGER,
            label TEXT,
            price REAL,
            entry_price REAL,
            stop_loss_price REAL,
            take_profit_price REAL,
            reasons TEXT,
            message TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            event_type TEXT,
            title TEXT,
            body TEXT,
            status TEXT,
            detail TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS worker_heartbeats (
            worker_name TEXT PRIMARY KEY,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT,
            detail TEXT
        )
        """
    )
    return conn


def load_json_state(key, default=None):
    default = default if default is not None else {}
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return default


def save_json_state(key, value):
    payload = json.dumps(value, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, payload),
        )


def load_worker_trade_state():
    return load_json_state("signal_worker_trade", {})


def save_worker_trade_state(state):
    save_json_state("signal_worker_trade", dict(state or {}))


def load_paper_broker_state():
    return load_json_state("paper_broker", {})


def save_paper_broker_state(broker):
    save_json_state(
        "paper_broker",
        {
            "position": broker.position,
            "entry_price": broker.entry_price,
            "stop_loss_price": broker.stop_loss_price,
            "take_profit_price": broker.take_profit_price,
            "realized_pnl": broker.realized_pnl,
            "trades": [trade.__dict__ for trade in broker.trades],
        },
    )


def restore_paper_broker_state(broker):
    state = load_paper_broker_state()
    if not state:
        return broker

    broker.position = int(state.get("position") or 0)
    broker.entry_price = float(state.get("entry_price") or 0)
    broker.stop_loss_price = float(state.get("stop_loss_price") or 0)
    broker.take_profit_price = float(state.get("take_profit_price") or 0)
    broker.realized_pnl = float(state.get("realized_pnl") or 0)
    broker.trades = [
        PaperTrade(
            time=item.get("time", ""),
            action=item.get("action", ""),
            price=float(item.get("price") or 0),
            quantity=int(item.get("quantity") or 0),
            pnl=float(item.get("pnl") or 0),
            note=item.get("note", ""),
            stop_loss_price=float(item.get("stop_loss_price") or 0),
            take_profit_price=float(item.get("take_profit_price") or 0),
        )
        for item in state.get("trades", [])
    ]
    return broker


def clear_paper_broker_state():
    with _connect() as conn:
        conn.execute("DELETE FROM app_state WHERE key = ?", ("paper_broker",))


def save_signal(signal):
    payload = dict(signal)
    payload["reasons"] = json.dumps(payload.get("reasons", []), ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO signals (
                signal_key, contract_code, bar_time, action, score, label, price,
                entry_price, stop_loss_price, take_profit_price, reasons, message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("signal_key"),
                payload.get("contract_code"),
                payload.get("bar_time"),
                payload.get("action"),
                payload.get("score"),
                payload.get("label"),
                payload.get("price"),
                payload.get("entry_price"),
                payload.get("stop_loss_price"),
                payload.get("take_profit_price"),
                payload.get("reasons"),
                payload.get("message"),
            ),
        )


def save_alert(alert):
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO alerts (alert_key, event_type, title, body, status, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                alert.get("alert_key"),
                alert.get("event_type"),
                alert.get("title"),
                alert.get("body"),
                alert.get("status", "created"),
                alert.get("detail", ""),
            ),
        )
    return cur.rowcount > 0


def update_heartbeat(worker_name, status, detail=""):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO worker_heartbeats (worker_name, updated_at, status, detail)
            VALUES (?, CURRENT_TIMESTAMP, ?, ?)
            ON CONFLICT(worker_name) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP,
                status = excluded.status,
                detail = excluded.detail
            """,
            (worker_name, status, detail),
        )


def get_worker_heartbeat(worker_name="signal_worker"):
    with _connect() as conn:
        row = conn.execute(
            "SELECT worker_name, updated_at, status, detail FROM worker_heartbeats WHERE worker_name = ?",
            (worker_name,),
        ).fetchone()
    return dict(row) if row else {}


def get_recent_alerts(limit=20):
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT created_at, event_type, title, body, status, detail
            FROM alerts
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_recent_signals(limit=20):
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT created_at, contract_code, bar_time, action, score, label, price,
                   entry_price, stop_loss_price, take_profit_price, message
            FROM signals
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]
