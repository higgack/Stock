"""지인 공유용 산업트렌드 단일 파일 export.

현재 저장된 산업트렌드(예: 2026-04 확정) + 저장된 🔍 추가신호 카드를 깨끗한
self-contained HTML 한 장으로 출력한다. 서버·인터넷 없이 어디서나 열리고,
받는 사람에게 깨지는 링크가 없다(동결 아카이브 페이지와 달리 back-link 제거 +
제목/출처/단위/생성일 한 줄 포함). LLM 호출 0 — 저장된 카드를 그대로 재사용.

    .venv/bin/python -m trade.scripts.export_industry [--out PATH]
    # → ~/trade_산업트렌드_export.html  (이 파일만 보내면 끝)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from trade import customs, industry_archive, insights

load_dotenv()

_CARDS_KEY = "llm_insight_cards"   # refresh_signals가 저장하는 pipeline_state 키


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=Path.home() / "trade_산업트렌드_export.html",
                    help="출력 HTML 경로(기본 ~/trade_산업트렌드_export.html)")
    args = ap.parse_args(argv)

    with customs.session() as conn:
        try:
            cards = json.loads(insights.get_state(conn, _CARDS_KEY) or "[]")
        except Exception:
            cards = []
        html = industry_archive.render_export(
            conn, cards if isinstance(cards, list) else [])

    if not html:
        print("저장된 산업트렌드 데이터가 없습니다 — 먼저 "
              "`industry_report --store`(또는 refresh_signals)를 실행하세요.")
        return 1
    args.out.write_text(html, encoding="utf-8")
    kb = len(html.encode("utf-8")) / 1024
    print(f"wrote {args.out} ({kb:.0f} KB) — 이 파일 하나만 지인에게 보내면 됩니다.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
