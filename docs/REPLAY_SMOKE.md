# Replay Smoke Validation

## 목적과 범위

Replay Smoke는 Stage 1~7.1의 Market Data → Quant Scanner → Strategy → Risk →
SimBroker → Shadow Result 경로를 여러 XNYS 세션에서 반복 검증한다. 수익률 연구나
정식 백테스터가 아니다. Strategy registry, optimizer, parameter sweep, 병렬 실행,
외부 데이터 연결은 만들지 않는다.

## Synthetic data와 거래 캘린더

`SyntheticReplayDataset`은 기본 20 XNYS 거래일과 24개 종목, 별도 SPY benchmark를
생성한다. 시작 범위는 2025-11-03이며 Thanksgiving holiday, weekend rollover와
2025-11-28 early close를 포함한다. Scanner용으로 각 실행일 직전 거래일까지 21개
이상의 Daily history를 제공한다. 각 실행일에는 premarket과 regular minute bar가
있다. 분봉 timestamp는 bar start이고 `available_at = timestamp + 1 minute`이다.
모든 수치와 pattern 배치는 공식 없이 임의 random state를 쓰지 않는다.

Daily scan은 실행일 직전 완료 세션의 close 이후 PIT replay view로 실제
`QuantScanner`를 호출한다. TOP8은 fixture에 하드코딩하지 않고 Quant V0 점수와
tie-break로 결정되며 날짜별 volume/price sequence 때문에 구성이 회전한다. 저가와
market-cap 미달 종목도 함께 있어 scanner rejection이 지속적으로 검증된다.

## Runner architecture

`ReplaySmokeRunner`는 얇은 순차 coordinator다. 날짜별 scanner 결과를 받고 Top8
각 candidate를 A~E로 fan-out한다. 각 path는 공통 `StrategyV0Engine`, `RiskEngine`,
`StrategyLifecycleRunner`, `SimBroker`를 사용한다. Candidate/variant별 broker와 risk
ledger를 분리하므로 stop, high-water mark, ADD, cash와 reservation이 섞이지 않는다.

계좌 정책은 `FIXED_RESEARCH_CAPITAL`이고 각 path는 동일한 10,000 USD에서 시작한다.
따라서 synthetic performance는 날짜 간 compound하지 않는다. 이는 lifecycle 비교를
위한 연구 context이며 실제 portfolio equity curve나 MDD를 의미하지 않는다.

매일 새 entry state와 daily risk context를 만든다. Overnight path만 calendar의
`next_trading_day`로 Day2까지 이어지고, 그 position은 다음 scanner 결과와 별도로
소유된다. 최종 smoke 날짜의 Day1 overnight도 다음 XNYS 세션까지 진행해 mandatory
exit한 뒤 run을 종료한다. Day3는 허용하지 않는다.

## Pattern coverage

결정적 pattern schedule은 momentum continuation, premarket/opening rejection,
initial stop, trailing winner, one-time pyramiding, overnight hold/reduce/reject,
Day2 mandatory exit, ambiguous bar, missing opening bar, missing next bar, early close,
weekend/holiday rollover를 날짜와 종목에 분산한다. Research fixture는 overnight
branch에 필요한 HIGH suitability만 주입하며 GPT 호출이나 성과 개선용 데이터를
생성하지 않는다.

## 결과와 invariant

Run result는 scanner run, daily result, candidate/variant path, A~E summary를 가진다.
Closed trade만 average/median Net R denominator에 포함한다. Win/Loss/Breakeven은
각각 Net PnL `> 0`, `< 0`, `== 0`이며 positive rate denominator는 전체 closed trade다.
Spread, slippage, commission, FX와 total cost를 별도 합산한다.

다음 invariant는 warning이 아니라 실패다: orphan lifecycle, negative cash, short,
Day3, add_count > 1, stop 감소, variant grain 충돌, shadow risk 오염, future bar 사용,
fill/state 불일치. `validate()`는 동일 run 두 번과 reverse-order universe run을 비교하며
audit run ID를 제외한 scanner, path, fill-derived result, 비용, R, ambiguity와 summary가
완전히 같아야 한다.

## Runtime report

다음 명령은 `data/runtime/replay_smoke_report.json`을 생성한다.

```bash
PYTHONPATH=backend .venv/bin/python -m app.replay_smoke
```

JSON에는 range, universe, version, account policy, scanner/daily/path/variant 결과,
invariant, repeat/shuffle 판정과 totals가 들어간다. 생성물은 runtime output이며 DB
schema나 migration을 추가하지 않는다.

> Synthetic Smoke results are implementation validation only.

Synthetic PnL, 승률 또는 variant 차이는 수익성 증거나 parameter 변경 근거가 아니다.
실제 historical replay는 provider 선정, corporate-action/PIT 품질 확인, portfolio equity
curve와 MDD 정의 후 별도 단계에서 수행한다. 다음 단계는 Failure/Safe Mode이며 실제
historical data와 Kiwoom 연결은 각각 이후 범위다.
