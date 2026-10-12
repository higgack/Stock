"""Daily 기관매수 아침 08:00 텔레그램 알림 + 대시보드 카드 표(사용자 2026-10-12).

알림은 블로그 「기관 순매수 테이블」 형식의 PNG 를 매일 아침 한 번 채널로 보낸다. 여기선 (a) 언제
무엇을 보내는지(정규장이 끝난 마지막 거래일 · 한 번만 · 재확인 기다림 · 창 밖 안 보냄), (b) 실패하면
보냄으로 안 적고 다시 묻는지, (c) 그림을 못 그리면 글자로 보내는지, (d) 봇 주기에 배선됐는지를 잰다.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot import daily_inst as di
from bot import daily_inst_alert as al

K = timezone(timedelta(hours=9))


def _at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=K)


def _rows(n=10, name="삼아알미늄"):
    return [{"t": f"{i:06d}.KS", "n": f"{name}{i}", "r": 1.0 - i * 0.05, "net": 1e9, "mcap": 1e12}
            for i in range(n)]


def _rec(date, recheck=True, nodata=False):
    r = {"date": date, "tiers": [
        {"key": "big", "label": "시가총액 1조 이상", "n": 300, "n_pos": 100,
         "lists": {"strength": _rows(), "turn": _rows(4), "streak": None}},
        {"key": "mid", "label": "시가총액 5천억 ~ 1조", "n": 180, "n_pos": 80,
         "lists": {"strength": _rows(), "turn": [], "streak": _rows()}}]}
    if recheck:
        r["recheck"] = {"at": f"{date}T07:10:00+09:00", "changed": 0}
    if nodata:
        r = {"date": date, "nodata": "KRX 가 자료를 주지 않음"}
    return r


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(di, "_DIR", tmp_path / "daily_inst")
    return tmp_path


# ── 언제 무엇을 ────────────────────────────────────────────────────────────────
# 사용자 2026-10-12 "월~금 08:00 — 한국시간" = KR 개장일 아침(장 시작 전)에 직전 거래일 표.
# 옛 계약(토 아침 = 금 기록, 월 아침엔 이미 보낸 금 기록이라 안 보냄)을 다시 썼다(#222) —
# 남는 보장(정규장이 끝난 마지막 거래일 · 한 번만 · 08~12시 창)은 그대로 잰다.
@pytest.mark.parametrize("now, recs, state, want", [
    (_at(2026, 10, 13, 8, 5), ["2026-10-12"], {}, "2026-10-12"),        # 화 아침 = 월 기록
    (_at(2026, 10, 13, 7, 55), ["2026-10-12"], {}, None),               # 08:00 전
    (_at(2026, 10, 13, 12, 0), ["2026-10-12"], {}, None),               # 12:00 지남 — 그날은 안 보냄
    (_at(2026, 10, 10, 8, 0), ["2026-10-08"], {}, None),                # 토 — 개장일이 아니다
    (_at(2026, 10, 11, 9, 30), ["2026-10-08"], {}, None),               # 일 — 개장일이 아니다
    (_at(2026, 10, 9, 8, 0), ["2026-10-08"], {}, None),                 # 금 10-09 한글날(평일 휴장)
    (_at(2026, 10, 12, 8, 0), ["2026-10-08"], {}, "2026-10-08"),        # 월 아침 = 직전 거래일(목)
    (_at(2026, 10, 12, 8, 0), ["2026-10-08"], {"sent": {"2026-10-08": {}}}, None),  # 이미 보냄
    (_at(2026, 10, 13, 8, 5), ["2026-10-08"], {}, None),                # 월 기록이 아직 없다
])
def test_alert_targets_the_last_closed_session_once(now, recs, state, want):
    rec, why = al.alert_target([_rec(d) for d in recs], state, now)
    assert (rec or {}).get("date") == want, why
    if want is None:
        assert why, "안 보내면 사유를 말한다"


def test_closed_morning_says_it_is_a_market_holiday_not_a_missing_record():
    """휴장 아침의 사유는 '기록 없음' 이 아니라 '개장일 아침에만' 이어야 한다(#82) — 금요일 기록이
    있어도 토요일엔 안 보내고, 그 기록은 다음 개장일 아침에 간다."""
    rec, why = al.alert_target([_rec("2026-10-16")], {}, _at(2026, 10, 17, 8, 5))
    assert rec is None and "휴장" in why and "10-17 토" in why, why
    rec, _ = al.alert_target([_rec("2026-10-16")], {}, _at(2026, 10, 19, 8, 5))
    assert rec["date"] == "2026-10-16", "다음 개장일(월) 아침에 금요일 기록이 간다"


def test_open_day_question_goes_to_the_calendar_and_unknown_means_no_send(monkeypatch):
    """개장일 판정은 거래소 달력에 묻는다 — 달력이 '모름' 이면 보내지 않고 그렇게 말한다(#54).
    요일로 추정하지 않는다: 달력이 평일을 휴장이라 하면 안 보내고, 주말을 개장이라 하면 보낸다."""
    from bot import market_calendar as mc
    asked = []
    monkeypatch.setattr(mc, "is_trading_day", lambda m, d: asked.append((m, d)) or None)
    rec, why = al.alert_target([_rec("2026-10-12")], {}, _at(2026, 10, 13, 8, 5))
    assert rec is None and "달력" in why and asked == [("KR", "2026-10-13")]
    monkeypatch.setattr(mc, "is_trading_day", lambda m, d: False)
    assert al.alert_target([_rec("2026-10-12")], {}, _at(2026, 10, 13, 8, 5))[0] is None
    monkeypatch.setattr(mc, "is_trading_day", lambda m, d: True)
    import bot.kr_session as ks
    monkeypatch.setattr(ks, "last_closed_session", lambda now: "2026-10-16")
    rec, _ = al.alert_target([_rec("2026-10-16")], {}, _at(2026, 10, 17, 8, 5))
    assert rec and rec["date"] == "2026-10-16", "판정은 달력이 한다 — 요일 규칙을 따로 두지 않는다"


def test_alert_waits_for_the_morning_recheck_until_nine():
    recs = [_rec("2026-10-12", recheck=False)]
    rec, why = al.alert_target(recs, {}, _at(2026, 10, 13, 8, 30))
    assert rec is None and "재확인" in why
    rec, _ = al.alert_target(recs, {}, _at(2026, 10, 13, 9, 0))
    assert rec["date"] == "2026-10-12"
    assert "재확인 전" in al.caption(rec), "저녁 값으로 보냈으면 캡션이 그렇게 말한다"
    assert "재확인 전" not in al.caption(_rec("2026-10-12"))


def test_alert_skips_a_no_data_card_and_stops_after_max_tries():
    # 09:30 — 재확인 대기 갈래가 아니라 '자료 없음' 갈래가 막는지 잰다(08:05 면 대기 갈래가 대신 막는다)
    rec, why = al.alert_target([_rec("2026-10-12", nodata=True)], {}, _at(2026, 10, 13, 9, 30))
    assert rec is None and "자료 없음" in why
    st = {"tries": {"2026-10-12": al.MAX_TRIES}}
    rec, why = al.alert_target([_rec("2026-10-12")], st, _at(2026, 10, 13, 8, 5))
    assert rec is None and "실패" in why


@pytest.fixture
def chans(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t" * 12)
    monkeypatch.setenv("CHANNEL_CHAT_IDS", "A,B")
    return ["A", "B"]


def test_maybe_alert_marks_sent_only_on_success_and_never_twice(home, chans, monkeypatch):
    monkeypatch.setattr(di, "load_records", lambda: [_rec("2026-10-12")])
    calls = []
    results = iter([{"A": None, "B": None}, {"A": "사진", "B": "사진"}])
    monkeypatch.setattr(al, "_send", lambda rec, cs: calls.append(list(cs)) or next(results))
    t = _at(2026, 10, 13, 8, 0)
    r1 = al.maybe_alert(t)
    assert r1["sent"] is False and "실패" in r1["reason"]
    assert "2026-10-12" not in (al.load_state().get("sent") or {})
    r2 = al.maybe_alert(t + timedelta(minutes=15))
    st = al.load_state()
    assert r2["sent"] is True and st["sent"]["2026-10-12"]["how"] == "사진"
    assert st["sent"]["2026-10-12"]["settled"] is True and st["last_reason"] == ""
    al.maybe_alert(t + timedelta(minutes=30))
    assert calls == [["A", "B"], ["A", "B"]], "보낸 날짜를 또 보냈다"


def test_one_failing_channel_does_not_resend_to_the_ones_that_got_it(home, chans, monkeypatch):
    """채널마다 따로 적는다 — 한 채널이 실패해도 이미 받은 채널엔 다시 안 보낸다(독립 리뷰 H1:
    옛 판은 A 에 사진 6장 + 글자 6개)."""
    import bot.daily_kr_flow as fl
    monkeypatch.setattr(di, "load_records", lambda: [_rec("2026-10-12")])
    monkeypatch.setattr(al, "render_png", lambda rec, out: Path(out).write_bytes(b"png") and out)
    got = []
    ok_b = [False, False, True]

    def photo(png, cap, filename="", *, chats=None):
        (c,) = chats
        got.append(("photo", c))
        return c == "A" or ok_b.pop(0)
    monkeypatch.setattr(fl, "push_telegram_photo", photo)
    monkeypatch.setattr(fl, "push_telegram", lambda text, *, chats=None: got.append(("text", chats[0])) or False)
    t = _at(2026, 10, 13, 8, 0)
    r = al.maybe_alert(t)
    assert r["sent"] is False and "채널 1/2곳 보내기 실패" in r["reason"]
    al.maybe_alert(t + timedelta(minutes=15))
    r = al.maybe_alert(t + timedelta(minutes=30))
    assert r["sent"] is True
    assert [g for g in got if g[1] == "A"] == [("photo", "A")], "받은 채널에 또 보냈다"
    assert got.count(("photo", "B")) == 3
    assert al.load_state()["sent"]["2026-10-12"]["chats"] == 2


def test_missing_channel_settings_are_named_not_counted_as_send_failures(home, monkeypatch):
    monkeypatch.setattr(di, "load_records", lambda: [_rec("2026-10-12")])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("STANDARDVIEW_TELEGRAM_TOKEN", "")
    monkeypatch.setattr(al, "_send", lambda rec, cs: pytest.fail("설정 없이 보냈다"))
    r = al.maybe_alert(_at(2026, 10, 13, 8, 0))
    assert r["sent"] is False and "TELEGRAM_BOT_TOKEN" in r["reason"]
    assert "2026-10-12" not in (al.load_state().get("tries") or {}), "설정 누락은 시도가 아니다"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t" * 12)
    monkeypatch.setenv("CHANNEL_CHAT_IDS", "")
    assert "CHANNEL_CHAT_IDS" in al.maybe_alert(_at(2026, 10, 13, 8, 15))["reason"]


def test_maybe_alert_counts_a_send_exception_as_a_try(home, chans, monkeypatch):
    """보내기가 예외를 내도 시도 횟수를 적는다 — 안 적으면 상한(MAX_TRIES)이 영영 안 걸린다."""
    monkeypatch.setattr(di, "load_records", lambda: [_rec("2026-10-12")])

    def boom(rec, cs):
        raise OSError("disk")
    monkeypatch.setattr(al, "_send", boom)
    r = al.maybe_alert(_at(2026, 10, 13, 8, 0))
    assert r["sent"] is False
    st = al.load_state()
    assert st["tries"]["2026-10-12"] == 1 and "예외(OSError)" in st["last_reason"]


def test_no_send_path_still_records_when_it_looked(home, monkeypatch):
    """`--why` 가 읽는 '마지막 확인 · 사유' 는 안 보내는 주기에도 적는다."""
    monkeypatch.setattr(di, "load_records", lambda: [])
    r = al.maybe_alert(_at(2026, 10, 13, 8, 0))
    st = al.load_state()
    assert st["last_check"].startswith("2026-10-13T08:00") and st["last_reason"] == r["reason"]
    assert "기록이 아직 없음" in st["last_reason"]


def test_old_send_records_are_pruned(home, chans, monkeypatch):
    monkeypatch.setattr(di, "load_records", lambda: [_rec("2026-10-12")])
    monkeypatch.setattr(al, "_send", lambda rec, cs: {c: "사진" for c in cs})
    old = {f"2026-0{1 + i // 28}-{i % 28 + 1:02d}": {"at": "x", "how": "사진"} for i in range(al.KEEP_SENT + 5)}
    al._save_state({"sent": dict(old), "tries": {k: 1 for k in old}, "sent_chats": {k: {} for k in old}})
    al.maybe_alert(_at(2026, 10, 13, 8, 0))
    st = al.load_state()
    assert len(st["sent"]) == al.KEEP_SENT and "2026-10-12" in st["sent"]
    assert len(st["tries"]) == al.KEEP_SENT and len(st["sent_chats"]) == al.KEEP_SENT


def test_a_record_first_collected_after_the_recheck_time_is_settled():
    """이튿날 07:00 뒤에 처음 받은 기록(백필 등)은 재확인을 안 하지만 이미 그 시각의 값이다 —
    09:00 까지 기다리게 하거나 '저녁 값' 이라 적으면 거짓이다(독립 리뷰 M1)."""
    rec = _rec("2026-10-12", recheck=False)
    rec["collected_at"] = "2026-10-13T07:20:00+09:00"
    got, why = al.alert_target([rec], {}, _at(2026, 10, 13, 8, 5))
    assert got is rec, why
    assert al.caption(rec) == "기관 순매수 테이블 (2026.10.12 기준)"
    rec["collected_at"] = "2026-10-12T20:40:00+09:00"
    assert al.alert_target([rec], {}, _at(2026, 10, 13, 8, 5))[0] is None
    assert al.caption(rec) == "기관 순매수 테이블 (2026.10.12 기준)\n(이튿날 재확인 전 저녁 값)"


def test_now_is_read_in_kst():
    import datetime as _dt
    assert al._now(None).utcoffset() == _dt.timedelta(hours=9)
    naive = _dt.datetime(2026, 10, 13, 8, 5)
    assert al._now(naive) == _at(2026, 10, 13, 8, 5), "시간대 없는 시각은 KST 로 읽는다"
    utc = _dt.datetime(2026, 10, 12, 23, 5, tzinfo=_dt.timezone.utc)
    assert al.alert_target([_rec("2026-10-12")], {}, utc)[0]["date"] == "2026-10-12", "UTC 23:05 = KST 08:05"


def test_no_calendar_means_no_target(monkeypatch):
    import bot.kr_session as ks
    monkeypatch.setattr(ks, "last_closed_session", lambda now: None)
    rec, why = al.alert_target([_rec("2026-10-12")], {}, _at(2026, 10, 13, 8, 5))
    assert rec is None and "달력" in why


def test_render_draws_the_requested_layout(tmp_path, monkeypatch):
    """그림이 '있다' 가 아니라 요청 형식이 그려졌는지(독립 리뷰 M3) — 그려진 글자 · 흰 바탕 · 크기 ·
    한글 폰트가 걸렸는지."""
    from bot.korean_font import find_font
    if not find_font():
        pytest.skip("한글 폰트 없음")
    import matplotlib
    import matplotlib.axes
    from matplotlib import font_manager as fm
    texts = []
    real = matplotlib.axes.Axes.text

    def spy(self, x, y, s, *a, **k):
        texts.append(str(s))
        return real(self, x, y, s, *a, **k)
    monkeypatch.setattr(matplotlib.axes.Axes, "text", spy)
    rec = _rec("2026-10-12")
    rec["tiers"][0]["lists"]["strength"][0]["n"] = "아주아주긴이름의종목명이들어가면잘리나"
    out = al.render_png(rec, str(tmp_path / "a.png"))
    assert out
    assert "2026.10.12 기준 · KRX 기관합계 순매수 ÷ 시가총액" in texts
    assert "시총대비 기관 순매수액: 1조 이상" in texts and "시총대비 기관 순매수액: 5천억 ~ 1조" in texts
    assert "해당 종목 없음" in texts and "자료 부족 — 판정 안 함" in texts
    assert "아주아주긴이름의종목명이들어…" in texts, "긴 이름은 15자 안으로 잘라 …"
    assert matplotlib.rcParams["font.family"][0] == fm.FontProperties(fname=find_font()).get_name()
    from PIL import Image
    im = Image.open(out)
    assert im.size[0] == 1389, "dpi 150 · 폭 926pt"
    assert im.convert("RGB").getpixel((3, 3)) == (255, 255, 255), "흰 바탕"


# ── 보내기 ───────────────────────────────────────────────────────────────────
def test_send_falls_back_to_text_when_the_picture_cannot_be_drawn(home, monkeypatch):
    import bot.daily_kr_flow as fl
    sent = {}
    monkeypatch.setattr(al, "render_png", lambda rec, out: None)
    monkeypatch.setattr(fl, "push_telegram_photo", lambda *a, **k: sent.setdefault("photo", True))
    monkeypatch.setattr(fl, "push_telegram", lambda text, *, chats=None: sent.setdefault("text", (text, chats)) and True)
    assert al._send(_rec("2026-10-12"), ["A"]) == {"A": "글자"} and "photo" not in sent
    text, chats = sent["text"]
    assert chats == ["A"]
    assert "삼아알미늄0 1.00%" in text and "시총대비 기관 순매수액: 1조 이상" in text


def test_send_uses_the_photo_and_stops_when_it_goes_through(home, monkeypatch):
    import bot.daily_kr_flow as fl
    seen = []
    monkeypatch.setattr(al, "render_png", lambda rec, out: Path(out).write_bytes(b"png") and out)
    monkeypatch.setattr(fl, "push_telegram_photo", lambda *a, **k: seen.append(("photo", a[1], k)) or True)
    monkeypatch.setattr(fl, "push_telegram", lambda *a, **k: seen.append(("text",)) or True)
    assert al._send(_rec("2026-10-12"), ["A"]) == {"A": "사진"}
    assert [x[0] for x in seen] == ["photo"], "사진이 갔는데 글자도 보냈다"
    assert seen[0][1] == "기관 순매수 테이블 (2026.10.12 기준)", "사용자가 준 캡션 형식 그대로"
    assert seen[0][2] == {"filename": "daily_inst.png", "chats": ["A"]}


def test_send_retries_as_text_when_the_photo_push_fails(home, monkeypatch):
    import bot.daily_kr_flow as fl
    seen = []
    monkeypatch.setattr(al, "render_png", lambda rec, out: Path(out).write_bytes(b"png") and out)
    monkeypatch.setattr(fl, "push_telegram_photo", lambda *a, **k: seen.append(("photo", k)) or False)
    monkeypatch.setattr(fl, "push_telegram", lambda text, *, chats=None: seen.append(("text", chats)) or True)
    assert al._send(_rec("2026-10-12"), ["A"]) == {"A": "글자"}
    assert [s[0] for s in seen] == ["photo", "text"] and seen[1][1] == ["A"]
    assert seen[0][1]["filename"] == "daily_inst.png"
    assert not list((home / "daily_inst").glob("alert_*.png")), "보낸 뒤 그림 파일을 지운다"


def test_text_body_escapes_names_and_says_why_a_list_is_empty():
    rec = _rec("2026-10-12")
    rec["tiers"][0]["lists"]["strength"][0]["n"] = "<b>가짜</b>"
    body = al.text_body(rec)
    assert "&lt;b&gt;가짜&lt;/b&gt;" in body and "<b>가짜</b>" not in body
    assert "(해당 종목 없음)" in body and "(판정 안 함 — 자료 부족)" in body


def test_photo_push_carries_the_given_file_name(tmp_path, monkeypatch):
    import httpx

    import bot.daily_kr_flow as fl
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t" * 10)
    monkeypatch.setenv("CHANNEL_CHAT_IDS", "1")
    got = {}

    class R:
        status_code, text = 200, ""

    def post(url, data=None, files=None, timeout=None):
        got["name"] = files["photo"][0]
        got["caption"] = data["caption"]
        return R()
    monkeypatch.setattr(httpx, "post", post)
    assert fl.push_telegram_photo(str(png), "cap", filename="daily_inst.png")
    assert got == {"name": "daily_inst.png", "caption": "cap"}


def test_render_png_draws_a_picture_when_a_korean_font_exists(tmp_path):
    from bot.korean_font import find_font
    if not find_font():
        pytest.skip("한글 폰트 없음")
    out = al.render_png(_rec("2026-10-12"), str(tmp_path / "a.png"))
    assert out and Path(out).read_bytes()[:4] == b"\x89PNG" and Path(out).stat().st_size > 20000


def test_alert_is_wired_into_the_daily_inst_bot_loop():
    """배선(#20) — 봇 모듈은 토큰 없이 import 가 안 돼 소스를 AST 로 본다(못 보는 축: 그 호출이
    실제로 도는지는 위 `maybe_alert` 테스트가 잰다)."""
    tree = ast.parse(Path("bot/telegram_bot.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_periodic_daily_inst")
    alias = next(n for n in ast.walk(fn) if isinstance(n, ast.alias) and n.name == "maybe_alert")
    local = alias.asname or alias.name
    # 가져오기만 하고 안 부르는 변형도 잡게 — `to_thread(<그 이름>)` 호출이 있는지(#20).
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
             and isinstance(c.func, ast.Attribute) and c.func.attr == "to_thread"
             and any(isinstance(a, ast.Name) and a.id == local for a in c.args)]
    assert calls, "알림을 가져오기만 하고 부르지 않는다"


def test_why_reads_only(home, chans, monkeypatch, capsys):
    monkeypatch.setattr(di, "load_records", lambda: [_rec("2026-10-12")])
    monkeypatch.setattr(al, "_send", lambda rec, cs: pytest.fail("진단이 보냈다"))
    assert al.why(_at(2026, 10, 13, 8, 5)) == 0
    out = capsys.readouterr().out
    assert "보낼 기록 있음 — 2026-10-12" in out and "⑤ 보낼 채널: 2곳" in out
    assert not (home / "daily_inst" / "alert.json").exists(), "진단이 상태를 썼다"


# ── 대시보드 카드 표(우리 디자인으로) ─────────────────────────────────────────────
def test_dashboard_table_is_a_card_with_rank_bar_and_subtitle():
    h = di._table_html({}, "strength", "순매수 강도 TOP 10", _rows(3))
    assert h.startswith("<div class='di-card'>") and "<table class='di-tbl'>" in h
    assert "기준일 순매수 ÷ 시총" in h
    assert [int(x) for x in __import__("re").findall(r"<td class='di-rk'>(\d+)</td>", h)] == [1, 2, 3]
    widths = [float(x) for x in __import__("re").findall(r"width:calc\(\(100% - 12px\) \* ([\d.]+)\)", h)]
    assert widths == [1.0, 0.95, 0.9], "1위 대비 막대 — 칸 폭에 비례(고정 12px 를 빼면 비례가 깨진다)"
    empty = di._table_html({}, "turn", "순매수 전환 TOP 10", [])
    assert "colspan='3'>해당 종목 없음" in empty


def test_daily_inst_palette_matches_the_board_palette():
    """`_DI_CSS` 는 대비 가드가 재게 하려고 --bg·--card 를 다시 적는다 — 공용 `_BOARD_CSS` 와 갈라지면
    두 팔레트가 조용히 어긋난다(독립 리뷰 L6)."""
    import re
    from bot.fred_boards import _BOARD_CSS

    def pal(css, sel):
        m = re.search(re.escape(sel) + r"\{([^}]*)\}", css)
        return dict(re.findall(r"--([\w-]+):\s*([^;}\s]+)", m.group(1)))
    for sel in (":root", ':root[data-theme="dark"]'):
        a, b = pal(di._DI_CSS, sel), pal(_BOARD_CSS, sel)
        assert {k: a[k] for k in ("bg", "card")} == {k: b[k] for k in ("bg", "card")}, sel


def test_value_text_stays_readable_over_its_bar_and_row_hover():
    """값 글자(--di-up)는 같은 색 막대(반투명)와 행 hover(--surface2) **위에** 놓인다 — 대비 가드는
    글자를 카드 배경에만 대조해 이 겹침을 못 본다(독립 리뷰 L6: 옛 #c62828·13% 는 hover 에서 4.08:1).
    WCAG AA 4.5 를 막대 혼합 배경 위에서 잰다."""
    import re
    from bot.fred_boards import _BOARD_CSS

    def pal(css, sel):
        m = re.search(re.escape(sel) + r"\{([^}]*)\}", css)
        return dict(re.findall(r"--([\w-]+):\s*([^;}\s]+)", m.group(1)))

    def rgb(h):
        h = h.lstrip("#")
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]

    def lum(c):
        v = [(x / 255) / 12.92 if x / 255 <= 0.03928 else ((x / 255 + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    op = float(re.search(r"\.di-bar\{[^}]*opacity:([\d.]+)", di._DI_CSS).group(1))
    for sel in (":root", ':root[data-theme="dark"]'):
        up = rgb(pal(di._DI_CSS, sel)["di-up"])
        board = pal(_BOARD_CSS, sel)
        for base in ("card", "surface2"):
            bg = [round(op * u + (1 - op) * b) for u, b in zip(up, rgb(board[base]))]
            assert ratio(up, bg) >= 4.5, (sel, base, round(ratio(up, bg), 2))


# ── 델타 리뷰(2026-10-12) ─────────────────────────────────────────────────────
def test_real_push_functions_send_only_to_the_given_chats(tmp_path, monkeypatch):
    """채널별 발송은 실제 push 함수가 `chats=` 를 지켜야 성립한다 — 무시하면 채널마다 전 채널로 가
    고치려던 중복이 그대로다(델타 리뷰 H1). 알림 테스트는 전부 push 를 스텁해 이걸 못 본다."""
    import httpx

    import bot.daily_kr_flow as fl
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t" * 12)
    monkeypatch.setenv("CHANNEL_CHAT_IDS", "A,B")
    posted = []

    class R:
        status_code, text = 200, ""

    def post(url, data=None, json=None, files=None, timeout=None):
        posted.append((url.rsplit("/", 1)[-1], (data or json)["chat_id"]))
        return R()
    monkeypatch.setattr(httpx, "post", post)
    assert fl.push_telegram_photo(str(png), "c", filename="daily_inst.png", chats=["B"])
    assert fl.push_telegram("t", chats=["B"])
    assert posted == [("sendPhoto", "B"), ("sendMessage", "B")]
    posted.clear()
    assert fl.push_telegram("t")
    assert [c for _, c in posted] == ["A", "B"], "chats 가 없으면 종전대로 전 채널"


def test_channel_targets_falls_back_to_the_standardview_token(monkeypatch):
    import bot.daily_kr_flow as fl
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("STANDARDVIEW_TELEGRAM_TOKEN", "s" * 12)
    monkeypatch.setenv("CHANNEL_CHAT_IDS", " A , ,B ")
    assert fl.channel_targets() == ("s" * 12, ["A", "B"])


def test_one_chat_raising_keeps_the_others_success(home, monkeypatch):
    import bot.daily_kr_flow as fl
    monkeypatch.setattr(al, "render_png", lambda rec, out: Path(out).write_bytes(b"png") and out)

    def photo(png, cap, filename="", *, chats=None):
        if chats == ["B"]:
            raise OSError("socket")
        return True
    monkeypatch.setattr(fl, "push_telegram_photo", photo)
    assert al._send(_rec("2026-10-12"), ["A", "B"]) == {"A": "사진", "B": None}


def test_text_body_carries_the_source_line():
    body = al.text_body(_rec("2026-10-12"))
    assert body.splitlines()[1] == al.SOURCE_LINE == "KRX 기관합계 순매수 ÷ 시가총액"


def test_a_removed_chat_does_not_cost_a_try_or_a_render(home, monkeypatch):
    monkeypatch.setattr(di, "load_records", lambda: [_rec("2026-10-12")])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t" * 12)
    monkeypatch.setenv("CHANNEL_CHAT_IDS", "A")
    al._save_state({"sent_chats": {"2026-10-12": {"A": {"at": "x", "how": "사진"}}},
                    "tries": {"2026-10-12": 2}})
    monkeypatch.setattr(al, "_send", lambda rec, cs: pytest.fail("받을 채널이 없는데 보냈다"))
    r = al.maybe_alert(_at(2026, 10, 13, 8, 30))
    st = al.load_state()
    assert r["sent"] is True and st["tries"]["2026-10-12"] == 2 and "2026-10-12" in st["sent"]


def test_settled_flag_records_an_evening_send(home, chans, monkeypatch):
    rec = _rec("2026-10-12", recheck=False)
    rec["collected_at"] = "2026-10-12T20:40:00+09:00"
    monkeypatch.setattr(di, "load_records", lambda: [rec])
    monkeypatch.setattr(al, "_send", lambda r, cs: {c: "사진" for c in cs})
    al.maybe_alert(_at(2026, 10, 13, 9, 0))
    assert al.load_state()["sent"]["2026-10-12"]["settled"] is False


def test_settled_and_needs_recheck_share_the_boundary():
    rec = {"date": "2026-10-12", "collected_at": "2026-10-13T07:00:00+09:00"}
    assert di.is_settled(rec) is True
    assert di.needs_recheck(rec, _at(2026, 10, 13, 8, 0)) is False
    rec["collected_at"] = "2026-10-13T06:59:59+09:00"
    assert di.is_settled(rec) is False and di.needs_recheck(rec, _at(2026, 10, 13, 8, 0)) is True


def test_why_tells_partial_sends_truthfully(home, chans, monkeypatch, capsys):
    """'다음 주기에 다시' 는 정말 다시 물을 때만(델타 리뷰 M1) · ① 은 어느 채널이든 받은 마지막 날."""
    monkeypatch.setattr(di, "load_records", lambda: [_rec("2026-10-12")])
    al._save_state({"sent_chats": {"2026-10-12": {"A": {"at": "x", "how": "사진"}}},
                    "tries": {"2026-10-12": 1}})
    al.why(_at(2026, 10, 13, 9, 0))
    out = capsys.readouterr().out
    assert "① 마지막으로 보낸 기준일: 2026-10-12 (일부 채널만)" in out
    assert "2026-10-12: 채널 1/2곳 받음 — 나머지는 다음 주기에 다시" in out
    al._save_state({"sent_chats": {"2026-10-12": {"A": {"at": "x", "how": "사진"}}},
                    "tries": {"2026-10-12": al.MAX_TRIES}})
    al.why(_at(2026, 10, 13, 9, 0))
    out = capsys.readouterr().out
    assert "나머지는 보내지 못하고 그날은 끝남" in out and "다음 주기에 다시" not in out
    al._save_state({"sent": {"2026-10-12": {"at": "t", "how": "사진"}},
                    "sent_chats": {"2026-10-12": {"A": {}, "B": {}}}})
    al.why(_at(2026, 10, 13, 9, 0))
    out = capsys.readouterr().out
    assert "2026-10-12 (t · 사진)" in out and "곳 받음" not in out, "다 받은 날은 일부 줄을 안 찍는다"


def test_cli_fills_only_the_channel_keys_from_dotenv(monkeypatch):
    """진단 프로세스는 `.env` 를 안 읽는다(#23) — 봇이 쓰는 세 키만 채운다. 안 하면 `--why` 가 키가
    있는데도 '채널 없음' 이라 말한다."""
    import os

    import bot.env_keys as ek
    vals = {"TELEGRAM_BOT_TOKEN": "t" * 12, "CHANNEL_CHAT_IDS": "A", "STANDARDVIEW_TELEGRAM_TOKEN": "",
            "OTHER_API_KEY": "nope"}
    asked = []
    monkeypatch.setattr(ek, "env_key", lambda k: asked.append(k) or vals.get(k, ""))
    for k in vals:
        # 없던 키를 그냥 delenv 하면 되돌릴 기록이 안 남아, 제품이 채운 값이 뒤 테스트로 샌다 —
        # 먼저 setenv 로 '원래 없음' 을 기록시킨다.
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    seen = []
    monkeypatch.setattr(al, "why", lambda now=None: seen.append(os.environ.get("CHANNEL_CHAT_IDS")) or 0)
    assert al.main([]) == 0
    assert seen == ["A"] and os.environ.get("TELEGRAM_BOT_TOKEN") == "t" * 12
    assert sorted(asked) == ["CHANNEL_CHAT_IDS", "STANDARDVIEW_TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN"]
    assert "OTHER_API_KEY" not in os.environ
