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
  - server(대시보드): 재시작해도 잃는 건 메모리 캐시뿐이다(맺어 둔 연결이 없다) → 폐포가 닿는 **가족 전체**(trade·bot 은 최상위 모듈과
    그 가족의 `data/`, tradingagents 는 패키지 전부)와 폐포의 하위 패키지 파일을 덮는다 — NOAH
    대시보드 2026-06-11 선례("bot/*.py 전체로 넓혀 클래스 자체 제거"). `data/` 는 코드가 아니라
    폐포로는 못 재지만 메모리에 캐시된다(`_family_files` 독스트링의 실례).
  - listener(Telethon 중계): 폐포를 덮되 다른 가족으로 가는 간선은 **사유와 함께** 끊을 수 있다
    (리스너 경로가 그 함수를 부르지 않을 때만 — 재시작마다 연결을 다시 맺으므로 NOAH 배포마다
    재시작하지 않으려고다, #411). 새 간선이 생기면 폐포가 규칙 밖으로 나가 실패한다 — 결정하라는
    뜻이다(#411 의 `bot_mods == {"bot/market.py"}` 를 일반화했다).
  - bot(stock-bot · trade-bot): 게이트를 통과한 배포마다 무조건 재시작한다 → 게이트가 폐포를 덮는다.
trade 쪽 조건부 재시작은 `TRADE_RELEVANT` 게이트를 통과해야 닿으므로 실효 조건 = 게이트 ∧ 규칙이다.

배선은 소스로만 재지 않는다 — 두 배포 스크립트를 임시 저장소에서 가짜 sudo·systemctl 로 **실제로
돌려** 재시작된 유닛이 예측(`_covered`)과 같은 집합인지 본다(파일 끝 `_Deploy`, 독립 리뷰 #423 M2 —
소스만 읽던 옛 판은 조건 반전·재대입·diff 범위·else 로 옮김 같은 스크립트 뮤테이션 20종을 전부
통과시켰다). 가짜 sudo 는 `-n` systemctl 을 설치기의 권한 줄과 글자 그대로 대조한다.

⚠️ 못 보는 축(#274): 동적 import(`importlib`·`__import__` — 대시보드와 DAJU 는 가족 전체를 덮어 같은
가족 안이면 무해하고, 두 trade 리스너의 폐포엔 지금 없다, 실측) · 다른 가족 모듈의 **함수 안** import 가
실제 경로에 있는 경우(그 import 가 유닛이 덮는 가족 안에 머물면 무해 — 덮지 않는 가족으로 번지면 못
본다 · 리스너는 그 간선을 끊는 순간 적는 사유가 그 판단이다) · 파일 **이름을 소스에 안 적고**
조립해 읽는 repo 데이터(`_data_read_by` 는 `trade/data`·`bot/data` 를 이름으로 찾는다 — 서버는 가족의
`data/` 전체를 덮어 무해 · 두 trade 리스너의 폐포가 읽는 repo 파일은 `trade/scripts/requirements.txt`
하나다(`tg_entities.pinned_telethon` 이 부를 때마다 읽어 캐시하지 않는다 — 재시작이 필요 없다) · 나머지는
런타임 디렉터리 `~/.trade` 다, 실측) · 진짜 systemd 의 시간(재시작 뒤 생존 확인은 3초 창이다 — 그 뒤에
죽는 프로세스는 배포 알림이 말하지 못한다) · 진짜 sudoers 의 의미(가짜 sudo 는 권한 줄을 글자
그대로만 대조한다 — 와일드카드는 이 레포 설치기가 쓰지 않는다).
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
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


def _is_main_guard(node: ast.AST) -> bool:
    """`if __name__ == "__main__":` (양변 순서 무관)."""
    t = node.test if isinstance(node, ast.If) else None
    if not (isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq)):
        return False
    sides = (t.left, t.comparators[0])
    return (any(isinstance(x, ast.Name) and x.id == "__name__" for x in sides)
            and any(isinstance(x, ast.Constant) and x.value == "__main__" for x in sides))


def _imports(f: Path, families: dict, *, entry: bool = False) -> list[tuple[Path, bool]]:
    """`f` 가 import 하는 가족 모듈 파일과 그 import 가 **함수 안인가**. 위 패키지들의
    `__init__.py` 는 `f` 를 import 하는 순간 실행되므로 최상위로 센다. `if __name__ ==
    "__main__":` 블록은 그 모듈이 **진입점일 때만** 돈다 — 진입점이 아닌 모듈의 그 블록은
    세지 않는다(독립 리뷰 #423 L4: `bot/dart_feed.py` 의 CLI 블록이 trade 대시보드 폐포에
    `bot/dashboard.py`·`bot/archive.py`·`bot/naver_diag.py` 를 넣고 있었다)."""
    base = families[_family(f, families)]
    parts = f.relative_to(base).parts
    pkg = parts[:-1]
    out: list[tuple[Path, bool]] = []

    def visit(node: ast.AST, lazy: bool) -> None:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                visit(ch, True)
                continue
            if not entry and _is_main_guard(ch):
                visit(ast.Module(body=ch.orelse, type_ignores=[]), lazy)   # else 가지는 import 때 돈다
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
        for p, lazy in _imports(f, families, entry=f == start):
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


def _code(sh: str) -> str:
    """주석 줄을 비운 스크립트(줄 위치·들여쓰기는 그대로) — 주석에 적힌 명령을 배선으로 세지
    않는다(독립 리뷰 #423 M2: `# TODO: sudo -n … restart daju-listener` 가 배선으로 통과했다)."""
    return "\n".join("" if ln.lstrip().startswith("#") else ln for ln in sh.split("\n"))


def _trade_var(var: str) -> re.Pattern:
    """`VAR=$(echo "$CHANGED_FILES" | grep -E '…' || true)` 의 정규식."""
    m = re.search(rf"""^{var}=\$\(echo "\$CHANGED_FILES" \| grep -E '([^']+)' \|\| true\)""",
                  _code(_script(_TRADE_SH)), re.M)
    assert m, f"{var} 줄의 모양이 바뀌었다 — 이 회귀를 같이 고칠 것"
    return re.compile(m.group(1))


def _noah_var(var: str) -> re.Pattern:
    """`if grep -qE '…' <<<"$PULLED_FILES"; then` 다음 줄 `VAR=1` 의 정규식."""
    m = re.search(rf"""^[ \t]*if grep -qE '([^']+)' <<<"\$PULLED_FILES"; then[ \t]*\n[ \t]*{var}=1[ \t]*$""",
                  _code(_script(_NOAH_SH)), re.M)
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
    """`head` 로 시작하는 if 줄부터 **같은 들여쓰기의** `else`/`elif`/`fi` 까지 — then 가지만
    (독립 리뷰 #423 M2: `fi` 까지 자르면 재시작 호출을 else 가지로 옮겨도 통과했다)."""
    code = _code(sh)
    m = re.search(rf"^(?P<ind>[ \t]*){re.escape(head)}[ \t]*$", code, re.M)
    assert m, f"{head!r} 가 없다 — 재시작 배선을 찾을 수 없다"
    end = re.compile(rf"^{m.group('ind')}(?:fi|else|elif)\b", re.M).search(code, m.end())
    assert end, f"{head!r} 블록의 끝(fi/else/elif)을 못 찾았다"
    return code[m.end():end.start()]


def _restarts(sh: str, block: str, unit: str) -> bool:
    """블록이 그 유닛을 재시작하나 — 직접 또는 블록이 부르는 함수를 거쳐(주석 줄은 빼고)."""
    rx = re.compile(rf"\bsudo(?: -n)? /bin/systemctl (?:try-)?restart {re.escape(unit)}(?![\w.-])")
    if rx.search(block):
        return True
    code = _code(sh)
    for name in re.findall(r"^[ \t]*(\w+)[ \t]*$", block, re.M):     # 인자 없는 함수 호출 줄
        body = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", code, re.M | re.S)
        if body and rx.search(body.group(1)):
            return True
    return False


# ── sudoers ───────────────────────────────────────────────────────────────────
# `sudo -n` 으로 부르는 systemctl 명령 전체(리다이렉트·연산자 앞까지) — sudoers 는 인자까지
# **글자 그대로** 대조한다(`restart x.service`·`--no-block` 하나만 달라도 거절된다)
_SUDO_N = re.compile(r"\bsudo -n (/bin/systemctl(?: (?![0-9]*[<>])[^\s;&|()<>]+)+)")


def _grants(lines: list[str]) -> set[str]:
    """higgack 이 비밀번호 없이 부를 수 있는 명령 — 주석·다른 사용자 줄은 권한이 아니다."""
    out = set()
    for ln in lines:
        m = re.fullmatch(r"\s*higgack\s+ALL=\((?:root|ALL)\)\s+NOPASSWD:\s*(\S.*?)\s*", ln)
        if m:
            out.add(m.group(1))
    return out


def _sudoers_writers() -> dict[str, list[set[str]]]:
    """drop-in 경로 → 그 파일을 **쓰는 설치기마다** 허락하는 명령. 같은 파일을 여러 설치기가
    번갈아 쓰면 마지막에 쓴 쪽만 남는다(독립 리뷰 #423 L6 — `higgack-trade-services` 를
    install.sh 와 install-trade-units.sh 가 둘 다 쓴다)."""
    out: dict[str, list[set[str]]] = {}
    inst = _script(_ROOT / "deploy" / "install.sh")
    blocks = re.findall(r"^(\w+)=(/etc/sudoers\.d/[\w.-]+)\n(\w+)=\"\$\(mktemp\)\"\n"
                        r"cat > \"\$\3\" <<'SUDOERS'\n(.*?)^SUDOERS$", inst, re.M | re.S)
    assert len(blocks) >= 2, "install.sh 의 sudoers 조각 모양이 바뀌었다 — 이 회귀를 같이 고칠 것"
    for _, dest, _, body in blocks:
        out.setdefault(dest, []).append(_grants(body.splitlines()))
    units = _script(_ROOT / "deploy" / "install-trade-units.sh")
    dest = re.search(r'^SUDOERS_DEST="(/etc/sudoers\.d/[\w.-]+)"', units, re.M)
    arr = re.search(r"^SUDOERS_LINES=\((.*?)^\)", units, re.M | re.S)
    assert dest and arr, "install-trade-units.sh 의 sudoers 모양이 바뀌었다 — 이 회귀를 같이 고칠 것"
    out.setdefault(dest.group(1), []).append(
        _grants(re.findall(r'^[ \t]*"([^"]*)"[ \t]*$', arr.group(1), re.M)))    # 주석 줄은 제외된다
    return out


def _effective_grants(writers: dict[str, list[set[str]]] | None = None) -> set[str]:
    """어느 설치기가 마지막에 돌았든 남아 있는 권한 — drop-in 마다 쓰는 쪽 모두가 허락하는 것."""
    ws = _sudoers_writers() if writers is None else writers
    return set().union(*(set.intersection(*w) for w in ws.values()))


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
        assert re.search(rf"\bsudo /bin/systemctl restart {re.escape(unit)}(?![\w.-])", _code(sh)), unit
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


def test_every_best_effort_restart_is_granted_by_every_writer_of_its_drop_in():
    """`sudo -n` 재시작은 NOPASSWD 줄과 **글자 그대로** 같아야 한다 — 재시작 대상을 늘리면 권한
    줄도 같은 커밋에(설치기는 `deploy/` 가 바뀐 배포에서 자동으로 돌아 drop-in 을 심는다).
    독립 리뷰 #423 M2 가 옛 판(합집합 · `restart [\\w-]+` 로 자름 · 주석·다른 사용자 줄도 인정)
    을 뚫은 변형 넷(`.service` 접미 · `try-restart` · `--no-block` · 권한 줄을 다른 설치기로 옮김)
    이 여기서 걸린다."""
    calls = set()
    for sh in (_NOAH_SH, _TRADE_SH):
        calls |= set(_SUDO_N.findall(_code(_script(sh))))
    assert len(calls) >= 4, f"재시작 호출을 못 읽었다({sorted(calls)}) — 모양이 바뀌었으면 이 회귀를 같이 고칠 것"
    missing = sorted(calls - _effective_grants())
    assert missing == [], f"어느 설치기가 마지막에 돌았든 남는 권한 줄이 없는 재시작: {missing}"


def test_sudoers_parser_is_not_blind():
    """권한 파서의 반대 증거(#25) — 주석·다른 사용자·합집합은 권한이 아니다."""
    assert _grants(["higgack ALL=(root) NOPASSWD: /bin/systemctl restart a",
                    "# higgack ALL=(root) NOPASSWD: /bin/systemctl restart b",
                    "nobody ALL=(root) NOPASSWD: /bin/systemctl restart c"]) == {"/bin/systemctl restart a"}
    writers = _sudoers_writers()
    assert "/etc/sudoers.d/higgack-stock-restart" in writers
    assert len(writers["/etc/sudoers.d/higgack-trade-services"]) == 2, "두 설치기가 같은 파일을 쓴다(L6)"
    assert "/bin/systemctl restart daju-listener" in _effective_grants()
    # 같은 파일을 번갈아 쓰면 **모두가** 허락하는 것만 남는다 · 다른 파일끼리는 합쳐진다
    assert _effective_grants({"f": [{"a", "b"}, {"a"}], "g": [{"c"}]}) == {"a", "c"}


@pytest.mark.parametrize("unit", _CONDITIONAL)
def test_restart_condition_variable_is_assigned_only_where_it_is_computed(unit):
    """조건 변수를 계산한 뒤 다시 대입하면 규칙과 무관하게 재시작이 켜지거나 꺼진다(독립 리뷰
    #423 M2 의 `CODE_CHANGED=0`·`DASHBOARD_RELEVANT=""` 변형)."""
    sh, var = _POLICY[unit][1]
    assigns = re.findall(rf"^[ \t]*(?:export[ \t]+|local[ \t]+|declare[ \t]+\S*[ \t]*)?{var}\+?=(.*)$",
                         _code(_script(sh)), re.M)
    if sh == _NOAH_SH:
        assert sorted(assigns) == ["0", "1"], assigns
    else:
        assert len(assigns) == 1 and assigns[0].startswith('$(echo "$CHANGED_FILES" | grep -E'), assigns


def _default_repo(sh: Path) -> str:
    m = re.search(r'^REPO="\$\{\w+:-([^}]+)\}"', _code(_script(sh)), re.M)
    assert m, f"{sh.name}: REPO 기본값을 못 읽었다"
    return m.group(1)


@pytest.mark.parametrize("unit", _UNITS)
def test_unit_runs_from_the_checkout_its_restart_script_updates(unit):
    """정책 표의 유닛→스크립트 매핑은 손으로 적었다 — 유닛이 도는 체크아웃(`WorkingDirectory`)이
    그 스크립트가 갱신하는 체크아웃과 같은지 파일에서 대조한다(독립 리뷰 #423 L8)."""
    txt = (_ROOT / "deploy" / f"{unit}.service").read_text(encoding="utf-8")
    wd = re.search(r"^WorkingDirectory=(\S+)", txt, re.M)
    assert wd, unit
    assert wd.group(1) == _default_repo(_POLICY[unit][1][0]), (unit, wd.group(1))


def test_family_files_cover_the_family_data_dir():
    """서버의 '가족 전체' 에 `data/` 가 들어 있다(오늘은 `_data_read_by` 가 같은 4개를 덮어
    이것을 빼도 위 회귀가 통과한다 — 독립 리뷰 #423 H4)."""
    for fam in ("trade", "bot"):
        data = {_rel(p, _ROOT) for p in (_ROOT / fam / "data").rglob("*") if p.is_file()}
        assert data <= _family_files(fam), fam
    assert any(f.startswith("trade/data/") for f in _family_files("trade"))


def test_wiring_helpers_ignore_comments_and_else_branches():
    """배선 헬퍼 자체의 계약(#91 — 헬퍼가 눈멀면 위 배선 회귀가 전부 통과한다): 주석에 적힌 명령은
    배선이 아니고, then 가지 밖(else)의 재시작은 그 조건의 재시작이 아니다."""
    sh = ('f() {\n    # sudo -n /bin/systemctl restart u\n    :\n}\n'
          'if [ "$X" = "1" ]; then\n    echo hi\nelse\n    sudo -n /bin/systemctl restart u\nfi\n'
          'if [ "$Y" = "1" ]; then\n    f\nfi\n'
          'if [ "$Z" = "1" ]; then\n    sudo -n /bin/systemctl restart u 2>&1\nfi\n')
    assert not _restarts(sh, _if_block(sh, 'if [ "$X" = "1" ]; then'), "u")    # else 가지
    assert not _restarts(sh, _if_block(sh, 'if [ "$Y" = "1" ]; then'), "u")    # 주석뿐인 함수
    assert _restarts(sh, _if_block(sh, 'if [ "$Z" = "1" ]; then'), "u")        # 반대 증거
    assert _SUDO_N.findall(_code(sh)) == ["/bin/systemctl restart u"] * 2     # 주석 속 명령은 빼고


def test_closure_follows_own_family_lazy_imports_but_only_top_level_of_others(tmp_path):
    """폐포 규칙 자체를 잰다(#91 — 폐포가 눈멀면 위 회귀가 전부 통과한다)."""
    def w(rel: str, src: str = "") -> None:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")

    w("fa/__init__.py")
    w("fa/sub/__init__.py")
    w("fa/sub/entry.py", "import fa.a\n\ndef f():\n    import fb.x\n    from . import b\n"
                         "\nif __name__ == '__main__':\n    import fa.cli\n")
    w("fa/a.py", "if __name__ == '__main__':\n    import fa.selftest\nelse:\n    import fa.lib\n")
    w("fa/cli.py")
    w("fa/selftest.py")
    w("fa/lib.py")
    w("fa/sub/b.py")
    w("fb/__init__.py")
    w("fb/x.py", "import fb.y\n\ndef g():\n    import fb.z\n")
    w("fb/y.py")
    w("fb/z.py")
    fams = {"fa": tmp_path, "fb": tmp_path}
    files, met = _closure("fa.sub.entry", families=fams, root=tmp_path)
    # 자기 가족의 함수 안 import(fb.x · 상대 import b)는 따라가고, 위 패키지 __init__ 도 센다 —
    # 다른 가족 모듈(fb.x)은 최상위(fb.y)만: 함수 안(fb.z)은 이 유닛이 부른다는 근거가 없다.
    # `__main__` 블록은 진입점(entry → fa.cli)에서만 돌고, 다른 모듈(fa.a → fa.selftest)에선
    # 안 돈다 — 그 else 가지(fa.lib)는 import 할 때 돈다(L4)
    assert files == {"fa/__init__.py", "fa/sub/__init__.py", "fa/sub/entry.py", "fa/a.py",
                     "fa/cli.py", "fa/lib.py",
                     "fa/sub/b.py", "fb/__init__.py", "fb/x.py", "fb/y.py"}, sorted(files)
    assert met == set()
    cut = frozenset({("fa/sub/entry.py", "fb/x.py")})
    files, met = _closure("fa.sub.entry", cut, families=fams, root=tmp_path)
    assert not any(f.startswith("fb/") for f in files) and met == set(cut), (sorted(files), met)


# ── 스크립트를 실제로 돌린다(독립 리뷰 #423 M2) ──────────────────────────────────
# 위 회귀는 소스를 읽어 잰다 — 셸 **동작**(조건 반전 · 재대입 · diff 범위 · 조기 exit · 호출을 else
# 로 옮김 · 매 tick 무조건 재시작 · 권한 줄과 한 글자 다른 명령)은 못 본다. 리뷰가 만든 스크립트
# 뮤테이션 20종이 옛 판을 전부 통과했다. 임시 bare origin 과 배포 체크아웃을 두고 두 스크립트를
# 가짜 sudo·systemctl·sleep·curl 로 돌려, 재시작된 유닛이 `_covered` 의 예측과 **같은 집합**인지
# 본다. 가짜 sudo 는 `-n` systemctl 을 설치기의 권한 줄(`_effective_grants`)과 글자 그대로 대조한다.
_FAKES = {
    "sudo": """#!/bin/bash
state="$FAKE_STATE"
nonint=0
if [ "${1:-}" = "-n" ]; then nonint=1; shift; fi
cmd="$*"
case "$cmd" in
    /bin/systemctl\\ *)
        if [ "$nonint" = 1 ] && ! grep -qxF -- "$cmd" "$state/grants"; then
            printf 'denied %s\\n' "$cmd" >> "$state/sudo.log"
            echo "sudo: a password is required" >&2
            exit 1
        fi
        if grep -qxF -- "$cmd" "$state/fail"; then
            printf 'failed %s\\n' "$cmd" >> "$state/sudo.log"
            echo "Job for x.service failed <code> & more" >&2
            exit 1
        fi
        unit="${cmd##* }"
        if grep -qxF -- "$unit" "$state/dies"; then
            printf '%s\\n' "$unit" >> "$state/inactive"
        else
            grep -vxF -- "$unit" "$state/inactive" > "$state/inactive.new" || true
            mv "$state/inactive.new" "$state/inactive"
        fi
        ;;
    *) [ -n "${FAKE_INSTALL_OUT:-}" ] && printf '%s\\n' "$FAKE_INSTALL_OUT" ;;   # 설치기 출력
esac
printf 'ok %s\\n' "$cmd" >> "$state/sudo.log"
exit 0
""",
    "systemctl": """#!/bin/bash
case "${1:-}" in
    is-active)
        if grep -qxF -- "${!#}" "$FAKE_STATE/inactive"; then exit 3; fi ;;
    show) printf '%s\\n' "${FAKE_BOT_START:-}" ;;
esac
exit 0
""",
    "sleep": "#!/bin/bash\nexit 0\n",
    "curl": """#!/bin/bash
for a in "$@"; do
    case "$a" in text=*) printf '%s\\n' "${a#text=}" >> "$FAKE_STATE/notify.log" ;; esac
done
echo '{"ok":true}'
""",
}
_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


class _Deploy:
    """배포 스크립트 하나를 임시 저장소에서 돌린다 — 운영 경로는 환경 변수로 갈아 끼운다
    (`STOCK_*`·`TRADE_*`, 기본값이 운영 경로다)."""

    def __init__(self, tmp: Path, script: Path):
        if not (shutil.which("bash") and shutil.which("git")):
            pytest.skip("bash·git 이 없다 — 이 환경에선 스크립트를 돌릴 수 없다")
        self.script, self.n = script, 0
        self.origin, self.author, self.repo = tmp / "origin.git", tmp / "author", tmp / "repo"
        self.state, self.bin = tmp / "state", tmp / "bin"
        self.state.mkdir()
        self.bin.mkdir()
        for name, src in _FAKES.items():
            (self.bin / name).write_text(src, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        self._git(tmp, "init", "-q", "--bare", "-b", "base", str(self.origin))
        self._git(tmp, "init", "-q", "-b", "base", str(self.author))
        self._git(self.author, "remote", "add", "origin", str(self.origin))
        self.commit(["README"])
        self._git(tmp, "clone", "-q", "-b", "base", str(self.origin), str(self.repo))
        (self.repo / ".env").write_text("TELEGRAM_BOT_TOKEN=t\nCHANNEL_CHAT_IDS=1\n"
                                        "TRADE_BOT_TOKEN=t\nTRADE_CHANNEL_CHAT_IDS=1\n", encoding="utf-8")

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        r = subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **_GIT_ENV},
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (args, r.stderr)
        return r.stdout.strip()

    def commit(self, paths) -> None:
        self.n += 1
        for rel in paths:
            f = self.author / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{self.n}\n", encoding="utf-8")
        self._git(self.author, "add", "-A")
        self._git(self.author, "commit", "-q", "-m", f"change {self.n}")
        self._git(self.author, "push", "-q", "origin", "base")

    def run(self, changed=(), *, inactive=(), dies=(), fail=(), deny=(), bot_start=None,
            install_out=""):
        """`changed` 를 바꾼 커밋 하나를 push 하고 스크립트를 한 번 돌린다 → (결과, 재시작된
        유닛, sudo 기록, 알림 본문, 반영됐나)."""
        # 앞 시나리오가 중간에 멈췄어도 이번 diff 가 이번 커밋만 되게 체크아웃을 맞춰 둔다
        self._git(self.repo, "fetch", "-q", "origin", "base")
        self._git(self.repo, "reset", "-q", "--hard", "origin/base")
        grants = _effective_grants() - set(deny)
        for name, items in (("grants", grants), ("inactive", inactive), ("dies", dies), ("fail", fail),
                            ("sudo.log", ()), ("notify.log", ())):
            (self.state / name).write_text("".join(f"{i}\n" for i in sorted(items)), encoding="utf-8")
        if changed:
            self.commit(changed)
        env = {**os.environ, **_GIT_ENV, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
               "FAKE_STATE": str(self.state), "STOCK_REPO": str(self.repo), "STOCK_BRANCH": "base",
               "STOCK_BUSY_MARKER": str(self.state / "busy"), "TRADE_REPO": str(self.repo),
               "TRADE_BRANCH": "base"}
        env.pop("FAKE_BOT_START", None)
        if bot_start:
            env["FAKE_BOT_START"] = bot_start
        env["FAKE_INSTALL_OUT"] = install_out
        r = subprocess.run(["bash", str(self.script)], cwd=self.repo, env=env,
                           capture_output=True, text=True, timeout=120)
        log = (self.state / "sudo.log").read_text(encoding="utf-8").splitlines()
        restarted = {ln.split()[-1] for ln in log
                     if re.match(r"ok /bin/systemctl (?:try-)?restart \S+$", ln)}
        notes = (self.state / "notify.log").read_text(encoding="utf-8")
        applied = (self._git(self.repo, "rev-parse", "HEAD")
                   == self._git(self.author, "rev-parse", "HEAD"))
        return r, restarted, log, notes, applied


def _predict(sh: Path, changed) -> set[str]:
    return {u for u in _UNITS if _POLICY[u][1][0] == sh and any(_covered(u)(f) for f in changed)}


# 유닛마다 재시작시키는 표본과 안 시키는 표본이 다 있다(아래 회귀가 확인한다). 한글 파일 이름은
# git 이 기본값(`core.quotePath`)으로 따옴표를 씌우는 경로다(L3). NOAH 쪽은 `deploy/*` 를 넣지
# 않는다 — 그 경로는 install.sh 출력을 /tmp 에 쓴다.
_SAMPLES = {
    _NOAH_SH: ("bot/dart_client.py", "bot/daju_parse.py", "bot/scripts/probe_progress.py",
               "bot/한글모듈.py",
               "trade/kg_candidates.py", "trade/data/reinforce_approved.csv",
               "TradingAgents/tradingagents/agents/utils/agent_utils.py",
               "trade/scripts/listen_beon.py", "bot/tests/test_dart_feed.py",
               "TradingAgents/tests/test_x.py", "docs/tests.md"),
    _TRADE_SH: ("bot/dart_client.py", "trade/tg_entities.py", "trade/scripts/listen_beon.py",
                "trade/한글모듈.py",
                "trade/scripts/listen_badonion.py", "trade/scripts/__init__.py",
                "trade/scripts/backfill_beon.py", "trade/data/hs_names.tsv",
                "trade/tests/test_relay_origins.py", "bot/scripts/probe_progress.py",
                "deploy/trade-bot-dashboard.service", "docs/tests.md"),
}


@pytest.mark.parametrize("sh", [_NOAH_SH, _TRADE_SH], ids=["noah", "trade"])
def test_script_restarts_exactly_the_predicted_units(sh, tmp_path):
    d = _Deploy(tmp_path, sh)
    bad = []
    for f in _SAMPLES[sh]:
        r, restarted, log, _, applied = d.run([f])
        want = _predict(sh, [f])
        if r.returncode != 0 or restarted != want or not applied:
            bad.append({"변경": f, "rc": r.returncode, "재시작": sorted(restarted), "예측": sorted(want),
                        "반영": applied, "sudo": log, "stderr": r.stderr[-300:]})
    assert bad == [], bad
    for u in (u for u in _CONDITIONAL if _POLICY[u][1][0] == sh):
        hits = [f for f in _SAMPLES[sh] if u in _predict(sh, [f])]
        assert hits and len(hits) < len(_SAMPLES[sh]), f"{u}: 표본이 재시작·비재시작을 다 태우지 않는다"


def test_noah_vm_direct_push_restarts_every_noah_unit_and_an_idle_tick_restarts_none(tmp_path):
    d = _Deploy(tmp_path, _NOAH_SH)
    noah = {u for u in _UNITS if _POLICY[u][1][0] == _NOAH_SH}
    r, restarted, log, _, _ = d.run(bot_start="2000-01-01 00:00:00 UTC")    # HEAD 가 봇 기동보다 새것
    assert r.returncode == 0 and restarted == noah, (restarted, log, r.stderr[-300:])
    r, restarted, log, notes, _ = d.run(bot_start="2099-01-01 00:00:00 UTC")  # 할 일 없는 tick
    assert r.returncode == 0 and log == [] and notes == "", (log, notes)


def test_trade_idle_tick_restarts_nothing(tmp_path):
    d = _Deploy(tmp_path, _TRADE_SH)
    r, restarted, log, notes, _ = d.run()
    assert r.returncode == 0 and log == [] and notes == "", (log, notes)


def test_daju_restart_outcomes_are_told_apart(tmp_path):
    """돌고 있지 않으면 되살리지 않는다 · 권한 없음 · 기동에서 죽음 · 재시작 자체 실패 — 처방이
    다르므로 알림이 갈래를 말한다(M1·L5). 오류 원문은 HTML 이스케이프해 싣는다(실수 #7)."""
    d = _Deploy(tmp_path, _NOAH_SH)
    f = ["bot/daju_parse.py"]
    r, restarted, _, notes, _ = d.run(f, inactive=["daju-listener"])
    assert "daju-listener" not in restarted and "비활성" in r.stdout and "DAJU" not in notes
    r, restarted, _, notes, _ = d.run(f, deny=["/bin/systemctl restart daju-listener"])
    assert "daju-listener" not in restarted and "NOPASSWD 권한 부재" in notes, notes
    r, restarted, _, notes, _ = d.run(f, dies=["daju-listener"])
    assert "daju-listener" in restarted and "DAJU 리스너 재시작 후 active 아님" in notes, notes
    assert "NOPASSWD" not in notes
    r, restarted, _, notes, _ = d.run(f, fail=["/bin/systemctl restart daju-listener"])
    assert "&lt;code&gt; &amp; more" in notes and "<code> & more" not in notes, notes
    assert "NOPASSWD" not in notes
    r, restarted, _, notes, _ = d.run(f)
    assert "daju-listener" in restarted and "also restarted daju-listener" in r.stdout
    assert "DAJU" not in notes, notes


def test_trade_restarted_unit_that_dies_is_named_in_the_deploy_message(tmp_path):
    """형제 재시작도 살아 있는지 본다 — 새 코드가 기동에서 죽으면 systemd 가 조용히 다시 띄울
    뿐이다(M1 의 형제). 살아 있으면 아무 말도 붙이지 않는다(#25)."""
    d = _Deploy(tmp_path, _TRADE_SH)
    r, restarted, _, notes, _ = d.run(["trade/tg_entities.py"], dies=["trade-bot-beon-listener"])
    assert r.returncode == 0 and "trade-bot-beon-listener" in restarted
    assert "trade-bot-beon-listener 재시작 후 active 아님" in notes, notes
    assert "trade-bot-dashboard 재시작 후" not in notes
    r, restarted, _, notes, _ = d.run(["trade/tg_entities.py"])
    assert "active 아님" not in notes and "배포 완료" in notes, notes


def test_trade_deploy_survives_an_installer_that_changed_nothing(tmp_path):
    """설치기(`install-trade-units.sh`)는 바꿀 게 없으면 SUMMARY 줄 없이 "no changes" 로 끝난다
    (`exit 0`). 옛 판은 그 출력에서 SUMMARY 를 grep 하다 `set -eo pipefail` 로 **trade-bot 재시작
    전에** 스크립트를 끝냈다 — 다음 tick 은 LOCAL==REMOTE 라 새 코드가 영영 안 실린다(이 동작
    회귀가 첫 실행에서 찾았다, #87a)."""
    d = _Deploy(tmp_path, _TRADE_SH)
    for out in ("install-trade-units: no changes",
                "install-trade-units: SUMMARY changed=1 new_timers=0 restarted=0 sudoers_installed=0"):
        r, restarted, log, notes, applied = d.run(["deploy/trade-bot-dashboard.service"], install_out=out)
        assert r.returncode == 0 and applied, (out, r.stderr[-300:])
        assert {"trade-bot", "trade-bot-dashboard"} <= restarted, (out, log)
        assert f"ok {d.repo}/deploy/install-trade-units.sh" in log, log
    assert "SUMMARY changed=1" in notes, notes
