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


def _font_ready() -> bool:
    """NanumGothic(또는 Nanum 계열) 가용 여부. 없으면 한글이 깨지므로
    렌더를 skip 한다."""
    try:
        import matplotlib.font_manager as fm
        for f in fm.fontManager.ttflist:
            if "Nanum" in f.name:
                return True
    except Exception:
        pass
    return False


def _setup_font() -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.font_manager as fm
        from matplotlib import rcParams
        # 등록된 Nanum 계열 중 NanumGothic 우선
        names = {f.name for f in fm.fontManager.ttflist}
        for cand in ("NanumGothic", "NanumBarunGothic", "NanumSquare"):
            if cand in names:
                rcParams["font.family"] = cand
                rcParams["axes.unicode_minus"] = False
                return True
    except Exception as exc:
        log.warning("infographic: font setup failed: %s", exc)
    return False


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

    # ── 레이아웃 (좌표계 0..100 x, 0..H y, 위→아래) ──────────────────
    W = 100.0
    # 동적 높이: 헤더 + 총평 + TOP(5행×2) + 경고 + 면책
    n_rev = min(len(reversals), 4)
    H = 150.0 + n_rev * 5.5
    fig_w = 8.6
    fig_h = fig_w * (H / W)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=132)
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

    y = 4.0
    # ── 헤더 ───────────────────────────────────────────────────────
    panel(3, y, 94, 18, fc=_ACCENT, ec=_ACCENT, rad=2.6)
    panel(3, y, 94, 18, fc="#1d4ed8", ec="#1d4ed8", rad=2.6)
    txt(7, y + 4.5, "DAILY BYTE · KR MARKET FLOW", size=9.5, color="#cfe3ff", weight="bold")
    txt(7, y + 10, "장 마감 후 투자주체별 수급 브리프", size=16, color="white", weight="bold")
    txt(7, y + 15, f"{date_iso} · KOSPI + KOSDAQ · pykrx EOD (KRX 공식)",
        size=9.5, color="#dbe9ff")
    y += 22

    # ── 시장 수급 총평 (막대) ──────────────────────────────────────
    section(4, y + 2, "시장 수급 총평 (당일 순매수, 억원)")
    y += 6
    bar_h = 6.0
    maxabs = max((abs(v) for _, v in inv_vals), default=1.0) or 1.0
    bx0 = 26.0          # 막대 시작 x
    bx_w = 56.0         # 막대 최대 폭
    for label, v in inv_vals:
        panel(4, y, 92, bar_h, fc=_PANEL, ec=_LINE, rad=1.4)
        txt(7, y + bar_h / 2, label, size=10.5, color=_MUTED, weight="bold")
        w = max(0.6, abs(v) / maxabs * bx_w)
        col = _POS if v >= 0 else _NEG
        ax.add_patch(Rectangle((bx0, y + bar_h / 2 - 1.3), w, 2.6,
                               facecolor=col, edgecolor="none"))
        txt(bx0 + w + 1.5, y + bar_h / 2, _eok(v), size=10.5, color=col, weight="bold")
        y += bar_h + 1.6
    y += 1

    # ── breadth ────────────────────────────────────────────────────
    if breadth.get("pct") is not None:
        panel(4, y, 92, 11, fc=_PANEL2, ec=_LINE, rad=1.8)
        pct = breadth.get("pct", 0)
        txt(9, y + 5.5, f"{pct:.0f}%", size=20, color=_ACCENT, weight="bold", ha="center")
        txt(20, y + 4, "순매수종목 비율 (breadth)", size=10.5, weight="bold")
        nb = breadth.get("net_buy_n"); tot = breadth.get("total_n")
        sub = f"{nb}/{tot}종목 순매수" if nb and tot else "시장 폭 지표"
        narrow = " · 자금 소수 대형주 응축" if pct and pct < 45 else ""
        txt(20, y + 8, sub + narrow, size=9.5, color=_MUTED)
        y += 13
    else:
        y += 1

    # ── 당일 순매수 TOP (2열: 외국인 / 기관) ────────────────────────
    section(4, y + 2, "당일 순매수 TOP 5", color=_ACCENTW)
    y += 6
    col_w = 45.0
    cols = [("외국인", fg_rows, 4.0), ("기관", inst_rows, 51.0)]
    block_h = 5 * 6.4 + 6
    for cname, rows, cx in cols:
        panel(cx, y, col_w, block_h, fc=_PANEL, ec=_LINE, rad=1.8)
        txt(cx + 3, y + 4, cname, size=10.5, color=_ACCENTW, weight="bold")
        ry = y + 9
        if not rows:
            txt(cx + 3, ry, "(데이터 없음)", size=9.5, color=_MUTED)
        for t, nm, net in rows:
            nm_s = (nm or t)[:9]
            txt(cx + 3, ry, nm_s, size=10, weight="bold")
            cpct = chg.get(str(t))
            cp_s = f"  {cpct:+.1f}%" if isinstance(cpct, (int, float)) else ""
            cp_col = _POS if (isinstance(cpct, (int, float)) and cpct >= 0) else _NEG
            txt(cx + col_w - 3, ry, f"{_eok(net)}", size=10, color=_POS, weight="bold", ha="right")
            if cp_s:
                txt(cx + col_w - 3, ry + 2.7, cp_s.strip(), size=8.2, color=cp_col, ha="right")
            ry += 6.4
        y_end = ry
    y += block_h + 2

    # ── 경고: 양→음 전환 ───────────────────────────────────────────
    if n_rev:
        wh = 6 + n_rev * 5.5
        panel(4, y, 92, wh, fc="#1f1320", ec="#5b2540", rad=1.8)
        ax.add_patch(Rectangle((7, y + 2.4), 1.1, 3.2, facecolor=_NEG, edgecolor="none"))
        txt(9.5, y + 4, "경고 시그널 — 양→음 전환 (5일 누적 매수 → 당일 매도)",
            size=11, color=_NEG, weight="bold")
        ry = y + 9
        for inv, tkr, nm, cum_net, t_net in reversals[:n_rev]:
            nm_s = (nm or tkr)[:10]
            txt(7, ry, f"{nm_s} ({tkr}) [{inv}]", size=9.8, weight="bold")
            txt(93, ry, f"5일 {_eok(cum_net)} → 당일 {_eok(t_net)}",
                size=9.8, color=_MUTED, ha="right")
            ry += 5.5
        y += wh + 2

    # ── 면책 ───────────────────────────────────────────────────────
    txt(4, H - 3, "수치: pykrx EOD (KRX 공식) · 환각 0", size=8.5, color=_MUTED)
    txt(96, H - 3, "수급 관찰 (교육·정보), 투자 권유 아님", size=8.5, color=_MUTED, ha="right")

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
