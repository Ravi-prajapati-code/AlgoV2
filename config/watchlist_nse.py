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

Nifty 500 expansion (2026-07-21): added 284 Nifty 500 constituents not already covered, applying
the same exclusion rules as above (Adani, PSU utilities/commodities, Realty, Telecommunication,
cyclical metals, defensive FMCG, range-bound large IT, holding cos, governance-risk names).

Nifty 500 full inclusion (2026-07-21b): user explicitly overrode the curated-exclusion approach
above — added the remaining 120 Nifty 500 names with NO sector/name filtering, reintroducing
every category the "Removed categories" list and per-stock comments above were built to keep out.
Universe is now the full Nifty 500 (504 symbols incl. GOLDBEES + 4 legacy non-Nifty500 names).
"Removed categories" and inline "# X removed" comments above describe the ORIGINAL 100-name
curation rationale only — they are historical record, not active filters, as of this revision.

RS≥72 admission gate is percentile-based and now spans a 5x larger universe — this is a
documented, accepted risk (see docs/13_Independent_Institutional_Review.md), not re-derived;
monitor closely.
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

    # ── Nifty 500 Expansion (2026-07-21) ──────────────────────────────────────
    # Added per user request to broaden live universe. Point-in-time tracked via
    # sync_static_universe_snapshot() so backtests before this date are unaffected
    # (see module docstring — avoids the 2026-07-17 look-ahead REJECT pattern).
    # Same exclusion rules applied as the curated Nifty 100 + Midcap 150 list above:
    # Adani group, PSU utilities/commodities, Realty, Telecommunication, cyclical
    # metals, defensive FMCG, range-bound large IT, holding cos, governance-risk
    # names, and every individually-tested proven loser named in the comments above
    # (BOSCHLTD, MRF, COFORGE, etc.) all excluded — see docs/24_Rejected_Forever.md.
    # RS admission gate (RS>=72 percentile) now spans a larger universe — accepted
    # risk, watching closely rather than re-deriving the threshold (see docs/13).

    # Automobile and Auto Components
    ('APOLLOTYRE.NS',   'Automobile and Auto Components',      'Apollo Tyres Ltd.'),
    ('ARE&M.NS',        'Automobile and Auto Components',      'Amara Raja Energy & Mobility Ltd.'),
    ('ASAHIINDIA.NS',   'Automobile and Auto Components',      'Asahi India Glass Ltd.'),
    ('ATHERENERG.NS',   'Automobile and Auto Components',      'Ather Energy Ltd.'),
    ('BELRISE.NS',      'Automobile and Auto Components',      'Belrise Industries Ltd.'),
    ('CEATLTD.NS',      'Automobile and Auto Components',      'Ceat Ltd.'),
    ('CIEINDIA.NS',     'Automobile and Auto Components',      'CIE Automotive India Ltd.'),
    ('EXIDEIND.NS',     'Automobile and Auto Components',      'Exide Industries Ltd.'),
    ('FORCEMOT.NS',     'Automobile and Auto Components',      'Force Motors Ltd.'),
    ('GABRIEL.NS',      'Automobile and Auto Components',      'Gabriel India Ltd.'),
    ('HYUNDAI.NS',      'Automobile and Auto Components',      'Hyundai Motor India Ltd.'),
    ('JBMA.NS',         'Automobile and Auto Components',      'JBM Auto Ltd.'),
    ('JKTYRE.NS',       'Automobile and Auto Components',      'JK Tyre & Industries Ltd.'),
    ('MINDACORP.NS',    'Automobile and Auto Components',      'Minda Corporation Ltd.'),
    ('MSUMI.NS',        'Automobile and Auto Components',      'Motherson Sumi Wiring India Ltd.'),
    ('OLAELEC.NS',      'Automobile and Auto Components',      'Ola Electric Mobility Ltd.'),
    ('OLECTRA.NS',      'Automobile and Auto Components',      'Olectra Greentech Ltd.'),
    ('RKFORGE.NS',      'Automobile and Auto Components',      'Ramkrishna Forgings Ltd.'),
    ('SONACOMS.NS',     'Automobile and Auto Components',      'Sona BLW Precision Forgings Ltd.'),
    ('TENNIND.NS',      'Automobile and Auto Components',      'Tenneco Clean Air India Ltd.'),
    ('TMPV.NS',         'Automobile and Auto Components',      'Tata Motors Passenger Vehicles Ltd.'),
    ('ZFCVINDIA.NS',    'Automobile and Auto Components',      'ZF Commercial Vehicle Control Systems India Ltd.'),

    # Capital Goods
    ('ACE.NS',          'Capital Goods',                       'Action Construction Equipment Ltd.'),
    ('AIAENG.NS',       'Capital Goods',                       'AIA Engineering Ltd.'),
    ('APARINDS.NS',     'Capital Goods',                       'Apar Industries Ltd.'),
    ('BDL.NS',          'Capital Goods',                       'Bharat Dynamics Ltd.'),
    ('BHEL.NS',         'Capital Goods',                       'Bharat Heavy Electricals Ltd.'),
    ('CARBORUNIV.NS',   'Capital Goods',                       'Carborundum Universal Ltd.'),
    ('COCHINSHIP.NS',   'Capital Goods',                       'Cochin Shipyard Ltd.'),
    ('CPPLUS.NS',       'Capital Goods',                       'Aditya Infotech Ltd.'),
    ('DATAPATTNS.NS',   'Capital Goods',                       'Data Patterns (India) Ltd.'),
    ('ELECON.NS',       'Capital Goods',                       'Elecon Engineering Co. Ltd.'),
    ('ELGIEQUIP.NS',    'Capital Goods',                       'Elgi Equipments Ltd.'),
    ('EMMVEE.NS',       'Capital Goods',                       'Emmvee Photovoltaic Power Ltd.'),
    ('ENRIN.NS',        'Capital Goods',                       'Siemens Energy India Ltd.'),
    ('FINCABLES.NS',    'Capital Goods',                       'Finolex Cables Ltd.'),
    ('GALLANTT.NS',     'Capital Goods',                       'Gallantt Ispat Ltd.'),
    ('GPIL.NS',         'Capital Goods',                       'Godawari Power & Ispat Ltd.'),
    ('GRAPHITE.NS',     'Capital Goods',                       'Graphite India Ltd.'),
    ('GRSE.NS',         'Capital Goods',                       'Garden Reach Shipbuilders & Engineers Ltd.'),
    ('GVT&D.NS',        'Capital Goods',                       'GE Vernova T&D India Ltd.'),
    ('HBLENGINE.NS',    'Capital Goods',                       'HBL Engineering Ltd.'),
    ('HEG.NS',          'Capital Goods',                       'H.E.G. Ltd.'),
    ('HONAUT.NS',       'Capital Goods',                       'Honeywell Automation India Ltd.'),
    ('INOXWIND.NS',     'Capital Goods',                       'Inox Wind Ltd.'),
    ('JINDALSAW.NS',    'Capital Goods',                       'Jindal Saw Ltd.'),
    ('JWL.NS',          'Capital Goods',                       'Jupiter Wagons Ltd.'),
    ('JYOTICNC.NS',     'Capital Goods',                       'Jyoti CNC Automation Ltd.'),
    ('KIRLOSENG.NS',    'Capital Goods',                       'Kirloskar Oil Eng Ltd.'),
    ('MAZDOCK.NS',      'Capital Goods',                       'Mazagoan Dock Shipbuilders Ltd.'),
    ('POWERINDIA.NS',   'Capital Goods',                       'Hitachi Energy India Ltd.'),
    ('PREMIERENE.NS',   'Capital Goods',                       'Premier Energies Ltd.'),
    ('PTCIL.NS',        'Capital Goods',                       'PTC Industries Ltd.'),
    ('RHIM.NS',         'Capital Goods',                       'RHI MAGNESITA INDIA LTD.'),
    ('RRKABEL.NS',      'Capital Goods',                       'R R Kabel Ltd.'),
    ('SCHNEIDER.NS',    'Capital Goods',                       'Schneider Electric Infrastructure Ltd.'),
    ('SHYAMMETL.NS',    'Capital Goods',                       'Shyam Metalics and Energy Ltd.'),
    ('SUZLON.NS',       'Capital Goods',                       'Suzlon Energy Ltd.'),
    ('SYRMA.NS',        'Capital Goods',                       'Syrma SGS Technology Ltd.'),
    ('TARIL.NS',        'Capital Goods',                       'Transformers And Rectifiers (India) Ltd.'),
    ('TEGA.NS',         'Capital Goods',                       'Tega Industries Ltd.'),
    ('TIMKEN.NS',       'Capital Goods',                       'Timken India Ltd.'),
    ('TITAGARH.NS',     'Capital Goods',                       'Titagarh Rail Systems Ltd.'),
    ('USHAMART.NS',     'Capital Goods',                       'Usha Martin Ltd.'),
    ('WAAREEENER.NS',   'Capital Goods',                       'Waaree Energies Ltd.'),
    ('WELCORP.NS',      'Capital Goods',                       'Welspun Corp Ltd.'),
    ('ZENTEC.NS',       'Capital Goods',                       'Zen Technologies Ltd.'),

    # Chemicals
    ('AARTIIND.NS',     'Chemicals',                           'Aarti Industries Ltd.'),
    ('ANURAS.NS',       'Chemicals',                           'Anupam Rasayan India Ltd.'),
    ('ATUL.NS',         'Chemicals',                           'Atul Ltd.'),
    ('BAYERCROP.NS',    'Chemicals',                           'Bayer Cropscience Ltd.'),
    ('CHAMBLFERT.NS',   'Chemicals',                           'Chambal Fertilizers & Chemicals Ltd.'),
    ('COROMANDEL.NS',   'Chemicals',                           'Coromandel International Ltd.'),
    ('DEEPAKFERT.NS',   'Chemicals',                           'Deepak Fertilisers & Petrochemicals Corp. Ltd.'),
    ('FACT.NS',         'Chemicals',                           'Fertilisers and Chemicals Travancore Ltd.'),
    ('HSCL.NS',         'Chemicals',                           'Himadri Speciality Chemical Ltd.'),
    ('JUBLINGREA.NS',   'Chemicals',                           'Jubilant Ingrevia Ltd.'),
    ('LINDEINDIA.NS',   'Chemicals',                           'Linde India Ltd.'),
    ('PARADEEP.NS',     'Chemicals',                           'Paradeep Phosphates Ltd.'),
    ('PCBL.NS',         'Chemicals',                           'PCBL Chemical Ltd.'),
    ('SPLPETRO.NS',     'Chemicals',                           'Supreme Petrochem Ltd.'),
    ('SUMICHEM.NS',     'Chemicals',                           'Sumitomo Chemical India Ltd.'),
    ('SWANCORP.NS',     'Chemicals',                           'Swan Corp Ltd.'),
    ('TATACHEM.NS',     'Chemicals',                           'Tata Chemicals Ltd.'),
    ('UPL.NS',          'Chemicals',                           'UPL Ltd.'),

    # Construction
    ('AFCONS.NS',       'Construction',                        'Afcons Infrastructure Ltd.'),
    ('CEMPRO.NS',       'Construction',                        'Cemindia Projects Ltd.'),
    ('ENGINERSIN.NS',   'Construction',                        'Engineers India Ltd.'),
    ('IRB.NS',          'Construction',                        'IRB Infrastructure Developers Ltd.'),
    ('KPIL.NS',         'Construction',                        'Kalpataru Projects International Ltd.'),
    ('RITES.NS',        'Construction',                        'RITES Ltd.'),
    ('TECHNOE.NS',      'Construction',                        'Techno Electric & Engineering Company Ltd.'),

    # Construction Materials
    ('AMBUJACEM.NS',    'Construction Materials',              'Ambuja Cements Ltd.'),
    ('INDIACEM.NS',     'Construction Materials',              'India Cements Ltd.'),
    ('JKCEMENT.NS',     'Construction Materials',              'J.K. Cement Ltd.'),
    ('JSWCEMENT.NS',    'Construction Materials',              'JSW Cement Ltd.'),
    ('NUVOCO.NS',       'Construction Materials',              'Nuvoco Vistas Corporation Ltd.'),
    ('RAMCOCEM.NS',     'Construction Materials',              'The Ramco Cements Ltd.'),

    # Consumer Durables
    ('BATAINDIA.NS',    'Consumer Durables',                   'Bata India Ltd.'),
    ('BERGEPAINT.NS',   'Consumer Durables',                   'Berger Paints India Ltd.'),
    ('BLUESTARCO.NS',   'Consumer Durables',                   'Blue Star Ltd.'),
    ('JSWDULUX.NS',     'Consumer Durables',                   'JSW Dulux Ltd.'),
    ('KAJARIACER.NS',   'Consumer Durables',                   'Kajaria Ceramics Ltd.'),
    ('LGEINDIA.NS',     'Consumer Durables',                   'LG Electronics India Ltd.'),
    ('PGEL.NS',         'Consumer Durables',                   'PG Electroplast Ltd.'),
    ('WHIRLPOOL.NS',    'Consumer Durables',                   'Whirlpool of India Ltd.'),

    # Consumer Services
    ('ABFRL.NS',        'Consumer Services',                   'Aditya Birla Fashion and Retail Ltd.'),
    ('ABLBL.NS',        'Consumer Services',                   'Aditya Birla Lifestyle Brands Ltd.'),
    ('BLS.NS',          'Consumer Services',                   'BLS International Services Ltd.'),
    ('CARTRADE.NS',     'Consumer Services',                   'Cartrade Tech Ltd.'),
    ('CHALET.NS',       'Consumer Services',                   'Chalet Hotels Ltd.'),
    ('EIHOTEL.NS',      'Consumer Services',                   'EIH Ltd.'),
    ('FIRSTCRY.NS',     'Consumer Services',                   'Brainbees Solutions Ltd.'),
    ('INDIAMART.NS',    'Consumer Services',                   'Indiamart Intermesh Ltd.'),
    ('ITCHOTELS.NS',    'Consumer Services',                   'ITC Hotels Ltd.'),
    ('LEMONTREE.NS',    'Consumer Services',                   'Lemon Tree Hotels Ltd.'),
    ('LENSKART.NS',     'Consumer Services',                   'Lenskart Solutions Ltd.'),
    ('MEESHO.NS',       'Consumer Services',                   'Meesho Ltd.'),
    ('NYKAA.NS',        'Consumer Services',                   'FSN E-Commerce Ventures Ltd.'),
    ('PWL.NS',          'Consumer Services',                   'Physicswallah Ltd.'),
    ('SAPPHIRE.NS',     'Consumer Services',                   'Sapphire Foods India Ltd.'),
    ('SWIGGY.NS',       'Consumer Services',                   'Swiggy Ltd.'),
    ('TBOTEK.NS',       'Consumer Services',                   'TBO Tek Ltd.'),
    ('THELEELA.NS',     'Consumer Services',                   'Leela Palaces Hotels & Resorts Ltd.'),
    ('TRAVELFOOD.NS',   'Consumer Services',                   'Travel Food Services Ltd.'),
    ('URBANCO.NS',      'Consumer Services',                   'Urban Company Ltd.'),
    ('VMM.NS',          'Consumer Services',                   'Vishal Mega Mart Ltd.'),

    # Diversified
    ('3MINDIA.NS',      'Diversified',                         '3M India Ltd.'),
    ('DCMSHRIRAM.NS',   'Diversified',                         'DCM Shriram Ltd.'),
    ('GODREJIND.NS',    'Diversified',                         'Godrej Industries Ltd.'),

    # Financial Services
    ('360ONE.NS',       'Financial Services',                  '360 ONE WAM Ltd.'),
    ('AADHARHFC.NS',    'Financial Services',                  'Aadhar Housing Finance Ltd.'),
    ('AAVAS.NS',        'Financial Services',                  'Aavas Financiers Ltd.'),
    ('ABSLAMC.NS',      'Financial Services',                  'Aditya Birla Sun Life AMC Ltd.'),
    ('AIIL.NS',         'Financial Services',                  'Authum Investment & Infrastructure Ltd.'),
    ('ANANDRATHI.NS',   'Financial Services',                  'Anand Rathi Wealth Ltd.'),
    ('ANGELONE.NS',     'Financial Services',                  'Angel One Ltd.'),
    ('APTUS.NS',        'Financial Services',                  'Aptus Value Housing Finance India Ltd.'),
    ('BAJAJHFL.NS',     'Financial Services',                  'Bajaj Housing Finance Ltd.'),
    ('BANDHANBNK.NS',   'Financial Services',                  'Bandhan Bank Ltd.'),
    ('BANKBARODA.NS',   'Financial Services',                  'Bank of Baroda'),
    ('BANKINDIA.NS',    'Financial Services',                  'Bank of India'),
    ('BSE.NS',          'Financial Services',                  'BSE Ltd.'),
    ('CANBK.NS',        'Financial Services',                  'Canara Bank'),
    ('CANFINHOME.NS',   'Financial Services',                  'Can Fin Homes Ltd.'),
    ('CANHLIFE.NS',     'Financial Services',                  'Canara HSBC Life Insurance Company Ltd.'),
    ('CENTRALBK.NS',    'Financial Services',                  'Central Bank of India'),
    ('CGCL.NS',         'Financial Services',                  'Capri Global Capital Ltd.'),
    ('CHOICEIN.NS',     'Financial Services',                  'Choice International Ltd.'),
    ('CHOLAHLDNG.NS',   'Financial Services',                  'Cholamandalam Financial Holdings Ltd.'),
    ('CREDITACC.NS',    'Financial Services',                  'CreditAccess Grameen Ltd.'),
    ('CRISIL.NS',       'Financial Services',                  'CRISIL Ltd.'),
    ('CUB.NS',          'Financial Services',                  'City Union Bank Ltd.'),
    ('FEDERALBNK.NS',   'Financial Services',                  'Federal Bank Ltd.'),
    ('FIVESTAR.NS',     'Financial Services',                  'Five-Star Business Finance Ltd.'),
    ('GODIGIT.NS',      'Financial Services',                  'Go Digit General Insurance Ltd.'),
    ('GROWW.NS',        'Financial Services',                  'Billionbrains Garage Ventures Ltd.'),
    ('HDBFS.NS',        'Financial Services',                  'HDB Financial Services Ltd.'),
    ('HOMEFIRST.NS',    'Financial Services',                  'Home First Finance Company India Ltd.'),
    ('HUDCO.NS',        'Financial Services',                  'Housing & Urban Development Corporation Ltd.'),
    ('ICICIAMC.NS',     'Financial Services',                  'ICICI Prudential Asset Management Company Ltd.'),
    ('ICICIPRULI.NS',   'Financial Services',                  'ICICI Prudential Life Insurance Company Ltd.'),
    ('IDBI.NS',         'Financial Services',                  'IDBI Bank Ltd.'),
    ('IDFCFIRSTB.NS',   'Financial Services',                  'IDFC First Bank Ltd.'),
    ('IEX.NS',          'Financial Services',                  'Indian Energy Exchange Ltd.'),
    ('IFCI.NS',         'Financial Services',                  'IFCI Ltd.'),
    ('INDIANB.NS',      'Financial Services',                  'Indian Bank'),
    ('IOB.NS',          'Financial Services',                  'Indian Overseas Bank'),
    ('IREDA.NS',        'Financial Services',                  'Indian Renewable Energy Development Agency Ltd.'),
    ('J&KBANK.NS',      'Financial Services',                  'Jammu & Kashmir Bank Ltd.'),
    ('JMFINANCIL.NS',   'Financial Services',                  'JM Financial Ltd.'),
    ('KARURVYSYA.NS',   'Financial Services',                  'Karur Vysya Bank Ltd.'),
    ('LICI.NS',         'Financial Services',                  'Life Insurance Corporation of India'),
    ('MAHABANK.NS',     'Financial Services',                  'Bank of Maharashtra'),
    ('MANAPPURAM.NS',   'Financial Services',                  'Manappuram Finance Ltd.'),
    ('MCX.NS',          'Financial Services',                  'Multi Commodity Exchange of India Ltd.'),
    ('MOTILALOFS.NS',   'Financial Services',                  'Motilal Oswal Financial Services Ltd.'),
    ('NAM-INDIA.NS',    'Financial Services',                  'Nippon Life India Asset Management Ltd.'),
    ('NIVABUPA.NS',     'Financial Services',                  'Niva Bupa Health Insurance Company Ltd.'),
    ('PAYTM.NS',        'Financial Services',                  'One 97 Communications Ltd.'),
    ('PINELABS.NS',     'Financial Services',                  'Pine Labs Ltd.'),
    ('PIRAMALFIN.NS',   'Financial Services',                  'Piramal Finance Ltd.'),
    ('PNB.NS',          'Financial Services',                  'Punjab National Bank'),
    ('PNBHOUSING.NS',   'Financial Services',                  'PNB Housing Finance Ltd.'),
    ('POLICYBZR.NS',    'Financial Services',                  'PB Fintech Ltd.'),
    ('POONAWALLA.NS',   'Financial Services',                  'Poonawalla Fincorp Ltd.'),
    ('RBLBANK.NS',      'Financial Services',                  'RBL Bank Ltd.'),
    ('SAMMAANCAP.NS',   'Financial Services',                  'Sammaan Capital Ltd.'),
    ('SBFC.NS',         'Financial Services',                  'SBFC Finance Ltd.'),
    ('STARHEALTH.NS',   'Financial Services',                  'Star Health and Allied Insurance Company Ltd.'),
    ('SUNDARMFIN.NS',   'Financial Services',                  'Sundaram Finance Ltd.'),
    ('TATACAP.NS',      'Financial Services',                  'Tata Capital Ltd.'),
    ('TATAINVEST.NS',   'Financial Services',                  'Tata Investment Corporation Ltd.'),
    ('UCOBANK.NS',      'Financial Services',                  'UCO Bank'),
    ('UNIONBANK.NS',    'Financial Services',                  'Union Bank of India'),
    ('UTIAMC.NS',       'Financial Services',                  'UTI Asset Management Company Ltd.'),
    ('YESBANK.NS',      'Financial Services',                  'Yes Bank Ltd.'),

    # Healthcare
    ('ABBOTINDIA.NS',   'Healthcare',                          'Abbott India Ltd.'),
    ('ACUTAAS.NS',      'Healthcare',                          'Acutaas Chemicals Ltd.'),
    ('AJANTPHARM.NS',   'Healthcare',                          'Ajanta Pharmaceuticals Ltd.'),
    ('ANTHEM.NS',       'Healthcare',                          'Anthem Biosciences Ltd.'),
    ('ASTERDM.NS',      'Healthcare',                          'Aster DM Healthcare Ltd.'),
    ('BLUEJET.NS',      'Healthcare',                          'Blue Jet Healthcare Ltd.'),
    ('CAPLIPOINT.NS',   'Healthcare',                          'Caplin Point Laboratories Ltd.'),
    ('COHANCE.NS',      'Healthcare',                          'Cohance Lifesciences Ltd.'),
    ('CONCORDBIO.NS',   'Healthcare',                          'Concord Biotech Ltd.'),
    ('EMCURE.NS',       'Healthcare',                          'Emcure Pharmaceuticals Ltd.'),
    ('ERIS.NS',         'Healthcare',                          'Eris Lifesciences Ltd.'),
    ('GLAXO.NS',        'Healthcare',                          'Glaxosmithkline Pharmaceuticals Ltd.'),
    ('GLENMARK.NS',     'Healthcare',                          'Glenmark Pharmaceuticals Ltd.'),
    ('GRANULES.NS',     'Healthcare',                          'Granules India Ltd.'),
    ('INDGN.NS',        'Healthcare',                          'Indegene Ltd.'),
    ('JUBLPHARMA.NS',   'Healthcare',                          'Jubilant Pharmova Ltd.'),
    ('KIMS.NS',         'Healthcare',                          'Krishna Institute of Medical Sciences Ltd.'),
    ('LALPATHLAB.NS',   'Healthcare',                          'Dr. Lal Path Labs Ltd.'),
    ('MEDANTA.NS',      'Healthcare',                          'Global Health Ltd.'),
    ('NATCOPHARM.NS',   'Healthcare',                          'NATCO Pharma Ltd.'),
    ('NEULANDLAB.NS',   'Healthcare',                          'Neuland Laboratories Ltd.'),
    ('NH.NS',           'Healthcare',                          'Narayana Hrudayalaya Ltd.'),
    ('ONESOURCE.NS',    'Healthcare',                          'Onesource Specialty Pharma Ltd.'),
    ('PFIZER.NS',       'Healthcare',                          'Pfizer Ltd.'),
    ('POLYMED.NS',      'Healthcare',                          'Poly Medicure Ltd.'),
    ('PPLPHARMA.NS',    'Healthcare',                          'Piramal Pharma Ltd.'),
    ('RAINBOW.NS',      'Healthcare',                          'Rainbow Childrens Medicare Ltd.'),
    ('SAILIFE.NS',      'Healthcare',                          'Sai Life Sciences Ltd.'),
    ('SYNGENE.NS',      'Healthcare',                          'Syngene International Ltd.'),
    ('VIJAYA.NS',       'Healthcare',                          'Vijaya Diagnostic Centre Ltd.'),
    ('WOCKPHARMA.NS',   'Healthcare',                          'Wockhardt Ltd.'),

    # Information Technology
    ('AFFLE.NS',        'Information Technology',              'Affle 3i Ltd.'),
    ('BSOFT.NS',        'Information Technology',              'Birlasoft Ltd.'),
    ('CYIENT.NS',       'Information Technology',              'Cyient Ltd.'),
    ('HEXT.NS',         'Information Technology',              'Hexaware Technologies Ltd.'),
    ('IKS.NS',          'Information Technology',              'Inventurus Knowledge Solutions Ltd.'),
    ('INTELLECT.NS',    'Information Technology',              'Intellect Design Arena Ltd.'),
    ('LATENTVIEW.NS',   'Information Technology',              'Latent View Analytics Ltd.'),
    ('LTM.NS',          'Information Technology',              'LTM Ltd.'),
    ('MAPMYINDIA.NS',   'Information Technology',              'C.E. Info Systems Ltd.'),
    ('NETWEB.NS',       'Information Technology',              'Netweb Technologies India Ltd.'),
    ('NEWGEN.NS',       'Information Technology',              'Newgen Software Technologies Ltd.'),
    ('SAGILITY.NS',     'Information Technology',              'Sagility Ltd.'),
    ('SONATSOFTW.NS',   'Information Technology',              'Sonata Software Ltd.'),
    ('TATATECH.NS',     'Information Technology',              'Tata Technologies Ltd.'),
    ('ZENSARTECH.NS',   'Information Technology',              'Zensar Technolgies Ltd.'),

    # Media Entertainment & Publication
    ('PVRINOX.NS',      'Media Entertainment & Publication',   'PVR INOX Ltd.'),
    ('SAREGAMA.NS',     'Media Entertainment & Publication',   'Saregama India Ltd'),
    ('SUNTV.NS',        'Media Entertainment & Publication',   'Sun TV Network Ltd.'),
    ('ZEEL.NS',         'Media Entertainment & Publication',   'Zee Entertainment Enterprises Ltd.'),

    # Metals & Mining
    ('GMDCLTD.NS',      'Metals & Mining',                     'Gujarat Mineral Development Corporation Ltd.'),
    ('GRAVITA.NS',      'Metals & Mining',                     'Gravita India Ltd.'),
    ('HINDCOPPER.NS',   'Metals & Mining',                     'Hindustan Copper Ltd.'),
    ('JAINREC.NS',      'Metals & Mining',                     'Jain Resource Recycling Ltd.'),
    ('JSL.NS',          'Metals & Mining',                     'Jindal Stainless Ltd.'),
    ('LLOYDSME.NS',     'Metals & Mining',                     'Lloyds Metals And Energy Ltd.'),
    ('NATIONALUM.NS',   'Metals & Mining',                     'National Aluminium Co. Ltd.'),
    ('NSLNISP.NS',      'Metals & Mining',                     'NMDC Steel Ltd.'),
    ('SARDAEN.NS',      'Metals & Mining',                     'Sarda Energy and Minerals Ltd.'),

    # Oil Gas & Consumable Fuels
    ('AEGISLOG.NS',     'Oil Gas & Consumable Fuels',          'Aegis Logistics Ltd.'),
    ('AEGISVOPAK.NS',   'Oil Gas & Consumable Fuels',          'Aegis Vopak Terminals Ltd.'),
    ('CASTROLIND.NS',   'Oil Gas & Consumable Fuels',          'Castrol India Ltd.'),
    ('CHENNPETRO.NS',   'Oil Gas & Consumable Fuels',          'Chennai Petroleum Corporation Ltd.'),
    ('IGL.NS',          'Oil Gas & Consumable Fuels',          'Indraprastha Gas Ltd.'),
    ('MGL.NS',          'Oil Gas & Consumable Fuels',          'Mahanagar Gas Ltd.'),
    ('MRPL.NS',         'Oil Gas & Consumable Fuels',          'Mangalore Refinery & Petrochemicals Ltd.'),
    ('PETRONET.NS',     'Oil Gas & Consumable Fuels',          'Petronet LNG Ltd.'),

    # Power
    ('ACMESOLAR.NS',    'Power',                               'ACME Solar Holdings Ltd.'),
    ('CESC.NS',         'Power',                               'CESC Ltd.'),
    ('JPPOWER.NS',      'Power',                               'Jaiprakash Power Ventures Ltd.'),
    ('NAVA.NS',         'Power',                               'Nava Ltd.'),
    ('NTPCGREEN.NS',    'Power',                               'NTPC Green Energy Ltd.'),
    ('RPOWER.NS',       'Power',                               'Reliance Power Ltd.'),
    ('TORNTPOWER.NS',   'Power',                               'Torrent Power Ltd.'),

    # Services
    ('BLUEDART.NS',     'Services',                            'Blue Dart Express Ltd.'),
    ('ECLERX.NS',       'Services',                            'eClerx Services Ltd.'),
    ('FSL.NS',          'Services',                            'Firstsource Solutions Ltd.'),
    ('GESHIP.NS',       'Services',                            'Great Eastern Shipping Co. Ltd.'),
    ('GMRAIRPORT.NS',   'Services',                            'GMR Airports Ltd.'),
    ('IGIL.NS',         'Services',                            'International Gemological Institute Ltd.'),
    ('MMTC.NS',         'Services',                            'MMTC Ltd.'),
    ('REDINGTON.NS',    'Services',                            'Redington Ltd.'),
    ('SCI.NS',          'Services',                            'Shipping Corporation of India Ltd.'),

    # Textiles
    ('KPRMILL.NS',      'Textiles',                            'K.P.R. Mill Ltd.'),
    ('TRIDENT.NS',      'Textiles',                            'Trident Ltd.'),
    ('VTL.NS',          'Textiles',                            'Vardhman Textiles Ltd.'),
    ('WELSPUNLIV.NS',   'Textiles',                            'Welspun Living Ltd.'),
    # ── Nifty 500 Full Inclusion (2026-07-21b) ────────────────────────────────
    # User explicitly overrode the earlier curated-exclusion approach: include ALL
    # remaining Nifty 500 constituents, no sector/name filtering. This reintroduces
    # categories previously excluded above for documented 0% WR / negative P&L
    # (Adani group, PSU utilities, Realty, Telecom, cyclical metals, defensive FMCG,
    # range-bound large IT, holding cos, governance-risk, individually-tested losers
    # like BOSCHLTD/MRF/COFORGE) — accepted risk per explicit instruction, watch closely.

    # Automobile and Auto Components
    ('BOSCHLTD.NS',     'Automobile and Auto Components',   'Bosch Ltd.'),
    ('HEROMOTOCO.NS',   'Automobile and Auto Components',   'Hero MotoCorp Ltd.'),
    ('MRF.NS',          'Automobile and Auto Components',   'MRF Ltd.'),
    ('SCHAEFFLER.NS',   'Automobile and Auto Components',   'Schaeffler India Ltd.'),
    ('TIINDIA.NS',      'Automobile and Auto Components',   'Tube Investments of India Ltd.'),

    # Capital Goods
    ('BEL.NS',          'Capital Goods',                    'Bharat Electronics Ltd.'),
    ('BEML.NS',         'Capital Goods',                    'BEML Ltd.'),
    ('ESCORTS.NS',      'Capital Goods',                    'Escorts Kubota Ltd.'),
    ('KEI.NS',          'Capital Goods',                    'KEI Industries Ltd.'),
    ('SUPREMEIND.NS',   'Capital Goods',                    'Supreme Industries Ltd.'),
    ('THERMAX.NS',      'Capital Goods',                    'Thermax Ltd.'),
    ('TRITURBINE.NS',   'Capital Goods',                    'Triveni Turbine Ltd.'),

    # Chemicals
    ('DEEPAKNTR.NS',    'Chemicals',                        'Deepak Nitrite Ltd.'),
    ('NAVINFLUOR.NS',   'Chemicals',                        'Navin Fluorine International Ltd.'),
    ('SOLARINDS.NS',    'Chemicals',                        'Solar Industries India Ltd.'),

    # Construction
    ('NBCC.NS',         'Construction',                     'NBCC (India) Ltd.'),

    # Consumer Durables
    ('AMBER.NS',        'Consumer Durables',                'Amber Enterprises India Ltd.'),
    ('DIXON.NS',        'Consumer Durables',                'Dixon Technologies (India) Ltd.'),

    # Consumer Services
    ('DEVYANI.NS',      'Consumer Services',                'Devyani International Ltd.'),
    ('ETERNAL.NS',      'Consumer Services',                'Eternal Ltd.'),
    ('INDHOTEL.NS',     'Consumer Services',                'Indian Hotels Co. Ltd.'),
    ('IRCTC.NS',        'Consumer Services',                'Indian Railway Catering And Tourism Corporation Ltd.'),
    ('JUBLFOOD.NS',     'Consumer Services',                'Jubilant Foodworks Ltd.'),
    ('NAUKRI.NS',       'Consumer Services',                'Info Edge (India) Ltd.'),

    # Fast Moving Consumer Goods
    ('ABDL.NS',         'Fast Moving Consumer Goods',       'Allied Blenders and Distillers Ltd.'),
    ('AWL.NS',          'Fast Moving Consumer Goods',       'AWL Agri Business Ltd.'),
    ('BALRAMCHIN.NS',   'Fast Moving Consumer Goods',       'Balrampur Chini Mills Ltd.'),
    ('BBTC.NS',         'Fast Moving Consumer Goods',       'Bombay Burmah Trading Corporation Ltd.'),
    ('BIKAJI.NS',       'Fast Moving Consumer Goods',       'Bikaji Foods International Ltd.'),
    ('BRITANNIA.NS',    'Fast Moving Consumer Goods',       'Britannia Industries Ltd.'),
    ('CCL.NS',          'Fast Moving Consumer Goods',       'CCL Products (I) Ltd.'),
    ('COLPAL.NS',       'Fast Moving Consumer Goods',       'Colgate Palmolive (India) Ltd.'),
    ('DABUR.NS',        'Fast Moving Consumer Goods',       'Dabur India Ltd.'),
    ('DOMS.NS',         'Fast Moving Consumer Goods',       'DOMS Industries Ltd.'),
    ('EIDPARRY.NS',     'Fast Moving Consumer Goods',       'E.I.D. Parry (India) Ltd.'),
    ('EMAMILTD.NS',     'Fast Moving Consumer Goods',       'Emami Ltd.'),
    ('GILLETTE.NS',     'Fast Moving Consumer Goods',       'Gillette India Ltd.'),
    ('GODFRYPHLP.NS',   'Fast Moving Consumer Goods',       'Godfrey Phillips India Ltd.'),
    ('GODREJCP.NS',     'Fast Moving Consumer Goods',       'Godrej Consumer Products Ltd.'),
    ('HINDUNILVR.NS',   'Fast Moving Consumer Goods',       'Hindustan Unilever Ltd.'),
    ('HONASA.NS',       'Fast Moving Consumer Goods',       'Honasa Consumer Ltd.'),
    ('ITC.NS',          'Fast Moving Consumer Goods',       'ITC Ltd.'),
    ('LTFOODS.NS',      'Fast Moving Consumer Goods',       'LT Foods Ltd.'),
    ('MARICO.NS',       'Fast Moving Consumer Goods',       'Marico Ltd.'),
    ('NESTLEIND.NS',    'Fast Moving Consumer Goods',       'Nestle India Ltd.'),
    ('PATANJALI.NS',    'Fast Moving Consumer Goods',       'Patanjali Foods Ltd.'),
    ('TATACONSUM.NS',   'Fast Moving Consumer Goods',       'Tata Consumer Products Ltd.'),
    ('UBL.NS',          'Fast Moving Consumer Goods',       'United Breweries Ltd.'),
    ('UNITDSPR.NS',     'Fast Moving Consumer Goods',       'United Spirits Ltd.'),
    ('ZYDUSWELL.NS',    'Fast Moving Consumer Goods',       'Zydus Wellness Ltd.'),

    # Financial Services
    ('ABCAPITAL.NS',    'Financial Services',               'Aditya Birla Capital Ltd.'),
    ('BAJAJFINSV.NS',   'Financial Services',               'Bajaj Finserv Ltd.'),
    ('BAJAJHLDNG.NS',   'Financial Services',               'Bajaj Holdings & Investment Ltd.'),
    ('GICRE.NS',        'Financial Services',               'General Insurance Corporation of India'),
    ('IIFL.NS',         'Financial Services',               'IIFL Finance Ltd.'),
    ('IRFC.NS',         'Financial Services',               'Indian Railway Finance Corporation Ltd.'),
    ('NIACL.NS',        'Financial Services',               'The New India Assurance Company Ltd.'),
    ('PFC.NS',          'Financial Services',               'Power Finance Corporation Ltd.'),
    ('RECLTD.NS',       'Financial Services',               'REC Ltd.'),
    ('SHRIRAMFIN.NS',   'Financial Services',               'Shriram Finance Ltd.'),

    # Healthcare
    ('BIOCON.NS',       'Healthcare',                       'Biocon Ltd.'),
    ('FORTIS.NS',       'Healthcare',                       'Fortis Healthcare Ltd.'),
    ('IPCALAB.NS',      'Healthcare',                       'Ipca Laboratories Ltd.'),
    ('LAURUSLABS.NS',   'Healthcare',                       'Laurus Labs Ltd.'),
    ('LUPIN.NS',        'Healthcare',                       'Lupin Ltd.'),
    ('ZYDUSLIFE.NS',    'Healthcare',                       'Zydus Lifesciences Ltd.'),

    # Information Technology
    ('COFORGE.NS',      'Information Technology',           'Coforge Ltd.'),
    ('HCLTECH.NS',      'Information Technology',           'HCL Technologies Ltd.'),
    ('INFY.NS',         'Information Technology',           'Infosys Ltd.'),
    ('PERSISTENT.NS',   'Information Technology',           'Persistent Systems Ltd.'),
    ('TECHM.NS',        'Information Technology',           'Tech Mahindra Ltd.'),
    ('WIPRO.NS',        'Information Technology',           'Wipro Ltd.'),

    # Metals & Mining
    ('ADANIENT.NS',     'Metals & Mining',                  'Adani Enterprises Ltd.'),
    ('HINDZINC.NS',     'Metals & Mining',                  'Hindustan Zinc Ltd.'),
    ('JINDALSTEL.NS',   'Metals & Mining',                  'Jindal Steel Ltd.'),
    ('JSWSTEEL.NS',     'Metals & Mining',                  'JSW Steel Ltd.'),
    ('NMDC.NS',         'Metals & Mining',                  'NMDC Ltd.'),
    ('SAIL.NS',         'Metals & Mining',                  'Steel Authority of India Ltd.'),
    ('TATASTEEL.NS',    'Metals & Mining',                  'Tata Steel Ltd.'),
    ('VEDL.NS',         'Metals & Mining',                  'Vedanta Ltd.'),

    # Oil Gas & Consumable Fuels
    ('ATGL.NS',         'Oil Gas & Consumable Fuels',       'Adani Total Gas Ltd.'),
    ('BPCL.NS',         'Oil Gas & Consumable Fuels',       'Bharat Petroleum Corporation Ltd.'),
    ('COALINDIA.NS',    'Oil Gas & Consumable Fuels',       'Coal India Ltd.'),
    ('GAIL.NS',         'Oil Gas & Consumable Fuels',       'GAIL (India) Ltd.'),
    ('HINDPETRO.NS',    'Oil Gas & Consumable Fuels',       'Hindustan Petroleum Corporation Ltd.'),
    ('IOC.NS',          'Oil Gas & Consumable Fuels',       'Indian Oil Corporation Ltd.'),
    ('OIL.NS',          'Oil Gas & Consumable Fuels',       'Oil India Ltd.'),
    ('ONGC.NS',         'Oil Gas & Consumable Fuels',       'Oil & Natural Gas Corporation Ltd.'),

    # Power
    ('ADANIENSOL.NS',   'Power',                            'Adani Energy Solutions Ltd.'),
    ('ADANIGREEN.NS',   'Power',                            'Adani Green Energy Ltd.'),
    ('ADANIPOWER.NS',   'Power',                            'Adani Power Ltd.'),
    ('NHPC.NS',         'Power',                            'NHPC Ltd.'),
    ('NLCINDIA.NS',     'Power',                            'NLC India Ltd.'),
    ('NTPC.NS',         'Power',                            'NTPC Ltd.'),
    ('POWERGRID.NS',    'Power',                            'Power Grid Corporation of India Ltd.'),
    ('SJVN.NS',         'Power',                            'SJVN Ltd.'),

    # Realty
    ('ABREL.NS',        'Realty',                           'Aditya Birla Real Estate Ltd.'),
    ('ANANTRAJ.NS',     'Realty',                           'Anant Raj Ltd.'),
    ('BRIGADE.NS',      'Realty',                           'Brigade Enterprises Ltd.'),
    ('DLF.NS',          'Realty',                           'DLF Ltd.'),
    ('GODREJPROP.NS',   'Realty',                           'Godrej Properties Ltd.'),
    ('LODHA.NS',        'Realty',                           'Lodha Developers Ltd.'),
    ('OBEROIRLTY.NS',   'Realty',                           'Oberoi Realty Ltd.'),
    ('PHOENIXLTD.NS',   'Realty',                           'Phoenix Mills Ltd.'),
    ('PRESTIGE.NS',     'Realty',                           'Prestige Estates Projects Ltd.'),
    ('SIGNATURE.NS',    'Realty',                           'Signatureglobal (India) Ltd.'),
    ('SOBHA.NS',        'Realty',                           'Sobha Ltd.'),

    # Services
    ('ADANIPORTS.NS',   'Services',                         'Adani Ports and Special Economic Zone Ltd.'),
    ('CONCOR.NS',       'Services',                         'Container Corporation of India Ltd.'),

    # Telecommunication
    ('BHARTIARTL.NS',   'Telecommunication',                'Bharti Airtel Ltd.'),
    ('BHARTIHEXA.NS',   'Telecommunication',                'Bharti Hexacom Ltd.'),
    ('HFCL.NS',         'Telecommunication',                'HFCL Ltd.'),
    ('IDEA.NS',         'Telecommunication',                'Vodafone Idea Ltd.'),
    ('INDUSTOWER.NS',   'Telecommunication',                'Indus Towers Ltd.'),
    ('ITI.NS',          'Telecommunication',                'ITI Ltd.'),
    ('RAILTEL.NS',      'Telecommunication',                'Railtel Corporation Of India Ltd.'),
    ('TATACOMM.NS',     'Telecommunication',                'Tata Communications Ltd.'),
    ('TEJASNET.NS',     'Telecommunication',                'Tejas Networks Ltd.'),
    ('TTML.NS',         'Telecommunication',                'Tata Teleservices (Maharashtra) Ltd.'),

    # Textiles
    ('PAGEIND.NS',      'Textiles',                         'Page Industries Ltd.'),
]

# Quick lookup maps
SYMBOL_TO_SECTOR = {sym: sector for sym, sector, _ in WATCHLIST}
SYMBOL_TO_NAME   = {sym: name   for sym, _, name  in WATCHLIST}
ALL_SYMBOLS      = [sym for sym, _, _ in WATCHLIST]
ALL_SECTORS      = sorted(set(sector for _, sector, _ in WATCHLIST))
