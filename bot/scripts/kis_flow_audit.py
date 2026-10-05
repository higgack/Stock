"""KIS 수급 4종 + 시장 분류 코드 실측 감사 — 실제 응답이 **공식 샘플의 모양**과 맞나(매일).

2026-10-04(실수 #433): 수급 4종(투자자·신용·공매도·프로그램)이 KIS 공식 샘플과 다른
필드 이름을 읽어 칸들이 늘 비거나(개인·기관 세부·대주·프로그램) 단위가 틀렸고(수량을
만원으로 읽음), 종목 페이지 쪽 블록은 없는 메서드를 불러 한 번도 안 돌았다 — 어느
감사도 그걸 안 봤다. 필드 이름은 공식 샘플(github.com/koreainvestment/open-trading-api
``examples_llm/domestic_stock/*/chk_*.py``)로 고쳤지만 **실제 응답**은 개발
샌드박스에서 잴 수 없다(KIS 는 운영 키로만 닿는다). 그래서 이 감사가 매일 운영에서 잰다:

  ① 투자자(FHKST01010900) · ② 신용(FHPST04760000, 결제일자 오늘/빈값 둘 다) ·
  ③ 공매도(FHPST04830000) · ④ 프로그램 일별(FHPPG04650201) — 원천이 답하나 ·
     기대한 필드가 다 있나 · 금액 단위가 항등식으로 잡히나(행마다도 맞나) ·
     순매수 = 매수 − 매도 인가(이름은 맞는데 뜻이 다른 필드를 잡는다) · 최신 확정일이
     마지막으로 끝난 거래일보다 몇 거래일 뒤인가(``_STALE_SESSIONS`` 넘게 멈추면 ❌)
  ⑤ 시장 분류 코드 — 코스닥 종목에 J(공식 값, 제품이 쓰는 값)와 Q(옛 판)를 나란히 물어
     원천이 어느 쪽을 받는지

판정 규약:
  ❌ = 우리가 고칠 것(필드 이름이 다름 · 단위를 못 잼·행마다 갈림 · 순≠매수−매도 ·
       최신 확정일이 오래 멈춤 · 원천이 **답하고** J 를 거절(rt_cd·4xx)하거나 답은 했는데
       J 로는 현재가가 빔 · 원천이 답하지 않음 · 자격증명 없음 · 원천 장애로 남은 조회 생략)
  ⚠️ = 지금은 판정을 보류하는 것(표본이 부족해 단위를 못 잼 — 거래가 적은 구간 · J 를
       일시 장애로 못 잼 — 타임아웃·5xx 는 거절이 아니다)
  ❓ = 판정 불가(국내 티커가 아님). 대조 0건은 ✅ 가 아니다(#54).

⚠️ 요청(경로·tr_id·파라미터)·기대 필드·파서는 제품(``bot.kis_client`` 의
   ``flow_request``·``FLOW_TRS``·``FLOW_FIELDS``·``FLOW_PARSERS``)과 **같은 것**을 쓴다 —
   따로 적으면 제품을 고쳐도 옛 요청을 잰다(#35·#38). 수급 캐시·차단기는 거치지 않고
   원천을 직접 묻는다(그래야 오늘의 응답을 잰다). 토큰 캐시는 제품과 공유한다.
⚠️ 진단이 운영 상태를 바꾸지 않는다 — 파서를 ``warn=False`` 로 불러 제품의 '필드 없음'
   1회 경고 예산을 쓰지 않는다(#264). 연속 전송 실패(5xx·타임아웃·연결) 2회면 남은
   조회를 건너뛴다 — 장애 때 조회마다 10초씩 직렬로 기다리면 뒤 감사가 밀린다.
⚠️ 판정 줄에는 종목을 싣는다 — 결산(``audit_sweep``)은 ❌ 줄만 올리므로 어느 종목인지가
   그 줄에 있어야 한다(#356). 정보 줄은 원숫자로 시작하지 않는다(결산이 원숫자 줄을 절
   제목으로 읽어 엉뚱한 축 이름을 붙였다).
⚠️ 못 보는 축: 이 감사는 매일 08:00(장 전)에 돌아 **오늘 행**(장중 부분값·자리표시)은
   관측하지 않는다 — 그건 제품이 잠정으로 빼고 ``pending_note`` 로 화면·프롬프트에
   적는다. 깨끗한 날 결산은 무음이라 잰 단위·행 수는 수동 실행해야 보인다.
⚠️ 판정 글자는 판정에만 쓴다 — 요약은 세기만 한다(#250·#268·#289).

  cd ~/stock && .venv/bin/python -m bot.scripts.kis_flow_audit [티커…]
"""
from __future__ import annotations

import sys

_VER = 3
# 코스피 1 · 코스닥 1 — 거래가 많은 대형주라 단위 표본이 넉넉하다.
_TICKERS = ("005930.KS", "247540.KQ")
_LABELS = {"investor": "① 투자자", "credit": "② 신용", "short": "③ 공매도",
           "program": "④ 프로그램(일별)"}
_STOP_AFTER = 2          # 연속 전송 실패가 이만큼이면 남은 조회를 건너뛴다
_NET_ROWS = 5            # 순 = 매수 − 매도 대조는 최근 확정 5행
# 최신 확정일이 이만큼 거래일 넘게 멈추면 ❌ — 원천이 멈췄거나 우리가 옛 행을 고른다.
# 공표 지연(신용잔고는 결제 뒤에야 나온다)으로 하루이틀 뒤처지는 건 정상이라 넉넉히 둔다
# (그 지연은 재지 않았다 — 늘 뜨는 ❌ 는 진짜 ❌ 를 가린다, #260).
_STALE_SESSIONS = 10
# 원천이 J 로는 값을 주지 않았다고 말할 수 있는 결과 — 원천이 **답했는데** 받지 않았거나
# (rt_cd·4xx) 답은 했는데 현재가가 비었다(``empty``). 전송 실패·토큰·응답 모양 문제는 J 를
# 잰 게 아니다(2026-10-04 델타 리뷰 H1).
_REJECT_KINDS = ("rt_cd", "http4xx", "empty")
_EMPTY_PRICE = "원천은 답했지만(rt_cd 0) 현재가(stck_prpr)가 비었거나 0 입니다"


def why_of(info: dict) -> str:
    """``_get_ex`` 의 사유 → 사람이 읽을 한 줄. 404 는 'TR 경로가 틀렸다' 일 수 있다 —
    네트워크 쪽으로 안내하면 엉뚱한 데를 고친다(2026-10-04 리뷰 M5)."""
    kind, st, msg = info.get("kind"), info.get("status"), info.get("msg") or ""
    if kind == "http4xx" and st == 404:
        return "HTTP 404 — TR 경로가 틀렸을 수 있습니다"
    return msg or str(kind or "사유 미기록")


def ask(path: str, tr_id: str, params: dict) -> tuple:
    """원천 직접 조회(수급 캐시·차단기 우회) → ``(응답 | None, 사유 한 줄, 사유 kind)``."""
    from bot import kis_client as kc
    data, info = kc._get_ex(path, tr_id, params)
    return data, why_of(info), info.get("kind")


def net_identity_lines(tag: str, kind: str, rows: list) -> list:
    """순매수 = 매수 − 매도 대조(수량은 정확히, 금액은 반올림 1단위 + 0.1% 까지).
    필드 이름이 샘플과 같아도 뜻이 다르면 이 항등식이 깨진다(리뷰 L9)."""
    from bot import kis_client as kc
    if kind == "investor":
        trios = [(f"{p}_ntby{k}", f"{p}_shnu{s}", f"{p}_seln{s}", amt)
                 for _w, p in kc._INV_WHO
                 for k, s, amt in (("_qty", "_vol", False), ("_tr_pbmn", "_tr_pbmn", True))]
    elif kind == "program":
        trios = [("whol_smtn_ntby_qty", "whol_smtn_shnu_vol", "whol_smtn_seln_vol", False),
                 ("whol_smtn_ntby_tr_pbmn", "whol_smtn_shnu_tr_pbmn",
                  "whol_smtn_seln_tr_pbmn", True)]
    else:
        return []
    checked, bad = 0, []
    for d, r in rows[:_NET_ROWS]:
        for nf, bf, sf, amt in trios:
            n, b, s = kc._int(r.get(nf)), kc._int(r.get(bf)), kc._int(r.get(sf))
            if n is None or b is None or s is None:
                continue
            checked += 1
            tol = max(1.0, 0.001 * max(abs(b), abs(s))) if amt else 0
            if abs(n - (b - s)) > tol:
                bad.append(f"{d} {nf} {n:,} vs 매수−매도 {b - s:,}")
    if bad:
        return [f"❌ {tag}: 순매수 ≠ 매수 − 매도 {len(bad)}칸 — {'; '.join(bad[:3])}"
                " (필드 뜻이 공식 샘플과 다를 수 있습니다)"]
    if not checked:
        return [f"   · {tag}: 순 = 매수 − 매도 를 대조할 칸이 없습니다"]
    return [f"✅ {tag}: 순매수 = 매수 − 매도 ({checked}칸 대조)"]


def tr_verdict(tag: str, kind: str, data, why: str, *, now=None) -> list:
    """한 TR 의 관측 → 판정 줄들(순수). ``tag`` = '종목 라벨'. 배선과 따로 값으로 재려고
    함수로 뺀다(#176)."""
    from bot import kis_client as kc
    if not data:
        return [f"❌ {tag}: 원천이 답하지 않았습니다 — {why or '사유 미기록'}"]
    _p, _t, rows_key, date_field, _pre = kc.FLOW_TRS[kind]
    rows = kc._dated(kc._rows_of(data, rows_key), date_field)
    if not rows:
        keys = sorted(data) if isinstance(data, dict) else []
        return [f"❌ {tag}: 응답은 왔는데 날짜({date_field})를 읽을 수 있는 행이 0개 — "
                f"응답 키: {', '.join(keys[:8]) or '없음'}"]
    out = [f"   · {tag}: 행 {len(rows)}개 · {rows[-1][0]} ~ {rows[0][0]}"]
    parsed = kc.FLOW_PARSERS[kind](data, now=now, warn=False)
    if not parsed:
        out.append(f"❌ {tag}: 파서가 값을 못 만들었습니다 — {kc.why_empty(kind, data, now=now)}")
        return out
    lag = sessions_lag(parsed.get("asof"), now)
    when = f"최근 {parsed.get('asof')}" + (f"({lag}거래일 전)" if lag is not None else "")
    if lag is not None and lag >= _STALE_SESSIONS:
        out.append(f"❌ {tag}: 최신 확정일 {parsed.get('asof')} 이 {lag}거래일 전입니다 — "
                   "원천이 멈췄거나 옛 행을 고르고 있습니다")
    miss = parsed.get("missing") or []
    if miss:
        out.append(f"❌ {tag}: 기대한 필드 {len(miss)}개가 응답에 없습니다 — "
                   f"{', '.join(miss)} (원천이 이름을 바꿨거나 공식 샘플과 다릅니다)")
    if "unit_won" in parsed:
        unit, note = parsed.get("unit_won"), parsed.get("unit_note") or ""
        if unit and not parsed.get("unit_bad"):
            out.append(f"✅ {tag}: 금액 단위 ×{unit:,} ({note}) · {when}")
        elif unit:
            out.append(f"❌ {tag}: 금액 단위 ×{unit:,} 이 일부 행과 맞지 않습니다 — {note}")
        elif (parsed.get("unit_samples") or 0) < 2:
            out.append(f"⚠️ {tag}: 표본이 부족해 금액 단위를 못 잽니다 — {note} "
                       "(거래가 적은 구간 · 판정 보류)")
        else:
            out.append(f"❌ {tag}: 금액 단위를 못 잽니다 — {note}")
        out += net_identity_lines(tag, kind, [(d, r) for d, r in rows if d <= parsed["asof"]])
    elif not miss:
        out.append(f"✅ {tag}: {when}")
    return out


def sessions_lag(asof, now) -> "int | None":
    """최신 확정일 ``asof`` 가 '마지막으로 끝난 KRX 거래일' 보다 몇 거래일 뒤인가(못 재면 None).
    하루 끝(20:00) 전이면 오늘은 아직 안 끝났다 — 감사는 08:00 에 돈다."""
    from datetime import timedelta
    from bot import kis_client as kc
    from bot import market_calendar as mc
    if not asof or now is None:
        return None
    done = now if now.hour * 60 + now.minute >= kc._krx_day_end_min() else now - timedelta(days=1)
    ref = mc.last_session_on_or_before("KR", done.strftime("%Y-%m-%d"))
    return mc.sessions_behind("KR", asof, ref) if ref else None


def credit_verdict(tkr: str, attempts: list) -> list:
    """신용 두 시도(결제일자 오늘·빈값, **제품 순서**) → 판정 줄들(순수). ``attempts`` =
    [(d1, 줄들, 제품이 이 응답을 쓰나)]. 제품(``_flow_get``)은 파서가 값을 만든 **첫** 응답을
    쓰고 멈춘다 — 그 응답의 ❌(예: 필드 누락)는 제품이 실제로 싣는 결함이라 그대로 둔다.
    제품이 안 쓰는 시도(폴백 전의 실패 · 이미 쓴 뒤의 시도)의 ❌·⚠️ 는 정보로 내린다
    (델타 리뷰 L1 — 옛 판은 빈값이 깨끗하면 오늘 응답의 ❌ 를 지워 결함을 숨겼다). 둘 다
    못 쓰면 **두 사유를 그 한 줄에** 싣는다 — 결산은 ❌ 줄만 올린다(리뷰 M4)."""
    used = next((i for i, a in enumerate(attempts) if a[2]), None)
    out, reasons = [], []
    for i, (d1, got, usable) in enumerate(attempts):
        if i == used:
            out += got
            continue
        bad = [g for g in got if g.startswith("❌")]
        if bad and not usable:
            reasons.append(f"{d1 or '빈값'}: {bad[0].split(': ', 1)[-1]}")
        out += [("   · " + g.split(" ", 1)[1].strip()) if g.startswith(("❌", "⚠️")) else g
                for g in got]
    if used is None:
        out.append(f"❌ {tkr} ② 신용: 결제일자 오늘·빈값 둘 다 쓸 수 있는 응답이 아닙니다 — "
                   + " · ".join(reasons))
    return out


def market_code_verdict(tkr: str, results: dict) -> list:
    """코스닥 종목에 J·Q 를 물은 결과 → 판정 줄들(순수). ``results`` = {코드: (가격|None,
    사유, 사유 kind)}. '거절' 은 원천이 **답했는데** 안 받은 것(``_REJECT_KINDS``)일 때만
    말한다 — 타임아웃·5xx·토큰으로 J 를 못 잰 날에 Q 가 우연히 받아졌다고 '_mkt_div 를
    되돌려야 합니다' 를 처방하면, 현재가·실시간·분봉·일봉 조회까지 같이 되돌리게 된다(델타
    리뷰 H1). 결산은 ❌ 줄만 올리므로 J 의 사유를 **그 줄에** 싣는다.

    원천이 답했는데(kind ``ok``) 현재가가 비었으면 '못 잼' 이 아니라 그 코드로는 값이 안
    나온다는 **관측**이다 — 옛 판은 그걸 '원천이 답하지 않음 — ok' 로 적고 보류했다(배포전
    셀프리뷰)."""
    j_px, j_why, j_kind = (tuple(results.get("J", ())) + (None, "", ""))[:3]
    q_px, q_why, q_kind = (tuple(results.get("Q", ())) + (None, "", ""))[:3]
    if not j_px and j_kind == "ok":
        j_why, j_kind = _EMPTY_PRICE, "empty"
    if not q_px and q_kind == "ok":
        q_why = _EMPTY_PRICE

    def _seen(px, why):
        return "받음 ₩" + format(px, ",") if px else "못 받음 — " + (why or "사유 미기록")
    out = [f"   · J(공식 값): {_seen(j_px, j_why)}", f"   · Q(옛 판): {_seen(q_px, q_why)}"]
    jw, qw = j_why or "사유 미기록", q_why or "사유 미기록"
    if j_px:
        out.append(f"✅ {tkr} 시장 분류 코드: 코스닥에 J 를 받습니다 — 제품이 쓰는 값")
    elif j_kind not in _REJECT_KINDS:
        out.append(f"⚠️ {tkr} 시장 분류 코드: J 를 못 쟀습니다(원천이 답하지 않음 — {jw}) · "
                   f"Q: {_seen(q_px, q_why)} — 판정 보류")
    elif q_px:
        out.append(f"❌ {tkr} 시장 분류 코드: 원천이 코스닥에 J 로는 값을 주지 않고(J: {jw}) "
                   "Q 로는 줍니다 — 이어지면 bot.kis_client._mkt_div 를 되돌릴 것"
                   "(현재가·실시간·분봉·일봉 조회도 같이 바뀐다)")
    else:
        out.append(f"❌ {tkr} 시장 분류 코드: J 로는 값을 주지 않고 Q 로도 값을 못 받았습니다 — "
                   f"J: {jw} · Q: {qw}")
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
    now = kc._now_kst()
    today = now.strftime("%Y%m%d")
    state = {"streak": 0, "skipped": 0, "last": "", "first": ""}

    def _ask(tag: str, path: str, tr_id: str, params: dict) -> tuple:
        if state["streak"] >= _STOP_AFTER:
            state["skipped"] += 1
            return None, "원천 장애로 묻지 않았습니다", "skipped"
        data, why, kind = ask(path, tr_id, params)
        if kind in kc._TRANSPORT_FAIL:
            if not state["streak"]:
                state["first"] = tag           # 이 연속 실패가 시작된 조회(델타 리뷰 L2)
            state["streak"] += 1
            state["last"] = why
        else:
            state["streak"] = 0
        return data, why, kind

    n_err = 0
    for tkr in tickers:
        code = kc._ticker_to_code(tkr)
        if not code:
            print(f"❓ {tkr}: 국내 6자리 티커가 아니라 건너뜁니다")
            continue
        print(f"\n── {tkr}")
        lines: list[str] = []
        for kind in ("investor", "credit", "short", "program"):
            tag = f"{tkr} {_LABELS[kind]}"
            if kind == "credit":
                attempts, n_skip = [], 0
                for d1 in (today, ""):
                    t1 = f"{tag}(결제일자={d1 or '빈값'})"
                    d, why, k = _ask(t1, *kc.flow_request(kind, tkr, date1=d1))
                    if k == "skipped":
                        # 묻지 못한 시도는 '쓸 수 있었다' 가 아니다 — 실패 사유로 센다
                        n_skip += 1
                        attempts.append((d1, [f"❌ {t1}: {why}"], False))
                        continue
                    # 제품(_flow_get)이 이 응답을 쓰나 — 파서가 값을 만들면 쓰고 멈춘다
                    usable = bool(d) and bool(kc.FLOW_PARSERS[kind](d, now=now, warn=False))
                    attempts.append((d1, tr_verdict(t1, kind, d, why, now=now), usable))
                if n_skip == len(attempts):        # 전부 못 물었다 — 끝의 장애 요약이 센다
                    lines.append(f"   · {tag}: 원천 장애로 묻지 않았습니다")
                else:
                    lines += credit_verdict(tkr, attempts)
                continue
            d, why, k = _ask(tag, *kc.flow_request(kind, tkr))
            if k == "skipped":
                lines.append(f"   · {tag}: {why}")
                continue
            lines += tr_verdict(tag, kind, d, why, now=now)
        if tkr.upper().endswith(".KQ"):
            res = {}
            for mc in ("J", "Q"):
                d, why, k = _ask(f"{tkr} 시장 분류 코드 {mc}",
                                 "/uapi/domestic-stock/v1/quotations/inquire-price",
                                 "FHKST01010100",
                                 {"FID_COND_MRKT_DIV_CODE": mc, "FID_INPUT_ISCD": code})
                out_ = (d or {}).get("output")
                px = kc._int(out_.get("stck_prpr")) if isinstance(out_, dict) else None
                res[mc] = (px, why, k)
            if any(r[2] == "skipped" for r in res.values()):
                lines.append(f"   · {tkr} 시장 분류 코드: 원천 장애로 묻지 않았습니다")
            else:
                lines.append("   · 시장 분류 코드(코스닥):")
                lines += market_code_verdict(tkr, res)
        for ln in lines:
            print(ln)
        n_err += sum(1 for ln in lines if ln.startswith("❌"))
    if state["skipped"]:
        # 절 제목을 따로 찍는다 — 안 찍으면 결산이 이 줄을 마지막 종목 절에 붙여 한 번도 안
        # 물은 종목을 장애로 지목한다(델타 리뷰 L2). 시작한 조회를 그 줄에 싣는다(#356).
        print("\n── 원천 장애")
        print(f"❌ 원천 장애 — {state['first'] or '사유 미기록'} 부터 연속 전송 실패 "
              f"{_STOP_AFTER}회({state['last']}) 뒤 남은 조회 {state['skipped']}개를 "
              "건너뛰었습니다")
        n_err += 1
    print(f"\n결함 {n_err}건")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
