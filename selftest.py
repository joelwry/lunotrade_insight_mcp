"""Offline self-test with synthetic data. Run: python selftest.py"""
import math, time
import indicators as ind, strategy

now = int(time.time() * 1000)
def series(n, dur, start=100.0, drift=0.004, amp=0.02):
    out, p = [], start
    for i in range(n):
        ts = now - (n - i) * dur * 1000
        p *= 1 + drift + amp * math.sin(i / 3) * 0.3
        out.append({"ts": ts, "open": p * 0.995, "high": p * 1.01, "low": p * 0.985, "close": p, "volume": 100 + i % 7})
    return out

d, h = series(200, 86400, drift=0.0012, amp=0.03), series(200, 14400, drift=0.0003)
px = d[-1]["close"]
tk = {"bid": px * 0.9995, "ask": px * 1.0005, "last_trade": px, "timestamp": now, "rolling_24_hour_volume": "10"}
r = strategy.analyze_swing("TESTNGN", d, h, tk, {}, 30000, 1.0)
print(r["signal"], r["score"], r.get("vetoes"))
print(r.get("illustrative_plan"))
assert r["trade_execution"] == "DISABLED"
c = [x["close"] for x in d]
assert 0 <= ind.rsi(c) <= 100 and ind.ema(c, 20) and ind.atr(d) > 0 and ind.macd(c)
# pullback-in-uptrend: rise, peak ~12% above, pull back, resume -> expect a plan
pb = []
p = 100.0
for i in range(200):
    p *= 1.004 if i < 150 else (0.992 if i < 175 else 1.003)
    pb.append({"ts": now - (200 - i) * 86400000, "open": p*.997, "high": p*1.008, "low": p*.992, "close": p, "volume": 100})
pbh = [{"ts": now-(200-i)*14400000, "open": 1, "high": 1, "low": 1, "close": p*(1+0.0003*i/200), "volume": 1} for i in range(200)]
for c_ in pbh: c_.update(open=c_["close"], high=c_["close"]*1.003, low=c_["close"]*.997)
tkp = {**tk, "bid": pb[-1]["close"]*.9995, "ask": pb[-1]["close"]*1.0005, "last_trade": pb[-1]["close"]}
rp = strategy.analyze_swing("PBNGN", pb, pbh, tkp, {}, 30000, 1.0)
print(rp["signal"], rp["score"], rp["vetoes"]); print(rp.get("illustrative_plan"))
assert rp["signal"] == "BUY_CANDIDATE" and abs(rp["illustrative_plan"]["max_loss_if_stopped_quote"] - 300) < 1
bear = series(200, 86400, drift=-0.004)
rb = strategy.analyze_swing("BEARNGN", bear, h, {**tk, "bid": bear[-1]["close"]*.999, "ask": bear[-1]["close"]*1.001}, {}, 30000, 1.0)
print(rb["signal"], rb["score"], rb["vetoes"]); assert rb["signal"] == "AVOID"
from luno_client import LunoClient, LunoError
try: LunoClient()._get("/api/1/marketorder", {}); raise SystemExit("GUARD FAILED")
except LunoError as e: print("guard ok:", e)
import random, backtest
random.seed(7)
big_d, p = [], 100.0
for i in range(900):
    p *= 1 + random.gauss(0.004 if (i // 120) % 2 == 0 else -0.003, 0.03)
    o = p * (1 + random.gauss(0, .005))
    big_d.append({"ts": now - (900 - i) * 86400000, "open": o, "high": max(o, p) * (1 + abs(random.gauss(0, .01))),
                  "low": min(o, p) * (1 - abs(random.gauss(0, .01))), "close": p, "volume": 100 * (1 + random.random())})
big_h = []
for dc in big_d:
    for k in range(6):
        c_ = dc["close"] * (1 + random.gauss(0, .004))
        big_h.append({"ts": dc["ts"] + k * 14400000, "open": c_, "high": c_ * 1.004, "low": c_ * .996, "close": c_, "volume": 10})
bt = backtest.run_backtest("RNDNGN", big_d, big_h, days=700)
print("backtest:", bt["overall"]["trades"], "trades, expectancy", bt["overall"]["expectancy_R_net"], "| B&H", bt["buy_and_hold_return_pct"])
assert bt["overall"]["trades"] > 5 and all(-4 < x["r"] < 4 for x in bt["recent_trades"])
print("ALL OK")
