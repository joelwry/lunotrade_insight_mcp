# LuTrade Insight MCP (read-only Luno analyst for Claude Desktop)

Trade execution is disabled by design: the client only sends GET requests to an allowlist of read endpoints.

## Setup (PowerShell, in this folder)
    py -3.11 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    copy .env.example .env      # then put your READ-ONLY key id/secret in .env
    python selftest.py          # offline logic test
    python check_connection.py  # live Luno test

## Claude Desktop  (%APPDATA%\Claude\claude_desktop_config.json)
Merge into "mcpServers" (keep other servers). Credentials are read from .env, so they need not be repeated here:

    "lutrade-insight": {
      "command": "C:\\Users\\dell\\Documents\\codebase\\pythonic_world\\pyscripts\\lutrade_insight_mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\dell\\Documents\\codebase\\pythonic_world\\pyscripts\\lutrade_insight_mcp\\server.py"]
    }

Fully quit and restart Claude Desktop (tray icon > Quit).

## Example prompts
- "Run server_status, then scan_watchlist and rank the setups."
- "Do a full swing analysis on XBTNGN with 30000 NGN capital and write me a report."
- "Show my portfolio_snapshot and flag concentration risk."
- "Log the best setup as a paper signal; later run review_paper_signals."

## Backtesting
    python backtest.py ADAZAR --days 730        # or any Luno pair you trade
Or ask Claude: "Run a backtest on XBTNGN for 2 years and compare to buy & hold."
History is cached in `cache/` so repeat runs are fast.
