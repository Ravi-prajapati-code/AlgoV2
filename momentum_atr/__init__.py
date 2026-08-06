"""
Standalone momentum x ATR live strategy (docs/57, docs/58).

Fully isolated vertical slice: own scoring/execution logic, own SQLite
ledger (db/momentum_atr_repo.py + db/momentum_atr.db), own capital
carve-out, own kill-switch. Reuses the broker layer (broker/base.py,
broker/upstox.py) and charges model (charges/calculator.py) directly, but
never imports from strategy/, portfolio/, or backtest/ -- see plan
velvet-cooking-minsky for the full architecture rationale.
"""
