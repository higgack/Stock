"""Read-only 캡션 덤프 — 나쁜양파(Badonions) 채널.

새 소스를 붙일 때 "파서가 실제 캡션과 맞는가"를 **추측 대신 실측**으로
확인하기 위한 도구. 2026-08-16 한국 수출(종목별)을 추가하며, 파서를
스크린샷의 *렌더된* 텍스트만 보고 만든 탓에 실제 캡션과 어긋났는지
확인할 방법이 없어서 만들었다(diagnose_dedup.py 의 BeOn 판을 미러).

각 메시지마다:
  - msg.id / date / grouped_id(앨범)
  - raw_text 와 text 를 **둘 다** repr() 로 전량 출력
      · raw_text = 서버 원문(= Message.message)
      · text     = Telethon 이 entities 로 마크다운을 되붙인 문자열
    파서는 text 를 받으므로(listen/backfill 이 `m.text or ""`) 둘이
    다르면 그 차이가 곧 파싱 실패 원인이다. `.caption` 속성은 없다.
  - 나쁜양파 관련성 필터(_is_relevant) 통과 여부
  - parse_kr_stock_export() 반환값
  substring 히트와 파서 히트가 갈리는 지점이 정규식이 깨지는 지점이다.

Read-only 계약 — 아래를 **호출하지 않는다**:
  - backfill_badonion.run() / _forward_unit() / client.forward_messages()
  - backfill_badonion._notify()  (curl 로 텔레그램 sendMessage POST)
  - client.send_read_acknowledge()  (읽음 처리 = 채널 상태 변경)
  - dest(전송 목적지) entity 생성 자체를 하지 않는다
쓰는 것은 client.get_entity / client.iter_messages 뿐.

세션 파일(.badonion-session)은 6시간 주기 백필 타이머가 쓰는 SQLite 라,
이 스크립트는 **임시 복사본**을 만들어 접속한다 — 백필 dry-run 과 **같은
함수**다(`backfill_badonion._dry_run_session` · `tg_entities.start_client`,
#38). 원본을 건드리지 않으므로 타이머를 멈출 필요가 없고, 진단 때문에 운영
백필이 `database is locked` 로 죽지도 않는다. auth key 가 복사되므로 인증된
세션이면 재로그인도 불필요하다. 인증이 풀린 세션이면 **로그인하지 않고
멈춘다** — 복사본에 로그인하면 실행 끝에 임시 디렉터리와 함께 지워진다(#403
3차 리뷰). 원본이 없거나 복사가 실패하면 라이브 경로로 접속하고 그렇다고
말한다(이때는 잠금 경합을 못 피한다). 세션 경로가 **cwd 상대**라 반드시
리포 루트에서 실행할 것. (리스너는 .badonion-listener-session 으로 파일
자체가 다르다.)

Usage on host:
    cd ~/stock-trade
    .backfill-venv/bin/python -m trade.scripts.diagnose_badonion --grep 한국
    .backfill-venv/bin/python -m trade.scripts.diagnose_badonion \
        --since 2026-07-01 --grep 한국 --limit 500
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from telethon import TelegramClient

from trade.kr_stock_exports import parse_kr_stock_export
from trade.scripts.backfill_badonion import (
    API_HASH,
    API_ID,
    SESSION_PATH,
    SOURCE_USERNAME,
    _dry_run_session,
    _group_by_album,
    _is_relevant,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("diagnose-badonion")

_SEP = "─" * 78


def _matches(msg, grep: str | None) -> bool:
    """⚠️ **자르지 않은 전체 텍스트**에 대해 검색한다 — diagnose_dedup 는
    `(msg.text or "")[:200]` 로 자른 뒤 grep 해서 200자 뒤 히트를 놓친다."""
    if not grep:
        return True
    hay = ((msg.raw_text or "") + "\n" + (msg.text or "")).lower()
    return grep.lower() in hay


def _dump(msg) -> None:
    """한 메시지의 원문·파서 결과 출력."""
    raw = msg.raw_text or ""
    txt = msg.text or ""
    print(_SEP)
    print(f"msg.id={msg.id}  date={msg.date.isoformat()}  "
          f"grouped_id={getattr(msg, 'grouped_id', None)}")
    print(f"  relevant(나쁜양파 필터) : {_is_relevant(txt)}")
    parsed = parse_kr_stock_export(txt)
    print(f"  parse_kr_stock_export   : {parsed}")
    if raw == txt:
        print(f"  raw_text == text        : {raw!r}")
    else:
        # 둘이 다르면 마크다운 재조립이 파싱을 깨는 케이스다.
        print(f"  raw_text                : {raw!r}")
        print(f"  text (파서 입력)         : {txt!r}")


async def run(since: datetime, until: datetime | None, grep: str | None,
              limit: int, sample: int, tmpdir: str) -> int:
    from trade.tg_entities import guarded_client, start_client
    session = _dry_run_session(tmpdir)
    # 복사본이면 형식이 올라가도 무해하다 — 못 읽는 형식만 처방과 함께 막는다.
    # 원본이 없거나 복사가 실패하면 라이브 경로라 운영 세션으로 잰다(#404).
    client = guarded_client(TelegramClient, session, API_ID, API_HASH,
                            live=(session == SESSION_PATH))
    try:
        await start_client(client, copy=(session != SESSION_PATH))
        log.info("Telethon session ready (read-only)")
        from telethon import utils as _tutils

        from trade.tg_entities import resolve_peer   # ResolveUsername 절약(#258)
        source = await resolve_peer(client, SOURCE_USERNAME)   # 조회만
        log.info("source(marked)=%s since=%s until=%s grep=%r limit=%d",
                 _tutils.get_peer_id(source), since.date(),
                 until.date() if until else None, grep, limit)

        msgs = []
        # reverse=True → offset_date 가 하한(오름차순). 앨범 그룹핑이
        # 시간순 인접을 전제하므로 이 순서를 유지해야 한다.
        async for m in client.iter_messages(source, offset_date=since,
                                            reverse=True, limit=limit):
            if until is not None and m.date > until:
                break
            msgs.append(m)
        log.info("scanned %d messages", len(msgs))

        # 앨범은 멤버 중 아무나 캡션을 가질 수 있다(코드도 any() 로 검사)
        # → 유닛 전체를 출력한다.
        printed = 0
        for unit in _group_by_album(msgs):
            if printed >= sample:
                break
            if not any(_matches(m, grep) for m in unit):
                continue
            if len(unit) > 1:
                print(f"\n=== 앨범 유닛 ({len(unit)}개 멤버) ===")
            for m in unit:          # 유닛 전체 — 어느 멤버가 캡션인지 모름
                _dump(m)
            printed += 1

        print("=" * 78)
        log.info("printed %d units (sample cap %d)", printed, sample)
        return 0
    finally:
        await client.disconnect()


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Read-only 나쁜양파 캡션 덤프 — 파서 실측용(전송 없음)")
    ap.add_argument("--since", default=None,
                    help="YYYY-MM-DD (UTC). 기본: 오늘 −--days")
    ap.add_argument("--days", type=int, default=7,
                    help="--since 미지정 시 되돌아볼 일수 (기본 7)")
    ap.add_argument("--to", default=None, help="YYYY-MM-DD (UTC) 상한")
    ap.add_argument("--grep", default="한국",
                    help="이 문자열을 포함한 메시지만 (대소문자 무시, "
                         "기본 '한국'). 전체를 보려면 --grep ''")
    ap.add_argument("--limit", type=int, default=1000,
                    help="순회할 최대 메시지 수 (기본 1000)")
    ap.add_argument("--sample", type=int, default=20,
                    help="출력할 최대 유닛 수 (기본 20)")
    args = ap.parse_args()
    since = (_parse_date(args.since) if args.since
             else datetime.now(timezone.utc) - timedelta(days=args.days))
    # --to 는 그 날을 포함하도록 하루 더한다(백필의 exclusive 동작과 다름).
    until = (_parse_date(args.to) + timedelta(days=1)) if args.to else None
    from trade.tg_entities import SessionFormatError, SessionNotAuthorizedError
    with tempfile.TemporaryDirectory() as tmp:
        try:
            rc = asyncio.run(run(since, until, args.grep or None,
                                 args.limit, args.sample, tmp))
        except (SessionFormatError, SessionNotAuthorizedError) as exc:
            # 처방을 담은 문장이다 — 트레이스백에 묻히면 VM 에서 무엇을 깔아야
            # 하는지 안 보인다(#404 · #82).
            log.error("시작 실패 — %s", exc)
            rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
