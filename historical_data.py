import sqlite3
from contextlib import contextmanager
from datetime import datetime, time
from pathlib import Path

import pandas as pd


DB_PATH = Path("data/market_history.db")
REQUIRED_COLUMNS = ("ts", "Open", "High", "Low", "Close", "Volume")


@contextmanager
def _connect(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS history_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS contracts (
            product_root TEXT NOT NULL,
            contract_code TEXT NOT NULL,
            delivery_date TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            active_from TEXT,
            active_to TEXT,
            PRIMARY KEY (product_root, contract_code)
        );

        CREATE TABLE IF NOT EXISTS contract_kbars (
            product_root TEXT NOT NULL,
            contract_code TEXT NOT NULL,
            ts TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            amount REAL,
            delivery_date TEXT,
            source TEXT,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (product_root, contract_code, ts)
        );

        CREATE INDEX IF NOT EXISTS idx_contract_kbars_ts
        ON contract_kbars(product_root, ts);

        CREATE TABLE IF NOT EXISTS continuous_kbars (
            product_root TEXT NOT NULL,
            ts TEXT NOT NULL,
            contract_code TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            amount REAL,
            source TEXT,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (product_root, ts)
        );

        CREATE TABLE IF NOT EXISTS contract_rollovers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_root TEXT NOT NULL,
            old_contract_code TEXT,
            new_contract_code TEXT NOT NULL,
            effective_at TEXT NOT NULL,
            detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_root, new_contract_code, effective_at)
        );

        CREATE TABLE IF NOT EXISTS history_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
            product_root TEXT NOT NULL,
            contract_code TEXT,
            raw_rows INTEGER DEFAULT 0,
            continuous_rows INTEGER DEFAULT 0,
            status TEXT,
            detail TEXT
        );
        """
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _state_key(product_root, name):
    return f"{str(product_root).upper()}:{name}"


def _get_state(conn, product_root, name, default=""):
    row = conn.execute(
        "SELECT value FROM history_state WHERE key = ?",
        (_state_key(product_root, name),),
    ).fetchone()
    return row[0] if row else default


def _set_state(conn, product_root, name, value):
    conn.execute(
        """
        INSERT INTO history_state (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (_state_key(product_root, name), str(value or "")),
    )


def _normalise_kbars(df):
    if df is None or df.empty:
        return pd.DataFrame()
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"K 線缺少欄位：{', '.join(missing)}")

    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume", "Amount"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=["ts", "Open", "High", "Low", "Close", "Volume"])
    out = out.drop_duplicates(subset=["ts"], keep="last").sort_values("ts")
    return out


def _parse_delivery_date(value):
    if not value:
        return None
    parsed = pd.to_datetime(str(value).replace("/", "-"), errors="coerce")
    return None if pd.isna(parsed) else parsed.to_pydatetime()


def _rollover_effective_at(previous_delivery_date, detected_at):
    delivery = _parse_delivery_date(previous_delivery_date)
    if delivery is not None:
        return datetime.combine(delivery.date(), time(15, 0))
    return detected_at.replace(hour=0, minute=0, second=0, microsecond=0)


def upsert_contract_kbars(
    df,
    contract_code,
    delivery_date="",
    product_root="TMF",
    source="Sinopac Shioaji kbars",
    synced_at=None,
    db_path=DB_PATH,
):
    product_root = str(product_root or "TMF").upper()
    contract_code = str(contract_code or "").upper()
    if not contract_code:
        raise ValueError("缺少實際契約代碼，無法保存歷史 K 線。")

    bars = _normalise_kbars(df)
    if bars.empty:
        return {"status": "empty", "raw_rows": 0, "continuous_rows": 0, "rollover": False}

    synced_at = synced_at or datetime.now()
    synced_text = synced_at.strftime("%Y-%m-%d %H:%M:%S")
    amount_values = bars["Amount"] if "Amount" in bars.columns else pd.Series([None] * len(bars), index=bars.index)
    raw_rows = [
        (
            product_root,
            contract_code,
            row.ts.strftime("%Y-%m-%d %H:%M:%S"),
            float(row.Open),
            float(row.High),
            float(row.Low),
            float(row.Close),
            float(row.Volume),
            None if pd.isna(amount_values.loc[index]) else float(amount_values.loc[index]),
            str(delivery_date or ""),
            str(source or ""),
            synced_text,
        )
        for index, row in bars.iterrows()
    ]

    with _connect(db_path) as conn:
        previous_code = _get_state(conn, product_root, "active_contract")
        previous_delivery = _get_state(conn, product_root, "active_delivery_date")
        active_from = _get_state(conn, product_root, "active_from")
        rollover = bool(previous_code and previous_code != contract_code)

        if not active_from:
            active_from_dt = bars["ts"].iloc[0].to_pydatetime()
        elif rollover:
            active_from_dt = _rollover_effective_at(previous_delivery, synced_at)
        else:
            active_from_dt = pd.to_datetime(active_from).to_pydatetime()
        active_from_text = active_from_dt.strftime("%Y-%m-%d %H:%M:%S")

        conn.executemany(
            """
            INSERT INTO contract_kbars (
                product_root, contract_code, ts, open, high, low, close,
                volume, amount, delivery_date, source, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_root, contract_code, ts) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                amount = excluded.amount,
                delivery_date = excluded.delivery_date,
                source = excluded.source,
                synced_at = excluded.synced_at
            """,
            raw_rows,
        )

        continuous_rows = [row for row in raw_rows if row[2] >= active_from_text]
        conn.executemany(
            """
            INSERT INTO continuous_kbars (
                product_root, ts, contract_code, open, high, low, close,
                volume, amount, source, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_root, ts) DO UPDATE SET
                contract_code = excluded.contract_code,
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                amount = excluded.amount,
                source = excluded.source,
                synced_at = excluded.synced_at
            """,
            [
                (
                    row[0],
                    row[2],
                    row[1],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[10],
                    row[11],
                )
                for row in continuous_rows
            ],
        )

        if rollover:
            conn.execute(
                "UPDATE contracts SET active_to = ? WHERE product_root = ? AND contract_code = ?",
                (active_from_text, product_root, previous_code),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO contract_rollovers (
                    product_root, old_contract_code, new_contract_code, effective_at
                ) VALUES (?, ?, ?, ?)
                """,
                (product_root, previous_code, contract_code, active_from_text),
            )

        conn.execute(
            """
            INSERT INTO contracts (
                product_root, contract_code, delivery_date, first_seen_at,
                last_seen_at, active_from, active_to
            ) VALUES (?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(product_root, contract_code) DO UPDATE SET
                delivery_date = excluded.delivery_date,
                last_seen_at = excluded.last_seen_at,
                active_from = COALESCE(contracts.active_from, excluded.active_from),
                active_to = NULL
            """,
            (
                product_root,
                contract_code,
                str(delivery_date or ""),
                synced_text,
                synced_text,
                active_from_text,
            ),
        )
        _set_state(conn, product_root, "active_contract", contract_code)
        _set_state(conn, product_root, "active_delivery_date", delivery_date)
        _set_state(conn, product_root, "active_from", active_from_text)
        _set_state(conn, product_root, "last_sync_at", synced_text)
        conn.execute(
            """
            INSERT INTO history_sync_runs (
                product_root, contract_code, raw_rows, continuous_rows, status, detail
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                product_root,
                contract_code,
                len(raw_rows),
                len(continuous_rows),
                "ok",
                f"rollover {previous_code} -> {contract_code}" if rollover else "incremental sync",
            ),
        )

    return {
        "status": "ok",
        "contract_code": contract_code,
        "delivery_date": str(delivery_date or ""),
        "raw_rows": len(raw_rows),
        "continuous_rows": len(continuous_rows),
        "active_from": active_from_text,
        "rollover": rollover,
        "previous_contract_code": previous_code,
    }


def load_continuous_kbars(product_root="TMF", start=None, end=None, db_path=DB_PATH):
    clauses = ["product_root = ?"]
    params = [str(product_root or "TMF").upper()]
    if start is not None:
        clauses.append("ts >= ?")
        params.append(pd.to_datetime(start).strftime("%Y-%m-%d %H:%M:%S"))
    if end is not None:
        clauses.append("ts <= ?")
        params.append(pd.to_datetime(end).strftime("%Y-%m-%d %H:%M:%S"))

    query = f"""
        SELECT ts, open AS Open, high AS High, low AS Low, close AS Close,
               volume AS Volume, amount AS Amount, contract_code
        FROM continuous_kbars
        WHERE {' AND '.join(clauses)}
        ORDER BY ts
    """
    with _connect(db_path) as conn:
        out = pd.read_sql_query(query, conn, params=params)
    if not out.empty:
        out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
    out.attrs["source"] = "Local continuous futures history"
    out.attrs["product_root"] = str(product_root or "TMF").upper()
    return out


def get_history_status(product_root="TMF", db_path=DB_PATH):
    product_root = str(product_root or "TMF").upper()
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS rows, MIN(ts) AS first_ts, MAX(ts) AS last_ts,
                   COUNT(DISTINCT contract_code) AS contracts
            FROM continuous_kbars WHERE product_root = ?
            """,
            (product_root,),
        ).fetchone()
        rollovers = conn.execute(
            "SELECT COUNT(*) FROM contract_rollovers WHERE product_root = ?",
            (product_root,),
        ).fetchone()[0]
        active_contract = _get_state(conn, product_root, "active_contract")
        delivery_date = _get_state(conn, product_root, "active_delivery_date")
        last_sync_at = _get_state(conn, product_root, "last_sync_at")
    return {
        "product_root": product_root,
        "rows": int(row["rows"] or 0),
        "first_ts": row["first_ts"] or "",
        "last_ts": row["last_ts"] or "",
        "contracts": int(row["contracts"] or 0),
        "rollovers": int(rollovers or 0),
        "active_contract": active_contract,
        "delivery_date": delivery_date,
        "last_sync_at": last_sync_at,
        "db_path": str(Path(db_path)),
    }


def get_recent_rollovers(product_root="TMF", limit=10, db_path=DB_PATH):
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT old_contract_code, new_contract_code, effective_at, detected_at
            FROM contract_rollovers
            WHERE product_root = ?
            ORDER BY id DESC LIMIT ?
            """,
            (str(product_root or "TMF").upper(), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]
