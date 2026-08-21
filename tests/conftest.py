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
    except Exception:
        pass
    yield
