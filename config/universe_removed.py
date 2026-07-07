"""
Permanent block-list of symbols confirmed as strategy losers by live trade history.

These were removed from config/watchlist_nse.py's WATCHLIST during the 2026-06 quality
revisions, each with documented trade-count/win-rate/P&L evidence (see watchlist_nse.py's
inline comments and module docstring for the full audit trail).

Why this file exists: removing a symbol from WATCHLIST alone does NOT stop
universe/manager.py's momentum-based scorer from later re-discovering and re-promoting it --
the scorer ranks on raw momentum and is blind to strategy P&L. The manager's only prior defense
was an 8-week `lockout` after manual removal, which expires and re-admits the symbol to
`watchlist` -- that expiry is what let LAURUSLABS.NS and THERMAX.NS (among others trending the
same way) leak back into `core` after their original 2026-06-17 removal, discovered 2026-07-06.

REMOVED_SYMBOLS is checked by UniverseManager on every weekly refresh (self-healing -- any
block-listed symbol found in any non-'removed' status is force-removed immediately, regardless
of how it got there) and at every promotion/watchlist-add entry point, with no expiry.

Do not add a symbol here without documented trade evidence (win rate / net P&L) that it
consistently loses money in THIS strategy -- this is not a general blacklist for stocks that are
merely unwanted. Symbols excluded from WATCHLIST for other reasons (e.g. BOSCHLTD/PAGEIND/MRF
for share-price/position-sizing reasons, or category-level curation like PSU utilities/FMCG/
cyclical metals) are deliberately NOT here, since there's no evidence tying them to losing trades.
"""

REMOVED_SYMBOLS = {
    # Governance risk / confirmed losers (docstring, 2026-06 revision)
    "IIFL.NS":        "governance risk, confirmed loser",
    "RECLTD.NS":      "governance risk, confirmed loser",
    "BIOCON.NS":      "governance risk, confirmed loser",
    "DEEPAKNTR.NS":   "governance risk, confirmed loser",
    "NAVINFLUOR.NS":  "governance risk, confirmed loser",

    # Quality revision, 2026-06-17 (per-symbol P&L evidence from watchlist_nse.py comments)
    "BEL.NS":         "3 trades, 0% WR, -Rs.6,271 (PSU defence, news-driven not momentum)",
    "SOLARINDS.NS":   "2 trades, 0% WR, -Rs.5,378 (niche defence chemicals, low float)",
    "HEROMOTOCO.NS":  "2 trades, 0% WR, -Rs.635 (range-bound, no momentum fit)",
    "TIINDIA.NS":     "2 trades, 0% WR, -Rs.1,265",
    "SHRIRAMFIN.NS":  "3 trades, 0% WR, -Rs.4,512 consistent loser",
    "LUPIN.NS":       "2 trades, 0% WR, -Rs.2,356 (FDA/recall risk disrupts momentum)",
    "ZYDUSLIFE.NS":   "3 trades, 0% WR, -Rs.2,486",
    "INDHOTEL.NS":    "4 trades, 0% WR, -Rs.2,145",
    "IRCTC.NS":       "1 trade, 0% WR, -Rs.2,009",
    "BHARTIARTL.NS":  "telecom sector 0% WR, -Rs.12.9k total combined with INDUSTOWER",
    "INDUSTOWER.NS":  "telecom sector 0% WR, -Rs.12.9k total combined with BHARTIARTL",
    "ESCORTS.NS":     "2 trades, 0% WR, -Rs.1,683 (agri-equipment, policy-driven)",
    "KEI.NS":         "3 trades, 0% WR, -Rs.3,722 (wires, similar to POLYCAB which never fires)",
    "SCHAEFFLER.NS":  "2 trades, 0% WR, -Rs.5,288 (industrial bearings, slow momentum)",
    "THERMAX.NS":     "6 trades, 17% WR, -Rs.4,281",
    "ABCAPITAL.NS":   "3 trades, 0% WR, -Rs.6,775",
    "AMBER.NS":       "4 trades, 25% WR, -Rs.3,570 (choppy EMS)",
    "TRITURBINE.NS":  "1 trade, 0% WR, -Rs.5,496 (industrial turbine, low momentum fit)",
    "SUPREMEIND.NS":  "2 trades, 0% WR, -Rs.1,129",
    "FORTIS.NS":      "4 trades, 0% WR, -Rs.6,248",
    "JBCHEPHARM.NS":  "4 trades, 25% WR, -Rs.4,698",
    "LAURUSLABS.NS":  "4 trades, 25% WR, -Rs.4,966",
    "IPCALAB.NS":     "1 trade, -Rs.5,425, consistent loser",
    "COFORGE.NS":     "4 trades, 25% WR, -Rs.3,072 (avg hold 1.5d = stop-hunted immediately)",
    "PERSISTENT.NS":  "2 trades, 0% WR, -Rs.2,471",
    "DIXON.NS":       "2 trades, 0% WR, -Rs.4,602",
    "ETERNAL.NS":     "2 trades, 0% WR, -Rs.4,657 (loss-making company, choppy price action)",
    "DEVYANI.NS":     "1 trade, 0% WR, -Rs.2,674 (QSR choppy)",
    "JUBLFOOD.NS":    "2 trades, 0% WR, -Rs.3,522 (QSR choppy)",
    "NAUKRI.NS":      "1 trade, 0% WR, -Rs.3,554 (news-driven not momentum)",
    "GODFRYPHLP.NS":  "2 trades, 0% WR, -Rs.4,323",
    "PHOENIXLTD.NS":  "realty sector 22% WR, -Rs.13,820 P&L drag combined with PRESTIGE",
    "PRESTIGE.NS":    "realty sector 22% WR, -Rs.13,820 P&L drag combined with PHOENIXLTD",
    "GODREJPROP.NS":  "realty sector removed, 22-25% WR, consistent negative P&L contribution",
}
