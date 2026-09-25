"""한국 수출·수입 카드 원천 = 관세청 수출입총괄(실수 #413, 사용자 2026-09-25).

ECOS(901Y118)는 관세청 확정치를 +34일에야 재게시해(VM 캐시 실측: 7월분을 09-03 에
처음 보임) 카드의 공표 규약 '관세청 통관 확정 익월 15일 전후' 보다 매달 열흘 뒤처졌다.
원천을 `bot.customs_trade_client`(getNewtradeList)로 옮기고 ECOS 는 계열 통째 폴백.

네트워크 0 — 원천은 `_http_get` 경계에서 스텁한다(#155 원천이 보내는 모양대로 ·
#20 배선은 수집기를 통째로 태워야 보인다). 날짜는 **시계에서 파생**한다(#249·#342
리터럴 날짜는 시한폭탄).
"""
from __future__ import annotations

import io
import time
from contextlib import redirect_stdout

import pytest

import bot.customs_trade_client as ct

_KEY = "".join(("test", "Key", "123", "=="))      # 조립 — 시크릿 스캐너 대상 아님


def _xml(rows, code="00", msg="NORMAL SERVICE.", extra=""):
    """관세청 수출입총괄 응답 모양(문서의 필드명 그대로) — rows: (year, 수출USD, 수입USD)."""
    items = "".join(
        f"<item><balPayments>{e - i}</balPayments><expCnt>7</expCnt><expDlr>{e}</expDlr>"
        f"<impCnt>9</impCnt><impDlr>{i}</impDlr><year>{y}</year></item>"
        for y, e, i in rows)
    return (f'<?xml version="1.0" encoding="UTF-8"?><response><header>'
            f"<resultCode>{code}</resultCode><resultMsg>{msg}</resultMsg></header>"
            f"<body><items>{items}{extra}</items></body></response>")


def _months(s, e):
    out, m = [], s
    while m <= e:
        out.append(m)
        m = ct.ym_shift(m, 1)
    return out


def _usd(ym):
    """달마다 다른 값 — 첫 달·끝 달을 값으로 가를 수 있게(#91c)."""
    return (500 + int(ym[4:]) * 10 + (int(ym[:4]) - 2000)) * 1e8


def _window_fetch(calls, *, status=200, path_ok=None, extra_months=()):
    """요청 창에 맞춰 응답을 만드는 가짜 원천. `path_ok` 이외 경로는 404."""
    def fetch(path, s, e, key):
        calls.append((path, s, e, key))
        if path_ok is not None and path != path_ok:
            return 404, "<html>Not Found</html>"
        if status != 200:
            return status, "<html>err</html>"
        rows = [(f"{m[:4]}.{m[4:]}", _usd(m), _usd(m) * 0.8) for m in _months(s, e)]
        rows += [(f"{m[:4]}.{m[4:]}", 1.0, 1.0) for m in extra_months]
        return 200, _xml(rows, extra="<item><year>총계</year><expDlr>1</expDlr></item>")
    return fetch


@pytest.fixture()
def cti(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "_CACHE_DIR", tmp_path / "customs")
    monkeypatch.setattr(ct, "_fail", {})
    monkeypatch.setattr(ct, "_good_path", None)
    monkeypatch.setattr(ct, "_warned", set())
    monkeypatch.setenv("DATA_GO_KR_API_KEY", _KEY)
    return ct


# ── 순수 헬퍼 ────────────────────────────────────────────────────────────
def test_windows_split_into_chunks_that_fit_a_default_page():
    """한 창은 6개월 — '총계' 행까지 7행이라 data.go.kr 기본 쪽 크기(10)에 안 잘린다(#280)."""
    assert ct.CHUNK_MONTHS <= 9, "기본 쪽 크기 10행 안에 '총계' 까지 들어가야 한다"
    assert ct.windows("202508", "202608", size=6) == [
        ("202508", "202601"), ("202602", "202607"), ("202608", "202608")]
    assert ct.windows("202601", "202612", size=12) == [("202601", "202612")]
    assert ct.windows("202612", "202612") == [("202612", "202612")]
    assert ct.ym_shift("202601", -1) == "202512" and ct.ym_shift("202512", 1) == "202601"
    assert ct.month_count("202508", "202608") == 13 and ct.month_count("202608", "202608") == 1


def test_norm_ym_takes_only_month_shapes():
    for raw in ("2026.08", "2026-08", "202608", " 2026.08 "):
        assert ct.norm_ym(raw) == "202608", raw
    for raw in ("총계", "2026.13", "", None, "2026"):
        assert ct.norm_ym(raw) == "", raw


def test_parse_reads_fields_and_skips_non_month_rows():
    rows, err, kind = ct.parse(_xml([("2026.07", 900, 700), ("2026.08", 990, 800)],
                                    extra="<item><year>총계</year><expDlr>1890</expDlr></item>"))
    assert err == "" and kind == "" and [r["ym"] for r in rows] == ["202607", "202608"]
    assert rows[1] == {"ym": "202608", "exp": 990.0, "imp": 800.0, "bal": 190.0}


def test_parse_names_both_error_envelopes():
    """오류 봉투는 두 벌이다(#352) — 한쪽만 보면 다른 쪽이 '행 없음' 으로 둔갑한다."""
    rows, err, _k = ct.parse(_xml([], code="10", msg="INVALID_REQUEST_PARAMETER_ERROR"))
    assert rows == [] and err.startswith("resultCode=10") and "INVALID" in err
    gw = ("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
          "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
          "<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>")
    rows, err, kind = ct.parse(gw)
    assert rows == [] and "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in err and "30" in err
    assert kind == "응답 오류"                    # 키 미등록 — 우리가 고칠 것
    assert "XML·JSON 이 아닌" in ct.parse("Unauthorized")[1]
    assert ct.parse("")[1] == "빈 응답"
    assert ct.parse("<response><header>")[1].startswith("XML 형식 오류")


def test_result_codes_split_no_data_transient_and_ours():
    """결과코드가 갈래를 정한다(#82) — 03 은 오류가 아니라 **빈 창**이고(그걸 오류로 보면
    확정 전 달만 담긴 창 하나에 계열 전체가 폴백한다), 22(일일 한도)·05(시간초과)는
    기다리면 풀리고, 10·30 은 우리가 고칠 것이다. 게이트웨이 봉투도 같은 표를 쓴다."""
    assert ct.parse(_xml([], code="03", msg="NODATA_ERROR")) == ([], "", "")
    for code in ("22", "05"):
        rows, err, kind = ct.parse(_xml([], code=code, msg="X"))
        assert rows == [] and kind == "조회 실패" and f"resultCode={code}" in err, code
    assert ct.parse(_xml([], code="11", msg="X"))[2] == "응답 오류"
    gw = ("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
          "<returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg>"
          "<returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>")
    assert ct.parse(gw)[2] == "조회 실패"


def test_parse_accepts_json_with_either_item_shape():
    one = ('{"response":{"header":{"resultCode":"00"},"body":{"items":{"item":'
           '{"year":"2026.08","expDlr":"990","impDlr":"800","balPayments":"190"}}}}}')
    many = ('{"header":{"resultCode":"00"},"body":{"items":{"item":['
            '{"year":"2026.07","expDlr":900,"impDlr":700,"balPayments":200},'
            '{"year":"2026.08","expDlr":990,"impDlr":800,"balPayments":190}]}}}')
    assert [r["ym"] for r in ct.parse(one)[0]] == ["202608"]
    assert [r["ym"] for r in ct.parse(many)[0]] == ["202607", "202608"]


def test_cross_check_three_states():
    ours = [("202607", 990.0), ("202608", 1000.0)]
    assert ct.cross_check(ours, [("202607", 990.0)])[0] is True
    ok, why = ct.cross_check(ours, [("202607", 0.99)])
    assert ok is False and "배" in why
    ok, why = ct.cross_check(ours, [("202601", 1.0)])
    assert ok is None and "겹치는 달이 없어" in why


# ── 수집기 ───────────────────────────────────────────────────────────────
def test_capitalised_path_is_asked_first():
    """형제 선례(`trade/customs.py` — 같은 GW 계열 `Itemtrade`)가 대문자다. 이 서비스는 재지
    않아 사다리로 두되(소문자도 남긴다), 선례 쪽을 먼저 물어 404 한 번을 아낀다."""
    from trade.customs import ENDPOINT
    assert ENDPOINT.split("/1220000/")[1][0].isupper()            # 선례가 여전히 대문자인가
    assert ct.PATHS[0][0].isupper() and ct.PATHS[0].lower() in [p.lower() for p in ct.PATHS[1:]]


def test_path_ladder_moves_on_only_after_a_404(cti):
    calls: list = []
    rows, info = cti.monthly_totals(fetch=_window_fetch(calls, path_ok=cti.PATHS[1]))
    assert info["path"] == cti.PATHS[1] and len(rows) == cti.MONTHS
    # 첫 창만 두 경로를 묻고, 찾은 뒤엔 그 경로만(#61 순손실 요청 금지)
    nwin = len(cti.windows(*info["window"]))
    assert nwin >= 2, "창이 하나면 '찾은 뒤엔 그 경로만' 을 못 잰다"
    assert [c[0] for c in calls] == [cti.PATHS[0]] + [cti.PATHS[1]] * nwin, calls
    assert all(c[3] == _KEY for c in calls)


def test_an_auth_error_is_not_retried_on_another_path(cti):
    """인증 오류는 경로를 바꿔도 같은 답이다 — 더 묻지 않는다(#82·#279)."""
    calls: list = []
    rows, info = cti.monthly_totals(fetch=_window_fetch(calls, status=401))
    assert rows == [] and info["kind"] == "응답 오류" and len(calls) == 1, calls


@pytest.mark.parametrize("status, kind", [(429, "조회 실패"), (503, "조회 실패"),
                                          (400, "응답 오류")])
def test_status_kinds_split_transient_from_ours(cti, status, kind):
    """429·5xx 는 기다리면 풀리고 4xx 는 우리가 고칠 것이다 — 처방이 반대다(#82)."""
    rows, info = cti.monthly_totals(fetch=_window_fetch([], status=status))
    assert rows == [] and info["kind"] == kind, info


def test_a_no_service_code_on_200_moves_the_ladder_like_a_404(cti):
    """틀린 경로에 게이트웨이가 404 대신 200 + 12(NO_OPENAPI_SERVICE)로 답해도 다음 후보를
    묻는다(재지 않았다 — 둘 다 받는다). 끝까지 없으면 '경로 없음' 으로 시도한 경로를 댄다."""
    gw12 = ("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
            "<returnAuthMsg>NO_OPENAPI_SERVICE_ERROR</returnAuthMsg>"
            "<returnReasonCode>12</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>")
    ok = _window_fetch([])
    calls: list = []

    def fetch(path, s, e, key):
        calls.append(path)
        return (200, gw12) if path == cti.PATHS[0] else ok(path, s, e, key)
    rows, info = cti.monthly_totals(fetch=fetch, use_cache=False)
    assert info["path"] == cti.PATHS[1] and len(rows) == cti.MONTHS, info
    cti._good_path = None
    rows, info = cti.monthly_totals(fetch=lambda p, s, e, k: (200, gw12), use_cache=False)
    assert rows == [] and info["kind"] == "경로 없음", info
    assert all(p in info["why"] for p in cti.PATHS) and "NO_OPENAPI_SERVICE" in info["why"]


def test_all_paths_404_names_the_paths(cti):
    rows, info = cti.monthly_totals(fetch=_window_fetch([], path_ok="nowhere"))
    assert info["kind"] == "경로 없음"
    assert all(p in info["why"] for p in cti.PATHS), info["why"]


def test_only_complete_months_inside_the_window_are_kept(cti):
    """당월은 미완결 — 확정치가 있을 수 없다(#40). 창 밖 행은 세어서 말한다(#45)."""
    today = cti._kst_today()
    cur = today.strftime("%Y%m")
    rows, info = cti.monthly_totals(fetch=_window_fetch(
        [], extra_months=(cur, "200001")))
    end = cti.ym_shift(cur, -1)
    assert rows[-1]["ym"] == end and rows[0]["ym"] == cti.ym_shift(end, -(cti.MONTHS - 1))
    assert all(r["ym"] != cur for r in rows) and info["dropped"] == 2


def _zero_tail_fetch(calls, zeros=2):
    """관세청 GW 가 확정 전 달을 0 으로 **미리 채워** 보내는 모양(`trade/customs_scan`
    실측 선례) — 창의 끝 `zeros` 달이 0."""
    def fetch(path, s, e, key):
        calls.append((path, s, e, key))
        end = ct.ym_shift(ct._kst_today().strftime("%Y%m"), -1)
        zero = {ct.ym_shift(end, -k) for k in range(zeros)}
        rows = [(f"{m[:4]}.{m[4:]}", 0 if m in zero else _usd(m),
                 0 if m in zero else _usd(m) * 0.8) for m in _months(s, e)]
        return 200, _xml(rows)
    return fetch


def test_zero_prefilled_unconfirmed_months_are_not_values(cti):
    """나라 전체 수출이 0 일 수는 없다 — 0 은 미확정이다. 카드가 '0억$' 를 그리면 안 되고
    버린 수는 말한다(#45). 전부 0 이면 '행 없음' 이되 **왜 없는지**를 적는다(#43·#82)."""
    rows, info = cti.monthly_totals(fetch=_zero_tail_fetch([], zeros=2))
    end = cti.ym_shift(cti._kst_today().strftime("%Y%m"), -1)
    assert rows[-1]["ym"] == cti.ym_shift(end, -2) and info["unconfirmed"] == 2, info
    assert all(r["exp"] and r["imp"] for r in rows)
    pts, _ = cti.series_points("export_amt", totals=(rows, info))
    assert all(v > 0 for _m, v in pts)
    cti._fail.clear()
    rows, info = cti.monthly_totals(fetch=_zero_tail_fetch([], zeros=cti.MONTHS),
                                    use_cache=False)
    assert rows == [] and info["kind"] == "행 없음" and "전부 0(미확정)" in info["why"], info


def test_a_zero_in_one_field_is_not_a_value_for_that_card():
    """한 칸만 0 인 달도 그 칸은 값이 아니다 — 수출 카드엔 빠지고 수입 카드엔 남는다."""
    rows = [{"ym": "202607", "exp": 9e10, "imp": 7e10}, {"ym": "202608", "exp": 0.0, "imp": 8e10}]
    exp, _ = ct.series_points("export_amt", totals=(rows, {}))
    imp, _ = ct.series_points("import_amt", totals=(rows, {}))
    assert [m for m, _v in exp] == ["202607"] and [m for m, _v in imp] == ["202607", "202608"]


def test_a_no_data_window_is_empty_not_a_failure(cti):
    """확정 전 달만 담긴 끝 창이 03(NODATA)을 받아도 앞 창들의 값은 산다 — 그 한 창을
    오류로 보면 계열 전체가 ECOS 로 폴백한다."""
    base = _window_fetch([])
    last_start = cti.windows(*cti.monthly_totals(fetch=base, use_cache=False)[1]["window"])[-1][0]

    def fetch(path, s, e, key):
        if s == last_start:
            return 200, _xml([], code="03", msg="NODATA_ERROR")
        return base(path, s, e, key)
    rows, info = cti.monthly_totals(fetch=fetch, use_cache=False)
    assert rows and info["kind"] == "" and rows[-1]["ym"] < last_start, info


def test_missing_key_never_calls_the_source(cti, monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_API_KEY")
    calls: list = []
    rows, info = cti.monthly_totals(fetch=_window_fetch(calls), key="")
    assert rows == [] and info["kind"] == "키 없음" and calls == []


def test_a_multiline_error_body_becomes_one_line(cti):
    """결산은 ❌ 줄 하나만 올린다 — 사유가 줄바꿈에서 잘리면 그 줄이 혼자 행동 가능하지
    않다(#356). 오류 본문은 흔히 여러 줄 HTML 이다."""
    rows, info = cti.monthly_totals(
        fetch=lambda p, s, e, k: (403, "<html>\n<body>\n Forbidden\n</body>\n</html>"))
    assert info["kind"] == "응답 오류" and "\n" not in info["why"], info["why"]
    assert "Forbidden" in info["why"]


def test_the_key_never_appears_in_a_failure_reason(cti):
    """예외 문구가 URL(키 포함)을 실을 수 있다 — 사유에서 가린다(§Secrets)."""
    def boom(path, s, e, key):
        raise RuntimeError(f"connect failed for https://x/{path}?serviceKey={key}&a=1")
    rows, info = cti.monthly_totals(fetch=boom)
    assert rows == [] and info["kind"] == "조회 실패"
    assert _KEY not in info["why"] and "***" in info["why"], info["why"]


def test_a_failure_is_remembered_briefly_but_check_bypasses_it(cti):
    calls: list = []
    rows, info = cti.monthly_totals(fetch=_window_fetch(calls, status=503))
    assert len(calls) == 1
    rows, info = cti.monthly_totals(fetch=_window_fetch(calls, status=503))
    assert len(calls) == 1 and "직전 실패" in info["why"], info   # 기억 — 안 묻는다
    cti.monthly_totals(fetch=_window_fetch(calls, status=503), use_cache=False)
    assert len(calls) == 2                                         # 진단은 지금을 잰다
    k = next(iter(cti._fail))
    cti._fail[k] = (time.monotonic() - cti._FAIL_TTL_S - 1,) + cti._fail[k][1:]
    cti.monthly_totals(fetch=_window_fetch(calls, status=503))
    assert len(calls) == 3                                         # 기억은 만료된다(#178)


def test_success_is_cached_and_check_neither_reads_nor_writes_it(cti):
    calls: list = []
    rows, info = cti.monthly_totals(fetch=_window_fetch(calls))
    n = len(calls)
    assert n == len(cti.windows(*info["window"])) and not info["cached"]
    rows2, info2 = cti.monthly_totals(fetch=_window_fetch(calls))
    assert len(calls) == n and info2["cached"] and rows2 == rows
    files = list(cti._CACHE_DIR.glob("*.json"))
    assert len(files) == 1
    files[0].unlink()
    cti.monthly_totals(fetch=_window_fetch(calls), use_cache=False)
    assert len(calls) == 2 * n and not list(cti._CACHE_DIR.glob("*.json"))


def test_a_broken_cache_file_is_a_miss_not_a_crash(cti):
    calls: list = []
    rows, _ = cti.monthly_totals(fetch=_window_fetch(calls))
    f = next(cti._CACHE_DIR.glob("*.json"))
    f.write_bytes(b"\xff\x00{not json")
    rows2, info = cti.monthly_totals(fetch=_window_fetch(calls))
    assert rows2 == rows and not info["cached"]                    # 다시 받았다(#331)


# ── 카드 계열(관세청 → ECOS 대조 → 폴백) ─────────────────────────────────
def _ecos_same(key):
    today = ct._kst_today()
    end = ct.ym_shift(today.strftime("%Y%m"), -2)                  # ECOS 는 한 달 늦다
    f = 1.0 if key == "export_amt" else 0.8
    return [(m, _usd(m) * f * ct.SCALE) for m in _months(ct.ym_shift(end, -11), end)]


def test_card_uses_customs_when_ecos_agrees(cti, monkeypatch):
    monkeypatch.setattr(cti, "_http_get", _window_fetch([]))
    for key in ("export_amt", "import_amt"):
        cs = cti.card_series(key, ecos=_ecos_same)
        assert cs["src"] == "관세청" and cs["why"] == "", cs
        assert cs["points"][-1][0] == ct.ym_shift(ct._kst_today().strftime("%Y%m"), -1)
        assert "비 중앙값" in cs["check"]


def test_a_unit_mismatch_falls_back_to_the_whole_ecos_series(cti, monkeypatch):
    """단위를 가정한 채 올리면 1000배 틀린 숫자가 조용히 뜬다(#139) — 대조가 막는다.
    폴백은 **계열 통째**다(한 차트에 두 원천을 섞지 않는다, #240·#33)."""
    monkeypatch.setattr(cti, "_http_get", _window_fetch([]))
    ecos = [(m, v / 1000) for m, v in _ecos_same("export_amt")]
    cs = cti.card_series("export_amt", ecos=lambda k: ecos)
    assert cs["src"] == "ECOS" and cs["why"] == "단위 불일치" and cs["points"] == ecos


def test_a_source_failure_falls_back_and_says_which_kind(cti, monkeypatch):
    monkeypatch.setattr(cti, "_http_get", _window_fetch([], status=401))
    cs = cti.card_series("export_amt", ecos=_ecos_same)
    assert cs["src"] == "ECOS" and cs["why"] == "응답 오류" and "HTTP 401" in cs["detail"]


def test_customs_is_used_when_ecos_cannot_check_it(cti, monkeypatch):
    """ECOS 가 없으면 대조를 못 할 뿐이다 — 사용자가 고른 원천을 버리지 않는다(판정 불가 ≠ 불일치).
    단 '못 잼' 을 통과로 접지 않는다 — `verified` 가 None 이고 진단이 ❓ 로 적는다(#54)."""
    monkeypatch.setattr(cti, "_http_get", _window_fetch([]))
    cs = cti.card_series("export_amt", ecos=lambda k: [])
    assert cs["src"] == "관세청" and "겹치는 달이 없어" in cs["check"]
    assert cs["verified"] is None
    assert cti.card_series("export_amt", ecos=_ecos_same)["verified"] is True
    monkeypatch.setattr(cti, "_ecos_points", lambda k: [])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cti._main(["--check"])
    assert rc == 0 and "export_amt: ✅ 카드 원천 = 관세청" in buf.getvalue()
    assert "❓ 단위 미대조 — ECOS 와 겹치는 달이 없어" in buf.getvalue(), buf.getvalue()


def test_a_transient_result_code_is_a_wait_not_ours(cti, monkeypatch):
    """HTTP 200 이어도 결과코드 22(일일 한도)는 기다리면 풀린다 — 폴백 갈래가 '조회 실패'
    여야 감사가 ⚠️ 로 센다(❌ 는 우리가 고칠 것만, #260)."""
    monkeypatch.setattr(cti, "_http_get",
                        lambda p, s, e, k: (200, _xml([], code="22", msg="LIMITED")))
    cs = cti.card_series("export_amt", ecos=_ecos_same)
    assert cs["src"] == "ECOS" and cs["why"] == "조회 실패" and "resultCode=22" in cs["detail"]


def test_both_sources_empty_is_an_empty_series_with_a_reason(cti, monkeypatch):
    monkeypatch.setattr(cti, "_http_get", _window_fetch([], status=503))
    cs = cti.card_series("export_amt", ecos=lambda k: [])
    assert cs["points"] == [] and cs["src"] == "" and cs["why"] == "조회 실패"


# ── 화면 배선(수집기를 통째로 태운다, #20) ────────────────────────────────
def _snapshot(tmp_path, monkeypatch, ecos_series):
    import bot.macro_snapshot as ms
    import bot.naver_marketindex as nm
    monkeypatch.setattr(ms, "_CACHE_DIR", tmp_path / "snap")
    (tmp_path / "snap").mkdir()
    monkeypatch.setattr(ms, "_fetch_macro_naver_values", lambda sids: {})
    monkeypatch.setattr(ms, "_yf_monthly_batch", lambda tk: {})
    monkeypatch.setattr(ms, "_yf_daily_1mo_batch", lambda tk: {})
    monkeypatch.setattr(ms, "_fred_monthly", lambda sid, months=12: [])
    monkeypatch.setattr(ms, "_ecos_series", ecos_series)
    for fn in ("fetch_commodity_spark", "fetch_naver_index_history",
               "fetch_naver_crypto_history", "fetch_naver_fx_history"):
        monkeypatch.setattr(nm, fn, lambda *a, **k: [])
    out = ms.fetch_macro_snapshot()
    return {r["key"]: r for r in list(out["domestic"]) + list(out["global"])}


def _ecos_all(key):
    """ECOS 카드들(경상수지 등)은 자기 값 — 수출·수입만 관세청과 같은 확정치."""
    if key in ("export_amt", "import_amt"):
        return _ecos_same(key)
    today = ct._kst_today()
    end = ct.ym_shift(today.strftime("%Y%m"), -2)
    return [(m, float(i)) for i, m in enumerate(_months(ct.ym_shift(end, -14), end))]


def test_macro_card_draws_customs_with_its_start_period(cti, tmp_path, monkeypatch):
    monkeypatch.setattr(cti, "_http_get", _window_fetch([]))
    rows = _snapshot(tmp_path, monkeypatch, _ecos_all)
    exp = rows["kr_export"]
    end = ct.ym_shift(ct._kst_today().strftime("%Y%m"), -1)
    start = ct.ym_shift(end, -11)
    assert exp["value"] == pytest.approx(_usd(end) * ct.SCALE)
    assert exp["asof"] == f"{end[:4]}-{end[4:]} · 관세청", exp["asof"]
    # 스파크라인 12점의 **첫 달** — '12개월 전' 어림이 아니다(실수 #413)
    assert exp["period_start_asof"] == f"{start[:4]}-{start[4:]}"
    assert exp["period_start"] == pytest.approx(round(_usd(start) * ct.SCALE, 0))
    assert exp["asof_stale"] is False
    imp = rows["kr_import"]
    assert imp["value"] == pytest.approx(_usd(end) * 0.8 * ct.SCALE)


def test_customs_series_exception_path_names_no_source_when_ecos_is_empty(monkeypatch):
    """관세청 모듈이 던지고 ECOS 도 비었으면 'ECOS 로 그렸다' 는 거짓이다 — 원천 칸을 비운다."""
    import bot.macro_snapshot as ms

    def boom(key, **kw):
        raise RuntimeError("x")
    monkeypatch.setattr(ct, "card_series", boom)
    monkeypatch.setattr(ms, "_ecos_series", lambda k: [])
    assert ms._customs_series("export_amt")["src"] == ""
    monkeypatch.setattr(ms, "_ecos_series", lambda k: [("202607", 1.0)])
    cs = ms._customs_series("export_amt")
    assert cs["src"] == "ECOS" and cs["why"] == "조회 실패" and "RuntimeError" in cs["detail"]


def test_macro_card_says_when_it_fell_back_to_ecos(cti, tmp_path, monkeypatch):
    monkeypatch.setattr(cti, "_http_get", _window_fetch([], status=401))
    rows = _snapshot(tmp_path, monkeypatch, _ecos_all)
    exp = rows["kr_export"]
    ecos = _ecos_same("export_amt")
    assert exp["value"] == pytest.approx(ecos[-1][1])
    assert exp["asof"].endswith("· ECOS(관세청 응답 오류)"), exp["asof"]


def test_every_ecos_card_names_its_start_period_too(cti, tmp_path, monkeypatch):
    """같은 어림('12개월 전')이 ECOS 카드 전부에 있었다 — 한 카드만 고치면 형제가 남는다(#38)."""
    monkeypatch.setattr(cti, "_http_get", _window_fetch([]))
    rows = _snapshot(tmp_path, monkeypatch, _ecos_all)
    ca = rows["kr_ca"]
    end = ct.ym_shift(ct._kst_today().strftime("%Y%m"), -2)
    first = ct.ym_shift(end, -11)
    assert ca["period_start_asof"] == f"{first[:4]}-{first[4:]}", ca


def test_the_card_prints_the_start_period_not_twelve_months_ago():
    from bot.dashboard import _render_macro_card
    html = _render_macro_card({
        "key": "kr_export", "label": "한국 수출", "unit": "억$", "value": 990.0,
        "decimals": 0, "spark": [583.0, 990.0], "spark_dir": 1, "spark_span": "12개월",
        "period_start": 583.0, "period_start_asof": "2025-09", "period_change": 407.0,
        "pct_style": False, "asof": "2026-08 · 관세청", "asof_kind": "obs"})
    assert "2025-09 대비" in html and "12개월 전" not in html
    assert "관세청" in html


# ── 감사(화면과 같은 경로, #35) ──────────────────────────────────────────
def test_fallback_line_marks_transient_and_ours_apart():
    from bot.scripts.macro_staleness_audit import customs_fallback_line as f
    pts = [("202607", 1.0)]
    assert f({"src": "관세청", "points": pts}) == ""
    assert f({"src": "ECOS", "points": pts, "why": "조회 실패",
              "detail": "ReadTimeout"}).startswith("⚠️ 관세청 원천을 못 써 ECOS 로")
    line = f({"src": "ECOS", "points": pts, "why": "응답 오류", "detail": "HTTP 401"})
    assert line.startswith("❌") and "HTTP 401" in line
    # 두 원천 다 비면 카드가 빠진다 — 갈래와 무관하게 ❌, 관세청 사유를 같이(#356)
    for why in ("조회 실패", "행 없음"):
        line = f({"src": "", "points": [], "why": why, "detail": "x9"})
        assert line.startswith("❌ 관측 없음") and why in line and "x9" in line, line


def test_audit_rows_include_the_customs_cards():
    import bot.market_overview as mo
    from bot import macro_snapshot as ms
    from bot.scripts.macro_staleness_audit import audit_rows
    keys = {k for _s, _l, k, _m, _lb in audit_rows(ms, mo)}
    assert {"customs:export_amt", "customs:import_amt"} <= keys, keys


def _run_audit(monkeypatch, cs):
    import bot.macro_snapshot as ms
    import bot.scripts.macro_staleness_audit as m
    monkeypatch.setattr(m, "audit_rows", lambda _ms, _mo: [
        ("Macro/국내", "한국 수출", "customs:export_amt", "spot", 400)])
    monkeypatch.setattr(m, "_treasury_status", lambda mo: None)
    monkeypatch.setattr(ms, "_customs_series", lambda key: cs)
    buf = io.StringIO()
    with redirect_stdout(buf):
        m.main()
    return buf.getvalue()


def test_a_fallback_and_its_staleness_count_once(monkeypatch):
    """폴백한 원천(ECOS)이 뒤처진 것은 폴백과 **같은 원인** — 두 번 세면 결산의 ❌ 가
    두 배다(#45·#250). 대조군: 관세청이 정상인데 뒤처지면 그 지연은 따로 센다."""
    from bot.audit_sweep import _findings
    old = ct.ym_shift(ct._kst_today().strftime("%Y%m"), -4)
    out = _run_audit(monkeypatch, {"points": [(old, 1.0)], "src": "ECOS",
                                   "why": "응답 오류", "detail": "HTTP 401"})
    hits = _findings(out)
    assert len(hits) == 1 and "관세청 원천을 못 써" in hits[0], hits
    assert "위 폴백 탓" in out
    out2 = _run_audit(monkeypatch, {"points": [(old, 1.0)], "src": "관세청",
                                    "why": "", "detail": ""})
    hits2 = _findings(out2)
    assert len(hits2) == 1 and "관세청 원천을 못 써" not in hits2[0], hits2


def test_an_empty_card_is_counted_once_even_when_the_cause_is_transient(monkeypatch):
    """관세청이 잠깐 막히고(⚠️ 갈래) ECOS 까지 비면 카드가 빠진다 — ❌ **한 줄**이어야
    한다. 폴백 줄과 '관측 없음' 줄을 따로 내면 결산이 한 카드를 두 번 센다(#45·#250)."""
    from bot.audit_sweep import _findings
    out = _run_audit(monkeypatch, {"points": [], "src": "", "why": "조회 실패",
                                   "detail": "ReadTimeout"})
    hits = _findings(out)
    assert len(hits) == 1 and "❌ 관측 없음" in hits[0] and "ReadTimeout" in hits[0], hits
    # ⚪ '판정 불가' 줄도 덧붙지 않는다 — 그 카드는 이미 한 줄로 판정됐다(FRED 메타를
    # 관세청 시리즈에 묻거나 '키 없음' 으로 둔갑하면 그게 오보다)
    assert out.count("한국 수출") == 1, out


def test_check_cli_reports_the_source_and_exits_by_it(cti, monkeypatch):
    """반복 확인은 제품에 심는다(#252) — 진입점을 태워 디스패치까지 본다."""
    monkeypatch.setattr(cti, "_ecos_points", _ecos_same)
    monkeypatch.setattr(cti, "_http_get", _window_fetch([]))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cti._main(["--check"])
    out = buf.getvalue()
    assert rc == 0, out
    assert "코드 지문" in out and "인터프리터" in out
    assert "export_amt: ✅ 카드 원천 = 관세청" in out and "import_amt: ✅" in out
    assert "✅ ECOS 와 겹치는" in out and "❓ 단위 미대조" not in out
    assert _KEY not in out
    assert f"받은 달 행 {cti.CHUNK_MONTHS}/{cti.CHUNK_MONTHS}" in out   # 창이 안 잘렸다
    assert "빠진 달" not in out
    # 창이 잘려 온 모양 — 받은 수와 **빠진 달 이름**을 사실로 적는다(원인은 읽는 쪽이 가른다)
    end = cti.ym_shift(cti._kst_today().strftime("%Y%m"), -1)
    full = _window_fetch([])

    def short(path, s_, e_, key):
        st, body = full(path, s_, e_, key)
        drop = f"<year>{end[:4]}.{end[4:]}</year>"
        return st, body.replace(drop, "<year>총계</year>")
    monkeypatch.setattr(cti, "_http_get", short)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cti._main(["--check"])
    ws, we = cti.windows(cti.ym_shift(end, -(cti.MONTHS - 1)), end)[-1]
    n = cti.month_count(ws, we)                     # 상수에서 파생(#67 리터럴 금지)
    assert f"받은 달 행 {n - 1}/{n} · 빠진 달 {end}" in buf.getvalue(), buf.getvalue()
    monkeypatch.setattr(cti, "_http_get", _window_fetch([], status=401))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cti._main(["--check"])
    assert rc == 1 and "❌ 카드 원천 = ECOS(관세청 응답 오류" in buf.getvalue()
    assert not list(cti._CACHE_DIR.glob("*.json")), "진단이 운영 캐시를 썼다(#264)"
