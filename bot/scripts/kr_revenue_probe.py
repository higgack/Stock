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

_PROBE_VER = 1

# 손익계산서에서 '수익'으로 읽힐 만한 행을 폭넓게 훑는다(우리 매핑 밖도 본다).
_REV_HINTS = ("수익", "매출", "영업이익", "Revenue", "revenue")


def _p(*a):
    print(*a, flush=True)


def main(argv: list[str]) -> int:
    from bot.dart_client import (_ACCOUNT_GROUPS, _DART_CODE_MAP,
                                 _DART_NAME_MAP, _NAME_MAP_NORM,
                                 _account_rank, _norm_acct_nm,
                                 _extract_dart_financials,
                                 calc_kr_financial_ratios, get_dart)
    tickers = [a for a in argv[1:] if not a.startswith("-")] or ["005940.KS"]
    _p(f"kr_revenue_probe v{_PROBE_VER} · 매출 그룹={_ACCOUNT_GROUPS['매출']}")
    dart = get_dart()
    if not dart:
        _p("❗ DART 클라이언트 없음 — DART_API_KEY 확인")
        return 1
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
