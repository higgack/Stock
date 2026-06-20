"""연계표(hsk_mti.tsv) ↔ 큐레이션 키 정합 가드 — 월례 점검(사용자 2026-06-20).

무역협회 HSK-MTI 연계표가 갱신되며 MTI품목명/코드가 바뀌면, 그 이름으로 키잉된
우리 큐레이션이 customs 노드와 안 맞아 **조용히 빠질(silent drop)** 수 있다. 이
스크립트는 연계표(카탈로그)에 없는 큐레이션 키를 찾아 운영자에게 DM 한다.

점검 3종:
  1) reinforce_approved.csv 품목키  → 카탈로그 MTI품목명(node["name"])에 존재?
     (없으면 build_rows 의 _reinforce_for(node["name"]) 가 영영 매칭 안 됨)
  2) _THEME_MTI_PIN 값(MTI6)         → 카탈로그 MTI6 에 존재?
  3) _THEME_ROWS HS                  → hs6_to_mti6 로 1개 이상 MTI6 해석?(핀 있으면 면제)

+ 연계표 보유 버전(hsk_mti.tsv 헤더) 리마인더 — KITA 신규본 확인 넛지(Claude 세션서
웹검색 요청). 월 1회(~18일) 타이머. 고아 0 + (옵션)무알림 시 조용히 종료 가능.

실행:
    .venv/bin/python -m trade.scripts.catalog_guard            # 점검 + (고아/리마인더) DM
    .venv/bin/python -m trade.scripts.catalog_guard --check-only  # 출력만(DM 안 함)
    .venv/bin/python -m trade.scripts.catalog_guard --quiet-if-clean  # 고아 없으면 무음
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("catalog-guard")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def _catalog_version() -> str:
    """hsk_mti.tsv 헤더의 source 파일명에서 버전 추출(없으면 '미상')."""
    try:
        from trade import mti_map
        for ln in Path(mti_map.DEFAULT_PATH).read_text(encoding="utf-8").splitlines()[:5]:
            m = re.search(r"(20\d{2}_MTIHSK[^\s]*\.xlsx|v[A-Za-z]*_?\d{6})", ln)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "미상"


def scan() -> dict:
    """카탈로그 대조 결과 dict — 순수(파일 읽기). graceful(카탈로그 부재 → 빈 결과)."""
    from trade import mti_companies as mc, mti_map
    try:
        names = mti_map.mti_names()                  # {mti6: (품목명, 산업)}
    except Exception as exc:
        log.warning("카탈로그 로드 실패 — skip: %s", exc)
        return {}
    valid_names = {_norm(nm) for nm, _ind in names.values()}
    valid_mti6 = set(names)

    # 1) reinforce 키
    mc._REINFORCE_APPROVED_CACHE = None
    reinforce = mc.load_reinforce_approved()          # {정규화품목키: [회사]}
    reinforce_orphan = sorted(k for k in reinforce if _norm(k) not in valid_names)

    # 2) _THEME_MTI_PIN 값(MTI6)
    pin_orphan = sorted({m6 for vals in mc._THEME_MTI_PIN.values()
                         for m6 in vals if m6 not in valid_mti6})

    # 3) _THEME_ROWS HS → MTI6 해석(핀 있으면 면제)
    theme_orphan = []
    for nm, _cat, hsk, _cos in mc._THEME_ROWS:
        if nm in mc._THEME_MTI_PIN:
            continue                                  # 핀으로 명시 → 면제
        if not any(mti_map.hs6_to_mti6(h) for h in mc._hs_list(hsk)):
            theme_orphan.append(nm)

    return {"version": _catalog_version(),
            "reinforce_orphan": reinforce_orphan,
            "pin_orphan": pin_orphan,
            "theme_orphan": sorted(theme_orphan),
            "n_reinforce": len(reinforce), "n_mti": len(valid_mti6)}


def _build_message(r: dict) -> str:
    ro, po, to = r["reinforce_orphan"], r["pin_orphan"], r["theme_orphan"]
    n_orphan = len(ro) + len(po) + len(to)
    head = (f"📋 <b>연계표 정합 점검</b> (월례)\n"
            f"보유 연계표: <b>{r['version']}</b> · MTI품목 {r['n_mti']}개 · "
            f"reinforce {r['n_reinforce']}품목\n"
            f"신규 연계표 확인: KITA stat.kita.net 자료실 — Claude 세션서 "
            f"'연계표 웹확인' 요청(꼼꼼한 웹검색).")
    if not n_orphan:
        return head + "\n\n✅ 카탈로그에 없는 큐레이션 키 0 — 정합 OK."
    body = [f"\n\n⚠️ <b>카탈로그에 없는 키 {n_orphan}개</b> (연계표 갱신 시 silent drop 위험):"]
    if ro:
        body.append("• reinforce 품목키(→customs 카드 미부착): " + ", ".join(ro[:30])
                    + (f" 외 {len(ro)-30}" if len(ro) > 30 else ""))
    if po:
        body.append("• _THEME_MTI_PIN MTI6(존재X): " + ", ".join(po))
    if to:
        body.append("• _THEME_ROWS HS 미해석 테마: " + ", ".join(to[:20])
                    + (f" 외 {len(to)-20}" if len(to) > 20 else ""))
    body.append("→ 연계표가 바뀐 거면 키(MTI품목명)를 신규 카탈로그명으로 교정 요청.")
    return head + "\n".join(body)


def _notify(text: str) -> None:
    """운영자 DM(resolve_check 동일 경로). 토큰/대상 없으면 silent skip."""
    token = os.environ.get("TRADE_BOT_TOKEN")
    try:
        from trade import operator
        chat_id = operator.get()
    except Exception:
        chat_id = None
    if not token or not chat_id:
        log.info("notify skipped: no TRADE_BOT_TOKEN / operator chat_id")
        return
    try:
        subprocess.run(
            ["curl", "-s", "-m", "10", "-X", "POST",
             f"https://api.telegram.org/bot{token}/sendMessage",
             "--data-urlencode", f"chat_id={chat_id}",
             "--data-urlencode", f"text={text}",
             "--data-urlencode", "parse_mode=HTML"],
            timeout=15, check=False, capture_output=True,
        )
    except Exception as e:
        log.warning("notify failed: %s", e)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true", help="출력만, DM 안 함")
    ap.add_argument("--quiet-if-clean", action="store_true",
                    help="고아 0 이면 무음(DM 안 함) — 타이머 스팸 방지용 선택지")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    r = scan()
    if not r:
        return 0
    msg = _build_message(r)
    n_orphan = len(r["reinforce_orphan"]) + len(r["pin_orphan"]) + len(r["theme_orphan"])
    log.info("연계표 %s · 고아 reinforce=%d pin=%d theme=%d",
             r["version"], len(r["reinforce_orphan"]),
             len(r["pin_orphan"]), len(r["theme_orphan"]))
    print(msg)
    if args.check_only:
        return 0
    if args.quiet_if_clean and n_orphan == 0:
        log.info("clean + --quiet-if-clean → 무음")
        return 0
    _notify(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
