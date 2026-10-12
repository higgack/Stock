"""섹터 ETF 보유 종목 — 「국가별 섹터종목」(사용자 2026-10-12)의 종목 목록.

Breadth 전략이 쓰는 섹터 ETF(`market_timing._BREADTH_SECTORS` — 미국 SPDR 11 · 한국 KODEX 13)
마다 **보유 비중 상위 15종목**을 매일 한 번 받는다.

- 미국 SPDR — 운용사(State Street) 일일 보유 엑셀 `holdings-daily-us-en-{티커}.xlsx`
  (주소 형식은 tidyquant `tq_stock_list.R` 가 2024-08-16 부터 쓰는 그것, 2026-10-12 확인).
  현금·선물 행(티커 없음·`-`·`CASH_` · 섹터 'Unassigned')은 뺀다. 섹터 칸은 그 밖엔 안 본다.
- 한국 KODEX — KRX ETF 구성종목(PDF, pykrx `get_etf_portfolio_deposit_file`). KRX 로그인
  (`KRX_ID`·`KRX_PW`)이 있어야 한다. KRX 상장 목록에 없는 코드(현금·선물·채권)는 뺀다 — 잘린
  현금 코드가 6자리 숫자로 오므로 코드 모양으로는 못 가른다(`kr_listed`).

이 환경(샌드박스)에선 두 원천 다 닿지 못해 **실측하지 못했다** — 실패는 ETF 마다 사유를 남기고
화면이 그 ETF 칸에 적는다(#43·#82). 받은 목록은 하루 캐시하고, 오늘 실패하면 마지막으로 받은
목록을 **그 기준일과 함께** 보여 준다(낡았다는 사실은 화면이 말한다, #41).
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.etf_holdings")

_KST = timezone(timedelta(hours=9))
_DIR = Path(os.environ.get("HOME") or str(Path.home())) / ".tradingagents" / "world_sectors" / "holdings"
TOP_N = 15
SSGA_URL = ("https://www.ssga.com/us/en/institutional/library-content/products/fund-data/"
            "etfs/us/holdings-daily-us-en-{t}.xlsx")
_TIMEOUT = (10, 30)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_KR_CODE = re.compile(r"^\d{6}$")


def _finite(v) -> bool:
    """비중이 쓸 수 있는 수인가 — NaN·무한은 `json.dumps` 가 `NaN` 으로 써 브라우저 `JSON.parse` 가
    실패하고, 그 뒤 ETF 표가 전부 안 그려진다. 비중이 없는(None) 행은 남긴다(이름은 보인다)."""
    if v is None:
        return True
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


class HoldingsError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def etfs(market: str) -> dict[str, str]:
    """Breadth 전략의 섹터 ETF — {티커: 섹터 이름}. 단일 출처(`market_timing`)를 그대로 읽는다(#38)."""
    from bot.market_timing import _BREADTH_SECTORS
    return dict(_BREADTH_SECTORS.get(market) or {})


def etf_label(market: str) -> str:
    from bot.market_timing import _BREADTH_SOURCE_LABEL
    return _BREADTH_SOURCE_LABEL.get(market, "")


# ── 미국 SPDR(State Street 엑셀) ─────────────────────────────────────────────
# 버린 행의 갈래 — 0행이 됐을 때 '왜' 를 사유로 낸다(#82). 순서 = 판정 순서.
DROP_REASONS = {"no_ticker": "티커 없음", "cash": "현금(티커 CASH_·섹터 Unassigned)",
                "weight": "비중 못 읽음", "nonfinite": "비중이 수가 아님"}


def _weight(v) -> float:
    """비중 칸 — 숫자 또는 글자('14.48' · '14.48%' · '1,234.5'). 못 읽으면 ValueError."""
    if isinstance(v, bool):
        raise ValueError("bool")
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace(",", "")
    if t.endswith("%"):
        t = t[:-1].strip()
    return float(t)


def scan_ssga_xlsx(data: bytes) -> dict:
    """엑셀 바이트 → 판정 재료 전부(순수) — `parse_ssga_xlsx` 와 `--probe` 가 같이 쓴다(#35).

    {asof, head(머리행 원문), kept[{code,name,weight}], drops{갈래: 수}, read(머리행 아래 읽은 행),
    sample(머리행 아래 첫 행 원문)}. 머리행을 못 찾으면 head=None."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    declared = None
    try:
        # 읽기 전용 모드는 파일이 적은 '사용 범위' 만 읽는다 — 그 값이 틀린 파일은 머리행 아래가
        # 통째로 잘려 '보유 0행' 이 된다(openpyxl 문서 · 샌드박스 재현). 다시 재게 해 끝까지 읽고,
        # 적힌 범위는 남겨 `--probe` 가 이 갈래였는지 말하게 한다(SSGA 파일은 재지 못했다, #165).
        declared = ws.max_row
        ws.reset_dimensions()
    except Exception:                                          # noqa: BLE001
        pass
    res = {"asof": "", "head": None, "kept": [], "drops": dict.fromkeys(DROP_REASONS, 0),
           "read": 0, "sample": None, "declared_rows": declared, "rows": 0}
    head = None
    done = False
    for row in ws.iter_rows(values_only=True):
        res["rows"] += 1                 # 끝까지 센다 — '적힌 범위 < 실제 행' 판정의 실제 쪽
        if done:
            continue
        cells = [("" if c is None else c) for c in row]
        texts = [str(c).strip() for c in cells]
        if head is None:
            for t in texts:
                m = re.search(r"as of[:\s]+(.+)$", t, re.I)
                if m:
                    res["asof"] = m.group(1).strip()
            low = [t.lower() for t in texts]
            if "ticker" in low and "weight" in low:
                head = {t: i for i, t in enumerate(low) if t}
                res["head"] = [t for t in texts if t]
            continue
        if not any(texts):
            if res["kept"] or res["read"]:
                done = True              # 표 끝 — 아래 고지문은 읽지 않는다
            continue
        res["read"] += 1
        if res["sample"] is None:
            res["sample"] = texts

        def at(key):
            i = head.get(key)
            return texts[i] if i is not None and i < len(texts) else ""
        tk, sector = at("ticker"), at("sector")
        name = at("name") or tk
        # 섹터 칸은 판정에 안 쓴다 — 'Unassigned' 만 현금이다. 이 칸이 '-' 로 오는 파일에서
        # '-' 를 현금으로 읽으면 전 행이 버려져 ETF 가 통째로 빈다(2026-10-12 미국 11개 전부
        # '엑셀에 보유 종목 행이 없음' — 원인 후보, VM `--probe` 가 확정한다).
        if not tk or tk in ("-", "--"):
            res["drops"]["no_ticker"] += 1
            continue
        if tk.upper().startswith("CASH_") or sector.lower() == "unassigned":
            res["drops"]["cash"] += 1
            continue
        i = head.get("weight")
        try:
            w = _weight(cells[i] if i is not None and i < len(cells) else "")
        except (TypeError, ValueError):
            res["drops"]["weight"] += 1
            continue
        if not math.isfinite(w):
            res["drops"]["nonfinite"] += 1
            continue
        res["kept"].append({"code": tk.upper(), "name": name, "weight": w})
    return res


def drop_summary(drops: dict) -> str:
    return " · ".join(f"{DROP_REASONS[k]} {n}" for k, n in drops.items() if n) or "버린 행 없음"


def parse_ssga_xlsx(data: bytes, top: int = TOP_N) -> tuple[list[dict], str]:
    """엑셀 바이트 → (상위 보유 [{code, name, weight}], 기준일 문자열). 순수.

    머리 몇 줄(펀드 이름·'Holdings: As of …')이 표 위에 있다 — 'Ticker' 와 'Weight' 가 같이 있는
    행을 머리행으로 찾는다(위치로 자르지 않는다, #46). 현금·선물 행은 티커가 없거나(-) `CASH_` 로
    시작하거나 섹터가 'Unassigned' 다. 남는 행이 없으면 **왜 없는지**(읽은 행 수 · 버린 갈래)를
    사유로 올린다 — '없음' 만 말하면 원인을 못 가른다(#82)."""
    r = scan_ssga_xlsx(data)
    if r["head"] is None:
        raise HoldingsError("엑셀에서 'Ticker'·'Weight' 머리행을 못 찾음 — 파일 형식이 바뀐 것으로 보임")
    if not r["kept"]:
        log.warning("etf_holdings: SSGA 보유 0행 — 머리행 %s · 첫 행 %s", r["head"],
                    (r["sample"] or [])[:8])
        raise HoldingsError(f"엑셀에 보유 종목 행이 없음 — 읽은 행 {r['read']} · "
                            f"{drop_summary(r['drops'])}")
    out = sorted(r["kept"], key=lambda x: -x["weight"])
    return out[:top], r["asof"]


def fetch_us_raw(ticker: str, session=None) -> bytes:
    """SSGA 엑셀 바이트 — 받기만 한다(판정은 호출부). 실패는 HoldingsError(사유)."""
    import requests
    from bot.naver_diag import http_reason
    s = session or requests
    url = SSGA_URL.format(t=ticker.lower())
    try:
        r = s.get(url, headers={"user-agent": _UA}, timeout=_TIMEOUT)
    except Exception as exc:                                   # noqa: BLE001
        raise HoldingsError(http_reason(None, exc=exc)) from exc
    if getattr(r, "status_code", None) != 200:
        raise HoldingsError(http_reason(getattr(r, "status_code", None),
                                        body=getattr(r, "content", b"")))
    content = getattr(r, "content", b"") or b""
    if not content.startswith(b"PK"):
        raise HoldingsError("엑셀이 아닌 본문이 옴(로그인·차단 페이지로 보임)")
    return content


def fetch_us(ticker: str, session=None) -> tuple[list[dict], str]:
    return parse_ssga_xlsx(fetch_us_raw(ticker, session=session))


# ── 한국 KODEX(KRX PDF) ──────────────────────────────────────────────────────
# 종목이 아닌 행 — pykrx 는 KRX 가 ISIN 으로 준 코드를 `[3:9]` 로 잘라 6자리로 만든다(pykrx
# `etx/wrap.py` — "웹 서버가 ISIN 과 축약형을 혼합해서 반환"). 그래서 원화예금(KRD010010001)은
# **`010010` 이라는 6자리 숫자**로 와 '6자리 숫자면 종목' 규칙을 통과한다(코드를 읽어 확인 —
# KRX 응답은 재지 못했다, #165). 종목인지는 코드 모양이 아니라 **KRX 상장 목록**으로 가른다(#444).
_KR_CASH_NAME = re.compile(r"현금|예금")
KR_DROP_REASONS = {"not_listed": "KRX 상장 목록에 없음(현금·선물·채권 등)",
                   "code_shape": "6자리 종목코드 아님", "cash_name": "이름이 현금·예금"}
_LISTED: dict[str, tuple[float, frozenset | None, str]] = {}
_LISTED_FAIL_SEC = 1800


def kr_listed(date: str, *, now: float | None = None) -> tuple[frozenset | None, str]:
    """그날 KRX 상장 종목 코드 집합 — (집합, 출처 한 줄). 못 구하면 (None, 사유).

    Daily 기관매수가 매 거래일 받아 두는 전 종목 시가총액 목록(`daily_inst` 원자료)을 먼저 쓴다 —
    그날 또는 직전 7일 안의 가장 가까운 날(네트워크 0). 없으면 KRX 시가총액을 한 번 묻는다(1콜).
    못 구한 결과는 30분만 기억한다(ETF 13개가 같은 실패를 13번 두드리지 않게, 영구히 굳지도 않게)."""
    import time as _t
    from datetime import date as _date
    t = _t.time() if now is None else now
    hit = _LISTED.get(date)
    if hit and (hit[1] is not None or t - hit[0] < _LISTED_FAIL_SEC):
        return hit[1], hit[2]
    from bot import daily_inst as di
    got, src = None, ""
    try:
        d0 = _date.fromisoformat(date)
        for k in range(8):
            d = (d0 - timedelta(days=k)).isoformat()
            m = di._mcap_cached(d)
            if m:
                got, src = frozenset(m), f"Daily 기관매수 {d} 시가총액 목록 {len(m):,}종목"
                break
        if got is None:
            m, why, _kind = di.fetch_mcap(date)
            if m:
                got, src = frozenset(m), f"KRX {date} 시가총액 목록 {len(m):,}종목"
            else:
                src = f"KRX 상장 목록을 못 구함({why})"
    except Exception as exc:                                   # noqa: BLE001
        src = f"KRX 상장 목록을 못 구함({type(exc).__name__}: {str(exc)[:60]})"
    _LISTED[date] = (t, got, src)
    return got, src


def parse_kr_pdf(df, top: int = TOP_N, *, listed=None, drops: dict | None = None) -> list[dict]:
    """pykrx PDF DataFrame → 상위 보유(순수). 비중 열이 있으면 비중, 없으면 평가금액으로 줄 세운다.

    `listed`(KRX 상장 코드 집합)가 있으면 **그 안에 있는 코드만** 종목이다. 없으면(상장 목록을 못
    구함) 6자리 숫자 코드 + 이름이 현금·예금이 아닌 행으로 물러선다 — 그 갈래는 잘린 현금 코드를
    이름으로만 거른다. 버린 행은 `drops` 에 갈래별로 센다(0행 사유, #82)."""
    if df is None or getattr(df, "empty", True):
        return []
    cols = list(df.columns)

    def col(*names):
        for n in names:
            if n in cols:
                return n
        return None

    wcol, vcol = col("비중", "구성비중"), col("금액", "평가금액", "시가총액")
    ncol = col("구성종목명", "종목명")
    cnt = drops if drops is not None else {}
    for k in KR_DROP_REASONS:
        cnt.setdefault(k, 0)
    out = []
    for code, r in df.iterrows():
        c = str(code).strip()
        name = str(r[ncol]).strip() if ncol else c
        if listed is not None:
            if c not in listed:
                cnt["not_listed"] += 1
                continue
        elif not _KR_CODE.match(c):
            cnt["code_shape"] += 1
            continue
        elif _KR_CASH_NAME.search(name):
            cnt["cash_name"] += 1
            continue
        w = float(r[wcol]) if wcol and r[wcol] == r[wcol] else None
        v = float(r[vcol]) if vcol and r[vcol] == r[vcol] else None
        w = w if w is not None and math.isfinite(w) else None
        v = v if v is not None and math.isfinite(v) else None
        out.append({"code": c, "name": name, "weight": w, "_v": v})
    tot = sum(x["_v"] for x in out if x["_v"])
    for x in out:
        if (x["weight"] is None or x["weight"] == 0) and x["_v"] and tot > 0:
            x["weight"] = x["_v"] / tot * 100
    out.sort(key=lambda x: -(x["weight"] or 0))
    for x in out:
        x.pop("_v", None)
    return out[:top]


def fetch_kr(ticker: str, date: str) -> tuple[list[dict], str]:
    """KRX PDF — `date` = 그 거래일(YYYY-MM-DD). KRX 로그인이 없으면 실패(사유를 남긴다)."""
    try:
        from bot.pykrx_client import krx_login_ready
        if not krx_login_ready():
            from bot.env_keys import env_diag
            # 갈래(파일 못 찾음 · 키 없음 · 값 비었음 …)를 같이 적는다 — '미설정' 만 적으면
            # 이미 있는 키를 넣으러 간다(#82·#294). 값은 안 찍는다.
            raise HoldingsError("KRX_ID/KRX_PW 미설정 — KRX ETF 구성종목을 받을 수 없음 "
                                f"({env_diag('KRX_ID', 'KRX_PW') or '갈래 판정 불가'})")
        from pykrx import stock
    except HoldingsError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        raise HoldingsError(f"pykrx 없음({type(exc).__name__})") from exc
    code = ticker.split(".")[0]
    try:
        df = stock.get_etf_portfolio_deposit_file(code, date.replace("-", ""))
    except Exception as exc:                                   # noqa: BLE001
        raise HoldingsError(f"KRX ETF 구성종목 조회 실패({type(exc).__name__}: {str(exc)[:80]})") from exc
    if df is None or getattr(df, "empty", True):
        raise HoldingsError(f"KRX 가 {date} 구성종목을 주지 않음(빈 표)")
    listed, src = kr_listed(date)
    if listed is None:
        log.warning("etf_holdings: %s — 상장 목록 없이 코드 모양·이름으로 거른다(%s)", ticker, src)
    drops: dict = {}
    rows = parse_kr_pdf(df, listed=listed, drops=drops)
    if not rows:
        why = " · ".join(f"{KR_DROP_REASONS[k]} {n}" for k, n in drops.items() if n) or "버린 행 없음"
        raise HoldingsError(f"KRX {date} 구성종목 {len(df)}행 중 종목이 없음 — {why} · 대조: {src}")
    return rows, date


# ── 캐시 ───────────────────────────────────────────────────────────────────
def _path(ticker: str) -> Path:
    return _DIR / f"{ticker.replace('/', '_')}.json"


def load(ticker: str) -> dict | None:
    try:
        d = json.loads(_path(ticker).read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return None
    return d if isinstance(d, dict) else None


def _save(ticker: str, obj: dict) -> None:
    p = _path(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


RETRY_HOURS = 2
MAX_TRIES = 4


def _retry_due(cur: dict, today: str, now: datetime) -> bool:
    """오늘 실패한 ETF 를 다시 물을 때인가 — 일시 장애 한 번이 하루를 비우지 않게(#161) 하되,
    원천이 오래 죽었을 때 15분마다 두드리지도 않게(#116) `RETRY_HOURS` 간격 · 하루 `MAX_TRIES` 회."""
    if cur.get("tried_day") != today:
        return True
    if int(cur.get("tries") or 1) >= MAX_TRIES:
        return False
    last = _tried_at(cur)
    return last is None or now - last >= timedelta(hours=RETRY_HOURS)


def _tried_at(cur: dict) -> datetime | None:
    try:
        last = datetime.fromisoformat(str(cur.get("tried_at")))
    except (TypeError, ValueError):
        return None
    return last if last.tzinfo else last.replace(tzinfo=_KST)


def retry_note(h: dict, now: datetime) -> str:
    """실패한 보유 목록을 언제 다시 묻나 — 화면이 '기다리면 되나' 를 사실대로 말하게(#380)."""
    today = now.astimezone(_KST).date().isoformat()
    if h.get("tried_day") != today:
        return "다음 주기에 다시 묻습니다"
    n = int(h.get("tries") or 1)
    if n >= MAX_TRIES:
        return f"오늘 {n}회 실패 — 내일 다시 묻습니다"
    last = _tried_at(h)
    nxt = f" · 다음 시도 {(last + timedelta(hours=RETRY_HOURS)).astimezone(_KST):%H:%M}" if last else ""
    return f"오늘 {n}/{MAX_TRIES}회 실패{nxt}"


def refresh(market: str, now: datetime, kr_date: str | None = None) -> dict:
    """하루 한 번 — 오늘(KST) 받은 목록이 있으면 건너뛴다. 실패하면 옛 목록은 두고
    `error`·`tried` 만 갈아 적는다(화면이 '마지막으로 받은 목록 · 오늘 실패 사유' 를 말한다).
    실패한 날은 `RETRY_HOURS` 마다 다시 묻는다(하루 `MAX_TRIES` 회까지)."""
    today = now.astimezone(_KST).date().isoformat()
    res = {"ok": [], "failed": []}
    for t in etfs(market):
        cur = load(t) or {}
        if cur.get("fetched_day") == today or not _retry_due(cur, today, now):
            continue
        tries = int(cur.get("tries") or 1) + 1 if cur.get("tried_day") == today else 1
        try:
            if market == "US":
                rows, asof = fetch_us(t)
            else:
                if not kr_date:
                    raise HoldingsError("기준 거래일을 못 정함(달력 없음)")
                rows, asof = fetch_kr(t, kr_date)
            rows = [r for r in rows if _finite(r.get("weight"))]
            if not rows:
                raise HoldingsError("보유 종목의 비중이 전부 수가 아님(NaN·무한)")
            _save(t, {"ticker": t, "market": market, "rows": rows, "asof": asof,
                      "fetched_day": today, "tried_day": today, "tries": tries, "error": "",
                      "fetched_at": now.astimezone(_KST).isoformat(timespec="seconds")})
        except Exception as exc:                               # noqa: BLE001
            # 원천이 형식을 바꿔 파서가 다른 예외(KeyError·BadZipFile·ValueError …)를 내도 **같은
            # 실패 경로**로 — 반복문을 빠져나가면 나머지 ETF 는 한 번도 안 묻고, 저장도 백오프도
            # 없이 15분마다 같은 파일을 받는다(독립 리뷰 실측: 3주기 · XLK 3콜 · 나머지 10개 0콜).
            why = (exc.reason if isinstance(exc, HoldingsError)
                   else f"보유 목록 처리 중 예외({type(exc).__name__}: {str(exc)[:80]}) — 원천 형식 "
                        "변경 또는 파서 결함")
            cur.update({"tried_day": today, "tries": tries, "error": why,
                        "tried_at": now.astimezone(_KST).isoformat(timespec="seconds")})
            try:
                _save(t, cur)
            except Exception as werr:                          # noqa: BLE001
                log.warning("etf_holdings: %s 실패 기록을 못 씀: %s", t, werr)
            res["failed"].append(t)
            log.warning("etf_holdings: %s 보유 종목 실패(오늘 %d/%d회) — %s", t, tries, MAX_TRIES, why)
            continue
        res["ok"].append(t)
    return res


def holdings_verdict(market: str, now: datetime) -> tuple[str, str]:
    """한 나라 섹터 ETF 보유 목록 — (갈래, 한 줄). 감사·`--why` 가 같이 쓴다(#35).

    fail = 목록이 하나도 없는 ETF 가 있다(그 칸은 영영 빈다 — KRX 로그인 없음 등, 우리가 고칠 것
    #303) · stale = 오늘 갱신은 실패했지만 마지막 목록이 있다 · none = 아직 한 번도 묻지 않음 ·
    ok = 전부 목록이 있다."""
    tickers = list(etfs(market))
    if not tickers:
        return "none", "섹터 ETF 목록이 없음"
    today = now.astimezone(_KST).date().isoformat()
    have, empty, old, untried = [], [], [], []
    for t in tickers:
        h = load(t) or {}
        if h.get("rows"):
            have.append(t)
            if h.get("error") and h.get("tried_day") == today and h.get("fetched_day") != today:
                old.append(t)
        elif h.get("error"):
            empty.append((t, h["error"]))
        else:
            untried.append(t)
    n = len(tickers)
    if empty:
        t, why = empty[0]
        more = f" 외 {len(empty) - 1}개" if len(empty) > 1 else ""
        return "fail", f"보유 목록이 없는 ETF {len(empty)}/{n}개 — {t}{more}: {why}"
    if len(untried) == n:
        return "none", "아직 보유 목록을 묻지 않음"
    if old:
        return "stale", f"{len(old)}/{n}개 ETF 오늘 갱신 실패 — 마지막으로 받은 목록으로 그린다"
    return "ok", f"{len(have)}/{n}개 ETF 보유 목록 있음" + (f" · 미시도 {len(untried)}" if untried else "")


# ── 진단 ─────────────────────────────────────────────────────────────────────
def probe(tickers: list[str] | None = None, *, session=None, out=print) -> int:
    """`python -m bot.etf_holdings --probe [XLK …]` — 미국 SPDR 엑셀을 받아 머리행·첫 행·버린 갈래·
    상위 3종목을 찍는다. **읽기 전용**: 보유 캐시(`_DIR`)를 쓰지 않는다. 0행이면 원인이 '형식'
    인지 '필터' 인지 이 출력이 가른다(#82·#109). 인자가 없으면 미국 섹터 ETF 전부.
    이상 없을 때의 끝 줄: `✅ N/N개 ETF 보유 목록을 읽음`."""
    from bot.world_sectors import _src_sig
    tickers = [t.upper() for t in (tickers or list(etfs("US")))]
    out(f"# etf_holdings --probe · 지문 {_src_sig()} · 미국 SPDR(SSGA 일일 엑셀) {len(tickers)}개")
    good = 0
    for t in tickers:
        try:
            data = fetch_us_raw(t, session=session)
        except HoldingsError as e:
            out(f"❌ {t} 받기 실패 — {e.reason}")
            continue
        try:
            r = scan_ssga_xlsx(data)
        except Exception as e:                                 # noqa: BLE001
            out(f"❌ {t} 엑셀을 못 엶 — {type(e).__name__}: {str(e)[:80]}")
            continue
        out(f"── {t} · {len(data):,}B · 기준 {r['asof'] or '못 읽음'}")
        d = r.get("declared_rows")
        if d is not None and d < r["rows"]:
            out(f"   ⚠️ 파일이 적은 사용 범위 {d}행 < 실제 {r['rows']}행 — 다시 재서 끝까지 읽음"
                "(옛 판은 여기서 잘려 0행이었다)")
        out(f"   머리행: {r['head'] if r['head'] is not None else '못 찾음'}")
        out(f"   첫 행 원문: {(r['sample'] or [])[:8]}")
        out(f"   읽은 행 {r['read']} · 남은 {len(r['kept'])} · {drop_summary(r['drops'])}")
        if r["head"] is None or not r["kept"]:
            out(f"❌ {t} 보유 0행")
            continue
        top = sorted(r["kept"], key=lambda x: -x["weight"])[:3]
        out("   상위: " + " · ".join(f"{x['code']} {x['weight']:.2f}" for x in top))
        good += 1
    mark = "✅" if good == len(tickers) else "❌"
    out(f"{mark} {good}/{len(tickers)}개 ETF 보유 목록을 읽음")
    return 0 if good == len(tickers) else 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m bot.etf_holdings")
    ap.add_argument("--probe", nargs="*", metavar="티커",
                    help="미국 SPDR 보유 엑셀을 받아 보기만(아무것도 안 쓴다) — 인자 없으면 전부")
    a = ap.parse_args(argv)
    if a.probe is None:
        ap.print_help()
        return 2
    return probe(a.probe or None)


if __name__ == "__main__":
    import sys
    sys.exit(main())
