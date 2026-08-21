"""중국 수입 데이터 (나쁜양파, **종목별**) — 레지스트리 어댑터.

사용자 2026-08-21: "중국수입 7월 기업도 있어. 수출건이랑 똑같이 하면 돼."

캡션 예시(사용자 첨부 스크린샷):
    SQM (SQM)
    중국 수입
    26년 7월 Update

    단가 YoY: +97.0%
    수입액 YoY: +280.6%
    3M 수입액 YoY: +238.9%

    동시상관: 0.91
    방향 일치율: 88%

    - 중국 양극재·배터리 체인의 재고 보충이 이어지면서 리튬 판매 회복을
      수요 측에서 확인하는 근거가 강화됐습니다.

⚠️ **수출과 방향이 반대다.** 중국이 사는 쪽이고 티커는 파는 회사다(SQM =
칠레 리튬). 그래서 라벨이 `수입액` 이고, 화면 문구도 "중국에서 수입하는"
이 아니라 방향을 그대로 적는다 — 여기를 복사만 하면 수출 문구가 남아
화면이 스스로 거짓말한다(실수 #55).

⚠️ 중국 **품목(HS) 기준 수입** 페이지는 아직 없다. 형제 링크를 억지로
걸면 404 가 되므로 `sibling=""` 로 둔다 — 생기면 그때 한 줄 채운다.

문법·저장·렌더는 `cn_stock_flow` 공용 엔진이 갖는다(실수 #38).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from trade import cn_stock_flow as _f

FLOW = _f.Flow(key="import", marker="수입", amount="수입액",
               table="cn_stock_imports",
               title="🧧 중국 수입 데이터(종목별)",
               sibling="")


def parse_cn_stock_import(caption: str) -> dict | None:
    return _f.parse(caption, FLOW)


def open_cn_stock_import_db(path: str | Path) -> sqlite3.Connection:
    return _f.open_db(path, FLOW)


def list_cn_stock_import(conn: sqlite3.Connection) -> list[dict]:
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
