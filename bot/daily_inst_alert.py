"""Daily 기관매수 텔레그램 알림 — KR 개장일(월~금) 아침 08:00 KST 부터(사용자 2026-10-12).

대시보드(`daily_inst.html`) 카드 모양 그대로(사용자 2026-10-13 "대시보드처럼 이쁘게" — 옛 판은
블로그 「기관 순매수 테이블」 흑백 격자) 시총 구간 셋 × 목록 셋(순매수 강도 · 순매수 전환 · 5일 연속
순매수 TOP 10)을 한 장의 PNG 로 그려 채널에 사진으로 보낸다. 표의 숫자·이름은 대시보드(`daily_inst.html`)와 **같은 기록**에서 온다(#38) — 이 모듈은
그리기와 보내기만 한다.

언제 무엇을 보내나
- **KR 개장일** 아침 08:00 KST 부터(사용자 2026-10-12 "월~금 08:00" — 보통 장 시작 전에 가지만,
  재확인 대기면 09:00 까지 · 봇이 늦게 뜨거나 전송을 다시 시도하면 12:00 까지 늦게 갈 수 있다),
  정규장이 끝난 마지막 KR 거래일(`kr_session.last_closed_session` = 그날 아침엔 직전 거래일) 기록을
  한 번 보낸다. 월요일 아침 = 금요일 기록, 화요일 아침 = 월요일 기록. 주말·평일 휴장일 아침엔
  보내지 않고 그 기록은 다음 개장일 아침에 간다(같은 날짜를 두 번 보내지 않는다, #9 영구 seen-set).
  개장일인지는 거래소 달력(`market_calendar.is_trading_day`)이 정하고, 달력이 없으면 단정하지
  않고 보내지 않는다(#54).
- 기록은 그날 20:30 이후 받고 이튿날 07:00 이후 한 번 더 받아 대조한다(`RECHECK_HOUR`). 재확인이
  아직이면 09:00 까지 기다리고, 그 뒤엔 저녁 값으로 보내며 캡션에 그렇게 적는다(#43).
- 12:00 이 지나도록 못 보냈으면 그날은 보내지 않는다 — 밤에 봇이 다시 떠서 아침 알림을 뒤늦게
  쏘지 않게. 그 사실은 상태 파일·`--why` 가 말한다.
- 보내기는 **채널마다** 따로 재고 적는다 — 한 채널이 실패해도 이미 받은 채널엔 다시 안 보낸다
  (`sent_chats`). 실패한 채널만 다음 주기(15분)에 다시 묻는다(하루 `MAX_TRIES` 회).
- 그림을 못 그리면(한글 폰트 없음 등) 같은 표를 **글자로** 보낸다 — 침묵보다 낫다(#43).
- ⚠️ 못 보는 축(#274): 사진이 전달됐는데 응답이 시간 초과로 끊기면 실패로 읽혀 글자판이 한 번 더
  간다 — 텔레그램 응답만으로는 '안 갔다' 와 '갔는데 답을 못 받았다' 를 가를 수 없다.

진단 — `cd ~/stock && .venv/bin/python -m bot.daily_inst_alert --why`(읽기 전용) ·
`--preview`(그림만 만들어 경로를 찍는다, 보내지 않는다).
"""
from __future__ import annotations

import html as _h
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.daily_inst_alert")

_KST = timezone(timedelta(hours=9))
ALERT_HOUR = 8            # 이 시각부터 보낸다(사용자 지정)
RECHECK_WAIT_HOUR = 9     # 재확인이 아직이면 이 시각까지 기다린다
ALERT_END_HOUR = 12       # 이 시각이 지나면 그날은 보내지 않는다
MAX_TRIES = 6             # 하루 보내기 시도 상한(15분 간격)
_WEEKDAY = "월화수목금토일"
KEEP_SENT = 60


def _dir() -> Path:
    from bot import daily_inst as di
    return di._DIR


def _state_path() -> Path:
    return _dir() / "alert.json"


def load_state() -> dict:
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}
    return st if isinstance(st, dict) else {}


def _save_state(st: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _now(now: datetime | None = None) -> datetime:
    t = now or datetime.now(_KST)
    return t.astimezone(_KST) if t.tzinfo else t.replace(tzinfo=_KST)


# ── 무엇을 보낼지(순수) ─────────────────────────────────────────────────────────
def alert_target(records: list[dict], state: dict, now: datetime) -> tuple[dict | None, str]:
    """(보낼 기록, 안 보내는 사유) — 보낼 게 있으면 사유는 "". 판정만 하고 아무것도 안 쓴다."""
    now = _now(now)
    if not (ALERT_HOUR <= now.hour < ALERT_END_HOUR):
        return None, f"알림 시간({ALERT_HOUR:02d}:00~{ALERT_END_HOUR:02d}:00) 밖"
    # 호출 시점에 모듈 속성으로 찾는다 — 테스트가 달력을 갈아끼운다.
    from bot import market_calendar as _mc
    today = now.date().isoformat()
    is_open = _mc.is_trading_day("KR", today)
    if is_open is None:
        return None, "거래소 달력으로 오늘이 KR 개장일인지 정하지 못함(달력 없음·범위 밖·오류)"
    if not is_open:
        return None, (f"오늘({today[5:]} {_WEEKDAY[now.weekday()]})은 KR 휴장 — 개장일(월~금) "
                      "아침에만 보낸다 · 그 기록은 다음 개장일 아침에")
    from bot.kr_session import last_closed_session
    want = last_closed_session(now)
    if want is None:
        return None, "거래소 달력이 없어 기준일을 정하지 못함"
    if want in (state.get("sent") or {}):
        return None, f"{want} 기록은 이미 보냄"
    rec = next((r for r in records if r.get("date") == want), None)
    if rec is None:
        return None, f"{want} 기록이 아직 없음(수집 전이거나 실패 — 대시보드 상태 줄 참조)"
    if rec.get("nodata"):
        return None, f"{want} 은 KRX 자료 없음 카드 — 보낼 표가 없음"
    tried = (state.get("tries") or {}).get(want, 0)
    if tried >= MAX_TRIES:
        return None, f"{want} 보내기 {tried}회 실패 — 오늘은 더 묻지 않음"
    if not _settled(rec) and now.hour < RECHECK_WAIT_HOUR:
        return None, f"{want} 재확인(07:00 이후) 대기 — {RECHECK_WAIT_HOUR:02d}:00 까지 기다린다"
    return rec, ""


def _settled(rec: dict) -> bool:
    from bot.daily_inst import is_settled
    return is_settled(rec)


def caption(rec: dict) -> str:
    """사용자가 준 형식 그대로 — `기관 순매수 테이블 (YYYY.MM.DD 기준)`. 출처는 그림 안 기준일 옆에."""
    d = rec["date"].replace("-", ".")
    tail = "" if _settled(rec) else "\n(이튿날 재확인 전 저녁 값)"
    return f"기관 순매수 테이블 ({d} 기준){tail}"


def text_body(rec: dict) -> str:
    """그림을 못 그릴 때 보내는 같은 표(글자)."""
    from bot.daily_inst import LISTS
    lines = [caption(rec), SOURCE_LINE, ""]
    for t in rec.get("tiers") or []:
        lines.append(f"<b>{_h.escape(_tier_title(t['label']))}</b>")
        for key, title in LISTS:
            rows = (t.get("lists") or {}).get(key)
            lines.append(f"· {_h.escape(title)}")
            if not rows:
                lines.append("  (해당 종목 없음)" if rows == [] else "  (판정 안 함 — 자료 부족)")
                continue
            lines.append("  " + ", ".join(f"{_h.escape(str(r.get('n') or r['t']))} {r['r']:.2f}%"
                                          for r in rows))
        lines.append("")
    return "\n".join(lines).strip()


SOURCE_LINE = "KRX 기관합계 순매수 ÷ 시가총액"


def _tier_title(label: str) -> str:
    return "시총대비 기관 순매수액: " + label.replace("시가총액 ", "")


# ── 그림(대시보드 카드 형식) ─────────────────────────────────────────────────────
# 사용자 2026-10-13 "대시보드처럼 이쁘게" — `daily_inst.html` 카드(.di-tier · .di-card · .di-tbl)를
# 그대로 옮긴다: 구간 머리(파란 세로줄 + 집계 종목 수) · 둥근 카드 · 회색 머리줄(목록 이름 + 무엇을
# 고른 것인지) · 순위 · 종목 · 빨간 값과 그 표 1위 대비 막대. 색은 대시보드 라이트 팔레트와 **같은
# 값**이다(`fred_boards._BOARD_CSS` :root · `daily_inst._DI_CSS` --di-up) — 그림은 테마를 못 따르므로
# 라이트로 고정한다. 빈 목록 사유도 화면과 같은 문장(`daily_inst._na_reason`)을 쓴다(#38).
_PAL = {"bg": "#f7f8f9", "card": "#ffffff", "surface2": "#f0f1f3", "border": "#e4e5e9",
        "fg": "#282a30", "muted": "#696e78", "accent": "#276ae5", "up": "#b71c1c"}
_W_CARD, _GAP, _MARGIN = 300.0, 12.0, 18.0
_ROW_H, _HEAD_H, _TIER_H, _TOP_H, _FOOT_H, _TIER_GAP = 21.0, 26.0, 30.0, 52.0, 24.0, 12.0
_RANK_W, _VAL_FRAC = 24.0, 0.36       # 순위 칸 폭 · 값 칸 비율(화면 .di-v 38% 에 맞춤)
_NAME_W = 12.5                        # 종목명 최대 폭(전각 환산 — 글자수가 아니라 폭, #98)


def _fit(name: str) -> str:
    """종목명을 칸 폭 안으로 — 넘치면 자르고 `…`."""
    from bot.textwidth import vlen, vtrim
    return name if vlen(name) <= _NAME_W else vtrim(name, _NAME_W - 1.0) + "…"


def _list_note(rec: dict, key: str, rows) -> str:
    """빈 칸이 왜 비었는지 — 대시보드와 같은 문장."""
    from bot.daily_inst import _na_reason
    if rows is None:
        return _na_reason(rec, key)
    return "해당 종목 없음" if not rows else f"조건에 맞는 종목이 {len(rows)}개뿐입니다"


def _wrap(text: str, width: float) -> list[str]:
    """폭(전각 환산) 기준 줄바꿈 — 사유 문장이 카드 밖으로 나가지 않게."""
    from bot.textwidth import vlen
    out, cur = [], ""
    for word in text.split(" "):
        cand = f"{cur} {word}" if cur else word
        if cur and vlen(cand) > width:
            out.append(cur)
            cur = word
        else:
            cur = cand
    return out + ([cur] if cur else [])


def render_png(rec: dict, out_path: str) -> str | None:
    """기록 → PNG(대시보드 카드 형식). 성공하면 경로, 못 그리면 None(사유는 로그)."""
    from bot.korean_font import find_font, setup_matplotlib
    if not find_font() or not setup_matplotlib():
        log.warning("daily_inst_alert: 한글 폰트가 없어 그림을 못 그림 — 글자로 보낸다")
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, Rectangle
    except Exception as exc:                                   # noqa: BLE001
        log.warning("daily_inst_alert: matplotlib 없음(%s) — 글자로 보낸다", exc)
        return None
    from bot.daily_inst import LIST_SUB, LISTS, TOP_N
    P = _PAL
    tiers = rec.get("tiers") or []
    width = _MARGIN * 2 + _W_CARD * 3 + _GAP * 2
    card_h = _HEAD_H + _ROW_H * TOP_N
    tier_h = _TIER_H + card_h + _TIER_GAP
    height = _TOP_H + tier_h * max(len(tiers), 1) + _FOOT_H
    fig = None
    try:
        d = datetime.fromisoformat(rec["date"])   # 형식이 어긋나도 None → 글자로(독립 리뷰 L3)
        fig = plt.figure(figsize=(width / 100, height / 100), dpi=150)
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)
        ax.axis("off")
        fig.patch.set_facecolor(P["bg"])

        def card(x, y, w, h, fc):
            ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=7",
                                        facecolor=fc, edgecolor=P["border"], linewidth=0.8,
                                        mutation_aspect=1))

        rend = fig.canvas.get_renderer()

        def name_txt(x0, yy, name, x_max):
            """종목명 — 폭 추정(`_fit`)으로 먼저 자르고, 실제로 그린 폭이 값 칸을 넘으면 더 자른다
            (영문 대문자는 `vlen` 의 0.55 보다 넓다 — 독립 리뷰 M1)."""
            t = ax.text(x0, yy, name, fontsize=8, va="center", ha="left", color=P["fg"])
            body = name.rstrip("…")
            while body:
                bb = t.get_window_extent(renderer=rend).transformed(ax.transData.inverted())
                if max(bb.x0, bb.x1) <= x_max:
                    break
                body = body[:-1]
                t.set_text(body + "…")
            return t

        # 머리 — 제목 · 기준일 · 산식(대시보드 페이지 머리와 같은 순서)
        ax.text(_MARGIN, 22, "Daily 기관매수", fontsize=14, fontweight="bold",
                va="center", ha="left", color=P["fg"])
        ax.text(_MARGIN, 40, f"{rec['date'].replace('-', '.')} ({_WEEKDAY[d.weekday()]}) 기준 · "
                f"{SOURCE_LINE}", fontsize=7.5, va="center", ha="left", color=P["muted"])
        if not _settled(rec):
            ax.text(width - _MARGIN, 22, "이튿날 재확인 전 저녁 값", fontsize=7.5,
                    fontweight="bold", va="center", ha="right", color=P["up"])
        y = _TOP_H
        for t in tiers:
            ax.add_patch(Rectangle((_MARGIN, y + 7), 3, _TIER_H - 16, facecolor=P["accent"],
                                   edgecolor="none"))
            ax.text(_MARGIN + 10, y + _TIER_H / 2 - 1, t["label"], fontsize=10.5,
                    fontweight="bold", va="center", ha="left", color=P["fg"])
            ax.text(width - _MARGIN, y + _TIER_H / 2 - 1,
                    f"KRX 기관 집계 {t.get('n', 0):,}종목 중 순매수 {t.get('n_pos', 0):,}종목",
                    fontsize=7, va="center", ha="right", color=P["muted"])
            cy = y + _TIER_H
            for li, (key, title) in enumerate(LISTS):
                x = _MARGIN + li * (_W_CARD + _GAP)
                rows = (t.get("lists") or {}).get(key)
                card(x, cy, _W_CARD, card_h, P["card"])
                # 회색 머리줄 — 둥근 윗모서리는 카드가, 아래 직선은 사각형이 맡는다
                ax.add_patch(FancyBboxPatch((x, cy), _W_CARD, _HEAD_H,
                                            boxstyle="round,pad=0,rounding_size=7",
                                            facecolor=P["surface2"], edgecolor="none"))
                ax.add_patch(Rectangle((x, cy + _HEAD_H - 8), _W_CARD, 8,
                                       facecolor=P["surface2"], edgecolor="none"))
                ax.plot([x, x + _W_CARD], [cy + _HEAD_H] * 2, color=P["border"], linewidth=0.8)
                ax.text(x + 10, cy + _HEAD_H / 2, title, fontsize=8.5, fontweight="bold",
                        va="center", ha="left", color=P["fg"])
                ax.text(x + _W_CARD - 10, cy + _HEAD_H / 2, LIST_SUB.get(key, ""), fontsize=6.5,
                        va="center", ha="right", color=P["muted"])
                by = cy + _HEAD_H
                shown = rows or []
                top = max((r["r"] for r in shown), default=0.0)
                for ri, r in enumerate(shown[:TOP_N]):
                    ry = by + ri * _ROW_H
                    if ri:
                        ax.plot([x + 8, x + _W_CARD - 8], [ry, ry], color=P["border"], linewidth=0.5)
                    mid = ry + _ROW_H / 2
                    ax.text(x + _RANK_W - 4, mid, str(ri + 1), fontsize=6.5, va="center",
                            ha="right", color=P["muted"])
                    name_txt(x + _RANK_W + 6, mid, _fit(str(r.get("n") or r["t"])),
                             x + _W_CARD * (1 - _VAL_FRAC) - 4)
                    if top > 0:
                        bw = (_W_CARD * _VAL_FRAC - 12) * max(0.04, min(1.0, r["r"] / top))
                        ax.add_patch(FancyBboxPatch((x + _W_CARD - 6 - bw, ry + 4), bw, _ROW_H - 8,
                                                    boxstyle="round,pad=0,rounding_size=3",
                                                    facecolor=P["up"], alpha=0.10,
                                                    edgecolor="none"))
                    ax.text(x + _W_CARD - 10, mid, f"{r['r']:.2f}%", fontsize=8,
                            fontweight="bold", va="center", ha="right", color=P["up"])
                if rows is None or len(shown) < TOP_N:
                    ny = by + len(shown) * _ROW_H
                    lines = _wrap(_list_note(rec, key, rows), 24)
                    mid = ny + (card_h - _HEAD_H - len(shown) * _ROW_H) / 2 if not shown \
                        else ny + _ROW_H / 2
                    if shown:
                        ax.plot([x + 8, x + _W_CARD - 8], [ny, ny], color=P["border"], linewidth=0.5)
                    for k, ln in enumerate(lines):
                        ax.text(x + _W_CARD / 2, mid + (k - (len(lines) - 1) / 2) * 12, ln,
                                fontsize=7, va="center", ha="center", color=P["muted"])
            y += tier_h
        ax.text(_MARGIN, height - _FOOT_H / 2 - 2,
                "숫자 = 기준일 기관 순매수 ÷ 시가총액 · 막대 = 그 표 1위 대비 · 종목별 순매수액·시총은 "
                "대시보드 Daily 기관매수", fontsize=6.5, va="center", ha="left", color=P["muted"])
        fig.savefig(out_path, dpi=150, facecolor=P["bg"])
        return out_path
    except Exception as exc:                                   # noqa: BLE001
        log.warning("daily_inst_alert: 그림 실패(%s: %s) — 글자로 보낸다", type(exc).__name__, exc)
        return None
    finally:
        if fig is not None:
            plt.close(fig)


# ── 보내기 ───────────────────────────────────────────────────────────────────
def _send(rec: dict, chats: list[str]) -> dict[str, str | None]:
    """채널마다 {chat: 방식 | None(실패)}. 그림이 되면 사진, 아니면(또는 사진이 실패하면) 같은 표를 글자로."""
    from bot.daily_kr_flow import push_telegram, push_telegram_photo
    out = _dir() / f"alert_{rec['date']}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    png = render_png(rec, str(out))
    res: dict[str, str | None] = {}
    try:
        for c in chats:
            # 채널마다 따로 감싼다 — 한 채널에서 던지면 이미 받은 채널의 성공까지 잃어 다음 주기에
            # 그 채널로 다시 간다(델타 리뷰 L1).
            try:
                if png and push_telegram_photo(png, caption(rec), filename="daily_inst.png", chats=[c]):
                    res[c] = "사진"
                    continue
                if png:
                    log.warning("daily_inst_alert: 채널 %s 사진 보내기 실패 — 글자로 다시 보낸다", c)
                res[c] = "글자" if push_telegram(text_body(rec), chats=[c]) else None
            except Exception as exc:                           # noqa: BLE001
                log.warning("daily_inst_alert: 채널 %s 보내기 예외(%s: %s)", c, type(exc).__name__, exc)
                res[c] = None
        return res
    finally:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass


def _targets() -> tuple[list[str], str]:
    """(보낼 채널, 못 보내는 사유) — 설정이 없으면 '보내기 실패' 가 아니라 그 이름을 댄다(#82)."""
    from bot.daily_kr_flow import channel_targets
    token, chats = channel_targets()
    if not token:
        return [], "TELEGRAM_BOT_TOKEN 이 없어 보낼 수 없음(.env)"
    if not chats:
        return [], "CHANNEL_CHAT_IDS 가 없어 보낼 곳이 없음(.env)"
    return chats, ""


def maybe_alert(now: datetime | None = None) -> dict:
    """15분 주기에서 부른다 — 보낼 때가 됐으면 아직 못 받은 채널에만 보낸다. {sent, date, how, reason}."""
    from bot import daily_inst as di
    now = _now(now)
    st = load_state()
    rec, why = alert_target(di.load_records(), st, now)
    st["last_check"] = now.isoformat(timespec="seconds")
    st["last_reason"] = why
    if rec is None:
        _save_state(st)
        return {"sent": False, "reason": why}
    d = rec["date"]
    chats, why = _targets()
    if not chats:
        st["last_reason"] = f"{d} — {why}"
        _save_state(st)
        log.warning("daily_inst_alert: %s", st["last_reason"])
        return {"sent": False, "date": d, "reason": st["last_reason"]}
    done = st.setdefault("sent_chats", {}).setdefault(d, {})
    todo = [c for c in chats if c not in done]
    tries = st.setdefault("tries", {})
    err = ""
    if todo:
        tries[d] = int(tries.get(d, 0)) + 1
        try:
            res = _send(rec, todo)
        except Exception as exc:                               # noqa: BLE001
            # 예외가 그대로 나가면 늘린 시도 횟수를 못 적어 상한(MAX_TRIES)이 영영 안 걸린다.
            res = {}
            err = f" · 예외({type(exc).__name__})"
            log.warning("daily_inst_alert: %s 보내기 예외 — %s", d, exc)
    else:
        res = {}          # 채널 설정에서 남은 채널이 빠졌다 — 받을 채널은 이미 다 받았다
    for c, how in res.items():
        if how:
            done[c] = {"at": now.isoformat(timespec="seconds"), "how": how}
    left = [c for c in chats if c not in done]
    hows = sorted({v["how"] for v in done.values()})
    if not left:
        st.setdefault("sent", {})[d] = {"at": now.isoformat(timespec="seconds"), "how": "·".join(hows),
                                        "settled": _settled(rec), "chats": len(chats)}
        log.info("daily_inst_alert: %s 기관 순매수 테이블을 채널 %d곳에 보냄(%s)", d, len(chats),
                 "·".join(hows))
    else:
        st["last_reason"] = (f"{d} 채널 {len(left)}/{len(chats)}곳 보내기 실패({tries[d]}/{MAX_TRIES}회)"
                             f"{err} — 다음 주기에 그 채널만 다시")
        log.warning("daily_inst_alert: %s", st["last_reason"])
    for k in sorted(st.get("sent") or {})[:-KEEP_SENT]:
        st["sent"].pop(k, None)
    for k in sorted(tries)[:-KEEP_SENT]:
        tries.pop(k, None)
    for k in sorted(st["sent_chats"])[:-KEEP_SENT]:
        st["sent_chats"].pop(k, None)
    _save_state(st)
    return {"sent": not left, "date": d, "how": "·".join(hows), "reason": st["last_reason"]}


def why(now: datetime | None = None) -> int:
    """읽기 전용 — 지금 보낼 기록이 있는지·마지막으로 보낸 날을 말한다."""
    from bot import daily_inst as di
    now = _now(now)
    st = load_state()
    rec, why_ = alert_target(di.load_records(), st, now)
    sent = st.get("sent") or {}
    part = {d: v for d, v in (st.get("sent_chats") or {}).items() if v}
    got = sorted(set(sent) | set(part))
    last = got[-1] if got else None
    print(f"# daily_inst_alert --why · {now.strftime('%Y-%m-%d %H:%M KST')}")
    if last in sent:
        print(f"① 마지막으로 보낸 기준일: {last} ({sent[last]['at']} · {sent[last]['how']})")
    else:
        print(f"① 마지막으로 보낸 기준일: {last or '—'}" + (" (일부 채널만)" if last else ""))
    print(f"② 마지막 확인: {st.get('last_check') or '—'} · 사유: {st.get('last_reason') or '—'}")
    print("③ 지금: " + (f"보낼 기록 있음 — {rec['date']}" if rec else f"보내지 않음 — {why_}"))
    try:
        from bot.korean_font import diagnose
        print(f"④ 한글 폰트: {diagnose()}")
    except Exception as exc:                                   # noqa: BLE001
        print(f"④ 한글 폰트: 판정 불가({type(exc).__name__})")
    chats, tw = _targets()
    print(f"⑤ 보낼 채널: {len(chats)}곳" if chats else f"⑤ 보낼 채널: ❌ {tw}")
    for d in sorted(part):
        if d in sent:
            continue
        # 다시 묻는다고 적는 건 **정말 다시 물을 때만** — 그날이 지금 대상이고 상한 전이어야 한다
        # (델타 리뷰: 상한을 넘긴 날에도 '다음 주기에 다시' 라고 적었다).
        again = rec is not None and rec.get("date") == d
        tail = "나머지는 다음 주기에 다시" if again else "나머지는 보내지 못하고 그날은 끝남"
        print(f"   {d}: 채널 {len(part[d])}/{len(chats) or '?'}곳 받음 — {tail}")
    return 0


def preview(date: str | None = None) -> int:
    """그림만 만든다(보내지 않는다) — 경로를 찍는다."""
    from bot import daily_inst as di
    recs = [r for r in di.load_records() if not r.get("nodata")]
    if date:
        recs = [r for r in recs if r["date"] == date]
    if not recs:
        print("❓ 그릴 기록이 없음")
        return 1
    rec = max(recs, key=lambda r: r["date"])
    out = _dir() / f"preview_{rec['date']}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    got = render_png(rec, str(out))
    print(f"✅ {got}" if got else "❌ 그림을 못 그림 — 위 경고 참조(한글 폰트 등)")
    return 0 if got else 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m bot.daily_inst_alert")
    ap.add_argument("--why", action="store_true", help="읽기 전용 상태 진단")
    ap.add_argument("--preview", nargs="?", const="", metavar="날짜",
                    help="그림만 만든다(보내지 않는다)")
    a = ap.parse_args(argv)
    _load_channel_env()
    if a.preview is not None:
        return preview(a.preview or None)
    return why()


def _load_channel_env() -> None:
    """진단 프로세스는 `.env` 를 안 읽는다(#23) — 봇이 쓰는 그 세 키만 `.env` 에서 채운다(전체 주입
    금지). 안 하면 `--why` 가 키가 **있는데도** '채널 없음' 이라 말한다."""
    try:
        from bot.env_keys import env_key
        for k in ("TELEGRAM_BOT_TOKEN", "STANDARDVIEW_TELEGRAM_TOKEN", "CHANNEL_CHAT_IDS"):
            v = env_key(k)
            if v:
                os.environ.setdefault(k, v)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("daily_inst_alert: .env 를 못 읽음(%s) — 채널 판정은 환경변수만 본다", exc)


if __name__ == "__main__":
    import sys
    sys.exit(main())
