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
        hits.append(f"[{section}] {txt}" if section else txt)
    return hits


def sweep(include_weekly: bool = False) -> dict:
    """감사 실행 → {"findings": [...], "warn": int, "errors": [...], "raw": str}.

    `include_weekly=False` 면 주간 감사(무거운 것)는 건너뛴다.
    findings 가 비면 호출자는 **아무것도 보내지 않는다**."""
    findings: list[str] = []
    errors: list[str] = []
    warn = 0
    chunks: list[str] = []
    for name, module, cadence in AUDITS:
        if cadence == "weekly" and not include_weekly:
            continue
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
    return {"findings": findings, "warn": warn, "errors": errors,
            "raw": "\n".join(chunks)}


def report_text(result: dict | None = None,
                include_weekly: bool = False) -> str:
    """텔레그램 HTML 본문. **이상 없으면 빈 문자열**(= 무음)."""
    r = result if result is not None else sweep(include_weekly)
    bad = list(r.get("errors") or []) + list(r.get("findings") or [])
    if not bad:
        return ""
    now = datetime.now(_KST).strftime("%Y-%m-%d %H:%M")
    head = (f"🔍 <b>대시보드 감사</b> · {now} KST\n"
            f"❌ {len(bad)}건"
            + (f" · ⚠️ {r.get('warn', 0)}건은 사람 확인 대상" if r.get("warn") else "")
            + "\n\n")
    body: list[str] = []
    for line in bad:
        s = _h.escape(line)
        body.append(f"• {s}")
        if sum(len(x) for x in body) > _TG_CAP - len(head) - 80:
            body.append(f"… 외 {len(bad) - len(body) + 1}건 "
                        "(<code>python -m bot.audit_sweep</code> 로 전문)")
            break
    return head + "\n".join(body)


def main() -> int:
    import sys
    logging.basicConfig(level=logging.WARNING)
    # 수동 실행은 기본이 전량(주간 포함) — 사람이 직접 돌릴 땐 다 보고 싶다.
    r = sweep(include_weekly="--daily" not in sys.argv)
    print(r["raw"])
    print("\n" + "=" * 72)
    txt = report_text(r)
    print(txt or "✅ ❌ 0건 — 알림 없음(무음)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
