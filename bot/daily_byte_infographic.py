"""Daily Byte 인포그래픽 렌더러 — 수급 데이터(pykrx 정확값)를 전문
금융 인포그래픽 PNG 로 변환. 텔레그램 사진 push 용.

숫자는 전부 `collect_flow_data()` 의 구조화 dict 에서 직접 주입되므로
환각 0. 한국어는 NanumGothic 으로 렌더 (VM: `sudo apt install -y
fonts-nanum`). 폰트 부재 시 graceful — render_infographic 가 None 반환
→ 호출측은 텍스트 브리프만 push.

섹션: 헤더 / 시장 수급 총평(주체별 막대 + breadth) / 당일 순매수 TOP
(외국인·기관) / 경고(양→음 전환) / 면책. 섹터 narrative 는 텍스트 브리프
담당(LLM 그룹핑) — 인포그래픽은 Python 이 정확히 아는 값만 시각화.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("bot.daily_byte_infographic")

# 팔레트 (HTML 데모와 동일 톤)
_BG = "#070a14"; _PANEL = "#131a2e"; _PANEL2 = "#1a2238"; _LINE = "#2a3656"
_TEXT = "#e8ecf6"; _MUTED = "#93a0bd"; _ACCENT = "#4da3ff"; _ACCENTW = "#22d3ee"
_POS = "#34d399"; _NEG = "#f87171"; _GOLD = "#fbbf24"

_INVESTOR_ORDER = ["외국인", "기관", "투신", "연기금", "개인"]


# ⚠️ 한글 폰트 판정은 `bot/korean_font` 단일 헬퍼로 (2026-08-18).
# 옛 구현은 Nanum 이름·경로 3개만 봐서, Noto CJK 처럼 한글이 완벽히 되는
# 폰트가 깔린 서버에서도 "미설치"로 단정하고 이미지를 포기했다(실수 #24
# 목록형 판정). 지금은 글리프 실측 — 이름이 무엇이든 한글이 그려지면 쓴다.


def _font_ready() -> bool:
    from bot.korean_font import find_font
    return bool(find_font())


def _setup_font() -> bool:
    from bot.korean_font import setup_matplotlib
    return setup_matplotlib()


def _eok(v: float) -> str:
    """억원 표기 (조 단위 자동). 음수는 ASCII '-' (NanumGothic 에 U+2212 없음)."""
    if abs(v) >= 10000:
        return f"{'+' if v >= 0 else '-'}{abs(v)/10000:,.2f}조"
    return f"{'+' if v >= 0 else '-'}{abs(v):,.0f}억"


def render_infographic(data: dict, date_iso: str, out_path: str) -> str | None:
    """data(collect_flow_data 출력) → PNG. 성공 시 out_path, 실패 시 None."""
    if not _font_ready() or not _setup_font():
        log.warning("infographic: Nanum 폰트 없음 — render skip (텍스트만 push)")
        return None
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, Rectangle
    except Exception as exc:
        log.warning("infographic: matplotlib import failed: %s", exc)
        return None

    totals = data.get("totals", {}) or {}
    # 주체 매핑: totals 키는 외국인/개인/기관/투신/연기금/사모...
    def _tv(k):
        return float(totals.get(k, 0) or 0)
    inv_vals = [("외국인", _tv("외국인")), ("기관", _tv("기관")),
                ("투신", _tv("투신")), ("연기금", _tv("연기금")),
                ("개인", _tv("개인"))]

    breadth = data.get("breadth") or {}
    chg = data.get("chg") or {}        # {ticker: 등락률%}
    today = data.get("today", {}) or {}
    reversals = data.get("reversals", []) or []

    def _top_rows(inv_key, n=5):
        td = today.get(inv_key) or {}
        rows = (td.get("top_buy") or [])[:n]
        return rows

    fg_rows = _top_rows("외국인")
    # 기관: totals 의 '기관' 은 기관합계, per-stock 은 '기관합계' 키
    inst_rows = _top_rows("기관합계") or _top_rows("기관")

    # ── 레이아웃: landscape 3-column. 헤더(전폭, breadth 우측) / 시장총평·
    #    외국인 TOP·기관 TOP 3열 / 경고(전폭). 좌표계 0..100 x, 0..H y.
    W = 100.0
    n_rev = min(len(reversals), 4)
    _MAIN_TOP = 25.0
    _MAIN_H = 30.0
    _WARN_TOP = _MAIN_TOP + _MAIN_H + 4         # 59
    _WARN_H = (7.0 + n_rev * 5.2) if n_rev else 0.0
    H = _WARN_TOP + (_WARN_H + 6 if n_rev else 6)
    fig_w = 11.6
    fig_h = fig_w * (H / W)                      # landscape (W > H)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=150)
    fig.patch.set_facecolor(_BG)
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.invert_yaxis()
    ax.axis("off")

    def panel(x, y, w, h, fc=_PANEL, ec=_LINE, lw=1.0, rad=2.2):
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={rad}",
            facecolor=fc, edgecolor=ec, linewidth=lw, mutation_aspect=1))

    def txt(x, y, s, size=11, color=_TEXT, weight="normal", ha="left", va="center"):
        ax.text(x, y, s, fontsize=size, color=color, weight=weight,
                ha=ha, va=va)

    def section(x, y, s, color=_ACCENT, size=12.5):
        """이모지 대신 컬러 탭 + 굵은 제목 (NanumGothic 이모지 미지원)."""
        ax.add_patch(Rectangle((x, y - 1.6), 1.1, 3.4, facecolor=color, edgecolor="none"))
        txt(x + 3, y, s, size=size, weight="bold")

    # ── 헤더 (전폭) + breadth (우측) ───────────────────────────────
    panel(2.5, 3, 95, 17, fc="#1d4ed8", ec="#1d4ed8", rad=2.6)
    txt(6, 7, "DAILY BYTE · KR MARKET FLOW", size=10, color="#cfe3ff", weight="bold")
    txt(6, 12.3, "장 마감 후 투자주체별 수급 브리프", size=17, color="white", weight="bold")
    txt(6, 17.3, f"{date_iso} · KOSPI + KOSDAQ · pykrx EOD (KRX 공식)",
        size=9.5, color="#dbe9ff")
    if breadth.get("pct") is not None:
        pct = breadth.get("pct", 0)
        txt(94, 8.5, f"{pct:.0f}%", size=23, color="white", weight="bold", ha="right")
        txt(94, 13.5, "순매수종목 비율 (breadth)", size=9, color="#dbe9ff", ha="right")
        nb = breadth.get("net_buy_n"); tot = breadth.get("total_n")
        if nb and tot:
            txt(94, 17, f"{nb}/{tot}종목 · 자금 소수 대형주 응축"
                if pct < 45 else f"{nb}/{tot}종목 순매수",
                size=8.3, color="#cfe3ff", ha="right")

    # ── 3열 헤더 ───────────────────────────────────────────────────
    C1, C1W = 2.5, 30.0
    C2, C2W = 34.5, 30.0
    C3, C3W = 66.5, 31.0
    section(C1, 23, "시장 수급 (당일, 억원)", size=10.5)
    section(C2, 23, "외국인 순매수 TOP 5", _ACCENTW, size=10.5)
    section(C3, 23, "기관 순매수 TOP 5", _ACCENTW, size=10.5)

    # ── COL1: 시장 총평 막대 ───────────────────────────────────────
    panel(C1, _MAIN_TOP, C1W, _MAIN_H, fc=_PANEL, ec=_LINE, rad=1.8)
    maxabs = max((abs(v) for _, v in inv_vals), default=1.0) or 1.0
    bx0 = C1 + 9.0
    bx_w = C1W - 17.0
    ry = _MAIN_TOP + (_MAIN_H - 5 * 5.2) / 2 + 2.6
    for label, v in inv_vals:
        txt(C1 + 2, ry, label, size=9, color=_MUTED, weight="bold")
        w = max(0.5, abs(v) / maxabs * bx_w)
        col = _POS if v >= 0 else _NEG
        ax.add_patch(Rectangle((bx0, ry - 1.0), w, 2.0, facecolor=col, edgecolor="none"))
        txt(C1 + C1W - 1.5, ry, _eok(v), size=8.5, color=col, weight="bold", ha="right")
        ry += 5.2

    # ── COL2/COL3: TOP 5 (종목 / net / 등락률) ─────────────────────
    def _render_top(cx, cw, rows):
        panel(cx, _MAIN_TOP, cw, _MAIN_H, fc=_PANEL, ec=_LINE, rad=1.8)
        ry2 = _MAIN_TOP + (_MAIN_H - 5 * 5.2) / 2 + 2.6
        if not rows:
            txt(cx + 2.5, ry2, "(데이터 없음)", size=9, color=_MUTED)
            return
        for t, nm, net in rows:
            txt(cx + 2.5, ry2, (nm or t)[:9], size=9.5, weight="bold")
            txt(cx + cw - 2.5, ry2 - 0.9, _eok(net), size=9, color=_POS,
                weight="bold", ha="right")
            cpct = chg.get(str(t))
            if isinstance(cpct, (int, float)):
                cp_col = _POS if cpct >= 0 else _NEG
                txt(cx + cw - 2.5, ry2 + 2.1, f"{cpct:+.1f}%", size=7.6,
                    color=cp_col, ha="right")
            ry2 += 5.2
    _render_top(C2, C2W, fg_rows)
    _render_top(C3, C3W, inst_rows)

    # ── 경고 (전폭) ────────────────────────────────────────────────
    if n_rev:
        panel(C1, _WARN_TOP, (C3 + C3W) - C1, _WARN_H, fc="#1f1320",
              ec="#5b2540", rad=1.8)
        ax.add_patch(Rectangle((C1 + 3, _WARN_TOP + 2.4), 1.1, 3.0,
                               facecolor=_NEG, edgecolor="none"))
        txt(C1 + 5.5, _WARN_TOP + 4,
            "경고 시그널 — 양→음 전환 (5일 누적 매수 → 당일 매도)",
            size=10.5, color=_NEG, weight="bold")
        ry = _WARN_TOP + 9
        for inv, tkr, nm, cum_net, t_net in reversals[:n_rev]:
            txt(C1 + 3, ry, f"{(nm or tkr)[:10]} ({tkr}) [{inv}]",
                size=9.5, weight="bold")
            txt(C3 + C3W - 1.5, ry, f"5일 {_eok(cum_net)} → 당일 {_eok(t_net)}",
                size=9.5, color=_MUTED, ha="right")
            ry += 5.2

    # ── footer (면책 문구 제거 — 사용자 정책 2026-06-11, 출처 표기만) ──
    txt(C1, H - 2.5, "수치: pykrx EOD (KRX 공식) · 환각 0", size=8.5, color=_MUTED)

    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        fig.savefig(out_path, facecolor=_BG, bbox_inches="tight", pad_inches=0.15)
        plt.close(fig)
        return out_path
    except Exception as exc:
        log.warning("infographic: savefig failed: %s", exc)
        try:
            plt.close(fig)
        except Exception:
            pass
        return None


if __name__ == "__main__":
    # mock 렌더 self-test
    mock = {
        "totals": {"외국인": -13600, "기관": 20548, "투신": 3184,
                   "연기금": 603, "개인": -11000, "사모": 291},
        "breadth": {"pct": 38, "net_buy_n": 760, "total_n": 2000},
        "chg": {"035420": 14.2, "064400": 29.9, "373220": 15.25,
                "005930": -0.8, "000660": -1.2, "131970": 2.2,
                "000150": 18.8, "006400": 3.1, "012330": 1.1, "005380": 0.9},
        "today": {
            "외국인": {"top_buy": [
                ("064400", "LG씨엔에스", 729.8), ("005380", "현대차", 435.0),
                ("035420", "NAVER", 278.0), ("001440", "대한전선", 459.0),
                ("010120", "LS ELECTRIC", 370.0)]},
            "기관합계": {"top_buy": [
                ("005930", "삼성전자", 16666.0), ("035420", "NAVER", 2052.0),
                ("373220", "LG에너지솔루션", 850.0), ("131970", "두산테스나", 480.0),
                ("006400", "삼성SDI", 422.0)]},
        },
        "reversals": [
            ("외국인", "042660", "한화오션", 1283.0, -52.0),
            ("외국인", "006400", "삼성SDI", 1266.0, -106.0),
            ("기관합계", "000660", "SK하이닉스", 19526.0, -953.0)],
    }
    p = render_infographic(mock, "2026.05.29 (목)", "/tmp/db_infographic_test.png")
    print("rendered:", p)
