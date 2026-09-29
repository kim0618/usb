"""BTC-VOL-A: was VOL-P0's implied volatility proxy actually conservative?

VOL-P0 concluded that a long straddle on P1's large-move signal loses money, but it never
measured an option price. It assumed implied volatility equalled trailing realised volatility,
which is the assumption most generous to a buyer, and found the structure still negative.

This checks that assumption against real Deribit quotes. The free Tardis sample covers the first
day of each month, which leaves nine high-confidence episodes across six dates: enough to see
whether implied volatility sits clearly above or below the break-even, not enough to settle
anything statistically.

No strategy is built. No exit is optimised. No order is placed.
"""
