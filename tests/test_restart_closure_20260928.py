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
유닛마다 bot 131~157 · TradingAgents 58(전부) 모듈이 된다 — trade 유닛 넷은 bot 131, NOAH 셋은
133·154·157, 실측). 모듈을 import 하면 **위 패키지들의 `__init__.py`** 도 돈다(진입점 `-m trade.scripts.x`
가 실행하는 `trade/scripts/__init__.py` — #411 L9).

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
조건은 폐포보다 **넓을** 수 있다(이름 대신 디렉터리째 덮는 등) — 그 부류는 `_BROAD` 에 사유와 함께 선언하고,
선언 밖으로 넓어지면 실패한다(델타 리뷰 L①: 하네스의 예측은 같은 정규식에서 나와 조건을 **넓히는** 변형은
예측도 같이 넓어져 통과했다).

배선은 소스로만 재지 않는다 — 두 배포 스크립트를 임시 저장소에서 가짜 sudo·systemctl 로 **실제로
돌려** 재시작된 유닛이 예측(`_covered`)과 같은 집합인지 본다(파일 끝 `_Deploy`, 독립 리뷰 #423 M2 —
소스만 읽던 옛 판은 조건 반전·재대입·diff 범위·else 로 옮김 같은 스크립트 뮤테이션 20종을 전부
통과시켰다). 가짜 sudo 는 `-n` 명령 **전부**(설치기 호출 포함)를 설치기들이 남기는 권한 줄(과 운영자
1회 권한 `_OPERATOR_GRANTS`)과 글자 그대로 대조한다 — 옛 판은 systemctl 만 대조하고 설치기 호출은 무조건
통과시켜, 설치기가 자기 권한 줄을 지우는 결함을 가렸다(델타 리뷰 M②).

⚠️ 못 보는 축(#274): 동적 import(`importlib`·`__import__` — 대시보드와 DAJU 는 가족 전체를 덮어 같은
가족 안이면 무해하고, 두 trade 리스너의 폐포엔 지금 없다, 실측) · 다른 가족 모듈의 **함수 안** import 가
실제 경로에 있는 경우(그 import 가 유닛이 덮는 가족 안에 머물면 무해 — 덮지 않는 가족으로 번지면 못
본다 · 리스너는 그 간선을 끊는 순간 적는 사유가 그 판단이다) · 파일 **이름을 소스에 안 적고**
조립해 읽는 repo 데이터(`_data_read_by` 는 `trade/data`·`bot/data` 를 이름으로 찾는다 — 서버는 가족의
`data/` 전체를 덮어 무해 · 두 trade 리스너의 폐포가 읽는 repo 파일은 `trade/scripts/requirements.txt`
하나다(`tg_entities.pinned_telethon` 이 부를 때마다 읽어 캐시하지 않는다 — 재시작이 필요 없다) · 나머지는
런타임 디렉터리 `~/.trade` 다, 실측) · 진짜 systemd 의 시간(재시작 뒤 생존 확인은 3초 창이다 — 그 뒤에
죽는 프로세스는 배포 알림이 말하지 못한다) · 생존 확인이 보는 것은 **기동**뿐이다(재시작을 부른 모듈이
함수 안 import 면 — trade 대시보드의 `bot.dart_client`, DAJU 의 `bot.dashboard` — 첫 요청·메시지 때 올라와, 그
모듈이 깨져도 확인은 통과한다, 델타 리뷰 L⑨) · `-n` 없는 sudo(stock-bot·trade-bot 재시작)는 레포가 쓰지 않는
운영자 권한에 기댄다(SETUP.md 의 `stock-bot` 줄 — trade-bot 줄은 레포에 적힌 곳이 없다) — 가짜 sudo 는 대조하지
않는다. 없으면 재시작이 실패해 "배포 실패" 알림이 나가므로 조용하지는 않다 · 진짜 sudoers 의 의미(가짜 sudo 는 권한 줄을 글자
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
# 폐포(서버는 닿는 가족 전체)보다 **넓게** 재시작하는 부류 — 사유와 함께 선언한다(델타 리뷰 L①). 조건에
# 걸리는 커밋될 파일 중 폐포에도 이 선언에도 없는 것이 있으면 실패하고(조건을 넓혔으면 이유를 적을 것),
# 아무것도 덮지 않는 선언도 실패한다(죽은 선언은 지울 것).
_TRADE_TOP = r"^trade/[^/]+\.py$"
_BROAD: dict[str, dict[str, str]] = {
    "trade-bot-beon-listener": {
        _TRADE_TOP: "리스너가 import 하는 trade 최상위 모듈을 이름으로 적지 않는다 — 새 모듈이 폐포에 들어와도 "
                    "규칙이 따라간다(재시작 사이 올라온 글은 주기 sync 가 회수한다 — trade-auto-update.sh 주석)",
    },
    "trade-bot-badonion-listener": {
        _TRADE_TOP: "BeOn 리스너와 같은 규칙이다(#411 — 재시작 사이 올라온 글은 주기 sync 가 회수한다)",
    },
    "trade-bot-dashboard": {
        r"^trade/scripts/[^/]+\.py$": "리포트·DART 매출 경로가 함수 안에서 부르는 스크립트(`customs_alert`·"
                                      "`probe_dart_revenue` 등)가 사는 곳 — 이름 대신 디렉터리째 덮는다",
        r"^deploy/trade-bot-dashboard[^/]*\.(service|timer)$": "자기 유닛 파일(과 refresh 타이머) — 옛 규칙 그대로",
    },
    "stock-bot-dashboard": {
        r"^bot/(scripts|screener_themes)/[^/]+\.py$":
            "`bot/screener_themes/` 는 레지스트리가 `pkgutil` 로 전부 로드한다(정적 폐포 밖) · `bot/scripts/` 는 "
            "대시보드가 부르는 진단 헬퍼(`probe_progress`)가 사는 곳 — 이름 대신 디렉터리째",
    },
    "daju-listener": {
        r"^bot/[^/]+\.py$": "폐포 안 동적 import(`highlow_render` 의 `importlib.import_module` 셋 · `audit_sweep`·"
                            "`daily_kr_flow` 의 `__import__`)가 정적 폐포로는 못 재는 bot 모듈을 부른다",
        r"^bot/(scripts|screener_themes)/[^/]+\.py$": "폐포 안 `bot/screener_themes/__init__.py` 레지스트리가 "
                                                      "`pkgutil` 로 테마 모듈 전부를 로드한다",
        r"^trade/[^/]+\.py$|^trade/data/|^TradingAgents/tradingagents/.+\.py$":
            "NOAH 대시보드와 한 조건 변수(`CODE_CHANGED`)를 쓴다 — 가르려면 변수가 하나 더 필요하다(재시작 "
            "사이 온 알림은 놓칠 수 있다 — auto-update.sh `restart_daju_listener` 주석이 그 저울질을 적는다)",
    },
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


def _sudoers_writer_lines() -> dict[str, list[tuple[str, list[str]]]]:
    """drop-in 경로 → (그 파일을 쓰는 설치기, 그 설치기가 쓰는 줄 — 순서 그대로). install.sh 의
    heredoc 은 줄마다 개행을 붙여 쓰고, install-trade-units.sh 는 배열 원소를 `printf '%s\\n'` 로 쓰므로
    두 목록이 같으면 파일도 바이트까지 같다."""
    out: dict[str, list[tuple[str, list[str]]]] = {}
    inst = _script(_ROOT / "deploy" / "install.sh")
    blocks = re.findall(r"^(\w+)=(/etc/sudoers\.d/[\w.-]+)\n(\w+)=\"\$\(mktemp\)\"\n"
                        r"cat > \"\$\3\" <<'SUDOERS'\n(.*?)^SUDOERS$", inst, re.M | re.S)
    assert len(blocks) >= 2, "install.sh 의 sudoers 조각 모양이 바뀌었다 — 이 회귀를 같이 고칠 것"
    for _, dest, _, body in blocks:
        out.setdefault(dest, []).append(("install.sh", body.splitlines()))
    units = _script(_ROOT / "deploy" / "install-trade-units.sh")
    dest = re.search(r'^SUDOERS_DEST="(/etc/sudoers\.d/[\w.-]+)"', units, re.M)
    arr = re.search(r"^SUDOERS_LINES=\((.*?)^\)", units, re.M | re.S)
    assert dest and arr, "install-trade-units.sh 의 sudoers 모양이 바뀌었다 — 이 회귀를 같이 고칠 것"
    out.setdefault(dest.group(1), []).append(
        ("install-trade-units.sh", re.findall(r'^[ \t]*"([^"]*)"[ \t]*$', arr.group(1), re.M)))  # 셸 주석 줄은 빠진다
    return out


def _sudoers_writers() -> dict[str, list[set[str]]]:
    """drop-in 경로 → 그 파일을 **쓰는 설치기마다** 허락하는 명령. 같은 파일을 여러 설치기가
    번갈아 쓰면 마지막에 쓴 쪽만 남는다(독립 리뷰 #423 L6 — `higgack-trade-services` 를
    install.sh 와 install-trade-units.sh 가 둘 다 쓴다)."""
    return {dest: [_grants(lines) for _, lines in ws] for dest, ws in _sudoers_writer_lines().items()}


# 레포의 어떤 설치기도 쓰지 않는 운영자 1회 권한 — NOAH 설치기를 root 로 부르는 줄은 그 설치기가 스스로
# 심을 수 없다(심으려면 이미 root 여야 한다). `CLAUDE_REFERENCE.md` 의 1회 setup(`/etc/sudoers.d/
# higgack-stock-deploy`)과 `deploy/auto-update.sh` 의 install.sh 주석이 적는 줄이다. trade 설치기의 줄은
# 이 NOAH 설치기가 심는다(두 설치기가 같은 줄을 같은 순서로 — 델타 리뷰 M②).
_OPERATOR_GRANTS = frozenset({"/home/higgack/stock/deploy/install.sh"})


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


def _tracked_files() -> list[str]:
    """커밋될 파일 전부 — 추적 + add 전 새 파일, 무시 목록 밖(#407·#412)."""
    if not shutil.which("git"):
        pytest.skip("git 이 없다")
    r = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                       cwd=_ROOT, env=_env(), capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return sorted({p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0") if p})


@pytest.mark.parametrize("unit", _CONDITIONAL)
def test_restart_condition_is_no_broader_than_the_closure_and_declared_classes(unit):
    """아래 하네스의 예측은 스크립트의 정규식에서 나온다 — 그래서 조건을 **넓히는** 변형(BeOn 조건에
    `^bot/scripts/…` 를 더함)은 예측도 같이 넓어져 통과했다(델타 리뷰 L① B10). 넓은 부류는 `_BROAD` 에
    사유와 함께 선언하고, 그 밖으로 넓어지면 여기서 걸린다(재시작은 리스너엔 연결을 다시 맺는 값이다)."""
    kind, _, cuts = _POLICY[unit]
    files, _ = _closure(_long_running_units()[unit], frozenset(cuts))
    reach = files | _data_read_by(files)
    if kind == "server":
        for fam in {_family(_ROOT / f, _FAMILIES) for f in files}:
            reach |= _family_files(fam)
    covered = _covered(unit)
    beyond = [f for f in _tracked_files() if covered(f) and f not in reach]
    declared = {rx: re.compile(rx) for rx in _BROAD.get(unit, {})}
    extra = [f for f in beyond if not any(c.search(f) for c in declared.values())]
    assert extra == [], f"{unit}: 폐포에도 `_BROAD` 선언에도 없는데 재시작시키는 파일 {len(extra)}개 {extra[:10]}"
    dead = [rx for rx, c in declared.items() if not any(c.search(f) for f in beyond)]
    assert dead == [], f"{unit}: 아무것도 덮지 않는 선언 — 지울 것 {dead}"
    assert all(why.strip() for why in _BROAD.get(unit, {}).values()), unit


# 하네스가 PATH 맨 앞의 가짜로 가로채는 명령 — 스크립트가 이것을 절대 경로로 부르면 가짜를 건너뛴다
_FAKED = ("sudo", "systemctl", "sleep", "curl")
_ABS_CALL = re.compile(r"(?<![\w/.-])/(?:usr/)?s?bin/(?:" + "|".join(_FAKED) + r")\b")


def _absolute_calls(sh: str) -> list[str]:
    """가로채는 명령을 절대 경로로 부르는 줄(주석 줄은 빼고). `sudo` 의 **인자**로 쓰는 `/bin/systemctl`
    은 가짜 sudo 가 받으므로 뺀다."""
    out = []
    for ln in _code(sh).splitlines():
        if any(not re.search(r"\bsudo(?: -n)?[ \t]+$", ln[:m.start()]) for m in _ABS_CALL.finditer(ln)):
            out.append(ln.strip())
    return out


def test_deploy_scripts_call_faked_commands_through_path():
    """하네스는 두 배포 스크립트를 가짜 sudo·systemctl·sleep·curl 로 돌린다 — 스크립트가 그 명령을 절대
    경로로 부르면 가짜를 건너뛰어, VM 에서 `make test` 가 진짜 운영 유닛을 재시작한다(델타 리뷰 L⑧: 지금은
    0건이지만 막는 것이 PATH 앞자리 하나뿐이었다)."""
    bad = {sh.name: _absolute_calls(_script(sh)) for sh in (_NOAH_SH, _TRADE_SH)}
    assert not any(bad.values()), bad
    # 반대 증거(#25) — 절대 경로 호출은 잡고, sudo 의 인자·주석은 잡지 않는다
    assert _absolute_calls("/bin/systemctl restart x\n  /usr/bin/sudo -n /bin/systemctl restart x\n"
                           "x=$(/usr/bin/curl -s y)\n") == [
        "/bin/systemctl restart x", "/usr/bin/sudo -n /bin/systemctl restart x", "x=$(/usr/bin/curl -s y)"]
    assert _absolute_calls("sudo /bin/systemctl restart x\nerr=$(LC_ALL=C sudo -n /bin/systemctl restart x)\n"
                           "# /bin/systemctl restart x\n") == []


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
    # trade 설치기를 부르는 권한도 어느 설치기가 마지막에 돌았든 남는다(델타 리뷰 M② — 옛 판엔 없었다)
    assert f"{_default_repo(_TRADE_SH)}/deploy/install-trade-units.sh" in _effective_grants()
    # 같은 파일을 번갈아 쓰면 **모두가** 허락하는 것만 남는다 · 다른 파일끼리는 합쳐진다
    assert _effective_grants({"f": [{"a", "b"}, {"a"}], "g": [{"c"}]}) == {"a", "c"}


def test_every_writer_of_a_drop_in_writes_the_same_lines():
    """같은 drop-in 을 여러 설치기가 쓰면 **같은 줄을 같은 순서로** 써야 한다(델타 리뷰 M②). 옛 판은
    `install-trade-units.sh` 가 자기 권한 줄을 빼고 `higgack-trade-services` 를 덮어써, 한 번 돌고 나면
    trade-auto-update.sh 가 `sudo -n` 으로 그 설치기를 못 불렀다(다음 NOAH `deploy/` 배포가 install.sh 로
    되살릴 때까지 — 두 타이머 중 어느 쪽이 먼저 도는지는 커밋마다 우연이다). 내용이 같으면 두 설치기가
    파일을 번갈아 다시 쓰지도 않는다(install.sh 의 `cmp -s` · 설치기의 문자열 대조가 같다고 본다)."""
    shared = {dest: ws for dest, ws in _sudoers_writer_lines().items() if len(ws) > 1}
    assert shared, "여러 설치기가 쓰는 drop-in 을 못 찾았다 — 모양이 바뀌었으면 이 회귀를 같이 고칠 것"
    for dest, ws in shared.items():
        first = ws[0][1]
        assert first and all(lines == first for _, lines in ws), (dest, ws)


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
    # `_SUDO_N` 은 명령 **전체**를 잡는다 — `restart [\w-]+` 로 자르면 `.service` 접미·`--no-block`·
    # `try-restart` 가 권한 줄과 달라도 같아 보인다(델타 리뷰 L⑦ C8: 오늘 호출이 전부 한 모양이라
    # 좁혀도 통과했다). 리다이렉트는 명령이 아니다.
    assert _SUDO_N.findall("sudo -n /bin/systemctl restart x.service --no-block 2>/dev/null\n"
                           "err=$(LC_ALL=C sudo -n /bin/systemctl try-restart y 2>&1)\n") == [
        "/bin/systemctl restart x.service --no-block", "/bin/systemctl try-restart y"]


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
# 본다. 가짜 sudo 는 `-n` 명령 전부를 권한 줄(`_effective_grants` · `_OPERATOR_GRANTS`)과 글자 그대로 대조하고
# (임시 저장소 경로는 그 스크립트의 운영 체크아웃 경로로 되돌려서), 가짜 sleep·`systemctl is-active` 는 같은
# 사건 기록(`events.log`)에 남아 순서를 잰다(델타 리뷰 L⑦ — 옛 가짜 sleep 은 아무것도 안 남겨 `sleep 3` 을
# 지워도 통과했다).
_FAKES = {
    "sudo": """#!/bin/bash
state="$FAKE_STATE"
nonint=0
if [ "${1:-}" = "-n" ]; then nonint=1; shift; fi
cmd="$*"
# 권한 줄은 운영 경로로 적혀 있다 — 임시 저장소 경로를 그 스크립트의 운영 체크아웃 경로로 되돌려 대조한다
want="$cmd"
if [ -n "${FAKE_REPO:-}" ]; then
    case "$cmd" in "$FAKE_REPO"/*) want="${FAKE_PROD_REPO}${cmd#"$FAKE_REPO"}" ;; esac
fi
if [ "$nonint" = 1 ] && ! grep -qxF -- "$want" "$state/grants"; then
    printf 'denied %s\\n' "$cmd" >> "$state/events.log"
    # 진짜 sudo 는 로캘을 따라 번역한다 — 스크립트는 권한 갈래를 영어 문구로 판정하므로 `LC_ALL=C` 로
    # 불러야 한다(델타 리뷰 L⑤). 하네스는 한국어 로캘로 돌려 그 접두가 빠지면 갈래가 틀어지게 한다.
    case "${LC_ALL:-${LC_MESSAGES:-${LANG:-C}}}" in
        C|C.*|POSIX) echo "sudo: a password is required" >&2 ;;
        *) echo "sudo: 암호가 필요합니다" >&2 ;;
    esac
    exit 1
fi
if grep -qxF -- "$cmd" "$state/fail"; then
    printf 'failed %s\\n' "$cmd" >> "$state/events.log"
    echo "Job for x.service failed <code> & more" >&2
    exit 1
fi
case "$cmd" in
    /bin/systemctl\\ *)
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
printf 'ok %s\\n' "$cmd" >> "$state/events.log"
exit 0
""",
    "systemctl": """#!/bin/bash
case "${1:-}" in
    is-active)
        printf 'is-active %s\\n' "${!#}" >> "$FAKE_STATE/events.log"
        if grep -qxF -- "${!#}" "$FAKE_STATE/inactive"; then exit 3; fi ;;
    show) printf '%s\\n' "${FAKE_BOT_START:-}" ;;
esac
exit 0
""",
    "sleep": """#!/bin/bash
printf 'sleep %s\\n' "$*" >> "$FAKE_STATE/events.log"
exit 0
""",
    "curl": """#!/bin/bash
for a in "$@"; do
    case "$a" in text=*) printf '%s\\n' "${a#text=}" >> "$FAKE_STATE/notify.log" ;; esac
done
echo '{"ok":true}'
""",
}
_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def _env(**extra: str) -> dict[str, str]:
    """자식 프로세스 환경 — 부모가 물려준 `GIT_*`(훅이 거는 `GIT_DIR`·`GIT_INDEX_FILE`·`GIT_WORK_TREE`
    등)는 걷어낸다. 남아 있으면 git 이 임시 저장소가 아니라 그 저장소를 건드린다(델타 리뷰 L⑧)."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**base, **_GIT_ENV, **extra}


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
        r = subprocess.run(["git", *args], cwd=cwd, env=_env(), capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (args, r.stderr)
        return r.stdout.strip()

    def commit(self, paths, *, exe=()) -> None:
        self.n += 1
        for rel in paths:
            f = self.author / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{self.n}\n", encoding="utf-8")
            if rel in exe:
                f.chmod(0o755)
        self._git(self.author, "add", "-A")
        self._git(self.author, "commit", "-q", "-m", f"change {self.n}")
        self._git(self.author, "push", "-q", "origin", "base")

    def run(self, changed=(), *, exe=(), inactive=(), dies=(), fail=(), deny=(), bot_start=None,
            install_out=""):
        """`changed` 를 바꾼 커밋 하나를 push 하고 스크립트를 한 번 돌린다 → (결과, 재시작된
        유닛, 사건 기록(sudo · sleep · is-active 순서대로), 알림 본문, 반영됐나). `deny` 는 운영 경로로
        적은 권한 줄을 뺀다 · `fail` 은 스크립트가 실제로 보내는 명령(임시 저장소 경로 그대로)을
        실패시킨다 · `exe` 는 실행 권한을 붙여 커밋한다."""
        # 앞 시나리오가 중간에 멈췄어도 이번 diff 가 이번 커밋만 되게 체크아웃을 맞춰 둔다
        self._git(self.repo, "fetch", "-q", "origin", "base")
        self._git(self.repo, "reset", "-q", "--hard", "origin/base")
        grants = (_effective_grants() | _OPERATOR_GRANTS) - set(deny)
        for name, items in (("grants", grants), ("inactive", inactive), ("dies", dies), ("fail", fail),
                            ("events.log", ()), ("notify.log", ())):
            (self.state / name).write_text("".join(f"{i}\n" for i in sorted(items)), encoding="utf-8")
        if changed:
            self.commit(changed, exe=exe)
        env = _env(PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                   FAKE_STATE=str(self.state), FAKE_REPO=str(self.repo),
                   FAKE_PROD_REPO=_default_repo(self.script), FAKE_INSTALL_OUT=install_out,
                   STOCK_REPO=str(self.repo), STOCK_BRANCH="base", STOCK_BUSY_MARKER=str(self.state / "busy"),
                   STOCK_INSTALL_LOG=str(self.state / "install.log"),
                   TRADE_REPO=str(self.repo), TRADE_BRANCH="base")
        for k in ("FAKE_BOT_START", "LC_ALL", "LC_MESSAGES", "LANGUAGE"):
            env.pop(k, None)
        env["LANG"] = "ko_KR.UTF-8"     # 운영 VM 로캘을 가정하지 않는다 — 번역되는 sudo 를 흉내 낸다(L⑤)
        if bot_start:
            env["FAKE_BOT_START"] = bot_start
        r = subprocess.run(["bash", str(self.script)], cwd=self.repo, env=env,
                           capture_output=True, text=True, timeout=120)
        log = (self.state / "events.log").read_text(encoding="utf-8").splitlines()
        restarted = {ln.split()[-1] for ln in log
                     if re.match(r"ok /bin/systemctl (?:try-)?restart \S+$", ln)}
        notes = (self.state / "notify.log").read_text(encoding="utf-8")
        applied = (self._git(self.repo, "rev-parse", "HEAD")
                   == self._git(self.author, "rev-parse", "HEAD"))
        return r, restarted, log, notes, applied


def _predict(sh: Path, changed) -> set[str]:
    return {u for u in _UNITS if _POLICY[u][1][0] == sh and any(_covered(u)(f) for f in changed)}


def _checked_after_a_pause(log: list[str], unit: str) -> bool:
    """재시작한 유닛의 생존 확인(`is-active`)이 재시작 **뒤에**, 그 사이 `sleep` 을 거쳐 온다(델타 리뷰
    L⑦ — `sleep 3` 을 지워도, 재시작 목록(`RESTARTED_UNITS`)에 싣는 줄을 지워도 통과했다: Type=simple
    유닛은 재시작 직후엔 대개 active 라 기다리지 않으면 확인이 무력하고, 목록에 없으면 확인 자체가 없다)."""
    done = f"ok /bin/systemctl restart {unit}"
    if done not in log:
        return False
    i = log.index(done)
    j = next((k for k in range(i + 1, len(log)) if log[k] == f"is-active {unit}"), None)
    return j is not None and any(ln.startswith("sleep ") for ln in log[i + 1:j])


# 유닛마다 재시작시키는 표본과 안 시키는 표본이 다 있다(아래 회귀가 확인한다). 한글 파일 이름은
# git 이 기본값(`core.quotePath`)으로 따옴표를 씌우는 경로다(L3). NOAH 의 `deploy/*` 표본은 install.sh
# 를 부르는 경로를 탄다 — 임시 저장소엔 install.sh 가 없어 건너뛰고, 그 경로 자체는
# `test_noah_deploy_change_runs_the_installer` 가 잰다(출력 로그는 `STOCK_INSTALL_LOG` 로 임시 디렉터리에).
_SAMPLES = {
    _NOAH_SH: ("bot/dart_client.py", "bot/daju_parse.py", "bot/scripts/probe_progress.py",
               "bot/한글모듈.py",
               "trade/kg_candidates.py", "trade/data/reinforce_approved.csv",
               "TradingAgents/tradingagents/agents/utils/agent_utils.py",
               "trade/scripts/listen_beon.py", "bot/tests/test_dart_feed.py",
               "TradingAgents/tests/test_x.py", "deploy/stock-bot.service", "docs/tests.md"),
    _TRADE_SH: ("bot/dart_client.py", "trade/tg_entities.py", "trade/scripts/listen_beon.py",
                "trade/한글모듈.py",
                "trade/scripts/listen_badonion.py", "trade/scripts/__init__.py",
                "trade/scripts/backfill_beon.py", "trade/data/hs_names.tsv",
                "trade/tests/test_relay_origins.py", "bot/scripts/probe_progress.py",
                "deploy/trade-bot-dashboard.service", "docs/tests.md"),
}
_TRADE_SIBLINGS = {   # trade 형제 유닛 → 배포 알림이 부르는 이름
    "trade-bot-beon-listener": "BeOn 리스너 재시작",
    "trade-bot-badonion-listener": "나쁜양파 리스너 재시작",
    "trade-bot-dashboard": "trade-bot-dashboard 재시작",
}
_TRADE_LISTENERS = sorted(u for u in _TRADE_SIBLINGS if _POLICY[u][0] == "listener")


@pytest.mark.parametrize("sh", [_NOAH_SH, _TRADE_SH], ids=["noah", "trade"])
def test_script_restarts_exactly_the_predicted_units(sh, tmp_path):
    d = _Deploy(tmp_path, sh)
    bad = []
    for f in _SAMPLES[sh]:
        r, restarted, log, _, applied = d.run([f])
        want = _predict(sh, [f])
        unchecked = sorted(u for u in restarted if not _checked_after_a_pause(log, u))
        if r.returncode != 0 or restarted != want or not applied or unchecked:
            bad.append({"변경": f, "rc": r.returncode, "재시작": sorted(restarted), "예측": sorted(want),
                        "반영": applied, "생존확인 없음": unchecked, "기록": log, "stderr": r.stderr[-300:]})
    assert bad == [], bad
    for u in (u for u in _CONDITIONAL if _POLICY[u][1][0] == sh):
        hits = [f for f in _SAMPLES[sh] if u in _predict(sh, [f])]
        assert hits and len(hits) < len(_SAMPLES[sh]), f"{u}: 표본이 재시작·비재시작을 다 태우지 않는다"


def test_noah_vm_direct_push_restarts_every_noah_unit_and_an_idle_tick_restarts_none(tmp_path):
    d = _Deploy(tmp_path, _NOAH_SH)
    noah = {u for u in _UNITS if _POLICY[u][1][0] == _NOAH_SH}
    r, restarted, log, _, _ = d.run(bot_start="2000-01-01 00:00:00 UTC")    # HEAD 가 봇 기동보다 새것
    assert r.returncode == 0 and restarted == noah, (restarted, log, r.stderr[-300:])
    assert all(_checked_after_a_pause(log, u) for u in noah), log
    r, restarted, log, notes, _ = d.run(bot_start="2099-01-01 00:00:00 UTC")  # 할 일 없는 tick
    assert r.returncode == 0 and log == [] and notes == "", (log, notes)


def test_harness_ignores_git_env_inherited_from_a_hook(tmp_path, monkeypatch):
    """git 훅 안에서 `make test` 를 돌리면 `GIT_DIR`·`GIT_WORK_TREE` 가 물려 온다 — 하네스의 git 이 그걸
    따르면 임시 저장소가 아니라 그 저장소에 커밋·reset 한다(델타 리뷰 L⑧). 미끼 저장소는 그대로 비어
    있어야 한다."""
    decoy = tmp_path / "decoy.git"
    subprocess.run(["git", "init", "-q", "--bare", str(decoy)], env=_env(), check=True, capture_output=True)
    monkeypatch.setenv("GIT_DIR", str(decoy))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    (tmp_path / "h").mkdir()
    d = _Deploy(tmp_path / "h", _TRADE_SH)
    r, restarted, log, _, applied = d.run(["trade/tg_entities.py"])
    assert r.returncode == 0 and applied and "trade-bot" in restarted, (log, r.stderr[-300:])
    empty = subprocess.run(["git", "--git-dir", str(decoy), "rev-parse", "--verify", "-q", "HEAD"],
                           env=_env(), capture_output=True)
    assert empty.returncode != 0, "하네스가 물려받은 GIT_DIR 저장소에 커밋했다"


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


@pytest.mark.parametrize("unit", _TRADE_LISTENERS)
def test_trade_listener_that_is_not_running_is_left_alone(unit, tmp_path):
    """멈춰 둔 리스너(미설치·세션 미인증 exit 78·운영자 중지)를 배포가 되살리지 않는다 — NOAH DAJU 와 같은
    가드다(델타 리뷰 M①, #38). 옛 판은 켰고, 이 PR 이 더한 생존 확인이 그때마다 경보까지 붙였다(리뷰
    재현: 켜면 기동에서 죽는 리스너 → `dies`). 재시작하지 않은 비활성 유닛은 생존 확인 대상도 아니다
    (L⑦ B6 — 확인 루프를 고정 목록으로 바꾸면 여기서 걸린다)."""
    d = _Deploy(tmp_path, _TRADE_SH)
    for dies in ((), (unit,)):
        r, restarted, log, notes, _ = d.run(["trade/tg_entities.py"], inactive=[unit], dies=dies)
        assert r.returncode == 0 and unit not in restarted, (restarted, log)
        assert f"{unit} 비활성" in r.stdout, r.stdout
        assert _TRADE_SIBLINGS[unit] not in notes and "active 아님" not in notes, notes
        # 돌고 있는 형제는 그대로 재시작한다
        assert set(_TRADE_SIBLINGS) - {unit} <= restarted, restarted


@pytest.mark.parametrize("unit", sorted(_TRADE_SIBLINGS))
def test_trade_restarted_unit_that_dies_is_named_in_the_deploy_message(unit, tmp_path):
    """형제 재시작도 살아 있는지 본다 — 새 코드가 기동에서 죽으면 systemd 가 조용히 다시 띄울
    뿐이다(M1 의 형제). 형제 셋 모두(델타 리뷰 M③ — 옛 회귀는 BeOn 하나만 태워, 나머지 둘을 재시작
    목록에 싣는 줄을 지워도 통과했다). 살아 있으면 아무 말도 붙이지 않는다(#25)."""
    d = _Deploy(tmp_path, _TRADE_SH)
    r, restarted, _, notes, _ = d.run(["trade/tg_entities.py"], dies=[unit])
    assert r.returncode == 0 and set(_TRADE_SIBLINGS) <= restarted, restarted
    assert f"{unit} 재시작 후 active 아님" in notes, notes
    assert not any(f"{o} 재시작 후" in notes for o in set(_TRADE_SIBLINGS) - {unit}), notes
    r, restarted, _, notes, _ = d.run(["trade/tg_entities.py"])
    assert "active 아님" not in notes and "배포 완료" in notes, notes


def test_trade_bot_death_carries_the_dead_siblings_into_the_failure_notice(tmp_path):
    """trade-bot 자체가 죽으면 "배포 실패" 로 끝난다 — 그 알림에도 함께 죽은 형제의 이름이 실린다(델타
    리뷰 M③ B4: 실패 알림에서 이 줄을 빼도 통과했다)."""
    d = _Deploy(tmp_path, _TRADE_SH)
    r, _, log, notes, _ = d.run(["trade/tg_entities.py"], dies=["trade-bot", "trade-bot-dashboard"])
    assert r.returncode == 1 and "배포 실패" in notes and "배포 완료" not in notes, (r.returncode, notes)
    assert "trade-bot-dashboard 재시작 후 active 아님" in notes, notes


@pytest.mark.parametrize("unit", sorted(_TRADE_SIBLINGS))
def test_trade_sibling_restart_failures_are_told_apart(unit, tmp_path):
    """권한 줄이 없는 것과 재시작 자체가 실패한 것은 처방이 다르다 — 옛 판은 원인과 상관없이 "권한
    없음" 이라 적었다(델타 리뷰 L④, NOAH DAJU 의 L5 를 옮겼다). 권한 부재엔 sudoers 를 다시 심는 명령을,
    실패엔 이스케이프한 원문과 저널 명령을 싣는다(실수 #7)."""
    d = _Deploy(tmp_path, _TRADE_SH)
    what, cmd = _TRADE_SIBLINGS[unit], f"/bin/systemctl restart {unit}"
    r, restarted, _, notes, _ = d.run(["trade/tg_entities.py"], deny=[cmd])
    assert r.returncode == 0 and unit not in restarted, restarted
    assert f"{what} 권한 없음" in notes and "sudo /home/higgack/stock/deploy/install.sh" in notes, notes
    assert f"{what} 실패" not in notes
    r, restarted, _, notes, _ = d.run(["trade/tg_entities.py"], fail=[cmd])
    assert r.returncode == 0 and unit not in restarted, restarted
    assert f"{what} 실패: <code>" in notes and "&lt;code&gt; &amp; more" in notes, notes
    assert "<code> & more" not in notes and f"journalctl -u {unit} -n 30" in notes, notes
    assert "권한 없음" not in notes, notes


def test_trade_deploy_survives_an_installer_that_changed_nothing(tmp_path):
    """설치기(`install-trade-units.sh`)는 바꿀 게 없으면 SUMMARY 줄 없이 "no changes" 로 끝난다
    (`exit 0`). 옛 판은 그 출력에서 SUMMARY 를 grep 하다 `set -eo pipefail` 로 **trade-bot 재시작
    전에** 스크립트를 끝냈다 — 다음 tick 은 LOCAL==REMOTE 라 다음 trade 관련 배포가 올 때까지 새 코드가
    안 실린다(이 동작 회귀가 첫 실행에서 찾았다, #87a). "no changes" 는 알림에 붙이지 않고, 요약 줄도
    그 문구도 아닌 출력은 "자동 설치 완료" 라 우기지 않는다(델타 리뷰 L③ — 옛 판은 둘 다 그렇게 적었다).
    설치기 호출은 권한 줄이 **남아 있어야** 된다(가짜 sudo 가 대조한다 — 델타 리뷰 M②)."""
    d = _Deploy(tmp_path, _TRADE_SH)
    for out, want in (("install-trade-units: no changes", None),
                      ("install-trade-units: SUMMARY changed=1 new_timers=0 restarted=0 sudoers_installed=0",
                       "+ systemd: SUMMARY changed=1"),
                      ("install-trade-units: copied x", "설치기는 돌았는데 요약 줄이 없다")):
        r, restarted, log, notes, applied = d.run(["deploy/trade-bot-dashboard.service"], install_out=out)
        assert r.returncode == 0 and applied, (out, r.stderr[-300:])
        assert {"trade-bot", "trade-bot-dashboard"} <= restarted, (out, log)
        assert f"ok {d.repo}/deploy/install-trade-units.sh" in log, log
        assert "자동 설치 완료" not in notes, notes
        if want is None:
            assert "systemd" not in notes, notes
        else:
            assert want in notes, (out, notes)


def test_trade_installer_that_cannot_run_says_why(tmp_path):
    """설치기를 못 부르면 갈래를 말한다(델타 리뷰 M②·L④) — 권한 줄이 없으면(두 설치기가 권한 줄을 서로
    지우던 옛 상태) sudoers 를 다시 심는 명령을, 설치기가 실패하면 원문을. 어느 쪽이든 trade-bot 은
    재시작한다(설치는 곁가지다)."""
    d = _Deploy(tmp_path, _TRADE_SH)
    prod = f"{_default_repo(_TRADE_SH)}/deploy/install-trade-units.sh"
    r, restarted, log, notes, applied = d.run(["deploy/trade-bot-dashboard.service"], deny=[prod])
    assert r.returncode == 0 and applied and "trade-bot" in restarted, (log, r.stderr[-300:])
    assert f"denied {d.repo}/deploy/install-trade-units.sh" in log, log
    assert "systemd 자동 설치(install-trade-units.sh) 권한 없음" in notes, notes
    r, restarted, log, notes, _ = d.run(["deploy/trade-bot-dashboard.service"],
                                        fail=[f"{d.repo}/deploy/install-trade-units.sh"])
    assert r.returncode == 0 and "trade-bot" in restarted, log
    assert "systemd 자동 설치(install-trade-units.sh) 실패: <code>" in notes, notes
    assert "권한 없음" not in notes, notes


def test_noah_deploy_change_runs_the_installer(tmp_path):
    """`deploy/*.{service,timer,sh}` 가 바뀐 배포는 install.sh 를 부른다(`DEPLOY_CHANGED`) — 이 경로는
    테스트가 없어 조건을 0 으로 박아도 통과했다(델타 리뷰 L⑥ A8). DAJU 권한 부재 알림의 "다음 deploy/ 변경
    때 install.sh 가 설치한다" 가 바로 이 경로에 기댄다. 조건의 **모양**도 잰다(`_noah_var` — 파이프
    `echo … | grep -q` 는 큰 배포에서 SIGPIPE 로 조용히 0 이 된다: 리뷰 L2 · 델타 리뷰 A7 이 그 모양으로
    되돌려도 통과했다). 대시보드 재시작 권한이 없을 때의 자가 치유도 같은 설치기·같은 로그 경로다."""
    assert _noah_var("DEPLOY_CHANGED").search("deploy/stock-bot.service")
    d = _Deploy(tmp_path, _NOAH_SH)
    inst = f"{d.repo}/deploy/install.sh"
    r, _, log, notes, applied = d.run(["deploy/install.sh"], exe=["deploy/install.sh"])
    assert r.returncode == 0 and applied and f"ok {inst}" in log, (log, r.stderr[-300:])
    assert "stock-bot systemd 자동 재설치" in notes and (d.state / "install.log").is_file(), notes
    r, _, log, notes, _ = d.run(["docs/tests.md"])
    assert f"ok {inst}" not in log and "재설치" not in notes, (log, notes)
    r, _, log, notes, _ = d.run(["deploy/stock-bot.service"], deny=[f"{_default_repo(_NOAH_SH)}/deploy/install.sh"])
    assert f"denied {inst}" in log and "재설치 실패" in notes, (log, notes)
    r, _, log, notes, _ = d.run(["bot/dart_client.py"], deny=["/bin/systemctl restart stock-bot-dashboard"])
    assert f"ok {inst}" in log and "self-heal" in notes, (log, notes)
