"""
LIVE_CASH_REBASELINE (2026-09-09) -- one-off, auditable correction of
momentum_atr's internal cash ledger + manual kill-switch clear.

Context (see docs/66, docs/67)
-------------------------------
docs/66's broker-truth correction (writing off CYIENT/WELCORP/ATHERENERG/
ASIANENE phantom shares to match Upstox reality) removed positions from
momentum_atr's ledger with no offsetting cash credit -- correctly, since no
real sale ever happened. That drove state.cash negative (-Rs.3,997.73)
purely as a bookkeeping artifact: the strategy's internal "cash I think I
have" number stopped matching the broker's real available cash
(Rs.38,482.92, verified live), even though the money itself was never
missing.

_get_effective_cash()'s min(internal_cash, real_cash, headroom) then floors
every BUY's budget at the negative internal number regardless of real
broker cash -- so momentum_atr could not deploy real, available capital.

Sequencing: this rebaseline was deliberately withheld until the kill switch
(momentum_atr/risk.py::check_kill_switch(), which computes equity from this
same state.cash) had a chance to evaluate the real ~44-46% drawdown against
the pre-correction peak_equity honestly. That evaluation already happened
(2026-09-09, ahead of the scheduled 09:17 IST run the next day -- an
operational mistake made while checking live numbers for the user, not the
scheduled run) and correctly tripped the kill switch. That resolves the
sequencing conflict: rebaselining cash now can no longer suppress or hide
that drawdown, since it is already recorded (kill_switch_tripped=True,
kill_switch_tripped_date=2026-09-09).

What this script does
----------------------
1. Reads real broker cash via UpstoxBroker.get_available_cash_or_none().
   Fails closed (aborts, writes nothing) if the broker read fails --
   a rebaseline must never be computed from a fabricated Rs.0.
2. Sets state.cash = real broker cash (the ledger is corrected to match
   the broker exactly, not adjusted by some inferred delta -- this is a
   deliberate "current reality is the new baseline" cutover, not a partial
   patch).
3. Does NOT touch peak_equity. It is left to the next natural
   check_kill_switch() call's own max(peak, equity) logic -- this script
   does not hand-pick a new peak.
4. Does NOT touch kill_switch_tripped / kill_switch_tripped_date --
   clearing the kill switch is a separate, explicit step (below), and the
   trip date/flag are a permanent historical record of when the drawdown
   was recorded; the rebaseline must never erase that.
5. Prints/logs the full event (timestamp, prior internal cash, new cash,
   broker cash read, source, reason) for the permanent record in docs/67.

Idempotency: if state.cash is already within Rs.1 of live broker cash, this
is a no-op (SKIP) -- safe to re-run.

Kill-switch clear (separate flag, --clear-kill-switch)
--------------------------------------------------------
User's explicit decision (2026-09-09): clear the kill switch now, resume
live BUYs, despite 3 of 4 docs/66 symbols' root cause remaining unconfirmed.
This is recorded here as a deliberate, explicit action -- not bundled into
the cash rebaseline by default, so the two decisions stay separately
auditable in the console/log output.

Usage: dry-run by default; --apply to write; --clear-kill-switch to also
clear kill_switch_tripped (only meaningful combined with --apply).
"""
import sys
from datetime import datetime

from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

REBASELINE_TOLERANCE = 1.0  # Rs. -- treat as already-applied within this margin


def main():
    apply = "--apply" in sys.argv
    clear_kill_switch = "--clear-kill-switch" in sys.argv

    from db import momentum_atr_repo as repo
    from broker.upstox import UpstoxBroker

    state = repo.get_state()
    prior_cash = state.cash

    broker = UpstoxBroker()
    real_cash = broker.get_available_cash_or_none()
    if real_cash is None:
        print("ABORT: broker cash read failed (get_available_cash_or_none() -> None). "
              "Refusing to rebaseline from an unverified number.")
        sys.exit(1)

    now = datetime.now().isoformat()
    print(f"[{'APPLY' if apply else 'DRY RUN'}] LIVE_CASH_REBASELINE -- {now}")
    print(f"  prior internal cash : Rs.{prior_cash:,.2f}")
    print(f"  verified broker cash: Rs.{real_cash:,.2f}  (source: Upstox get-funds-and-margin)")
    print(f"  adjustment          : Rs.{real_cash - prior_cash:,.2f}")

    if abs(prior_cash - real_cash) <= REBASELINE_TOLERANCE:
        print("  SKIP: internal cash already matches broker cash within tolerance.")
    else:
        print(f"  ACTION: set state.cash = Rs.{real_cash:,.2f} "
              f"(peak_equity and kill_switch_tripped left untouched by this step)")
        if apply:
            repo.update_state(cash=real_cash)
            print("  DONE: state.cash updated.")

    if clear_kill_switch:
        state_now = repo.get_state()
        if not state_now.kill_switch_tripped:
            print("  SKIP kill-switch clear: already not tripped.")
        else:
            print(f"  ACTION: clear kill_switch_tripped "
                  f"(tripped_date {state_now.kill_switch_tripped_date} kept as historical record)")
            if apply:
                repo.update_state(kill_switch_tripped=False)
                print("  DONE: kill_switch_tripped cleared.")

    if not apply:
        print("\nDry run only -- re-run with --apply (and --clear-kill-switch if desired) to write.")


if __name__ == "__main__":
    main()
