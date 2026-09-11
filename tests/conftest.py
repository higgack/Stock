"""pytest 설정 — repo root 를 sys.path 에 추가해 'import bot.*' / 'import
standardview.*' 가 venv 설치 없이 working tree 에서 바로 동작하게.

tests/ 는 회귀 영구 차단 전용 — 매 commit 전 `python -m pytest tests/`
한 줄로 catastrophic-regex / details-balance / ETF-dedup / FSC-cache /
screener-idempotency 등 실제 우리가 당했던 버그 클래스 재발 차단.
"""
import sys
from pathlib import Path

import pytest

# repo root = tests/ 의 parent. test 가 어디서 실행돼도 동일.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    """collect_stock_snapshot 단기 캐시(120초)는 티커 단위라, 같은 티커를 다른
    mock 으로 부르는 별개 테스트끼리 오염될 수 있다(예: KLAC glitch vs normal).
    테스트마다 비워 격리 — 프로덕션 캐시 동작과 무관(테스트 결정성 전용)."""
    try:
        import bot.stock_snapshot as _ss
        with _ss._SNAP_CACHE_LOCK:
            _ss._SNAP_CACHE.clear()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _isolate_disk_caches(tmp_path_factory, monkeypatch):
    """디스크 캐시를 쓰는 모듈은 테스트에서 **사용자 실제 캐시**를 만진다.

    2026-08-20 실측(board_audit 이 발각): `make test` 를 돌린 뒤 변동성
    last-good 캐시에 `{"value": 369.0, "date": "d299"}` 라는 **테스트 픽스처
    값**이 들어 있었다. VM 에서 커밋 전 회귀를 돌리면 그 가짜 값이 운영
    캐시를 덮고, 원천이 실패하는 날 화면이 그걸 '저장분'으로 표시한다 —
    테스트가 프로덕션 데이터를 오염시키는 경로다.

    개별 테스트가 직접 monkeypatch 하는 것보다 여기서 한 번에 막는 게 맞다
    (목록형 방어는 새 테스트를 못 잡는다 — 실수 #24).
    """
    root = tmp_path_factory.mktemp("caches")
    try:
        import bot.market_timing as _mt
        monkeypatch.setattr(_mt, "_VOL_CACHE_DIR", root / "market_timing")
    except Exception:
        pass
    # DART 원문 negative-cache — 실패한 rcept_no 를 **디스크**에 기록한다.
    # 2026-08-20 실측: 새 테스트가 가짜 rcept_no("R1")로 fetch 를 태우자
    # 그 실패가 운영 파일(~/.tradingagents/dart_doc_fail.json)에 남았고,
    # 같은 id 를 쓰는 다음 실행이 통째로 막혔다(테스트끼리도 오염). 위
    # 변동성 캐시와 **같은 사고**라 여기서 함께 막는다.
    try:
        import bot.dart_feed as _df
        monkeypatch.setattr(_df, "_DOC_FAIL", root / "dart_doc_fail.json")
        _df._DOC_TEXT_MEM.clear()
        _df._DOC_BLOB_MEM.clear()      # 원문 zip 바이트 캐시도 격리
        _df._DOC_TRUNC.clear()         # 잘림 플래그도 함께
    except Exception:
        pass
    # 공용 JSON 디스크 캐시(finviz_client 의 `_cached`/`_cache_write`)를 쓰는
    # 모듈이 여럿이다(차트 공시 마커·DART 상세요약 등) — 위 둘과 같은 사고라
    # 여기서 함께 막는다. 테스트마다 monkeypatch 하는 건 목록형 방어라 새
    # 테스트를 못 잡는다(#24).
    try:
        import bot.finviz_client as _fv
        monkeypatch.setattr(_fv, "_CACHE_DIR", root / "finviz")
    except Exception:
        pass
    # 업종맵 실패 기록(`kr_industry_fail.json`) — 2026-09-11 실측: 페이지를
    # 렌더하는 테스트가 `kr_industry_map()` 을 킥하고, 네트워크가 막힌 테스트
    # 환경에선 그 빌드가 실패해 **운영 캐시**에 실패 도장을 남겼다. 그 도장은
    # 15분 백오프의 근거라, 다음 실행이 운영에서도 업종맵을 안 만든다.
    try:
        import bot.naver_sector_client as _ns
        monkeypatch.setattr(_ns, "_CACHE_DIR", root / "naver_sector")
    except Exception:
        pass
    # 리서치 상세(목표가·투자의견) **영구 캐시**(2026-09-12) — 발행 리포트는
    # 안 바뀌므로 30일 TTL 이다. 테스트가 가짜 nid 를 여기 구우면 운영이 그걸
    # 한 달 동안 '저장분' 으로 서빙한다(#30·#344). 메모리 사본도 같이 비운다 —
    # 파일만 돌려놓으면 이미 읽어 둔 맵이 다음 테스트로 샌다.
    try:
        import bot.naver_research_client as _nr
        monkeypatch.setattr(_nr, "_CACHE_DIR", root / "naver_research")
        monkeypatch.setattr(_nr, "_DETAIL_MEM", {})
        monkeypatch.setattr(_nr, "_DETAIL_MEM_AT", 0.0)
        monkeypatch.setattr(_nr, "_DETAIL_DIRTY", False)
    except Exception:
        pass
    yield

# ⚠️ 이 fixture 는 **완전하지 않다** — `bot/` 에는 `~/.tradingagents` 아래를
# 가리키는 모듈 상수가 131개 있고(2026-09-11 AST 실측) 여기 막는 건 그중 넷이다.
# 전부 리다이렉트하려면 테스트마다 130개 모듈을 import 해야 해서 비용이 크다.
# 그래서 "새 캐시를 쓰는 테스트를 여기서 자동으로 막는다" 고 **주장하지 않는다**
# (#286 지시서가 자기 자신에 대해 사실이 아닌 것을 말하면 다음 사람이 가드를
# 건너뛴다). 오염을 관측하면 그 모듈을 여기 한 줄로 추가할 것.

# 바깥 원천 차단은 **레포 루트 conftest.py** 로 옮겼다(2026-09-11) — 여기 두면
# `pytest bot/tests` 단독 실행이 무방비다. 회귀가 그 모듈을 파일로 찾아 읽는다.
