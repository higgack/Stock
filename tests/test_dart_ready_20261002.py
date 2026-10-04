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

⚠️ 못 보는 축(#274) — #429 뒤에 남은 것: 함수 경계를 넘는 보관소에 **클라이언트가
아닌 바인딩이 하나라도** 있으면(`_D = None` 지연 초기화) 그 판정은 살아 있는
검사로 보고 넘긴다(`get_dart` 자신이 그 모양이다) · 타입 주석 없는 인자의 속성
(`def f(o): o.d` — 어느 객체인지 모른다) · `Optional[C]` 같은 주석 · 다른 모듈이
그 객체의 칸을 쓰는 것(`import m; m.CTX['d'] = None` — 보관소 규칙이 그 쓰기를
못 봐 **오탐** 쪽이다) · 컨테이너를 바꾸는 메서드(`update`·`setdefault`·`append`)
· 펼친 dict(`{**a, 'k': …}` — 모른다로 본다) · 식별자가 아닌 문자열 칸 · 음수·변수
칸 · 표에 없는 콜백 등록(`atexit.register` 등) · 위치로 넘긴 `Thread` 의 target ·
이름으로 넘긴 `args=` · 슬라이스·풀기로 옮긴 `*args` · 여러 조상(다이아몬드)의
메서드 순서(왼쪽 우선 깊이 우선으로 근사) · else 판정의 칸·객체 속성·`d or 기본값`
· 인자의 else(부르는 쪽을 다 모른다) · 보관소끼리 서로 옮겨 담는 순환(최소
고정점이라 증명되지 않는다) · 반복문에서 몇 번째 바퀴인지로 막은 판정
(`if i > 0 and not d`). 판정 사슬 상한 400 은 넘으면 '아니다' 로 접지 않고
실패한다(레포 실측 깊이 4). 오탐 쪽: 같은 스코프에서 다시 묶은 이름
(`d = get_dart(); d = None; if d is None`)은 첫 묶음을 기억해 잡는다(엄격 —
고칠 곳은 늘 `dart_ready` 다).
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
# 모른다(감싼 함수가 None 을 줄 수 있다, 리뷰 F2).
_TRANSPARENT_DECOS = frozenset({"staticmethod", "classmethod", "lru_cache",
                                "cache"})
# 운반 판정 상한 — 메모가 살아 있으면 레포 전수가 수만 회다(실측은 아래 레포
# 회귀가 단언한다). 넘으면 멈추고 실패한다 — 메모가 깨진 판정이 계속 도는 대신
# (리뷰 L3 · 메모 없던 옛 판은 같은 이름을 30번 다시 묶는 함수에서 30초 안에 안
# 끝났다).
_EVAL_BUDGET = 200_000
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
          "call_at": (1, 2, False)}
# 키워드로 함수·인자를 받는 것 — 이름 → (함수 키워드, 함수의 위치 자리).
# `Thread(target=f, args=(…), kwargs={…})` · `Process` 같음 ·
# `Timer(t, f, args, kwargs)`(위치로도 받는다).
_DEFER_KW = {"Thread": ("target", None), "Process": ("target", None),
             "Timer": ("function", 1)}
# 칸을 키워드로 받는 리터럴 생성자 — `dict(k=…)` 는 칸, `SimpleNamespace(k=…)`
# 는 속성.
_NS_CALLS = {"dict": "item", "SimpleNamespace": "attr"}


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
    """칸 열쇠로 쓰는 상수 — 식별자 모양 문자열 · 정수(불은 아니다)."""
    if isinstance(e, ast.Constant):
        v = e.value
        if isinstance(v, str) and v.isidentifier():
            return v
        if isinstance(v, int) and not isinstance(v, bool):
            return v
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
        b, i = _key(e.value), _const_index(e.slice)
        return f"{b}[{i!r}]" if b and i is not None else None
    if isinstance(e, ast.Call) and not e.keywords:
        f = e.func
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
    칸 열쇠는 식별자·정수뿐이라 점·괄호가 섞이지 않는다."""
    if k.endswith("?"):
        b, _kind, n = _split(k[:-1])
        return b, "get", n
    if k.endswith("]"):
        i = k.rindex("[")
        return k[:i], "item", ast.literal_eval(k[i + 1:-1])
    if "." in k:
        b, a = k.rsplit(".", 1)
        return b, "attr", a
    return None


def _store_key(k):
    """그 칸에 **쓰는** 열쇠 — `.get` 으로 읽어도 쓰기는 `a['x'] = …` 다."""
    return k[:-1] if k and k.endswith("?") else k


def _root(k):
    return re.match(r"\w+", k).group()


def _is_none(e):
    return isinstance(e, ast.Constant) and e.value is None


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
    """값의 후보 — `a if k else b` · `a or b` 는 갈래마다 본다(1차 규칙)."""
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
        if any(k is None for k in v.keys):          # {**다른것} — 덮였을지 모른다
            return None
        for kk, vv in zip(v.keys, v.values):
            c = _const_index(kk)
            if c == name and type(c) is type(name):
                return ("있음", vv)
        return ("없음", None)
    if kind == "item" and isinstance(v, (ast.List, ast.Tuple)):
        if (not isinstance(name, int)
                or any(isinstance(e, ast.Starred) for e in v.elts)):
            return None
        return ("있음", v.elts[name]) if name < len(v.elts) else ("없음", None)
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
        # `setattr(o, 'd', v)` 는 `o.d = v` 다(리뷰 F2) — 이름이 상수가 아니면
        # 어느 속성인지 모른다(이 스코프는 아무 속성이나 쓸 수 있다).
        self.dyn_setattr = False
        for x in self.nodes:
            if (isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                    and x.func.id == "setattr" and len(x.args) == 3):
                nm, b = x.args[1], _key(x.args[0])
                if (isinstance(nm, ast.Constant) and isinstance(nm.value, str)
                        and nm.value.isidentifier() and b):
                    self._add(f"{b}.{nm.value}", x.lineno, x.args[2])
                else:
                    self.dyn_setattr = True
        for x in self.nodes:
            if (isinstance(x, (ast.Name, ast.Attribute, ast.Subscript))
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
        return mods.get(name)

    def mention(names):
        pat = re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, names)))
        for n, src in sources.items():
            if n not in mods and pat.search(src):
                build(n)

    carrier: dict = {}             # id(def) → 클라이언트가 실려 오는 인자(·칸)
    factory_fns: dict = {}         # id(def) → 이름(클라이언트를 돌려주는 def)

    # ── 판정 노드(메모 · 순환 감지) ────────────────────────────────────
    # 순환 안에서 잠정으로 '아니다' 가 된 판정 → [값, 그 판정이 기대는 가장 낮은
    # 진행 중 자리]. 순환의 뿌리가 끝날 때 한꺼번에 확정하거나 버린다 — 옛 판은
    # 뿌리만 메모하고 순환의 나머지를 닿을 때마다 다시 재서, 서로를 가리키는
    # 전역 N개에 판정이 지수로 늘었다(리뷰 F5 실측: N=18 이 35,363회 · N=22 가
    # 예산 초과).
    prov: dict = {}
    prov_list: list = []

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
        고정점도 그렇다(리뷰 F5)."""
        if ck in memo:
            return memo[ck]
        if ck in stack:
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
        try:
            v = fn()
        finally:
            del stack[ck]
        mine = low[0]
        low[0] = min(saved, mine) if stack else _STACK_LIMIT
        mine_entries = prov_list[start:]
        if v:
            memo[ck] = True
            for e in mine_entries:          # 이 판정을 '아니다' 로 가정한 잠정
                prov.pop(e, None)
            del prov_list[start:]
            return True
        if mine < idx:                      # 더 먼저 시작한 판정에 기댄다 — 잠정
            for e in mine_entries:          # 이 판정에 기대던 것은 이제 그 아래에
                if prov[e][1] >= idx:
                    prov[e][1] = mine
            prov[ck] = [False, mine]
            prov_list.append(ck)
            return False
        for e in mine_entries:              # 순환의 뿌리가 '아니다' — 전부 확정
            memo[e] = prov.pop(e)[0]
        del prov_list[start:]
        memo[ck] = False
        return False

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
        return c

    def class_scope(s, expr, lazy=False):
        """식(이름 · `모듈.클래스` · 문자열 주석) → 클래스 스코프. 모르면 None."""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            try:
                expr = ast.parse(expr.value, mode="eval").body
            except SyntaxError:
                return None
            sym = _symbol(s, expr) if isinstance(expr, ast.Name) else None
        elif isinstance(expr, ast.Name):
            sym = symbol(s, expr)
        else:
            sym = None
        if sym is None and isinstance(expr, ast.Attribute):
            b = _key(expr.value)
            mp = module_path(s, b) if b else None
            sym = ("qual", mp, expr.attr) if mp else None
        c = resolve(sym, lazy) if sym and sym[0] in ("qual", "scope") else None
        return c if c is not None and c.kind == "class" else None

    def mro(cls):
        """cls 와 조상 — 왼쪽 우선 깊이 우선(읽은 모듈 안에서만)."""
        if id(cls) not in mro_cache:
            out, todo = [], [cls]
            while todo:
                c = todo.pop(0)
                if c in out:
                    continue
                out.append(c)
                todo[0:0] = [b for b in (class_scope(c.parent, e)
                                         for e in c.bases) if b is not None]
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
        if c.kind != "def":
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

    def shadowed(s, name):
        """그 이름이 공장이 아닌 다른 값으로 묶였나 — 인자·지역 대입 · 모듈의
        import 아닌 대입."""
        h = holder(s, name)
        if h is None:
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
        if isinstance(e.func, ast.Name) and shadowed(s, e.func.id):
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
        if k in home.params or k in home.kwonly:
            ev.append((home, 0, ("param", k)))
        if k in (home.vararg, home.kwarg):
            ev.append((home, 0, None))      # 인자 묶음 자체는 클라이언트가 아니다
        return ev

    def ev_attr(cls, attr):
        """`self.attr` — 클래스 가족(조상·자손)의 메서드 대입 + 클래스 본문."""
        ev = []
        for c in family(cls):
            for m in c.children.values():
                if m.kind == "def" and m.params and not m.static:
                    ev += [(m, ln, v) for ln, v in
                           m.assigns.get(f"{m.params[0]}.{attr}", ())]
            ev += [(c, ln, v) for ln, v in c.assigns.get(attr, ())]
        return ev

    def member_ev(base_ev, kind, name, stores, seen):
        """담는 쪽(base)의 바인딩 + 그 칸에 직접 쓴 것 → 그 칸의 증거. 담는
        쪽이 리터럴이면 그 칸(없으면 그 바인딩은 이 칸을 안 채운다 — 읽으면
        KeyError 다. `.get` 으로 읽으면 기본값이 오므로 '실리지 않음' 으로 센다)
        · `C(...)` 인스턴스면 C 의 속성 · 모르는 값이면 None(무엇이 담겼는지
        모른다)."""
        ev = list(stores)
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
                        ev.append((sc, ln, None))
                    continue
                cls = instance_of(sc, c) if kind == "attr" else None
                if cls is None:
                    return None
                ev += ev_attr(cls, name)
        return ev

    def member_ev_at(home, base, kind, name, seen=frozenset()):
        """보관소 이름 base 의 칸 — 그 칸에 쓰는 **모든 스코프**(global 선언
        없이도 쓴다) + base 의 바인딩."""
        if (id(home), base) in seen:
            return None
        seen = seen | {(id(home), base)}
        mk = f"{base}.{name}" if kind == "attr" else f"{base}[{name!r}]"
        stores = [(sc, ln, v) for sc in home.mod.scopes
                  for ln, v in sc.assigns.get(mk, ())
                  if holder(sc, base) is home]
        ent = _arg_entry(home, base, kind, name)
        if ent is not None:
            return [(home, 0, ("ent", ent))] + stores
        return member_ev(ev_name(home, base), kind, name, stores, seen)

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
        """보관소 규칙 — 증거가 있고 **모두** 실어야."""
        return bool(ev) and all(bear(sc, v, ln) for sc, ln, v in ev)

    # ── 운반 판정 ────────────────────────────────────────────────────
    def bearing(s, v, line):
        """값이 클라이언트를 싣나 — 후보 하나라도(1차 규칙과 같은 엄격)."""
        if v is None:
            return False
        if isinstance(v, tuple):
            if v[0] == "import":                    # from M import X
                m = mods.get(v[1])
                return bool(m) and holder_carrier(m.top, v[2])
            return v[1] in carrier.get(id(s), ())   # ("param"|"ent", …)
        for c in _cands(v):
            if is_factory_call(s, c):
                return True
            k = _key(c)
            if k and is_carrier(s, k, line):
                return True
        return False

    def holder_carrier(home, k):
        """함수 경계를 넘는 보관소는 **모든** 바인딩이 실어야 운반자다.
        `_D = None` 같은 지연 초기화가 하나라도 있으면 그 None 판정은 살아
        있는 검사다(아직 안 만들었다)."""
        return node(("H", id(home), k),
                    lambda: all_bear(ev_name(home, k), bearing))

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
        # 같은 스코프 줄 순서(1차 규칙) — 그 스코프가 직접 대입한 것
        if any(ln <= line and bearing(s, v, ln)
               for ln, v in s.assigns.get(_store_key(k), ())):
            return True
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

    def member_carrier(s, base, kind, name, line):
        """`obj.attr` · `obj['k']` · `args[0]` — 담는 쪽(base)에 따라."""
        acc = _split(base)
        if acc is None:
            home = holder(s, base)
            if home is None:
                return False
            if home is not s:                       # 보관소 — 모든 바인딩
                return holder_member(home, base, kind, name)
        elif acc[1] == "attr":
            meth = method_of(s, acc[0])
            if meth is not None:                    # self.ctx['d'] · self.h.d
                cls = meth.parent
                return node(("AM", id(cls), acc[2], kind, name),
                            lambda: all_bear(attr_member_ev(
                                cls, acc[2], kind, name), bearing))
            mp = module_path(s, acc[0])             # m.CTX['d'] · m.CTX.d
            if mp:
                return mp in mods and holder_member(mods[mp].top, acc[2],
                                                    kind, name)
        # 지역 — 앞선 바인딩 하나라도(1차 규칙): 리터럴의 그 칸 · `C(...)` 의 속성
        for ln, v in s.assigns.get(base, ()):
            if ln > line or not isinstance(v, ast.AST):
                continue
            for c in _cands(v):
                e = _entry(c, kind, name)
                if e is not None:
                    if e[0] == "있음" and bearing(s, e[1], ln):
                        return True
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
        return False

    def carries(s, e, line):
        if is_factory_call(s, e):
            return True
        k = _key(e)
        return bool(k) and is_carrier(s, k, line)

    # ── 언제나 클라이언트인가(긍정 판정의 else 갈래) ─────────────────────
    def surely_bearing(s, v, line):
        """값이 **언제나** 클라이언트인가 — 후보가 모두."""
        if isinstance(v, tuple) and v[0] == "import":
            m = mods.get(v[1])
            return bool(m) and node(("SH", id(m.top), v[2]), lambda: all_bear(
                ev_name(m.top, v[2]), surely_bearing))
        if not isinstance(v, ast.AST):
            return False                    # 모름 · 인자(부르는 쪽을 다 모른다)
        return all(surely_call(s, c) or (
            _key(c) is not None and surely(s, _key(c), line))
            for c in _cands(v))

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
        if real_factory(s, e.func):
            return True
        t, _ = target(s, e.func, lazy=False)
        return (t is not None and id(t) in factory_fns
                and all(_deco_name(d) in _TRANSPARENT_DECOS
                        for d in t.node.decorator_list)
                and node(("SR", id(t)), lambda: surely_returns(t)))

    def surely_returns(t):
        # 제너레이터는 여기 오지 않는다 — `returns_client` 가 공장에서 뺀다(여기
        # 두었던 같은 검사는 도달할 수 없었다, #291)
        if not isinstance(t.node.body[-1], (ast.Return, ast.Raise)):
            return False
        return all(surely_bearing(t, x.value, x.lineno) for x in t.nodes
                   if isinstance(x, ast.Return))

    def attr_written_elsewhere(name, own):
        """`<무엇>.name = …` 을 증거 밖에서 쓰는 스코프가 있나 — 있으면 '언제나'
        를 믿지 않는다(리뷰 F2: 클래스 밖 `c.d = None` · `setattr(self, 'd',
        None)` · 이름을 모르는 `setattr`). `own(sc, base)` 는 그 저장이 이미 증거에
        들었나(가족 메서드의 self 등) · 다른 객체라고 확실한가."""
        for sc in scopes:
            if sc.dyn_setattr:
                return True
            for k in sc.assigns:
                acc = _split(k)
                if (acc is not None and acc[1] == "attr" and acc[2] == name
                        and not own(sc, acc[0])):
                    return True
        return False

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
            if home is s and (k in s.params or k in s.kwonly
                              or k in (s.vararg, s.kwarg)):
                return False
            # 그 이름을 쓰는 **모든** 스코프 — nonlocal·global 로 바꾸는 안쪽
            # 함수까지(리뷰 F2: `reset()` 이 `nonlocal d; d = None` 하면 else 가
            # 산다). 지역이어도 같다(`ev_name` 은 그 쓰는 쪽을 함께 준다).
            return node(("SH", id(home), k), lambda: (
                not (home.kind == "module" and module_written(home, k))
                and all_bear(ev_name(home, k), surely_bearing)))
        base, kind, name = acc
        if kind == "attr":
            meth = method_of(s, base)
            if meth is not None:
                cls = meth.parent
                return node(("SA", id(cls), name), lambda: (
                    not attr_written_elsewhere(
                        name, lambda sc, b: family_self(cls, sc, b))
                    and all_bear(ev_attr(cls, name), surely_bearing)))
            mp = module_path(s, base)
            if mp and mp in mods:
                top = mods[mp].top
                return node(("SH", id(top), name), lambda: (
                    not module_written(top, name)
                    and all_bear(ev_name(top, name), surely_bearing)))
        return False                        # 칸·객체 속성의 else 는 보지 않는다

    def family_self(cls, sc, base):
        """그 저장이 이미 `ev_attr` 증거에 든 것(가족 메서드의 self·cls) ·
        가족 밖 클래스의 인스턴스라고 확실한 것."""
        fam = family(cls)
        if (sc.kind == "def" and sc.params and not sc.static
                and sc.parent in fam and base == sc.params[0]):
            return True
        k = instance_class(sc, base, 10 ** 9)
        return k is not None and k not in fam

    def module_written(top, name):
        """다른 스코프가 `모듈.name = …` 으로 그 전역을 바꾸나(리뷰 F2)."""
        return attr_written_elsewhere(
            name, lambda sc, b: module_path(sc, b) != top.mod.name)

    # ── 고정점 ───────────────────────────────────────────────────────
    def calls(x):
        """(호출 대상, 위치 인자, 키워드) — 미뤄 부르기도 그 함수 호출로 본다."""
        yield x.func, x.args, x.keywords
        nm = _call_name(x)
        if nm in _DEFER:
            fi, ai, kw_too = _DEFER[nm]
            head = x.args[:ai]
            if len(x.args) > fi and not any(isinstance(a, ast.Starred)
                                            for a in head):
                yield x.args[fi], x.args[ai:], (x.keywords if kw_too else [])
        if nm in _DEFER_KW:
            fkw, fpos = _DEFER_KW[nm]
            kw = {k.arg: k.value for k in x.keywords if k.arg}

            def at(name, off):
                if name in kw:
                    return kw[name]
                if fpos is not None and len(x.args) > fpos + off:
                    return x.args[fpos + off]
                return None
            fn, a, k2 = at(fkw, 0), at("args", 1), at("kwargs", 2)
            if fn is not None:
                pos = list(a.elts) if isinstance(a, (ast.Tuple, ast.List)) else []
                more = [ast.keyword(arg=None, value=k2)] if k2 is not None else []
                if pos or more:
                    yield fn, pos, more

    def carried(s, line, args, kws):
        """실어 보내는 자리 — (위치 [자리], 키워드 {이름}). 별표: 리터럴
        (`*[…]`·`**{…}`·`**dict(…)`)은 펼치고, 받은 `*args`·`**kwargs` 를 그대로
        넘기면 그 칸을 옮긴다 — 그 밖의 별표 뒤 위치는 어디에 닿는지 모른다."""
        pos, kwd = [], set()
        i, exact = 0, True
        for a in args:
            if isinstance(a, ast.Starred):
                v = a.value
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
            v = k.value
            if isinstance(v, ast.Dict) and None not in v.keys:
                kwd |= {kk.value for kk, vv in zip(v.keys, v.values)
                        if isinstance(kk, ast.Constant)
                        and isinstance(kk.value, str) and carries(s, vv, line)}
            elif (isinstance(v, ast.Call) and _call_name(v) == "dict"
                  and not v.args):
                kwd |= {kw.arg for kw in v.keywords
                        if kw.arg and carries(s, kw.value, line)}
            elif (isinstance(v, ast.Name) and v.id == s.kwarg
                  and holder(s, v.id) is s and not s.assigns.get(v.id)):
                kwd |= {e[2] for e in carrier.get(id(s), ())
                        if isinstance(e, tuple) and e[:2] == ("**", v.id)}
        return pos, kwd

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
        for x in s.nodes:
            if isinstance(x, ast.Return) and x.value is not None:
                for c in _cands(x.value):
                    k = _key(c)
                    if is_factory_call(s, c) or (
                            k and k not in own and _root(k) not in bundles
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
                        pos, kwd = carried(s, x.lineno, args, kws)
                        if not pos and not kwd:
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
        out = set(factory_fns.values())
        for m in list(mods.values()):
            # 정렬해서 훑는다 — 집합 순회는 해시 시드마다 달라 판정 순서(어느
            # 순환에 먼저 닿나)가 프로세스마다 갈렸다. 맞는 판정은 순서와
            # 무관하지만, 순서에 기대는 메모 버그는 그러면 실행마다 나타났다
            # 사라진다(2026-10-04 F5 뮤테이션이 실측 — 같은 그래프가 한
            # 프로세스에선 틀리고 다른 프로세스에선 맞았다)
            for k in sorted(set(m.top.assigns) | set(m.writers)):
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
                if (isinstance(op, ast.Call) and is_factory_call(s, op)) or (
                        k and is_carrier(s, k, x.lineno) and not any(
                            bearing(s, v, ln)
                            for ln, v in s.resolves.get(id(x), ()))):
                    hits.append((s.mod.name, x.lineno, ast.unparse(x)))
                    break
            # 긍정 판정의 else 갈래 — 값이 **언제나** 클라이언트면 그 갈래는
            # 죽었다(키가 없을 때를 다루려던 갈래가 한 번도 안 돈다, #427).
            # 기본값만 두는 갈래(`else None`)는 해가 없어 넘긴다.
            if isinstance(x, (ast.If, ast.IfExp)) and x.orelse and not (
                    _trivial_branch(x.orelse) if isinstance(x, ast.If)
                    else _trivial(x.orelse)):
                op = _pos_operand(x.test)
                k = _key(op) if op is not None else None
                if op is not None and (surely_call(s, op)
                                       or (k and surely(s, k, x.lineno))):
                    hits.append((s.mod.name, x.lineno,
                                 f"{ast.unparse(x.test)} → else"))
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
        "def f(base):\n    ctx = {**base, 'dart': get_dart()}\n"
        "    if not ctx['dart']:\n        return\n",
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
        자기 자신을 다시 담는 바인딩이 멈춰야 하고(재귀 폭주·무한 반복 없음),
        끝까지 증명되지 않은 것은 '아니다' 다(최소 고정점 — 잡지 않는다)."""
        r = scan({"m": "def a():\n    global A\n    A = B\ndef b():\n"
                  "    global B\n    B = A\ndef c():\n    global A\n"
                  "    A = get_dart()\ndef f():\n    if not B:\n        return\n"
                  "def g():\n    global D\n    D = D\ndef h():\n    if not D:\n"
                  "        return\n"})
        assert r["hits"] == [] and r["depth"] < 20, r

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
    ])
    def test_fires_on_the_real_shapes(self, src, want):
        assert dead_guards(src) == [want], src

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
        """무작위 그래프 300개 — 판정이 최소 고정점(끝까지 따라가 증명된 것만
        참)과 같아야 한다. 잠정 '아니다' 를 메모하거나(옛 판의 깊이 메모 병) ·
        뿌리가 참으로 끝났는데 그 아래 잠정을 남기거나 · 기대는 자리를 위로
        안 올리면 순서에 따라 답이 갈린다(한 픽스처로는 안 보인다). 시드를
        고정했고 판정 순서도 해시 시드와 무관하게 정렬했다(`exported()`) —
        실패가 재현된다. ⚠️ 드문 모양(자리 물려주기 · 잠정 조회의 자리 낮추기)은
        300개로는 안 걸려 아래에 줄인 반례를 따로 둔다."""
        import random
        rng = random.Random(20261004)
        hit_total, checks, bad = 0, 0, []
        for t in range(300):
            n, binds, src = _random_carrier_graph(rng)
            want = _least_fixpoint(binds)
            lines = src.splitlines()
            at = {i: lines.index(f"    if not G{i}:") + 1 for i in range(n)}
            r = scan({"m": src})
            got = {ln for _m, ln, _e in r["hits"]}
            exp = {at[i] for i in range(n) if want[i]}
            hit_total += len(exp)
            checks += n
            if got != exp:
                bad.append((t, sorted(exp ^ got), src))
        assert not bad, bad[:2]
        # 참·거짓이 둘 다 충분히 나와야 판정을 잰다(한쪽뿐이면 상수로도 통과)
        assert hit_total >= 100 and checks - hit_total >= 100, (hit_total,
                                                                checks)

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
        assert dead_guards(src) == [(16, "not G4")]

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
        assert dead_guards(src) == [(4, "not G8")]

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
        # G0 만 공장을 받고 나머지는 서로를 받는다 — 바인딩이 모두 실어야 하는
        # 보관소 규칙에서 G3 은 증명되지 않는다
        assert r["hits"] == [], r["hits"]
        assert n <= r["evals"] <= 10 * n, (n, r["evals"])


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
