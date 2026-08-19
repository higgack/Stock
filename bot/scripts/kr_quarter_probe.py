"""분기 단독값이 **누적**으로 들어가는지 — 계정별 원본 필드 대조.

사용자 2026-08-19(NH투자증권 005940): 당기순이익이 25.2Q 4,650억 · 25.3Q
7,481억인데 네이버는 2,569억 · 2,831억이다. 우리 값은 **누적치와 일치**한다
(2,082+2,569=4,651 · +2,831=7,482). 같은 표의 영업이익은 네이버와 정확히
맞으므로 "분기보고서 전체가 누적"인 것은 아니고 **계정마다 다르다**.

의심 지점(가설 — 이 프로브가 확정한다): `_extract_dart_financials` 는
sj_div 를 가리지 않고 같은 canonical 키에 여러 행이 걸리면 **절댓값이 큰
행**을 고른다. 손익계산서(IS)의 3개월 값과 포괄손익계산서(CIS)의 누적 값이
함께 오면 누적이 항상 더 커서 이긴다.

그래서 추측 대신 **원본 필드를 그대로 찍는다** — 어느 sj_div 의 어느 계정이
어떤 값을 갖고 우리가 무엇을 골랐는지.

    cd ~/stock && .venv/bin/python -m bot.scripts.kr_quarter_probe 005940

읽기 전용 · LLM 0 · ₩0.
"""
from __future__ import annotations

import sys

_PROBE_VER = 1

# 보고서코드 → 사람이 읽는 이름(분기 단독/누적 구분이 핵심인 것만).
_REPORTS = (("11013", "1분기"), ("11012", "반기"), ("11014", "3분기"),
            ("11011", "사업(연간)"))
_KEYS = ("매출", "영업이익", "당기순이익")


def _p(*a):
    print(*a, flush=True)


def _fmt(v):
    if v is None:
        return "—"
    return f"{v / 1e8:>12,.0f}억"


def main() -> int:
    code = (sys.argv[1] if len(sys.argv) > 1 else "005940").split(".")[0]
    year = int(sys.argv[2]) if len(sys.argv) > 2 else 2025

    import requests
    from bot.dart_client import (DartClient, _DART_CODE_MAP, _DART_NAME_MAP,
                                 _NAME_MAP_NORM, _extract_dart_financials,
                                 _norm_acct_nm, _parse_dart_amount)

    dart = DartClient()
    if not dart.api_key:
        _p("❌ DART_API_KEY 미설정 — bot/env_keys 경유로도 못 찾음")
        return 1
    corp = dart.stock_code_to_corp_code(code)
    if not corp:
        _p(f"❌ corp_code 없음: {code}")
        return 1
    _p(f"kr_quarter_probe v{_PROBE_VER} · {code} · {year}년")
    _p("")

    for rc, name in _REPORTS:
        try:
            js = requests.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                params={"crtfc_key": dart.api_key, "corp_code": corp,
                        "bsns_year": str(year), "reprt_code": rc,
                        "fs_div": "CFS"}, timeout=20).json()
        except Exception as exc:                               # noqa: BLE001
            _p(f"── {name}({rc}): 요청 실패 {type(exc).__name__}: {exc}")
            continue
        if js.get("status") != "000":
            _p(f"── {name}({rc}): status={js.get('status')}")
            continue
        items = js.get("list") or []
        picked = _extract_dart_financials(items)
        cum = _extract_dart_financials(items, amount_field="thstrm_add_amount")

        _p(f"── {name}({rc}) · 행 {len(items)}개")
        for key in _KEYS:
            # 이 canonical 로 매핑되는 **모든** 행을 원본 필드째 나열한다.
            rows = []
            for it in items:
                aid = (it.get("account_id") or "").strip()
                nm = (it.get("account_nm") or "").strip()
                canon = (_DART_CODE_MAP.get(aid) or _DART_NAME_MAP.get(nm)
                         or _NAME_MAP_NORM.get(_norm_acct_nm(nm)))
                if canon != key:
                    continue
                rows.append((it.get("sj_div") or "", nm, aid[:38],
                             _parse_dart_amount(it.get("thstrm_amount"),
                                                as_float=True),
                             _parse_dart_amount(it.get("thstrm_add_amount"),
                                                as_float=True)))
            _p(f"   [{key}] 후보 {len(rows)}행 · 우리가 고른 값 "
               f"{_fmt(picked.get(key))} · 누적필드 {_fmt(cum.get(key))}")
            for sj, nm, aid, amt, add in rows:
                mark = "  ← 채택" if (amt is not None
                                    and picked.get(key) == amt) else ""
                _p(f"        {sj:4} {nm[:24]:26} {aid:38} "
                   f"3개월={_fmt(amt)} 누적={_fmt(add)}{mark}")
        _p("")

    _p("읽는 법: 같은 [키] 안에서 sj_div 가 IS 와 CIS 로 갈리고 값이 다르면,")
    _p("        '절댓값 큰 행 채택' 규칙이 **누적을 단독분기로** 뽑고 있는 것.")
    _p("        한 행뿐인데도 네이버와 다르면 그 행 자체가 누적이라는 뜻.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
