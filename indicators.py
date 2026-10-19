"""Pure-python technical indicators (no numpy/pandas needed)."""
from __future__ import annotations


def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def ema_series(values: list[float], n: int) -> list[float]:
    if len(values) < n:
        return []
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    out = [e]
    for v in values[n:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def ema(values: list[float], n: int) -> float | None:
    s = ema_series(values, n)
    return s[-1] if s else None


def rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def atr(candles: list[dict], n: int = 14) -> float | None:
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def macd(values: list[float], fast=12, slow=26, signal=9):
    """Returns (macd, signal, histogram, prev_histogram) or None."""
    ef, es = ema_series(values, fast), ema_series(values, slow)
    if not es:
        return None
    ef = ef[len(ef) - len(es):]
    line = [a - b for a, b in zip(ef, es)]
    sig = ema_series(line, signal)
    if len(sig) < 2:
        return None
    line = line[len(line) - len(sig):]
    hist = [a - b for a, b in zip(line, sig)]
    return line[-1], sig[-1], hist[-1], hist[-2]


def bollinger(values: list[float], n: int = 20, k: float = 2.0):
    if len(values) < n:
        return None
    w = values[-n:]
    m = sum(w) / n
    sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
    return m - k * sd, m, m + k * sd


def pivot_highs(candles: list[dict], left: int = 3, right: int = 3) -> list[float]:
    out = []
    for i in range(left, len(candles) - right):
        h = candles[i]["high"]
        if all(h >= candles[j]["high"] for j in range(i - left, i + right + 1)):
            out.append(h)
    return out


def pivot_lows(candles: list[dict], left: int = 3, right: int = 3) -> list[float]:
    out = []
    for i in range(left, len(candles) - right):
        l = candles[i]["low"]
        if all(l <= candles[j]["low"] for j in range(i - left, i + right + 1)):
            out.append(l)
    return out
