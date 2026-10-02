"""`get_dart()` 뒤의 `if not dart` 는 키 부재를 못 잡는다 — 실수 #427 (2026-10-02).

`get_dart()` 는 키가 없어도 **항상** `DartClient` 를 돌려주고 그 클래스엔
`__bool__` 이 없다. 그래서 `if not dart:` · `dart is None` 은 늘 거짓이었고,
"DART_API_KEY 없음" 을 말하려던 19곳이 한 번도 그 말을 못 했다 — 메서드가
각자 빈 값을 돌려줘 '원천에 없다'·'정기보고서 미확인' 으로 읽혔다(#82).
6곳은 #426 이 고쳤고 남은 13곳을 `bot.dart_client.dart_ready` 단일 술어로
옮겼다(#38).

계약 셋:
1. 술어 자체 — 키 유무로 판정, `None` 도 받는다, 옛 이름은 위임한다.
2. 제품 경로 — 키 없는 **실물 클래스**(`DartClient("")`)를 넣으면 각 진입점이
   키 부재를 말하고 DART 메서드를 부르지 않는다(스텁이 아니라 실물이어야
   옛 판에서 실패한다 — `__bool__` 없는 그 모양이 결함이다, #155).
3. 전수 회귀 — `bot/`·`trade/` 어디서든 `get_dart()`/`DartClient()` 로 받은
   이름을 **부정 판정**(`not X`·`X is None`)하면 실패. 이름 열거가 아니라
   커밋될 파일 전수(#24·#412). 긍정 분기(`if dart:` 뒤 메서드 호출)는
   허용한다 — 메서드가 키를 스스로 보고, `stock_code_to_name` 처럼 키 없이
   디스크 캐시로 답하는 것이 있어 막으면 그 답을 잃는다.

⚠️ 못 보는 축(#274): 클라이언트를 **인자로 받는** 함수의 `if not dart`
(`backlog_probe`·`quarterly_infographic`·`fcf_audit`)는 호출부가 `None` 을
넘길 수 있어 정당하므로 이 검사 밖이다. 같은 함수 안에서 받은 이름만 본다.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── 1. 술어 ────────────────────────────────────────────────────────────
class TestDartReadyPredicate:
    def test_judges_by_key_not_by_object(self):
        import bot.dart_client as dc
        assert dc.dart_ready(None) is False
        assert dc.dart_ready(dc.DartClient("")) is False      # 객체는 있다
        assert dc.dart_ready(dc.DartClient("k-1234567890")) is True

    def test_real_client_is_truthy_without_key(self):
        """이 결함의 뿌리 — 실물 클래스는 키가 없어도 참이다. 이게 바뀌면
        (누가 `__bool__` 을 넣으면) 이 회귀의 전제를 다시 볼 것."""
        import bot.dart_client as dc
        assert bool(dc.DartClient("")) is True

    def test_backlog_name_delegates(self, monkeypatch):
        """`dart_backlog.dart_ready` 는 **위임**이다 — 복제면 두 술어가 갈린다."""
        import bot.dart_backlog as bl
        import bot.dart_client as dc
        seen = []
        monkeypatch.setattr(dc, "dart_ready",
                            lambda d: seen.append(d) or "sentinel")
        assert bl.dart_ready("x") == "sentinel" and seen == ["x"]


# ── 2. 제품 경로 ───────────────────────────────────────────────────────
@pytest.fixture
def keyless(monkeypatch):
    """키 없는 **실물** `DartClient` — 공개 메서드를 쓰면 기록한다."""
    import bot.dart_client as dc

    used: list[str] = []

    class _Keyless(dc.DartClient):
        def __getattribute__(self, name):
            if not name.startswith("_") and name != "api_key":
                used.append(name)
            return super().__getattribute__(name)

    cli = _Keyless("")
    monkeypatch.setattr(dc, "get_dart", lambda *a, **k: cli)
    return used


def _no_doc_fetch(monkeypatch):
    import bot.dart_feed as df
    monkeypatch.setattr(df, "_fetch_doc_text",
                        lambda *a, **k: pytest.fail("키 없이 원문을 받으러 갔다"))


class TestKeylessEntryPoints:
    @pytest.mark.parametrize("mod", ["backlog_format_probe",
                                     "backlog_format_probe2"])
    def test_backlog_format_probe(self, mod, keyless, monkeypatch, capsys):
        import importlib
        _no_doc_fetch(monkeypatch)
        m = importlib.import_module(f"bot.scripts.{mod}")
        assert m.probe("005930.KS") is None
        assert "DART_API_KEY 없음" in capsys.readouterr().out
        assert keyless == []

    @pytest.mark.parametrize("mod", ["backlog_format_probe3",
                                     "backlog_format_probe4"])
    def test_backlog_format_probe_err(self, mod, keyless, monkeypatch, capsys):
        import importlib
        _no_doc_fetch(monkeypatch)
        m = importlib.import_module(f"bot.scripts.{mod}")
        assert m.probe("005930") == "ERR"
        assert "DART_API_KEY 없음" in capsys.readouterr().out
        assert keyless == []

    @pytest.mark.parametrize("fn", ["probe_dart_accounts", "probe_backlog"])
    def test_detail_gaps_probe(self, fn, keyless, monkeypatch, capsys):
        import bot.scripts.detail_gaps_probe as m
        _no_doc_fetch(monkeypatch)
        getattr(m, fn)("005930.KS")
        assert "DART_API_KEY 없음" in capsys.readouterr().out
        assert keyless == []

    def test_kr_metrics_probe(self, keyless, capsys):
        import bot.scripts.kr_metrics_probe as m
        m._dart_raw("005930.KS", {})
        assert "DART_API_KEY 없음" in capsys.readouterr().out
        assert keyless == []

    def test_kr_revenue_probe(self, keyless, capsys):
        import bot.scripts.kr_revenue_probe as m
        assert m.main(["kr_revenue_probe", "005930.KS"]) == 1
        assert "DART_API_KEY 없음" in capsys.readouterr().out
        assert keyless == []

    def test_production_format_probe(self, keyless, capsys):
        import bot.scripts.production_format_probe as m
        assert m.main(["005930"]) == 1
        assert "DART 키 미설정" in capsys.readouterr().out
        assert keyless == []

    def test_fcf_probe_kr_needs_key(self, keyless, monkeypatch, capsys):
        """옛 판은 `dart is None and has_kr` — KR 이 있으면 dart 를 만드니 늘
        거짓이었다(반환 0, 'DART 경로' 를 빈손으로 돌았다).

        ⚠️ 그렇다고 통째로 멈추면 안 된다(독립 리뷰 M1) — 기본 표본은 시장이
        섞여 있고, KR 도 yfinance 경로는 키 없이 돈다. 섞인 목록에서 **모든
        종목의 yfinance 경로가 찍히고** KR 의 DART 경로만 판정 불가, rc=2."""
        import bot.scripts.fcf_probe as m
        seen = []
        monkeypatch.setattr(m, "_yf_periods",
                            lambda t: seen.append(t) or ([], []))
        assert m.main(["005930.KS", "AAPL"]) == 2
        out = capsys.readouterr().out
        assert "DART_API_KEY 없음" in out and "DART 경로 ❓ 판정 불가" in out
        assert "── AAPL" in out, out                 # KR 뒤의 종목도 본다
        assert {"005930.KS", "AAPL"} <= set(seen)     # KR 도 yfinance 는 탄다
        assert keyless == []

    def test_fcf_probe_non_kr_runs_without_dart(self, monkeypatch, capsys):
        """반대 증거 — KR 이 없으면 키를 묻지 않는다(키 없다고 AAPL 을 막지 않기)."""
        import bot.dart_client as dc
        import bot.scripts.fcf_probe as m
        monkeypatch.setattr(dc, "get_dart",
                            lambda *a, **k: pytest.fail("비-KR 인데 DART 를 만들었다"))
        monkeypatch.setattr(m, "_yf_periods",
                            lambda t: ([("2026-06-30", 10.0)], []))
        assert m.main(["AAPL"]) == 0
        assert "DART_API_KEY 없음" not in capsys.readouterr().out

    def test_collect_kr_financials(self, keyless):
        import bot.stock_snapshot as ss
        assert ss.collect_kr_financials("005930.KS") == {}
        assert keyless == []


# ── 3. 전수 회귀 ───────────────────────────────────────────────────────
_FACTORIES = {"get_dart", "DartClient"}


def dead_guards(src: str) -> list[tuple[int, str]]:
    """`get_dart()`/`DartClient()` 로 받은 이름을 **부정 판정**하는 자리.

    함수 단위로 본다(중첩 def 는 따로) · 받은 줄 **뒤**만 · 받는 식이
    `IfExp`/`BoolOp` 안이어도 · 주석 달린 대입·`:=` 도 · `None is d` 도 ·
    `import … as` 별칭도 따라간다."""
    return _scan(src)[0]


def _scan(src: str) -> tuple[list[tuple[int, str]], int]:
    """(부정 판정 자리, 받은 자리 수) — 수는 '대조 0건' 하한에 쓴다(#54)."""
    tree = ast.parse(src)
    names = set(_FACTORIES)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name in _FACTORIES and a.asname:
                    names.add(a.asname)

    def factory_call(e) -> bool:
        if not isinstance(e, ast.Call):
            return False
        f = e.func
        nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
        return nm in names

    scope = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)

    def own(fn):
        # 본문 맨 위의 중첩 def 도 건너뛴다 — 그 안은 따로 훑는다.
        stack = [x for x in fn.body if not isinstance(x, scope)]
        while stack:
            x = stack.pop()
            yield x
            stack.extend(c for c in ast.iter_child_nodes(x)
                         if not isinstance(c, scope))

    hits, n_bound = [], 0
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = list(own(fn))
        bound: dict[str, int] = {}
        for x in nodes:
            if isinstance(x, ast.Assign):
                v, targets = x.value, x.targets
            elif isinstance(x, (ast.AnnAssign, ast.NamedExpr)) and x.value:
                v, targets = x.value, [x.target]
            else:
                continue
            cands = ([v.body, v.orelse] if isinstance(v, ast.IfExp)
                     else v.values if isinstance(v, ast.BoolOp) else [v])
            if any(factory_call(c) for c in cands):
                for t in targets:
                    if isinstance(t, ast.Name):
                        bound.setdefault(t.id, x.lineno)
                        n_bound += 1
        if not bound:
            continue
        def name_of(e):
            # `(d := get_dart()) is None` — 판정 대상이 대입식 자체다.
            if isinstance(e, ast.NamedExpr):
                e = e.target
            return e.id if isinstance(e, ast.Name) else None

        for x in nodes:
            nm = None
            if isinstance(x, ast.UnaryOp) and isinstance(x.op, ast.Not):
                nm = name_of(x.operand)
            elif (isinstance(x, ast.Compare)
                  and any(isinstance(o, (ast.Is, ast.Eq)) for o in x.ops)):
                sides = [x.left, *x.comparators]
                if any(isinstance(c, ast.Constant) and c.value is None
                       for c in sides):
                    nm = next((name_of(c) for c in sides
                               if name_of(c)), None)
            if nm in bound and bound[nm] <= x.lineno:
                hits.append((x.lineno, ast.unparse(x)))
    return hits, n_bound


def _production_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "--", "bot", "trade"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [ROOT / p for p in out
            if p.endswith(".py") and "/tests/" not in p and (ROOT / p).exists()]


class TestNoDeadDartGuard:
    def test_repo_has_no_negative_guard_on_get_dart(self):
        files = _production_files()
        assert len(files) > 300, f"훑은 파일이 {len(files)}개 — 범위가 줄었다"
        bad, scanned_bindings = [], 0
        for p in files:
            src = p.read_text(encoding="utf-8")
            if "get_dart" not in src and "DartClient" not in src:
                continue
            hits, n = _scan(src)
            scanned_bindings += n
            for ln, expr in hits:
                bad.append(f"{p.relative_to(ROOT)}:{ln}  {expr}")
        # 대조 0건은 통과가 아니다(#54) — 오늘 레포엔 받는 자리가 수십 곳이다.
        # ⚠️ 문자열 `"get_dart()"` 를 세면 주석·독스트링이 대신 채운다 —
        # 스캐너가 **실제로 받은** 자리를 센다(독립 리뷰 L6).
        assert scanned_bindings >= 20, scanned_bindings
        assert not bad, (
            "`get_dart()` 는 키가 없어도 객체를 돌려준다 — "
            "`dart_ready(dart)` 로 잴 것(실수 #427):\n" + "\n".join(bad))

    @pytest.mark.parametrize("src", [
        "def f():\n    d = get_dart()\n    if not d:\n        return\n",
        "def f(k):\n    d = get_dart() if k else None\n"
        "    if d is None:\n        return\n",
        "def f():\n    d = DartClient()\n    x = 1 if not d else 2\n",
        "def f():\n    d = dc.get_dart()\n    ok = a and not d\n",
        "from bot.dart_client import get_dart as gd\n"
        "def f():\n    d = gd()\n    if not d:\n        return\n",
        "def f():\n    d = get_dart()\n    if d == None:\n        return\n",
        "def f():\n    d = get_dart()\n    if None is d:\n        return\n",
        "def f():\n    d: object = get_dart()\n    if not d:\n        return\n",
        "def f():\n    if (d := get_dart()) is None:\n        return\n",
        "def f():\n    if not (d := get_dart()):\n        return\n",
    ])
    def test_scanner_fires(self, src):
        assert dead_guards(src), src

    @pytest.mark.parametrize("src", [
        # 단일 술어 — 허용
        "def f():\n    d = get_dart()\n    if not dart_ready(d):\n        return\n",
        # 긍정 분기 — 메서드가 키를 스스로 본다(캐시로 답하는 것도 있다)
        "def f():\n    d = get_dart()\n    if d:\n        d.x()\n",
        "def f():\n    d = get_dart()\n    q = d.n() if d else None\n",
        # 인자로 받은 이름 — 호출부가 None 을 넘길 수 있다(못 보는 축)
        "def f(dart):\n    if not dart:\n        return\n",
        # 받기 **전** 의 판정
        "def f(d):\n    if not d:\n        d = get_dart()\n",
        # 중첩 함수의 같은 이름은 별개
        "def f():\n    d = get_dart()\n    def g(d):\n        return not d\n",
    ])
    def test_scanner_spares(self, src):
        assert not dead_guards(src), src


class TestDetailDiagnoseDartName:
    def test_keyless_reports_key_but_still_reads_cache(self, keyless):
        """`/api/quote?debug=1` 의 dart_name 칸 — 키 없음을 **말하되** 멈추지
        않는다. 이름은 corp_code 디스크 캐시로 답할 수 있어서다(긍정 분기를
        막지 않는 이유와 같다). 옛 판은 `if not dart` 가 도달 불가라 키 부재를
        한 마디도 안 했다."""
        import bot.dashboard as d
        got = d.diagnose_detail_sources("005930.KS")["sources"]["dart_name"]
        assert "DART_API_KEY 미설정" in (got.get("error") or ""), got
        # 캐시 조회는 여전히 탄다 — **이 칸의** 결과에 그 키가 실려야 한다.
        # ⚠️ `keyless` 기록으로 재면 옆 `_naver` 프로브가 같은 메서드를 불러
        # 조기 반환으로 되돌려도 통과했다(실측, #75).
        assert "stock_code_to_name" in got, got


class TestKeylessParameterGuards:
    """인자로 받는 자리도 호출부가 `get_dart()` 를 넘기면 같은 병이다(독립 리뷰 M2)."""

    def test_backlog_probe_says_no_key_instead_of_logging_a_miss(
            self, keyless, monkeypatch):
        import bot.dart_backlog as bl
        import bot.dart_client as dc
        logged = []
        monkeypatch.setattr(bl, "_log_miss", lambda *a, **k: logged.append(a))
        val, why = bl.backlog_probe(dc.get_dart(), "005930.KS", 2026, "11012")
        assert (val, why) == (None, "DART없음")
        assert logged == [] and keyless == []        # 원장에 '원문미제공' 안 남김

    def test_fcf_audit_names_the_key_not_the_payload_axis(self, keyless, monkeypatch):
        import bot.dart_client as dc
        import bot.scripts.fcf_audit as fa
        import bot.stock_snapshot as ss
        monkeypatch.setattr(ss, "collect_stock_snapshot", lambda *a, **k: {})
        r = fa.audit_one("005930.KS", dc.get_dart(), years=1)
        axes = r.get("unknown_axes") or []
        assert "DART경로(키 없음)" in axes, (axes, r.get("lines"))
        assert "③payload" not in axes
        assert keyless == []


class TestDiagnoseRestartHint:
    def test_key_in_env_but_not_in_client_says_restart(self, keyless, monkeypatch):
        """클라이언트는 프로세스당 하나 — 시작 뒤 `.env` 에 키를 넣으면 env 칸은
        True 인데 클라이언트엔 키가 없다. 그때 '미설정' 은 거짓이다(리뷰 M3)."""
        import bot.dashboard as d
        import bot.env_keys as ek
        monkeypatch.setattr(ek, "env_ready", lambda *n: True)
        got = d.diagnose_detail_sources("005930.KS")["sources"]["dart_name"]
        assert "재시작" in (got.get("error") or ""), got
        assert "미설정" not in got.get("error", "")
