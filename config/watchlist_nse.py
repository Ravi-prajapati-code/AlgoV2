"""
NSE watchlist — Quality momentum universe, cleaned 2026-06.

REQUIRED after editing ALL_SYMBOLS: run `python3 scripts/sync_static_universe.py` before
committing. Backtests reconstruct point-in-time universe membership from dated snapshots logged
in the DB (see db/universe_repo.py's static-watchlist tracking functions) rather than always
applying this file's current contents to every historical date — that unconditional-current-list
behavior is exactly the confirmed look-ahead/survivorship bias documented in
docs/13_Independent_Institutional_Review.md (§2, §4.3, §10). An edit here with no matching sync
call means any backtest run afterward silently reverts to treating this file's contents as always
having applied, for every historical date — the same bug, recurring. `tests/test_static_universe_sync.py`
checks the sync mechanism itself works, but cannot detect a forgotten sync call after a future edit.

Removed categories:
  - Adani group (headline/governance risk)
  - PSU utilities & commodity PSUs (NTPC, ONGC, BPCL, GAIL, etc.)
  - FMCG stocks (defensive/range-bound, no momentum fit)
  - Range-bound large IT (INFY, HCLTECH, WIPRO, TECHM)
  - Cyclical metals (JSWSTEEL, TATASTEEL, VEDL, JINDALSTEL)
  - High-debt realty (DLF, LODHA, SOBHA, OBEROIRLTY)
  - Holding companies (BAJAJHLDNG, BAJAJFINSV)
  - Governance risks & confirmed losers (IIFL, RECLTD, BIOCON, DEEPAKNTR, NAVINFLUOR)

Universe revision test (2026-06): removing dead-weight large-caps (RELIANCE, TCS, ICICIBANK,
AXISBANK, GRASIM, ASIANPAINT, DRREDDY, CIPLA, APOLLOHOSP) and adding defence PSUs / mid-cap IT
HURT performance (20-34% CAGR vs 39.46% baseline). Root cause: RS rank = percentile across
universe — adding high-momentum stocks displaces existing winners below RS≥72 threshold.
Range-bound large-caps serve as RS anchors — keep them.

Quality revision (2026-06-17): systematic audit removed 20 confirmed losers (0% WR multi-trade
or WR<30% + negative P&L with 4+ trades). Also removed Consumer Services losers (INDHOTEL,
IRCTC, DEVYANI, JUBLFOOD, NAUKRI) and Realty sector (GODREJPROP, PHOENIXLTD, PRESTIGE).
Removed: INDUSTOWER, BHARTIARTL, BEL, KEI, SOLARINDS, SCHAEFFLER, ETERNAL, PERSISTENT, LUPIN,
HEROMOTOCO, TIINDIA, ZYDUSLIFE, ESCORTS, SUPREMEIND, FORTIS, JBCHEPHARM, LAURUSLABS, THERMAX,
COFORGE, DIXON. Universe: 100 stocks.

RS anchor rule (2026-06-17): EICHERMOT, GRINDWELL, DALBHARAT tested for removal but caused
-6.5pp CAGR drop (RS anchor effect). Restored. Never prune anchors by P&L alone.
"""

WATCHLIST = [
    # ── Nifty 100 ─────────────────────────────────────────────────────────────
    # Capital Goods / Industrial
    ('ABB.NS',          'Capital Goods',                        'ABB India Ltd.'),
    ('ASHOKLEY.NS',     'Automobile and Auto Components',       'Ashok Leyland Ltd.'),
    ('ASTRAL.NS',       'Capital Goods',                        'Astral Ltd.'),
    # BEL removed — 3 trades, 0% WR, -₹6,271 (PSU defence, news-driven not momentum),
    ('BHARATFORG.NS',   'Automobile and Auto Components',       'Bharat Forge Ltd.'),
    # BOSCHLTD removed — ₹37k/share, cannot size at ₹75k capital
    ('HAL.NS',          'Capital Goods',                        'Hindustan Aeronautics Ltd.'),
    ('LT.NS',           'Construction',                         'Larsen & Toubro Ltd.'),
    ('POLYCAB.NS',      'Capital Goods',                        'Polycab India Ltd.'),
    ('SIEMENS.NS',      'Capital Goods',                        'Siemens Ltd.'),
    # SOLARINDS removed — 2 trades, 0% WR, -₹5,378 (niche defence chemicals, low float)

    # Automobile & Auto Components
    ('BAJAJ-AUTO.NS',   'Automobile and Auto Components',       'Bajaj Auto Ltd.'),
    ('EICHERMOT.NS',    'Automobile and Auto Components',       'Eicher Motors Ltd.'),           # RS anchor; removal hurts (-6.5pp CAGR)
    # HEROMOTOCO removed — 2 trades, 0% WR, -₹635 (range-bound, no momentum fit)
    ('M&M.NS',          'Automobile and Auto Components',       'Mahindra & Mahindra Ltd.'),
    ('MARUTI.NS',       'Automobile and Auto Components',       'Maruti Suzuki India Ltd.'),
    ('MOTHERSON.NS',    'Automobile and Auto Components',       'Samvardhana Motherson International Ltd.'),
    # TIINDIA removed — 2 trades, 0% WR, -₹1,265
    ('TMCV.NS',         'Automobile and Auto Components',       'Tata Motors Ltd.'),
    ('TVSMOTOR.NS',     'Automobile and Auto Components',       'TVS Motor Company Ltd.'),

    # Financial Services — quality private
    ('AXISBANK.NS',     'Financial Services',                   'Axis Bank Ltd.'),
    ('BAJFINANCE.NS',   'Financial Services',                   'Bajaj Finance Ltd.'),
    ('CHOLAFIN.NS',     'Financial Services',                   'Cholamandalam Investment and Finance Company Ltd.'),
    ('HDFCAMC.NS',      'Financial Services',                   'HDFC Asset Management Company Ltd.'),
    ('HDFCBANK.NS',     'Financial Services',                   'HDFC Bank Ltd.'),
    ('HDFCLIFE.NS',     'Financial Services',                   'HDFC Life Insurance Company Ltd.'),
    ('ICICIBANK.NS',    'Financial Services',                   'ICICI Bank Ltd.'),
    ('ICICIGI.NS',      'Financial Services',                   'ICICI Lombard General Insurance Company Ltd.'),
    ('INDUSINDBK.NS',   'Financial Services',                   'IndusInd Bank Ltd.'),
    ('JIOFIN.NS',       'Financial Services',                   'Jio Financial Services Ltd.'),
    ('KOTAKBANK.NS',    'Financial Services',                   'Kotak Mahindra Bank Ltd.'),
    ('LTF.NS',          'Financial Services',                   'L&T Finance Ltd.'),
    ('M&MFIN.NS',       'Financial Services',                   'Mahindra & Mahindra Financial Services Ltd.'),
    ('MUTHOOTFIN.NS',   'Financial Services',                   'Muthoot Finance Ltd.'),
    ('SBICARD.NS',      'Financial Services',                   'SBI Cards and Payment Services Ltd.'),
    ('SBILIFE.NS',      'Financial Services',                   'SBI Life Insurance Company Ltd.'),
    ('SBIN.NS',         'Financial Services',                   'State Bank of India'),
    # SHRIRAMFIN removed — 3 trades, 0% win rate, -₹4,512 consistent loser

    # Healthcare
    ('APOLLOHOSP.NS',   'Healthcare',                           'Apollo Hospitals Enterprise Ltd.'),
    ('CIPLA.NS',        'Healthcare',                           'Cipla Ltd.'),
    ('DIVISLAB.NS',     'Healthcare',                           "Divi's Laboratories Ltd."),
    ('DRREDDY.NS',      'Healthcare',                           "Dr. Reddy's Laboratories Ltd."),
    # LUPIN removed — 2 trades, 0% WR, -₹2,356 (FDA/recall risk disrupts momentum),
    ('SUNPHARMA.NS',    'Healthcare',                           'Sun Pharmaceutical Industries Ltd.'),
    ('TORNTPHARM.NS',   'Healthcare',                           'Torrent Pharmaceuticals Ltd.'),
    # ZYDUSLIFE removed — 3 trades, 0% WR, -₹2,486,

    # Information Technology — specialty/niche only
    ('MPHASIS.NS',      'Information Technology',               'MphasiS Ltd.'),
    ('TCS.NS',          'Information Technology',               'Tata Consultancy Services Ltd.'),
    ('TATAELXSI.NS',    'Information Technology',               'Tata Elxsi Ltd.'),

    # Consumer Durables
    ('ASIANPAINT.NS',   'Consumer Durables',                    'Asian Paints Ltd.'),
    ('HAVELLS.NS',      'Consumer Durables',                    'Havells India Ltd.'),
    ('TITAN.NS',        'Consumer Durables',                    'Titan Company Ltd.'),

    # Consumer Services — winners only
    ('DMART.NS',        'Consumer Services',                    'Avenue Supermarts Ltd.'),
    ('INDIGO.NS',       'Services',                             'InterGlobe Aviation Ltd.'),
    ('TRENT.NS',        'Consumer Services',                    'Trent Ltd.'),
    # INDHOTEL removed — 4 trades, 0% WR, -₹2,145
    # IRCTC removed — 1 trade, 0% WR, -₹2,009

    # Chemicals — specialty
    ('PIDILITIND.NS',   'Chemicals',                            'Pidilite Industries Ltd.'),
    ('SRF.NS',          'Chemicals',                            'SRF Ltd.'),

    # Construction Materials / Cement
    ('GRASIM.NS',       'Construction Materials',               'Grasim Industries Ltd.'),
    ('SHREECEM.NS',     'Construction Materials',               'Shree Cement Ltd.'),
    ('ULTRACEMCO.NS',   'Construction Materials',               'UltraTech Cement Ltd.'),

    # Telecommunication — removed BHARTIARTL + INDUSTOWER (sector 0% WR, -₹12.9k total)
    # Fast-moving EMS sector added in Capital Goods midcap section

    # Power — private/renewable only
    ('JSWENERGY.NS',    'Power',                                'JSW Energy Ltd.'),
    ('TATAPOWER.NS',    'Power',                                'Tata Power Co. Ltd.'),

    # Realty — removed (22-25% WR, consistent negative P&L contribution)

    # Metals — selective
    ('HINDALCO.NS',     'Metals & Mining',                      'Hindalco Industries Ltd.'),

    # FMCG — growth FMCG only (proven momentum in backtest)
    ('VBL.NS',          'Fast Moving Consumer Goods',           'Varun Beverages Ltd.'),

    # Oil & Gas — Reliance only (diversified conglomerate)
    ('RELIANCE.NS',     'Oil Gas & Consumable Fuels',           'Reliance Industries Ltd.'),

    # ── Nifty Midcap 150 additions ────────────────────────────────────────────
    # Capital Goods / Infrastructure
    ('CGPOWER.NS',      'Capital Goods',                        'CG Power and Industrial Solutions Ltd.'),
    ('CUMMINSIND.NS',   'Capital Goods',                        'Cummins India Ltd.'),
    # ESCORTS removed — 2 trades, 0% WR, -₹1,683 (agri-equipment, policy-driven)
    ('GRINDWELL.NS',    'Capital Goods',                        'Grindwell Norton Ltd.'),        # RS anchor; removal hurts (-6.5pp CAGR)
    ('IRCON.NS',        'Capital Goods',                        'IRCON International Ltd.'),
    ('JSWINFRA.NS',     'Services',                             'JSW Infrastructure Ltd.'),
    ('KEC.NS',          'Capital Goods',                        'KEC International Ltd.'),
    # KEI removed — 3 trades, 0% WR, -₹3,722 (wires similar to POLYCAB which never fires),
    ('NCC.NS',          'Capital Goods',                        'NCC Ltd.'),
    ('RVNL.NS',         'Capital Goods',                        'Rail Vikas Nigam Ltd.'),
    # SCHAEFFLER removed — 2 trades, 0% WR, -₹5,288 (industrial bearings, slow momentum),
    # THERMAX removed — 6 trades, 17% WR, -₹4,281
    # ABCAPITAL removed — 3 trades 0% win rate -₹6,775

    # EMS — Electronics Manufacturing Services (highest momentum sector on NSE)
    ('KAYNES.NS',       'Capital Goods',                        'Kaynes Technology India Ltd.'),
    # AMBER removed — 4 trades, 25% WR, -₹3,570 (choppy EMS)
    # TRITURBINE removed — 1 trade, 0% WR, -₹5,496 (industrial turbine, low momentum fit)
    # SUPREMEIND removed — 2 trades, 0% WR, -₹1,129
    ('CAMS.NS',         'Financial Services',                   'Computer Age Management Services Ltd.'),

    # Automobile & Auto Components
    ('BALKRISIND.NS',   'Automobile and Auto Components',       'Balkrishna Industries Ltd.'),
    ('UNOMINDA.NS',     'Automobile and Auto Components',       'UNO Minda Ltd.'),
    ('CRAFTSMAN.NS',    'Automobile and Auto Components',       'Craftsman Automation Ltd.'),
    ('ENDURANCE.NS',    'Automobile and Auto Components',       'Endurance Technologies Ltd.'),
    ('SUNDRMFAST.NS',   'Automobile and Auto Components',       'Sundram Fasteners Ltd.'),

    # Financial Services — midcap quality
    ('NUVAMA.NS',       'Financial Services',                   'Nuvama Wealth Management Ltd.'),
    ('AUBANK.NS',       'Financial Services',                   'AU Small Finance Bank Ltd.'),
    ('CDSL.NS',         'Financial Services',                   'Central Depository Services (India) Ltd.'),
    ('KFINTECH.NS',     'Financial Services',                   'KFin Technologies Ltd.'),
    ('LICHSGFIN.NS',    'Financial Services',                   'LIC Housing Finance Ltd.'),
    ('MFSL.NS',         'Financial Services',                   'Max Financial Services Ltd.'),

    # Healthcare — midcap
    ('ALKEM.NS',        'Healthcare',                           'Alkem Laboratories Ltd.'),
    ('AUROPHARMA.NS',   'Healthcare',                           'Aurobindo Pharma Ltd.'),
    # FORTIS removed — 4 trades, 0% WR, -₹6,248
    ('GLAND.NS',        'Healthcare',                           'Gland Pharma Ltd.'),
    # JBCHEPHARM removed — 4 trades, 25% WR, -₹4,698
    # LAURUSLABS removed — 4 trades, 25% WR, -₹4,966
    ('MANKIND.NS',      'Healthcare',                           'Mankind Pharma Ltd.'),
    ('MAXHEALTH.NS',    'Healthcare',                           'Max Healthcare Institute Ltd.'),
    # IPCALAB removed — consistent loser (1 trade -₹5,425)

    # Information Technology — specialty/niche midcap
    ('KPITTECH.NS',     'Information Technology',               'KPIT Technologies Ltd.'),
    ('LTTS.NS',         'Information Technology',               'L&T Technology Services Ltd.'),
    ('OFSS.NS',         'Information Technology',               'Oracle Financial Services Software Ltd.'),
    # COFORGE removed — 4 trades, 25% WR, -₹3,072 (avg hold 1.5d = stop-hunted immediately)
    # PERSISTENT removed — 2 trades, 0% WR, -₹2,471

    # Consumer Durables — midcap
    ('CROMPTON.NS',     'Consumer Durables',                    'Crompton Greaves Consumer Electricals Ltd.'),
    # DIXON removed — 2 trades, 0% WR, -₹4,602
    ('KALYANKJIL.NS',   'Consumer Durables',                    'Kalyan Jewellers India Ltd.'),
    ('VOLTAS.NS',       'Consumer Durables',                    'Voltas Ltd.'),
    # PAGEIND removed — ₹37k/share + blocked sector

    # Consumer Services — midcap
    # ETERNAL removed — 2 trades, 0% WR, -₹4,657 (loss-making company, choppy price action)
    ('DELHIVERY.NS',    'Services',                             'Delhivery Ltd.'),
    # DEVYANI removed — 1 trade, 0% WR, -₹2,674 (QSR choppy)
    # JUBLFOOD removed — 2 trades, 0% WR, -₹3,522 (QSR choppy)
    # NAUKRI removed — 1 trade, 0% WR, -₹3,554 (news-driven not momentum)
    # MRF removed — ₹1.3L/share, cannot buy even 1 share at ₹75k capital

    # Chemicals — specialty midcap
    ('CLEAN.NS',        'Chemicals',                            'Clean Science and Technology Ltd.'),
    ('FLUOROCHEM.NS',   'Chemicals',                            'Gujarat Fluorochemicals Ltd.'),
    ('PIIND.NS',        'Chemicals',                            'PI Industries Ltd.'),

    # Construction Materials — midcap
    ('ACC.NS',          'Construction Materials',               'ACC Ltd.'),
    ('DALBHARAT.NS',    'Construction Materials',               'Dalmia Bharat Ltd.'),           # RS anchor; removal hurts (-6.5pp CAGR)

    # Metals — value-add only
    ('APLAPOLLO.NS',    'Metals & Mining',                      'APL Apollo Tubes Ltd.'),

    # Oil & Gas — private gas distribution
    ('GUJGASLTD.NS',    'Oil Gas & Consumable Fuels',           'Gujarat Gas Ltd.'),

    # GODFRYPHLP removed — 2 trades 0% win rate -₹4,323
    # PHOENIXLTD, PRESTIGE removed — Realty sector 22% WR, -₹13,820 P&L drag

    # FMCG — growth FMCG
    ('JYOTHYLAB.NS',    'Fast Moving Consumer Goods',           'Jyothy Labs Ltd.'),
    ('RADICO.NS',       'Fast Moving Consumer Goods',           'Radico Khaitan Ltd.'),

    # ETF — defensive basket
    ('GOLDBEES.NS',     'ETF',                                  'Nippon India ETF Gold BeES'),
]

# Quick lookup maps
SYMBOL_TO_SECTOR = {sym: sector for sym, sector, _ in WATCHLIST}
SYMBOL_TO_NAME   = {sym: name   for sym, _, name  in WATCHLIST}
ALL_SYMBOLS      = [sym for sym, _, _ in WATCHLIST]
ALL_SECTORS      = sorted(set(sector for _, sector, _ in WATCHLIST))
