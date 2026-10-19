"""Read-only Luno API client.

Safety design:
  * Only HTTP GET is ever sent (there is no code path that sends POST/PUT/DELETE).
  * Every request path must match an explicit allowlist of read endpoints.
  * Trading / withdrawal / send endpoints are NOT in the allowlist, so even a
    coding mistake elsewhere cannot place or cancel an order.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))
log = logging.getLogger("lutrade.luno")

BASE_URL = "https://api.luno.com"

# (path regex, requires_auth)
ALLOWED_GET = [
    (re.compile(r"^/api/exchange/1/markets$"), False),
    (re.compile(r"^/api/exchange/1/candles$"), False),
    (re.compile(r"^/api/1/ticker$"), False),
    (re.compile(r"^/api/1/tickers$"), False),
    (re.compile(r"^/api/1/orderbook_top$"), False),
    (re.compile(r"^/api/1/orderbook$"), False),
    (re.compile(r"^/api/1/trades$"), False),
    (re.compile(r"^/api/1/balance$"), True),
    (re.compile(r"^/api/exchange/2/listorders$"), True),
    (re.compile(r"^/api/1/listtrades$"), True),
    (re.compile(r"^/api/1/fee_info$"), True),
]

# Candle durations Luno supports (seconds)
CANDLE_DURATIONS = {60, 300, 900, 1800, 3600, 10800, 14400, 86400, 259200, 604800}


class LunoError(RuntimeError):
    pass


class LunoClient:
    def __init__(self) -> None:
        key_id = os.getenv("LUNO_API_KEY_ID", "").strip()
        key_secret = os.getenv("LUNO_API_KEY_SECRET", "").strip()
        self.timeout = float(os.getenv("LUNO_TIMEOUT_SECONDS", "20"))
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "lutrade-insight-mcp/1.0"
        self.has_credentials = bool(key_id and key_secret)
        if self.has_credentials:
            self.session.auth = (key_id, key_secret)

    # ---------------------------------------------------------------- core
    @staticmethod
    def validate_pair(pair: str) -> str:
        pair = (pair or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{5,12}", pair):
            raise ValueError("Invalid market code. Use e.g. XBTNGN, ETHNGN, XBTZAR.")
        return pair

    def _get(self, path: str, params: dict | None = None) -> Any:
        rule = next((r for r in ALLOWED_GET if r[0].match(path)), None)
        if rule is None:
            raise LunoError(f"Endpoint not allowed (read-only guard): {path}")
        if rule[1] and not self.has_credentials:
            raise LunoError("This data needs LUNO_API_KEY_ID / LUNO_API_KEY_SECRET in .env")
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                r = self.session.get(BASE_URL + path, params=params, timeout=self.timeout)
            except requests.RequestException as e:  # network error
                last_err = e
                time.sleep(1.0 * (attempt + 1))
                continue
            if r.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                last_err = LunoError("Rate limited (429)")
                continue
            if r.status_code in (401, 403):
                raise LunoError(
                    f"Luno rejected the key ({r.status_code}). Check the key ID/secret "
                    "and that it has the needed read permission (balances / orders / trades)."
                )
            if not r.ok:
                try:
                    msg = r.json().get("error_message", r.text[:200])
                except Exception:
                    msg = r.text[:200]
                raise LunoError(f"Luno API {r.status_code}: {msg}")
            return r.json()
        raise LunoError(f"Luno request failed after retries: {last_err}")

    # -------------------------------------------------------------- public
    def markets(self) -> list[dict]:
        return self._get("/api/exchange/1/markets").get("markets", [])

    def ticker(self, pair: str) -> dict:
        return self._get("/api/1/ticker", {"pair": self.validate_pair(pair)})

    def tickers(self) -> list[dict]:
        return self._get("/api/1/tickers").get("tickers", [])

    def orderbook_top(self, pair: str) -> dict:
        return self._get("/api/1/orderbook_top", {"pair": self.validate_pair(pair)})

    def orderbook(self, pair: str) -> dict:
        return self._get("/api/1/orderbook", {"pair": self.validate_pair(pair)})

    def public_trades(self, pair: str, since_ms: int | None = None) -> list[dict]:
        return self._get("/api/1/trades", {"pair": self.validate_pair(pair), "since": since_ms}).get("trades", [])

    def candles(self, pair: str, duration: int, count: int = 200) -> list[dict]:
        """Return up to `count` most recent candles (oldest first, floats)."""
        if duration not in CANDLE_DURATIONS:
            raise ValueError(f"duration must be one of {sorted(CANDLE_DURATIONS)}")
        since = int((time.time() - duration * count) * 1000)
        raw = self._get(
            "/api/exchange/1/candles",
            {"pair": self.validate_pair(pair), "duration": duration, "since": since},
        ).get("candles", [])
        out = [
            {
                "ts": int(c["timestamp"]),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"]),
            }
            for c in raw
        ]
        out.sort(key=lambda c: c["ts"])
        return out[-count:]

    def candles_history(self, pair: str, duration: int, days: int) -> list[dict]:
        """Page forward through history (oldest first), cached on disk per pair/interval."""
        import json
        pair = self.validate_pair(pair)
        cache_dir = Path(__file__).with_name("cache")
        cache_dir.mkdir(exist_ok=True)
        f = cache_dir / f"{pair}_{duration}.json"
        have: dict[int, dict] = {}
        if f.exists():
            try:
                have = {c["ts"]: c for c in json.loads(f.read_text())}
            except Exception:
                have = {}
        now_ms = int(time.time() * 1000)
        want_from = now_ms - days * 86400 * 1000
        since = max(want_from, (max(have) + duration * 1000) if have and min(have) <= want_from + duration * 1000 else want_from)
        for _ in range(60):  # hard page cap
            raw = self._get("/api/exchange/1/candles",
                            {"pair": pair, "duration": duration, "since": since}).get("candles", [])
            new = [c for c in raw if int(c["timestamp"]) not in have]
            for c in raw:
                have[int(c["timestamp"])] = {
                    "ts": int(c["timestamp"]), "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]), "volume": float(c["volume"])}
            if not new:
                break
            since = max(int(c["timestamp"]) for c in raw) + duration * 1000
            if since >= now_ms:
                break
        out = sorted(have.values(), key=lambda c: c["ts"])
        f.write_text(json.dumps(out))
        return [c for c in out if c["ts"] >= want_from - duration * 1000 * 200]  # keep warm-up history

    # ------------------------------------------------------------- private
    def balances(self) -> list[dict]:
        return self._get("/api/1/balance").get("balance", [])

    def orders(self, pair: str = "", state: str = "") -> list[dict]:
        params: dict[str, Any] = {"limit": 100}
        if pair:
            params["market_pair"] = self.validate_pair(pair)
        if state:
            params["state"] = state.upper()
        return self._get("/api/exchange/2/listorders", params).get("orders") or []

    def my_trades(self, pair: str, days_back: int = 30, limit: int = 100) -> list[dict]:
        since = int((time.time() - days_back * 86400) * 1000)
        return self._get(
            "/api/1/listtrades",
            {"pair": self.validate_pair(pair), "since": since, "limit": min(limit, 100)},
        ).get("trades") or []

    def fee_info(self, pair: str) -> dict:
        return self._get("/api/1/fee_info", {"pair": self.validate_pair(pair)})
