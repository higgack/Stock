"""실수 #430 — #429 의 ①②를 운영 실데이터로 재다가 드러난 것들.

`bot/scripts/dart_gap_audit.py` 의 키 없는 격리 자식이 첫 시험에서 결함 둘을
찾았다:

  · DART 회사 목록(corpCode) 로더가 다운로드 실패를 **프로세스 수명 내내** 빈
    목록으로 기억했다 — 싱글턴이라 한 번의 일시 실패가 재시작 전까지 이름→코드
    조회를 전부 죽인다(#161 빈 목록을 영구히 믿지 말 것). 그리고 만료된 디스크
    캐시가 있어도 빈 목록을 냈다(#384 낡은 값이 빈 값보다 낫다 — 회사 목록은
    천천히 바뀐다).
  · trade 회사 보고서가 그 빈 목록 때문에 이름을 못 풀면 '미확보(비상장·해외·
    미발견)' 라고 적었다 — 키가 없거나 목록을 못 받은 것이지 회사가 없는 게
    아니다(#82·#429 ②).
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile

import pytest

_KEY = "k-1234567890abcdef"


def _corp_zip(rows) -> bytes:
    xml = "<result>" + "".join(
        f"<list><corp_code>{cc}</corp_code><corp_name>{nm}</corp_name>"
        f"<stock_code>{sc}</stock_code></list>" for cc, nm, sc in rows) + "</result>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml.encode("utf-8"))
    return buf.getvalue()


class _Resp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


@pytest.fixture
def corp(monkeypatch, tmp_path):
    """회사 목록 다운로드를 갈아 끼운다 — `st['fail']` 이면 던지고, 아니면 zip."""
    import bot.dart_client as dc
    cache = tmp_path / "dart_corpcode_v2.json"
    monkeypatch.setattr(dc, "_CORPCODE_CACHE", cache)
    st = {"fail": False, "calls": 0,
          "rows": [("00126380", "삼성전자", "005930")]}

    def get(url, params=None, timeout=None, **k):
        assert url.endswith("/corpCode.xml"), url
        st["calls"] += 1
        if st["fail"]:
            raise ConnectionError("dart down")
        return _Resp(_corp_zip(st["rows"]))
    monkeypatch.setattr(dc.requests, "get", get)
    st["cache"] = cache
    st["dc"] = dc
    return st


def _write_cache(path, age_days: float, rows) -> None:
    data = {"stock_to_corp": {sc: cc for cc, _n, sc in rows},
            "name_to_entries": {}}
    import bot.dart_client as dc
    for cc, nm, sc in rows:
        data["name_to_entries"].setdefault(dc._normalize_name(nm), []).append(
            {"name": nm, "stock_code": sc, "corp_code": cc})
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))


class TestCorpMapLoader:
    def test_failure_is_retried_after_window_not_remembered_forever(
            self, corp, monkeypatch):
        """재현: 첫 다운로드가 실패하면 그 클라이언트(싱글턴)는 재시작 전까지
        이름→코드를 하나도 못 풀었다. 지금은 재시도 창이 지나면 다시 묻는다."""
        dc = corp["dc"]
        cl = dc.DartClient(_KEY)
        corp["fail"] = True
        assert cl.find_by_name("삼성전자") == []
        assert corp["calls"] == 1
        # 창 안에서는 다시 묻지 않는다 — 장애 중 요청마다 두드리지 않게(#303)
        assert cl.find_by_name("삼성전자") == []
        assert corp["calls"] == 1, "재시도 창 안에서 원천을 또 두드렸다"
        corp["fail"] = False
        now = time.time()
        monkeypatch.setattr(dc.time, "time",
                            lambda: now + dc._CORPCODE_RETRY_SEC + 1)
        assert [e["stock_code"] for e in cl.find_by_name("삼성전자")] == ["005930"]
        assert corp["calls"] == 2

    def test_failure_falls_back_to_expired_cache(self, corp):
        """재현: 만료된 디스크 캐시가 있어도 다운로드가 실패하면 빈 목록이었다."""
        dc = corp["dc"]
        _write_cache(corp["cache"], dc._CORPCODE_TTL_DAYS + 5,
                     [("00164779", "SK하이닉스", "000660")])
        corp["fail"] = True
        cl = dc.DartClient(_KEY)
        assert [e["stock_code"] for e in cl.find_by_name("SK하이닉스")] == ["000660"]
        assert cl.stock_code_to_corp_code("000660") == "00164779"
        assert cl.corp_map_ready() is True

    def test_keyless_uses_expired_cache(self, corp):
        """재현: 키 없는 프로세스는 만료된 캐시가 있어도 빈 목록이었다 — 키가 없으면
        새로 받을 수 없으니 낡은 목록이 유일한 답이다(#428 키 없이도 캐시로 답한다)."""
        dc = corp["dc"]
        _write_cache(corp["cache"], dc._CORPCODE_TTL_DAYS + 5,
                     [("00164779", "SK하이닉스", "000660")])
        cl = dc.DartClient("")
        assert cl.stock_code_to_name("000660") == "SK하이닉스"
        assert corp["calls"] == 0, "키 없이 원천을 불렀다"

    def test_keyless_without_cache_is_not_ready(self, corp):
        dc = corp["dc"]
        cl = dc.DartClient("")
        assert cl.find_by_name("삼성전자") == []
        assert cl.corp_map_ready() is False
        assert corp["calls"] == 0

    def test_fresh_cache_is_used_without_network(self, corp):
        dc = corp["dc"]
        _write_cache(corp["cache"], 1, [("00126380", "삼성전자", "005930")])
        cl = dc.DartClient(_KEY)
        assert cl.stock_code_to_corp_code("005930") == "00126380"
        assert corp["calls"] == 0

    def test_success_after_failure_writes_cache_and_stops_retrying(
            self, corp, monkeypatch):
        dc = corp["dc"]
        cl = dc.DartClient(_KEY)
        corp["fail"] = True
        cl.find_by_name("삼성전자")
        corp["fail"] = False
        now = time.time()
        monkeypatch.setattr(dc.time, "time",
                            lambda: now + dc._CORPCODE_RETRY_SEC + 1)
        cl.find_by_name("삼성전자")
        assert corp["cache"].exists(), "회복한 목록을 디스크에 안 남겼다"
        calls = corp["calls"]
        monkeypatch.setattr(dc.time, "time",
                            lambda: now + 10 * dc._CORPCODE_RETRY_SEC)
        cl.find_by_name("삼성전자")
        assert corp["calls"] == calls, "받은 뒤에도 재시도를 계속한다"

    def test_reverse_name_map_follows_refresh(self, corp, monkeypatch):
        """역방향(코드→이름) 표는 처음 부를 때 한 번 만든다 — 만료 목록으로 만든
        표가 새 목록을 받은 뒤에도 남으면 새 상장사의 이름을 영영 못 찾는다."""
        dc = corp["dc"]
        _write_cache(corp["cache"], dc._CORPCODE_TTL_DAYS + 5,
                     [("00164779", "SK하이닉스", "000660")])
        corp["fail"] = True
        cl = dc.DartClient(_KEY)
        assert cl.stock_code_to_name("000660") == "SK하이닉스"
        assert cl.stock_code_to_name("005930") is None
        corp["fail"] = False
        now = time.time()
        monkeypatch.setattr(dc.time, "time",
                            lambda: now + dc._CORPCODE_RETRY_SEC + 1)
        assert cl.stock_code_to_name("005930") == "삼성전자"


class TestKeylessSentenceMatcher:
    """감사가 화면에서 키 없음 문장을 찾는 판정 — 문장 틀(`keyless_reason`)과 대상
    표(`KEYLESS_WHAT`)를 그대로 쓴다(#38). 화면이 쓰는 것과 감사가 재는 것이
    같은 표에서 나와야 감사가 눈이 멀지 않는다."""

    def test_table_covers_every_section(self):
        import bot.dart_client as dc
        assert set(dc.KEYLESS_WHAT) == set(dc.KEYLESS_SECTIONS)

    @pytest.mark.parametrize("at_collection", [True, False])
    def test_each_section_finds_its_own_sentence_only(self, at_collection):
        import html
        import bot.dart_client as dc
        for s in dc.KEYLESS_SECTIONS:
            txt = ('<div class="si-empty">'
                   + html.escape(dc.keyless_reason(dc.KEYLESS_WHAT[s],
                                                   at_collection=at_collection))
                   + "</div>")
            got = {t for t in dc.KEYLESS_SECTIONS if dc.keyless_sentence_in(txt, t)}
            assert got == {s}, (s, got)

    def test_holders_sentence_is_a_bundle(self):
        """주주 칸 문장은 다른 표와 묶인다 — 묶여도 그 칸을 찾는다."""
        import bot.dart_client as dc
        txt = dc.keyless_reason("·".join([dc.KEYLESS_WHAT["insiders"], "최대주주"])
                                + " 표를")
        assert dc.keyless_sentence_in(txt, "insiders")
        assert not dc.keyless_sentence_in(txt, "company")

    def test_not_a_sentence(self):
        import bot.dart_client as dc
        what = dc.KEYLESS_WHAT["disclosures"]
        assert dc.keyless_sentences("") == []
        # 태그가 끼면 한 문장이 아니다 · 머리 없는 대상 문구는 문장이 아니다
        assert not dc.keyless_sentence_in(
            f"DART_API_KEY 없음 — <b>{what}</b> 받지 못했습니다", "disclosures")
        assert not dc.keyless_sentence_in(f"표: {what}", "disclosures")

    def test_rendered_pages_are_found(self):
        """화면이 실제로 그리는 문장을 판정이 찾는다 — 네 칸 전부 키 없이 모은
        스냅샷을 실제 렌더러로 그린다."""
        import bot.dart_client as dc
        from bot.dashboard import _render_stock_info_html
        kr: dict = {}
        for s in dc.KEYLESS_SECTIONS:
            dc.mark_keyless(kr, s)
        si = {"long_name": "가나다", "currency": "KRW", "kr": kr}
        parts = _render_stock_info_html({"ticker": "018260.KS", "stock_info": si})
        html = parts["other_panes"]
        missing = [s for s in dc.KEYLESS_SECTIONS
                   if not dc.keyless_sentence_in(html, s)]
        assert missing == [], missing


# ── 감사: ① 운영 캐시 실측 ────────────────────────────────────────────────
def _rec(path, body, age, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    t = now - age
    os.utime(path, (t, t))


class TestCacheSection:
    """① 은 운영 캐시를 **읽기만** 한다 — 판정은 기록 자체의 불변식과 제품의
    읽는 쪽(`_tables_cached`·`_bl_cached`)이 수명 규칙대로 읽는지."""

    @pytest.fixture
    def cache(self, monkeypatch, tmp_path):
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
        return tmp_path

    def _tbl(self, cache, tail, body, age, now):
        import bot.dart_production as dp
        name = f"{dp._TABLES_PREFIX}{dp._parse_sig()}_{tail}.json"
        _rec(cache / name, body, age, now)
        return name

    def test_clean_records_pass_and_real_shorts_are_shown(self, cache):
        from bot.dart_client import PROVISIONAL_TTL_SEC
        from bot.scripts import dart_gap_audit as ga
        now = time.time()
        self._tbl(cache, "A_26.2Q11012_products",
                  {"data": {"products": {"rows": [1]}}}, 60, now)
        alive = self._tbl(
            cache, "B_26.2Q11012_products",
            {"data": {"products": {"rows": [1], "stale_note": "⚠️ 이번 조회에서 26.2Q 보고서를 받지 못해"}},
             "short": "partial", "at": now - 60}, 60, now)
        self._tbl(cache, "C_26.2Q11012_products",
                  {"data": {}, "short": "empty", "at": now - PROVISIONAL_TTL_SEC - 60},
                  PROVISIONAL_TTL_SEC + 60, now)
        _rec(cache / "dart_tables_oldsig0000_D_x.json", {"data": {}}, 99999, now)
        txt = "\n".join(ga.cache_section(now=now))
        assert "❌" not in txt, txt
        assert "표 롤링: 현 지문 기록 3 — 24시간 1 · 짧은 2(살아 있음 1 · 만료 1) · 옛 지문 1" in txt
        assert "✅ 표 롤링" in txt
        assert f"실제로 못 물어본 곳(살아 있는 짧은 기록): {alive} — ⚠️ 이번 조회에서 26.2Q" in txt

    def test_partial_baked_for_24h_is_a_finding(self, cache):
        """못 물어본 결과(표에 stale_note)가 짧은 표식 없이 24시간 기록으로 구워졌다."""
        from bot.scripts import dart_gap_audit as ga
        now = time.time()
        bad = self._tbl(cache, "E_26.2Q11012_products",
                        {"data": {"products": {"rows": [1], "stale_note": "⚠️ x"}}}, 60, now)
        lines = ga.cache_section(now=now)
        hit = [ln for ln in lines if "❌" in ln]
        assert len(hit) == 1 and "24시간 기록으로 구워졌다" in hit[0] and bad in hit[0], hit
        assert not any("✅ 표 롤링" in ln for ln in lines)

    def test_short_without_time_is_a_finding(self, cache):
        from bot.scripts import dart_gap_audit as ga
        now = time.time()
        bad = self._tbl(cache, "F_x", {"data": {}, "short": "partial"}, 60, now)
        hit = [ln for ln in ga.cache_section(now=now) if "❌" in ln]
        assert len(hit) == 1 and "형식 이상" in hit[0] and bad in hit[0], hit

    def test_reader_disagreeing_with_the_ttl_rule_is_a_finding(self, cache, monkeypatch):
        """읽는 쪽이 만료된 짧은 기록을 내주면 — 제품의 수명 규칙이 깨졌다."""
        import bot.dart_production as dp
        from bot.dart_client import PROVISIONAL_TTL_SEC
        from bot.scripts import dart_gap_audit as ga
        now = time.time()
        name = self._tbl(cache, "G_x", {"data": {}, "short": "empty",
                                        "at": now - PROVISIONAL_TTL_SEC - 120}, 60, now)
        monkeypatch.setattr(dp, "_tables_cached", lambda key: {})   # 늘 읽어 준다
        hit = [ln for ln in ga.cache_section(now=now) if "❌" in ln]
        assert len(hit) == 1 and "수명 규칙과 다르게" in hit[0] and name in hit[0], hit

    def test_edge_of_lifetime_is_not_judged(self, cache, monkeypatch):
        import bot.dart_production as dp
        from bot.scripts import dart_gap_audit as ga
        now = time.time()
        self._tbl(cache, "H_x", {"data": {}}, dp._TABLES_TTL - 1, now)
        monkeypatch.setattr(dp, "_tables_cached", lambda key: None)
        txt = "\n".join(ga.cache_section(now=now))
        assert "❌" not in txt and "경계 1건 제외" in txt, txt

    def test_broken_record_is_a_warning(self, cache):
        import bot.dart_production as dp
        from bot.scripts import dart_gap_audit as ga
        now = time.time()
        p = cache / f"{dp._TABLES_PREFIX}{dp._parse_sig()}_I_x.json"
        p.write_text("{not json", encoding="utf-8")
        txt = "\n".join(ga.cache_section(now=now))
        assert "⚠️ 표 롤링 — 못 읽는 기록 1건" in txt and p.name in txt, txt

    def test_no_records_is_unjudged_not_passed(self, cache):
        """대조 0건은 통과가 아니다(#54)."""
        from bot.scripts import dart_gap_audit as ga
        lines = ga.cache_section(now=time.time())
        assert sum("❓" in ln for ln in lines) == 2, lines
        assert not any("✅" in ln for ln in lines)

    def test_backlog_records(self, cache):
        import bot.dart_backlog as bl
        from bot.dart_client import PROVISIONAL_TTL_SEC
        from bot.scripts import dart_gap_audit as ga
        now = time.time()
        pre = f"{bl._BL_PREFIX}{bl._parse_sig()}_"
        _rec(cache / f"{pre}A.json", {"v": 1.0, "why": "ok"}, 60, now)
        _rec(cache / f"{pre}B.json", {"v": None, "why": "목록 실패", "short": True,
                                      "at": now - 60}, 60, now)
        _rec(cache / f"{pre}C.json", {"v": None, "why": "x", "short": True}, 60, now)
        lines = ga.cache_section(now=now)
        txt = "\n".join(lines)
        assert "수주잔고: 현 지문 기록 3 — 24시간 1 · 짧은 1(살아 있음 1 · 만료 0)" in txt, txt
        hit = [ln for ln in lines if "❌" in ln]
        assert len(hit) == 1 and "수주잔고" in hit[0] and f"{pre}C.json" in hit[0], hit
        assert f"{pre}B.json — 사유 목록 실패" in txt
        assert PROVISIONAL_TTL_SEC > 60


# ── 감사: ② 판정(자식 결과 → 줄) ──────────────────────────────────────────
def _child_ok(**over):
    import bot.dart_client as dc
    res = {"v": 1, "ticker": "005930.KS", "key_absent": True, "snapshot": True,
           "flags": {s: "collection" for s in dc.KEYLESS_SECTIONS},
           "page": {"company": ["si-company"], "financials": ["si-company"],
                    "insiders": ["si-holders"], "disclosures": ["si-disclosures"]},
           "live_panes": ["si-disclosures", "si-holders", "si-news"],
           "live": {"company": [], "financials": [], "insiders": ["si-holders"],
                    "disclosures": ["si-disclosures"]},
           "surfaces": {"분기실적 탭": "국내 분기 재무(DART)를",
                        "성장 카드": "근거가 될 정기보고서를",
                        "차트 공시 마커": None, "DART 피드": "새 공시를",
                        "trade 매출표(코드)": "DART 매출표를",
                        "trade 매출표(이름)": "이름으로 회사를 찾을 DART 회사 목록을"}}
    res.update(over)
    return res


class TestJudgeChild:
    def _bad(self, res, err=""):
        from bot.scripts import dart_gap_audit as ga
        lines = ga.judge_child(res, err)
        return [ln for ln in lines if "❌" in ln], lines

    def test_all_good(self):
        bad, lines = self._bad(_child_ok())
        assert bad == [], lines
        ok = [ln for ln in lines if "✅" in ln]
        assert len(ok) == 1 and "칸 기록 4/4 · 종목 페이지 사유 4/4 · 라이브 일치" in ok[0]
        assert "다른 화면 5/6" in ok[0]
        assert any("❓" in ln and "차트 공시 마커" in ln for ln in lines)

    def test_missing_flag(self):
        res = _child_ok()
        res["flags"]["insiders"] = None
        bad, _ = self._bad(res)
        assert len(bad) == 1 and "기록이 빠진 칸: insiders" in bad[0], bad

    def test_flag_without_sentence(self):
        res = _child_ok()
        res["page"]["company"] = []
        bad, _ = self._bad(res)
        assert len(bad) == 1 and "사유 문장이 없는 칸: company" in bad[0], bad

    def test_live_overlay_erases_the_reason(self):
        res = _child_ok()
        res["live"]["insiders"] = []
        bad, _ = self._bad(res)
        assert len(bad) == 1 and "insiders(si-holders)" in bad[0], bad

    def test_live_not_replacing_the_pane_is_not_erasing(self):
        """라이브가 그 pane 을 안 보내면 정적 문장이 그대로 남는다 — 지움이 아니다."""
        res = _child_ok(live_panes=["si-news"])
        res["live"] = {k: [] for k in res["live"]}
        bad, _ = self._bad(res)
        assert bad == [], bad

    def test_surface_silent_and_crashed(self):
        res = _child_ok()
        res["surfaces"]["성장 카드"] = ""
        res["surfaces"]["DART 피드"] = "!KeyError: x"
        bad, _ = self._bad(res)
        assert any("사유를 말하지 않는 화면: 성장 카드" in b for b in bad), bad
        assert any("예외로 끝나는 화면: DART 피드 KeyError: x" in b for b in bad), bad

    def test_snapshot_failure_still_judges_the_other_screens(self):
        res = {"v": 1, "ticker": "005930.KS", "key_absent": True, "snapshot": False,
               "surfaces": {"분기실적 탭": "", "DART 피드": "새 공시를"}}
        bad, lines = self._bad(res)
        assert any("❓" in ln and "수집 실패" in ln for ln in lines), lines
        assert len(bad) == 1 and "분기실적 탭" in bad[0], bad
        assert not any("기록이 빠진 칸" in ln for ln in lines), \
            "스냅샷이 없는데 칸 기록이 빠졌다고 단정했다(#165)"

    def test_key_found_is_an_isolation_failure(self):
        bad, _ = self._bad({"ticker": "005930.KS", "key_absent": False})
        assert len(bad) == 1 and "격리 실패" in bad[0]

    def test_timeout_is_unjudged_and_crash_is_a_finding(self):
        from bot.scripts import dart_gap_audit as ga
        assert ["❓" in ln for ln in ga.judge_child(None, "timeout")] == [True]
        lines = ga.judge_child(None, "exit=1 NameError: x")
        assert len(lines) == 1 and "❌" in lines[0] and "NameError" in lines[0]


class TestJudgeControl:
    def _ctrl(self, tmp_path, panes):
        p = tmp_path / "005930_KS_full_v1_k1.json"
        p.write_text(json.dumps({"ok": True, "quote": {"mode": "full", "panes": panes}},
                                ensure_ascii=False), encoding="utf-8")
        return p

    def test_clean_control(self, tmp_path):
        from bot.scripts import dart_gap_audit as ga
        lines = ga.judge_control("005930.KS", self._ctrl(tmp_path, {"si-news": "<p>x</p>"}), [])
        assert len(lines) == 1 and "✅ 대조군" in lines[0]

    def test_keyless_sentence_in_a_keyed_render(self, tmp_path):
        import bot.dart_client as dc
        from bot.scripts import dart_gap_audit as ga
        h = "<div>" + dc.keyless_reason("공시 목록을", at_collection=True) + "</div>"
        lines = ga.judge_control("005930.KS", self._ctrl(tmp_path, {"si-disclosures": h}), [])
        assert len(lines) == 1 and lines[0].lstrip().startswith("⚠️") and "공시 목록을" in lines[0]

    def test_missing_control_and_recent_keyless_renders(self, tmp_path):
        from bot.scripts import dart_gap_audit as ga
        lines = ga.judge_control("005930.KS", None,
                                 [("A_full_v1_k0r0s0f0.json", 3600.0)])
        assert any("❓ 대조군 없음" in ln for ln in lines)
        assert any("❌" in ln and "키 없이 그린 본문 1건" in ln
                   and "A_full_v1_k0r0s0f0.json" in ln for ln in lines), lines


class TestControlPickers:
    def test_pick_control_most_recent_kr_full_keyed(self, tmp_path):
        import bot.dashboard_server as ds
        from bot.scripts import dart_gap_audit as ga
        now = time.time()
        k1, k0 = "k1r0s1f0", "k0r1s0f1"      # DART 축만 본다 — 다른 축은 섞어 둔다
        for name, age in ((ds._quote_name("005930_KS", "full", k1), 100),
                          (ds._quote_name("000660_KQ", "full", "k1r1s1f1"), 10),
                          (ds._quote_name("AAPL", "full", k1), 1),          # 비-KR
                          (ds._quote_name("035720_KS", "full", k0), 1),     # 키 없음
                          (ds._quote_name("035420_KS", "light", k1), 1),    # LIGHT
                          (ds._quote_name("051910_KS", "full", "k1"), 1),   # #429 규약
                          ("068270_KS_full_v1_k1r0s0f0.json", 1)):          # 옛 렌더러
            _rec(tmp_path / name, {}, age, now)
        t, p = ga.pick_control(tmp_path)
        assert t == "000660.KQ" and p.name == ds._quote_name("000660_KQ", "full",
                                                             "k1r1s1f1")

    def test_pick_control_default(self, tmp_path):
        from bot.scripts import dart_gap_audit as ga
        assert ga.pick_control(tmp_path / "none") == (ga._DEFAULT_TICKER, None)

    def test_recent_keyless_renders(self, tmp_path):
        import bot.dashboard_server as ds
        from bot.scripts import dart_gap_audit as ga
        now = time.time()
        _rec(tmp_path / ds._quote_name("A_KS", "full", "k0r1s1f1"), {}, 3600, now)
        _rec(tmp_path / ds._quote_name("B_KS", "light", "k0r0s0f0"), {}, 60, now)
        _rec(tmp_path / ds._quote_name("C_KS", "full", "k0r0s0f0"), {}, 2 * 86400, now)
        _rec(tmp_path / ds._quote_name("D_KS", "full", "k1r0s0f0"), {}, 60, now)
        _rec(tmp_path / ds._quote_name("E_KS", "full", "kxr0s0f0"), {}, 60, now)
        got = ga.recent_keyless_renders(tmp_path, now=now)
        assert [n for n, _a in got] == [ds._quote_name("B_KS", "light", "k0r0s0f0"),
                                        ds._quote_name("A_KS", "full", "k0r1s1f1")]


# ── 감사: ② 자식 격리 ─────────────────────────────────────────────────────
class TestChildIsolation:
    def test_env_is_an_allow_list(self, tmp_path):
        from bot.scripts import dart_gap_audit as ga
        parent = {"PATH": "/bin", "LANG": "C.UTF-8", "PYTHONPATH": "/guard",
                  "DART_API_KEY": "x" * 20, "GEMINI_API_KEY": "y" * 20,
                  "TELEGRAM_BOT_TOKEN": "z" * 20, "SOME_NEW_SECRET": "w" * 20,
                  "HOME": "/real/home"}
        env = ga.child_env(parent, str(tmp_path), "/repo")
        assert env == {"PATH": "/bin", "LANG": "C.UTF-8", "HOME": str(tmp_path),
                       "PYTHONPATH": "/repo" + os.pathsep + "/guard",
                       "PYTHONDONTWRITEBYTECODE": "1", ga._CHILD_ENV: "1"}

    def test_child_mode_refuses_without_the_marker(self, monkeypatch, capsys):
        from bot.scripts import dart_gap_audit as ga
        monkeypatch.delenv(ga._CHILD_ENV, raising=False)
        assert ga.main([ga._CHILD_FLAG, "005930.KS"]) == 2

    def test_real_subprocess_never_sees_the_parents_key(self, monkeypatch):
        """실제로 자식을 띄운다 — 부모 환경에 키가 있어도 자식은 키가 없다. 테스트의
        자식 그물(루트 conftest, PYTHONPATH)이 그대로 걸려 바깥 원천은 막힌다(#401)."""
        from bot.scripts import dart_gap_audit as ga
        monkeypatch.setenv("DART_API_KEY", "k-" + "abcdefghijklmnop")
        res, err = ga.run_child("005930.KS", timeout=240)
        assert err == "", err
        assert res["key_absent"] is True
        assert res["ticker"] == "005930.KS"
        assert isinstance(res.get("surfaces"), dict) and res["surfaces"], res


# ── 감사: ② 자식 본문(실제 진입점 · 오프라인) ──────────────────────────────
class _FakeYF:
    """yfinance.Ticker 대역 — 수집이 계속 가게 최소 `.info` 만 준다(시세는 비운다)."""
    def __init__(self, t):
        self.ticker = t

    @property
    def info(self):
        return {"quoteType": "EQUITY", "longName": "가나다전자", "currency": "KRW",
                "currentPrice": 70000, "regularMarketPrice": 70000,
                "previousClose": 69000, "sharesOutstanding": 1000000,
                "marketCap": 70000000000, "exchange": "KSC"}

    def __getattr__(self, name):
        raise AttributeError(name)


class TestChildRunOffline:
    """자식 본문을 실제 진입점으로 태운다 — 키 없는 실제 클라이언트, 실제 수집기
    (`_note_dart_keyless`·`collect_kr_financials`), 실제 렌더러. 바깥 원천은 conftest
    그물이 막아 빈손이 된다(= 캐시 없는 키 부재)."""

    def test_every_section_is_marked_and_said(self, monkeypatch):
        import yfinance
        import bot.dart_client as dc
        import bot.stock_snapshot as ss
        import bot.translate as tr
        from bot.scripts import dart_gap_audit as ga
        monkeypatch.setattr(yfinance, "Ticker", _FakeYF)
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: dc.DartClient(""))
        # 자식 본문은 프로세스 전역을 바꾼다(번역 캐시 전용 · 120초 스냅샷 캐시) —
        # 자식에선 무해하지만 테스트 프로세스에선 다음 테스트로 샌다. 되돌린다.
        monkeypatch.setattr(tr, "_CACHE_ONLY", tr._CACHE_ONLY)   # 라이브 경로가 켠다
        monkeypatch.setattr(ss, "_SNAP_CACHE", {})
        res = ga.child_run("999990.KS")
        assert res["key_absent"] is True and res["snapshot"] is True, res
        assert res["flags"] == {s: "collection" for s in dc.KEYLESS_SECTIONS}, res["flags"]
        assert all(res["page"][s] for s in dc.KEYLESS_SECTIONS), res["page"]
        surf = res["surfaces"]
        for k in ("분기실적 탭", "성장 카드", "DART 피드", "trade 매출표(코드)"):
            assert surf.get(k), (k, surf)
        bad = [ln for ln in ga.judge_child(res, "") if "❌" in ln]
        assert bad == [], bad



class TestChildWiring:
    def test_child_runs_in_the_temp_home_not_the_repo(self, monkeypatch):
        """cwd 도 임시 디렉터리다 — 레포에서 돌면 `find_dotenv(usecwd=True)` 가 레포
        `.env` 의 DART 키를 찾아 '키 없는 경로' 를 못 잰다(env 허용 목록만으론 부족)."""
        import subprocess as sp
        from bot.scripts import dart_gap_audit as ga
        seen = {}

        def run(cmd, **kw):
            seen.update(kw, cmd=cmd)
            assert os.path.isdir(kw["cwd"])          # 실행 중엔 있다
            return sp.CompletedProcess(cmd, 0, stdout='{"ticker": "T"}\n', stderr="")
        monkeypatch.setattr(ga.subprocess, "run", run)
        res, err = ga.run_child("T")
        assert (res, err) == ({"ticker": "T"}, "")
        assert seen["cwd"] == seen["env"]["HOME"]
        repo = str(__import__("pathlib").Path(ga.__file__).resolve().parents[2])
        assert not seen["cwd"].startswith(repo)
        assert seen["cmd"][-2:] == [ga._CHILD_FLAG, "T"]
        assert not os.path.exists(seen["cwd"]), "임시 HOME 을 안 지웠다"

    def test_child_failure_and_garbage(self, monkeypatch):
        import subprocess as sp
        from bot.scripts import dart_gap_audit as ga
        monkeypatch.setattr(ga.subprocess, "run", lambda cmd, **kw: sp.CompletedProcess(
            cmd, 1, stdout="", stderr="Traceback ...\nNameError: boom\n"))
        res, err = ga.run_child("T")
        assert res is None and err == "exit=1 NameError: boom"

        def slow(cmd, **kw):
            raise sp.TimeoutExpired(cmd, 1)
        monkeypatch.setattr(ga.subprocess, "run", slow)
        assert ga.run_child("T") == (None, "timeout")


class TestMain:
    def test_cache_only_never_spawns(self, monkeypatch, capsys):
        from bot.scripts import dart_gap_audit as ga
        monkeypatch.setattr(ga, "cache_section", lambda: ["① x", "  ✅ ok"])

        def boom(*a, **k):
            raise AssertionError("--cache 인데 자식을 띄웠다")
        monkeypatch.setattr(ga, "run_child", boom)
        assert ga.main(["--cache"]) == 0
        out = capsys.readouterr().out
        assert "② 건너뜀(--cache)" in out and "✅ ok" in out

    def test_one_section_failing_does_not_stop_the_other(self, monkeypatch, capsys):
        from bot.scripts import dart_gap_audit as ga

        def bad():
            raise RuntimeError("disk gone")
        monkeypatch.setattr(ga, "cache_section", bad)
        monkeypatch.setattr(ga, "keyless_section", lambda ticker=None: ["② y", "  ✅ fine"])
        assert ga.main([]) == 1, "❌ 가 있으면 rc 1"
        out = capsys.readouterr().out
        assert "❌ ① 섹션 실패: RuntimeError: disk gone" in out
        assert "✅ fine" in out

    def test_clean_run_returns_zero_and_ticker_flag_reaches_section(
            self, monkeypatch, capsys):
        from bot.scripts import dart_gap_audit as ga
        got = {}
        monkeypatch.setattr(ga, "cache_section", lambda: ["① x"])
        monkeypatch.setattr(ga, "keyless_section",
                            lambda ticker=None: got.update(t=ticker) or ["② y"])
        assert ga.main(["--ticker", "000660.KS"]) == 0
        assert got == {"t": "000660.KS"}


class TestCompanyListProbeFailure:
    def test_unknown_probe_does_not_blame_the_list(self):
        """목록 상태를 못 물으면(대역에 메서드가 없음 · 예외) 탓하지 않는다(#165)."""
        from unittest import mock

        import trade.company_report as C

        class _Dart:
            api_key = ""
            find_by_name = lambda self, q: []

            def corp_map_ready(self):
                raise RuntimeError("probe broke")

        with mock.patch("bot.dart_client.get_dart", return_value=_Dart()), \
                mock.patch.object(C, "_load_alerts", return_value=[]), \
                mock.patch("trade.dart_revenue.load_inventory", return_value={}), \
                mock.patch("trade.customs.session"), \
                mock.patch("trade.industry.load_mti_stored", return_value={}), \
                mock.patch("trade.industry.load_mti_imports", return_value={}), \
                mock.patch("trade.mti_companies.load_channel_pairs", return_value=[]):
            data = C.gather("어떤회사")
        assert data["code"] is None and data["products_why"] == ""
