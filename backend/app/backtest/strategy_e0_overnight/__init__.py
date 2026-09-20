"""Strategy E0 - overnight / closing-strength pre-validation.

An independent research experiment, not a strategy. It reads the frozen common Historical
Store through Strategy D's loader (imported, never modified) and asks one question: does
information observable up to the regular-session close raise the expected value of the next
session's open above the unconditional base rate? Nothing here places, sizes or simulates a
trade, and no module outside this package is touched.
"""
