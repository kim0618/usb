# Strategy E — E-D1 Signal Contract V1

## Status and authority

- Stage: `E-D1 SIGNAL IDENTITY`
- E-D0 rules canonical SHA256:
  `f1534f07688c801f2979491e447eefbb3e62b8045afb9c4ab302b90e4593d4b2`
- Signal version: `STRATEGY_E_H5_SIGNAL_V1`
- Scope: 09:25 PIT eligible feature frame → frozen H5 candidates and provenance

This contract adds no Alpha, execution, risk, entry, exit, sizing, cost, or PnL rule. The E-D0
JSON remains unchanged.

## Canonical signal path

```text
strategy_e1_premarket.evaluate.mask("H5", features)  [authoritative evaluator]
    ├── strategy_e1_h5_confirm.run.h5_mask
    ├── strategy_e1_forward.seal.build
    └── strategy_e.signal.evaluate_h5_signal
```

Trading does not transcribe the H5 expression. It calls the Research evaluator directly. The
frozen H5 inputs remain `premarket_gap`, `premarket_rvol`,
`position_in_premarket_range`, and `return_0900_0925`; Research alone owns their thresholds and
missing-value mask semantics.

## Input boundary

`SignalFrame` contains an already eligible symbol row set, the Forward seal feature columns,
session, source digest, E-D0 rules digest, decision time, and latest feature-bar start. It must
state decision time 09:25 ET and may contain no information from a bar starting after 09:24 ET.

The additional Forward seal columns are provenance/control inputs, not Alpha inputs. Any unrelated
execution or risk columns are ignored and cannot alter H5 or its decision digest. Feature arrays
are neither filled, rounded, clipped, ranked, renamed, nor defaulted.

## Output boundary

`SignalResult` contains only:

- strategy and signal identity;
- session, cutoff, rules digest, and source digest;
- eligible and candidate counts;
- the H5 mask and candidate identifiers;
- the existing Forward seal decision digest.

Candidate identifiers are lexicographically canonicalized for repeatable identity only. This is
not the E-D0 same-session candidate priority policy, which remains TBD and must be frozen before
E-D2 backtesting. No entry, exit, quantity, risk decision, or return appears in this output.

## Determinism and digest

E-D1 reuses `strategy_e1_forward.seal` serialization and digest without changing its bytes:

```text
SEAL_FORMAT
+ rows sorted by symbol
+ SEALED_FEATURES at full float precision
+ Research H5 boolean
→ sha256 decision_digest
```

The rules and source digests remain explicit provenance fields. Reordering equivalent symbol rows
does not change the decision digest or canonical candidate identifiers. Repeating the same call
returns an equal immutable result.

## Fail-closed behavior

The signal boundary rejects:

- missing sealed/H5 feature columns;
- non-numeric, non-vector, or wrong-length feature data;
- duplicate or empty symbol identifiers;
- decision time other than 09:25 ET;
- a latest feature bar after 09:24 ET;
- a missing source digest;
- an input rules digest other than the frozen E-D0 digest;
- a changed or unsupported E-D0 rules file.

Numeric `NaN` retains Research semantics: any H5 predicate reading it is false. `None`, object data,
and a missing column are explicit contract errors rather than implicit fills.

## Separation after Alpha

```text
Frozen H5 Alpha Candidate
→ future Execution Eligibility
→ future Risk Eligibility
→ future Tradable Candidate
```

Later stages may reject an H5 candidate but may not merge those rejection conditions into H5 or
rewrite the Alpha mask.
