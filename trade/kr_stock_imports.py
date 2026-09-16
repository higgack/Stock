"""한국 수입 데이터 (나쁜양파, **회사별**) — 레지스트리 어댑터.

사용자 2026-09-16: "대시보드에 한국수입 회사별은 없는것 같은데 만들어줘.
현재는 8월부터 텔레칩스 하나야."

캡션 예시(사용자 첨부 스크린샷):
    **🇰🇷 8월 수입 한국**

    **▶️ 텔레칩스 — 차량용 AP·프로세서**

    **26년08월: $2,175.2M  (+49.4% YoY)  (+7.3% MoM)**

    최근 추이 (단위: USD M$)
    26년07월: $2,027.2M  (+16.0% YoY)  (-3.8% MoM)
    26년06월: $2,108.1M  (+39.2% YoY)  (+31.4% MoM)

⚠️ **방향을 그대로 적는다** — 한국이 사는 쪽이고 ▶️ 의 회사는 그 품목과
엮인 국내 종목이다. 수출 문구를 복사만 하면 화면이 스스로 거짓말한다(#55).

⚠️ 한국 **품목(HS) 기준 수입** 페이지는 없다. 형제 링크를 억지로 걸면
404 이므로 `sibling=""` 로 둔다(cn_stock_imports 와 같은 규율).

⚠️ 수출분은 여기로 오지 않는다 — 사용자 결정(2026-09-16)으로 기존
`한국 수출 데이터(종목별)` 페이지(`kr_stock_exports`)에 합친다. 문법만
`kr_company_flow` 가 공유한다(#38·#84).

금액 단위는 원천이 적은 `USD M$` 를 그대로 쓴다 — 우리가 환산하지 않는다(#165).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from trade import kr_company_flow as _f

FLOW = _f.Flow(key="import", marker="수입", amount="수입액",
               table="kr_stock_imports",
               title="🏢 한국 수입 데이터(회사별)",
               country="한국",
               sibling="kr_stock.html",
               sibling_label="한국 수출 데이터(종목별)")


def parse_kr_stock_import(caption: str) -> dict | None:
    return _f.parse(caption, FLOW)


def open_kr_stock_import_db(path: str | Path) -> sqlite3.Connection:
    return _f.open_db(path, FLOW)


def list_kr_stock_import(conn: sqlite3.Connection) -> list[dict]:
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
