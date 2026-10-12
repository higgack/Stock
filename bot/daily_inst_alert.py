"""Daily 기관매수 텔레그램 알림 — KR 개장일(월~금) 아침 08:00 KST 부터(사용자 2026-10-12).

사용자가 준 형식(블로그 「기관 순매수 테이블」 이미지) 그대로 — 흰 바탕에 시총 구간 셋 × 목록 셋
(순매수 강도 · 순매수 전환 · 5일 연속 순매수 TOP 10) 표를 한 장의 PNG 로 그려 채널에 사진으로
보낸다. 표의 숫자·이름은 대시보드(`daily_inst.html`)와 **같은 기록**에서 온다(#38) — 이 모듈은
그리기와 보내기만 한다.

언제 무엇을 보내나
- **KR 개장일** 아침 08:00 KST 부터(사용자 2026-10-12 "월~금 08:00" — 보통 장 시작 전에 가지만,
  재확인 대기면 09:00 까지 · 봇이 늦게 뜨면 12:00 까지 늦게 갈 수 있다),
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


# ── 그림(블로그 형식) ───────────────────────────────────────────────────────────
_W_TBL, _GAP, _MARGIN = 290.0, 12.0, 16.0
_ROW_H, _HEAD_H, _TITLE_H, _TIER_GAP = 19.0, 23.0, 30.0, 14.0
_NAME_CHARS = 15


def _fit(name: str) -> str:
    return name if len(name) <= _NAME_CHARS else name[:_NAME_CHARS - 1] + "…"


def render_png(rec: dict, out_path: str) -> str | None:
    """기록 → PNG(블로그 표 형식). 성공하면 경로, 못 그리면 None(사유는 로그)."""
    from bot.korean_font import find_font, setup_matplotlib
    if not find_font() or not setup_matplotlib():
        log.warning("daily_inst_alert: 한글 폰트가 없어 그림을 못 그림 — 글자로 보낸다")
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except Exception as exc:                                   # noqa: BLE001
        log.warning("daily_inst_alert: matplotlib 없음(%s) — 글자로 보낸다", exc)
        return None
    from bot.daily_inst import LISTS, TOP_N
    tiers = rec.get("tiers") or []
    width = _MARGIN * 2 + _W_TBL * 3 + _GAP * 2
    tier_h = _TITLE_H + _HEAD_H + _ROW_H * TOP_N + _TIER_GAP
    height = _MARGIN + tier_h * max(len(tiers), 1) + _MARGIN / 2
    fig = None
    try:
        fig = plt.figure(figsize=(width / 100, height / 100), dpi=150)
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)
        ax.axis("off")
        fig.patch.set_facecolor("white")
        black, grid = "#111111", "#9a9a9a"
        y = _MARGIN
        for ti, t in enumerate(tiers):
            ax.text(_MARGIN, y + _TITLE_H * 0.55, _tier_title(t["label"]), fontsize=10.5,
                    fontweight="bold", va="center", ha="left", color=black)
            if ti == 0:
                ax.text(width - _MARGIN, y + _TITLE_H * 0.55,
                        f"{rec['date'].replace('-', '.')} 기준 · {SOURCE_LINE}",
                        fontsize=6.5, va="center",
                        ha="right", color="#555555")
            ty = y + _TITLE_H
            for li, (key, title) in enumerate(LISTS):
                x = _MARGIN + li * (_W_TBL + _GAP)
                rows = (t.get("lists") or {}).get(key)
                body_h = _ROW_H * TOP_N
                ax.add_patch(Rectangle((x, ty), _W_TBL, _HEAD_H, facecolor="#f2f2f2",
                                       edgecolor=black, linewidth=1.0))
                ax.text(x + _W_TBL / 2, ty + _HEAD_H / 2, title, fontsize=7.5,
                        fontweight="bold", ha="center", va="center", color=black)
                by = ty + _HEAD_H
                ax.add_patch(Rectangle((x, by), _W_TBL, body_h, facecolor="white",
                                       edgecolor=black, linewidth=0.8))
                if not rows:
                    msg = "해당 종목 없음" if rows == [] else "자료 부족 — 판정 안 함"
                    ax.text(x + _W_TBL / 2, by + body_h / 2, msg, fontsize=7, ha="center",
                            va="center", color="#666666")
                    continue
                split = x + _W_TBL * 0.68
                ax.plot([split, split], [by, by + body_h], color=grid, linewidth=0.5)
                for ri in range(TOP_N):
                    ry = by + ri * _ROW_H
                    if ri:
                        ax.plot([x, x + _W_TBL], [ry, ry], color=grid, linewidth=0.4)
                    if ri >= len(rows):
                        continue
                    r = rows[ri]
                    ax.text(x + 5, ry + _ROW_H / 2, _fit(str(r.get("n") or r["t"])),
                            fontsize=7, ha="left", va="center", color=black)
                    ax.text(x + _W_TBL - 5, ry + _ROW_H / 2, f"{r['r']:.2f}%", fontsize=7,
                            ha="right", va="center", color=black)
            y += tier_h
        fig.savefig(out_path, dpi=150, facecolor="white")
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
