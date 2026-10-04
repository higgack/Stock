"""KIS 수급 4종 — 공식 샘플 필드 · 날짜 · 금액 단위 · 단일 수집 함수 · 감사 (실수 #433).

2026-10-04 까지:
  (a) ``stock_snapshot`` 과 ``dashboard`` 의 수급 블록 두 사본이 ``KisClient`` 에 **없는**
      ``_ready()`` 를 불러 AttributeError 가 DEBUG 에 삼켜졌다 — 수급 탭의 KIS 칸은 한 번도
      채워지지 않았다. 분석 경로의 ``get_kis().get_price()`` 두 곳도 같은 병이었다.
  (b) 분석 파이프라인(``agent_utils``)은 같은 메서드를 게이트 없이 부르는데, 메서드가 공식
      샘플과 다른 필드 이름을 읽어 개인·기관 세부·대주·프로그램이 늘 None 이었고 '당일
      순매수' 는 **수량(주)** 을 만원으로 읽어 억원으로 바꿔 프롬프트에 실었다.
  (c) 시장 분류 코드로 코스닥에 공식 값이 아닌 ``Q`` 를 보냈다.
판 3(같은 날 독립 리뷰 H2·M2·M3): 거래 0 인 확정일을 '미제공' 으로 버려 9거래일 구간을
'5거래일' 이라 불렀고, 금액 단위를 응답 전체 다수결로 재 오래된 행이 이길 수 있었고,
신용·공매도·프로그램은 장중 오늘 행(자리표시·부분값)을 그대로 실었다.

픽스처의 필드 이름은 공식 샘플 COLUMN_MAPPING 그대로다(github.com/koreainvestment/
open-trading-api ``examples_llm/domestic_stock/{inquire_investor,daily_credit_balance,
daily_short_sale,program_trade_by_stock_daily}/chk_*.py``). **값은 합성**이다 — 실제
응답의 행 수·순서·금액 단위는 개발 샌드박스에서 잴 수 없어 ``kis_flow_audit`` 가 운영에서
매일 잰다(이 파일은 그 가정이 깨졌을 때 코드가 어떻게 말하는지를 잰다). 날짜는 실제 KRX
거래일이고(09-24~26 추석·10-03 개천절 제외) 시계는 고정한다(#249·#425).
"""
from __future__ import annotations

import ast
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_KST = timezone(timedelta(hours=9))
# 10-02(금) 장중 — 그날 행은 잠정이다.
_NOW = datetime(2026, 10, 2, 14, 0, tzinfo=_KST)
_AFTER_CLOSE = datetime(2026, 10, 2, 20, 30, tzinfo=_KST)


# ─── 픽스처(공식 필드 이름 · 합성 값) ─────────────────────────────────────────

def _inv_row(date, i, *, unit=1_000_000, close=70_000, filled=True, drop=()):
    r = {"stck_bsop_date": date, "stck_clpr": str(close)}
    for j, p in enumerate(("frgn", "orgn", "prsn")):
        buy = (1_000_000 + 10_000 * i + 1_000 * j) if filled else 0
        sell = (900_000 + 5_000 * i + 2_000 * j) if filled else 0
        r[f"{p}_shnu_vol"] = str(buy)
        r[f"{p}_seln_vol"] = str(sell)
        r[f"{p}_shnu_tr_pbmn"] = str(round(buy * close / unit))
        r[f"{p}_seln_tr_pbmn"] = str(round(sell * close / unit))
        r[f"{p}_ntby_qty"] = str(buy - sell)
        r[f"{p}_ntby_tr_pbmn"] = str(round(buy * close / unit) - round(sell * close / unit))
    for k in drop:
        r.pop(k, None)
    return r


# 실제 KRX 거래일(오래된 날부터) — 마지막(10-02)은 _NOW 에선 장중이라 잠정이다.
_DATES = ["20260922", "20260923", "20260928", "20260929", "20260930", "20261001", "20261002"]


def _inv_payload(*, unit=1_000_000, head_unfilled=True, decoy=True, zero=()):
    """오래된 날부터 준다(원천 순서에 기대지 않는지 재려고). 가장 최근(10-02)은
    장중처럼 비어 있다. ``decoy`` 면 옛 판이 읽던 이름(``indv_*``)에 **다른 값**을 둔다
    — 코드가 옛 이름으로 되돌아가면 값이 바뀌어 잡힌다. ``zero`` 의 날은 거래가 0 인
    확정일이다(거래 정지 등)."""
    rows = []
    for i, d in enumerate(_DATES):
        filled = not ((head_unfilled and d == _DATES[-1]) or d in zero)
        r = _inv_row(d, i, unit=unit, filled=filled)
        if decoy:
            r["indv_ntby_qty"] = "-999999999"
            r["indv_ntby_tr_pbmn"] = "-999999999"
            r["pnsn_ntby_tr_pbmn"] = "123"
        rows.append(r)
    return {"rt_cd": "0", "output": rows}


def _credit_row(d, stcn, rate, stln):
    return {"deal_date": d, "whol_loan_rmnd_stcn": str(stcn), "whol_loan_rmnd_rate": rate,
            "whol_stln_rmnd_stcn": str(stln), "whol_stln_rmnd_rate": "0.02",
            "stln_rmnd_qty": "77"}


def _credit_payload(today_row=None):
    rows = [_credit_row("20260929", 1000, "0.50", 10), _credit_row("20261001", 1200, "4.20", 35)]
    if today_row:
        rows.append(today_row)
    return {"rt_cd": "0", "output": rows}


def _short_row(d, qty, rlim):
    return {"stck_bsop_date": d, "ssts_cntg_qty": str(qty), "ssts_vol_rlim": rlim,
            "ssts_tr_pbmn_rlim": "16.00", "avrg_prc": "70200"}


def _short_payload(today_row=None):
    rows = [_short_row("20260930", 500, "3.10"), _short_row("20261001", 800, "16.40")]
    if today_row:
        rows.append(today_row)
    return {"rt_cd": "0", "output1": {"stck_prpr": "70000"}, "output2": rows}


def _prog_row(d, i, *, unit=1_000_000, close=70_000, zero=False):
    buy, sell = (0, 0) if zero else (500_000 + 1_000 * i, 300_000 + 2_000 * i)
    return {"stck_bsop_date": d, "stck_clpr": str(close), "acml_vol": "9000000",
            "whol_smtn_shnu_vol": str(buy), "whol_smtn_seln_vol": str(sell),
            "whol_smtn_shnu_tr_pbmn": str(round(buy * close / unit)),
            "whol_smtn_seln_tr_pbmn": str(round(sell * close / unit)),
            "whol_smtn_ntby_qty": str(buy - sell),
            "whol_smtn_ntby_tr_pbmn": str(round(buy * close / unit)
                                          - round(sell * close / unit)),
            # 옛 판이 읽던(공식 목록에 없는) 이름 — 읽히면 안 된다
            "pgtr_seln_amt1": "999", "pgtr_shnu_amt1": "1"}


def _prog_payload(unit=1_000_000, close=70_000, *, today=None, zero=()):
    """오늘(10-02) 행은 ``today`` 가 'partial' 이면 장중 부분값으로 붙인다(부분값이 '하루'
    로 실리면 안 된다)."""
    rows = [_prog_row(d, i, unit=unit, close=close, zero=d in zero)
            for i, d in enumerate(_DATES[:-1])]
    if today == "partial":
        rows.append(_prog_row(_DATES[-1], 50, unit=unit, close=close))
    return {"rt_cd": "0", "output": list(reversed(rows))}


@pytest.fixture
def kc(monkeypatch, tmp_path):
    """KIS 클라이언트 — 캐시는 이 테스트 전용 디렉터리, 자격증명은 있는 것으로, 시계는
    10-02 장중으로 고정. 차단기·토큰 실패 기억은 비운다(테스트끼리 새지 않게)."""
    from bot import kis_client
    monkeypatch.setattr(kis_client, "_CACHE_DIR", tmp_path / "kis")
    monkeypatch.setattr(kis_client, "_app_key", lambda: "k")
    monkeypatch.setattr(kis_client, "_app_secret", lambda: "s")
    monkeypatch.setattr(kis_client, "_now_kst", lambda: _NOW)
    monkeypatch.setattr(kis_client, "_BREAKER", {})
    monkeypatch.setattr(kis_client, "_TOKEN_FAIL_UNTIL", 0.0)
    monkeypatch.setattr(kis_client, "_FLOW_PURGED", True)
    kis_client._MISSING_WARNED.clear()
    return kis_client


_OK = {"kind": "ok", "status": 200, "msg": ""}
_RT1 = {"kind": "rt_cd", "status": 200, "msg": "rt_cd=1 테스트 거절"}


def _router(monkeypatch, kc, table):
    """``_get_ex`` 대역. 값: dict = 정상 응답 · None = rt_cd 거절 · (None, 사유) = 그 사유 ·
    호출 가능 = 파라미터를 받아 위 셋 중 하나를 돌려준다."""
    calls: list = []

    def _get_ex(path, tr_id, params, custtype=None):
        calls.append((path, tr_id, dict(params)))
        v = table.get(tr_id)
        v = v(params) if callable(v) else v
        if isinstance(v, tuple):
            return v
        return (v, dict(_OK)) if isinstance(v, dict) else (None, dict(_RT1))

    monkeypatch.setattr(kc, "_get_ex", _get_ex)
    return calls


def _ok_table():
    return {"FHKST01010900": _inv_payload(), "FHPST04760000": _credit_payload(),
            "FHPST04830000": _short_payload(), "FHPPG04650201": _prog_payload()}


def _won_of(date, who="orgn", *, unit=1_000_000):
    i = _DATES.index(date)
    return int(_inv_row(date, i, unit=unit)[f"{who}_ntby_tr_pbmn"]) * unit


# ─── 투자자 파서 ─────────────────────────────────────────────────────────────

def test_investor_reads_official_fields_by_date_and_pends_only_todays_row(kc):
    r = kc.parse_investor_flow(_inv_payload())
    assert r["schema"] == kc._FLOW_SCHEMA == 3
    # 원천은 오래된 날부터 줬다 — 날짜로 정렬해 가장 최근 **확정** 날을 쓴다
    assert r["asof"] == "2026-10-01" and r["latest"]["date"] == "2026-10-01"
    assert r["pending"] == 1 and "2026-10-02(오늘 — KRX 거래가 끝나는 20:00 전" in r["pending_note"]
    want = _inv_row("20261001", _DATES.index("20261001"))
    # 개인은 공식 이름 prsn_* — 옛 이름(indv_*)의 미끼 값이 읽히면 안 된다
    assert r["latest"]["qty"]["individual"] == int(want["prsn_ntby_qty"])
    assert r["latest"]["qty"]["foreign"] == int(want["frgn_ntby_qty"])
    # 금액 = 원천 값 × 단위(원). 단위는 싣는 행으로 잰 ×100만
    assert r["unit_won"] == 1_000_000 and r["unit_bad"] == [], r["unit_note"]
    assert r["latest"]["won"]["foreign"] == int(want["frgn_ntby_tr_pbmn"]) * 1_000_000
    assert r["latest"]["unit_ok"] is True
    # 창 = 그날까지 최근 5거래일(09-23~10-01) — 달력으로 대조돼 '5거래일'
    w = r["window"]
    assert (w["from"], w["to"], w["days"], w["label"]) == (
        "2026-09-23", "2026-10-01", 5, "5거래일"), w
    assert w["sessions_checked"] is True and w["gaps"] == [] and w["note"] == ""
    assert w["won"]["institution"] == sum(_won_of(d) for d in _DATES[1:6])
    assert "inst_breakdown" not in r and r["missing"] == [] and r["blank"] == 0


def test_zero_trade_confirmed_day_counts_as_zero_not_as_missing(kc):
    """거래가 0 인 확정일(정지일)은 0 으로 센다 — 판 2 는 '거래량이 0 이면 미제공' 으로
    버려 창이 거래일을 건너뛰어 늘어났고(9거래일을 '5거래일'), 최신 쪽이면 '원천이 아직
    안 채움' 이라는 틀린 사유를 달았다(리뷰 H2)."""
    r = kc.parse_investor_flow(_inv_payload(zero=("20260930",)))
    w = r["window"]
    assert (w["from"], w["to"], w["label"]) == ("2026-09-23", "2026-10-01", "5거래일"), w
    assert w["won"]["institution"] == sum(_won_of(d) for d in ("20260923", "20260928",
                                                              "20260929", "20261001"))
    # 가장 최근 확정일이 0 이면 그날이 최신이다 — 잠정이 아니다
    r2 = kc.parse_investor_flow(_inv_payload(zero=("20261001",)))
    assert r2["asof"] == "2026-10-01" and r2["pending"] == 1
    assert r2["latest"]["qty"]["foreign"] == 0 and r2["latest"]["won"]["foreign"] == 0
    assert "2026-10-01" not in r2["pending_note"]


def test_todays_row_after_day_end_is_used_when_it_has_trades(kc):
    full = kc.parse_investor_flow(_inv_payload(head_unfilled=False), now=_AFTER_CLOSE)
    assert full["asof"] == "2026-10-02" and full["pending"] == 0
    assert full["window"]["from"] == "2026-09-28"
    # 마감 뒤에도 비었거나 0 이면 아직 확정으로 보지 않는다(사유를 사실대로)
    empty = kc.parse_investor_flow(_inv_payload(), now=_AFTER_CLOSE)
    assert empty["asof"] == "2026-10-01" and empty["pending"] == 1
    assert "마감 뒤에도 값이 비었거나 0" in empty["pending_note"]
    # 장중엔 채워져 있어도(부분값일 수 있다) 잠정이다
    early = kc.parse_investor_flow(_inv_payload(head_unfilled=False))
    assert early["asof"] == "2026-10-01" and early["pending"] == 1


def test_day_end_comes_from_kr_session_single_source(kc):
    from bot.kr_session import VENUES
    end = next(sh * 60 + sm for sh, sm, _e, _m, key, _l in VENUES["KRX"] if key == "after_close")
    assert kc._krx_day_end_min() == end == 20 * 60
    assert kc._last_day_end(_NOW) == datetime(2026, 10, 1, 20, 0, tzinfo=_KST)
    assert kc._last_day_end(_AFTER_CLOSE) == datetime(2026, 10, 2, 20, 0, tzinfo=_KST)


def test_future_dated_row_is_pending(kc):
    p = _inv_payload(head_unfilled=False)
    p["output"].append(_inv_row("20261005", 9))
    r = kc.parse_investor_flow(p)
    assert r["asof"] == "2026-10-01" and r["pending"] == 2
    assert "2026-10-05(오늘보다 뒤 날짜)" in r["pending_note"]


def _blank(row):
    """값 칸을 전부 빈 문자열로 — 원천이 그날 행은 줬는데 값을 비워 둔 모양."""
    return {k: ("" if k not in ("stck_bsop_date", "deal_date") else v) for k, v in row.items()}


def test_blank_rows_are_skipped_for_latest_and_counted(kc):
    """확정일인데 값이 전부 빈 행은 '최신' 이 될 수 없다 — 빈 칸을 0 이나 값으로 싣지
    않고 건너뛴 사실을 센다(#43). 0 은 값이고 빈 칸은 값이 아니다."""
    p = _inv_payload()
    i = _DATES.index("20261001")
    p["output"][i] = _blank(p["output"][i])
    r = kc.parse_investor_flow(p)
    assert r["asof"] == "2026-09-30" and r["blank"] == 1
    assert r["window"]["to"] == "2026-09-30" and r["window"]["label"] == "5거래일"
    txt = kc.format_kis_block({"investor_flow": r})
    assert "(원천이 값을 비워 둔 최근 1일은 뺐습니다)" in txt
    pg = _prog_payload()
    pg["output"][0] = _blank(pg["output"][0])                    # 10-01(최신)이 빈 행
    rp = kc.parse_program_daily(pg)
    assert rp["asof"] == "2026-09-30" and rp["blank"] == 1


def test_window_refuses_a_partial_sum(kc):
    """창의 한 날이라도 비면 그 칸의 합을 만들지 않는다 — 4일 합을 5거래일 누적이라
    부르면 거짓말이다(#99). 다른 칸은 그대로, 이유는 note 가 말한다."""
    p = _inv_payload()
    p["output"][_DATES.index("20260929")].pop("frgn_ntby_tr_pbmn")
    r = kc.parse_investor_flow(p)
    assert r["window"]["won"]["foreign"] is None
    assert r["window"]["won"]["institution"] is not None
    assert "값이 빈 날" in r["window"]["note"]
    assert "frgn_ntby_tr_pbmn" not in r["missing"]               # 최신(10-01)엔 있다


def test_window_refuses_when_the_source_skips_a_trading_day(kc):
    """원천이 거래일 하나를 아예 빼고 주면 5개 행이 5거래일이 아니다 — 달력으로 대조해
    합을 만들지 않고 그 날을 이름으로 말한다(리뷰 H2)."""
    p = _inv_payload()
    p["output"] = [r for r in p["output"] if r["stck_bsop_date"] != "20260929"]
    w = kc.parse_investor_flow(p)["window"]
    assert w["gaps"] == ["2026-09-29"] and w["from"] == "2026-09-22"
    assert all(v is None for v in w["won"].values())
    assert "2026-09-29" in w["note"] and w["label"] == "최근 5개 영업일 행"


def test_window_label_without_a_calendar_does_not_claim_trading_days(kc, monkeypatch):
    from bot import market_calendar as mc
    monkeypatch.setattr(mc, "sessions_between", lambda *a, **k: None)
    w = kc.parse_investor_flow(_inv_payload())["window"]
    assert w["label"] == "최근 5개 영업일 행" and w["sessions_checked"] is False
    assert w["won"]["institution"] is not None


def test_missing_official_field_is_reported_once_and_audit_mode_is_silent(kc, caplog):
    p = _inv_payload(head_unfilled=False)
    for row in p["output"]:
        row.pop("prsn_ntby_qty")
    with caplog.at_level(logging.WARNING, logger="bot.kis"):
        quiet = kc.parse_investor_flow(p, warn=False)
        assert kc._MISSING_WARNED == set()                       # 진단은 예산을 안 쓴다
        r1 = kc.parse_investor_flow(p)
        kc.parse_investor_flow(p)
    assert quiet["missing"] == r1["missing"] == ["prsn_ntby_qty"]
    assert r1["latest"]["qty"]["individual"] is None
    warns = [x for x in caplog.records if "prsn_ntby_qty" in x.getMessage()]
    assert len(warns) == 1, [w.getMessage() for w in warns]


# ─── 금액 단위 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("unit", [1, 1_000, 10_000, 1_000_000, 100_000_000])
def test_unit_calibration_finds_each_candidate(kc, unit):
    samples = [(1_000_000 + 7 * i, 70_000, 1_000_000 * 70_000 / unit * (0.8 + 0.05 * i))
               for i in range(6)]
    got, note = kc._calibrate_unit(samples)
    assert got == unit, note


def test_unit_calibration_refuses_rather_than_guesses(kc):
    # 표본 부족
    assert kc._calibrate_unit([(10, 100, 1)])[0] is None
    # 반반 갈림 → 모른다
    mixed = [(1000, 100, 100)] * 3 + [(1000, 100, 0.1)] * 3
    assert kc._calibrate_unit(mixed)[0] is None
    # 0.5~2배 창의 경계 — 비(수량×가격÷금액)가 1.9·0.5 면 ×1, 2.5·0.4 면 어느 후보와도
    # 안 맞아 거절(후보끼리 10배 이상 떨어져 창이 겹치지 않는다)
    assert kc._calibrate_unit([(10, 19, 100)] * 3)[0] == 1
    assert kc._calibrate_unit([(10, 5, 100)] * 3)[0] == 1
    for p in (25, 4):
        far = kc._calibrate_unit([(10, p, 100)] * 3)
        assert far[0] is None and "맞지 않습니다" in far[1], (p, far)
    # 0 · 음수 · None 은 표본이 아니다
    assert kc._calibrate_unit([(0, 1, 1), (None, 1, 1), (-5, 1, 1), (5, 1, -1)])[0] is None


def test_unit_agreement_window_and_two_thirds_are_locked(kc):
    """합의는 0.5~2배 창 **안**에서 3분의 2 — 창을 넓히면 5배 어긋난 표본이 '합의' 로
    세어지고(반이 5배 어긋나도 단위를 고른다), 만장일치로 좁히면 20% 가 어긋난 정상
    응답을 버린다(리뷰 생존 뮤테이션 M06·M07b)."""
    # 6/10 만 ×1 창 안(나머지 4개는 5배 어긋남) — 중앙 비는 ×1 에 걸리지만 합의 60% 는
    # 3분의 2 미만 → 모른다. 창을 0.1~10 으로 넓히면 10/10 '합의' 가 된다.
    most_not_enough = [(10, 1, 10)] * 6 + [(10, 5, 10)] * 4
    assert kc._calibrate_unit(most_not_enough)[0] is None
    # 8/10 이 ×1 · 2/10 이 5배 어긋남 → ×1(3분의 2 이상)
    most = [(10, 1, 10)] * 8 + [(10, 5, 10)] * 2
    assert kc._calibrate_unit(most)[0] == 1


def test_unit_is_measured_on_displayed_rows_and_checked_per_row(kc):
    """단위는 화면에 싣는 행(최신+창)으로 잰다 — 응답 전체 다수결이면 오래된 행이 최근
    행을 이긴다(며칠 전 분할 뒤 옛 종가가 수정주가가 아니면 비가 10배 갈린다, 리뷰 M2)."""
    p = _inv_payload(head_unfilled=False)
    # 옛 행 25개(창 밖)는 금액이 10배 다른 단위로 온다 — 다수결이면 이쪽이 이긴다
    old = [_inv_row(f"202608{d:02d}", 30 + d, unit=10_000_000) for d in range(1, 26)]
    p["output"] = old + p["output"]
    r = kc.parse_investor_flow(p, now=_AFTER_CLOSE)
    assert r["unit_won"] == 1_000_000 and r["unit_bad"] == [], r["unit_note"]
    # 창 안의 한 날이 어긋나면 그날이 최신이면 그날 금액·창 합 둘 다 싣지 않는다
    p2 = _inv_payload(head_unfilled=False)
    p2["output"][-1] = _inv_row("20261002", 6, unit=10_000_000)
    r2 = kc.parse_investor_flow(p2, now=_AFTER_CLOSE)
    assert r2["unit_won"] == 1_000_000 and r2["unit_bad"] == ["2026-10-02"]
    assert r2["latest"]["unit_ok"] is False
    assert all(v is None for v in r2["latest"]["won"].values())
    assert all(v is None for v in r2["window"]["won"].values())
    assert "단위가 맞지 않는 날" in r2["window"]["note"]
    assert r2["latest"]["qty"]["foreign"] is not None             # 수량은 그대로


def test_unit_unknown_keeps_quantities_and_drops_amounts(kc):
    p = _inv_payload(head_unfilled=False)
    for row in p["output"]:                                     # 대금 칸을 비워 단위를 못 재게
        for k in list(row):
            if k.endswith(("shnu_tr_pbmn", "seln_tr_pbmn")):
                row[k] = "0"
    r = kc.parse_investor_flow(p)
    assert r["unit_won"] is None and "표본" in r["unit_note"] and r["unit_samples"] == 0
    assert r["latest"]["qty"]["foreign"] is not None and r["latest"]["unit_ok"] is False
    assert all(v is None for v in r["latest"]["won"].values())
    assert all(v is None for v in r["window"]["won"].values())
    assert r["window"]["note"] == "금액 단위를 확정하지 못했습니다"


def test_zero_amount_needs_no_unit(kc):
    assert kc._won(0, None) == 0 and kc._won(None, 1) is None and kc._won(5, None) is None
    assert kc._won(-3, 1_000) == -3_000


# ─── 신용·공매도·프로그램 파서 ────────────────────────────────────────────────

def test_credit_reads_official_names_and_latest_confirmed_deal_date(kc):
    r = kc.parse_credit_balance(_credit_payload())
    assert r["asof"] == "2026-10-01" and r["pending"] == 0
    assert r["credit_balance_shares"] == 1200 and r["credit_balance_pct"] == 4.2
    # 대주 잔고 = whol_stln_rmnd_stcn (옛 판의 stln_rmnd_qty=77 이 읽히면 안 된다)
    assert r["credit_short_shares"] == 35
    assert "loan_balance_shares" not in r and "credit_balance_amt" not in r


def test_credit_and_short_skip_todays_placeholder_row(kc):
    """장중 오늘 행(0·빈 값 자리표시)이 '신용잔고율 0.00%' 로 실리면 안 된다(리뷰 M3).
    마감 뒤 값이 있으면 쓴다."""
    ph = _credit_row("20261002", 0, "0.00", 0)
    r = kc.parse_credit_balance(_credit_payload(ph))
    assert r["asof"] == "2026-10-01" and r["pending"] == 1 and r["credit_balance_pct"] == 4.2
    real = _credit_row("20261002", 1300, "4.40", 40)
    assert kc.parse_credit_balance(_credit_payload(real))["asof"] == "2026-10-01"
    assert kc.parse_credit_balance(_credit_payload(real), now=_AFTER_CLOSE)["asof"] == "2026-10-02"
    zero_after = kc.parse_credit_balance(_credit_payload(ph), now=_AFTER_CLOSE)
    assert zero_after["asof"] == "2026-10-01" and "마감 뒤에도" in zero_after["pending_note"]
    s = kc.parse_short_sale(_short_payload(_short_row("20261002", 0, "0.00")))
    assert s["asof"] == "2026-10-01" and s["short_ratio_pct"] == 16.4 and s["pending"] == 1
    s2 = kc.parse_short_sale(_short_payload(_short_row("20261002", 0, "0.00")), now=_AFTER_CLOSE)
    assert s2["asof"] == "2026-10-01" and "마감 뒤에도" in s2["pending_note"]


def test_short_sale_reads_output2_latest(kc):
    r = kc.parse_short_sale(_short_payload())
    assert r["asof"] == "2026-10-01" and r["short_qty"] == 800
    assert r["short_ratio_pct"] == 16.4 and r["short_amt_ratio_pct"] == 16.0


def test_program_daily_reads_whole_sum_and_no_arbitrage_split(kc):
    r = kc.parse_program_daily(_prog_payload())
    assert r["asof"] == "2026-10-01" and r["unit_won"] == 1_000_000
    i = 5                                                       # _DATES[5] = 10-01
    buy, sell = 500_000 + 1_000 * i, 300_000 + 2_000 * i
    assert r["latest"]["qty"] == buy - sell
    assert r["latest"]["won"] == (round(buy * 70_000 / 1e6) - round(sell * 70_000 / 1e6)) * 1_000_000
    assert r["window"]["days"] == 5 and r["window"]["to"] == "2026-10-01"
    assert r["window"]["label"] == "5거래일"
    assert not {"arb_net", "nonarb_net", "arb_buy"} & set(r)


def test_program_partial_today_is_pending_and_latest_is_by_date(kc):
    """장중 오늘 행은 부분값이다 — 판 2 는 그걸 '하루' 로 12시간 구웠다(리뷰 M3 · M13).
    최신은 행 순서가 아니라 날짜로 고른다(M49)."""
    r = kc.parse_program_daily(_prog_payload(today="partial"))
    assert r["asof"] == "2026-10-01" and r["pending"] == 1
    assert "2026-10-02" in r["pending_note"]
    p = _prog_payload()
    p["output"] = list(reversed(p["output"]))                    # 오래된 날이 [0]
    assert kc.parse_program_daily(p)["asof"] == "2026-10-01"
    # 마감 뒤라도 오늘 행에 프로그램 체결이 0 이면 확정으로 보지 않는다(오늘은 '원천이
    # 아직 안 채움' 과 '정말 0' 을 못 가른다 — 다음 날엔 지난 날짜라 0 으로 센다)
    z = _prog_payload()
    z["output"].insert(0, _prog_row("20261002", 6, zero=True))
    za = kc.parse_program_daily(z, now=_AFTER_CLOSE)
    assert za["asof"] == "2026-10-01" and "마감 뒤에도" in za["pending_note"]
    nxt = kc.parse_program_daily(z, now=datetime(2026, 10, 5, 10, 0, tzinfo=_KST))
    assert nxt["asof"] == "2026-10-02" and nxt["latest"]["won"] == 0


def test_program_zero_day_counts_and_empty_cells_refuse_the_window(kc):
    """프로그램 매매가 없던 확정일은 0 이다(M14 — 판 2 는 빈 행처럼 건너뛰었다). 창의 한
    칸이 비면 합을 만들지 않는다(M53 — 투자자에만 잠겨 있던 #99 거절)."""
    r = kc.parse_program_daily(_prog_payload(zero=("20260930",)))
    assert r["window"]["from"] == "2026-09-23" and r["window"]["won"] is not None
    p = _prog_payload()
    p["output"][1]["whol_smtn_ntby_tr_pbmn"] = ""                # 09-30 이 빈 칸
    w = kc.parse_program_daily(p)["window"]
    assert w["won"] is None and "값이 빈 날" in w["note"]


def test_rows_of_accepts_a_single_dict_and_dated_drops_bad_dates(kc):
    """원천이 단건을 dict 로 주면 한 행이다(M50). 날짜는 8자리 숫자만(M51) — 아니면 그
    행이 '최신' 이 될 수 있다."""
    assert kc._rows_of({"output": {"a": 1}}) == [{"a": 1}]
    assert kc._rows_of({"output": [{"a": 1}, "x", 3]}) == [{"a": 1}]
    assert kc._rows_of(None) == [] and kc._rows_of({"output": "x"}) == []
    rows = [{"d": d} for d in ("20261001", "", "2026-10-09", "2026100", "202610091",
                               "2026100A", "20260930")]
    assert [d for d, _r in kc._dated(rows, "d")] == ["2026-10-01", "2026-09-30"]
    single = {"rt_cd": "0", "output": _credit_row("20261001", 1200, "4.20", 35)}
    assert kc.parse_credit_balance(single)["credit_short_shares"] == 35


# ─── 원천 조회 사유(_get_ex) ─────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.mark.parametrize("make, kind, status", [
    (lambda: _Resp(200, {"rt_cd": "0", "output": []}), "ok", 200),
    (lambda: _Resp(404, {}), "http4xx", 404),
    (lambda: _Resp(503, {}), "http5xx", 503),
    (lambda: _Resp(200, {"rt_cd": "1", "msg1": "조회할 자료가 없습니다"}), "rt_cd", 200),
    (lambda: _Resp(200, ValueError("not json")), "json", None),
    (lambda: _Resp(200, ["not", "dict"]), "json", 200),
])
def test_get_ex_names_the_failure(kc, monkeypatch, make, kind, status):
    """실패를 None 하나로 접으면 'TR 경로가 틀렸다(404)' 와 '원천 장애(5xx)' 와 '키
    문제(rt_cd)' 가 같은 모양이 된다(#82 · 리뷰 M5). ``_get`` 은 응답만 돌려준다."""
    import requests
    monkeypatch.setattr(kc, "_get_token", lambda: "tok")
    monkeypatch.setattr(requests, "get", lambda *a, **k: make())
    data, info = kc._get_ex("/p", "TR", {})
    assert info["kind"] == kind and info["status"] == status, info
    assert (data is not None) == (kind == "ok")
    assert kc._get("/p", "TR", {}) == data
    if kind == "rt_cd":
        assert "조회할 자료가 없습니다" in info["msg"]


@pytest.mark.parametrize("exc, kind", [("Timeout", "timeout"),
                                       ("ConnectionError", "network")])
def test_get_ex_names_transport_failures(kc, monkeypatch, exc, kind):
    import requests

    def boom(*a, **k):
        raise getattr(requests.exceptions, exc)("x")

    monkeypatch.setattr(kc, "_get_token", lambda: "tok")
    monkeypatch.setattr(requests, "get", boom)
    assert kc._get_ex("/p", "TR", {})[1]["kind"] == kind
    monkeypatch.setattr(kc, "_get_token", lambda: None)
    assert kc._get_ex("/p", "TR", {})[1]["kind"] == "token"


def test_token_failure_is_not_retried_within_the_cooldown(kc, monkeypatch, tmp_path):
    """발급이 실패하면 1분은 다시 POST 하지 않는다 — 인증 서버가 죽은 동안 조회마다 10초
    POST 를 기다리면 상세 페이지가 TR 수만큼 붙잡힌다(리뷰 M7)."""
    import requests
    posts: list = []

    def post(*a, **k):
        posts.append(1)
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(kc, "_TOKEN_CACHE", tmp_path / "tok.json")
    monkeypatch.setattr(requests, "post", post)
    assert kc._get_token() is None and kc._get_token() is None
    assert len(posts) == 1
    monkeypatch.setattr(kc, "_TOKEN_FAIL_UNTIL", 0.0)            # 냉각이 지나면 다시 묻는다
    assert kc._get_token() is None and len(posts) == 2


# ─── TR · 파라미터 · 캐시 ────────────────────────────────────────────────────

def test_market_code_is_J_for_kosdaq_too(monkeypatch, kc):
    """공식 샘플은 J:KRX·NX:NXT·UN:통합 만 적는다 — 코스닥도 J 다."""
    assert kc._mkt_div("247540.KQ") == "J" and kc._mkt_div("005930.KS") == "J"
    calls = _router(monkeypatch, kc, _ok_table())
    k = kc.KisClient()
    for fn in (k.get_investor_flow, k.get_credit_short_balance, k.get_short_sale,
               k.get_program_trade):
        fn("247540.KQ")
    assert calls and {c[2]["FID_COND_MRKT_DIV_CODE"] for c in calls} == {"J"}, calls


def test_each_method_calls_the_official_tr(monkeypatch, kc):
    calls = _router(monkeypatch, kc, _ok_table())
    k = kc.KisClient()
    assert k.get_investor_flow("005930.KS")["schema"] == kc._FLOW_SCHEMA
    assert k.get_short_sale("005930.KS")["short_qty"] == 800
    assert k.get_program_trade("005930.KS")["unit_won"] == 1_000_000
    seen = {(c[0], c[1]) for c in calls}
    assert ("/uapi/domestic-stock/v1/quotations/inquire-investor", "FHKST01010900") in seen
    assert ("/uapi/domestic-stock/v1/quotations/daily-short-sale", "FHPST04830000") in seen
    assert ("/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
            "FHPPG04650201") in seen
    assert not any(c[1] == "FHPPG04650100" for c in calls)


def test_credit_tries_today_first_then_blank(monkeypatch, kc):
    seq: list = []

    def credit(params):
        seq.append(params["FID_INPUT_DATE_1"])
        return None if params["FID_INPUT_DATE_1"] else _credit_payload()

    _router(monkeypatch, kc, {"FHPST04760000": credit})
    r = kc.KisClient().get_credit_short_balance("005930.KS")
    assert seq == ["20261002", ""] and r["credit_short_shares"] == 35
    # 오늘 날짜로 확정 행이 오면 두 번 묻지 않는다
    seq.clear()
    _router(monkeypatch, kc, {"FHPST04760000": lambda p: (seq.append(p["FID_INPUT_DATE_1"])
                                                          or _credit_payload())})
    kc.KisClient().get_credit_short_balance("000660.KS")
    assert seq == ["20261002"], seq


_KINDS = [("investor", "get_investor_flow", "FHKST01010900", _inv_payload),
          ("credit", "get_credit_short_balance", "FHPST04760000", _credit_payload),
          ("short", "get_short_sale", "FHPST04830000", _short_payload),
          ("program", "get_program_trade", "FHPPG04650201", _prog_payload)]


def _age(kc, key, hours, *, now=_NOW):
    t = now.timestamp() - hours * 3600
    os.utime(kc._CACHE_DIR / key, (t, t))


@pytest.mark.parametrize("kind, meth, tr, payload", _KINDS)
def test_flow_cache_rules_hold_for_all_four(monkeypatch, kc, kind, meth, tr, payload):
    """수급 캐시 규칙은 네 종 모두 — 판이 맞아야 쓰고(이름에도 판), 오늘 행이 잠정이던
    응답은 1시간, 마지막 'KRX 하루 끝'(20:00) 전에 쓴 것은 그 뒤 믿지 않는다(리뷰 L1·L4)."""
    calls = _router(monkeypatch, kc, {tr: payload()})
    key = kc._flow_cache_key(kind, "005930")
    assert f"_v{kc._FLOW_SCHEMA}_" in key
    kc._CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # 옛 이름·옛 모양 파일은 읽지 않는다
    (kc._CACHE_DIR / f"{kc.FLOW_TRS[kind][4]}_005930.json").write_text(
        '{"today": 1}', encoding="utf-8")
    (kc._CACHE_DIR / key).write_text('{"schema": 2, "x": 1}', encoding="utf-8")
    first = getattr(kc.KisClient(), meth)("005930.KS")
    assert first["schema"] == kc._FLOW_SCHEMA and len(calls) >= 1
    n = len(calls)
    _age(kc, key, 0.5)
    getattr(kc.KisClient(), meth)("005930.KS")
    assert len(calls) == n, "판이 맞는 신선한 캐시는 써야 한다"
    _age(kc, key, 13)                                            # 12시간 TTL
    getattr(kc.KisClient(), meth)("005930.KS")
    assert len(calls) > n
    # 마지막 하루 끝(20:00) 보다 먼저 쓴 캐시는 믿지 않는다(그 뒤 원천에 그날 확정 값이
    # 생긴다) — 45분 전(1시간·12시간 TTL 안)이라도. 하루 끝 뒤에 쓴 것은 쓴다.
    monkeypatch.setattr(kc, "_now_kst", lambda: _AFTER_CLOSE)
    after = datetime(2026, 10, 2, 20, 10, tzinfo=_KST).timestamp()
    os.utime(kc._CACHE_DIR / key, (after, after))
    n = len(calls)
    getattr(kc.KisClient(), meth)("005930.KS")
    assert len(calls) == n, "하루 끝 뒤에 쓴 캐시는 써야 한다"
    before = datetime(2026, 10, 2, 19, 45, tzinfo=_KST).timestamp()
    os.utime(kc._CACHE_DIR / key, (before, before))
    getattr(kc.KisClient(), meth)("005930.KS")
    assert len(calls) > n


def test_pending_cache_lives_one_hour_and_none_thirty_minutes(monkeypatch, kc):
    calls = _router(monkeypatch, kc, {"FHKST01010900": _inv_payload()})
    key = kc._flow_cache_key("investor", "005930")
    kc.KisClient().get_investor_flow("005930.KS")                # 10-02 행이 잠정 → pending
    assert json.loads((kc._CACHE_DIR / key).read_text())["pending"] == 1
    _age(kc, key, 0.9)
    kc.KisClient().get_investor_flow("005930.KS")
    assert len(calls) == 1
    _age(kc, key, 1.1)
    kc.KisClient().get_investor_flow("005930.KS")
    assert len(calls) == 2
    # 원천이 답했는데 확정 행이 없으면(오늘 잠정 행만) 그 답을 30분 기억한다
    only_today = {"rt_cd": "0", "output": [_inv_row("20261002", 6)]}
    c2 = _router(monkeypatch, kc, {"FHKST01010900": only_today})
    k2 = kc._flow_cache_key("investor", "000660")
    v, why = kc._flow_get("investor", "000660.KS")
    assert v is None and "확정된 행이 없습니다" in why and len(c2) == 1
    v, why = kc._flow_get("investor", "000660.KS")
    assert v is None and "30분 안에 받은 같은 답" in why and len(c2) == 1
    _age(kc, k2, 0.6)
    kc._flow_get("investor", "000660.KS")
    assert len(c2) == 2
    # 전송 실패는 기억하지 않는다(차단기가 맡는다)
    c3 = _router(monkeypatch, kc, {"FHKST01010900": (None, {"kind": "timeout", "msg": "t"})})
    kc._flow_get("investor", "035720.KS")
    assert not (kc._CACHE_DIR / kc._flow_cache_key("investor", "035720")).exists()
    assert len(c3) == 1


def test_flow_cache_purges_two_day_old_flow_files_once(monkeypatch, kc):
    """이름에 판을 싣는 캐시는 지우는 코드가 없으면 쌓인다(#430) — 이틀 지난 수급 캐시
    (옛 이름 포함)만 지우고 다른 KIS 캐시는 건드리지 않는다. 프로세스당 한 번."""
    import time as _t
    monkeypatch.setattr(kc, "_FLOW_PURGED", False)
    d = kc._CACHE_DIR
    d.mkdir(parents=True, exist_ok=True)
    old = _t.time() - 3 * 86400
    for name in ("investor_005930.json", "prog_v2_005930.json", "price_005930.json",
                 "short_v3_000660.json"):
        (d / name).write_text("{}", encoding="utf-8")
        os.utime(d / name, (old, old))
    (d / "credit_v3_005930.json").write_text("{}", encoding="utf-8")   # 새 파일은 남는다
    kc._flow_put("investor_v3_123456.json", {"schema": 3})
    left = sorted(p.name for p in d.iterdir())
    assert left == ["credit_v3_005930.json", "investor_v3_123456.json", "price_005930.json"]
    (d / "investor_005930.json").write_text("{}", encoding="utf-8")
    os.utime(d / "investor_005930.json", (old, old))
    kc._flow_put("investor_v3_654321.json", {"schema": 3})
    assert (d / "investor_005930.json").exists()                 # 두 번째부터는 안 훑는다


# ─── 차단기 · 예산 ───────────────────────────────────────────────────────────

def test_breaker_opens_after_two_transport_failures_and_closes_on_success(monkeypatch, kc):
    """수급 TR 이 연속 전송 실패(5xx·타임아웃·연결)하면 5분 묻지 않는다 — #72 와 같은
    규약(리뷰 M7). 4xx·rt_cd 는 세지 않는다. 성공하면 카운터를 지운다."""
    state = {"v": (None, {"kind": "timeout", "status": None, "msg": "t"})}
    calls = _router(monkeypatch, kc, {"FHKST01010900": lambda p: state["v"]})
    for _ in range(2):
        kc._flow_ask("investor", "005930.KS")
    assert len(calls) == 2 and kc._BREAKER["FHKST01010900"][0] == 2
    data, info = kc._flow_ask("investor", "005930.KS")
    assert data is None and info["kind"] == "breaker" and len(calls) == 2
    # 냉각이 지나면 다시 묻고, 성공하면 카운터가 사라진다
    kc._BREAKER["FHKST01010900"] = (2, 0.0)
    state["v"] = _inv_payload()
    assert kc._flow_ask("investor", "005930.KS")[1]["kind"] == "ok"
    assert "FHKST01010900" not in kc._BREAKER
    # 4xx·rt_cd 는 몇 번이어도 열지 않는다
    state["v"] = (None, {"kind": "http4xx", "status": 404, "msg": "HTTP 404"})
    for _ in range(3):
        kc._flow_ask("investor", "005930.KS")
    state["v"] = None
    for _ in range(3):
        kc._flow_ask("investor", "005930.KS")
    assert "FHKST01010900" not in kc._BREAKER and len(calls) == 9
    # 다른 TR 은 따로 센다
    _router(monkeypatch, kc, {"FHPST04830000": (None, {"kind": "http5xx", "status": 502,
                                                       "msg": "HTTP 502"})})
    kc._flow_ask("short", "005930.KS")
    assert kc._BREAKER["FHPST04830000"][0] == 1 and kc._BREAKER["FHPST04830000"][1] == 0.0


def test_collect_budget_skips_the_rest_and_says_so(monkeypatch, kc, caplog):
    _router(monkeypatch, kc, _ok_table())
    ticks = iter([0.0, 0.0, 30.0, 31.0, 32.0])
    with caplog.at_level(logging.WARNING, logger="bot.kis"):
        out = kc.collect_kis_flow("005930.KS", budget=25.0, clock=lambda: next(ticks))
    assert set(out) == {"investor_flow", "kis_why"}
    assert set(out["kis_why"]) == {"credit", "short_sale", "program"}
    assert all("예산 25초" in v for v in out["kis_why"].values())
    assert any("예산" in r.getMessage() for r in caplog.records)


# ─── 단일 수집 함수 · 두 호출부 E2E ──────────────────────────────────────────

def test_collect_without_keys_says_why_and_asks_nothing(monkeypatch, kc):
    monkeypatch.setattr(kc, "_app_key", lambda: "")
    calls = _router(monkeypatch, kc, _ok_table())
    out = kc.collect_kis_flow("005930.KS")
    assert calls == [] and set(out) == {"kis_why"}
    assert set(out["kis_why"]) == {"investor_flow", "credit", "short_sale", "program"}
    assert all("자격증명" in v for v in out["kis_why"].values())


def test_collect_isolates_one_failure_and_records_why(monkeypatch, kc, caplog):
    _router(monkeypatch, kc, dict(_ok_table(), FHPPG04650201=None))
    real = kc._flow_get

    def flaky(kind, ticker):
        if kind == "short":
            raise RuntimeError("short boom")
        return real(kind, ticker)

    monkeypatch.setattr(kc, "_flow_get", flaky)
    with caplog.at_level(logging.WARNING, logger="bot.kis"):
        out = kc.collect_kis_flow("005930.KS")
    assert set(out) == {"investor_flow", "credit", "kis_why"}, set(out)
    assert out["kis_why"]["short_sale"].startswith("수집 중 예외")
    assert "원천 응답 실패" in out["kis_why"]["program"] and "rt_cd=1" in out["kis_why"]["program"]
    assert any("short_sale" in r.getMessage() and "short boom" in r.getMessage()
               for r in caplog.records)


def test_snapshot_enrich_fills_kis_flow(monkeypatch, kc):
    """스냅샷 경로(``_enrich_kr`` → ``_t_flow``)가 실제로 KIS 칸을 채운다 — 옛 판은
    없는 메서드 호출로 한 번도 안 돌았다."""
    import bot.stock_snapshot as ss
    _router(monkeypatch, kc, _ok_table())
    snap: dict = {}
    ss._enrich_kr("005930.KS", snap)
    flow = snap["kr"]["flow"]
    assert flow["investor_flow"]["schema"] == kc._FLOW_SCHEMA
    assert flow["credit"]["credit_short_shares"] == 35
    assert flow["program"]["unit_won"] == 1_000_000


def test_detail_enrichment_fills_kis_flow_even_when_stored_flow_has_only_trends(monkeypatch, kc):
    """'수급 칸이 있으면 건너뜀' 이면 pykrx 추세만 든 저장 스냅샷이 KIS 칸을 영영 못
    얻는다(#18 · 리뷰 L2). 지금 판의 KIS 값이 있으면 다시 묻지 않는다."""
    import bot.dashboard as dash
    calls = _router(monkeypatch, kc, _ok_table())
    si = {"news": [1], "kr": {"research_reports": [1]}}
    dash._ensure_detail_enrichment("005930.KS", si)
    assert si["kr"]["flow"]["investor_flow"]["asof"] == "2026-10-01"
    trend = {"d": "2026-10-01", "pct": 50.0}
    si2 = {"news": [1], "kr": {"research_reports": [1],
                               "flow": {"foreign_ownership": [trend],
                                        "investor_flow": {"today": {"foreign": 5}}}}}
    dash._ensure_detail_enrichment("005930.KS", si2)
    f2 = si2["kr"]["flow"]
    assert f2["investor_flow"]["schema"] == kc._FLOW_SCHEMA and f2["foreign_ownership"] == [trend]
    n = len(calls)
    dash._ensure_detail_enrichment("005930.KS", si2)              # 이미 있다 — 다시 묻지 않는다
    assert len(calls) == n


def test_no_call_site_reaches_for_a_missing_ready_method():
    """옛 사본 둘이 부르던 ``_ready()`` 는 어디서도 다시 나오면 안 된다(이름은 아래
    전수 가드가 일반형으로 막는다)."""
    for rel in ("bot/stock_snapshot.py", "bot/dashboard.py"):
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        bad = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.Attribute) and n.attr == "_ready"]
        assert bad == [], (rel, bad)


# ─── 화면 ────────────────────────────────────────────────────────────────────

def _flow_pane(monkeypatch, flow):
    import bot.dashboard as dash
    monkeypatch.setattr("bot.pykrx_client.get_kr_multi_period_trends", lambda t, **kw: None)
    si = {"currency": "KRW", "kr": {"flow": flow}}
    seg = dash._render_stock_info_html({"ticker": "005930.KS", "stock_info": si})["other_panes"]
    i = seg.index('id="si-flow"')
    return seg[i:seg.index("</div>\n</div>", i) + 13]


def _parsed_flow(kc):
    return {"investor_flow": kc.parse_investor_flow(_inv_payload()),
            "credit": kc.parse_credit_balance(_credit_payload()),
            "short_sale": kc.parse_short_sale(_short_payload()),
            "program": kc.parse_program_daily(_prog_payload())}


def _row_of(pane, label):
    i = pane.index(f"<td>{label}</td>")
    return pane[i:pane.index("</tr>", i)]


def test_flow_pane_values_are_locked_to_the_parsed_numbers(monkeypatch, kc):
    """화면 금액·수량 칸을 **값으로** 잰다 — 단위 글자만 보면 100배 틀려도 통과한다
    (리뷰 M1: `/1e8 → /1e6` · 수량 칸에 금액이 살아남았다)."""
    flow = _parsed_flow(kc)
    inv, prog = flow["investor_flow"], flow["program"]
    pane = _flow_pane(monkeypatch, flow)
    assert "투자자별 순매수 (KIS)" in pane
    assert "2026-10-01 수량" in pane and "2026-10-01 금액" in pane
    assert "5거래일 누적<br>2026-09-23~2026-10-01" in pane
    row = _row_of(pane, "외국인")
    q = inv["latest"]["qty"]["foreign"]
    assert f">+{q:,}주</td>" in row
    assert f">{kc.fmt_eok(inv['latest']['won']['foreign'])}억</td>" in row
    assert f">{kc.fmt_eok(inv['window']['won']['foreign'])}억</td>" in row
    # 프로그램 칸도 값으로
    assert f">{kc.fmt_eok(prog['latest']['won'])}억</td>" in pane
    assert f">{kc.fmt_eok(prog['window']['won'])}억</td>" in pane
    assert "잠정이라 뺀 행: 2026-10-02(오늘" in pane
    assert "대주잔고 (신용 매도, 주)" in pane and ">35<" in pane
    assert "프로그램 순매수 (KIS · 전체 합계)" in pane
    # 옛 판이 지어 그리던 칸은 없다 — 각주의 '차익·비차익을 나누지 않습니다' 는 그 사실을
    # 말하는 문장이라 낱말이 아니라 **행 이름**으로 잰다(#75)
    for gone in ("기관 세부", "비차익 순매수", "<td>차익 순매수", "대차잔고"):
        assert gone not in pane, gone


def test_flow_pane_does_not_color_amounts_that_round_to_zero(monkeypatch, kc):
    """1천만원 미만 순매수를 소수 1자리로 찍으면 '+0.0억' 이 초록으로 칠해진다(리뷰 M1).
    부호와 색은 반올림한 값에서 — 프롬프트와 같은 함수(``fmt_eok``)."""
    assert kc.fmt_eok(400_000) == "0.00" and kc.eok_sign(400_000) == 0
    assert kc.fmt_eok(-400_000) == "0.00" and kc.eok_sign(-400_000) == 0
    assert kc.fmt_eok(1_234_567_890) == "+12.35" and kc.eok_sign(1_234_567_890) == 1
    assert kc.fmt_eok(None) is None and kc._fmt_eok(None) == "N/A"
    inv = kc.parse_investor_flow(_inv_payload())
    inv["latest"]["won"]["foreign"] = 400_000
    pane = _flow_pane(monkeypatch, {"investor_flow": inv})
    assert '<td class="num">0.00억</td>' in _row_of(pane, "외국인")


def test_flow_pane_skips_old_shapes(monkeypatch, kc):
    """옛 모양(수량을 만원으로 읽던 판)은 그리지 않는다 — 그리면 틀린 숫자다. 공매도도
    판이 맞아야 그린다(리뷰 생존 M37)."""
    old = {"investor_flow": {"today": {"foreign": 5}, "5d": {"foreign": 9},
                             "inst_breakdown": {"pension": 1}},
           "program": {"arb_net": 1, "nonarb_net": 2},
           "credit": {"credit_balance_pct": 1.0, "loan_balance_shares": 3},
           "short_sale": {"schema": 2, "asof": "2026-10-01", "short_ratio_pct": 20.0,
                          "short_qty": 9}}
    pane = _flow_pane(monkeypatch, old)
    assert "투자자별 순매수 (KIS)" not in pane and "만</td>" not in pane
    assert "프로그램 순매수" not in pane and "신용·공매도 (KIS)" not in pane
    assert "공매도 비율" not in pane


def test_flow_pane_says_why_amounts_are_missing(monkeypatch, kc):
    p = _inv_payload(head_unfilled=False)
    for row in p["output"]:
        for k in list(row):
            if k.endswith(("shnu_tr_pbmn", "seln_tr_pbmn")):
                row[k] = "0"
    pane = _flow_pane(monkeypatch, {"investor_flow": kc.parse_investor_flow(p)})
    assert "금액 단위를 확정하지 못해 그날 금액은 싣지 않았습니다" in pane
    assert "5거래일 누적 중 합을 만들지 않은 칸이 있습니다 — 금액 단위를 확정하지 못했습니다" in pane


def test_flow_pane_says_why_kis_cells_are_empty(monkeypatch, kc):
    """KIS 칸이 통째로 사라지면 '기능이 없다' 로 읽힌다(#43 · 리뷰 L3) — 수집할 때 적힌
    사유를 그 자리에 싣는다. 값이 있는 칸의 사유는 싣지 않는다."""
    flow = {"investor_flow": kc.parse_investor_flow(_inv_payload()),
            "kis_why": {"investor_flow": "x", "credit": "원천 응답 실패 — HTTP 503",
                        "program": "KIS 자격증명(KIS_APP_KEY/KIS_APP_SECRET)이 없습니다"}}
    pane = _flow_pane(monkeypatch, flow)
    assert "KIS 칸이 비어 있는 이유(수집 시점)" in pane
    assert "신용·대주: 원천 응답 실패 — HTTP 503" in pane and "프로그램: KIS 자격증명" in pane
    assert "투자자별 순매수: x" not in pane
    only = _flow_pane(monkeypatch, {"kis_why": {"credit": "a"}})
    assert "신용·대주: a" in only


def test_flow_pane_program_without_unit_shows_quantity_and_why(monkeypatch, kc):
    p = _prog_payload()
    for r in p["output"]:
        r["whol_smtn_shnu_tr_pbmn"] = r["whol_smtn_seln_tr_pbmn"] = "0"
    prog = kc.parse_program_daily(p)
    assert prog["unit_won"] is None and prog["latest"]["won"] is None
    pane = _flow_pane(monkeypatch, {"program": prog})
    assert f">+{prog['latest']['qty']:,}주</td>" in pane and "하루 (수량)" in pane
    assert "금액 단위를 확정하지 못해 금액은 싣지 않았습니다" in pane


# ─── 분석 프롬프트 블록 ─────────────────────────────────────────────────────

def _flow_dict(kc, f5, i5, p5, *, unit=1_000_000):
    return {"schema": kc._FLOW_SCHEMA, "asof": "2026-10-01", "unit_won": unit, "unit_note": "",
            "latest": {"date": "2026-10-01", "qty": {"foreign": 1000, "institution": -5,
                                                     "individual": 3},
                       "won": {"foreign": 2 * 10**8, "institution": None, "individual": 0},
                       "unit_ok": unit is not None},
            "window": {"from": "2026-09-23", "to": "2026-10-01", "days": 5, "label": "5거래일",
                       "won": {"foreign": f5, "institution": i5, "individual": p5},
                       "note": ""},
            "pending": 1, "pending_note": "2026-10-02(오늘 — KRX 거래가 끝나는 20:00 전이라 잠정)",
            "blank": 0, "missing": []}


def test_prompt_block_dates_units_and_rule10_in_won(kc):
    txt = kc.format_kis_block({"investor_flow": _flow_dict(kc, 5 * 10**9, 2 * 10**10, 1)})
    assert "순매수 수량 (2026-10-01 하루): 외인 +1,000주" in txt
    assert "순매수 금액 (2026-10-01 하루): 외인 +2.00억원" in txt
    assert "5거래일 누적 (2026-09-23~2026-10-01): 외인 +50.00억원" in txt
    # RULE 10 noise 판정은 원 단위로 — 외인 50억은 noise, 기관 200억은 아니다
    assert "⚠️ RULE 10: 외인 5거래일 누적 +50.00억원" in txt
    assert "RULE 10: 기관" not in txt
    assert "잠정이라 뺀 행: 2026-10-02(오늘" in txt
    assert "당일 순매수" not in txt                        # 옛 라벨(날짜 없는 '당일')


def test_prompt_block_uses_the_window_label_and_says_why_a_sum_is_missing(kc):
    d = _flow_dict(kc, None, 3 * 10**10, 1)
    d["window"].update(label="최근 5개 영업일 행", note="원천이 그 구간의 거래일 2026-09-29 을 주지 않았습니다")
    txt = kc.format_kis_block({"investor_flow": d})
    assert "• 최근 5개 영업일 행 누적 (2026-09-23~2026-10-01)" in txt
    assert "(최근 5개 영업일 행 누적 중 합을 만들지 않은 칸이 있습니다 — 원천이 그 구간의 거래일 2026-09-29" in txt


def test_prompt_block_retail_support_pattern(kc):
    txt = kc.format_kis_block({"investor_flow": _flow_dict(kc, -12 * 10**9, None, 15 * 10**9)})
    assert "Retail 떠받침 패턴 — 개인 +150.00억원 vs 외인 -120.00억원" in txt
    quiet = kc.format_kis_block({"investor_flow": _flow_dict(kc, -12 * 10**9, None, 5 * 10**9)})
    assert "Retail" not in quiet


def test_prompt_block_without_unit_says_so(kc):
    d = _flow_dict(kc, None, None, None, unit=None)
    d["latest"]["won"] = {"foreign": None, "institution": None, "individual": None}
    d["unit_note"] = "표본 0개"
    txt = kc.format_kis_block({"investor_flow": d})
    assert "금액 단위를 확정하지 못해 그날 금액은 싣지 않습니다" in txt and "표본 0개" in txt
    assert "억원" not in txt


def test_prompt_block_program_without_unit_says_so(kc):
    p = _prog_payload()
    for r in p["output"]:
        r["whol_smtn_shnu_tr_pbmn"] = r["whol_smtn_seln_tr_pbmn"] = "0"
    txt = kc.format_kis_block({"program_trade": kc.parse_program_daily(p)})
    assert "• 프로그램 순매수 수량 (2026-10-01 하루, 전체 합계): +" in txt
    assert "금액 단위를 확정하지 못해 프로그램 금액은 싣지 않습니다" in txt
    assert "(프로그램 5거래일 누적은 싣지 않습니다 — 금액 단위를 확정하지 못했습니다)" in txt


def test_prompt_block_skips_old_shapes(kc):
    txt = kc.format_kis_block({
        "investor_flow": {"today": {"foreign": 11730}, "5d": {"foreign": 10_000_001}},
        "program_trade": {"arb_net": 1, "nonarb_net": 30_000_000},
        "credit_short": {"credit_balance_pct": 5.0, "loan_balance_shares": 9},
        "short_sale": {"short_ratio_pct": 20.0}})
    assert txt == "", txt


def test_prompt_block_program_credit_short(kc):
    txt = kc.format_kis_block({
        "program_trade": kc.parse_program_daily(_prog_payload()),
        "credit_short": kc.parse_credit_balance(_credit_payload()),
        "short_sale": kc.parse_short_sale(_short_payload())})
    assert "프로그램 순매수 (2026-10-01 하루, 전체 합계 — 차익·비차익 구분 없음)" in txt
    assert "프로그램 5거래일 누적 (2026-09-23~2026-10-01)" in txt
    assert "신용잔고율 (2026-10-01): 4.20% ⚠️ 신용 과열" in txt
    assert "대주잔고(신용 매도) (2026-10-01): 35주" in txt
    assert "공매도 비율 (거래량 대비, 2026-10-01): 16.4% ⚠️" in txt
    assert "비차익 " not in txt.replace("차익·비차익", "")


def test_guide_makes_no_claims_the_data_cannot_back(kc):
    g = kc.KIS_INTERP_GUIDE
    assert "fetch 완료" not in g                       # 한도소진율은 KIS 가 주지 않는다
    assert "전체 합계" in g and "기준일" in g
    for gone in ("연기금 5일 순매수 양수", "투신 5일 -50억", "프로그램 비차익 ±200억"):
        assert gone not in g, gone


# ─── 분석가 프롬프트 RULE 10 보강 — 규칙이 요구하는 수치를 실제로 찍는 블록이 있나 ─────
# 규칙 줄의 머리(→ 앞) 접두 → (그 수치를 찍는 블록, 그 블록 출력의 **한 줄**에 함께 있어야
# 하는 낱말들). ⚠️ 닫힌 세계다 — 이 표에 없는 머리로 시작하는 새 줄은 실패한다. 그 수치를
# 실제로 찍는 블록을 찾아 여기 적거나(라벨은 실제 포매터 출력으로 확인된다) 규칙을 뺄 것.
# 2026-10-04 까지 연기금·투신·프로그램 비차익 세 줄은 어느 블록도 안 찍는 수치를 '명시
# 의무' 로 요구했다(KIS 옛 판은 공식 응답에 없는 필드를 읽어 늘 None 이었다 — 실수 #433).
# 문자열 금지 목록은 표현만 바꾼 줄을 못 잡는다(#373) — 그래서 대응 표다. None = 수치를
# 요구하지 않는 줄(아래 테스트가 그 줄이 말하는 '없음' 이 사실인지 따로 잰다).
_RULE10_SOURCES = {
    "외인 5거래일 누적": ("kis", ("거래일 누적", "외인")),
    "신용잔고율": ("kis", ("신용잔고율",)),
    "공매도 비율(거래량 대비)": ("kis", ("공매도 비율", "거래량 대비")),
    # ⚠️ 콜론까지 — 95% 경고 줄(`⚠️ 한도소진율 95%+ …`)도 같은 낱말을 품어, 값 줄을 지워도
    # 경고 줄이 대신 만족시켰다(실측 뮤테이션 생존, #75).
    "[Step 2C] 외인 한도소진율(외국인 보유현황 블록)": ("seibro", ("한도소진율:",)),
    "[Step 2C] 개인 5거래일": ("kis", ("거래일 누적", "개인", "기관", "외인")),
    "주입된 블록에 없는 수치": None,
}


def _rule10_bullets() -> list:
    """분석가 프롬프트의 RULE 10 보강 줄들 — 인접 문자열 리터럴은 AST 에서 상수 하나로
    합쳐지므로 그 상수를 찾아 자른다(LLM 을 부르지 않고 실제 프롬프트 문구를 읽는다)."""
    src = (_REPO / "TradingAgents/tradingagents/agents/analysts/fundamentals_analyst.py"
           ).read_text(encoding="utf-8")
    texts = [n.value for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and "RULE 10 보강 — KIS" in n.value]
    assert len(texts) == 1, len(texts)
    t = texts[0]
    seg = t[t.index("RULE 10 보강 — KIS"):t.index("위 기준에 해당하지 않는 경우")]
    return [ln.strip()[1:].strip() for ln in seg.splitlines() if ln.strip().startswith("•")]


def _unbacked_rule_bullets(bullets, outputs, sources=None) -> list:
    """규칙 줄 중 그 수치를 찍는 블록이 없는 것(순수). ``outputs`` = {블록: 출력 텍스트}."""
    sources = _RULE10_SOURCES if sources is None else sources
    bad = []
    for b in bullets:
        key = next((k for k in sources if b.startswith(k)), None)
        if key is None:
            bad.append(f"대응 표에 없는 규칙 줄: {b[:60]}")
            continue
        if sources[key] is None:
            continue
        block, words = sources[key]
        if not any(all(w in ln for w in words) for ln in (outputs.get(block) or "").splitlines()):
            bad.append(f"{block} 블록의 어느 줄도 {words} 를 함께 찍지 않는다: {b[:60]}")
    return bad


def _real_block_outputs(kc) -> dict:
    """프롬프트에 실제로 실리는 두 블록의 출력 — 공식 필드 이름 픽스처를 파서·포매터에 통째로 태운다."""
    from bot.seibro_client import format_foreign_holding_block
    kis = kc.format_kis_block({
        "investor_flow": kc.parse_investor_flow(_inv_payload()),
        "credit_short": kc.parse_credit_balance(_credit_payload()),
        "short_sale": kc.parse_short_sale(_short_payload()),
        "program_trade": kc.parse_program_daily(_prog_payload())})
    seibro = format_foreign_holding_block({
        "foreign_pct": 50.0, "foreign_shares": 5, "listed_shares": 10, "date": "2026-10-01",
        "limit_exhaustion_pct": 96.0, "limit_shares": 9})
    return {"kis": kis, "seibro": seibro}


def test_rule10_kis_rules_cite_only_numbers_some_block_prints(kc):
    """분석가 프롬프트 RULE 10 보강의 줄마다 그 수치를 실제로 찍는 블록이 있어야 한다 —
    없으면 '명시 의무' 가 지어내기를 부른다(실수 #433). 그리고 '없다' 고 말하는 줄은 정말
    어느 블록도 그 수치를 안 찍어야 참이다."""
    bullets = _rule10_bullets()
    assert len(bullets) >= 5, bullets          # 추출이 깨져 0줄이면 아무것도 안 잰다(#54)
    outs = _real_block_outputs(kc)
    assert _unbacked_rule_bullets(bullets, outs) == []
    absent = [b for b in bullets if b.startswith("주입된 블록에 없는 수치")]
    assert len(absent) == 1, absent
    for word in ("연기금", "투신"):
        assert word in absent[0], word
        assert all(word not in text for text in outs.values()), word
    assert "비차익" in absent[0]
    assert all("비차익 " not in text.replace("차익·비차익", "") for text in outs.values())


def test_rule10_contract_fires(kc):
    outs = _real_block_outputs(kc)
    # ① 대응 표에 없는 새 줄(옛 연기금 줄)은 실패한다
    assert _unbacked_rule_bullets(["연기금 5일 누적 순매수 양수 → '연기금 저점 지지' 명시."], outs)
    # ② 표에 있어도 그 블록이 라벨을 안 찍으면 실패한다 — 블록 출력이 비면
    assert _unbacked_rule_bullets(
        ["[Step 2C] 외인 한도소진율(외국인 보유현황 블록) 95% 이상 → '…' 명시."],
        dict(outs, seibro=""))
    # ③ 낱말들은 **한 줄**에 함께 있어야 한다 — 다른 줄에 흩어져 있으면 그 수치를 찍은 게 아니다
    assert _unbacked_rule_bullets(["공매도 비율(거래량 대비) 15% 이상 → …"],
                                  {"kis": "• 공매도 비율: 1%\n• 거래량 대비: x"})
    # ④ 정상 줄은 통과
    assert not _unbacked_rule_bullets(["신용잔고율 4% 이상 → …"], outs)




# ─── 감사 ────────────────────────────────────────────────────────────────────

def _verdict(kc, kind, data, why=""):
    from bot.scripts import kis_flow_audit as au
    return au.tr_verdict(f"005930.KS {au._LABELS[kind]}", kind, data, why, now=_NOW)


def test_audit_tr_verdict_branches(kc):
    """감사의 판정 분기마다 값으로 잰다 — '단위를 못 잼' 이 ✅ 로 뒤집혀도, 파서 실패
    분기가 빠져 감사가 예외로 죽어도 전 슈트가 green 이었다(리뷰 M9 · M45·M46)."""
    no = _verdict(kc, "investor", None, "HTTP 503")
    assert no == ["❌ 005930.KS ① 투자자: 원천이 답하지 않았습니다 — HTTP 503"]
    ok = _verdict(kc, "investor", _inv_payload())
    assert any(x.startswith("✅ 005930.KS ① 투자자: 금액 단위 ×1,000,000") for x in ok), ok
    assert any(x.startswith("✅ 005930.KS ① 투자자: 순매수 = 매수 − 매도") for x in ok), ok
    assert not any(x.startswith("❌") for x in ok), ok
    p = _inv_payload()
    for r in p["output"]:
        r.pop("prsn_ntby_qty")
    miss = _verdict(kc, "investor", p)
    assert any(x.startswith("❌") and "prsn_ntby_qty" in x for x in miss), miss
    empty = _verdict(kc, "investor", {"rt_cd": "0", "output": []})
    assert empty[0].startswith("❌") and "0개" in empty[0]
    # 행은 있는데 확정 행이 없다(오늘 잠정 행만) — 파서 실패 분기
    only_today = _verdict(kc, "investor", {"rt_cd": "0", "output": [_inv_row("20261002", 6)]})
    assert only_today[-1] == "❌ 005930.KS ① 투자자: 파서가 값을 못 만들었습니다(확정된 행 없음)"
    # 표본이 부족해 단위를 못 잼 → ⚠️(판정 보류) — ✅ 가 아니다(금액 칸이 전부 0 인
    # 응답 — 순=매수−매도 는 그대로 맞는다)
    z = _inv_payload()
    for r in z["output"]:
        for k in list(r):
            if k.endswith("_tr_pbmn"):
                r[k] = "0"
    thin = _verdict(kc, "investor", z)
    assert any(x.startswith("⚠️ 005930.KS ① 투자자: 표본이 부족해") for x in thin), thin
    assert not any(x.startswith(("✅ 005930.KS ① 투자자: 금액", "❌")) for x in thin), thin
    # 표본이 갈려 못 잼 → ❌
    split = _inv_payload()
    for i, r in enumerate(split["output"]):
        if i % 2:
            for k in list(r):
                if k.endswith(("shnu_tr_pbmn", "seln_tr_pbmn")):
                    r[k] = str(int(r[k]) * 100)
    sp = _verdict(kc, "investor", split)
    assert any(x.startswith("❌ 005930.KS ① 투자자: 금액 단위를 못 잽니다") for x in sp), sp
    # 행마다 단위가 갈림 → ❌
    bad = _inv_payload(head_unfilled=False)
    bad["output"][-2] = _inv_row("20261001", 5, unit=10_000_000)
    bv = _verdict(kc, "investor", bad)
    assert any(x.startswith("❌") and "일부 행과 맞지 않습니다" in x for x in bv), bv


def test_audit_net_identity_catches_a_field_that_means_something_else(kc):
    """이름은 공식 샘플과 같아도 뜻이 다르면 순 = 매수 − 매도 가 깨진다(리뷰 L9)."""
    p = _inv_payload()
    for r in p["output"]:
        r["frgn_ntby_qty"] = str(int(r["frgn_ntby_qty"]) * 2 if int(r["frgn_ntby_qty"]) else 0)
    v = _verdict(kc, "investor", p)
    assert any(x.startswith("❌ 005930.KS ① 투자자: 순매수 ≠ 매수 − 매도")
               and "frgn_ntby_qty" in x for x in v), v
    # 금액은 반올림 1단위까지 봐준다
    q = _inv_payload()
    for r in q["output"]:
        r["orgn_ntby_tr_pbmn"] = str(int(r["orgn_ntby_tr_pbmn"]) + 1)
    assert not any(x.startswith("❌") for x in _verdict(kc, "investor", q))
    pg = _prog_payload()
    pg["output"][0]["whol_smtn_ntby_qty"] = "1"
    assert any("순매수 ≠ 매수 − 매도" in x for x in _verdict(kc, "program", pg))


def test_audit_credit_verdict_single_and_double_failure(kc):
    """한쪽만 쓸 수 있어도 제품은 폴백으로 받으니 그 ❌ 는 정보로 내리고(리뷰 M43),
    둘 다 못 쓰면 **두 사유를 그 한 줄에** 싣는다 — 결산은 ❌ 줄만 올린다(M42 · M4)."""
    from bot.scripts import kis_flow_audit as au
    okl = _verdict(kc, "credit", _credit_payload())
    one = au.credit_verdict("005930.KS", [("20261002", ["❌ 005930.KS ② 신용(결제일자=20261002): 원천이 답하지 않았습니다 — rt_cd=1 X"]),
                                         ("", okl)])
    assert not any(x.startswith("❌") for x in one), one
    assert any("원천이 답하지 않았습니다 — rt_cd=1 X" in x for x in one)
    both = au.credit_verdict("005930.KS", [
        ("20261002", ["❌ a: 원천이 답하지 않았습니다 — rt_cd=1 A"]),
        ("", ["❌ b: 응답은 왔는데 날짜(deal_date)를 읽을 수 있는 행이 0개 — 응답 키: x"])])
    last = both[-1]
    assert last.startswith("❌ 005930.KS ② 신용: 결제일자 오늘·빈값 둘 다")
    assert "20261002: 원천이 답하지 않았습니다 — rt_cd=1 A" in last
    assert "빈값: 응답은 왔는데" in last
    assert sum(1 for x in both if x.startswith("❌")) == 1


def test_audit_market_code_verdict(kc):
    from bot.scripts import kis_flow_audit as au
    assert au.market_code_verdict("247540.KQ", {"J": (100, ""), "Q": (None, "x")})[-1].startswith(
        "✅ 247540.KQ 시장 분류 코드")
    bad = au.market_code_verdict("247540.KQ", {"J": (None, "rt_cd=1"), "Q": (100, "")})[-1]
    assert bad.startswith("❌ 247540.KQ") and "_mkt_div" in bad
    both = au.market_code_verdict("247540.KQ", {"J": (None, "a"), "Q": (None, "b")})[-1]
    assert both.startswith("❌") and "J: a" in both and "Q: b" in both


def test_audit_why_names_a_404(kc):
    """'TR 경로가 틀렸다' 는 이 감사가 잡으려던 바로 그 실패다 — 404 를 네트워크 쪽으로
    안내하면 엉뚱한 데를 고친다(리뷰 M5)."""
    from bot.scripts import kis_flow_audit as au
    assert au.why_of({"kind": "http4xx", "status": 404, "msg": "HTTP 404"}) == (
        "HTTP 404 — TR 경로가 틀렸을 수 있습니다")
    assert au.why_of({"kind": "timeout", "msg": "응답 시간 초과(10초)"}) == "응답 시간 초과(10초)"
    assert au.why_of({}) == "사유 미기록"


def test_audit_main_reports_missing_keys(monkeypatch, kc, capsys):
    from bot.scripts import kis_flow_audit as au
    monkeypatch.setattr(kc, "_app_secret", lambda: "")
    assert au.main([]) == 1
    out = capsys.readouterr().out
    assert "❌ KIS 자격증명이 없습니다" in out and "kis_flow_audit v" in out


def _audit_table():
    table = _ok_table()
    table["FHKST01010100"] = lambda p: ({"rt_cd": "0", "output": {"stck_prpr": "250000"}}
                                        if p["FID_COND_MRKT_DIV_CODE"] == "J" else None)
    return table


def test_audit_main_end_to_end(monkeypatch, kc, capsys):
    from bot.scripts import kis_flow_audit as au
    table = _audit_table()
    calls = _router(monkeypatch, kc, table)
    before = set(kc._MISSING_WARNED)
    assert au.main([]) == 0
    out = capsys.readouterr().out
    assert "❌" not in out, out
    assert "✅ 005930.KS ① 투자자" in out and "✅ 247540.KQ ④ 프로그램(일별)" in out
    assert "✅ 247540.KQ 시장 분류 코드: 코스닥에 J 를 받습니다" in out
    # 수급 캐시를 쓰지 않는다(원천을 직접 묻는 감사) · 차단기를 건드리지 않는다
    assert not kc._CACHE_DIR.exists() or not any(kc._CACHE_DIR.iterdir())
    assert kc._BREAKER == {} and kc._MISSING_WARNED == before
    assert {c[1] for c in calls} >= {"FHKST01010900", "FHPST04760000", "FHPST04830000",
                                     "FHPPG04650201", "FHKST01010100"}
    assert len(calls) == 12, len(calls)          # 코스피 5 · 코스닥 7(J·Q 대조 2)
    # J 를 거절하면 ❌ 로 결산에 오른다
    table["FHKST01010100"] = lambda p: ({"rt_cd": "0", "output": {"stck_prpr": "250000"}}
                                        if p["FID_COND_MRKT_DIV_CODE"] == "Q" else None)
    _router(monkeypatch, kc, table)
    assert au.main(["247540.KQ"]) == 1
    assert "❌ 247540.KQ 시장 분류 코드" in capsys.readouterr().out


def test_audit_does_not_spend_the_products_warning_budget(monkeypatch, kc, capsys):
    """감사는 봇 프로세스 안에서 돈다 — 파서의 '필드 없음' 1회 경고 예산을 08:00 에 써
    버리면 제품이 그 경고를 못 한다(진단이 운영 상태를 바꾼다, #264 · 리뷰 L8)."""
    from bot.scripts import kis_flow_audit as au
    table = _audit_table()
    p = _inv_payload()
    for r in p["output"]:
        r.pop("prsn_ntby_qty")
    table["FHKST01010900"] = p
    _router(monkeypatch, kc, table)
    assert au.main(["005930.KS"]) == 1
    assert "prsn_ntby_qty" in capsys.readouterr().out
    assert kc._MISSING_WARNED == set()


def test_audit_uses_the_product_request_builder(monkeypatch, kc, capsys):
    """감사는 제품과 **같은** 요청을 보낸다 — 따로 적으면 제품 파라미터를 고쳐도 옛
    요청을 잰다(#35·#38 · 리뷰 M6). 요청 빌더를 바꾸면 감사 요청도 바뀌어야 한다."""
    from bot.scripts import kis_flow_audit as au
    calls = _router(monkeypatch, kc, _audit_table())
    real = kc.flow_request

    def tagged(kind, ticker, **kw):
        path, tr, params = real(kind, ticker, **kw)
        return path, tr, dict(params, X_PRODUCT="1")

    monkeypatch.setattr(kc, "flow_request", tagged)
    au.main(["005930.KS"])
    flow_calls = [c for c in calls if c[1] != "FHKST01010100"]
    assert len(flow_calls) == 5 and all(c[2].get("X_PRODUCT") == "1" for c in flow_calls)


def test_audit_findings_carry_the_ticker_and_the_right_axis(monkeypatch, kc, capsys):
    """결산(``audit_sweep._findings``)은 ❌ 줄만 올리고 바로 위 절 제목을 붙인다 — 판정
    줄에 종목이 없고 정보 줄이 원숫자로 시작하면 같은 줄 둘(어느 종목인지 모름)과
    엉뚱한 축 이름(프로그램 실패에 '③ 공매도')이 나왔다(리뷰 M4)."""
    from bot import audit_sweep
    from bot.scripts import kis_flow_audit as au
    table = _audit_table()
    table["FHPPG04650201"] = lambda p: (None if p["FID_INPUT_ISCD"] == "247540"
                                        else _prog_payload())
    _router(monkeypatch, kc, table)
    au.main([])
    out = capsys.readouterr().out
    found = audit_sweep._findings(out)
    assert len(found) == 1, found
    assert found[0].startswith("[247540.KQ] ❌ 247540.KQ ④ 프로그램(일별): 원천이 답하지 않았습니다")
    for ln in out.splitlines():
        s = ln.strip()
        assert not (s[:1] in "①②③④⑤" and audit_sweep._section_of(ln)), ln


def test_audit_stops_after_consecutive_transport_failures(monkeypatch, kc, capsys):
    """KIS 장애 때 조회마다 10초씩 직렬로 기다리면 뒤 감사가 밀린다 — 연속 전송 실패 2회
    뒤 남은 조회를 건너뛰고 그 사실을 한 줄로 말한다(리뷰 L10)."""
    from bot.scripts import kis_flow_audit as au
    down = (None, {"kind": "timeout", "status": None, "msg": "응답 시간 초과(10초)"})
    calls = _router(monkeypatch, kc, {tr: down for tr in (
        "FHKST01010900", "FHPST04760000", "FHPST04830000", "FHPPG04650201", "FHKST01010100")})
    assert au.main([]) == 1
    out = capsys.readouterr().out
    assert len(calls) == 2, calls
    assert "❌ 원천 장애 — 연속 전송 실패 2회(응답 시간 초과(10초)) 뒤 남은 조회 10개를 건너뛰었습니다" in out
    # 장애 사이에 정상 응답이 끼면 다시 센다 — 투자자 실패 → 신용(오늘) 성공 → 신용(빈값)
    # 실패 순이면 연속 2회가 아니므로 공매도·프로그램까지 묻는다
    calls2 = _router(monkeypatch, kc, {
        "FHKST01010900": down,
        "FHPST04760000": lambda p: down if p["FID_INPUT_DATE_1"] == "" else _credit_payload(),
        "FHPST04830000": _short_payload(), "FHPPG04650201": _prog_payload()})
    au.main(["005930.KS"])
    assert len(calls2) == 5, calls2


def test_audit_is_registered_daily():
    from bot.audit_sweep import AUDITS
    assert dict((m, c) for _n, m, c in AUDITS)["bot.scripts.kis_flow_audit"] == "daily"


# ─── 전수 가드: 레포 클래스 인스턴스에서 없는 메서드를 부르는 자리 ─────────────

_PKGS = ("bot", "trade", "TradingAgents/tradingagents")


def _scan_missing_methods(root: Path, pkgs=_PKGS) -> list:
    """레포 클래스의 인스턴스에 그 클래스(기반 포함)에 없는 이름을 부르는 자리 — 두 모양:

    (a) 같은 함수 안에서 ``x = Cls(...)`` 또는 ``x = factory()``(반환 주석이 레포 클래스)
        로 만든 이름에 ``x.method()``
    (b) 묶지 않고 바로 부르는 체인 ``factory().method()`` · ``Cls().method()`` ·
        ``모듈.factory().method()`` — 함수·람다·클래스 본문·모듈 맨 위 어디서든.
        2026-10-04 독립 리뷰가 (a) 만 보던 판이 ``get_kis().get_price(ticker)`` 두 곳
        (분석 경로의 피어 PER/PBR 폴백·D1 Phase 4)을 못 본 것을 잡았다(실수 #433).

    ⚠️ 정확성보다 **오탐 없음**을 고른다 — 이름이 그 스코프에서 다른 방법으로도
    묶이면(루프·다른 대입·인자·with·예외·중첩 def), 기반이 레포 밖이거나
    ``__getattr__`` 이 있으면, 반환 주석이 ``Optional[...]`` 처럼 이름 하나가 아니면
    판정하지 않는다. 그래서 못 보는 축: 인자로 받은 인스턴스·속성에 담긴
    인스턴스·컨테이너를 거친 인스턴스·주석 없는 공장(#274)."""
    mods: dict = {}
    for pkg in pkgs:
        base = root / pkg
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            if "/tests/" in rel or p.name.startswith("test_"):
                continue
            dotted = rel[:-3].replace("TradingAgents/", "").replace("/", ".")
            if dotted.endswith(".__init__"):
                dotted = dotted[:-9]
            try:
                mods[dotted] = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

    def resolve(mod, name, depth=0):
        tree = mods.get(mod)
        if tree is None or depth > 6:
            return None
        for n in tree.body:
            if isinstance(n, ast.ClassDef) and n.name == name:
                return ("class", mod, n)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                return ("func", mod, n)
        for n in tree.body:
            if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                for a in n.names:
                    if (a.asname or a.name) == name:
                        return resolve(n.module, a.name, depth + 1)
        return None

    def members(mod, cls, depth=0):
        if depth > 8:
            return None
        out = set()
        for n in cls.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if n.name in ("__getattr__", "__getattribute__"):
                    return None
                out.add(n.name)
            elif isinstance(n, ast.Assign):
                out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                out.add(n.target.id)
        for n in ast.walk(cls):
            if (isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
                    and isinstance(n.value, ast.Name) and n.value.id == "self"):
                out.add(n.attr)
        for b in cls.bases:
            if isinstance(b, ast.Name) and b.id == "object":
                continue
            if not isinstance(b, ast.Name):
                return None
            r = resolve(mod, b.id)
            if not r or r[0] != "class":
                return None
            sub = members(r[1], r[2], depth + 1)
            if sub is None:
                return None
            out |= sub
        return out

    def class_of(mod, local_imports, call):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            return None
        nm = call.func.id
        r = resolve(*local_imports[nm]) if nm in local_imports else resolve(mod, nm)
        if not r:
            return None
        if r[0] == "class":
            return r
        ann = r[2].returns
        an = ann.id if isinstance(ann, ast.Name) else (
            ann.value if isinstance(ann, ast.Constant) and isinstance(ann.value, str) else None)
        if an:
            rr = resolve(r[1], an)
            if rr and rr[0] == "class":
                return rr
        return None

    def own_nodes(fn):
        """함수 자신의 스코프 노드만(중첩 함수·람다·클래스·컴프리헨션 본문 제외)."""
        stack = list(fn.body) + list(fn.args.args) + list(fn.args.kwonlyargs)
        if fn.args.vararg:
            stack.append(fn.args.vararg)
        if fn.args.kwarg:
            stack.append(fn.args.kwarg)
        while stack:
            n = stack.pop()
            yield n
            for c in ast.iter_child_nodes(n):
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                  ast.ClassDef, ast.ListComp, ast.SetComp, ast.DictComp,
                                  ast.GeneratorExp)):
                    continue
                stack.append(c)

    hits = []
    for mod, tree in mods.items():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes = list(own_nodes(fn))
            local_imports = {}
            for n in nodes:
                if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                    for a in n.names:
                        local_imports[a.asname or a.name] = (n.module, a.name)
            made: dict = {}
            other: set = set()
            for n in nodes:
                if isinstance(n, ast.arg):
                    other.add(n.arg)
                elif (isinstance(n, ast.Assign) and len(n.targets) == 1
                      and isinstance(n.targets[0], ast.Name)):
                    r = class_of(mod, local_imports, n.value)
                    if r:
                        made.setdefault(n.targets[0].id, []).append(r)
                    else:
                        other.add(n.targets[0].id)
                elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    pass  # 아래에서 대입 외 묶임을 따로 센다
            for n in nodes:
                tgt = []
                if isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
                    tgt = [n.target]
                elif isinstance(n, ast.Assign) and not (
                        len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)):
                    tgt = n.targets
                elif isinstance(n, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
                    tgt = [n.target]
                elif isinstance(n, ast.withitem) and n.optional_vars is not None:
                    tgt = [n.optional_vars]
                elif isinstance(n, ast.ExceptHandler) and n.name:
                    other.add(n.name)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    other |= {(a.asname or a.name).split(".")[0] for a in n.names}
                for t in tgt:
                    other |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
            bound = {k: v[0] for k, v in made.items()
                     if k not in other and len({id(x[2]) for x in v}) == 1}
            for n in nodes:
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and isinstance(n.func.value, ast.Name) and n.func.value.id in bound):
                    _, cm, cls = bound[n.func.value.id]
                    mem = members(cm, cls)
                    if mem is not None and n.func.attr not in mem:
                        hits.append(f"{mod}:{n.lineno} {n.func.value.id}.{n.func.attr}() "
                                    f"— {cm}.{cls.name} 에 없음")

    # ── (b) 묶지 않은 체인 — 스코프를 제대로 따라간다(클래스 본문의 이름은 메서드에
    # 안 보인다 · 컴프리헨션·람다는 자기 스코프) ──
    scope_t = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
               ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

    def scope_nodes(scope):
        if isinstance(scope, ast.Module):
            start = list(scope.body)
        elif isinstance(scope, ast.Lambda):
            start = [scope.args, scope.body]
        elif isinstance(scope, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            start = [scope.elt] + list(scope.generators)
        elif isinstance(scope, ast.DictComp):
            start = [scope.key, scope.value] + list(scope.generators)
        elif isinstance(scope, ast.ClassDef):
            start = list(scope.body)
        else:
            start = list(scope.body) + [scope.args]
        out, stack = [], list(start)
        while stack:
            n = stack.pop()
            out.append(n)
            if isinstance(n, scope_t):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    stack.extend(n.decorator_list)        # 장식은 바깥 스코프에서 평가
                continue
            stack.extend(ast.iter_child_nodes(n))
        return out

    def bindings(nodes):
        imp, other = {}, set()
        for n in nodes:
            if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                for a in n.names:
                    imp[a.asname or a.name] = (n.module, a.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.asname:
                        imp[a.asname] = ("<module>", a.name)
                    else:
                        other.add(a.name.split(".")[0])
            elif isinstance(n, ast.arg):
                other.add(n.arg)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                other.add(n.id)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                other.add(n.name)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                other.add(n.name)
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                other |= set(n.names)
        return imp, other            # import 과 다른 묶임이 겹치면 lookup 이 판정하지 않는다

    def as_module(target):
        """``(모듈, 이름)`` 이 레포 모듈을 가리키면 그 모듈 이름."""
        m, name = target
        if m == "<module>":
            return name if name in mods else None
        full = f"{m}.{name}"
        return full if full in mods else None

    def lookup(mod, env, name):
        """스코프 사슬(안쪽부터)에서 이름 → ('mod', 모듈) | resolve 결과 | None."""
        for imp, other in reversed(env[1:]):
            if name in other:
                return None
            if name in imp:
                mm = as_module(imp[name])
                return ("mod", mm) if mm else resolve(*imp[name])
        imp, other = env[0]
        top_defs = {n.name for n in mods[mod].body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        if name in other - top_defs:
            return None                 # 모듈 맨 위에서 대입 등으로 다시 묶인 이름
        if name in imp and name not in top_defs:
            mm = as_module(imp[name])
            if mm:
                return ("mod", mm)
        return resolve(mod, name)

    def instance_class(r):
        if not r or r[0] == "mod":
            return None
        if r[0] == "class":
            return r
        ann = r[2].returns
        an = ann.id if isinstance(ann, ast.Name) else (
            ann.value if isinstance(ann, ast.Constant) and isinstance(ann.value, str) else None)
        if an:
            rr = resolve(r[1], an)
            if rr and rr[0] == "class":
                return rr
        return None

    def chain_target(mod, env, call):
        f = call.func
        if isinstance(f, ast.Name):
            return instance_class(lookup(mod, env, f.id))
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            r = lookup(mod, env, f.value.id)
            if r and r[0] == "mod":
                return instance_class(resolve(r[1], f.attr))
        return None

    def visit(mod, scope, env):
        nodes = scope_nodes(scope)
        env2 = env + [bindings(nodes)]
        for n in nodes:
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Call)):
                r = chain_target(mod, env2, n.func.value)
                if r:
                    _, cm, cls = r
                    mem = members(cm, cls)
                    if mem is not None and n.func.attr not in mem:
                        hits.append(f"{mod}:{n.lineno} {ast.unparse(n.func)}() "
                                    f"— {cm}.{cls.name} 에 없음")
            if isinstance(n, scope_t):
                # 클래스 본문의 이름은 그 안의 스코프(메서드·컴프리헨션)에 안 보인다
                visit(mod, n, env if isinstance(scope, ast.ClassDef) else env2)

    for mod, tree in mods.items():
        visit(mod, tree, [])
    return sorted(set(hits))


def test_no_calls_to_methods_missing_on_repo_class_instances():
    """레포 클래스의 인스턴스에 그 클래스에 없는 메서드를 부르면 AttributeError 가
    대개 넓은 ``except`` 에 삼켜져 기능이 조용히 죽는다 — 수급 탭 KIS 칸이 그렇게
    비어 있었다(실수 #433 · 이 레포의 얕은 이력에서 셀 수 있는 가장 이른 기록은
    2026-08-20 이고 KIS 클라이언트 도입은 2026-05-22 다 — 그 사이 언제부터인지는 재지
    못했다). 분석 경로의 ``get_kis().get_price()`` 두 곳(묶지 않은 체인)도 같은
    병이었다. 이름 열거가 아니라 레포 전수로 잰다(#24)."""
    hits = _scan_missing_methods(_REPO)
    assert hits == [], "\n".join(hits)


def test_missing_method_scanner_fires_and_spares(tmp_path):
    pkg = tmp_path / "bot"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli.py").write_text(
        "class Base:\n"
        "    def inherited(self):\n        return 1\n"
        "class Client(Base):\n"
        "    def __init__(self):\n        self.cb = None\n"
        "    def real(self):\n        return 1\n"
        "class Dyn:\n"
        "    def __getattr__(self, k):\n        return lambda: 0\n"
        "def get_client() -> Client:\n    return Client()\n", encoding="utf-8")
    (pkg / "use.py").write_text(
        "from bot.cli import Client, Dyn, get_client\n"
        "def bad():\n    c = Client()\n    return c._ready()\n"            # 발화
        "def bad_factory():\n    k = get_client()\n    return k.nope()\n"  # 발화(반환 주석)
        "def ok_inherited():\n    c = Client()\n    return c.inherited() + c.real()\n"
        "def ok_attr_callable():\n    c = Client()\n    return c.cb()\n"
        "def ok_dynamic():\n    d = Dyn()\n    return d.anything()\n"
        "def ok_rebound(xs):\n    c = Client()\n    for c in xs:\n        c.get()\n"
        "def ok_local_import():\n    from bot.cli import Client as C\n"
        "    c = C()\n    return c.real()\n"
        "def bad_local_import():\n    from bot.cli import Client as C\n"
        "    c = C()\n    return c.ghost()\n", encoding="utf-8")
    # (b) 묶지 않은 체인 — 2026-10-04 리뷰가 잡은 get_kis().get_price() 모양
    (pkg / "chain.py").write_text(
        "from bot import cli\n"
        "from bot import cli as cm\n"
        "import bot.cli as icm\n"
        "from bot.cli import Client, Dyn, get_client\n"
        "def bad_factory_chain():\n    return get_client().gone()\n"         # 발화
        "def bad_ctor_chain():\n    return Client().gone2()\n"            # 발화
        "def bad_mod_chain():\n    return cli.get_client().gone3()\n"      # 발화(모듈.공장)
        "def bad_alias_chain():\n    return cm.Client().gone4()\n"          # 발화(별칭)
        "def bad_import_as_chain():\n    return icm.get_client().gone5()\n"  # 발화(import as)
        "def bad_local_chain():\n    from bot.cli import get_client as g\n"
        "    return g().gone6()\n"                                            # 발화(지역 import)
        "bad_lambda = lambda: get_client().gone7()\n"                        # 발화(람다·모듈 맨 위)
        "class W:\n    get_client = None\n"
        "    def m(self):\n        return get_client().gone8()\n"            # 발화(클래스 본문은 안 가림)
        "def bad_comp(xs):\n    return [get_client().gone9() for _ in xs]\n"  # 발화(컴프리헨션)
        "def ok_chain():\n    return get_client().real() + Client().inherited()\n"
        "def ok_chain_dyn():\n    return Dyn().anything()\n"
        "def ok_shadow_arg(get_client):\n    return get_client().nope()\n"
        "def ok_shadow_assign():\n    get_client = object\n    return get_client().nope()\n"
        "def ok_shadow_nested():\n    def get_client():\n        return 1\n"
        "    return get_client().nope()\n"
        "def ok_comp_target(fs):\n    return [get_client().nope() for get_client in fs]\n"
        "def ok_unknown():\n    return dict().nope()\n", encoding="utf-8")
    hits = _scan_missing_methods(tmp_path, ("bot",))
    names = sorted(h.split(" ")[1] for h in hits)
    assert names == sorted(
        ["c._ready()", "c.ghost()", "k.nope()",
         "get_client().gone()", "Client().gone2()", "cli.get_client().gone3()",
         "cm.Client().gone4()", "icm.get_client().gone5()", "g().gone6()",
         "get_client().gone7()", "get_client().gone8()", "get_client().gone9()"]), hits
