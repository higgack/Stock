"""금융사 '매출' 계정 진단 — 총액(영업수익)이 왜 안 잡히는지 원문으로 본다.

사용자 2026-08-19(NH투자증권 005940.KS): "매출보다 영익이 더 나오는데 이거
맞는지도 봐줘." 화면의 매출 자리에 **이자수익**(구성요소)이 들어가 영업이익률
117.7% 같은 불가능한 숫자가 나왔다. 총액 계정(영업수익)이 원문에 있는데 우리가
못 잡은 것인지, 애초에 공시되지 않은 것인지 **원문으로** 가른다.

    cd ~/stock && .venv/bin/python -m bot.scripts.kr_revenue_probe 005940.KS

읽기 전용 · LLM 0 · ₩0(DART 무과금).
"""
from __future__ import annotations

import sys

_PROBE_VER = 4

# 손익계산서에서 '수익'으로 읽힐 만한 행을 폭넓게 훑는다(우리 매핑 밖도 본다).
_REV_HINTS = ("수익", "매출", "영업이익", "Revenue", "revenue")


# 스윕 기본 대상 — 총액 계정을 안 쓰기 쉬운 업종(금융·지주·건설) 위주로
# 손으로 고른 표본. 전 종목 스윕은 DART 일일 한도(20,000)를 크게 먹는다.
_SWEEP_DEFAULT = [
    # 증권
    "005940.KS", "016360.KS", "006800.KS", "003530.KS", "071050.KS",
    # 은행·금융지주
    "105560.KS", "055550.KS", "086790.KS", "316140.KS", "138040.KS",
    "138930.KS", "175330.KS", "024110.KS",
    # 보험
    "032830.KS", "088350.KS", "005830.KS", "001450.KS", "000810.KS",
    # 지주·기타
    "003550.KS", "034730.KS", "001040.KS", "000070.KS", "004990.KS",
    # 건설(도급공사수익 계열)
    "000720.KS", "006360.KS", "047040.KS", "028050.KS",
]


def _p(*a):
    print(*a, flush=True)


# FnGuide/네이버에서 Financial Summary(매출액 총액)를 담고 있을 **후보**
# 엔드포인트. 어느 것이 실제로 표를 주는지는 추측하지 않고 VM 에서 재본다
# (2026-08-19: c1010001.aspx 단독으로는 15종목 전부 '표 없음'이었다).
_FG_CANDIDATES = [
    ("navercomp c1010001",
     "https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={c}"),
    ("navercomp cF1001 Q",
     "https://navercomp.wisereport.co.kr/v2/company/cF1001.aspx"
     "?cmp_cd={c}&fin_typ=0&freq_typ=Q"),
    ("navercomp cF1001 Y",
     "https://navercomp.wisereport.co.kr/v2/company/cF1001.aspx"
     "?cmp_cd={c}&fin_typ=0&freq_typ=Y"),
    ("comp SVD_Main",
     "https://comp.fnguide.com/SVO2/asp/SVD_Main.asp"
     "?pGB=1&gicode=A{c}&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701"),
    ("comp SVD_Finance",
     "https://comp.fnguide.com/SVO2/asp/SVD_Finance.asp"
     "?pGB=1&gicode=A{c}&cID=&MenuYn=Y&ReportGB=D&NewMenuID=103&stkGb=701"),
]


def _fnguide_debug(codes: list[str]) -> int:
    """후보 URL 을 하나씩 받아 **무엇이 들어 있는지** 그대로 찍는다.

    파서가 '표 없음'만 반복하면 원인이 (a) 요청 실패 (b) 표가 AJAX 라 HTML 에
    없음 (c) 우리 파싱 규칙 문제 중 무엇인지 알 수 없다 — 셋을 가른다."""
    import re as _re

    import requests
    from bot.wisereport_financials import _HEADERS, parse_financial_summary
    for tk in (codes or ["005940.KS"]):
        c = tk.split(".")[0]
        _p("")
        _p(f"── {tk}")
        for name, tmpl in _FG_CANDIDATES:
            url = tmpl.format(c=c)
            try:
                r = requests.get(url, headers=_HEADERS, timeout=15)
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding or "utf-8"
                html = r.text
                st = r.status_code
            except Exception as exc:                    # noqa: BLE001
                _p(f"   {name:<20} ❗ {type(exc).__name__}: {exc}")
                continue
            n_tbl = len(_re.findall(r"<table", html, _re.I))
            has_rev = "매출액" in html
            has_hl = "highlight_D_" in html
            parsed = parse_financial_summary(html)
            nq = len(parsed.get("quarter") or {})
            na = len(parsed.get("annual") or {})
            _p(f"   {name:<20} HTTP {st} · {len(html):>7,}B · table {n_tbl:>3}"
               f" · '매출액' {'O' if has_rev else 'X'}"
               f" · highlight_D_ {'O' if has_hl else 'X'}"
               f" · 파싱 분기 {nq}/연간 {na}")
            if nq or na:
                for kind in ("quarter", "annual"):
                    for per in sorted(parsed.get(kind) or {})[-2:]:
                        row = parsed[kind][per]
                        _p(f"      {kind} {per}: "
                           + " · ".join(f"{k}={v / 1e8:,.0f}억"
                                        for k, v in sorted(row.items())))
            elif has_rev:
                # 표는 있는데 못 읽었다 — 우리 파싱 규칙 문제. 주변을 보여준다.
                i = html.index("매출액")
                _p("      ↪ '매출액' 주변 원문 200자: "
                   + _re.sub(r"\s+", " ", html[max(0, i - 100):i + 100]))
    return 0


def _try_fallback(tk: str, fin: dict) -> str:
    """FnGuide 총액 보강이 **실제로** 되는지 한 줄로. 실패면 이유를 남긴다."""
    try:
        from bot.kr_revenue_fallback import fill_total_revenue
        from bot.wisereport_financials import fetch_financial_summary
    except Exception as exc:                            # noqa: BLE001
        return f"보강 모듈 없음({type(exc).__name__})"
    try:
        summary = fetch_financial_summary(tk)
    except Exception as exc:                            # noqa: BLE001
        return f"FnGuide 조회 실패({type(exc).__name__})"
    if not summary:
        from bot.wisereport_financials import _LAST_REASON
        return f"FnGuide 실패: {_LAST_REASON.get(tk.split('.')[0], '원인 미상')}"
    e = {"매출": fin.get("매출"), "영업이익": fin.get("영업이익"),
         "당기순이익": fin.get("당기순이익"),
         "_component_accounts": dict(fin.get("_component_accounts") or {})}
    ok = fill_total_revenue(tk, e, year=2025, quarter=None, summary=summary)
    if not ok:
        keys = sorted((summary.get("annual") or {}))[-3:]
        return f"❌ 보강 불가(연간 키={keys})"
    return (f"✅ 보강 {e['매출'] / 1e12:.2f}조"
            f" · 영업이익률 {e.get('영업이익률', 0):.1f}%")


def _sweep(dart, tickers: list[str]) -> int:
    """여러 종목을 훑어 **세 부류로 가른다** — 사용자 2026-08-19 "비슷한
    종목들도 고쳐진 거라고 봐야지?" 에 사실로 답하기 위한 것.

      ① 총액 정상            — 손볼 것 없음
      ② 구성요소만 공시      — 원천 한계. 비율 비우고 계정명 표기(고칠 수 없음)
      ③ 표준 태그로 구제됨   — **이번 fix 의 실제 수혜자**(이름이 목록 밖인데
                              `ifrs-full_Revenue` 라 총액으로 인정된 경우)
    """
    import requests
    from bot.dart_client import (_ACCOUNT_GROUPS, _DART_CODE_MAP,
                                 _DART_NAME_MAP, _NAME_MAP_NORM,
                                 _account_rank, _norm_acct_nm,
                                 _extract_dart_financials,
                                 calc_kr_financial_ratios)
    buckets: dict[str, list[str]] = {"총액": [], "구성요소": [], "태그구제": [],
                                     "데이터없음": []}
    _p(f"── 스윕 {len(tickers)}종목 (2025 사업보고서·CFS)")
    for n, tk in enumerate(tickers, 1):
        code = tk.split(".")[0]
        corp = dart.stock_code_to_corp_code(code)
        if not corp:
            buckets["데이터없음"].append(f"{tk}(corp_code 없음)")
            continue
        try:
            js = requests.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                params={"crtfc_key": dart.api_key, "corp_code": corp,
                        "bsns_year": "2025", "reprt_code": "11011",
                        "fs_div": "CFS"}, timeout=20).json()
        except Exception as exc:                        # noqa: BLE001
            buckets["데이터없음"].append(f"{tk}({type(exc).__name__})")
            continue
        if js.get("status") != "000":
            buckets["데이터없음"].append(f"{tk}(status={js.get('status')})")
            continue
        items = [i for i in (js.get("list") or [])
                 if (i.get("sj_div") or "") in ("IS", "CIS")]
        fin = _extract_dart_financials(items)
        comp = (fin.get("_component_accounts") or {}).get("매출")
        rat = calc_kr_financial_ratios(fin)
        # 승자의 이름·랭크를 다시 구해 '태그로 구제된' 경우를 식별한다.
        winner = None
        for i in items:
            nm, aid = (i.get("account_nm") or "").strip(), (i.get("account_id") or "").strip()
            canon = (_DART_CODE_MAP.get(aid) or _DART_NAME_MAP.get(nm)
                     or _NAME_MAP_NORM.get(_norm_acct_nm(nm)))
            if canon == "매출":
                r = _account_rank(canon, nm, aid)
                if winner is None or r < winner[0]:
                    winner = (r, nm, aid)
        if comp:
            # ② 그룹은 FnGuide 총액으로 채워지는지까지 본다 — "고쳐졌나?"에
            # 사실로 답하려면 검산 통과 여부를 실제로 돌려봐야 한다.
            fg = _try_fallback(tk, fin)
            buckets["구성요소"].append(f"{tk} 매출={comp} · {fg}")
        elif winner and winner[0] == 1 and _norm_acct_nm(winner[1]) not in {
                _norm_acct_nm(g) for grp in _ACCOUNT_GROUPS["매출"] for g in grp}:
            buckets["태그구제"].append(f"{tk} 승자='{winner[1]}' id={winner[2]}")
        elif fin.get("매출"):
            buckets["총액"].append(tk)
        else:
            buckets["데이터없음"].append(f"{tk}(매출 계정 없음)")
        if n % 10 == 0:
            _p(f"   … {n}/{len(tickers)}")
    _p("")
    for k, label in (("태그구제", "③ 표준 태그로 구제 — 이번 fix 의 실제 수혜자"),
                     ("구성요소", "② 구성요소만 공시 — 원천 한계(비율 비움이 정답)"),
                     ("총액", "① 총액 정상"),
                     ("데이터없음", "· 조회 불가")):
        v = buckets[k]
        _p(f"{label}: {len(v)}개")
        for x in v[:40]:
            _p(f"   {x}")
        if len(v) > 40:
            _p(f"   … 외 {len(v) - 40}개")
    return 0


def main(argv: list[str]) -> int:
    from bot.dart_client import (_ACCOUNT_GROUPS, _DART_CODE_MAP,
                                 _DART_NAME_MAP, _NAME_MAP_NORM,
                                 _account_rank, _norm_acct_nm,
                                 _extract_dart_financials,
                                 calc_kr_financial_ratios, get_dart)
    tickers = [a for a in argv[1:] if not a.startswith("-")]
    _p(f"kr_revenue_probe v{_PROBE_VER} · 매출 그룹={_ACCOUNT_GROUPS['매출']}")
    if "--fnguide-debug" in argv:
        return _fnguide_debug(tickers)
    dart = get_dart()
    if not dart:
        _p("❗ DART 클라이언트 없음 — DART_API_KEY 확인")
        return 1
    if "--sweep" in argv:
        return _sweep(dart, tickers or _SWEEP_DEFAULT)
    tickers = tickers or ["005940.KS"]
    import requests
    for tk in tickers:
        code = tk.split(".")[0]
        corp = dart.stock_code_to_corp_code(code)
        _p("")
        _p(f"── {tk} (corp_code={corp})")
        if not corp:
            _p("   ❗ corp_code 못 찾음")
            continue
        for year, rc in ((2025, "11011"), (2026, "11012")):
            try:
                r = requests.get(
                    "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                    params={"crtfc_key": dart.api_key, "corp_code": corp,
                            "bsns_year": str(year), "reprt_code": rc,
                            "fs_div": "CFS"}, timeout=20)
                js = r.json()
            except Exception as exc:                    # noqa: BLE001
                _p(f"   {year}/{rc}: ❗ 호출 실패 {exc}")
                continue
            if js.get("status") != "000":
                _p(f"   {year}/{rc}: DART status={js.get('status')} — 데이터 없음")
                continue
            items = [i for i in (js.get("list") or [])
                     if (i.get("sj_div") or "") in ("IS", "CIS")]
            _p(f"   {year}/{rc}: 손익 항목 {len(items)}개")
            hits = [i for i in items
                    if any(h in (i.get("account_nm") or "") for h in _REV_HINTS)
                    or (i.get("account_id") or "").endswith("Revenue")]
            for i in hits[:14]:
                nm = (i.get("account_nm") or "").strip()
                aid = (i.get("account_id") or "").strip()
                canon = (_DART_CODE_MAP.get(aid) or _DART_NAME_MAP.get(nm)
                         or _NAME_MAP_NORM.get(_norm_acct_nm(nm)))
                rk = _account_rank(canon, nm, aid) if canon else "-"
                _p(f"      {nm:<22} id={aid:<45} → {canon or '(미매핑)'} rank={rk}"
                   f"  {i.get('thstrm_amount','')}")
            if len(hits) > 14:
                _p(f"      … 외 {len(hits) - 14}건")
            fin = _extract_dart_financials(items)
            comp = fin.get("_component_accounts") or {}
            rat = calc_kr_financial_ratios(fin)
            _p(f"      ▶ 채택 매출={fin.get('매출')}"
               f" · 구성요소여부={comp.get('매출') or '아님(총액)'}"
               f" · 영업이익={fin.get('영업이익')}")
            _p(f"      ▶ 영업이익률={rat.get('영업이익률')}"
               f" · 순이익률={rat.get('순이익률')}"
               f"   (구성요소면 None 이 정상 — 비율은 비운다)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
