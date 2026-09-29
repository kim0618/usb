"""D5.5 event-triggered direction confirmation.

D5.4 asked whether a large event predicts direction and answered no: it entered at the shock and
the symmetric bracket came out a coin flip, with one candidate stopping 104 times and hitting
target 104 times. The displacement was twelve to fifteen times the round-trip cost, so the
failure was not about movement or fees. There was simply no direction information at the event.

So here the event stops being a predictor and becomes an activator. Nothing is traded until the
price itself has shown a direction inside a fixed window, and when it never does, the answer is
NO_TRADE rather than a forced guess.

Contract: docs/crypto/CRYPTO_D5_5_DIRECTION_CONFIRMATION_CONTRACT_V1.md
Event detector reused unchanged from app.crypto.research.d5_4.events.
"""
