"""큐레이션 후보 알림 — 관세청 갱신일(1·11·15·21일) 18:00 KST 텔레그램.

목적(사용자 2026-06-15): 산업트렌드 품목 카드의 '관련기업' 은 수동 큐레이션
(mti_companies._MAP) + 채널 조인 두 출처인데, **둘 다 없는 미매핑 품목**은
관련기업이 안 뜬다. 관세청에서 자주/크게 뜨는데 미매핑인 품목을 주기적으로
뽑아 알려주면, 그걸 보고 관련 상장사 큐레이션을 추가할 수 있다.

원칙:
  - **재스캔 0·LLM 0** — 저장된 by_mti(industry.load_mti_stored)만 재사용
    (관세청 fetch 타이머가 이미 적재). 최신월 수출액 기준 랭킹.
  - 미매핑 = companies_for(수동) **그리고** channel_companies_for(채널) 둘 다 빈
    품목만. 이미 매핑된 건 제외.
  - 저빈도 노이즈 컷(--min-eok, 기본 5억$) + 상위 N(--top, 기본 12)만.
  - 후보 0 이면 **무음**(스팸 방지). operator DM 으로 발송(_send_alert 재사용).

run: .venv/bin/python -m trade.scripts.curation_candidates [--top N] [--min-eok F]
타이머: trade-bot-curation.timer (OnCalendar=*-*-01,11,15,21 18:00 Asia/Seoul).
"""
from __future__ import annotations

import argparse
import logging
import sys

from trade import customs, industry, mti_companies

log = logging.getLogger("curation-candidates")

_TOP_DEFAULT = 12
_MIN_EOK_DEFAULT = 5.0   # 최신월 수출 5억$ 미만 품목은 제외(노이즈 컷)


def find_candidates(by_mti: dict, pairs: list, *, top: int,
                    min_usd: float) -> list[tuple[str, str, float]]:
    """저장된 by_mti → 미매핑(수동·채널 둘 다 없음) 고수출 품목 [(name, industry,
    최신월 수출 USD)], 수출액 내림차순 top N. 순수(단위테스트)."""
    out: list[tuple[str, str, float]] = []
    for mti6, node in by_mti.items():
        name = (node.get("name") or mti6).strip()
        months = node.get("months") or {}
        if not months:
            continue
        val = months[max(months)] or 0          # 최신월 수출액(USD)
        if val < min_usd:
            continue
        if mti_companies.companies_for(name):    # 수동 큐레이션 있음 → 제외
            continue
        if mti_companies.channel_companies_for(name, pairs):  # 채널 매핑 → 제외
            continue
        out.append((name, node.get("industry", ""), float(val)))
    out.sort(key=lambda x: -x[2])
    return out[:top]


def compose(cands: list[tuple[str, str, float]],
            suggestions: dict | None = None) -> str | None:
    """후보 → 텔레그램 HTML. 후보 0 이면 None(무음). suggestions = {품목명: [회사…]}
    (G2 — DART 인벤토리 매칭 추천, 사용자 2026-06-17)."""
    if not cands:
        return None
    suggestions = suggestions or {}
    lines = [f"🏷️ <b>큐레이션 후보 {len(cands)}건</b> (미매핑 고수출 품목)",
             "큐레이션·채널 둘 다 없어 관련기업이 안 뜨는 품목 — 최신월 수출액순. "
             "관련 상장사 매핑 후보로 검토.", ""]
    for i, (name, ind, val) in enumerate(cands, 1):
        suffix = f" · {ind}" if ind else ""
        line = f"{i}. <b>{name}</b>{suffix} — {industry._eokusd(val)}"
        sug = suggestions.get(name)
        if sug:                       # G2: DART 매출구성 매칭 회사 추천
            line += f"\n    🏢 DART 추천: {', '.join(sug[:5])}"
        lines.append(line)
    lines += ["", "→ mti_companies.py _MAP 에 (키워드, 기업) 추가 시 산업트렌드 "
              "카드 '관련기업(큐레이션)'에 노출. 🏢 = DART 매출구성 자동 추천(검토 후 승인)."]
    return "\n".join(lines)


def compose_reinforce(reinforce: dict, top: int = 12) -> str | None:
    """보강 후보 → 텔레그램 HTML 요약(후보수 상위 top 품목). 0이면 None(무음).
    전체 목록은 레퍼런스북 '🧬 DART 보강 후보' 패널. 순수."""
    if not reinforce:
        return None
    items = sorted(reinforce.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    total = sum(len(v) for v in reinforce.values())
    lines = [f"🧬 <b>DART 보강 후보 {total}건</b> ({len(reinforce)}품목, 상위 {min(top,len(items))})",
             "이미 매핑된 품목에도 DART 매출구성상 <b>추가로 붙일 수 있는</b> 상장사. "
             "검토 후 승인 추가. 전체는 레퍼런스북 🧬 패널.", ""]
    for name, cos in items[:top]:
        lines.append(f"• <b>{name}</b> — {', '.join(cos[:6])}"
                     + (f" 외 {len(cos)-6}" if len(cos) > 6 else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="큐레이션 후보 알림 (미매핑 고수출 품목)")
    p.add_argument("--top", type=int, default=_TOP_DEFAULT)
    p.add_argument("--min-eok", type=float, default=_MIN_EOK_DEFAULT,
                   help="최신월 수출액 하한(억$). 미만 품목 제외")
    p.add_argument("--dry-run", action="store_true", help="발송 없이 본문만 출력")
    args = p.parse_args(argv)

    with customs.session() as conn:
        by_mti = industry.load_mti_stored(conn)
    if not by_mti:
        log.info("curation: by_mti 미저장 — skip")
        return 0
    pairs = mti_companies.load_channel_pairs()
    cands = find_candidates(by_mti, pairs, top=args.top,
                            min_usd=args.min_eok * 1e8)
    # DART 매출구성 인벤토리(G1) 1회 로드 — G2(미매핑 추천) + 보강(추가 발굴) 공용.
    inv: dict = {}
    try:
        from trade import dart_match, dart_revenue
        inv = dart_revenue.load_inventory()
    except Exception as exc:
        log.warning("curation: DART 인벤토리 skip: %s", exc)
    # G2 (사용자 2026-06-17): 미매핑 품목의 회사 후보 추천.
    suggestions = (dart_match.suggest_companies_for_items(inv, [c[0] for c in cands])
                   if inv else {})
    # 보강 후보 (사용자 2026-06-19): 이미 매핑된 품목 포함 **전 품목**에 DART 매출구성상
    # 추가 상장사 발굴 → 레퍼런스북 '🧬 DART 보강 후보' 패널이 읽는 JSON 으로 적재.
    reinforce: dict = {}
    if inv:
        try:
            from trade import reference_book
            item_current = {
                r["name"]: {mti_companies.canon_company(c).replace(" ", "").lower()
                            for c in (r.get("companies") or [])}
                for r in reference_book.build_rows()}
            reinforce = dart_match.additional_candidates(inv, item_current)
            reference_book.save_reinforce(reinforce)
            log.info("curation: 보강 후보 %d품목 적재", len(reinforce))
        except Exception as exc:
            log.warning("curation: 보강 skip: %s", exc)
    body = compose(cands, suggestions)
    rf_note = compose_reinforce(reinforce)
    if body is None and rf_note is None:
        log.info("curation: 후보 없음 — 무음")
        return 0
    full = "\n\n".join(x for x in (body, rf_note) if x)
    if args.dry_run:
        print(full)
        return 0
    from trade.scripts.scan_customs import _send_alert
    ok = _send_alert(full)
    log.info("curation: %d unmapped + %d reinforce %s",
             len(cands), len(reinforce), "sent" if ok else "send failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
