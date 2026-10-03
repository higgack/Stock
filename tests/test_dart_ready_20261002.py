"""`get_dart()` 뒤의 `if not dart` 는 키 부재를 못 잡는다 — 실수 #427·#428.

`get_dart()` 는 키가 없어도 **항상** `DartClient` 를 돌려주고 그 클래스엔
`__bool__` 이 없다. 그래서 `if not dart:` · `dart is None` 은 늘 거짓이었고,
"DART_API_KEY 없음" 을 말하려던 19곳이 한 번도 그 말을 못 했다 — 메서드가
각자 빈 값을 돌려줘 '원천에 없다'·'정기보고서 미확인' 으로 읽혔다(#82).
6곳은 #426 이 고쳤고 남은 13곳을 `bot.dart_client.dart_ready` 단일 술어로
옮겼다(#38).

계약 다섯:
1. 술어 자체 — 키 유무로 판정, `None` 도 받는다, 옛 이름은 위임한다.
2. 제품 경로 — 키 없는 **실물 클래스**(`DartClient("")`)를 넣으면 각 진입점이
   키 부재를 말하고 DART 메서드를 부르지 않는다(스텁이 아니라 실물이어야
   옛 판에서 실패한다 — `__bool__` 없는 그 모양이 결함이다, #155).
3. 인자로 받는 함수 — 키 없이 할 수 있는 일(디스크 캐시)은 하고, 문서를 한
   건도 못 읽은 빈손은 **짧게(30분)만** 굽는다(#428 — 키 검사를 캐시 앞에 두면
   받아 둔 답을 잃고, 안 두면 접수번호 0건의 `{}` 가 24시간 구워졌다. 키가
   있어도 목록 조회가 일시 실패하면 같은 0건이고, 아예 안 구우면 0건이 영구인
   회사가 탭마다 목록을 다시 걷는다 — 델타 리뷰 M1). ⚠️ 이건 **함수 계약**이다 —
   지금 진입점은 키가 없으면 분기 시계열이 먼저 비어 여기 닿지 않는다(독립
   리뷰 H1, 첫 판의 '하루 동안 표 없음' 은 재지 않은 운영 서술이었다).
4. 키 없을 때 화면 — 분기실적 탭은 '소스 미제공' 대신 키 부재를 말하고,
   대시보드 최대주주·계열회사 블록은 메서드를 부르지 않고 사유를 로그에
   적는다(키 부재가 원천 부재로 읽히면 안 된다, #82).
5. 전수 회귀 — `bot/`·`trade/` 어디서든 클라이언트가 **흘러간 자리**에서
   부정 판정(`not X`·`X is None`)하면 실패. 같은 함수(#427) · 받지 않고 바로
   부정(`not get_dart()`) · 클로저·lambda · 모듈 전역(다른 모듈이 import 해
   가도, 점이 둘인 경로도) · `self.속성` · 다른 함수의 인자(호출 그래프 고정점,
   정적 메서드 포함) · 클라이언트를 돌려주는 함수의 반환값(#428). 이름 열거가
   아니라 커밋될 파일 전수(#24·#412). 긍정 분기(`if dart:` 뒤 메서드 호출)는
   허용한다 — 메서드가 키를 스스로 보고, `stock_code_to_name` 처럼 키 없이
   디스크 캐시로 답하는 것이 있어 막으면 그 답을 잃는다.

⚠️ 못 보는 축(#274): 함수 경계를 넘는 보관소(모듈 전역·`self.속성`·바깥
함수 이름)에 **클라이언트가 아닌 바인딩이 하나라도** 있으면(`_D = None`
지연 초기화) 그 판정은 살아 있는 검사로 보고 넘긴다 · 클래스 밖에서 쓰는
`obj.속성` · 상속 · `Cls.method(...)` 처럼 클래스로 부르는 메서드 ·
`getattr`·dict·리스트에 담은 클라이언트 · `*args`·`**kwargs` 로 넘기기 ·
`functools.partial`·콜백 등록(실행기 `submit`·`Thread(target=…)` 만 따라간다) ·
객체 메서드 호출 중 `self.메서드` 가 아닌 것 · 다른 철자(`not (d and x)` ·
`bool(d) is False`) · 긍정 판정의 `else` 갈래 · 반복문에서 받기 전 줄의 판정 ·
깊이 상한 6(이름을 일곱 번 이상 옮겨 담은 사슬 · 일곱 모듈 이상 거친 import).
오탐 쪽: 같은 스코프에서 다시 묶은 이름(`d = get_dart(); d = None;
if d is None`)은 첫 묶음을 기억해 잡는다.
"""
from __future__ import annotations

import ast
import re
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
_FACTORIES = frozenset({"get_dart", "DartClient"})
# 운반 판정 상한 — 메모가 살아 있으면 레포 전수가 2만여 회다(2026-10-02 실측
# 26,839). 넘으면 멈추고 실패한다 — 메모가 깨진 판정이 계속 도는 대신(리뷰 L3 ·
# 메모 없던 옛 판은 같은 이름을 30번 다시 묶는 함수에서 30초 안에 안 끝났다).
_EVAL_BUDGET = 200_000
_FN = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPED = (*_FN, ast.Lambda, ast.ClassDef)
_PARAM = object()          # 인자 바인딩 표지 — 운반 인자면 클라이언트를 싣는다


def _own(body):
    """한 스코프의 노드 — 중첩 def·lambda·class 는 따로 훑는다."""
    stack = [x for x in body if not isinstance(x, _SCOPED)]
    while stack:
        x = stack.pop()
        yield x
        stack.extend(c for c in ast.iter_child_nodes(x)
                     if not isinstance(c, _SCOPED))


def _key(e):
    """판정·대입 대상의 열쇠 — 이름(`d`) 또는 속성 사슬(`self.d`)."""
    if isinstance(e, ast.NamedExpr):
        e = e.target                     # `(d := get_dart()) is None`
    if isinstance(e, ast.Name):
        return e.id
    if isinstance(e, ast.Attribute):
        b = _key(e.value)
        return f"{b}.{e.attr}" if b else None
    return None


def _is_none(e):
    return isinstance(e, ast.Constant) and e.value is None


def _neg_operand(x):
    """부정 판정(`not E` · `E is None` · `E == None` · `None is E`)의 대상 식."""
    if isinstance(x, ast.UnaryOp) and isinstance(x.op, ast.Not):
        return x.operand
    if (isinstance(x, ast.Compare)
            and any(isinstance(o, (ast.Is, ast.Eq)) for o in x.ops)):
        sides = [x.left, *x.comparators]
        if any(_is_none(c) for c in sides):
            return next((c for c in sides if not _is_none(c)), None)
    return None


def _neg_key(x):
    """부정 판정의 열쇠 — 이름(`d`) 또는 속성 사슬(`self.d`)."""
    op = _neg_operand(x)
    return _key(op) if op is not None else None


def _cands(v):
    """값의 후보 — `a if k else b` · `a or b` 는 갈래마다 본다(1차 규칙)."""
    if isinstance(v, ast.IfExp):
        return [v.body, v.orelse]
    if isinstance(v, ast.BoolOp):
        return list(v.values)
    return [v]


class _Scope:
    """모듈·def·lambda·class 하나 — 그 안의 대입·import·선언을 들고 있다."""

    def __init__(self, mod, node, parent, kind, in_class=False):
        self.mod, self.node, self.parent, self.kind = mod, node, parent, kind
        self.in_class = in_class            # 클래스 본문 바로 아래의 def
        # `@staticmethod` 의 첫 인자는 self 가 아니다 — `self.f(x)` 의 x 는
        # 첫 인자로 간다(리뷰 L1: 건너뛰면 인자가 한 칸씩 밀린다).
        self.static = any(isinstance(d, ast.Name) and d.id == "staticmethod"
                          for d in getattr(node, "decorator_list", ()))
        self.nodes = list(_own([node.body] if kind == "lambda" else node.body))
        a = getattr(node, "args", None)
        self.params = [x.arg for x in a.posonlyargs + a.args] if a else []
        self.kwonly = [x.arg for x in a.kwonlyargs] if a else []
        self.glob = {n for x in self.nodes if isinstance(x, ast.Global)
                     for n in x.names}
        self.nonloc = {n for x in self.nodes if isinstance(x, ast.Nonlocal)
                       for n in x.names}
        self.children: dict = {}            # 바로 아래 def·class 이름
        # 열쇠 → [(줄, 값)] — 값이 None 이면 '무엇이 담겼는지 모르는' 바인딩,
        # ("import", 모듈, 이름) 은 import.
        self.assigns: dict = {}
        self.imp_from: dict = {}
        self.imp_mod: dict = {}
        self.resolves: dict = {}            # 기본값 채우기: id(판정 식) → [(줄, 값)]
        # 값이 보이는 대입(`d = …` · `d: T = …` · `d := …`)은 값과 함께,
        # 그 밖의 바인딩(튜플 풀기·for·with·`+=`)은 '무엇이 담겼는지 모름'으로.
        direct = set()
        for x in self.nodes:
            if isinstance(x, ast.Assign):
                for t in x.targets:
                    if not isinstance(t, (ast.Tuple, ast.List)):
                        self._add(_key(t), x.lineno, x.value)
                        direct.add(id(t))
            elif (isinstance(x, (ast.AnnAssign, ast.NamedExpr))
                  and x.value is not None):
                self._add(_key(x.target), x.lineno, x.value)
                direct.add(id(x.target))
        for x in self.nodes:
            if (isinstance(x, (ast.Name, ast.Attribute))
                    and isinstance(x.ctx, ast.Store) and id(x) not in direct):
                self._add(_key(x), getattr(x, "lineno", 0), None)
            elif isinstance(x, ast.ImportFrom) and x.level == 0 and x.module:
                for a in x.names:
                    nm = a.asname or a.name
                    self.imp_from[nm] = (x.module, a.name)
                    self._add(nm, x.lineno, ("import", x.module, a.name))
            elif isinstance(x, ast.Import):
                for a in x.names:
                    nm = a.asname or a.name.split(".")[0]
                    self.imp_mod[nm] = a.name if a.asname else nm
                    self._add(nm, x.lineno, None)
            elif isinstance(x, ast.If):
                # `if not d: d = get_dart()` — 기본값 채우기. 넘겨받은 클라이언트가
                # 있어도 판정이 살아 있다(`None` 이 오면 채운다) — 받기 **전**
                # 판정의 확장. 채우는 값이 클라이언트일 때만(판정 때 잰다).
                k = _neg_key(x.test)
                fills = [(y.lineno, y.value) for y in x.body
                         if isinstance(y, (ast.Assign, ast.AnnAssign))
                         and y.value is not None
                         and k in [_key(t) for t in (y.targets if isinstance(
                             y, ast.Assign) else [y.target])]]
                if k and fills:
                    self.resolves[id(x.test)] = fills

    def _add(self, k, line, v):
        if k:
            self.assigns.setdefault(k, []).append((line, v))

    def is_local(self, name):
        if self.kind == "module":
            return True
        if name in self.glob or name in self.nonloc:
            return False
        return (name in self.params or name in self.kwonly
                or name in self.assigns or name in self.children)


class _Mod:
    def __init__(self, name, tree):
        self.name = name
        self.factories = set(_FACTORIES)
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if a.name in _FACTORIES and a.asname:
                        self.factories.add(a.asname)
        self.scopes: list = []
        self.top = self._build(tree, None, "module")
        # global·nonlocal 로 바깥 이름을 바꾸는 스코프(보관소 판정 색인)
        self.writers: dict = {}
        for sc in self.scopes:
            for n in sc.glob | sc.nonloc:
                self.writers.setdefault(n, []).append(sc)

    def _build(self, node, parent, kind, in_class=False):
        s = _Scope(self, node, parent, kind, in_class)
        self.scopes.append(s)
        stack = list([node.body] if kind == "lambda" else node.body)
        while stack:
            x = stack.pop()
            if isinstance(x, (*_FN, ast.ClassDef)):
                c = self._build(x, s, "class" if isinstance(x, ast.ClassDef)
                                else "def", in_class=(kind == "class"))
                s.children[x.name] = c
                s._add(x.name, x.lineno, None)
            elif isinstance(x, ast.Lambda):
                self._build(x, s, "lambda")
            else:
                stack.extend(ast.iter_child_nodes(x))
        return s


def scan(sources: dict) -> dict:
    """{모듈명: 소스} → {"hits": [(모듈, 줄, 식)], 계수…}.

    `get_dart()`/`DartClient()` 가 돌려준 클라이언트가 **어디로 흘러가든**
    부정 판정되는 자리를 찾는다 — 같은 함수(1차) · 클로저·lambda · 모듈 전역 ·
    `self.속성` · 다른 함수의 인자 · 그 클라이언트를 돌려주는 함수의 반환값.
    공장 이름이 나오는 모듈에서 시작해 클라이언트가 실려 가는 호출의 대상
    모듈, 공장을 감싼 함수·전역 이름이 나오는 모듈만 그때그때 읽는다."""
    mods: dict = {}
    scopes: list = []
    # 운반 판정 메모 — (스코프, 열쇠, 줄, 깊이). 같은 이름을 거듭 다시 묶는
    # 함수(`ok = ok and …`)는 메모 없이 갈래가 깊이마다 곱해져 줄이 둘 늘 때마다
    # 두 배쯤 느려졌다(리뷰 L3 · 옛 판 실측 16줄 1.6초 · 30줄은 30초 안에 안
    # 끝남). 판정이 기대는 것(운반 인자·공장 함수·읽은 모듈)이 바뀌면 비운다.
    memo: dict = {}
    evals = [0]

    def build(name):
        if name not in mods and name in sources:
            mods[name] = m = _Mod(name, ast.parse(sources[name]))
            scopes.extend(m.scopes)
            memo.clear()
        return mods.get(name)

    def mention(names):
        pat = re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, names)))
        for n, src in sources.items():
            if n not in mods and pat.search(src):
                build(n)

    carrier: dict = {}             # id(def) → 클라이언트가 실려 오는 인자
    factory_fns: dict = {}         # id(def) → 이름(클라이언트를 돌려주는 def)

    # ── 이름 해석 ────────────────────────────────────────────────────
    hs_cache: dict = {}

    def holder(s, name):
        """이름이 사는 스코프 — LEGB(메서드에선 클래스 본문 이름을 안 본다)."""
        ck = (id(s), name)
        if ck not in hs_cache:
            hs_cache[ck] = _holder(s, name)
        return hs_cache[ck]

    def _holder(s, name):
        if s.kind == "module":
            return s
        if s.kind == "class":
            return s if name in s.assigns else holder(s.parent, name)
        if name in s.glob:
            return s.mod.top
        if s.is_local(name):
            return s
        cur = s.parent                 # 자유 이름·nonlocal — 바깥 함수로
        while cur is not None:
            if cur.kind == "module" or (cur.kind != "class"
                                        and cur.is_local(name)):
                return cur
            cur = cur.parent
        return None

    def method_of(s, base):
        """`base` 가 어느 메서드의 첫 인자(self·cls)면 그 메서드."""
        h = holder(s, base)
        if (h is not None and h.kind == "def" and h.in_class and h.params
                and not h.static and h.params[0] == base):
            return h
        return None

    def module_path(s, base):
        """`m` · `bot.x` 같은 이름 사슬 → 모듈 경로(import 를 따라). 모르면 None."""
        head = base.split(".")[0]
        cur = s
        while cur is not None:
            if head in cur.imp_mod:
                return cur.imp_mod[head] + base[len(head):]
            if head in cur.imp_from:
                return ".".join(cur.imp_from[head]) + base[len(head):]
            cur = cur.parent
        return None

    sym_cache: dict = {}

    def symbol(s, func):
        ck = (id(s), id(func))
        if ck not in sym_cache:
            sym_cache[ck] = _symbol(s, func)
        return sym_cache[ck]

    def _symbol(s, func):
        """호출 대상 → ("scope"|"method", 스코프) · ("qual", 모듈, 이름) · None."""
        if isinstance(func, ast.Name):
            cur = s
            while cur is not None:
                if cur.kind == "class" and cur is not s:
                    cur = cur.parent
                    continue
                if func.id in cur.imp_from:
                    return ("qual", *cur.imp_from[func.id])
                c = cur.children.get(func.id)
                if c is not None:
                    return ("scope", c)
                if cur.kind != "module" and cur.is_local(func.id):
                    return None          # 다른 값이 그 이름을 가렸다
                cur = cur.parent
            return None
        if isinstance(func, ast.Attribute):
            base = _key(func.value)
            if base is None:
                return None
            meth = method_of(s, base)
            if meth is not None:
                c = meth.parent.children.get(func.attr)
                return ("method", c) if c is not None else None
            modp = module_path(s, base)
            return ("qual", modp, func.attr) if modp else None
        return None

    def target(s, func, lazy):
        """호출 대상 def 와 첫 인자(self) 건너뛰기. 클래스면 `__init__`."""
        sym = symbol(s, func)
        if sym is None:
            return None, False
        if sym[0] == "qual":
            m = build(sym[1]) if lazy else mods.get(sym[1])
            c = m.top.children.get(sym[2]) if m else None
        else:
            c = sym[1]
        if c is None:
            return None, False
        if c.kind == "class":
            init = c.children.get("__init__")
            return (init, True) if init else (None, False)
        if c.kind != "def":
            return None, False
        return c, sym[0] == "method" and not c.static

    def is_factory_call(s, e):
        if not isinstance(e, ast.Call):
            return False
        f = e.func
        nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
        if nm in s.mod.factories:
            return True
        t, _ = target(s, f, lazy=False)
        return t is not None and id(t) in factory_fns

    # ── 운반 판정 ────────────────────────────────────────────────────
    def bearing(s, v, line, depth):
        """대입 값이 클라이언트를 싣나 — 후보 하나라도(1차 규칙과 같은 엄격)."""
        if v is None or depth > 6:
            return False
        if isinstance(v, tuple):                    # from M import X
            m = mods.get(v[1])
            return bool(m) and holder_carrier(m.top, v[2], depth + 1)
        for c in _cands(v):
            if is_factory_call(s, c):
                return True
            k = _key(c)
            if k and is_carrier(s, k, line, depth + 1):
                return True
        return False

    def is_carrier(s, k, line, depth=0):
        """열쇠 k 가 이 줄에서 클라이언트를 가리키나(메모)."""
        ck = (id(s), k, line, depth)
        if ck not in memo:
            evals[0] += 1
            if evals[0] > _EVAL_BUDGET:
                raise RuntimeError(f"운반 판정 {_EVAL_BUDGET}회 초과 — 메모가 "
                                   "깨졌거나 판정이 폭주한다(리뷰 L3)")
            memo[ck] = _is_carrier(s, k, line, depth)
        return memo[ck]

    def _is_carrier(s, k, line, depth):
        # 같은 스코프 줄 순서(1차 규칙) — 그 스코프가 직접 대입한 것
        if any(ln <= line and bearing(s, v, ln, depth)
               for ln, v in s.assigns.get(k, ())):
            return True
        if "." in k:
            # **마지막** 점에서 가른다 — `self.d` 는 메서드의 속성, `pkg.h.D` 는
            # 모듈 전역(첫 점에서 가르면 점이 둘인 모듈 경로를 놓쳤다, 리뷰
            # M4). `self.a.b` 는 클라이언트가 아니라 그 안의 값이다 — 점이 든
            # base 는 인자 이름일 수 없어 `method_of` 가 None 을 낸다.
            base, attr = k.rsplit(".", 1)
            meth = method_of(s, base)
            if meth is not None:
                return attr_carrier(meth.parent, attr, depth)
            mp = module_path(s, base)               # `import M` 뒤 `M.D`
            m = mods.get(mp) if mp else None
            return bool(m) and holder_carrier(m.top, attr, depth)
        home = holder(s, k)
        if home is s:
            return k in carrier.get(id(s), ())      # 운반 인자(엄격)
        return home is not None and holder_carrier(home, k, depth)

    def holder_carrier(home, k, depth):
        """함수 경계를 넘는 보관소(모듈 전역·바깥 함수 이름)는 **모든**
        바인딩이 클라이언트를 실어야 운반자다. `_D = None` 같은 지연 초기화가
        하나라도 있으면 그 None 판정은 살아 있는 검사다(아직 안 만들었다)."""
        ev = [(home, ln, v) for ln, v in home.assigns.get(k, ())]
        for t in home.mod.writers.get(k, ()):
            if t is not home and holder(t, k) is home:
                ev += [(t, ln, v) for ln, v in t.assigns.get(k, ())]
        if k in home.params or k in home.kwonly:
            ev.append((home, 0, _PARAM))
        if not ev:
            return False
        for sc, ln, v in ev:
            if v is _PARAM:
                if k not in carrier.get(id(sc), ()):
                    return False
            elif not bearing(sc, v, ln, depth):
                return False
        return True

    def attr_carrier(cls, attr, depth):
        """`self.attr` — 그 클래스 메서드들의 대입 + 클래스 본문 대입이 **모두**
        실어야(보관소 규칙과 같다). 클래스 밖에서 쓰는 `obj.attr` 은 안 본다."""
        ev = [(m, ln, v) for m in cls.children.values()
              if m.kind == "def" and m.params and not m.static
              for ln, v in m.assigns.get(f"{m.params[0]}.{attr}", ())]
        ev += [(cls, ln, v) for ln, v in cls.assigns.get(attr, ())]
        return bool(ev) and all(bearing(sc, v, ln, depth) for sc, ln, v in ev)

    def carries(s, e, line):
        if is_factory_call(s, e):
            return True
        k = _key(e)
        return bool(k) and is_carrier(s, k, line)

    # ── 고정점 ───────────────────────────────────────────────────────
    def calls(x):
        """(호출 대상, 위치 인자, 키워드) — 미뤄 부르기도 그 함수 호출로 본다."""
        yield x.func, x.args, x.keywords
        f = x.func
        nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
        if nm == "submit" and x.args:                 # executor.submit(f, *a)
            yield x.args[0], x.args[1:], x.keywords
        if nm == "Thread":                            # Thread(target=f, args=(…))
            kw = {k.arg: k.value for k in x.keywords if k.arg}
            a = kw.get("args")
            if "target" in kw and isinstance(a, (ast.Tuple, ast.List)):
                yield kw["target"], a.elts, []

    def returns_client(s):
        """스스로 받은 클라이언트를 돌려주나 — 인자를 그대로 돌려주는 것은
        아니다(`pick(None)` 이 운반자가 되면 안 된다)."""
        for x in s.nodes:
            if isinstance(x, ast.Return) and x.value is not None:
                for c in _cands(x.value):
                    k = _key(c)
                    if is_factory_call(s, c) or (
                            k and k not in s.params and k not in s.kwonly
                            and is_carrier(s, k, x.lineno)):
                        return True
        return False

    def fixpoint():
        changed = True
        while changed:
            changed = False
            for s in list(scopes):
                if (s.kind == "def" and id(s) not in factory_fns
                        and returns_client(s)):
                    factory_fns[id(s)] = s.node.name
                    memo.clear()
                    changed = True
                for x in s.nodes:
                    if not isinstance(x, ast.Call):
                        continue
                    for func, args, kws in calls(x):
                        pos = [i for i, a in enumerate(args)
                               if not isinstance(a, ast.Starred)
                               and carries(s, a, x.lineno)]
                        kwd = [k.arg for k in kws
                               if k.arg and carries(s, k.value, x.lineno)]
                        if any(isinstance(a, ast.Starred)
                               for a in args[:max(pos, default=-1) + 1]):
                            pos = []              # 별표 뒤 위치는 못 맞춘다
                        if not pos and not kwd:
                            continue
                        tgt, skip = target(s, func, lazy=True)
                        if tgt is None:
                            continue
                        names = list(tgt.params)[1 if skip else 0:]
                        got = {names[i] for i in pos if i < len(names)}
                        got |= {k for k in kwd
                                if k in names or k in tgt.kwonly}
                        new = got - carrier.setdefault(id(tgt), set())
                        if new:
                            carrier[id(tgt)] |= new
                            memo.clear()
                            changed = True

    def exported():
        """다른 모듈이 이름으로 받아 갈 수 있는 운반자 — 공장을 감싼 def ·
        클라이언트를 담은 모듈 전역."""
        out = set(factory_fns.values())
        for m in list(mods.values()):
            for k in set(m.top.assigns) | set(m.writers):
                if holder_carrier(m.top, k, 0):
                    out.add(k)
        return out

    mention(_FACTORIES)
    seen: set = set()
    while True:
        n_mods = len(mods)
        fixpoint()
        fresh = exported() - seen
        seen |= fresh
        if fresh:
            mention(fresh)
        if len(mods) == n_mods:
            break

    # ── 판정 ─────────────────────────────────────────────────────────
    hits, bindings, cross_reads = [], 0, 0
    for s in scopes:
        for evs in s.assigns.values():
            bindings += sum(1 for ln, v in evs if bearing(s, v, ln, 0))
        for x in s.nodes:
            if (isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
                    and holder(s, x.id) not in (s, None)
                    and is_carrier(s, x.id, x.lineno)):
                cross_reads += 1
            op = _neg_operand(x)
            if op is None:
                continue
            # 받지 않고 바로 부정하는 철자(`not get_dart()` · `mk() is None`)
            # — 이름을 거치지 않으니 열쇠가 없다(리뷰 M1).
            k = _key(op)
            if (isinstance(op, ast.Call) and is_factory_call(s, op)) or (
                    k and is_carrier(s, k, x.lineno) and not any(
                        bearing(s, v, ln, 0)
                        for ln, v in s.resolves.get(id(x), ()))):
                hits.append((s.mod.name, x.lineno, ast.unparse(x)))
    return {"hits": sorted(hits), "bindings": bindings,
            "carrier_params": sum(map(len, carrier.values())),
            "cross_reads": cross_reads, "factory_fns": len(factory_fns),
            "modules": len(mods), "built": sorted(mods), "evals": evals[0]}


def dead_guards(src: str) -> list:
    """한 모듈의 부정 판정 자리 [(줄, 식)] — 픽스처용."""
    return [(ln, e) for _m, ln, e in scan({"m": src})["hits"]]


def _production_sources() -> dict:
    """커밋될 운영 파일(#24·#412) — {모듈명: 소스}."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "--", "bot", "trade"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    srcs = {}
    for p in out:
        if p.endswith(".py") and "/tests/" not in p and (ROOT / p).exists():
            parts = p[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            srcs[".".join(parts)] = (ROOT / p).read_text(encoding="utf-8")
    return srcs


_USE = "def use(dart):\n    if not dart:\n        return\n"


class TestNoDeadDartGuard:
    def test_repo_has_no_negative_guard_on_get_dart(self):
        srcs = _production_sources()
        assert len(srcs) > 300, f"훑은 파일이 {len(srcs)}개 — 범위가 줄었다"
        r = scan(srcs)
        # 대조 0건은 통과가 아니다(#54) — 각 축이 오늘 레포에서 실제로 잡는
        # 수의 하한이다. 문자열을 세면 주석이 대신 채운다(독립 리뷰 L6).
        assert r["bindings"] >= 20, r            # 받는 자리
        assert r["carrier_params"] >= 15, r      # 인자로 실려 간 자리
        assert r["cross_reads"] >= 3, r          # 클로저·lambda·전역으로 읽는 자리
        # `trade/` 도 실제로 읽었나 — 범위에서 빠져도 위 하한은 bot 만으로
        # 채워져 통과했다(리뷰 S57) · 필요한 모듈만 읽나 — 전수를 읽으면 같은
        # 결과가 3배 느리게 나와 아무 단언도 안 깨졌다(리뷰 S58).
        assert any(m.startswith("trade.") for m in r["built"]), r["built"]
        assert r["modules"] * 5 <= len(srcs), (r["modules"], len(srcs))
        assert not r["hits"], (
            "`get_dart()` 는 키가 없어도 객체를 돌려준다 — 키가 필요한 판정은 "
            "`dart_ready(dart)`, 키 없이도 되는 경로(디스크 캐시)는 긍정 분기 "
            "`if dart:` 로(실수 #427·#428):\n"
            + "\n".join(f"{m}:{ln}  {e}" for m, ln, e in r["hits"]))

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
        # 같은 함수 안에서 이름을 옮겨 담아도 · `or` 로 받아도
        "def f():\n    d = get_dart()\n    e = d\n    if not e:\n        return\n",
        "def f(x):\n    d = x or get_dart()\n    if not d:\n        return\n",
        # 여섯 번 옮겨 담아도 — 깊이 상한(6)이 이 사슬에서 정확히 닿는다
        # (상한을 5로 줄이면 놓친다, 리뷰 S23·S24)
        "def f():\n    v0 = get_dart()\n"
        + "".join(f"    v{i} = v{i - 1}\n" for i in range(1, 7))
        + "    if not v6:\n        return\n",
        # 받지 않고 바로 부정 — 이름이 없어 열쇠가 없다(리뷰 M1)
        "def f():\n    if not get_dart():\n        return\n",
        "def f():\n    if get_dart() is None:\n        return\n",
        "def mk():\n    return get_dart()\n"
        "def f():\n    if mk() is None:\n        return\n",
    ])
    def test_scanner_fires(self, src):
        assert dead_guards(src), src

    @pytest.mark.parametrize("src", [
        # ① 속성에 담은 경우
        "class C:\n    def f(self):\n        self.d = get_dart()\n"
        "        if not self.d:\n            return\n",
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "    def f(self):\n        if self.d is None:\n            return\n",
        "class C:\n    def __init__(self, dart):\n        self.d = dart\n"
        "    def f(self):\n        if not self.d:\n            return\n"
        "def g():\n    C(get_dart())\n",
        "def f(o):\n    o.dart = get_dart()\n    if not o.dart:\n        return\n",
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "    def f(self):\n        h = lambda: not self.d\n",
        # ② 모듈 수준·전역·클로저
        "D = get_dart()\nif not D:\n    pass\n",
        "D = get_dart()\ndef f():\n    if not D:\n        return\n",
        "if __name__ == '__main__':\n    d = get_dart()\n    if not d:\n"
        "        raise SystemExit(1)\n",
        "def init():\n    global D\n    D = get_dart()\n"
        "def f():\n    if D is None:\n        return\n",
        "def f():\n    d = get_dart()\n    def g():\n        if not d:\n"
        "            return\n",
        "def f():\n    d = get_dart()\n    h = lambda: d is None\n",
        # ③ 다른 함수로 넘긴 뒤
        _USE + "def f():\n    use(get_dart())\n",
        "def use(x, dart=None):\n    if dart is None:\n        return\n"
        "def f():\n    d = get_dart()\n    use(1, dart=d)\n",
        _USE + "def mid(dart):\n    use(dart)\ndef f():\n    mid(get_dart())\n",
        _USE + "def f(xs):\n    d = get_dart()\n"
        "    list(map(lambda x: use(d), xs))\n",
        # 판정 뒤에 클라이언트가 아닌 값을 채우면 기본값 채우기가 아니다
        "def use(dart):\n    if dart is None:\n        dart = {}\n"
        "def f():\n    use(get_dart())\n",
        "class C:\n    def use(self, dart):\n        if not dart:\n"
        "            return\n    def f(self):\n        self.use(get_dart())\n",
        _USE + "def f(ex):\n    ex.submit(use, get_dart())\n",
        _USE + "def f():\n"
        "    threading.Thread(target=use, args=(get_dart(),)).start()\n",
        "def outer(dart):\n    def g():\n        if not dart:\n            return\n"
        "    g()\ndef f():\n    outer(get_dart())\n",
        # ④ 클라이언트를 돌려주는 함수
        "def mk():\n    return get_dart()\ndef f():\n    d = mk()\n"
        "    if not d:\n        return\n",
        "def mk(k):\n    if k:\n        return get_dart()\n    return None\n"
        "def f():\n    d = mk(1)\n    if d is None:\n        return\n",
        # ── 독립 리뷰가 통과시킨 변형마다 그 갈래를 실제로 태운다(#91·#291) ──
        # 키워드 전용 인자로 받는 자리 · 그걸 클로저가 읽는 자리(S01~S03)
        "def use(*, dart):\n    if not dart:\n        return\n"
        "def f():\n    use(dart=get_dart())\n",
        "def outer(*, dart):\n    def g():\n        if not dart:\n"
        "            return\n    g()\ndef f():\n    outer(dart=get_dart())\n",
        # 남의 함수의 nonlocal None 은 이 보관소의 바인딩이 아니다(S07)
        "def f():\n    d = get_dart()\n    def g():\n        if not d:\n"
        "            return\n"
        "def h():\n    d = None\n    def k():\n        nonlocal d\n"
        "        d = None\n",
        # 클래스 본문은 둘러싼 함수의 이름을 읽는다(S09)
        "def f():\n    d = get_dart()\n    class C:\n        ok = not d\n",
        # 메서드 안의 맨 이름 호출은 형제 메서드가 아니라 모듈 함수다(S10)
        _USE + "class C:\n    def use(self, x):\n        pass\n"
        "    def f(self):\n        use(get_dart())\n",
        # 클래스 본문에 담은 클라이언트를 self 로 읽는다(S13)
        "class C:\n    d = get_dart()\n    def f(self):\n        if not self.d:\n"
        "            return\n",
        # Thread 의 args 가 리스트여도(S34) · submit 의 키워드도(S68)
        _USE + "def f():\n"
        "    threading.Thread(target=use, args=[get_dart()]).start()\n",
        _USE + "def f(ex):\n    ex.submit(use, dart=get_dart())\n",
        # 판정 뒤 채우는 게 **다른 이름**이면 기본값 채우기가 아니다(S43)
        "def use(dart):\n    if dart is None:\n        x = get_dart()\n"
        "def f():\n    use(get_dart())\n",
        # 두 겹 안쪽 클로저도(S60)
        "def f():\n    d = get_dart()\n    def g():\n        def h():\n"
        "            if not d:\n                return\n",
        # 정적 메서드는 첫 인자를 건너뛰지 않는다(리뷰 L1)
        "class C:\n    @staticmethod\n    def use(dart):\n        if not dart:\n"
        "            return\n    def f(self):\n        self.use(get_dart())\n",
    ])
    def test_scanner_fires_across_scopes(self, src):
        """1차가 못 보던 자리(실수 #428) — 속성 · 모듈 전역 · 클로저 ·
        다른 함수의 인자 · 감싼 함수의 반환값."""
        assert dead_guards(src), src

    @pytest.mark.parametrize("srcs,where", [
        ({"pkg.a": _USE, "pkg.b": "from pkg.a import use\n"
          "def f():\n    use(get_dart())\n"}, "pkg.a"),
        ({"pkg.a": _USE, "pkg.b": "import pkg.a as a\n"
          "def f():\n    a.use(get_dart())\n"}, "pkg.a"),
        ({"pkg.a": _USE, "pkg.b": "import pkg.a\n"
          "def f():\n    pkg.a.use(get_dart())\n"}, "pkg.a"),
        ({"pkg.a": _USE, "pkg.b": "from pkg import a\n"
          "def f():\n    a.use(get_dart())\n"}, "pkg.a"),
        ({"pkg.a": _USE, "pkg.b": "def f():\n    from pkg.a import use as u\n"
          "    u(get_dart())\n"}, "pkg.a"),
        # 공장 이름이 안 나오는 모듈 — 감싼 함수 이름으로 찾아 읽는다
        ({"pkg.w": "def mk():\n    return get_dart()\n",
          "pkg.c": "from pkg.w import mk\n"
          "def f():\n    d = mk()\n    if not d:\n        return\n"}, "pkg.c"),
        ({"pkg.h": "D = get_dart()\n", "pkg.c": "from pkg.h import D\n"
          "def f():\n    if not D:\n        return\n"}, "pkg.c"),
        ({"pkg.h": "D = get_dart()\n", "pkg.c": "import pkg.h as h\n"
          "def f():\n    if not h.D:\n        return\n"}, "pkg.c"),
        ({"pkg.k": "class C:\n    def __init__(self, dart):\n"
          "        self.d = dart\n    def f(self):\n        if not self.d:\n"
          "            return\n",
          "pkg.m": "from pkg.k import C\ndef g():\n    C(get_dart())\n"},
         "pkg.k"),
        # 모듈 수준 대입 없이 `global` 로만 채운 전역(S41)
        ({"pkg.h": "def init():\n    global D\n    D = get_dart()\n",
          "pkg.c": "from pkg.h import D\ndef f():\n    if not D:\n        return\n"},
         "pkg.c"),
        # 공장을 감싼 함수를 또 감싼 함수 — 세 모듈에 걸쳐(S42·S67)
        ({"pkg.w": "def mk():\n    return get_dart()\n",
          "pkg.v": "from pkg.w import mk\ndef mk2():\n    return mk()\n",
          "pkg.c": "from pkg.v import mk2\n"
          "def f():\n    d = mk2()\n    if not d:\n        return\n"}, "pkg.c"),
        # 점이 둘인 모듈 경로(리뷰 M4)
        ({"pkg.h": "D = get_dart()\n", "pkg.c": "import pkg.h\n"
          "def f():\n    if not pkg.h.D:\n        return\n"}, "pkg.c"),
        # ── 판정 메모가 낡으면 놓치는 자리(리뷰 L3 메모의 반대 증거) ──
        # 운반 인자가 늘면 메모를 비운다 — 안 비우면 중계 함수가 먼저 물은
        # '아직 아니다' 가 남는다(정의 순서가 판정 순서를 정한다)
        ({"pkg.r": "def f():\n    mid(get_dart())\ndef mid(dart):\n"
          "    use(dart)\ndef use(dart):\n    if not dart:\n        return\n"},
         "pkg.r"),
        # 공장 함수가 늘면 비운다 — 감싼 함수를 감싼 함수가 먼저 물었다
        ({"pkg.r": "def h():\n    x = g()\n    if not x:\n        return\n"
          "def mk():\n    return get_dart()\ndef mk2():\n    return mk()\n"
          "def g():\n    d = mk2()\n    return d\n"}, "pkg.r"),
        # 모듈을 새로 읽으면 비운다 — 처음부터 읽힌 모듈이 그 모듈 없이 물었다
        ({"pkg.c": "def mk():\n    return get_dart()\n",
          "pkg.b": "from pkg.c import mk\nD = mk()\n",
          "pkg.a": "# get_dart 를 주석에만 둔 모듈 — 처음부터 읽힌다\n"
          "from pkg.b import D\n" + _USE + "def f():\n    use(D)\n"}, "pkg.a"),
        # 메모 열쇠에 깊이 — 깊은 자리에서 먼저 물은 답(상한에 걸린 '아니다')을
        # 얕은 자리가 다시 쓰면 안 된다
        ({"pkg.a": "# get_dart 를 주석에만 둔 모듈 — 처음부터 읽힌다\n"
          "from pkg.b import D\n" + _USE + "def f():\n    x = D\n    use(x)\n",
          "pkg.b": "u0 = get_dart()\nu1 = u0\nu2 = u1\nu3 = u2\nu4 = u3\n"
          "D = u4\ndef g():\n    if not D:\n        return\n"}, "pkg.b"),
    ])
    def test_scanner_fires_across_modules(self, srcs, where):
        hits = scan(srcs)["hits"]
        assert any(m == where for m, _ln, _e in hits), (hits, srcs)

    @pytest.mark.parametrize("src", [
        # 단일 술어 — 허용
        "def f():\n    d = get_dart()\n    if not dart_ready(d):\n        return\n",
        # 긍정 분기 — 메서드가 키를 스스로 본다(캐시로 답하는 것도 있다)
        "def f():\n    d = get_dart()\n    if d:\n        d.x()\n",
        "def f():\n    d = get_dart()\n    q = d.n() if d else None\n",
        # 아무도 클라이언트를 넘기지 않는 인자
        "def f(dart):\n    if not dart:\n        return\n",
        _USE + "def f():\n    use(None)\n",
        "def use(x, dart=None):\n    if dart is None:\n        return\n"
        "def f():\n    use(get_dart())\n",          # 위치 1 ≠ dart
        _USE + "def f(use):\n    use(get_dart())\n",   # 인자가 그 이름을 가렸다
        # 받기 **전** 의 판정 · 기본값 채우기(넘겨받아도 살아 있는 판정)
        "def f(d):\n    if not d:\n        return\n    d = get_dart()\n",
        "def f(d):\n    if not d:\n        d = get_dart()\n",
        "def f(d=None):\n    if d is None:\n        d = get_dart()\n    d.x()\n"
        "def g():\n    f(get_dart())\n",
        # 중첩 함수의 같은 이름은 별개
        "def f():\n    d = get_dart()\n    def g(d):\n        return not d\n",
        # 지연 초기화 — 보관소에 None 바인딩이 있으면 그 판정은 살아 있다
        "_S = None\ndef get():\n    global _S\n    if _S is None:\n"
        "        _S = DartClient()\n    return _S\n"
        "def f():\n    if _S is None:\n        return\n",
        "class C:\n    def __init__(self):\n        self.d = None\n"
        "    def connect(self):\n        self.d = get_dart()\n"
        "    def f(self):\n        if self.d is None:\n            return\n",
        "def f(k):\n    d = None\n    def g():\n        if d is None:\n"
        "            return\n    if k:\n        d = get_dart()\n",
        # 무엇이 담겼는지 모르는 바인딩(for·튜플 풀기)이 섞인 보관소
        "D = get_dart()\nfor D in []:\n    pass\n"
        "def f():\n    if not D:\n        return\n",
        "def f(xs):\n    d = get_dart()\n    def g():\n        for d in xs:\n"
        "            if not d:\n                return\n",
        # 클라이언트가 아니라 그 키를 보는 판정 · 남의 클래스 같은 이름
        "def f():\n    dart = get_dart()\n    if not dart.api_key:\n        return\n",
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "class E:\n    def f(self):\n        if not self.d:\n            return\n",
        # 인자를 그대로 돌려주는 함수는 공장이 아니다(어딘가 클라이언트를 넘겨도)
        "def pick(d):\n    return d\n"
        "def f():\n    pick(get_dart())\n    x = pick(None)\n"
        "    if x is None:\n        return\n",
        # 메서드의 다른 인자 · 클래스 본문 이름(메서드에선 안 보인다)
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "    def f(self, o):\n        if not o.d:\n            return\n",
        "class C:\n    d = get_dart()\n    def f(self):\n        if not d:\n"
        "            return\n",
        # 클래스 본문의 None 도 지연 초기화다
        "class C:\n    d = None\n    def connect(self):\n"
        "        self.d = get_dart()\n    def f(self):\n"
        "        if self.d is None:\n            return\n",
        # 별표 뒤 위치는 어느 인자에 닿는지 모른다 — 짐작하지 않는다
        "def use(a, dart):\n    if not dart:\n        return\n"
        "def f(xs):\n    use(*xs, get_dart())\n",
        # ── 독립 리뷰가 통과시킨 변형마다 반대 증거(#25·#91) ──
        # 키워드 전용 인자를 그대로 돌려주는 함수도 공장이 아니다(S04)
        "def pick(*, d):\n    return d\n"
        "def f():\n    pick(d=get_dart())\n    x = pick(d=None)\n"
        "    if x is None:\n        return\n",
        # nonlocal 로 None 을 넣는 쪽이 있으면 살아 있는 판정이다(S05·S06)
        "def f():\n    d = get_dart()\n    def reset():\n        nonlocal d\n"
        "        d = None\n    def g():\n        if d is None:\n"
        "            return\n",
        # `global D` 를 선언한 안쪽은 바깥 함수의 D 가 아니라 모듈 D 를 본다(S08)
        "D = None\ndef outer():\n    D = get_dart()\n    def inner():\n"
        "        global D\n        if D is None:\n            return\n",
        # 메서드가 아닌 함수의 첫 인자는 self 가 아니다(S11)
        "def init(o):\n    o.d = get_dart()\n"
        "def f(o):\n    if not o.d:\n        return\n",
        # 주석 달린 대입으로 채워도 기본값 채우기다(S44)
        "def f(d=None):\n    if d is None:\n        d: object = get_dart()\n"
        "    d.x()\ndef g():\n    f(get_dart())\n",
        # 안쪽 함수의 import 가 그 이름을 가렸다(S48)
        "def f():\n    d = get_dart()\n    def g():\n        import json as d\n"
        "        if not d:\n            return\n",
        # 같은 이름의 def 가 전역을 다시 묶었다(S49)
        "D = get_dart()\ndef D():\n    pass\ndef f():\n    if not D:\n"
        "        return\n",
        # 정적 메서드 — 첫 인자가 밀리지 않는다 · 정적 메서드의 첫 인자 속성은
        # self 속성이 아니다(리뷰 L1)
        "class C:\n    @staticmethod\n    def pick(a, d):\n        if not d:\n"
        "            return\n    def f(self):\n        self.pick(get_dart(), None)\n",
        "class C:\n    @staticmethod\n    def g(o):\n        o.d = get_dart()\n"
        "    def f(self):\n        if self.d is None:\n            return\n",
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "    @staticmethod\n    def g(o):\n        if not o.d:\n            return\n",
        # 단일 술어로 감싼 직접 호출 — 허용(리뷰 M1 의 반대 증거)
        "def f():\n    if not dart_ready(get_dart()):\n        return\n",
        # 클라이언트의 속성의 속성 — 클라이언트가 아니라 그 안의 값(리뷰 M4)
        "class C:\n    def __init__(self):\n        self.a = get_dart()\n"
        "    def f(self):\n        if not self.a.b:\n            return\n",
    ])
    def test_scanner_spares(self, src):
        assert not dead_guards(src), src

    @pytest.mark.parametrize("srcs", [
        {"pkg.h": "D = None\ndef init():\n    global D\n    D = get_dart()\n",
         "pkg.c": "from pkg.h import D\n"
         "def f():\n    if D is None:\n        return\n"},
        {"pkg.a": _USE, "pkg.b": "from pkg.a import use\n"
         "def f():\n    use(None)\n"},
        {"pkg.a": _USE, "pkg.b": "from pkg.other import use\n"
         "def f():\n    use(get_dart())\n"},          # 다른 모듈의 같은 이름
    ])
    def test_scanner_spares_across_modules(self, srcs):
        assert not scan(srcs)["hits"], srcs

    def test_scanner_rebinding_stays_linear(self):
        """같은 이름을 거듭 다시 묶는 함수(`ok = ok and chk(i)` 30줄) — 메모
        없이는 이전 바인딩을 깊이마다 다시 훑어 갈래가 곱해졌다(리뷰 L3 · 옛 판
        실측: 16줄 1.6초 · 18줄 3.0초 · 30줄은 30초 안에 안 끝났다). 판정
        횟수가 줄 수에 비례하는지 잰다(이 판 30줄 = 241회) — 메모가 깨지면
        상한(`_EVAL_BUDGET`)에서 멈추고 실패한다."""
        body = "".join(f"    ok = ok and chk({i})\n" for i in range(30))
        # 공장 이름이 나와야 그 모듈을 읽는다(안 나오면 판정 0회로 거짓 통과)
        r = scan({"m": "from bot.dart_client import get_dart\n"
                  "def f():\n    ok = True\n" + body
                  + "    if not ok:\n        return\n"})
        assert r["modules"] == 1 and not r["hits"], r
        assert 30 <= r["evals"] <= 1_000, r["evals"]

    def test_scanner_budget_stops_instead_of_spinning(self, monkeypatch):
        """상한을 넘으면 계속 도는 대신 멈추고 실패한다 — 그 갈래도 탄다."""
        import sys
        monkeypatch.setattr(sys.modules[__name__], "_EVAL_BUDGET", 3)
        with pytest.raises(RuntimeError, match="운반 판정"):
            scan({"m": "def f():\n    v0 = get_dart()\n"
                  + "".join(f"    v{i} = v{i - 1}\n" for i in range(1, 7))
                  + "    if not v6:\n        return\n"})


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


class TestKeylessCallees:
    """인자로 받는 함수 — 키 없는 실물이 넘어오면 **키 없이 할 수 있는 것은
    하고, 못 한 것은 굽지 않는다**(실수 #428). 옛 판의 `if not dart` 는 키
    없는 클라이언트를 못 걸러, 걸렀다면 하지 말았어야 할 일을 했다."""

    _QS = [{"year": 2026, "reprt_code": "11012", "label": "26.2Q",
            "financials": {}}]

    def test_backlog_probe_keyless_still_serves_cache(self, keyless, monkeypatch):
        """#427 이 키 검사를 캐시 **앞**에 둬, 키 없는 프로세스가 받아 둔
        수주잔고까지 잃었다(그 커밋이 적은 '그 뒤 경로가 키 없이 무엇을 할 수
        있었나를 셀 것' 을 같은 커밋에서 어겼다)."""
        import bot.dart_backlog as bl
        import bot.dart_client as dc
        monkeypatch.setattr(bl, "_bl_cached", lambda ck: (1.5e12, "정상"))
        assert bl.backlog_probe(dc.get_dart(), "005930.KS", 2026,
                                "11012") == (1.5e12, "정상")
        assert keyless == []

    def test_fill_backlog_keyless_fills_from_cache(self, keyless, monkeypatch):
        import bot.dart_backlog as bl
        import bot.dart_client as dc
        import bot.quarterly_infographic as qi
        monkeypatch.setattr(bl, "_bl_cached", lambda ck: (2.0e12, "정상"))
        qs = [dict(q, financials={}) for q in self._QS]
        qi._fill_backlog(dc.get_dart(), "005930.KS", qs)
        assert qs[0]["financials"].get("수주잔고") == 2.0e12, qs
        assert keyless == []

    def test_tables_rolling_keyless_serves_cache_and_bakes_nothing(
            self, keyless, monkeypatch, caplog):
        """옛 판은 키 없이 걸어 접수번호를 하나도 못 얻고 `{}` 를 24시간
        캐시에 구웠다(함수 계약의 결함 — 지금 운영 진입점은 키가 없으면
        분기 시계열이 먼저 비어 여기까지 오지 않는다, 독립 리뷰 H1).

        로그도 계약이다 — 키 부재는 화면에 사유를 남기지 않는 경로라 이
        한 줄이 유일한 흔적이다(리뷰 L6: 지워도 green 이었다)."""
        import logging

        import bot.dart_client as dc
        import bot.dart_production as dp
        _no_doc_fetch(monkeypatch)
        store: dict = {}
        monkeypatch.setattr(dp, "_tables_cached", store.get)
        monkeypatch.setattr(dp, "_tables_cache_write", store.__setitem__)
        with caplog.at_level(logging.INFO, logger=dp.log.name):
            assert dp.tables_rolling(dc.get_dart(), "005930.KS",
                                     self._QS) == {}
        assert store == {}, store
        assert "DART_API_KEY 없음" in caplog.text, caplog.text
        ck = dp._tables_cache_key("005930.KS", self._QS, tuple(dp._PARSERS))
        store[ck] = {"products": {"rows": [1]}}
        assert dp.tables_rolling(dc.get_dart(), "005930.KS",
                                 self._QS) == {"products": {"rows": [1]}}
        assert keyless == []

    @staticmethod
    def _real_cache(monkeypatch, tmp_path):
        """실물 `_tables_cached`·`_tables_cache_write` 를 tmp 디렉터리에 — 짧은
        기록의 나이는 `dp.time` 이 잰다(테스트가 시계를 옮긴다)."""
        import time
        import types

        import bot.dart_production as dp
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
        clock = {"t": time.time()}
        monkeypatch.setattr(dp, "time",
                            types.SimpleNamespace(time=lambda: clock["t"]))
        return clock

    def test_tables_rolling_keyed_but_read_nothing_bakes_short(
            self, monkeypatch, tmp_path, caplog):
        """키가 있어도 **문서를 한 건도 못 읽었으면** 빈손이다. 24시간 구우면
        목록 조회(`list.json`, 캐시 없음) 한 번의 일시 실패가 하루를 비우고
        (리뷰 M3), 아예 안 구우면 0건이 **영구인** 회사 — 결산월이 12월이
        아니라 `_periodic_report_window` 의 제출창이 안 맞는 회사 — 가 탭을
        열 때마다 목록을 다시 걷는다. 미리받기 스레드와 핸들러가 각자 걸어
        요청마다 16회였고(실측) 장애 땐 타임아웃만 합해도 요청마다 최대 ~80초다
        (델타 리뷰 M1).
        그래서 **짧게(30분)** 믿는다 — 그 안에서는 핸들러도 미리받기도 다시
        걷지 않고, 지나면 다시 걷는다(#303 실패는 짧게만 믿는다)."""
        import logging
        import types

        import bot.dart_client as dc
        import bot.dart_production as dp
        _no_doc_fetch(monkeypatch)
        walks: list = []
        monkeypatch.setattr(dc.DartClient, "find_periodic_reports",
                            lambda self, *a: walks.append(a) or [])
        monkeypatch.setattr(dc.DartClient, "find_periodic_report",
                            lambda self, *a: None)
        clock = self._real_cache(monkeypatch, tmp_path)
        cli = dc.DartClient("k-1234567890")
        with caplog.at_level(logging.INFO, logger=dp.log.name):
            assert dp.tables_rolling(cli, "005930.KS", self._QS) == {}
        assert walks and "읽은 문서 0건" in caplog.text, caplog.text
        n = len(walks)
        clock["t"] += dp._TABLES_EMPTY_TTL - 60
        assert dp.tables_rolling(cli, "005930.KS", self._QS) == {}
        assert len(walks) == n, "짧은 기록 안인데 목록을 다시 걸었다"
        made: list = []
        monkeypatch.setattr(dp, "_PREFETCH", set())
        monkeypatch.setattr(dp, "threading", types.SimpleNamespace(
            Thread=lambda *a, **k: made.append(k) or types.SimpleNamespace(
                start=lambda: None)))
        dp.prefetch_tables(cli, "005930.KS", self._QS)
        assert made == [], "빈손 기록이 살아 있는데 미리받기가 또 걸으러 갔다"
        clock["t"] += 120                      # 이제 30분을 넘겼다
        assert dp.tables_rolling(cli, "005930.KS", self._QS) == {}
        assert len(walks) > n, "짧은 기록이 지났는데 다시 걷지 않았다"
        assert dp._TABLES_EMPTY_TTL == 30 * 60      # 크기도 못박는다(#66)

    def test_tables_rolling_unreadable_document_bakes_short(
            self, monkeypatch, tmp_path):
        """'원문 실패' 갈래 — 접수번호는 있는데 원문을 못 받았으면 본 것이
        없다. 24시간 구우면 그 하루 내내 '표 없음' 이다(델타 리뷰 L1:
        `read_any = True` 변형이 살아남았다 — 접수번호 0건만 재고 있었다)."""
        import bot.dart_client as dc
        import bot.dart_feed as df
        import bot.dart_production as dp
        fetched: list = []
        monkeypatch.setattr(dc.DartClient, "find_periodic_reports",
                            lambda self, *a: [{"rcept_no": "20260814000001"}])
        monkeypatch.setattr(df, "_fetch_doc_text",
                            lambda rn, *a, **k: fetched.append(rn) or None)
        monkeypatch.setattr(df, "doc_was_truncated", lambda *a, **k: False)
        clock = self._real_cache(monkeypatch, tmp_path)
        cli = dc.DartClient("k-1234567890")
        assert dp.tables_rolling(cli, "005930.KS", self._QS) == {}
        n = len(fetched)
        assert n, fetched
        clock["t"] += dp._TABLES_EMPTY_TTL + 60
        assert dp.tables_rolling(cli, "005930.KS", self._QS) == {}
        assert len(fetched) > n, "원문을 못 받은 빈손이 30분을 넘겨 살아 있다"

    def test_tables_rolling_any_read_document_bakes_long(
            self, monkeypatch, tmp_path):
        """반대 증거 — 한 건이라도 **읽었으면** 그건 답이라 24시간 굽는다.
        마지막 문서만 보고 판정하면(`read_any = bool(markup)`) 앞에서 읽은
        문서가 있어도 빈손으로 쳐 30분마다 같은 원문을 다시 받아 훑는다
        (델타 리뷰 L1 · P03)."""
        import bot.dart_client as dc
        import bot.dart_feed as df
        import bot.dart_production as dp
        fetched: list = []
        monkeypatch.setattr(dc.DartClient, "find_periodic_reports",
                            lambda self, *a: [{"rcept_no": "A"},
                                              {"rcept_no": "B"}])
        monkeypatch.setattr(
            df, "_fetch_doc_text",
            lambda rn, *a, **k: fetched.append(rn) or (
                "<P>표 없는 본문</P>" if rn == "A" else None))
        monkeypatch.setattr(df, "doc_was_truncated", lambda *a, **k: False)
        clock = self._real_cache(monkeypatch, tmp_path)
        cli = dc.DartClient("k-1234567890")
        assert dp.tables_rolling(cli, "005930.KS", self._QS) == {}
        assert fetched == ["A", "B"], fetched
        clock["t"] += dp._TABLES_EMPTY_TTL + 60
        assert dp.tables_rolling(cli, "005930.KS", self._QS) == {}
        assert fetched == ["A", "B"], "문서를 읽었는데 30분 만에 다시 걸었다"

    def test_tables_rolling_read_document_without_table_is_cached(
            self, monkeypatch):
        """반대 증거 — 문서를 **읽었는데** 표가 없으면 그건 답이다. 굽지
        않으면 매 요청이 같은 원문을 다시 받아 훑는다(이 캐시가 생긴 이유 —
        2.8M자 정규식이 GIL 을 붙잡았다)."""
        import bot.dart_client as dc
        import bot.dart_feed as df
        import bot.dart_production as dp
        monkeypatch.setattr(dc.DartClient, "find_periodic_reports",
                            lambda self, *a: [{"rcept_no": "20260814000001"}])
        monkeypatch.setattr(df, "_fetch_doc_text",
                            lambda *a, **k: "<P>표 없는 본문</P>")
        monkeypatch.setattr(df, "doc_was_truncated", lambda *a, **k: False)
        store: dict = {}
        monkeypatch.setattr(dp, "_tables_cached", store.get)
        monkeypatch.setattr(dp, "_tables_cache_write", store.__setitem__)
        assert dp.tables_rolling(dc.DartClient("k-1234567890"),
                                 "005930.KS", self._QS) == {}
        ck = dp._tables_cache_key("005930.KS", self._QS, tuple(dp._PARSERS))
        assert store == {ck: {}}, store

    def test_prefetch_tables_keyless_starts_nothing(self, keyless, monkeypatch):
        """미리받기는 데우는 일뿐이다 — 키가 없으면 데울 게 없다(받아 둔 표는
        `tables_rolling` 이 그대로 낸다)."""
        import types

        import bot.dart_client as dc
        import bot.dart_production as dp
        made = []
        monkeypatch.setattr(dp, "_tables_cached", lambda k: None)
        monkeypatch.setattr(dp, "_PREFETCH", set())
        monkeypatch.setattr(dp, "threading", types.SimpleNamespace(
            Thread=lambda *a, **k: made.append(k) or types.SimpleNamespace(
                start=lambda: None)))
        dp.prefetch_tables(dc.get_dart(), "005930.KS", self._QS)
        assert made == [] and keyless == []

    def test_dart_name_keyless_still_reads_cache(self, keyless, monkeypatch):
        """반대 증거 — 이 자리는 `dart_ready` 로 막으면 **안 된다**. 키 없는
        클라이언트도 corp_code 디스크 캐시로 이름을 답한다(비-KR 은 `None`)."""
        import bot.dart_client as dc
        import bot.quarterly_infographic as qi
        monkeypatch.setattr(dc.DartClient, "stock_code_to_name",
                            lambda self, code: f"이름-{code}")
        assert qi._dart_name(dc.get_dart(), "005930.KS") == "이름-005930"
        assert qi._dart_name(None, "AAPL") is None

    def test_dart_name_none_is_a_normal_path_not_a_failure(self, caplog):
        """`None` 은 비-KR 경로가 **정상적으로** 넘긴다. `if dart:` 를 빼면
        반환값은 같지만(예외를 `except` 가 먹는다) 매 비-KR 렌더가
        `'NoneType' object has no attribute` 를 실패처럼 남긴다 — 정상 경로를
        실패로 적으면 진짜 실패를 가린다(#82, 리뷰 P04 생존)."""
        import logging

        import bot.quarterly_infographic as qi
        with caplog.at_level(logging.DEBUG, logger=qi.log.name):
            assert qi._dart_name(None, "AAPL") is None
        assert "corp name" not in caplog.text, caplog.text


class TestMajorShareholdersKeyless:
    def test_keyless_skips_table_and_says_why(self, keyless, caplog):
        """옛 `if dart:` 는 늘 참이라 키가 없어도 `get_major_shareholders` 를
        불러 빈 목록을 받았고 'returned empty' 로 남았다 — 키 부재가 원천
        부재로 읽혔다. 이 else 갈래엔 테스트가 없었다(#427 못 보는 축)."""
        import logging

        from bot.dashboard import _render_stock_info_html
        with caplog.at_level(logging.INFO):
            seg = _render_stock_info_html({
                "ticker": "018260.KS",
                "stock_info": {"currency": "KRW"}})["other_panes"]
        assert "get_major_shareholders" not in keyless, keyless
        assert "DART_API_KEY 없음 — 최대주주 표 생략" in caplog.text
        assert "최대주주 현황" not in seg

    def test_keyless_skips_affiliate_table_and_says_why(self, keyless, caplog):
        """바로 아래 계열회사 블록(P6b)도 같은 모양이었다 — `if dart2:` 는
        긍정 분기라 스캐너가 허용하는데, 그 메서드는 키 검사가 디스크 캐시
        **앞**이라 키 없이 할 수 있는 일이 없고, 빈 목록이 'returned empty'
        로 남아 키 부재가 원천 부재로 읽혔다(리뷰). 긍정 분기를 허용하는
        근거는 '키 없이도 답하는 메서드' 이므로 메서드마다 그 근거가 서는지
        봐야 한다."""
        import logging

        from bot.dashboard import _render_stock_info_html
        with caplog.at_level(logging.INFO):
            seg = _render_stock_info_html({
                "ticker": "018260.KS",
                "stock_info": {"currency": "KRW"}})["other_panes"]
        assert "get_affiliate_investments" not in keyless, keyless
        assert "DART_API_KEY 없음 — 계열회사 표 생략" in caplog.text
        assert "returned empty" not in caplog.text, caplog.text
        assert "계열회사(타법인 출자) 현황" not in seg

    def test_keyed_renders_affiliate_table(self, monkeypatch):
        """반대 증거 — 운영 VM 은 키가 있어 **이 갈래만** 탄다. 키가 있으면
        메서드를 부르고 표를 그린다(델타 리뷰 L2: `if dart_ready(dart2) and
        False:` 가 살아남았다 — 키 있는 스텁이 빈 목록만 줘 표가 한 번도
        그려지지 않았다)."""
        import bot.dart_client as dc
        from bot.dashboard import _render_stock_info_html
        cli = dc.DartClient("k-1234567890")
        monkeypatch.setattr(dc, "get_dart", lambda *a, **k: cli)
        monkeypatch.setattr(dc.DartClient, "get_major_shareholders",
                            lambda self, code: [])
        monkeypatch.setattr(
            dc.DartClient, "get_affiliate_investments",
            lambda self, code: [{"name": "삼성디스플레이", "purpose": "경영참여",
                                 "pct": 84.8, "book_value": 18_000_000_000_000}])
        seg = _render_stock_info_html({
            "ticker": "018260.KS",
            "stock_info": {"currency": "KRW"}})["other_panes"]
        assert "계열회사(타법인 출자) 현황 (DART 사업보고서 · 1사)" in seg, seg
        assert "삼성디스플레이" in seg and "84.80%" in seg, seg


class TestQuarterlyEmptyReason:
    """분기실적 탭이 비었을 때 사용자가 실제로 보는 문구(리뷰 H1 — 운영
    영향은 여기서 난다). 키 없는 국내 종목은 DART 분기 재무를 못 받아
    (`get_normalized_financials` 의 키 검사가 디스크 캐시보다 **앞**이다)
    `build_payload` 가 None 이 되는데, 화면은 '소스 미제공 또는 미지원
    시장' 이라 적어 키 부재가 원천 부재로 읽혔다(#82).

    ⚠️ 렌더 계측(`_RENDER_TIMING`)은 모듈 전역이라 테스트마다 새것으로 갈아
    끼운다 — 안 그러면 여기서 남긴 AAPL 항목이 '남의 값이 샌다' 를 재는 다른
    테스트(`test_quarterly_stages_are_measured`)를 깬다(첫 `make test` 실측)."""

    @pytest.fixture(autouse=True)
    def _fresh_timing(self, monkeypatch):
        import bot.quarterly_infographic as qi
        monkeypatch.setattr(qi, "_RENDER_TIMING", type(qi._RENDER_TIMING)())

    def test_kr_keyless_says_the_key(self, keyless):
        import bot.quarterly_infographic as qi
        r = qi.get_or_render("005930.KS", {})
        assert r["ok"] is False
        assert "DART_API_KEY 없음" in r["error"], r
        assert "소스 미제공" not in r["error"], r

    def test_kr_keyed_keeps_the_old_reason(self, monkeypatch):
        """반대 증거 — 키가 있는데 비었으면 키를 탓하지 않는다."""
        import bot.dart_client as dc
        import bot.quarterly_infographic as qi
        monkeypatch.setattr(dc, "get_dart",
                            lambda *a, **k: dc.DartClient("k-1234567890"))
        monkeypatch.setattr(qi, "build_payload", lambda *a, **k: None)
        r = qi.get_or_render("005930.KS", {})
        assert r["ok"] is False and "DART_API_KEY" not in r["error"], r
        assert "분기 재무 데이터 없음" in r["error"], r

    @pytest.mark.parametrize("ticker", ["AAPL", "7203.T", "0700.HK",
                                        "2330.TW", "600519.SS"])
    def test_non_kr_does_not_ask_dart(self, ticker, monkeypatch):
        """반대 증거 — 비-KR 은 DART 를 안 쓰므로 묻지도 않는다. 시장마다 잰다
        (델타 리뷰 L3: `!= "KR"` 을 `== "US"` 로 바꾼 변형이 US 하나로는
        살아남았다 — 그러면 키 없는 프로세스의 7203.T·0700.HK 탭이 '국내 분기
        재무' 를 탓한다)."""
        import bot.dart_client as dc
        import bot.quarterly_infographic as qi
        monkeypatch.setattr(dc, "get_dart",
                            lambda *a, **k: pytest.fail("비-KR 인데 DART 를 물었다"))
        monkeypatch.setattr(qi, "build_payload", lambda *a, **k: None)
        r = qi.get_or_render(ticker, {})
        assert r["ok"] is False and "분기 재무 데이터 없음" in r["error"], r


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
