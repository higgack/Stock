"""companiesmarketcap.com 순위 스크레이퍼 — Market cap 대시보드 (다축 임베드).

사용자 2026-07-06: "이 사이트 자체를 카피 — 우리 대시보드에 이식" +
2026-07-08: "원본 양식 그대로(로고·Today·30일 차트·국가) + Earnings/Revenue/
P/E ratio/MC gain/MC loss 도 Market Cap 처럼 박아서".

전략: 각 축의 **HTML 페이지를 원본 그대로 파싱**(로고 img·메트릭 셀 원문·
주가·Today %·30일 스파크라인 img·국가) — CSV 는 Today/차트가 없어 Market Cap
축의 최후 폴백만. 로고·스파크라인은 원본 이미지 URL 을 그대로 hot-link
(사설 대시보드 — 브라우저가 직접 원본에서 받아옴).

축당 2.5h 디스크 캐시(재생성 주기 3h 보다 짧게 — 실수 #36; 마지막 성공분
보존이라 일시 차단에도 최근 데이터 유지),
수집 실패는 WARNING 로그 + stale 플래그(silent-fail 금지). 축 간 1.5초
간격(예의상 rate-limit). 샌드박스 프록시는 403 이라 실검증은 VM 런타임.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.marketcap")

_KST = timezone(timedelta(hours=9))
_BASE = "https://companiesmarketcap.com/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_CACHE_DIR = Path.home() / ".tradingagents"
# ⚠️ 재생성 주기(_periodic_marketcap, 3h)보다 **짧아야** 한다. 같으면 사이클의
# 절반이 캐시에 걸려 "3시간 갱신" 표기가 거짓이 된다(실수 #36 — FRED 보드에서
# 같은 병으로 TTL 5h/주기 3h 를 고쳤다). 회귀가 TTL < 주기를 고정한다.
_TTL = 2.5 * 3600
# 파서 버전 — 파싱 로직 변경 시 +1 (구버전 파싱 결과 캐시 1회 무효화.
# 2026-07-08 엔티티/로고 fix 가 3h 캐시에 막혀 안 보이던 것 재발 방지).
_PARSER_V = 6   # 6 = rank-td 셀을 클래스로 식별(정수 메트릭 유실 fix, 2026-08-20)
_AXIS_GAP_SEC = 1.5     # 축 간 수집 간격(안티봇 예의)

# 임베드 축 (사용자 2026-07-08 지정 6축) — key, 필 라벨, slug, 메트릭 컬럼 라벨.
# slug 는 2026-07-06 웹검색 전수 검증분(추측 slug 금지).
EMBED_AXES = (
    ("marketcap", "Market Cap", "", "Market Cap"),
    ("earnings", "Earnings", "most-profitable-companies/", "Earnings"),
    ("revenue", "Revenue", "largest-companies-by-revenue/", "Revenue"),
    ("pe", "P/E ratio", "top-companies-by-pe-ratio/", "P/E ratio"),
    ("mc_gain", "MC gain", "top-companies-by-market-cap-gain/", "MC gain"),
    ("mc_loss", "MC loss", "top-companies-by-market-cap-loss/", "MC loss"),
)


def _cache_path(axis: str) -> Path:
    return _CACHE_DIR / f"marketcap_{axis}.json"


def _cache_read(axis: str) -> dict:
    try:
        with open(_cache_path(axis), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _cache_write(axis: str, payload: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _cache_path(axis).with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, _cache_path(axis))
    except Exception as exc:
        log.warning("marketcap cache write failed (%s): %s", axis, exc)


def _get(url: str) -> str:
    import requests
    r = requests.get(url, timeout=25, headers={
        "User-Agent": _UA, "Accept-Language": "en",
        "Referer": _BASE})
    r.raise_for_status()
    return r.text


def _abs_url(src: str) -> str:
    """이미지 src → 절대 URL (원본 hot-link)."""
    s = (src or "").strip()
    if not s:
        return ""
    if s.startswith("//"):
        return "https:" + s
    if s.startswith("/"):
        return _BASE.rstrip("/") + s
    return s


# '$ 4.769 T' — 원본 시총 셀이 <span>$</span>4.769 T 라 태그 제거 시 $와 숫자
# 사이 공백 생김(2026-07-08 VM 실측 행) → \s? 허용 + 표시 시 정규화.
# 단위: 축약(T/B/M — marketcap 등) + 풀네임(Billion — MC gain/loss 축, 실측)
_MONEY_RE = re.compile(
    r"^-?\$\s?[\d.,]+\s*(?:[TBM]|Trillion|Billion|Million)?$")
_NUM_RE = re.compile(r"^-?[\d.,]+$")                        # 12.34 (P/E 등)
_PCT_RE = re.compile(r"^[▲▼+-]?\s*[\d.,]+%$")               # 1.18% / -1.60%
# 30일 차트 = 인라인 <svg><path d="..."> (이미지 아님 — 2026-07-08 실측).
_SPARK_RE = re.compile(
    r'<td[^>]*class="[^"]*sparkline[^"]*"[^>]*>.*?<path d="([^"]+)"', re.S)
_SPARK_TD_RE = re.compile(r'<td[^>]*class="[^"]*sparkline[^"]*"[^>]*>')
_PATH_SAFE_RE = re.compile(r"^[0-9MLHVCSQTAZmlhvcsqtaz,.\s\-]+$")


def _parse_rank_rows(html: str) -> list[dict]:
    """순위 페이지 HTML → 원본 양식 행 목록.

    행: {rank, name, ticker, logo, metric(원문), price(원문), chg_pct,
    chg_dir(+1/-1/0), spark(30일 차트 img URL), country(원문 'USA' 등)}.
    메트릭/주가는 **원문 그대로**(단위 재해석 없음 — 원본 양식 유지 + 파싱
    버그 원천 차단). 구조 변경 시 빈 리스트 + 경고."""
    rows: list[dict] = []
    _saw_rank_td = False
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        # 속성값 안의 '<br/>'(ttm 정보아이콘 tooltip-title — earnings/PE 실측)
        # 가 태그제거를 중간에 끊어 셀 텍스트를 오염 → 텍스트성 속성만 제거
        # (class/src 는 보존 — 등락 부호·로고 판정에 필요).
        tr = re.sub(r'\s(?:tooltip-title|title|alt)="[^"]*"', "", tr)
        name_m = re.search(r'class="company-name"[^>]*>([^<]+)<', tr)
        if not name_m:
            continue
        # 순위변화 = rank-td 의 moves 속성(2026-07-08 실측: moves="3"/"-3")
        mv_m = re.search(r'<td[^>]*class="[^"]*rank-td[^"]*"[^>]*\smoves="(-?\d+)"', tr)
        try:
            rank_move = int(mv_m.group(1)) if mv_m else 0
        except ValueError:
            rank_move = 0
        # company-code div 는 실측(2026-07-08) 순위행에서 안에 빈
        # '<span class="rank d-none"></span>' 를 먼저 두고 그 뒤에 티커
        # 텍스트가 오는 구조라, 이전 '>([^<]+)<' 는 '>' 직후가 '<' 라 항상
        # 미매칭 → ticker 가 전 행에서 계속 빈 문자열이던 버그(2026-07-16
        # 발견 — 마켓캡 회사명 클릭 링크 기능 추가 중 rows 실측 fixture 로
        # 재현). div 전체 텍스트를 잡고 중첩 태그 제거로 티커만 남김.
        code_m = re.search(r'class="company-code"[^>]*>(.*?)</div>', tr, re.S)
        _nm = name_m.group(1).strip()
        _cd = re.sub(r"<[^>]+>", "", code_m.group(1)).strip() if code_m else ""
        # 이미지 분류는 src 내용으로만(위치 추정 금지 — 2026-07-08 실화면서
        # 즐겨찾기 ★ 아이콘이 첫 img 라 로고 자리에 별이 찍힘): 로고 = 'logo'
        # 포함 src, 30일 차트 = 'spark'/'chart' 포함 src. 그 외(별/국기 등) 무시.
        imgs = re.findall(r'<img[^>]+(?:src|data-src)="([^"]+)"', tr)
        logo = spark = ""
        for src in imgs:
            low = src.lower()
            if not logo and "logo" in low:
                logo = _abs_url(src)
            elif not spark and re.search(r"spark|chart", low):
                spark = _abs_url(src)
        # 인라인 SVG 스파크라인(원본은 CSS 로 크기/색 — path 데이터만 추출해
        # 렌더 쪽에서 재구성). path d 는 안전 문자만 통과(주입 차단).
        spark_d = ""
        spark_neg = False
        sp_m = _SPARK_RE.search(tr)
        if sp_m and _PATH_SAFE_RE.match(sp_m.group(1)):
            spark_d = re.sub(r"\s+", " ", sp_m.group(1)).strip()
            td_m = _SPARK_TD_RE.search(tr)
            spark_neg = bool(td_m and "red" in td_m.group(0))
        _tdm = list(re.finditer(r"<td([^>]*)>(.*?)</td>", tr, re.S))
        tds = [m.group(2) for m in _tdm]
        # 순위 셀은 **클래스로** 식별한다(원본: <td class="rank-td ..." >13</td>).
        # 예전엔 '순수 1~4자리 정수는 순위' 휴리스틱으로 걸렀는데, 그러면 P/E
        # 처럼 메트릭 자체가 정수인 행("27")이 통째로 버려져 그 칸이 '—' 로
        # 뜬다(2026-08-20 감사). 클래스로 아는 행에선 휴리스틱을 끈다.
        _rank_i = next((i for i, m in enumerate(_tdm)
                        if "rank-td" in (m.group(1) or "")), None)
        _saw_rank_td = _saw_rank_td or _rank_i is not None
        import html as _h
        txt = [re.sub(r"<[^>]+>", " ", t) for t in tds]
        # HTML 엔티티/NBSP 정규화 — 원본 시총 셀 '$4.792&nbsp;T' 가 매칭 안
        # 되면서 주가가 시총 컬럼으로 밀리던 실버그(2026-07-08 실화면).
        txt = [re.sub(r"\s+", " ", _h.unescape(t).replace(" ", " ")).strip()
               for t in txt]
        vals: list[str] = []          # 메트릭·주가류(원문) — 등장 순서 유지
        chg_pct = None
        chg_dir = 0
        country_cands: list[str] = []
        for i, t in enumerate(txt):
            if i == _rank_i:                       # 순위 셀 — 값이 아니다
                continue
            _numeric = _NUM_RE.match(t) and len(t) <= 12 and (
                _rank_i is not None                # 순위 셀을 아는 행 = 정수도 값
                or (t not in (str(len(rows) + 1),)
                    and not re.fullmatch(r"\d{1,4}", t)))
            if _MONEY_RE.match(t) or _numeric:
                vals.append(t)
            elif chg_pct is None and _PCT_RE.match(t):
                try:
                    chg_pct = float(re.sub(r"[^\d.]", "", t))
                except ValueError:
                    continue
                low = tds[i].lower()
                if "-" in t or "▼" in t or "down" in low or "red" in low:
                    chg_dir = -1
                    chg_pct = -chg_pct
                else:
                    chg_dir = 1
            elif (t and len(t) < 30 and not re.search(r"[\d$%]", t)
                  and _nm not in t and (not _cd or _cd not in t)):
                country_cands.append(t)
        if not vals:                   # 메트릭 없는 행(헤더 등) skip
            continue
        # '$ 4.769' → '$4.769' 표시 정규화(스팬 분리로 생긴 공백 제거)
        vals = [re.sub(r"^(-?)\$\s+", r"\1$", v) for v in vals]
        metric, price = vals[0], (vals[1] if len(vals) >= 2 else "")
        # 값이 1개뿐이고 단위(T/B/M) 없는 $금액이면 십중팔구 '주가만 잡힌'
        # 행 — 시총 컬럼에 주가를 넣지 말고 주가 슬롯으로(재발 방지 가드).
        if not price and re.match(r"^-?\$[\d.,]+$", metric):
            metric, price = "", metric
        # 순위 = 원본 셀 값 우선. 파싱 순서로 매기면 한 행이 스킵될 때마다
        # 이후 순위가 **조용히** 한 칸씩 밀린다(화면상 오류로 안 보인다).
        _site_rank = None
        if _rank_i is not None and re.fullmatch(r"\d{1,5}", txt[_rank_i]):
            _site_rank = int(txt[_rank_i])
        rows.append({
            "rank": _site_rank if _site_rank is not None else len(rows) + 1,
            "name": _nm, "ticker": _cd, "logo": logo,
            "metric": metric,
            "price": price,
            "chg_pct": chg_pct, "chg_dir": chg_dir,
            "rank_move": rank_move,
            "spark": spark, "spark_d": spark_d, "spark_neg": spark_neg,
            "country": country_cands[-1] if country_cands else "",
        })
    if not rows:
        log.warning("marketcap html: 파싱 0행 — 사이트 구조 변경 의심")
    elif not _saw_rank_td:
        # 폴백이 조용히 돌면 순위 밀림을 못 본다(실수 #42a) — 반드시 알린다.
        log.warning("marketcap html: rank-td 셀 0행 — 순위를 파싱 순서로 매김"
                    "(사이트 구조 변경 의심)")
    return rows


def _parse_csv_fallback(text: str) -> list[dict]:
    """Market Cap 축 최후 폴백 — CSV(?download=csv). Today/차트/로고 없음."""
    rows: list[dict] = []
    rdr = csv.DictReader(io.StringIO(text))
    if not rdr.fieldnames:
        return []
    fmap = {k.lower().strip(): k for k in rdr.fieldnames}

    def _col(*names):
        for n in names:
            if n in fmap:
                return fmap[n]
        return None

    c_name = _col("name")
    c_sym = _col("symbol", "ticker")
    c_mcap = _col("marketcap", "market cap", "marketcap (usd)")
    c_price = _col("price (usd)", "price")
    c_ctry = _col("country")
    if not (c_name and c_mcap):
        log.warning("marketcap csv: 예상 밖 헤더 %s", rdr.fieldnames)
        return []
    for r in rdr:
        try:
            mcap = float(str(r.get(c_mcap, "")).replace(",", "") or 0)
        except (ValueError, TypeError):
            continue
        if mcap <= 0:
            continue
        for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
            if mcap >= div:
                metric = f"${mcap / div:,.3f} {unit}"
                break
        else:
            metric = f"${mcap:,.0f}"
        _ps = str(r.get(c_price, "") or "").replace(",", "").strip() if c_price else ""
        try:
            price = f"${float(_ps):,.2f}" if _ps else ""
        except ValueError:
            price = ""
        rows.append({
            "rank": len(rows) + 1,
            "name": (r.get(c_name) or "").strip(),
            "ticker": (r.get(c_sym) or "").strip() if c_sym else "",
            "logo": "", "metric": metric, "price": price,
            "chg_pct": None, "chg_dir": 0, "spark": "",
            "country": (r.get(c_ctry) or "").strip() if c_ctry else "",
        })
    return rows


def fetch_axis(axis: str, slug: str, limit: int = 100) -> dict:
    """한 축 수집 {rows, fetched_at, source, stale}. 3h 캐시 · 실패 시 마지막
    성공분(stale=True)."""
    c = _cache_read(axis)
    now = time.time()
    if (c.get("rows") and c.get("_pv") == _PARSER_V
            and now - (c.get("ts") or 0) < _TTL):
        return {"rows": c["rows"][:limit], "fetched_at": c.get("fetched_at", ""),
                "source": c.get("source", ""), "stale": False}
    rows: list[dict] = []
    source = ""
    try:
        rows = _parse_rank_rows(_get(_BASE + slug))
        source = "html"
    except Exception as exc:
        log.warning("marketcap %s html fetch failed: %s", axis, exc)
    if not rows and axis == "marketcap":
        try:
            rows = _parse_csv_fallback(_get(_BASE + "?download=csv"))
            source = "csv"
        except Exception as exc:
            log.warning("marketcap csv fallback failed: %s", exc)
    if rows:
        # ⚠️ **초까지** 찍는다. 6축을 1.5초 간격으로 따로 긁으므로, 장이 열려
        # 있는 시장(KR·CN)의 종목은 축마다 Today% 가 소수점 아래에서 갈린다
        # (2026-08-20 실측: SK Hynix 11.19 vs 11.46, 축간 대조로 발각).
        # 분 단위로만 찍으면 여섯 탭이 같은 순간의 스냅샷처럼 보인다 —
        # 나란히 놓인 값이 어긋나면 화면이 그 이유를 말해야 한다(실수 #33).
        fetched_at = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
        _cache_write(axis, {"rows": rows, "ts": now, "_pv": _PARSER_V,
                            "fetched_at": fetched_at, "source": source})
        return {"rows": rows[:limit], "fetched_at": fetched_at,
                "source": source, "stale": False}
    if c.get("rows"):
        return {"rows": c["rows"][:limit], "fetched_at": c.get("fetched_at", ""),
                "source": c.get("source", ""), "stale": True}
    return {"rows": [], "fetched_at": "", "source": "", "stale": True}


def fetch_all_axes(limit: int = 100) -> dict:
    """임베드 6축 일괄 수집 {axis_key: {rows,...}}. 실수집(캐시 미스) 사이
    _AXIS_GAP_SEC 대기(안티봇 예의). graceful — 축별 독립 실패."""
    out: dict = {}
    for key, _lbl, slug, _mcol in EMBED_AXES:
        _c = _cache_read(key)
        fresh_cache = (_c.get("rows") and _c.get("_pv") == _PARSER_V
                       and time.time() - (_c.get("ts") or 0) < _TTL)
        out[key] = fetch_axis(key, slug, limit)
        if not fresh_cache:
            time.sleep(_AXIS_GAP_SEC)
    return out


def fetch_top_companies(limit: int = 100) -> dict:
    """(하위호환) Market Cap 축 단독."""
    return fetch_axis("marketcap", "", limit)
