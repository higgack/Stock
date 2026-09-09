"""볼린저 밴드 순수 산식·판정 — 네트워크·디스크 없음.

🔋 Bollinger 보드(`bot/bollinger_board.py`)와 차트 탭(`bot/chart_data.py`)이
**같은 `bands()` 를 쓴다**. 복제하면 같은 종목의 상단밴드가 화면마다 달라지고
사용자가 눈으로 검산할 수 없다(실수 #33·#38).

판정(추이·수준·백분위·국면)도 전부 여기 순수 함수로 둔다 — 렌더·감사·CLI 가
같은 값을 보게 하려면 판정이 화면 안에 인라인으로 있으면 안 된다(#176·#35).

전략 출처: 사용자 2026-09-09 캡처("볼린저 밴드 상단 돌파 종목수를 활용한 시장
에너지 분석"). 문턱 20/10 은 원문이 **예시**라고 밝힌 값이고, 원문의 주의사항
("절댓값보다 추이", "선행 지표가 아니라 현재 에너지")을 판정 우선순위에 그대로
반영했다 — 자세한 근거는 `docs/plans/bollinger_board_plan.md` §5.
"""
from __future__ import annotations

# ── 원문(캡처)이 제시한 예시 문턱 ───────────────────────────────────────────
# "강세장: 5일 평균 종목수가 20 이상 · 약세장: 10 이하" — 코스피200+코스닥150
# (350 종목) 기준이다. 다른 시장은 유니버스 크기가 달라 그대로 쓸 수 없으므로
# **비율로 환산**하되, 그게 검증된 기준이 아니라는 사실을 화면이 밝힌다(#165).
_REF_UNIVERSE = 350
_REF_STRONG = 20
_REF_WEAK = 10

# 추이 판정 문턱 — 유니버스의 1%(최소 1종목). 절댓값이 아니라 **변화**를 보라는
# 원문 주의사항의 구현이고, 유니버스 크기에 비례해야 시장 간 뜻이 같아진다.
_TREND_PCT = 0.01

# 이력 백분위를 낼 수 있는 최소 세션 수. 이보다 적으면 '하위 12%' 같은 말이
# 표본 3개에서 나온 것일 수 있어 판정하지 않는다(#54 대조 부족 = 판정 불가).
_MIN_HISTORY = 60

# 원문이 '현금 60~70%' 를 예로 든 조건 = "5일 평균 10 이하가 두 달 연속".
# 거래일로 ≈40세션. 이보다 짧으면 아무 말도 안 한다(늘 뜨는 문구는 아무것도
# 재지 않는 것과 같다 — #25·#260).
_WEAK_STREAK_CITE = 40

LEVEL_LABEL = {"strong": "강세", "neutral": "중립", "weak": "약세"}
DIR_LABEL = {"up": "↑ 상승", "flat": "→ 횡보", "down": "↓ 하락"}

# 수준 × 추이 = 에너지 국면. 원문이 "현재 시장의 에너지를 나타내는 지표"라고
# 못박았으므로 이 이름들은 **현재 상태의 이름**이지 예측이 아니다.
PHASE = {
    ("strong", "up"): "에너지 확장",
    ("strong", "flat"): "강세 유지",
    ("strong", "down"): "과열 후 냉각",
    ("neutral", "up"): "회복 진행",
    ("neutral", "flat"): "중립",
    ("neutral", "down"): "약화 진행",
    ("weak", "up"): "바닥 회복 조짐",
    ("weak", "flat"): "에너지 소진",
    ("weak", "down"): "에너지 소진",
}


# ── 밴드 ────────────────────────────────────────────────────────────────────
def bands(close, n: int = 20, k: float = 2.0):
    """(중간선, 상단, 하단) — 이동평균 ± k·표준편차.

    `close` 는 pandas Series. 표준편차는 pandas 기본(ddof=1, 표본)이다 —
    `bot/chart_data.py` 의 차트 오버레이가 쓰던 식 그대로라 두 화면의 값이
    정의상 같다. ⚠️ HTS 는 모집단 표준편차(ddof=0)를 쓸 수도 있어 돌파 종목수가
    ±1~2 다를 수 있다(재지 않았으므로 화면은 '다를 수 있다'까지만 말한다, #165).
    """
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std()
    return mid, mid + k * sd, mid - k * sd


def _above_flags(close, n: int, k: float):
    """(밴드 밖 여부, 신규 여부, 밴드 유효 여부) — 세는 곳과 나열하는 곳이
    **같은 비교**를 쓰게 하는 단일 출처.

    복제하면 언젠가 카드의 개수와 표의 행수가 갈린다(#38·#45). 실제로 이
    함수를 뽑기 전에는 `breakouts_by_date` 와 `breakouts_on` 에 같은 식이 두
    벌 있었고, 뮤테이션 앵커가 2건으로 잡히면서 드러났다.
    """
    _mid, upper, _low = bands(close, n, k)
    valid = upper.notna()
    above = (close > upper) & valid
    prev_valid = valid.shift(1, fill_value=False)
    # ⚠️ 전일 밴드를 못 만든 날은 '신규인지 알 수 없는' 것이다 — 모르는 것을
    # 신규라고 부르지 않는다(#54).
    fresh = above & (~above.shift(1, fill_value=False)) & prev_valid
    return above, fresh, valid, upper


def breakouts_by_date(closes_by_ticker: dict, n: int = 20,
                      k: float = 2.0) -> dict:
    """{날짜: {count, new, scanned}} — 그날 **종가가 상단밴드 위**인 종목 수.

    `count` 는 '상태' 계수다(그날 밴드 밖에서 마감한 종목 수). 원문의 "상단
    돌파는 2.3% 의 낮은 확률" 은 **종가 분포** 진술이라 상태 계수가 그 확률에
    대응한다(교차 사건으로 세면 밴드에 올라탄 채 며칠 가는 강세 종목이 첫날만
    세어져 강세장에서 오히려 줄어든다 — 계획서 §1).

    `new` 는 그중 **전일에는 밴드 안이었던** 종목 수(🆕). ⚠️ 전일 밴드를 만들 수
    없었던 날(계열 앞머리)은 '신규인지 알 수 없는' 것이므로 세지 않는다 —
    모르는 것을 신규라고 부르지 않는다(#54).

    `scanned` 는 그날 밴드를 만들 수 있었던 종목 수 = **분모**. 날짜마다 다르다
    (신규상장·결측) → 비율은 반드시 그 날의 scanned 로 나눈다.
    """
    counts: dict = {}
    for _tk, close in (closes_by_ticker or {}).items():
        if close is None or len(close) < n:
            continue
        above, fresh, valid, _upper = _above_flags(close, n, k)
        for d, v in valid.items():
            if not bool(v):
                continue
            key = _date_key(d)
            rec = counts.setdefault(key, {"count": 0, "new": 0, "scanned": 0})
            rec["scanned"] += 1
            if bool(above.get(d, False)):
                rec["count"] += 1
                if bool(fresh.get(d, False)):
                    rec["new"] += 1
    return counts


def breakouts_on(closes_by_ticker: dict, dates, n: int = 20,
                 k: float = 2.0) -> dict:
    """{날짜: [행]} — 그날 상단밴드 위에서 마감한 **종목 목록**(표에 싣는 것).

    행 = {ticker, close, upper, over_pct, pct_chg, new}. `breakouts_by_date` 와
    **같은 밴드·같은 비교**를 쓴다 — 세는 곳과 나열하는 곳이 갈리면 카드의
    개수와 표의 행수가 어긋난다(#45 총계와 소계는 같은 리스트에서).
    """
    want = {str(d)[:10] for d in (dates or []) if d}
    out: dict = {d: [] for d in want}
    for tk, close in (closes_by_ticker or {}).items():
        if close is None or len(close) < n:
            continue
        above, fresh, _valid, upper = _above_flags(close, n, k)
        for i, (d, hit) in enumerate(above.items()):
            key = _date_key(d)
            if key not in want or not bool(hit):
                continue
            up = float(upper.iloc[i])
            cl = float(close.iloc[i])
            prev_close = float(close.iloc[i - 1]) if i else None
            out[key].append({
                "ticker": tk, "close": cl, "upper": up,
                "over_pct": (cl / up - 1) * 100.0 if up else None,
                "pct_chg": ((cl / prev_close - 1) * 100.0
                            if prev_close else None),
                "new": bool(fresh.iloc[i]),
            })
    return out


def _date_key(d) -> str:
    """인덱스 값 → 'YYYY-MM-DD' 문자열. JSON 캐시를 왕복해도 키가 안 변한다
    (디스크 캐시가 int/Timestamp 키를 문자열로 바꿔 화면이 통째로 비던 실수 #22)."""
    s = getattr(d, "strftime", None)
    return d.strftime("%Y-%m-%d") if s else str(d)[:10]


# ── 시계열 ──────────────────────────────────────────────────────────────────
def series_rows(series: dict) -> list[dict]:
    """{날짜: {...}} → 날짜 오름차순 리스트 [{date, count, new, scanned, basis}]."""
    out = []
    for d in sorted((series or {}).keys()):
        rec = dict(series[d] or {})
        rec["date"] = str(d)
        out.append(rec)
    return out


def avg5(rows: list[dict]) -> tuple[float | None, str]:
    """(최근 5세션 평균 돌파 종목수, 사유). 5세션 미만이면 **None** 이다.

    4개로 낸 평균을 '5일 평균'이라 부르면 라벨이 거짓말이다(#99 창이 다르면
    비교하지 말 것). 비었으면 왜 비었는지 같이 돌려준다(#43·#131)."""
    vals = [r.get("count") for r in (rows or []) if r.get("count") is not None]
    if len(vals) < 5:
        return None, f"세션 5개 미만({len(vals)}개)"
    return sum(vals[-5:]) / 5.0, ""


def avg5_series(rows: list[dict]) -> list[float | None]:
    """각 시점의 5세션 평균(앞 4개는 None) — 추이·백분위가 같이 쓴다."""
    vals = [r.get("count") for r in (rows or [])]
    out: list[float | None] = []
    for i in range(len(vals)):
        win = vals[max(0, i - 4):i + 1]
        out.append(sum(win) / 5.0 if len(win) == 5 and None not in win else None)
    return out


def level_thresholds(scanned) -> tuple[int | None, int | None]:
    """(강세 문턱, 약세 문턱) — 원문 예시 20/10 을 유니버스 크기로 환산.

    코스피200+코스닥150(350) 이면 정확히 (20, 10) 이고, S&P500(503) 이면
    (29, 14) 다. ⚠️ 환산값은 **검증된 기준이 아니다** — 화면이 그렇게 밝힌다.
    분모를 모르면 문턱도 없다(판정 불가, #54)."""
    try:
        s = float(scanned)
    except (TypeError, ValueError):
        return None, None
    if s <= 0:
        return None, None
    return (int(round(_REF_STRONG / _REF_UNIVERSE * s)),
            int(round(_REF_WEAK / _REF_UNIVERSE * s)))


def level_of(value, scanned) -> tuple[str | None, str]:
    """(수준 'strong'|'neutral'|'weak', 사유). 재료가 없으면 (None, 사유)."""
    if value is None:
        return None, "5일 평균 없음"
    strong, weak = level_thresholds(scanned)
    if strong is None:
        return None, "분모(스캔 종목수) 없음"
    if value >= strong:
        return "strong", ""
    if value <= weak:
        return "weak", ""
    return "neutral", ""


def trend(rows: list[dict]) -> dict:
    """5일선의 **추이** — 원문이 "절댓값보다 추이(변화율)가 중요하다"고 못박은 축.

    {d5, d20, pct5, dir, th, reason}. `dir` 은 |Δ5| 가 문턱(유니버스의 1%,
    최소 1종목)을 넘을 때만 ↑/↓ 이고 그 안이면 횡보다 — 문턱이 없으면 1종목
    흔들림도 '추세'가 된다."""
    out: dict = {"d5": None, "d20": None, "pct5": None, "dir": None,
                 "th": None, "reason": ""}
    a5 = avg5_series(rows)
    cur = a5[-1] if a5 else None
    if cur is None:
        out["reason"] = "5일 평균 없음"
        return out
    scanned = (rows[-1] or {}).get("scanned") if rows else None
    try:
        th = max(1.0, float(scanned) * _TREND_PCT)
    except (TypeError, ValueError):
        out["reason"] = "분모(스캔 종목수) 없음"
        return out
    out["th"] = th
    prev5 = a5[-6] if len(a5) >= 6 else None
    prev20 = a5[-21] if len(a5) >= 21 else None
    if prev5 is None:
        out["reason"] = "5세션 전 비교값 없음"
        return out
    d5 = cur - prev5
    out["d5"] = d5
    out["pct5"] = (d5 / prev5 * 100.0) if prev5 else None
    if prev20 is not None:
        out["d20"] = cur - prev20
    out["dir"] = "up" if d5 > th else "down" if d5 < -th else "flat"
    return out


def energy_phase(level: str | None, direction: str | None) -> str | None:
    """수준 × 추이 → 국면 이름. 어느 한쪽이라도 없으면 **None**(판정 불가)."""
    if not level or not direction:
        return None
    return PHASE.get((level, direction))


def history_pct_rank(rows: list[dict]) -> tuple[float | None, str]:
    """(오늘 5일선의 이력 백분위 0~100, 사유).

    유니버스 크기가 시장마다 달라 원문 문턱을 그대로 못 믿는 비-KR 의 **대조군**
    이다 — 자기 이력과 비교하므로 크기와 무관하다. 표본이 얇으면 판정하지
    않는다(#54)."""
    a5 = [v for v in avg5_series(rows) if v is not None]
    if len(a5) < _MIN_HISTORY:
        return None, f"이력 부족({len(a5)}세션 · {_MIN_HISTORY} 필요)"
    cur = a5[-1]
    below = sum(1 for v in a5 if v < cur)
    return below / len(a5) * 100.0, ""


def weak_streak(rows: list[dict]) -> int:
    """5일선이 **연속으로** 약세 문턱 이하였던 세션 수(최근부터 거슬러).

    누적 합이 아니라 연속이다 — 중간에 한 번이라도 올라오면 0 으로 리셋된다.
    원문의 '두 달 연속' 조건을 판정하는 재료이고, 판정 자체(현금 비중)는
    우리가 하지 않는다(§5.3)."""
    a5 = avg5_series(rows)
    streak = 0
    for i in range(len(rows) - 1, -1, -1):
        v = a5[i]
        if v is None:
            break
        _strong, weak = level_thresholds((rows[i] or {}).get("scanned"))
        if weak is None or v > weak:
            break
        streak += 1
    return streak


def weak_streak_note(streak: int) -> str:
    """원문 예시(현금 60~70%) 인용문 — **조건을 충족할 때만**. 아니면 빈 문자열.

    우리가 비중을 처방하지 않는다. 원문이 그 조건에서 무엇을 예로 들었는지만
    사실로 옮긴다(#165 재지 않은 것을 단정하지 않기)."""
    if streak < _WEAK_STREAK_CITE:
        return ""
    return (f"원문 예시 조건 충족(5일선 약세 {streak}세션 연속 ≈ 두 달) — "
            "원문은 이때 현금 비중 60~70% 를 예로 든다. 참고용이며 "
            "이 보드는 비중을 처방하지 않는다.")


# ── 저장 병합 ───────────────────────────────────────────────────────────────
RECENT_REWRITE = 25    # 이 세션 수만큼은 매 실행 덮어쓴다(늦게 온 봉 정정)
MAX_ROWS = 400         # 약 1.5년


def merge_series(stored: dict, fresh: dict, *, partial: bool = False) -> dict:
    """저장분 + 이번 수집분 → 새 시계열(순수).

    ① fresh 의 **최근 25세션은 덮어쓴다** — 원천이 봉을 늦게 채우면 그날 값이
       나중에 달라지는데, 멱등 키가 굵으면 처음 쓴 값이 영원히 굳는다(#299).
    ② 그보다 오래된 날짜는 **저장분 우선** — 유니버스는 시간이 지나면 바뀌므로
       과거를 오늘 유니버스로 다시 쓰면 이력이 조용히 변조된다.
    ③ `partial`(스캔이 유니버스를 충분히 못 덮음)이면 **아무것도 쓰지 않는다** —
       부분 결과를 완전본으로 구우면 그날 값이 영구히 낮게 남는다(#280).
    """
    if partial:
        return dict(stored or {})
    out = {str(k): dict(v) for k, v in (stored or {}).items()}
    keys = sorted(str(k) for k in (fresh or {}))
    recent = set(keys[-RECENT_REWRITE:])
    for k in keys:
        rec = dict(fresh[k] if k in fresh else fresh[str(k)])
        if k in recent or k not in out:
            # ⚠️ 한 번 **실제로 관측한 날**(live)은 나중 실행이 되짚어 다시
            # 계산해도 live 로 남는다 — 안 그러면 어제 live 로 본 행이 오늘
            # 백필로 강등돼 '옅은 막대'가 늘어난다(#43 라벨이 사실과 어긋남).
            if (out.get(k) or {}).get("basis") == "live":
                rec["basis"] = "live"
            out[k] = rec
    if len(out) > MAX_ROWS:
        for k in sorted(out)[:len(out) - MAX_ROWS]:
            out.pop(k, None)
    return out
