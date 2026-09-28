"""배포가 상시 프로세스를 재시작하는 조건이 그 프로세스의 import 폐포를 덮는가 (실수 #423).

배포(base squash merge → VM auto-update 의 `git reset --hard`)는 **디스크**를 바꿀 뿐이다.
상시 프로세스(`Type=simple` 유닛)는 재시작돼야 새 코드를 import 한다. 2026-09-28 #422 fix
(aae6ea6 — `bot/env_keys.py`·`bot/dart_client.py`)는 base 에 들어갔지만 trade 대시보드의 재시작
조건은 `trade/*.py` 뿐이라 그 서버는 옛 코드로 계속 돌았고 사용자가 손으로 재시작했다(#11 —
#411 이 나쁜양파 리스너에서 고친 병의 대시보드판). 재 보니 같은 병이 셋 더 있었다(#38): BeOn
리스너(자기가 import 하는 `trade/tg_entities.py`·`trade/listener_health.py` 를 안 봄) · NOAH
대시보드(`trade.kg_candidates`·TradingAgents 를 안 봄) · DAJU 리스너(재시작 규칙이 **아예** 없었다).

유닛은 **파일에서 파생**한다(#24): `deploy/*.service` 중 oneshot 이 아닌 것이 상시 유닛이고,
정책 표에 없는 상시 유닛이 생기면 실패한다 — 재시작 규칙을 정하라는 뜻이다.

폐포(`_closure`): 진입 모듈이 사는 **자기 가족**(trade · bot · tradingagents)의 모듈은 함수 안
import 까지 따라간다 — 그 코드 경로는 이 유닛의 것이다(aae6ea6 이 그랬다: `trade/company_report.py`
가 함수 안에서 `bot.dart_client` 를 부른다). **다른 가족** 모듈은 최상위 import 만 따라간다 —
import 하는 순간 도는 건 최상위뿐이고, 다른 가족의 헬퍼 하나를 부르는 유닛이 그 모듈의 다른 함수까지
부른다는 근거는 없다(다 따라가면 허브 셋 — `bot.dashboard`·`bot.market`·`bot.daily_kr_flow` — 때문에
모든 유닛이 bot 131 · TradingAgents 58 모듈이 된다, 실측). 모듈을 import 하면 **위 패키지들의
`__init__.py`** 도 돈다(진입점 `-m trade.scripts.x` 가 실행하는 `trade/scripts/__init__.py` — #411 L9).

정책(`_POLICY`):
  - server(대시보드): 재시작이 무상태·몇 초다 → 폐포가 닿는 **가족 전체**(trade·bot 은 최상위 모듈과
    그 가족의 `data/`, tradingagents 는 패키지 전부)와 폐포의 하위 패키지 파일을 덮는다 — NOAH
    대시보드 2026-06-11 선례("bot/*.py 전체로 넓혀 클래스 자체 제거"). `data/` 는 코드가 아니라
    폐포로는 못 재지만 메모리에 캐시된다(`_family_files` 독스트링의 실례).
  - listener(Telethon 중계): 폐포를 덮되 다른 가족으로 가는 간선은 **사유와 함께** 끊을 수 있다
    (리스너 경로가 그 함수를 부르지 않을 때만 — 재시작마다 연결을 다시 맺으므로 NOAH 배포마다
    재시작하지 않으려고다, #411). 새 간선이 생기면 폐포가 규칙 밖으로 나가 실패한다 — 결정하라는
    뜻이다(#411 의 `bot_mods == {"bot/market.py"}` 를 일반화했다).
  - bot(stock-bot · trade-bot): 게이트를 통과한 배포마다 무조건 재시작한다 → 게이트가 폐포를 덮는다.
trade 쪽 조건부 재시작은 `TRADE_RELEVANT` 게이트를 통과해야 닿으므로 실효 조건 = 게이트 ∧ 규칙이다.

⚠️ 못 보는 축(#274): 동적 import(`importlib`·`__import__` — 대시보드와 DAJU 는 가족 전체를 덮어 같은
가족 안이면 무해하고, 두 trade 리스너의 폐포엔 지금 없다, 실측) · 다른 가족 모듈의 **함수 안** import 가
실제 경로에 있는 경우(그 import 가 유닛이 덮는 가족 안에 머물면 무해 — 덮지 않는 가족으로 번지면 못
본다 · 리스너는 그 간선을 끊는 순간 적는 사유가 그 판단이다) · 파일 **이름을 소스에 안 적고**
조립해 읽는 repo 데이터(`_data_read_by` 는 이름으로 찾는다 — 서버는 가족의 `data/` 전체를 덮어 무해 ·
지금 두 trade 리스너의 폐포 모듈은 repo 데이터를 안 읽고 런타임 디렉터리 `~/.trade` 를 읽는다, 실측) ·
재시작 명령 자체의 실패(sudoers — 스크립트가 로그·알림으로 말한다).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TRADE_SH = _ROOT / "deploy" / "trade-auto-update.sh"
_NOAH_SH = _ROOT / "deploy" / "auto-update.sh"
# 코드 가족 → 그 가족의 최상위 패키지가 놓인 디렉터리(tradingagents 는 편집 가능 설치)
_FAMILIES = {"trade": _ROOT, "bot": _ROOT, "tradingagents": _ROOT / "TradingAgents"}


# ── 폐포 ──────────────────────────────────────────────────────────────────────
def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def _module_path(mod: str, families: dict) -> Path | None:
    base = families.get(mod.split(".")[0])
    if base is None or not mod:
        return None
    f = base.joinpath(*mod.split(".")).with_suffix(".py")
    if f.is_file():
        return f
    d = base.joinpath(*mod.split(".")) / "__init__.py"
    return d if d.is_file() else None


def _family(p: Path, families: dict) -> str:
    for name, base in families.items():
        try:
            if p.relative_to(base).parts[0] == name:
                return name
        except ValueError:
            continue
    raise AssertionError(f"가족 밖 파일: {p}")


def _imports(f: Path, families: dict) -> list[tuple[Path, bool]]:
    """`f` 가 import 하는 가족 모듈 파일과 그 import 가 **함수 안인가**. 위 패키지들의
    `__init__.py` 는 `f` 를 import 하는 순간 실행되므로 최상위로 센다."""
    base = families[_family(f, families)]
    parts = f.relative_to(base).parts
    pkg = parts[:-1]
    out: list[tuple[Path, bool]] = []

    def visit(node: ast.AST, lazy: bool) -> None:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                visit(ch, True)
                continue
            mods: list[str] = []
            if isinstance(ch, ast.Import):
                mods = [a.name for a in ch.names]
            elif isinstance(ch, ast.ImportFrom):
                b = ch.module or ""
                if ch.level:
                    b = ".".join(list(pkg[:len(pkg) - (ch.level - 1)]) + ([b] if b else []))
                mods = [b] + [f"{b}.{a.name}" for a in ch.names]
            for m in mods:
                p = _module_path(m, families)
                if p is not None:
                    out.append((p, lazy))
            visit(ch, lazy)

    visit(ast.parse(f.read_text(encoding="utf-8")), False)
    for i in range(1, len(parts)):
        init = base.joinpath(*parts[:i]) / "__init__.py"
        if init.is_file() and init != f:
            out.append((init, False))
    return out


def _closure(entry: str, cuts: frozenset = frozenset(), *, families: dict = _FAMILIES,
             root: Path = _ROOT) -> tuple[set[str], set[tuple[str, str]]]:
    """`entry` 모듈의 import 폐포(`root` 기준 경로)와 실제로 만난 끊는 간선."""
    start = _module_path(entry, families)
    assert start is not None, f"진입 모듈을 못 찾았다: {entry}"
    home = _family(start, families)
    seen, todo, met = {start}, [start], set()
    while todo:
        f = todo.pop()
        for p, lazy in _imports(f, families):
            if lazy and _family(f, families) != home:
                continue    # 다른 가족 모듈의 함수 안 import — 이 유닛이 그 함수를 부른다는 근거가 없다
            edge = (_rel(f, root), _rel(p, root))
            if edge in cuts:
                met.add(edge)
                continue
            if p not in seen:
                seen.add(p)
                todo.append(p)
    return {_rel(p, root) for p in seen}, met


def _data_read_by(files: set[str]) -> set[str]:
    """폐포 모듈이 **이름으로** 가리키는 repo 데이터 파일(`trade/data/**` · `bot/data/**`).
    코드가 아니라 폐포로는 안 잡히지만 모듈이 읽어 메모리에 캐시할 수 있다(`_family_files`
    독스트링의 실례). 경로를 조립하는 방식은 모듈마다 달라 파일 이름으로 찾는다(4개 전부 그
    이름이 읽는 모듈 소스에 그대로 나온다 — 실측)."""
    data = [p for fam in ("trade", "bot") for p in (_ROOT / fam / "data").rglob("*") if p.is_file()]
    out: set[str] = set()
    for f in files:
        src = (_ROOT / f).read_text(encoding="utf-8")
        out |= {_rel(p, _ROOT) for p in data if p.name in src}
    return out


def _family_files(fam: str) -> set[str]:
    """서버가 덮는 '가족 전체' — trade·bot 은 최상위 모듈(하위 패키지는 폐포가 닿는 것만)과
    그 가족의 `data/`(코드는 아니지만 메모리에 캐시된다 — `trade/mti_companies.py` 의
    `_REINFORCE_APPROVED_CACHE` 는 오버레이 mtime 만 보고 repo CSV 의 변경은 안 본다),
    tradingagents 는 패키지 전부."""
    if fam == "tradingagents":
        return {_rel(p, _ROOT) for p in (_FAMILIES[fam] / fam).rglob("*.py")}
    data = (_ROOT / fam / "data")
    return ({_rel(p, _ROOT) for p in (_ROOT / fam).glob("*.py")}
            | {_rel(p, _ROOT) for p in data.rglob("*") if p.is_file()})


# ── 유닛 · 재시작 조건 ────────────────────────────────────────────────────────
def _long_running_units() -> dict[str, str]:
    """`deploy/*.service` 중 oneshot 이 아닌 유닛 → `python -m` 진입 모듈."""
    out = {}
    for f in sorted((_ROOT / "deploy").glob("*.service")):
        txt = f.read_text(encoding="utf-8")
        typ = re.search(r"^Type=(\S+)", txt, re.M)
        if typ and typ.group(1) == "oneshot":
            continue
        m = re.search(r"^ExecStart=\S*python\S*\s+-m\s+([\w.]+)", txt, re.M)
        assert m, f"{f.name}: 상시 유닛인데 `python -m <모듈>` 진입점을 못 읽었다 — 폐포를 잴 수 없다"
        out[f.stem] = m.group(1)
    return out


# 유닛 → (종류, (재시작 조건이 사는 스크립트, 변수 — None 이면 게이트를 통과한 배포마다), 끊는 간선)
_POLICY: dict[str, tuple[str, tuple[Path, str | None], dict[tuple[str, str], str]]] = {
    "stock-bot": ("bot", (_NOAH_SH, None), {}),
    "stock-bot-dashboard": ("server", (_NOAH_SH, "CODE_CHANGED"), {}),
    "daju-listener": ("listener", (_NOAH_SH, "CODE_CHANGED"), {}),
    "trade-bot": ("bot", (_TRADE_SH, None), {}),
    "trade-bot-dashboard": ("server", (_TRADE_SH, "DASHBOARD_RELEVANT"), {}),
    "trade-bot-beon-listener": ("listener", (_TRADE_SH, "BEON_LISTENER_RELEVANT"), {
        ("trade/scripts/listen_beon.py", "bot/daily_kr_flow.py"):
            "`--why` 진단(`_why`)에서만 부른다 — 상시 리스너 경로(`_run_listener`)는 부르지 않는다",
    }),
    "trade-bot-badonion-listener": ("listener", (_TRADE_SH, "BADONION_LISTENER_RELEVANT"), {
        ("trade/stock_link.py", "bot/market.py"):
            "종목 링크 렌더가 함수 안에서 부른다 — 리스너 경로(관련성 판정·보증)는 링크를 그리지 않는다",
    }),
}
_UNITS = sorted(_POLICY)
_CONDITIONAL = [u for u in _UNITS if _POLICY[u][1][1] is not None]

# 폐포가 눈멀지 않았다(#54 반대 증거) — 이 유닛이 실제로 실행하는 코드가 폐포에 있다
_MUST_REACH = {
    # aae6ea6 — 기업 리포트가 함수 안에서 부르는 DART 클라이언트(#423 이 막으려는 바로 그 경로)
    "trade-bot-dashboard": {"trade/company_report.py", "bot/dart_client.py"},
    # #423 — 옛 규칙(`listen_beon.py` 만)이 놓친, 리스너가 최상위에서 import 하는 모듈
    "trade-bot-beon-listener": {"trade/tg_entities.py", "trade/listener_health.py",
                                "trade/__init__.py", "trade/scripts/__init__.py"},
    # #411 — 보증·필터·세션 가드와 파서들
    "trade-bot-badonion-listener": {"trade/relay_origins.py", "trade/badonion_sources.py",
                                    "trade/tg_entities.py", "trade/kr_stock_imports.py",
                                    "trade/__init__.py", "trade/scripts/__init__.py"},
    # #423 — NOAH 대시보드가 부르는 trade · TradingAgents
    "stock-bot-dashboard": {"trade/kg_candidates.py",
                            "TradingAgents/tradingagents/agents/utils/agent_utils.py"},
    # #423 — 메시지마다 도는 파서 · 블로그 재생성
    "daju-listener": {"bot/daju_parse.py", "bot/dashboard.py"},
}
_MIN_FILES = {"trade-bot-badonion-listener": 20}     # #411 — 레지스트리 파서들까지 따라간다
# 데이터 탐지가 눈멀지 않았다 — 운영자 승인 보강 목록(`mti_companies`·`kg_candidates`) · HS 이름표(`hs_names`)
_MUST_READ = {"trade-bot-dashboard": {"trade/data/reinforce_approved.csv", "trade/data/hs_names.tsv"},
              "stock-bot-dashboard": {"trade/data/reinforce_approved.csv"}}

# 어느 상시 프로세스도 읽지 않는 것 — 이것들로 재시작하면 소음이다(#25·#260)
_NEVER = ("trade/tests/test_relay_origins.py", "bot/tests/test_dart_feed.py",
          "tests/test_regression.py", "TradingAgents/tests/test_x.py",
          "docs/tests.md", "CLAUDE.md", "trade/README.md")
# 리스너는 폐포 밖 코드로도 재시작하지 않는다 — 재시작마다 연결을 다시 맺는다(#411)
_LISTENER_NEVER = {
    "trade-bot-beon-listener": ("trade/scripts/backfill_beon.py", "bot/market.py"),
    "trade-bot-badonion-listener": ("trade/scripts/backfill_badonion.py", "bot/market.py"),
}


def _script(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _trade_var(var: str) -> re.Pattern:
    """`VAR=$(echo "$CHANGED_FILES" | grep -E '…' || true)` 의 정규식."""
    m = re.search(rf"""^{var}=\$\(echo "\$CHANGED_FILES" \| grep -E '([^']+)' \|\| true\)""",
                  _script(_TRADE_SH), re.M)
    assert m, f"{var} 줄의 모양이 바뀌었다 — 이 회귀를 같이 고칠 것"
    return re.compile(m.group(1))


def _noah_var(var: str) -> re.Pattern:
    """`if … | grep -qE '…'; then` 다음 줄 `VAR=1` 의 정규식."""
    m = re.search(rf"""grep -qE '([^']+)'; then[ \t]*\n[ \t]*{var}=1\b""", _script(_NOAH_SH))
    assert m, f"{var} 조건의 모양이 바뀌었다 — 이 회귀를 같이 고칠 것"
    return re.compile(m.group(1))


def _covered(unit: str):
    """이 유닛을 재시작하게 만드는 변경 파일인가(스크립트의 실효 조건)."""
    sh, var = _POLICY[unit][1]
    if sh == _NOAH_SH:
        if var is None:
            return lambda f: True                    # stock-bot — 가져온 변경마다 재시작
        rx = _noah_var(var)
        return lambda f: bool(rx.search(f))
    gate = _trade_var("TRADE_RELEVANT")              # 이 게이트를 못 넘으면 조용히 pull 만 한다
    if var is None:
        return lambda f: bool(gate.search(f))
    rx = _trade_var(var)
    return lambda f: bool(gate.search(f) and rx.search(f))


def _if_block(sh: str, head: str) -> str:
    """`head` 로 시작하는 if 줄부터 **같은 들여쓰기의** `fi` 까지."""
    m = re.search(rf"^(?P<ind>[ \t]*){re.escape(head)}[ \t]*$", sh, re.M)
    assert m, f"{head!r} 가 없다 — 재시작 배선을 찾을 수 없다"
    end = re.compile(rf"^{m.group('ind')}fi\b", re.M).search(sh, m.end())
    assert end, f"{head!r} 블록의 fi 를 못 찾았다"
    return sh[m.end():end.start()]


def _restarts(sh: str, block: str, unit: str) -> bool:
    """블록이 그 유닛을 재시작하나 — 직접 또는 블록이 부르는 함수를 거쳐."""
    rx = re.compile(rf"systemctl (?:try-)?restart {re.escape(unit)}(?![\w-])")
    if rx.search(block):
        return True
    for name in re.findall(r"^[ \t]*(\w+)[ \t]*$", block, re.M):     # 인자 없는 함수 호출 줄
        body = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", sh, re.M | re.S)
        if body and rx.search(body.group(1)):
            return True
    return False


# ── 회귀 ──────────────────────────────────────────────────────────────────────
def test_every_long_running_unit_has_a_restart_policy():
    units = _long_running_units()
    assert set(units) == set(_POLICY), (
        f"정책 표에 없는 상시 유닛(재시작 규칙을 정할 것): {sorted(set(units) - set(_POLICY))} · "
        f"없어진 유닛의 정책(지울 것): {sorted(set(_POLICY) - set(units))}")


@pytest.mark.parametrize("unit", _UNITS)
def test_restart_condition_covers_the_import_closure(unit):
    kind, _, cuts = _POLICY[unit]
    files, _ = _closure(_long_running_units()[unit], frozenset(cuts))
    assert _MUST_REACH.get(unit, set()) <= files, sorted(_MUST_REACH[unit] - files)
    assert len(files) >= _MIN_FILES.get(unit, 1), sorted(files)
    covered = _covered(unit)
    missing = sorted(f for f in files if not covered(f))
    assert missing == [], f"{unit}: 바뀌어도 이 프로세스를 재시작하지 않는 모듈 {missing}"
    data = _data_read_by(files)
    assert _MUST_READ.get(unit, set()) <= data, sorted(_MUST_READ[unit] - data)
    stale = sorted(d for d in data if not covered(d))
    assert stale == [], f"{unit}: 폐포 모듈이 읽는 repo 데이터가 바뀌어도 재시작하지 않는다 {stale}"
    if kind == "server":
        fams = {_family(_ROOT / f, _FAMILIES) for f in files}
        narrow = sorted(f for fam in fams for f in _family_files(fam) if not covered(f))
        assert narrow == [], f"{unit}: 서버는 닿는 가족 전체를 덮는다 — 빠진 모듈 {narrow[:10]}"


@pytest.mark.parametrize("unit", _UNITS)
def test_cut_edges_are_real_cross_family_and_listener_only(unit):
    kind, _, cuts = _POLICY[unit]
    if kind != "listener":
        assert not cuts, f"{unit}: 간선을 끊는 건 리스너뿐 — 서버·봇은 폐포를 다 덮는다"
        return
    _, met = _closure(_long_running_units()[unit], frozenset(cuts))
    assert sorted(set(cuts) - met) == [], f"{unit}: 더는 없는 간선의 사유 — 지울 것"
    for (a, b), why in cuts.items():
        assert why.strip(), (a, b)
        assert a.split("/")[0] != b.split("/")[0], f"같은 가족 안 간선은 끊지 않는다: {a} → {b}"


@pytest.mark.parametrize("unit", [u for u in _CONDITIONAL])
def test_restart_condition_ignores_what_no_process_imports(unit):
    covered = _covered(unit)
    fired = [f for f in _NEVER + _LISTENER_NEVER.get(unit, ()) if covered(f)]
    assert fired == [], f"{unit} 이 이 변경들로 재시작한다: {fired}"


@pytest.mark.parametrize("unit", _UNITS)
def test_restart_condition_is_wired_to_that_unit(unit):
    sh_path, var = _POLICY[unit][1]
    sh = _script(sh_path)
    if var is None:
        assert re.search(rf"systemctl restart {re.escape(unit)}(?![\w-])", sh), unit
        return
    head = f'if [ -n "${var}" ]; then' if sh_path == _TRADE_SH else f'if [ "${var}" = "1" ]; then'
    assert _restarts(sh, _if_block(sh, head), unit), f"{var} 블록이 {unit} 을 재시작하지 않는다"


def test_vm_direct_push_restarts_every_conditional_noah_unit():
    """VM 에서 직접 push 한 배포는 diff 범위가 없어 무엇이 바뀌었는지 모른다 — 그 경로는 조건
    없이 대시보드를 재시작해 왔다(2026-05-21). 같은 이유로 조건부 NOAH 유닛 전부를."""
    sh = _script(_NOAH_SH)
    block = _if_block(sh, 'if [ "$LOCAL" = "$REMOTE" ]; then')
    units = [u for u in _CONDITIONAL if _POLICY[u][1][0] == _NOAH_SH]
    assert units and all(_restarts(sh, block, u) for u in units), units


def test_every_best_effort_restart_has_a_provisioned_sudoers_line():
    """`sudo -n` 재시작은 NOPASSWD 줄이 없으면 거절된다 — 재시작 대상을 늘리면 권한 줄도 같은
    커밋에 있어야 한다(설치기는 `deploy/` 가 바뀐 배포에서 자동으로 돌아 drop-in 을 심는다)."""
    calls: set[str] = set()
    for sh in (_NOAH_SH, _TRADE_SH):
        calls |= set(re.findall(r"sudo -n /bin/systemctl (restart [\w-]+)", _script(sh)))
    granted: set[str] = set()
    for inst in ("install.sh", "install-trade-units.sh"):
        granted |= set(re.findall(r"NOPASSWD: /bin/systemctl (restart [\w-]+)",
                                  _script(_ROOT / "deploy" / inst)))
    assert calls, "재시작 호출을 하나도 못 읽었다 — 모양이 바뀌었으면 이 회귀를 같이 고칠 것"
    assert sorted(calls - granted) == [], "권한 줄 없는 재시작"


def test_closure_follows_own_family_lazy_imports_but_only_top_level_of_others(tmp_path):
    """폐포 규칙 자체를 잰다(#91 — 폐포가 눈멀면 위 회귀가 전부 통과한다)."""
    def w(rel: str, src: str = "") -> None:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")

    w("fa/__init__.py")
    w("fa/sub/__init__.py")
    w("fa/sub/entry.py", "import fa.a\n\ndef f():\n    import fb.x\n    from . import b\n")
    w("fa/a.py")
    w("fa/sub/b.py")
    w("fb/__init__.py")
    w("fb/x.py", "import fb.y\n\ndef g():\n    import fb.z\n")
    w("fb/y.py")
    w("fb/z.py")
    fams = {"fa": tmp_path, "fb": tmp_path}
    files, met = _closure("fa.sub.entry", families=fams, root=tmp_path)
    # 자기 가족의 함수 안 import(fb.x · 상대 import b)는 따라가고, 위 패키지 __init__ 도 센다 —
    # 다른 가족 모듈(fb.x)은 최상위(fb.y)만: 함수 안(fb.z)은 이 유닛이 부른다는 근거가 없다
    assert files == {"fa/__init__.py", "fa/sub/__init__.py", "fa/sub/entry.py", "fa/a.py",
                     "fa/sub/b.py", "fb/__init__.py", "fb/x.py", "fb/y.py"}, sorted(files)
    assert met == set()
    cut = frozenset({("fa/sub/entry.py", "fb/x.py")})
    files, met = _closure("fa.sub.entry", cut, families=fams, root=tmp_path)
    assert not any(f.startswith("fb/") for f in files) and met == set(cut), (sorted(files), met)
