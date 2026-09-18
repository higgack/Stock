"""KR 네이버 보드 감사 — 급등·급락 / 거래량 상위 / 52주 신고저가 **행을 내나**.

2026-09-18: 네이버 `front-api/domestic/stock/list` 가 우리 요청 모양을 400 으로
거절해 급등·급락과 거래량 상위가 빈 화면이 됐는데, **어떤 감사도 한 마디도 안
했다**. `source_health._naver_domestic` 이 바로 그 주소를 이미 찌르고 있었지만
텔레그램 명령에만 걸려 있어 일일 결산이 못 봤다(#303 감사는 잡고 있었는데 집계
규약이 갈린 그 병의 다음 형태 — 여기선 아예 **등록이 안 돼 있었다**, #24).
그래서 사용자가 화면을 눈으로 보고 먼저 물었다(#52 조용한 것과 죽은 것).

판정 규약:
  ❌ = **우리가 고칠 것**(행도 없고 폴백도 없다 = 화면이 빈다)
  ⚠️ = 원천이 막혀 폴백으로 서빙 중(값은 보이지만 실시간이 아니다) — 우리가
       당장 고칠 수 있는 것이 아니므로 ❌ 로 올리면 매일 못 고칠 ❌ 가 되어
       진짜 ❌ 를 가린다(#260·#25).
  ❓ = 판정 불가(예외·의존성). 0건은 ✅ 가 아니다(#54).

⚠️ 화면이 쓰는 **그 경로**를 태운다(#35) — 원천을 따로 찌르면 캐시·폴백·필터가
빠져 통계가 화면과 갈린다. 캐시 TTL 안이면 추가 요청이 0 이다.
⚠️ 판정 글자는 **판정에만** 쓴다 — 요약은 세기만 한다(#250·#268·#289·#303
sweep 이 `❌`/`⚠️` 든 줄을 결산에 올리므로 설명 문구에 쓰면 두 번 세어진다).
"""
from __future__ import annotations


def board_verdict(name: str, n: int, fallback: bool, reason: str) -> str:
    """관측 → 한 줄 판정(순수). 배선과 따로 값으로 재려고 함수로 뺀다(#176).

    ⚠️ **운영자가 끈 것은 결함이 아니다** — `/naverpause` 중에 ❌ 를 내면
    끄는 동안 매일 못 고칠 ❌ 가 쌓여 진짜 ❌ 를 가린다(#260·#279·#345 일시
    정지는 실패가 아니라 판정 보류). 이 모듈 독스트링이 스스로 그렇게 적어
    놓고 이 함수만 안 지키고 있었다(독립 리뷰 2026-09-18 · #55·#286)."""
    from bot.naver_diag import PAUSED
    if n and not fallback:
        return f"✅ {name}: {n}행(원천 정상)"
    if n and fallback:
        return (f"⚠️ {name}: {n}행 — 원천이 막혀 폴백으로 서빙 중"
                + (f" · {reason}" if reason else ""))
    if PAUSED in (reason or ""):
        return f"⏸ {name}: 0행 — 네이버 호출을 **우리가 꺼 뒀습니다**(결함 아님)"
    return (f"❌ {name}: 0행 — 화면이 빕니다"
            + (f" · {reason}" if reason else " · 사유 미기록"))


def _movers() -> str:
    try:
        from bot.naver_ranking_client import fetch_kr_movers
        d = fetch_kr_movers(limit=30)
    except Exception as exc:                                   # noqa: BLE001
        return f"❓ 급등·급락: 판정 불가({type(exc).__name__}: {exc})"
    n = len(d.get("up") or []) + len(d.get("down") or [])
    return board_verdict("급등·급락", n, bool(d.get("fallback")),
                         str(d.get("reason") or ""))


def _volume() -> str:
    try:
        from bot.kr_volume_client import fetch_kr_volume_top
        d = fetch_kr_volume_top(limit=50)
    except Exception as exc:                                   # noqa: BLE001
        return f"❓ 거래량 상위: 판정 불가({type(exc).__name__}: {exc})"
    return board_verdict("거래량 상위", len(d.get("rows") or []),
                         bool(d.get("fallback")), str(d.get("reason") or ""))


def _endpoint() -> str:
    """원천 자체의 도달성 — 보드가 폴백으로 살아 있어도 **원천이 죽은 사실**은
    따로 말해야 한다(#45 두 모집단 · #41 여유로 사실을 덮지 말 것)."""
    try:
        from bot.source_health import _naver_domestic
        ok, detail = _naver_domestic()
    except Exception as exc:                                   # noqa: BLE001
        return f"❓ 원천 도달성: 판정 불가({type(exc).__name__})"
    return (f"✅ 원천 `domestic/stock/list`: {detail}" if ok
            else f"⚠️ 원천 `domestic/stock/list`: {detail} — 네이버가 우리 요청 "
                 "모양을 거절합니다. 새 모양은 `python -m bot.scripts."
                 "kr_board_probe` 의 ①-b 가 잽니다")


def main() -> int:
    print("═══ KR 네이버 보드 ═══")
    lines = [_endpoint(), _movers(), _volume()]
    for ln in lines:
        print("   " + ln)
    bad = sum(1 for ln in lines if ln.startswith("❌"))
    # ⚠️ 요약은 **세기만** 한다 — 판정 글자를 여기 쓰면 sweep 이 같은 결함을
    # 두 번 센다(#250·#268·#289).
    print(f"   요약: 검사 {len(lines)}건 · 화면이 비는 보드 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
