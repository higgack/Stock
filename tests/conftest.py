"""pytest 설정 — repo root 를 sys.path 에 추가해 'import bot.*' / 'import
standardview.*' 가 venv 설치 없이 working tree 에서 바로 동작하게.

tests/ 는 회귀 영구 차단 전용 — 매 commit 전 `python -m pytest tests/`
한 줄로 catastrophic-regex / details-balance / ETF-dedup / FSC-cache /
screener-idempotency 등 실제 우리가 당했던 버그 클래스 재발 차단.
"""
import sys
from pathlib import Path

# repo root = tests/ 의 parent. test 가 어디서 실행돼도 동일.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
