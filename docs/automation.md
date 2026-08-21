# 자동화 인벤토리

> CLAUDE.md "Automation-first" 원칙(반복작업은 전부 cron/systemd timer/asyncio task)의
> 실제 현황 스냅샷. pm-ai-shipping 스킬의 `automation.md` 패턴 채택(2026-07-26) —
> 트리거/오너/승인게이트/kill-switch 를 한 표로 추적. 신규 자동화 추가 시 이 표에 한 줄
> 추가(같은 커밋). 상세 이력·설계 배경 = `CLAUDE_REFERENCE.md` §Automation-first.

## In-process asyncio task (bot/telegram_bot.py, 봇 프로세스 안에서 실행)

| 태스크 | 주기 | 담당 함수 | 하는 일 | Kill-switch / 승인게이트 |
|---|---|---|---|---|
| 지정가 체결 확인 + 씨시스 sync | 30분 | `_periodic_paper_pending` | E0.5d 지정가 도달 시 체결(RiskGate 통과 필요) + KIS 잔고 reconcile + 트레이드 씨시스 MFE/MAE·청산 감지 | `risk_gate.halt_active()`(TRADING_HALT 파일) — 매수만 차단, 매도/청산은 항상 허용 |
| 메모리 pending 항목 정산 | 12시간 | `_periodic_auto_resolve` | 분석 판정의 5거래일 결과 자동 채점(재분석 없이도 정확도 통계 누적) | 없음(읽기 전용 채점) |
| market.html 재생성 | 30초 | `_periodic_market_refresh` | 홈 대시보드 실시간 신선도 | 없음 |
| 신고저/급등락/상한가 pre-warm | 매일 07:30·16:30 KST | `_periodic_highlow_prewarm` | 첫 방문자 대기 0 위해 캐시 미리 채움 | `/yfpause` `/naverpause` 로 소스별 일시정지 |
| 신고저 슬롯 스캔(in-process 폴백) | 15분 슬롯 | `_periodic_highlow_scan` | `highlow-scan.timer`(별도 프로세스)가 활성이면 자동 skip(이중스캔 방지) | 위와 동일 + 타이머 활성 감지 가드 |
| 경량 동적 보드 워머 | 180초(`LIGHT_BOARD_WARM_SEC`) | `_periodic_light_board_warm` | 무버·KR52주·장전후·NXT·테마 페이지 렌더 호출(방문 시뮬)로 파일캐시 채움 | 장시간+pause 게이트, `_LIGHT_WARM_RUNNING` 재진입 가드 |
| 카드 알람 발송 | 60초 | `_periodic_reminders` | 예약 시각 도달 알람 텔레그램 발송, 미확인 시 다음날 재발송 | `CHANNEL_CHAT_IDS` 미설정 시 스킵 |
| 대시보드 분석/실행 요청 스풀 | 5초 | `_periodic_dashboard_requests` | 대시보드 버튼 클릭 → 텔레그램 채널과 동일 실행 경로 | `CHANNEL_CHAT_IDS` 미설정 시 요청 소비만(게시 안 함) |
| 관심종목 DART 알림 | 75초 | `_periodic_dart_fav_alerts` | `dart-feed.timer`(별도 프로세스) 아카이브를 스캔해 신규만 알림 | 영구 seen-set(중복 알림 차단) |
| marketcap.html 재생성 | 3시간 | `_periodic_marketcap` | 글로벌 시총 순위 갱신 | 없음 |
| FRED 계열 보드 재생성 | 6시간 | `_periodic_fred_boards` | PPI/CPI/유동성/시장타이밍/경제캘린더 동시 갱신(각 try 분리, 한쪽 실패해도 나머지 진행) | `FRED_API_KEY` 부재 시 해당 보드만 graceful 빈 상태 |
| 자정 대시보드 전체 재생성 | 매일 00:01 KST | `_periodic_dashboard_refresh` | index.html 등 일일 전면 재생성 | 없음 |

## systemd timer — bot/ (VM `~/stock`)

| Timer | 주기 | 실행 | 하는 일 |
|---|---|---|---|
| `dart-feed.timer` | 1분(OnUnitActiveSec) | `bot.dart_feed` | DART 공시 준실시간 수집(당일 3p 증분+시간당 4일 풀스캔) |
| `dart-feed-backlog.timer` | 5분 | `bot.dart_feed --backfill-pending` | 미파싱 항목 점진 백필 |
| `highlow-scan.timer` | 매시 00/15/30/45 | `bot.highlow_scan` | 52주 신고저 슬롯 스캔(봇 재배포와 무관하게 독립 프로세스) |
| `trend-precompute.timer` | 평일 16:30 KST | `bot.screener_precompute` | Minervini 추세템플릿(KR 시총상위) 캐시 워밍 |
| `daily-byte.timer` | 평일 19:00 KST | `bot.daily_kr_flow` | 한국 수급 Daily Byte |
| `daily-byte-weekly.timer` / `-us.timer` | 일 22:00 KST | `bot.daily_kr_weekly` / `bot.us_market_weekly` | 주간 요약(한/미) |
| `us-market-daily.timer` | 평일 08:00 KST | `bot.us_market_daily` | 미국 마감 브리프 |
| `cheongyak-byte.timer` | 평일 10·14시 KST | `bot.cheongyak_brief` | 청약 피드 |
| `realestate-byte.timer` | 금 09:00 KST | `bot.realestate_brief` | 부동산 주간 |
| `realestate-byte-monthly.timer` | 매월 1일 09:00 KST | `bot.realestate_monthly` | 부동산 월간 |
| `screener-gics-check.timer` | 분기(3/6/9/12월 5일 09:00) | `bot.screener_gics_check` | GICS 분류 정합성 점검 |
| `blog-watch.timer` | 30분 | `bot.blog_watch` | 감시 블로그 신규글 수집 |
| `reddit-insider-watch.timer` | 1분 | `bot.reddit_insider_watch` | 레딧 인사이더 감시 |
| `portfolio-watch.timer` | 2분 | `bot.portfolio_watch` | 업로드 포트폴리오 변동 감시 |
| `watchlist-check.timer` | 30분 | `bot.watchlist` | 조건 알림(rsi/price/sma/52w/earnings) 체크 |
| `stock-bot-update.timer` | 1분 | `deploy/auto-update.sh` | git 폴링 → 코드 변경 시 재배포(base 브랜치만 감시, **squash merge = 배포**) |
| `stock-bot-watchdog.timer` | 1분 | `deploy/watchdog.sh` | 12분 무응답 시 봇 재시작(180초 polling-hang + `.busy` marker 이중 체크 — 무거운 작업은 `_busy_acquire`/`_busy_release` 필수) |

## systemd timer — trade/ (VM `~/stock-trade`, 독립 체크아웃)

| Timer | 주기 | 실행 | 하는 일 |
|---|---|---|---|
| `trade-bot-update.timer` | 1분 | `deploy/trade-auto-update.sh` | 같은 base 브랜치 추적, 독립 재배포 |
| `trade-bot-watchdog.timer` | 1분 | `deploy/trade-watchdog.sh` | 무응답 재시작 |
| `trade-bot-customs-probe.timer` | 10분 | `trade.scripts.scan_customs --if-changed` | 관세청 변경 감지 스캔 |
| `trade-bot-prov-fetch.timer` | 월 1-3/11-13/21-23일 30분 | `trade.scripts.fetch_provisional` | 잠정치 수집(발표 몰린 기간 집중) |
| `trade-bot-daily-digest.timer` | 매일 00:03 KST | `trade.scripts.daily_digest` | 일일 다이제스트 |
| `trade-bot-dart-revenue.timer` | 매월 18일 05:00 KST | `trade.dart_revenue --refresh` | DART 매출 데이터 갱신 |
| `trade-bot-dart-reparse.timer` | 매일 04:30 KST | `trade.dart_revenue --reparse-stale --budget 1000` | 실패분 재파싱(예산 캡) |
| `trade-bot-catalog-guard.timer` | 매월 18일 09:00 KST | `trade.scripts.catalog_guard` | HS↔회사 매칭 카탈로그 정합성 가드 |
| `trade-bot-curation.timer` | 매월 1/11/15/21일 18:00 KST | `trade.scripts.curation_candidates` | 큐레이션 후보 생성(운영자 확인 대기) |
| `trade-bot-badonion-sync.timer` | 6시간 | `trade/scripts/backfill_badonion.py` | 배도니언 소스 백필 동기화 |
| `trade-bot-dashboard-refresh.timer` | 5분 | `ingest_inbox`→`purge_ignored`→`fetch_provisional --if-stale`→`build_krx_codes --if-stale`→`fetch_quotes`→`trade.dashboard`→`resolve_check --if-due` | **inbox.jsonl → DB → 화면**. 나쁜양파/BeOn 데이터가 대시보드에 오르는 유일한 경로 |
| `trade-bot-unstored-check.timer` | 매일 00:00 KST | `trade.scripts.unstored_check` | 캡션이 store 에 안 들어간 건 감지 → 텔레그램(성공 시 무음) |
| `trade-bot-health.timer` | 1시간 | `trade.scripts.health_check` | 휴면·사이클 갭 감지 |
| `trade-bot-customs-fetch.timer` | 4×/일(UTC 00:30·04:30·08:30·16:30) | `fetch_customs`→`customs_alert`→`scan_customs`→`industry_report --store`→`refresh_signals`→`fetch_provisional`→`trade.dashboard` | 관세청 확정치 수집·급변 스캔·산업 집계 |
| `trade-bot-backup.timer` | 매일 03:00 KST | `trade/scripts/backup_store.sh` | store.db 일일 스냅샷 |
| `trade-bot-dashboard-audit.timer` | 매일 08:10 KST | `trade.scripts.dashboard_audit --notify` | 대시보드 표면 감사 + 유닛 드리프트 점검 → 텔레그램 |

## 상시구동 리스너 (Telethon userbot, timer 아님 — Restart=on-failure 데몬)

> 2026-08-02 감사에서 발견: 아래 5개 중 `daju-listener` 는 이번 세션에 추가하며
> 이 표 갱신을 빠뜨렸고(자동화 원칙 위반), `trade-bot-beon-listener` +
> `trade-bot-beon-sync.{service,timer}` 는 코드(`daily_digest.py`·
> `listen_beon.py`·`trade-auto-update.sh`)가 전부 이름으로 참조하는데도
> **`deploy/` 에 파일 자체가 없었다**(VM 에 수동 설치만 되고 repo 미반영 추정).
> 형제 유닛(badonion) 패턴으로 재구성했으나, VM 실제 유닛과 대조 확인 필요
> (`systemctl cat trade-bot-beon-listener`).
>
> **2026-08-21 같은 사고 재발** — VM 에서 도는데 `deploy/` 에 없던 유닛이
> **5쌍** 더 있었다: `dashboard-refresh`·`unstored-check`·`health`·
> `customs-fetch`·`backup`. 그중 `dashboard-refresh` 는 inbox→DB→화면의
> **유일한 경로**이고 `unstored-check` 는 그게 멈춘 걸 알려줄 안전망이라,
> `install-trade-units.sh` 로 VM 을 재구축하면 데이터가 조용히 안 오르고
> 그 사실도 안 알려지는 상태가 된다. VM 실물(`systemctl cat`)을 그대로
> 복제해 위 표에 등재했다.
> 재발 방지는 규율이 아니라 **자동 점검**이다 —
> `trade.scripts.unit_drift_check` 가 `dashboard-audit` 틱마다 VM 유닛과
> `deploy/` 를 대조해 한쪽에만 있는 걸 보고한다.

| 서비스 | 소스 | 하는 일 | Kill-switch |
|---|---|---|---|
| `daju-listener.service` | `bot.daju_watch` | DAJU(다주) 실적 예정 알림 실시간 포워드 → 블로그 대시보드 아카이브 | 세션 미인증(exit 78) → RestartPreventExitStatus 로 hot-loop 방지 |
| `trade-bot-beon-listener.service` | `trade.scripts.listen_beon` | BeOn_BeClear(대만·중국·일본 수출통계) 실시간 forward | 위와 동일 패턴 |
| `trade-bot-beon-sync.timer`(2h) | `trade.scripts.backfill_beon` | 리스너 다운타임 안전망(--lookback-days 2 기본) | 없음(idempotent 재스캔) |
| `trade-bot-badonion-listener.service` | `trade.scripts.listen_badonion` | 나쁜양파(태국·말련·필리핀·멕시코 등) 실시간 forward | 세션 미인증(exit 78) |
| `trade-bot-badonion-sync.timer`(6h) | `trade/scripts/backfill_badonion.py` | 위 안전망 | 없음 |

## 원칙 위반 시 재설계 규칙
운영자가 같은 명령을 두 번 이상 반복 실행해야 하는 fix 는 잘못된 fix — 우선순위
in-process scheduler > systemd timer > cron > (일회성만) 수동. 새 반복작업 추가 = 이 표에
한 줄 + 실제 자동화 메커니즘 구현이 같은 커밋.

- **수주잔고 파서 리뷰** (`_periodic_backlog_review`, telegram_bot) — 격주 금요일 16:00 KST. `dart_backlog._MISS_LOG` 에 쌓인 '파서가 값을 못 낸 사유'를 요약해 텔레그램 발송. **새 미스가 없으면 무음**(격주 무음 알림은 곧 무시된다). 격주 판정은 ISO 주차 짝수 + 금요일 + 16시 — 상태 파일 없이 시각만으로 결정돼 봇 재시작에 흔들리지 않는다. 수신자는 DART 공시알림 chat_id 재사용.
