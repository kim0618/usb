# USB — Agent Rules

이 파일은 Codex, Claude Code 등 코딩 에이전트가 반드시 따라야 하는 프로젝트 규칙이다.

## 1. 최상위 문서

작업 시작 전 반드시 아래 파일을 읽는다.

1. `docs/V1_FINAL_SPEC.md`
2. `docs/ARCHITECTURE.md`
3. `docs/DEVELOPMENT_PLAN.md`
4. `docs/V2_BACKLOG.md`
5. 현재 작업 프롬프트

충돌 시 우선순위:

```text
현재 사용자 명시 지시
>
V1_FINAL_SPEC
>
ARCHITECTURE
>
DEVELOPMENT_PLAN
>
V2_BACKLOG
```

---

## 2. Scope

- V1_FINAL_SPEC에 없는 기능을 임의로 추가하지 않는다.
- V2_BACKLOG 항목을 V1에 구현하지 않는다.
- 설계 개선 아이디어가 있으면 구현하지 말고 보고서에 제안한다.
- 사용자의 명시 승인 없이 Scope를 확장하지 않는다.

---

## 3. Architecture Rules

### 절대 규칙

1. Live/Paper/Shadow Strategy Engine을 분리하지 않는다.
2. Risk Engine도 공통으로 사용한다.
3. Execution Adapter만 분리한다.
4. Strategy는 Broker 구현을 직접 참조하지 않는다.
5. Broker/Data Provider는 interface 뒤에 둔다.
6. Point-in-Time 원칙을 깨지 않는다.
7. America/New_York 기준 Market Calendar를 사용한다.
8. KST market time 하드코딩을 하지 않는다.

---

## 4. V1 Infrastructure

- Backend: Python 3.12 + FastAPI
- ORM: SQLAlchemy
- DB: SQLite
- Market Data File: Parquet
- Frontend: React + TypeScript + Vite + Tailwind
- Local: WSL2 Ubuntu
- Production: Naver Cloud Ubuntu
- Process: systemd

PostgreSQL, Docker, Kubernetes 등을 임의로 도입하지 않는다.

---

## 5. Broker Rule

실제 Kiwoom API는 Stage 10 전까지 구현하지 않는다.

그 전에는:

- FakeMarketDataProvider
- ReplayMarketDataProvider
- SimBroker

를 사용한다.

Kiwoom 연결 시에도 기존 Strategy/Risk 코드 수정 없이 Adapter로 추가해야 한다.

---

## 6. Trading Safety Rules

- Human APPROVE != BUY
- 동일 종목 당일 재진입 금지
- Averaging Down 금지
- Pyramiding은 승자에만
- V1 Pyramiding 최대 1회
- Overnight 최대 1종목
- Gap Stress Rule 우선
- Safe Mode에서는 신규 진입 금지

---

## 7. Shadow Rules

- TOP8 전부 Shadow Trading
- Human APPROVE/REJECT와 무관하게 Shadow 계속
- Shadow와 Paper는 동일 Strategy/Risk 사용
- Sim 체결은 보수적으로 처리
- ambiguous bar를 기록
- 비용 차감 후 Net R을 공식 성과로 사용

---

## 8. GPT Research Rules

- GPT API 자동호출은 V1에서 구현하지 않는다.
- Prompt Generator와 JSON Import만 구현한다.
- Source 없는 핵심 claim을 신뢰하지 않는다.
- UNKNOWN을 허용한다.
- model / prompt_version / analysis_at을 기록한다.

---

## 9. Code Quality

- 타입힌트 사용
- 작은 함수 선호
- 명확한 Domain Model 사용
- 매직넘버는 config/constants로 이동
- Strategy 파라미터는 버전 관리 가능해야 한다.
- 변경한 전략 로직에는 테스트를 추가한다.
- 기존 테스트를 깨뜨리지 않는다.

---

## 10. Test Rules

각 작업은 최소:

- Unit Test
- Edge Case
- Determinism
- 기존 Regression

을 확인한다.

시장 관련 테스트는 가능한 한 고정 Fixture를 사용한다.

---

## 11. Git Rules

명시적 요청 없이는:

- commit 금지
- push 금지
- merge 금지
- rebase 금지

기존 미커밋 변경을 임의로 초기화하지 않는다.

`git reset --hard`, `git clean -fd` 등의 파괴적 명령은 사용자 승인 없이 금지한다.

---

## 12. File Rules

- 기존 파일을 이유 없이 전체 재작성하지 않는다.
- 불필요한 파일을 삭제하지 않는다.
- `.env`를 commit하지 않는다.
- Secret/API Key를 코드/로그/문서에 기록하지 않는다.

---

## 13. 작업 완료 보고

모든 작업 완료 후 반드시 아래 형식으로 보고한다.

### Git

- Branch
- HEAD
- Working tree 상태
- Commit/Push 수행 여부

### Changed

- 변경한 파일
- 주요 구현

### Tests

- 실행한 명령
- 통과/실패

### Design

- 중요한 설계 판단
- V1 Spec과의 정합성

### Issues

- 발견된 문제
- 미구현
- 다음 작업 주의점

### Scope

- V2 항목을 구현하지 않았는지 확인

---

## 14. Production 운영 규칙 (CRYPTO 포함)

운영 중인 실거래 화면과 실계좌가 걸려 있으므로 아래는 예외 없이 적용한다.

### 배포

- 운영 배포 직전 STOP하고 보고한다. 배포 자체가 승인된 작업이어도 배포 시점은 보고 후다.
- Next 빌드는 운영 서버에서 하지 않는다. 운영 호스트는 RAM과 디스크가 모두 빠듯하다.
  production-equivalent 격리 빌드를 다른 곳에서 만들어 산출물만 올린다.
- 빌드는 반드시 clean한 소스에서 한다. 공용 워크트리의 dirty 파일이 산출물에 섞이면
  다른 세션의 미완성 변경이 운영에 배포된다.
- 교체 전 이전 산출물을 rollback 경로로 보존한다.
- 무엇이 배포되어 있는지는 산출물과 런타임으로 판정한다. 서버의 git HEAD로 판정하지 않는다.
  재시작은 재빌드가 아니고, 백엔드는 repo가 아닌 런타임 복사본에서 돌 수 있다.

### 측정과 증명

- 렌더링하거나 실측하지 않고 "정상"이라고 단정하지 않는다.
- "없다"는 결과에는 반드시 대조군을 둔다. 있어야 할 문자열이 같은 방법으로 검출되는지
  먼저 확인하고, 그 다음에 없음을 주장한다.
- LIVE order path를 건드린 변경은 mutation 0을 증명한다. 집계는 같은 축으로 재측정한다.

### 손대지 않는 것

- 다른 세션이 수정 중인 파일은 읽기만 한다. 덮어쓰지 않고, 포인터 한 줄도 추가하지 않는다.
- `git add .`를 쓰지 않는다. 변경 파일을 명시해 staging하고 `git diff --cached`로 전수 확인한다.
- 다른 세션의 미push commit이 섞여 있으면 main을 push하지 않는다.
- `reset --hard`, `rebase`로 다른 세션의 history를 재작성하지 않는다.
- 공용 repo에서 `git stash`, `git checkout`으로 트리를 바꾸지 않는다. 비교는 `git show`로 한다.

### 기본값 OFF

- Market Context는 env 기본값 OFF를 유지한다. `NEXT_PUBLIC_MARKET_CONTEXT_BASE_URL`은
  별도 승인 없이 설정하지 않는다. 미설정이 곧 "기존 화면과 동일"이어야 한다.
- QA 전용 preview 컴포넌트의 mutation sentinel은 운영자의 주문을 막는다.
  운영자가 쓰는 route에 절대 올리지 않는다.
- crypto segment historical migration은 별도 승인 사항이다. 신규 segment 압축과 구분한다.
- exit-guard는 V2 전까지 운영에서 활성화하지 않는다. 단일 예외로 영구 비활성되는 구조이고
  rate-limit 문제가 남아 있다.
- AUTO, order, leverage, Safe MAX는 요청 범위 밖에서 바꾸지 않는다.

### 문서 역할 분리

- Instruction file(`AGENTS.md`) = 원칙
- `docs/crypto/CRYPTO_CURRENT_STATUS_*.md` = 현황, 수치, commit, 날짜
- `docs/crypto/CRYPTO_SERVER_RUNBOOK_V1.md` = 절차

서로 섞지 않는다. 수치가 원칙 파일에 들어가면 금방 낡고, 원칙이 현황 문서에 들어가면 묻힌다.
