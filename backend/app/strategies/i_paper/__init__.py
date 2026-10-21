"""US-B Strategy I (MOM12-2 V2.1) order-less paper book.

The trading rules are the frozen V1 / V2 / V2.1 research modules, executed unchanged (sha-checked) on a
point-in-time view of the production stores: only files dated on or before the as-of session are
visible. The V2 ticker-continuity table (TRANS / AMBIG) is not used: it is built from reference
snapshots dated after the transition (future information), so it is emptied and a held name whose
price stops is reported as ``CONTINUITY_UNRESOLVED`` (see ``engine``). No orders are sent anywhere.
The industry overlay is observation-only and never touches holdings, cash or trades.
"""
