"""부동산 Byte 인포그래픽 렌더러 — 아파트 실거래가(MOLIT 정확값)를 전문
금융 인포그래픽 PNG 로 변환. 텔레그램 사진 push + 대시보드 임베드.

daily_byte_infographic 패턴 mirror — 숫자는 collect_realestate_data() 의
구조화 dict 에서 직접 주입(환각 0). 한국어는 NanumGothic. 폰트 부재 시
None 반환 → 텍스트 브리프만 push.

섹션: 헤더(기준월) / 지역별 평균 거래가 막대(좌) · 거래량 막대(우) /
평당가 비교 / 면책. matplotlib landscape.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("bot.realestate_infographic")

_BG = "#070a14"; _PANEL = "#131a2e"; _LINE = "#2a3656"
_TEXT = "#e8ecf6"; _MUTED = "#93a0bd"; _ACCENT = "#4da3ff"; _ACCENTW = "#22d3ee"
_POS = "#34d399"; _GOLD = "#fbbf24"; _PUR = "#a78bfa"


# NanumGothic 디스크 경로 (matplotlib 폰트캐시가 stale 해도 직접 등록)
_NANUM_PATHS = (
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/opentype/nanum/NanumGothic.ttf",
)


def _font_ready() -> bool:
    try:
        import matplotlib.font_manager as fm
        if any("Nanum" in f.name for f in fm.fontManager.ttflist):
            return True
    except Exception:
        pass
    return any(os.path.exists(p) for p in _NANUM_PATHS)


def _setup_font() -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.font_manager as fm
        from matplotlib import rcParams
        names = {f.name for f in fm.fontManager.ttflist}
        for cand in ("NanumGothic", "NanumBarunGothic", "NanumSquare"):
            if cand in names:
                rcParams["font.family"] = cand
                rcParams["axes.unicode_minus"] = False
                return True
        # 캐시에 없으면 디스크에서 직접 등록 (fonts-nanum 설치 후 matplotlib
        # 캐시 미갱신 케이스 — realestate 2026-05-31 surfaced)
        for p in _NANUM_PATHS:
            if os.path.exists(p):
                fm.fontManager.addfont(p)
                rcParams["font.family"] = fm.FontProperties(fname=p).get_name()
                rcParams["axes.unicode_minus"] = False
                return True
    except Exception as exc:
        log.warning("re-infographic: font setup failed: %s", exc)
    return False


def _eok(manwon: float) -> str:
    return f"{manwon / 10000:.1f}억" if manwon >= 10000 else f"{manwon:,.0f}만"


def render_infographic(data: dict, out_path: str) -> str | None:
    """data(collect_realestate_data 출력) → PNG. 성공 시 out_path, 실패 None."""
    if not _font_ready() or not _setup_font():
        log.warning("re-infographic: Nanum 폰트 없음 — skip")
        return None
    regs = data.get("regions") or {}
    if not regs:
        return None
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, Rectangle
    except Exception as exc:
        log.warning("re-infographic: matplotlib import failed: %s", exc)
        return None

    # 평균 거래가 내림차순 정렬
    items = sorted(regs.items(), key=lambda kv: kv[1].get("avg_manwon", 0), reverse=True)
    names = [n for n, _ in items]
    avgs = [r.get("avg_manwon", 0) for _, r in items]
    deals = [r.get("n_deals", 0) for _, r in items]
    ppps = [r.get("avg_per_pyeong") or 0 for _, r in items]
    n = len(items)

    W = 100.0
    _has_ppp = any((r.get("avg_per_pyeong") or 0) for _, r in items)
    # 평당가 비교도 위 섹션과 같은 행(row) 레이아웃(2026-08-08 가독성 개선 —
    # 옆으로 n등분한 좁은 막대 + 6.5pt 글씨가 지역 늘어날수록 더 읽기 힘들어지던
    # 문제) 이라 같은 n*row_h 만큼 세로 공간 필요.
    H = 30.0 + n * 6.0 + ((10.0 + n * 6.0) if _has_ppp else 8)
    fig_w = 11.6
    fig, ax = plt.subplots(figsize=(fig_w, fig_w * (H / W)), dpi=150)
    fig.patch.set_facecolor(_BG)
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.invert_yaxis(); ax.axis("off")

    def panel(x, y, w, h, fc=_PANEL, ec=_LINE, rad=2.0):
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                     boxstyle=f"round,pad=0,rounding_size={rad}",
                     facecolor=fc, edgecolor=ec, linewidth=1.0, mutation_aspect=1))

    def txt(x, y, s, size=11, color=_TEXT, weight="normal", ha="left", va="center"):
        ax.text(x, y, s, fontsize=size, color=color, weight=weight, ha=ha, va=va)

    # ── 헤더 ───────────────────────────────────────────────────────
    ymd = data.get("ymd", "")
    panel(2.5, 3, 95, 17, fc="#1d4ed8", ec="#1d4ed8", rad=2.6)
    txt(6, 7, "부동산 BYTE · KR APARTMENT REAL TRANSACTIONS", size=10, color="#cfe3ff", weight="bold")
    txt(6, 12.3, "아파트 실거래가 주간 브리프", size=17, color="white", weight="bold")
    txt(6, 17.3, f"{ymd[:4]}년 {ymd[4:6]}월 신고 기준 · 국토교통부(MOLIT) 공식",
        size=9.5, color="#dbe9ff")

    # ── 지역별 막대 (평균 거래가 + 거래량) ─────────────────────────
    def _section(x, y, s, color):
        ax.add_patch(Rectangle((x, y - 1.6), 1.1, 3.4, facecolor=color, edgecolor="none"))
        txt(x + 3, y, s, size=11, weight="bold")

    top = 24.0
    _section(4, top, "지역별 평균 거래가 (내림차순)", _ACCENT)
    rows_top = top + 4
    row_h = 6.0
    panel(4, rows_top, 92, n * row_h + 2, fc=_PANEL, rad=1.8)
    max_avg = max(avgs) or 1
    max_deal = max(deals) or 1
    bx0 = 22.0          # 평균가 막대 시작
    bx_w = 38.0         # 평균가 최대 폭
    dx0 = 66.0          # 거래량 막대 시작
    dx_w = 22.0
    ry = rows_top + 3.5
    for i in range(n):
        txt(7, ry, names[i][:9], size=10, color=_MUTED, weight="bold")
        # 평균가 막대
        w = max(0.5, avgs[i] / max_avg * bx_w)
        ax.add_patch(Rectangle((bx0, ry - 1.1), w, 2.2, facecolor=_ACCENT, edgecolor="none"))
        txt(bx0 + w + 1.5, ry, _eok(avgs[i]), size=9, color=_ACCENTW, weight="bold")
        # 거래량 막대
        dw = max(0.4, deals[i] / max_deal * dx_w)
        ax.add_patch(Rectangle((dx0, ry - 1.1), dw, 2.2, facecolor=_GOLD, edgecolor="none"))
        txt(dx0 + dw + 1.2, ry, f"{deals[i]}건", size=8.5, color=_GOLD, weight="bold")
        ry += row_h
    txt(bx0, rows_top - 1.5, "■ 평균 거래가", size=8, color=_ACCENTW)
    txt(dx0, rows_top - 1.5, "■ 거래량", size=8, color=_GOLD)

    # ── 평당가 비교 (있으면) ───────────────────────────────────────
    # 위 '평균 거래가' 섹션과 같은 가로 막대 행 레이아웃(2026-08-08 가독성
    # 개선 — 옛 방식은 n등분한 좁은 세로막대 + 지역명 4글자 절단 + 6.5pt 라
    # 지역 수가 늘어날수록(현재 14개) 더 안 보이던 문제, 사용자 스크린샷).
    y2 = rows_top + n * row_h + 5
    if any(ppps):
        _section(4, y2, "평당가 비교 (만원/평, 내림차순)", _PUR)
        ppp_rows_top = y2 + 4
        panel(4, ppp_rows_top, 92, n * row_h + 2, fc=_PANEL, rad=1.8)
        ppp_order = sorted(range(n), key=lambda i: ppps[i], reverse=True)
        max_ppp = max(ppps) or 1
        pbx0 = 22.0          # 평당가 막대 시작
        pbx_w = 60.0         # 평당가 최대 폭 (거래량 열 없어 더 넓게)
        pry = ppp_rows_top + 3.5
        for i in ppp_order:
            if not ppps[i]:
                continue
            txt(7, pry, names[i][:9], size=10, color=_MUTED, weight="bold")
            w = max(0.5, ppps[i] / max_ppp * pbx_w)
            ax.add_patch(Rectangle((pbx0, pry - 1.1), w, 2.2, facecolor=_PUR, edgecolor="none"))
            txt(pbx0 + w + 1.5, pry, f"{ppps[i]:,.0f}만원/평", size=9, color=_PUR, weight="bold")
            pry += row_h

    txt(4, H - 2.5, "수치: MOLIT 아파트 실거래가 (공식) · 환각 0", size=8.5, color=_MUTED)
    txt(96, H - 2.5, "공공데이터 관찰 (교육·정보), 투자 권유 아님", size=8.5, color=_MUTED, ha="right")

    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        fig.savefig(out_path, facecolor=_BG, bbox_inches="tight", pad_inches=0.15)
        plt.close(fig)
        return out_path
    except Exception as exc:
        log.warning("re-infographic: savefig failed: %s", exc)
        try:
            plt.close(fig)
        except Exception:
            pass
        return None


if __name__ == "__main__":
    mock = {"ymd": "202604", "regions": {
        "서울 강남구": {"avg_manwon": 250000, "n_deals": 180, "avg_per_pyeong": 9500, "max_manwon": 700000},
        "서울 서초구": {"avg_manwon": 230000, "n_deals": 150, "avg_per_pyeong": 8800, "max_manwon": 600000},
        "서울 마포구": {"avg_manwon": 150000, "n_deals": 210, "avg_per_pyeong": 5200, "max_manwon": 280000},
        "성남 분당구": {"avg_manwon": 140000, "n_deals": 190, "avg_per_pyeong": 4800, "max_manwon": 300000},
        "부산 해운대구": {"avg_manwon": 90000, "n_deals": 160, "avg_per_pyeong": 3100, "max_manwon": 200000},
        "대구 중구": {"avg_manwon": 55000, "n_deals": 90, "avg_per_pyeong": 1900, "max_manwon": 120000},
    }}
    p = render_infographic(mock, "/tmp/re_infographic_test.png")
    print("rendered:", p)
