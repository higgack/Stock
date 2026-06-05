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
- **비목표(초기):** 신용/마진, 공매도, 선물·옵션, **타인 계좌·동일 브로커
  복수 계좌**(시장별 멀티 브로커 라우팅과는 다름 — §2), 완전 무인 무한
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

## 2. 멀티 브로커 라우터 — 시장별 어댑터 (핵심 결정)

브로커는 **교체·추가 가능한 단일 pluggable 어댑터**다(§3). 위 계층(Policy·
Risk Gate·Order Manager·Ledger·Reconcile·Notify·Dashboard)은 전부 브로커-
무관 → 브로커 교체 = 새 어댑터 1개 작성. **여러 브로커 동시 운용**도 가능
하므로 "전 시장"은 시장별로 최적 어댑터에 라우팅한다.

| | KIS (한국투자증권) | IBKR (Interactive Brokers) | 토스 Open API |
|---|---|---|---|
| 우리 통합 | **이미 됨**(시세·수급) | 0 | 0 (미오픈) |
| **모의/페이퍼** | **모의투자 도메인** ✅ | **paper account** ✅ | 없음 |
| 시장 | KR + 미·일·홍콩·중(SH/SZ)·베트남 | **글로벌 ~전 시장**(EU·TW 포함) | KR + US |
| 약점 | **TW·EU 미지원** | 외국인 적격요건(TW FINI/CN Stock Connect) | 미오픈·계좌한정 |

**시장 → 브로커 라우팅 (초안):**

| 시장 | 1차 브로커 | 비고 |
|---|---|---|
| KR | **KIS** (모의 ✅) | 첫 어댑터 · E0 페이퍼 |
| US/JP/HK/CN/VN | KIS 해외 **또는** IBKR | 비용·편의로 택일 |
| **TW** | **IBKR** | KIS 미지원. FINI 외국인 등록 필요 |
| **EU** (독·프·북유럽…) | **IBKR** | KIS 미지원 |

→ **권고:** KR = **KIS**(모의투자로 E0~E2 를 돈 없이 검증 — 실전 전환은
도메인+scope flip), 글로벌 나머지 = **IBKR**(유니버설 어댑터, paper 계좌
보유). **토스**는 오픈 후 KR+US secondary. 라우터가 `(시장 → 어댑터)`
테이블로 주문을 분배하고, 어댑터가 없는 시장은 fail-closed(거부).

⚠️ **외국인 접근 규제 캐비엇(소프트웨어가 아닌 현실 벽):** TW(FINI 등록)·
CN A주(Stock Connect 적격)·일부 EU 는 개인 외국인 **직접 매매가 제약**된다.
브로커가 있어도 체결 불가일 수 있으며, 그 경우 대안은 **ADR(미국 상장)로
우회**. 즉 *아키텍처는 전 시장 first-class 지원하되, 실제 체결 가능 여부는
시장별 규제에 종속* — 라우터는 미지원·미적격 시장을 명시적으로 거부한다.

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
[Broker Router]  (시장 → 어댑터 라우팅 테이블; 미지원 시장 fail-closed)
        ├─ KIS Adapter   ── PAPER(모의) / LIVE(실전)
        ├─ IBKR Adapter  ── PAPER / LIVE   (TW·EU·글로벌)
        └─ Toss Adapter  ── (오픈 후, KR+US)
        │   ※ 어댑터마다 동일 인터페이스 · 페이퍼/실전은 모드로 분기
        ▼
[Ledger]  포지션·평단·실현/미실현 P&L (우리 뷰)
        │        ▲
        │   [Reconcile Loop]  주기적 broker 상태 동기 → drift 시 HALT
        ▼
[Notify(텔레그램) · Dashboard(positions/orders/P&L/audit) · Audit Log(append-only)]

         [Kill-switch / Circuit Breaker]  ── 전 계층 위에서 즉시 차단
```

각 모듈 단일 책임. **Broker Router + 어댑터만 브로커별로 다르고 나머지는
universal.** 새 브로커 = 같은 어댑터 인터페이스 구현 1개 + 라우팅 테이블 1줄.

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

1. **브로커 라우팅**: KR=KIS(모의 ✅, 첫 어댑터)·TW/EU=IBKR·US/JP/HK/CN=
   KIS 해외 또는 IBKR 중 택 — 이 라우팅으로 동의? IBKR 유니버설 어댑터 우선?
2. **시장 범위**: 전 시장 목표(라우터)가 기본. 단 1차 구현은 어느 시장부터
   (KR? US?)? 외국인-제약 시장(TW/CN A)은 ADR 우회 허용?
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
