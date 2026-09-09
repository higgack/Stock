"""수출입 대시보드 헤더('현재 잠정 X · 확정 Y')의 **건강 판정** — 순수 함수.

2026-09-09 실측: 헤더가 `잠정 7월 1-20일 · 확정 6월 전체` 에서 멈춘 채 OpenAPI
카드는 `2026-08 전월(1~말일)` 을 보이고 있었다. 헤더는 **채널 알림**(텔레그램 →
inbox.jsonl → store.db)에서, 카드는 **OpenAPI** 에서 오므로 채널 경로가 7/21 뒤
멈추면 정확히 이렇게 갈린다. 갈래는 셋이고 처방이 다 다르다(#82):
  listener      — 리스너(trade-bot.service)가 죽어 inbox 에 새 줄이 없다
  channel_quiet — 리스너는 살아 있는데 채널 메시지가 안 왔다/전달 안 됐다
  ingest        — inbox 엔 새 줄이 있는데 DB 최신이 그보다 옛날(인제스트·타이머)
  parser        — inbox 엔 있고 eval_misses 에 최근 드랍이 쌓임(서식 변경, #261)
판정은 여기서 하고 `trade.dashboard --why` 는 사실을 모아 넘기기만 한다(#41).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

# 관세청 발표 규약(11일·21일 잠정 / 익월 1일 전월 전체 잠정 / 익월 15일 전월 확정).
# `trade/scripts/health_check.py` 의 `_expected_recent_publications` 와 같은 규약 —
# 회귀가 두 구현의 결과를 대조한다(복제가 갈리면 잡힌다, #38·#317).
_MONTHLY_RULE = ((11, "decadal_10"), (21, "decadal_20"),
                 (1, "monthly_preliminary"), (15, "monthly_final"))


def expected_publications(today: date, lookback_days: int = 60,
                          grace_days: int = 2) -> list[tuple[str, str]]:
    """[(YYYY-MM-DD, kind)] — `today - grace_days` 이전에 나왔어야 할 발표들을
    lookback 창 안에서 날짜순으로."""
    start = today - timedelta(days=lookback_days)
    cutoff = today - timedelta(days=grace_days)
    out: list[tuple[str, str]] = []
    y, m = start.year, start.month
    while (y, m) <= (today.year, today.month):
        for day, kind in _MONTHLY_RULE:
            d = date(y, m, day)
            if start <= d <= cutoff:
                out.append((d.isoformat(), kind))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(out)


def missing_publications(expected: list[tuple[str, str]],
                         posted: dict[str, set[str]], window_days: int = 2) -> list[tuple[str, str]]:
    """예정 발표 중 ±window 일 안에 같은 kind 알림이 **없는** 것. `posted` =
    {kind: {posted_date, ...}}."""
    miss = []
    for d, kind in expected:
        dd = date.fromisoformat(d)
        hit = any(abs((date.fromisoformat(p) - dd).days) <= window_days
                  for p in posted.get(kind, set()))
        if not hit:
            miss.append((d, kind))
    return miss


def _age_days(iso: str | None, today: date) -> int | None:
    if not iso:
        return None
    try:
        return (today - date.fromisoformat(str(iso)[:10])).days
    except ValueError:
        return None


def verdict(facts: dict, today: date) -> dict:
    """facts = {db_newest, inbox_newest, inbox_lines_after_db, eval_miss_recent,
    listener_active(bool|None), missing:[(date,kind)]} → {branch, reason, lines}.
    단정할 재료가 없으면 `unknown` 이고 왜 모르는지 적는다(#54·#165)."""
    missing = facts.get("missing") or []
    if not missing:
        return {"branch": "ok", "reason": "예정 발표가 전부 도착했다(±2일)", "lines": []}
    db_age = _age_days(facts.get("db_newest"), today)
    ib_age = _age_days(facts.get("inbox_newest"), today)
    after = facts.get("inbox_lines_after_db")
    misses = facts.get("eval_miss_recent") or 0
    active = facts.get("listener_active")
    first_missing, last_missing = missing[0][0], missing[-1][0]
    lines = [f"놓친 발표 {len(missing)}건 — 첫 누락 {first_missing} ({missing[0][1]}) · "
             f"마지막 누락 {last_missing} ({missing[-1][1]})"]
    if after:
        if misses:
            return {"branch": "parser", "lines": lines,
                    "reason": (f"inbox 엔 DB 최신 이후 {after}줄이 있고 eval_misses 에 최근 "
                               f"{misses}건 — 채널 서식이 바뀌어 파서가 드랍하는 중(#261). "
                               "unstored_check 백로그를 보고 파서를 맞춘 뒤 ingest 재실행")}
        return {"branch": "ingest", "lines": lines,
                "reason": (f"inbox 엔 DB 최신 이후 {after}줄이 있는데 DB 에 안 들어옴 — "
                           "dashboard-refresh 타이머/ingest_inbox 를 볼 것")}
    # inbox 자체가 없거나 비어 있으면(리스너가 한 줄도 못 썼거나 경로가 다름) 시각
    # 비교 없이 서비스 상태로 가른다 — 픽스처에 파일이 없자 unknown 으로 새어 발각.
    if not facts.get("inbox_newest"):
        note = "inbox.jsonl 에 기록이 없음(파일 없음/빈 파일 — 경로가 다르면 --why 의 ④ 경로를 볼 것)"
        if active is False:
            return {"branch": "listener", "lines": lines,
                    "reason": f"{note} · trade-bot.service 비활성 — 리스너가 죽었다. `systemctl status trade-bot` 부터"}
        if active is True:
            return {"branch": "channel_quiet", "lines": lines,
                    "reason": f"{note} · 리스너는 활성 — 채널 메시지가 안 왔거나 전달 필터에 걸림"}
        return {"branch": "unknown", "lines": lines,
                "reason": f"{note} · 리스너 상태를 못 물음(systemctl 실패)"}
    # 리스너/채널 갈래는 '가장 최근에 놓친 발표'보다 inbox 가 더 옛날일 때 — 첫 누락과
    # 비교하면 중간에 죽은 리스너를 못 잡는다(첫 판 스모크에서 unknown 으로 새어 발각).
    if ib_age is not None and \
            date.fromisoformat(str(facts["inbox_newest"])[:10]) < date.fromisoformat(last_missing):
        if active is False:
            return {"branch": "listener", "lines": lines,
                    "reason": (f"inbox 최신이 {ib_age}일 전이고 trade-bot.service 가 활성이 아님 — "
                               "리스너가 죽었다. `systemctl status trade-bot` 부터")}
        if active is True:
            return {"branch": "channel_quiet", "lines": lines,
                    "reason": (f"리스너는 활성인데 inbox 최신이 {ib_age}일 전 — 채널에 메시지가 "
                               "안 왔거나 전달 필터에 걸림(원천 채널을 직접 확인)")}
        return {"branch": "unknown", "lines": lines,
                "reason": f"inbox 최신 {ib_age}일 전, 리스너 상태를 못 물음(systemctl 실패)"}
    return {"branch": "unknown", "lines": lines,
            "reason": "누락은 있는데 inbox·DB 시각으로는 갈래가 안 갈린다 — 아래 사실을 볼 것"}
