"""Setup backtester: score codified intraday setups on historical candles.

data.py   fetch/cache Kite candles (VM, after hours) and load them (anywhere)
setups.py exact setup rules (params as data)
engine.py bar-by-bar simulation, no lookahead, costs, context per trade

Design: docs/plans/2026-10-29-setup-backtester.md
"""
