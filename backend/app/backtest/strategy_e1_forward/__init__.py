"""Forward holdout infrastructure for the frozen E1-H5 rule.

This package collects no alpha and proposes no hypothesis. It exists so that H5 - unchanged - can
be tested on market sessions nobody has looked at yet. Three properties are enforced structurally
rather than by convention:

* **a forward observation starts at 2026-09-17.** Anything dated on or before the last historical
  session is refused by the registry, so a hidden slice of old data cannot become a "holdout";
* **the decision is sealed before the outcome exists.** Features and the H5 mask are written and
  digested at the 09:25 cutoff; the label step refuses to run without a seal and refuses to
  rewrite one;
* **the store is append-only.** Nothing here writes into a frozen snapshot, and a session already
  recorded cannot be silently replaced.
"""
