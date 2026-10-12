"""
Paper ("shadow") books for momentum_atr score research (docs/80, docs/82).

NO ORDERS, NO BROKER CALLS, NO WRITES to the live momentum_atr database.
Each book is a virtual Rs.1,00,000 portfolio run on the same rules as live
(momentum_atr/execution.py) and the research engine
(research/momentum_atr_audit/audit_sim.py, defaults):

  - top-N (3) by score, equal cash split + greedy leftover cascade
  - swap: worst holding <= -3% and best >= +3% -> sell worst, put all cash
    into best
  - rank exit: at most one holding per day whose rank > N, proceeds split
    over the current top-N (holders included)
  - top-up of empty slots with the best unheld names; re-entry when flat
  - cost_pct per side

Timing: every decision uses the close of session d-1 and fills at the
OPEN of session d; marks use the close of d -- the same "decide on close,
execute next open" lag as live and the backtest. The book is advanced only
over COMPLETED daily bars, so it is run after market close.

Books differ only in the score formula (see FORMULAS). Both use live's
eligibility: >= 50 bars, 20-day avg volume >= 300k, close > SMA50 and
close > SMA200.
"""
import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

N = 3
SWAP_PCT = 3.0
COST_PCT = 0.15
START_CAPITAL = 100_000.0
MIN_HISTORY = 50
MIN_AVG_VOLUME_20D = 300_000


def _features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - close.shift()).abs(),
                    (df["low"] - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
    out = pd.DataFrame(index=df.index)
    out["close"] = close
    out["open"] = df["open"]
    out["mom"] = (close - sma50) / sma50 * 100
    out["atr_pct"] = atr / close * 100
    out["px_over_sma50"] = close / sma50
    out["eligible"] = ((out["mom"] > 0) & (close > sma200)
                       & (df["volume"].rolling(20).mean() >= MIN_AVG_VOLUME_20D)
                       & (pd.Series(range(1, len(df) + 1), index=df.index) >= MIN_HISTORY))
    return out


FORMULAS = {
    # identical to momentum_atr/scoring.py with MOMENTUM_ATR_SMA200_FILTER on
    "LIVE": lambda f: f["mom"] * f["atr_pct"],
    # docs/80 B2: mom x (close/SMA50) x ATR%
    "B2": lambda f: f["mom"] * f["px_over_sma50"] * f["atr_pct"],
}


def score_frames(data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """formula -> DataFrame[date x symbol] of scores (0 where ineligible,
    NaN where the symbol has no bar that day), plus 'open'/'close' frames."""
    feats = {s: _features(df.sort_index()) for s, df in data.items() if len(df)}
    out = {}
    for name, fn in FORMULAS.items():
        out[name] = pd.DataFrame({s: fn(f).where(f["eligible"], 0.0).where(f["close"].notna())
                                  for s, f in feats.items()})
    out["open"] = pd.DataFrame({s: f["open"] for s, f in feats.items()})
    out["close"] = pd.DataFrame({s: f["close"] for s, f in feats.items()})
    for k in out:
        out[k].index = pd.to_datetime(out[k].index).normalize()
    return out


def ranked(scores: pd.Series) -> List[str]:
    s = scores.dropna()
    s = s[s > 0]
    return list(s.sort_values(ascending=False, kind="mergesort").index)


@dataclass
class Book:
    name: str
    cash: float = START_CAPITAL
    positions: Dict[str, dict] = field(default_factory=dict)  # sym -> shares, entry_price, entry_date
    pending_swap: Optional[list] = None                        # [loser, winner]
    pending_exit: Optional[str] = None
    initial_done: bool = False
    last_date: Optional[str] = None
    trades: List[dict] = field(default_factory=list)
    equity: List[dict] = field(default_factory=list)

    # ---------------------------------------------------------------- io
    @classmethod
    def load(cls, path: str, name: str) -> "Book":
        if not os.path.exists(path):
            return cls(name=name)
        with open(path) as fh:
            return cls(**json.load(fh))

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.__dict__, fh, indent=1, default=str)
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written state

    # ----------------------------------------------------------- mechanics
    def _buy(self, day, sym, n, px, reason):
        price = px * (1 + COST_PCT / 100)
        self.cash -= n * price
        p = self.positions.get(sym)
        if p:
            tot = p["shares"] + n
            p["entry_price"] = (p["shares"] * p["entry_price"] + n * price) / tot
            p["shares"] = tot
        else:
            self.positions[sym] = {"shares": n, "entry_price": price, "entry_date": day}
        self.trades.append({"date": day, "action": "BUY", "symbol": sym, "shares": n, "price": round(px, 4), "reason": reason})

    def _sell(self, day, sym, px, reason):
        price = px * (1 - COST_PCT / 100)
        p = self.positions.pop(sym)
        self.cash += p["shares"] * price
        self.trades.append({"date": day, "action": "SELL", "symbol": sym, "shares": p["shares"], "price": round(px, 4),
                            "reason": reason, "entry_date": p["entry_date"],
                            "net_pnl": round((price - p["entry_price"]) * p["shares"], 2),
                            "ret_pct": round((price / p["entry_price"] - 1) * 100, 3)})

    def _split(self, day, names, opens, reason):
        names = [s for s in names if opens.get(s, 0) and opens[s] > 0]
        if not names or self.cash <= 0:
            return
        each = self.cash / len(names)
        cost = 1 + COST_PCT / 100
        buys = {}
        for s in names:
            n = int(each // (opens[s] * cost))
            if n > 0:
                buys[s] = n
        remaining = self.cash - sum(n * opens[s] * cost for s, n in buys.items())
        changed = True
        while changed:  # greedy leftover cascade, same order as engine._greedy_fill
            changed = False
            for s in names:
                n = int(remaining // (opens[s] * cost))
                if n > 0:
                    buys[s] = buys.get(s, 0) + n
                    remaining -= n * opens[s] * cost
                    changed = True
        for s, n in buys.items():
            self._buy(day, s, n, opens[s], reason)

    def step(self, day: str, rank_prev: List[str], rank_today: List[str],
             opens: Dict[str, float], closes: Dict[str, float]) -> None:
        """Advance one completed session. rank_prev = ranking at the previous
        close (drives today's open fills); rank_today = ranking at today's
        close (schedules tomorrow's exits)."""
        if not self.initial_done:
            if len(rank_prev) >= N:
                self._split(day, rank_prev[:N], opens, "INITIAL_TOPN_SPLIT")
                self.initial_done = True
        else:
            if self.pending_swap:
                loser, winner = self.pending_swap
                if loser in self.positions and opens.get(loser):
                    self._sell(day, loser, opens[loser], "STOP_LOSS_-3%_SWAP")
                if opens.get(winner):
                    n = int(self.cash // (opens[winner] * (1 + COST_PCT / 100)))
                    if n > 0:
                        self._buy(day, winner, n, opens[winner], "TAKE_PROFIT_+3%_SWAP")
            self.pending_swap = None
            if self.pending_exit and self.pending_exit in self.positions and opens.get(self.pending_exit):
                self._sell(day, self.pending_exit, opens[self.pending_exit], "RANK_RULE_EXIT")
                self._split(day, rank_prev[:N], opens, "RANK_EXIT_REALLOC_TOPN")
            self.pending_exit = None
            if 0 < len(self.positions) < N:
                fill = [s for s in rank_prev if s not in self.positions][:N - len(self.positions)]
                self._split(day, fill, opens, "TOPUP_FILL_SLOTS")
        if self.initial_done and not self.positions and len(rank_prev) >= N:
            self._split(day, rank_prev[:N], opens, "REENTRY_TOPN_AFTER_FLATTEN")

        invested = sum(p["shares"] * closes.get(s, p["entry_price"]) for s, p in self.positions.items())
        self.equity.append({"date": day, "equity": round(self.cash + invested, 2), "cash": round(self.cash, 2),
                            "positions": {s: p["shares"] for s, p in self.positions.items()}})
        self.last_date = day
        if not self.positions:
            return
        rets = {s: (closes[s] - p["entry_price"]) / p["entry_price"] * 100
                for s, p in self.positions.items() if closes.get(s)}
        if rets:
            best, worst = max(rets, key=rets.get), min(rets, key=rets.get)
            if worst != best and rets[worst] <= -SWAP_PCT and rets[best] >= SWAP_PCT:
                self.pending_swap = [worst, best]
        rank_of = {s: i + 1 for i, s in enumerate(rank_today)}
        for s in self.positions:
            if self.pending_swap and s == self.pending_swap[0]:
                continue
            if rank_of.get(s, 10 ** 9) > N:
                self.pending_exit = s
                break


def advance(books: Dict[str, Book], frames: Dict[str, pd.DataFrame], start: str) -> List[str]:
    """Process every completed session after each book's last_date (or from
    `start`). A session is skipped -- book NOT advanced, retried next run --
    if any currently held symbol has no open/close bar for it (fetch stall),
    so a data gap can never masquerade as a rank exit."""
    days = [d for d in frames["close"].index if d >= pd.Timestamp(start)]
    log = []
    for name, book in books.items():
        sc = frames[name]
        for d in days:
            ds = str(d.date())
            if book.last_date and ds <= book.last_date:
                continue
            k = sc.index.get_loc(d)
            if k == 0:
                continue
            opens = frames["open"].loc[d].dropna().to_dict()
            closes = frames["close"].loc[d].dropna().to_dict()
            held = list(book.positions)
            if book.pending_swap:
                held += book.pending_swap
            missing = [s for s in held if s not in opens or s not in closes or (isinstance(opens[s], float) and math.isnan(opens[s]))]
            if missing:
                log.append(f"{name} {ds}: deferred, no bar for held {missing}")
                break  # keep sessions in order; retry from this day next run
            book.step(ds, ranked(sc.iloc[k - 1]), ranked(sc.loc[d]), opens, closes)
            log.append(f"{name} {ds}: equity {book.equity[-1]['equity']:.0f} holdings {list(book.positions)}")
    return log
