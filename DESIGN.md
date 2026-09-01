# DESIGN.md — NOAH/trade 대시보드 디자인 시스템

> AI 에이전트가 새 대시보드 표면(HTML)을 만들 때 읽는 plain-text 설계 명세
> (awesome-design-md 패턴, 2026-07-10 도입). 목적: 인라인 CSS·다크/라이트
> 대시보드가 표면마다 제각각 되지 않게 토큰·패턴을 한 곳에 고정 — CSS 추측
> 삽질(CLAUDE.md 실수 #14) 감소. **자동 로드 아님**(CLAUDE.md 만 주입) —
> 대시보드/CSS 작업 전 참조. 실제 값은 `bot/dashboard.py` 가 소스오브트루스.

## 1. 색상 토큰 (CSS 변수 — 실측값)

전 대시보드는 `:root` 라이트 + `:root[data-theme="dark"]` 다크 2벌을 정의하고
`prefers-color-scheme` + 시간 테마(`_THEME_JS`, 19:00~07:00 KST 자동 다크,
60초 재체크)로 전환. **새 색은 리터럴 금지 → 아래 변수 사용.**

| 토큰 | 라이트 | 다크 | 용도 |
|---|---|---|---|
| `--bg` | `#f7f8f9` | `#0b0c0e` | 페이지 배경 |
| `--card` | `#fff` | `#141518` | 카드·패널 표면 |
| `--border` | `#e8e8ea` | `#26272b` | 구분선·테두리 |
| `--text` | `#282a30` | `#e2e3e6` | 본문 |
| `--muted` | `#8a8f98` | `#8a8f98` | 보조·라벨·기준시각 |
| `--accent` | `#5e6ad2` | `#7c84e8` | 링크·활성 필·강조 |
| `--pos` | `#059669` | `#10B981` | 상승·긍정(▲) |
| `--neg` | `#dc2626` | `#EF4444` | 하락·부정(▼) |
| `--neu` | `#8a8f98` | `#6B7280` | 중립(-) |

차트 라인 팔레트(라벨색): 보라 `#ab47bc`·파랑 `#42a5f5`·시안 `#26c6da`·
초록 `#66bb6a`/`#26a69a`·주황 `#f5a623`/`#ffa726`·자주 `#7e57c2`. 등락은
반드시 pos/neg 토큰(초록↑/빨강↓ — 한국 관습 아님, 글로벌 관습 유지).

## 2. 레이아웃 · 컴포넌트 패턴

### 2.1 블록 사전 — 용도 ↔ 컴포넌트 1:1

**장식으로 쓰지 말 것 — 이 표의 용도에 맞을 때만 쓴다.** 새 컴포넌트를 만들기
전에 같은 용도가 이 표에 있는지 먼저 보고, 없으면 여기 한 줄을 추가한 뒤 만든다
(클래스만 쓰고 CSS 를 안 정의하면 조용히 스타일이 빠진다 — 실수 #201).

| 용도 | 컴포넌트 | 쓰면 안 되는 경우 |
|---|---|---|
| 한 주제의 값+맥락 묶음 | `.card` | "강조하고 싶어서" 감싸기 — 카드는 경계지 강조가 아니다 |
| 단일 KPI 숫자 1개 | `.stat` / `_stat_card()` | 값이 여럿이면 표(타일 나열은 세로로 안 읽힌다) |
| 시계열 · 분포 | `_chart_card()` + `_svg_line_chart()` | 관측 3점 이하 — 표가 정확하다 |
| 여러 행 × 여러 열 비교 | 표(§2.2 표 규칙) | 한 열에 출처가 둘이면 금지(실수 #32) |
| 목록 좁히기(필터) | `.df-pill` · `.mc-pill` (활성 = `--accent`) | 링크 대용 — 외부링크는 `↗` 접미 |
| 읽기 전용 분류·상태 라벨 | `.badge` · `.df-badge-*` | 클릭될 것처럼 보이면 필로 바꿀 것 |
| 값의 기준 · 출처 · 산식 | `.si-note`(11px muted) | 본문 크기 각주(표보다 커 보인다) |
| 판정 실패 · 미확정 · 빈칸 사유 | `.df-badge-unp` 류 + `.si-note` 사유 | 사유 없이 빈칸만 — 침묵이 최악(실수 #43) |
| 같은 주제어의 다른 기준 | 라벨에 기준을 박은 `.badge`/`.si-note` | 기준 없는 같은 이름 두 개(실수 #34) |
| 긴 보조 내용 접기 | `.csec`(`<details>`, 상태 localStorage) | 기본 노출이어야 할 만큼 중요한 것 |
| 원시 데이터 내보내기 | `.csv-btn` · `.df-csv-btn` | 화면에 이미 다 보이는 표 |

### 2.2 컴포넌트 실측 스펙

- **wrap**: `max-width:1100px; margin:0 auto; padding:24px 16px` 중앙 컬럼.
- **nav**: `market.html`=홈 허브. 링크 = `<a>` + `·`/`|` 구분자, `--accent` 색.
  새 대시보드 = 해당그룹 nav + 홈 nav(CLAUDE.md §Help).
- **card** (`.card`, 최다 사용): `background:var(--card); border:1px solid
  var(--border); border-radius:12px; padding`. 그림자 최소(플랫).
- **stat 타일** (`_stat_card()` / `.stat`): 값 위(큰 굵은 tabular-nums) + 라벨
  아래(작은 muted). KPI 행은 `.dp-grid` 처럼 `repeat(auto-fit,minmax(Npx,1fr))`.
- **chart-card** (`_chart_card(title, legend, svg, foot)`): 제목 + 범례(색점+
  라벨) + 인라인 SVG 라인차트 + 하단 출처 foot. 차트 = `_svg_line_chart()`
  (의존성 0, 순수 SVG). `.chart-row` 는 `grid-template-columns:repeat(N,1fr)`,
  4카드+ 는 3열 wrap.
- **필(pill)** (`.mc-pill`·`.df-pill`): `border-radius:16px; padding:4px 12px`
  칩. 활성 = `--accent` 배경 흰 글씨. 외부링크 필은 `↗` 접미.
- **접기 섹션** (`.csec` = `<details>`): 제목(h2)이 `<summary>` = 제목 클릭
  토글. 상태 localStorage 유지(`_CSEC_JS`, 30분 자동반영에도 살아남음).
- **표**: `border-collapse; th 하단 2px border; td 하단 1px border(--border)`.
  숫자열 우측정렬(`text-align:right` — colspan 상세셀 누수 주의, 실수 #14).

## 3. 표기 규칙 (CLAUDE.md 실수 #10·#11 연장)

- 모든 시각 = **KST 명시계산**(서버 로컬타임 의존 금지). 위젯 하단 = 적용시각
  + 출처 라벨(렌더시각 아님). 예: `기준 2026-07-08 12:00 KST · 출처 X`.
- 숫자 = `font-variant-numeric:tabular-nums`(자리 흔들림 방지) + 콤마.
- 비용카드 = 오늘/이번달/누적 3창(KST) 전 대시보드 통일.
- HTML escape: 사용자입력·조건식 `<`/`>` → `&lt;`/`&gt;`(parse_mode HTML).

## 4. 반응형 · 접근성

- 모바일 폭(≤640/700px)서 그리드 열 축소(`repeat(2,1fr)` 또는 1열), 패딩·
  폰트 축소. 표는 `overflow-x:auto` 래퍼(가로 스크롤은 표 안에서만 — 페이지
  body 가로 스크롤 금지).
- 다크/라이트 둘 다 대비 확보(양쪽 토큰 정의 필수). 이미지 로고 = 흰 배경
  타일(`background:#fff` — 다크서 투명로고 뭉개짐 방지).

## 5. CSS 디버깅 함정 (실수 #14 — 샌드박스서 렌더 불가)

추측 반복 금지 → **생성 HTML 먼저 찍어 구조 확인**. 알려진 함정:
- `.ind-table td{text-align:right}` 가 colspan 상세셀로 누수(직속 표만 타깃).
- grid 를 `<td>` 직속에 두면 트랙 깨짐 → 블록 div 래퍼.
- `direction:rtl` 는 JS scrollLeft 와 충돌.
- 자식표 max-content 가 부모표 폭 늘림 → 직속표만 `width:100%`.
- 2회+ 같은 증상 = DOM/스크린샷 요청(curl 200 ≠ 브라우저 정상, 프록시).
