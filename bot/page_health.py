"""화면이 스스로 적은 경고를 읽어 판정한다 — 모든 대시보드 공용(순수 함수).

사용자 2026-11-11: 한국 업종 등락이 **엿새째 저장분**이고 미국은 폴백이었는데
일일 결산은 조용했다. 원인 둘 — ① 메인 대시보드의 그 위젯을 보는 감사가 없었고
(#24 감사는 화면마다 손으로 만든 목록이다) ② 화면은 `저장분 ⚠️` 로 사실대로
말했지만 결산은 ❌ 만 올린다(⚠️ 는 며칠을 이어져도 조용하다).

그래서 화면별 감사를 하나 더 만드는 대신, **화면이 이미 적는 문구**를 전 화면에서
읽는다(#35 화면이 쓰는 그 경로 · #86 상태는 아는 쪽이 말한다). 새 대시보드는
아무것도 등록하지 않아도 덮인다.

판정(한 줄이 결산에 그대로 오르므로 화면·문구·경과를 그 줄에 싣는다, #356):
  · 저장분 나이 ≥ 96시간(주말 포함 한 세션 간격을 넘는다)  → 알림
  · '표시할 수 없습니다'(값이 아예 없다) 가 다음 날에도 그대로  → 알림
  · 그 밖의 ⚠️ 문구가 사흘째 그대로                         → 알림
  · 알린 뒤에는 7일마다 한 번만 다시 알린다 — 매일 같은 ❌ 가 오면 진짜 ❌ 를
    가린다(#260). 그 사이 날은 ⚠️ 로 '며칠째' 만 적는다.

⚠️ 못 보는 축(#274): 경고 문구 없이 값이 틀린 화면 · 가이드(`<details class=
"…guide">`) 안의 문구(일부러 뺀다 — 고정 설명문이 사흘 뒤 알림이 된다) · 스크립트가
런타임에 그리는 문구.
"""
from __future__ import annotations

import html as _h
import re

STALE_FAIL_H = 96          # 금 15:30 저장분이 월 08:00 감사에 64시간 — 그보다 넉넉히
BLOCKED_FAIL_DAYS = 1
WARN_FAIL_DAYS = 3
REPEAT_DAYS = 7
MAX_CHUNK = 240            # 이보다 긴 ⚠️ 문단은 설명문이다(상태 한 줄이 아니다)
STATE_KEEP_DAYS = 2        # 하루 걸러 사라졌다 돌아온 문구는 같은 사건으로 센다

_DROP_BLOCK = re.compile(
    r"<(script|style)\b.*?</\1>"
    r"|<details\b[^>]*class=\"[^\"]*guide[^\"]*\".*?</details>",
    re.S | re.I)
_BLOCK_TAG = re.compile(
    r"</?(?:div|p|li|tr|h[1-6]|section|header|footer|table|ul|ol|details|summary|br)\b[^>]*>",
    re.I)
_HEAD_TAG = re.compile(r"<h[1-3]\b[^>]*>", re.I)
HEAD_MARK = "\x01"        # 제목 조각 표식 — 어느 위젯의 문구인지 붙이려고
_TAG = re.compile(r"<[^>]+>")
_STALE = re.compile(r"저장분[^()\n]{0,60}\(\s*(\d+)\s*(일|시간|분)\s*전")
_BLOCKED = "표시할 수 없습니다"
_UNIT_H = {"일": 24.0, "시간": 1.0, "분": 1.0 / 60}


def visible_chunks(html: str) -> list:
    """HTML → 블록 단위 보이는 글 조각(순수). 스크립트·스타일·가이드는 뺀다."""
    s = _DROP_BLOCK.sub("\n", html or "")
    s = _HEAD_TAG.sub("\n" + HEAD_MARK, s)
    s = _BLOCK_TAG.sub("\n", s)
    out = []
    for ln in _TAG.sub("", s).split("\n"):
        t = " ".join(_h.unescape(ln).split())
        if t:
            out.append(t)
    return out


def findings(html: str) -> list:
    """화면 → [{kind, text, age_h}] (순수). kind = stale | blocked | warn.

    한 조각은 한 번만 센다 — 가장 무거운 갈래로(저장분 > 표시 불가 > 그 밖)."""
    out, seen, where = [], set(), ""
    for t in visible_chunks(html):
        if t.startswith(HEAD_MARK):
            where = t.lstrip(HEAD_MARK).strip()[:50]
            continue
        m = _STALE.search(t)
        if m:
            kind, age = "stale", int(m.group(1)) * _UNIT_H[m.group(2)]
        elif _BLOCKED in t and "⚠" in t:
            kind, age = "blocked", None
        elif t.startswith("⚠") and len(t) <= MAX_CHUNK:
            kind, age = "warn", None
        else:
            continue
        text = t[:MAX_CHUNK]
        k = (kind, norm_text(text))
        if k in seen:
            continue
        seen.add(k)
        out.append({"kind": kind, "text": text, "age_h": age, "where": where})
    return out


def norm_text(text: str) -> str:
    """경과를 세는 열쇠 — 숫자(시각·나이·건수)는 날마다 바뀌므로 지운다."""
    return re.sub(r"\d+", "#", text or "")[:160]


def state_key(page: str, f: dict) -> str:
    return f"{page}|{f['kind']}|{norm_text(f['text'])}"


def alert_due(days: int, threshold: int) -> bool:
    """오늘 알릴 날인가 — 문턱에 닿은 날과 그 뒤 7일마다."""
    return days >= threshold and (days - threshold) % REPEAT_DAYS == 0


def judge(f: dict, days: int) -> tuple:
    """(등급, 사유) — 등급은 'fail'(오늘 알림) · 'warn'(기록만).

    `days` = 이 문구를 처음 본 날부터 지난 날 수(오늘 처음이면 0)."""
    kind = f["kind"]
    if kind == "stale":
        age = f.get("age_h") or 0
        if age < STALE_FAIL_H:
            return "warn", f"저장분 {age:.0f}시간(문턱 {STALE_FAIL_H}시간 미만)"
        why = f"저장분이 {age / 24:.1f}일째 — 수집이 멈췄습니다"
        return ("fail" if alert_due(days, 0) else "warn"), why
    thr = BLOCKED_FAIL_DAYS if kind == "blocked" else WARN_FAIL_DAYS
    what = "값을 못 보여 주는 상태" if kind == "blocked" else "경고 문구"
    if days < thr:
        return "warn", f"{what} {days + 1}일째(알림 문턱 {thr + 1}일째)"
    return ("fail" if alert_due(days, thr) else "warn"), f"{what}가 {days + 1}일째 그대로"


def roll_state(state: dict, keys, today: str, day_diff) -> dict:
    """본 열쇠들로 기록을 굴린다(순수) → {key: {"first": d, "last": d}}.

    오늘 안 보인 열쇠는 `STATE_KEEP_DAYS` 까지만 남긴다 — 그 뒤 다시 보이면 새
    사건이다. `day_diff(a, b)` = 날짜 문자열 차(일)."""
    out = {}
    for k, v in (state or {}).items():
        if isinstance(v, dict) and v.get("first") and v.get("last") \
                and day_diff(v["last"], today) <= STATE_KEEP_DAYS:
            out[k] = {"first": v["first"], "last": v["last"]}
    for k in keys:
        cur = out.get(k)
        out[k] = {"first": cur["first"] if cur else today, "last": today}
    return out
