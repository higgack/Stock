"""중국 수출 데이터 (나쁜양파, **종목별**) — 레지스트리 어댑터.

`cn_exports.py` 는 같은 채널의 **품목(HS) 기준** 중국 수출이고, 이 모듈은
**회사(종목) 기준**이다. PK 가 `(ticker, month)` 라 스키마가 다르다.

발견 경위(사용자 2026-08-21): 26년 7월 중국 수출(기업별)이 채널에 떴는데
대시보드에 안 올라왔다. 품목 파서의 마커는 `N월 수출 중국` 인데 이 포맷은
`중국 수출`(어순이 반대)이라 **관련성 필터를 통과 못 하고 조용히 드랍**
됐다 — 저장도 안 되고 미매칭 알림에도 안 잡히는 유실. 일본 2026-08-16,
말레이시아 2026-08-20 과 **같은 사고**의 네 번째다.

캡션 예시(사용자 첨부 스크린샷 기반 — 렌더된 화면이라 원문 마크다운은
미확인. 형제 모듈과 같은 방침으로 포맷을 단정하지 않고 **관용 파싱**):
    TTM Technologies (TTMI)
    중국 수출
    26년 7월 Update

    단가 YoY: +45.2%
    수출액 YoY: +55.8%
    3M 수출액 YoY: +84.7%

    동시상관: 0.74
    방향 일치율: 87%
    선행상관: 0.85
    선행 방향 일치율: 93%

    - AI 서버·네트워크용 고다층 PCB 수요와 신규 생산능력 램프가 겹치며
      데이터센터 제품 믹스가 개선되는 구간입니다.

⚠️ 티커는 **국적 무관**이다 — 중국에서 수출하는 기업을 다루므로 TTMI·DELL
처럼 미국 상장이 섞여 온다(형제 종목판과 같은 성질).

문법·저장·렌더는 `cn_stock_flow` 가 갖는다 — 같은 날 중국 **수입**(기업별)
요청이 들어와, 마커 한 단어만 다른 두 소스를 복제하면 규약이 갈라진다
(실수 #38). 이 모듈은 방향 정체성(`FLOW`)과 레지스트리 계약 함수만 갖는다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from trade import cn_stock_flow as _f

FLOW = _f.Flow(key="export", marker="수출", amount="수출액",
               table="cn_stock_exports",
               title="🏮 중국 수출 데이터(종목별)",
               sibling="cn.html", sibling_label="중국 수출 데이터(품목)")


def parse_cn_stock_export(caption: str) -> dict | None:
    return _f.parse(caption, FLOW)


def open_cn_stock_db(path: str | Path) -> sqlite3.Connection:
    return _f.open_db(path, FLOW)


def list_cn_stock(conn: sqlite3.Connection) -> list[dict]:
    return _f.list_latest(conn, FLOW)


def history(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    return _f.history(conn, FLOW, ticker)


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
