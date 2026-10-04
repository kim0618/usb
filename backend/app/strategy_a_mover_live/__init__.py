"""A-MOVER-LIVE-V1: Strategy A's mover pipeline on the shared Kiwoom premarket collector.

The research contract ``a-mover-scanner-v1.2`` stays the research authority and is never
restamped as a live run. This package is the separate live contract: the same ranking
architecture read from the same declarations, fed by Kiwoom premarket data instead of the
Massive historical mirror, and labelled so a persisted row says which provider produced it.

Nothing here is on by default. ``config.enabled`` reads one environment flag and every entry
point refuses with a named reason when it is off, so an unconfigured runtime runs no scan,
makes no GPT call and injects no candidate.
"""
