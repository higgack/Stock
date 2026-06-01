# NOAH stock bot — convenience commands.
#
# 사용자 정책 2026-06-01: venv 경로 매번 안 외우게 한 줄 shortcuts.
# 모든 target 은 .venv 의 python/pytest 를 사용.
#
# 사용:
#   make test       — 회귀 슈트 (tests/) 실행. commit 전 의무.
#   make test-fast  — 회귀 슈트만 (단축 출력).
#   make syntax     — 만진 .py 파일 ast.parse (변경 후 1초 sanity).
#   make help-len   — _HELP_TEXT UTF-16 길이 (4096 cap 확인).
#   make install    — requirements.txt 설치 (pytest 포함).

PY := .venv/bin/python

.PHONY: test test-fast syntax help-len install

test:
	$(PY) -m pytest tests/ -v

test-fast:
	$(PY) -m pytest tests/ -q

syntax:
	@$(PY) -c "import ast, sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]; print('syntax OK')" \
		bot/screener.py bot/dashboard.py bot/telegram_bot.py \
		standardview/scripts/weekly_pusher.py

help-len:
	@$(PY) -c "import re; t=re.search(r'_HELP_TEXT\s*=\s*\"\"\"(.*?)\"\"\"', open('bot/telegram_bot.py').read(), re.DOTALL).group(1); n=len(t.encode('utf-16-le'))//2; print(f'_HELP_TEXT UTF-16: {n} / 4096 (slack {4096-n})')"

install:
	$(PY) -m pip install -r requirements.txt
