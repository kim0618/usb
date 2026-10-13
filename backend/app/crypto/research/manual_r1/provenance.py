"""Who made each trade, and how confidently we can say so.

MANUAL-R1 treated all 17 completed trades as the operator's discretionary trading. They were
not. Most came from development and verification work on the production paper account, and the
ledger cannot tell the two apart: every order carries `reason: MANUAL` and the same
`open-<ts>-<seq>` request id, because they all went through the same UI order route.

So provenance has to come from outside the ledger: work reports, deployment notes, and the
operator's own declaration. Each classification here names its evidence and its tier, and
anything that cannot be tied to a specific record stays UNKNOWN. A wrong confident label is worse
than an honest gap, in both directions: calling a test a user trade invents a losing habit, and
calling a user trade a test hides a real one.

MANUAL-R1.2 closed the gap from the one source the ledger never had: the operator stated which
trades were theirs. That declaration is authoritative about *attribution* for every completed
trade at once, and silent about *which kind of test* each of the others was. Those are two
different questions, and conflating them is exactly the mistake R1.1 corrected, so this module
now records them on two axes: `confidence` answers whose trade it is and decides which
performance figure it enters, while `detail_confidence` answers which test it was and decides
nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# --- closed vocabulary. Free text would let this grow until it stops being a partition. ---
USER_MANUAL = "USER_MANUAL"
DEV_SMOKE = "DEV_SMOKE"
DEPLOY_SMOKE = "DEPLOY_SMOKE"
SIZING_TEST = "SIZING_TEST"
PNL_TEST = "PNL_TEST"
RESET_TEST = "RESET_TEST"
LIQUIDATION_TEST = "LIQUIDATION_TEST"
ACCOUNTING_TEST = "ACCOUNTING_TEST"
RESEARCH_TEST = "RESEARCH_TEST"
TEST_UNSPECIFIED = "TEST_UNSPECIFIED"
AUTO_PAPER = "AUTO_PAPER"
UNKNOWN = "UNKNOWN"
UNKNOWN_LEGACY = "UNKNOWN_LEGACY"

ORIGINS = (USER_MANUAL, DEV_SMOKE, DEPLOY_SMOKE, SIZING_TEST, PNL_TEST, RESET_TEST,
           LIQUIDATION_TEST, ACCOUNTING_TEST, RESEARCH_TEST, TEST_UNSPECIFIED,
           AUTO_PAPER, UNKNOWN, UNKNOWN_LEGACY)

USER = "USER"
TEST = "TEST"
RESEARCH = "RESEARCH"
AUTO = "AUTO"
UNKNOWN_GROUP = "UNKNOWN"

ORIGIN_GROUP = {
    USER_MANUAL: USER,
    DEV_SMOKE: TEST, DEPLOY_SMOKE: TEST, SIZING_TEST: TEST, PNL_TEST: TEST,
    RESET_TEST: TEST, LIQUIDATION_TEST: TEST, ACCOUNTING_TEST: TEST,
    TEST_UNSPECIFIED: TEST,
    RESEARCH_TEST: RESEARCH,
    AUTO_PAPER: AUTO,
    UNKNOWN: UNKNOWN_GROUP, UNKNOWN_LEGACY: UNKNOWN_GROUP,
}

CONFIRMED = "CONFIRMED"
HIGH = "HIGH"
MEDIUM = "MEDIUM"
CONFIDENCE_UNKNOWN = "UNKNOWN"

#: A row that makes no claim about which kind of test it was. `TEST_UNSPECIFIED` is not a weak
#: guess at a subtype, it is the absence of one, and this is how that absence is written down.
DETAIL_UNSPECIFIED = "UNSPECIFIED"

#: Only these confidences let a trade into its group's performance figures. Everything else is
#: reported but counted nowhere, which is what keeps an uncertain trade from quietly picking a
#: side. The confidence read here is the attribution one: `detail_confidence` deliberately has
#: no say, because how precisely a test is named cannot change whose money it was.
COUNTABLE = (CONFIRMED, HIGH)

TIER_1 = "TIER_1_DIRECT"        # a document or the operator names this exact trade
TIER_2 = "TIER_2_STRONG"        # quantity, side and time match a documented action
TIER_3 = "TIER_3_WEAK"          # only the period or the shape matches; never decisive

#: The operator's Q3 answer of 2026-09-28, given after reading the full 13-trade table. It is
#: what the ledger could never supply, and it is TIER_1 about attribution for every completed
#: trade at once: trade 17 and the open position are theirs, and no completed trade before
#: trade 17 is. Q3 was the only immediate way to settle the 13, and it settled them against
#: the operator's own interest: the 600.56 USDT liquidation moves into the development column
#: on their word, not on a document.
DECLARATION = "operator declaration, MANUAL-R1.2 Q3 final, 2026-09-28"

DECLARATION_TEXT = (
    "실제 투자 판단으로 수행한 거래는 #17 완료 익절 거래와 현재 OPEN SHORT 두 건뿐이다. "
    "그 이전 완료 거래는 전부 개발/테스트 과정에서 발생했다.")


@dataclass(frozen=True)
class Classification:
    """One trade's provenance, on two independent axes.

    `confidence`/`evidence_*` answer *whose trade was this*, and only that answer may move
    money between performance figures. `detail_confidence`/`detail_*` answer *which test was
    this*, and are reported without ever being counted on. Keeping them apart is what stops a
    strong attribution from lending its authority to a guessed subtype.
    """

    trade_index: int | None
    origin: str
    confidence: str
    evidence_tier: str | None
    evidence_reference: str | None
    evidence_note: str
    detail_confidence: str = DETAIL_UNSPECIFIED
    detail_evidence_tier: str | None = None
    detail_reference: str | None = None

    @property
    def origin_group(self) -> str:
        return ORIGIN_GROUP[self.origin]

    @property
    def claims_a_test_subtype(self) -> bool:
        return self.origin_group == TEST and self.origin != TEST_UNSPECIFIED

    @property
    def include_in_manual_performance(self) -> bool:
        return self.origin == USER_MANUAL and self.confidence in COUNTABLE

    @property
    def include_in_test_performance(self) -> bool:
        return self.origin_group == TEST and self.confidence in COUNTABLE

    def as_dict(self) -> dict[str, Any]:
        return {"trade_index": self.trade_index, "origin": self.origin,
                "origin_group": self.origin_group, "confidence": self.confidence,
                "evidence_tier": self.evidence_tier,
                "evidence_reference": self.evidence_reference,
                "evidence_note": self.evidence_note,
                "detail_confidence": self.detail_confidence,
                "detail_evidence_tier": self.detail_evidence_tier,
                "detail_reference": self.detail_reference,
                "include_in_manual_performance": self.include_in_manual_performance,
                "include_in_test_performance": self.include_in_test_performance}


#: The audit itself. Each row states what it is based on, so a reader can disagree with the
#: reasoning rather than only with the label.
#:
#: Every attribution below rests on DECLARATION. The document-based findings from R1.1 are kept
#: as the detail axis where they survive, and as observations in the notes where they do not:
#: the 9/23 smoke shape and the development-block adjacency are still true, they simply were
#: never strong enough to name a subtype, and the declaration does not make them stronger.
AUDIT: dict[int, Classification] = {
    1: Classification(1, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION,
                      "Development, on the operator's word. The shape still looks like an "
                      "order-path smoke: 0.001 BTC is the instrument's minimum grid quantity, "
                      "trades 1 to 5 run LONG/SHORT/LONG/SHORT/LONG inside 9 minutes with holds "
                      "of 0.1 to 0.5 minutes, and the ledger carries a REVERSE_NOT_ALLOWED "
                      "rejection at 0.001. R1.1 read that as DEV_SMOKE against a deployment "
                      "note saying '스모크 체결 4건이 원장에 남아있음', but the note counts four "
                      "and this group is five, so the subtype was never established and is not "
                      "claimed here."),
    2: Classification(2, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION, "see trade 1"),
    3: Classification(3, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION, "see trade 1"),
    4: Classification(4, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION, "see trade 1"),
    5: Classification(5, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION, "see trade 1"),
    6: Classification(6, SIZING_TEST, CONFIRMED, TIER_1, DECLARATION,
                      "Development, on the operator's word, and the subtype is independently "
                      "documented.",
                      HIGH, TIER_2, "memory project_usb_crypto_server_deploy line 53"),
    7: Classification(7, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION,
                      "Development, on the operator's word. It sits inside the same continuous "
                      "9/23 development block as the documented tests, 06:20 to 08:27 UTC, with "
                      "gaps of 2.6 to 32.5 minutes between trades. No document names this "
                      "quantity, so which test it was is not established."),
    8: Classification(8, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION, "see trade 7"),
    9: Classification(9, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION, "see trade 7"),
    10: Classification(10, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION,
                       "see trade 7; the ledger also holds a NO_LIQUIDITY rejection at 0.082, "
                       "this trade's quantity, which fits depth probing."),
    11: Classification(11, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION,
                       "see trade 7; a LEVERAGE_CHANGE to 50 is recorded two seconds before this "
                       "entry, and again before trades 12 and 13: the signature of someone "
                       "exercising the leverage selector. Which test it served is not "
                       "documented."),
    12: Classification(12, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION, "see trade 11"),
    13: Classification(13, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION,
                       "The 600.56 USDT liquidation. Opened at 08:26:55 UTC inside the 9/23 "
                       "development block, six seconds after trade 12 closed, and liquidated "
                       "5.8 hours later at 14:13:00. R1.1 left it UNKNOWN because no work report "
                       "or deployment note mentions a liquidation on paper-server-001, and no "
                       "document has been found since: this row rests on the declaration alone. "
                       "It is still not established whether it was a deliberate test or a "
                       "position left open when a session ended, and neither is claimed."),
    14: Classification(14, TEST_UNSPECIFIED, CONFIRMED, TIER_1, DECLARATION,
                       "SHORT 0.051 at 50x on 9/24 03:31 UTC, held 46.5 minutes, net +5.41. "
                       "R1.1 left it UNKNOWN because it sits alone, 798 minutes after trade 13 "
                       "and 68 minutes before trade 15, matching neither the 9/23 development "
                       "block nor the 9/24 D4.1 session. Attribution now rests on the "
                       "declaration; the isolation is unexplained and no subtype is claimed."),
    15: Classification(15, SIZING_TEST, CONFIRMED, TIER_1, DECLARATION,
                       "Development, on the operator's word, and the subtype is independently "
                       "documented: the D4.1 sizing document states verbatim "
                       "'실측(운영, 2026-09-24): 포지션 진입 1.082 BTC ask depth 10.740 -> 체결 "
                       "/ 전량 청산 시도 1.082 BTC bid depth 0.157 -> NO_LIQUIDITY'. This trade "
                       "is LONG 1.082 on 2026-09-24 and the ledger holds two NO_LIQUIDITY "
                       "rejections at 1.082. It is the trade that produced the SAFE MAX "
                       "finding.",
                       CONFIRMED, TIER_1, "docs/crypto/CRYPTO_D4_1_SAFE_SIZING_V1.md section 1"),
    16: Classification(16, DEV_SMOKE, CONFIRMED, TIER_1, DECLARATION,
                       "Development, on the operator's word, and the subtype is independently "
                       "documented: the D4.1 note records '이번에 내 smoke 2건 확인 후 cleanup "
                       "완료'. Trades 15 and 16 are the only two trades between the second and "
                       "third ACCOUNT_RESET, and that third reset is the cleanup, which left the "
                       "account flat at 10,000,000 KRW with 16 trades in history: exactly the "
                       "count up to and including this trade.",
                       CONFIRMED, TIER_1, "memory project_usb_crypto_server_deploy line 147"),
    17: Classification(17, USER_MANUAL, CONFIRMED, TIER_1, DECLARATION,
                       "The operator's own trade, named by them twice: first by its net result "
                       "of about +330.75 USDT, which is unique in the ledger, and then in the Q3 "
                       "declaration as one of the only two positions they took on their own "
                       "judgement. It is the first trade after the developer's cleanup reset "
                       "left the account flat with 16 trades in history.",
                       CONFIRMED, TIER_1, DECLARATION),
}

OPEN_POSITION = Classification(
    None, USER_MANUAL, CONFIRMED, TIER_1, DECLARATION,
    "The operator's own position, and the second of the two they claim. The V2.7 deployment note "
    "of 9/27 independently records '사용자 SHORT 0.921@84038.20' while verifying the ledger was "
    "unchanged across a restart. Still open, so it enters no realised figure.",
    CONFIRMED, TIER_1, "memory project_usb_crypto_server_deploy line 177")


def classify(trade_index: int) -> Classification:
    return AUDIT.get(trade_index, Classification(
        trade_index, UNKNOWN, CONFIDENCE_UNKNOWN, None, None,
        "not covered by the audit"))


def summary() -> dict[str, Any]:
    rows = list(AUDIT.values())
    by_origin: dict[str, int] = {}
    by_group: dict[str, int] = {}
    for row in rows:
        by_origin[row.origin] = by_origin.get(row.origin, 0) + 1
        by_group[row.origin_group] = by_group.get(row.origin_group, 0) + 1
    return {
        "classified_trades": len(rows),
        "by_origin": by_origin,
        "by_origin_group": by_group,
        "countable_user": [r.trade_index for r in rows if r.include_in_manual_performance],
        "countable_test": [r.trade_index for r in rows if r.include_in_test_performance],
        "counted_nowhere": [r.trade_index for r in rows
                            if not r.include_in_manual_performance
                            and not r.include_in_test_performance],
        "unknown_attribution": [r.trade_index for r in rows
                                if r.origin_group == UNKNOWN_GROUP],
        "subtype_unspecified": [r.trade_index for r in rows
                                if r.origin == TEST_UNSPECIFIED],
        "open_position": OPEN_POSITION.as_dict(),
        "attribution_source": DECLARATION,
        "attribution_statement": DECLARATION_TEXT,
        "rule": "only CONFIRMED and HIGH enter a performance figure; MEDIUM and UNKNOWN are "
                "reported and counted nowhere",
        "two_axis_rule": "confidence answers whose trade it was and decides inclusion; "
                         "detail_confidence answers which test it was and decides nothing",
    }
