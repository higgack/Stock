"""DART 정기보고서 「매출 및 수주상황」 → 수주잔고(원). LLM 0·₩0·stdlib.

수주잔고는 **의무 공시 항목이 아니다.** 「II.사업의 내용 > 4.매출 및 수주상황」에
회사가 쓸지 말지 정하고, 쓰더라도 표 형태가 회사마다 다르다. 그래서 이 모듈은
2026-08-17 VM 프로브로 **실제 원문 54종목**을 3차에 걸쳐 받아본 뒤에 짰다
(추측 금지 — 실수 #12 '검증불가면 단정 금지'). 관측된 형태:

  A. 표 — 헤더에 잔고 열 + 총액/기초 열 + 납품/매출/수익인식 열.
     A-1 `합 계` 행이 있으면 그 행을 검산해서 쓴다(가장 안전).
     A-2 합계행이 **없으면** 데이터 행을 각각 검산해 잔고열을 합산한다.
     ※ 티에스이는 `(단위 : 개, 백만원)` 로 수량까지 숫자여서 6열이 나온다 —
       홀수 인덱스(금액)만 뽑는다. 안 그러면 수량 162,577개가 잔고가 된다.
  B. XBRL 주석형 — `수주잔고, 기말 N`(한전KPS) / `건설계약 수주잔고 N`(현대건설).
  C. 단일값형 — 방산 보안으로 잔액만. LIG넥스원 `구 분 수주잔액 … 245,781`.
  D. 잔고 단일열형 — 총액·납품 열이 없다. 테크윙 `구분 주요 고객 수주잔고금액`.
     항등식이 없어 **부문 합 = 합계**로 검산한다.
  G. 산문·인라인단위형 — 한국항공우주 주석 "수주잔고는 25,808,760백만원입니다".
     표(억원)와 주석(백만원)의 단위가 달라 문장에서 직접 읽는다.
  H. 전치형 — 항목이 **행**이다(`기초계약잔액 / 신규계약액 / 수익 / 기말계약잔액`).
     케이씨텍(당반기·전기 2열)·코오롱글로벌(6부문+합계 7열).
     어느 열을 쓸지는 **열 개수**로 정한다 — 2열이면 첫 열(당반기), 3열 이상이면
     마지막 열(합계). 부문별로도 항등식이 성립해 검산만으론 못 가른다.
  I. 건설 도급표 — `기본도급액 / 완성공사액 / 계약잔액` + 합계행.
     대우건설 53.4조·GS건설(국내) 48.5조.

⚠️ **평문 표 읽기의 두 함정**(3차 프로브가 드러냄 — 둘 다 실제로 값을 잃고 있었다):
  · **표의 끝을 못 잡으면 다음 표를 먹는다.** 넥스틴은 수주표 뒤에 붙은 신용위험
    표가 4열 묶음으로 잡혀 검산이 깨졌고, 그 탓에 멀쩡한 표가 버려졌다.
    → `_cut_table` 이 각주(※·주N)·다음 캡션·절번호에서 자른다.
  · **콤마를 필수로 하면 소액이 사라진다.** 억원 단위 표는 값이 세 자리라
    엠플러스 `합 계 - 1,461 - 830 - 631` 의 830·631 이 끊겼다.
    → 콤마 없는 1~4자리도 값으로 받고, 정합성은 검산에 맡긴다.

**가져오지 않는 것**(전부 관측된 실물 — 조용히 틀린 숫자를 내느니 없음이 낫다):
  · 기재 생략 선언 — HD현대건설기계·HPSP "영업비밀 … 기재는 생략".
    키워드는 있는데 값이 없다. 근처 숫자(계약금액)를 집으면 대형 오류.
  · 계약잔액이라도 **도급표가 아니면** 안 쓴다 — 한전기술은 원문이 스스로
    "회사 전체의 계약 잔액과 다릅니다"라고 밝힌다. `계약잔액` 이라는 단어는
    XBRL 주석마다 나오므로 헤더 구성(기본도급액+완성공사액)으로 가른다.
  · 외화 표기 — 씨에스윈드 "수주잔고는 1,097백만USD". 환산 없이 원화 축에
    올리면 1400배 오차. 환율 소스를 붙이기 전까진 거부.
  · 잔고가 전부 `-` 인 표 — 한미반도체·케이엠더블유는 완료 계약만 실어 잔고가
    비어 있다. 형식은 맞지만 낼 값이 없다.
  · 두산에너빌리티 — 프로젝트별 표가 **두 개**이고 행이 겹친다(UAE BNPP 등).
    회사 전체 합계가 없어 합산하면 부분값이거나 이중계상이다.
  · 에스에프에이 — `신규수주 / 매출 / 기말잔고` 3열이라 기초가 없어 항등식을
    세울 수 없다(수직 합만 성립). 검산 없이 낼 수는 없다.

⚠️ **검산이 이 모듈의 핵심 안전장치다.** 원문이 계산식을 직접 밝히고 있고
(`※ 수주잔고 = 기초계약잔액 + 신규계약액 - 기납품액`), 관측된 7건 전부
정확히 맞아떨어졌다. 그래서 합계행에서 뽑은 값이 그 항등식을 만족하지 못하면
**컬럼을 잘못 집은 것으로 보고 버린다**(None). 표 구조가 바뀌어도 조용히 틀린
숫자가 나가지 않는다.
"""
from __future__ import annotations

import json as _json
import logging
import re
from pathlib import Path as _Path

log = logging.getLogger("bot.dart_backlog")

# 단위 → 원 배수. `백만USD`·`천불` 류는 **의도적으로 없다** — 매칭되면 거부한다.
# ⚠️ `백만USD`·`천불` 류가 **없는 것이 곧 외화 차단**이다 — 매칭되지 않으면
# `_unit_mult` 가 None 을 돌려 그 표를 통째로 버린다. 환율 소스를 붙이기
# 전까지 여기에 외화를 추가하면 원화 축에 1400배 값이 올라간다(씨에스윈드
# "수주잔고는 1,097백만USD" 실측). 별도 가드 함수를 두면 죽은 코드가 되므로
# **이 표 자체가 가드**다.
_UNIT_MULT = {"원": 1.0, "천원": 1e3, "백만원": 1e6, "억원": 1e8,
              "십억원": 1e9, "조원": 1e12}
_UNIT_RE = re.compile(r"단위\s*[:：]?[^)\]]{0,20}?(조원|십억원|백만원|억원|천원|원)")
# 값 토큰. 콤마 묶음 또는 **콤마 없는 1~4자리**.
# ⚠️ 처음엔 콤마를 필수로 했는데(절번호·연도 차단용) 억원 단위 표에서 값이
# 네 자리 미만이면 통째로 잃는다 — 엠플러스 `합 계 - 1,461 - 830 - 631` 에서
# 830·631 이 끊겨 수주잔고를 못 읽었다(2026-08-17 3차 프로브). 비에이치아이
# `합계 69 3,812,597 …`(건수)·코오롱글로벌 수량 `1` 도 같은 이유로 막혔다.
# 이제 절번호·다음 표 차단은 콤마가 아니라 **표 끝 마커**(_TABLE_END)가 맡고,
# 값의 정합성은 어차피 검산이 최종 판정한다.
_NUM_TOK = re.compile(r"^\(?-?(?:\d{1,3}(?:,\d{3})+|\d{1,4})\)?$")

# 표의 끝. ⚠️ 이게 없으면 **다음 표의 숫자까지 행으로 읽는다** — 넥스틴은
# 수주표 바로 뒤 신용위험 표(현금및현금성자산 20,205,538,777 …)가 4열 묶음으로
# 잡혀 검산에 실패했고, 그 탓에 멀쩡한 표가 통째로 버려졌다(3차 프로브 실측).
# 세진중공업도 동일. 삼성E&A·효성중공업은 픽스처가 짧아 우연히 통과했을 뿐
# 전문에서는 같은 위험이 있었다.
_TABLE_END = re.compile(
    r"[※☞]|주\s*\d+\s*\)|\(\s*주\s*\d|\(\s*단위|\(\s*기준일"
    r"|\(\d+\)\s*[가-힣]|\d+\s*\.\s*[가-힣]{2,}|[가-하]\s*\.\s+[가-힣]")


def _cut_table(seg: str) -> str:
    """표 본문만 남긴다 — 각주(※·주1)·다음 표 캡션·다음 절 번호에서 자른다."""
    m = _TABLE_END.search(seg)
    return seg[:m.start()] if m else seg

# 헤더 판정 — 잔고 컬럼 라벨 / 시작잔고 라벨 / 납품 라벨.
# ⚠️ 긴 것부터. `수주잔` 은 효성중공업이 헤더를 `전기말 수주잔(2025.12.31)` 로
# 줄여 쓰기 때문에 필요하다(2026-08-17 2차 프로브).
_BAL_LABELS = ("기말수주잔고", "수주잔고금액", "수주잔고", "수주잔액",
               "기말잔고", "수주잔", "기말")
_OPEN_LABELS = ("기초계약잔액", "기초수주잔액", "이월 수주잔액", "이월수주잔액",
                "수주총액", "수주액", "수주잔", "기초")
# `매출액` 은 효성중공업이 기납품액 대신 쓰는 열이다. 넓어 보이지만 헤더에
# 잔고 라벨이 함께 있어야만 도달하고, 최종 관문은 어차피 **검산**이다.
_DELIV_LABELS = ("기납품액", "기납품", "납품액", "공사수익", "수익인식",
                 "매출액")

# 검산 허용오차 1%. 0.5% 였는데 효성중공업이 0.78% 벗어난다 — 원문이 사유를
# 직접 밝힌다: "수주잔액은 수주 취소, 계약금액 변경 등으로 … 단순 가감 결과와
# 차이가 있을 수 있습니다". 컬럼을 잘못 집으면 오차가 %가 아니라 자릿수
# 단위라, 1% 로 넓혀도 가드는 그대로 유효하다.
_TOL = 0.01


def _to_num(tok: str) -> float | None:
    """`(9,538,147)` → -9538147.0. 괄호 = 회계식 음수."""
    t = tok.strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "")
    if not t or not t.lstrip("-").isdigit():
        return None
    v = float(t)
    return -v if neg else v


def _unit_mult(text: str, at: int) -> float | None:
    """`at` **앞쪽** 가장 가까운 `(단위 : X)`. 표 캡션은 항상 표 위에 온다.

    없으면 None — 단위 없이 스케일을 가정하면 100배 오차가 난다(백만원 vs
    억원). 못 정하면 값을 버리는 쪽이 옳다."""
    best = None
    for m in _UNIT_RE.finditer(text, 0, at):
        best = m
    if not best or at - best.end() > 4000:
        return None
    return _UNIT_MULT.get(best.group(1))


def _row_values(text: str, at: int, limit: int = 400) -> list[float]:
    """`at` 부터 이어지는 표 한 행의 숫자들. `-`(수량 자리표시자)는 건너뛰고,
    숫자도 `-` 도 아닌 토큰이 나오면 행이 끝난 것으로 본다."""
    out: list[float] = []
    for tok in text[at:at + limit].split():
        if tok == "-" or tok == "―":
            continue
        if not _NUM_TOK.match(tok):
            break
        v = _to_num(tok)
        if v is not None:
            out.append(v)
    return out


def _verify(vals: list[float]) -> float | None:
    """합계행 숫자들이 원문이 명시한 항등식을 만족하면 잔고를 반환.

    · 3열: 수주총액 − 기납품액 = 수주잔고
    · 4열: 기초잔액 + 신규 − 기납품액 = 수주잔고
    (기납품액이 `(12,248,487)` 처럼 이미 음수로 적힌 회사가 있어 **절대값**으로
    뺀다 — HD현대중공업 실측.)

    ⚠️ 이 검산이 유일한 컬럼 정합성 보증이다. 표 구조가 바뀌어 엉뚱한 열을
    집으면 산수가 안 맞고, 그때는 값을 내지 않는다(조용한 오답 방지)."""
    # 6열 = (수량, 금액) 쌍 3벌. 티에스이가 `(단위 : 개, 백만원)` 으로 수량까지
    # 숫자로 적어 6개가 나온다 — 홀수 인덱스(금액)만 뽑아 3열과 같게 본다.
    if len(vals) == 6:
        vals = vals[1::2]
    if len(vals) == 3:
        a, b, c = vals
        exp, got = a - abs(b), c
    elif len(vals) == 4:
        a, b, c, d = vals
        exp, got = a + b - abs(c), d
    else:
        return None
    if exp <= 0 or got <= 0:
        return None
    if abs(exp - got) > _TOL * max(abs(exp), abs(got)):
        return None
    return got


def _runs(seg: str) -> list[list[float]]:
    """평문 표에서 **연속된 숫자 묶음**(= 행 하나)들을 뽑는다.

    태그가 지워진 표는 행 구분자가 없어서 `품목명 … 숫자 숫자 숫자 다음품목명`
    처럼 이어진다. 숫자(또는 `-` 자리표시자)가 이어지는 동안을 한 행으로 보고,
    그 외 토큰이 나오면 행을 닫는다. 콤마 없는 토큰은 숫자로 치지 않으므로
    연도·건수·절번호에서 자연히 끊긴다."""
    out, cur = [], []
    for tok in seg.split():
        if tok in ("-", "―"):
            continue                      # 수량 자리표시자 — 행을 끊지 않는다
        if _NUM_TOK.match(tok):
            v = _to_num(tok)
            if v is not None:
                cur.append(v)
            continue
        if cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _first_run(seg: str) -> list[float]:
    """첫 숫자 묶음. ⚠️ `_row_values` 와 달리 **앞의 비숫자 토큰을 건너뛴다** —
    전치표는 라벨에 각주 표시가 붙는다(케이씨텍 `기말공사계약잔액(*) 34,500,592`).
    `_row_values` 는 `(*)` 에서 즉시 끊겨 빈 행을 돌려준다."""
    runs = _runs(seg)
    return runs[0] if runs else []


def _parse_table(text: str) -> tuple[float, str] | None:
    """형태 A — 헤더(총액/기초 + 기납품 + 잔고)가 있는 표.

    ① `합 계` 행이 있으면 그 행을 검산해서 쓴다(가장 안전).
    ② 합계행이 **아예 없으면** 데이터 행들을 각각 검산해 잔고열을 합산한다
       — 제이티(1행)·삼성E&A(3행)·효성중공업(3행)이 이 형태다.
       ⚠️ 합계행이 **있는데 검산에 실패한 경우엔 행 합산으로 넘어가지 않는다.**
       합계가 있는데 못 맞췄다는 건 내 컬럼 모델이 틀렸다는 신호라, 거기서
       행을 더 파면 틀린 값을 그럴듯하게 만들어낸다."""
    for m in re.finditer("|".join(_BAL_LABELS), text):
        head = text[max(0, m.start() - 260):m.start()]
        if not any(k in head for k in _OPEN_LABELS):
            continue
        if not any(k in head for k in _DELIV_LABELS):
            continue
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        # 표는 헤더 뒤 가까이에서 끝난다. 넓게 잡으면 다음 표를 먹는다.
        seg = _cut_table(text[m.end():m.end() + 2500])
        tm = re.search(r"합\s*계", seg)
        if tm:
            got = _verify(_row_values(seg, tm.end()))
            if got is not None:
                return got * mult, "표·합계행"
            continue
        # ② 합계행 없음 — 행별 검산 후 합산. 3열 이상 묶음은 **전부** 통과해야
        #    한다(하나라도 어긋나면 표를 잘못 읽고 있다는 뜻).
        rows = [r for r in _runs(seg) if len(r) in (3, 4, 6)]
        if not rows:
            continue
        bals = [_verify(r) for r in rows]
        if any(b is None for b in bals):
            continue
        return sum(bals) * mult, "표·행합산"
    return None


# ── 형태 H: 전치형(세로) 계약잔액 표 ────────────────────────────────
# 항목이 **행**이고 기간·부문이 열이다. 케이씨텍(당반기/전기 2열)·코오롱글로벌
# (부문 6개 + 합계 7열) 실측. 라벨 사이에 공백이 끼어 있어(`신 규 계 약 액`)
# 글자마다 `\s*` 를 허용한다.
def _sp(word: str) -> str:
    return r"\s*".join(word)


_TRANS_STEPS = (
    (r"기초\s*(?:공사)?\s*계약잔액", "기초"),
    (rf"{_sp('신규계약액')}|{_sp('신규계약')}|당기\s*계약액|{_sp('신규수주')}", "신규"),
    (rf"당기\s*공사수익금액|{_sp('수익인식')}|{_sp('수익')}", "수익"),
    (r"기말\s*(?:공사)?\s*계약잔액", "기말"),
)


def _parse_transposed(text: str) -> tuple[float, str] | None:
    """형태 H — `기초계약잔액 / 신규계약액 / 수익 / 기말계약잔액` 이 **행**으로
    쌓인 표. 각 행에서 같은 자리의 숫자를 뽑아 A + B − |C| = D 로 검산한다.

    ⚠️ 어느 열을 쓸지는 **열 개수**로 정한다 — 2열이면 (당반기, 전기)라 첫 열이
    당기이고(케이씨텍), 3열 이상이면 (부문…, 합계)라 마지막이 전사 합계다
    (코오롱글로벌 6부문+합계). 부문별로도 항등식이 성립해서 검산만으로는
    둘을 못 가른다 — 첫 열을 집으면 코오롱은 국내토목 부문만 나온다."""
    for m0 in re.finditer(_TRANS_STEPS[0][0], text):
        pos, rows, ok = m0.end(), [], True
        for pat, _lbl in _TRANS_STEPS[1:]:
            mm = re.compile(pat).search(text, pos, pos + 900)
            if not mm:
                ok = False
                break
            rows.append(_first_run(text[pos:mm.start()]))
            pos = mm.end()
        if not ok:
            continue
        rows.append(_first_run(text[pos:pos + 300]))
        if not rows[0]:
            continue
        n = len(rows[0])
        if n < 2 or any(len(r) != n for r in rows):
            continue
        idx = 0 if n == 2 else n - 1
        got = _verify([r[idx] for r in rows])
        if got is None:
            continue
        mult = _unit_mult(text, m0.start())
        if mult is None:
            continue
        return got * mult, "전치·계약잔액"
    return None


# ── 형태 I: 건설 도급 표 ─────────────────────────────────────────
# 헤더 `기본도급액 / 완성공사액 / 계약잔액` + 합계행. 대우건설·GS건설 실측.
# ⚠️ 한전기술은 `계약잔액` 을 쓰지만 헤더가 `사업건수 / 수익인식액 / 계약잔액 /
# 원도급액` 이라 이 게이트를 **통과하지 못한다** — 원문이 스스로 "회사 전체의
# 계약 잔액과 다릅니다"라고 밝힌 표라 배제되는 게 맞고, 별도 문구 감지 없이
# 헤더 구성만으로 갈린다.
_CONTRACT_HEAD = re.compile(r"기본\s*도급\s*금?액.{0,40}?완성공사액.{0,40}?계약잔액",
                            re.S)


def _parse_contract_table(text: str) -> tuple[float, str] | None:
    for m in _CONTRACT_HEAD.finditer(text):
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        seg = text[m.end():m.end() + 12000]      # 프로젝트가 수백 줄일 수 있다
        tm = re.search(r"합\s*계", seg)
        if not tm:
            continue
        got = _verify(_row_values(seg, tm.end()))
        if got is None:
            continue
        return got * mult, "건설·계약잔액"
    return None


def _parse_domestic_export(text: str) -> tuple[float, str] | None:
    """형태 J — `신규수주 / 매출 / 기말수주잔고` 3열이 내수·수출·소계로 쌓인
    블록(에스에프에이).

    기초 잔고 열이 없어 A 의 항등식(기초+신규−납품=잔고)을 세울 수 없다.
    대신 **내수 + 수출 = 소계**가 세 열 모두에서 성립해야 하고, 이게 검산이
    된다 — 열을 잘못 집으면 세 등식이 동시에 맞을 수 없다."""
    for m in re.finditer(r"수주잔고액|기말\s*수주잔고", text):
        if "신규수주" not in text[max(0, m.start() - 200):m.start()]:
            continue
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        seg = _cut_table(text[m.end():m.end() + 3000])
        tms = list(re.finditer(r"합\s*계", seg))
        if not tms:
            continue
        runs = [r for r in _runs(seg[tms[-1].end():]) if len(r) == 3][:3]
        if len(runs) != 3:
            continue
        dom, exp_, tot = runs
        if any(abs(dom[i] + exp_[i] - tot[i]) > _TOL * max(abs(tot[i]), 1.0)
               for i in range(3)):
            continue
        if tot[2] <= 0:
            continue
        return tot[2] * mult, "내수·수출 소계"
    return None


def _parse_balance_column(text: str) -> tuple[float, str] | None:
    """형태 D — 잔고 **한 열만** 있는 표(테크윙).

        구분 주요 고객 수주잔고금액
        반도체 장비 Micron, SK hynix 등 55,508
        디스플레이장비 삼성디스플레이 등 4,760
        합계 - 60,268

    총액·납품액 열이 없어 A 의 항등식을 쓸 수 없다. 대신 **부문 합 = 합계**가
    검산이 된다 — 이게 맞으면 열을 제대로 집은 것이다."""
    for m in re.finditer(r"수주잔고금액|수주잔고\s*금액", text):
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        seg = _cut_table(text[m.end():m.end() + 1200])
        tm = re.search(r"합\s*계", seg)
        if not tm:
            continue
        parts = [r[0] for r in _runs(seg[:tm.start()]) if len(r) == 1]
        total = _row_values(seg, tm.end())
        if not parts or len(total) != 1 or total[0] <= 0:
            continue
        if abs(sum(parts) - total[0]) > _TOL * total[0]:
            continue
        return total[0] * mult, "표·잔고열"
    return None


# 형태 G — 산문에 **단위가 값에 붙어** 나온다(한국항공우주 재무제표 주석:
# "고객과의 계약 관련 수주잔고는 25,808,760백만원입니다"). 표가 억원인데 주석은
# 백만원이라, 단위를 문장에서 직접 읽어야 한다.
# ⚠️ 통화를 `원` 으로 끝나게 강제하는 것이 외화 차단이다 — 씨에스윈드
# "1,097백만USD" 는 `백만원` 에 매칭되지 않아 자동으로 걸러진다.
_PROSE_RE = re.compile(
    r"수주잔(?:고|액)[^.]{0,20}?([\d]{1,3}(?:,\d{3})+)\s*"
    r"(조원|십억원|백만원|억원|천원|원)")


def _parse_prose(text: str) -> tuple[float, str] | None:
    m = _PROSE_RE.search(text)
    if not m:
        return None
    v = _to_num(m.group(1))
    mult = _UNIT_MULT.get(m.group(2))
    if v is None or v <= 0 or not mult:
        return None
    return v * mult, "산문·인라인단위"


def _parse_xbrl(text: str) -> tuple[float, str] | None:
    """형태 B — 재무제표 주석의 건설계약 공시. 라벨 바로 뒤가 값이다."""
    for pat in (r"수주잔고,\s*기말", r"건설계약\s*수주잔고"):
        m = re.search(pat, text)
        if not m:
            continue
        vals = _row_values(text, m.end(), limit=80)
        if not vals or vals[0] <= 0:
            continue
        mult = _unit_mult(text, m.start())
        if mult is None:
            continue
        return vals[0] * mult, "주석·건설계약"
    return None


def _parse_single(text: str) -> tuple[float, str] | None:
    """형태 C — 방산 보안 등으로 잔액 한 칸만 공시.

    · LIG넥스원 `구 분 수주잔액 제25기(2026년 2분기) 245,781`
    · 퍼스텍   `품목 수주잔고 금액 방산사업 944,080`
    라벨과 값 사이에 기수·부문명이 끼므로 **첫 값 토큰까지 건너뛴다**. 다만
    건너뛰는 구간에 다른 표가 끼어들지 않도록 좁게(120자) 본다."""
    m = re.search(r"(?:구\s*분|품\s*목)\s*(?:수주잔액|수주잔고)"
                  r"(?:\s*금\s*액)?", text)
    if not m:
        return None
    seg = text[m.end():m.end() + 120]
    nums = [t for t in seg.split() if _NUM_TOK.match(t)]
    if not nums:
        return None
    v = _to_num(nums[0])
    if v is None or v <= 0:
        return None
    mult = _unit_mult(text, m.start())
    if mult is None:
        return None
    return v * mult, "단일값"


# ── 런타임 관측 (사용자 2026-08-17) ──────────────────────────────────
# 프로브를 손으로 도는 건 확장이 안 된다 — 상장사 2,700개 중 88개를 내 감으로
# 골랐고 그 과정에서 회사명을 두 번 잘못 붙였다. 대신 **실사용이 곧 프로브**가
# 되게 한다: 파서가 값을 못 내면 사유를 남기고, 나중에 사유별로 훑어 진짜
# 파서 개선 여지(형식미지원·검산실패)만 골라낸다. 미공시는 원천에 값이 없는
# 것이라 개선 대상이 아니다. CLAUDE.md Automation-first.
_MISS_LOG = _Path.home() / ".tradingagents" / "backlog_misses.jsonl"
_MISS_CAP = 4000          # 줄 수 상한 — 장수 프로세스에서 무한 증가 방지

# 원문이 스스로 미공시를 밝히는 문구. 실측: 영화금속 "산정은 불가능합니다" ·
# SNT모티브 "관리하고 있지 않습니다" · 상아프론테크 "수주잔고는 없습니다" ·
# HD현대건설기계·HPSP "기재는 생략". 파서로 해결 불가 — 개선 대상이 아니다.
_NO_DATA_RE = re.compile(
    r"수주잔고[^.]{0,30}?(?:산정[^.]{0,10}?불가|없습니다|기재[^.]{0,10}?생략)"
    r"|(?:수주물량[^.]{0,20}?)?수주잔고[^.]{0,20}?관리하고\s*있지\s*않"
    r"|기재[는를]?\s*생략[^.]{0,40}?수주잔고")


def diagnose(text: str) -> str:
    """값을 못 낸 이유를 짧은 코드로 분류한다.

    ⚠️ 분류가 곧 **개선 여지 판정**이다 — `미공시`·`명시적미공시` 는 원천에
    값이 없어 파서를 고칠 여지가 없고, `형식미지원`·`검산실패`·`단위없음` 만
    새 형식이 필요하다는 신호다. 이 구분이 없으면 로그가 노이즈가 된다."""
    if not text:
        # ⚠️ 미공시와 **다른 신호**다. 원천에 값이 없는 게 아니라 DART 가 그
        # 접수건의 원문을 안 준다(`status=014 파일이 존재하지 않습니다`).
        # 한화에어로 2026 1분기 실측 — 정정도 없는 원본인데 문서가 없다
        # (`--list` 로 확대 창까지 훑어 정정 부재를 확인, 2026-08-18).
        # 여러 종목에서 몰리면 키 권한·일일한도 같은 계정 문제일 수 있으므로
        # 격주 리포트에 **보여야 한다**.
        return "원문미제공"
    hits = len(re.findall("|".join(_BAL_LABELS), text))
    if not hits:
        return "미공시"
    if _NO_DATA_RE.search(text):
        return "명시적미공시"
    # 잔고 라벨은 있는데 그 앞에 단위 캡션이 없으면 스케일을 못 정한다.
    first = min(text.find(k) for k in _BAL_LABELS if k in text)
    if _unit_mult(text, first) is None:
        return "단위없음"
    if not re.search(r"합\s*계", text[first:first + 2500]):
        return "합계없음"
    return "형식미지원"


def _log_miss(ticker: str, year, reprt_code, reason: str) -> None:
    """미스 1건 기록. 실패는 조용히 삼킨다 — 진단 로그가 본 기능을 막으면 안 된다."""
    if reason in ("미공시", "명시적미공시"):
        return                      # 원천에 값이 없다 — 개선 대상 아님
    try:
        _MISS_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = _json.dumps({"ticker": ticker, "year": year,
                            "reprt": reprt_code, "reason": reason},
                           ensure_ascii=False)
        old = []
        if _MISS_LOG.exists():
            old = _MISS_LOG.read_text(encoding="utf-8").splitlines()[-_MISS_CAP:]
        if line in old:
            return                  # 같은 종목·분기 중복 기록 안 함
        _MISS_LOG.write_text("\n".join(old + [line]) + "\n", encoding="utf-8")
    except Exception as exc:
        log.debug("dart_backlog: 미스 로그 실패: %s", exc)


def parse_backlog(text: str) -> dict | None:
    """정기보고서 평문 → {value(원), unit_src, form} 또는 None.

    None 의 의미는 **'없거나 확신할 수 없음'** 이며 둘을 구분하지 않는다 —
    호출부는 어느 쪽이든 패널을 생략해야 하기 때문이다(날조 금지)."""
    if not text:
        return None
    # ⚠️ '기재 생략' 선언을 따로 검사하지 않는다. 형식 파서 3종이 이미 전부
    #    거부하고(mutation 으로 확인), 별도 검사를 두면 **종속회사 한 곳의 생략
    #    문구가 지배회사 값을 죽이는** 오탐이 생긴다(한국항공우주 본문에 자회사
    #    '한국표면처리' 의 생략 문구가 있다). 값의 안전은 검산이 보장한다.
    # 순서 = 신뢰도 순. 검산 가능한 표가 먼저이고, 산문은 검산할 항등식이
    # 없으므로 **마지막**이다(파크시스템스는 표 112,509백만원과 주석의 "약
    # 1,023억원"이 기준일이 달라 다른데, 표가 먼저 잡혀 기준일 값이 이긴다).
    for fn in (_parse_table, _parse_domestic_export,
               _parse_balance_column, _parse_transposed,
               _parse_contract_table, _parse_xbrl, _parse_single,
               _parse_prose):
        try:
            got = fn(text)
        except Exception as exc:            # 한 형태의 실패가 나머지를 막지 않게
            log.debug("dart_backlog: %s 실패: %s", fn.__name__, exc)
            continue
        if not got:
            continue
        value, form = got
        return {"value": value, "form": form}
    return None


def review_text() -> str:
    """미스 요약 HTML — 보낼 게 없으면 빈 문자열.

    ⚠️ 기록 자체가 **개선 여지 있는 사유만** 담는다(미공시는 `_log_miss` 가
    거른다). 그래서 여기 뭔가 있다는 건 곧 '새 형식이 나타났다' 는 뜻이다."""
    import json
    from collections import Counter
    if not _MISS_LOG.exists():
        return ""
    rows = []
    for ln in _MISS_LOG.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue
    if not rows:
        return ""
    by = Counter(r.get("reason", "?") for r in rows)
    tick = Counter(r.get("ticker") for r in rows)
    out = [f"📐 <b>수주잔고 파서 리뷰</b> (격주 금요일)",
           f"막힌 조회 {len(rows)}건 · 종목 {len(tick)}개",
           ""]
    out += [f"· {r}: {n}건" for r, n in by.most_common()]
    out += ["", "<b>종목</b> (상위 10)"]
    out += [f"· {t} ×{n}" for t, n in tick.most_common(10)]
    out += ["", "이 목록을 Claude 에게 그대로 붙여넣으면 파서를 확장합니다.",
            "원문 확인: <code>backlog_misses --ticker &lt;코드&gt;</code>"]
    return "\n".join(out)


def backlog_for(dart, ticker: str, year: int, reprt_code: str) -> float | None:
    """해당 분기 정기보고서의 수주잔고(원). 없으면 None.

    ⚠️ 원문을 40MB 로 받는다 — 「매출 및 수주상황」은 목차상 II.사업의 내용
    뒤라 기본 3MB 상한 밖으로 밀리고, 그러면 **공시하는 회사도 '없음'으로
    오판된다**(2026-08-17 프로브로 확인)."""
    if not dart:
        return None
    try:
        from bot.dart_feed import _DOC_TEXT_MAX_FULL, _fetch_doc_text
        # ⚠️ 후보를 **순서대로** 시도한다. 가장 최근 접수건에 문서가 없는
        # 경우가 있어(한화에어로 사업보고서·1분기보고서 실측: document.xml 이
        # `status=014 파일이 존재하지 않습니다`) 1건만 보면 원본이 가려진다.
        reps = dart.find_periodic_reports(ticker, year, reprt_code)
        if not reps:
            rep = dart.find_periodic_report(ticker, year, reprt_code)
            reps = [rep] if rep and rep.get("rcept_no") else []
        text = ""
        for rep in reps:
            if not rep.get("rcept_no"):
                continue
            text = _fetch_doc_text(rep["rcept_no"], dart.api_key,
                                   max_bytes=_DOC_TEXT_MAX_FULL) or ""
            if text:
                break
        got = parse_backlog(text)
        if got:
            return got["value"]
        # 실사용이 곧 프로브 — 못 낸 이유를 남긴다(미공시류는 _log_miss 가 스킵).
        _log_miss(ticker, year, reprt_code, diagnose(text or ""))
        return None
    except Exception as exc:
        log.debug("dart_backlog: %s %s/%s: %s", ticker, year, reprt_code, exc)
        return None
