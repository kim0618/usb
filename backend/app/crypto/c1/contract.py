"""The D5.2 C1 contract, restated for operations.

Nothing here is a new decision. Every number is copied from the frozen research artefacts and
`backend/tests/crypto/test_c1_contract_provenance.py` fails if any of them stops matching its
source:

  contract   docs/crypto/CRYPTO_D5_2_DERIVATIVES_FLOW_CONTRACT_V1.md  (sha256 below, section 4)
  results    docs/crypto/CRYPTO_D5_2_DERIVATIVES_FLOW_RESULTS_V1.md   (sections 12, 13, 18)
  cells      data/runtime/crypto/d5_2/cells_v1.json                   (taker, micro, vol cutoffs)
  fees       data/runtime/crypto/reference/fee_source_verification_v1.json
  micro      data/runtime/crypto/d5/realtime_cost_measurement_v1.json

C1 is `dislocation_event`, one binary feature, and the contract's section 4 defines it as three
conditions on the Bybit BTCUSDT 1m decision grid:

  1. bucket(S1) == B1, S1 = ln(bybit_perp_last_5m / binance_spot_5m), B1 = below the 10th
     percentile of the previous 30 whole UTC days of the same 1m series,
  2. ln(OI_bybit[t] / OI_bybit[t-60]) < 0,
  3. volatility regime == HIGH, i.e. the trailing 24 h standard deviation of 1m log returns is
     above a cutoff estimated once on the F1 training window and frozen.

DIRECTION is LONG and only LONG. The research grid scored both sides of every cell; C1's SHORT
side is the exact negative of its LONG side and was REJECT at every horizon (results section 18,
and `C1-EVENT-240m-SHORT` net -41.3 bp in cells_v1.json). There is no SHORT definition in D5.2 to
implement, so this module does not invent one.

What the research verdict was, and what it was not: C1-EVENT-240m-LONG is **WEAK (W1)**, not
SURVIVE. It is the one cell of 720 whose net return cleared the VIP_0 cost with a positive CI
lower bound, and it failed on sample size (N_eff 77 < 200, 4 decidable folds < 6) and on a
volatility robustness test that the feature's own HIGH-volatility condition makes structurally
unpassable. D5.2's gate is CASE C: no SURVIVE, no D6 promotion. That is precisely why what this
package drives is a shadow ledger and a chart marker and not an order.
"""
from __future__ import annotations

CONTRACT_DOC = "docs/crypto/CRYPTO_D5_2_DERIVATIVES_FLOW_CONTRACT_V1.md"
CONTRACT_SHA256 = "c49cd0e577a8b81321c943a8eb6fe20b59635592816191efc30f67454bc8cf81"
RESULTS_DOC = "docs/crypto/CRYPTO_D5_2_DERIVATIVES_FLOW_RESULTS_V1.md"
SCHEMA_VERSION = "C1_SIGNAL_V1"

STRATEGY = "C1"
FEATURE_ID = "C1"
FEATURE_NAME = "dislocation_event"
# Contract section 4 scores LONG and SHORT; only LONG was ever positive. See the module docstring.
DIRECTION = "LONG"
DIRECTION_CONTRACT = "LONG_ONLY"
RESEARCH_VERDICT = "WEAK_W1_NOT_SURVIVE"
RESEARCH_CELL = "C1-EVENT-240m-LONG"

MINUTE_MS = 60_000
DAY_MS = 86_400_000
FIVE_MIN_MS = 5 * MINUTE_MS

# --- condition 1: the spot basis bucket (contract sections 4 and 5) -------------------------
NORM_DAYS = 30                       # the bucket window is the previous 30 whole UTC days
DAY_BARS = 1440
BUCKET_QUANTILES = (0.10, 0.30, 0.70, 0.90)
B1_QUANTILE = 0.10                   # B1 is strictly below this quantile
BUCKET_MIN_VALID_SHARE = 0.5         # a window less than half populated yields no bucket
# A 5m value may stand in for one missing 5m bar and never for two (contract section 2).
CARRY_FORWARD_MAX_BARS = 1

# --- condition 2: open interest (contract section 4, PIT rule in section 2) -----------------
OI_CHANGE_LAG_MIN = 60               # ln(OI[t] / OI[t-60])
OI_KNOWN_DELAY_MS = FIVE_MIN_MS      # a 5m OI record stamped T counts as known at T + 5 min
# An operational guard, not part of the frozen contract, and the one place this package adds a
# rule the study does not have. The study joins open interest as-of, which carries the last known
# value forward for as long as necessary - correct over a complete historical file, and a hazard
# against a live feed, where a venue that stops answering would leave a decision resting on an
# hours-old number while reporting nothing wrong.
#
# The bound is chosen so it cannot alter a historical decision. In the study's sample window
# (2021-03-04 onwards) the 5m series has 584,352 records and no gap at all, so the oldest stamp a
# bar can be reading is 10 minutes behind its own close: one interval, plus the five-minute
# knowledge delay. 15 minutes leaves a margin above that and still catches an outage within three
# intervals. (The single 25-minute gap in the raw series is at 2021-02-08, before the sample
# window, and no sample ever saw it.)
OI_MAX_STALENESS_MS = 15 * MINUTE_MS
OI_STALENESS_GUARD = "OPERATIONAL_ADDITION_NOT_IN_D5_2_CONTRACT"
OI_OBSERVED_MAX_STALENESS_MS = 10 * MINUTE_MS

# --- condition 3: the volatility regime (contract section 8 = D5.1 section 8) ---------------
VOL_WINDOW_BARS = 1440               # trailing 24 h of 1m log returns, ddof 0
VOL_REGIME_QUANTILES = (0.30, 0.70)
# Estimated once on the F1 training window (2021-03-04 .. 2022-01-01) and frozen there. Both
# cutoffs are carried so the provenance test can compare the pair it finds in cells_v1.json.
VOL_CUTOFF_LO_F1_TRAIN = 0.0007592730942805516
VOL_CUTOFF_HI_F1_TRAIN = 0.0010924950359531157
VOL_REGIME_HIGH_CUTOFF = VOL_CUTOFF_HI_F1_TRAIN
VOL_CUTOFF_SOURCE = "data/runtime/crypto/d5_2/cells_v1.json#vol_cutoffs_f1_train"

# --- the trade (contract sections 2, 3 and 6) ----------------------------------------------
# Decision at the close of 1m bar t, entry at open[t+1], exit at open[t+1+h].
OFFICIAL_HORIZON_MIN = 240
ENTRY_OFFSET_BARS = 1
# 4 h is the official result. The rest are observations kept for a later holding-period study and
# are never folded into C1's official performance. 360 (6 h) is not a D5.2 horizon at all: the
# contract's set is 15/30/60/120/240 primary and 480 reference-only.
OBSERVATION_HORIZONS_MIN = (30, 60, 120, 240, 360, 480)
RESEARCH_HORIZONS_MIN = (15, 30, 60, 120, 240)
RESEARCH_REFERENCE_HORIZON_MIN = 480
HORIZONS_OUTSIDE_RESEARCH_MIN = (360,)

# Cost, contract section 6, scenario VIP0_BASE (the study's main scenario). The round trip is
# taker * (1 + X / E), not a flat constant: the exit is charged on the exit notional. At X ~ E
# that is 2 * 5.5 bp = 11.0 bp, which is what "11 bp" in the results document means.
TAKER_RATE = 0.00055
MICRO_BASE_FRAC = 1.1841571614073798e-06      # p50 spread + p50 buy + p50 sell impact at 0.1 BTC
MICRO_STRESS_FRAC = 0.0002537159730588128     # the same three at their observed maxima
COST_SCENARIO = "VIP0_BASE"
FEE_SOURCE = "data/runtime/crypto/reference/fee_source_verification_v1.json#VIP_0"
MICRO_SOURCE = "data/runtime/crypto/d5/realtime_cost_measurement_v1.json"

# --- venues (contract sections 0 and 2) ----------------------------------------------------
TARGET_VENUE = "BYBIT"
TARGET_SYMBOL = "BTCUSDT"            # linear perpetual; the traded instrument and the target
SPOT_VENUE = "BINANCE"
SPOT_SYMBOL = "BTCUSDT"              # Binance spot, the denominator of S1

# --- operational policy, which the research did not need and therefore did not fix ---------
# The study counted every 1m bar inside an event as one sample (18,498 bars over 179 OOS days)
# and corrected for the overlap with N_eff and a block bootstrap. An operator cannot act on a
# bar; they act on an event. So one lifecycle covers one contiguous run of C1-true bars, and a
# new signal needs the condition to lapse first. Shadow trades may overlap, exactly as the
# research samples did; nothing here waits for a previous shadow to finish.
OVERLAP_POLICY = "CONDITION_REARM_OVERLAPPING_SHADOWS"
REARM_REQUIRES_FALSE_BAR = True
# A bar whose inputs are incomplete is NOT_ELIGIBLE, never false, and never a stale fill-in.
MISSING_DATA_POLICY = "NOT_ELIGIBLE_NEVER_FALSE_NEVER_FORWARD_FILLED"

# This package observes. It must never be given an order path.
PLACES_ORDERS = False
MUTATES_ACCOUNT = False

STATES = ("OFF", "NOT_ELIGIBLE", "TRIGGERED", "ACTIVE", "COMPLETED", "EXPIRED")

# Published figures for the one cell this signal tracks, from results sections 12 and 13. Carried
# so the UI and the reports can quote the research instead of re-deriving it.
RESEARCH_OOS = {
    "cell": RESEARCH_CELL,
    "verdict": "WEAK",
    "verdict_code": "W1",
    "gross_bp": 30.19125021669215,
    "net_vip0_base_bp": 19.278043969946737,
    "net_ci95_bp": (4.2795386373652584, 35.47948597732855),
    "net_vip0_stress_bp": 16.752725810972673,
    "n_bars": 18498,
    "n_eff": 77.075,
    "days": 179,
    "gate": "CASE_C_NO_SURVIVE",
}


# =============================================================================================
# C1x: the premium-normalization FORWARD DIAGNOSTIC (E2), not an exit
# =============================================================================================
#
# Source: docs/crypto/c1_exit/C1_EXIT_E2_DYNAMIC_CONTRACT_V1.md section 3, implemented in
# app/crypto/research/c1_exit/e2.py `trig_c1x` / `_confirmed`. The rule is transcribed, never
# re-derived, and no threshold here was chosen by this package.
#
#     after a C1 LONG entry, on 5m evaluation bars only, S1's bucket is B3 or higher
#     (>= that day's point-in-time 30th percentile cutoff) on TWO CONSECUTIVE evaluations.
#
# What it means: the extreme discount that C1 entered on has returned to normal. That is all it
# means. It is **not** an exit, a sell, a close or a stop, and nothing in this codebase may wire
# it to one. The operator decides whether to act.
#
# Why it is a diagnostic and not a rule to follow: E2 scored C1x INCONCLUSIVE. It failed gate G2
# on winner preservation - it keeps only **48.8%** of E0's top-5% winners, i.e. it cuts more than
# half of the large winners short. Its higher mean comes from truncating losses, not from timing.
# E2's own closing note proposed recording it as a shadow diagnostic beside E0, which is exactly
# what this is.
C1X_CONTRACT_DOC = "docs/crypto/c1_exit/C1_EXIT_E2_DYNAMIC_CONTRACT_V1.md"
C1X_CONTRACT_SHA256 = "61d194fdafcd05ef47680a6a31755578345b176321d030c22a9bb05a6f51348a"
C1X_RESULTS_DOC = "docs/crypto/c1_exit/C1_EXIT_E2_RESULTS_V1.md"
C1X_SCHEMA_VERSION = "C1X_DIAGNOSTIC_V1"

C1X_ID = "C1x"
C1X_MEANING = "PREMIUM_NORMALIZATION_DIAGNOSTIC"
C1X_IS_EXIT = False
# The bucket index the premium must reach: 2 is B3, the day's 30th percentile cutoff or above.
C1X_MIN_BUCKET = 2
C1X_CONFIRMATIONS_REQUIRED = 2
# Evaluated only on bars whose close lands on a 5m boundary, which is the cadence the premium
# feature itself updates on. The entry engine stays on the contract's 1m decision grid; E2 put
# this candidate on 5m and the two cadences are deliberately not reconciled.
C1X_EVAL_CADENCE_MIN = 5
# E2 section 2: a false condition or a NaN input resets the run to zero.
C1X_RESET_ON_NAN = True
# E2 section 2 max hold. Past this the candidate is censored; the diagnostic simply never fires.
C1X_MAX_HOLD_MIN = 480
C1X_STATES = ("NOT_TRIGGERED", "CONFIRM_1", "CONFIRM_2", "TRIGGERED", "EXPIRED_MAX_HOLD")

# The benchmark C1x is paired against: the fixed 4 h shadow this package already keeps.
E0_BENCHMARK_ID = "E0"
E0_HORIZON_MIN = OFFICIAL_HORIZON_MIN

# E2 counterfactual figures over the same 1,603 OOS events, for the UI to quote rather than
# re-derive. BASE cost scenario, which is the same ~11 bp round trip this package applies.
C1X_RESEARCH = {
    "candidate": "C1x",
    "status": "INCONCLUSIVE",
    "failed_gate": "G2_WINNER_PRESERVATION",
    "winner_preservation": 0.488,
    "trigger_rate": 0.83,
    "censored_rate": 0.17,
    "holding_minutes_mean": 152,
    "holding_minutes_median": 60,
    "net_mean_bp": 15.35,
    "net_median_bp": -1.70,
    "delta_vs_e0_mean_bp": 5.08,
    "delta_ci95_bp": (-16.0, 27.6),
    "p_boot_delta_positive": 0.66,
    "p5_bp": -201.3,
    "p10_bp": -123.2,
    "profit_factor": 1.35,
    "e0_net_mean_bp": 10.27,
    "e0_p5_bp": -329.7,
    "events": 1603,
}
