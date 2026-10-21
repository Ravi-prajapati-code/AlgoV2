from scripts.check_momentum_atr_broker import compare


def test_match_is_clean():
    assert compare(1000.0, {"A.NS": 5}, 1000.0, {"A.NS": 5}) == ([], [])


def test_sold_outside_bot_is_problem():
    problems, _ = compare(1000.0, {}, 1000.0, {"A.NS": 5})
    assert len(problems) == 1 and "A.NS" in problems[0]


def test_withdrawal_is_problem_deposit_is_note():
    problems, notes = compare(500.0, {}, 1000.0, {})
    assert len(problems) == 1 and "withdrawal" in problems[0]
    problems, notes = compare(3967.47, {}, 1340.15, {})
    assert problems == [] and "deposit?" in notes[0] and "2,627.32" in notes[0]


def test_non_bot_holdings_and_paise_drift_ignored():
    assert compare(1000.004, {"LEGACY.NS": 3}, 1000.0, {}) == ([], [])
