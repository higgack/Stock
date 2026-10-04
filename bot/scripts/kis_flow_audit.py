"""KIS 수급 4종 + 시장 분류 코드 실측 감사 — 실제 응답이 **공식 샘플의 모양**과 맞나(매일).

2026-10-04(실수 #433): 수급 4종(투자자·신용·공매도·프로그램)이 KIS 공식 샘플과 다른
필드 이름을 읽어 칸들이 늘 비거나(개인·기관 세부·대주·프로그램) 단위가 틀렸고(수량을
만원으로 읽음), 종목 페이지 쪽 블록은 없는 메서드를 불러 한 번도 안 돌았다 — 어느
감사도 그걸 안 봤다. 필드 이름은 공식 샘플(github.com/koreainvestment/open-trading-api
``examples_llm/domestic_stock/*/chk_*.py``)로 고쳤지만 **실제 응답**은 개발
샌드박스에서 잴 수 없다(KIS 는 운영 키로만 닿는다). 그래서 이 감사가 매일 운영에서 잰다:

  ① 투자자(FHKST01010900) · ② 신용(FHPST04760000, 결제일자 오늘/빈값 둘 다) ·
  ③ 공매도(FHPST04830000) · ④ 프로그램 일별(FHPPG04650201) — 원천이 답하나 ·
     기대한 필드가 다 있나 · 금액 단위가 항등식으로 잡히나 · 최신 영업일은 언제인가
  ⑤ 시장 분류 코드 — 코스닥 종목에 J(공식 값, 제품이 쓰는 값)와 Q(옛 판)를 나란히 물어
     원천이 어느 쪽을 받는지

판정 규약:
  ❌ = 우리가 고칠 것(필드 이름이 다름 · 단위를 못 잼 · J 를 거절 · 원천이 답하지 않음 ·
       자격증명 없음)
  ⚠️ = 우리가 당장 고칠 수 없는 것(예: 거래가 없는 날만 와서 단위 표본이 부족)
  ❓ = 판정 불가(의존성·예외). 대조 0건은 ✅ 가 아니다(#54).

⚠️ 수급 캐시를 읽지도 쓰지도 않는다 — 원천을 직접 묻는다(그래야 오늘의 응답을 잰다).
   토큰 캐시는 제품과 공유한다(발급 제한이 있어 따로 받으면 제품 쪽 발급을 막는다).
⚠️ 판정 글자는 판정에만 쓴다 — 요약은 세기만 한다(#250·#268·#289).

  cd ~/stock && .venv/bin/python -m bot.scripts.kis_flow_audit [티커…]
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime

_VER = 1
# 코스피 1 · 코스닥 1 — 거래가 많은 대형주라 단위 표본이 넉넉하다.
_TICKERS = ("005930.KS", "247540.KQ")

_INVESTOR_FIELDS = tuple(
    ["stck_bsop_date", "stck_clpr"]
    + [f"{p}_{k}" for p in ("frgn", "orgn", "prsn")
       for k in ("ntby_qty", "ntby_tr_pbmn", "shnu_vol", "shnu_tr_pbmn",
                 "seln_vol", "seln_tr_pbmn")])
_CREDIT_FIELDS = ("deal_date", "whol_loan_rmnd_stcn", "whol_loan_rmnd_rate",
                  "whol_stln_rmnd_stcn", "whol_stln_rmnd_rate")
_SHORT_FIELDS = ("stck_bsop_date", "ssts_cntg_qty", "ssts_vol_rlim", "ssts_tr_pbmn_rlim")
_PROGRAM_FIELDS = ("stck_bsop_date", "stck_clpr", "whol_smtn_ntby_qty",
                   "whol_smtn_ntby_tr_pbmn", "whol_smtn_shnu_vol",
                   "whol_smtn_shnu_tr_pbmn", "whol_smtn_seln_vol",
                   "whol_smtn_seln_tr_pbmn")


class _Capture(logging.Handler):
    """``bot.kis`` 경고(rt_cd·HTTP 사유)를 그 조회 동안만 모은다 — ``_get`` 은 실패를
    None 으로 접고 사유는 로그에만 남기므로, 감사가 그 사유를 판정 줄에 싣으려면
    여기서 잡아야 한다(#82). 비밀값은 로그 레코드 팩토리가 이미 가린다(#416)."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.msgs: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.msgs.append(record.getMessage())
        except Exception:                                        # noqa: BLE001
            self.msgs.append(str(record.msg))


def ask(path: str, tr_id: str, params: dict) -> tuple:
    """원천 직접 조회 → (응답 dict | None, 그동안 남은 경고들)."""
    from bot import kis_client as kc
    cap = _Capture()
    lg = logging.getLogger("bot.kis")
    lg.addHandler(cap)
    try:
        data = kc._get(path, tr_id, params)
    finally:
        lg.removeHandler(cap)
    return data, cap.msgs


def tr_verdict(label: str, data, msgs: list, *, rows_key: str, date_field: str,
               expected, parser) -> list:
    """한 TR 의 관측 → 판정 줄들(순수). 배선과 따로 값으로 재려고 함수로 뺀다(#176)."""
    from bot import kis_client as kc
    if not data:
        why = msgs[-1] if msgs else "사유 미기록(네트워크 실패이면 bot.kis 경고가 남는다)"
        return [f"❌ {label}: 원천이 답하지 않았습니다 — {why}"]
    rows = kc._dated(kc._rows_of(data, rows_key), date_field)
    if not rows:
        keys = sorted(data) if isinstance(data, dict) else []
        return [f"❌ {label}: 응답은 왔는데 날짜({date_field})를 읽을 수 있는 행이 0개 — "
                f"응답 키: {', '.join(keys[:8]) or '없음'}"]
    out = [f"   {label}: 행 {len(rows)}개 · {rows[-1][0]} ~ {rows[0][0]}"]
    miss = [f for f in expected if f not in rows[0][1]]
    if miss:
        out.append(f"❌ {label}: 기대한 필드 {len(miss)}개가 응답에 없습니다 — "
                   f"{', '.join(miss)} (원천이 이름을 바꿨거나 공식 샘플과 다릅니다)")
    parsed = parser(data)
    if not parsed:
        out.append(f"❌ {label}: 파서가 값을 못 만들었습니다(채워진 행 없음)")
        return out
    if "unit_won" in parsed:
        if parsed.get("unit_won"):
            out.append(f"✅ {label}: 금액 단위 ×{parsed['unit_won']:,} "
                       f"({parsed.get('unit_note')}) · 최근 {parsed.get('asof')}")
        else:
            out.append(f"❌ {label}: 금액 단위를 못 잽니다 — {parsed.get('unit_note')}")
    elif not miss:
        out.append(f"✅ {label}: 최근 {parsed.get('asof')}")
    return out


def market_code_verdict(results: dict) -> list:
    """코스닥 종목에 J·Q 를 물은 결과 → 판정 줄들(순수). ``results`` = {코드: (가격|None, 사유)}."""
    j_px, j_why = results.get("J", (None, ""))
    q_px, q_why = results.get("Q", (None, ""))
    out = [f"   J(공식 값): {'받음 ₩' + format(j_px, ',') if j_px else '거절 — ' + (j_why or '사유 미기록')}",
           f"   Q(옛 판): {'받음 ₩' + format(q_px, ',') if q_px else '거절 — ' + (q_why or '사유 미기록')}"]
    if j_px:
        out.append("✅ 시장 분류 코드: 코스닥에 J 를 받습니다 — 제품이 쓰는 값")
    elif q_px:
        out.append("❌ 시장 분류 코드: 원천이 코스닥에 J 를 거절하고 Q 만 받습니다 — "
                   "bot.kis_client._mkt_div 를 되돌려야 합니다")
    else:
        out.append("❌ 시장 분류 코드: J·Q 둘 다 거절 — 위 사유를 볼 것")
    return out


def main(argv: list | None = None) -> int:
    from bot import kis_client as kc
    from bot.env_keys import env_diag
    tickers = list(argv or []) or list(_TICKERS)
    print(f"# kis_flow_audit v{_VER} · 수급 판 {kc._FLOW_SCHEMA} · interpreter {sys.executable}")
    if not kc.kis_ready():
        print("❌ KIS 자격증명이 없습니다 — 수급 탭·차트 실시간가가 KIS 를 못 씁니다: "
              + env_diag("KIS_APP_KEY", "KIS_APP_SECRET"))
        return 1
    today = datetime.now(kc._KST).strftime("%Y%m%d")
    n_err = 0
    for tkr in tickers:
        code = kc._ticker_to_code(tkr)
        if not code:
            print(f"❓ {tkr}: 국내 6자리 티커가 아니라 건너뜁니다")
            continue
        print(f"\n── {tkr}")
        lines: list[str] = []
        d, m = ask("/uapi/domestic-stock/v1/quotations/inquire-investor", "FHKST01010900",
                   {"FID_COND_MRKT_DIV_CODE": kc._mkt_div(tkr), "FID_INPUT_ISCD": code})
        lines += tr_verdict("① 투자자", d, m, rows_key="output", date_field="stck_bsop_date",
                            expected=_INVESTOR_FIELDS, parser=kc.parse_investor_flow)
        credit_ok = []
        for d1 in (today, ""):
            d, m = ask("/uapi/domestic-stock/v1/quotations/daily-credit-balance",
                       "FHPST04760000",
                       {"FID_COND_MRKT_DIV_CODE": kc._mkt_div(tkr),
                        "FID_COND_SCR_DIV_CODE": "20476", "FID_INPUT_ISCD": code,
                        "FID_INPUT_DATE_1": d1})
            tag = f"② 신용(결제일자={d1 or '빈값'})"
            got = tr_verdict(tag, d, m, rows_key="output", date_field="deal_date",
                             expected=_CREDIT_FIELDS, parser=kc.parse_credit_balance)
            if not any(g.startswith("❌") for g in got):
                credit_ok.append(d1 or "빈값")
            lines += [g.replace("❌", "·", 1) if g.startswith("❌") else g for g in got]
        if not credit_ok:
            lines.append("❌ ② 신용: 결제일자 오늘·빈값 둘 다 쓸 수 있는 응답이 아닙니다 — 위 사유를 볼 것")
        d, m = ask("/uapi/domestic-stock/v1/quotations/daily-short-sale", "FHPST04830000",
                   {"FID_COND_MRKT_DIV_CODE": kc._mkt_div(tkr), "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": "", "FID_INPUT_DATE_2": ""})
        lines += tr_verdict("③ 공매도", d, m, rows_key="output2", date_field="stck_bsop_date",
                            expected=_SHORT_FIELDS, parser=kc.parse_short_sale)
        d, m = ask("/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
                   "FHPPG04650201",
                   {"FID_COND_MRKT_DIV_CODE": kc._mkt_div(tkr), "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": ""})
        lines += tr_verdict("④ 프로그램(일별)", d, m, rows_key="output",
                            date_field="stck_bsop_date", expected=_PROGRAM_FIELDS,
                            parser=kc.parse_program_daily)
        if tkr.upper().endswith(".KQ"):
            res = {}
            for mc in ("J", "Q"):
                d, m = ask("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                           {"FID_COND_MRKT_DIV_CODE": mc, "FID_INPUT_ISCD": code})
                out_ = (d or {}).get("output")
                px = kc._int(out_.get("stck_prpr")) if isinstance(out_, dict) else None
                res[mc] = (px, m[-1] if m else "")
            lines.append("   ⑤ 시장 분류 코드(코스닥):")
            lines += market_code_verdict(res)
        for ln in lines:
            print(ln)
        n_err += sum(1 for ln in lines if ln.startswith("❌"))
    print(f"\n결함 {n_err}건")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
