# Stage 10A.1 — Session Policy Audit

Audited on 2026-09-04. This is a code and provider-contract audit only; it
does not change Strategy or Risk semantics.

## Existing Session Model

USB represents `PREMARKET`, `REGULAR`, and `POSTMARKET` explicitly on each
minute bar. Kiwoom minute data does not currently map a direct session field.
The adapter derives the label from an aware `America/New_York` timestamp:
before 09:30 is premarket, 09:30 through 15:59 is regular, and 16:00 onward is
postmarket. The XNYS market calendar remains authoritative for holidays, DST,
and early closes, so timestamp-only labels are not sufficient policy gates.

| Session | Kiwoom direct field | Derivable | Current result |
| --- | --- | --- | --- |
| PREMARKET | Not confirmed | Yes, timestamp + calendar | Calendar validation required |
| REGULAR | Not confirmed | Yes, timestamp + calendar | Calendar validation required |
| POSTMARKET | Not confirmed | Yes, timestamp + calendar | Calendar validation required |

## Current Strategy Behavior

| Session | Current behavior |
| --- | --- |
| PREMARKET | The premarket gate evaluates gap, volume, research, and catalyst context. Day-2 premarket can hold or signal an exit for a new negative catalyst. Opening entry evaluation does not use premarket bars. |
| REGULAR | Opening range, VWAP entry, stop, trailing, and winner-only add logic use regular-session bars. Closing review determines exit or overnight hold. |
| POSTMARKET | Bars are represented and recordable, but normal position evaluation filters them out. There is no explicit postmarket entry/add path or postmarket position-management policy. |

## Intended V1 Policy Candidate

- PREMARKET: market observation and risk context only; no new entry or add.
- REGULAR: primary entry, add, exit, and general strategy execution.
- POSTMARKET: no new entry or add; existing-position management and overnight
  review only.

## Gap and Stage 10B-2 Resolution

- The current implementation agrees that opening entries and adds are
  regular-session only.
- Session labels are clock-derived and do not encode holidays or early-close
  postmarket boundaries themselves. Orchestration must combine them with the
  market calendar.
- Postmarket remains monitoring/overnight-review context only. Closing review
  is designed around the regular close; no arbitrary postmarket execution path
  was added.
- Day-2 premarket may emit an exit decision on negative catalyst. Whether that
  is merely a risk signal or an executable premarket action must be specified
  before a non-simulation broker exists.
- Kiwoom direct session fields and extended-hours coverage remain unconfirmed
  until a live minute/realtime sample is observed.

- Before Stage 10B-2, the lifecycle coordinator trusted its caller's session.
  A directly supplied ENTER/ADD decision could therefore reach SimBroker, and
  its next-bar fill could cross from REGULAR into POSTMARKET.
- Stage 10B-2 added one minimal, XNYS-aware execution guard. ENTER and ADD are
  accepted only in REGULAR, and their fill candidates must remain within that
  session. Holidays, DST, and early closes are derived from `MarketCalendar`.
- PREMARKET and POSTMARKET retain market-data and existing-position monitoring
  permission but cannot create entry/add execution.

No Strategy/Risk parameter, Quant weight, entry signal, gap threshold, shadow
variant, or broker fill rule was changed.
