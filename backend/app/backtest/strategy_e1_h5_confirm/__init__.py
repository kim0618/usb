"""Independent confirmation of Strategy E1's H5, with H5 itself untouched.

This package adds no feature and no rule. It reproduces H5 by importing the E1 code unchanged,
applies it to a block of symbols the E1 run never saw, and subjects the result to the two tests
the E1 report named as its own weaknesses: price and liquidity matched controls, and a round-trip
cost stress. Nothing here can change H5; a different rule would be a different study.
"""
