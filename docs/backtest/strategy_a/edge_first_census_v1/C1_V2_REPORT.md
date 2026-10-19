# C1 V2 EXACT-A CENSUS RESULT

prereg `PREREGISTRATION_C1_V2.md` sha256 `4b88cd17c1b30f61b83756068923ce02a061fe16f2dc3d716ee2a54e500c4c42`
결과: `c1_results.json`, 거래: `c1_trades.csv`, 원자료: `c1_raw.jsonl`(8,389 후보)

FINAL = DATA_INSUFFICIENT (F1 INSUFFICIENT 16 trades, F3 INSUFFICIENT 11 trades)

참고(판정 아님): F1 Gross Total R -1.70 / Net -3.28 / Net PF 0.46. F3 Gross +0.49 / Fee-only -0.02 / Net -0.80 / Net PF 0.77.
병목: E gap 게이트(GAP_TOO_LOW F1 71%, F3 65%), 장전 바 없음(NO_PREMARKET_BARS F1 13%, F3 28%, 원본 표본 25/25 실제 장전 체결 0), 신호의 절반이 PRICE_ABOVE_LIMIT(F1 17/33, F3 12/23).
유효성: PIT 0, invariant 0, replay error 0, net identity mismatch 0, cost/turnover 25.0bp.
