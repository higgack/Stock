"""수출입 대시보드 형제 링크의 NEW 표시 (사용자 2026-09-30 "수출입에 여기 대시보드쪽에
업데이트가 되는것이 있으면 5일간 New 라는 표시가 있어서 체크해야한다는걸 내가 알아볼수
있게해줘").

링크마다 **그 페이지에 원천이 마지막으로 새 데이터를 게시한 시각**을 구하고, 그 시각이
최근 ``NEW_DAYS`` 일 안이면 링크 옆에 NEW 를 붙인다. 날짜는 KST 달력일로 세고 게시일이
1일째다 — 9월 28일에 게시됐으면 9월 28일~10월 2일에 NEW 가 붙는다.

기준 시각은 DB 의 ``posted_at`` 이다. ingest_inbox 가 텔레그램 **원 게시 시각**
(``forward_origin_date``, 포워드가 아니면 봇이 받은 시각)을 싣는다. 같은 대시보드 알림
카드의 NEW(``isAlertNew``)와 같은 축(posted_at)이지만 창과 날짜 계산은 다르다 — 카드는
posted_at 문자열의 날짜 부분(앞 10자)을 브라우저의 KST 오늘과 비교해 7일, 이 링크는
KST 달력일로 ``NEW_DAYS`` 일을 렌더 시각에 판정한다.

- 같은 글을 다시 받거나(재포워드) 파서를 올려 다시 파싱해도 ``posted_at`` 은 그대로라
  NEW 가 켜지지 않는다 — ingest 가 원 게시 시각을 먼저 쓰기 때문이고, 그 순서는
  ``trade/tests/test_link_new.py`` 가 ingest 를 태워 지킨다. ``updated_at`` 은 매 upsert
  마다 지금 시각으로 바뀌어(재포워드·재파싱 포함) 기준이 될 수 없다.
- 판정 시각은 렌더 시각이다. 수출입 대시보드는 5분마다 다시 그려지므로 NEW 가 붙고
  떨어지는 것도 그 주기를 따른다.

못 보는 축(재지 않았다):
- 놓쳤던 글을 나중에 회수(백필)했을 때 그 글의 게시 시각이 창보다 오래됐으면, 페이지에
  새 행이 생겨도 NEW 가 붙지 않는다. 자동 회수는 평소 최근 3일을 훑지만 관련성 필터가
  바뀐 배포 뒤에는 40일을 한 번 훑는다(#403) — 새 소스를 붙인 날이 그렇다.
- 나쁜양파가 다른 채널의 글을 재게시하면(#411) posted_at 은 재게시 시각이 아니라 **원래
  채널의 게시 시각**이다. 재게시가 며칠 늦으면 NEW 가 짧게 붙거나 안 붙는다.
- ingest 는 5분마다 inbox 전체를 도착 순서대로 다시 upsert 하고, 한 행의 posted_at 은
  그 행에 마지막으로 쓴 글의 값이다. 같은 (키, 월)의 옛 글이 회수로 늦게 들어오면 그
  행의 posted_at 이 옛 값으로 돌아갈 수 있다(이 기능 이전부터의 동작).
"""
from __future__ import annotations

import html
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("trade.link_new")

NEW_DAYS = 5
KST = timezone(timedelta(hours=9))
# 지금보다 이만큼 넘게 미래인 값은 원 게시 시각으로 보지 않는다 — 텔레그램 시각은 실제
# 서버 시각이다. 호스트 시계 차이나 KST 벽시계에 +00:00 을 붙인 실수(9시간)까지는 받는다.
FUTURE_SLACK = timedelta(days=1)


def now_utc() -> datetime:
    """판정 기준 시각(지금, UTC). 렌더가 시각을 받지 않으면 이걸 쓴다 — 호스트 로컬
    시간대에 기대지 않는다(규칙 10a). 테스트는 여기를 고정해 렌더의 시계를 잰다."""
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """시간대 없는 시각은 UTC 로 본다 — 서버 로컬 시간대에 기대지 않는다."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def parse_ts(value) -> datetime | None:
    """ISO 8601 문자열 → 시간대가 붙은 datetime. 오프셋이 없으면 UTC 로 본다(텔레그램이
    주는 시각은 UTC 다). 문자열이 아니거나 못 읽으면 None.

    달력 끝(0001년·9999년)이라 KST 로 옮기지 못하는 값도 None 이다 — 그런 값 하나가
    판정·툴팁에서 OverflowError 를 내 대시보드 렌더 전체를 멈췄다(독립 리뷰 실측).

    쓰는 쪽은 전부 ``datetime.isoformat()`` 이라 ``+00:00``·``+09:00`` 꼴이다
    (ingest_inbox 의 posted_at · report_archive 의 ts)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = _aware(datetime.fromisoformat(value.strip()))
        dt.astimezone(KST)
    except (ValueError, OverflowError):
        return None
    return dt


def _latest(values, now: datetime | None, where: str) -> datetime | None:
    """원 게시 시각 문자열들 중 가장 늦은 시각. 지금보다 ``FUTURE_SLACK`` 넘게 미래인
    값은 빼고 몇 건을 뺐는지 경고한다 — 먼 미래 값 하나가 최댓값으로 굳으면 NEW 가
    영원히 켜지고, 진짜 마지막 게시 시각은 툴팁에서 사라진다."""
    limit = _aware(now if now is not None else now_utc()) + FUTURE_SLACK
    best: datetime | None = None
    future: list[datetime] = []
    for v in values:
        dt = parse_ts(v)
        if dt is None:
            continue
        if dt > limit:
            future.append(dt)
        elif best is None or dt > best:
            best = dt
    if future:
        log.warning("NEW 판정: %s 에 지금보다 %d일 넘게 미래인 게시 시각 %d건(가장 늦은 값 "
                    "%s)은 원 게시 시각으로 볼 수 없어 빼고 판정합니다",
                    where, FUTURE_SLACK.days, len(future), max(future).isoformat())
    return best


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def latest_posted_at(db_path: Path | str, *, now: datetime | None = None) -> datetime | None:
    """그 DB 에서 ``posted_at`` 열을 가진 모든 테이블의 가장 늦은 게시 시각.

    읽기 전용(``mode=ro``)으로 연다. 소스의 ``open_*_db`` 는 파일이 없으면 빈 DB 를
    만들고 스키마 마이그레이션까지 하므로 그 경로를 타지 않는다 — DB 파일은 만들지도
    바꾸지도 않는다. 다만 WAL DB 는 읽기 전용으로 열어도 ``-shm``·``-wal`` 사이드카가
    생길 수 있고, 닫은 뒤에도 남는다(독립 리뷰 실측).
    파일이 없으면 None(아직 데이터가 없는 소스다). 열거나 읽다가 실패하면 경고를
    남기고 None — 그 링크만 NEW 를 판정하지 않고 나머지는 그대로 그린다.

    ``MAX(posted_at)`` 을 SQL 로 구하지 않는다. 문자열 비교라 오프셋이 섞이면
    (``+00:00`` 과 ``+09:00``) 더 늦은 시각을 못 고른다. 값을 파싱해 비교한다.
    ``now`` 는 먼 미래 값을 거르는 기준이다(없으면 지금)."""
    p = Path(db_path)
    if not p.is_file():
        return None
    values: list[str] = []
    conn = None
    try:
        # sqlite 는 파일이 DB 가 아니어도 connect 에선 안 던지고 첫 질의에서 던진다 —
        # 열기와 읽기를 한 분기로 받는다.
        conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            cols = {r[1] for r in conn.execute(
                f"PRAGMA table_info({_quote_ident(t)})")}
            if "posted_at" not in cols:
                continue
            values.extend(v for (v,) in conn.execute(
                f"SELECT DISTINCT posted_at FROM {_quote_ident(t)} "
                "WHERE posted_at IS NOT NULL AND posted_at != ''"))
    except sqlite3.Error as exc:
        log.warning("NEW 판정: %s 을(를) 읽지 못해 이 링크는 NEW 를 판정하지 않습니다 "
                    "(%s: %s)", p.name, type(exc).__name__, exc)
        return None
    finally:
        if conn is not None:
            conn.close()
    return _latest(values, now, p.name)


def latest_report_ts(*, now: datetime | None = None) -> datetime | None:
    """AI 보고서 아카이브에 마지막으로 보고서가 쌓인 시각 — 색인 페이지가 읽는 그
    jsonl(``report_archive.load_runs``)에서 구한다. 레코드는 유료 AI 보고서
    (company_report·period_report 의 ``render_llm``)가 성공할 때만 쌓인다 — 대시보드의
    유료 보고서 요청(기간 보고서는 채널 발송도)이나 CLI ``--llm`` 이고, 이걸 부르는 타이머는 없다."""
    from trade import report_archive
    return _latest((rec.get("ts") for rec in report_archive.load_runs()
                    if isinstance(rec, dict)),      # load_runs 는 JSON 이기만 하면 싣는다
                   now, "AI 보고서 아카이브")


def latest_for(source: str | None, data_dir: Path | str, *,
               now: datetime | None = None) -> datetime | None:
    """링크의 NEW 판정 원천 → 마지막 게시 시각.

    ``"db:<파일>"`` = ``data_dir`` 아래 그 DB 의 posted_at 최댓값,
    ``"report_archive"`` = AI 보고서 아카이브 색인의 ts 최댓값,
    ``None`` = 판정하지 않는다(게시 시각이 없는 페이지). 모르는 값은 오타이므로 던진다."""
    if source is None:
        return None
    if source == "report_archive":
        return latest_report_ts(now=now)
    if source.startswith("db:") and len(source) > 3:
        return latest_posted_at(Path(data_dir) / source[3:], now=now)
    raise ValueError(f"모르는 NEW 판정 원천: {source!r}")


def is_new(ts: datetime | None, now: datetime, *, days: int | None = None) -> bool:
    """게시일이 오늘로부터 ``days``(기본 ``NEW_DAYS``) 일 안인가(KST 달력일, 게시일 = 1일째).

    ``now`` 에 시간대가 없으면 UTC 로 본다 — 서버의 로컬 시간대에 기대지 않는다.
    게시 시각이 조금 미래여도(텔레그램 서버와 이 호스트의 시계 차이) 새 글로 본다 —
    먼 미래 값은 읽는 쪽(``latest_*``)이 ``FUTURE_SLACK`` 으로 먼저 거른다."""
    if ts is None:
        return False
    days = NEW_DAYS if days is None else days
    return (_aware(now).astimezone(KST).date() - ts.astimezone(KST).date()).days < days


def badge_html(ts: datetime | None, now: datetime, *, days: int | None = None) -> str:
    """NEW 배지 조각 — 새 게시가 창 안이면 앞에 공백을 둔 ``<span>``, 아니면 빈 문자열.
    배지에 마우스를 올리면 마지막 게시 시각(KST)이 보인다. ``days`` 기본값은 호출
    시점의 ``NEW_DAYS`` 다(가이드 문구도 같은 값을 읽는다)."""
    days = NEW_DAYS if days is None else days
    if not is_new(ts, now, days=days):
        return ""
    when = ts.astimezone(KST).strftime("%Y-%m-%d %H:%M")
    title = f"새 데이터 게시 {when} KST · 게시일 포함 {days}일간 표시"
    return f' <span class="link-new" title="{html.escape(title)}">NEW</span>'
