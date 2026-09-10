"""대만 수출 데이터 (나쁜양파, **종목별**) — 레지스트리 어댑터.

`tw_exports.py` 는 같은 채널의 **품목(HS) 기준** 대만 수출(마커 `N월 수출
대만`)이고, 이 모듈은 **회사(종목) 기준**(마커 `대만 수출`)이다.

발견 경위(사용자 2026-09-10): 26년 8월 대만 수출(기업별)이 채널에 떴는데
포워드도 대시보드도 안 됐다. `badonion_sources.is_relevant` 는 레지스트리
파서 중 하나라도 캡션을 받아야 통과인데 대만 종목판 파서가 **없었다** —
저장도 미매칭 알림도 없는 조용한 유실. 일본 2026-08-16 · 말레이시아 08-20 ·
중국 08-21 에 이은 **다섯 번째**이고, #83 이 "새 나라의 종목판이 뜨면 또
난다"고 예측한 그대로다.

캡션 예시(사용자 첨부 스크린샷 4장 기반 — 렌더된 화면이라 원문 마크다운은
미확인, 형제와 같은 방침으로 **관용 파싱**):
    ASE Technology Holding (ASX)
    대만 수출
    26년 8월 Update

    단가 YoY: +23.2%
    수출액 YoY: +33.1%
    3M 수출액 YoY: +33.1%

    선행상관: 0.79
    선행 방향 일치율: 76%

    badonion.co.kr

관측된 변주: `동시상관/방향 일치율` 만 오는 카드(DIOD·MXL), `3M 수출액`
이 없는 카드(MXL), 꼬리 URL 이 `badonion.co.kr` 또는 `https://badonion.co.kr/
trade/mapping`. 지표는 `badonion_metrics` 가 계열을 가른다(#262).

⚠️ 티커는 **국적 무관**이다 — 대만에서 수출하는 기업을 다루므로 ASX·DIOD·
PTON·MXL 처럼 미국 상장이 온다(형제 종목판과 같은 성질).

문법·저장·렌더는 `cn_stock_flow` 엔진이 갖는다(나라는 `Flow.country`).
이 모듈은 정체성(`FLOW`)과 레지스트리 계약 함수만 갖는다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from trade import cn_stock_flow as _f

FLOW = _f.Flow(key="export", marker="수출", amount="수출액",
               table="tw_stock_exports",
               title="🧋 대만 수출 데이터(종목별)",
               sibling="tw.html", sibling_label="대만 수출 데이터(품목)",
               country="대만")


def parse_tw_stock_export(caption: str) -> dict | None:
    return _f.parse(caption, FLOW)


def open_tw_stock_db(path: str | Path) -> sqlite3.Connection:
    return _f.open_db(path, FLOW)


def list_tw_stock(conn: sqlite3.Connection) -> list[dict]:
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
