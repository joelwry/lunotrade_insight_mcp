"""Live check. Run: python check_connection.py"""
from luno_client import LunoClient
c = LunoClient()
print("ticker:", c.ticker("ADANGN"))
print("candles:", len(c.candles("ADANGN", 86400, 60)), "daily")
if c.has_credentials:
    print("balances:", [f'{b["asset"]}: {b["balance"]}' for b in c.balances()])
else:
    print("No credentials in .env - private tools disabled.")
