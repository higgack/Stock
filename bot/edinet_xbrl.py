"""EDINET XBRL(CSV) 파싱 — 일본 종목의 **연차 재무 이력**.

왜 필요한가
-----------
yfinance 는 JP 연간 손익을 **4~5열**밖에 주지 않는다(2026-08-22 프로브 실측:
6758.T 5열 · 9984.T 분기는 2025-03-31 결측). PER 밴드는 최소 4점을 요구하고
'5년 분포'라는 이름값을 하려면 그보다 넉넉해야 한다 — 그래서 원천으로 간다.

핵심 관찰: **有価証券報告書(연차보고서) 한 부에 5개 사업연도가 들어 있다.**
「主要な経営指標等の推移」절이 매출·순이익·EPS·BPS 를 당기 + 과거 4기 =
5기분 싣는다(`Prior1YearDuration` … `Prior4YearDuration`). 즉 문서 **한 건**
으로 5년, **두 건**으로 10년이 된다 — 날짜를 하나씩 훑는 비용이 유계다.

왜 XBRL 이 아니라 CSV 인가
--------------------------
EDINET API v2 는 같은 문서를 `type=5` 로 **CSV(ZIP)** 로도 준다. 컬럼이
`要素ID/項目名/コンテキストID/相対年度/連結・個別/期間・時点/ユニットID/単位/値`
로 고정이라 택소노미를 해석할 필요가 없다 — DART 급 파싱을 피하는 정공법이다.
인코딩은 **UTF-16 LE(BOM)** · 구분자는 탭이다.

⚠️ 일본은 2024년 4분기보고서(docTypeCode 140)를 폐지하고 **半期報告書**(160)로
바꿨다. 그래서 이 원천에서 얻는 분기 해상도는 연도에 따라 다르다 — 분기 계열은
**여기서 만들지 않는다**(섞인 주기를 'TTM' 이라 부르면 #147 재발). 연차만 낸다.

⚠️ 캐시 키에 **파서 소스 지문**을 넣는다 — 손으로 올리는 버전 상수는 이
레포에서 네 번 잊혔다(#18·#21b·#95·#124).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

_BASE = "https://api.edinet-fsa.go.jp/api/v2"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "edinet_xbrl"
_HTTP_TIMEOUT = 60          # 문서 ZIP 은 수 MB — 일별 목록보다 넉넉히
_DOC_TYPE_ANNUAL = "120"    # 有価証券報告書


def _parse_sig() -> str:
    """이 모듈 소스의 sha1 — 파서를 고치면 캐시가 자동으로 갈린다."""
    try:
        return hashlib.sha1(
            Path(__file__).read_bytes()).hexdigest()[:8]
    except Exception:                                          # noqa: BLE001
        return "nosig"


# ── 요소 ID 매핑 ──────────────────────────────────────────────────────
# 「主要な経営指標等の推移」(jpcrp_cor) — 회계기준(JP GAAP/IFRS)과 무관하게
# 같은 이름을 쓰므로 여기가 가장 안정적이다. 연결(連結)이 없으면 개별을 쓴다.
#
# ⚠️ 이름을 **추측해서 늘리지 말 것** — 못 잡은 요소는 프로브가 원문 그대로
# 찍는다(#109 '표본 원문부터'). 늘릴 땐 그 출력에서 복사해 온다.
SUMMARY_ELEMENTS: dict[str, tuple[str, ...]] = {
    # ⚠️ 순서가 **우선순위**다(앞이 정본). 희석이 먼저, 없으면 기본주당이익 —
    # EDGAR 경로와 같은 규율이다.
    "eps": (
        "jpcrp_cor:DilutedEarningsLossPerShareIFRSSummaryOfBusinessResults",
        "jpcrp_cor:DilutedEarningsPerShareSummaryOfBusinessResults",
        "jpcrp_cor:BasicEarningsLossPerShareIFRSSummaryOfBusinessResults",
        "jpcrp_cor:BasicEarningsLossPerShareSummaryOfBusinessResults",
        "jpcrp_cor:BasicEarningsPerShareIFRSSummaryOfBusinessResults",
        "jpcrp_cor:BasicEarningsPerShareSummaryOfBusinessResults",
    ),
    "revenue": (
        "jpcrp_cor:RevenueIFRSSummaryOfBusinessResults",
        "jpcrp_cor:RevenuesIFRSSummaryOfBusinessResults",
        "jpcrp_cor:NetSalesSummaryOfBusinessResults",
    ),
    "net_income": (
        "jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
        "jpcrp_cor:NetIncomeLossSummaryOfBusinessResults",
    ),
    "bps": (
        "jpcrp_cor:EquityToAssetRatioIFRSSummaryOfBusinessResults",
        "jpcrp_cor:NetAssetsPerShareSummaryOfBusinessResults",
    ),
    "equity": (
        "jpcrp_cor:EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "jpcrp_cor:NetAssetsSummaryOfBusinessResults",
    ),
}
# ⚠️ `jpcrp_cor:OrdinaryIncomeLossSummaryOfBusinessResults`(経常利益)를 매출
# 후보에 넣었다가 **도요타 매출이 4.2조엔**으로 찍혔다(실제 ~48조). 経常利益은
# **이익 항목**이다 — 후보 목록에 넣을 때 그 항목이 정말 그 뜻인지 먼저 답할 것.

# 상대 연도 컨텍스트 → 당기로부터 몇 해 전인가.
_CTX_BACK = {
    "CurrentYearDuration": 0, "CurrentYearInstant": 0,
    "Prior1YearDuration": 1, "Prior1YearInstant": 1,
    "Prior2YearDuration": 2, "Prior2YearInstant": 2,
    "Prior3YearDuration": 3, "Prior3YearInstant": 3,
    "Prior4YearDuration": 4, "Prior4YearInstant": 4,
}
# 연결 우선. 값이 둘 다 있으면 連結(`_NonConsolidatedMember` 없는 쪽)을 쓴다.
_NONCONSOLIDATED = "NonConsolidatedMember"


def _ctx_years_back(ctx: str) -> Optional[int]:
    """컨텍스트 ID → 당기 기준 몇 해 전(0=당기). 모르는 컨텍스트면 None.

    실제 컨텍스트는 `Prior1YearDuration_NonConsolidatedMember` 처럼 접미가
    붙는다 — **앞부분으로 가른다**(이름 열거로는 새 접미를 못 잡는다, #24).
    """
    if not ctx:
        return None
    head = ctx.split("_")[0]
    return _CTX_BACK.get(head)


# ── CSV 파싱 ─────────────────────────────────────────────────────────
_CSV_COLS = ("要素ID", "項目名", "コンテキストID", "相対年度",
             "連結・個別", "期間・時点", "ユニットID", "単位", "値")


def parse_csv_bytes(raw: bytes) -> list[dict]:
    """EDINET CSV(UTF-16 LE, 탭 구분) → [{컬럼: 값}].

    ⚠️ 인코딩을 UTF-8 로 가정하면 **전 행이 조용히 깨진다** — BOM 을 보고
    고르되, 못 읽으면 빈 리스트가 아니라 예외를 올린다(조용한 실패 금지).

    ⚠️⚠️ **필드가 큰따옴표로 감싸져 온다**(2026-08-22 VM 실측: 헤더가
    `['"要素ID"', '"項目名"', …]` 으로 찍혔다). 탭으로만 쪼개면 키가
    `"要素ID"` 가 되어 `row.get("要素ID")` 가 전부 None 이고, 2,182행을
    받아 놓고 **매칭 0종**이 된다 — '원천에 없음'과 구별이 안 된다(#149).
    직접 쪼개지 말고 `csv` 에 맡긴다(따옴표 안의 탭·줄바꿈까지 처리한다).
    """
    import csv
    for enc in ("utf-16", "utf-16-le", "utf-8-sig"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if "要素ID" in text[:400]:
            break
    else:
        raise ValueError("EDINET CSV 헤더를 못 찾았다 — 인코딩/포맷 변경 의심")
    rd = csv.reader(io.StringIO(text.replace("\r\n", "\n")),
                    delimiter="\t", quotechar='"')
    try:
        header = [h.lstrip("\ufeff").strip() for h in next(rd)]
    except StopIteration:
        return []
    out: list[dict] = []
    for cells in rd:
        if not any(c.strip() for c in cells):
            continue
        if len(cells) < len(header):
            cells = list(cells) + [""] * (len(header) - len(cells))
        out.append(dict(zip(header, cells)))
    return out


def _to_number(v: str) -> Optional[float]:
    """`値` 칸 → 숫자. `－`·공백·`※` 등 비수치는 None(빈칸이 낫다, #29)."""
    s = (v or "").strip().replace(",", "").replace("△", "-").replace("▲", "-")
    if not s or s in ("－", "-", "―", "—", "NA", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _basis_of(row: dict) -> str:
    """`連結` / `個別` — 원천이 **컬럼으로 알려준다**. 컨텍스트 ID 접미로
    추측하지 말 것(제출회사 블록은 접미가 다를 수 있다)."""
    v = (row.get("連結・個別") or "").strip()
    if v in ("連結", "個別"):
        return v
    return "個別" if _NONCONSOLIDATED in (row.get("コンテキストID") or "") \
        else "連結"


def summary_series(rows: list[dict], fiscal_end: str) -> list[dict]:
    """한 유가증권보고서의 「主要な経営指標等の推移」→ 최대 5기 연차 시계열.

    `fiscal_end` = 이 문서의 **당기 결산일**(YYYY-MM-DD). 과거 기는 거기서
    한 해씩 뺀다 — 상대 연도를 위치가 아니라 **날짜로 되짚는다**(#29).

    ⚠️ **連結과 個別을 한 행에 섞지 않는다**(2026-08-22 도요타 실측: 連結은
    IFRS, 提出会社 블록은 JP GAAP 라 매출·이익 정의가 통째로 다르다). 문서에
    連結이 하나라도 있으면 **連結만** 쓰고, 그 항목에 連結이 없으면 비운다 —
    빈칸이 정의가 갈린 숫자보다 낫다(#32 '옆에 다른 출처가 놓이는가').
    連結이 아예 없는 문서(비연결 제출사)면 個別이 곧 그 회사다.

    반환: `[{"period": "YYYY-MM-DD", "revenue":…, "net_income":…,
    "eps":…, "bps":…, "equity":…, "basis": "連結"|"個別"}]` (오래된 것부터).
    """
    # 요소 ID → (키, 우선순위) — 목록에서 앞에 있을수록 정본이다.
    want: dict[str, tuple[str, int]] = {}
    for key, ids in SUMMARY_ELEMENTS.items():
        for i, eid in enumerate(ids):
            want[eid] = (key, i)
    picked = [r for r in rows if (r.get("要素ID") or "").strip() in want]
    basis = "連結" if any(_basis_of(r) == "連結" for r in picked) else "個別"
    # {years_back: {key: (priority, value)}}
    acc: dict[int, dict[str, tuple[int, float]]] = {}
    for r in picked:
        key, prio = want[(r.get("要素ID") or "").strip()]
        if _basis_of(r) != basis:
            continue
        back = _ctx_years_back((r.get("コンテキストID") or "").strip())
        if back is None:
            continue
        val = _to_number(r.get("値", ""))
        if val is None:
            continue
        cur = acc.setdefault(back, {}).get(key)
        if cur is not None and cur[0] <= prio:   # 앞선 후보가 이미 이겼다
            continue
        acc[back][key] = (prio, val)
    try:
        y, m, d = (int(x) for x in fiscal_end.split("-"))
    except Exception:                                          # noqa: BLE001
        return []
    out = []
    for back in sorted(acc, reverse=True):
        vals = {k: v for k, (_p, v) in acc[back].items()}
        if not vals:
            continue
        # 결산일을 그대로 한 해씩 되짚는다(2/29 는 28일로).
        yy = y - back
        dd = d
        if m == 2 and d == 29:
            dd = 28 if (yy % 4 or (yy % 100 == 0 and yy % 400)) else 29
        vals["period"] = f"{yy:04d}-{m:02d}-{dd:02d}"
        vals["basis"] = basis
        out.append(vals)
    return out


# ── 원천 접근 ────────────────────────────────────────────────────────
def _cache_path(name: str) -> Path:
    return _CACHE_DIR / name


def _cached_json(name: str, ttl_hours: Optional[float] = None):
    p = _cache_path(name)
    if not p.exists():
        return None
    if ttl_hours is not None:
        if (time.time() - p.stat().st_mtime) / 3600 > ttl_hours:
            return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                                   # noqa: BLE001
        log.warning("edinet_xbrl: 캐시 읽기 실패 %s — %s", name, exc)
        return None


def _put_json(name: str, value) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(name).write_text(json.dumps(value, ensure_ascii=False),
                                     encoding="utf-8")
    except Exception as exc:                                   # noqa: BLE001
        log.warning("edinet_xbrl: 캐시 쓰기 실패 %s — %s", name, exc)


def fetch_doc_csv(doc_id: str, api_key: str) -> list[dict]:
    """문서 CSV(type=5) → 파싱된 행들. 제출된 문서는 안 바뀌므로 **영구 캐시**.

    ⚠️ 캐시 키에 파서 지문을 넣는다 — 파서를 고쳤는데 출력이 한 글자도 안
    바뀌면 캐시부터 의심할 것(#21b). 지문이 있으면 의심할 필요가 없다.
    """
    name = f"csv_{doc_id}_{_parse_sig()}.json"
    hit = _cached_json(name)
    if hit is not None:
        return hit
    url = f"{_BASE}/documents/{doc_id}?type=5&Subscription-Key={api_key}"
    resp = requests.get(url, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    rows: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError(f"CSV 가 없는 ZIP — {z.namelist()[:5]}")
        # `jpcrp` 본문 파일이 본체다. 감사보고서(jpaud)·헤더는 뒤로 민다.
        names.sort(key=lambda n: (0 if "jpcrp" in n else 1, n))
        for n in names:
            rows.extend(parse_csv_bytes(z.read(n)))
    _put_json(name, rows)
    return rows


def _scan_days(cl, sec4: str, days, *, want: int, t0: float,
               budget_s: float,
               progress, label: str) -> list[dict]:
    """주어진 날짜들을 훑어 유가증권보고서(120)를 최대 `want` 건 모은다.

    `sec4` 는 EDINET 증권코드 **앞 4자리**(= 티커) — 체크디지트는 안 본다.
    """
    out: list[dict] = []
    for i, day in enumerate(days):
        if len(out) >= want:
            break
        if time.time() - t0 > budget_s:
            log.warning("edinet_xbrl: %s 탐색 예산 초과 — %d일까지 훑고 %d건",
                        label, i, len(out))
            if progress:
                progress(f"    ⏱ {label} 예산 초과 — {i}일 훑고 {len(out)}건에서 중단")
            break
        if progress and i % 10 == 0:
            progress(f"    …{label} {day.isoformat()} ({len(out)}건, "
                     f"{time.time() - t0:.0f}초)")
        for d in cl._fetch_day(day):
            if str(d.get("secCode") or "")[:4] != sec4:
                continue
            if (d.get("docTypeCode") or "") != _DOC_TYPE_ANNUAL:
                continue
            out.append({
                "doc_id": d.get("docID") or "",
                "submitted": (d.get("submitDateTime") or "")[:10],
                # 당기 결산일 — EDINET 이 문서 메타로 준다. 없으면 못 쓴다
                # (기간 라벨을 위치로 되짚으면 거짓말한다, #29).
                "period_end": d.get("periodEnd") or "",
                "filer": d.get("filerName") or "",
                "description": d.get("docDescription") or "",
            })
            if len(out) >= want:
                break
    return out


def find_annual_docs(ticker: str, api_key: str, *, max_docs: int = 2,
                     days_back: int = 200,
                     budget_s: float = 300.0,
                     progress=None) -> list[dict]:
    """유가증권보고서(120) 를 최신부터 `max_docs` 건 찾는다.

    EDINET 에는 회사별 검색 API 가 없어 **날짜를 하나씩** 훑어야 한다. 대신
    일별 목록은 전 종목 공용이라 한 번 받으면 다른 JP 종목이 공짜로 쓴다.
    문서 한 건이 5기를 담으므로 `max_docs=2` 면 10년이다.

    ⚠️ **두 번째 문서를 선형 스캔으로 찾지 않는다**(2026-08-22 VM 실측: 294일을
    훑고도 1건이라 예산을 통째로 태웠다). 유가증권보고서는 **해마다 같은 무렵**
    에 나오므로, 첫 건을 찾으면 그 제출일에서 **1년 전 ±45일** 창만 본다 —
    430일 선형(≈430 요청)이 ~90일로 줄고 보통 처음 몇 건에서 잡힌다.
    """
    from bot.edinet_client import _sec_code_for, get_edinet
    sec = _sec_code_for(ticker)
    if not sec or not api_key:
        return []
    # ⚠️ **앞 4자리로 맞춘다**. `_sec_code_for` 는 체크디지트를 '0' 으로
    # 가정하는데(그 독스트링이 스스로 "다른 9개도 시도해야 한다"고 적어 뒀다)
    # 2026-08-22 실측에서 6758.T(소니)가 200일을 훑고도 **0건**이었다 —
    # 원천에 있는 문서를 '없다'고 보고한 것이다. 티커 4자리는 고유하므로
    # 앞자리 비교는 다른 회사를 잘못 잡지 않는다.
    sec4 = sec[:4]
    cl = get_edinet()
    t0 = time.time()
    today = date.today()
    out = _scan_days(cl, sec4, [today - timedelta(days=o)
                               for o in range(days_back + 1)],
                     want=1, t0=t0, budget_s=budget_s, progress=progress,
                     label="최근")
    if not out or len(out) >= max_docs:
        return out
    # 해마다 같은 무렵을 좁게 본다. 앵커는 **직전에 찾은 문서**의 제출일 —
    # 제출일이 조금씩 밀리므로 첫 건에서만 재면 3부째부터 창을 벗어난다.
    for back in range(1, max_docs):
        try:
            y, m, d = (int(x) for x in out[-1]["submitted"].split("-"))
            anchor = date(y, m, min(d, 28)) - timedelta(days=365)
        except Exception:                                      # noqa: BLE001
            return out
        if progress:
            progress(f"    ↪ {back}년 전({anchor.isoformat()} ±45일) 창으로 이동")
        # 앵커에서 바깥쪽으로 번갈아 — 보통 며칠 안에 잡힌다.
        days = [anchor + timedelta(days=s * k)
                for k in range(46) for s in ((1, -1) if k else (1,))]
        got = _scan_days(cl, sec4, days, want=1, t0=t0, budget_s=budget_s,
                         progress=progress, label=f"{back}년 전")
        if not got:
            if progress:
                progress(f"    ↪ {back}년 전 창에서 못 찾음 — 여기서 멈춘다")
            break
        out.extend(got)
    return out


# 같은 티커를 두 요청이 동시에 데우면 원천을 두 배로 두드린다(#113).
_WARMING: set[str] = set()
_WARM_LOCK = threading.Lock()


def annual_history(ticker: str, *, years: int = 10,
                   api_key: Optional[str] = None,
                   wait: bool = False,
                   progress=None) -> list[dict]:
    """JP 종목 연차 재무 이력(오래된 것부터) — EDINET 유가증권보고서 기준.

    ⚠️ **탐색은 본 응답 경로에서 하지 않는다**(#116). EDINET 에는 회사별 검색
    API 가 없어 일별 목록을 최대 430일 훑어야 하는데, 콜드 캐시면 수백 건의
    HTTP 다. 그래서 캐시가 없으면 **빈 리스트를 즉시 돌려주고**(호출부는
    yfinance 로 폴백한다) 백그라운드가 캐시를 데운다 — 다음 조회가 곧바로
    받는다. 일별 목록은 전 종목 공용이라 두 번째 JP 종목부터는 거의 공짜다.

    `wait=True` 는 프로브·크론용 — 그 자리에서 다 훑는다.

    문서 2건(=10기)을 합치고 같은 결산일은 **최신 문서 쪽**을 쓴다(정정 반영).
    """
    if api_key is None:
        from bot.env_keys import env_key
        api_key = (env_key("EDINET_API_KEY") or "").strip()
    if not api_key:
        log.warning("edinet_xbrl: EDINET_API_KEY 미설정 — 연차 이력 불가")
        return []
    tk = (ticker or "").upper()
    name = f"annual_{tk}_{years}y_{_parse_sig()}.json"
    hit = _cached_json(name, ttl_hours=24 * 30)
    if hit is not None:
        return hit
    if not wait:
        _warm_async(tk, years, api_key)
        return []
    return _build_annual(tk, years, api_key, name, progress)


def _warm_async(ticker: str, years: int, api_key: str) -> None:
    """캐시를 백그라운드로 데운다 — 같은 티커는 한 번만(#127)."""
    key = f"{ticker}:{years}"
    with _WARM_LOCK:
        if key in _WARMING:
            return
        _WARMING.add(key)

    def _run():
        try:
            _build_annual(ticker, years, api_key,
                          f"annual_{ticker}_{years}y_{_parse_sig()}.json", None)
        except Exception as exc:                               # noqa: BLE001
            log.warning("edinet_xbrl: %s 예열 실패 — %s", ticker, exc)
        finally:
            with _WARM_LOCK:
                _WARMING.discard(key)
    threading.Thread(target=_run, daemon=True,
                     name=f"edinet-warm-{ticker}").start()


def _build_annual(ticker: str, years: int, api_key: str, name: str,
                  progress) -> list[dict]:
    """실제 탐색·파싱. 예열 스레드와 `wait=True` 가 같은 코드를 탄다(#35)."""
    docs = find_annual_docs(ticker, api_key,
                            max_docs=max(1, (years + 4) // 5),
                            budget_s=300.0, progress=progress)
    merged: dict[str, dict] = {}
    for doc in reversed(docs):            # 오래된 문서부터 → 최신이 덮는다
        pe = (doc.get("period_end") or "")[:10]
        if len(pe) != 10:
            log.warning("edinet_xbrl: %s 결산일 미상 — 문서 건너뜀 (%s)",
                        ticker, doc.get("doc_id"))
            continue
        try:
            rows = fetch_doc_csv(doc["doc_id"], api_key)
        except Exception as exc:                               # noqa: BLE001
            log.warning("edinet_xbrl: %s CSV 실패 %s — %s",
                        ticker, doc.get("doc_id"), exc)
            continue
        for rec in summary_series(rows, pe):
            merged[rec["period"]] = rec
    out = [merged[k] for k in sorted(merged)]
    # ⚠️ 아무것도 못 찾은 실행은 캐시하지 않는다 — 원천 장애 한 번이 한 달
    # 빈 표가 된다(#119 의 짝).
    if out:
        _put_json(name, out)
    return out


def eps_rows(ticker: str, *, years: int = 10, api_key: Optional[str] = None,
             wait: bool = False) -> list[tuple[str, float]]:
    """PER 밴드용 [(결산일, 주당순이익)] — EPS 가 없는 기는 뺀다.

    화면 경로는 `wait=False` 다(콜드면 빈 리스트 → yfinance 폴백 + 예열).
    """
    return [(r["period"], r["eps"]) for r in annual_history(
        ticker, years=years, api_key=api_key, wait=wait)
        if isinstance(r.get("eps"), (int, float))]
