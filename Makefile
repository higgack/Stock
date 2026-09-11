# NOAH stock bot — convenience commands.
#
# 사용자 정책 2026-06-01: venv 경로 매번 안 외우게 한 줄 shortcuts.
# 모든 target 은 .venv 의 python/pytest 를 사용.
#
# 사용:
#   make test       — 회귀 슈트 전체(pytest.ini testpaths) 실행. commit 전 의무.
#   make test-fast  — 회귀 슈트만 (단축 출력).
#   make syntax     — 만진 .py 파일 ast.parse (변경 후 1초 sanity).
#   make help-len   — _HELP_TEXT UTF-16 길이 (4096 cap 확인).
#   make install    — requirements.txt 설치 (pytest 포함).

PY := .venv/bin/python

.PHONY: test test-fast syntax help-len install

# ⚠️ 옛 판은 `tests/` 만 돌려 `bot/tests` 182건이 **어떤 게이트에도 안 걸렸다**.
# 그렇다고 한 세션에 합치면 깨진다 — `bot/tests/conftest.py` 가 sys.modules 를
# 모듈 레벨로 오염시켜 `tests/` 73건이 빨간불이 된다(2026-09-12 실측, 이유는
# pytest.ini 주석). **별도 프로세스로 둘 다** 돌리는 것이 게이트다.
test:
	$(PY) -m pytest -v
	$(PY) -m pytest bot/tests -v

test-fast:
	$(PY) -m pytest -q
	$(PY) -m pytest bot/tests -q

syntax:
	@$(PY) -c "import ast, sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]; print('syntax OK')" \
		bot/screener.py bot/dashboard.py bot/telegram_bot.py

help-len:
	@$(PY) -c "import re; t=re.search(r'_HELP_TEXT\s*=\s*\"\"\"(.*?)\"\"\"', open('bot/telegram_bot.py').read(), re.DOTALL).group(1); n=len(t.encode('utf-16-le'))//2; print(f'_HELP_TEXT UTF-16: {n} / 4096 (slack {4096-n})')"

install:
	$(PY) -m pip install -r requirements.txt
