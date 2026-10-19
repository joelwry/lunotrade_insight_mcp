"""Swing-trade analysis engine (spot, long-only, 2 days - 2 weeks).

Produces a 0-100 score from transparent rules plus hard vetoes, using COMPLETED
candles only. It is a screening aid, NOT a proven edge: backtest / paper-trade first.
"""
from __future__ import annotations

import time
from typing import Any

import indicators as ind

DAY, H4 = 86400, 14400


def completed(candles: list[dict], duration_s: int, now_ms: int | None = None) -> list[dict]:
    """Drop the still-forming candle so signals don't repaint."""
    now_ms = now_ms or int(time.time() * 1000)
    return [c for c in candles if c["ts"] + duration_s * 1000 <= now_ms]


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def analyze_swing(
    pair: str,
    daily: list[dict],
    four_hour: list[dict],
    ticker: dict,
    book_top: dict,
    capital: float = 30000.0,
    risk_percent: float = 1.0,
    fee_rate: float = 0.001,
    now_ms: int | None = None,
) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    d = completed(daily, DAY, now_ms)
    h = completed(four_hour, H4, now_ms)
    if len(d) < 60 or len(h) < 60:
        return {
            "pair": pair, "signal": "INSUFFICIENT_DATA", "trade_execution": "DISABLED",
            "reason": f"Need >=60 completed candles (daily={len(d)}, 4h={len(h)}). "
                      "Market may be illiquid or new.",
        }

    dc = [c["close"] for c in d]
    hc = [c["close"] for c in h]
    bid, ask = _f(ticker.get("bid")), _f(ticker.get("ask"))
    last = _f(ticker.get("last_trade"))
    if bid <= 0 or ask <= 0:
        return {"pair": pair, "signal": "NO_DATA", "trade_execution": "DISABLED",
                "reason": "Ticker has no live bid/ask (market inactive?)."}
    price = ask  # conservative entry reference: you buy at the ask
    spread_pct = (ask - bid) / ((ask + bid) / 2) * 100

    ema20d, ema50d = ind.ema(dc, 20), ind.ema(dc, 50)
    ema20h, ema50h = ind.ema(hc, 20), ind.ema(hc, 50)
    rsi_d, rsi_h = ind.rsi(dc), ind.rsi(hc)
    atr_d = ind.atr(d) or 0.0
    macd_d = ind.macd(dc)
    vol_recent = sum(c["volume"] for c in d[-5:]) / 5
    vol_base = sum(c["volume"] for c in d[-25:-5]) / 20
    vol_ratio = vol_recent / vol_base if vol_base else 0.0

    # --- levels
    swing_low = min(c["low"] for c in d[-10:])
    piv_hi = [p for p in ind.pivot_highs(d[-120:]) if p > price]
    resistance = min(piv_hi) if piv_hi else max(c["high"] for c in d[-90:])
    resistance_is_ath = not piv_hi and resistance <= price * 1.001

    # --- trade plan
    stop = max(swing_low - 0.25 * atr_d, price - 2.5 * atr_d)
    stop = min(stop, price - 1.0 * atr_d)           # never tighter than 1 ATR
    stop = max(stop, 0.0)
    risk_per_unit = price - stop
    fees_per_unit = price * fee_rate * 2             # entry + exit
    eff_risk = risk_per_unit + fees_per_unit
    target_2r, target_3r = price + 2 * risk_per_unit, price + 3 * risk_per_unit
    room_r = (resistance - price) / risk_per_unit if risk_per_unit > 0 else 0
    if resistance_is_ath:
        room_r = 3.0  # price discovery: no overhead resistance known

    # --- scoring
    score, notes = 0, []

    def add(pts: int, ok: bool, label: str, partial: int = 0):
        nonlocal score
        got = pts if ok else partial
        score += got
        notes.append({"rule": label, "points": got, "max": pts, "passed": ok})

    trend_d = ema20d > ema50d and price > ema50d
    add(25, trend_d, "Daily uptrend (EMA20>EMA50, price>EMA50)", 10 if price > ema50d else 0)
    trend_h = ema20h > ema50h
    add(15, trend_h, "4h trend aligned (EMA20>EMA50)", 5 if hc[-1] > ema50h else 0)
    add(15, 50 <= rsi_d <= 68, f"Daily RSI in 50-68 (is {rsi_d:.1f})",
        7 if 45 <= rsi_d < 50 or 68 < rsi_d <= 72 else 0)
    macd_ok = bool(macd_d and macd_d[2] > 0 and macd_d[2] >= macd_d[3])
    add(10, macd_ok, "Daily MACD histogram positive & rising", 4 if macd_d and macd_d[2] > 0 else 0)
    add(5, vol_ratio >= 0.8, f"Volume not fading (5d/20d = {vol_ratio:.2f})")
    add(10, spread_pct <= 0.5, f"Spread {spread_pct:.2f}% <= 0.5%", 5 if spread_pct <= 1.0 else 0)
    extension = (price - ema20d) / atr_d if atr_d else 99
    add(10, extension <= 2.5, f"Not overextended ({extension:.1f} ATR above EMA20)")
    add(10, room_r >= 1.5, f"Room to resistance {room_r:.1f}R", 4 if room_r >= 1.0 else 0)

    vetoes = []
    if price <= ema50d:
        vetoes.append("Price below daily EMA50 (downtrend/no trend)")
    if rsi_d > 75:
        vetoes.append("Daily RSI > 75 (chasing)")
    if spread_pct > 1.5:
        vetoes.append("Spread > 1.5% (costly to trade)")
    if room_r < 1.0:
        vetoes.append("Resistance too close for decent reward/risk")
    tk_age = (now_ms - int(_f(ticker.get("timestamp"), now_ms))) / 1000
    if tk_age > 300:
        vetoes.append(f"Ticker is {tk_age:.0f}s old (stale)")

    if vetoes:
        signal = "AVOID"
    elif score >= 70:
        signal = "BUY_CANDIDATE"
    elif score >= 50:
        signal = "WATCH"
    else:
        signal = "AVOID"

    report: dict[str, Any] = {
        "pair": pair, "signal": signal, "score": score, "vetoes": vetoes,
        "trade_execution": "DISABLED",
        "horizon": "2 days to 2 weeks (spot, long-only)",
        "market": {
            "bid": bid, "ask": ask, "last_trade": last, "spread_pct": round(spread_pct, 3),
            "ticker_age_seconds": round(tk_age),
            "rolling_24h_volume": ticker.get("rolling_24_hour_volume"),
        },
        "indicators": {
            "daily": {"ema20": ema20d, "ema50": ema50d, "rsi14": round(rsi_d, 1), "atr14": atr_d,
                      "macd_hist": macd_d[2] if macd_d else None, "volume_ratio_5v20": round(vol_ratio, 2)},
            "four_hour": {"ema20": ema20h, "ema50": ema50h, "rsi14": round(rsi_h, 1)},
        },
        "levels": {"recent_swing_low": swing_low, "nearest_resistance": resistance},
        "score_breakdown": notes,
        "data_note": f"{len(d)} completed daily and {len(h)} completed 4h candles used.",
    }
    if signal in ("BUY_CANDIDATE", "WATCH"):
        risk_budget = capital * risk_percent / 100
        units = risk_budget / eff_risk if eff_risk > 0 else 0
        notional = min(units * price, capital)
        units = notional / price
        report["illustrative_plan"] = {
            "entry_reference": round(price, 8),
            "stop_loss": round(stop, 8),
            "target_2R": round(target_2r, 8),
            "target_3R": round(target_3r, 8),
            "stop_distance_pct": round(risk_per_unit / price * 100, 2),
            "position_size_quote": round(notional, 2),
            "estimated_units": round(units, 8),
            "max_loss_if_stopped_quote": round(units * eff_risk, 2),
            "risk_budget_quote": round(risk_budget, 2),
            "assumed_fee_rate_per_side": fee_rate,
            "capital_cap_applied": units * price >= capital * 0.9999,
        }
    report["caveats"] = [
        "Unbacktested rule set - treat as a screening aid, not a prediction.",
        "Stops can fill worse than planned on a thin market; confirm minimum order size & fees on Luno.",
        "Check news/regulatory events manually; this engine only sees price, volume and order book.",
    ]
    return report
