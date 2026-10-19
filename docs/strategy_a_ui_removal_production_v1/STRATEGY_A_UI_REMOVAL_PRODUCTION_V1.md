# STRATEGY A UI REMOVAL PRODUCTION V1

결과: **ACTIVE** (2026-10-19 KST, frontend 15:29:43 swap, 15:30:46 ready)

출처: `4041cf6`(origin/main) frontend diff만. backend·DB·env·다른 서비스 변경 0.

## 요약
| 항목 | 값 |
|---|---|
| FINAL STATUS | **A_UI_REMOVED_FROM_PRODUCTION** |
| Strategy A | CLOSED / ARCHIVED, 운영 runtime OFF, Paper OFF, active UI 없음 |
| 운영 frontend 기준 서빙본 | **R6xplGezseLQq2HrES91s** |
| 직전 서빙본 | c4VYa7VRAWu0aFBN3fAvV |
| 롤백 | `/root/usb/frontend/.next-build.pre-a-ui-removal-20261019-151548` |
| 제거 라우트 (운영 404) | /trading, /adoption, /candidates, /research, /shadow |
| A 메뉴 / A active UI / A 버튼 / A API 호출 | 0 / 0 / 0 / 0 |
| E UI / H UI / Crypto UI | PASS / PASS / PASS |
| backend 변경 | 0 (재시작 0, 파일 0, env 0) |
| DB 기록 삭제 | 0 (DB 쓰기 0, 스모크는 GET만) |
| 실주문 | 0 (비GET 0, binance 경로 차단) |
| 재시작 | usb-frontend 1회만 |

**다음 프런트 배포는 반드시 아래 "서빙본 재구성" 레시피 + `4041cf6`으로 만든 트리(= R6xpl)에서 시작한다.** `/root/usb/frontend` 소스나 로컬 HEAD 전체를 빌드하면 안 된다: 운영의 H 화면·`lib/strategies.ts`는 HEAD보다 구판(H autonomy V1)이고, crypto-terminal에는 병합 조정이 들어 있다. 새 변경은 커밋별 frontend diff만 얹고, 얹기 전에 기준선 빌드를 서빙본과 대조(라우트, 한글 문자열 집합, baked 주소 3종)한다.

## 서빙본 재구성 (c4VYa7)
- 레시피(기록 근거): 3e2a8ad + 5093f6c/1c6c900/04428f1/2b1285d/16e7597/447b690/4906e95 (lhfMQ) + 52f8b93/4e31be4 + `crypto-terminal.merge.diff`의 contextBase 구조분해 (07OdWY) + 043cee1 (PrBzRXD3) + fb5e21b/ebf6898 (c4VYa7). rej 0. 병합 조정은 패치 뒤 남은 차이인 `CryptoTerminal({ marketContext, contextBase })` 한 줄뿐(import·렌더 줄은 패치가 유지).
- 빌드: node v20.20.1(서버와 동일, 공식 SHASUMS 검증), next 15.5.25, env API=https://usb.jptcalc.kr, CRYPTO=/crypto-api, BTC_CONTEXT=/btc-context-api.
- parity vs 서빙본: 라우트 20개 동일, 한글 문자열 1433종 완전 일치(차이 0), baked 주소 3종 동일.

## 4041cf6 적용
- 대상 56파일 중 54개가 재구성 트리에서 4041cf6^와 sha 일치. 불일치 2개(lib/strategies.ts, trading-h.test.tsx)는 운영이 HEAD보다 구판인 H autonomy 영역 차이 → 패치 rej 0, 두 파일의 변경 집합이 4041cf6과 동일함을 확인(H 영역 무변경).
- tsc 0. vitest 52파일 1052 통과, 실패 3(crypto-terminal-integration, fx-display, strategy-compare 축약표기)은 재구성 기준선에서도 동일하게 실패 → 신규 실패 0.
- 빌드 delta: 라우트 5개 제거(/trading /adoption /candidates /research /shadow), 한글 문자열 405종 제거(`build_string_removed.txt`, 전부 A 화면·A 전용 표시 헬퍼), 5종 추가(비교 화면 문구 정정). baked 주소 동일.

## 배포
- BUILD_ID c4VYa7VRAWu0aFBN3fAvV → **R6xplGezseLQq2HrES91s**, 롤백 `.next-build.pre-a-ui-removal-20261019-151548`(cache는 새 빌드로 이동, 나머지 sha 동일 확인). tgz sha `2b8fe54e…`.
- `usb-frontend`만 1회 재시작 PID 2520193→2618562, NRestarts 0. 다른 서비스 PID 전부 불변(backend 2606983, crypto-paper 2519676, crypto-context 1582777, q123 1684303, btc-context-ui 2063894, nginx 707). 디스크 여유 1.31GB→1.30GB.
- 롤백: `systemctl stop usb-frontend; mv .next-build .next-build.failed-…; mv .next-build.pre-a-ui-removal-20261019-151548 .next-build`(cache는 옮겨 오거나 없이 시작), start.

## 운영 브라우저 스모크 (SSH 터널 + GET 전용 프록시, 비GET 0, binance 경로 차단, crypto URL당 1회 캐시)
- 메뉴 = 대시보드 /dashboard · 트레이딩 /strategy-e · 선물 · 시스템. 트레이딩 탭 = E, H.
- /dashboard 카드 = E, H. /strategy-compare 열 = E, H(공식·레거시), 게이트 = E, 선택지 = ALL/E/H. /daily 열 = E, H.
- A 라우트 5개 404, A 링크 0, A 버튼 0, A API 호출 0(research/scanner/trading/shadow/portfolio/STRATEGY_A 패턴), A 문구 0(/runtime의 "자동 Strategy Action" 부분일치 1건은 오탐).
- E(계좌·결정·일별 손익), H(paper 계좌·자동 운영·관찰 종목), Crypto(PAPER BTC SHORT 포지션·차트·주문 패널) 정상 렌더, page error 0.
