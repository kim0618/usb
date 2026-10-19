# F2 EXISTING-SEC-AUTHORITY RECOVERY AUDIT V1

네트워크 0(socket 차단 상태 실행, 운영 서버는 ssh 읽기 전용 복사만), prereg 무수정(C0/C1 3baea825, C1 V2 4b88cd17).

FINAL = F2_EXISTING_DATA_INSUFFICIENT (원인: coverage가 아니라 PIT 접수시각 authority)

- C0 coverage 재현: 89,005 / 245,149 = 36.306% (일치)
- 기존 자산 합산 coverage: C0 규칙 245,149 / 245,149 = 100.00%, 수집시각이 창 이후인 것만 인정하면 240,617 / 245,149 = 98.15% (Strategy C-E0 submissions 2,339 CIK이 결정적)
- PIT FAIL: 같은 accession의 acceptanceDateTime이 수집본마다 정확히 ±4h/±5h(ET 오프셋) 다름. 8-K 2.02 31,532건 중 2,834건(8.99%) 충돌, 1,160건(3.68%)은 장중/창안 판정이 뒤바뀜. 충돌 없는 건도 3.92%가 ET-as-Z 시그니처 시간대(ET 01~04시, 11~12시). EDGAR 헤더 대조(40건)로도 C-E0 값 38건 UTC 일치, 2건 ET 일치. 어느 원천도 PIT-clean 아님(C0 BASE 포함).
- Item 2.02 파서 PASS: 창 안 8-K items 100% 정형, substring 오탐 0, 공백 처리 차이 0, 헤더 40/40 일치(2.02 포함 14건 전부 일치), AMD 8건 확인.
- H_AUTO_LOCAL은 원장 6,130개 중 본문 73개(ledger≠body), 커버리지 기여는 본문만 셈.
