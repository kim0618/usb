"""A/E common premarket producer (one-session live shadow cutover, 2026-10).

One Kiwoom acquisition process owns the app key's two chart lanes for the whole premarket and
hands each strategy an immutable, digest-checked view of the same raw cache:

    04:00 - 09:15      shared rolling over the A union + E-only symbols (minute lane)
    09:15 - 09:25      A sweep on both lanes; the tick lane takes E's tick shard first and then
                       steals A-only symbols, so no alphabetical tail is starved
    ~09:25             snapshot_0915 sealed (bars < 09:15)  -> A consumer (own process, no Kiwoom)
    09:25 - 09:29:45   E finalization, started by the clock alone
    ~09:29:45          snapshot_0924 sealed (bars <= 09:24) -> E shadow consumer (read-only)

Every chart request passes :class:`deadline.DeadlineRateLimiter`, which re-reads the clock after
its pacing wait, so no request leaves at or after a phase deadline. Consumers never wait for each
other and never call Kiwoom. Nothing here changes a strategy rule: A's cut, union, snapshot and
scan, and E's cutoff, universe, features and decision are the existing code, read from the file.

``a_consumer`` and ``snapshot`` import only modules whose content is identical in the production
A/E operations tree, so they can be added to that tree without replacing any of its files.
"""
