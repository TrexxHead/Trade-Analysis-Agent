import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "trades.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    volume REAL,
    open_time TEXT NOT NULL,
    close_time TEXT NOT NULL,
    open_price REAL,
    close_price REAL,
    pnl REAL NOT NULL,
    balance_after REAL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS trade_flags (
    trade_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    detail TEXT,
    PRIMARY KEY (trade_id, rule_id),
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS trade_annotations (
    trade_id TEXT PRIMARY KEY REFERENCES trades(id),
    setup TEXT,
    emotion TEXT,
    notes TEXT,
    risk_amount REAL
);

CREATE TABLE IF NOT EXISTS trade_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    platform TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    order_spec_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT,
    decision_note TEXT,
    executed_order_id TEXT
);

CREATE TABLE IF NOT EXISTS scan_status (
    symbol TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    direction TEXT,
    rationale TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_close_time ON trades(close_time);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON trade_proposals(status);
"""


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_trades(conn: sqlite3.Connection, trades: list[dict]) -> int:
    rows = [
        (
            t["id"],
            t["source"],
            t["account_id"],
            t["symbol"],
            t["direction"],
            t.get("volume"),
            t["open_time"],
            t["close_time"],
            t.get("open_price"),
            t.get("close_price"),
            t["pnl"],
            t.get("balance_after"),
            json.dumps(t.get("raw", {}), default=str),
        )
        for t in trades
    ]
    conn.executemany(
        """
        INSERT INTO trades (id, source, account_id, symbol, direction, volume,
                             open_time, close_time, open_price, close_price,
                             pnl, balance_after, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            pnl=excluded.pnl,
            balance_after=excluded.balance_after,
            close_time=excluded.close_time,
            close_price=excluded.close_price,
            raw_json=excluded.raw_json
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def get_trades(conn: sqlite3.Connection, start: str | None = None, end: str | None = None,
               source: str | None = None) -> list[dict]:
    query = """
        SELECT trades.*, trade_annotations.setup AS setup, trade_annotations.emotion AS emotion,
               trade_annotations.notes AS notes, trade_annotations.risk_amount AS risk_amount
        FROM trades
        LEFT JOIN trade_annotations ON trade_annotations.trade_id = trades.id
        WHERE 1=1
    """
    params: list = []
    if start:
        query += " AND trades.close_time >= ?"
        params.append(start)
    if end:
        query += " AND trades.close_time < ?"
        params.append(end)
    if source:
        query += " AND trades.source = ?"
        params.append(source)
    query += " ORDER BY trades.close_time ASC"
    return [dict(row) for row in conn.execute(query, params)]


def get_trade(conn: sqlite3.Connection, trade_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT trades.*, trade_annotations.setup AS setup, trade_annotations.emotion AS emotion,
               trade_annotations.notes AS notes, trade_annotations.risk_amount AS risk_amount
        FROM trades
        LEFT JOIN trade_annotations ON trade_annotations.trade_id = trades.id
        WHERE trades.id = ?
        """,
        (trade_id,),
    ).fetchone()
    return dict(row) if row else None


def upsert_annotation(conn: sqlite3.Connection, trade_id: str, setup: str | None = None,
                       emotion: str | None = None, notes: str | None = None,
                       risk_amount: float | None = None) -> None:
    conn.execute(
        """
        INSERT INTO trade_annotations (trade_id, setup, emotion, notes, risk_amount)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(trade_id) DO UPDATE SET
            setup=excluded.setup,
            emotion=excluded.emotion,
            notes=excluded.notes,
            risk_amount=excluded.risk_amount
        """,
        (trade_id, setup, emotion, notes, risk_amount),
    )
    conn.commit()


def set_trade_flags(conn: sqlite3.Connection, trade_id: str, flags: list[dict]) -> None:
    conn.execute("DELETE FROM trade_flags WHERE trade_id = ?", (trade_id,))
    conn.executemany(
        "INSERT INTO trade_flags (trade_id, rule_id, detail) VALUES (?, ?, ?)",
        [(trade_id, f["rule_id"], f.get("detail", "")) for f in flags],
    )
    conn.commit()


def get_flags(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> list[dict]:
    query = """
        SELECT trade_flags.*, trades.close_time, trades.source, trades.symbol
        FROM trade_flags
        JOIN trades ON trades.id = trade_flags.trade_id
        WHERE 1=1
    """
    params: list = []
    if start:
        query += " AND trades.close_time >= ?"
        params.append(start)
    if end:
        query += " AND trades.close_time < ?"
        params.append(end)
    return [dict(row) for row in conn.execute(query, params)]


def create_proposal(conn: sqlite3.Connection, platform: str, symbol: str, direction: str,
                     order_spec: dict, rationale: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO trade_proposals (created_at, platform, symbol, direction, order_spec_json, rationale)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (datetime.now(timezone.utc).isoformat(), platform, symbol, direction, json.dumps(order_spec), rationale),
    )
    conn.commit()
    return cursor.lastrowid


def has_pending_proposal(conn: sqlite3.Connection, symbol: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM trade_proposals WHERE symbol = ? AND status = 'pending' LIMIT 1", (symbol,)
    ).fetchone()
    return row is not None


def get_proposal(conn: sqlite3.Connection, proposal_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM trade_proposals WHERE id = ?", (proposal_id,)).fetchone()
    return dict(row) if row else None


def list_proposals(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM trade_proposals WHERE status = ? ORDER BY created_at ASC", (status,)
        )
    else:
        rows = conn.execute("SELECT * FROM trade_proposals ORDER BY created_at ASC")
    return [dict(row) for row in rows]


def upsert_scan_status(conn: sqlite3.Connection, symbol: str, platform: str, timeframe: str,
                        direction: str | None = None, rationale: str | None = None,
                        error: str | None = None) -> None:
    """Records what the last scan saw for one instrument - a "no setup"
    result and a hard error both get recorded, same as a hit, so the
    Scanner page can distinguish "checked recently, nothing there" from
    "hasn't been checked" or "broken". Written by run_scan.py, read by the
    dashboard, so the Scanner page never has to call MetaApi live itself -
    a live per-instrument network call on every page load doesn't scale
    once you're watching several instruments (MetaApi's default per-request
    timeout is 60s), and everywhere else in this dashboard already just
    presents what a script already computed rather than recomputing live.
    """
    conn.execute(
        """
        INSERT INTO scan_status (symbol, platform, timeframe, checked_at, direction, rationale, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            platform=excluded.platform,
            timeframe=excluded.timeframe,
            checked_at=excluded.checked_at,
            direction=excluded.direction,
            rationale=excluded.rationale,
            error=excluded.error
        """,
        (symbol, platform, timeframe, datetime.now(timezone.utc).isoformat(), direction, rationale, error),
    )
    conn.commit()


def get_scan_status(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM scan_status ORDER BY symbol ASC")]


def update_proposal_status(conn: sqlite3.Connection, proposal_id: int, status: str,
                            note: str | None = None, executed_order_id: str | None = None) -> None:
    conn.execute(
        """
        UPDATE trade_proposals
        SET status = ?, decided_at = ?, decision_note = ?, executed_order_id = COALESCE(?, executed_order_id)
        WHERE id = ?
        """,
        (status, datetime.now(timezone.utc).isoformat(), note, executed_order_id, proposal_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Kora analyst tables. Kora only *analyses* - it never places orders - so
# everything below is market state, analysis output, and service state.
# ---------------------------------------------------------------------------

KORA_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    code TEXT PRIMARY KEY,
    bid REAL, ask REAL, price REAL,
    day_open REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    bias TEXT, confidence REAL, opportunity REAL,
    report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    code TEXT,
    action TEXT NOT NULL,
    tone TEXT NOT NULL,          -- g (setup) / a (watch) / r (skip) / b (system)
    confidence REAL,
    explanation TEXT NOT NULL,
    analysis_id INTEGER
);

CREATE TABLE IF NOT EXISTS setup_stats (
    code TEXT NOT NULL,
    setup TEXT NOT NULL,
    direction TEXT NOT NULL,
    n INTEGER NOT NULL, wins INTEGER NOT NULL,
    avg_r REAL NOT NULL, expectancy REAL NOT NULL,
    timeframe TEXT, days INTEGER,
    trained_at TEXT NOT NULL,
    PRIMARY KEY (code, setup, direction)
);

CREATE TABLE IF NOT EXISTS trade_charts (
    trade_id TEXT PRIMARY KEY,
    timeframe TEXT NOT NULL,
    candles_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts_sent (
    dedupe_key TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analyses_code ON analyses(code, created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def kora_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = get_connection(db_path)
    conn.executescript(KORA_SCHEMA)
    return conn


def kv_get(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def kv_get_with_time(conn, key: str):
    row = conn.execute("SELECT value, updated_at FROM kv WHERE key = ?", (key,)).fetchone()
    return (json.loads(row["value"]), row["updated_at"]) if row else (None, None)


def kv_set(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value, default=str), _now()),
    )
    conn.commit()


def upsert_quotes(conn, quotes: dict[str, dict]) -> None:
    now = _now()
    conn.executemany(
        "INSERT INTO quotes (code, bid, ask, price, day_open, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(code) DO UPDATE SET bid=excluded.bid, ask=excluded.ask, price=excluded.price, "
        "day_open=COALESCE(excluded.day_open, quotes.day_open), updated_at=excluded.updated_at",
        [(c, q.get("bid"), q.get("ask"), q.get("price"), q.get("day_open"), now) for c, q in quotes.items()],
    )
    conn.commit()


def get_quotes(conn) -> dict[str, dict]:
    return {r["code"]: dict(r) for r in conn.execute("SELECT * FROM quotes")}


def save_analysis(conn, code: str, cycle_id: str, report: dict) -> int:
    cur = conn.execute(
        "INSERT INTO analyses (code, created_at, cycle_id, bias, confidence, opportunity, report_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (code, _now(), cycle_id, report.get("bias"), report.get("confidence"), report.get("opportunity"),
         json.dumps(report, default=str)),
    )
    conn.commit()
    return cur.lastrowid


def latest_analyses(conn) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT a.* FROM analyses a JOIN (SELECT code, MAX(id) AS mid FROM analyses GROUP BY code) m "
        "ON a.id = m.mid"
    )
    out = {}
    for r in rows:
        rep = json.loads(r["report_json"])
        rep["_id"], rep["_created_at"] = r["id"], r["created_at"]
        out[r["code"]] = rep
    return out


def analyses_between(conn, code: str, start: str, end: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, created_at, report_json FROM analyses WHERE code = ? AND created_at BETWEEN ? AND ? "
        "ORDER BY id DESC", (code, start, end))
    return [{"_id": r["id"], "_created_at": r["created_at"], **json.loads(r["report_json"])} for r in rows]


def prune_analyses(conn, keep_days: int = 30) -> None:
    conn.execute("DELETE FROM analyses WHERE created_at < datetime('now', ?)", (f"-{keep_days} days",))
    conn.commit()


def add_decision(conn, action: str, tone: str, explanation: str, code: str | None = None,
                 confidence: float | None = None, analysis_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO decisions (created_at, code, action, tone, confidence, explanation, analysis_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", (_now(), code, action, tone, confidence, explanation, analysis_id))
    conn.commit()
    return cur.lastrowid


def list_decisions(conn, limit: int = 50, since: str | None = None) -> list[dict]:
    if since:
        rows = conn.execute("SELECT * FROM decisions WHERE created_at >= ? ORDER BY id DESC LIMIT ?", (since, limit))
    else:
        rows = conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def save_setup_stats(conn, code: str, rows: list[dict], timeframe: str, days: int) -> None:
    conn.execute("DELETE FROM setup_stats WHERE code = ?", (code,))
    conn.executemany(
        "INSERT INTO setup_stats (code, setup, direction, n, wins, avg_r, expectancy, timeframe, days, trained_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(code, r["setup"], r["direction"], r["n"], r["wins"], r["avg_r"], r["expectancy"], timeframe, days, _now())
         for r in rows])
    conn.commit()


def get_setup_stats(conn, code: str | None = None) -> dict:
    q, p = "SELECT * FROM setup_stats", ()
    if code:
        q, p = q + " WHERE code = ?", (code,)
    return {(r["code"], r["setup"], r["direction"]): dict(r) for r in conn.execute(q, p)}


def save_trade_chart(conn, trade_id: str, timeframe: str, candles: list[dict]) -> None:
    conn.execute("INSERT OR REPLACE INTO trade_charts (trade_id, timeframe, candles_json) VALUES (?, ?, ?)",
                 (trade_id, timeframe, json.dumps(candles)))
    conn.commit()


def get_trade_charts(conn) -> dict[str, dict]:
    return {r["trade_id"]: {"timeframe": r["timeframe"], "candles": json.loads(r["candles_json"])}
            for r in conn.execute("SELECT * FROM trade_charts")}


def alert_already_sent(conn, key: str, within_hours: float) -> bool:
    row = conn.execute("SELECT sent_at FROM alerts_sent WHERE dedupe_key = ?", (key,)).fetchone()
    if not row:
        return False
    age = datetime.now(timezone.utc) - datetime.fromisoformat(row["sent_at"])
    return age.total_seconds() < within_hours * 3600


def mark_alert_sent(conn, key: str) -> None:
    conn.execute("INSERT OR REPLACE INTO alerts_sent (dedupe_key, sent_at) VALUES (?, ?)", (key, _now()))
    conn.commit()


PLANS_SCHEMA = """
CREATE TABLE IF NOT EXISTS kora_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT UNIQUE NOT NULL,
    code TEXT NOT NULL, setup TEXT NOT NULL, setup_name TEXT, direction TEXT NOT NULL,
    grade TEXT, confidence REAL, entry_type TEXT, entry REAL, stop REAL, tp1 REAL, tp2 REAL, rr1 REAL,
    digits INTEGER, created_at TEXT NOT NULL, analysis_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open', resolved_at TEXT, result_r REAL
);
CREATE INDEX IF NOT EXISTS idx_plans_status ON kora_plans(status);
"""


def record_plan(conn, key: str, code: str, cand: dict, digits: int, analysis_id: int | None) -> bool:
    conn.executescript(PLANS_SCHEMA)
    cur = conn.execute(
        "INSERT OR IGNORE INTO kora_plans (dedupe_key, code, setup, setup_name, direction, grade, confidence, entry_type, "
        "entry, stop, tp1, tp2, rr1, digits, created_at, analysis_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, code, cand["setup"], cand["setup_name"], cand["direction"], cand["grade"], cand["confidence"],
         cand["entry_type"], cand["entry"], cand["stop"], cand["tp1"], cand["tp2"], cand["rr1"], digits, _now(), analysis_id))
    conn.commit()
    return cur.rowcount > 0


def open_plans(conn) -> list[dict]:
    conn.executescript(PLANS_SCHEMA)
    return [dict(r) for r in conn.execute("SELECT * FROM kora_plans WHERE status = 'open'")]


def resolve_plan_row(conn, plan_id: int, status: str, result_r: float | None) -> None:
    conn.execute("UPDATE kora_plans SET status = ?, result_r = ?, resolved_at = ? WHERE id = ?",
                 (status, result_r, _now(), plan_id))
    conn.commit()


def list_plans(conn, code: str | None = None, limit: int = 200) -> list[dict]:
    conn.executescript(PLANS_SCHEMA)
    q, p = "SELECT * FROM kora_plans", []
    if code:
        q, p = q + " WHERE code = ?", [code]
    return [dict(r) for r in conn.execute(q + " ORDER BY id DESC LIMIT ?", (*p, limit))]
