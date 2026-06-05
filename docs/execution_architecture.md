# NOAH 실거래 실행 서브시스템 — 설계 문서 (v0.1 draft)

> 상태: **설계 초안.** 구현 미착수. 본 문서는 "실제 매매" 연결의 안전
> 아키텍처·가드레일을 확정하기 위한 검토 대상이다. §12 결정이 닫히고
> 사용자가 단계별로 승인하기 전에는 어떤 실행 코드도 작성하지 않는다.

## 0. 위치와 전제

- **목적:** NOAH 분석 신호를 (궁극적으로) 실제 주문으로 연결하는 실행 계층을,
  **돈이 걸린 상태에서 안전하게** 운용하기 위한 아키텍처·가드레일을 확정한다.
- **⚠️ 스탠스 전환 전제:** 현재 CLAUDE.md 는 "**실행 아님 — 알림만 (교육
  스탠스 유지)**" 가 명문 원칙이다. 본 서브시스템은 그 원칙을 **의식적으로
  뒤집는** 결정이며, 구현 착수 전 그 정책 변경을 CLAUDE.md 에 명시적으로
  박는 것이 선결 조건이다. 조용히 진행 금지.
- **비목표(초기):** 신용/마진, 공매도, 선물·옵션, 다계좌, 완전 무인 무한
  자동매매. 전부 후순위 또는 영구 제외.

## 1. 불변 설계 원칙 (협상 불가)

1. **Fail-closed** — 어떤 불확실성(데이터 이상·가드 미확인·브로커 응답 모호)
   에서도 기본은 **주문 안 함**. 의심되면 멈춘다.
2. **Paper-first** — 기본 모드는 모의. 실거래는 명시적 opt-in 토글 + 단계
   통과 후에만.
3. **Human-in-the-loop (초기)** — 모든 주문은 텔레그램 confirm 필요. 자동은
   좁은 mandate 안에서만 후순위 허용.
4. **하드 한도는 코드로 강제** — 포지션/손실/노출 캡을 config 신뢰가 아니라
   코드 게이트로. 위반 시 거부.
5. **Idempotency** — 재시작·재시도에도 중복 주문 0. 모든 주문에 client-side
   idempotency key.
6. **Kill-switch** — 봇이 죽어도 작동하는 즉시 정지 수단(파일 플래그). 발동
   시 신규 주문 전면 차단.
7. **분석 ≠ 실행 분리** — NOAH 분석 엔진은 **절대 직접 주문하지 않는다.**
   신호만 emit 하고, 별도 실행 게이트웨이가 자체 가드를 거쳐 결정·제출한다.

## 2. 브로커 선택 — KIS 우선 (핵심 결정)

| | KIS (한국투자증권) | 토스 Open API |
|---|---|---|
| 우리 통합 | **이미 됨** (시세·수급 inquiry) | 0 (미오픈) |
| 주문 지원 | **있음** (현재 미사용) | 있음 (미오픈) |
| **모의투자(paper)** | **공식 제공** — 별도 도메인, 실전과 동일 API | 없음 |
| 시장 | KR + 해외 | KR + US |
| 가용성 | 지금 | 사전신청·계좌 한정 |

→ **권고: 실행은 KIS.** 결정적 이유는 KIS 모의투자 도메인(실거래와 동일한
API 표면, 가짜 돈). E0~E2 를 KIS 모의로 만들고, 실전 전환은 **도메인 + scope
flip** 만으로 같은 코드가 그대로 동작 → 실행 코드를 돈 없이 100% 검증 가능.
**토스는 오픈 후 secondary 어댑터**로 합류(US 단일창구 이점 시).

## 3. 컴포넌트 아키텍처

```
NOAH 분석/워치리스트/수동 /trade
        │  (Signal: ticker, side, 판정, entry/stop/target, horizon)
        ▼
[Policy 계층]  포지션 사이징 · horizon 적합성 · 중복 포지션 체크
        │  (Intended Order)
        ▼
[Risk Gate]   하드 한도 · price_sanity · corp-action freeze · 장운영시간
        │      ↘ 위반 → REJECT (사유 로그+알림)
        ▼
[Order Manager]  idempotent submit/modify/cancel · 상태기계
        │
        ▼
[Broker Adapter]  ── PAPER(KIS 모의) ──┐
                  └─ LIVE(KIS 실전/Toss) ┘   (동일 인터페이스, 모드로 분기)
        │
        ▼
[Ledger]  포지션·평단·실현/미실현 P&L (우리 뷰)
        │        ▲
        │   [Reconcile Loop]  주기적 broker 상태 동기 → drift 시 HALT
        ▼
[Notify(텔레그램) · Dashboard(positions/orders/P&L/audit) · Audit Log(append-only)]

         [Kill-switch / Circuit Breaker]  ── 전 계층 위에서 즉시 차단
```

각 모듈 단일 책임. Broker Adapter 만 브로커별로 다르고 나머지는 universal.

## 4. 주문 생명주기 (상태기계)

```
SIGNAL → PROPOSED → (confirm/auto-approve) → RISK_CHECKED → SUBMITTED
       → ACK → PARTIAL ⇄ FILLED → CLOSED
분기: REJECTED · CANCELLED · EXPIRED · ERROR(→reconcile)
```

각 전이는 audit 로그 1줄(불변). idempotency key 로 SUBMITTED 중복 차단.
재시작 시 미완결 주문은 reconcile 로 broker 실상태 재확인 후 복구.

## 5. 리스크 가드레일 (하드 한도) — 본체

| 한도 | 기본값(보수적) | 위반 시 |
|---|---|---|
| 거래당 최대 금액 | 총자본의 2% (절대 상한 ₩) | 거부 |
| 종목당 최대 비중 | 10% | 거부 |
| 동시 보유 종목 수 | 5 | 신규 거부 |
| 일일 손실 한도 | 총자본의 3% | **당일 전면 HALT** |
| 계좌 MDD 서킷브레이커 | -10% | **전면 HALT + 알림** |
| 가격 collar | last/지정가 ±3% 밖 주문 | 거부 |
| 슬리피지 캡 | 체결 추정 슬리피지 > 1% | 보류·재호가 |
| 주문 rate | 브로커 한도(~1/s) 내 | 큐잉 |
| 장운영/정지/단기과열 | 휴장·VI·거래정지 시 | 거부 |
| **corp-action freeze** | price_sanity / HARD GUARD 발동 종목 | **거부** |
| fat-finger | 수량·금액 sane bound 밖 | 거부 |
| reconcile mismatch | 우리 ledger ≠ broker | **HALT** |

핵심: 위 게이트는 **이미 가진 가드를 재사용** — `bot/price_sanity.py`(글리치),
corp-action HARD GUARD(감자/분할 freeze), 시장 캘린더(`bot/market_calendar.py`).
신규 제작은 자본/손실 캡과 서킷브레이커뿐.

## 6. 운영 모드 & 단계적 롤아웃

```
DISABLED(기본) → PAPER → SHADOW → LIVE_CONFIRM → LIVE_BOUNDED
```

- **E0 PAPER** (KIS 모의): 신호→모의주문→ledger→P&L. 실행 코드 전체를 돈
  없이 검증.
- **E1 SHADOW**: 실전 데이터 읽기전용 + 모의 체결 병행, 실주문 0. 인증·토큰·
  reconcile 검증.
- **E2 LIVE_CONFIRM**: 단일 종목·소액(예: 1주), 거래당 텔레그램 human 확인.
  실주문 첫 발.
- **E3 LIVE_BOUNDED**: 하드 캡 안에서 자동, 여전히 일일/MDD 서킷.
- **각 단계 go/no-go 기준**: 예) E0→E1 은 모의 N거래 무버그 + reconcile
  0-drift M일; E2→E3 은 confirm 거래 K건 정상 + 슬리피지 실측 ≤ 가정.

## 7. 보안 / 인증 / kill-switch

- 브로커 키는 **`.env` 에만** (코드·git·CLAUDE.md 금지) — DASHBOARD_PASSWORD
  와 동일 정책. 노출 시 회전.
- 가능하면 **읽기 token ↔ 거래 token 분리**(scope 최소권한). 거래 scope 는
  명시적 활성화.
- 토큰 갱신 규율(KIS/Toss): 만료 선제 재발급 + 401 1회 재시도(재귀 방지) +
  중앙 캐시.
- **kill-switch**: `~/.tradingagents/TRADING_HALT` 파일 존재 시 전 주문 차단.
  봇 외부(SSH·cron)에서도 touch 가능 → 봇이 죽어도 정지 보장. 텔레그램
  `/halt` 도 동일 플래그 set.

## 8. 기존 NOAH 자산 재사용 맵

| 필요 | 재사용 |
|---|---|
| 가격 fetch | `chart_data.fetch_chart_payload` / `auto_resolve` |
| 글리치·freeze 가드 | `price_sanity` + corp-action HARD GUARD |
| 거래일 만기 계산 | `bot/market_calendar.py` |
| 신호 트리거 | `watchlist`(edge-trigger) |
| 포지션 표시 | 자산 대시보드(`portfolio.html`) 패턴 |
| 비용/감사 로그 | `usage_tracker` jsonl 패턴 |
| 직렬화·watchdog 공존 | `_busy_acquire/release` |
| 판정 규율 | PM override discipline(실행도 같은 판정 따름) |
| horizon | 5거래일 — 그 밖 보유는 재평가 강제 |

## 9. 장애·복구

- **재시작 중 주문**: idempotency key + startup reconcile 로 broker 실상태
  기준 복구.
- **브로커 다운**: fail-closed(신규 0) + 알림.
- **부분 체결**: ledger 가 partial 추적, 잔량 정책(취소/유지) 명시.
- **reconcile drift**: 우리 ledger ≠ broker → 즉시 HALT + 사람 호출(자동
  보정 금지 — 돈이라 보수적).

## 10. 자동화 vs 사람 (automation-first 준수)

- **자동(plumbing)**: 토큰 갱신·reconcile 루프·EOD P&L·stop 감시·서킷
  브레이커 — asyncio/systemd.
- **사람(trigger)**: 초기엔 주문 발동 자체는 human confirm. "배관은 무인,
  방아쇠는 사람" → 단계적으로 mandate 확대.

## 11. 법적 · 면책

- 본인 계좌·본인 자금·개인용. 브로커 API ToS(KIS/토스 약관) 준수. audit
  로그 = 거래 기록(세무 대비). 교육 출발이나 실행은 사용자 본인 결정·책임 —
  대시보드/봇에 면책 명시.

## 12. 사용자 결정 필요 (open decisions)

1. **브로커**: KIS 우선(모의 있음) 동의? 토스는 secondary?
2. **시장**: 처음엔 KR 만? US 포함?
3. **자본·캡 수치**: 거래당 %·일일 손실%·종목당 비중·동시 종목 수 — §5
   기본값으로 갈지.
4. **트리거**: NOAH 판정 자동 신호 vs 수동 `/trade` 만(초기).
5. **horizon**: 5거래일 청산 강제 vs 보유 연장 허용.

---

### 부록 A — 권고 구현 순서 (착수 시)

1. **E0 페이퍼 엔진** (별도 작업, 토스 무관·리스크 0): NOAH 판정/워치리스트
   신호 → 모의 장부(다음 거래일 시가 체결 가정) → 포지션·평단·P&L·승률·MDD →
   `paper.html` + 텔레그램 `/paper`. 가격은 `chart_data`/`auto_resolve` 재사용.
   이것이 실행 골격에서 돈만 뺀 것 — KIS 실주문은 이 위에 어댑터로 얹는다.
2. KIS 모의투자 어댑터 (E0 의 체결을 KIS 모의 API 로 교체) → E1 reconcile.
3. Risk Gate + 캡 + kill-switch (E2 전 필수).
4. LIVE_CONFIRM (텔레그램 human 확인) → LIVE_BOUNDED.
