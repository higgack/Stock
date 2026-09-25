"""관세청 「수출입총괄(GW)」 — 월별 한국 수출·수입 총액(매크로 카드 원천, 실수 #413).

data.go.kr 15102108 · `getNewtradeList`(serviceKey · strtYymm · endYymm) →
item: `year` · `expDlr` · `impDlr` · `balPayments` · `expCnt` · `impCnt`.
키는 NOAH 공용 `DATA_GO_KR_API_KEY`(`bot.env_keys` 단일 헬퍼, #23).

⚠️ 왜 ECOS 가 아니라 여기인가(사용자 2026-09-25 "관세청으로 바꿔줘 원천을").
카드의 공표 규약은 '관세청 통관 확정 익월 15일 전후' 인데 원천은 그 확정치를
**재게시**하는 ECOS(901Y118)였다. VM 캐시 30개로 날짜별로 잰 결과 ECOS 는 7월분을
2026-09-03(관측월 종료 +34일)에야 실었다 — 규약과 원천이 한 단계 어긋나 매달 열흘가량
⚠ 지연이 떴다. 원천을 관세청으로 옮겨 규약과 원천을 맞춘다.

  · ECOS 는 **계열 통째 폴백**이다 — 한 차트에 두 원천을 섞지 않는다(#240·#33).
    폴백하면 카드가 그 사실과 사유를 적는다(#43·#136).
  · 금액 단위는 ECOS 와 **겹치는 달의 비**로 검산한다 — 같은 확정치를 재게시하므로
    비는 1 이어야 한다. 어긋나면 관세청 값을 쓰지 않는다: 단위를 가정한 채 화면에
    올리면 1000배 틀린 숫자가 조용히 뜬다(#139·#34).
  · 당월 행은 **미완결**이라 버린다(월말 전엔 확정치가 있을 수 없다, #40).
  · 금액이 **0 인 달은 미확정**이다 — 같은 관세청 GW 계열이 확정 전 달을 0 으로
    미리 채워 보낸다(`trade/customs_scan._latest_move` 실측 선례 — 2026-06-01 '0 으로
    랭킹이 지워진' 사고). 나라 전체 수출·수입이 0 일 수는 없으므로 0 은 값이 아니다.

실측(2026-09-25 VM — 이 서비스를 키로 직접 쳤다):
  · 경로는 `Newtrade/getNewtradeList`(대문자) — HTTP 200 · resultCode 00. 소문자 `newtrade`
    는 **HTTP 400 + resultCode 12**('해당 오픈API 서비스가 없거나 폐기됨')다. 재기 전엔 경로
    사다리를 뒀으나(#345 찾음 ≠ 동작함) 쟀으므로 죽은 후보는 지운다(§작업 원칙).
  · 조회기간은 **1년 이내**만 받는다 — 13개월 창(202508~202608)은 HTTP 200 + resultCode 99
    ('시작과 종료의 조회기간은 1년이내 기간만 가능합니다'). 99 는 기다려도 안 풀린다 — 우리
    창 크기를 고칠 일이라 일시 오류로 세지 않는다(#82). 창은 6개월씩 나눈다.
  · body 엔 `items` 뿐이다(numOfRows·totalCount 칸이 없다) — 한 달 창은 그 달 + '총계' 2행.
  · 금액은 **USD 원값**(9월 누계 expDlr 93,402,261,770 = 934억$ — ECOS 7월 989.6억$ 와 같은
    자릿수) · balPayments = expDlr − impDlr 가 정확히 맞는다. 그래서 SCALE 1e-8(USD→억$).
  · **진행 중인 달도 값을 준다** — 2026-09-25 에 9월분 934억$(월말 전이라 부분 누계다). 그래서
    당월을 빼는 것은 실측으로도 필요하다(#40). 0 으로 미리 채운 달은 이 서비스에선 안 보였다 —
    형제 계열의 선례라 방어로만 남긴다.
  · 지난달분은 익월 1일부터 온다(ECOS 재게시보다 한 달 이상 빠르다) — 관세청 확정치는 **익월
    15일 전후**라 그 전엔 **잠정치**다. 카드가 그 사실을 적는다(`provisional`, #34·#375).
재지 않은 것(#165): 6개월 창이 잘리지 않는지(쪽 크기 칸이 없으니 잘린다면 조용히 잘린다) ·
지난달분이 익월 1일 몇 시부터 온전한지. VM 에서 `cd ~/stock && .venv/bin/python -m
bot.customs_trade_client --check` 가 창마다 받은 달 행 수까지 찍어 잰다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from bot.env_keys import env_key as _env_key

log = logging.getLogger("bot.customs_trade")

_BASE = "https://apis.data.go.kr/1220000"
# 경로는 하나다 — 2026-09-25 실측으로 소문자 후보는 HTTP 400 + resultCode 12 였다(모듈 설명).
PATH = "Newtrade/getNewtradeList"
_TIMEOUT = 10
_UA = "Mozilla/5.0 (NOAH macro)"
# 한 요청의 조회 달 수. 원천 상한은 **1년 이내**(실측 — 13개월 창은 resultCode 99). 응답에
# 쪽 크기(numOfRows)·총 건수 칸이 없어 잘려도 알 길이 없으므로(#280) 6개월씩 나눈다 —
# '총계' 까지 7행이라 data.go.kr 흔한 기본 쪽 크기(10행) 안이다. 잘림 여부는 `--check` 가 잰다.
CHUNK_MONTHS = 6
# 관세청 월별 확정치 공표일(익월 15일 전후 — `macro_cadence` export_amt 근거와 같다). 그 전의
# 지난달 값은 잠정치다(모듈 설명).
CONFIRM_DAY = 15
MONTHS = 13                # 카드 12점 + 여유 1(최신 달이 아직 안 나온 날에도 12점)
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "customs_trade"
_CACHE_VER = 1
_CACHE_TTL_S = 6 * 3600
# 실패는 **짧게만** 믿는다(#303·#161) — 메모리 전용이라 재시작하면 다시 묻는다.
_FAIL_TTL_S = 600
RATIO_TOL = 0.03           # ECOS 대조 허용 — 같은 확정치 재게시라 개정 차이만
SCALE = 1e-8               # 원천 USD → 카드 억$ (형제 선례 USD · ECOS 대조가 지킨다)
_OK_CODES = ("00", "0", "000")
# data.go.kr 공통 결과코드 중 **그 창에 자료가 없다**(03 NODATA) — 오류가 아니다. 확정 전
# 달만 담긴 창이 이 답을 받을 수 있는데, 그걸 오류로 보면 계열 전체가 폴백한다.
_NODATA_CODES = ("03",)
# 기다리면 풀리는 결과코드(원천 쪽 오류·시간초과·일일 한도) — 키·파라미터·미등록·만료
# (10·11·20·30·31·32 …)는 우리가 고칠 것이라 처방이 반대다(#82). 나머지는 '응답 오류'.
# ⚠️ 99 는 공통 규약상 '기타 에러'지만 이 서비스는 **요청 검증 오류**에 쓴다(2026-09-25 실측:
# 13개월 창 → '조회기간은 1년이내'). 기다려도 안 풀리므로 여기 넣으면 우리가 고칠 것을
# 감사가 ⚠️(기다림)로 덮는다(#260 을 거꾸로).
_TRANSIENT_CODES = ("01", "02", "04", "05", "22")
# 12(NO_OPENAPI_SERVICE) = 그 **경로**에 서비스가 없다 — 실측으로는 HTTP 400 에 실려 왔다
# (소문자 경로). 상태코드만 보면 '파라미터 오류' 로 읽히므로 본문의 코드가 갈래를 정한다.
_NO_SERVICE_CODES = ("12",)

# 카드 계열 키 → 원천 필드. 키는 ECOS 시리즈 키와 같다 — 공표 규약(`macro_cadence`)과
# ECOS 폴백이 같은 이름을 쓴다(#38).
FIELDS = {"export_amt": "exp", "import_amt": "imp"}

_fail: dict[tuple[str, str], tuple[float, str, str]] = {}
# 폴백 경고는 (계열, 갈래)마다 프로세스당 한 번 — 카드는 30초마다 다시 그려져, 원천이
# 막힌 동안 같은 줄이 저널을 덮으면 다른 사실이 묻힌다(#260). 건마다는 debug 로.
_warned: set[tuple[str, str]] = set()


# ── 순수 헬퍼 ────────────────────────────────────────────────────────────
def _kst_today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()


def ym_shift(ym: str, k: int) -> str:
    """'YYYYMM' 을 k 개월 옮긴다(순수)."""
    n = int(ym[:4]) * 12 + int(ym[4:6]) - 1 + k
    return f"{n // 12:04d}{n % 12 + 1:02d}"


def month_count(start: str, end: str) -> int:
    """[start, end](YYYYMM, 양끝 포함)의 달 수(순수)."""
    return ((int(end[:4]) - int(start[:4])) * 12 + int(end[4:6]) - int(start[4:6]) + 1)


def windows(start: str, end: str, size: int = CHUNK_MONTHS) -> list[tuple[str, str]]:
    """[start, end](YYYYMM, 양끝 포함) → `size` 개월 이하의 연속 창들(순수)."""
    out: list[tuple[str, str]] = []
    s = start
    while s <= end:
        e = min(ym_shift(s, size - 1), end)
        out.append((s, e))
        s = ym_shift(e, 1)
    return out


_YM_RE = re.compile(r"^(\d{4})[.\-]?(\d{2})$")


def norm_ym(raw) -> str:
    """'2026.08' · '2026-08' · '202608' → '202608'. 달 모양이 아니면 ''('총계' 등)."""
    m = _YM_RE.match(str(raw or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        return ""
    return m.group(1) + m.group(2)


def _num(v) -> Optional[float]:
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s not in ("", "-") else None
    except (TypeError, ValueError):
        return None


def _code_kind(code: str) -> str:
    """결과코드 → 실패 갈래(순수). 03 NODATA 는 '' — 오류가 아니다."""
    if code in _NODATA_CODES:
        return ""
    if code in _NO_SERVICE_CODES:
        return "경로 없음"
    return "조회 실패" if code in _TRANSIENT_CODES else "응답 오류"


def _parse(text: str) -> tuple[list[dict], str, str, str]:
    """`parse` + 원천 결과코드(없으면 ''). 비-200 응답도 본문의 코드로 갈래를 정하려고
    코드를 따로 돌려준다(`status_reason`)."""
    t = (text or "").lstrip()
    if not t:
        return [], "빈 응답", "응답 오류", ""
    items: list[dict] = []
    if t.startswith("<"):
        try:
            root = ET.fromstring(t)
        except ET.ParseError as exc:
            return [], f"XML 형식 오류({exc})", "응답 오류", ""
        # 결과코드는 **문서 어디에 있든** 읽는다 — 2026-09-25 VM 프로브가 `.//resultCode` 로
        # 400·200 응답 둘 다에서 코드를 읽었다(header 밑이라고 가정하지 않는다)
        code = (root.findtext(".//resultCode") or "").strip()
        msg = (root.findtext(".//resultMsg") or "").strip()
        if not code:
            auth = (root.findtext(".//returnAuthMsg") or "").strip()
            err = (root.findtext(".//errMsg") or "").strip()
            rc = (root.findtext(".//returnReasonCode") or "").strip()
            if auth or err:
                return ([], f"게이트웨이 오류 {rc} {auth or err}".strip(),
                        _code_kind(rc) or "응답 오류", rc)
        for it in root.iter("item"):
            items.append({c.tag: (c.text or "").strip() for c in it})
    elif t.startswith("{"):
        try:
            doc = json.loads(t)
        except ValueError as exc:
            return [], f"JSON 형식 오류({exc})", "응답 오류", ""
        doc = doc.get("response", doc) if isinstance(doc, dict) else {}
        hdr = doc.get("header") if isinstance(doc.get("header"), dict) else {}
        code = str(hdr.get("resultCode") or "").strip()
        msg = str(hdr.get("resultMsg") or "").strip()
        body = doc.get("body") if isinstance(doc.get("body"), dict) else {}
        its = body.get("items")
        its = its.get("item") if isinstance(its, dict) else its
        if isinstance(its, dict):
            its = [its]
        items = [i for i in (its or []) if isinstance(i, dict)]
    else:
        return [], f"XML·JSON 이 아닌 응답({t[:60]!r})", "응답 오류", ""
    if code and code not in _OK_CODES:
        kind = _code_kind(code)
        if not kind:
            return [], "", "", code            # 03 NODATA — 이 창엔 자료가 없다
        return [], f"resultCode={code} {msg}".strip(), kind, code
    rows: list[dict] = []
    for it in items:
        ym = norm_ym(it.get("year"))
        if not ym:
            continue                       # '총계' 등 달이 아닌 행
        rows.append({"ym": ym, "exp": _num(it.get("expDlr")),
                     "imp": _num(it.get("impDlr")), "bal": _num(it.get("balPayments"))})
    return rows, "", "", code


def parse(text: str) -> tuple[list[dict], str, str]:
    """응답 본문 → (달 행들, 오류 사유, 실패 갈래). 문서상 형식은 XML 이지만 JSON 도 받는다.

    ⚠️ 오류 봉투는 **두 벌**이다(#352) — 서비스의 `resultCode` 와 게이트웨이의
    `cmmMsgHeader`(인증키 미등록 등). 한쪽만 보면 다른 쪽 오류가 '행 없음' 으로 둔갑한다.
    갈래는 결과코드가 정한다(`_code_kind`) — 한도 초과(22)와 키 미등록(30)은 같은 '오류'
    지만 처방이 반대다(#82). 03(NODATA)은 오류가 아니라 **빈 창**이다.
    """
    return _parse(text)[:3]


def status_reason(status: Optional[int], text: str) -> tuple[str, str]:
    """HTTP 200 이 아닌 응답 → (실패 갈래, 사유)(순수).

    본문에 결과코드가 있으면 **그 코드가 갈래를 정한다** — 2026-09-25 실측: 없는 경로는 404 가
    아니라 HTTP 400 + resultCode 12('해당 오픈API 서비스가 없거나 폐기됨')로 왔다. 상태만 보면
    '경로가 없다' 가 '파라미터 오류' 로 읽히고, 원천이 적어 보낸 문장은 버려진다(#82·#352)."""
    _rows, err, kind, code = _parse(text)
    if code and err:
        return kind or "응답 오류", (f"HTTP {status} — {err}"
                                   + (f" — 경로 {PATH}" if kind == "경로 없음" else ""))
    if status == 404:
        return "경로 없음", f"HTTP 404 — 경로 {PATH}"
    # 429·5xx 는 기다리면 풀린다 — 키·파라미터 오류(4xx)와 처방이 반대다(#82)
    kind = "조회 실패" if status == 429 or (status or 0) >= 500 else "응답 오류"
    return kind, f"HTTP {status} — {(text or '')[:160]}"


def provisional(ym: str, today: Optional[date] = None) -> bool:
    """그 달(YYYYMM) 값이 아직 **잠정**인가(순수) — 관세청 확정치 공표일(익월 `CONFIRM_DAY`일)
    당일까지는 잠정으로 본다. 그날 확정이 나와도 그 하루는 '잠정' 이라 적는 쪽이 안전한
    방향이다(확정을 잠정이라 부르는 건 틀려도 잠정을 확정이라 부르는 것보다 가볍다, #375)."""
    try:
        y, m = int(ym[:4]), int(ym[4:6])
    except (TypeError, ValueError):
        return False
    if not 1 <= m <= 12:
        return False
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return (today or _kst_today()) <= date(ny, nm, CONFIRM_DAY)


def cross_check(ours, ecos, tol: float = RATIO_TOL) -> tuple[Optional[bool], str]:
    """관세청 계열과 ECOS 계열의 **겹치는 달** 비 중앙값으로 단위·계열을 검산(순수).

    True = 같은 확정치다 · False = 단위·계열이 다르다(쓰지 말 것) · None = 겹치는 달이
    없어 못 쟀다(판정 불가를 통과로 접지 않는다 — 사유를 돌려준다, #54)."""
    b = {str(t)[:6]: v for t, v in (ecos or []) if v}
    ratios = [v / b[str(m)[:6]] for m, v in (ours or []) if str(m)[:6] in b and v is not None]
    if not ratios:
        return None, "ECOS 와 겹치는 달이 없어 단위를 대조하지 못했다"
    med = statistics.median(ratios)
    if abs(med - 1) <= tol:
        return True, f"ECOS 와 겹치는 {len(ratios)}개월 비 중앙값 {med:.4f}"
    return False, (f"관세청 값이 ECOS 의 {med:.4g}배(겹치는 {len(ratios)}개월) — "
                   "단위나 계열이 달라 보여 쓰지 않는다")


def _mask(text: str, key: str) -> str:
    """오류 문구에서 서비스 키를 지운다(§Secrets — 예외 문구가 URL 을 실을 수 있다)."""
    if not key:
        return text
    from urllib.parse import quote
    for k in {key, quote(key, safe=""), quote(key)}:
        if k:
            text = text.replace(k, "***")
    return text


# ── 네트워크 ─────────────────────────────────────────────────────────────
def _http_get(path: str, s: str, e: str, key: str) -> tuple[Optional[int], str]:
    """(HTTP 상태, 본문). 키 규약은 레포 공용(`bot/fsc_client._fetch2` 와 같다, #38) —
    이미 인코딩된 키(`%` 포함)는 그대로 싣고, 아니면 httpx 가 인코딩한다."""
    import httpx
    url = f"{_BASE}/{path}"
    q = {"strtYymm": s, "endYymm": e}
    h = {"User-Agent": _UA, "Accept": "application/xml, text/xml, */*"}
    if "%" in key:
        from urllib.parse import urlencode
        r = httpx.get(f"{url}?serviceKey={key}&{urlencode(q)}", headers=h,
                      timeout=_TIMEOUT, follow_redirects=True)
    else:
        r = httpx.get(url, params={"serviceKey": key, **q}, headers=h,
                      timeout=_TIMEOUT, follow_redirects=True)
    return r.status_code, r.text


def _cache_file(start: str, end: str) -> Path:
    return _CACHE_DIR / f"newtrade_v{_CACHE_VER}_{start}_{end}.json"


def _cache_read(start: str, end: str) -> Optional[dict]:
    p = _cache_file(start, end)
    try:
        if time.time() - p.stat().st_mtime >= _CACHE_TTL_S:
            return None
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return None                        # 어떤 바이트가 와도 캐시 미스로(#331)
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return None
    return doc


def _cache_write(start: str, end: str, doc: dict) -> None:
    p = _cache_file(start, end)
    tmp = p.with_name(f"{p.name}.tmp{os.getpid()}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)                 # 쓰다 만 파일을 읽지 않게(#379)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("customs_trade: 캐시 쓰기 실패(%s)", type(exc).__name__)
        try:
            tmp.unlink()
        except OSError:
            pass


def monthly_totals(*, months: int = MONTHS, today: Optional[date] = None,
                   fetch: Optional[Callable] = None, use_cache: bool = True,
                   key: Optional[str] = None) -> tuple[list[dict], dict]:
    """최근 `months` 개 **완결** 달의 수출·수입 총액(원천 단위 USD) →
    (행 오름차순, 진단 {"path", "attempts", "kind", "why", "cached", "dropped",
    "unconfirmed", "window"}).

    `kind` 는 실패 갈래의 짧은 이름(카드에 실린다) — 키 없음 · 조회 실패 · 경로 없음 ·
    응답 오류 · 행 없음. `why` 는 사유 원문(키는 가린다). `attempts` 는 요청마다
    (경로, 시작, 끝, HTTP 상태, 예외 이름, 받은 달들) — 창이 잘렸는지 잰다.
    `use_cache=False` 면 디스크 캐시도 실패 기억도 건너뛴다 — 진단은 **지금** 을
    재야 한다(#346·#368). 한 창이라도 실패하면 거기서 멈춘다 — 다른 창도 같은 경로·키라
    같은 답이다(#82·#279 '더 물어서 답이 바뀌나')."""
    today = today or _kst_today()
    end = ym_shift(today.strftime("%Y%m"), -1)            # 당월은 미완결 — 전월까지
    start = ym_shift(end, -(months - 1))
    info: dict = {"path": "", "attempts": [], "kind": "", "why": "", "cached": False,
                  "dropped": 0, "unconfirmed": 0, "window": (start, end)}
    key = _env_key("DATA_GO_KR_API_KEY") if key is None else key
    if not key:
        info.update(kind="키 없음", why="DATA_GO_KR_API_KEY 미설정 — ~/stock/.env 에 넣을 것")
        return [], info
    if use_cache:
        c = _cache_read(start, end)
        if c is not None:
            info.update(cached=True, path=str(c.get("path") or ""))
            return list(c["rows"]), info
        f = _fail.get((start, end))
        if f and time.monotonic() - f[0] < _FAIL_TTL_S:
            info.update(kind=f[1], why=f"직전 실패를 {_FAIL_TTL_S // 60}분 믿는다 — {f[2]}")
            return [], info
    fetch = fetch or _http_get
    rows: dict[str, dict] = {}

    def _fail_out(kind: str, why: str):
        # 한 줄로 접는다 — 감사 결산은 ❌ 줄 하나만 올리므로 사유가 줄바꿈에서 잘리면
        # 그 줄이 혼자 행동 가능하지 않다(#356 · 오류 본문은 흔히 여러 줄 HTML 이다)
        info.update(kind=kind, why=" ".join(_mask(why, key).split())[:300])
        if use_cache:
            _fail[(start, end)] = (time.monotonic(), kind, info["why"])
        return [], info

    for s, e in windows(start, end):
        try:
            status, text = fetch(PATH, s, e, key)
        except Exception as exc:                               # noqa: BLE001
            info["attempts"].append((PATH, s, e, None, type(exc).__name__, None))
            return _fail_out("조회 실패", f"{type(exc).__name__}: {exc}")
        if status != 200:
            info["attempts"].append((PATH, s, e, status, "", None))
            return _fail_out(*status_reason(status, text))
        got, err, kind = parse(text)
        info["attempts"].append((PATH, s, e, status, "", tuple(r["ym"] for r in got)))
        if err:
            return _fail_out(kind or "응답 오류",
                             err + (f" — 경로 {PATH}" if kind == "경로 없음" else ""))
        for r in got:
            rows[r["ym"]] = r
    info["path"] = PATH
    inwin = [r for ym, r in sorted(rows.items()) if start <= ym <= end]
    info["dropped"] = len(rows) - len(inwin)                  # 창 밖(당월 등) 행
    # 0 으로 미리 채운 미확정 달은 값이 아니다(모듈 설명 · trade/customs_scan 선례).
    out = [r for r in inwin if r.get("exp") or r.get("imp")]
    info["unconfirmed"] = len(inwin) - len(out)
    if not out:
        return _fail_out("행 없음", (
            f"응답의 {start}~{end} 달 행 {len(inwin)}개가 전부 0(미확정)" if inwin
            else f"응답에 {start}~{end} 달 행이 없다"))
    if use_cache:
        _cache_write(start, end, {"rows": out, "path": info["path"]})
        _fail.pop((start, end), None)
    return out, info


def series_points(key: str, **kw) -> tuple[list[tuple[str, float]], dict]:
    """카드 계열 키 → ([(YYYYMM, 억$)], 진단). 원천 단위 USD 에 `SCALE` 을 곱한다."""
    field = FIELDS.get(key)
    if not field:
        return [], {"kind": "응답 오류", "why": f"모르는 계열 {key}"}
    rows, info = kw.pop("totals", None) or monthly_totals(**kw)
    # 0 은 미확정 달이다(모듈 설명) — 한 칸만 0 인 달도 그 칸은 값이 아니다.
    return [(r["ym"], r[field] * SCALE) for r in rows if r.get(field)], info


def _ecos_points(key: str) -> list[tuple[str, float]]:
    try:
        from bot.bok_ecos_client import fetch_series_points
        return fetch_series_points(key)
    except Exception as exc:                                   # noqa: BLE001
        log.info("customs_trade: ECOS %s 대조·폴백 실패: %s", key, exc)
        return []


def card_series(key: str, *, ecos: Optional[Callable] = None, **kw) -> dict:
    """카드가 그릴 계열 — 관세청 → ECOS 대조 → 실패면 ECOS **통째** 폴백.

    → {"points": [(YYYYMM, 억$)], "src": "관세청" | "ECOS" | "", "why": 폴백 갈래(짧게,
    카드에 실린다), "detail": 사유 원문, "check": ECOS 대조 결과, "verified": 대조 판정
    (True · None=못 잼), "info": 원천 진단}.
    ⚠️ 폴백이 **조용하면 안 된다**(#42a) — 폴백하면 로그와 카드 둘 다 말한다."""
    ecos = ecos or _ecos_points
    pts, info = series_points(key, **kw)
    ecos_pts: Optional[list] = None
    kind, why, check = info.get("kind", ""), info.get("why", ""), ""
    if pts:
        ecos_pts = ecos(key)
        ok, check = cross_check(pts, ecos_pts)
        if ok is not False:
            # verified=None 이면 단위를 **못 잰 채** 관세청을 쓴다 — 판정 불가를 통과로
            # 접지 않도록 진단이 그 사실을 따로 적는다(#54)
            return {"points": pts, "src": "관세청", "why": "", "detail": "",
                    "check": check, "verified": ok, "info": info}
        kind, why = "단위 불일치", check
    if ecos_pts is None:
        ecos_pts = ecos(key)
    (log.warning if (key, kind) not in _warned else log.debug)(
        "customs_trade: %s 관세청 원천을 못 써 %s — %s", key,
        "ECOS 로 폴백" if ecos_pts else "ECOS 도 없음", why or kind)
    _warned.add((key, kind))
    return {"points": ecos_pts or [], "src": "ECOS" if ecos_pts else "",
            "why": kind, "detail": why, "check": check, "verified": None, "info": info}


# ── 진단 CLI ─────────────────────────────────────────────────────────────
def _banner() -> str:
    try:
        sig = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:10]
    except Exception as exc:                                   # noqa: BLE001
        sig = f"지문불가({type(exc).__name__})"
    return (f"# customs_trade_client --check · 코드 지문 {sig} · "
            f"인터프리터 {sys.executable}")


def _eok(usd) -> str:
    """원천 USD → '583.1억$'(진단 출력용). 없으면 '—'."""
    return f"{usd * SCALE:,.1f}억$" if usd is not None else "—"


def check() -> int:
    """카드가 쓰는 **그 경로**를 관세청 캐시 없이 한 번 태운다(#35·#252). 관세청 디스크
    캐시·실패 기억은 읽지도 쓰지도 않는다(#264 진단이 운영 상태를 바꾸면 안 된다) —
    ⚠️ 단 ECOS 대조는 카드와 **같은** ECOS 경로를 타므로 ECOS 캐시가 비었으면 채운다
    (카드가 다음에 할 일과 같다, #284 '안 쓴다' 는 무엇을 안 쓰는지까지).
    → rc 0 두 계열 다 관세청 · 1 하나라도 폴백·실패."""
    from bot.env_keys import env_source
    from bot.macro_cadence import judge
    print(_banner())
    src = env_source("DATA_GO_KR_API_KEY")
    k = _env_key("DATA_GO_KR_API_KEY")
    print(f"키 DATA_GO_KR_API_KEY = {src}"
          + ((" · 인코딩된 키" if "%" in k else " · 디코딩 키") if k else ""))
    rows, info = monthly_totals(use_cache=False)
    s, e = info["window"]
    print(f"① 요청 창 {s}~{e}(당월은 미완결이라 뺀다) · {CHUNK_MONTHS}개월씩 나눠 묻는다")
    for path, ws, we, status, exc, got in info["attempts"]:
        # 받은 달·빠진 달을 **사실만** 적는다 — 빠진 게 최신 달이면 미확정일 수 있고
        # 중간·앞 달이면 쪽 잘림이다(처방이 다르다, #82). 원인은 읽는 쪽이 가른다.
        tail = ""
        if got is not None:
            miss = [m for m in (ym_shift(ws, j) for j in range(month_count(ws, we)))
                    if m not in got]
            tail = (f" · 받은 달 행 {len(got)}/{month_count(ws, we)}"
                    + (f" · 빠진 달 {', '.join(miss)}" if miss else ""))
        print(f"   {path} {ws}~{we} → " + (f"HTTP {status}{tail}" if status is not None
                                           else f"요청 실패 {exc}"))
    if not rows:
        print(f"② ❌ 행 없음 — {info['kind']}: {info['why']}")
    else:
        print(f"② 달 행 {len(rows)}개({rows[0]['ym']}~{rows[-1]['ym']}) · 경로 "
              f"{info['path']}" + (f" · 창 밖 행 {info['dropped']}개 버림"
                                   if info["dropped"] else "")
              + (f" · 금액 0 인 미확정 달 {info['unconfirmed']}개 버림"
                 if info["unconfirmed"] else ""))
        for r in rows[-3:]:
            bal_ok = (None if None in (r["exp"], r["imp"], r["bal"])
                      else abs(r["exp"] - r["imp"] - r["bal"]) <= 1)
            print(f"   {r['ym']} 수출 {_eok(r['exp'])} · 수입 {_eok(r['imp'])}"
                  f" · 수지 {'✅ 수출−수입과 같다' if bal_ok else ('❓ 칸 없음' if bal_ok is None else '❌ 수출−수입과 다르다')}")
    rc = 0
    for key in FIELDS:
        cs = card_series(key, totals=(rows, info))
        last = cs["points"][-1][0] if cs["points"] else ""
        j = judge(key, last) if last else None
        tail = ""
        if j and j.get("expected"):
            tail = (f" · 규약상 기대 {j['expected']} " +
                    ("⚠️ 뒤처짐" if j.get("stale") else "✅ 최신"))
        if cs["src"] == "관세청":
            unit = ("✅ " if cs.get("verified") else "❓ 단위 미대조 — ") + cs["check"]
            # 최신 달이 확정 공표 전이면 카드가 '잠정' 이라 적는다 — 진단도 같은 판정을 댄다(#35)
            prov = (f" · {last} 는 잠정(확정 익월 {CONFIRM_DAY}일 전후)"
                    if last and provisional(last) else "")
            print(f"③ {key}: ✅ 카드 원천 = 관세청 · 최신 {last} · {len(cs['points'])}점 · "
                  f"{unit}{prov}{tail}")
        else:
            rc = 1
            print(f"③ {key}: ❌ 카드 원천 = {cs['src'] or '없음'}(관세청 {cs['why']}: "
                  f"{cs['detail']}) · 최신 {last or '—'}{tail}")
    return rc


def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m bot.customs_trade_client")
    ap.add_argument("--check", action="store_true",
                    help="카드 원천(관세청 수출입총괄)을 캐시 없이 재고 ECOS 와 대조")
    a = ap.parse_args(argv)
    if a.check:
        return check()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
