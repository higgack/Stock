"""피드형 대시보드 4종 전수 감사 — Daily Byte · 레딧 · 블로그 · 부동산(청약 포함).

사용자 2026-08-20: "Daily bite, 레딧, 블로그, 부동산 대시보드도 똑같이 꼼꼼히
모든 걸, 코드부터 로직, 대시보드 디스플레이 등등 점검해줘."

네 화면은 로더(아카이브 스캔) → 렌더(월>일>카드) → 공용 JS(_DAILY_BYTE_JS)
라는 **같은 뼈대**를 쓴다. 그래서 감사도 한 함수로 돌린다 — 한 화면에서 나온
결함이 나머지에도 있는지 즉시 보이도록(실수 #38: 같은 병이 화면마다 따로 산다).

보는 것:
  ① 데이터  — 날짜 결측·`ts` 날짜와 폴더 날짜 불일치·미래 날짜·중복 파일
  ② 카운트  — 총건수 = Σ(월 헤더) = Σ(일 헤더) = 실제 카드 수 (프래그먼트 포함)
  ③ 기준시각 — "이거 최신이야?"에 화면이 답하는가(규칙 10b·실수 #43)
  ④ 라벨    — 서버가 찍은 상태문구 vs 검색 해제 시 JS 가 덮어쓰는 문구
  ⑤ 배선    — data-total · 삭제 API · 문서 마감(갱신배너 주입 요건)
  ⑥ 비용    — 대시보드 카드(아카이브 합) vs 메인 집계(usage 로그) 대조

    cd ~/stock && .venv/bin/python -m bot.scripts.feed_boards_audit

읽기 전용 · LLM 0 · ₩0.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

_PROBE_VER = 3   # 3 = 청약 단독 페이지 제거 반영(부동산에 합쳐서 검증)
_KST = timezone(timedelta(hours=9))
_OK, _NG, _WARN = "✅", "❌", "⚠️"

_CARD_RE = re.compile(r'<details class="card"')
_DAY_RE = re.compile(r'<details class="day"[^>]*>\s*<summary class="day-head">'
                     r'\s*<span>📅 ([^<]+)</span>\s*<span class="count">(\d+) 건</span>')
_MONTH_RE = re.compile(r'<details class="month"[^>]*>\s*<summary class="month-head">'
                       r'\s*<span>📆 ([^<]+)</span>\s*<span class="count">(\d+) 건</span>')


def _p(*a):
    print(*a, flush=True)


def _mark(ok: bool, warn: bool = False) -> str:
    return _WARN if warn else (_OK if ok else _NG)


def _day_card_counts(html: str) -> list[tuple[str, int, int]]:
    """(날짜라벨, 헤더가 주장하는 건수, 그 그룹 안 실제 카드 수)."""
    out = []
    ms = list(_DAY_RE.finditer(html))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(html)
        out.append((m.group(1), int(m.group(2)),
                    len(_CARD_RE.findall(html, m.end(), end))))
    return out


def _audit_surface(name: str, runs: list[dict], render, *,
                   date_key: str = "_date", ts_key: str = "ts",
                   cost_key: str | None = "cost_krw",
                   expect_asof: bool = True,
                   del_api: str | None = None) -> None:
    _p("")
    _p("=" * 72)
    _p(f"{name}")
    _p("=" * 72)
    if not runs:
        _p(f"{_NG} 기록 0건 — 아카이브 경로 확인")
        return
    dates = sorted({str(r.get(date_key) or "") for r in runs})
    _p(f"[데이터] {len(runs)}건 · {len(dates)}일 · {dates[0]} ~ {dates[-1]}")

    # ① 데이터 무결성
    blank = sum(1 for r in runs if not (r.get(date_key) or "").strip())
    _p(f"{_mark(not blank)} 날짜(_date) 결측 {blank}건 "
       "(빈 키는 라벨 없는 월/일 그룹을 만든다)")
    today = datetime.now(_KST).date().isoformat()
    fut = sorted({d for d in dates if d > today})
    _p(f"{_mark(not fut)} 미래 날짜 {len(fut)}건 {fut[:5]}")
    mism = [(str(r.get(date_key)), str(r.get(ts_key) or "")[:10])
            for r in runs
            if (r.get(ts_key) and str(r[ts_key])[:10]
                and str(r[ts_key])[:10] != str(r.get(date_key) or ""))]
    _p(f"{_mark(not mism)} ts 날짜 ≠ 폴더 날짜 {len(mism)}건 {mism[:4]} "
       "(카드에 찍히는 날짜 라벨이 실제 시각과 갈리는 자리)")
    dup = [k for k, n in Counter(
        (str(r.get(date_key)), str(r.get("_filename") or "")) for r in runs).items()
        if n > 1]
    _p(f"{_mark(not dup)} (날짜,파일명) 중복 {len(dup)}건 {dup[:3]}")
    nots = sum(1 for r in runs if not r.get(ts_key))
    _p(f"{_mark(not nots, warn=bool(nots))} ts 결측 {nots}건 "
       "(카드 시각이 빈칸으로 뜬다)")

    # ⑥ 비용 — 대시보드 카드가 쓰는 아카이브 합계
    if cost_key:
        _m = today[:7]
        _t = sum(r.get(cost_key, 0) or 0 for r in runs
                 if str(r.get(date_key)) == today)
        _mo = sum(r.get(cost_key, 0) or 0 for r in runs
                  if str(r.get(date_key) or "").startswith(_m))
        _tot = sum(r.get(cost_key, 0) or 0 for r in runs)
        _p(f"[비용] 아카이브 합 오늘 ₩{_t:,.0f} · 이번달 ₩{_mo:,.0f} · 누적 ₩{_tot:,.0f}")

    # 렌더
    res = render(runs)
    html, frags = res if isinstance(res, tuple) else (res, {})
    _p(f"[렌더] {len(html):,}자 · 프래그먼트 {len(frags)}개")
    # DAJU·관계후보 섹션은 검색바 **앞**에 오고 같은 details.month/day 클래스를
    # 쓴다 — 카드 스트림만 보도록 상태줄 이후로 자른다(안 자르면 오검출).
    _i = html.find('id="scr-status"')
    stream = html[_i:] if _i >= 0 else html
    all_text = [stream] + list(frags.values())

    cards = sum(len(_CARD_RE.findall(t)) for t in all_text)
    _p(f"{_mark(cards == len(runs))} 실제 카드 {cards} = 기록 {len(runs)}")

    days = [x for t in all_text for x in _day_card_counts(t)]
    if not days:
        _p(f"{_NG} 일자 헤더 파싱 0건 — 감사 패턴이 렌더와 어긋남(검증 불가)")
    else:
        bad = [x for x in days if x[1] != x[2]]
        _p(f"{_mark(not bad)} 일자 헤더 = 그 날 실제 카드 수 "
           f"({len(days)}일 중 {len(bad)}일 불일치) {bad[:4]}")
        _p(f"{_mark(sum(x[1] for x in days) == len(runs))} Σ(일 헤더) "
           f"{sum(x[1] for x in days)} = 기록 {len(runs)}")

    months = [x for t in all_text for x in _MONTH_RE.finditer(t)]
    if not months:
        _p(f"{_NG} 월 헤더 파싱 0건 — 감사 패턴이 렌더와 어긋남(검증 불가)")
    else:
        msum = sum(int(m.group(2)) for m in months)
        _p(f"{_mark(msum == len(runs))} Σ(월 헤더) {msum} = 기록 {len(runs)} "
           f"({len(months)}개월)")

    # ④ 라벨 — 검색을 지웠을 때 상태줄이 **서버 문구 그대로** 돌아오는가
    srv = re.search(r'id="scr-status"[^>]*>([^<]*)<', html)
    srv_txt = (srv.group(1) if srv else "").strip()
    wired = ("const baseStatus = (sts.textContent || '').trim();" in html
             and "sts.textContent = baseStatus;" in html)
    hard = re.findall(r"sts\.textContent = '총 ' \+ \w+ \+ '건의 ([^']*)'", html)
    _p(f"{_mark(wired and not hard)} 검색 해제 시 상태줄 = 서버 문구 복원 "
       + ("" if not hard else f"— JS 에 하드코딩된 문구 {hard} 잔존"))
    _p(f"      서버 문구: {srv_txt!r} (기록 {len(runs)}건과 일치해야)")
    _p(f"{_mark(str(len(runs)) in srv_txt)} 서버 문구의 숫자 = 기록 수")
    if del_api:
        _p(f"{_mark(del_api in html)} 삭제 API 배선 {del_api}")
        if del_api != "api/daily_byte_delete":
            _p(f"{_mark('api/daily_byte_delete' not in html)} 남의 삭제 API"
               "(api/daily_byte_delete) 누수 없음")
    _p(f"{_mark(html.rstrip().endswith('</body></html>'))} 문서 마감 "
       "(</body></html> — 갱신배너 주입 요건)")

    # ③ 기준시각
    if expect_asof:
        stats = re.findall(r'<div class="stat-v">([^<]*)</div>\s*'
                           r'<div class="stat-l">([^<]*)</div>', html)
        asof = [v for v, l in stats if "마지막" in l or "최신" in l]
        _p(f"{_mark(bool(asof))} 기준시각 stat: "
           + (", ".join(f"{l}={v}" for v, l in stats if "마지막" in l or "최신" in l)
              or "(없음 — 화면이 '이거 최신이야?'에 답을 못 한다)"))
        _p("      전체 stat: " + " · ".join(f"{l}={v}" for v, l in stats))
        # '마지막 항목' 만으론 조용한 것과 죽은 것이 구별 안 된다 —
        # 수집기 점검 시각이 같이 있어야 화면이 답을 한다(실수 #43).
        chk = [l for _v, l in stats if "점검" in l]
        _p(f"{_mark(bool(chk), warn=not chk)} 수집기 점검 표기 "
           + (chk[0] if chk else "(없음 — 고정 스케줄 화면이면 정상)"))


def main() -> int:
    _p(f"=== feed_boards_audit v{_PROBE_VER} · "
       f"{datetime.now(_KST).strftime('%Y-%m-%d %H:%M:%S')} KST · "
       f"python {sys.version.split()[0]} ===")
    import bot.dashboard as d

    _audit_surface("1) Daily Byte", d._load_daily_byte_runs(),
                   d._render_daily_byte_page, del_api="api/daily_byte_delete")

    _audit_surface("2) 미국 레딧 게시물 분석", d._load_reddit_insider_runs(),
                   d._render_reddit_insider_page, cost_key=None, del_api=None)

    _audit_surface("3) 블로그", d._load_blog_runs(),
                   d._render_blog_page, cost_key=None, del_api="api/blog_delete")

    _re_runs = d._load_realestate_runs()
    _ch_runs = d._load_cheongyak_runs()
    for r in _re_runs:
        r.setdefault("_kind", "realestate")
    for r in _ch_runs:
        r["_kind"] = "cheongyak"
    # 청약은 **부동산 화면 안에서만** 산다(단독 페이지 2026-08-20 제거).
    _audit_surface("4) 부동산(실거래 + 청약)", _re_runs + _ch_runs,
                   d._render_realestate_page, del_api="api/realestate_delete")
    _p(f"      구성: 실거래 {len(_re_runs)}건 + 청약 {len(_ch_runs)}건")

    _p("")
    _p("읽는 법: ❌ = 화면이 사실과 다른 것(고쳐야 함) · ⚠️ = 사람 확인 대상.")
    _p("        '파싱 0건'은 '이상 없음'이 아니라 감사 실패다(실수 #41·#47).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
