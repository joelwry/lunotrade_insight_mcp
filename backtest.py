"""Walk-forward backtester for the swing strategy (long-only spot, daily decisions).

Honesty rules built in:
  * Signal at day t uses only candles completed by the close of day t (no look-ahead).
  * Entry is the NEXT day's open + slippage (you can't buy yesterday's close).
  * Fees on both sides; stop slippage; if stop and target are both touched the same
    day, the STOP is assumed to hit first (pessimistic).
  * Ticker spread is ASSUMED (history has no order book) - see `assumed_half_spread`.
  * Position sized by risk_percent of current equity, capped at 100% of equity.
  * Results are split into in-sample (first 60%) and out-of-sample (last 40%) and
    compared to simply buying and holding.
Past results do not guarantee future results.
"""
from __future__ import annotations

import argparse
import json
import math
from typing import Any

import strategy

DAY_MS = 86400 * 1000
H4_MS = 14400 * 1000


def _metrics(trades: list[dict], start_eq: float, end_eq: float, curve: list[float]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    peak, mdd = curve[0], 0.0
    for e in curve:
        peak = max(peak, e)
        mdd = max(mdd, (peak - e) / peak)
    gp, gl = sum(wins), -sum(losses)
    return {
        "trades": n,
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "avg_win_R": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss_R": round(sum(losses) / len(losses), 2) if losses else 0,
        "expectancy_R_net": round(sum(rs) / n, 3),
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "total_return_pct": round((end_eq / start_eq - 1) * 100, 1),
        "max_drawdown_pct": round(mdd * 100, 1),
        "avg_hold_days": round(sum(t["days"] for t in trades) / n, 1),
        "exit_reasons": {k: sum(1 for t in trades if t["exit"] == k) for k in {t["exit"] for t in trades}},
    }


def run_backtest(
    pair: str,
    daily: list[dict],
    four_hour: list[dict],
    days: int = 730,
    min_score: int = 70,
    risk_percent: float = 1.0,
    fee_rate: float = 0.001,
    slippage: float = 0.002,
    assumed_half_spread: float = 0.0015,
    max_hold_days: int = 14,
    start_equity: float = 30000.0,
) -> dict[str, Any]:
    min_score = max(50, min(min_score, 100))
    warm = 60
    if len(daily) < warm + 30:
        return {"error": f"Not enough daily history for {pair}: {len(daily)} candles (need >= {warm + 30})."}
    first_i = max(warm, len(daily) - days)
    eq, trades, curve = start_equity, [], [start_equity]
    in_pos_until = -1
    i = first_i
    while i < len(daily) - 1:
        if i <= in_pos_until:
            i += 1
            continue
        t_close = daily[i]["ts"] + DAY_MS
        d = daily[: i + 1][-200:]
        h = [c for c in four_hour if c["ts"] + H4_MS <= t_close][-200:]
        px = daily[i]["close"]
        tk = {"bid": px * (1 - assumed_half_spread), "ask": px * (1 + assumed_half_spread),
              "last_trade": px, "timestamp": t_close}
        r = strategy.analyze_swing(pair, d, h, tk, {}, eq, risk_percent, fee_rate, now_ms=t_close)
        plan = r.get("illustrative_plan")
        if r.get("vetoes") or not plan or r.get("score", 0) < min_score:
            i += 1
            continue
        stop, target = plan["stop_loss"], plan["target_2R"]
        entry_bar = daily[i + 1]
        entry = entry_bar["open"] * (1 + slippage)
        if entry <= stop or entry >= target:   # gapped through the plan: skip
            i += 1
            continue
        risk_unit = entry - stop + entry * fee_rate * 2
        units = min(eq * risk_percent / 100 / risk_unit, eq / entry)
        exit_px, reason, j = None, None, i + 1
        for j in range(i + 1, min(i + 1 + max_hold_days, len(daily))):
            c = daily[j]
            if c["low"] <= stop:   # stop first (pessimistic); gap-down fills at the open
                exit_px, reason = min(stop, c["open"]) * (1 - slippage), "STOP"
                break
            if c["high"] >= target:
                exit_px, reason = target * (1 - slippage / 2), "TARGET"
                break
        if exit_px is None:
            j = min(i + max_hold_days, len(daily) - 1)
            exit_px, reason = daily[j]["close"] * (1 - slippage / 2), "TIME"
        pnl = units * (exit_px - entry) - units * (entry + exit_px) * fee_rate
        r_mult = pnl / (units * (entry - stop)) if entry > stop else 0
        eq += pnl
        curve.append(eq)
        trades.append({"signal_date": daily[i]["ts"], "entry": round(entry, 8), "exit": reason,
                       "exit_price": round(exit_px, 8), "days": j - i, "r": round(r_mult, 3),
                       "pnl": round(pnl, 2), "score": r["score"], "_i": i})
        in_pos_until = j
        i = j + 1

    span = daily[first_i:]
    bh = (span[-1]["close"] / span[0]["close"] - 1) * 100
    split = int(len(trades) * 0.6)
    ins, oos = trades[:split], trades[split:]

    def sub(tr):
        e, cv = start_equity, [start_equity]
        for t in tr:
            e += t["pnl"]; cv.append(e)
        return _metrics(tr, start_equity, e, cv)

    n = len(trades)
    verdict = ("Too few trades to conclude anything (need ~30+)." if n < 30 else
               "Positive expectancy after costs in-sample; verify the out-of-sample block before trusting it."
               if sum(t["r"] for t in trades) / n > 0 else "Negative expectancy after costs - do not trade these rules as-is.")
    for t in trades:
        t.pop("_i", None)
    return {
        "pair": pair, "period_days": len(span), "min_score": min_score,
        "assumptions": {"fee_per_side": fee_rate, "slippage": slippage,
                        "assumed_half_spread": assumed_half_spread, "max_hold_days": max_hold_days,
                        "risk_percent": risk_percent, "starting_equity": start_equity,
                        "intrabar_order": "stop assumed first if both touched"},
        "overall": _metrics(trades, start_equity, eq, curve),
        "in_sample_first_60pct": sub(ins),
        "out_of_sample_last_40pct": sub(oos),
        "buy_and_hold_return_pct": round(bh, 1),
        "verdict": verdict,
        "recent_trades": trades[-8:],
        "warnings": [
            "Spread is assumed (no historical order book).",
            "Small trade counts are statistical noise; do not tune parameters to make the curve look good.",
            "Crypto regimes change; a strategy that worked in one period may fail in the next.",
        ],
    }


if __name__ == "__main__":
    from luno_client import LunoClient
    ap = argparse.ArgumentParser()
    ap.add_argument("pair"); ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--min-score", type=int, default=70); ap.add_argument("--risk", type=float, default=1.0)
    a = ap.parse_args()
    cl = LunoClient()
    res = run_backtest(a.pair.upper(), cl.candles_history(a.pair, 86400, a.days + 120),
                       cl.candles_history(a.pair, 14400, a.days + 120), a.days, a.min_score, a.risk)
    print(json.dumps(res, indent=2))
