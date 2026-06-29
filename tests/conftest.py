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
