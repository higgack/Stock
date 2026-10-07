"""미국 수출 데이터 (나쁜양파, **종목별**) — 레지스트리 어댑터.

사용자 2026-10-08: "수출입대시보드에 미국수출종목은 없는데 나쁜 양파에 이것도 떴어.
이것도 대시보드만들어주고 이틀정도만 백필하면 될거야."

캡션 예시(사용자 첨부 스크린샷 — 2026-10-07 23:42 채널 게시분을 손으로 옮긴 것):
    🇺🇸 8월 수출 미국

    ▶️ Everpure, Inc. — FlashBlade 데이터 저장시스템, FlashArray 올플래시 스토리지

    26년08월: $489.9M  (+173.9% YoY)  (-7.0% MoM)

    최근 추이 (단위: USD M$)
    26년07월: $526.7M  (+145.8% YoY)  (+7.4% MoM)
    26년06월: $490.4M  (+166.7% YoY)  (+48.1% MoM)
    #P
    🔗 맵핑에서 보기

⚠️ 한국 회사별 금액판과 **같은 문법**이다 — 마커 어순(`N월 수출 <나라>`) ·
▶️ `회사 — 품목` · 금액/YoY/MoM. 복제하지 않고 엔진(`kr_company_flow`)을 나라만
바꿔 쓴다(#84·#330). 그동안 이 캡션을 받는 파서가 없어 관련성 필터에서 **조용히**
드랍됐다 — #83·#261·#330·#332·#370 에 이은 같은 계열이다(실수 #441).

⚠️ 카드 제목 딥링크는 캡션의 해시태그(`#P`)가 밝힌 심볼로 건다(`link="hashtag"`).
회사명을 KRX 목록에 대조하는 한국 규칙은 미국 회사에 쓸 수 없다(#34·#400).
Everpure 는 Pure Storage 가 이름을 바꾼 회사이고(2026-02-23 리브랜드 발표) NYSE
심볼이 PSTG → P 로 바뀌었다(2026-04-17 거래분부터, 회사 보도자료) — 캡션의 `#P`
와 일치한다.

⚠️ 미국 **품목(HS) 기준 수출** 페이지는 없다. 형제 링크는 같은 나라의 미국 수입
(품목) 페이지로 건다(kri 가 한국 수출 페이지로 거는 것과 같은 규율).

금액 단위는 원천이 적은 `USD M$` 를 그대로 쓴다 — 우리가 환산하지 않는다(#165).
⚠️ 픽스처는 **스크린샷 재구성**이다 — 배포 뒤 첫 실물 캡션으로 대조할 것(#155·#334).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from trade import kr_company_flow as _f

FLOW = _f.Flow(key="export", marker="수출", amount="수출액",
               table="us_stock_exports",
               title="🦅 미국 수출 데이터(종목별)",
               country="미국",
               unit="종목",
               link="hashtag",
               sibling="us.html",
               sibling_label="미국 수입 데이터(나쁜양파)")


def parse_us_stock_export(caption: str) -> dict | None:
    return _f.parse(caption, FLOW)


def open_us_stock_db(path: str | Path) -> sqlite3.Connection:
    return _f.open_db(path, FLOW)


def list_us_stock(conn: sqlite3.Connection) -> list[dict]:
    return _f.list_latest(conn, FLOW)


def history(conn: sqlite3.Connection, company: str) -> list[dict]:
    return _f.history(conn, FLOW, company)


def ingest(conn: sqlite3.Connection, caption: str, *, source_message_id=None,
           posted_at: str = "", media_paths: list[str] | None = None) -> bool:
    return _f.ingest(conn, FLOW, caption, source_message_id=source_message_id,
                     posted_at=posted_at, media_paths=media_paths)


def render_html(conn: sqlite3.Connection, *,
                media_url_prefix: str = "../") -> str:
    return _f.render_html(conn, FLOW, media_url_prefix=media_url_prefix)


def regenerate(db_path: Path | str, out_path: Path | str, *,
               media_url_prefix: str = "../") -> None:
    _f.regenerate(db_path, out_path, FLOW, media_url_prefix=media_url_prefix)
