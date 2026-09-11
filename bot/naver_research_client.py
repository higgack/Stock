"""Naver Finance 종목별 리서치 리포트 scraper.

한경 컨센서스(consensus.hankyung.com) 가 2026 초 JS 렌더링으로 전환되어
정적 HTML scrape 불가 → Naver Finance 종목 리서치를 대체 소스로 활용.

⚠️ 2026-09-11 원천이 Next.js SPA 로 전환돼 **목록 HTML 이 사라졌다**(VM 실측:
121,364B 응답에 `<table` 0건). 전 시장 목록 셋(종목·산업·전략)은
`m.stock.naver.com/api/research/{company|industry|invest}` JSON 으로 갈아탔고,
옛 목록 파서는 정의상 0건이라 지웠다. **종목별**(`fetch_research(ticker)`)은
아직 옛 2단 HTML 경로다 — 그 페이지가 살아 있는지는 아직 안 쟀다(#12·#165).

2단 fetch 구조 — **종목별 조회 전용**(2026-06-08 실 HTML 검증):
  1) List 페이지 (company_list.naver?searchType=itemCode) — 종목명·제목·
     증권사·날짜·조회수만 제공. 목표가·투자의견 컬럼 없음.
  2) Detail 페이지 (company_read.naver?nid=N) — 개별 리포트에 목표가·
     투자의견이 명시적 HTML 태그로 존재:
       목표가   <em class="money"><strong>300,000</strong></em>
       투자의견 <em class="coment">Buy</em>

키 불필요 (static HTML). 12h 디스크 캐시. 같은 {date, broker, rating,
target, title} 스키마를 반환해 한경 → Naver 교체가 dashboard 와 호환.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from bot import naver_diag as _nd

log = logging.getLogger("bot.naver_research")

_BASE_URL = "https://finance.naver.com/research/company_list.naver"

# 마지막 종목 리서치 수집이 **왜** 비었나 — 호출부(market_overview)가 화면에
# 싣는다. 리스트 반환형을 바꾸면 형제 호출부(board_audit)까지 깨지므로 사유만
# 따로 둔다. 한 프로세스에서 이 수집은 렌더당 1회라 동시 실행이 없다(#117 은
# 같은 dict 를 여러 요청이 나눠 쓰는 경우 — 여기선 키가 하나뿐인 마지막 결과).
# ⚠️ 형제 목록(산업·전략)도 **같은 사유 어휘**를 쓴다 — 2026-09-11 첫 판은
# 종목 탭만 고쳐, 산업·전략 탭은 원천이 막힌 날에도 "최근 산업 리포트가
# 없습니다" 라고 거짓말했다(독립 리뷰 M2). 한 화면에서 고쳤으면 같은 계산을
# 하는 형제를 즉시 grep 할 것(#38·#147).
_LAST_MARKET_FAIL: dict = {"reason": "", "industry": "", "strategy": "",
                           "detail": ""}
# 창 절단 사실 — **실패가 아니라** 값이 있는 채로 말해야 하는 것이라 칸을
# 따로 둔다(같은 칸을 쓰면 행이 오는 순간 덮인다, 독립 리뷰 H1).
_WINDOW_NOTE: dict = {}


def last_market_fail_reason() -> str:
    """직전 `fetch_recent_research_market` 이 0건이었던 사유("" = 사유 없음).

    계산해 둔 판정을 표시까지 배선하지 않으면 없는 것과 같다(#123·#129·#189·#228)."""
    return _LAST_MARKET_FAIL.get("reason") or ""


def last_fail_reason(kind: str) -> str:
    """`market`(종목)·`industry`(산업)·`strategy`(전략) 목록의 직전 실패 사유.

    산업·전략은 사유를 **계산해 놓고 버리고** 있었다(write-only) — 화면이
    '없습니다' 라고 말하면 원천 장애가 '새 게 없음' 으로 읽힌다(#43·#52·#82).
    """
    return _LAST_MARKET_FAIL.get("reason" if kind == "market" else kind) or ""


_PAGE_CAP = 20          # 실측(v2): 세 목록 모두 한 응답에 20행. 페이징 미측정.


def window_note(raw_n: int, parsed: int, kept: int, days_back: int) -> str:
    """원천이 **한 쪽 상한**만큼 줬을 때 화면이 값과 같이 말할 사실(순수).

    두 가지를 말한다 — 창을 다 못 채웠을 수 있다는 것과, 형식이 달라 **우리가
    못 읽은 행**이 있다는 것. 둘 다 실패가 아니라 값과 같이 가는 사실이다(#43).
    페이징이 되는지는 아직 안 쟀다(#165 단정 금지): `naver_spa_probe ⑥` 이
    재고, 되면 그때 이어받기를 배선한다.

    ⚠️ `raw_n` 은 **원천이 준 행 수**여야 한다 — 파싱 뒤 수를 넘기면 못 읽은
    행 하나가 `raw_n` 을 상한 밑으로 내려 **경고가 통째로 꺼진다**(독립 리뷰
    2026-09-11 M1 실측: 20행 중 id 없는 1행 → 19행을 30일치인 양 조용히 그림).
    즉 목록을 짧게 만드는 바로 그 입력이 '짧다' 는 경고를 끄고 있었다.
    ⚠️ `kept < parsed`(읽은 행 일부가 창 밖)면 창은 이미 다 덮인 것이므로
    절단을 말하지 않는다 — 늘 뜨는 배지는 아무것도 안 재는 것과 같다(#25·#260).
    """
    if raw_n < _PAGE_CAP:
        return ""                      # 상한에 안 닿았다 = 원천에 그게 전부다
    bits = []
    if kept >= parsed:
        bits.append(f"원천이 한 번에 {raw_n}건만 줍니다 — {days_back}일 창을 "
                    "다 못 채웠을 수 있습니다(더 오래된 건 누락 가능)")
    if raw_n > parsed:                 # 계산해 둔 것을 화면까지(#123 계열)
        bits.append(f"형식이 달라 못 읽은 {raw_n - parsed}행은 뺐습니다")
    return " · ".join(bits)


def cached_window_note(n: int, days_back: int) -> str:
    """캐시에 **저장된 행 수**만 아는 자리의 사실 — 원천 원시 수는 모른다.

    저장된 것은 파싱·창 필터를 통과한 행이라 `raw_n` 을 못 잰다. 그래서 상한에
    닿았는지만 보고(그 이상은 단정하지 않는다, #165) 같은 문구를 만든다 —
    **두 캐시 층**(이 모듈 12h · `market_overview` 10분)이 같은 함수를 쓴다(#38).
    """
    return window_note(n, n, n, days_back)


def last_window_note(kind: str) -> str:
    """행은 왔지만 **창을 다 못 채웠을 때** 의 사실("" = 할 말 없음).

    ⚠️ 실패 사유와 **다른 칸**이어야 한다 — 옛 판은 같은 칸을 써서 행이
    하나라도 오면 `""` 로 덮었고(`_LAST_MARKET_FAIL["reason"] = ""`), 화면은
    사유가 없을 때만 note 를 읽었다. 그래서 30일·300행을 요청해 **20행**을
    받아도 화면이 한 마디도 안 했다(독립 리뷰 2026-09-11 실측 H1 — 창의 93%가
    조용히 사라진다, #43·#52·#45). 값이 **있어도** 말해야 하는 사실이다(#43·#45).
    """
    return _WINDOW_NOTE.get(kind) or ""


_DETAIL_URL = "https://finance.naver.com/research/company_read.naver"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "naver_research"
_CACHE_TTL_HOURS = 12
# 전체 시장 종목/산업 리포트는 장중 새 리포트가 자주 올라옴 — 빠른 반영
# (사용자 2026-06-10 "가장 빠르게"). 리스트 8p + detail ~150 GET/시간이라
# 1h 가 리스크-freshness 균형. per-ticker fetch_research 는 12h 유지.
_MARKET_TTL_HOURS = 1
_HTTP_TIMEOUT = 15
_DETAIL_WORKERS = 4

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Referer": "https://finance.naver.com/research/company_list.naver",
}

_RATING_TO_DIRECTION = {
    "매수": "buy", "Buy": "buy", "BUY": "buy", "강력매수": "buy",
    "보유": "hold", "Hold": "hold", "HOLD": "hold", "중립": "hold",
    "Neutral": "hold", "Marketperform": "hold", "Market Perform": "hold",
    "매도": "sell", "Sell": "sell", "SELL": "sell",
    "비중축소": "sell", "Underweight": "sell",
    "비중확대": "buy", "Overweight": "buy",
    "Trading Buy": "buy", "Outperform": "buy",
    "Not Rated": "",
}

_RATING_KEYWORDS = (
    "강력매수", "적극매수", "비중확대", "비중축소", "매수", "매도", "보유", "중립",
    "Strong Buy", "Trading Buy", "Outperform", "Overweight", "Accumulate",
    "Marketperform", "Market Perform", "Sector Perform",
    "Underperform", "Underweight", "Reduce",
    "Not Rated", "NR", "Coverage Initiated",
    "Buy", "Hold", "Sell", "Neutral",
)

_BROKER_SUFFIX_RE = re.compile(
    r"(증권|투자|자산운용|금융투자|리서치|캐피탈|Securities|Investment)\s*$", re.I)
_BROKER_RE = re.compile(
    r"증권|투자|자산운용|금융투자|리서치|캐피탈|Securities|Investment", re.I)

_NID_RE = re.compile(r'company_read\.naver\?nid=(\d+)')
# Detail page regex — allow arbitrary HTML tags between label and value
# (Naver uses <th>목표가</th><td><em>... table structure)
_TARGET_RE = re.compile(
    r'목표가(?:<[^>]*>|\s)*<em[^>]*>\s*<strong[^>]*>([\d,]+)</strong>', re.I)
_RATING_DETAIL_RE = re.compile(
    r'투자의견(?:<[^>]*>|\s)*<em[^>]*>([^<]+)</em>', re.I)
# Class-based fallback — match <em class="money"> / <em class="coment">
# independently (no label prefix required)
_TARGET_CLASS_RE = re.compile(
    r'<em[^>]*class=["\'][^"\']*\bmoney\b[^"\']*["\'][^>]*>\s*'
    r'<strong[^>]*>([\d,]+)</strong>', re.I)
_RATING_CLASS_RE = re.compile(
    r'<em[^>]*class=["\'][^"\']*\bcoment\b[^"\']*["\'][^>]*>'
    r'([^<]+)</em>', re.I)


def _normalize_code(ticker: str) -> Optional[str]:
    if not ticker:
        return None
    code = ticker.upper().split(".")[0]
    if code.isdigit() and len(code) == 6:
        return code
    return None


def _get2(url: str, respect_pause: bool = True, _kw: dict | None = None
          ) -> tuple[Optional[str], str]:
    """(본문, 실패 사유) — 갈래를 이름으로(#82). 성공이면 사유는 "".

    옛 `_get` 은 정지·403·타임아웃·빈본문을 `None` 하나로 뭉쳐, 리서치 탭이
    "최근 리서치 액션이 없습니다" 라고 **거짓말**했다(원천 장애인데 '새 게 없다'
    로 읽힌다 — 2026-09-11 사용자 지적 · #43·#52).

    ⚠️ `respect_pause` 는 **목록 수집 전용**이다. 종목별 `fetch_research` 는 이
    모듈에 원래 정지 게이트가 없었고, 거기에 게이트를 새로 달면 정지 중 분석이
    조용히 한경 컨센서스로 대체되어 **아카이브에 그대로 구워진다**(사유도 안
    남는다 — 독립 리뷰 2026-09-11 · #18·#43). 동작을 바꾸려면 그 substitution 을
    화면이 밝히는 것이 먼저다."""
    if respect_pause:
        try:
            from bot.finviz_client import naver_paused
            if naver_paused():
                return None, _nd.PAUSED
        except Exception:
            pass
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_HTTP_TIMEOUT,
                            **(_kw or {}))
        resp.encoding = "euc-kr"
        if resp.status_code != 200 or not resp.text:
            log.warning("naver_research: %s -> HTTP %s (%dB)", url,
                        resp.status_code, len(resp.content or b""))
            return None, _nd.http_reason(resp.status_code,
                                         len(resp.content or b""))
        return resp.text, ""
    except Exception as exc:
        log.warning("naver_research: fetch failed for %s: %s", url, exc)
        return None, _nd.http_reason(None, exc=exc)


def _get(url: str, **kwargs) -> Optional[str]:
    """본문만 — 사유가 필요한 호출부는 `_get2` 를 쓴다. 정지 게이트는 **타지 않는다**
    (이 모듈의 종전 동작 그대로 — `_get2` 독스트링의 ⚠️ 참조)."""
    return _get2(url, respect_pause=False, _kw=kwargs)[0]


def _cell_texts(row_html: str) -> list[str]:
    """Strip a <tr> into a list of plain-text cell values."""
    raw_cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html,
                           re.DOTALL | re.I)
    out = []
    for inner in raw_cells:
        txt = re.sub(r"<[^>]+>", " ", inner)
        txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&")
               .replace("&middot;", "·"))
        out.append(" ".join(txt.split()).strip())
    return out


# ------------------------------------------------------------------
# Stage 1: list page → report metadata + nid links
# ------------------------------------------------------------------

def _parse_list_page(html: str, cutoff) -> list[dict]:
    """Parse the list page to extract report metadata + nid links.

    Actual columns: 종목명 | 제목(nid link) | 증권사 | 첨부 | 작성일 | 조회수
    """
    rows: list[dict] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", html,
                               re.DOTALL | re.I):
        nid_m = _NID_RE.search(row_html)
        if not nid_m:
            continue
        nid = nid_m.group(1)

        cells = _cell_texts(row_html)
        if len(cells) < 4:
            continue

        # date — YY.MM.DD
        date_str = None
        for c in cells:
            m = re.search(r"(\d{2})\.(\d{2})\.(\d{2})", c)
            if m:
                yy, mm, dd = m.group(1), m.group(2), m.group(3)
                date_str = f"{int(yy) + 2000}-{mm}-{dd}"
                break
        if not date_str:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue

        # broker — prefer suffix-anchored, then loose fallback
        broker = ""
        for c in cells:
            if c and len(c) <= 22 and _BROKER_SUFFIX_RE.search(c):
                broker = c
                break
        if not broker:
            for c in cells:
                if c and len(c) <= 22 and _BROKER_RE.search(c):
                    broker = c
                    break

        # title — from the nid link text
        title_m = re.search(
            r'company_read\.naver\?nid=\d+[^"]*"[^>]*>([^<]+)',
            row_html, re.I)
        title = title_m.group(1).strip()[:80] if title_m else ""
        if not title:
            for c in sorted(cells, key=len, reverse=True):
                if c and c != broker and not re.fullmatch(r"[\d,.\-/\s]+", c):
                    title = c[:80]
                    break

        rows.append({
            "nid": nid,
            "title": title,
            "broker": broker,
            "analyst": "",
            "target": None,
            "rating": "",
            "date": date_str,
        })
    return rows


# ------------------------------------------------------------------
# Stage 2: detail pages → target price + rating
# ------------------------------------------------------------------

def _fetch_report_detail(nid: str) -> tuple[Optional[float], str]:
    """Fetch target price and rating from an individual report page."""
    html = _get(f"{_DETAIL_URL}?nid={nid}")
    if not html:
        return None, ""

    target: Optional[float] = None
    for pat in (_TARGET_RE, _TARGET_CLASS_RE):
        m = pat.search(html)
        if m:
            try:
                target = float(m.group(1).replace(",", ""))
            except ValueError:
                pass
            if target:
                break

    rating = ""
    for pat in (_RATING_DETAIL_RE, _RATING_CLASS_RE):
        m = pat.search(html)
        if m:
            raw = m.group(1).strip()
            for kw in _RATING_KEYWORDS:
                if kw.lower() == raw.lower():
                    rating = kw
                    break
            if not rating:
                rating = raw
            if rating:
                break

    return target, rating


# ------------------------------------------------------------------
# Market-wide recent research (전체 시장 최신 리포트 — itemCode 없이)
# ------------------------------------------------------------------

# 종목명 link: <a href="/item/main.naver?code=005930">삼성전자</a>


# ── 리서치: 네이버 JSON API (2026-09-11 SPA 전환) ──────────────────────────
# finance.naver.com 이 Next.js SPA 로 바뀌어 세 목록(종목·산업·전략)의 서버
# 렌더 표가 통째로 사라졌다(VM 실측: 117,442B 응답에 `<table` 0건).
#
# VM 실측(naver_spa_probe v2)으로 확정한 경로·모양:
#   m.stock.naver.com/api/research/{company|industry|invest|market|economy}
#   행: {researchCategory, category, itemCode, itemName, researchId, title,
#        brokerName, writeDate:"2026-09-11", readCount, endUrl}
#   · company 만 itemCode/itemName 을 준다(나머지는 그 두 키가 아예 없다).
#   · `writeDate` 는 **이미 YYYY-MM-DD** — 옛 HTML 은 YY.MM.DD 를 변환해 썼다.
#   · bond 는 404 다(형제라고 다 있는 게 아니다 — 실측으로 확인).
_RESEARCH_API = "https://m.stock.naver.com/api/research"
_RESEARCH_KINDS = {"company": "종목", "industry": "산업", "invest": "전략"}


# 파서가 버린 행 수 — 목록이 짧은 이유를 진단이 말할 수 있게 남긴다(#54·#82).
_LAST_DROPPED: dict = {}


def _abs_url(url: str) -> str:
    """절대 http(s) URL 이면 그대로, 아니면 빈 문자열(순수).

    빈 문자열이면 호출부가 옛 `*_read.naver?nid=` 조립본으로 떨어진다 —
    죽은 주소일 수 있지만 **우리 호스트로 해석되는 상대 경로보다는 낫다**.

    ⚠️ `u[:8]` 슬라이싱은 `"http://"`(호스트 없음)·`"https:/host/x"`(슬래시
    하나)를 통과시켰다 — 앞엣것은 화면에 **죽은 링크**를 만든다(리뷰 L1).
    모양을 손으로 재지 말고 파서에 맡길 것(#26·#155).
    """
    from urllib.parse import urlparse

    u = (url or "").strip()
    try:
        parts = urlparse(u)
    except ValueError:                 # 포트 자리에 글자 등 — 주소가 아니다
        return ""
    return u if parts.scheme in ("http", "https") and parts.netloc else ""


def research_rows_from_json(rows: object, kind: str) -> list[dict]:
    """네이버 리서치 JSON → 기존 스키마 그대로(순수).

    반환 키는 **옛 HTML 파서와 동일**하다 — 화면·캐시·상세 보강이 그 키를
    읽으므로 여기서 이름을 바꾸면 한쪽이 조용히 빈칸이 된다(#34·#38).
    날짜가 없는 행은 버린다(정렬·윈도 판정의 기준이라 없으면 못 쓴다).

    ⚠️ `researchId` 가 없는 행도 버린다 — 호출부가 `nid` 로 중복을 거르므로
    빈 nid 가 둘이면 **둘째부터 전부 같은 행으로 보여** 목록이 한 줄로
    쪼그라든다(독립 리뷰 2026-09-11 실측: 20행 → 1행, 사유는 `""`).
    행 모양은 `company` 에서 실측했고 `industry`/`invest` 는 그 키가 온다는
    보장을 **재지 않았다** — 그래서 조용히 버리지 않고 세어서 말한다(#54).
    """
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    dropped = {"date": 0, "title": 0, "nid": 0}
    for r in rows:
        if not isinstance(r, dict):
            continue
        date_str = str(r.get("writeDate") or "").strip()
        if len(date_str) != 10 or date_str[4] != "-":
            dropped["date"] += 1
            continue
        title = str(r.get("title") or "").strip()[:80]
        if not title:
            dropped["title"] += 1
            continue
        nid = str(r.get("researchId") or "").strip()
        if not nid:
            dropped["nid"] += 1
            continue
        item = {"nid": nid,
                "broker": str(r.get("brokerName") or "").strip(),
                "title": title, "date": date_str,
                # 원천이 주는 링크를 그대로 보존한다 — 옛 `*_read.naver?nid=`
                # 는 SPA 전환으로 죽은 주소다. 우리가 조립하면 사용자가 클릭해
                # 빈 페이지를 본다(#150 우리가 그걸 다 쓰고 있나).
                # ⚠️ **절대 URL 일 때만** — 상대 경로가 오면 우리 대시보드
                # 호스트로 해석돼 전 링크가 엉뚱한 곳을 가리킨다. 실측한 건
                # `company` 뿐이라 나머지 형태는 보장이 아니다(#50·#155).
                "url": _abs_url(str(r.get("endUrl") or "").strip())}
        if kind == "company":
            item.update(code=str(r.get("itemCode") or "").strip(),
                        name=str(r.get("itemName") or "").strip(), rating="")
        else:
            item["category"] = str(r.get("researchCategory")
                                   or r.get("category") or "").strip()
        out.append(item)
    if any(dropped.values()):
        log.warning("naver_research %s: %d행 중 %d행 버림(날짜 %d·제목 %d·id %d)",
                    kind, len(rows), sum(dropped.values()),
                    dropped["date"], dropped["title"], dropped["nid"])
        _LAST_DROPPED[kind] = dict(dropped)
    else:
        _LAST_DROPPED[kind] = {}
    return out


def fetch_research_json(kind: str) -> tuple[list[dict], str, int]:
    """(행, 실패 사유, **원천이 준 행 수**) — 한 목록을 JSON 으로 받는다.

    빈 리스트는 실패가 아니다(#54). 세 번째 값이 필요한 이유는 `window_note`
    독스트링에 있다 — 파싱 뒤 수로 상한을 재면 못 읽은 행 하나가 경고를 끈다.
    """
    if kind not in _RESEARCH_KINDS:
        return [], f"모르는 목록: {kind}", 0
    raw, why = _get2_json(f"{_RESEARCH_API}/{kind}")
    if raw is None:
        return [], why, 0
    if not isinstance(raw, list):      # 목록이 아니면 계약 변경 — 0건과 다르다
        return [], _nd.shape_reason(f"{_RESEARCH_KINDS[kind]} 리서치 목록", raw), 0
    rows = research_rows_from_json(raw, kind)
    if raw and not rows:               # 행은 왔는데 한 건도 못 읽음 = 구조 변경
        return [], _nd.parse_reason(f"{_RESEARCH_KINDS[kind]} 리서치 행", len(raw),
                                 unit="행"), len(raw)
    return rows, "", len(raw)


_RESEARCH_JSON_HEADERS = dict(_HEADERS, **{
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://m.stock.naver.com/"})


def _get2_json(url: str, **kwargs) -> tuple[object, str]:
    """(파싱된 JSON, 실패 사유) — 공용 구현(`naver_diag.get_json`)에 위임한다.

    2026-09-11 리뷰 M6: 형제(`naver_sector_client`)와 복제돼 있었고 이미
    갈라져 있었다 — 이쪽만 'JSON 아님' 을 로그에 안 남겼다(#38).
    """
    return _nd.get_json(url, headers=_RESEARCH_JSON_HEADERS, log=log,
                        tag="naver_research", **kwargs)


def fetch_recent_research_market(limit: int = 25, days_back: int = 14,
                                 fetch_detail: bool = True) -> list[dict]:
    """전체 시장 최근 종목 리서치 리포트 (Naver Finance 리서치 목록).

    한경 컨센서스가 JS 렌더링으로 정적 scrape 불가 → market.html 의
    '최근 리서치 액션' KR 탭 대체 소스. Returns
    [{code, name, broker, rating, title, date}] (날짜 내림차순) —
    dashboard 의 research_kr 스키마와 호환. 키 불필요, 12h 디스크 캐시.

    ⚠️ 2026-09-11 SPA 전환 뒤로는 **페이지네이션이 없다** — JSON 이 한 번에
    20행을 주고 우리가 `days_back` 으로 거른다. 창을 다 못 채우면 그 사실을
    `last_window_note("market")` 에 남겨 화면이 말한다(독립 리뷰 H1). 페이징
    가능 여부는 아직 안 쟀다 — `naver_spa_probe ⑥` 이 재고 나서 배선한다.
    fetch_detail=True 면 각 리포트 상세에서 투자의견·목표가를 채운다."""
    cache_key = (f"naver_market_v2_{date.today().isoformat()}"
                 f"_{days_back}_{limit}.json")
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _MARKET_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                # 캐시 히트도 **창 절단 사실은 말해야** 한다 — 재시작 뒤 첫 렌더가
                # 절단된 캐시를 조용히 그리면 #43 이 그대로 재발한다. 저장 형식은
                # 그대로 두고 행 수에서 파생한다(#270 렌더타임 파생).
                _WINDOW_NOTE["market"] = cached_window_note(len(cached or []), days_back)
                return cached or []
        except Exception as exc:
            log.warning("naver_research: market cache read failed: %s", exc)

    today = date.today()
    cutoff = today - timedelta(days=days_back)
    rows: list[dict] = []
    seen_nid: set[str] = set()
    why = ""
    # 2026-09-11: 원천이 Next.js SPA 로 바뀌어 서버 렌더 표가 사라졌다(VM
    # 실측: 117,442B 응답에 `<table` 0건). 페이지네이션하던 HTML 경로는
    # 정의상 0건이라 지웠다(죽은 경로는 남기지 않는다, §작업 원칙) —
    # JSON API 가 한 번에 20행을 주므로 받고 cutoff 로 거른다.
    # ⚠️ 옛 HTML 경로는 여러 쪽을 훑어 더 긴 윈도를 채울 수 있었다. 지금은
    # 한 응답 20행이 상한이라 `days_back` 이 길면 창을 다 못 채운다 — 화면이
    # 그걸 모르면 '새 게 없다' 로 읽으므로 `_WINDOW_NOTE` 로 남겨 값과 **같이**
    # 표시한다(#52·#43·#45).
    j_rows, why, raw_n = fetch_research_json("company")
    cut = cutoff.isoformat() if hasattr(cutoff, "isoformat") else str(cutoff)
    for r in j_rows:
        if r.get("date") and r["date"] < cut:
            continue
        if r["nid"] in seen_nid:
            continue
        seen_nid.add(r["nid"])
        rows.append(r)
    # ⚠️ 창 절단은 **실패가 아니다** — 실패 칸(`why`)에 넣으면 행이 하나라도
    # 오는 순간 아래에서 `""` 로 덮여 화면이 영영 모른다(독립 리뷰 H1).
    _WINDOW_NOTE["market"] = window_note(raw_n, len(j_rows), len(rows), days_back)

    rows = rows[:limit]
    if not rows:
        # 빈 결과는 캐시하지 않음(truthy-only) — Naver 일시 차단/실패가 1h 동안
        # '리서치 없음'으로 박히던 문제(사용자 2026-06-10 '또 갑자기'). 다음
        # 호출이 재시도.
        log.info("naver_research: no recent market reports (%d-day window) — 캐시 안 함",
                 days_back)
        _LAST_MARKET_FAIL["reason"] = why
        # 이번 실행은 상세를 한 건도 안 걸었다 — 지난 실행의 수율 사유를 남기면
        # 화면이 **하지도 않은 상세 수집**을 두고 경고한다(리뷰 L3 · #165).
        _LAST_MARKET_FAIL["detail"] = ""
        return []
    _LAST_MARKET_FAIL["reason"] = ""
    _LAST_MARKET_FAIL["detail"] = ""      # 아래 블록이 이번 실행 값으로 채운다

    if fetch_detail:
        detail_map: dict[str, tuple[Optional[float], str]] = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_fetch_report_detail, r["nid"]): r["nid"]
                       for r in rows}
            for fut in as_completed(futures):
                nid = futures[fut]
                try:
                    detail_map[nid] = fut.result()
                except Exception:
                    detail_map[nid] = (None, "")
        for r in rows:
            tgt, rt = detail_map.get(r["nid"], (None, ""))
            r["rating"] = rt
            r["target"] = tgt
        # ⚠️ **수율을 잰다** — 상세 페이지(`company_read.naver`)는 아직 옛 HTML
        # 이다. 목록이 SPA 로 죽었으니 여기도 죽었을 수 있는데, 죽으면 매 수집이
        # 수십 건을 순손실로 던지고 목표가·투자의견 칸만 조용히 빈다(#79 그
        # 경로가 실제로 실행됐나 · #116 장식용 값의 비용). 0 이면 사유로 남겨
        # **화면이** 말하게 한다 — 아직 재지 않았으므로 끄지는 않는다(#12).
        # ⚠️ 옛 주석은 "`--check` 가 말하게 한다" 였는데 `check()` 는 자기
        # 단건 프로브만 돌고 이 칸을 **읽지 않는다** — 읽는 곳이 로그뿐인
        # write-only 였다(독립 리뷰 M6 · #123·#129·#189·#228 계열).
        _hit = sum(1 for t, rt2 in detail_map.values() if t or rt2)
        _LAST_MARKET_FAIL["detail"] = ("" if _hit else (
            f"상세 {len(detail_map)}건에서 목표가·투자의견을 한 건도 못 읽었습니다 "
            "— 상세 페이지도 SPA 전환됐을 수 있습니다(`--check` 로 잴 것)"))
        if not _hit and detail_map:
            log.warning("naver_research: 상세 수율 0/%d — %s",
                        len(detail_map), _LAST_MARKET_FAIL["detail"])

    out = [{
        "code": r["code"], "name": r["name"], "broker": r["broker"],
        "rating": r.get("rating", ""), "target": r.get("target"),
        "title": r["title"], "date": r["date"],
        # 원천이 준 링크가 있으면 그것 — 옛 조립 주소는 SPA 전환으로 죽었다.
        "link": r.get("url") or f"{_DETAIL_URL}?nid={r['nid']}",
    } for r in rows]

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False))
    except Exception as exc:
        log.warning("naver_research: market cache write failed: %s", exc)

    return out


# ------------------------------------------------------------------
# 산업(업종) 리포트 — m.stock.naver.com/api/research/industry
# ⚠️ 옛 `industry_list.naver` HTML 목록은 SPA 전환(2026-09-11)으로 죽었고 그
# 파서도 지웠다(§작업 원칙). 상세 주소는 `endUrl` 폴백으로만 남는다.
# ------------------------------------------------------------------

_INDUSTRY_DETAIL_URL = "https://finance.naver.com/research/industry_read.naver"


def fetch_recent_research_industry(limit: int = 80,
                                   days_back: int = 7) -> list[dict]:
    """전체 시장 최근 산업(업종) 리서치 리포트 (Naver industry_list).

    종목 리포트와 동일 윈도(기본 7일=일주일치). 산업 리포트는 단일 목표가가
    없어 detail fetch 생략(빠름). Returns [{category, broker, title, date,
    link}] 날짜 내림차순. 키 불필요, 12h 디스크 캐시."""
    cache_key = (f"naver_industry_v1_{date.today().isoformat()}"
                 f"_{days_back}_{limit}.json")
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _MARKET_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                # 캐시 히트도 **창 절단 사실은 말해야** 한다 — 재시작 뒤 첫 렌더가
                # 절단된 캐시를 조용히 그리면 #43 이 그대로 재발한다. 저장 형식은
                # 그대로 두고 행 수에서 파생한다(#270 렌더타임 파생).
                _WINDOW_NOTE["industry"] = cached_window_note(len(cached or []), days_back)
                return cached or []
        except Exception as exc:
            log.warning("naver_research: industry cache read failed: %s", exc)

    cutoff = date.today() - timedelta(days=days_back)
    rows: list[dict] = []
    seen_nid: set[str] = set()
    # 2026-09-11: 원천이 Next.js SPA 로 바뀌어 서버 렌더 표가 사라졌다(VM
    # 실측: 117,442B 응답에 `<table` 0건). 페이지네이션하던 HTML 경로는
    # 정의상 0건이라 지웠다(죽은 경로는 남기지 않는다, §작업 원칙) —
    # JSON API 가 한 번에 20행을 주므로 받고 cutoff 로 거른다.
    # ⚠️ 옛 HTML 경로는 여러 쪽을 훑어 더 긴 윈도를 채울 수 있었다. 지금은
    # 한 응답 20행이 상한이라 `days_back` 이 길면 창을 다 못 채운다 — 화면이
    # 그걸 모르면 '새 게 없다' 로 읽으므로 `_WINDOW_NOTE` 로 남겨 값과 **같이**
    # 표시한다(#52·#43·#45).
    j_rows, why, raw_n = fetch_research_json("industry")
    cut = cutoff.isoformat() if hasattr(cutoff, "isoformat") else str(cutoff)
    for r in j_rows:
        if r.get("date") and r["date"] < cut:
            continue
        if r["nid"] in seen_nid:
            continue
        seen_nid.add(r["nid"])
        rows.append(r)
    # 사유를 **계산만 하고 버리면 없는 것과 같다**(#123·#129·#189·#228 계열).
    _LAST_MARKET_FAIL["industry"] = why or ""
    # 창 절단은 실패가 아니라 **값과 같이** 말할 사실이다(독립 리뷰 H1).
    _WINDOW_NOTE["industry"] = window_note(raw_n, len(j_rows), len(rows), days_back)

    rows = rows[:limit]
    out = [{
        "category": r["category"], "broker": r["broker"],
        "title": r["title"], "date": r["date"],
        # 원천이 준 링크가 있으면 그것 — 옛 조립 주소는 SPA 전환으로 죽었다.
        "link": r.get("url") or f"{_INDUSTRY_DETAIL_URL}?nid={r['nid']}",
    } for r in rows]

    if out:  # truthy-only — 빈 결과(일시 실패) 캐시 안 함
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(out, ensure_ascii=False))
        except Exception as exc:
            log.warning("naver_research: industry cache write failed: %s", exc)

    return out


# ------------------------------------------------------------------
# 투자전략(투자정보) 리포트 — m.stock.naver.com/api/research/invest
# 산업 리포트와 동일 구조이나 분류(category) 컬럼이 없음(전략/자산배분).
# ⚠️ 2026-09-11 SPA 전환으로 옛 `invest_list.naver` HTML 목록·그 파서는 정의상
# 0건이라 지웠다(죽은 경로는 남기지 않는다, §작업 원칙·#53·#59). 상세 주소
# (`invest_read.naver`)는 원천이 `endUrl` 로 주지 않을 때만 쓰는 폴백이다.
# ------------------------------------------------------------------

_STRATEGY_DETAIL_URL = "https://finance.naver.com/research/invest_read.naver"


def fetch_recent_research_strategy(limit: int = 80,
                                   days_back: int = 7) -> list[dict]:
    """전체 시장 최근 투자전략(투자정보) 리서치 리포트 (Naver invest_list).

    종목/산업 리포트와 동일 윈도(기본 7일). 단일 목표가가 없어 detail fetch
    생략(빠름). Returns [{broker, title, date, link}] 날짜 내림차순. 키
    불필요, 12h 디스크 캐시."""
    cache_key = (f"naver_strategy_v1_{date.today().isoformat()}"
                 f"_{days_back}_{limit}.json")
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _MARKET_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                # 캐시 히트도 **창 절단 사실은 말해야** 한다 — 재시작 뒤 첫 렌더가
                # 절단된 캐시를 조용히 그리면 #43 이 그대로 재발한다. 저장 형식은
                # 그대로 두고 행 수에서 파생한다(#270 렌더타임 파생).
                _WINDOW_NOTE["strategy"] = cached_window_note(len(cached or []), days_back)
                return cached or []
        except Exception as exc:
            log.warning("naver_research: strategy cache read failed: %s", exc)

    cutoff = date.today() - timedelta(days=days_back)
    rows: list[dict] = []
    seen_nid: set[str] = set()
    # 2026-09-11: 원천이 Next.js SPA 로 바뀌어 서버 렌더 표가 사라졌다(VM
    # 실측: 117,442B 응답에 `<table` 0건). 페이지네이션하던 HTML 경로는
    # 정의상 0건이라 지웠다(죽은 경로는 남기지 않는다, §작업 원칙) —
    # JSON API 가 한 번에 20행을 주므로 받고 cutoff 로 거른다.
    # ⚠️ 옛 HTML 경로는 여러 쪽을 훑어 더 긴 윈도를 채울 수 있었다. 지금은
    # 한 응답 20행이 상한이라 `days_back` 이 길면 창을 다 못 채운다 — 화면이
    # 그걸 모르면 '새 게 없다' 로 읽으므로 `_WINDOW_NOTE` 로 남겨 값과 **같이**
    # 표시한다(#52·#43·#45).
    j_rows, why, raw_n = fetch_research_json("invest")
    cut = cutoff.isoformat() if hasattr(cutoff, "isoformat") else str(cutoff)
    for r in j_rows:
        if r.get("date") and r["date"] < cut:
            continue
        if r["nid"] in seen_nid:
            continue
        seen_nid.add(r["nid"])
        rows.append(r)
    # 사유를 **계산만 하고 버리면 없는 것과 같다**(#123·#129·#189·#228 계열).
    _LAST_MARKET_FAIL["strategy"] = why or ""
    # 창 절단은 실패가 아니라 **값과 같이** 말할 사실이다(독립 리뷰 H1).
    _WINDOW_NOTE["strategy"] = window_note(raw_n, len(j_rows), len(rows), days_back)

    rows = rows[:limit]
    out = [{
        "broker": r["broker"], "title": r["title"], "date": r["date"],
        # 원천이 준 링크가 있으면 그것 — 옛 조립 주소는 SPA 전환으로 죽었다.
        "link": r.get("url") or f"{_STRATEGY_DETAIL_URL}?nid={r['nid']}",
    } for r in rows]

    if out:  # truthy-only — 빈 결과(일시 실패) 캐시 안 함
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(out, ensure_ascii=False))
        except Exception as exc:
            log.warning("naver_research: strategy cache write failed: %s", exc)

    return out


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def fetch_research(ticker: str, days_back: int = 90) -> Optional[dict]:
    """Scrape Naver Finance 종목 리서치 리포트 목록 + 상세.

    Returns dict with same shape as hk_consensus_client.fetch_consensus:
    {target_price, rating, analyst_count, last_report_date, report_count,
     reports: [...]} or None."""
    code = _normalize_code(ticker)
    if not code:
        return None

    cache_key = f"naver_research_v2_{code}_{date.today().isoformat()}.json"
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                return cached if cached else None
        except Exception as exc:
            log.warning("naver_research: cache read failed for %s: %s",
                        code, exc)

    html = _get(_BASE_URL, params={
        "searchType": "itemCode", "itemCode": code})
    if not html:
        return None

    today = date.today()
    cutoff = today - timedelta(days=days_back)
    rows = _parse_list_page(html, cutoff)

    if not rows:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text("null")
        except Exception:
            pass
        log.info("naver_research: no recent reports for %s (%d-day window)",
                 code, days_back)
        return None

    # Parallel-fetch detail pages for target + rating
    detail_map: dict[str, tuple[Optional[float], str]] = {}
    with ThreadPoolExecutor(max_workers=_DETAIL_WORKERS) as pool:
        futures = {pool.submit(_fetch_report_detail, r["nid"]): r["nid"]
                   for r in rows}
        for fut in as_completed(futures):
            nid = futures[fut]
            try:
                detail_map[nid] = fut.result()
            except Exception:
                detail_map[nid] = (None, "")

    for r in rows:
        t, rt = detail_map.get(r["nid"], (None, ""))
        r["target"] = t
        r["rating"] = rt
        del r["nid"]

    # Aggregate
    target_vals = [r["target"] for r in rows if r["target"]]
    avg_target = sum(target_vals) / len(target_vals) if target_vals else None

    rating_counts: dict[str, int] = {}
    for r in rows:
        direction = _RATING_TO_DIRECTION.get(r["rating"], "")
        if direction:
            rating_counts[direction] = rating_counts.get(direction, 0) + 1
    dominant_rating = (max(rating_counts, key=rating_counts.get)
                       if rating_counts else "")

    distinct_analysts = len({(r["broker"], r["analyst"]) for r in rows})
    last_date = max(r["date"] for r in rows)

    result = {
        "target_price": avg_target,
        "rating": dominant_rating,
        "analyst_count": distinct_analysts,
        "last_report_date": last_date,
        "report_count": len(rows),
        "reports": sorted(rows, key=lambda r: r["date"], reverse=True)[:15],
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        log.warning("naver_research: cache write failed for %s: %s",
                    code, exc)

    return result


# ── 진단 ────────────────────────────────────────────────────────────────────
_CHECK_VER = 4        # 3 = 빈 목록·계약변경 갈래 · 4 = 상세 수율        # 진단은 버전을 찍는다(#21)


def check() -> int:
    """`--check` — 리서치 목록이 왜 비었는지 갈래로 말한다(#82).

    2026-09-11: 원천이 SPA 로 바뀌어 HTML 세 목록이 통째로 0건이 됐고, JSON
    API 로 갈아탔다. 진단도 **화면이 쓰는 그 경로**를 태운다(#35) — 옛 HTML
    을 계속 재면 고친 뒤에도 영원히 ❌ 다.
    """
    import sys

    print(f"[naver_research --check v{_CHECK_VER}]")
    print(f"① 인터프리터: {sys.executable}")
    rc, empty, company_rows = 0, [], []
    for kind, label in _RESEARCH_KINDS.items():
        rows, why, raw_n = fetch_research_json(kind)
        if kind == "company":
            company_rows = rows      # ④ 가 다시 묻지 않게(#61·#160 한 번만)
        if rows:
            newest = max((r.get("date") or "") for r in rows)
            # 버린 행을 안 세면 `✅ 19건` 이 정상으로 읽힌다 — 원천이 한 목록
            # 에서만 키를 바꾸면 **부분 유실**이 조용하다(리뷰 2026-09-11 M2:
            # `_LAST_DROPPED` 가 write-only 였다, #123·#129·#189·#228 계열).
            drop = _LAST_DROPPED.get(kind) or {}
            n_drop = sum(drop.values())
            tail = (f" · ⚠️ 원천 {raw_n}행 중 {n_drop}행 버림"
                    f"(날짜 {drop.get('date', 0)}·제목 {drop.get('title', 0)}"
                    f"·id {drop.get('nid', 0)})" if n_drop else "")
            print(f"② {label}({kind}): ✅ {len(rows)}건 · 최신 {newest}{tail}")
            continue
        if why:                        # 정지·HTTP·구조변경·계약변경 = 우리가 볼 것
            print(f"② {label}({kind}): ❌ {why}")
            rc = 1
            continue
        # 여기까지 왔으면 원천이 **빈 목록**을 준 것이다. '못 받았다' 와 뭉뚱그리면
        # 운영자를 네트워크·헤더 확인으로 보낸다(#82·#260 — 갈래는 이름으로).
        print(f"② {label}({kind}): ⚠️ 원천이 빈 목록([])을 줬습니다 "
              "— 도달·파싱은 정상이고 행이 0건입니다(원천 쪽 확인)")
        empty.append(label)
    # ── 상세 페이지(목표가·투자의견) — 목록과 **다른 경로**(옛 HTML)다.
    # 목록이 SPA 로 죽었으니 여기도 죽었는지 재야 한다. 한 건만 친다(#66
    # '없다'의 근거가 한 번의 관측인지 물을 것 — 그래서 판정이 아니라 관측으로
    # 적는다). 상세를 읽는 경로가 곧 화면의 목표가 칸이다(#35).
    nid = next((r.get("nid") for r in company_rows if r.get("nid")), "")
    if not nid:
        print("④ 상세 수율: ❓ 목록이 비어 판정 불가")
    else:
        tgt, rating = _fetch_report_detail(nid)
        if tgt or rating:
            print(f"④ 상세 수율: ✅ nid={nid} → 목표가 {tgt} · 의견 {rating or '—'}")
        else:
            print(f"④ 상세 수율: ⚠️ nid={nid} 에서 목표가·투자의견을 못 읽었다 "
                  "— 그 리포트에 없을 수도, 상세 페이지도 SPA 로 죽었을 수도 "
                  "있다(한 건 관측이라 단정하지 않는다)")
            print(f"   ↪ 주소: {_DETAIL_URL}?nid={nid}")
    if rc == 0 and not empty:
        print("⑤ 세 목록 모두 수신됨 — 화면이 비었다면 캐시·렌더를 볼 것")
    elif rc == 0:
        print(f"⑤ 도달은 전부 정상 · 빈 목록 {len(empty)}건({' · '.join(empty)}) "
              "— 우리가 고칠 것은 없습니다")
    return rc


def main(argv: list | None = None) -> int:
    """CLI 진입점. 디스패치를 `if __name__` 안에 인라인으로 두면 테스트가 못
    태워 '게이트만 꺼도 통과' 하는 눈먼 회귀가 된다(#252)."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO)
    if "--check" in args:
        return check()
    rows = fetch_recent_research_market(limit=10, fetch_detail=False)
    print(f"종목 리서치 {len(rows)}건")
    for r in rows[:5]:
        print("  ", r.get("date"), r.get("name"), r.get("broker"))
    return 0


# 엔트리포인트는 **항상 파일 끝**(#276 — 위에 두면 아래 정의에 영영 못 닿는다)
if __name__ == "__main__":
    import sys as _sys

    _sys.exit(main())
