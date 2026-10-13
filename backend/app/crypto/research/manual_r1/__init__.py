"""MANUAL-R1: auditing the human's discretionary paper trading, then reading its behaviour.

Six rule-based AUTO studies found nothing (D5, D5.1, D5.2, D6 FDN-V1, D5.4, D5.5). The operator
reports feeling consistently profitable trading the same market by hand. This package asks
whether the ledger agrees, and only then whether the entries carry repeatable information.

Order matters and is deliberate: verify the money first, describe the behaviour second, and
write hypotheses last. Starting from "the human is good at this" would decide the answer before
the data speaks.

Everything here is read-only research on a snapshot. No order, no reset, no service touched.
"""
