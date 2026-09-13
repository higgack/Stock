"""대시보드 감사 일괄 실행 → **이상이 있을 때만** 보고.

사용자 2026-08-20: "매번 할때마다 왜 새로운것이 나오지?" — 대부분은 새로
깨진 게 아니라 **그 표면을 처음 기계적으로 검사**해서 보인 것이었다. 그렇다면
사람이 물어봐야 도는 감사는 같은 일을 반복하게 만든다(자동화 원칙 위반).
봇이 매일 알아서 돌리고, ❌ 가 있을 때만 알린다.

⚠️ **깨끗한 날은 아무것도 안 보낸다.** 매일 오는 '이상 없음' 알림은 곧
무시되고, 그러면 진짜 신호가 왔을 때도 안 읽힌다(`_periodic_backlog_review`
와 같은 규율).

⚠️ **감사가 예외로 죽으면 그것도 ❌ 다.** 조용히 통과시키면 도구가 눈이 먼 걸
아무도 모른다 — 이번 세션에 board_audit 두 섹션이 정확히 그 상태였다(#54).

  cd ~/stock && .venv/bin/python -m bot.audit_sweep        # 수동 실행(전문 출력)
"""
from __future__ import annotations

import contextlib
import html as _h
import io
import logging
import re
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bot.audit_sweep")

_KST = timezone(timedelta(hours=9))
_TG_CAP = 3500          # 텔레그램 4096 UTF-16 상한 여유

# (표시명, 모듈경로, 주기) — 새 감사를 만들면 여기에 한 줄.
# ⚠️ 등록을 잊으면 그 표면만 조용히 빠진다 — 회귀가 `bot/scripts/*_audit.py`
# 전부가 여기 있는지 확인한다(실수 #24: 열거하되 **누락을 테스트가 잡게**).
#    실제로 이 테스트가 liquidity·peer_currency·macro_staleness 3개 누락을
#    바로 잡아냈다.
# 주기: "daily" = 매일 · "weekly" = 월요일만(무거운 것). peer_currency 는
# 671종목을 yfinance 로 두드려(재시도·냉각 포함) 매일 돌리면 라이브 봇의
# yfinance 예산을 잠식한다 — 통화 불일치는 자주 바뀌는 성질도 아니다.
AUDITS: tuple[tuple[str, str, str], ...] = (
    ("📋 DART · 🏆 Market cap", "bot.scripts.dart_mcap_audit", "daily"),
    ("📰 피드 대시보드 4종", "bot.scripts.feed_boards_audit", "daily"),
    ("🔗 밸류체인", "bot.scripts.valuechain_audit", "daily"),
    ("📊 차트보드 · 홈 표면", "bot.scripts.board_audit", "daily"),
    ("💧 유동성 배치·단위", "bot.scripts.liquidity_audit", "daily"),
    ("🕰 발표지표 신선도", "bot.scripts.macro_staleness_audit", "daily"),
    ("💱 피어 통화 불일치", "bot.scripts.peer_currency_audit", "weekly"),
    ("💼 자산·가계부·ASIA·아카이브·Screener", "bot.scripts.asset_pages_audit", "daily"),
    # FCF 는 한 종목이 **세 화면**에 실리고 원천이 둘(DART·yfinance)이라
    # 정의 차이로 갈릴 수 있다 — 사람이 눈으로 못 재는 값이라 기계가 잰다
    # (사용자 2026-08-21). 종목마다 스냅샷을 새로 받아 무거우므로 weekly.
    ("💵 FCF 정확도(3화면 교차)", "bot.scripts.fcf_audit", "weekly"),
    # PER 밴드 표는 각 행의 산수가 맞아도 **행 사이**가 어긋날 수 있다 —
    # 주가는 분할반영인데 EPS 가 as-reported 면 분할 시점에서 갈리고(LRCX),
    # 결산분기 복원이 어긋나면 회계연도말마다 TTM 이 붕괴한다(KLAC). 둘 다
    # 눈으로는 안 잡혀 사용자가 외부 사이트로 검산하고 있었다(2026-08-22).
    # 종목마다 스냅샷을 새로 받아 무거우므로 weekly.
    ("📉 PER 밴드 표(산수·분할·결산검산)", "bot.scripts.per_band_audit", "weekly"),
)

# 섹션 제목으로 볼 줄 — ❌ 가 **어느 화면**에서 났는지 붙여 주기 위해.
# 감사 도구 7종이 쓰는 제목 형태가 다 달라서(`1)` / `①` / `──` / `══`) 전부 받는다.
def _section_of(line: str) -> str | None:
    """제목 줄이면 제목, 아니면 None. (`❌` 줄은 호출부에서 이미 갈라져 온다.)

    ⚠️ **구분선만 있는 줄**을 제목으로 받으면 '[=] ❌ …' 같은 게 나온다(첫
    구현이 그랬다). 형태마다 걸러지는 이유가 다르다:
      · `====` / `----` — 아래 어느 모양 규칙에도 안 맞아 None
      · `──────` / `══════` — 접두 규칙엔 걸리지만 구분자를 벗기면 빈 문자열
    처음엔 구분선 정규식과 '마크(✅⚠️❓) 있는 줄 제외' 가드를 따로 뒀는데,
    앞의 것은 **지워도 동작이 안 변했고**(위 두 경로가 이미 처리) 뒤의 것은
    **해로웠다**: Market cap 축이 실패하면 헤더가
    `── Market Cap … ⚠️ stale(최신 수집 실패)` 라 마크를 달고 나오는데, 그걸
    건너뛰면 바로 아래 ❌ 가 **직전 섹션 이름으로 잘못 붙는다** — 하필 결함이
    난 그때. 지워도 동작이 안 변하는 가드는 안전한 게 아니라 안전해 **보이는**
    것이고, 실물을 안 보고 넣은 가드는 그보다 나쁠 수 있다."""
    s = line.strip()
    if not s:
        return None
    if re.match(r"^\d+\)\s", s) or re.match(r"^[①-⑳]\s", s):
        return s
    if s.startswith("──") or s.startswith("══"):
        return s.strip("─═ ").strip() or None
    return None


def _run_one(module: str) -> tuple[str, str]:
    """감사 하나 실행 → (stdout, 오류메시지). 예외는 삼키지 않고 문자열로."""
    buf = io.StringIO()
    try:
        mod = __import__(module, fromlist=["main"])
        with contextlib.redirect_stdout(buf):
            # main() 시그니처가 둘로 갈린다 — 인자 없는 것과 main(argv)
            # (peer_currency_audit 등). inspect 로 맞춰 부른다.
            import inspect
            if inspect.signature(mod.main).parameters:
                mod.main([])
            else:
                mod.main()
        return buf.getvalue(), ""
    except SystemExit:                      # sys.exit(0) 로 끝나는 스크립트
        return buf.getvalue(), ""
    except Exception as exc:                # noqa: BLE001
        log.warning("audit_sweep: %s 실행 실패: %s", module, exc)
        return buf.getvalue(), f"{type(exc).__name__}: {exc}"


def _findings(out: str) -> list[str]:
    """❌ 줄만 뽑되, 바로 위 섹션 제목을 붙여 어느 화면인지 알 수 있게."""
    section = ""
    hits: list[str] = []
    for raw in out.splitlines():
        ln = raw.rstrip()
        if not ln.strip():
            continue
        if "❌" not in ln:
            sec = _section_of(ln)
            if sec:
                section = sec[:60]
            continue
        # '읽는 법: ❌ = …' 류 **범례 줄**은 결함이 아니다(샌드박스 스모크에서
        # 범례가 결함으로 집계됐다). 범례는 항상 '❌ =' 꼴로 등호가 따라온다 —
        # 실제 판정 줄은 '❌ 날짜 2건' 처럼 바로 내용이 온다.
        txt = ln.strip()
        if re.search(r"❌\s*=", txt):
            continue
        # ⚠️ **`❌ … 0` 을 '계수 라벨' 이라 보고 거르지 말 것.** 2026-08-26 에
        # `❌ 불일치 0`(정상)을 걸러내려다 `❌ 카드 0개`(대조 0건 = 진짜 결함,
        # #54)까지 삼켰다 — 0 은 문맥에 따라 정상이기도 결함이기도 하므로
        # **문자열로는 못 가른다**(#65·#60 문자열이 아니라 구조로). 해법은
        # 생산부다: 판정 글자를 계수 라벨로 쓰지 않는다(board_audit 관심종목).
        hits.append(f"[{section}] {txt}" if section else txt)
    return hits


# 지문에 넣을 **우리 패키지**. `startswith("bot")` 로 재면 `boto3` 처럼
# 접두가 겹치는 남의 패키지까지 문다 — 경계는 `bot` 자신이거나 `bot.` 이다.
_PKG = "bot"


def audit_fingerprint(modules: tuple[str, ...] | list[str] | None = None) -> str:
    """이 결산을 만든 **코드의 지문**(sha1 앞 10자) — 실수 #365.

    ⚠️ 왜 필요한가(2026-09-13 실측): 사용자가 아침 결산 `❌ 3건` 을 붙여
    줬는데 판정 줄에 시리즈명이 없었다 — 그건 #356 이 **그날 14:44 에
    배포하며 고친** 바로 그 증상이다. 08:13 실행이 배포 전 코드였던 것인데,
    **출력만 봐선 나도 사용자도 그걸 못 가른다**. 나는 코드를 태워 재고서야
    갈랐고(#360 설명이 둘이면 둘 다 재라), 그 재기 전까지 "내 fix 가 안
    먹었나" 와 "옛 코드다" 가 같은 화면이었다.
    #364 가 `blog_watch --check` 에 같은 배너를 심은 바로 그 이유이고,
    #359 가 "배너를 만들면 **어느 화면에 뜨나**를 그 자리에서 답하라" 고
    적은 그 사각이다 — 매일 아침 읽는 이 결산엔 없었다.

    ⚠️ 모집단은 **등록된 감사 전부**(`AUDITS`)이지 이번에 돌린 것이 아니다.
    옛 판은 `ran` 을 해싱해 **코드가 그대로여도 월요일**(주간 감사 3종 추가)
    에 지문이 달라졌고, 수동 실행(기본 주간 포함)은 평일 결산과 **영원히
    안 맞았다**(독립 리뷰 실측 `a635f0d916` vs `a5b30fcd29`). 그러면
    "지문이 다르면 낡은 코드" 라는 계약이 매주 거짓이 된다 — 지문은
    **코드만의 함수**여야 한다(#34 한 값이 두 뜻을 겸하면 한쪽은 거짓말).

    ⚠️ 깊이는 **전이 폐포**다(#364d 과대 주장 금지). 한 단계만 훑던 옛 판은
    25개를 덮고 **63개를 놓쳤다** — 예: 매크로 카드 판정 문구를 만드는
    `naver_marketindex` 가 빠져 그걸 고친 배포에 지문이 안 변했다. 지금은
    **154개**·1.7초로, 분(分) 단위 감사에 견주면 공짜다(실측).
    ⚠️ `from bot import feed_health` 형태는 `n.module` 이 `bot` 이라 **서브
    모듈이 안 잡힌다** — `board_audit` 하나가 그 형태로 제품 모듈 10여 개를
    끌어오는데 옛 판은 `bot/__init__.py` 만 해싱했다. 별칭까지 후보로 넣어
    푼다(함수 이름이면 `find_spec` 이 못 찾고 조용히 넘어간다).

    ⚠️ **못 보는 축**(#274): 이건 **디스크**를 잰다. 장수 프로세스가 새
    코드를 못 올린 채(배포 후 재시작 실패) 돌면 결산은 **옛 메모리 코드**로
    만들어지는데 지문은 새것을 찍는다 — 신호가 뒤집히는 자리다. 그래서
    `main()` 배너가 `code_freshness.drift()` 로 **프로세스 축을 따로**
    말한다(둘은 다른 사실이다, #45). 손 bump 가 아니라 소스 해시인 이유는
    #119(규율은 여섯 번 졌다).
    """
    import ast
    import hashlib
    import importlib.util
    import pathlib          # ⚠️ 빠뜨리면 아래 NameError 가 except 에 먹혀
                            # **이름만 해싱한 상수 지문**이 나온다(실측: 감사
                            # 모듈을 고쳐도 지문 불변 = 눈먼 가드, #12·#91b).
    missing: list[str] = []
    if modules is None:
        modules = tuple(m for _n, m, _c in AUDITS)

    def _src(mod: str, *, required: bool) -> bytes:
        """모듈 소스 바이트. 못 찾으면 b"".

        ⚠️ `required` 는 **우리가 덮기로 약속한 것**(등록 감사·이 모듈)에만
        참이다. 의존으로 **발견된** 이름은 모듈이 아닐 수 있다 —
        `from bot.fcf import dart_capex` 의 `dart_capex` 는 함수다. 그걸
        `missing` 으로 세면 영구 `?` 가 붙어 커버리지 문제가 아닌 상시
        경보가 된다(#25·#260).
        """
        try:
            spec = importlib.util.find_spec(mod)
            if spec and spec.origin:
                return pathlib.Path(spec.origin).read_bytes()
            if spec is not None:
                # 네임스페이스 패키지(`bot.scripts` 는 `__init__.py` 가 없다)
                # 는 소스가 없는 게 정상이다.
                return b""
        except Exception:                                      # noqa: BLE001
            pass
        if required:
            missing.append(mod)
        return b""

    # **전이 폐포** — 큐로 훑는다(한 단계만 보면 63개를 놓쳤다, 위 ⚠️).
    def _ours(name: str) -> bool:
        return name == _PKG or name.startswith(_PKG + ".")

    seen: dict[str, bytes] = {}
    required = {__name__, *modules}
    todo = list(required)
    while todo:
        m = todo.pop()
        if m in seen:              # ⚠️ `setdefault(k, _src(k))` 는 인자를
            continue               # **먼저 평가**해 같은 파일을 몇 번씩
        b = _src(m, required=m in required)   # 읽는다(실측 25개 모듈에 56회,
        seen[m] = b                # 1.1MB `dashboard.py` 를 10번). 먼저 막는다.
        if not b:
            continue
        try:
            tree = ast.parse(b.decode("utf-8", "replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and _ours(n.module or ""):
                todo.append(n.module)
                # ⚠️ `from bot import feed_health` 는 `n.module` 이 `bot`
                # 이라 **서브모듈이 폐포에 안 들어온다** — `board_audit`
                # 하나가 이 형태로 제품 모듈 10여 개를 끌어오는데 옛 판은
                # `bot/__init__.py` 만 해싱했다(테스트가 잡았다, #87a).
                # 함수·상수 이름이면 `find_spec` 이 못 찾고 조용히 넘어간다.
                todo += [f"{n.module}.{a.name}" for a in n.names]
            elif isinstance(n, ast.Import):
                todo += [a.name for a in n.names if _ours(a.name)]
    h = hashlib.sha1()
    for name in sorted(seen):      # 순서를 고정해야 지문이 안정적이다
        h.update(name.encode())
        h.update(seen[name])
    # ⚠️ 못 읽은 소스가 있으면 **조용히 덜 덮은 지문을 내지 않는다** —
    # 그건 "이 지문이 전부를 덮는다" 는 과대 주장이 된다(#364·#54·#43).
    # `?` 접미로 읽는 쪽이 부분 지문임을 알게 한다.
    return h.hexdigest()[:10] + ("?" if missing else "")


def sweep(include_weekly: bool = False) -> dict:
    """감사 실행 → {"findings": [...], "warn": int, "errors": [...], "raw": str}.

    `include_weekly=False` 면 주간 감사(무거운 것)는 건너뛴다.
    findings 가 비면 호출자는 **아무것도 보내지 않는다**."""
    findings: list[str] = []
    errors: list[str] = []
    warn = 0
    chunks: list[str] = []
    ran: list[str] = []
    for name, module, cadence in AUDITS:
        if cadence == "weekly" and not include_weekly:
            continue
        ran.append(module)
        out, err = _run_one(module)
        chunks.append(f"───── {name} ({module}) ─────\n{out}")
        if err:
            # 감사가 죽은 것도 결함이다 — 조용히 넘어가면 도구가 눈이 먼 걸
            # 아무도 모른다(#12 silent-fail 금지 · #54).
            errors.append(f"{name}: 감사 실행 실패 — {err}")
            continue
        for f in _findings(out):
            findings.append(f"{name} {f}")
        warn += out.count("⚠️")
    # ⚠️ 지문은 `ran` 이 아니라 **등록 전부** 기준이다 — `ran` 으로 해싱하면
    # 코드가 그대로여도 월요일(주간 3종 추가)에 값이 달라져 계약이 거짓이
    # 된다(독립 리뷰 실측). 돈 개수는 **다른 사실**이라 따로 싣는다(#45).
    return {"findings": findings, "warn": warn, "errors": errors,
            "fp": audit_fingerprint(), "ran": len(ran),
            "raw": "\n".join(chunks)}


def report_text(result: dict | None = None,
                include_weekly: bool = False) -> str:
    """텔레그램 HTML 본문. **이상 없으면 빈 문자열**(= 무음)."""
    r = result if result is not None else sweep(include_weekly)
    bad = list(r.get("errors") or []) + list(r.get("findings") or [])
    if not bad:
        return ""
    now = datetime.now(_KST).strftime("%Y-%m-%d %H:%M")
    # ⚠️ 지문은 **두 표면 모두**에 — 텔레그램 결산과 `main()` 원문이
    # 갈리면 한쪽만 "어느 코드였나" 에 답한다(#359·#364 배너를 한 장에만
    # 달지 말 것). 사용자가 읽는 건 이 결산이다.
    fp = r.get("fp") or "지문불가"
    head = (f"🔍 <b>대시보드 감사</b> · {now} KST · 코드 {fp}\n"
            f"❌ {len(bad)}건"
            + (f" · ⚠️ {r.get('warn', 0)}건은 사람 확인 대상" if r.get("warn") else "")
            + "\n\n")
    body: list[str] = []
    for line in bad:
        s = _h.escape(line)
        body.append(f"• {s}")
        if sum(len(x) for x in body) > _TG_CAP - len(head) - 80:
            body.append(f"… 외 {len(bad) - len(body) + 1}건 "
                        "(<code>cd ~/stock && .venv/bin/python -m bot.audit_sweep</code> 로 전문)")
            break
    return head + "\n".join(body)


def main() -> int:
    import sys
    logging.basicConfig(level=logging.WARNING)
    # 수동 실행은 기본이 전량(주간 포함) — 사람이 직접 돌릴 땐 다 보고 싶다.
    r = sweep(include_weekly="--daily" not in sys.argv)
    # ⚠️ `len(AUDITS)` 를 그대로 적으면 `--daily` 에서 7종만 돌고도 "10종"
    # 이라 배너가 거짓말한다 — 모호함을 없애려고 만든 줄에서(#55).
    ran = r.get("ran")
    # ⚠️ 지문은 **디스크**를 잰다. 프로세스가 옛 코드를 들고 있으면 신호가
    # 뒤집히므로(배포 후 재시작 실패) 그 축은 따로 말한다(#45·#274).
    try:
        from bot.code_freshness import drift
        d = drift()
        proc = (f" · ⚠️ 이 프로세스는 소스보다 {d['lag_sec'] / 60:.0f}분 낡음"
                if d.get("stale") else
                ("" if d.get("measurable") else " · 프로세스 신선도 판정 불가"))
    except Exception as exc:                                   # noqa: BLE001
        proc = f" · 프로세스 신선도 판정 불가({type(exc).__name__})"
    print(f"# audit_sweep · 코드 지문 {r.get('fp') or '지문불가'}"
          f" · 감사 {ran if ran is not None else '?'}/{len(AUDITS)}종 실행"
          f"{proc}")
    print(r["raw"])
    print("\n" + "=" * 72)
    txt = report_text(r)
    print(txt or "✅ ❌ 0건 — 알림 없음(무음)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
