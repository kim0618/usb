# Host Resource Runbook V1 (trader-j, 961MB RAM, no upgrade)

RAM 증설을 하지 않는 동안의 운영 절차다. 원칙은 `AGENTS.md` 14절(운영 호스트 조회와 정리)에 있고,
이 문서는 시간대와 명령만 다룬다. 근거 수치는 2026-10-13 HOST RESOURCE AUDIT V1 / OPTIMIZATION V1.

## 1. RESOURCE-SENSITIVE WINDOW

**평일 16:40~22:40 KST** (usb-e-paper 실행 창)

- `usb-e-paper.timer`는 `Mon..Fri 03:45 America/New_York`이다. KST로는 DST 중 16:45, 표준시(11/1 이후) 17:45.
  표준시 기간에는 창을 **17:40~23:40 KST**로 읽는다. 기준은 ET이고 KST는 환산값이다.
- 2026-10-12 실측: e-paper 16:45:05~22:34:43 KST, MemoryMax 350M, 창 동안 MemAvailable 평균 99MB, 최저 75MB.
  평상시(220~245MB)의 절반 이하다.

이 창에서 하지 않는다:

| 금지 | 이유 |
|---|---|
| frontend build (`npm run build`, `next build`) | Next 15 빌드는 1.5~2GB. 9/8 OOM으로 VM 재시작 |
| 대규모 rsync, scp 업로드 | page cache와 IO가 paper/q123를 swap으로 밀어낸다 |
| tar/zip/gzip 대용량 작업 | 같은 이유 |
| 연구 replay, 백테스트, pytest 전체 | CPU 1코어, 상주 서비스와 경쟁 |
| broad filesystem scan (`find`, `du`, 재귀 grep) | 9월·10월 사고 이력. known path에서도 창 밖에서 |
| preview 서버 다중 실행, 운영 UI 탭 여러 개 | crypto 터미널 탭 하나가 7종 API(state, performance, ledger, trades, sizing, pnl-breakdown, hot)를 반복 호출한다(아래 3절) |

창 안에서 꼭 해야 하면: `cat /proc/pressure/memory`의 `avg10`을 먼저 보고, `ionice -c3 nice -n19`로 건다.

## 2. 정해진 시각에 도는 것 (KST, DST 기준)

| 시각 | unit | 비고 |
|---|---|---|
| 매분 :05 / :10 / :20 / ~:58 | structure / flow / manual-research / feedlog | 60초 유지. feedlog는 정체 감지 센서 |
| 10:15 | usb-crypto-liqfwd-daily | 평소 20초. 10/13 정체 때 3분 52초 |
| 13:20 / 13:40 / 13:55 / 14:10 | e-universe / grouped-daily / splits / h-forward | |
| 13:35~약 14:40 | e-rvol 일일 append (약 15,000 페이지) | timer화 후 `usb-e-rvol-once.timer` 00:35 ET |
| 16:45~22:35 | **usb-e-paper** | RESOURCE-SENSITIVE WINDOW |

## 3. 알려진 압박 원인

- **운영 UI를 여는 것 자체.** 2026-10-13 11:18 crypto 터미널 탭이 열리자 paper uvicorn CPU 40%,
  io PSI some 85~98, frontend 메모리 15→41MB. paper가 MemoryMax 200M에 붙어 있어 요청이 몰리면
  자기 cgroup 안에서 회수·swap이 일어난다. 창 안에서는 탭을 하나만 연다.
- swap 700MB 안팎은 상시 상태다. 문제 신호는 swap 크기가 아니라 PSI `io full`과 1분 타이머 timeout이다.

## 4. 2026-10-13에 정리한 OS 데몬

| unit | 조치 | 원복 |
|---|---|---|
| fwupd.service | stop + mask (dbus 활성화 차단) | `systemctl unmask fwupd.service` (dbus 요청 시 자동 기동) |
| fwupd-refresh.timer / .service | disable + mask | `systemctl unmask fwupd-refresh.timer fwupd-refresh.service && systemctl enable --now fwupd-refresh.timer` |
| ModemManager.service | disable --now | `systemctl enable --now ModemManager.service` |
| udisks2.service | disable --now | `systemctl enable --now udisks2.service` |
| upower.service | stop (원래 disabled, dbus 활성화) | `systemctl start upower.service` |

근거: dbus 외부 클라이언트 0, 역의존 패키지는 fwupd→udisks2뿐, Naver 에이전트·cloud-init·qemu 설정 참조 0,
모뎀 0, legacy BIOS라 UEFI 펌웨어 갱신 불가. fwupd-refresh는 이 호스트에서 **매시간** 돌았고
fwupd 프로세스는 9/8 이후 최대 220MB를 기록했다.

## 5. 보류: multipathd

SAFE 판정(기본 blacklist가 `sd|dasd|nvme` 외 전부 제외라 `vda` 비관리, multipath 맵 0, root는 UUID로 `/dev/vda2`,
`multipath-tools-boot` 미설치라 initramfs 비포함, 직접 역의존 0). 상주 약 22MB(mlock, swap 불가).
**재부팅으로 검증할 수 있는 유지보수 창 전에는 disable하지 않는다.** 그때 명령:

```
systemctl disable --now multipathd.socket multipathd.service
# 재부팅 후: findmnt /, systemctl --failed, 모든 usb-* active 확인
# 원복: systemctl enable --now multipathd.socket multipathd.service
```
