# Execution audit

- Contract hash frozen before any R0 fitting or outcome metric. One model configuration run.
- 36 fold/horizon prediction files are the first valid frozen-model run. No outcome-driven
  feature, parameter, target, percentile or threshold changes; no refitting after review.
- Funding accounting was checked against existing C1 E1 trades before fitting. D6-C uses
  settlement-stamped `mark_close` for realized funding; retained in labels/accounting only.
  Features use settled funding available at decision time. R0 bp denominator is entry reference
  notional; E1 uses actual entry fill notional. Normalization reconciled in regression test.
- Reporting-only corrections after first results: sparse weekly clusters no longer produce
  misleading degenerate CIs; fewer than 20 clusters or 10 effective within-fold resampling
  degrees yields unavailable CI. PF with zero observed losses is unavailable, not a large
  number obtained by dividing by epsilon. Saved predictions were summarized again, not refit.
  Verdict remained WEAK_DIRECTIONAL_MODEL.
- Calendar-week reporting correction: use Monday-start UTC weeks instead of epoch-anchored
  Thursday-start seven-day blocks. Predictions, model fits and evaluation rows are unchanged.
- Read-only additional diagnostics examine LONG-minus-SHORT ranking conditional on either
  favorable label. The score is never presented as a calibrated probability. Six descriptive
  signed-score bins are not action rules, model variants or threshold selection.
- Independent LONG/SHORT classifiers sometimes sum above 1 although the labels are mutually
  exclusive. No post-hoc normalization repairs this. This is a reported coherence failure and
  a limitation of the R0 family; a future model would require a separately preregistered joint
  distribution on new data.
- C1 is reproduced from its original spot-premium/OI/volatility definition; 18,498 historical
  OOS state bars and original volatility cutoffs match. It is not a model feature or fit filter.
- Local PIT contracts use candle-close availability, OI +5m delay and settled funding.
  Historical archives do not prove original publication latency or absence of vendor revisions.
  Same-boundary next-open evaluation has no measured 440ms feed-latency/orderbook replay;
  synthetic spread and stress costs do not establish live execution feasibility.
- Statistical intervals use weekly clusters stratified by fold, not an independence claim for
  all 5m rows. Fixed UTC 4h-phase rows supply a nonoverlap sensitivity check. Long-run regime
  dependence beyond a week remains a limitation. Four-horizon directional intervals use 98.75%
  nominal coverage; this is not correction for prior research across the reused archive.
