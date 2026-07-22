"""
Trading charges calculator — Upstox Equity Delivery (NSE).

E6 (docs/26 Portfolio Truth Audit): verified 2026-07-22 against Upstox's live
GET /v2/charges/brokerage API on a real account trade (CEMPRO.NS, 18 shares
@ Rs.1637.90) -- two assumptions below were wrong and are now corrected:
  - Brokerage was assumed Rs.0 ("free delivery" marketing claim); this
    account's real plan charges min(2.5% of trade value, Rs.30) per leg,
    confirmed across 3 trade sizes (Rs.1,000/Rs.10,000/Rs.50,000). At this
    system's typical trade sizes (tens of thousands+) the Rs.30 cap binds
    almost every time.
  - STT was assumed sell-side only; the live API confirms 0.1% STT is
    charged on the BUY leg too (Rs.29.48 on a Rs.29,482.20 buy -- exact
    match).
Previously the model understated real per-trade cost by roughly half (buy
leg: ~Rs.5.49 modeled vs Rs.70.41 real for the CEMPRO trade) -- every
reported backtest/live net P&L number to date has been mildly optimistic.

Upstox equity delivery charges (as of 2026-07-22, this account's plan):
  - Brokerage:       min(2.5% of trade value, Rs.30) per leg
  - STT:             0.1% on BOTH buy and sell
  - NSE Exchange:    0.00297% both sides
  - SEBI:            0.0001% both sides
  - GST:             18% on (brokerage + exchange + SEBI charges)
  - Stamp Duty:      0.015% on buy side only (capped at Rs.1500/instrument)
  - DP Charges:      Rs.18.5 flat per scrip per sell day (CDSL debit)

All rates are per-transaction (per order), not per share.
"""

from dataclasses import dataclass


# ── Upstox NSE Delivery rates ─────────────────────────────────────────────────
BROKERAGE_RATE       = 0.025         # 2.5% of trade value per leg, capped below
BROKERAGE_CAP        = 30.0          # Rs.30 max per leg -- verified live 2026-07-22
STT_RATE             = 0.001         # 0.1% on BOTH buy and sell -- verified live 2026-07-22
NSE_EXCHANGE_RATE    = 0.0000297     # 0.00297% both sides
SEBI_RATE            = 0.000001      # 0.0001% both sides
GST_RATE             = 0.18          # 18% on (brokerage + exchange + SEBI)
STAMP_DUTY_RATE      = 0.00015       # 0.015% on buy value
STAMP_DUTY_MAX       = 1500.0        # Rs.1500 cap per instrument
DP_CHARGE_PER_SELL   = 18.5          # Rs.18.5 flat CDSL DP charge per sell


@dataclass
class ChargeBreakdown:
    brokerage:    float
    stt:          float
    exchange:     float
    sebi:         float
    gst:          float
    stamp_duty:   float
    dp_charges:   float
    total:        float

    def as_dict(self) -> dict:
        return {
            "brokerage":  round(self.brokerage, 2),
            "stt":        round(self.stt, 2),
            "exchange":   round(self.exchange, 2),
            "sebi":       round(self.sebi, 2),
            "gst":        round(self.gst, 2),
            "stamp_duty": round(self.stamp_duty, 2),
            "dp_charges": round(self.dp_charges, 2),
            "total":      round(self.total, 2),
        }


def buy_charges(trade_value: float) -> ChargeBreakdown:
    """Calculate all charges for a buy order of given value (₹)."""
    brokerage    = min(BROKERAGE_RATE * trade_value, BROKERAGE_CAP)
    stt          = STT_RATE * trade_value                      # verified live on buy leg too
    exchange     = NSE_EXCHANGE_RATE * trade_value
    sebi         = SEBI_RATE * trade_value
    gst          = GST_RATE * (brokerage + exchange + sebi)
    stamp_duty   = min(STAMP_DUTY_RATE * trade_value, STAMP_DUTY_MAX)
    total        = brokerage + stt + exchange + sebi + gst + stamp_duty
    return ChargeBreakdown(
        brokerage=brokerage, stt=stt, exchange=exchange,
        sebi=sebi, gst=gst, stamp_duty=stamp_duty, dp_charges=0.0, total=total
    )


def sell_charges(trade_value: float) -> ChargeBreakdown:
    """Calculate all charges for a sell order of given value (₹)."""
    brokerage    = min(BROKERAGE_RATE * trade_value, BROKERAGE_CAP)
    stt          = STT_RATE * trade_value
    exchange     = NSE_EXCHANGE_RATE * trade_value
    sebi         = SEBI_RATE * trade_value
    gst          = GST_RATE * (brokerage + exchange + sebi)
    dp           = DP_CHARGE_PER_SELL
    total        = brokerage + stt + exchange + sebi + gst + dp
    return ChargeBreakdown(
        brokerage=brokerage, stt=stt, exchange=exchange,
        sebi=sebi, gst=gst, stamp_duty=0.0, dp_charges=dp, total=total
    )


def round_trip_charges(buy_value: float, sell_value: float) -> dict:
    """Total charges for a complete buy + sell round trip."""
    buy  = buy_charges(buy_value)
    sell = sell_charges(sell_value)
    return {
        "buy_charges":  buy.as_dict(),
        "sell_charges": sell.as_dict(),
        "total_charges": round(buy.total + sell.total, 2),
        "charges_pct":   round((buy.total + sell.total) / buy_value * 100, 4) if buy_value > 0 else 0,
    }


def net_pnl(entry_price: float, exit_price: float, shares: int) -> dict:
    """Calculate net P&L after all charges for a round trip trade."""
    buy_value    = entry_price * shares
    sell_value   = exit_price * shares
    gross_pnl    = sell_value - buy_value
    buy_c        = buy_charges(buy_value)
    sell_c       = sell_charges(sell_value)
    total_charges = buy_c.total + sell_c.total
    net           = gross_pnl - total_charges
    return {
        "buy_value":      round(buy_value, 2),
        "sell_value":     round(sell_value, 2),
        "gross_pnl":      round(gross_pnl, 2),
        "total_charges":  round(total_charges, 2),
        "net_pnl":        round(net, 2),
        "gross_pct":      round(gross_pnl / buy_value * 100, 2) if buy_value > 0 else 0,
        "net_pct":        round(net / buy_value * 100, 2) if buy_value > 0 else 0,
        "buy_charges":    buy_c.as_dict(),
        "sell_charges":   sell_c.as_dict(),
    }


# ── Example / quick check ─────────────────────────────────────────────────────
if __name__ == "__main__":
    # Trade: buy 10 shares @ ₹1000, sell @ ₹1130 (13% gain)
    result = net_pnl(1000, 1130, 10)
    print("=== Round Trip on ₹10,000 position (13% gain) ===")
    for k, v in result.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for k2, v2 in v.items():
                print(f"    {k2}: ₹{v2}")
        else:
            print(f"  {k}: {v}")
