# H-V2-D4-E7R - Decision-Leakage Detector Semantic Repair

```
H-V2-D4-E7R
= READY FOR D5 CONTRACT DESIGN

live Opus calls      0
live cost            $0.00
stored outputs replayed          15 final outputs, 19 analysis records, 5 graded runs
true decision leakage            0
false positives removed          1
newly exposed violations         0
D4-BR-C historical verdict       FAIL, unchanged
D5                               READY FOR CONTRACT DESIGN
```

E7 prohibits producing an investment action or verdict. It was implemented as a list of words, and a
word is not an action. This step replaces the list with a rule about the shape of a decision, makes
one classifier the authority for both the generating validator and the grading audit, and replays the
new semantics over every D4 final output this project has ever stored.

Not an alpha result, not a backtest, not a regrade of any live run, and not a statement about any
issuer.

---

## A. Why E7R Exists

D4-BR-C answered its convergence question affirmatively - 4 of 4 final valid, two honest abstentions,
zero fabricated consensus - and failed on E7, with one finding, in COLL:

```
path    root.market_expectation_evidence.pre_event_positioning_reading[1].text
match   \bsell\b
text    The flat pre-event return suggests the guidance cut was not anticipated in the price
        beforehand: there was neither a run-up nor a sell-off before the event.
```

The sentence describes observed price behaviour, and describes its absence. It recommends nothing.
`DECISION_VOCABULARY` compiled each term as `\b<term>\b`, and in "sell-off" the hyphen is a word
boundary.

The same run had already paid for the same defect once, in a different component. COLL's initial
response failed generation-time validation on this sentence in `limitations`:

```
Nothing here is a valuation, a price target or a decision.
```

`_UNAMBIGUOUS_TERMS` matched `\bprice target\b` with no context window, so a disclaimer that denies
producing a price target was a violation of the rule against producing one. That cost COLL one of its
two repair rounds.

One defect, two instances: a lexical hit read as an assertion. And the two components disagreed with
each other - the validator passed the sell-off sentence that the audit then failed, and rejected the
disclaimer the audit would have passed. Two answers to one question is one answer too many, which is
why §6 of this step's brief asked for a single authority rather than a patch to each.

---

## B. Historical BR-C Result

Preserved exactly. `replay_d4_e7r_decision_leakage.HISTORICAL` carries these as literals and
`test_the_coll_bytes_now_pass_e7_through_the_real_audit_path` asserts the stored artifact on disk
still reports them.

```
H-V2-D4-BR-C          FAIL
E1                    4/4 = 100.0%     PASS
E2-E6, E8             PASS
E7                    1                FAIL
SF1                   0 of 18          PASS
C1                    NOT_EVALUATED
D5                    NOT READY
run                   D4_BR_C-20261001T005758Z, $11.2124832 of $30.40
```

`H_V2_D4_BR_C_DISJOINT_CONVERGENCE_CONFIRMATION_RESULT_V1.md` and the run's
`.gateaudit.json` are unedited. No live call, no rerun, no new issuer, no E1 threshold change, no
C1/C4 change, no Gap semantics change, no confidence change, no State Fidelity change, no M8/R3
change, no D5 execution.

The replayed result is recorded separately, as §F, and is labelled an offline replay everywhere it
appears.

---

## C. sell-off False Positive

What `\bsell\b` does and does not match, which is the whole mechanism:

| text | `\bsell\b` | why |
|---|---|---|
| `sell-off` | **matches** | `-` is a non-word character, so there is a boundary after "sell" |
| `sell-side` | **matches** | same |
| `selloff` | does not | "o" is a word character; no boundary |
| `selling` | does not | no boundary |
| `sells` | does not | no boundary |

So the detector's exposure was never the word "sell". It was specifically the hyphenated compounds,
and `selloff` written closed would have passed the identical gate. A rule whose verdict depends on
whether an author hyphenated a compound noun is not measuring what E7 is about.

The repair does not add "sell-off" to an exemption list. It inverts the direction of matching: a
family matches the **shape** of an instruction, and market vocabulary never has that shape.

```
imperative          ^(buy|sell|short|accumulate|trim|exit|avoid)\b(?!-)     at a clause start
prescription        (you|we|investors) (should|must|need to) (buy|sell|...)
recommendation      (I|we|investors) recommend/advise/urge ... (buy|sell)\w*
rating              strong buy | buy rating | sell recommendation | rated a buy
decision token      APPROVE | WATCH | REJECT, uppercase, case-sensitive
price directive     (entry|exit|stop|target) (is|at|of) $...  | stop-loss | take-profit
valuation           ^(the|our|my)? (fair|intrinsic) value (is|of) $...
price opinion       cheap | expensive | attractive, with a security word in the clause
jargon              price target | target price | price objective | undervalued | ...
```

`(?!-)` is the single character that closes the BR-C defect, and the gerunds and plurals that the
word boundary already excluded are reached through the recommendation frame instead, where an actual
recommender is named. "sell-off", "buyback", "selling pressure", "sell-side coverage", "customer
purchase" and "the company sells HVAC equipment" are not excused by a rule - they are never matched.

---

## D. Negated Price-Target False Positive

Negation is read **per clause**, which is not a new convention: D4-BR §G froze it when it found that
a disclaimer behind a semicolon does not reach the clause above it. A cue counts only when it sits
before the matched term in the same clause.

```
Nothing here is a valuation, a price target or a decision.       cue before term, same clause   ALLOW
This is not a price target.                                     ALLOW
No price target is provided.                                    ALLOW
This analysis does not issue an investment decision.             ALLOW
our price target is $50                                          no cue                       VIOLATION
Our price target is $50. This is not a price target.             clause 1 has no cue          VIOLATION
Buy the stock; this is not a recommendation.                     clause 1 has no cue          VIOLATION
Do not sell; buy instead.                                        clause 2 is an instruction   VIOLATION
```

The last two lines are what stops this from being a blanket negation exemption, and the brief named
the fourth case specifically. Commas are deliberately **not** clause boundaries: "Nothing here is a
valuation, a price target or a decision" is one denial, and splitting on the comma would strand the
cue in a clause of its own.

A consequence worth stating rather than burying: the generating validator's two pre-existing
contextual families, "fair value" and "approve/reject", are now negation-aware too. Their window
logic is untouched - D3.2 §G measured those windows on real filings and rewriting them would move
verdicts this step has no business moving - so a denial is read as a denial everywhere, and nothing
else about them changed.

---

## E. E7 Semantic Contract

```
authority    validation_v2.decision_leakage_findings
callers      audit_strategy_h_v2_d4_1 (E7)          - the grading audit
             schema_v2._no_investment_language_v2    - every D3 V2 and D4 analysis text field
retired      audit_strategy_h_v2_d4_1.DECISION_VOCABULARY
             validation_v2._UNAMBIGUOUS_TERMS / _UNAMBIGUOUS_RE
```

`decision_leakage_findings` returns only `VIOLATION` matches. An `UNCLASSIFIED` lexical hit is not a
decision leak, and a gate whose threshold is zero is only meaningful if what it counts are assertions.

What stays local and independent is `AUDIT_BANNED_FIELDS`, the field-NAME check. A field name is a
different obligation from prose semantics - `{"fair_value": 42}` is prohibited whatever the
surrounding sentence says - and that list is still deliberately a separate statement from
`analysis_schema.BANNED_D4_FIELD_NAMES`.

**Both directions of the change, stated plainly.** More permissive: hyphenated market compounds, a
bare mention of "recommendation", and any denial of a prohibited phrase. Stricter: the generating
validator now also rejects imperatives, prescriptions, recommendation frames, ratings, uppercase
decision tokens, price directives and bare valuation declarations, none of which it had a family for
before. A D4 output that says "Buy the stock" is now refused at the layer that produces it rather
than only at the layer that grades it, which is the point of having one authority.

The `(fair|intrinsic) value (is|of) $` family is the one place this step chose a deliberate trade-off.
`Fair value is $60.` has to be a violation, and `The fair value was $5.0 million as of December 31`
has to not be, so the pattern is restricted to the present tense at a clause start and yields to the
GAAP safe markers. Mid-clause accounting prose is therefore left where it was - `UNCLASSIFIED`, which
`_no_investment_language_v2` treats as safe - and that is the same default D3.2 §G argued for on
measured evidence rather than a new concession.

---

## F. Full Corpus Replay

`app/dev/replay_d4_e7r_decision_leakage.py`. Zero live calls, `$0`, both reported as fields on the
result. The pre-E7R vocabulary is reconstructed verbatim in that module and run beside the new
semantics, because a comparison against a detector that no longer exists is the only way to say which
findings were removed and which are new.

The walk is over the whole runtime tree rather than a path per stage, and a D4 analysis record is
identified by carrying `analysis_id`, so coverage does not depend on remembering where each stage
stored its records.

```
D4 analysis records seen         19
final outputs replayed           15
records with no final output      4      D4.1's GOOG and SCCO, Tier B's FRPT and SPSC
                                         E7 is measured on final outputs by contract

old E7 findings                   1
new E7 findings                   0
false positives removed           1
newly exposed violations          0
retained violations               0
true decision leakage             0
```

Every stage the brief named is present:

| run | stage | records | final outputs |
|---|---|---|---|
| `D4_1_A-20260929T045619Z` | D4.1 pilot | 2 | 0 |
| `D4_2_A-20260929T072105Z` | D4.3A Tier A V2 | 3 | 3 |
| `D4_2_A-20260930T012115Z` | D4.4A Final Tier A V3 | 3 | 3 |
| `D4_2_A-20260930T034348Z` | D4-S1 SCCO limited live smoke | 1 | 1 |
| `D4_B-20260930T053146Z` | Tier B | 6 | 4 |
| `D4_BR_C-20261001T005758Z` | D4-BR-C | 4 | 4 |

The single removed finding is COLL's, and it is the only finding either detector produces anywhere in
the corpus:

```
REMOVED   \bsell\b   root.market_expectation_evidence.pre_event_positioning_reading[1].text
          "...there was neither a run-up nor a sell-off before the event."
```

**Same-byte BR-C replay, through the real audit path.** `audit_confirmation` re-scored run
`D4_BR_C-20261001T005758Z` in memory, over the stored bytes, with nothing written:

```
                      stored (historical)    E7R offline replay
E1                    PASS  4/4 = 100.0%     PASS  4/4 = 100.0%
E2, E3, E4, E5, E6    PASS                   PASS
E7                    FAIL  1                PASS  0
E8                    PASS                   PASS
SF1                   PASS  0 of 18          PASS  0 of 18
C1 / C4 / C5 / C6     NOT_EVALUATED / PASS / PASS / PASS      identical
verdict               FAIL                   PASS
D5                    NOT READY              READY FOR CONTRACT DESIGN
```

Diffed key by key rather than summarised: the only gate that moved is E7, and `rules`,
`state_fidelity`, `m8_scope`, `expectation_gap_distribution`, `budget`, `models` and
`sample_integrity` are byte-identical. At candidate level the only change in the whole report is
COLL's `decision_vocabulary_leaks` going from one entry to none; AEYE, FG and VRRM have no changed
keys at all. Tier B was replayed the same way and is unchanged on verdict and on all nine gates.

That is the evidence that this is a rule change and not a COLL exemption. The classifier contains no
issuer, no ticker and no run id.

**§10 initial-response diagnostic.** The disclaimer that cost COLL a repair round is allowed under the
new classifier, and the validator and the audit now agree on both of COLL's sentences. This is
recorded as a diagnostic and is **not** used to reduce the historical repair count: the model spent
that round, the record says two rounds, and a detector repaired afterwards does not refund it.
`test_the_coll_repair_count_is_not_rewritten` asserts the stored record still reads 2.

---

## G. Regression Tests

`backend/tests/strategy_h_v2/expectation/test_d4_e7r_decision_leakage.py`, 65 tests.

The two motivating sentences are fixtures quoted verbatim from the run, not paraphrases. Every case
the brief listed is asserted in both directions:

```
22 decision-language fixtures still rejected
   Buy/Sell the stock, I recommend buying/selling, APPROVE/WATCH/REJECT, APPROVE this investment,
   price target is $50, Fair value is $60, Entry is $40, Exit at $55, you should buy,
   strong buy, undervalued, the stock looks cheap, we approve this stock

28 market and business fixtures allowed
   sell-off, sold off, buyback, buyer, selling pressure, no sell-off, not a buy recommendation,
   not a price target, no valuation is provided, the filing was approved, customer purchase,
   board approved the share repurchase, shareholders rejected the proposal,
   customers buy replacement parts, the company sells HVAC equipment,
   expensive to manufacture, sell-side coverage, buy-side has not published

negation is per clause, not blanket
   Do not sell; buy instead            -> VIOLATION on the BUY
   Buy the stock; this is not a rec.   -> VIOLATION
   not a recommendation to buy         -> ALLOW

the pre-E7R contextual work preserved
   GAAP fair value (5 phrasings)       -> ALLOW
   stock's fair value is $180          -> VIOLATION
   intrinsic value of options exercised-> ALLOW

one authority
   the audit has no DECISION_VOCABULARY, and does import decision_leakage_findings
   the retired unconditional jargon list is gone, its phrases are in _JARGON_TERMS
   the validator and the audit agree on both COLL sentences
```

The corpus-replay and stored-audit tests skip on a clean checkout, because `data/runtime` is
gitignored; every contract claim is also asserted on an inline fixture so nothing rests only on data
that may be absent.

Pre-existing suites that pin the earlier behaviour - `test_validation_v2`, `test_schema_v2`,
`test_analysis_schema`, `test_d4_validate` - pass unedited. The one test that had to change is this
project's own: `test_d4_br_c_confirmation` recorded, as a finding, that the detector matched
"sell-off". That recording is now a historical statement rather than a current one, and the BR-C
verdict assertions in that file are untouched.

Full H-V2 suite after the repair: see §I.

---

## H. Deferred Limitations

**R3 compound set-valued numeric semantics: OBSERVED / DEFERRED.** Unchanged by this step and carried
into D5 as a known limitation. Its measured exposure is still Tier B's one CRK occurrence; D4-BR-C
added none. R3 changes M8's unit of comparison, which is a gate-semantics change, and a change to
what E5 measures has to be frozen before the run it grades.

**C1 has never been evaluated.** Three consecutive graded runs - Tier A V3, Tier B, D4-BR-C - produced
no POSITIVE-family output, so C1's denominator has been zero every time. Nothing in this step
addressed it and no output was steered to give it a case. D5's contract design should decide whether
C1 is reachable at all under the current Gap semantics, rather than inheriting a rule that has never
had a case.

**The classifier is rules, not understanding.** It matches the shapes of instruction that this corpus
and this brief named. A decision phrased in a shape none of these families covers would pass, and the
honest bound on that claim is the corpus: 15 final outputs, in which it finds zero decision language
and so has demonstrated zero true positives on real data. Its true-positive evidence is 22 fixtures,
which is a statement about the rules and not about model behaviour.

**Mid-clause accounting valuation prose stays UNCLASSIFIED.** §E states the trade-off. A sentence like
"Under ASC 820 the fair value is $3.2 million" is not matched, by design, and a valuation opinion
phrased that way would also not be matched.

**The generating validator got stricter in families no stored output has ever exercised.** The action,
rating, decision-token and price-directive families have 0 occurrences in 15 final outputs, so their
effect on repair rates is unmeasured. They can only reject text E7 would have failed anyway, which
bounds the risk to a repair round rather than a wrong verdict.

---

## I. Verdict

```
H-V2-D4-E7R
= READY FOR D5 CONTRACT DESIGN
```

The §12 acceptance conditions, each fixed before the replay was run and each met:

```
known sell-off false positive removed                          YES   corpus replay, 1 removed
known "not a price target" false positive removed              YES   fixture + §10 diagnostic
actual decision language fixtures still rejected               YES   22 of 22
unresolved true decision leakage in the stored corpus = 0      YES   0 retained, 0 newly exposed
no other D4 correctness gate semantics changed                 YES   diffed key by key, E7 only
```

```
D4 = COMPLETE
```

No further D4 live issuer validation is generated. D4-BR-C's convergence question was answered on
four unseen issuers, and the gate that failed it was a detector defect that is now repaired and
replayed at zero cost. The known deferred limitations are named in §H and travel to D5 as
limitations rather than as open work.

Honest about what this is: a detector repair, offline, on 15 stored outputs. It did not re-ask any
live question and it did not make the pipeline better at expectation analysis. What it did is make
E7 measure the thing E7 is named after.

---

## J. D5 Authorization

```
D5 Valuation Fundamentals
= READY FOR CONTRACT DESIGN
```

Contract design only. D5 execution is a further user authorization that this step does not grant, and
no valuation, no APPROVE/WATCH/REJECT, and no forward return is produced anywhere in it.

---

## Final Declaration

```
live Opus calls?                     NO    0
live cost?                           $0
new issuer?                          NO
D4 rerun?                            NO
D4-BR-C historical verdict changed?  NO    FAIL, asserted on the stored artifact
E1 threshold changed?                NO
C1/C4 changed?                       NO
Gap semantics changed?               NO
confidence semantics changed?        NO
State Fidelity changed?              NO
M8/R3 changed?                       NO
repair-count history rewritten?      NO
COLL-specific exemption?             NO
D5 executed?                         NO
valuation / APPROVE-WATCH-REJECT?    NO
forward returns?                     NO
existing unrelated dirty modified?   NO
push?                                NO
```
