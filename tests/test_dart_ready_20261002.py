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
   정적 메서드 포함) · 클라이언트를 돌려주는 함수의 반환값(#428). 그리고
   #428 이 '못 보는 축' 으로 남긴 모양(#429): 다른 철자(드모르간 · `bool()` ·
   `is False`) · 리터럴 칸(dict·리스트·`dict()`·`SimpleNamespace()`·`getattr` ·
   `.get`) · 미뤄 부르기(`partial`·`to_thread`·실행기·이벤트 루프·`Thread`
   kwargs·`Timer`) · `*args`·`**kwargs`(넘기기·리터럴 펼치기·받은 쪽의 칸) ·
   클래스로 부른 메서드·classmethod · 객체 메서드(`C()` 인스턴스·`self.속성`
   인스턴스·타입 주석 인자) · 상속(조상의 속성·메서드·`__init__`, 자손이 다르게
   채우면 살아 있다) · 클래스 밖 객체 속성 · 다시 내보낸 이름 · 깊이 상한 없음
   (순환 감지). 값이 **언제나** 클라이언트인 긍정 판정의 else 갈래도 죽은
   검사다(기본값만 두는 갈래는 넘긴다). 이름 열거가 아니라 커밋될 파일
   전수(#24·#412). 긍정 분기(`if dart:` 뒤 메서드 호출)는 허용한다 — 메서드가
   키를 스스로 보고, `stock_code_to_name` 처럼 키 없이 디스크 캐시로 답하는
   것이 있어 막으면 그 답을 잃는다.

⚠️ 못 보는 축(#274) — #430 이 #429 의 목록(쓰기 축 · 호출 축 · 흐름·클래스 축 ·
else 판정 · 보관소 순환)을 메운 뒤 남은 것은 **정적으로 가를 수 없는** 경계다:
보관소에 클라이언트가 아닌 바인딩이 하나라도 있으면(`_D = None` 지연 초기화) 살아
있는 검사로 본다 — 의도다(`get_dart` 자신이 그 모양이고, 값을 담기 전에 읽히는
순서는 실행이 정한다) · 반복문에서 몇 번째 바퀴인지로 막은 판정(`if i > 0 and not
d`) — 값이 아니라 실행 횟수에 달렸다 · 실행 중에 정해지는 이름(`getattr(o, name)`
의 변수 이름 · 변수로 고른 모듈) — 읽기는 모른다로 본다 · 칸 **뒤** 에 펼침·상수가
아닌 열쇠가 오는 dict(`{'d': X, **a}` · `{'d': X, k: None}`) — 덮였을지 모른다(그
앞의 칸은 확정, 같은 열쇠는 마지막이 이긴다) · 인자의 else 는 닫힌 세계(함수 안에서 정의해 그 이름을 곧바로 부르는
데에만 쓴 함수)에서만 — 열린 함수는 부르는 곳을 다 모른다 · 대상을 못 찾은 메서드
이름(`x.get_dart()`)은 이름으로 본다(엄격 쪽) · 이름공간 객체를 변수·인자에 담아
쓰는 쓰기(`g = globals(); g['G'] = None` · `exec`)는 쓰기로 안 본다(엄격 쪽 — 그
자리에서 부른 `globals()[…]` 만 본다) · 세터 인자로 들어오는 None(`def setd(p):
global G; G = p`)은 인자 규칙('갈래 하나라도')을 따라 잡는다(엄격 쪽). 순환은 E-게이트 최대 고정점으로
잰다(바닥 — 공장 호출 — 에 닿아야 참, 무작위 보관소 그래프에서 기대값과 정확히
일치). 판정 사슬 상한 400 은 넘으면 '아니다' 로 접지 않고 실패한다(레포 실측 깊이
5). 오탐 쪽: 같은 스코프에서 다시 묶은 이름(`d = get_dart(); d = None; if d is
None`)은 첫 묶음을 기억해 잡는다(엄격 — 고칠 곳은 늘 `dart_ready` 다).
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
        """⚠️ 2026-10-04 다시 씀(#222): 옛 판은 빈 dict 를 못박았다. 이제 키 없이
        멈춘 사실을 스냅샷의 **재무 칸**에 적는다(`kr.dart_keyless_financials` —
        화면이 K-IFRS 칸에 사유를 말하고, 키가 생기면 다시 받는다, `tests/test_
        dart_blindspots_20261004.py`). 남는 보장은 그대로다: 키 없이 DART
        메서드를 부르지 않는다."""
        import bot.stock_snapshot as ss
        assert ss.collect_kr_financials("005930.KS") == {
            "kr": {"dart_keyless_financials": "collection"}}
        assert keyless == []


# ── 3. 전수 회귀 ───────────────────────────────────────────────────────
_FACTORIES = frozenset({"get_dart", "DartClient"})
_DART_MOD = "bot.dart_client"
# 호출 결과를 바꾸지 않는 장식 — 그 밖의 장식이 붙은 함수는 무엇을 돌려줄지
# 모른다(감싼 함수가 None 을 줄 수 있다, 리뷰 F2). 이름 → 그 이름이 와야 하는
# 곳(델타 리뷰 L4 — 이름만 같은 사용자 정의 `cache`·다른 라이브러리의
# `lru_cache`·다시 묶은 `staticmethod` 는 아니다).
_TRANSPARENT_DECOS = {"staticmethod": "builtins", "classmethod": "builtins",
                      "lru_cache": "functools", "cache": "functools"}
# 위 표를 (출처, 원래 이름) 꼴로 — 장식 이름을 별칭까지 따라가 이것과 견준다.
_TRANSPARENT_SRC = {(src, n) for n, src in _TRANSPARENT_DECOS.items()}
# 읽으면 게터를 부르는 장식(`ev_attr`)
_PROPERTY_SRC = {("builtins", "property"), ("functools", "cached_property")}
# 운반 판정 상한 — 메모가 살아 있으면 레포 전수가 수만 회다(실측은 아래 레포
# 회귀가 단언한다). 넘으면 멈추고 실패한다 — 메모가 깨진 판정이 계속 도는 대신
# (리뷰 L3 · 메모 없던 옛 판은 같은 이름을 30번 다시 묶는 함수에서 30초 안에 안
# 끝났다).
_EVAL_BUDGET = 200_000
# 순환 재기 회차 상한 — 회차마다 직전 회차의 참을 가정해 다시 재고, 참 집합이
# 그대로면 멈춘다(자기 일관). 넘으면 확정하지 않는다(놓치는 쪽 · 실수 #430)
_RERUN_PASSES = 32
# 판정 사슬 상한 — 옛 깊이 상한 6(이름을 여섯 번까지만 따라간다)은 **순환
# 감지**로 바꿨다(사슬이 길어도 끝까지 따라간다, #428 못 보는 축). 남은 상한은
# 파이썬 재귀를 지키는 안전판이고, 넘으면 조용히 '아니다' 로 접지 않고 멈추고
# 실패한다(#54).
_STACK_LIMIT = 400
_FN = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPED = (*_FN, ast.Lambda, ast.ClassDef)
# 미뤄 부르기 — 이름 → (함수 자리, 인자 시작 자리, 키워드도 그 함수로 가나).
# 실행기 · 부분 적용 · 스레드·이벤트 루프로 넘기기(#428 은 `submit`·`Thread`
# 만 따라갔다 — 못 보는 축).
_DEFER = {"submit": (0, 1, True), "partial": (0, 1, True),
          "partialmethod": (0, 1, True), "to_thread": (0, 1, True),
          "call_soon": (0, 1, False), "call_soon_threadsafe": (0, 1, False),
          "run_in_executor": (1, 2, False), "call_later": (1, 2, False),
          "call_at": (1, 2, False), "add_reader": (1, 2, False),
          "add_writer": (1, 2, False), "add_signal_handler": (1, 2, False)}
# 이름이 흔해 **받는 쪽**을 가려야 하는 미뤄 부르기 — 이름 → (받는 쪽, 함수 자리,
# 인자 시작 자리, 키워드도 그 함수로 가나). 받는 쪽: ("qual", {모듈.이름}) = 부른
# 것이 그 함수 · ("recv", {모듈.생성자}) = 그 생성자로 만든 객체의 메서드. 아무
# 객체의 `register`·`callback` 을 콜백 등록으로 보면 오탐이다(실수 #430).
_EXITSTACKS = frozenset({"contextlib.ExitStack", "contextlib.AsyncExitStack"})
_POOLS = frozenset({"multiprocessing.Pool", "multiprocessing.pool.Pool",
                    "multiprocessing.pool.ThreadPool", "multiprocessing.dummy.Pool"})
_DEFER_CHECKED = {
    "register": (("qual", frozenset({"atexit.register"})), 0, 1, True),
    "finalize": (("qual", frozenset({"weakref.finalize"})), 1, 2, True),
    "callback": (("recv", _EXITSTACKS), 0, 1, True),
    "push_async_callback": (("recv", frozenset({"contextlib.AsyncExitStack"})),
                            0, 1, True)}
# 함수·인자 묶음·키워드 묶음을 자리나 키워드로 받는 것 — 이름 → (받는 쪽 또는
# None, (함수 키워드, 자리), (인자 묶음 키워드, 자리), (키워드 묶음 키워드, 자리)).
# `Thread(group, target, name, args, kwargs)` · `Process` 같음 ·
# `Timer(t, f, args, kwargs)` · `scheduler.enter(delay, prio, action, argument,
# kwargs)` · `Pool.apply_async(func, args, kwds)`.
_SCHED = frozenset({"sched.scheduler"})
_DEFER_KW = {
    "Thread": (None, ("target", 1), ("args", 3), ("kwargs", 4)),
    "Process": (None, ("target", 1), ("args", 3), ("kwargs", 4)),
    "Timer": (None, ("function", 1), ("args", 2), ("kwargs", 3)),
    "enter": (("recv", _SCHED), ("action", 2), ("argument", 3), ("kwargs", 4)),
    "enterabs": (("recv", _SCHED), ("action", 2), ("argument", 3), ("kwargs", 4)),
    "apply_async": (("recv", _POOLS), ("func", 0), ("args", 1), ("kwds", 2))}
# 칸을 키워드로 받는 리터럴 생성자 — `dict(k=…)` 는 칸, `SimpleNamespace(k=…)`
# 는 속성.
_NS_CALLS = {"dict": "item", "SimpleNamespace": "attr"}
# 컨테이너를 바꾸는 메서드(`_Scope._method_store`)
_MUTATORS = frozenset({"update", "setdefault", "__setitem__", "pop", "__delitem__",
                       "popitem", "clear", "insert", "remove", "sort",
                       "reverse", "append", "extend"})
# 컨테이너를 바꾸지 않는 메서드 — 지역 컨테이너가 '새어 나가지 않았나' 를 볼 때
# 이 이름으로 꺼낸 것만 안전한 쓰임으로 본다(실수 #430)
_READERS = frozenset({"get", "keys", "values", "items", "copy", "count", "index",
                      "__contains__", "__getitem__", "__len__"})
# `.get` 으로 읽었는데 그 칸이 없는 바인딩 — 기본값이 온다(누가 읽나에 따라
# 기본값이 다르므로 증거에는 표시만 남기고 읽는 쪽이 채운다 · 실수 #430)
_MISSING = object()


def _own(body):
    """한 스코프의 노드 — 중첩 def·lambda·class 는 따로 훑는다."""
    stack = [x for x in body if not isinstance(x, _SCOPED)]
    while stack:
        x = stack.pop()
        yield x
        stack.extend(c for c in ast.iter_child_nodes(x)
                     if not isinstance(c, _SCOPED))


def _call_name(e):
    """호출의 이름 — `f(...)` · `m.f(...)` 의 f."""
    f = e.func
    return f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)


def _deco_name(d):
    if isinstance(d, ast.Call):
        d = d.func
    return d.id if isinstance(d, ast.Name) else getattr(d, "attr", None)


def _const_index(e):
    """칸 열쇠로 쓰는 상수 — 문자열(식별자가 아니어도) · 정수(음수 포함 · 불은
    아니다). 옛 판은 식별자 모양 문자열·0 이상 정수만 받아 `CTX['api-key']` ·
    `L[-1]` 을 아예 열쇠로 못 만들었다(실수 #430)."""
    if (isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.USub)
            and isinstance(e.operand, ast.Constant)
            and type(e.operand.value) is int):
        return -e.operand.value
    if isinstance(e, ast.Constant):
        v = e.value
        if isinstance(v, str):
            return v
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    return None


def _modkey(path):
    """모듈 객체의 열쇠 — `@` + 점을 `:` 로 바꾼 경로(점이 없어야 속성 열쇠와 안
    섞인다). `@__name__` 은 그 열쇠를 쓴 모듈 자신."""
    return "@" + path.replace(".", ":")


def _ns_base(e):
    """이름공간 식 → 그 객체의 열쇠 — `globals()` → 이 모듈 · `vars(X)` ·
    `X.__dict__` → X(모르는 객체면 ""). 그 칸은 속성이다(`globals()['D']` =
    전역 D). 이름공간이 아니면 None."""
    if isinstance(e, ast.Call) and not e.keywords and isinstance(e.func, ast.Name):
        if e.func.id == "globals" and not e.args:
            return "@__name__"
        if e.func.id == "vars" and len(e.args) == 1:
            return _key(e.args[0]) or ""
    if isinstance(e, ast.Attribute) and e.attr == "__dict__":
        return _key(e.value) or ""
    return None


def _key(e):
    """판정·대입 대상의 열쇠 — 이름(`d`) · 속성 사슬(`self.d`) · 상수 칸
    (`ctx['dart']` · `args[0]`). `getattr(o, "d")` 는 `o.d`, `m.get("dart")` 는
    `m['dart']?` 로 본다(#428 못 보는 축 — getattr·dict·리스트). 기본값을 준
    `getattr(o, "d", None)` 은 '아직 없을 수 있다'(지연 초기화)는 뜻이라 열쇠가
    아니다 — 그 판정은 살아 있다. `.get` 도 같은 뜻이다 — 없는 칸은 KeyError 가
    아니라 기본값이 온다. 그래서 칸 열쇠와 가르는 꼬리표(`?`)를 단다: 리터럴에
    그 칸이 없는 바인딩(`CTX = {}`)을 칸 열쇠는 '이 칸을 안 채운다' 로 건너뛰지만
    `.get` 열쇠는 '기본값이 온다 — 실리지 않음' 으로 센다(옛 판은 둘을 같은
    열쇠로 봐서 지연 초기화 `if CTX.get('d') is None: init()` 을 죽은 검사로
    잡았다, 2026-10-04 F2 테스트 중 발견). 기본값이 무엇이든 그 값은 따라가지
    않는다(못 보는 축 — 없는 칸은 '실리지 않음' 으로만 센다)."""
    if isinstance(e, ast.NamedExpr):
        e = e.target                     # `(d := get_dart()) is None`
    if isinstance(e, ast.Name):
        return e.id
    if isinstance(e, ast.Attribute):
        b = _key(e.value)
        return f"{b}.{e.attr}" if b else None
    if isinstance(e, ast.Subscript):
        i = _const_index(e.slice)
        ns = _ns_base(e.value)
        if ns is not None:              # globals()['D'] · vars(o)['d'] — 속성
            return (f"{ns}.{i}" if ns and isinstance(i, str) and i.isidentifier()
                    else None)
        b = _key(e.value)
        if b == "sys.modules":          # 그 모듈 객체
            if isinstance(i, str):
                return _modkey(i)
            if isinstance(e.slice, ast.Name) and e.slice.id == "__name__":
                return "@__name__"
            return None
        return f"{b}[{i!r}]" if b and i is not None else None
    if isinstance(e, ast.Call) and not e.keywords:
        f = e.func
        if ((isinstance(f, ast.Name) and f.id == "import_module"
             or isinstance(f, ast.Attribute) and f.attr == "import_module"
             and _key(f.value) == "importlib")
                and len(e.args) == 1 and isinstance(e.args[0], ast.Constant)
                and isinstance(e.args[0].value, str)):
            return _modkey(e.args[0].value)
        if (isinstance(f, ast.Name) and f.id == "getattr" and len(e.args) == 2
                and isinstance(e.args[1], ast.Constant)
                and isinstance(e.args[1].value, str)
                and e.args[1].value.isidentifier()):
            b = _key(e.args[0])
            return f"{b}.{e.args[1].value}" if b else None
        if (isinstance(f, ast.Attribute) and f.attr == "get"
                and 1 <= len(e.args) <= 2
                and _const_index(e.args[0]) is not None):
            b = _key(f.value)
            return f"{b}[{_const_index(e.args[0])!r}]?" if b else None
    return None


def _split(k):
    """열쇠의 마지막 접근자 — `a.b` → ("a", "attr", "b") · `a['x']` →
    ("a", "item", "x") · `a.get('x')` → ("a", "get", "x") · 이름 하나면 None.
    칸 열쇠는 repr 로 적었다 — 문자열 칸 안의 괄호·점에 속지 않게 오른쪽의
    `[` 부터 재파싱해 **리터럴로 읽히는** 첫 자리에서 가른다(실수 #430 — 옛
    판은 식별자·정수 칸만 있어 마지막 `[` 를 믿었다). 문자열 안의 `[` 뒤는
    리터럴이 못 된다 — repr 이 감싸는 따옴표를 안에서 늘 이스케이프해서다(무작위
    30만 개로 반례 없음을 쟀다). 못 가르면 던진다(우리가 만든 열쇠라 그건
    스캐너 결함이다)."""
    if k.endswith("?"):
        b, _kind, n = _split(k[:-1])
        return b, "get", n
    if k.endswith("]"):
        i = len(k) - 1
        while True:
            i = k.rindex("[", 0, i)
            lit = k[i + 1:-1]
            try:
                return k[:i], "item", ast.literal_eval(lit)
            except (ValueError, SyntaxError):
                continue
    if "." in k:
        b, a = k.rsplit(".", 1)
        return b, "attr", a
    return None


def _get_default(e):
    """`x.get(k, 기본값)` 의 기본값 식 — `.get` 이 아니면 None. 기본값이 None·빈
    값이어도 그대로 돌려준다: 그런 식은 어느 판정에서도 클라이언트를 싣지 않아
    걸러 두면 같은 답을 두 번 막는 죽은 가드였다(뮤테이션 D33 — 지우는 변형이
    전 슈트·레포 스캔에서 같은 답이었다)."""
    if (isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute)
            and e.func.attr == "get" and len(e.args) == 2 and not e.keywords):
        return e.args[1]
    return None


def _store_key(k):
    """그 칸에 **쓰는** 열쇠 — `.get` 으로 읽어도 쓰기는 `a['x'] = …` 다."""
    return k[:-1] if k and k.endswith("?") else k


def _root(k):
    return re.match(r"[@\w:]+", k).group()


def _is_none(e):
    return isinstance(e, ast.Constant) and e.value is None


def _unwrap_optional(e):
    """`Optional[C]` · `Union[C, None]` · `C | None` → C(몇 겹이든 · 실수 #430 —
    옛 판은 그 주석의 인자를 어느 객체인지 모른다고 봤다). 아니면 그대로."""
    while True:
        if isinstance(e, ast.Subscript):
            v = e.value
            head = v.attr if isinstance(v, ast.Attribute) else getattr(v, "id", None)
            if head == "Optional":
                e = e.slice
                continue
            if head == "Union" and isinstance(e.slice, ast.Tuple):
                rest = [x for x in e.slice.elts if not _is_none(x)]
                if len(rest) == 1 and len(rest) < len(e.slice.elts):
                    e = rest[0]
                    continue
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.BitOr):
            if _is_none(e.right):
                e = e.left
                continue
            if _is_none(e.left):
                e = e.right
                continue
        return e


def _is_const(e, *vals):
    return isinstance(e, ast.Constant) and any(e.value is v for v in vals)


def _unwrap_bool(e):
    """`bool(E)` → E(몇 겹이든)."""
    while (isinstance(e, ast.Call) and isinstance(e.func, ast.Name)
           and e.func.id == "bool" and len(e.args) == 1 and not e.keywords):
        e = e.args[0]
    return e


def _demorgan(e):
    """`not` 아래의 식 — 불 연산이면 각 항이 부정된다(`not (d and x)` =
    `not d or not x`). 부정의 부정(`not (… and not d)`)은 긍정이라 뺀다 — 그
    안쪽 `not d` 노드도 판정에서 빠진다(`_odd_negated`, 리뷰 F2). 긍정 비교를
    부정하면 부정 판정이다(`not (d is not None)` = `d is None`)."""
    if isinstance(e, ast.BoolOp):
        out = []
        for v in e.values:
            v = _unwrap_bool(v)
            if not (isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not)):
                out.extend(_demorgan(v))
        return out
    if isinstance(e, ast.Compare):
        sub = _pos_operand(e)
        if sub is not None:
            return [sub]
    return [e]


def _odd_negated(nodes):
    """부정 아래 **홀수 번** 놓인 판정 노드(`not`·비교)의 id — 부정의 부정은
    긍정이다(`not not d` · `not (x and not d)` · `not d is None`, 리뷰 F2).
    불 연산 사슬(`not`·`and`/`or`·`bool()`)만 따라간다 — 그 밖(호출 인자 등)에
    들어가면 새 사슬이다. 사슬의 뿌리에서 패리티를 센다(안쪽 `not` 에서 다시
    세면 `not not not d` 의 맨 안쪽을 두 번 뒤집는다)."""
    def kids(e):
        e = _unwrap_bool(e)
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not):
            return [e.operand]
        if isinstance(e, ast.BoolOp):
            return list(e.values)
        return []
    inner = {id(_unwrap_bool(c)) for x in nodes for c in kids(x)}
    odd: set = set()

    def walk(e, par):
        e = _unwrap_bool(e)
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not):
            if par:
                odd.add(id(e))
            walk(e.operand, not par)
        elif isinstance(e, ast.BoolOp):
            for v in e.values:
                walk(v, par)
        elif isinstance(e, ast.Compare) and par:
            odd.add(id(e))
    for x in nodes:
        if kids(x) and id(x) not in inner:
            walk(x, False)
    return odd


def _neg_operands(x):
    """부정 판정의 대상 식들 — `not E` · `E is None` · `E == None` · `None is E`
    · `E is False` · `E == False` · `E is not True` · `E != True`. 다른 철자도
    같다(#428 못 보는 축): `bool(E)` 는 E 로 풀고, `not (A and B)` ·
    `not (A or B)` 는 각 항이 부정된다."""
    if isinstance(x, ast.UnaryOp) and isinstance(x.op, ast.Not):
        return _demorgan(_unwrap_bool(x.operand))
    if isinstance(x, ast.Compare):
        sides = [x.left, *x.comparators]
        out = []
        for i, o in enumerate(x.ops):
            for a, b in ((sides[i], sides[i + 1]), (sides[i + 1], sides[i])):
                # `E is not True` 는 E 가 `bool(…)` 일 때만 참거짓 판정이다 — 객체를
                # True 와 견주면 늘 참이라 '키 없음' 을 가르는 판정이 아니다(F2).
                neg = ((isinstance(o, (ast.Is, ast.Eq))
                        and _is_const(b, None, False))
                       or (isinstance(o, (ast.IsNot, ast.NotEq))
                           and _is_const(b, True) and _unwrap_bool(a) is not a))
                if neg and not _is_const(a, None, False, True):
                    out.append(_unwrap_bool(a))
        return out
    return []


def _neg_key(x):
    """부정 판정의 열쇠 — 대상이 하나일 때(기본값 채우기 판정용)."""
    ops = _neg_operands(x)
    return _key(ops[0]) if len(ops) == 1 else None


def _pos_operand(t):
    """긍정 판정의 대상 — `if E:` · `if bool(E):` · `E is not None` ·
    `E != None` · `bool(E) is True`. 아니면 None.

    ⚠️ 날것의 `E is True`·`E == True` 는 아니다 — 클라이언트 객체는 True 가
    아니라 그 판정이 **늘 거짓**이고, else 가 오히려 유일하게 사는 갈래다(리뷰
    F2 — 옛 판은 그 else 를 죽은 갈래로 잡았다)."""
    t = _unwrap_bool(t)
    if isinstance(t, ast.Compare):
        if len(t.ops) != 1:
            return None
        o, sides = t.ops[0], (t.left, t.comparators[0])
        for a, b in (sides, sides[::-1]):
            if ((isinstance(o, (ast.IsNot, ast.NotEq))
                 and _is_const(b, None, False))
                    or (isinstance(o, (ast.Is, ast.Eq)) and _is_const(b, True)
                        and _unwrap_bool(a) is not a)):
                return _unwrap_bool(a)
        return None
    if isinstance(t, (ast.Name, ast.Attribute, ast.Subscript, ast.NamedExpr,
                      ast.Call)):
        return t
    return None


def _trivial(e):
    """기본값 — `None` · `""` · `0` · `False` · 빈 컨테이너."""
    if e is None:
        return True
    if isinstance(e, ast.Constant):
        return not e.value
    if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
        return not e.elts
    if isinstance(e, ast.Dict):
        return not e.keys
    return (isinstance(e, ast.Call) and _call_name(e) in (
        "dict", "list", "tuple", "set") and not e.args and not e.keywords)


def _trivial_branch(stmts):
    """기본값만 두는 갈래 — `pass` · 기본값 대입 · `return 기본값` · `continue` ·
    `break` · 설명 문자열. 그 밖의 무언가(로그·사유·예외·다른 경로)가 있으면 그
    갈래는 '클라이언트가 없을 때' 를 다루려던 것이다."""
    for y in stmts:
        if isinstance(y, (ast.Pass, ast.Continue, ast.Break)):
            continue
        if isinstance(y, ast.Expr) and isinstance(y.value, ast.Constant):
            continue
        if isinstance(y, (ast.Return, ast.Assign, ast.AnnAssign)) and _trivial(
                y.value):
            continue
        return False
    return True


def _is_generator(sc):
    """그 스코프가 제너레이터 함수인가(자기 본문의 `yield`)."""
    return any(isinstance(x, (ast.Yield, ast.YieldFrom)) for x in sc.nodes)


def _cands(v):
    """값의 후보 — `a if k else b` · `a or b` 는 갈래마다 본다(1차 규칙) ·
    `await x` 는 x 의 결과다(코루틴 함수가 돌려준 것)."""
    if isinstance(v, ast.Await):
        return _cands(v.value)
    if isinstance(v, ast.IfExp):
        return [v.body, v.orelse]
    if isinstance(v, ast.BoolOp):
        return list(v.values)
    return [v]


def _entry(v, kind, name):
    """리터럴 컨테이너 v 의 그 칸 — ("있음", 값식) · ("없음", None). 리터럴이
    아니면 None(무엇이 담겼는지 모른다). `.get`("get")은 칸과 같은 리터럴을
    본다 — 없는 칸의 뜻(기본값이 온다)은 부르는 쪽이 가른다."""
    if kind == "get":
        kind = "item"
    if kind == "item" and isinstance(v, ast.Dict):
        # 뒤에서부터 — 같은 열쇠는 마지막이 이긴다. `{**다른것}` 을 먼저 만나면
        # 그 앞의 칸은 덮였을지 모르고 그 칸이 거기서 올지도 모른다(모른다).
        # 펼침 **뒤** 에 적은 칸은 무엇이 펼쳐지든 그 값이다(실수 #430 — 옛
        # 판은 펼침이 하나라도 있으면 전부 모른다로 봤다)
        # 상수가 아닌 열쇠(`{k: None}` · f-문자열)도 같다 — 그 칸일 수 있다(독립 리뷰)
        for kk, vv in zip(reversed(v.keys), reversed(v.values)):
            c = None if kk is None else _const_index(kk)
            if c is None:
                return None
            if c == name and type(c) is type(name):
                return ("있음", vv)
        return ("없음", None)
    if kind == "item" and isinstance(v, (ast.List, ast.Tuple)):
        if (not isinstance(name, int)
                or any(isinstance(e, ast.Starred) for e in v.elts)):
            return None
        return (("있음", v.elts[name]) if -len(v.elts) <= name < len(v.elts)
                else ("없음", None))
    if (isinstance(v, ast.Call) and _NS_CALLS.get(_call_name(v)) == kind
            and not v.args and all(k.arg for k in v.keywords)):
        for k in v.keywords:
            if k.arg == name:
                return ("있음", k.value)
        return ("없음", None)
    return None


def _slots(v):
    """리터럴 컨테이너 v 의 칸들 [(종류, 이름)]."""
    if isinstance(v, ast.Dict):
        return [("item", c) for c in map(_const_index, v.keys) if c is not None]
    if isinstance(v, (ast.List, ast.Tuple)):
        return [("item", i) for i in range(len(v.elts))]
    if isinstance(v, ast.Call) and _call_name(v) in _NS_CALLS:
        return [(_NS_CALLS[_call_name(v)], k.arg) for k in v.keywords if k.arg]
    return []


def _arg_entry(s, base, kind, name):
    """받은 `*args`·`**kwargs` 의 그 칸 — 운반 꼬리표(다시 묶지 않았을 때만)."""
    if s.assigns.get(base):
        return None
    if base == s.vararg and kind == "item" and isinstance(name, int):
        return ("*", base, name)
    if (base == s.kwarg and kind in ("item", "get")
            and isinstance(name, str)):
        return ("**", base, name)
    return None


def _binds(node, k):
    """노드 아래에 이름 k 를 묶는 자리가 있나 — 대입·`del`·대입식·import·def·
    class 이름·`except … as`·패턴 이름·컴프리헨션 변수(보수적으로 센다). 중첩
    def·lambda·class 의 **본문**은 다른 스코프라 안 본다(이름 자체는 묶음이다)."""
    return k in _bound_names(node)


def _bound_names(node):
    """`_binds` 의 이름 집합 — 노드에 한 번만 재서 붙인다. 도달 정의는 같은
    문장을 이름마다 다시 묻는데, 문장 아래를 매번 훑으면 긴 모듈 맨 위에서 레포
    전수가 몇 배 느려졌다(실수 #430 실측)."""
    got = getattr(node, "_noah_bound", None)
    if got is not None:
        return got
    got = set()
    stack = [node]
    while stack:
        x = stack.pop()
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            got.add(x.id)
        elif isinstance(x, (ast.Import, ast.ImportFrom)):
            got.update(a.asname or a.name.split(".")[0] for a in x.names)
        elif isinstance(x, (*_FN, ast.ClassDef)):
            got.add(x.name)
            continue                        # 본문은 다른 스코프
        elif isinstance(x, ast.Lambda):
            continue
        elif isinstance(x, ast.ExceptHandler) and x.name:
            got.add(x.name)
        elif isinstance(x, (ast.MatchAs, ast.MatchStar)) and x.name:
            got.add(x.name)
        elif isinstance(x, ast.MatchMapping) and x.rest:
            got.add(x.rest)
        stack.extend(ast.iter_child_nodes(x))
    node._noah_bound = got
    return got


def _simple_binding(st, k):
    """문장 st 가 k 를 **그 값으로** 묶는 단순 바인딩이면 그 값, 아니면 `_NO`.
    `k = v` · `k: T = v` · `from m import k` · `import k` · `def k` · `class k`
    — 값식 안에 k 를 또 묶는 대입식이 있으면 단순하지 않다."""
    if isinstance(st, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == k for t in st.targets):
        if any(not isinstance(t, ast.Name) and _binds(t, k) for t in st.targets) \
                or _binds(st.value, k):
            return _NO
        return st.value
    if (isinstance(st, ast.AnnAssign) and st.value is not None
            and isinstance(st.target, ast.Name) and st.target.id == k):
        return _NO if _binds(st.value, k) else st.value
    if isinstance(st, ast.ImportFrom):
        for a in st.names:
            if (a.asname or a.name) == k:
                return (("import", st.module, a.name)
                        if st.level == 0 and st.module else None)
    if isinstance(st, ast.Import) and any(
            (a.asname or a.name.split(".")[0]) == k for a in st.names):
        return None
    if isinstance(st, (*_FN, ast.ClassDef)) and st.name == k:
        return None
    return _NO


_NO = object()          # `_simple_binding` 의 '단순 바인딩 아님'
_DYN = object()         # 쓰기 색인의 '어느 칸인지 모름'


def _blocks(st):
    """복합문 st 의 하위 문장 목록들 [(갈래, 목록)]."""
    out = []
    for name in ("body", "orelse", "finalbody"):
        b = getattr(st, name, None)
        if isinstance(b, list) and b and isinstance(b[0], ast.stmt):
            out.append((name, b))
    for h in getattr(st, "handlers", ()) or ():
        out.append(("handler", h.body))
    for c in getattr(st, "cases", ()) or ():
        out.append(("case", c.body))
    return out


def _header_binds(st, k):
    """복합문의 머리(본문 전에 평가되는 식)가 k 를 묶나 — if/while 조건 · for
    이터러블 · with 항목(`as` 포함) · match 대상."""
    parts = []
    if isinstance(st, (ast.If, ast.While)):
        parts = [st.test]
    elif isinstance(st, (ast.For, ast.AsyncFor)):
        parts = [st.iter]
    elif isinstance(st, (ast.With, ast.AsyncWith)):
        parts = [p for it in st.items
                 for p in (it.context_expr, it.optional_vars) if p is not None]
    elif isinstance(st, ast.Match):
        parts = [st.subject]
    return any(_binds(p, k) for p in parts)


class _Scope:
    """모듈·def·lambda·class 하나 — 그 안의 대입·import·선언을 들고 있다."""

    def __init__(self, mod, node, parent, kind, in_class=False):
        self.mod, self.node, self.parent, self.kind = mod, node, parent, kind
        self.in_class = in_class            # 클래스 본문 바로 아래의 def
        # `@staticmethod` 의 첫 인자는 self 가 아니다 — `self.f(x)` 의 x 는
        # 첫 인자로 간다(리뷰 L1: 건너뛰면 인자가 한 칸씩 밀린다).
        # `@classmethod` 는 클래스로 불러도 cls 가 묶여 온다(`C.m(x)`).
        decos = {_deco_name(d) for d in getattr(node, "decorator_list", ())}
        self.static = "staticmethod" in decos
        self.classm = "classmethod" in decos
        self.bases = list(node.bases) if kind == "class" else []
        self.nodes = list(_own([node.body] if kind == "lambda" else node.body))
        self.node_ids = {id(x) for x in self.nodes}
        a = getattr(node, "args", None)
        self.params = [x.arg for x in a.posonlyargs + a.args] if a else []
        self.kwonly = [x.arg for x in a.kwonlyargs] if a else []
        self.vararg = a.vararg.arg if a and a.vararg else None
        self.kwarg = a.kwarg.arg if a and a.kwarg else None
        # 인자 타입 주석 — `def f(o: C)` 의 o 는 C 인스턴스(객체 속성 축)
        self.ann = {x.arg: x.annotation
                    for x in (a.posonlyargs + a.args + a.kwonlyargs if a else [])
                    if x.annotation is not None}
        self.glob = {n for x in self.nodes if isinstance(x, ast.Global)
                     for n in x.names}
        self.nonloc = {n for x in self.nodes if isinstance(x, ast.Nonlocal)
                       for n in x.names}
        self.children: dict = {}            # 바로 아래 def·class 이름(뒤의 것)
        self.defs: dict = {}                # 이름 → 그 이름의 def·class 전부
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
                    else:
                        self._unpack(t, x.value, x.lineno, direct)
            elif (isinstance(x, (ast.AnnAssign, ast.NamedExpr))
                  and x.value is not None):
                self._add(_key(x.target), x.lineno, x.value)
                direct.add(id(x.target))
        # `setattr(o, 'd', v)` 는 `o.d = v` 다(리뷰 F2) — 이름이 상수가 아니면
        # 어느 속성인지 모른다(이 스코프는 아무 속성이나 쓸 수 있다).
        self.dyn_setattr = False
        # 쓰기 축(실수 #430 — 옛 판은 아래 쓰기를 못 봐, 그 칸·전역을 바꾸는 함수가
        # 있어도 보관소 규칙이 판정을 잡았다):
        self.dyn_items: dict = {}           # 컨테이너 열쇠 → [줄] — 어느 칸인지 모름
        self.tail: dict = {}                # 컨테이너 열쇠 → [(줄, 값)] — 끝에 붙임
        self.dyn_ns: list = []              # [(객체 열쇠, 줄)] — 이름 모르는 속성 쓰기
        for x in self.nodes:
            if (isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                    and x.func.id == "setattr" and len(x.args) == 3):
                nm, b = x.args[1], _key(x.args[0])
                if (isinstance(nm, ast.Constant) and isinstance(nm.value, str)
                        and nm.value.isidentifier() and b):
                    self._add(f"{b}.{nm.value}", x.lineno, x.args[2])
                else:
                    self.dyn_setattr = True
                    if b:
                        self.dyn_ns.append((b, x.lineno))
        for x in self.nodes:
            # 어느 칸인지 모르는 쓰기·지우기 — 변수 칸 · 이름공간의 식별자 아닌
            # 이름(음수 칸 쓰기는 열쇠로 남고, 색인이 양수 칸 읽기와 겹친다고 본다)
            if (isinstance(x, ast.Subscript)
                    and isinstance(x.ctx, (ast.Store, ast.Del))):
                i = _const_index(x.slice)
                ns = _ns_base(x.value)
                if ns is not None:
                    if not (ns and isinstance(i, str) and i.isidentifier()):
                        self._dyn_store(x.value, x.lineno)
                elif i is None:
                    self._dyn_store(x.value, x.lineno)
            elif isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute):
                self._method_store(x)
        for x in self.nodes:
            # `del` 도 그 칸·속성·이름을 바꾼다(없앤다 — 값 모름)
            if (isinstance(x, (ast.Name, ast.Attribute, ast.Subscript))
                    and isinstance(x.ctx, (ast.Store, ast.Del))
                    and id(x) not in direct):
                self._add(_key(x), getattr(x, "lineno", 0), None)
            elif isinstance(x, ast.ExceptHandler) and x.name:
                # `except … as e` · match 의 캡처 — 이름 노드가 아니라 옛 판은
                # 바인딩으로 안 셌다(else 판정이 그 갈래의 다른 값을 몰랐다, #430)
                self._add(x.name, x.lineno, None)
            elif isinstance(x, (ast.MatchAs, ast.MatchStar)) and x.name:
                self._add(x.name, x.lineno, None)
            elif isinstance(x, ast.MatchMapping) and x.rest:
                self._add(x.rest, x.lineno, None)
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
                # 판정이 조건의 일부(`if d is None or force:`)여도 같다.
                filled: dict = {}
                for y in x.body:
                    if (isinstance(y, (ast.Assign, ast.AnnAssign))
                            and y.value is not None):
                        for t in (y.targets if isinstance(y, ast.Assign)
                                  else [y.target]):
                            if _key(t):
                                filled.setdefault(_key(t), []).append(
                                    (y.lineno, y.value))
                for z in (ast.walk(x.test) if filled else ()):
                    nk = _store_key(_neg_key(z))
                    if nk in filled:
                        self.resolves[id(z)] = filled[nk]

    def _add(self, k, line, v):
        if k:
            self.assigns.setdefault(k, []).append((line, v))

    def _unpack(self, t, v, line, direct):
        """`a, b = v` — 자리마다 그 값(`v[i]` · 리터럴이면 그 원소). 별표
        하나(`a, *b, c = v`)는 앞은 `v[i]`, 뒤는 `v[-k]`, 별표 자리는 가운데
        조각이다(실수 #430 — 옛 판은 풀기를 모두 값 모름으로 봐 `a, b = args`
        뒤 b 의 판정을 놓쳤다). 리터럴 길이가 안 맞으면 자리를 모른다(값
        모름으로 남긴다). 별표 둘은 문법 오류라 오지 않는다."""
        n = len(t.elts)
        si = next((k for k, e in enumerate(t.elts) if isinstance(e, ast.Starred)),
                  None)
        lit = isinstance(v, (ast.Tuple, ast.List))
        if lit and (any(isinstance(z, ast.Starred) for z in v.elts)
                    or (len(v.elts) != n if si is None else len(v.elts) < n - 1)):
            return

        def sub(sl):
            return ast.copy_location(
                ast.Subscript(value=v, slice=sl, ctx=ast.Load()), t)

        for i, e in enumerate(t.elts):
            if i == si:
                after = n - 1 - si
                val = (ast.List(elts=v.elts[si:len(v.elts) - after], ctx=ast.Load())
                       if lit else sub(ast.Slice(
                           lower=ast.Constant(si),
                           upper=ast.Constant(-after) if after else None)))
                e = e.value
            elif si is not None and i > si:
                val = v.elts[i - n] if lit else sub(ast.Constant(i - n))
            else:
                val = v.elts[i] if lit else sub(ast.Constant(i))
            if isinstance(e, (ast.Tuple, ast.List)):
                self._unpack(e, val, line, direct)
            elif _key(e):
                self._add(_key(e), line, val)
                direct.add(id(e))

    def _dyn_store(self, base_expr, line):
        """어느 칸·속성인지 모르는 쓰기 — 이름공간이면 이름 모르는 속성
        (`dyn_ns` · else 판정은 아무 속성이나로 본다), 아니면 그 컨테이너의
        아무 칸(`dyn_items`)."""
        ns = _ns_base(base_expr)
        if ns is not None:
            self.dyn_setattr = True
            if ns:
                self.dyn_ns.append((ns, line))
            return
        bk = _key(base_expr)
        if bk:
            self.dyn_items.setdefault(bk, []).append(line)

    def _method_store(self, x):
        """컨테이너를 바꾸는 메서드 — `update`·`setdefault`·`__setitem__` 은 그
        칸에 쓴다 · `pop`(문자열 칸)·`__delitem__` 은 그 칸을 없앤다 · `clear`·
        `popitem`·모르는 `update`·`insert`·`remove`·`sort`·`reverse`·리스트
        `pop` 은 어느 칸인지 모른다 · `append`·`extend` 는 끝에 붙인다. 이름공간
        (`globals()`·`vars(o)`·`o.__dict__`)의 칸은 속성이다."""
        m, r, ln = x.func.attr, x.func.value, x.lineno
        if m not in _MUTATORS:
            return
        ns = _ns_base(r)
        if ns == "":                    # 모르는 객체의 이름공간 — 아무 속성이나
            self.dyn_setattr = True
            return
        bk = None if ns is not None else _key(r)
        if not (ns or bk):
            return

        def put(name, v):
            if name is None or (ns is not None and not (
                    isinstance(name, str) and name.isidentifier())):
                self._dyn_store(r, ln)
            elif ns is not None:
                self._add(f"{ns}.{name}", ln, v)
            else:
                self._add(f"{bk}[{name!r}]", ln, v)

        args = x.args
        if m == "update":
            for a in args:
                if isinstance(a, ast.Dict):
                    for kk, vv in zip(a.keys, a.values):
                        put(None if kk is None else _const_index(kk), vv)
                else:
                    put(None, None)
            for kw in x.keywords:
                put(kw.arg, kw.value)           # `**mapping` 은 arg 가 None
        elif m in ("setdefault", "__setitem__"):
            if args:
                put(_const_index(args[0]), args[1] if len(args) > 1 else None)
        elif m in ("pop", "__delitem__"):
            i = _const_index(args[0]) if args else None
            put(i if isinstance(i, str) else None, None)
        elif m in ("append", "extend"):
            if ns is not None:
                return
            if m == "append":
                vals = [args[0]] if args else [None]
            elif args and isinstance(args[0], (ast.List, ast.Tuple)):
                vals = list(args[0].elts)
            else:
                vals = [None]
            self.tail.setdefault(bk, []).extend((ln, v) for v in vals)
        else:
            put(None, None)

    def is_local(self, name):
        if self.kind == "module":
            return True
        if name in self.glob or name in self.nonloc:
            return False
        return (name in self.params or name in self.kwonly
                or name in (self.vararg, self.kwarg)
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
        self.lam: dict = {}                 # id(lambda 노드) → 그 스코프
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
                # 같은 이름의 def 가 여럿이면 **뒤의 것**(직선 코드에서 이기는 쪽)
                # — 옛 판은 뒤에서부터 훑어 앞의 정의가 남았다(#430)
                old = s.children.get(x.name)
                if old is None or old.node.lineno < x.lineno:
                    s.children[x.name] = c
                s.defs.setdefault(x.name, []).append(c)
                s._add(x.name, x.lineno, None)
            elif isinstance(x, ast.Lambda):
                self.lam[id(x)] = self._build(x, s, "lambda")
            else:
                stack.extend(ast.iter_child_nodes(x))
        return s


def scan(sources: dict) -> dict:
    """{모듈명: 소스} → {"hits": [(모듈, 줄, 식)], 계수…}.

    `get_dart()`/`DartClient()` 가 돌려준 클라이언트가 **어디로 흘러가든**
    부정 판정되는 자리를 찾는다 — 같은 함수(1차) · 클로저·lambda · 모듈 전역 ·
    `self.속성`(상속 포함) · 객체 속성(`C()` 인스턴스 · 타입 주석 인자) ·
    리터럴 칸(dict·리스트·`getattr`) · 다른 함수의 인자(`*args`·`**kwargs` ·
    미뤄 부르기 · 클래스로 부른 메서드 · 객체 메서드) · 그 클라이언트를
    돌려주는 함수의 반환값. 언제나 클라이언트인 값의 **긍정 판정 else 갈래**도
    죽은 검사다. 공장 이름이 나오는 모듈에서 시작해 클라이언트가 실려 가는
    호출의 대상 모듈, 공장을 감싼 함수·전역·클래스 이름이 나오는 모듈, 그
    모듈 클래스가 상속하는 클래스의 모듈만 그때그때 읽는다."""
    import sys
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 20_000))
    try:
        return _scan(sources)
    finally:
        sys.setrecursionlimit(old_limit)


def _scan(sources):
    mods: dict = {}
    scopes: list = []
    # 판정 메모 — 판정이 기대는 것(운반 인자·공장 함수·읽은 모듈)이 바뀌면 비운다.
    memo: dict = {}
    evals = [0]
    stack: dict = {}            # 진행 중인 판정 → 사슬 안 자리
    low = [_STACK_LIMIT]
    peak = [0]
    sym_cache: dict = {}
    mro_cache: dict = {}
    sub_cache: dict = {}
    fam_cache: dict = {}
    top_cache: dict = {}            # (모듈, 이름) → 맨 위 마지막 바인딩
    reach_cache: dict = {}          # (스코프, 이름, 줄) → 같은 블록 도달 정의
    xself_cache: dict = {}          # 클래스 → 그 메서드를 클래스로 부르는 자리가 있나
    attr_idx: dict = {}             # 속성 이름 → [(스코프, 바탕)] · None → 이름 모르는 setattr
    cs_idx: dict = {}               # (id(보관 스코프), 이름) → 그 컨테이너에 쓰는 자리
    nsdyn_idx: dict = {}            # 모듈 경로 → [(스코프, 줄)] — 이름 모르는 전역 쓰기

    def build(name):
        if name not in mods and name in sources:
            mods[name] = m = _Mod(name, ast.parse(sources[name]))
            scopes.extend(m.scopes)
            memo.clear()
            prov.clear()
            prov_list.clear()
            # 이름 해석·상속은 읽은 모듈에 기대므로 같이 비운다
            sym_cache.clear()
            mro_cache.clear()
            sub_cache.clear()
            fam_cache.clear()
            top_cache.clear()           # 다른 모듈의 `m.D = …` 가 판정을 바꾼다
            reach_cache.clear()
            xself_cache.clear()
            attr_idx.clear()
            cs_idx.clear()
            nsdyn_idx.clear()
            esc_cache.clear()           # 새 모듈의 스코프가 그 이름을 쓸 수 있다
            closed_cache.clear()
            back.clear()
            for g in gstk:
                g["tmemo"].clear()
        return mods.get(name)

    def mention(names):
        pat = re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, names)))
        for n, src in sources.items():
            if n not in mods and pat.search(src):
                build(n)

    carrier: dict = {}             # id(def) → 클라이언트가 실려 오는 인자(·칸)
    pcls: dict = {}                # id(def) → {인자: [넘어온 인스턴스의 클래스]}
    factory_fns: dict = {}         # id(def) → 이름(클라이언트를 돌려주는 def)

    # ── 판정 노드(메모 · 순환 감지) ────────────────────────────────────
    # 순환 안에서 잠정으로 '아니다' 가 된 판정 → [값, 그 판정이 기대는 가장 낮은
    # 진행 중 자리]. 순환의 뿌리가 끝날 때 한꺼번에 확정하거나 버린다 — 옛 판은
    # 뿌리만 메모하고 순환의 나머지를 닿을 때마다 다시 재서, 서로를 가리키는
    # 전역 N개에 판정이 지수로 늘었다(리뷰 F5 실측: N=18 이 35,363회 · N=22 가
    # 예산 초과).
    prov: dict = {}
    prov_list: list = []

    # 순환에서 증명되는 보관소 — 최소 고정점이 '아니다' 로 끝낸 순환의 뿌리를
    # **참으로 가정**하고 다시 잰다(최대 고정점 쪽 · 실수 #430). 가정에 기대
    # 얻은 참은 그 재기의 임시 메모(`tmemo`)에만 두고, 뿌리가 참으로 끝나며 가정
    # 없이 참인 근거(바닥: 공장 호출·가정 없이 증명된 이름)에 닿았을 때만(E-
    # 게이트) 확정한다 — 바닥 없이 서로만 가리키는 순환(`D = D`)은 값이 한 번도
    # 안 담기므로 확정하지 않는다. 거짓은 가정을 더해도 거짓이라(단조) 바로
    # 메모한다. 재기 안의 재기는 바깥 가정에 기댄 만큼 바깥 임시 메모로 간다.
    gstk: list = []             # 진행 중인 재기 [{root, tmemo}] (바깥→안)
    # 판정마다 [기댄 가장 바깥 재기 깊이(없으면 _NODEP), 바닥에 닿았나]. 임시
    # 메모는 (값, 바닥, 깊이) — 깊이로 그 참이 어느 재기의 가정까지 기대는지
    # 안다(재기가 확정되면 그 깊이 이상만 확정하고 나머지는 바깥으로 넘긴다)
    frames: list = []
    back: set = set()           # 진행 중에 자기 자신에게 되돌아온 판정
    _NODEP = 10 ** 9

    def _dep(d):
        if frames and d < frames[-1][0]:
            frames[-1][0] = d

    def _ground():
        if frames:
            frames[-1][1] = True

    def _sub(fn, *a):
        """하위 판정 하나를 따로 재서 (값, 기댄 깊이, 바닥) — 참이면 그 기댐·바닥을
        지금 판정에 올린다(거짓은 가정을 더해도 거짓이라 아무것도 안 올린다).
        ⚠️ 거짓의 기댐까지 올리는 변형(D22)은 결과를 바꾸는 입력을 못 찾았다
        (무작위 보관소 그래프 4,000 · 앞선 대입 사슬 차분 3,000) — 더 보수적인
        쪽이라 답은 같고 재기만 는다. 도달 불가라고 재지는 않았다(#377)."""
        frames.append([_NODEP, False])
        try:
            r = fn(*a)
        finally:
            d, gr = frames.pop()
        if r:
            _dep(d)
            if d == _NODEP or gr:
                _ground()
        return r

    def node(ck, fn):
        """판정 하나 — 메모하고, 순환은 진행 중인 판정을 '아니다' 로 보고 끊는다
        (최소 고정점 — 끝까지 따라가 증명된 것만 참). 옛 판은 깊이 6에서 끊어
        이름을 일곱 번 옮겨 담은 사슬을 놓쳤다(#428 못 보는 축). 참은 그
        가정('아니다')에 기대지 않으므로 바로 메모한다 — 다만 그 판정이 진행
        중이던 동안 생긴 잠정 '아니다' 는 그 판정을 '아니다' 로 가정했으니
        버린다(다시 닿으면 새로 잰다). 거짓은 자기보다 먼저 시작한 진행 중
        판정을 건드렸으면 잠정으로 두고, 순환의 뿌리(자기보다 먼저 시작한 것을
        건드리지 않은 판정)가 '아니다' 로 끝나면 그 아래 잠정을 전부 확정한다 —
        남은 잠정은 모두 '아니다' 끼리만 기대므로 그 배정이 고정점이고, 최소
        고정점도 그렇다(리뷰 F5). 보관소 판정의 순환 뿌리는 그 뒤 위의 '참으로
        가정한 재기' 를 한 번 더 받는다(실수 #430)."""
        if ck in memo:
            if memo[ck]:
                _ground()
            return memo[ck]
        for j in range(len(gstk) - 1, -1, -1):
            hit = gstk[j]["tmemo"].get(ck)
            if hit is not None:
                _dep(hit[2])
                if hit[1]:
                    _ground()
                return True
        for j, g in enumerate(gstk):
            if g["root"] == ck or (ck in stack and ck in g["assume"]):
                _dep(j)                     # 가정 — 바닥은 아니다
                return True
        if ck in stack:
            back.add(ck)
            low[0] = min(low[0], stack[ck])
            return False
        if ck in prov:
            low[0] = min(low[0], prov[ck][1])
            return False
        evals[0] += 1
        if evals[0] > _EVAL_BUDGET:
            raise RuntimeError(f"운반 판정 {_EVAL_BUDGET}회 초과 — 메모가 "
                               "깨졌거나 판정이 폭주한다(리뷰 L3)")
        idx = len(stack)
        if idx >= _STACK_LIMIT:
            raise RuntimeError(f"판정 사슬 {_STACK_LIMIT} 초과 — 재귀가 "
                               "폭주한다(조용히 '아니다' 로 접지 않는다)")
        stack[ck] = idx
        peak[0] = max(peak[0], idx + 1)
        saved, low[0] = low[0], idx
        start = len(prov_list)
        frames.append([_NODEP, False])
        try:
            v = fn()
        finally:
            del stack[ck]
            dep, fl = frames.pop()
        mine = low[0]
        low[0] = min(saved, mine) if stack else _STACK_LIMIT
        mine_entries = prov_list[start:]
        # 거짓은 가정을 더해도 거짓이다 — 거짓 가지의 기댐은 부모에 안 올린다
        # (올리면 바닥 없는 참이 바깥 재기로 미뤄져 확정된다)
        if v:
            for e in mine_entries:          # 이 판정을 '아니다' 로 가정한 잠정
                prov.pop(e, None)
            del prov_list[start:]
            back.discard(ck)
            if dep != _NODEP:               # 재기의 가정에 기댄 참 — 임시로만
                # 가장 안쪽 재기에 둔다 — 안쪽 가정에도 기댔을 수 있어 바깥에
                # 두면 안쪽이 기각돼도 살아남는다(안쪽이 확정되면 바깥으로 간다)
                gstk[-1]["tmemo"][ck] = (True, fl, dep)
                _dep(dep)
                if fl:
                    _ground()
                return True
            memo[ck] = True
            _ground()
            return True
        if mine < idx:                      # 더 먼저 시작한 판정에 기댄다 — 잠정
            for e in mine_entries:          # 이 판정에 기대던 것은 이제 그 아래에
                if prov[e][1] >= idx:
                    prov[e][1] = mine
            prov[ck] = [False, mine]
            prov_list.append(ck)
            back.discard(ck)
            return False
        cyc = ck in back
        back.discard(ck)
        if cyc:                             # 순환의 뿌리 — 참으로 가정해 다시
            for e in mine_entries:          # 그 순환의 잠정은 재기가 새로 잰다
                prov.pop(e, None)
            del prov_list[start:]
            got = _rerun(ck, fn, idx)
            if got is True:
                return True
            if got is not False:            # 조상에 닿았다 — 잠정으로 두고
                prov[ck] = [False, got]     # 그 조상의 판정이 다시 재게 한다
                prov_list.append(ck)
                return False
            memo[ck] = False
            return False
        for e in mine_entries:              # 순환의 뿌리가 '아니다' — 전부 확정
            memo[e] = prov.pop(e)[0]
        del prov_list[start:]
        memo[ck] = False
        return False

    def _rerun(ck, fn, idx):
        """순환의 뿌리 ck 를 참으로 가정하고 다시 잰다 — 참이고 바닥에 닿았으면
        확정(바깥 재기의 가정에 기댔으면 그 임시 메모로)하고 True, 아니면 False.
        다시 잰 판정이 뿌리보다 먼저 시작한 진행 중 판정(조상)을 건드렸으면 그
        답은 조상을 '아니다' 로 가정한 것이라 확정할 수 없다 — 그 조상의 자리를
        돌려줘 잠정으로 남긴다(조상이 순환의 뿌리로 끝날 때 다시 잰다). 재기는
        후보를 다 재므로 첫 판정이 안 닿던 조상에 닿을 수 있다."""
        k = len(gstk)
        prev: set = set()
        for _ in range(_RERUN_PASSES):
            g = {"root": ck, "tmemo": {}, "assume": prev}
            gstk.append(g)
            stack[ck] = idx
            saved, low[0] = low[0], idx
            start = len(prov_list)
            frames.append([_NODEP, False])
            try:
                v = fn()
            finally:
                del stack[ck]
                dep, fl = frames.pop()
                gstk.pop()
            mine = low[0]
            low[0] = min(saved, mine) if stack else _STACK_LIMIT
            for e in prov_list[start:]:     # 재기 안에서 끝나지 않은 잠정은 버린다
                prov.pop(e, None)
            del prov_list[start:]
            if mine < idx:
                return mine
            if not v:
                return False
            now = {key for key, hv in g["tmemo"].items() if hv[0]}
            if now == prev:
                break                       # 가정한 참이 전부 다시 참 — 자기 일관
            prev = now                      # 이번 회차의 참을 다음 회차의 가정으로
        else:
            return False                    # 수렴하지 않았다 — 놓치는 쪽
        out = dep if dep < k else _NODEP    # 바깥 재기의 가정에 기댄 깊이
        if out == _NODEP and not fl:
            return False                    # 바닥 없는 순환 — 값이 안 담긴다
        if out != _NODEP:
            # 바깥 가정에 기댔다 — 바닥 판정도 바깥으로 미룬다(바닥이 그 바깥
            # 쪽에 있을 수 있다). 이 뿌리에 기댄 참도 같이 그 깊이로 넘긴다
            for key, (_v, gr, d) in g["tmemo"].items():
                gstk[-1]["tmemo"][key] = (True, gr, min(d, out))
            gstk[-1]["tmemo"][ck] = (True, fl, out)
            _dep(out)
            if fl:
                _ground()
            return True
        for key, hv in g["tmemo"].items():
            # 이 뿌리의 가정에만 기댔다 — 확정. 바깥에 기댄 참은 거짓 가지에서만
            # 생기는데 그 가지는 첫 회차에 메모돼 마지막 회차 tmemo 엔 거의 안
            # 남는다 — 늘 확정하는 변형(D24)이 바꾸는 입력은 못 찾았다(위 D22 와
            # 같은 퍼징). 방어로 둔다
            if hv[2] >= k:
                memo[key] = True
            else:                           # 바깥에 기댄 거짓 가지의 참 — 넘긴다
                gstk[-1]["tmemo"][key] = hv
        memo[ck] = True
        _ground()
        return True

    # ── 이름 해석 ────────────────────────────────────────────────────
    hs_cache: dict = {}
    esc_cache: dict = {}
    closed_cache: dict = {}

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
            if name in s.assigns:
                return s
            # 바깥 클래스 본문의 이름은 안 보인다(파이썬 이름 규칙 — `_symbol` 도
            # 건너뛴다). 옛 판은 바로 위 클래스에서 찾아 중첩 클래스 본문의
            # 전역 이름을 바깥 클래스 이름으로 읽었다(실수 #430).
            cur = s.parent
            while cur.kind == "class":
                cur = cur.parent
            return holder(cur, name)
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
        """`m` · `bot.x` 같은 이름 사슬 → 모듈 경로(import 를 따라). 모르면 None.
        `@경로`(`sys.modules['m']` · `import_module('m')` · `globals()` 로 얻은
        모듈 객체 — `_modkey`)는 그 경로(`@__name__` 은 s 의 모듈)."""
        head = base.split(".")[0]
        if head.startswith("@"):
            mp = (s.mod.name if head == "@__name__"
                  else head[1:].replace(":", "."))
            return mp + base[len(head):]
        cur = s
        while cur is not None:
            if head in cur.imp_mod:
                return cur.imp_mod[head] + base[len(head):]
            if head in cur.imp_from:
                return ".".join(cur.imp_from[head]) + base[len(head):]
            cur = cur.parent
        return None

    def resolve(sym, lazy, hops=0):
        """기호 → 스코프. ("qual", 모듈, 이름) 은 그 모듈 맨 위의 def·class
        (다시 내보낸 이름은 몇 겹 따라간다)."""
        if sym[0] != "qual":
            return sym[1]
        m = build(sym[1]) if lazy else mods.get(sym[1])
        if m is None:
            return None
        c = m.top.children.get(sym[2])
        if c is None and sym[2] in m.top.imp_from and hops < 4:
            return resolve(("qual", *m.top.imp_from[sym[2]]), lazy, hops + 1)
        if c is None:
            # 맨 위에 lambda 로 묶은 이름(`mk = lambda: get_dart()`) — 마지막
            # 바인딩이 그 lambda 일 때만(실수 #430 — 옛 판은 다른 모듈에서
            # import 한 lambda 공장을 대상 모름으로 봤다)
            last = toplevel_last(m.top, sym[2])
            if last is not None and isinstance(last[1], ast.Lambda):
                c = m.lam.get(id(last[1]))
        return c

    def class_scope(s, expr, lazy=False):
        """식(이름 · `모듈.클래스` · 문자열 주석) → 클래스 스코프. 모르면 None."""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            try:
                expr = _unwrap_optional(ast.parse(expr.value, mode="eval").body)
            except SyntaxError:
                return None
            sym = _symbol(s, expr) if isinstance(expr, ast.Name) else None
        elif isinstance(_unwrap_optional(expr), ast.Name):
            expr = _unwrap_optional(expr)
            sym = symbol(s, expr)
        else:
            expr = _unwrap_optional(expr)
            sym = None
        if sym is None and isinstance(expr, ast.Attribute):
            b = _key(expr.value)
            mp = module_path(s, b) if b else None
            sym = ("qual", mp, expr.attr) if mp else None
        c = resolve(sym, lazy) if sym and sym[0] in ("qual", "scope") else None
        return c if c is not None and c.kind == "class" else None

    def bases_of(c):
        return [b for b in (class_scope(c.parent, e) for e in c.bases)
                if b is not None]

    def dfs_mro(cls):
        """왼쪽 우선 깊이 우선 — C3 가 서지 않는 계층(파이썬은 TypeError)의 근사."""
        out, todo = [], [cls]
        while todo:
            c = todo.pop(0)
            if c in out:
                continue
            out.append(c)
            todo[0:0] = bases_of(c)
        return out

    def mro(cls):
        """cls 와 조상 — 파이썬과 같은 **C3 선형화**(읽은 모듈 안에서만). 옛 판은
        왼쪽 우선 깊이 우선으로 근사해, 다이아몬드(`D(B, C)` · B·C 가 A 를
        상속)에서 A 의 메서드를 C 보다 먼저 골랐다(#429 못 보는 축 · 실수 #430).
        선형화가 안 서는 계층은 옛 근사로 둔다."""
        if id(cls) not in mro_cache:
            mro_cache[id(cls)] = [cls]      # 자기 상속 순환을 끊는 자리표시
            bases = bases_of(cls)
            seqs = [list(mro(b)) for b in bases] + [list(bases)]
            out = [cls]
            while True:
                seqs = [q for q in seqs if q]
                if not seqs:
                    break
                head = next((q[0] for q in seqs
                             if not any(q[0] in t[1:] for t in seqs)), None)
                if head is None:
                    out = dfs_mro(cls)
                    break
                out.append(head)
                for q in seqs:
                    if q[0] is head:
                        del q[0]
            mro_cache[id(cls)] = out
        return mro_cache[id(cls)]

    def subclasses(cls):
        """읽은 모듈에서 cls 를 조상으로 둔 클래스(색인은 한 번 만든다)."""
        if not sub_cache:
            sub_cache[None] = []                # 색인을 만들었다는 표시
            for c in scopes:
                if c.kind == "class":
                    for b in mro(c)[1:]:
                        sub_cache.setdefault(id(b), []).append(c)
        return sub_cache.get(id(cls), [])

    def family(cls):
        """self 가 될 수 있는 클래스들의 조상까지 — cls·조상·자손(자손이 그
        속성을 다르게 채우면 그 판정은 살아 있다)."""
        if id(cls) not in fam_cache:
            fam = []
            for c in [cls, *subclasses(cls)]:
                for x in mro(c):
                    if x not in fam:
                        fam.append(x)
            fam_cache[id(cls)] = fam
        return fam_cache[id(cls)]

    def find_method(cls, name):
        for c in mro(cls):
            m = c.children.get(name)
            if m is not None:
                return m
        return None

    def instance_of(s, e):
        """`C(...)` 면 C."""
        return (class_scope(s, e.func) if isinstance(e, ast.Call) else None)

    def instance_class(s, base, line):
        """`obj.m(...)` 의 obj 가 어느 클래스 인스턴스인가 — 바인딩 하나라도
        `C(...)` 면 C(1차 규칙과 같은 엄격) · 인자는 타입 주석으로 · self 속성은
        클래스 가족의 바인딩으로."""
        acc = _split(base)
        if acc is None:
            home = holder(s, base)
            if home is None:
                return None
            if base in home.ann:
                c = class_scope(home.parent or home, home.ann[base])
                if c is not None:
                    return c
            vals = [(home, v) for ln, v in home.assigns.get(base, ())
                    if home is not s or ln <= line]
        elif acc[1] == "attr" and method_of(s, acc[0]) is not None:
            vals = [(sc, v) for sc, _ln, v in
                    ev_attr(method_of(s, acc[0]).parent, acc[2])]
        else:
            return None
        for sc, v in vals:
            if isinstance(v, ast.AST):
                for c in _cands(v):
                    k = instance_of(sc, c)
                    if k is not None:
                        return k
        return None

    def symbol(s, func):
        ck = (id(s), id(func))
        if ck not in sym_cache:
            sym_cache[ck] = _symbol(s, func)
        return sym_cache[ck]

    def _symbol(s, func):
        """호출 대상 → ("scope"|"method"|"classattr", 스코프) · ("qual", 모듈,
        이름) · None."""
        if isinstance(func, ast.Name):
            cur = s
            while cur is not None:
                if cur.kind == "class" and cur is not s:
                    cur = cur.parent
                    continue
                if cur.kind == "module":
                    b = module_binding(s, cur, func.id, func)
                    if b is not None:
                        return (last_symbol(cur, func.id, b[1]) if b[0] == "at"
                                else None)
                if func.id in cur.imp_from:
                    return ("qual", *cur.imp_from[func.id])
                c = cur.children.get(func.id)
                if c is not None:
                    return ("scope", c)
                if cur.kind != "module" and cur.is_local(func.id):
                    # 같은 블록에서 닿는 값이 lambda 면 그것(`mk = lambda: …`)
                    r = (reaching(cur, func.id, func.lineno) if cur is s
                         else None)
                    if (r is not None and r[0] == "def"
                            and isinstance(r[2], ast.Lambda)
                            and id(r[2]) in cur.mod.lam):
                        return ("scope", cur.mod.lam[id(r[2])])
                    return None          # 다른 값이 그 이름을 가렸다
                cur = cur.parent
            return None
        if isinstance(func, ast.Attribute):
            base = _key(func.value)
            if base is None:
                return None
            meth = method_of(s, base)
            if meth is not None:                    # self.m(...) — 상속 포함
                c = find_method(meth.parent, func.attr)
                return ("method", c) if c is not None else None
            cls = class_scope(s, func.value)
            if cls is not None:                     # C.m(...) — 클래스로 부름
                c = find_method(cls, func.attr)
                return ("classattr", c) if c is not None else None
            k = instance_class(s, base, func.lineno)
            if k is not None:                       # obj.m(...) — obj = C(...)
                c = find_method(k, func.attr)
                return ("method", c) if c is not None else None
            modp = module_path(s, base)
            return ("qual", modp, func.attr) if modp else None
        return None

    def target(s, func, lazy):
        """호출 대상 def 와 첫 인자(self) 건너뛰기. 클래스면 `__init__`(상속
        포함)."""
        sym = symbol(s, func)
        if sym is None:
            return None, False
        how = sym[0]
        if how == "qual":
            c = resolve(sym, lazy)
            if c is None and "." in sym[1]:
                # `모듈.클래스.메서드` · import 한 클래스의 메서드(클래스로 부름)
                pm, cn = sym[1].rsplit(".", 1)
                m = build(pm) if lazy else mods.get(pm)
                cls = m.top.children.get(cn) if m else None
                if cls is not None and cls.kind == "class":
                    c, how = find_method(cls, sym[2]), "classattr"
        else:
            c = sym[1]
        if c is None:
            return None, False
        if c.kind == "class":
            init = find_method(c, "__init__")
            return (init, True) if init else (None, False)
        if c.kind not in ("def", "lambda"):
            return None, False
        if how == "method":
            return c, not c.static
        if how == "classattr":
            return c, c.classm
        return c, False

    def real_factory(s, func):
        """부른 것이 `bot.dart_client` 의 공장인가 — 이름만 같은 사용자 함수·
        메서드·인자는 아니다(리뷰 F2). import 없이 맨 이름으로 부르면 공장으로
        본다(픽스처 관례 — 운영 코드는 import 가 있어 그 경로로 가린다)."""
        if isinstance(func, ast.Name):
            if func.id not in s.mod.factories:
                return False
            sym = symbol(s, func)
            if sym is not None:
                return (sym[0] == "qual" and sym[1] == _DART_MOD
                        and sym[2] in _FACTORIES)
            h = holder(s, func.id)
            return (h is not None and h.kind == "module"
                    and func.id in _FACTORIES and func.id not in h.assigns)
        if isinstance(func, ast.Attribute):
            b = _key(func.value)
            return (func.attr in _FACTORIES and b is not None
                    and module_path(s, b) == _DART_MOD)
        return False

    def inert(top, st):
        """import 시점에 우리 함수를 부를 수 없는 맨 위 문장 — import · 장식·호출
        없는 def · 호출 없는 대입(`bot.dart_client` 공장 호출은 된다 — 이 모듈을
        다시 부르지 않는다) · 설명 문자열 · pass."""
        if isinstance(st, (ast.Import, ast.ImportFrom, ast.Pass)):
            return True
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            return True
        if isinstance(st, _FN):
            a = st.args
            evald = [*a.defaults, *[d for d in a.kw_defaults if d is not None],
                     *[x.annotation for x in (*a.posonlyargs, *a.args,
                                              *a.kwonlyargs)
                       if x.annotation is not None]]
            if st.returns is not None:
                evald.append(st.returns)
            return not st.decorator_list and not any(
                isinstance(n, ast.Call) for d in evald for n in ast.walk(d))
        if isinstance(st, (ast.Assign, ast.AnnAssign)):
            # 맨 위가 직접 평가하는 호출만 — lambda 본문은 나중에 돈다(바로 부른
            # `(lambda: …)()` 는 대상이 lambda 라 공장이 아니다 — 막는 쪽)
            return st.value is None or all(
                real_factory(top, n.func) for n in _own([st.value])
                if isinstance(n, ast.Call))
        return False

    def toplevel_last(top, k):
        """모듈 맨 위에서만 묶이는 이름의 **마지막** 바인딩 — (줄, 값, 문장) 또는
        None(모른다 — 옛 규칙으로 간다). 조건: 바인딩이 **전부** 모듈 맨 위의
        직접 문장(중첩·풀기·for 없음) · 다른 스코프(global)·다른 모듈(`m.k = …`)·
        이름 모르는 setattr 이 안 쓴다 · 첫 바인딩부터 마지막 바인딩까지의 문장이
        import 시점에 우리 함수를 부를 수 없다(`inert`). 그러면 함수 안에서 읽는
        값은 늘 마지막 바인딩이다(실수 #430 — 옛 판은 바인딩이 여럿이면 모두를
        봐, `_D = None` 뒤 `_D = get_dart()` 를 놓치고 import 뒤 재정의한 공장
        이름을 공장으로 봤다)."""
        ck = (id(top), k)
        if ck not in top_cache:
            top_cache[ck] = _toplevel_last(top, k)
        return top_cache[ck]

    def _toplevel_last(top, k):
        if top.kind != "module":
            return None
        evs = top.assigns.get(k, ())
        if not evs or any(t is not top and holder(t, k) is top
                          for t in top.mod.writers.get(k, ())):
            return None
        body = top.node.body
        idx = [i for i, st in enumerate(body) if _simple_binding(st, k) is not _NO]
        if len(idx) != len(evs) or module_written(top, k):
            return None
        if len(idx) > 1 and not all(inert(top, st)
                                    for st in body[idx[0]:idx[-1] + 1]):
            return None
        st = body[idx[-1]]
        return (st.lineno, _simple_binding(st, k), st)

    def last_symbol(top, name, last):
        """맨 위 마지막 바인딩 → 호출 대상 기호(`_symbol` 과 같은 꼴)."""
        st = last[2]
        if isinstance(st, ast.ImportFrom):
            v = last[1]
            return ("qual", v[1], v[2]) if isinstance(v, tuple) else None
        if isinstance(st, (*_FN, ast.ClassDef)):
            return next((("scope", c) for c in top.defs.get(name, ())
                         if c.node is st), None)
        if isinstance(last[1], ast.Lambda) and id(last[1]) in top.mod.lam:
            return ("scope", top.mod.lam[id(last[1])])  # `mk = lambda: …`
        return None                         # 대입·import 한 모듈 — 대상을 모른다

    def at_import(s, use):
        """이름 노드 use 가 import 시점에 **그 자리에서** 평가되나 — s 의 본문
        노드이고 s 가 모듈 맨 위이거나 맨 위까지 클래스 본문만 거친다(def·lambda
        본문은 나중에 돈다). 주석·기반 클래스·장식·기본값은 어느 스코프의 본문
        노드도 아니라 이 판정 밖이다(옛 규칙 — 문자열 주석은 줄 번호가 없다)."""
        if use is None or id(use) not in s.node_ids:
            return False
        cur = s
        while cur.kind == "class":
            cur = cur.parent
        return cur.kind == "module"

    def module_binding(s, h, name, use):
        """모듈 h 의 이름 name 이 그 자리(s 의 이름 노드 use)에서 가리키는 바인딩
        — ("at", (줄, 값, 문장)) · ("unbound",) · None(모른다 — 옛 규칙).

        import 시점 코드(`at_import`)면 **그 줄에 닿는** 바인딩, 함수 안이면 맨 위
        마지막 바인딩(`toplevel_last` — 함수는 import 가 끝난 뒤 돈다). 옛 판은
        맨 위 문장에도 마지막 바인딩을 써 `_D = get_dart()` 뒤 `def get_dart()`
        를 재정의로 읽었고, 판정 순서에 따라 답이 갈렸으며, `get_dart =
        get_dart()` 에서 맨 위 판정이 자기를 다시 불러 끝없이 돌았다(실수 #430)."""
        if at_import(s, use):
            r = reaching(h, name, use.lineno)
            if r is None:
                return None
            return ("at", r[1:]) if r[0] == "def" else ("unbound",)
        last = toplevel_last(h, name)
        return ("at", last) if last is not None else None

    def ev_eff(home, k):
        """보관소 이름의 **효력 있는** 바인딩 — 모듈 맨 위 마지막 바인딩을 알면 그
        하나, 아니면 전부(`ev_name`)."""
        if home.kind == "module":
            last = toplevel_last(home, k)
            if last is not None:
                return [(home, last[0], last[1])]
        return ev_name(home, k)

    def reaching(s, k, line):
        """같은 블록 사슬에서 그 줄에 닿는 지역 이름 k 의 값 — ("def", 줄, 값,
        문장) · ("param",) · ("unbound",) · None(모른다 — 옛 규칙으로 간다).
        모듈이면 맨 위 클래스 본문 안의 줄도 된다 — 그 사슬의 클래스 본문이 k 를
        묶으면 클래스 이름이라 None.

        옛 판은 같은 스코프의 **앞선 바인딩 하나라도**(1차 규칙) 봐서
        `d = get_dart(); d = None; if d is None:` 을 잡았다(오탐, #429 못 보는
        축). 그 줄을 담은 문장에서 위로 거슬러 올라가며 k 를 묶는 첫 문장을 찾는다
        — 단순 바인딩이면 그것이 유일하게 닿는 값이다(사이에 k 를 묶는 문장이
        없고, 반복문의 다음 바퀴가 닿지 않을 때만). 갈래·반복·예외로 여러 값이
        닿을 수 있으면 None."""
        ck = (id(s), k, line)
        if ck not in reach_cache:
            reach_cache[ck] = _reaching(s, k, line)
        return reach_cache[ck]

    def _reaching(s, k, line):
        if s.kind not in ("def", "module") or not k.isidentifier() \
                or holder(s, k) is not s:
            return None
        if s.kind == "module":
            if s.mod.writers.get(k) or module_written(s, k):
                return None
        elif any(holder(t, k) is s for t in s.mod.writers.get(k, ())):
            return None                     # 안쪽 함수가 nonlocal 로 바꾼다
        path, stmts, in_cls = [], s.node.body, False
        while True:
            hits = [i for i, st in enumerate(stmts)
                    if st.lineno <= line <= (st.end_lineno or st.lineno)]
            if len(hits) != 1:
                return None                 # 한 줄에 문장이 여럿 — 가르지 않는다
            st = stmts[hits[0]]
            sub = next(((kind, b) for kind, b in _blocks(st)
                        if b[0].lineno <= line <= (b[-1].end_lineno or line)),
                       None)
            path.append((stmts, hits[0], st, sub[0] if sub else None, in_cls))
            if sub is None:
                break
            stmts, in_cls = sub[1], isinstance(st, ast.ClassDef)
        stmts, i, st, _kind, _cls = path[-1]
        # 그 줄의 문장 — 머리(조건 등)면 그 머리가, 단순문이면 문장 전체가 k 를
        # 먼저 묶을 수 있다(대입식). while 머리는 본문이 다음 바퀴에 닿는다.
        if _blocks(st):
            if _header_binds(st, k):
                return None
            if isinstance(st, ast.While) and _binds(st, k):
                return None
        elif any(isinstance(n, ast.NamedExpr) and _binds(n.target, k)
                 for n in ast.walk(st)):
            return None
        for level in range(len(path) - 1, -1, -1):
            stmts, i, st, kind, in_cls = path[level]
            if level < len(path) - 1:
                # 하위 갈래에서 올라왔다 — 그 복합문의 성질
                if isinstance(st, (ast.For, ast.AsyncFor, ast.While)) and _binds(
                        st, k):
                    return None             # 다음 바퀴(또는 else)가 닿는다
                if isinstance(st, ast.Try) or type(st).__name__ == "TryStar":
                    if kind != "body" and _binds(st, k):
                        return None         # 일부만 돈 try 본문이 닿는다
                if _header_binds(st, k):
                    return None
                if kind == "case" and _binds(st, k):
                    return None             # 패턴·가드가 묶을 수 있다
            for prev in reversed(stmts[:i]):
                if not _binds(prev, k):
                    continue
                if in_cls:
                    return None             # 클래스 본문의 이름 — 모듈 이름이 아니다
                v = _simple_binding(prev, k)
                return None if v is _NO else ("def", prev.lineno, v, prev)
        if k in s.params or k in s.kwonly:
            return ("param",)
        return ("unbound",)

    def shadowed(s, name, use=None):
        """그 이름이 공장이 아닌 다른 값으로 묶였나 — 인자·지역 대입 · 모듈의
        import 아닌 대입. 공장 import 가 바인딩에 **하나라도** 있으면 가린 게
        아니다(델타 리뷰 M1 — 부정 판정은 갈래 하나라도: `try: from
        bot.dart_client import get_dart` / `except: def get_dart(): …` 는 import
        가 성공하는 갈래에서 키 없는 클라이언트를 준다. 옛 판은 공장 모듈을 같이
        읽은 실행에서만 그걸 잡았다)."""
        h = holder(s, name)
        if h is None:
            return False
        if h.kind == "module":
            b = module_binding(s, h, name, use)
            if b is not None and b[0] == "unbound":
                # 그 줄 앞에 묶인 것이 없다 — 어디서도 안 묶은 맨 공장 이름(픽스처
                # 관례)만 가리지 않는다. 뒤에서 묶는 이름은 그 자리에서 없다.
                return name in h.assigns
            if b is not None:               # 효력 있는 바인딩 하나로 가른다
                v = b[1][1]
                return not (isinstance(v, tuple) and v[0] == "import"
                            and v[1] == _DART_MOD and v[2] in _FACTORIES)
        if any(isinstance(v, tuple) and v[0] == "import" and v[1] == _DART_MOD
               and v[2] in _FACTORIES for _sc, _ln, v in ev_name(h, name)):
            return False
        if h.kind != "module":
            return h.is_local(name)
        return any(not (isinstance(v, tuple) and v[0] == "import")
                   for _ln, v in h.assigns.get(name, ()))

    def is_factory_call(s, e):
        """부정 판정 쪽(갈래 하나라도) — 공장이거나 클라이언트를 돌려줄 수 있는
        함수. 대상을 찾으면 그 대상으로 가른다(이름이 같은 사용자 함수는 그
        함수가 클라이언트를 주는가로) · 인자·지역 값으로 가린 이름은 아니다 ·
        대상을 모르는 이름(`x.get_dart()`)은 이름으로 본다(못 보는 축)."""
        if not isinstance(e, ast.Call):
            return False
        t, _ = target(s, e.func, lazy=False)
        if t is not None:
            return id(t) in factory_fns or real_factory(s, e.func)
        if isinstance(e.func, ast.Name) and shadowed(s, e.func.id, e.func):
            return False
        return _call_name(e) in s.mod.factories

    # ── 증거(함수 경계를 넘는 보관소의 모든 바인딩) ─────────────────────
    def ev_name(home, k):
        """보관소(모듈·바깥 함수) 이름 k 의 모든 바인딩 — global·nonlocal 로 쓰는
        다른 스코프 포함 · 인자면 ("param", 이름)."""
        ev = [(home, ln, v) for ln, v in home.assigns.get(k, ())]
        for t in home.mod.writers.get(k, ()):
            if t is not home and holder(t, k) is home:
                ev += [(t, ln, v) for ln, v in t.assigns.get(k, ())]
        if home.kind == "module":
            ev += module_attr_writes(home, k)
        if k in home.params or k in home.kwonly:
            ev.append((home, 0, ("param", k)))
        if k in (home.vararg, home.kwarg):
            ev.append((home, 0, None))      # 인자 묶음 자체는 클라이언트가 아니다
        return ev

    def module_attr_writes(top, k):
        """모듈 객체로 그 전역에 쓰는 자리 — `m.k = …` · `setattr(m, 'k', …)` ·
        `vars(m)['k'] = …` · `globals()['k'] = …` · `sys.modules['m'].k = …` ·
        `import_module('m').k = …`(값과 함께, 어느 모듈에서든) · 이름 모르는 쓰기
        (`globals().update(x)` · `setattr(m, n, …)` — 값 모름). 옛 판은 else
        판정만 이 쓰기를 막는 쪽으로 봤고 부정 판정의 보관소 규칙은 못 봤다(오탐,
        실수 #430)."""
        out = []
        for sc, b in attr_stores(k)[1]:
            if module_path(sc, b) == top.mod.name:
                out += [(sc, ln, v) for ln, v in sc.assigns.get(f"{b}.{k}", ())]
        if not nsdyn_idx:
            nsdyn_idx[None] = []
            for sc in scopes:
                for b, ln in sc.dyn_ns:
                    nsdyn_idx.setdefault(module_path(sc, b), []).append((sc, ln))
        out += [(sc, ln, None) for sc, ln in nsdyn_idx.get(top.mod.name, ())]
        return out

    def container_targets(sc, b):
        """스코프 sc 의 컨테이너 열쇠 b 가 가리킬 수 있는 보관소 [(id(스코프),
        이름)] — 그 이름이 사는 스코프 · 그 이름이 다른 모듈에서 import 한 것이면
        그 모듈의 이름도(같은 객체다) · `m.X` · `sys.modules['m'].X` 면 모듈 m 의 X."""
        acc = _split(b)
        if acc is None:
            if b.startswith("@"):
                return []
            h = holder(sc, b)
            if h is None:
                return []
            out = [(id(h), b)]
            for _ln, v in h.assigns.get(b, ()):
                if isinstance(v, tuple) and v[0] == "import" and v[1] in mods:
                    out.append((id(mods[v[1]].top), v[2]))
            return out
        x, kind, nm = acc
        if kind == "attr":
            mp = module_path(sc, x)             # `pkg.h.CTX` 처럼 점이 여럿이어도
            if mp in mods:
                return [(id(mods[mp].top), nm)]
        return []

    def container_index():
        """(id(보관 스코프), 이름) → [(스코프, 종류, 칸, 줄, 값)] — 읽은 모듈의 모든
        칸·속성 쓰기를 그 컨테이너가 사는 보관소로 모은다(한 번 만들고, 모듈을 더
        읽으면 비운다). 종류: "item"·"attr" · 어느 칸인지 모르면 칸이 `_DYN` ·
        끝에 붙인 값은 "tail"."""
        if not cs_idx:
            cs_idx[None] = []
            for sc in scopes:
                for k, evs in sc.assigns.items():
                    acc = _split(k)
                    if acc is None or acc[1] == "get":
                        continue
                    b, kind, nm = acc
                    for tgt in container_targets(sc, b):
                        cs_idx.setdefault(tgt, []).extend(
                            (sc, kind, nm, ln, v) for ln, v in evs)
                for b, lines in sc.dyn_items.items():
                    for tgt in container_targets(sc, b):
                        cs_idx.setdefault(tgt, []).extend(
                            (sc, "item", _DYN, ln, None) for ln in lines)
                for b, vals in sc.tail.items():
                    for tgt in container_targets(sc, b):
                        cs_idx.setdefault(tgt, []).extend(
                            (sc, "tail", None, ln, v) for ln, v in vals)
        return cs_idx

    def ev_attr(cls, attr):
        """`self.attr` — 클래스 가족(조상·자손)의 메서드 대입 + 클래스 본문.
        `property`·`cached_property` 게터면 그 게터를 부른 값(`("call", 게터)`)."""
        ev = []
        for c in family(cls):
            for m in c.children.values():
                if m.kind == "def" and m.params and not m.static:
                    ev += [(m, ln, v) for ln, v in
                           m.assigns.get(f"{m.params[0]}.{attr}", ())]
            gs = prop_getters(c, attr)
            ev += [(c, ln, ("call", gs[ln]) if ln in gs else v)
                   for ln, v in c.assigns.get(attr, ())]
        return ev

    def prop_getters(c, attr):
        """클래스 c 본문의 `attr` def 중 property 게터 — {def 줄: def}. 장식이
        `property`·`cached_property` **하나**일 때만(겹친 장식은 그 값을 무엇이
        감싸는지 모른다). 그 밖의 같은 이름 def(`@dart.setter` · 다른 장식)는
        제 바인딩 그대로다 — 클라이언트가 아닌 바인딩이 하나라도 있으면 보관소
        규칙이 판정을 막는다(실수 #430 — 옛 판은 게터를 값 모르는 바인딩으로
        봤고, 같은 이름 def 가 둘이면 if/else 로 고른 게터 둘까지 통째로 놓쳤다)."""
        out = {}
        for g in c.defs.get(attr, ()):
            decos = g.node.decorator_list if g.kind == "def" else ()
            if len(decos) != 1:
                continue
            d = decos[0]
            if isinstance(d, ast.Name):
                src = deco_source(c, d.id)
            elif isinstance(d, ast.Attribute) and _key(d.value):
                src = (module_path(c, _key(d.value)), d.attr)
            else:
                continue
            if src in _PROPERTY_SRC:
                out[g.node.lineno] = g
        return out

    def member_ev(base_ev, kind, name, stores, seen, tail=()):
        """담는 쪽(base)의 바인딩 + 그 칸에 직접 쓴 것 → 그 칸의 증거. 담는
        쪽이 리터럴이면 그 칸(없으면 그 바인딩은 이 칸을 안 채운다 — 읽으면
        KeyError 다. `.get` 으로 읽으면 기본값이 오므로 '실리지 않음' 으로 센다)
        · `C(...)` 인스턴스면 C 의 속성 · 모르는 값이면 None(무엇이 담겼는지
        모른다)."""
        ev = list(stores)
        reach = False
        for sc, ln, v in base_ev:
            if isinstance(v, tuple) and v[0] == "import":
                m = mods.get(v[1])
                sub = member_ev_at(m.top, v[2], kind, name, seen) if m else None
                if sub is None:
                    return None
                ev += sub
                continue
            if isinstance(v, tuple) and v[0] == "param":
                cls = (class_scope(sc.parent or sc, sc.ann[v[1]])
                       if kind == "attr" and v[1] in sc.ann else None)
                if cls is None:
                    return None             # 인자 — 무엇이 담겼는지 모른다
                ev += ev_attr(cls, name)
                continue
            if not isinstance(v, ast.AST):
                return None
            for c in _cands(v):
                e = _entry(c, kind, name)
                if e is not None:
                    if e[0] == "있음":
                        ev.append((sc, ln, e[1]))
                    elif kind == "get":         # 없는 칸 — 기본값이 온다
                        ev.append((sc, ln, _MISSING))
                    if (isinstance(name, int) and isinstance(c, (ast.List, ast.Tuple))
                            and not -len(c.elts) <= name < len(c.elts)):
                        reach = True            # 끝에 붙인 값이 그 칸일 수 있다
                    continue
                cls = instance_of(sc, c) if kind == "attr" else None
                if cls is None:
                    return None
                ev += ev_attr(cls, name)
        if tail and isinstance(name, int):
            if name < 0:
                return None                     # 끝이 바뀌면 음수 칸은 모른다
            if reach:
                ev += tail
        return ev

    def member_ev_at(home, base, kind, name, seen=frozenset()):
        """보관소 이름 base 의 칸 — 그 칸에 쓰는 **모든 스코프**(global 선언
        없이도 쓴다) + base 의 바인딩."""
        if (id(home), base) in seen:
            return None
        seen = seen | {(id(home), base)}
        want = "item" if kind == "get" else kind
        stores, tail = [], []
        for sc, k2, nm, ln, v in container_index().get((id(home), base), ()):
            if k2 == "tail":
                if want == "item":
                    tail.append((sc, ln, v))
            elif k2 != want:
                continue
            elif nm is _DYN:
                stores.append((sc, ln, None))   # 어느 칸인지 모르는 쓰기
            elif (nm == name and type(nm) is type(name)) or (
                    isinstance(nm, int) and isinstance(name, int)
                    and (nm < 0 or name < 0)):  # 음수 칸은 어느 칸과도 겹칠 수 있다
                stores.append((sc, ln, v))
        ent = _arg_entry(home, base, kind, name)
        if ent is not None:
            return [(home, 0, ("ent", ent))] + stores
        return member_ev(ev_eff(home, base), kind, name, stores, seen, tail)

    def attr_member_ev(cls, attr, kind, name):
        """`self.attr` 의 칸·속성(`self.ctx['d']` · `self.h.d`)."""
        stores = []
        for c in family(cls):
            for m in c.children.values():
                if m.kind == "def" and m.params and not m.static:
                    mk = (f"{m.params[0]}.{attr}.{name}" if kind == "attr"
                          else f"{m.params[0]}.{attr}[{name!r}]")
                    stores += [(m, ln, v) for ln, v in m.assigns.get(mk, ())]
        return member_ev(ev_attr(cls, attr), kind, name, stores, frozenset())

    def all_bear(ev, bear):
        """보관소 규칙 — 증거가 있고 **모두** 실어야. 재기 중엔 가정 없이 참인
        증거가 바닥이다(E-게이트)."""
        if not ev:
            return False
        if not gstk:
            return all(bear(sc, v, ln) for sc, ln, v in ev)
        return all(_sub(bear, sc, v, ln) for sc, ln, v in ev)

    # ── 운반 판정 ────────────────────────────────────────────────────
    def bearing(s, v, line):
        """값이 클라이언트를 싣나 — 후보 하나라도(1차 규칙과 같은 엄격)."""
        if v is None or v is _MISSING:
            return False
        if isinstance(v, tuple):
            if v[0] == "import":                    # from M import X
                m = mods.get(v[1])
                return bool(m) and holder_carrier(m.top, v[2])
            if v[0] == "call":                      # property 게터를 부른 값
                return id(v[1]) in factory_fns
            return v[1] in carrier.get(id(s), ())   # ("param"|"ent", …)
        if not gstk:
            return any(carries(s, c, line) for c in _cands(v))
        # 재기 중엔 후보를 다 잰다 — 가정에 기댄 후보에서 멈추면 그 뒤 바닥
        # 후보를 못 봐 E-게이트가 순서에 기댄다
        return any([_sub(carries, s, c, line) for c in _cands(v)])

    def holder_carrier(home, k):
        """함수 경계를 넘는 보관소는 **모든** 바인딩이 실어야 운반자다.
        `_D = None` 같은 지연 초기화가 하나라도 있으면 그 None 판정은 살아
        있는 검사다(아직 안 만들었다)."""
        return node(("H", id(home), k),
                    lambda: all_bear(ev_eff(home, k), bearing))

    def attr_carrier(cls, attr):
        """`self.attr` — 클래스 가족의 대입이 **모두** 실어야."""
        return node(("A", id(cls), attr),
                    lambda: all_bear(ev_attr(cls, attr), bearing))

    def holder_member(home, base, kind, name):
        return node(("M", id(home), base, kind, name),
                    lambda: all_bear(member_ev_at(home, base, kind, name),
                                     bearing))

    def is_carrier(s, k, line):
        """열쇠 k 가 이 줄에서 클라이언트를 가리키나."""
        return node(("C", id(s), k, line), lambda: _is_carrier(s, k, line))

    def _is_carrier(s, k, line):
        # 같은 블록 사슬에서 닿는 값을 알면 그것 하나(실수 #430)
        r = reaching(s, k, line)
        if r is not None:
            if r[0] == "def":
                return bearing(s, r[2], r[1])
            return r[0] == "param" and k in carrier.get(id(s), ())
        # 같은 스코프 줄 순서(1차 규칙) — 그 스코프가 직접 대입한 것
        first = [(ln, v) for ln, v in s.assigns.get(_store_key(k), ())
                 if ln <= line]
        if not gstk:
            return (any(bearing(s, v, ln) for ln, v in first)
                    or _carrier_rest(s, k, line))
        # 재기 중엔 두 길을 다 잰다 — 앞선 대입이 순환의 가정으로 참이 되면 거기서
        # 멈춰 보관소 쪽 바닥을 못 보고, 바닥 없는 순환으로 기각한다(E-게이트)
        a = any([_sub(bearing, s, v, ln) for ln, v in first])
        b = _sub(_carrier_rest, s, k, line)
        return a or b

    def _carrier_rest(s, k, line):
        acc = _split(k)
        if acc is None:
            home = holder(s, k)
            if home is s:
                return k in carrier.get(id(s), ())  # 운반 인자(엄격)
            return home is not None and holder_carrier(home, k)
        base, kind, name = acc
        if kind == "attr":
            # **마지막** 접근자에서 가른다 — `self.d` 는 메서드의 속성, `pkg.h.D`
            # 는 모듈 전역(첫 점에서 가르면 점이 둘인 모듈 경로를 놓쳤다, 리뷰
            # M4). `self.a.b` 는 `self.a` 에 담긴 것의 속성이다.
            meth = method_of(s, base)
            if meth is not None:
                return attr_carrier(meth.parent, name)
            mp = module_path(s, base)               # `import M` 뒤 `M.D`
            if mp and mp in mods:
                return holder_carrier(mods[mp].top, name)
        # 그 밖은 담는 쪽(base)에 따라 — `from M import CTX` 뒤 `CTX.d` 는
        # member_carrier 가 import 를 따라간다
        return member_carrier(s, base, kind, name, line)

    def member_carrier(s, base, kind, name, line, missing_ok=False):
        """`obj.attr` · `obj['k']` · `args[0]` — 담는 쪽(base)에 따라.
        `missing_ok` — `.get` 의 기본값이 클라이언트라 없는 칸도 실음으로 센다."""
        bear = bearing if not missing_ok else (
            lambda sc, v, ln: v is _MISSING or bearing(sc, v, ln))
        acc = _split(base)
        if acc is None:
            home = holder(s, base)
            if home is None:
                return False
            if home is not s:                       # 보관소 — 모든 바인딩
                if missing_ok:
                    return all_bear(member_ev_at(home, base, kind, name), bear)
                return holder_member(home, base, kind, name)
        elif acc[1] == "attr":
            meth = method_of(s, acc[0])
            if meth is not None:                    # self.ctx['d'] · self.h.d
                cls = meth.parent
                if missing_ok:
                    return all_bear(attr_member_ev(cls, acc[2], kind, name),
                                    bear)
                return node(("AM", id(cls), acc[2], kind, name),
                            lambda: all_bear(attr_member_ev(
                                cls, acc[2], kind, name), bearing))
            mp = module_path(s, acc[0])             # m.CTX['d'] · m.CTX.d
            if mp:
                if missing_ok:
                    return mp in mods and all_bear(member_ev_at(
                        mods[mp].top, acc[2], kind, name), bear)
                return mp in mods and holder_member(mods[mp].top, acc[2],
                                                    kind, name)
        # 지역 — 앞선 바인딩 하나라도(1차 규칙): 리터럴의 그 칸 · `C(...)` 의 속성
        # · 끝에 붙인 값(0 이상의 칸이면 그 칸일 수 있다)
        if acc is None and isinstance(name, int) and name >= 0 and any(
                ln <= line and v is not None and bearing(s, v, ln)
                for ln, v in s.tail.get(base, ())):
            return True
        for ln, v in s.assigns.get(base, ()):
            if ln > line or not isinstance(v, ast.AST):
                continue
            for c in _cands(v):
                e = _entry(c, kind, name)
                if e is not None:
                    if e[0] == "있음" and bearing(s, e[1], ln):
                        return True
                    if e[0] == "없음" and missing_ok:
                        return True                 # 없는 칸 — 기본값이 실린다
                    continue
                cls = instance_of(s, c) if kind == "attr" else None
                if cls is not None and attr_carrier(cls, name):
                    return True
        if acc is None:
            ent = _arg_entry(s, base, kind, name)   # 받은 *args·**kwargs 의 칸
            if ent is not None:
                return ent in carrier.get(id(s), ())
            if kind == "attr" and base in s.ann:    # def f(o: C) 의 o.d
                cls = class_scope(s.parent or s, s.ann[base])
                return cls is not None and attr_carrier(cls, name)
            # 주석 없는 인자 — 부르는 쪽이 넘긴 인스턴스의 클래스(갈래 하나라도)
            if kind == "attr" and (base in s.params or base in s.kwonly):
                return any(attr_carrier(c, name)
                           for c in pcls.get(id(s), {}).get(base, ()))
        return False

    def carries(s, e, line):
        if is_factory_call(s, e):
            return True
        k = _key(e)
        if not k:
            return False
        if is_carrier(s, k, line):
            return True
        d = _get_default(e)
        return d is not None and get_carrier(s, k, d, line)

    def get_carrier(s, k, dflt, line):
        """`x.get(k, 기본값)` — 그 칸이 없으면 기본값이 온다. 기본값이 클라이언트를
        실을 때만 '없는 칸' 바인딩을 실음으로 센다(실수 #430 — 옛 판은 기본값을
        버리고 없는 칸을 늘 None 으로 봐, `CTX.get('d', get_dart())` 뒤의 판정을
        놓쳤다)."""
        if not carries(s, dflt, line):
            return False
        base, kind, name = _split(k)
        return member_carrier(s, base, kind, name, line, missing_ok=True)

    # ── 언제나 클라이언트인가(긍정 판정의 else 갈래) ─────────────────────
    def surely_bearing(s, v, line):
        """값이 **언제나** 클라이언트인가 — 후보가 모두."""
        if isinstance(v, tuple) and v[0] == "import":
            m = mods.get(v[1])
            return bool(m) and node(("SH", id(m.top), v[2]), lambda: all_bear(
                ev_eff(m.top, v[2]), surely_bearing))
        if isinstance(v, tuple) and v[0] == "call":   # property 게터
            t = v[1]
            return id(t) in factory_fns and node(("SR", id(t)),
                                                 lambda: surely_returns(t))
        if not isinstance(v, ast.AST):
            return False                    # 모름 · 인자 · 없는 칸(_MISSING)
        return all(surely_expr(s, c, line) for c in _cands(v))

    def surely_expr(s, c, line):
        """식 하나가 **언제나** 클라이언트인가 — 공장 호출 · 그런 이름·칸·속성 ·
        `.get(k, 기본값)`(없는 칸이면 기본값이 언제나 클라이언트여야)."""
        if surely_call(s, c):
            return True
        k = _key(c)
        if k is None:
            return False
        d = _get_default(c)
        if d is not None:
            b, _kind, nm = _split(k)
            return surely_member(s, b, "get", nm, line, d)
        return surely(s, k, line)

    def dead_else(s, test, line):
        """긍정 판정의 else 가 죽었나 — `A or B` 는 한 항이라도, `A and B` 는
        모든 항이 언제나 클라이언트일 때(실수 #430 — 옛 판은 판정 대상이 이름
        하나일 때만 봤다)."""
        t = _unwrap_bool(test)
        if isinstance(t, ast.BoolOp):
            f = any if isinstance(t.op, ast.Or) else all
            return f(dead_else(s, v, line) for v in t.values)
        op = _pos_operand(t)
        return op is not None and surely_expr(s, op, line)

    def surely_call(s, e):
        """부르면 **언제나** 클라이언트가 오나. 공장은 `bot.dart_client` 의 것만
        (`real_factory` — 이름만 같은 사용자 함수·메서드·인자는 그 대상으로
        가른다, 리뷰 F2). 사용자 함수는 `returns_client`(갈래 하나라도 — 부정
        판정 쪽의 엄격)로 공장이 되므로 `get_dart() if k else None` 을 돌려주는
        함수도 공장이다 — else 판정은 그 함수의 **모든** return 이 언제나
        클라이언트이고 끝으로 흘러 None 이 되지 않을 때만 믿는다(마지막 문장이
        return·raise). 결과를 바꿀 수 있는 장식이 붙었으면 믿지 않는다."""
        if not isinstance(e, ast.Call):
            return False
        if isinstance(e.func, ast.Name) and not sole_binding(s, e.func.id,
                                                             e.func):
            return False
        if real_factory(s, e.func):
            return True
        t, _ = target(s, e.func, lazy=False)
        return (t is not None and id(t) in factory_fns
                and all(transparent_deco(t.parent, d)
                        for d in getattr(t.node, "decorator_list", ()))
                and node(("SR", id(t)), lambda: surely_returns(t)))

    def sole_binding(s, name, use=None):
        """else 판정이 그 이름을 한 대상으로 해석해도 되나(델타 리뷰 M1). 이름이
        사는 스코프의 바인딩이 둘 이상이면 — `try: from bot.dart_client import
        get_dart` / `except: def get_dart(): return None` 같은 폴백 · 뒤의
        재정의 · lambda · `global` 로 다시 묶는 함수 — 런타임에 어느 쪽이
        묶일지 모른다(흐름을 보지 않는다 — 앞의 정의를 import 가 덮는 경우도
        모른다고 본다, 놓치는 쪽). 같은 import 를 여러 갈래에 둔 것은 한
        바인딩이다."""
        h = holder(s, name)
        if h is None:
            return True
        if h.kind == "module" and module_binding(s, h, name, use) is not None:
            return True                     # 효력 있는 바인딩이 하나로 정해진다
        vals = [v for _sc, _ln, v in ev_name(h, name)]
        return len(vals) <= 1 or all(
            isinstance(v, tuple) and v[0] == "import" and v == vals[0]
            for v in vals)

    def transparent_deco(s, d):
        """결과를 바꾸지 않는 장식인가 — 이름이 아니라 **해석**으로(델타 리뷰
        L4). `staticmethod`·`classmethod` 는 어디서도 다시 묶지 않은 내장일
        때만 · `lru_cache`·`cache` 는 functools 에서 온 것일 때만(`from
        functools import …` · `functools.…`). 장식은 def 를 둔 스코프에서
        평가되므로 s 는 그 스코프다."""
        f = d.func if isinstance(d, ast.Call) else d
        if isinstance(f, ast.Name):
            return deco_source(s, f.id) in _TRANSPARENT_SRC
        if isinstance(f, ast.Attribute):
            b = _key(f.value)
            want = _TRANSPARENT_DECOS.get(f.attr)
            return (want is not None and b is not None
                    and module_path(s, b) == want)
        return False

    def deco_source(s, name, hops=0):
        """장식 이름이 가리키는 것 — ("builtins"|모듈, 원래 이름) 또는 None. 별칭도
        따라간다(`from functools import cache as memo` · `sm = staticmethod` —
        실수 #430, 옛 판은 이름이 표에 있어야만 투명으로 봤다)."""
        h = holder(s, name)
        vals = [v for _sc, _ln, v in ev_name(h, name)] if h else []
        if not vals:
            return ("builtins", name)       # 어디서도 다시 묶지 않은 내장
        if all(isinstance(v, tuple) and v[0] == "import" for v in vals):
            srcs = {(v[1], v[2]) for v in vals}
            return srcs.pop() if len(srcs) == 1 else None
        if (len(vals) == 1 and isinstance(vals[0], ast.Name) and hops < 4
                and vals[0].id != name):
            return deco_source(h, vals[0].id, hops + 1)
        return None

    def surely_returns(t):
        # 제너레이터는 여기 오지 않는다 — `returns_client` 가 공장에서 뺀다(여기
        # 두었던 같은 검사는 도달할 수 없었다, #291)
        if t.kind == "lambda":              # 본문 식이 곧 반환값
            return surely_bearing(t, t.node.body, t.node.lineno)
        if not isinstance(t.node.body[-1], (ast.Return, ast.Raise)):
            return False
        return all(surely_bearing(t, x.value, x.lineno) for x in t.nodes
                   if isinstance(x, ast.Return))

    def attr_stores(name):
        """읽은 모듈의 `<바탕>.name = …` 저장 [(스코프, 바탕)] 과 '이름을 모르는
        setattr 이 있나' — 색인은 한 번 만든다(모듈을 더 읽으면 비운다). 옛 판은
        부를 때마다 전 스코프의 대입을 훑어 레포 전수가 몇 배 느려졌다(실수 #430)."""
        if not attr_idx:
            attr_idx[None] = any(sc.dyn_setattr for sc in scopes)
            for sc in scopes:
                for k in sc.assigns:
                    acc = _split(k)
                    if acc is not None and acc[1] == "attr":
                        attr_idx.setdefault(acc[2], []).append((sc, acc[0]))
        return attr_idx[None], attr_idx.get(name, ())

    def attr_written_elsewhere(name, own):
        """`<무엇>.name = …` 을 증거 밖에서 쓰는 스코프가 있나 — 있으면 '언제나'
        를 믿지 않는다(리뷰 F2: 클래스 밖 `c.d = None` · `setattr(self, 'd',
        None)` · 이름을 모르는 `setattr`). `own(sc, base)` 는 그 저장이 이미 증거에
        들었나(가족 메서드의 self 등) · 다른 객체라고 확실한가."""
        dyn, stores = attr_stores(name)
        return dyn or any(not own(sc, b) for sc, b in stores)

    def surely(s, k, line):
        return node(("S", id(s), k, line), lambda: _surely(s, k, line))

    def _surely(s, k, line):
        """엄격한 is_carrier(갈래 하나라도)의 반대 쪽 — 바인딩·갈래가 **모두**
        실어야 하고, 지역 이름은 줄 순서와 무관하게 그 스코프의 **모든**
        바인딩을 본다(반복문의 뒷줄이 다음 바퀴에 닿는다). 인자는 아니다."""
        acc = _split(k)
        if acc is None:
            home = holder(s, k)
            if home is None:
                return False
            r = reaching(s, k, line)
            if r is not None:               # 그 줄에 닿는 값이 하나로 정해진다
                if r[0] == "param":
                    return surely_param(s, k)
                return r[0] == "def" and surely_bearing(s, r[2], r[1])
            if home is s and (k in s.params or k in s.kwonly
                              or k in (s.vararg, s.kwarg)):
                return not s.assigns.get(k) and surely_param(s, k)
            # 그 이름을 쓰는 **모든** 스코프 — nonlocal·global 로 바꾸는 안쪽
            # 함수까지(리뷰 F2: `reset()` 이 `nonlocal d; d = None` 하면 else 가
            # 산다). 지역이어도 같다(`ev_name` 은 그 쓰는 쪽을 함께 준다).
            return node(("SH", id(home), k), lambda: (
                not (home.kind == "module" and module_written(home, k))
                and all_bear(ev_eff(home, k), surely_bearing)))
        base, kind, name = acc
        if kind == "attr":
            meth = method_of(s, base)
            if meth is not None:
                return surely_self_attr(meth.parent, name)
            mp = module_path(s, base)
            if mp and mp in mods:
                top = mods[mp].top
                return node(("SH", id(top), name), lambda: (
                    not module_written(top, name)
                    and all_bear(ev_name(top, name), surely_bearing)))
        return surely_member(s, base, kind, name, line)

    def surely_member(s, base, kind, name, line, dflt=None):
        """지역 컨테이너의 칸 · 지역 인스턴스의 속성 · 닫힌 세계 인자의 속성이
        **언제나** 클라이언트인가(실수 #430 — 옛 판은 칸·객체 속성의 else 를
        보지 않았다). 지역 이름이 함수 밖으로 새어 나가면(다른 함수에 넘김·
        돌려줌·담음·별칭) 남이 바꿀 수 있어 믿지 않는다. 보관소(모듈 전역·
        `self.속성`)의 칸은 보지 않는다 — 읽지 않은 모듈이 바꿀 수 있다. `dflt` 는
        `.get` 의 기본값(없는 칸이면 그것이 온다)."""
        if _split(base) is not None:
            return False
        home = holder(s, base)
        if home is None or home.kind not in ("def", "lambda"):
            return False
        if base in home.params or base in home.kwonly:
            return (kind == "attr" and not home.assigns.get(base)
                    and surely_param_attr(home, base, name))
        if base in (home.vararg, home.kwarg) or escapes(home, base, kind):
            return False
        ev = member_ev_at(home, base, kind, name)
        if not ev:
            return False
        if kind == "attr" and not all(
                attr_closed(c, name) for c in attr_classes(home, base) or [None]):
            return False

        def sb(sc, v, ln):
            if v is _MISSING:
                return dflt is not None and surely_expr(s, dflt, line)
            return surely_bearing(sc, v, ln)
        if dflt is not None:
            return all_bear(ev, sb)
        return node(("SM", id(home), base, kind, name),
                    lambda: all_bear(ev, sb))

    def attr_classes(home, base):
        """지역 이름의 **모든** 바인딩이 `C(...)` 면 그 클래스들 · 아니면 None
        (`attr_closed(None)` 은 거짓이 아니라 아래에서 걸러야 하므로 호출부가 그
        경우를 `[None]` 으로 바꿔 거짓을 낸다)."""
        out = []
        for _ln, v in home.assigns.get(base, ()):
            if not isinstance(v, ast.AST):
                return None
            for c in _cands(v):
                k = instance_of(home, c)
                if k is None:
                    return None
                out.append(k)
        return out or None

    def surely_self_attr(cls, name):
        """그 클래스 인스턴스의 속성이 **언제나** 클라이언트인가 — 가족 밖에서
        쓰지 않고, 가족의 바인딩(게터 포함)이 모두 언제나 클라이언트."""
        return node(("SA", id(cls), name), lambda: (
            attr_closed(cls, name)
            and all_bear(ev_attr(cls, name), surely_bearing)))

    def attr_closed(cls, name):
        """그 클래스 인스턴스의 속성을 가족 밖에서 쓰는 자리가 없나(이름 모르는
        setattr 포함) — `self.d` else 판정과 같은 조건. 클래스를 모르면 거짓."""
        return cls is not None and not attr_written_elsewhere(
            name, lambda sc, b: family_self(cls, sc, b))

    def escapes(home, base, kind):
        """지역 이름이 새어 나가나 — 그 이름을 쓰는 스코프(안쪽 함수 포함)에서
        칸 읽기·쓰기(`base[...]`) · 바꾸지 않는/추적하는 메서드(`base.get` ·
        `base.update`) · 속성 접근(객체일 때) 밖의 쓰임이 하나라도 있으면 참."""
        ck = (id(home), base, kind)
        if ck in esc_cache:
            return esc_cache[ck]
        out = False
        for sc in scopes:
            if sc.mod is not home.mod or (sc is not home
                                          and holder(sc, base) is not home):
                continue
            safe = set()
            for x in sc.nodes:
                v = getattr(x, "value", None)
                if not (isinstance(v, ast.Name) and v.id == base):
                    continue
                if isinstance(x, ast.Subscript) and kind != "attr":
                    safe.add(id(v))
                elif isinstance(x, ast.Attribute) and (
                        kind == "attr" or x.attr in _READERS | _MUTATORS):
                    safe.add(id(v))
            if any(isinstance(x, ast.Name) and x.id == base
                   and isinstance(x.ctx, ast.Load) and id(x) not in safe
                   for x in sc.nodes):
                out = True
                break
        esc_cache[ck] = out
        return out

    def closed_calls(t):
        """t 가 **닫힌 세계**의 함수인가 — 다른 함수 안에서 정의했고, 장식이 없고,
        그 이름을 바깥에서 곧바로 부르는 데에만 쓴다(넘기거나 돌려주거나 담지
        않는다 · 다시 묶지 않는다). 그러면 t 를 부르는 곳은 그 호출들이 전부다 —
        [(스코프, 호출)] · 아니면 None. 모듈 맨 위·클래스의 함수는 읽지 않은
        모듈이 부를 수 있어 아니다(실수 #430)."""
        if id(t) in closed_cache:
            return closed_cache[id(t)]
        out = None
        par = t.parent
        if (t.kind == "def" and par is not None
                and par.kind in ("def", "lambda") and not t.node.decorator_list
                and len(par.defs.get(t.node.name, ())) == 1
                and len(par.assigns.get(t.node.name, ())) == 1):
            calls, ok = [], True
            name = t.node.name
            for sc in scopes:
                if sc.mod is not t.mod or (sc is not par
                                           and holder(sc, name) is not par):
                    continue
                funcs = {id(x.func) for x in sc.nodes if isinstance(x, ast.Call)}
                for x in sc.nodes:
                    if isinstance(x, ast.Name) and x.id == name and \
                            isinstance(x.ctx, ast.Load):
                        if id(x) not in funcs:
                            ok = False
                            break
                    if (isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                            and x.func.id == name):
                        calls.append((sc, x))
                if not ok:
                    break
            out = calls if ok and calls else None
        closed_cache[id(t)] = out
        return out

    def call_arg(t, k, call):
        """그 호출이 인자 k 에 넘긴 식 — 넘기지 않았으면 기본값 · 별표가 끼면
        None(어느 칸인지 모른다)."""
        if any(isinstance(a, ast.Starred) for a in call.args) or any(
                kw.arg is None for kw in call.keywords):
            return None
        if k in t.params:
            i = t.params.index(k)
            if i < len(call.args):
                return call.args[i]
        for kw in call.keywords:
            if kw.arg == k:
                return kw.value
        a = t.node.args
        if k in t.params:
            j = t.params.index(k) - (len(t.params) - len(a.defaults))
            return a.defaults[j] if j >= 0 else None
        j = t.kwonly.index(k)
        return a.kw_defaults[j]

    def surely_param(t, k):
        """닫힌 세계 함수의 인자 — 모든 호출이 언제나 클라이언트를 넘기면(넘기지
        않은 호출은 기본값이 그래야) 그 인자는 언제나 클라이언트다."""
        if k not in t.params and k not in t.kwonly:
            return False
        return node(("SP", id(t), k), lambda: _surely_param(t, k))

    def _surely_param(t, k):
        calls = closed_calls(t)
        if not calls:
            return False
        for sc, call in calls:
            e = call_arg(t, k, call)
            if e is None:
                return False
            passed = any(e is a for a in call.args) or any(
                kw.value is e for kw in call.keywords)
            if not (surely_expr(sc, e, call.lineno) if passed else
                    surely_expr(t.parent, e, t.node.lineno)):
                return False
        return True

    def surely_param_attr(t, k, name):
        """닫힌 세계 함수 인자의 속성 — 모든 호출이 `C(...)` 인스턴스를 넘기고
        그 속성이 언제나 클라이언트일 때."""
        calls = closed_calls(t)
        if not calls:
            return False
        for sc, call in calls:
            e = call_arg(t, k, call)
            c = instance_of(sc, e) if isinstance(e, ast.Call) else None
            if c is None or not surely_self_attr(c, name):
                return False
        return True

    def family_self(cls, sc, base):
        """그 저장이 이미 `ev_attr` 증거에 든 것(가족 메서드의 self·cls) ·
        가족 밖 클래스의 인스턴스라고 확실한 것."""
        fam = family(cls)
        if (sc.kind == "def" and sc.in_class and sc.params and not sc.static
                and base == sc.params[0]):
            if sc.parent in fam:
                return True
            # 가족 밖 클래스의 **자기** 메서드 self — 다른 객체다(실수 #430 — 옛
            # 판은 막는 쪽으로 봐 놓쳤다). 단 그 클래스의 메서드를 클래스로 불러
            # 남의 객체를 self 로 넘길 수 있으면(`Other.reset(c)`) 그 self 는
            # 무엇이든 될 수 있다.
            return not explicit_self(sc.parent)
        k = instance_class(sc, base, 10 ** 9)
        return k is not None and k not in fam

    def explicit_self(c):
        """클래스 c 의 (정적이 아닌) 메서드를 **클래스로** 참조하는 자리가 읽은
        모듈에 있나 — `C.m(obj)` 로 부르거나 `C.m` 을 꺼내 두면 self 는 무엇이든
        될 수 있다."""
        if id(c) not in xself_cache:
            hit = False
            for sc in scopes:
                for x in sc.nodes:
                    if (isinstance(x, ast.Attribute)
                            and isinstance(x.ctx, ast.Load)
                            and class_scope(sc, x.value) is c):
                        m = find_method(c, x.attr)
                        if (m is not None and m.kind == "def"
                                and not m.static and not m.classm):
                            hit = True
                            break
                if hit:
                    break
            xself_cache[id(c)] = hit
        return xself_cache[id(c)]

    def module_written(top, name):
        """다른 스코프가 `모듈.name = …` 으로 그 전역을 바꾸나(리뷰 F2)."""
        return attr_written_elsewhere(
            name, lambda sc, b: module_path(sc, b) != top.mod.name)

    # ── 고정점 ───────────────────────────────────────────────────────
    def calls(s, x):
        """(호출 대상, 위치 인자, 키워드) — 미뤄 부르기도 그 함수 호출로 본다.
        이름이 흔한 것(`register`·`callback`·`enter`…)은 받는 쪽을 가린다."""
        yield x.func, x.args, x.keywords
        nm = _call_name(x)
        spec, chk = _DEFER.get(nm), None
        if spec is None and nm in _DEFER_CHECKED:
            chk, *spec = _DEFER_CHECKED[nm]
        if spec and defer_ok(s, x, chk):
            fi, ai, kw_too = spec
            head = x.args[:ai]
            if len(x.args) > fi and not any(isinstance(a, ast.Starred)
                                            for a in head):
                yield x.args[fi], x.args[ai:], (x.keywords if kw_too else [])
        if nm in _DEFER_KW:
            chk, (fkw, fpos), (akw, apos), (kkw, kpos) = _DEFER_KW[nm]
            if not defer_ok(s, x, chk):
                return
            kw = {k.arg: k.value for k in x.keywords if k.arg}

            def at(name, i):
                if name in kw:
                    return kw[name]
                if len(x.args) > i and not any(isinstance(a, ast.Starred)
                                               for a in x.args[:i + 1]):
                    return x.args[i]
                return None
            fn = at(fkw, fpos)
            a = literal_of(s, at(akw, apos), x.lineno)
            k2 = literal_of(s, at(kkw, kpos), x.lineno)
            if fn is not None:
                pos = list(a.elts) if isinstance(a, (ast.Tuple, ast.List)) else []
                more = [ast.keyword(arg=None, value=k2)] if k2 is not None else []
                if pos or more:
                    yield fn, pos, more

    def defer_ok(s, x, chk):
        """미뤄 부르기의 받는 쪽 판정 — 없으면(표에 이름만) 그대로 · ("qual",
        이름들) 이면 부른 것이 그 함수 · ("recv", 생성자들) 이면 그 생성자로 만든
        객체의 메서드."""
        if chk is None:
            return True
        kind, names = chk
        if kind == "qual":
            return qualname(s, x.func) in names
        recv = x.func.value if isinstance(x.func, ast.Attribute) else None
        return recv is not None and made_by(s, recv, names)

    def qualname(s, func):
        sym = symbol(s, func)
        return f"{sym[1]}.{sym[2]}" if sym and sym[0] == "qual" else None

    def made_by(s, recv, names):
        """recv 가 그 생성자들로 만든 객체인가 — `X()` 자체 · 이름의 대입 ·
        `with X() as name`."""
        if isinstance(recv, ast.Call):
            return qualname(s, recv.func) in names
        if not isinstance(recv, ast.Name):
            return False
        h = holder(s, recv.id)
        if h is None:
            return False
        if any(isinstance(v, ast.Call) and qualname(h, v.func) in names
               for _ln, v in h.assigns.get(recv.id, ())):
            return True
        return any(isinstance(w, ast.withitem)
                   and isinstance(w.optional_vars, ast.Name)
                   and w.optional_vars.id == recv.id
                   and isinstance(w.context_expr, ast.Call)
                   and qualname(h, w.context_expr.func) in names
                   for w in h.nodes)

    def literal_of(s, e, line):
        """이름이면 같은 블록에서 그 줄에 닿는 리터럴(튜플·리스트·dict·`dict(…)`)
        로 — `args=a` · `*a` · `**kw` 로 넘긴 변수(실수 #430). 아니면 그대로."""
        if isinstance(e, ast.Name):
            r = reaching(s, e.id, line)
            if (r is not None and r[0] == "def"
                    and (isinstance(r[2], (ast.Tuple, ast.List, ast.Dict))
                         or isinstance(r[2], ast.Call)
                         and _call_name(r[2]) == "dict")):
                return r[2]
        return e

    def fn_name(s):
        """공장 함수의 이름 — 다른 모듈에서 그 이름을 찾아 읽는다. lambda 는 담은
        이름(없으면 None — 이름으로 찾을 수 없다)."""
        if s.kind != "lambda":
            return s.node.name
        return next((k for k, evs in sorted(s.parent.assigns.items())
                     if k.isidentifier() and any(v is s.node for _ln, v in evs)),
                    None)

    def carried(s, line, args, kws):
        """실어 보내는 자리 — (위치 [자리], 키워드 {이름}). 별표: 리터럴
        (`*[…]`·`**{…}`·`**dict(…)`)은 펼치고, 받은 `*args`·`**kwargs` 를 그대로
        넘기면 그 칸을 옮긴다 — 그 밖의 별표 뒤 위치는 어디에 닿는지 모른다."""
        pos, kwd = [], set()
        i, exact = 0, True
        for a in args:
            if isinstance(a, ast.Starred):
                v = literal_of(s, a.value, line)
                if (isinstance(v, (ast.List, ast.Tuple))
                        and not any(isinstance(e, ast.Starred) for e in v.elts)):
                    for e in v.elts:
                        if exact and carries(s, e, line):
                            pos.append(i)
                        i += 1
                    continue
                if (exact and isinstance(v, ast.Name) and v.id == s.vararg
                        and holder(s, v.id) is s and not s.assigns.get(v.id)):
                    pos += [i + e[2] for e in carrier.get(id(s), ())
                            if isinstance(e, tuple) and e[:2] == ("*", v.id)]
                # 받은 `*args` 를 잘라 넘김(`*args[1:]`·`[1:3]`·`[::2]`) — 남는
                # 칸만 앞으로 당겨진다
                sl = vararg_slice(s, v) if exact else None
                if sl is not None:
                    lo, hi, st = sl
                    pos += [i + (e[2] - lo) // st for e in carrier.get(id(s), ())
                            if isinstance(e, tuple) and e[:2] == ("*", s.vararg)
                            and e[2] >= lo and (hi is None or e[2] < hi)
                            and (e[2] - lo) % st == 0]
                exact = False
                continue
            if exact and carries(s, a, line):
                pos.append(i)
            i += 1
        for k in kws:
            if k.arg is not None:
                if carries(s, k.value, line):
                    kwd.add(k.arg)
                continue
            v = literal_of(s, k.value, line)
            if isinstance(v, ast.Dict):
                # 마지막 펼침(·상수 아닌 열쇠) 뒤의 칸만 — 그 앞은 덮였을지
                # 모른다(`_entry`)
                cut = max((j + 1 for j, kk in enumerate(v.keys)
                           if not (isinstance(kk, ast.Constant)
                                   and isinstance(kk.value, str))),
                          default=0)
                last = {kk.value: vv for kk, vv in zip(v.keys[cut:],
                                                       v.values[cut:])
                        if isinstance(kk, ast.Constant)
                        and isinstance(kk.value, str)}
                kwd |= {nm for nm, vv in last.items() if carries(s, vv, line)}
            elif (isinstance(v, ast.Call) and _call_name(v) == "dict"
                  and not v.args):
                kwd |= {kw.arg for kw in v.keywords
                        if kw.arg and carries(s, kw.value, line)}
            elif (isinstance(v, ast.Name) and v.id == s.kwarg
                  and holder(s, v.id) is s and not s.assigns.get(v.id)):
                kwd |= {e[2] for e in carrier.get(id(s), ())
                        if isinstance(e, tuple) and e[:2] == ("**", v.id)}
        return pos, kwd

    def vararg_slice(s, v):
        """`args[lo:hi:step]`(받은 `*args` 를 다시 묶지 않았을 때) → (lo, hi,
        step) — hi 는 끝이 없으면 None. 음수 시작·걸음은 길이를 몰라 자리를
        모른다(음수 끝은 남는 칸이 없게 계산된다 — 못 보는 쪽으로만 틀린다 · 실수
        #430 — 옛 판은 끝·걸음이 있으면 통째로 놓쳤다)."""
        if not (isinstance(v, ast.Subscript) and isinstance(v.value, ast.Name)
                and v.value.id == s.vararg and holder(s, s.vararg) is s
                and not s.assigns.get(s.vararg) and isinstance(v.slice, ast.Slice)):
            return None
        sl = v.slice
        lo = 0 if sl.lower is None else _const_index(sl.lower)
        hi = None if sl.upper is None else _const_index(sl.upper)
        st = 1 if sl.step is None else _const_index(sl.step)
        if not (isinstance(lo, int) and lo >= 0 and isinstance(st, int)
                and st >= 1 and (hi is None or isinstance(hi, int))):
            return None
        return lo, hi, st

    def arg_classes(s, line, args, kws):
        """넘긴 인스턴스의 클래스 — {자리 또는 키워드: 클래스} (별표 앞 위치만).
        `C(...)` · 그렇게 묶은 이름 · 주석 있는 인자."""
        out = {}
        for i, a in enumerate(args):
            if isinstance(a, ast.Starred):
                break
            c = value_class(s, a, line)
            if c is not None:
                out[i] = c
        for k in kws:
            if k.arg is not None:
                c = value_class(s, k.value, line)
                if c is not None:
                    out[k.arg] = c
        return out

    def value_class(s, e, line):
        if isinstance(e, ast.Call):
            return instance_of(s, e)
        if isinstance(e, ast.Name):
            return instance_class(s, e.id, line)
        return None

    def add_param_classes(tgt, skip, typed):
        """받는 쪽 인자에 그 클래스를 더한다 — 바뀌었으면 참."""
        names = list(tgt.params)[1 if skip else 0:]
        got = pcls.setdefault(id(tgt), {})
        changed = False
        for k, c in typed.items():
            p = (names[k] if isinstance(k, int) and k < len(names) else
                 k if isinstance(k, str) and (k in names or k in tgt.kwonly)
                 else None)
            if p is not None and c not in got.setdefault(p, []):
                got[p].append(c)
                changed = True
        return changed

    def place(tgt, skip, pos, kwd):
        """실어 보낸 자리 → 받는 쪽 인자(·`*args`·`**kwargs` 의 칸)."""
        names = list(tgt.params)[1 if skip else 0:]
        got = set()
        for i in pos:
            if i < len(names):
                got.add(names[i])
            elif tgt.vararg:
                got.add(("*", tgt.vararg, i - len(names)))
        for k in kwd:
            if k in names or k in tgt.kwonly:
                got.add(k)
            elif tgt.kwarg:
                got.add(("**", tgt.kwarg, k))
        return got

    def returns_client(s):
        """스스로 받은 클라이언트를 돌려주나 — 인자를 그대로 돌려주는 것은
        아니다(`pick(None)` 이 운반자가 되면 안 된다 · `*args`·`**kwargs` 의
        칸도 같다). 제너레이터는 부르면 제너레이터가 온다(클라이언트가 아니다)."""
        if _is_generator(s):
            return False
        own = set(s.params) | set(s.kwonly)
        bundles = {s.vararg, s.kwarg} - {None}
        rets = ([(s.node.body, s.node.lineno)] if s.kind == "lambda" else
                [(x.value, x.lineno) for x in s.nodes
                 if isinstance(x, ast.Return) and x.value is not None])
        for v, line in rets:
            for c in _cands(v):
                k = _key(c)
                if is_factory_call(s, c) or (
                        k and k not in own and _root(k) not in bundles
                        and is_carrier(s, k, line)):
                    return True
        return False

    def fixpoint():
        changed = True
        while changed:
            changed = False
            for s in list(scopes):
                if (s.kind in ("def", "lambda") and id(s) not in factory_fns
                        and returns_client(s)):
                    factory_fns[id(s)] = fn_name(s)
                    memo.clear()
                    changed = True
                for x in s.nodes:
                    if not isinstance(x, ast.Call):
                        continue
                    for func, args, kws in calls(s, x):
                        pos, kwd = carried(s, x.lineno, args, kws)
                        typed = arg_classes(s, x.lineno, args, kws)
                        if not (pos or kwd or typed):
                            continue
                        tgt, skip = target(s, func, lazy=True)
                        if tgt is None:
                            continue
                        new = (place(tgt, skip, pos, kwd)
                               - carrier.setdefault(id(tgt), set()))
                        if new:
                            carrier[id(tgt)] |= new
                            memo.clear()
                            changed = True
                        if typed and add_param_classes(tgt, skip, typed):
                            memo.clear()
                            changed = True

    def class_carries(cls):
        """인스턴스 속성에 클라이언트를 담는 클래스인가(다른 모듈의 `C().d` ·
        `def f(o: C)` 를 찾아 읽게)."""
        attrs = set(cls.assigns)
        for m in cls.children.values():
            if m.kind == "def" and m.params:
                p = m.params[0] + "."
                attrs |= {k[len(p):] for k in m.assigns
                          if k.startswith(p) and _split(k[len(p):]) is None}
        return any(attr_carrier(cls, a) for a in attrs)

    def exported():
        """다른 모듈이 이름으로 받아 갈 수 있는 운반자 — 공장을 감싼 def ·
        클라이언트를 담은 모듈 전역(리터럴의 칸·나중에 쓴 칸에 담은 것도) ·
        그런 클래스."""
        out = {n for n in factory_fns.values() if n}
        for m in list(mods.values()):
            # 정렬해서 훑는다 — 집합 순회는 해시 시드마다 달라 판정 순서(어느
            # 순환에 먼저 닿나)가 프로세스마다 갈렸다. 맞는 판정은 순서와
            # 무관하지만, 순서에 기대는 메모 버그는 그러면 실행마다 나타났다
            # 사라진다(2026-10-04 F5 뮤테이션이 실측 — 같은 그래프가 한
            # 프로세스에선 틀리고 다른 프로세스에선 맞았다)
            for k in sorted(set(m.top.assigns) | set(m.writers)):
                if k.startswith("@"):
                    # `globals()['D'] = …` — 그 모듈의 전역 D
                    acc = _split(k)
                    if (acc and acc[1] == "attr"
                            and module_path(m.top, acc[0]) == m.name
                            and holder_carrier(m.top, acc[2])):
                        out.add(acc[2])
                    continue
                acc = _split(k)
                if acc is None:
                    slots = {e for _ln, v in m.top.assigns.get(k, ())
                             for c in (_cands(v) if isinstance(v, ast.AST)
                                       else ()) for e in _slots(c)}
                    if holder_carrier(m.top, k) or any(
                            holder_member(m.top, k, kd, nm)
                            for kd, nm in sorted(slots, key=repr)):
                        out.add(k)
                elif _split(acc[0]) is None and holder_member(
                        m.top, acc[0], acc[1], acc[2]):
                    out.add(acc[0])
            for nm, c in m.top.children.items():
                if c.kind == "class" and class_carries(c):
                    out.add(nm)
        return out

    def prefetch_bases():
        """읽은 모듈의 클래스가 상속하는 클래스의 모듈도 읽는다(상속 축)."""
        for sc in list(scopes):
            if sc.kind == "class":
                for b in sc.bases:
                    class_scope(sc.parent, b, lazy=True)

    mention(_FACTORIES)
    seen: set = set()
    while True:
        n_mods = len(mods)
        fixpoint()
        prefetch_bases()
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
            bindings += sum(1 for ln, v in evs if bearing(s, v, ln))
        odd = _odd_negated(s.nodes)
        tests = {id(_unwrap_bool(y.test)) for y in s.nodes
                 if isinstance(y, (ast.If, ast.IfExp, ast.While, ast.Assert))}
        for x in s.nodes:
            if (isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
                    and holder(s, x.id) not in (s, None)
                    and is_carrier(s, x.id, x.lineno)):
                cross_reads += 1
            # 부정 판정 — 받지 않고 바로 부정하는 철자(`not get_dart()` ·
            # `mk() is None`)는 이름을 거치지 않으니 열쇠가 없다(리뷰 M1).
            # 부정 아래 홀수 번 놓인 판정은 긍정이다(`not not d` · `not d is
            # None` — 바깥 판정이 드모르간으로 가른다, 리뷰 F2).
            for op in (() if id(x) in odd else _neg_operands(x)):
                k = _key(op)
                dflt = _get_default(op)
                if (isinstance(op, ast.Call) and is_factory_call(s, op)) or (
                        k and (is_carrier(s, k, x.lineno) or (
                            dflt is not None
                            and get_carrier(s, k, dflt, x.lineno)))
                        and not any(bearing(s, v, ln)
                                    for ln, v in s.resolves.get(id(x), ()))):
                    hits.append((s.mod.name, x.lineno, ast.unparse(x)))
                    break
            # 긍정 판정의 else 갈래 — 값이 **언제나** 클라이언트면 그 갈래는
            # 죽었다(키가 없을 때를 다루려던 갈래가 한 번도 안 돈다, #427).
            # 기본값만 두는 갈래(`else None`)는 해가 없어 넘긴다.
            if isinstance(x, (ast.If, ast.IfExp)) and x.orelse and not (
                    _trivial_branch(x.orelse) if isinstance(x, ast.If)
                    else _trivial(x.orelse)):
                if dead_else(s, x.test, x.lineno):
                    hits.append((s.mod.name, x.lineno,
                                 f"{ast.unparse(x.test)} → else"))
            # `d or 기본값` — 앞 항이 언제나 클라이언트면 뒤 항은 한 번도 안
            # 돈다(판정 자리의 or 는 위 else 가 본다 · 실수 #430)
            if (isinstance(x, ast.BoolOp) and isinstance(x.op, ast.Or)
                    and id(x) not in tests):
                for i, v in enumerate(x.values[:-1]):
                    if dead_else(s, v, x.lineno):
                        if not all(_trivial(w) for w in x.values[i + 1:]):
                            hits.append((s.mod.name, x.lineno,
                                         f"{ast.unparse(x)} → else"))
                        break
    return {"hits": sorted(hits), "bindings": bindings,
            "carrier_params": sum(map(len, carrier.values())),
            "cross_reads": cross_reads, "factory_fns": len(factory_fns),
            "modules": len(mods), "built": sorted(mods), "evals": evals[0],
            "depth": peak[0]}


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
        # 맨 위에 lambda 로 묶은 공장(실수 #430)
        ({"pkg.w": "mk = lambda: get_dart()\n",
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

    # ── #428 이 '못 보는 축' 으로 남긴 모양(2026-10-04) — 갈래마다 픽스처 ──
    @pytest.mark.parametrize("src", [
        # 다른 철자 — 드모르간(각 항이 부정된다) · `bool()` · `is False` 류
        "def f(x):\n    d = get_dart()\n    if not (d and x):\n        return\n",
        "def f(x):\n    d = get_dart()\n    if not (x or d):\n        return\n",
        "def f(x, y):\n    d = get_dart()\n    if not (x and (y or d)):\n"
        "        return\n",
        "def f():\n    d = get_dart()\n    if not bool(d):\n        return\n",
        "def f():\n    d = get_dart()\n    if bool(d) is False:\n        return\n",
        "def f():\n    d = get_dart()\n    if bool(d) == False:\n        return\n",
        "def f():\n    d = get_dart()\n    if bool(d) is not True:\n"
        "        return\n",
        "def f():\n    d = get_dart()\n    if bool(d) != True:\n        return\n",
        "def f():\n    d = get_dart()\n    if d is False:\n        return\n",
        # 긍정 판정의 else 갈래 — 언제나 클라이언트면 그 갈래는 한 번도 안 돈다
        "def f():\n    d = get_dart()\n    if d:\n        d.x()\n    else:\n"
        "        log('키 없음')\n",
        "def f():\n    d = get_dart()\n    m = d.x() if d else '키 없음'\n",
        "def f():\n    d = get_dart()\n    if d is not None:\n        d.x()\n"
        "    else:\n        raise RuntimeError('키 없음')\n",
        "def f(k):\n    d = get_dart()\n    if d:\n        d.x()\n    elif k:\n"
        "        log('키 없음')\n",
        "def f():\n    if get_dart():\n        pass\n    else:\n"
        "        log('키 없음')\n",
        "D = get_dart()\ndef f():\n    if D:\n        D.x()\n    else:\n"
        "        warn()\n",
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "    def f(self):\n        return self.d.x() if self.d else self.why()\n",
        # 컨테이너 — dict·리스트·`dict()`·`SimpleNamespace()`·`getattr`·`.get`
        "def f():\n    ctx = {'dart': get_dart()}\n    if not ctx['dart']:\n"
        "        return\n",
        "def f():\n    ctx = {}\n    ctx['dart'] = get_dart()\n"
        "    if ctx['dart'] is None:\n        return\n",
        "def f():\n    ctx = {'dart': get_dart()}\n"
        "    if ctx.get('dart') is None:\n        return\n",
        "def f():\n    xs = [get_dart()]\n    if not xs[0]:\n        return\n",
        "def f():\n    ctx = dict(dart=get_dart())\n    if not ctx['dart']:\n"
        "        return\n",
        "def f():\n    ns = SimpleNamespace(dart=get_dart())\n    if not ns.dart:\n"
        "        return\n",
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "    def f(self):\n        if getattr(self, 'd') is None:\n"
        "            return\n",
        "CTX = {'d': get_dart()}\ndef f():\n    if not CTX['d']:\n        return\n",
        "CTX = SimpleNamespace()\nCTX.d = get_dart()\ndef f():\n"
        "    if not CTX.d:\n        return\n",
        "class C:\n    def __init__(self):\n        self.ctx = {'d': get_dart()}\n"
        "    def f(self):\n        if not self.ctx['d']:\n            return\n",
        "class H:\n    def __init__(self):\n        self.d = get_dart()\n"
        "class C:\n    def __init__(self):\n        self.h = H()\n"
        "    def f(self):\n        if not self.h.d:\n            return\n",
        # 미뤄 부르기 — partial · to_thread · 실행기·이벤트 루프 · Thread kwargs ·
        # Timer(위치로 받는 인자)
        _USE + "def f():\n    functools.partial(use, get_dart())()\n",
        _USE + "def f():\n    partial(use, dart=get_dart())()\n",
        _USE + "async def f():\n    await asyncio.to_thread(use, get_dart())\n",
        _USE + "def f(loop):\n    loop.run_in_executor(None, use, get_dart())\n",
        _USE + "def f(loop):\n    loop.call_soon(use, get_dart())\n",
        _USE + "def f():\n    threading.Thread(target=use, "
        "kwargs={'dart': get_dart()}).start()\n",
        _USE + "def f():\n    threading.Timer(1.0, use, [get_dart()]).start()\n",
        # `*args`·`**kwargs` — 넘기기 · 리터럴 펼치기 · 받은 쪽의 칸
        _USE + "def wrap(*a):\n    return use(*a)\ndef f():\n"
        "    wrap(get_dart())\n",
        _USE + "def wrap(**kw):\n    return use(**kw)\ndef f():\n"
        "    wrap(dart=get_dart())\n",
        _USE + "def f():\n    use(*[get_dart()])\n",
        _USE + "def f():\n    use(**{'dart': get_dart()})\n",
        _USE + "def f():\n    use(**dict(dart=get_dart()))\n",
        "def use(*a):\n    if not a[0]:\n        return\ndef f():\n"
        "    use(get_dart())\n",
        "def use(**kw):\n    if kw.get('dart') is None:\n        return\n"
        "def f():\n    use(dart=get_dart())\n",
        # 클래스 — 클래스로 부른 메서드 · classmethod · 객체 메서드 · 상속
        "class C:\n    def use(self, dart):\n        if not dart:\n"
        "            return\ndef f(o):\n    C.use(o, get_dart())\n",
        "class C:\n    @classmethod\n    def use(cls, dart):\n        if not dart:\n"
        "            return\ndef f():\n    C.use(get_dart())\n",
        "class C:\n    def use(self, dart):\n        if not dart:\n"
        "            return\ndef f():\n    c = C()\n    c.use(get_dart())\n",
        "class H:\n    def use(self, dart):\n        if not dart:\n"
        "            return\nclass C:\n    def __init__(self):\n"
        "        self.h = H()\n    def f(self):\n        self.h.use(get_dart())\n",
        "class B:\n    def __init__(self):\n        self.d = get_dart()\n"
        "class C(B):\n    def f(self):\n        if not self.d:\n            return\n",
        "class B:\n    def use(self, dart):\n        if not dart:\n"
        "            return\nclass C(B):\n    def f(self):\n"
        "        self.use(get_dart())\n",
        "class B:\n    def __init__(self, dart):\n        self.d = dart\n"
        "    def f(self):\n        if not self.d:\n            return\n"
        "class C(B):\n    pass\ndef g():\n    C(get_dart())\n",
        # 클래스 밖에서 쓰는 객체 속성 — 인스턴스 · 모듈 인스턴스 · 타입 주석 인자
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "def f():\n    c = C()\n    if not c.d:\n        return\n",
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "OBJ = C()\ndef f():\n    if OBJ.d is None:\n        return\n",
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "def f(o: C):\n    if not o.d:\n        return\n",
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "def f(o: 'C'):\n    if not o.d:\n        return\n",
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "def f(o: C):\n    def g():\n        if not o.d:\n            return\n",
        "class C:\n    def use(self, dart):\n        if not dart:\n"
        "            return\ndef f(o: C):\n    o.use(get_dart())\n",
        # 깊이 상한이 없다 — 서른 번 옮겨 담아도
        "def f():\n    v0 = get_dart()\n"
        + "".join(f"    v{i} = v{i - 1}\n" for i in range(1, 31))
        + "    if not v30:\n        return\n",
        # 반복문 — 앞선 클라이언트가 있으면 받기 전 줄도 잡는다(첫 바퀴부터 죽었다)
        "def f(xs):\n    d = get_dart()\n    for x in xs:\n        if not d:\n"
        "            return\n        d = get_dart()\n",
        # else 갈래 — 언제나 클라이언트를 돌려주는 함수(감싼 함수 · 감싼 것을
        # 다시 감싼 함수 · 못 주면 raise · 판정에서 바로 부르기)
        "def mk():\n    return get_dart()\ndef use():\n    d = mk()\n"
        "    if d:\n        work(d)\n    else:\n        log('키 없음')\n",
        "def mk():\n    return get_dart()\ndef mk2():\n    d = mk()\n"
        "    return d\ndef use():\n    x = mk2()\n    if x:\n        work(x)\n"
        "    else:\n        log('키 없음')\n",
        "def mk(k):\n    if k:\n        return get_dart()\n    raise ValueError(k)\n"
        "def use(k):\n    d = mk(k)\n    if d:\n        work(d)\n    else:\n"
        "        log('키 없음')\n",
        "def mk():\n    return get_dart()\ndef use():\n    if mk():\n"
        "        work()\n    else:\n        log('키 없음')\n",
    ])
    def test_scanner_fires_on_former_blind_spots(self, src):
        """#428 문서가 '못 보는 축' 으로 남긴 모양 — 다른 철자 · else 갈래 ·
        컨테이너 · 미뤄 부르기 · `*args`·`**kwargs` · 클래스로 부른 메서드 ·
        객체 메서드 · 상속 · 클래스 밖 객체 속성 · 깊이 상한 · 반복문."""
        assert dead_guards(src), src

    @pytest.mark.parametrize("srcs,where", [
        # 다른 모듈의 조상이 채운 속성(조상 모듈은 자손 모듈이 읽게 한다)
        ({"pkg.b": "class B:\n    def __init__(self):\n"
          "        self.d = get_dart()\n",
          "pkg.c": "from pkg.b import B\nclass C(B):\n    def f(self):\n"
          "        if not self.d:\n            return\n"}, "pkg.c"),
        # 다른 모듈 클래스의 인스턴스 속성(그 클래스 이름으로 찾아 읽는다)
        ({"pkg.k": "class C:\n    def __init__(self):\n"
          "        self.d = get_dart()\n",
          "pkg.u": "from pkg.k import C\ndef f():\n    c = C()\n"
          "    if not c.d:\n        return\n"}, "pkg.u"),
        # 다른 모듈 객체의 칸(그 객체 이름으로 찾아 읽는다)
        ({"pkg.h": "CTX = {'d': get_dart()}\n",
          "pkg.u": "from pkg.h import CTX\ndef f():\n    if not CTX['d']:\n"
          "        return\n"}, "pkg.u"),
        ({"pkg.h": "CTX = {'d': get_dart()}\n",
          "pkg.u": "import pkg.h as h\ndef f():\n    if not h.CTX['d']:\n"
          "        return\n"}, "pkg.u"),
        # 다른 모듈 클래스로 부른 메서드 — import 한 클래스 · 모듈 경로
        ({"pkg.k": "class C:\n    def use(self, dart):\n        if not dart:\n"
          "            return\n",
          "pkg.m": "from pkg.k import C\ndef f(o):\n    C.use(o, get_dart())\n"},
         "pkg.k"),
        ({"pkg.k": "class C:\n    def use(self, dart):\n        if not dart:\n"
          "            return\n",
          "pkg.m": "import pkg.k\ndef f(o):\n    pkg.k.C.use(o, get_dart())\n"},
         "pkg.k"),
        # 조상 모듈엔 공장 이름이 없다 — 상속하는 클래스의 모듈도 읽는다
        ({"pkg.b": "class B:\n    def use(self, dart):\n        if not dart:\n"
          "            return\n",
          "pkg.c": "from pkg.b import B\nclass C(B):\n    def f(self):\n"
          "        self.use(get_dart())\n"}, "pkg.b"),
        # 다른 모듈 전역의 긍정 판정 else 갈래
        ({"pkg.h": "D = get_dart()\n",
          "pkg.c": "import pkg.h as h\ndef f():\n    if h.D:\n        h.D.x()\n"
          "    else:\n        warn()\n"}, "pkg.c"),
        # 다시 내보낸 이름 — 받은 모듈이 그 이름을 정의하지 않고 import 만 한다
        ({"pkg.b": _USE, "pkg.a": "from pkg.b import use\n",
          "pkg.c": "from pkg.a import use\ndef f():\n    use(get_dart())\n"},
         "pkg.b"),
        # 여덟 모듈을 거친 import 사슬 — 깊이 상한 6 이 없다
        ({**{f"pkg.m{i}": f"from pkg.m{i - 1} import D\n" for i in range(1, 9)},
          "pkg.m0": "D = get_dart()\n",
          "pkg.z": "from pkg.m8 import D\ndef f():\n    if not D:\n        return\n"},
         "pkg.z"),
    ])
    def test_scanner_fires_across_modules_on_former_blind_spots(self, srcs,
                                                               where):
        hits = scan(srcs)["hits"]
        assert any(m == where for m, _ln, _e in hits), (hits, srcs)

    @pytest.mark.parametrize("src", [
        # 다른 철자의 반대 증거 — 긍정은 긍정(else 가 없으면 아무것도 아니다)
        "def f():\n    d = get_dart()\n    if bool(d) is True:\n        d.x()\n",
        "def f():\n    d = get_dart()\n    if d is not None:\n        d.x()\n",
        "def f():\n    d = get_dart()\n    if d != None:\n        d.x()\n",
        # else 갈래 — 기본값만이면 해가 없다 · 클라이언트가 아닐 수 있으면 살아 있다
        "def f():\n    d = get_dart()\n    if d:\n        x = d.f()\n    else:\n"
        "        x = None\n",
        "def f():\n    d = get_dart()\n    if d:\n        d.x()\n    else:\n"
        "        pass\n",
        "def f(xs):\n    for x in xs:\n        d = get_dart()\n        if d:\n"
        "            d.x()\n        else:\n            continue\n",
        "def f():\n    d = get_dart()\n    if d:\n        return d.x()\n    else:\n"
        "        return []\n",
        "def f(k):\n    d = get_dart() if k else None\n    if d:\n        d.x()\n"
        "    else:\n        log('비-KR')\n",
        "def use(dart):\n    if dart:\n        dart.x()\n    else:\n"
        "        log('인자로 None')\ndef f():\n    use(get_dart())\n",
        "def f(xs):\n    d = get_dart()\n    for x in xs:\n        if d:\n"
        "            d.x()\n        else:\n            log('다음 바퀴엔 None')\n"
        "        d = None\n",
        "D = None\ndef init():\n    global D\n    D = get_dart()\ndef f():\n"
        "    if D:\n        D.x()\n    else:\n        warn()\n",
        "def f(d):\n    if d:\n        d.x()\n    else:\n        log()\n",
        # 인자는 뒤에서 클라이언트로 다시 묶여도 인자다 — 그 판정 때의 값은
        # 부르는 쪽이 정한다(바인딩만 보면 '언제나 클라이언트' 로 보인다)
        "def use(dart):\n    if dart:\n        dart.x()\n    else:\n"
        "        log('인자로 None')\n    dart = get_dart()\n",
        # 컨테이너의 반대 증거 — 기본값 있는 getattr(지연 초기화) · 없는 칸 ·
        # None 칸 · 펼친 dict · 다른 칸 · 나중에 None 을 쓰는 스코프 · 모르는
        # 값으로 다시 묶은 보관소 · 인자로 받은 dict
        "class C:\n    def connect(self):\n        self._d = get_dart()\n"
        "    def ensure(self):\n        if getattr(self, '_d', None) is None:\n"
        "            self.connect()\n",
        "def f():\n    ctx = {'x': 1}\n    if not ctx['dart']:\n        return\n",
        "def f():\n    ctx = {'dart': None}\n    if not ctx['dart']:\n        return\n",
        # 펼침이 칸 **뒤** 면 덮였을지 모른다(앞이면 그 칸은 확정 — TestScannerElse
        # AndCycles 로 옮겼다, 실수 #430) · 같은 열쇠는 마지막이 이긴다
        "def f(base):\n    ctx = {'dart': get_dart(), **base}\n"
        "    if not ctx['dart']:\n        return\n",
        "def f():\n    ctx = {'dart': get_dart(), 'dart': None}\n"
        "    if not ctx['dart']:\n        return\n",
        # 상수가 아닌 열쇠가 뒤에 오면 그 칸일 수 있다(독립 리뷰)
        "def f(k):\n    ctx = {'dart': get_dart(), k: None}\n"
        "    if not ctx['dart']:\n        return\n",
        "def f(k):\n    ctx = {'dart': get_dart(), f'{k}': None}\n"
        "    if not ctx.get('dart'):\n        return\n",
        "def f():\n    ctx = {'dart': get_dart()}\n    if not ctx['other']:\n"
        "        return\n",
        "def f():\n    xs = [get_dart()]\n    if not xs[1]:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset():\n    CTX['d'] = None\ndef f():\n"
        "    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef load():\n    global CTX\n    CTX = read()\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "def f(ctx):\n    if not ctx['dart']:\n        return\n",
        "def f():\n    ns = SimpleNamespace(dart=get_dart())\n    if not ns.other:\n"
        "        return\n",
        # 미뤄 부르기의 반대 증거 — 클라이언트가 다른 자리로 간다
        "def use(a, dart):\n    if not dart:\n        return\n"
        "def f():\n    functools.partial(use, get_dart())(None)\n",
        "def use(dart):\n    if not dart:\n        return\n"
        "def f(loop):\n    loop.call_later(get_dart(), use)\n",
        "def use(dart):\n    if not dart:\n        return\n"
        "def f(loop):\n    loop.call_soon(use, None, context=get_dart())\n",
        "def use(dart=None, context=None):\n    if not context:\n        return\n"
        "def f(loop):\n    loop.call_soon(use, None, context=get_dart())\n",
        # `*args`·`**kwargs` 의 반대 증거 — 다시 묶은 묶음 · 다른 칸 · 앞에
        # 다른 인자가 있어 자리가 밀린다
        "def use(*a):\n    a = list(a)\n    if not a[0]:\n        return\n"
        "def f():\n    use(get_dart())\n",
        "def use(*a):\n    if not a[1]:\n        return\ndef f():\n"
        "    use(get_dart(), None)\n",
        "def use(**kw):\n    if kw.get('other') is None:\n        return\n"
        "def f():\n    use(dart=get_dart())\n",
        # `*args` 의 칸을 그대로 돌려주는 함수는 공장이 아니다(인자를 돌려준다)
        "def pick(*a):\n    return a[0]\ndef f():\n    pick(get_dart())\n"
        "    x = pick(None)\n    if x is None:\n        return\n",
        # 묶음 인자(`*a`)를 클라이언트로 다시 묶어도 들어올 때의 묶음 바인딩을
        # 센다 — 안쪽 함수가 그 다시 묶기보다 먼저 불릴 수 있는지(흐름)는 안
        # 보므로 보통 인자와 같이 다룬다(보수적 · 옛 판은 묶음을 바인딩으로 안
        # 세서 이 모양을 잡았고, 안쪽 함수가 먼저 불리는 순서에선 오탐이었다)
        "def f(*a):\n    a = get_dart()\n    def g():\n        if not a:\n"
        "            return\n",
        _USE + "def wrap(*a):\n    return use(1, *a)\ndef f():\n"
        "    wrap(get_dart())\n",
        _USE + "def f(xs):\n    use(*xs, *[get_dart()])\n",
        # 클래스의 반대 증거 — 자손이 None 을 넣는다 · 지연 초기화 · 모르는 주석 ·
        # 클래스로 부르면 첫 인자가 self 다 · 정적 메서드
        "class B:\n    def __init__(self):\n        self.d = get_dart()\n"
        "    def f(self):\n        if self.d is None:\n            return\n"
        "class C(B):\n    def reset(self):\n        self.d = None\n",
        "class H:\n    def __init__(self):\n        self.d = None\n"
        "    def connect(self):\n        self.d = get_dart()\n"
        "def f():\n    h = H()\n    if h.d is None:\n        return\n",
        "def f(o: 'Optional[C]'):\n    if not o.d:\n        return\n",
        "class C:\n    def use(self, dart):\n        if not dart:\n"
        "            return\ndef f():\n    C.use(get_dart(), None)\n",
        "class C:\n    @staticmethod\n    def use(a, dart):\n        if not dart:\n"
        "            return\ndef f():\n    C.use(get_dart(), None)\n",
        "class C:\n    def use(self, dart):\n        if not dart:\n"
        "            return\ndef f(c):\n    c.use(get_dart())\n",
        # 반복문 — 첫 바퀴는 받기 전 값을 본다(살아 있는 판정)
        "def f(xs):\n    d = None\n    for x in xs:\n        if d is None:\n"
        "            log('첫 바퀴')\n        d = get_dart()\n",
        # 판정이 조건의 일부여도 기본값 채우기는 살아 있다
        "def f(d=None, force=False):\n    if d is None or force:\n"
        "        d = get_dart()\n    d.x()\ndef g():\n    f(get_dart())\n",
        # else 갈래 — 클라이언트가 아닐 수도 있는 값을 돌려주는 함수는 부정
        # 판정 쪽에선 공장(갈래 하나라도)이지만 else 판정엔 '언제나' 가 아니다
        # (배포전 셀프리뷰가 찾은 오탐): 갈래 하나가 None · 끝으로 흘러 None ·
        # 어느 return 이 None · 판정에서 바로 부르기
        "def maybe(k):\n    return get_dart() if k else None\ndef use(k):\n"
        "    d = maybe(k)\n    if d:\n        work(d)\n    else:\n"
        "        log('키 없음')\n",
        "def mk(k):\n    if k:\n        return get_dart()\ndef use(k):\n"
        "    d = mk(k)\n    if d:\n        work(d)\n    else:\n"
        "        log('키 없음')\n",
        "def mk(k):\n    if not k:\n        return None\n    return get_dart()\n"
        "def use(k):\n    d = mk(k)\n    if d:\n        work(d)\n    else:\n"
        "        log('키 없음')\n",
        "def maybe(k):\n    return get_dart() if k else None\ndef use(k):\n"
        "    if maybe(k):\n        work()\n    else:\n        log('키 없음')\n",
    ])
    def test_scanner_spares_former_blind_spot_lookalikes(self, src):
        """새 축마다 반대 증거(#25) — 살아 있는 판정을 죽은 검사로 잡지 않는다."""
        assert not dead_guards(src), src

    @pytest.mark.parametrize("srcs", [
        # 다른 모듈의 자손이 속성을 None 으로 채운다 — 그 자손 모듈도 읽어 본다
        {"pkg.b": "class B:\n    def __init__(self):\n        self.d = get_dart()\n"
         "    def f(self):\n        if not self.d:\n            return\n",
         "pkg.c": "from pkg.b import B\nclass C(B):\n    def reset(self):\n"
         "        self.d = None\n"},
        # 다른 모듈 객체의 칸을 그 모듈이 None 으로 다시 쓴다
        {"pkg.h": "CTX = {'d': get_dart()}\ndef reset():\n    CTX['d'] = None\n",
         "pkg.u": "from pkg.h import CTX\ndef f():\n    if not CTX['d']:\n"
         "        return\n"},
    ])
    def test_scanner_spares_across_modules_on_former_blind_spots(self, srcs):
        assert not scan(srcs)["hits"], srcs

    def test_scanner_cycles_terminate_without_depth_cap(self):
        """깊이 상한을 순환 감지로 바꿨다 — 보관소끼리 서로를 옮겨 담는 순환·
        자기 자신을 다시 담는 바인딩이 멈춰야 한다(재귀 폭주·무한 반복 없음).
        A↔B 는 `c()` 의 공장 호출(바닥)에 닿아 언제나 클라이언트다 — `not B` 는
        죽었다(E-게이트 최대 고정점 · 실수 #430. 옛 최소 고정점 판은 잡지
        못했다). `D = D` 는 바닥이 없어(값이 한 번도 안 담긴다) 잡지 않는다."""
        r = scan({"m": "def a():\n    global A\n    A = B\ndef b():\n"
                  "    global B\n    B = A\ndef c():\n    global A\n"
                  "    A = get_dart()\ndef f():\n    if not B:\n        return\n"
                  "def g():\n    global D\n    D = D\ndef h():\n    if not D:\n"
                  "        return\n"})
        assert r["hits"] == [("m", 11, "not B")] and r["depth"] < 20, r

    def test_scanner_provisional_false_is_not_memoized(self):
        """순환 속에서 '아직 진행 중' 인 판정을 '아니다' 로 가정해 얻은 거짓은
        **메모하지 않는다** — 그 판정이 참으로 끝나면 이 답도 바뀐다.

        S 의 x 는 B(바깥 함수 F 의 이름)와 `get_dart()` 를 받는다. x 를 판정하는
        중에 B 를 묻고, B 는 S 가 쓴 `B = x` 때문에 다시 x 를 묻는다(진행 중 →
        '아니다'). 그 거짓을 메모하면 x 가 참으로 끝난 뒤에도 B 가 거짓으로 남아
        8행(`z = not B`)과 T 의 3행을 놓친다 — 메모 키에 깊이를 넣었던 옛 판의
        '깊은 자리에서 먼저 물은 답' 과 같은 병이다. 이 판정 순서는 스코프·
        바인딩을 훑는 순서(뒤에서부터)에 기대므로, 이 픽스처가 그 순서를 쓴다."""
        r = scan({"m": "def F():\n"
                  "    def T():\n"
                  "        if not B:\n"
                  "            return\n"
                  "    def S():\n"
                  "        nonlocal B\n"
                  "        x = get_dart()\n"
                  "        x = B; z = not B\n"
                  "        B = x; y = not x\n"
                  "    B = get_dart()\n"})
        lines = {ln for _m, ln, _e in r["hits"]}
        assert {3, 8, 9} <= lines, r["hits"]

    def test_scanner_stack_limit_stops_instead_of_folding(self, monkeypatch):
        """판정 사슬 상한을 넘으면 '아니다' 로 접지 않고 멈추고 실패한다(#54) —
        옛 깊이 상한은 조용히 놓쳤다."""
        import sys
        monkeypatch.setattr(sys.modules[__name__], "_STACK_LIMIT", 3)
        with pytest.raises(RuntimeError, match="판정 사슬"):
            scan({"m": "def f():\n    v0 = get_dart()\n"
                  + "".join(f"    v{i} = v{i - 1}\n" for i in range(1, 7))
                  + "    if not v6:\n        return\n"})

    def test_scanner_rebinding_stays_linear(self):
        """같은 이름을 거듭 다시 묶는 함수(`ok = ok and chk(i)` 30줄) — 메모
        없이는 이전 바인딩을 깊이마다 다시 훑어 갈래가 곱해졌다(리뷰 L3 · 옛 판
        실측: 16줄 1.6초 · 18줄 3.0초 · 30줄은 30초 안에 안 끝났다). 판정
        횟수가 줄 수에 비례하는지 잰다(이 판 30줄 = 64회) — 메모가 깨지면
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


_IF_D = "    if d:\n        work(d)\n    else:\n        log('키 없음')\n"
_IF_SELF_D = ("        if self.d:\n            work()\n        else:\n"
              "            log('키 없음')\n")
_C_SELF_D = ("class C:\n    def __init__(self):\n        self.d = get_dart()\n"
             "    def f(self):\n" + _IF_SELF_D)
_H_ELSE = ("D = get_dart()\ndef f():\n    if D:\n        work()\n    else:\n"
           "        log('키 없음')\n")


def _random_carrier_graph(rng):
    """전역 N개(2~9)가 서로·자기 자신·공장·None 을 옮겨 담는 무작위 그래프 →
    (전역 수, 바인딩 모형, 소스). 바인딩 하나는 후보 1~3개(둘이면 `A if c else
    B` 또는 `A or B`, 셋이면 `A or B or C` — `_cands` 가 펴는 모양 그대로)고,
    모듈 맨 위의 `G = None`·`G = get_dart()` 도 바인딩이다. 판정 자리는
    전역마다 하나(`if not G:`). 전역마다 바인딩 1~4개."""
    n = rng.randint(2, 9)
    binds = {i: [] for i in range(n)}
    head, fns = [], []

    def ex(c):
        return ("get_dart()" if c == "F" else "None" if c is None
                else f"G{c}")
    for i in range(n):
        r = rng.random()
        if r < 0.15:
            binds[i].append([None])
            head.append(f"G{i} = None\n")
        elif r < 0.22:
            binds[i].append(["F"])
            head.append(f"G{i} = get_dart()\n")
        for b in range(rng.randint(1, 4)):
            cands = []
            for _ in range(rng.randint(1, 3)):
                x = rng.random()
                cands.append("F" if x < 0.15 else None if x < 0.25
                             else rng.randrange(n))
            binds[i].append(cands)
            if len(cands) == 2 and rng.random() < 0.5:
                rhs = f"{ex(cands[0])} if c else {ex(cands[1])}"
            else:
                rhs = " or ".join(ex(c) for c in cands)
            fns.append(f"def w{i}_{b}(c):\n    global G{i}\n    G{i} = {rhs}\n")
    fns += [f"def r{i}():\n    if not G{i}:\n        return\n" for i in range(n)]
    rng.shuffle(fns)            # 판정 순서를 바꾼다(순환에 처음 닿는 자리)
    return n, binds, ("from bot.dart_client import get_dart\n" + "".join(head)
                      + "".join(fns))


def _egated_gfp(binds):
    """보관소 규칙의 **E-게이트 최대 고정점**(실수 #430) — 꼭대기(전부 '싣는다')
    에서 내려 최대 고정점을 구하고, 거기서 바닥(공장 후보)에 닿지 않는 전역은
    값이 한 번도 안 담기는 순환이라 '아니다' 로 굳힌 뒤 다시 내린다(굳힌 전역에
    기대던 바인딩도 그만큼 무너진다). 바닥에 닿는 길은 최대 고정점 안의 전역만
    따라간다. 최소 고정점은 늘 이 안에 든다."""
    forced: set = set()
    while True:
        val = {i: i not in forced for i in binds}
        changed = True
        while changed:
            changed = False
            for i, bs in binds.items():
                v = (i not in forced and bool(bs) and all(any(
                    c == "F" or (isinstance(c, int) and val[c]) for c in b)
                    for b in bs))
                if val[i] and not v:
                    val[i], changed = False, True
        ok = {i: False for i in binds}
        changed = True
        while changed:
            changed = False
            for i, bs in binds.items():
                if val[i] and not ok[i] and any(
                        c == "F" or (isinstance(c, int) and val[c] and ok[c])
                        for b in bs for c in b):
                    ok[i] = changed = True
        loose = {i for i in binds if val[i] and not ok[i]}
        if not loose:
            return ok
        forced |= loose


def _least_fixpoint(binds):
    """보관소 규칙의 최소 고정점 — 전역은 바인딩이 **모두** 실어야, 바인딩은
    후보 **하나라도** 실으면 싣는다. 바닥(전부 '아니다')에서 단조롭게 올린다."""
    val = {i: False for i in binds}
    changed = True
    while changed:
        changed = False
        for i, bs in binds.items():
            v = bool(bs) and all(any(c == "F" or (isinstance(c, int) and val[c])
                                     for c in b) for b in bs)
            if v and not val[i]:
                val[i] = changed = True
    return val


class TestScannerTrustAndCycles:
    """독립 리뷰 F2·F5(2026-10-04) — else 판정의 '언제나' 약속 · 부정의 부정 ·
    순환 메모.

    F2: else 판정은 값이 **언제나** 클라이언트일 때만 그 갈래를 죽은 것으로
    본다. 옛 판은 그 약속을 이름으로 대신했다 — `get_dart` 라는 이름이면 사용자
    함수·메서드·인자도 공장이었고, 지역 이름은 자기 스코프의 대입만 봐서
    `nonlocal`/`global` 로 다시 쓰는 안쪽 함수를 놓쳤고, 클래스 밖 `c.d = None`·
    `setattr`·결과를 바꾸는 장식·`is True` 를 몰랐다. 부정 쪽은 `not not d`·
    `not d is None`·`not (x and not d)` 의 안쪽 `not d` 를 부정 판정으로 잡았다
    (리뷰 실측 · 레포 0건이라 잠복). 갈래마다 반대 증거(#25)를 같이 둔다 — 고친
    판정이 진짜 공장·진짜 부정까지 놓치면 그건 고친 게 아니라 끈 것이다.

    F5: 순환의 뿌리만 메모하던 옛 판은 서로를 가리키는 전역 N개에 판정이 지수로
    늘었다(N=18 이 35,363회 · N=22 가 예산 초과). 잠정 '아니다' 를 순환의
    뿌리에서 한꺼번에 확정하게 바꿨고, 그 답이 최소 고정점과 같은지 무작위
    그래프로 잰다(순서에 기대는 메모 버그는 한 픽스처로는 안 보인다)."""

    @pytest.mark.parametrize("src", [
        # ── 이름만 같은 공장 — 부른 것이 bot.dart_client 의 공장이 아니다 ──
        "import os\ndef get_dart():\n"
        "    return DartClient() if os.environ.get('K') else None\n"
        "def use():\n    d = get_dart()\n" + _IF_D,
        "class Svc:\n    def __init__(self):\n        self._d = None\n"
        "    def get_dart(self):\n        return self._d\n"
        "    def run(self):\n        d = self.get_dart()\n        if d:\n"
        "            work(d)\n        else:\n            log('키 없음')\n",
        "def f(get_dart):\n    d = get_dart()\n" + _IF_D,
        "def f(get_dart):\n    d = get_dart()\n    if not d:\n        return\n",
        "def f(fac):\n    get_dart = fac\n    d = get_dart()\n    if not d:\n"
        "        return\n",
        "get_dart = make_getter()\ndef f():\n    d = get_dart()\n" + _IF_D,
        # 부정 판정도 모듈에서 다른 값으로 묶은 이름은 공장이 아니다(F2-07)
        "get_dart = make_getter()\ndef f():\n    d = get_dart()\n    if not d:\n"
        "        return\n",
        "def get_dart():\n    return {}\ndef f():\n    d = get_dart()\n"
        "    if not d:\n        return\n",
        "from otherlib import get_dart\ndef f():\n    d = get_dart()\n" + _IF_D,
        "def f(cfg):\n    d = cfg.get_dart()\n" + _IF_D,
        # ── 다시 쓰는 쪽 — nonlocal(몇 겹이든) · global · 클래스 밖 · setattr ──
        "def f():\n    d = get_dart()\n    def reset():\n        nonlocal d\n"
        "        d = None\n    reset()\n" + _IF_D,
        "def f():\n    d = get_dart()\n    def g():\n        def h():\n"
        "            nonlocal d\n            d = None\n        h()\n    g()\n"
        + _IF_D,
        "D = get_dart()\ndef reset():\n    global D\n    D = None\nreset()\n"
        "if D:\n    D.x()\nelse:\n    warn()\n",
        _C_SELF_D + "def g():\n    c = C()\n    c.d = None\n    c.f()\n",
        _C_SELF_D + "def g(o):\n    o.d = None\n",          # o 가 C 일 수 있다
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "    def drop(self):\n        setattr(self, 'd', None)\n"
        "    def f(self):\n" + _IF_SELF_D,
        _C_SELF_D + "def g(o, name):\n    setattr(o, name, None)\n",
        # ── 결과를 바꿀 수 있는 장식 · 제너레이터(부르면 제너레이터가 온다) ──
        "def maybe_none(fn):\n    def w(*a):\n        return None\n    return w\n"
        "@maybe_none\ndef mk():\n    return get_dart()\n"
        "def use():\n    d = mk()\n" + _IF_D,
        "def mk():\n    yield get_dart()\ndef use():\n    d = mk()\n"
        "    if not d:\n        return\n",
        "def mk():\n    yield None\n    return get_dart()\ndef use():\n"
        "    if mk():\n        work()\n    else:\n        log('키 없음')\n",
        # ── `is True`·`== True` — 객체는 True 가 아니라 else 가 사는 갈래다 ──
        "def f():\n    d = get_dart()\n    if d is True:\n        d.x()\n"
        "    else:\n        log('사는 갈래')\n",
        "def f():\n    d = get_dart()\n    if d == True:\n        d.x()\n"
        "    else:\n        log('사는 갈래')\n",
        "def f():\n    d = get_dart()\n    x = d.x() if d is True else why()\n",
        "def f():\n    d = get_dart()\n    if d is not True:\n"
        "        log('늘 돈다')\n",
        # ── 부정의 부정은 긍정 ──
        "def f():\n    d = get_dart()\n    if not not d:\n        d.x()\n",
        "def f():\n    d = get_dart()\n    if not (not d):\n        d.x()\n",
        "def f():\n    d = get_dart()\n    if not d is None:\n        d.x()\n",
        "def f(x):\n    d = get_dart()\n    if not (x and not d):\n        d.x()\n",
        "def f(x):\n    d = get_dart()\n    if not (not d or x):\n        d.x()\n",
        "def f(x):\n    d = get_dart()\n    if not (d is None or x):\n"
        "        d.x()\n",
        "def f(x):\n    d = get_dart()\n    if not (x or not d):\n        d.x()\n",
        "def f():\n    d = get_dart()\n    if not bool(not d):\n        d.x()\n",
        # ── `.get` 은 없는 칸에 기본값이 온다 — 빈 리터럴은 '아직 없음'(지연
        # 초기화)이다. 칸 열쇠(`CTX['d']`)는 그 바인딩을 건너뛰지만(읽으면
        # KeyError) `.get` 은 '실리지 않음' 으로 센다 ──
        "CTX = {}\ndef init():\n    CTX['d'] = get_dart()\ndef f():\n"
        "    if CTX.get('d') is None:\n        init()\n",
        "CTX = dict()\ndef init():\n    CTX['d'] = get_dart()\ndef f():\n"
        "    if CTX.get('d', None) is None:\n        init()\n",
        "class C:\n    def __init__(self):\n        self.cache = {}\n"
        "    def connect(self):\n        self.cache['dart'] = get_dart()\n"
        "    def ensure(self):\n        if self.cache.get('dart') is None:\n"
        "            self.connect()\n",
        # 기본값 채우기는 `.get` 으로 물어도 같다(채우는 대입은 칸 열쇠로 쓴다)
        "def f():\n    ctx = {'d': get_dart()}\n    if ctx.get('d') is None:\n"
        "        ctx['d'] = get_dart()\n",
        # ── 리뷰가 남긴 생존 뮤테이션의 반대 증거 ──
        # S8: 부정의 부정 항은 기본값 채우기 판정의 대상에서도 빠진다 — 채우는
        # 이름 하나만 남아야 그 판정이 채우기로 읽힌다
        "def f(x=None):\n    d = get_dart()\n    if not (x and not d):\n"
        "        x = get_dart()\n    work(x)\ndef g():\n    f(get_dart())\n",
        # S39: 함수 자리 앞에 별표가 있으면 어느 인자가 함수인지 모른다
        _USE + "def f(loop, xs):\n    loop.run_in_executor(*xs, use, get_dart())\n",
        # S49: 지역 컨테이너는 판정 **앞** 바인딩만(뒤에서 다시 묶은 것은 아니다)
        "def f():\n    ctx = {'d': None}\n    if not ctx['d']:\n        return\n"
        "    ctx = {'d': get_dart()}\n",
        # S50: 부를 때 o 는 인자다 — 뒤에서 `C()` 로 다시 묶은 것은 아니다
        "class C:\n    def use(self, dart):\n        if not dart:\n"
        "            return\ndef f(o):\n    o.use(get_dart())\n    o = C()\n",
        # ── 델타 리뷰 M1(2026-10-04) — 공장 import 와 같은 이름의 다른 바인딩이
        # 같은 스코프에 섞이면 런타임에 어느 쪽이 묶일지 모른다(흐름을 안 본다):
        # try/except 폴백 · 뒤의 재정의 · lambda · 앞의 정의(import 가 이겨도
        # 놓치는 쪽) · 함수 안의 폴백. else 판정은 '언제나' 를 믿지 않는다 ──
        "try:\n    from bot.dart_client import get_dart\nexcept Exception:\n"
        "    def get_dart():\n        return None\ndef f():\n    d = get_dart()\n"
        + _IF_D,
        "from bot.dart_client import get_dart\ndef get_dart():\n    return None\n"
        "def f():\n    d = get_dart()\n" + _IF_D,
        "from bot.dart_client import get_dart\nget_dart = lambda: None\n"
        "def f():\n    d = get_dart()\n" + _IF_D,
        "def f():\n    try:\n        from bot.dart_client import get_dart\n"
        "    except Exception:\n        get_dart = lambda: None\n"
        "    d = get_dart()\n" + _IF_D,
        "from bot.dart_client import get_dart\ndef reset():\n    global get_dart\n"
        "    get_dart = lambda: None\ndef f():\n    d = get_dart()\n" + _IF_D,
        # ── 델타 리뷰 L4 — 장식도 이름이 아니라 해석으로: 사용자 정의 `cache` ·
        # 다른 라이브러리의 `cache`/`lru_cache` · 다시 묶은 `staticmethod` 는
        # 감싼 함수가 무엇을 돌려줄지 모른다 ──
        "from mylib import cache\n@cache\ndef mk():\n    return get_dart()\n"
        "def use():\n    d = mk()\n" + _IF_D,
        "def cache(fn):\n    def w(*a):\n        return None\n    return w\n"
        "@cache\ndef mk():\n    return get_dart()\ndef use():\n    d = mk()\n"
        + _IF_D,
        "from mylib import lru_cache\n@lru_cache(maxsize=None)\ndef mk():\n"
        "    return get_dart()\ndef use():\n    d = mk()\n" + _IF_D,
        "staticmethod = make_wrapper()\nclass K:\n    @staticmethod\n"
        "    def mk():\n        return get_dart()\ndef use():\n    d = K.mk()\n"
        + _IF_D,
        "import mylib\n@mylib.cache\ndef mk():\n    return get_dart()\n"
        "def use():\n    d = mk()\n" + _IF_D,
    ])
    def test_spares_what_the_promise_cannot_cover(self, src):
        assert not dead_guards(src), src

    @pytest.mark.parametrize("src, want", [
        # ── 진짜 공장 — import · 모듈 별칭 · 모듈 경로 · 이름 별칭 · DartClient ·
        # 공장을 감싼 메서드 · 정적 메서드 · lru_cache(이름·호출 모양) ──
        ("from bot.dart_client import get_dart\ndef f():\n    d = get_dart()\n"
         + _IF_D, (4, "d → else")),
        ("import bot.dart_client as dc\ndef f():\n    d = dc.get_dart()\n"
         + _IF_D, (4, "d → else")),
        ("import bot.dart_client\ndef f():\n"
         "    d = bot.dart_client.get_dart()\n" + _IF_D, (4, "d → else")),
        ("from bot.dart_client import get_dart as gd\ndef f():\n    d = gd()\n"
         + _IF_D, (4, "d → else")),
        ("from bot.dart_client import DartClient\ndef f():\n    d = DartClient()\n"
         + _IF_D, (4, "d → else")),
        ("from bot.dart_client import get_dart\nclass Svc:\n"
         "    def get_dart(self):\n        return get_dart()\n"
         "    def run(self):\n        d = self.get_dart()\n        if d:\n"
         "            work(d)\n        else:\n            log('키 없음')\n",
         (7, "d → else")),
        ("class K:\n    @staticmethod\n    def mk():\n        return get_dart()\n"
         "def use():\n    d = K.mk()\n" + _IF_D, (7, "d → else")),
        ("from functools import lru_cache\n@lru_cache\ndef mk():\n"
         "    return get_dart()\ndef use():\n    d = mk()\n" + _IF_D,
         (7, "d → else")),
        ("import functools\n@functools.lru_cache(maxsize=None)\ndef mk():\n"
         "    return get_dart()\ndef use():\n    d = mk()\n" + _IF_D,
         (7, "d → else")),
        # ── 같은 이름의 다른 변수·다른 속성·다른 객체는 막지 않는다 ──
        ("def f():\n    d = get_dart()\n    def show():\n        print(d)\n"
         "    show()\n" + _IF_D, (6, "d → else")),
        ("def f():\n    d = get_dart()\n    def other():\n        d = None\n"
         "        return d\n" + _IF_D, (6, "d → else")),
        ("def f():\n    d = get_dart()\n" + _IF_D + "def k():\n    d = None\n"
         "    def h():\n        nonlocal d\n        d = 1\n", (3, "d → else")),
        ("class C:\n    def __init__(self):\n        self.d = get_dart()\n"
         "    def drop(self):\n        self.x = None\n    def f(self):\n"
         + _IF_SELF_D, (7, "self.d → else")),
        (_C_SELF_D + "class Other:\n    pass\ndef g():\n    o = Other()\n"
         "    o.d = None\n", (5, "self.d → else")),
        ("class C:\n    def __init__(self):\n"
         "        setattr(self, 'd', get_dart())\n    def f(self):\n"
         "        if not self.d:\n            return\n", (5, "not self.d")),
        # ── 홀수 번 부정 · 긍정 비교의 부정 · bool() 로 감싼 True 비교 ──
        ("def f():\n    d = get_dart()\n    if not not not d:\n        return\n",
         (3, "not d")),
        ("def f():\n    d = get_dart()\n    if not (d is not None):\n"
         "        return\n", (3, "not d is not None")),
        ("def f(x):\n    d = get_dart()\n    if not (x and d is not None):\n"
         "        return\n", (3, "not (x and d is not None)")),
        ("def f(x):\n    d = get_dart()\n    if not (x or not not d):\n"
         "        return\n", (3, "not d")),
        ("def f():\n    d = get_dart()\n    if bool(d) is True:\n        d.x()\n"
         "    else:\n        log('키 없음')\n", (3, "bool(d) is True → else")),
        ("def f():\n    d = get_dart()\n    if bool(d) == True:\n        d.x()\n"
         "    else:\n        log('키 없음')\n", (3, "bool(d) == True → else")),
        # 대입식으로 받는 긍정 판정의 else(리뷰 S43)
        ("def f():\n    if (d := get_dart()):\n        d.x()\n    else:\n"
         "        log('키 없음')\n", (2, "(d := get_dart()) → else")),
        # ── `.get` 의 반대 증거 — 칸이 있는 리터럴 · 먼저 쓴 칸 · self 칸 ·
        # 기본값이 상수가 아니어도(옛 판은 그 `.get` 을 열쇠로 안 봤다) ──
        ("CTX = {'d': get_dart()}\ndef f():\n    if CTX.get('d') is None:\n"
         "        return\n", (3, "CTX.get('d') is None")),
        ("def f():\n    ctx = {}\n    ctx['d'] = get_dart()\n"
         "    if ctx.get('d') is None:\n        return\n",
         (4, "ctx.get('d') is None")),
        ("class C:\n    def __init__(self):\n"
         "        self.cache = {'dart': get_dart()}\n    def f(self):\n"
         "        if self.cache.get('dart') is None:\n            return\n",
         (5, "self.cache.get('dart') is None")),
        ("def f():\n    ctx = {'d': get_dart()}\n"
         "    if ctx.get('d', make()) is None:\n        return\n",
         (3, "ctx.get('d', make()) is None")),
        # 칸 열쇠는 빈 리터럴을 건너뛴다(읽으면 KeyError — 채워진 뒤에만 닿는다)
        ("CTX = {}\ndef init():\n    CTX['d'] = get_dart()\ndef f():\n"
         "    if not CTX['d']:\n        init()\n", (5, "not CTX['d']")),
        # S14: 같은 이름의 지역 컨테이너에 쓴 것은 모듈 전역의 증거가 아니다
        ("CTX = {'d': get_dart()}\ndef g():\n    CTX = {}\n    CTX['d'] = None\n"
         "def f():\n    if not CTX['d']:\n        return\n", (6, "not CTX['d']")),
        # S39·S8 의 대조(별표·채우기가 없으면 잡는다)
        (_USE + "def f(loop):\n    loop.run_in_executor(None, use, get_dart())\n",
         (2, "not dart")),
        ("def f(x=None):\n    d = get_dart()\n    if not (x and not d):\n"
         "        log('x 없음')\n    work(x)\ndef g():\n    f(get_dart())\n",
         (3, "not (x and (not d))")),
        # ── 델타 리뷰 M1 의 반대 증거 — 부정 판정은 갈래 하나라도: import 가
        # 성공하는 갈래에선 키 없는 클라이언트가 와서 `not d` 가 그 갈래의 '키
        # 없음' 을 못 잡는다(고치는 법은 `dart_ready` — None 도 받는다). 공장
        # 모듈을 안 읽은 실행도 같은 답이어야 한다(옛 판은 읽었을 때만 잡았다) ──
        ("try:\n    from bot.dart_client import get_dart\nexcept Exception:\n"
         "    def get_dart():\n        return None\ndef f():\n    d = get_dart()\n"
         "    if not d:\n        log('키 없음')\n", (8, "not d")),
        ("def f():\n    try:\n        from bot.dart_client import get_dart\n"
         "    except Exception:\n        get_dart = lambda: None\n"
         "    d = get_dart()\n    if not d:\n        log('키 없음')\n",
         (7, "not d")),
        # 같은 import 를 두 갈래에 둔 것은 한 바인딩이다(else 도 잡는다)
        ("if X:\n    from bot.dart_client import get_dart\nelse:\n"
         "    from bot.dart_client import get_dart\ndef f():\n    d = get_dart()\n"
         + _IF_D, (7, "d → else")),
        # L4 의 반대 증거 — functools 에서 온 `cache` 는 결과를 바꾸지 않는다
        ("from functools import cache\n@cache\ndef mk():\n    return get_dart()\n"
         "def use():\n    d = mk()\n" + _IF_D, (7, "d → else")),
        # ── 실수 #430 — 모듈 맨 위의 **마지막** 바인딩이 이긴다(직선 코드, 사이에
        # import 시점 호출 없음). 옛 판은 바인딩이 둘이면 '섞였다' 로 봐 앞의
        # 정의 뒤 import 를 놓쳤다(옛 반대 증거 — 지우지 않고 뒤집는다, #222) ──
        ("def get_dart():\n    return None\nfrom bot.dart_client import get_dart\n"
         "def f():\n    d = get_dart()\n" + _IF_D, (6, "d → else")),
    ])
    def test_fires_on_the_real_shapes(self, src, want):
        assert dead_guards(src) == [want], src

    def test_mixed_binding_with_the_factory_module_read(self):
        """델타 리뷰 M1 — 실제 레포 스캔처럼 `bot.dart_client` 를 같이 읽으면
        이름이 그 import 로 해석된다. else 판정은 그 해석을 믿지 않고(폴백이 섞인
        이름) · 부정 판정은 잡는다(갈래 하나라도). 공장이 아닌 도우미도 같다 —
        폴백이 섞이면 else 는 믿지 않고, 섞이지 않으면 잡는다(반대 증거)."""
        dc = ("_one = None\nclass DartClient:\n    def __init__(self, k=None):\n"
              "        self.api_key = k\ndef get_dart():\n    global _one\n"
              "    if _one is None:\n        _one = DartClient()\n    return _one\n")
        fb = ("try:\n    from bot.dart_client import get_dart\n"
              "except Exception:\n    def get_dart():\n        return None\n")
        r = scan({"bot.dart_client": dc,
                  "bot.user": fb + "def f():\n    d = get_dart()\n" + _IF_D})
        assert r["hits"] == [], r
        r = scan({"bot.dart_client": dc,
                  "bot.user": fb + "def f():\n    d = get_dart()\n"
                  "    if not d:\n        log('키 없음')\n"})
        assert r["hits"] == [("bot.user", 8, "not d")], r
        mk = "def mk():\n    return get_dart()\n"
        mixed = ("try:\n    from pkg.helpers import mk\nexcept Exception:\n"
                 "    def mk():\n        return None\ndef f():\n    d = mk()\n"
                 + _IF_D)
        r = scan({"pkg.helpers": mk, "pkg.u": mixed})
        assert r["hits"] == [], r
        r = scan({"pkg.helpers": mk, "pkg.u": "from pkg.helpers import mk\n"
                  "def f():\n    d = mk()\n" + _IF_D})
        assert r["hits"] == [("pkg.u", 4, "d → else")], r

    @pytest.mark.parametrize("srcs", [
        {"pkg.h": _H_ELSE, "pkg.r": "import pkg.h\ndef reset():\n"
         "    pkg.h.D = None\n"},
        {"pkg.h": _H_ELSE, "pkg.r": "from pkg import h\ndef reset():\n"
         "    h.D = None\n"},
    ])
    def test_spares_module_global_written_from_another_module(self, srcs):
        """다른 모듈이 `모듈.D = None` 으로 그 전역을 바꾼다 — 그 모듈도 읽는다
        (읽지 않으면 else 를 잡아 이 단언이 깨진다)."""
        r = scan(srcs)
        assert r["hits"] == [] and r["built"] == ["pkg.h", "pkg.r"], r

    def test_spares_module_attribute_read_from_a_third_module(self):
        """셋째 모듈이 `pkg.h.D` 로 읽는 else — 속성 경로 갈래도 다른 모듈의
        `pkg.h.D = None` 을 본다(F2-13: 그 갈래만 외부 쓰기를 안 보는 변형이
        살아남았다 — 같은 모듈 안에서 읽는 픽스처만 있었다)."""
        u = ("import pkg.h\ndef f():\n    if pkg.h.D:\n        work()\n"
             "    else:\n        log('키 없음')\n")
        r = scan({"pkg.h": "D = get_dart()\n",
                  "pkg.r": "import pkg.h\ndef reset():\n    pkg.h.D = None\n",
                  "pkg.u": u})
        assert r["hits"] == [] and r["built"] == ["pkg.h", "pkg.r", "pkg.u"], r
        r = scan({"pkg.h": "D = get_dart()\n",
                  "pkg.r": "import pkg.other\ndef reset():\n"
                  "    pkg.other.D = None\n", "pkg.u": u})
        assert r["hits"] == [("pkg.u", 3, "pkg.h.D → else")], r

    def test_fires_when_another_module_writes_an_unrelated_module(self):
        r = scan({"pkg.h": _H_ELSE, "pkg.r": "import pkg.other\n"
                  "def reset():\n    pkg.other.D = None\n"})
        assert r["hits"] == [("pkg.h", 3, "D → else")], r
        assert r["built"] == ["pkg.h", "pkg.r"], r   # 읽고도 막지 않는다

    def test_cycles_reach_the_least_fixpoint(self):
        """무작위 그래프 600개 — 판정이 E-게이트 최대 고정점(`_egated_gfp` —
        서로 옮겨 담는 순환도 바닥에 닿으면 참 · 실수 #430. 옛 판은 최소 고정점
        이었다)과 같아야 하고, 최소 고정점은 늘 그 안에 든다. 잠정 '아니다' 를 메모하거나(옛 판의 깊이 메모 병) ·
        뿌리가 참으로 끝났는데 그 아래 잠정을 남기거나 · 기대는 자리를 위로
        안 올리면 순서에 따라 답이 갈린다(한 픽스처로는 안 보인다). 시드를
        고정했고 판정 순서도 해시 시드와 무관하게 정렬했다(`exported()`) —
        실패가 재현된다. ⚠️ 드문 모양(자리 물려주기 · 잠정 조회의 자리 낮추기)은
        300개로는 안 걸려 아래에 줄인 반례를 따로 둔다."""
        import random
        rng = random.Random(20261004)
        hit_total, checks, bad, gfp_only = 0, 0, [], 0
        for t in range(600):
            n, binds, src = _random_carrier_graph(rng)
            want = _egated_gfp(binds)
            lfp = _least_fixpoint(binds)
            assert all(want[i] for i in binds if lfp[i]), (t, src)
            gfp_only += sum(1 for i in binds if want[i] and not lfp[i])
            lines = src.splitlines()
            at = {i: lines.index(f"    if not G{i}:") + 1 for i in range(n)}
            r = scan({"m": src})
            # 판정 줄만 — 같은 그래프의 `G3 or G2`(앞 항이 늘 클라이언트라 뒤
            # 항이 안 도는 자리)도 잡히지만 그건 이 오라클이 재는 것이 아니다
            got = {ln for _m, ln, e in r["hits"] if not e.endswith("→ else")}
            exp = {at[i] for i in range(n) if want[i]}
            hit_total += len(exp)
            checks += n
            if got != exp:
                bad.append((t, sorted(exp ^ got), src))
        assert not bad, bad[:2]
        # 참·거짓이 둘 다 충분히 나와야 판정을 잰다(한쪽뿐이면 상수로도 통과)
        assert hit_total >= 100 and checks - hit_total >= 100, (hit_total,
                                                                checks)
        # 최대 고정점에서만 참인 전역(순환으로만 증명되는 것)도 충분히 나와야
        # 그 갈래를 잰다
        assert gfp_only >= 100, gfp_only

    def test_memo_reset_also_resets_cycle_provisionals(self):
        """고정점이 운반 인자를 새로 찾아 메모를 비우면 순환의 잠정 거짓도
        **같이** 사라져야 한다(F5-04 — 뿌리에서 확정하지 않고 잠정으로 남기는
        변형은 그 잠정이 메모 비우기를 살아남아, 다음 바퀴에 낡은 '아니다' 를
        돌려줬다). `ra()` 가 먼저 A 를 물어 A↔B 순환이 '아니다' 로 끝나고, 그
        뒤 `go()` 가 wb 의 p 에 클라이언트를 실어 B 가 참이 된다 — 스코프는
        뒤에서부터 훑으므로 ra 가 go 보다 뒤에 정의돼야 그 순서가 된다."""
        assert dead_guards(
            "def wa(c):\n    global A\n    A = B if c else None\n"
            "def wb(p, c):\n    global B\n    B = A if c else p\n"
            "def go():\n    wb(get_dart(), 1)\n"
            "def ra():\n    return A\n"
            "def use():\n    if not A:\n        return\n") == [(12, "not A")]

    def test_provisional_lookup_lowers_the_root(self):
        """잠정 '아니다' 에 닿은 판정은 그 잠정이 기대는 자리까지 내려간다 —
        안 내려가면 스스로를 순환의 뿌리로 알고 거짓을 메모해, 위의 판정이
        참으로 끝난 뒤에도 그 거짓이 남는다(F5-08 — 무작위 탐색이 찾은 반례를
        줄인 것. `exported()` 를 정렬해 판정 순서가 해시 시드와 무관해진 뒤에야
        재현됐다)."""
        src = ("from bot.dart_client import get_dart\nG3 = get_dart()\n"
               "def w0_2(c):\n    global G0\n    G0 = G2 or G3\n"
               "def w4_1(c):\n    global G4\n    G4 = G1\n"
               "def w2_0(c):\n    global G2\n    G2 = G4\n"
               "def w1_0(c):\n    global G1\n    G1 = G0\n"
               "def r4():\n    if not G4:\n        return\n"
               "def w4_0(c):\n    global G4\n    G4 = G1 or get_dart()\n")
        # 부정 판정만 — `or` 꼬리 판정(실수 #430)은 이 반례가 재는 것이 아니다
        assert [h for h in dead_guards(src)
                if not h[1].endswith("→ else")] == [(16, "not G4")]

    def test_lowered_dependency_survives_its_owner(self):
        """잠정으로 끝난 판정이 기대던 자리를 그 아래 잠정들에게 물려준다 — 안
        물려주면 그 잠정에 나중에 닿은 판정이 이미 비운 자리를 기대는 것으로
        보고 뿌리가 돼 거짓을 메모한다(F5-05 — 무작위 탐색 6만 그래프 중
        하나꼴로 나타나 줄인 반례. 9개 전역 그래프가 필요했다)."""
        src = ("from bot.dart_client import get_dart\nG3 = get_dart()\n"
               "def r8():\n    if not G8:\n        return\n"
               "def w8_0(c):\n    global G8\n    G8 = G4\n"
               "def w4_0(c):\n    global G4\n    G4 = G7\n"
               "def w7_1(c):\n    global G7\n    G7 = G4 or G0\n"
               "def w8_1(c):\n    global G8\n    G8 = G6 or G5\n"
               "def w6_0(c):\n    global G6\n    G6 = G7\n"
               "def w0_0(c):\n    global G0\n    G0 = G8 or G3\n"
               "def w5_1(c):\n    global G5\n    G5 = G3\n")
        assert [h for h in dead_guards(src)
                if not h[1].endswith("→ else")] == [(4, "not G8")]

    @pytest.mark.parametrize("n", [10, 22, 40])
    def test_cycle_evals_stay_linear(self, n):
        """리뷰 F5 의 모양 — 전역 N개가 각자 뒤의 둘 중 하나를 받고, 끝이 머리로
        돌아온다. 옛 판: N=18 이 35,363회 · N=22 예산 초과. 이 판은 N 에
        비례한다(실측 5N+5)."""
        lines = ["from bot.dart_client import get_dart"]
        for i in range(n):
            a, b = (i + 1) % n, (i + 2) % n
            lines.append(f"def w{i}(c):\n    global G{i}\n"
                         f"    G{i} = G{a} if c else G{b}")
        lines.append("def z():\n    global G0\n    G0 = get_dart()")
        lines.append("def r():\n    if not G3:\n        return")
        r = scan({"m": "\n".join(lines) + "\n"})
        # G0 만 공장을 받고 나머지는 서로를 받는다 — 서로 옮겨 담는 순환이
        # G0 의 공장 호출(바닥)에 닿으므로 모든 전역이 언제나 클라이언트다(E-게이트
        # 최대 고정점 · 실수 #430. 옛 판의 최소 고정점은 증명하지 못했다)
        assert [e for _m, _ln, e in r["hits"]] == ["not G3"], r["hits"]
        # 재기가 순환을 회차마다 다시 재므로(직전 회차의 참을 가정해 한 번 더 →
        # 같으면 멈춤) 상수는 커졌지만 여전히 N 에 비례한다(실측 15N+2 · 옛
        # 최소 고정점 판은 5N+5)
        assert n <= r["evals"] <= 20 * n, (n, r["evals"])


class TestScannerFlowAndClasses:
    """실수 #430 — #429 가 '못 보는 축' 으로 남긴 흐름·클래스 축. 갈래마다 발화와
    반대 증거(#25)를 같이 둔다.

    · 같은 블록 도달 정의 — 옛 판은 같은 스코프의 앞선 바인딩 **하나라도** 봐
      `d = get_dart(); d = None; if d is None:` 을 잡았다(오탐). 위로 거슬러 첫
      바인딩이 단순하고 갈래·반복·예외가 끼지 않으면 그 값 하나다.
    · 모듈 맨 위 마지막 바인딩 — 맨 위 직선 코드(사이에 import 시점 호출 없음)면
      함수 안에서 읽는 값은 마지막 바인딩이다(`_D = None; _D = get_dart()`).
      맨 위 문장·맨 위 클래스 본문의 호출은 import 시점에 돌므로 **그 줄에 닿는**
      바인딩이다(`_D = get_dart()` 뒤 `def get_dart()` 는 그 자리에 없다).
    · 장식 별칭 — `from functools import cache as memo` · `sm = staticmethod`.
    · C3 — 다이아몬드에서 파이썬과 같은 메서드를 고른다.
    · 가족 밖 클래스가 **자기** 메서드에서 쓰는 `self.d` 는 다른 객체다(그
      메서드를 클래스로 불러 남의 객체를 넘기는 자리가 없을 때)."""

    @pytest.mark.parametrize("src, want", [
        # ── C3 ──
        ("class A:\n    def mk(self):\n        return None\nclass B(A):\n"
         "    pass\nclass C(A):\n    def mk(self):\n        return get_dart()\n"
         "class D(B, C):\n    def f(self):\n        d = self.mk()\n"
         "        if not d:\n            return\n", [(12, "not d")]),
        # ── 가족 밖 클래스의 자기 메서드 self · classmethod 의 cls ──
        (_C_SELF_D + "class Other:\n    def reset(self):\n        self.d = None\n",
         [(5, "self.d → else")]),
        (_C_SELF_D + "class Other:\n    @classmethod\n    def reset(cls):\n"
         "        cls.d = None\n", [(5, "self.d → else")]),
        # ── 모듈 맨 위 마지막 바인딩 ──
        ("_D = None\n_D = get_dart()\ndef f():\n    if not _D:\n        return\n",
         [(4, "not _D")]),
        ("_D = None\n_D = get_dart()\ndef f():\n    if _D:\n        work()\n"
         "    else:\n        log('키 없음')\n", [(4, "_D → else")]),
        # 장식이 붙은 재정의는 import 시점에 무엇을 부를지 모른다 — 옛 규칙(엄격)
        ("from bot.dart_client import get_dart\n@deco\ndef get_dart():\n"
         "    return None\ndef f():\n    d = get_dart()\n    if not d:\n"
         "        return\n", [(7, "not d")]),
        # ── 장식 별칭 ──
        ("from functools import cache as memo\n@memo\ndef mk():\n"
         "    return get_dart()\ndef use():\n    d = mk()\n" + _IF_D,
         [(7, "d → else")]),
        ("from functools import cache as lru_cache\n@lru_cache\ndef mk():\n"
         "    return get_dart()\ndef use():\n    d = mk()\n" + _IF_D,
         [(7, "d → else")]),
        ("sm = staticmethod\nclass K:\n    @sm\n    def mk():\n"
         "        return get_dart()\ndef use():\n    d = K.mk()\n" + _IF_D,
         [(8, "d → else")]),
        # ── 같은 블록 도달 정의 ──
        ("def f():\n    d = None\n    d = get_dart()\n    if d is None:\n"
         "        return\n", [(4, "d is None")]),
        ("def f():\n    d = None\n    d = get_dart()\n" + _IF_D, [(4, "d → else")]),
        ("def f(d):\n    d = get_dart()\n    if not d:\n        return\n",
         [(3, "not d")]),
        # 갈래·예외·한 줄 여러 문장·대입식 — 옛 규칙(앞선 바인딩 하나라도)
        ("def f(x):\n    d = get_dart()\n    if x:\n        d = None\n"
         "    if not d:\n        return\n", [(5, "not d")]),
        ("def f():\n    d = get_dart()\n    try:\n        d = None\n"
         "    except Exception:\n        pass\n    if not d:\n        return\n",
         [(7, "not d")]),
        ("def f():\n    d = get_dart(); d = None; ok = d is None\n",
         [(2, "d is None")]),
        ("def f():\n    d = None\n    if (d := get_dart()) and not d:\n"
         "        return\n", [(3, "not d")]),
        # 안쪽 갈래 안에서 받은 뒤의 판정 — 그 갈래 안에서는 하나로 정해진다
        ("def f(x):\n    d = None\n    if x:\n        d = get_dart()\n"
         "        if not d:\n            return\n", [(5, "not d")]),
        # classmethod 를 클래스로 부르는 것은 cls 가 그 클래스다(남의 객체가 아니다)
        (_C_SELF_D + "class Other:\n    @classmethod\n    def reset(cls):\n"
         "        cls.d = None\nOther.reset()\n", [(5, "self.d → else")]),
        # 반복문의 다음 바퀴 · while 머리 · try 의 except · 바깥 if 머리의 대입식 ·
        # 모듈 맨 위를 global 로 쓰는 함수 · nonlocal 로 쓰는 안쪽 함수 · 기본값에
        # 호출이 있는 재정의 — 하나로 정할 수 없어 옛 규칙(앞선 바인딩 하나라도)
        ("def f(xs):\n    d = get_dart()\n    d = None\n    for x in xs:\n"
         "        if not d:\n            return\n        d = get_dart()\n",
         [(5, "not d")]),
        ("def f():\n    d = get_dart()\n    d = None\n    while not d:\n"
         "        d = get_dart()\n", [(4, "not d")]),
        ("def f():\n    d = None\n    try:\n        d = get_dart()\n"
         "    except Exception:\n        if not d:\n            return\n",
         [(6, "not d")]),
        ("def f():\n    d = None\n    if (d := get_dart()):\n        if not d:\n"
         "            return\n", [(4, "not d")]),
        ("def reset():\n    global d\n    d = get_dart()\nd = get_dart()\nd = None\n"
         "reset()\nif d is None:\n    pass\n", [(7, "d is None")]),
        ("def f():\n    d = get_dart()\n    d = None\n    def g():\n"
         "        nonlocal d\n        d = get_dart()\n    g()\n    if d is None:\n"
         "        return\n", [(8, "d is None")]),
        ("from bot.dart_client import get_dart\ndef get_dart(x=init()):\n"
         "    return None\ndef f():\n    d = get_dart()\n    if not d:\n"
         "        return\n", [(6, "not d")]),
        # ── import 시점 코드는 그 줄에 닿는 바인딩 ──
        # 맨 위 문장이 부른 공장 — 뒤의 재정의는 그 자리에 없다(옛 판은 마지막
        # 바인딩을 봐 놓쳤고, 앞 둘은 맨 위 판정이 자기를 다시 불러 끝없이 돌았다)
        ("from bot.dart_client import get_dart\nget_dart = get_dart()\n"
         "def f():\n    if not get_dart:\n        return\n",
         [(4, "not get_dart")]),
        ("from bot.dart_client import get_dart\n_D = get_dart()\n"
         "def get_dart():\n    return None\ndef f():\n    if not _D:\n"
         "        return\n", [(6, "not _D")]),
        ("from bot.dart_client import get_dart\n_D = get_dart()\n"
         "get_dart = None\ndef f():\n    if not _D:\n        return\n",
         [(5, "not _D")]),
        ("from bot.dart_client import get_dart\n_D = get_dart()\nif _D:\n"
         "    pass\nelse:\n    log('키 없음')\nget_dart = None\n",
         [(3, "_D → else")]),
        # 맨 위 클래스 본문도 import 시점에 돈다 · 중첩 클래스는 바깥 클래스
        # 본문의 이름을 못 본다(전역으로 간다 — 옛 판은 바깥 클래스 이름으로 읽었다)
        ("from bot.dart_client import get_dart\nclass K:\n    d = get_dart()\n"
         "    def f(self):\n        if not self.d:\n            return\n"
         "def get_dart():\n    return None\n", [(5, "not self.d")]),
        ("from bot.dart_client import get_dart\nclass A:\n    get_dart = None\n"
         "    class B:\n        d = get_dart()\n        def f(self):\n"
         "            if not self.d:\n                return\n",
         [(7, "not self.d")]),
        # 한 줄 여러 문장이라 그 줄에 닿는 것을 못 가르면 옛 규칙 — 마지막 바인딩
        # 으로 가지 않는다(가면 맨 위 판정이 자기를 다시 불러 끝없이 돈다)
        ("from bot.dart_client import get_dart\n_D = get_dart(); x = 1\n"
         "def get_dart():\n    return None\ndef f():\n    if not _D:\n"
         "        return\n", [(6, "not _D")]),
        # 맨 위 바인딩 사이의 설명 문자열은 아무것도 부르지 않는다 · 재정의의 반환
        # 주석·키워드 기본값·인자 주석은 import 시점에 평가된다(호출이 있으면 옛 규칙)
        ("_D = None\n'\'\'설명\'\'\'\n_D = get_dart()\ndef f():\n    if not _D:\n"
         "        return\n", [(5, "not _D")]),
        ("from bot.dart_client import get_dart\ndef get_dart() -> init():\n"
         "    return None\ndef f():\n    d = get_dart()\n    if not d:\n"
         "        return\n", [(6, "not d")]),
        ("from bot.dart_client import get_dart\ndef get_dart(*, x=init()):\n"
         "    return None\ndef f():\n    d = get_dart()\n    if not d:\n"
         "        return\n", [(6, "not d")]),
        ("from bot.dart_client import get_dart\ndef get_dart(x: init()):\n"
         "    return None\ndef f():\n    d = get_dart()\n    if not d:\n"
         "        return\n", [(6, "not d")]),
    ])
    def test_fires(self, src, want):
        assert dead_guards(src) == want, src

    @pytest.mark.parametrize("src", [
        # C3 의 반대 — A 가 공장이어도 C 의 None 이 이긴다(옛 판은 A 를 골랐다)
        "class A:\n    def mk(self):\n        return get_dart()\nclass B(A):\n"
        "    pass\nclass C(A):\n    def mk(self):\n        return None\n"
        "class D(B, C):\n    def f(self):\n        d = self.mk()\n"
        "        if not d:\n            return\n",
        # 가족 밖 클래스 메서드를 클래스로 부르거나 꺼내 두면 self 는 무엇이든 된다
        _C_SELF_D + "class Other:\n    def reset(self):\n        self.d = None\n"
        "def g(c):\n    Other.reset(c)\n",
        _C_SELF_D + "class Other:\n    def reset(self):\n        self.d = None\n"
        "h = Other.reset\n",
        # 가족(믹스인)의 자기 메서드는 증거다
        "class M:\n    def reset(self):\n        self.d = None\nclass C(M):\n"
        "    def __init__(self):\n        self.d = get_dart()\n"
        "    def f(self):\n" + _IF_SELF_D,
        # 마지막 바인딩의 반대 — 마지막이 None · 사이에 호출 · 중첩 바인딩 ·
        # global 로 쓰는 함수(옛 규칙: 바인딩이 모두 실어야)
        "_D = get_dart()\n_D = None\ndef f():\n    if not _D:\n        return\n",
        "_D = None\ninit()\n_D = get_dart()\ndef f():\n    if not _D:\n"
        "        return\n",
        "_D = None\nif X:\n    _D = get_dart()\ndef f():\n    if not _D:\n"
        "        return\n",
        "D = get_dart()\nD = None\ndef reset():\n    global D\n    D = get_dart()\n"
        "def f():\n    if not D:\n        return\n",
        # import 뒤에 재정의한 공장 이름 — 효력 있는 것은 재정의다(옛 판은 부정
        # 판정에서 공장으로 봤다 — 오탐)
        "from bot.dart_client import get_dart\ndef get_dart():\n    return None\n"
        "def f():\n    d = get_dart()\n    if not d:\n        return\n",
        # 장식 별칭의 반대 — 만든 래퍼 · 다른 라이브러리의 cache
        "memo = make_wrapper()\n@memo\ndef mk():\n    return get_dart()\n"
        "def use():\n    d = mk()\n" + _IF_D,
        "from mylib import cache as memo\n@memo\ndef mk():\n"
        "    return get_dart()\ndef use():\n    d = mk()\n" + _IF_D,
        # 같은 블록 도달 정의의 반대 — 마지막이 None(옛 판의 오탐) · 모듈 맨 위 ·
        # while 머리(본문이 다음 바퀴에 닿는다)
        "def f():\n    d = get_dart()\n    d = None\n    if d is None:\n"
        "        return\n",
        "def f():\n    d = get_dart()\n    d = None\n" + _IF_D,
        "d = get_dart()\nd = None\nif d is None:\n    pass\n",
        "def f():\n    d = None\n    while not d:\n        d = get_dart()\n",
        # 맨 위 마지막 바인딩이 클라이언트여도 global 로 None 을 쓰는 함수가 있으면
        # 옛 규칙(바인딩이 모두 실어야)
        "D = None\nD = get_dart()\ndef reset():\n    global D\n    D = None\n"
        "def f():\n    if not D:\n        return\n",
        # 공장 import 뒤에 다른 값(호출 없는 대입)으로 재정의 — 효력 있는 것은 그 값
        "from bot.dart_client import get_dart\nget_dart = other_getter\n"
        "def f():\n    d = get_dart()\n    if not d:\n        return\n",
        # 함수 안의 같은 이름 def 둘 — 뒤의 것이 이긴다
        "def outer():\n    def mk():\n        return get_dart()\n    def mk():\n"
        "        return None\n    d = mk()\n    if not d:\n        return\n",
        # else 판정 — with … as · match 패턴 · try 본문(except 에서) · 반복문의 뒷줄이
        # 그 이름을 다시 묶으면 '언제나' 가 아니다(하나로 정할 수 없다)
        "def f():\n    d = get_dart()\n    with open(x) as d:\n        if d:\n"
        "            work(d)\n        else:\n            log('키 없음')\n",
        "def f(x):\n    d = get_dart()\n    match x:\n        case {'k': d}:\n"
        "            if d:\n                work(d)\n            else:\n"
        "                log('키 없음')\n",
        "def f():\n    d = get_dart()\n    try:\n        d = load()\n"
        "    except Exception:\n        if d:\n            work(d)\n        else:\n"
        "            log('키 없음')\n",
        "def f(xs):\n    d = get_dart()\n    for x in xs:\n        if d:\n"
        "            work(d)\n        else:\n            log('키 없음')\n"
        "        d = load(x)\n",
        # `except … as` · match 캡처(`*d` · `**d`)도 바인딩이다(옛 판은 안 셌다)
        "def f():\n    d = get_dart()\n    try:\n        work()\n"
        "    except Exception as d:\n        pass\n" + _IF_D,
        "def f(x):\n    d = get_dart()\n    match x:\n        case [*d]:\n"
        "            pass\n" + _IF_D,
        "def f(x):\n    d = get_dart()\n    match x:\n        case {**d}:\n"
        "            pass\n" + _IF_D,
        # import 시점의 반대 — 클래스 본문 앞에서 재정의 · 함수 본문은 import 가
        # 끝난 뒤 돈다(마지막 바인딩) · lambda 본문도 나중에 돈다
        "from bot.dart_client import get_dart\ndef get_dart():\n    return None\n"
        "class K:\n    d = get_dart()\n    def f(self):\n"
        "        if not self.d:\n            return\n",
        "from bot.dart_client import get_dart\ndef f():\n    d = get_dart()\n"
        "    if not d:\n        return\ndef get_dart():\n    return None\n",
        "from bot.dart_client import get_dart\nmk = lambda: get_dart()\n"
        "def get_dart():\n    return None\ndef f():\n    d = mk()\n"
        "    if not d:\n        return\n",
        # 맨 위에서 바로 부른 lambda — 무엇을 부를지 따라가지 않는다(놓치는 쪽 ·
        # 옛 판은 여기서 끝없이 돌았다)
        "from bot.dart_client import get_dart\nget_dart = (lambda: get_dart())()\n"
        "def f():\n    if not get_dart:\n        return\n",
        # 맨 위 클래스 본문 뒤의 import 는 그 본문에서 안 보인다(그때는 사용자 함수)
        "def get_dart():\n    return None\nclass K:\n    d = get_dart()\n"
        "    def f(self):\n        if not self.d:\n            return\n"
        "from bot.dart_client import get_dart\n",
        # 그 줄 앞에 묶인 것이 없는 이름 — 뒤의 import 는 그 자리에 없다(import 가
        # NameError 로 멈춘다 · 부정·else 판정 모두)
        "_D = get_dart()\nfrom bot.dart_client import get_dart\ndef f():\n"
        "    if not _D:\n        return\n",
        "_D = get_dart()\nfrom bot.dart_client import get_dart\nif _D:\n    pass\n"
        "else:\n    log('키 없음')\n",
        # 같은 단순문 안의 대입식이 판정보다 먼저 다시 묶는다(else 는 산다)
        "def f():\n    d = get_dart()\n"
        "    ok = (d := load()) and (d.x() if d else log('키 없음'))\n",
        # 서로를 가리키는 장식 별칭 — 끝없이 따라가지 않는다(투명하지 않다)
        "a = b\nb = a\n@a\ndef mk():\n    return get_dart()\ndef use():\n"
        "    d = mk()\n" + _IF_D,
    ])
    def test_spares(self, src):
        assert not dead_guards(src), src

    def test_inconsistent_hierarchy_does_not_crash(self):
        """C3 가 서지 않는 계층(파이썬은 TypeError) — 옛 근사로 둔다."""
        r = scan({"m": "class A:\n    def mk(self):\n        return get_dart()\n"
                  "class B(A):\n    pass\nclass X(A, B):\n    def f(self):\n"
                  "        d = self.mk()\n        if not d:\n            return\n"})
        assert r["hits"] == [("m", 9, "not d")], r

    def test_module_last_binding_sees_other_module_writes(self):
        """다른 모듈이 `pkg.h.D = None` 으로 쓰면 맨 위 마지막 바인딩을 믿지 않는다."""
        r = scan({"pkg.h": "D = None\nD = get_dart()\ndef f():\n    if not D:\n"
                  "        return\n",
                  "pkg.r": "import pkg.h\ndef reset():\n    pkg.h.D = None\n"})
        assert r["hits"] == [] and "pkg.r" in r["built"], r
        r = scan({"pkg.h": "D = None\nD = get_dart()\ndef f():\n    if not D:\n"
                  "        return\n",
                  "pkg.r": "import pkg.other\ndef reset():\n    pkg.other.D = None\n"})
        assert r["hits"] == [("pkg.h", 4, "not D")], r

    def test_duplicate_def_last_wins(self):
        """같은 이름의 def 가 여럿이면 뒤의 것 — 옛 판은 뒤에서부터 훑어 앞의
        정의가 남았다(공장이 아닌 뒤 정의를 공장으로 봤다)."""
        src = ("def mk():\n    return get_dart()\ndef mk():\n    return None\n"
               "def f():\n    d = mk()\n    if not d:\n        return\n")
        assert dead_guards(src) == []
        src2 = ("def mk():\n    return None\ndef mk():\n    return get_dart()\n"
                "def f():\n    d = mk()\n    if not d:\n        return\n")
        assert dead_guards(src2) == [(7, "not d")]


class TestScannerWrites:
    """실수 #430 — #429 가 '못 보는 축' 으로 남긴 **쓰기** 축. 보관소 규칙(모든
    바인딩이 실어야 운반자)은 그 칸·전역을 바꾸는 쓰기를 **모두** 봐야 한다 —
    옛 판은 아래 쓰기를 못 봐 바꾸는 함수가 있어도 판정을 잡았다(오탐).

    · 다른 모듈의 쓰기 — `m.D = …` · `m.CTX['d'] = …` · `from m import CTX`
      뒤 `CTX['d'] = …`(같은 객체) · `m.NS.d = …`.
    · 컨테이너를 바꾸는 메서드 — `update`·`setdefault`·`__setitem__` 은 그 칸에
      쓴다 · `pop`·`del`·`__delitem__` 은 그 칸을 없앤다 · `clear`·`popitem`·
      모르는 `update`·`insert`·`remove`·`sort`·`reverse` 는 어느 칸인지 모른다 ·
      `append`·`extend` 는 끝에 붙인다(리터럴 길이 밖의 칸 후보 · 음수 칸은 모른다).
    · 식별자가 아닌 문자열 칸 · 음수 칸(쓰기는 어느 칸과 겹칠지 모른다) · 변수 칸.
    · 이름공간 — `globals()['D']` · `vars(m)['D']` · `m.__dict__['D']` ·
      `sys.modules['m'].D` · `sys.modules[__name__].D` ·
      `importlib.import_module('m').D` 는 그 모듈 전역이다."""

    @pytest.mark.parametrize("src, want", [
        # 식별자가 아닌 문자열 칸 · 음수 칸(리터럴 그대로) — 이제 열쇠가 된다
        ("CTX = {'api-key': get_dart()}\ndef f():\n    if not CTX['api-key']:\n"
         "        return\n", [(3, "not CTX['api-key']")]),
        ("CTX = {'a[b': get_dart()}\ndef f():\n    if not CTX['a[b']:\n"
         "        return\n", [(3, "not CTX['a[b']")]),
        ("L = [None, get_dart()]\ndef f():\n    if not L[-1]:\n        return\n",
         [(3, "not L[-1]")]),
        # 메서드로 채운 칸 — 맨 위 · 지역
        ("CTX = {}\nCTX.update(d=get_dart())\ndef f():\n    if not CTX['d']:\n"
         "        return\n", [(4, "not CTX['d']")]),
        ("CTX = {}\nCTX.update({'d': get_dart()})\ndef f():\n"
         "    if not CTX['d']:\n        return\n", [(4, "not CTX['d']")]),
        ("CTX = {}\nCTX.setdefault('d', get_dart())\ndef f():\n"
         "    if not CTX['d']:\n        return\n", [(4, "not CTX['d']")]),
        ("def f():\n    ctx = {}\n    ctx.update(d=get_dart())\n"
         "    if not ctx['d']:\n        return\n", [(4, "not ctx['d']")]),
        # 끝에 붙인 값 — 리터럴 길이 밖의 칸 후보(모두 실으면 잡는다)
        ("L = []\nL.append(get_dart())\ndef f():\n    if not L[0]:\n"
         "        return\n", [(4, "not L[0]")]),
        ("def f():\n    xs = []\n    xs.append(get_dart())\n    if not xs[0]:\n"
         "        return\n", [(4, "not xs[0]")]),
        # 끝에 붙여도 리터럴 길이 안의 칸은 그대로다
        ("L = [get_dart()]\nL.append(None)\ndef f():\n    if not L[0]:\n"
         "        return\n", [(4, "not L[0]")]),
        # 다른 칸에 쓰는 것은 막지 않는다(반대 증거)
        ("CTX = {'d': get_dart()}\ndef reset():\n    CTX.update(e=None)\n"
         "    CTX.pop('e')\n    del CTX['e']\ndef f():\n    if not CTX['d']:\n"
         "        return\n", [(7, "not CTX['d']")]),
        # 이름공간으로 쓴 전역 — 이름 바인딩이 없어도 그 전역이다
        ("globals()['D'] = get_dart()\ndef f():\n    if not D:\n        return\n",
         [(3, "not D")]),
        ("import sys\nsys.modules[__name__].D = get_dart()\ndef f():\n"
         "    if not D:\n        return\n", [(4, "not D")]),
        ("globals().update(D=get_dart())\ndef f():\n    if not D:\n"
         "        return\n", [(3, "not D")]),
        # 리터럴 extend 는 그 값들이 끝에 붙는다
        ("L = []\nL.extend([get_dart()])\ndef f():\n    if not L[0]:\n"
         "        return\n", [(4, "not L[0]")]),
        # 속성과 칸은 다른 자리다 — 같은 이름 속성에 쓰는 것은 그 칸을 막지 않는다
        ("CTX = {'d': get_dart()}\ndef reset():\n    CTX.d = None\ndef f():\n"
         "    if not CTX['d']:\n        return\n", [(5, "not CTX['d']")]),
    ])
    def test_fires(self, src, want):
        assert dead_guards(src) == want, src

    @pytest.mark.parametrize("src", [
        # 그 칸을 바꾸는 메서드·del · 어느 칸인지 모르는 쓰기
        "CTX = {'d': get_dart()}\ndef reset():\n    CTX.update(d=None)\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset():\n    CTX.update({'d': None})\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset():\n    CTX.pop('d')\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset():\n    del CTX['d']\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset():\n    CTX.__delitem__('d')\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset():\n    CTX.__setitem__('d', None)\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset():\n    CTX.clear()\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset(other):\n    CTX.update(other)\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset(**kw):\n    CTX.update(**kw)\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset(k):\n    CTX[k] = None\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        "CTX = {'d': get_dart()}\ndef reset(k):\n    CTX.setdefault(k, None)\n"
        "def f():\n    if not CTX['d']:\n        return\n",
        # 음수 칸 쓰기는 어느 칸과 겹칠지 모른다 · 양수 칸 쓰기는 음수 읽기와 겹친다
        "L = [get_dart()]\ndef reset():\n    L[-1] = None\n"
        "def f():\n    if not L[0]:\n        return\n",
        "L = [get_dart()]\ndef reset():\n    L[0] = None\n"
        "def f():\n    if not L[-1]:\n        return\n",
        # 칸이 밀리는 메서드 · 끝이 바뀌면 음수 칸은 모른다
        "L = [get_dart()]\ndef reset():\n    L.insert(0, None)\n"
        "def f():\n    if not L[0]:\n        return\n",
        "L = [get_dart()]\ndef reset():\n    L.append(None)\n"
        "def f():\n    if not L[-1]:\n        return\n",
        "L = []\nL.append(get_dart())\nL.append(None)\ndef f():\n"
        "    if not L[1]:\n        return\n",
        # 이름공간으로 바꾸는 전역 — 같은 모듈의 globals() · 모르는 이름
        "D = get_dart()\ndef reset():\n    globals()['D'] = None\n"
        "def f():\n    if not D:\n        return\n",
        "D = get_dart()\ndef reset():\n    globals().update(D=None)\n"
        "def f():\n    if not D:\n        return\n",
        "D = get_dart()\ndef reset(name):\n    globals()[name] = None\n"
        "def f():\n    if not D:\n        return\n",
        "import sys\nD = get_dart()\ndef reset():\n"
        "    setattr(sys.modules[__name__], 'D', None)\ndef f():\n    if not D:\n"
        "        return\n",
        "import sys\nD = get_dart()\ndef reset(n):\n"
        "    setattr(sys.modules[__name__], n, None)\ndef f():\n    if not D:\n"
        "        return\n",
        "D = get_dart()\ndef reset():\n    del globals()['D']\n"
        "def f():\n    if not D:\n        return\n",
        # else 판정도 같은 쓰기를 본다(모듈 전역을 이름공간으로 바꾼다)
        "D = get_dart()\ndef reset():\n    globals()['D'] = None\ndef f():\n"
        "    if D:\n        work()\n    else:\n        log('키 없음')\n",
        "import sys\nD = get_dart()\ndef reset():\n"
        "    sys.modules[__name__].D = None\ndef f():\n    if D:\n        work()\n"
        "    else:\n        log('키 없음')\n",
        # 모르는 객체의 이름공간 쓰기는 아무 속성이나로 본다(else 판정)
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "    def f(self):\n        if self.d:\n            work()\n        else:\n"
        "            log('키 없음')\ndef g(o):\n    vars(make(o))['d'] = None\n",
        # 모듈 객체를 돌려주는 함수는 공장이 아니다(끝없이 묻지 않는다)
        "import importlib\nfrom bot.dart_client import get_dart\ndef mk():\n"
        "    return importlib.import_module('pkg.h')\ndef f():\n    m = mk()\n"
        "    if not m:\n        return\n",
        # 리스트 pop(번호)은 뒤 칸을 당긴다 — 그 번호 칸만의 쓰기가 아니다
        "L = [None, get_dart(), None]\ndef reset():\n    L.pop(0)\ndef f():\n"
        "    if not L[1]:\n        return\n",
        # 모르는 객체의 이름공간을 메서드로 바꾸는 것도 아무 속성이나(else 판정)
        "class C:\n    def __init__(self):\n        self.d = get_dart()\n"
        "    def f(self):\n        if self.d:\n            work()\n        else:\n"
        "            log('키 없음')\ndef g(o):\n    vars(make(o)).update(d=None)\n",
    ])
    def test_spares(self, src):
        assert not dead_guards(src), src

    def test_namespace_global_reaches_importers(self):
        """`globals()['D'] = get_dart()` 로만 묶인 전역도 다른 모듈이 받아 간다 —
        그 이름을 쓰는 모듈을 읽는다."""
        r = scan({"pkg.h": "globals()['D'] = get_dart()\n",
                  "pkg.u": "from pkg.h import D\ndef f():\n    if not D:\n"
                  "        return\n"})
        assert r["hits"] == [("pkg.u", 3, "not D")], r

    @pytest.mark.parametrize("writer", [
        "import pkg.h\ndef reset():\n    pkg.h.D = None\n",
        "from pkg import h\ndef reset():\n    h.D = None\n",
        "import pkg.h\ndef reset():\n    setattr(pkg.h, 'D', None)\n",
        "import pkg.h\ndef reset():\n    vars(pkg.h)['D'] = None\n",
        "import pkg.h\ndef reset():\n    pkg.h.__dict__['D'] = None\n",
        "import sys\ndef reset():\n    sys.modules['pkg.h'].D = None\n",
        "import importlib\ndef reset():\n"
        "    importlib.import_module('pkg.h').D = None\n",
        "from importlib import import_module\ndef reset():\n"
        "    import_module('pkg.h').D = None\n",
        "import pkg.h\ndef reset():\n    vars(pkg.h).update(D=None)\n",
    ])
    def test_spares_module_global_written_elsewhere(self, writer):
        """다른 모듈이 그 전역을 바꾼다 — 부정 판정도 그 쓰기를 증거로 본다(옛
        판은 else 판정만 막고 부정 판정은 잡았다 — 오탐)."""
        r = scan({"pkg.h": "D = get_dart()\ndef f():\n    if not D:\n"
                  "        return\n", "pkg.r": writer})
        assert r["hits"] == [] and "pkg.r" in r["built"], r

    @pytest.mark.parametrize("writer", [
        "import pkg.h\ndef reset():\n    pkg.h.CTX['d'] = None\n",
        "from pkg.h import CTX\ndef reset():\n    CTX['d'] = None\n",
        "from pkg.h import CTX\ndef reset():\n    CTX.pop('d')\n",
        "from pkg.h import CTX\ndef reset():\n    CTX.clear()\n",
        "def reset():\n    from pkg.h import CTX\n    CTX['d'] = None\n",
        "import sys\ndef reset():\n    sys.modules['pkg.h'].CTX['d'] = None\n",
    ])
    def test_spares_slot_written_elsewhere(self, writer):
        r = scan({"pkg.h": "CTX = {'d': get_dart()}\ndef f():\n"
                  "    if not CTX['d']:\n        return\n", "pkg.r": writer})
        assert r["hits"] == [] and "pkg.r" in r["built"], r

    def test_attr_and_other_slots_written_elsewhere(self):
        """속성 칸도 같다 · 다른 칸·다른 모듈의 같은 이름에 쓰는 것은 막지 않는다
        (반대 증거)."""
        h = ("class N:\n    pass\nNS = N()\nNS.d = get_dart()\ndef f():\n"
             "    if not NS.d:\n        return\n")
        r = scan({"pkg.h": h, "pkg.r": "import pkg.h\ndef reset():\n"
                  "    pkg.h.NS.d = None\n"})
        assert r["hits"] == [], r
        ctx = ("CTX = {'d': get_dart()}\ndef f():\n    if not CTX['d']:\n"
               "        return\n")
        for w in ("import pkg.h\ndef reset():\n    pkg.h.CTX['e'] = None\n",
                  "import pkg.other\ndef reset():\n    pkg.other.CTX['d'] = None\n",
                  "from pkg.other import CTX\ndef reset():\n    CTX['d'] = None\n"):
            r = scan({"pkg.h": ctx, "pkg.r": w})
            assert r["hits"] == [("pkg.h", 3, "not CTX['d']")], (w, r)
        r = scan({"pkg.h": "D = get_dart()\ndef f():\n    if not D:\n"
                  "        return\n",
                  "pkg.r": "import pkg.other\ndef reset():\n    pkg.other.D = None\n"})
        assert r["hits"] == [("pkg.h", 3, "not D")], r

    def test_cross_module_write_reads(self):
        """`sys.modules['m'].D` · `importlib.import_module('m').D` 로 **읽는** 것도
        그 모듈 전역이다."""
        for u in ("import sys\ndef g():\n    if not sys.modules['pkg.h'].D:\n"
                  "        return\n",
                  "import importlib\ndef g():\n"
                  "    if not importlib.import_module('pkg.h').D:\n        return\n"):
            r = scan({"pkg.h": "D = get_dart()\n", "pkg.u": u})
            assert r["hits"] == [("pkg.u", 3, u.split("if ")[1].split(":")[0])], r

    def test_split_round_trips_awkward_keys(self):
        """칸 열쇠는 repr 로 적는다 — 문자열 안의 괄호·점·물음표에 속지 않는다."""
        for k, want in (("CTX['a[b']", ("CTX", "item", "a[b")),
                        ("CTX['a]']", ("CTX", "item", "a]")),
                        ("CTX['x.y']", ("CTX", "item", "x.y")),
                        ("CTX['what?']", ("CTX", "item", "what?")),
                        ("CTX['a[b']?", ("CTX", "get", "a[b")),
                        ("L[-1]", ("L", "item", -1)),
                        ("a['k'].b", ("a['k']", "attr", "b")),
                        ("@pkg:h.D", ("@pkg:h", "attr", "D"))):
            assert _split(k) == want, (k, _split(k))


_CB = "def cb(d):\n    if not d:\n        return\n"
_C_D = "class C:\n    def __init__(self):\n        self.d = get_dart()\n"


class TestScannerCalls:
    """실수 #430 — #429 가 '못 보는 축' 으로 남긴 **호출** 축. 클라이언트가 그
    함수의 인자·반환·속성으로 흘러가는 길을 더 따라간다.

    · 콜백 등록 — `atexit.register` · `weakref.finalize` · `add_reader`·
      `add_writer`·`add_signal_handler` · `ExitStack.callback` ·
      `sched.scheduler.enter`/`enterabs` · `Pool.apply_async`. 이름이 흔한 것은
      **받는 쪽**을 가린다(아무 객체의 `register`·`callback`·`enter` 를 콜백
      등록으로 보면 오탐이다).
    · `Thread`/`Process` 의 **위치** target · 이름으로 넘긴 `args=`·`kwargs=`
      변수(같은 블록에서 닿는 리터럴) · `*args[1:]` 슬라이스 · `a, b = args` 풀기.
    · lambda 에 담은 공장 · `property`·`cached_property` 로 읽는 클라이언트 ·
      `await` 한 결과.
    · 주석 없는 인자의 속성 — 부르는 쪽이 넘긴 인스턴스의 클래스로(부정 판정은
      갈래 하나라도) · `Optional[C]` · `Union[C, None]` · `C | None` 주석."""

    @pytest.mark.parametrize("src, want", [
        # ── 콜백 등록 ──
        ("import atexit\n" + _CB + "def g():\n    atexit.register(cb, get_dart())\n",
         [(3, "not d")]),
        ("from atexit import register\n" + _CB
         + "def g():\n    register(cb, d=get_dart())\n", [(3, "not d")]),
        ("import weakref\n" + _CB
         + "def g(o):\n    weakref.finalize(o, cb, get_dart())\n", [(3, "not d")]),
        (_CB + "def g(loop, fd):\n    loop.add_reader(fd, cb, get_dart())\n",
         [(2, "not d")]),
        (_CB + "def g(loop, sig):\n    loop.add_signal_handler(sig, cb, get_dart())\n",
         [(2, "not d")]),
        ("import contextlib\n" + _CB + "def g():\n"
         "    with contextlib.ExitStack() as st:\n        st.callback(cb, get_dart())\n",
         [(3, "not d")]),
        ("from contextlib import ExitStack\n" + _CB + "def g():\n"
         "    st = ExitStack()\n    st.callback(cb, get_dart())\n", [(3, "not d")]),
        ("import sched, time\n" + _CB + "def g():\n"
         "    s = sched.scheduler(time.time, time.sleep)\n"
         "    s.enter(1, 1, cb, (get_dart(),))\n", [(3, "not d")]),
        ("import sched, time\n" + _CB + "def g():\n"
         "    s = sched.scheduler(time.time, time.sleep)\n"
         "    s.enterabs(1, 1, cb, kwargs={'d': get_dart()})\n", [(3, "not d")]),
        ("from multiprocessing import Pool\n" + _CB + "def g():\n"
         "    with Pool() as p:\n        p.apply_async(cb, args=(get_dart(),))\n",
         [(3, "not d")]),
        ("import multiprocessing\n" + _CB + "def g():\n"
         "    p = multiprocessing.Pool()\n    p.apply_async(cb, (), {'d': get_dart()})\n",
         [(3, "not d")]),
        # ── Thread 위치 target · 이름으로 넘긴 args·kwargs ──
        ("import threading\n" + _CB + "def g():\n"
         "    threading.Thread(None, cb, None, (get_dart(),)).start()\n",
         [(3, "not d")]),
        ("import threading\n" + _CB + "def g():\n    a = (get_dart(),)\n"
         "    threading.Thread(target=cb, args=a).start()\n", [(3, "not d")]),
        ("import threading\n" + _CB + "def g():\n    kw = {'d': get_dart()}\n"
         "    threading.Thread(target=cb, kwargs=kw).start()\n", [(3, "not d")]),
        # ── 받은 *args 의 슬라이스 · 풀기 · 지역 리터럴 펼치기 ──
        (_CB + "def relay(*args):\n    cb(*args[1:])\ndef g():\n"
         "    relay(None, get_dart())\n", [(2, "not d")]),
        ("def f(*args):\n    a, b = args\n    if not b:\n        return\n"
         "def g():\n    f(None, get_dart())\n", [(3, "not b")]),
        ("def f():\n    a, b = None, get_dart()\n    if not b:\n        return\n",
         [(3, "not b")]),
        (_CB + "def g():\n    a = (get_dart(),)\n    cb(*a)\n", [(2, "not d")]),
        (_CB + "def g():\n    kw = {'d': get_dart()}\n    cb(**kw)\n",
         [(2, "not d")]),
        # 끝·걸음이 있는 슬라이스 — 남는 칸만 당겨진다
        (_CB + "def relay(*args):\n    cb(*args[1:2])\ndef g():\n"
         "    relay(None, get_dart())\n", [(2, "not d")]),
        (_CB + "def relay(*args):\n    cb(*args[1::2])\ndef g():\n"
         "    relay(None, get_dart(), None)\n", [(2, "not d")]),
        # 별표 하나 낀 풀기 — 앞은 v[i] · 리터럴이면 뒤는 v[-k]
        ("def f(*args):\n    a, *b = args\n    if not a:\n        return\n"
         "def g():\n    f(get_dart(), None)\n", [(3, "not a")]),
        ("def f():\n    *a, b = None, get_dart()\n    if not b:\n        return\n",
         [(3, "not b")]),
        # 등록 대상을 그 자리에서 만든 객체(`ExitStack().callback`)
        ("import contextlib\n" + _CB + "def g():\n"
         "    contextlib.ExitStack().callback(cb, get_dart())\n", [(3, "not d")]),
        # ── lambda 공장 ──
        ("mk = lambda: get_dart()\ndef f():\n    d = mk()\n    if not d:\n"
         "        return\n", [(4, "not d")]),
        ("def f():\n    mk = lambda: get_dart()\n    d = mk()\n    if not d:\n"
         "        return\n", [(4, "not d")]),
        ("mk = lambda: get_dart()\ndef f():\n    d = mk()\n" + _IF_D,
         [(4, "d → else")]),
        # ── property · cached_property ──
        # if/else 로 고른 게터 둘 — 둘 다 클라이언트를 주면 그 속성은 클라이언트
        ("import sys\nclass C:\n    if sys.platform:\n        @property\n"
         "        def dart(self):\n            return get_dart()\n    else:\n"
         "        @property\n        def dart(self):\n            return get_dart()\n"
         "    def f(self):\n        if not self.dart:\n            return\n",
         [(12, "not self.dart")]),
        ("class C:\n    @property\n    def dart(self):\n        return get_dart()\n"
         "    def f(self):\n        if not self.dart:\n            return\n",
         [(6, "not self.dart")]),
        ("class C:\n    @property\n    def dart(self):\n        return get_dart()\n"
         "def f():\n    c = C()\n    if not c.dart:\n        return\n",
         [(7, "not c.dart")]),
        ("import functools\nclass C:\n    @functools.cached_property\n"
         "    def dart(self):\n        return get_dart()\n    def f(self):\n"
         "        if self.dart:\n            work()\n        else:\n"
         "            log('키 없음')\n", [(7, "self.dart → else")]),
        # ── await ──
        ("async def mk():\n    return get_dart()\nasync def f():\n"
         "    d = await mk()\n    if not d:\n        return\n", [(5, "not d")]),
        ("async def mk():\n    return get_dart()\nasync def f():\n"
         "    d = await mk()\n" + _IF_D, [(5, "d → else")]),
        # ── 주석 없는 인자의 속성(부르는 쪽 인스턴스) · Optional 주석 ──
        (_C_D + "def f(o):\n    if not o.d:\n        return\ndef g():\n    f(C())\n",
         [(5, "not o.d")]),
        (_C_D + "def f(o):\n    if not o.d:\n        return\ndef g():\n"
         "    c = C()\n    f(o=c)\n", [(5, "not o.d")]),
        ("from typing import Optional\n" + _C_D
         + "def f(o: Optional[C]):\n    if not o.d:\n        return\n",
         [(6, "not o.d")]),
        (_C_D + "def f(o: 'Optional[C]'):\n    if not o.d:\n        return\n",
         [(5, "not o.d")]),
        (_C_D + "def f(o: C | None):\n    if not o.d:\n        return\n",
         [(5, "not o.d")]),
        ("from typing import Union\n" + _C_D
         + "def f(o: Union[C, None]):\n    if not o.d:\n        return\n",
         [(6, "not o.d")]),
        # 메서드로 받은 인자 — self 를 건너뛴 자리에 클래스를 단다
        (_C_D + "class K:\n    def m(self, o):\n        if not o.d:\n"
         "            return\ndef g():\n    k = K()\n    k.m(C())\n",
         [(6, "not o.d")]),
        # 인자 클래스가 늦게 붙어도(부르는 함수가 먼저 나온다) 다시 판정한다
        (_C_D + "def g():\n    f(C())\ndef f(o):\n    use(o.d)\ndef use(d):\n"
         "    if not d:\n        return\n", [(9, "not d")]),
    ])
    def test_fires(self, src, want):
        assert dead_guards(src) == want, src

    @pytest.mark.parametrize("src", [
        # 이름이 흔한 등록 — 받는 쪽이 그것이 아니면 콜백 호출이 아니다
        _CB + "def g(app):\n    app.register(cb, get_dart())\n",
        _CB + "def g(obj):\n    obj.callback(cb, get_dart())\n",
        _CB + "def g(x):\n    x.enter(1, 1, cb, (get_dart(),))\n",
        _CB + "def g(x):\n    x.apply_async(cb, (get_dart(),))\n",
        _CB + "def g(o):\n    finalize(o, cb, get_dart())\n",
        # 위치 target 앞에 별표가 있으면 자리를 모른다
        "import threading\n" + _CB + "def g(xs):\n"
        "    threading.Thread(*xs, cb, None, (get_dart(),)).start()\n",
        # 슬라이스 시작보다 앞 칸은 넘어가지 않는다
        _CB + "def relay(*args):\n    cb(*args[1:])\ndef g():\n"
        "    relay(get_dart(), None)\n",
        # lambda 공장의 반대 — 갈래에 None 이 있으면 else 는 산다
        "mk = lambda k: get_dart() if k else None\ndef f(k):\n    d = mk(k)\n"
        + _IF_D,
        # property 에 setter 가 있으면 무엇이 들어올지 모른다
        "class C:\n    @property\n    def dart(self):\n        return get_dart()\n"
        "    @dart.setter\n    def dart(self, v):\n        self._d = v\n"
        "    def f(self):\n        if not self.dart:\n            return\n",
        # 끝·걸음 밖의 칸은 넘어가지 않는다
        "def cb(x, d=None):\n    if not d:\n        return\n"
        "def relay(*args):\n    cb(*args[0:1])\ndef g():\n"
        "    relay(None, get_dart())\n",
        # 음수 시작은 길이를 몰라 어느 칸인지 모른다
        "def cb(x, d=None):\n    if not d:\n        return\n"
        "def relay(*args):\n    cb(*args[-1:])\ndef g():\n"
        "    relay(get_dart(), None)\n",
        _CB + "def relay(*args):\n    cb(*args[0::2])\ndef g():\n"
        "    relay(None, get_dart(), None)\n",
        # 별표 뒤 자리는 길이를 모르는 받은 묶음에선 모른다 · 리터럴이면 그 칸
        "def f(*args):\n    *a, b = args\n    if not b:\n        return\n"
        "def g():\n    f(None, get_dart(), None)\n",
        "def f():\n    *a, b = get_dart(), None\n    if not b:\n        return\n",
        # 길이가 안 맞는 리터럴 풀기는 자리를 모른다(실행하면 ValueError)
        "def f():\n    a, b = (None, get_dart(), None)\n    if not b:\n        return\n",
        # 갈래 하나의 게터가 None — 보관소 규칙(바인딩 전부)으로 판정 안 함
        "import sys\nclass C:\n    if sys.platform:\n        @property\n"
        "        def dart(self):\n            return get_dart()\n    else:\n"
        "        @property\n        def dart(self):\n            return None\n"
        "    def f(self):\n        if not self.dart:\n            return\n",
        # 겹친 장식 · 모르는 장식 — 그 값을 무엇이 감싸는지 모른다
        "class C:\n    @wrap\n    @property\n    def dart(self):\n"
        "        return get_dart()\n    def f(self):\n        if not self.dart:\n"
        "            return\n",
        "class C:\n    @weird\n    def dart(self):\n        return get_dart()\n"
        "    def f(self):\n        if not self.dart:\n            return\n",
        # 클래스가 둘 이상인 Union — 어느 객체인지 모른다
        "from typing import Union\n" + _C_D + "class E:\n    def __init__(self):\n"
        "        self.d = None\ndef f(o: Union[C, E, None]):\n    if not o.d:\n"
        "        return\n",
        # 이름 없는 lambda 공장 — 그 함수가 돌려주는 것은 map 객체다
        "def f(xs):\n    ys = map(lambda x: get_dart(), xs)\n    return ys\n"
        "def g(xs):\n    d = f(xs)\n    if not d:\n        return\n",
        # 다른 클래스 인스턴스만 넘어온다
        _C_D + "class D:\n    def __init__(self):\n        self.d = None\n"
        "def f(o):\n    if not o.d:\n        return\ndef g():\n    f(D())\n",
    ])
    def test_spares(self, src):
        assert not dead_guards(src), src


_ELSE = "    else:\n        log('키 없음')\n"
_ELSE2 = "        else:\n            log('키 없음')\n"


class TestScannerElseAndCycles:
    """실수 #430 — #429 가 '못 보는 축' 으로 남긴 **else·순환** 축.

    · `A or B` 판정의 else(한 항이라도 언제나 클라이언트) · `A and B`(모든 항) ·
      판정 밖의 `d or 기본값`(앞 항이 언제나 클라이언트면 뒤 항은 안 돈다).
    · 지역 컨테이너의 칸 · 지역 인스턴스의 속성 else — 그 이름이 함수 밖으로
      새어 나가지 않을 때만(넘김·돌려줌·별칭·담음이 없고, 쓰기는 추적된다).
    · `.get(k, 기본값)` — 없는 칸이면 기본값이 온다(부정 판정·else 둘 다).
    · 닫힌 세계 인자 else — 함수 안에서 정의해 이름을 곧바로 부르는 데에만
      쓰면 부르는 곳이 전부다(장식 없음 · 다시 묶지 않음 · 별표 없음).
    · 보관소끼리 서로 옮겨 담는 순환 — 최소 고정점이 '아니다' 로 끝낸 순환의
      뿌리를 참으로 가정해 다시 잰다(E-게이트 최대 고정점 — 바닥에 닿아야)."""

    @pytest.mark.parametrize("src, want", [
        # ── or / and 판정 · d or 기본값 ──
        ("def f(x):\n    d = get_dart()\n    if d or x:\n        work()\n" + _ELSE,
         [(3, "d or x → else")]),
        ("def f():\n    d = get_dart()\n    e = get_dart()\n    if d and e:\n"
         "        work()\n" + _ELSE, [(4, "d and e → else")]),
        ("def f():\n    d = get_dart() or make()\n    return d\n",
         [(2, "get_dart() or make() → else")]),
        # ── 지역 컨테이너의 칸 · 지역 인스턴스의 속성 ──
        ("def f():\n    ctx = {'d': get_dart()}\n    if ctx['d']:\n        work()\n"
         + _ELSE, [(3, "ctx['d'] → else")]),
        ("def f():\n    L = [get_dart()]\n    if L[0]:\n        work()\n" + _ELSE,
         [(3, "L[0] → else")]),
        (_C_D + "def f():\n    o = C()\n    if o.d:\n        work()\n" + _ELSE,
         [(6, "o.d → else")]),
        # ── .get 기본값 ──
        ("def f():\n    ctx = {}\n    if ctx.get('d', get_dart()):\n        work()\n"
         + _ELSE, [(3, "ctx.get('d', get_dart()) → else")]),
        ("def f():\n    ctx = {}\n    if not ctx.get('d', get_dart()):\n        return\n",
         [(3, "not ctx.get('d', get_dart())")]),
        ("CTX = {}\ndef f():\n    if not CTX.get('d', get_dart()):\n        return\n",
         [(3, "not CTX.get('d', get_dart())")]),
        ("CTX = {}\ndef f():\n    d = CTX.get('d', get_dart())\n    if not d:\n"
         "        return\n", [(4, "not d")]),
        # ── 닫힌 세계 인자 ──
        ("def outer():\n    def use(d):\n        if d:\n            work()\n" + _ELSE2
         + "    use(get_dart())\n", [(3, "d → else")]),
        ("def outer():\n    def use(d=get_dart()):\n        if d:\n            work()\n"
         + _ELSE2 + "    use()\n", [(3, "d → else")]),
        ("def outer():\n    def use(x, d=None):\n        if d:\n            work()\n"
         + _ELSE2 + "    use(1, d=get_dart())\n", [(3, "d → else")]),
        (_C_D + "def outer():\n    def use(o):\n        if o.d:\n            work()\n"
         + _ELSE2 + "    use(C())\n", [(6, "o.d → else")]),
        # ── 펼친 dict — 마지막 펼침 뒤에 적은 칸은 무엇이 펼쳐지든 그 값 ──
        ("def f(base):\n    ctx = {**base, 'd': get_dart()}\n    if not ctx['d']:\n"
         "        return\n", [(3, "not ctx['d']")]),
        ("def f():\n    ctx = {'d': None, 'd': get_dart()}\n    if not ctx['d']:\n"
         "        return\n", [(3, "not ctx['d']")]),
        ("def outer(a):\n    def use(d=None):\n        if not d:\n            return\n"
         "    use(**{**a, 'd': get_dart()})\n", [(3, "not d")]),
        # ── 순환 — 바닥(공장 호출)에 닿는 서로 옮겨 담기 ──
        ("def wa(c):\n    global A\n    A = B\ndef wb(c):\n    global B\n    B = A\n"
         "def wb2(c):\n    global B\n    B = get_dart()\ndef r():\n    if not A:\n"
         "        return\n", [(11, "not A")]),
        ("def w0(c):\n    global G\n    G = G\ndef w1(c):\n    global G\n"
         "    G = get_dart()\ndef r():\n    if not G:\n        return\n",
         [(8, "not G")]),
        # 자기 자신을 다시 담는 전역 — 바닥(맨 위 공장 호출)에 닿아 언제나
        # 클라이언트다. 그 줄의 `or` 꼬리도 죽었다
        ("_D = get_dart()\ndef f():\n    global _D\n    _D = _D or get_dart()\n"
         "def g():\n    if _D:\n        work()\n" + _ELSE,
         [(4, "_D or get_dart() → else"), (6, "_D → else")]),
        # 앞선 대입(첫 규칙)이 가정한 참에만 기대도 보관소 규칙(나머지)을 마저
        # 재야 바닥이 모인다 — `G1 = None or G1` 은 G1 가정에만 기대지만 G1 의
        # 다른 바인딩 G4 → G2 → 공장이 바닥이다. 첫 규칙에서 단락하면 G3 가
        # 바닥 없는 순환으로 기각된다(차분 퍼징이 찾은 반례 — 뮤테이션 D28)
        ("def w3(c):\n    global G0, G1, G3\n    G1 = None or G1\n    G3 = G1\n"
         "    G1 = G4\ndef w4(c):\n    global G0, G2, G4\n    G4 = G2\n"
         "    G0 = G3\n    G2 = get_dart()\n    if not G3:\n        return\n",
         [(11, "not G3")]),
    ])
    def test_fires(self, src, want):
        assert dead_guards(src) == want, src

    @pytest.mark.parametrize("src", [
        # and 는 모든 항이 언제나 클라이언트여야
        "def f(x):\n    d = get_dart()\n    if d and x:\n        work()\n" + _ELSE,
        # 기본값만 두는 or 꼬리 · 앞 항을 모르는 or
        "def f():\n    d = get_dart() or None\n    return d\n",
        "def f(x):\n    d = x or make()\n    return d\n",
        # 새어 나가는 지역 컨테이너 — 넘김 · 별칭 · 돌려줌
        "def f():\n    ctx = {'d': get_dart()}\n    g(ctx)\n    if ctx['d']:\n"
        "        work()\n" + _ELSE,
        "def f():\n    ctx = {'d': get_dart()}\n    c2 = ctx\n    c2['d'] = None\n"
        "    if ctx['d']:\n        work()\n" + _ELSE,
        "def f():\n    ctx = {'d': get_dart()}\n    if ctx['d']:\n        work()\n"
        + _ELSE + "    return ctx\n",
        # 추적되는 쓰기 — 칸 쓰기 · 바꾸는 메서드
        "def f():\n    ctx = {'d': get_dart()}\n    ctx['d'] = None\n    if ctx['d']:\n"
        "        work()\n" + _ELSE,
        "def f():\n    L = [get_dart()]\n    L.insert(0, None)\n    if L[0]:\n"
        "        work()\n" + _ELSE,
        # 없는 칸은 None — 기본값이 없거나 None
        "def f():\n    ctx = {}\n    if ctx.get('d'):\n        work()\n" + _ELSE,
        "CTX = {}\ndef f():\n    if not CTX.get('d', None):\n        return\n",
        # 칸이 있으면 기본값은 안 온다 — 그 칸이 None
        "CTX = {'d': None}\ndef f():\n    if not CTX.get('d', get_dart()):\n"
        "        return\n",
        # 객체 속성을 다른 곳에서 바꾼다
        _C_D + "def f():\n    o = C()\n    o.d = None\n    if o.d:\n        work()\n"
        + _ELSE,
        _C_D + "def r(x):\n    x.d = None\ndef f():\n    o = C()\n    if o.d:\n"
        "        work()\n" + _ELSE,
        # 닫힌 세계가 아니다 — 다른 값도 넘김 · 이름이 새어 나감 · 맨 위 함수 ·
        # 별표 · 장식
        "def outer(x):\n    def use(d):\n        if d:\n            work()\n" + _ELSE2
        + "    use(get_dart())\n    use(x)\n",
        "def outer():\n    def use(d):\n        if d:\n            work()\n" + _ELSE2
        + "    use(get_dart())\n    return use\n",
        "def use(d):\n    if d:\n        work()\n" + _ELSE + "def g():\n    use(get_dart())\n",
        "def outer(a):\n    def use(d):\n        if d:\n            work()\n" + _ELSE2
        + "    use(*a)\n",
        "def outer():\n    @wrap\n    def use(d):\n        if d:\n            work()\n"
        + _ELSE2 + "    use(get_dart())\n",
        # 모르는 메서드로 꺼낸 지역 컨테이너는 새어 나갔다고 본다
        "def f():\n    ctx = {'d': get_dart()}\n    ctx.frob()\n    if ctx['d']:\n"
        "        work()\n" + _ELSE,
        # 보관소(모듈 전역)의 칸 else 는 보지 않는다 — 읽지 않은 모듈이 바꿀 수 있다
        "CTX = {'d': get_dart()}\ndef f():\n    if CTX['d']:\n        work()\n" + _ELSE,
        # 기본값이 클라이언트가 아니면 없는 칸은 실리지 않는다
        "CTX = {}\ndef f():\n    if not CTX.get('d', make()):\n        return\n",
        # 닫힌 세계 인자의 속성 — 넘긴 인스턴스의 속성이 None
        _C_D + "class E:\n    def __init__(self):\n        self.d = None\n"
        "def outer():\n    def use(o):\n        if o.d:\n            work()\n" + _ELSE2
        + "    use(E())\n",
        # 상수가 아닌 열쇠가 뒤에 오면 그 칸일 수 있다(else · `**` 넘기기)
        "def r(k):\n    E2 = {'d': get_dart(), k: None}\n    if E2['d']:\n"
        "        work()\n" + _ELSE,
        "def outer(k):\n    def use(d=None):\n        if not d:\n            return\n"
        "    use(**{'d': get_dart(), k: None})\n",
        # 펼침이 넘긴 칸 뒤에 오면 덮였을지 모른다
        "def outer(a):\n    def use(d=None):\n        if not d:\n            return\n"
        "    use(**{'d': get_dart(), **a})\n",
        # 순환 — 바닥 없이 서로만 가리키면 값이 한 번도 안 담긴다
        "def w(c):\n    global D\n    D = D\ndef r():\n    if not D:\n        return\n",
        "def wa(c):\n    global A\n    A = B\ndef wb(c):\n    global B\n    B = A\n"
        "def r():\n    if not A:\n        return\n",
        # 순환에 None 바인딩이 끼면 바닥이 있어도 살아 있다
        "def wa(c):\n    global A\n    A = B\ndef wb(c):\n    global B\n    B = A\n"
        "def wb2(c):\n    global B\n    B = get_dart()\ndef wa2(c):\n    global A\n"
        "    A = None\ndef r():\n    if not A:\n        return\n",
    ])
    def test_spares(self, src):
        # 공장 이름이 한 번도 안 나오면 스캐너가 그 모듈을 읽지도 않는다 — 반대
        # 증거가 빈 픽스처가 되지 않게 import 를 붙여 반드시 읽힌다(#291)
        src = "from bot.dart_client import get_dart\n" + src
        r = scan({"m": src})
        assert r["built"] == ["m"] and not r["hits"], (src, r["hits"])


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
        clock["t"] += dc.PROVISIONAL_TTL_SEC - 60
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
        assert dc.PROVISIONAL_TTL_SEC == 30 * 60      # 크기도 못박는다(#66)

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
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
        assert dp.tables_rolling(cli, "005930.KS", self._QS) == {}
        assert len(fetched) > n, "원문을 못 받은 빈손이 30분을 넘겨 살아 있다"

    def test_tables_rolling_any_read_document_bakes_long(
            self, monkeypatch, tmp_path):
        """반대 증거 — 한 건이라도 **읽었으면** 그건 답이라 24시간 굽는다.
        마지막 문서만 보고 판정하면(`read_any = bool(markup)`) 앞에서 읽은
        문서가 있어도 빈손으로 쳐 30분마다 같은 원문을 다시 받아 훑는다
        (델타 리뷰 L1 · P03).

        ⚠️ 2026-10-04 다시 씀(#222): 옛 판은 B 를 **답 없는 실패**(None)로
        두었는데, 이제 그건 '못 물어본 곳' 이라 정당하게 짧은 기록이다(부분
        캐시 — 델타 리뷰 L4, `tests/test_dart_blindspots_20261004.py`). P03 을
        계속 재려면 B 가 마지막 문서이면서 **답이어야** 한다 — 원천이 status
        014 로 '파일 없음' 이라 답한 접수건(정정·첨부 계열 실측)으로 둔다."""
        import bot.dart_client as dc
        import bot.dart_feed as df
        import bot.dart_production as dp
        fetched: list = []
        monkeypatch.setattr(df, "_DOC_NO_FILE", {})

        def fetch(rn, *a, **k):
            fetched.append(rn)
            if rn == "A":
                return "<P>표 없는 본문</P>"
            df._DOC_NO_FILE[rn] = "014"     # 제품의 `_fetch_doc_text` 가 하는 대로
            return None
        monkeypatch.setattr(dc.DartClient, "find_periodic_reports",
                            lambda self, *a: [{"rcept_no": "A"},
                                              {"rcept_no": "B"}])
        monkeypatch.setattr(df, "_fetch_doc_text", fetch)
        monkeypatch.setattr(df, "doc_was_truncated", lambda *a, **k: False)
        clock = self._real_cache(monkeypatch, tmp_path)
        cli = dc.DartClient("k-1234567890")
        assert dp.tables_rolling(cli, "005930.KS", self._QS) == {}
        assert fetched == ["A", "B"], fetched
        clock["t"] += dc.PROVISIONAL_TTL_SEC + 60
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
