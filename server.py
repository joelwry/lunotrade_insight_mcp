"""LuTrade Insight - read-only Luno analyst MCP server (stdio, for Claude Desktop).

IMPORTANT: stdout is the MCP protocol channel. Never print() here; log to stderr.
There are deliberately NO tools that place, cancel or modify orders or move funds.
"""
from __future__ import annotations

import functools
import logging
import sys
import time

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server

import journal
import strategy
from luno_client import LunoClient, LunoError

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
mcp = _Server("lutrade-insight")
luno = LunoClient()

DEFAULT_WATCHLIST = ["XBTNGN", "ETHNGN", "USDCNGN", "XRPNGN", "LTCNGN"]


def _safe(fn):
    """Return errors as data so Claude can explain them instead of crashing the tool."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except (LunoError, ValueError) as e:
            return {"error": str(e)}
    return wrapper


def _fee_rate(pair: str, default: float = 0.001) -> float:
    if not luno.has_credentials:
        return default
    try:
        return float(luno.fee_info(pair).get("taker_fee", default))
    except Exception:
        return default


def _analysis(pair: str, capital: float, risk_percent: float) -> dict:
    pair = luno.validate_pair(pair)
    return strategy.analyze_swing(
        pair=pair,
        daily=luno.candles(pair, 86400, 200),
        four_hour=luno.candles(pair, 14400, 200),
        ticker=luno.ticker(pair),
        book_top=luno.orderbook_top(pair),
        capital=capital,
        risk_percent=risk_percent,
        fee_rate=_fee_rate(pair),
    )


# ------------------------------------------------------------------ status
@mcp.tool()
@_safe
def server_status() -> dict:
    """Check the server: credentials present, trade execution state, server time."""
    out = {"read_only": True, "trade_execution": "DISABLED",
           "credentials_loaded": luno.has_credentials,
           "server_time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
    if luno.has_credentials:
        try:
            luno.balances()
            out["private_access"] = "OK (balances readable)"
        except LunoError as e:
            out["private_access"] = f"FAILED: {e}"
    return out


# ------------------------------------------------------------- market data
@mcp.tool()
@_safe
def list_markets() -> dict:
    """List all Luno markets (pair, status, min/max volume, price scale)."""
    return {"markets": [
        {k: m.get(k) for k in ("market_id", "trading_status", "base_currency", "counter_currency",
                                "min_volume", "min_price", "fee_scale")}
        for m in luno.markets()]}


@mcp.tool()
@_safe
def get_ticker(pair: str = "XBTNGN") -> dict:
    """Live bid/ask/last price and 24h volume for a market, e.g. XBTNGN."""
    return luno.ticker(pair)


@mcp.tool()
@_safe
def get_orderbook(pair: str = "XBTNGN", depth: int = 10) -> dict:
    """Order book summary: top levels, spread, and bid/ask depth imbalance."""
    depth = max(1, min(depth, 50))
    b = luno.orderbook(pair)
    bids = [(float(x["price"]), float(x["volume"])) for x in b.get("bids", [])[:depth]]
    asks = [(float(x["price"]), float(x["volume"])) for x in b.get("asks", [])[:depth]]
    bv, av = sum(v for _, v in bids), sum(v for _, v in asks)
    spread = (asks[0][0] - bids[0][0]) / asks[0][0] * 100 if bids and asks else None
    return {"pair": pair.upper(), "spread_pct": spread, "bid_volume_top": bv, "ask_volume_top": av,
            "bid_ask_volume_ratio": bv / av if av else None, "bids": bids, "asks": asks}


@mcp.tool()
@_safe
def get_candles(pair: str = "XBTNGN", interval: str = "1d", count: int = 60) -> dict:
    """OHLCV candles. interval: 1h, 4h, 1d, 3d, 1w (also 1m,5m,15m,30m,3h). count max 200."""
    m = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "3h": 10800,
         "4h": 14400, "1d": 86400, "3d": 259200, "1w": 604800}
    if interval not in m:
        return {"error": f"interval must be one of {list(m)}"}
    c = luno.candles(pair, m[interval], max(1, min(count, 200)))
    return {"pair": pair.upper(), "interval": interval, "count": len(c), "candles": c}


# ------------------------------------------------------------ account data
@mcp.tool()
@_safe
def get_balances() -> dict:
    """Account balances (non-zero only) with reserved amounts."""
    rows = [b for b in luno.balances() if float(b["balance"]) or float(b["reserved"])]
    return {"balances": [{"asset": b["asset"], "balance": b["balance"], "reserved": b["reserved"],
                          "unconfirmed": b.get("unconfirmed")} for b in rows]}


@mcp.tool()
@_safe
def portfolio_snapshot(quote: str = "NGN") -> dict:
    """Value holdings in a quote currency (default NGN) using live tickers, with allocation %."""
    quote = quote.upper()
    tick = {t["pair"]: float(t["last_trade"]) for t in luno.tickers()}
    items, total, unpriced = [], 0.0, []
    for b in luno.balances():
        amt = float(b["balance"])
        if amt <= 0:
            continue
        a = b["asset"]
        px = 1.0 if a == quote else tick.get(f"{a}{quote}")
        if px is None:
            unpriced.append(a); continue
        v = amt * px
        total += v
        items.append({"asset": a, "amount": amt, "price": px, "value": round(v, 2)})
    for i in items:
        i["allocation_pct"] = round(i["value"] / total * 100, 1) if total else 0
    return {"quote": quote, "total_value": round(total, 2), "holdings": items, "unpriced_assets": unpriced}


@mcp.tool()
@_safe
def get_open_orders(pair: str = "") -> dict:
    """Pending orders (read-only view). Optionally filter by pair."""
    return {"orders": luno.orders(pair, "PENDING")}


@mcp.tool()
@_safe
def get_order_history(pair: str = "", limit: int = 50) -> dict:
    """Completed orders, most recent first."""
    o = luno.orders(pair, "COMPLETE")
    return {"orders": o[:max(1, min(limit, 100))]}


@mcp.tool()
@_safe
def get_my_trades(pair: str = "XBTNGN", days_back: int = 30) -> dict:
    """Your executed trades on a market over the last N days."""
    return {"pair": pair.upper(), "trades": luno.my_trades(pair, max(1, min(days_back, 365)))}


@mcp.tool()
@_safe
def get_fee_info(pair: str = "XBTNGN") -> dict:
    """Your maker/taker fee rates and 30-day volume for a market."""
    return luno.fee_info(pair)


# ---------------------------------------------------------------- analysis
@mcp.tool()
@_safe
def analyze_swing_trade(pair: str = "XBTNGN", capital: float = 30000, risk_percent: float = 1.0) -> dict:
    """Score a long-only 2d-2w swing setup (0-100) with stop/targets and sizing. Never trades.
    capital is in the quote currency (NGN for *NGN pairs)."""
    if capital <= 0:
        return {"error": "capital must be positive"}
    if not 0 < risk_percent <= 2:
        return {"error": "risk_percent must be >0 and <=2"}
    return _analysis(pair, capital, risk_percent)


@mcp.tool()
@_safe
def scan_watchlist(pairs: list[str] | None = None, capital: float = 30000, risk_percent: float = 1.0) -> dict:
    """Rank several markets by swing score. Defaults to a built-in NGN watchlist."""
    pairs = pairs or DEFAULT_WATCHLIST
    results = []
    for p in pairs[:12]:
        try:
            r = _analysis(p, capital, risk_percent)
            results.append({"pair": r["pair"], "signal": r["signal"], "score": r.get("score"),
                            "vetoes": r.get("vetoes"), "plan": r.get("illustrative_plan")})
        except Exception as e:  # one bad market must not kill the scan
            results.append({"pair": p, "signal": "ERROR", "error": str(e)})
    results.sort(key=lambda x: x.get("score") or -1, reverse=True)
    return {"ranked": results}


@mcp.tool()
@_safe
def run_backtest(pair: str = "XBTNGN", days: int = 730, min_score: int = 70,
                 risk_percent: float = 1.0, start_equity: float = 30000) -> dict:
    """Walk-forward backtest of the swing rules on Luno history (fees, slippage, next-day-open
    entry, in/out-of-sample split, buy&hold comparison). May take 10-40s on first run per pair."""
    import backtest
    pair = luno.validate_pair(pair)
    days = max(120, min(days, 1500))
    if not 0 < risk_percent <= 2:
        return {"error": "risk_percent must be >0 and <=2"}
    return backtest.run_backtest(
        pair, luno.candles_history(pair, 86400, days + 120), luno.candles_history(pair, 14400, days + 120),
        days=days, min_score=min_score, risk_percent=risk_percent,
        fee_rate=_fee_rate(pair), start_equity=start_equity)


# ----------------------------------------------------------- paper journal
@mcp.tool()
@_safe
def log_paper_signal(pair: str, entry: float, stop: float, target: float, score: int = 0, note: str = "") -> dict:
    """Record a hypothetical (paper) trade idea in the local journal. Moves no money."""
    if not (stop < entry < target):
        return {"error": "Need stop < entry < target for a long paper trade."}
    return {"logged_id": journal.add(luno.validate_pair(pair), entry, stop, target, score, note)}


@mcp.tool()
@_safe
def review_paper_signals() -> dict:
    """Update open paper trades against 4h candles (stop wins ties), then show stats."""
    def fetch(pair, since_ms):
        return [c for c in luno.candles(pair, 14400, 200) if c["ts"] >= since_ms]
    updated = journal.evaluate_open(fetch)
    return {"updated": updated, "stats": journal.stats(), "open": journal.rows("OPEN")}


@mcp.tool()
@_safe
def list_paper_signals(status: str = "") -> dict:
    """List journal entries (status: OPEN, STOPPED, TARGET, EXPIRED or blank for all)."""
    return {"signals": journal.rows(status.upper() or None)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
