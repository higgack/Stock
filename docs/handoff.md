# 세션 인수인계 — 2026-09-18

> ⚠️ **이 파일은 규칙이 아니라 그 시점의 스냅샷이다.** 행동 규칙의 단일 출처는
> `CLAUDE.md`(+ `CLAUDE_REFERENCE.md` · `trade/CLAUDE.md`)이고, 이 파일과 충돌하면
> **CLAUDE.md 가 이긴다**. 아래 커밋 해시·수치는 작성 시점의 실측이므로 새 세션은
> **먼저 `git log origin/<base>` 로 지금 상태를 재고** 시작할 것(#12·#286).
> 여기 적힌 "미측정" 항목은 그 뒤 실측되면 이 파일이 아니라 CLAUDE.md 실수 항목이
> 갱신 지점이다.

## 1. 인수 시점 상태

- base `claude/stock-trading-automation-xqYf7` = `be3a3b3`
- dev `claude/awesome-newton-7j01su` = base 와 동일(#17 동기화 완료) · 미커밋 0
- 게이트 실측: `tests/` 4,550 · `bot/tests` 174 · `trade/tests` 1,248 · `make surface` ✅

직전 배포 3건:

| PR | 내용 | 실수 항목 |
|---|---|---|
| #1283 | 연계표 월례 가드 — 고아를 repo CSV / 런타임 오버레이로 갈라 처방을 따로 적는다 | #386 |
| #1284 | 수주잔고 격주 보고서에 **원문 발췌** 동봉(계수만으론 네 번 오진했다) | #387 |
| #1285 | 허용값 **키 귀속** + 급등·급락 **사유 채널** + 수주잔고 4열 롤링 표 | #388 |

## 2. 화면 체크포인트 (규칙 #11 — VM auto-update 1분 폴링 뒤)

| 화면 | 기대 |
|---|---|
| `/krvolume` | 값이 뜨거나, 빈 화면이면 리터럴 갈래 문구(`expected "dividend"` + "같은 주소가 다른 sortType 값으로는 행을 주므로 그 키의 스키마 전체가 아닙니다") |
| `/highlow` | 빈 화면이 `(잠시 후 다시 시도)` 가 아니라 **일시정지 / HTTP / 계약 변경 / 우리 필터(`_is_real_stock`) / 원천 0건** 중 하나를 이름으로 |
| 격주 수주잔고 보고서 | `형식미지원` 계수가 4열 롤링분만큼 감소 + 미스마다 원문 발췌 |

진단(전부 base 에 있음):

```
cd ~/stock && python3 -m bot.scripts.kr_board_probe
cd ~/stock && python3 -m bot.dart_backlog --explain <티커>
```

## 3. 먼저 결정이 필요한 것 — CLAUDE.md 예산 소진

작성 시점 **331,530자 / 상한 331,600 = 여유 70자**, `claude_md_fold` 접을 차례 **0개**.
즉 **새 실수 항목을 쓸 자리가 없다.** `tests/test_docs_consistency.py::
test_injected_rules_file_stays_within_budget` 의 처방이 "접을 게 없으면 상한을 올리지
말고 **새 항목 크기 정책**을 물을 것" 이므로 이건 사용자 결정 사항이다.

선택지(측정값 포함해 제시할 것): (a) 오래된 번호대를 REFERENCE 로 통째 이관하고 색인만
남기기 (b) 상한을 한 항목분(+900) 더 올리기 (c) `_ENTRY_CAP` 을 900→600 으로 조이기.

## 4. 미측정으로 남긴 축 (#274 — 고치지 않고 적어 둔 것)

1. **거래량 보드가 왜 zod 유니온 봉투를 받게 됐나** — 원천 스키마 변경인지, 우리가
   `fieldErrors` 둘째 사유를 버려서였는지 미측정. 다음 VM 실행의 사유 줄이 답한다.
2. **사유 채널은 급등·급락에만 배선** — 52주 신고저·상한가 보드는 여전히 값만 쓰는
   `_get_stocks` 라 빈 화면이 갈래를 말하지 않는다(#38).
3. `_parse_rolling` 이 `_balance_matches` 의 수주 문맥 게이트(#109)를 안 거치고
   `_parse_xbrl` 보다 먼저 시도된다 — 표본 1개짜리 우선순위 변경.
4. 키 귀속은 `k: v` 부분이 하나라도 있으면 **귀속 없는 열거를 버린다**(의도한 #32
   맞교환이고 `learn_fail_reason` 이 그 사실을 말하지만, 원천이 키 이름을 안 적기
   시작하면 테마 프로브가 조용히 0종이 된다).
5. `size_cap_from` 의 `_LE_RE` 200자 창 — 열거를 앞세운 뒤 긴 사유가 앞에 오면 상한을
   못 읽는다(오늘 봉투는 필드당 사유가 하나라 잠복).
6. `expected_literal` 의 파이프 목록 가드는 유일한 호출부 조건상 **발화 경로가 없다**
   — 방어용으로만 남겼다(#291·#373).
7. **PPIACO ❌ 원인 미확정**(#374b) — 창 차이는 원인이 될 수 없다는 것까지만 확정.
   다음 일일 결산이 갈래를 스스로 말한다(#82).
8. **NXT/KRX 체결 귀속**(#373b·#375) — 필드로는 못 가르고 창으로만 하한을 잰다.
   NXT 거래종목 목록 원천은 이름을 추측해 배선하지 않았다(#151·#345).
9. 팔라듐 네이버 코드 미측정(#367) · 대만 上櫃 업종맵 지수 백오프 실측 대기(#384).

## 5. 새 세션 시작 절차

```bash
cd ~/Stock                                              # 샌드박스는 /home/user/Stock
git fetch origin claude/stock-trading-automation-xqYf7  # Copilot 커밋 확인(규칙 (a))
git log origin/claude/stock-trading-automation-xqYf7 --oneline -5
git checkout claude/awesome-newton-7j01su
```

## 6. 이 환경에서 실측한 주의사항

- **`.venv` 가 없다** → `python3 -m pytest`. 게이트는 3트리 별도 프로세스 + `make surface`.
- 샌드박스 네트워크 차단 실측: 네이버(403) · treasury.gov(ProxyError) · badonion ·
  yfinance. **"원천이 안 준다"와 "내가 못 받는다"를 대조군 없이 단정 금지**(#143).
- **GitHub MCP 정상 동작**(PR 생성·ready·squash merge 전부 성공) — 시도 없이 '불가' 로
  판단 금지(§브랜치·배포).
- 뮤테이션 규율: 백업은 **green 이 된 뒤** `*.FIXED` 로 뜨고 복원은 파일 복사 +
  `md5sum -c`(#139·#278·#358) · scratch 스크립트 이름에 **주제+타임스탬프**(#383) ·
  리뷰·뮤테이션이 파일을 변형하는 동안 커밋 금지(#328).
- 독립 리뷰(`/code-review`)가 이번 라운드에 **뮤테이션 38종 중 13종 생존**을 실측했다
  — 비자명 변경엔 반드시 돌릴 것(§Pre-commit 8).
