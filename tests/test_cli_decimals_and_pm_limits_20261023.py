"""후속 ① 경기선행지수 행 소수 2자리 · ③ PM '데이터 한계' 오분류 방지 (2026-10-23)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from bot import dashboard as dash
from bot import fred_boards as fb


def _fv_js() -> str:
    page = fb.render_liquidity_page([], {}, None)
    a = page.index("function fv(r){")
    return page[a:page.index("function pills()", a)]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_fv_cli_rows_two_decimals_others_unchanged(tmp_path):
    """fv 를 **실행**한다 — CLI 행만 2자리, 다른 Index 행(STLFSI4 등)은 1자리 그대로."""
    js = _fv_js() + r"""
console.log(JSON.stringify([
  fv({id:'OECD:CLI:KOR',unit:'Index',latest:100.1234}),
  fv({id:'OECD:CLI:G20',unit:'Index',latest:99.5}),
  fv({id:'STLFSI4',unit:'Index',latest:-0.4567}),
  fv({id:'DTWEXBGS',unit:'Index',latest:120.456}),
  fv({id:'X',unit:'%',latest:4.567})]));
"""
    f = tmp_path / "t.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["100.12", "99.50", "-0.5", "120.5", "4.57%"]


def _record(body: str) -> dict:
    return {"full_report": body, "summary": ""}


_LIMITS = ("**Rating**: Hold\n\n**데이터 한계**:\n"
           "- 공시 API 호출 실패로 최근 분기 수치 확인 못 함\n"
           "- get_risk_metrics 데이터 없음\n")


def test_pm_limits_section_is_not_model_apology():
    assert dash._detect_issues(_record(_LIMITS)) == []


def test_apology_outside_limits_section_still_flagged():
    body = "## 뉴스\n죄송합니다. 도구를 사용할 수 없습니다.\n\n" + _LIMITS
    assert dash._detect_issues(_record(body)) == ["도구 사용 불가 사과 (한국어)"]


def test_text_after_limits_bullets_is_still_scanned():
    body = _LIMITS + "\n다음 단락: API 호출 실패"
    assert dash._detect_issues(_record(body)) == ["API 호출 실패 사과 (모델)"]
