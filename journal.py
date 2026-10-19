"""Paper-trade journal (local SQLite). Records signals and tracks outcomes honestly."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DB = Path(__file__).with_name("paper_journal.db")


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_ms INTEGER, pair TEXT,
            entry REAL, stop REAL, target REAL, score INTEGER, note TEXT,
            status TEXT DEFAULT 'OPEN', closed_ms INTEGER, exit_price REAL, r_multiple REAL)"""
    )
    return c


def add(pair, entry, stop, target, score, note=""):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO signals(created_ms,pair,entry,stop,target,score,note) VALUES(?,?,?,?,?,?,?)",
            (int(time.time() * 1000), pair, entry, stop, target, score, note),
        )
        return cur.lastrowid


def rows(status=None):
    with _conn() as c:
        q = "SELECT * FROM signals" + (" WHERE status=?" if status else "") + " ORDER BY id DESC"
        return [dict(r) for r in c.execute(q, (status,) if status else ())]


def close(sig_id, status, exit_price, ts_ms=None):
    with _conn() as c:
        r = c.execute("SELECT entry, stop FROM signals WHERE id=?", (sig_id,)).fetchone()
        risk = (r["entry"] - r["stop"]) or 1e-9
        c.execute(
            "UPDATE signals SET status=?, closed_ms=?, exit_price=?, r_multiple=? WHERE id=?",
            (status, ts_ms or int(time.time() * 1000), exit_price, (exit_price - r["entry"]) / risk, sig_id),
        )


def evaluate_open(candles_for):
    """candles_for(pair, since_ms) -> list of candle dicts. Stop wins ties (conservative)."""
    updated = []
    for s in rows("OPEN"):
        for c in candles_for(s["pair"], s["created_ms"]):
            if c["ts"] < s["created_ms"]:
                continue
            if c["low"] <= s["stop"]:
                close(s["id"], "STOPPED", s["stop"], c["ts"]); updated.append((s["id"], "STOPPED")); break
            if c["high"] >= s["target"]:
                close(s["id"], "TARGET", s["target"], c["ts"]); updated.append((s["id"], "TARGET")); break
            if c["ts"] - s["created_ms"] > 14 * 86400 * 1000:
                close(s["id"], "EXPIRED", c["close"], c["ts"]); updated.append((s["id"], "EXPIRED")); break
    return updated


def stats():
    done = [r for r in rows() if r["status"] != "OPEN"]
    if not done:
        return {"closed_trades": 0, "note": "No closed paper trades yet."}
    rs = [r["r_multiple"] for r in done]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    eq, peak, mdd = 0.0, 0.0, 0.0
    for x in reversed(rs):  # chronological
        eq += x; peak = max(peak, eq); mdd = max(mdd, peak - eq)
    return {
        "closed_trades": len(done),
        "win_rate_pct": round(len(wins) / len(done) * 100, 1),
        "avg_win_R": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss_R": round(sum(losses) / len(losses), 2) if losses else 0,
        "expectancy_R": round(sum(rs) / len(rs), 3),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        "max_drawdown_R": round(mdd, 2),
        "open_trades": len(rows("OPEN")),
        "note": "Need ~30+ closed trades before trusting these numbers (before fees).",
    }
