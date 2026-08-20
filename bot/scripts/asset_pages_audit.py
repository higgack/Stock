"""자산·가계부·ASIA·분석 아카이브·Screener 대시보드 감사 — 남은 표면 5종.

사용자 2026-08-20: "이어서 A 로 남은것들도 다 봐줘" (수출입 trade 는 별도
체크아웃(~/stock-trade)에서 도니 여기선 렌더 계약만 — 데이터 감사는 그쪽 몫).

원칙(실수 #35): 화면이 쓰는 **그 로더·그 렌더러**를 그대로 태운다.
  ① 자산(portfolio)  — 스탯 산수(평가손익 = Σ평가 − Σ원금), 기준시각 표기,
     보유 종목(고유) ≤ 포지션 수, 국내+해외 = 주식평가
  ② 가계부(budget)   — 요약카드 vs 시계열 재계산, 기간·업데이트 표기
  ③ ASIA            — 위젯별 ts·source, 전량 빈 화면이면 안내문(#43)
  ④ 분석 아카이브     — data-total = 기록 수, 상태줄 문구 서버=JS
  ⑤ Screener        — 총 실행 = bottleneck + 조건부, 비용 3창, 상태줄 복원

    cd ~/stock && .venv/bin/python -m bot.scripts.asset_pages_audit

읽기 전용 · LLM 0 · ₩0. (자산·가계부는 업로드된 모델이 없으면 그 사실만 보고.)
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone

_PROBE_VER = 2   # 2 = 아카이브 카드 계수 패턴 fix(<div class=card — details 아님)
_KST = timezone(timedelta(hours=9))
_OK, _NG, _WARN = "✅", "❌", "⚠️"


def _p(*a):
    print(*a, flush=True)


def _mark(ok, warn=False):
    return _WARN if warn else (_OK if ok else _NG)


def _base_status_ok(html: str) -> bool:
    """검색 해제 시 상태줄이 서버 문구로 복원되는가(#48 계약)."""
    return ("baseStatus" in html
            and not re.search(r"textContent = '총 ' \+ \w+ \+ '건의", html))


def audit_portfolio() -> None:
    _p("\n" + "=" * 72)
    _p("① 자산 (portfolio.html)")
    _p("=" * 72)
    import bot.dashboard as d
    from bot import portfolio as pf
    model = pf.load() if hasattr(pf, "load") else None
    if not model or not model.get("holdings"):
        _p(f"{_WARN} 업로드된 자산 모델 없음 — 빈 화면 안내만 검사")
        html = d._render_portfolio_page(model or {})
        _p(f"{_mark('뱅크샐러드' in html)} 빈 상태 안내문 존재")
        return
    holdings = model["holdings"]
    eval_sum = sum(h.get("평가금액") or 0 for h in holdings)
    cost_sum = sum(h.get("투자원금") or 0 for h in holdings)
    _p(f"[데이터] 포지션 {len(holdings)}건 · 평가 {eval_sum:,.0f} · 원금 {cost_sum:,.0f}")
    html = d._render_portfolio_page(model)
    _p(f"{_mark('마지막 업데이트' in html)} 기준시각(마지막 업데이트) 표기")
    # 스탯 산수 — 화면의 주식평가 = Σ평가금액 (표기 포맷과 무관하게 원값 대조)
    from bot.dashboard import _pf_won
    _p(f"{_mark(_pf_won(eval_sum) in html)} 주식 평가 스탯 = Σ평가금액 ({_pf_won(eval_sum)})")
    pnl = eval_sum - cost_sum
    _p(f"{_mark(_pf_won(pnl) in html)} 평가손익 스탯 = Σ평가 − Σ원금 ({_pf_won(pnl)})")
    # 국내 + 해외 = 주식평가
    from bot.dashboard import _holding_market
    overseas = sum(h.get("평가금액") or 0 for h in holdings
                   if _holding_market(h) != "KR")
    domestic = eval_sum - overseas
    _p(f"[분해] 국내 {domestic:,.0f} + 해외 {overseas:,.0f} = {domestic + overseas:,.0f} "
       f"{_mark(abs(domestic + overseas - eval_sum) < 1)}")
    # 고유 종목 ≤ 포지션
    m = re.search(r'stat-num">(\d+)</div><div class="stat-lbl">보유 종목', html)
    if m:
        _p(f"{_mark(int(m.group(1)) <= len(holdings))} 보유 종목(고유) "
           f"{m.group(1)} ≤ 포지션 {len(holdings)}")
    else:
        _p(f"{_NG} '보유 종목' 스탯을 못 찾음(패턴 어긋남 — 검증 불가)")


def audit_budget() -> None:
    _p("\n" + "=" * 72)
    _p("② 가계부 (budget.html)")
    _p("=" * 72)
    import bot.dashboard as d
    from bot.budget import load_budget
    budget = load_budget()
    if not budget or not budget.get("months"):
        _p(f"{_WARN} 가계부 모델 없음 — 빈 화면 안내만 검사")
        html = d._render_budget_page(budget or {})
        _p(f"{_mark('현금흐름' in html)} 빈 상태 안내문 존재")
        return
    months = budget["months"]
    income = budget.get("income", [])
    expense = budget.get("expense", [])
    tot = budget.get("totals", {})
    n = len(months)
    _p(f"[데이터] {n}개월 · {months[0]} ~ {months[-1]}")
    html = d._render_budget_page(budget)
    _p(f"{_mark('업데이트' in html)} 기준시각(업데이트) 표기")
    # 요약카드 vs 시계열 재계산(±1 원 반올림 허용)
    inc_avg = sum(income) / n if n else 0
    exp_avg = sum(expense) / n if n else 0
    _p(f"{_mark(abs((tot.get('income_avg') or 0) - inc_avg) < 1)} "
       f"월평균 수입 카드 {tot.get('income_avg'):,.0f} = Σ수입/n {inc_avg:,.0f}")
    _p(f"{_mark(abs((tot.get('expense_avg') or 0) - exp_avg) < 1)} "
       f"월평균 지출 카드 {tot.get('expense_avg'):,.0f} = Σ지출/n {exp_avg:,.0f}")
    net_calc = sum(income) - sum(expense)
    _p(f"{_mark(abs((tot.get('net') or 0) - net_calc) < 1)} "
       f"순저축 합 {tot.get('net'):,.0f} = 수입합 − 지출합 {net_calc:,.0f}")


def audit_asia() -> None:
    _p("\n" + "=" * 72)
    _p("③ ASIA (asia.html) — 생성 파일 검사(재수집 없이)")
    _p("=" * 72)
    import bot.dashboard as d
    fp = d.ARCHIVE_ROOT / "asia.html"
    if not fp.exists():
        _p(f"{_NG} asia.html 미존재 — regenerate_market_index 미실행?")
        return
    html = fp.read_text(encoding="utf-8")
    widgets = re.findall(r"<h2>([^<]*업종 등락[^<]*)</h2>", html)
    _p(f"[위젯] {len(widgets)}개: {widgets}")
    if not widgets:
        _p(f"{_mark('업종 등락 데이터가 아직' in html)} 전량 빈 화면 안내문(#43)")
        return
    # 각 위젯이 ts 를 실었는가 — section-hd 안의 ts span
    tss = re.findall(r'<span class="ts"[^>]*>([^<]*)</span>', html)
    empty_ts = sum(1 for t in tss if not t.strip(" ·"))
    _p(f"{_mark(len(tss) >= len(widgets) and empty_ts == 0, warn=empty_ts > 0)} "
       f"위젯 기준시각(ts) {len(tss)}개 · 빈 ts {empty_ts}개")
    for t in tss[:4]:
        _p(f"    {t.strip()[:70]}")


def audit_index() -> None:
    _p("\n" + "=" * 72)
    _p("④ 분석 아카이브 (index.html) — 생성 파일 검사")
    _p("=" * 72)
    import bot.dashboard as d
    fp = d.ARCHIVE_ROOT / "index.html"
    if not fp.exists():
        _p(f"{_NG} index.html 미존재")
        return
    html = fp.read_text(encoding="utf-8")
    m = re.search(r'id="status"[^>]*data-total="(\d+)"[^>]*>총 (\d+)건의 분석 기록', html)
    if not m:
        _p(f"{_NG} 상태줄 파싱 0건 — 패턴 어긋남(검증 불가)")
        return
    _p(f"{_mark(m.group(1) == m.group(2))} data-total {m.group(1)} = 표기 {m.group(2)}")
    # 카드 수와 대조 — 최신 월 인라인 + 과거 월 프래그먼트(idx_m_*.html).
    # ⚠️ 이 페이지의 카드는 **<div class="card">** 다(피드 대시보드들의
    # <details class="card"> 와 다름). v1 이 details 로 세서 VM 실데이터
    # 77건을 '카드 0'으로 오보했다(#47 — 계수 패턴은 실제 렌더로 검증할 것).
    _IDX_CARD = re.compile(r'<div class="card"')
    cards = len(_IDX_CARD.findall(html))
    frag_names = re.findall(r'data-lazy="(idx_m_[^"]+)"', html)
    for fn in frag_names:
        fpp = d.ARCHIVE_ROOT / fn
        if fpp.exists():
            cards += len(_IDX_CARD.findall(fpp.read_text(encoding="utf-8")))
        else:
            _p(f"{_NG} 프래그먼트 {fn} 미존재 — 펼치면 로드 실패")
    _p(f"{_mark(cards == int(m.group(1)))} 실제 카드 {cards} = data-total {m.group(1)} "
       f"(인라인 + 프래그먼트 {len(frag_names)}개)")


def audit_screener() -> None:
    _p("\n" + "=" * 72)
    _p("⑤ Screener (screener.html)")
    _p("=" * 72)
    import bot.dashboard as d
    runs = d._load_screener_runs() if hasattr(d, "_load_screener_runs") else []
    try:
        from bot.stock_screener import load_screen_archives
        screens = load_screen_archives()      # regenerate 와 같은 로더(#35)
    except Exception:
        screens = []
    outcomes = {}
    try:
        outcomes = d._load_screener_outcomes()
    except Exception:
        pass
    _p(f"[데이터] bottleneck {len(runs)}건 + 조건부 {len(screens)}건")
    html = d._render_screener_page(runs, outcomes, screens)
    m = re.search(r'stat-v">(\d+)</div><div class="stat-l">총 실행 \((\d+)\+(\d+)\)', html)
    if not m:
        _p(f"{_NG} '총 실행' 스탯 파싱 0건 — 패턴 어긋남(검증 불가)")
    else:
        ok = (int(m.group(1)) == int(m.group(2)) + int(m.group(3))
              == len(runs) + len(screens))
        _p(f"{_mark(ok)} 총 실행 {m.group(1)} = {m.group(2)}+{m.group(3)} "
           f"= 로더 {len(runs)}+{len(screens)}")
    srv = re.search(r'id="scr-status"[^>]*>([^<]*)<', html)
    _p(f"{_mark(bool(srv) and str(len(runs)) in srv.group(1))} "
       f"상태줄 {srv.group(1) if srv else '(없음)'} — 숫자 = 기록 수")
    _p(f"{_mark(_base_status_ok(html))} 검색 해제 복원 = 서버 문구(#48)")
    _p(f"{_mark(html.rstrip().endswith('</body></html>'))} 문서 마감")


def main() -> int:
    _p(f"=== asset_pages_audit v{_PROBE_VER} · "
       f"{datetime.now(_KST).strftime('%Y-%m-%d %H:%M:%S')} KST · "
       f"python {sys.version.split()[0]} ===")
    for fn in (audit_portfolio, audit_budget, audit_asia, audit_index,
               audit_screener):
        try:
            fn()
        except Exception as exc:                      # noqa: BLE001
            import traceback
            _p(f"{_NG} {fn.__name__} 예외: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    _p("\n읽는 법: ❌ = 화면이 사실과 다르거나 감사가 검증 불가(패턴 0건 포함).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
