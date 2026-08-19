"""업종 한글화가 **무엇을 못 덮고 있나** — 미번역 값을 빈도순으로.

사용자 2026-08-19: "미국 신고가/신저가, 급등/급락, 장전/장후 모두 업종 최대한
한글화해줘."

왜 미국만 영문이었나: 한국·일본 화면의 업종은 yfinance/GICS 어휘
("Software - Infrastructure")인데 **미국 페이지는 NASDAQ screener 분류**
("EDP Services"·"Biotechnology: Pharmaceutical Preparations")라 어휘가 통째로
다르다. 사전이 앞의 계열만 갖고 있었다.

⚠️ 화면에 잘려 보이는 라벨("Investment Banking & Brok…")을 **추측해서**
사전에 넣으면 엉뚱한 번역이 굳는다(실수 #12). 그래서 원천 캐시에서 **전체
문자열**을 그대로 뽑아 세고, 미번역만 빈도순으로 보여 준다 — 그 목록대로
번역하면 추측이 0 이다.

    cd ~/stock && .venv/bin/python -m bot.scripts.industry_kr_probe
    cd ~/stock && .venv/bin/python -m bot.scripts.industry_kr_probe 60   # 상위 60개

읽기 전용 · 네트워크 0(디스크 캐시만) · LLM 0 · ₩0.
"""
from __future__ import annotations

import sys
from collections import Counter

_PROBE_VER = 1

# (캐시 파일, 설명) — 값이 {티커: 업종} 인 맵만.
_SOURCES = (
    ("nasdaq_industries.json", "미국(NASDAQ screener) — 신고저·급등락·장전장후"),
    ("sp500_inds_github.json", "미국 S&P500(GICS sub-industry)"),
    ("sp500_industry.json", "미국 S&P500(보조)"),
    ("tw_industry_map", "대만(TWSE/TPEx)"),
)


def _p(*a):
    print(*a, flush=True)


def main() -> int:
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    from bot.finviz_client import _cached
    from bot.translate import _INDUSTRY_KR, industry_kr

    _p(f"industry_kr_probe v{_PROBE_VER} · 사전 {len(_INDUSTRY_KR)}개 항목")

    for name, desc in _SOURCES:
        m = _cached(name, ttl=10 ** 9)      # 나이 무시 — 지금 디스크에 있는 것
        if not isinstance(m, dict) or not m:
            _p("")
            _p(f"── {desc}: 캐시 없음({name}) — 아직 안 받았거나 소스 실패")
            continue
        vals = [str(v).strip() for v in m.values() if str(v or "").strip()]
        cnt = Counter(vals)
        # 번역되면 한글이 섞인다 — 사전을 통과했는데 그대로면 미번역.
        missing = Counter({k: c for k, c in cnt.items() if industry_kr(k) == k
                           and not any("가" <= ch <= "힣" for ch in k)})
        done = sum(cnt.values()) - sum(missing.values())
        pct = 100.0 * done / max(1, sum(cnt.values()))
        _p("")
        _p(f"── {desc}")
        _p(f"   종목 {len(m):,} · 업종 {len(cnt)}종 · "
           f"한글화 {done:,}/{sum(cnt.values()):,} ({pct:.1f}%)")
        if not missing:
            _p("   ✅ 미번역 없음")
            continue
        _p(f"   미번역 {len(missing)}종 — 빈도순 상위 {min(top, len(missing))}개"
           f"(그대로 복사해 사전에 넣으면 된다):")
        for k, c in missing.most_common(top):
            _p(f'      {c:>5}종목  "{k}"')
        if len(missing) > top:
            _p(f"      … 외 {len(missing) - top}종(인자로 개수 지정)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
