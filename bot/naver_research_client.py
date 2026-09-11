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
import threading
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


_PAGE_CAP = 20          # 실측(v2): 세 목록 모두 **무인자** 한 응답에 20행.
# 안전 상한 — **리터럴로 못박는다**(#66: 자기 상수로 자기를 검증하면 상한을
# 올리는 뮤테이션이 그대로 통과한다). 옛 HTML 루프가 시장 4쪽·산업/전략 5쪽
# 이었으므로 그보다 넉넉하되 유계다.
_MAX_PAGES = 8
# `?pageSize=` 로 한 번에 받아 볼 크기. 형제 실측이 갈린다 —
# `stock.naver.com/.../upjong/list` 는 pageSize=100 이 먹었고(20→79),
# `m.stock.naver.com/front-api/domestic/stock/list` 는 **50 초과면 빈 배열**
# 이었다(#naver_ranking_client 137-140). 그래서 큰 값을 박지 않는다 — 빈 배열이
# 오면 '원천에 0건' 으로 읽혀 더 나쁘다.
_PAGE_SIZE_TRY = 50


def _first_id(rows: list) -> str:
    """목록의 첫 행 식별자(순수). 페이지 파라미터가 **무시됐는지** 가른다.

    `upjong/list` 실측에서 `?page=2` 가 무시되고 **같은 20행**이 돌아왔다
    (naver_sector_client.py:360). 행 **수**만 보면 '2쪽도 20행' 과 구별이 안
    되므로 첫 행을 본다(#25 '있다'만 묻는 검사는 눈이 먼다).
    """
    if not rows:
        return ""
    r = rows[0]
    return str((r or {}).get("nid") or "") if isinstance(r, dict) else ""


def page_stop(*, page: int, got: int, page_size: int, fresh: int,
              in_window: int, total_kept: int, limit: int,
              max_pages: int) -> str:
    """이 쪽을 받고 나서 **멈출 이유**(순수). "" = 계속.

    옛 HTML 루프(aa298583^)의 중단 조건을 그대로 옮기되 갈래를 **이름으로**
    부른다 — 처방이 다르기 때문이다(#82):
      · `page_ignored`  파라미터가 무시됐다(새 행 0) → 페이징 불가, 절단 아님이
                        아니라 **더 못 받는다**
      · `source_end`    원천이 더 안 준다(빈 쪽 / 상한 미만) → 창을 다 덮었다
      · `window_end`    이 쪽이 통째로 창 밖(오래된 쪽) → 창을 다 덮었다
      · `limit`         우리가 요청한 개수를 채웠다 → 창을 다 덮었다
      · `max_pages`     **우리 상한에 걸렸다** → 이것만 절단이다
    """
    if page > 1 and got and not fresh:
        # ⚠️ 1쪽에서 `fresh=0` 은 쪽 넘기기와 무관하다 — 행이 통째로 안 읽힌
        # 것(스키마 변경)이다. 그걸 '원천이 쪽 넘기기를 안 받는다' 라고 적으면
        # 운영자를 엉뚱한 데로 보낸다(독립 리뷰 · #292 틀린 라벨).
        return "page_ignored"
    if got == 0 or got < page_size:
        return "source_end"
    if fresh and not in_window:
        return "window_end"
    if total_kept >= limit:
        return "limit"
    if page >= max_pages:
        return "max_pages"
    return ""


def window_note(meta: dict, days_back: int) -> str:
    """창을 다 못 덮었을 때 화면이 **값과 같이** 말할 사실(순수). "" = 할 말 없음.

    ⚠️ 계약이 2026-09-12 에 바뀌었다(#222 — 옛 테스트는 지우지 않고 다시 썼다).
    옛 판정은 `raw_n >= _PAGE_CAP` 하나였다: 한 응답이 상한에 닿았으면 경고.
    페이지를 이어받기 시작하면 그 기준이 **영구 오탐**이 된다(100행을 정상
    수집해도 "한 번에 100건만 줍니다" 가 뜬다). 이제 기준은 '한 응답의 크기'가
    아니라 **'창을 다 못 덮고 멈췄나'**(`meta["stop"]`)다.

    `source_end`·`window_end`·`limit` 으로 끝났으면 원천이 가진 만큼 다 본
    것이므로 **아무 말도 안 한다** — 늘 뜨는 배지는 아무것도 안 재는 것과
    같다(#25·#260). 절단은 두 갈래뿐이다:
      · `max_pages`     우리 상한 — 더 받을 수 있는데 안 받았다
      · `page_ignored`  원천이 페이지를 안 받는다 — 더 받을 방법이 없다
    """
    if not isinstance(meta, dict):
        return ""
    bits = []
    stop = str(meta.get("stop") or "")
    pages = int(meta.get("pages") or 0)
    raw = int(meta.get("raw_total") or 0)
    if stop == "max_pages":
        bits.append(f"{days_back}일 창을 다 못 채웠습니다 — {pages}쪽 {raw}건까지 "
                    "받고 우리 상한에서 멈췄습니다(더 오래된 건 누락 가능)")
    elif stop == "fetch_failed":
        bits.append(f"쪽을 이어받다 실패해 {pages}쪽 {raw}건에서 멈췄습니다 — "
                    f"{days_back}일 창을 다 못 채웠습니다")
    elif stop == "page_ignored":
        bits.append(f"원천이 쪽 넘기기를 받지 않아 {raw}건이 전부입니다 — "
                    f"{days_back}일 창을 다 못 채웠을 수 있습니다")
    drop = int(meta.get("unreadable") or 0)
    if drop > 0:                        # 계산해 둔 것을 화면까지(#123 계열)
        bits.append(f"형식이 달라 못 읽은 {drop}행은 뺐습니다")
    return " · ".join(bits)


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

# ── 상세(목표가·투자의견): JSON 경로(2026-09-12 SPA 전환) ──────────────────
# 목록은 이미 JSON 으로 옮겼는데(2026-09-11) **상세만 옛 HTML** 이었다 —
# `company_read.naver?nid=` 도 Next.js SPA 라 `_TARGET_RE`·`_RATING_DETAIL_RE`
# 가 한 건도 안 맞는다(화면: "상세에서 목표가·투자의견을 한 건도 못 읽었습니다").
#
# 근거(추측이 아니다): 목록 JSON 이 행마다 `endUrl` 을 주는데 그 값이
#   https://m.stock.naver.com/research/company/96103
# 이고 목록 자체는 `m.stock.naver.com/api/research/company` 다 — 즉 상세의
# 같은 자리는 `api/research/company/{researchId}` 다. 실재 여부는 응답이
# 말한다(#25·#151) — 그래서 사다리로 두고 어느 단이 답했는지 기록한다.
_DETAIL_API_RUNGS = (
    ("api/research/{kind}/{id}",
     "https://m.stock.naver.com/api/research/company/{nid}"),
    ("front-api/research/{id}",
     "https://m.stock.naver.com/front-api/research/company/{nid}"),
)
# 목표가·투자의견이 **어떤 키로** 오는지는 재지 않았다 — 그래서 이름 하나에
# 걸지 않고 후보 이름 + **값의 모양**으로 찾는다(#46 위치·형태로 추정하지 말고
# 식별할 것의 반대편: 여기선 원천 계약을 모르므로 양쪽을 다 본다).
# ⚠️ `"tp"` 를 **부분문자열**로 두면 `httpUrl`·`ftpPath` 같은 키가 걸린다 —
# 짧은 힌트는 전체 일치로만 본다(#75 옆 것이 대신 만족시키는 것의 키 이름판).
_TGT_KEY_HINTS = ("targetprice", "goalprice", "target", "objective")
_TGT_KEY_EXACT = ("tp", "goal")
_OPI_KEY_HINTS = ("opinion", "rating", "invest", "grade", "recommend")
# 국내 목표가의 상식 범위 — 이걸 안 두면 `readCount`·`researchId` 가 목표가
# 자리에 앉는다(#212 값이 티커와 같으면 그건 값이 아니다).
_TGT_MIN, _TGT_MAX = 100.0, 10_000_000.0


def _num(v) -> Optional[float]:
    try:
        f = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return f if _TGT_MIN <= f <= _TGT_MAX else None


def _rating_of(v) -> str:
    """문자열이 **아는 투자의견**이면 정규화해 돌려준다. 아니면 빈 문자열."""
    raw = str(v or "").strip()
    if not raw or len(raw) > 24:
        return ""
    for kw in _RATING_KEYWORDS:
        if kw.lower() == raw.lower():
            return kw
    return ""


def detail_from_json(obj, *, _depth: int = 0) -> tuple:
    """상세 JSON → (목표가, 투자의견) (순수·재귀).

    키 이름을 모르므로 **힌트 이름 + 값 모양**을 같이 본다. 힌트에 안 걸려도
    값이 아는 투자의견 낱말이면 채택한다(원천이 `opinion` 이 아니라 `code` 로
    부를 수도 있다) — 다만 목표가는 이름 힌트 없이 숫자만 보고 채택하지
    **않는다**: 조회수·식별자가 그 자리에 앉는다(#212).
    """
    tgt: Optional[float] = None
    rating = ""
    if _depth > 6:
        return None, ""
    if isinstance(obj, dict):
        items = list(obj.items())
    elif isinstance(obj, list):
        items = [("", v) for v in obj[:200]]
    else:
        return None, ""
    for k, v in items:
        kl = str(k).lower()
        if isinstance(v, (dict, list)):
            t2, r2 = detail_from_json(v, _depth=_depth + 1)
            tgt = tgt if tgt is not None else t2
            rating = rating or r2
            continue
        if tgt is None and (any(h in kl for h in _TGT_KEY_HINTS)
                            or kl in _TGT_KEY_EXACT):
            tgt = _num(v)
        if not rating:
            if any(h in kl for h in _OPI_KEY_HINTS) or isinstance(v, str):
                rating = _rating_of(v)
    return tgt, rating


def _detail_json(nid: str) -> tuple:
    """사다리를 순서대로 실호출 → (목표가, 투자의견, 단 이름, 단별 사유).

    전멸이면 단="". 사유는 단마다 왜 못 읽었는지 — 404 와 '200 인데 아는 키가
    없다' 는 처방이 정반대다(#82). 계산해 놓고 버리면 없는 것과 같다(#123 계열).
    """
    whys: list = []
    for label, tmpl in _DETAIL_API_RUNGS:
        raw, why = _get2_json(tmpl.format(nid=nid))
        if raw is None:
            whys.append(f"{label}: {why or '수신 실패'}")
            continue
        tgt, rating = detail_from_json(raw)
        if tgt is not None or rating:
            return tgt, rating, label, whys
        whys.append(f"{label}: 200 인데 아는 키에 목표가·투자의견이 없음")
    return None, "", "", whys


# ── 상세(목표가·투자의견) **영구 캐시** ────────────────────────────────────
# 사용자 2026-09-12: "투자의견이랑 목표가도 안나오는것들 최대한 모두 나오게".
# 옛 판은 매 수집이 `_DETAIL_BUDGET`(40) 건만 걸고 나머지는 **영원히 빈칸**
# 이었다 — 창을 일주일로 넓히자 295건이 되어 260건이 상시 빈칸이다.
# 열쇠는 예산을 키우는 게 아니라 **이미 읽은 것을 다시 안 읽는 것**이다:
# 발행된 리포트의 목표가·투자의견은 **안 바뀐다**(정정본은 새 nid 로 온다).
# 그래서 **원천이 답했나**로 가른다(#82 — 같은 '빈 결과' 인데 처방이 반대다):
#   값이 있다        → 길게(발행분은 안 바뀐다)
#   답했는데 값이 없다 → 길게(그 리포트엔 없는 것이다 — NOT RATED·탐방노트)
#   못 닿았다        → 짧게(원천 장애 한 번이 영영 빈칸으로 굳으면 안 된다,
#                      #303·#161·#152)
# ⚠️ 가운데 갈래가 없으면 **수렴하지 않는다**(독립 리뷰 2026-09-12 Blocking,
# 실측): 실패 TTL(15분) < 수집 주기(1시간)라 값 없는 리포트가 매 주기 날짜
# 상위 40건을 영구 점유하고 그 아래는 한 번도 시도되지 않는다(24주기 뒤에도
# 화면 0/295 · 시도 40/295). 예산은 **캐시 미스**에만 쓰므로, 갈래를 나누면
# 몇 주기면 전 행을 한 번씩 걸고 정상 상태 비용은 신규 리포트뿐이다(#116).
# ⚠️ 단 '값이 없다' 는 **우리 파서의 판정**이라 파서가 바뀌면 무효다 — 그
# 기록에 소스 지문을 같이 실어 배포가 곧 무효화가 되게 한다(#119·#233:
# 손으로 버전을 올리는 방식은 이 레포에서 일곱 번 졌다).
_DETAIL_CACHE_NAME = "research_detail.json"
_DETAIL_TTL_OK = 30 * 24 * 3600
_DETAIL_TTL_EMPTY = 30 * 24 * 3600
_DETAIL_TTL_MISS = 15 * 60
_DETAIL_MEM: dict = {}
_DETAIL_MEM_AT: float = 0.0
_DETAIL_DIRTY = False
_DETAIL_LOCK = threading.Lock()


def _detail_cache_path() -> Path:
    return _CACHE_DIR / _DETAIL_CACHE_NAME


def _detail_cache_load() -> dict:
    """디스크 맵을 한 번만 읽어 메모리에 둔다. 못 읽으면 빈 맵(던지지 않는다).

    ⚠️ 어떤 바이트가 와도 안 던진다 — 이 맵을 읽다 예외가 나면 그걸 부르는
    화면 셋이 통째로 빈다(#331 캐시 독자 계약).
    """
    global _DETAIL_MEM, _DETAIL_MEM_AT
    with _DETAIL_LOCK:
        if _DETAIL_MEM_AT:
            return _DETAIL_MEM
        try:
            obj = json.loads(_detail_cache_path().read_text(encoding="utf-8",
                                                            errors="replace"))
            _DETAIL_MEM = obj if isinstance(obj, dict) else {}
        except Exception:                                      # noqa: BLE001
            _DETAIL_MEM = {}
        _DETAIL_MEM_AT = time.time()
        return _DETAIL_MEM


def detail_cached(nid: str) -> Optional[tuple]:
    """캐시된 (목표가, 투자의견, 경로) — 없거나 식었으면 None(순수-ish).

    성공(값이 하나라도 있음)과 실패(둘 다 없음)의 **TTL 이 다르다**.
    """
    rec = _detail_cache_load().get(str(nid))
    if not isinstance(rec, dict):
        return None
    try:
        age = time.time() - float(rec.get("at") or 0)
    except (TypeError, ValueError):
        return None
    tgt, rating = rec.get("t"), str(rec.get("r") or "")
    if tgt is not None or rating:
        ttl = _DETAIL_TTL_OK
    elif rec.get("a"):
        # 원천이 답했는데 값이 없었다 — 그 판정은 **우리 파서**가 낸 것이므로
        # 파서가 바뀌면 다시 묻는다(지문 불일치 = 캐시 없음).
        if str(rec.get("s") or "") != client_sig():
            return None
        ttl = _DETAIL_TTL_EMPTY
    else:
        ttl = _DETAIL_TTL_MISS
    if age >= ttl:
        return None
    return (tgt, rating, str(rec.get("v") or ""))


def detail_cache_put(nid: str, tgt, rating: str, via: str, *,
                     answered: bool = False) -> None:
    """`answered` = **원천이 상세를 주긴 했다**(값이 없었을 뿐).

    값이 없는 기록만 이 플래그로 갈린다 — 답한 것은 길게, 못 닿은 것은 짧게
    믿는다. 값이 있는 기록엔 의미가 없다(늘 길게).
    """
    global _DETAIL_DIRTY
    _detail_cache_load()
    rec = {"t": tgt, "r": rating or "", "v": via or "", "at": time.time()}
    if answered and tgt is None and not rating:
        rec["a"] = 1
        rec["s"] = client_sig()       # 파서가 바뀌면 이 판정은 무효다(#119)
    with _DETAIL_LOCK:
        _DETAIL_MEM[str(nid)] = rec
        _DETAIL_DIRTY = True


_FLUSH_SEQ = 0


def _flush_seq() -> int:
    """flush 마다 늘어나는 번호 — tmp 파일 이름을 가른다.

    ⚠️ `threading.get_ident()` 로는 부족하다 — **끝난 스레드의 id 가 재사용**
    되어 실측에서 6개 중 2개가 같은 이름을 받았다(#25 이름이 아니라 실측).
    """
    global _FLUSH_SEQ
    with _DETAIL_LOCK:
        _FLUSH_SEQ += 1
        return _FLUSH_SEQ


def detail_cache_flush() -> None:
    """워커가 아니라 **호출부가 한 번** 쓴다 — 6개 스레드가 각자 쓰면 마지막이
    나머지를 덮는다. 원자적 저장(tmp + `os.replace`, #280)."""
    global _DETAIL_DIRTY
    with _DETAIL_LOCK:
        if not _DETAIL_DIRTY:
            return
        cut = time.time() - max(_DETAIL_TTL_OK, _DETAIL_TTL_EMPTY)
        snap = {k: v for k, v in _DETAIL_MEM.items()
                if isinstance(v, dict) and float(v.get("at") or 0) >= cut}
        _DETAIL_MEM.clear()
        _DETAIL_MEM.update(snap)
        _DETAIL_DIRTY = False
    try:
        import os
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dst = _detail_cache_path()
        # ⚠️ tmp 이름이 프로세스 상수면 두 스레드의 flush 가 **같은 파일**을
        # 쓰고 뒤엣것의 `os.replace` 가 ENOENT 로 죽어 그 flush 가 조용히
        # 유실된다(독립 리뷰 2026-09-12 L1 — ThreadingHTTPServer 에서 시장·
        # 종목 경로가 겹친다). flush 마다 늘어나는 번호로 갈라 준다.
        tmp = dst.with_suffix(f".{os.getpid()}.{_flush_seq()}.tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, dst)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("naver_research: 상세 캐시 저장 실패 — %s", exc)


def _fetch_report_detail_via(nid: str, *, use_cache: bool = True) -> tuple:
    """(목표가, 투자의견, **어느 경로로 읽었나**) — JSON 사다리 먼저, 그다음 옛 HTML.

    호출부가 셋(시장·산업·개별종목)이라 여기 한 곳에서 갈아야 세 화면이 안
    갈린다(#38). 폴백은 지우지 않는다 — 원천이 되돌릴 수도 있고, 이 레포에서
    폴백 사다리는 버그가 아니라 fix 였다(§작업 원칙·#122·#136·#191).

    ⚠️ 경로를 **모듈 전역 카운터**에 쌓지 않는다(자기검토 2026-09-12): 이 함수는
    풀 워커 6개가 동시에 부르므로 `d[k] = d.get(k,0)+1` 은 증가를 잃고, 전역이라
    시장·산업·종목 **세 화면의 건수가 한 통에 섞인다** — 그러면 화면이 제 행
    수가 아닌 수를 적는다(#45 총계와 소계가 다른 모집단 · #114 잔여 상태).
    경로는 **그 행에 실어** 화면이 자기 행에서 세게 한다.
    """
    if use_cache:
        _hit = detail_cached(nid)
        if _hit is not None:
            return _hit
    tgt, rating, rung, _whys = _detail_json(nid)
    # ⚠️ **둘 다** 채워졌을 때만 폴백을 건너뛴다(독립 리뷰 2026-09-12 High).
    # 옛 판은 `tgt is not None or rating` 이라, 새 단이 투자의견만 주고 목표가는
    # 우리가 모르는 키(`expectPrice` 등)로 주면 **옛 HTML 에 있는 목표가를 통째로
    # 버렸다** — 그러면 목표가 칸과 컨센서스가 아무 설명 없이 비고, 지금 동작하는
    # 경로보다 나빠진다. 폴백 조건은 '실패했나' 가 아니라 **'요구를 충족했나'**
    # 다(#136 — 테마 쪽엔 적용해 놓고 여기만 빠뜨렸다).
    if tgt is not None and rating:
        if use_cache:
            detail_cache_put(nid, tgt, rating, rung)
        return tgt, rating, rung
    html = _get(f"{_DETAIL_URL}?nid={nid}")
    if not html:
        # HTML 을 못 받았으면 JSON 이 준 **부분이라도** 살린다(빈칸보다 낫다).
        # ⚠️ `answered` 를 세우지 않는다 — 아무 값도 없는 이 결과는 '원천에
        # 없다' 가 아니라 **못 닿았다** 이므로 짧게만 믿는다(#303·#161).
        _out = (tgt, rating, rung) if (tgt is not None or rating) \
            else (None, "", "")
        if use_cache:
            detail_cache_put(nid, _out[0], _out[1], _out[2])
        return _out

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

    # ⚠️ 이름을 `rating` 으로 재사용하면 JSON 이 읽은 투자의견을 덮어쓴다 —
    # 합치려면 **별도 이름**이어야 한다.
    rating_html = ""
    for pat in (_RATING_DETAIL_RE, _RATING_CLASS_RE):
        m = pat.search(html)
        if m:
            raw = m.group(1).strip()
            for kw in _RATING_KEYWORDS:
                if kw.lower() == raw.lower():
                    rating_html = kw
                    break
            if not rating_html:
                rating_html = raw
            if rating_html:
                break

    # 두 원천을 **합친다** — JSON 이 구조화해 준 값이 있으면 그것을 우선하고,
    # 빠진 칸만 HTML 이 채운다. 어느 쪽이 기여했는지 라벨이 그대로 말한다(#43).
    out_t = tgt if tgt is not None else target
    out_r = rating or rating_html
    parts = [p for p in (rung, "옛 HTML" if (target is not None or rating_html)
                         else "") if p]
    _via = "+".join(parts) if (out_t is not None or out_r) else ""
    # 여기까지 왔으면 원천이 상세를 **줬다** — 값이 없다면 그 리포트에 없는
    # 것이므로 길게 믿는다(#82 갈래 · 위 상수 주석의 Blocking 실측).
    if use_cache:
        detail_cache_put(nid, out_t, out_r, _via, answered=True)
    return out_t, out_r, _via


def _fetch_report_detail(nid: str) -> tuple[Optional[float], str]:
    """(목표가, 투자의견) — 경로가 필요 없는 호출부용 얇은 래퍼.

    ⚠️ 래퍼를 만들 땐 "버리는 정보가 화면에 필요한가" 를 먼저 묻는다(#129) —
    시장 목록은 경로를 화면에 적으므로 `_fetch_report_detail_via` 를 직접 쓴다.
    """
    tgt, rating, _ = _fetch_report_detail_via(nid)
    return tgt, rating


def detail_rungs_note(rows: list) -> str:
    """**이 화면의 행**에서 경로별 건수를 센다 — 화면·`--check` 가 그대로 적는다.

    폴백을 로그로만 알리면 사용자는 영영 모른다(#42a·#136). 아무것도 안 읽었으면
    빈 문자열(늘 뜨는 배지 금지, #25·#260).
    """
    got: dict = {}
    for r in rows or []:
        via = str((r or {}).get("_via") or "")
        if via:
            got[via] = got.get(via, 0) + 1
    if not got:
        return ""
    return " · ".join(f"{k} {v}건" for k, v in sorted(got.items()))


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


def fetch_research_json(kind: str,
                       params: dict | None = None) -> tuple[list[dict], str, int]:
    """(행, 실패 사유, **원천이 준 행 수**) — 한 목록을 JSON 으로 받는다.

    빈 리스트는 실패가 아니다(#54). 세 번째 값이 필요한 이유는 `window_note`
    독스트링에 있다 — 파싱 뒤 수로 상한을 재면 못 읽은 행 하나가 경고를 끈다.

    `params` 는 `requests.get` 으로 그대로 간다(`naver_diag.get_json` 이
    `**kwargs` 를 넘긴다). **어떤 페이지 파라미터를 받는지는 안 쟀으므로**
    (#12·#165) 호출부가 후보를 순서대로 시도하고 결과로 판정한다.
    """
    if kind not in _RESEARCH_KINDS:
        return [], f"모르는 목록: {kind}", 0
    _kw = {"params": dict(params)} if params else {}
    raw, why = _get2_json(f"{_RESEARCH_API}/{kind}", **_kw)
    if raw is None:
        return [], why, 0
    if not isinstance(raw, list):      # 목록이 아니면 계약 변경 — 0건과 다르다
        return [], _nd.shape_reason(f"{_RESEARCH_KINDS[kind]} 리서치 목록", raw), 0
    rows = research_rows_from_json(raw, kind)
    if raw and not rows:               # 행은 왔는데 한 건도 못 읽음 = 구조 변경
        return [], _nd.parse_reason(f"{_RESEARCH_KINDS[kind]} 리서치 행", len(raw),
                                 unit="행"), len(raw)
    return rows, "", len(raw)



_CLIENT_SIG = ""


def client_sig() -> str:
    """이 모듈 소스의 지문 — **캐시 키에 싣는다**(손 bump 금지).

    ⚠️ 옛 키는 `naver_market_v2_…`·`naver_industry_v1_…` 처럼 버전이 **리터럴**
    이었다. 페이지네이션을 넣어도 그 숫자를 안 올리면 TTL 이 끝날 때까지 옛
    20행 캐시가 그대로 서빙돼 **고친 날 화면이 안 바뀐다** — 이 레포에서
    #18·#21b·#95·#124·#198·#216·#233 으로 일곱 번 진 실패다. 규율로 기억할
    일을 구조로 옮긴다(#119·#266: 배포가 곧 무효화다).

    독스트링·주석은 걷어내고 잰다 — 주석 한 줄에 전 캐시가 날아가면 안 된다.
    """
    global _CLIENT_SIG
    if not _CLIENT_SIG:
        try:
            import ast as _ast
            import hashlib as _hl
            src = Path(__file__).read_text(encoding="utf-8")
            tree = _ast.parse(src)
            for node in _ast.walk(tree):       # 독스트링 제거
                if isinstance(node, (_ast.Module, _ast.FunctionDef,
                                     _ast.AsyncFunctionDef, _ast.ClassDef)):
                    b = getattr(node, "body", None)
                    if (b and isinstance(b[0], _ast.Expr)
                            and isinstance(b[0].value, _ast.Constant)
                            and isinstance(b[0].value.value, str)):
                        b[0].value.value = ""
            _CLIENT_SIG = _hl.sha1(
                _ast.dump(tree).encode("utf-8")).hexdigest()[:10]
        except Exception:                                      # noqa: BLE001
            _CLIENT_SIG = "nosig"      # 못 재면 지문을 주장하지 않는다(#54)
    return _CLIENT_SIG


def cache_envelope(rows: list, note: str, meta: dict,
                   detail_note: str = "") -> dict:
    """디스크에 저장할 봉투 — 행과 **함께 사실도** 저장한다.

    ⚠️ 옛 판은 행만 저장하고 캐시 히트 때 행 수에서 절단 경고를 **파생**했다.
    페이지네이션이 들어가면 행 수로는 '몇 쪽에서 왜 멈췄나'를 복원할 수 없고,
    그러면 캐시가 사는 1시간 동안 화면이 조용해진다(#342 그대로 — 값과 같이
    가는 메타는 캐시 경로에서도 복원해야 한다).
    """
    return {"rows": list(rows or []), "note": note or "",
            "meta": dict(meta or {}),
            # 상세 수율 사유도 **저장**한다 — 행에서 되짚으면 '한 번도 안 걸었다'
            # 와 '걸었는데 다 실패' 를 못 가르고, 예산에서 뺀 건수도 잃는다
            # (독립 리뷰 2026-09-12 · #82 갈래는 이름으로).
            "detail_note": detail_note or ""}


def cache_rows(obj) -> tuple[list, str]:
    """저장분 → (행, 사실 문구). 옛 리스트 형식도 읽는다(형식 전환 내구).

    옛 형식은 note 를 **모르므로** 지어내지 않는다 — "" 를 준다(#165).
    """
    if isinstance(obj, dict):
        return list(obj.get("rows") or []), str(obj.get("note") or "")
    return list(obj or []), ""


def cache_detail_note(obj) -> str | None:
    """저장분의 상세 수율 사유. 옛 형식(리스트)·미기록이면 **None**(모름).

    None 과 "" 는 다르다 — 전자는 '못 잰다', 후자는 '할 말 없음' 이다(#54).
    """
    if isinstance(obj, dict) and "detail_note" in obj:
        return str(obj.get("detail_note") or "")
    return None


def fetch_research_pages(kind: str, *, cutoff: str, limit: int,
                         max_pages: int = _MAX_PAGES) -> tuple[list, str, dict]:
    """여러 쪽을 이어받아 `cutoff` 창을 채운다 → (행, 실패 사유, meta).

    2026-09-11 SPA 전환 커밋이 옛 HTML 의 `for page in range(1, max_pages+1)`
    루프를 통째로 지웠고, 그래서 KR 3탭이 **하루치 20건**으로 줄었다(사용자
    2026-09-12 "그전에는 … 한국은 일주일치 긁어왔어"). 복원할 것은 옛 URL 이
    아니라 옛 **중단 조건**이다(`page_stop` 참조).

    ⚠️ `m.stock.naver.com/api/research/*` 가 어떤 페이지 파라미터를 받는지는
    **한 번도 안 쟀다**. 형제 실측이 서로 갈린다 — `upjong/list` 는 `pageSize`
    만 먹었고 `page` 는 무시됐으며, `front-api/domestic/stock/list` 는 둘 다
    먹되 `pageSize>50` 이면 빈 배열이다. 그래서 하나를 **추측해 박지 않고**
    ① `pageSize` ② `page` 순으로 시도하고 **결과로 판정**한다(#25 능력은 이름이
    아니라 실측 · #151 죽은 이름 · #136 폴백 조건은 '실패'가 아니라 '요구를
    충족했나'). 어느 쪽이 먹었는지는 `meta["mode"]` 가 말하고 화면·프로브가
    같은 값을 읽는다(#35).

    세 탭이 이 함수 하나를 쓴다 — 복제하면 한 탭만 하루치로 남는다(#38·#147).
    """
    rows: list = []
    seen: set = set()
    unreadable = 0
    raw_total = 0
    why = ""

    def _take(j_rows: list) -> tuple[int, int]:
        """(새로 추가된 행 수, 그중 창 안 행 수) — in-place 로 rows 를 채운다."""
        fresh = in_win = 0
        for r in j_rows:
            nid = str(r.get("nid") or "")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            fresh += 1
            if r.get("date") and r["date"] < cutoff:
                continue
            in_win += 1
            rows.append(r)
        return fresh, in_win

    # ── 1쪽: `pageSize` 를 **먼저** 물어 한 번에 크게 받아 본다 ────────
    # 먹으면 요청 수가 줄고, 안 먹으면 기본 크기(20행)가 그대로 온다 — 어느
    # 쪽이든 **이 응답을 쓴다**(무인자 기준선을 따로 받으면 요청 하나가 순손실).
    j1, why, raw1 = fetch_research_json(kind, {"pageSize": _PAGE_SIZE_TRY})
    if raw1 == 0 and not why:
        # ⚠️ 형제 실측: `front-api` 는 pageSize 가 상한을 넘으면 **빈 배열**을
        # 준다(naver_ranking_client 137-140). 빈 배열을 '원천에 0건' 으로 읽으면
        # 화면이 통째로 빈다 — 무인자로 한 번 더 묻는다(#136 폴백 조건은
        # '실패' 가 아니라 '요구를 충족했나').
        j1, why, raw1 = fetch_research_json(kind)
        page_size = _PAGE_CAP
        mode = "single"
    elif raw1 > _PAGE_CAP:
        page_size, mode = raw1 if raw1 < _PAGE_SIZE_TRY else _PAGE_SIZE_TRY, "pageSize"
    else:
        page_size, mode = _PAGE_CAP, "single"

    pages, raw_total = 1, raw1
    unreadable += max(0, raw1 - len(j1))
    f1, w1 = _take(j1)
    base_first = _first_id(j1)
    stop = page_stop(page=1, got=raw1, page_size=page_size, fresh=f1,
                     in_window=w1, total_kept=len(rows), limit=limit,
                     max_pages=max_pages)

    # ── 2쪽 이후: `page` 로 이어받기 ──────────────────────────────────
    if not stop:
        for page in range(2, max_pages + 1):
            _p = {"page": page}
            if mode == "pageSize":
                _p["pageSize"] = page_size
            jp, _why_p, rawp = fetch_research_json(kind, _p)
            if _why_p and not jp:
                # ⚠️ 옛 판은 사유를 버리고 `rawp=0` 을 `source_end` 로 읽어,
                # 중간 쪽의 429·타임아웃이 **창을 조용히 자르고** 아무 말도
                # 안 했다(독립 리뷰 2026-09-12). '원천에 그게 전부' 와 '못
                # 받았다' 는 처방이 정반대다(#82·#136).
                why = why or _why_p
                stop = "fetch_failed"
                log.warning("naver_research: %s %d쪽 수신 실패 — %s",
                            kind, page, _why_p)
                break
            if _first_id(jp) and _first_id(jp) == base_first:
                # 파라미터가 무시돼 **같은 쪽**이 돌아왔다(`upjong/list` 실측
                # 형태). 행 **수**만 보면 '2쪽도 20행' 과 구별이 안 된다(#25).
                stop = "page_ignored"
                break
            pages = page
            raw_total += rawp
            unreadable += max(0, rawp - len(jp))
            fr, iw = _take(jp)
            if mode == "single" and fr:
                mode = "page"
            stop = page_stop(page=page, got=rawp, page_size=page_size,
                             fresh=fr, in_window=iw, total_kept=len(rows),
                             limit=limit, max_pages=max_pages)
            if stop:
                break

    return rows, why, {"pages": pages, "raw_total": raw_total, "mode": mode,
                       "stop": stop or "max_pages", "unreadable": unreadable}


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


# 상세(목표가·투자의견) 수집 예산 — 리터럴로 못박는다(#66). 쪽 이어받기로
# 행이 20 → 최대 수백이 되는데 상세는 행당 HTTP 1건이고 그 경로(`company_read
# .naver`)는 **아직 옛 HTML** 이라 살아 있는지도 안 쟀다(#79). 본문이 아닌 값에
# 예산을 같이 다는 규약(#116) — 최신 순으로 이만큼만 걸고 나머지는 비운다.
_DETAIL_BUDGET = 40


def _detail_tag(scope: dict) -> str:
    """캐시 키의 상세-수집 축(순수-ish). `fetch_detail` 이 없는 목록은 "".

    ⚠️ 감사(`board_audit`)는 화면과 **같은 인자**로 부르되 `fetch_detail=False`
    다. 그런데 그 축이 키에 없어 **상세 없는 행을 공용 캐시에 구웠고**, 그러면
    라이브 탭의 목표가·투자의견이 통째로 비면서 "상세 페이지도 SPA 전환됐을 수
    있습니다" 라는 **거짓 경고**까지 뜬다(독립 리뷰 2026-09-12). 키를 나누는
    축은 **결과를 바꾸는 인자 전부**여야 한다(#61 의 반대 방향 — 덜 나눠서 생긴
    오염, #141).
    """
    return "" if scope.get("fetch_detail", True) else "_nodetail"


def detail_yield_note(rows: list, *, budget_left: int = 0,
                      fetched: int = 0, from_cache: int = 0) -> str:
    """목표가·투자의견 칸이 빈 이유(순수). "" = 할 말 없음.

    두 갈래를 **다른 이름으로** 말한다(#82) — 예산에서 빠진 것(정상)과 상세
    경로가 한 건도 못 읽은 것(결함)은 처방이 정반대다.
    """
    rows = list(rows or [])
    if not rows:
        return ""
    graded = [r for r in rows if r.get("target") or r.get("rating")]
    _via_note = detail_rungs_note(rows)
    if not graded:
        # ⚠️ 2026-09-12 까지 이 문구는 '`--check` 로 잴 것' 에서 끝났다 —
        # 사용자에게 숙제를 넘긴 것이다(#252 반복 확인은 제품에 심는다).
        # 이제 상세 경로는 **JSON 사다리**이고, 어느 단이 답했는지 기록이
        # 남는다. 전멸이면 그 사실을 그대로 적는다(#82·#43).
        tried = " · ".join(k for k, _ in _DETAIL_API_RUNGS)
        return ("상세에서 목표가·투자의견을 한 건도 못 읽었습니다 — 후보 경로"
                f"({tried})와 옛 HTML 이 모두 값을 주지 않았습니다. "
                "`naver_spa_probe` ⑧ 로 원천 응답을 잽니다")
    if budget_left > 0:
        # ⚠️ 옛 문구는 "최신 N건에만 붙였습니다" 라 **N 이 무엇 중의 N 인지**
        # 말하지 않았다 — 295건짜리 표에서 35 를 보면 나머지가 왜 비었는지,
        # 그게 늘 그런 건지 이번만인지 알 수 없다(#45 모집단 · #82 갈래).
        # 분모와 **이번 주기에 새로 건 수**를 같이 적으면 다음 주기 출력이
        # 곧 측정이 된다(커버리지가 오르는 게 보인다).
        # ⚠️ `저장분` 은 **캐시 적중 수**(값 없는 기록 포함)라 '채워진 건수' 가
        # 아니다 — 앞의 `35/295` 바로 뒤에 붙으면 35 를 분해하는 것처럼 읽힌다
        # (독립 리뷰 L2). 무엇을 센 수인지 이름으로 말한다(#34·#45).
        return (f"목표가·투자의견 {len(graded)}/{len(rows)}건 — 이번 주기에 "
                f"{fetched}건 조회(저장분 재사용 {from_cache}건 · 예산 "
                f"{_DETAIL_BUDGET}건), 남은 {budget_left}건은 다음 주기에 채웁니다"
                + (f" · 경로 {_via_note}" if _via_note else ""))
    if len(graded) < len(rows):
        # 예산은 남았는데 못 채운 행이 있다 = 그 행들은 **상세가 값을 안 준 것**
        # 이다(예산에서 빠진 것과 처방이 다르다, #82). 그 사실을 말한다(#43).
        return (f"목표가·투자의견 {len(graded)}/{len(rows)}건 — 나머지는 상세에 "
                f"목표가·투자의견이 없는 리포트입니다"
                + (f" · 경로 {_via_note}" if _via_note else ""))
    # ⚠️ **경로가 갈렸을 때만** 말한다(독립 리뷰 2026-09-12 Medium).
    # 옛 판은 '1단이 아니면 경고' 였는데, JSON 단은 `endUrl` 에서 **추론**한
    # 것이라 아직 한 번도 측정된 적이 없다 — 즉 오늘은 전 행이 `옛 HTML` 이고
    # 그 배지가 **매 수집마다** 뜬다. 지금 유일하게 동작하는 경로를 상시
    # 경고로 다는 것은 아무것도 안 재는 것과 같다(#25·#260).
    # 갈린 경우(일부만 JSON, 일부는 HTML)는 진짜 이상이므로 그때 말한다.
    _used = {str(r.get("_via") or "") for r in rows if r.get("_via")}
    if len(_used) > 1:
        return f"목표가·투자의견 경로가 행마다 다릅니다 — {_via_note}"
    return ""


def restored_detail_note(note: str, age_h: float) -> str:
    """캐시에서 복원한 수율 문구에 **언제 잰 것인지**를 붙인다.

    저장된 문구는 `이번 주기에 40건 조회` 라고 적혀 있는데, 캐시 히트 주기는
    **한 건도 안 걸었다** — 그대로 내보내면 화면이 안 한 일을 했다고 말한다
    (독립 리뷰 2026-09-12 L3 · #43·#136 폴백은 payload 가 밝힌 대로 적을 것).
    """
    note = str(note or "")
    if not note:
        return ""
    try:
        mins = max(0, int(float(age_h) * 60))
    except (TypeError, ValueError):
        return note
    return f"{note} · 직전 수집 기록({mins}분 전)"


def fetch_recent_research_market(limit: int = 25, days_back: int = 14,
                                 fetch_detail: bool = True) -> list[dict]:
    """전체 시장 최근 종목 리서치 리포트 (Naver Finance 리서치 목록).

    한경 컨센서스가 JS 렌더링으로 정적 scrape 불가 → market.html 의
    '최근 리서치 액션' KR 탭 대체 소스. Returns
    [{code, name, broker, rating, title, date}] (날짜 내림차순) —
    dashboard 의 research_kr 스키마와 호환. 키 불필요, 디스크 캐시 `_MARKET_TTL_HOURS`(=1h).

    쪽 이어받기는 `fetch_research_pages` 가 한다(어느 파라미터가 먹는지는
    **런타임에 판정**한다 — 그 독스트링 참조). 창을 다 못 채우고 멈추면 그
    사실을 `last_window_note("market")` 에 남겨 화면이 값과 **같이** 말한다.
    fetch_detail=True 면 각 리포트 상세에서 투자의견·목표가를 채우되
    `_DETAIL_BUDGET` 건까지만 건다(#116 장식용 값에 예산)."""
    cache_key = (f"naver_market_{client_sig()}_{date.today().isoformat()}"
                 f"_{days_back}_{limit}{_detail_tag(locals())}.json")
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _MARKET_TTL_HOURS:
                _env = json.loads(cache_file.read_text())
                _rows, _note = cache_rows(_env)
                # 캐시 히트도 **창 절단 사실은 말해야** 한다 — 재시작 뒤 첫 렌더가
                # 절단된 캐시를 조용히 그리면 #43 이 그대로 재발한다. 행 수에서
                # 파생하면 '몇 쪽에서 왜 멈췄나'를 복원할 수 없으므로 저장할 때
                # 같이 굽고 여기서 **읽는다**(#342 의 캐시 층 교훈).
                _WINDOW_NOTE["market"] = _note
                # ⚠️ 상세 수율 사유도 **캐시 히트에서 복원**해야 한다 — 옛 판은
                # 수집 경로에서만 세워서, 캐시가 사는 1시간 동안 목표가·투자의견
                # 열이 전부 '—' 인데 화면이 한 마디도 안 했다(#342 의 다음 층).
                # 저장된 행에서 그대로 되짚는다(#270 렌더타임 파생 — 재수집 0).
                # ⚠️ 행에서 되짚으면 '상세를 한 번도 안 걸었다'(fetch_detail
                # =False)와 '걸었는데 다 실패' 가 같은 말이 되고, 예산에서 뺀
                # 건수도 잃는다 — 저장된 사유를 **읽는다**(독립 리뷰).
                _dn = cache_detail_note(_env)
                _LAST_MARKET_FAIL["detail"] = (
                    restored_detail_note(_dn, age_h) if _dn is not None
                    else detail_yield_note(_rows))
                return _rows
        except Exception as exc:
            log.warning("naver_research: market cache read failed: %s", exc)

    today = date.today()
    cutoff = today - timedelta(days=days_back)
    # 2026-09-12: 쪽 이어받기 복원(사용자 "그전에는 한국은 일주일치 긁어왔어").
    # 세 탭이 `fetch_research_pages` **한 함수**를 쓴다 — 복제하면 한 탭만
    # 하루치로 남는다(#38·#147). 중단 조건·파라미터 판정은 그 안에 있다.
    cut = cutoff.isoformat() if hasattr(cutoff, "isoformat") else str(cutoff)
    rows, why, _meta = fetch_research_pages("company", cutoff=cut, limit=limit)
    # ⚠️ 창 절단은 **실패가 아니다** — 실패 칸(`why`)에 넣으면 행이 하나라도
    # 오는 순간 아래에서 `""` 로 덮여 화면이 영영 모른다(독립 리뷰 H1).
    _WINDOW_NOTE["market"] = window_note(_meta, days_back)

    # 독스트링이 '날짜 내림차순' 을 약속하는데 **정렬을 한 번도 안 했다** —
    # 원천 순서에 기대고 있었고, 쪽을 합치면 그 가정이 처음으로 부담을 진다.
    # 자르기(`[:limit]`)는 정렬 **뒤**에 와야 최신이 남는다.
    rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
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
        detail_map: dict[str, tuple] = {}
        # ⚠️ **캐시에 있는 것은 전 행 채운다**(네트워크 0) — 예산은 **미스에만**
        # 쓴다. 옛 판은 `rows[:40]` 이라 41번째부터는 몇 주기를 돌아도 영원히
        # 빈칸이었다(창이 일주일=295건이 되며 260건 상시 빈칸). 이렇게 두면
        # 몇 주기 만에 커버리지가 100% 로 가고, 정상 상태 비용은 신규 리포트
        # 몇 건뿐이다(사용자 2026-09-12 "최대한 모두 나오게").
        for _r in rows:
            _c = detail_cached(_r["nid"])
            if _c is not None:
                detail_map[_r["nid"]] = _c
        _cached_n = len(detail_map)
        _miss = [r for r in rows if r["nid"] not in detail_map]
        # 최신 순으로 예산만큼만 — rows 는 바로 위에서 날짜 내림차순 정렬됐다.
        _targets = _miss[:_DETAIL_BUDGET]
        _skipped = max(0, len(_miss) - len(_targets))
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_fetch_report_detail_via, r["nid"]): r["nid"]
                       for r in _targets}
            for fut in as_completed(futures):
                nid = futures[fut]
                try:
                    detail_map[nid] = fut.result()
                except Exception:
                    detail_map[nid] = (None, "", "")
        for r in rows:
            tgt, rt, via = detail_map.get(r["nid"], (None, "", ""))
            r["rating"] = rt
            r["target"] = tgt
            # 경로는 **그 행에** 남긴다(전역 카운터 금지 — 위 주석 참조).
            # `out` 은 아래에서 키를 골라 만들므로 캐시·화면으로 새지 않는다.
            r["_via"] = via
        # ⚠️ **수율을 잰다** — 상세 페이지(`company_read.naver`)는 아직 옛 HTML
        # 이다. 목록이 SPA 로 죽었으니 여기도 죽었을 수 있는데, 죽으면 매 수집이
        # 수십 건을 순손실로 던지고 목표가·투자의견 칸만 조용히 빈다(#79 그
        # 경로가 실제로 실행됐나 · #116 장식용 값의 비용). 0 이면 사유로 남겨
        # **화면이** 말하게 한다 — 아직 재지 않았으므로 끄지는 않는다(#12).
        # ⚠️ 옛 주석은 "`--check` 가 말하게 한다" 였는데 `check()` 는 자기
        # 단건 프로브만 돌고 이 칸을 **읽지 않는다** — 읽는 곳이 로그뿐인
        # write-only 였다(독립 리뷰 M6 · #123·#129·#189·#228 계열).
        # 워커가 아니라 **여기서 한 번** 쓴다(마지막 쓰기가 나머지를 덮는 것 방지).
        detail_cache_flush()
        _hit = sum(1 for _t, _rt2, *_ in detail_map.values() if _t or _rt2)
        _LAST_MARKET_FAIL["detail"] = detail_yield_note(
            rows, budget_left=_skipped, fetched=len(_targets),
            from_cache=_cached_n)
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
        cache_file.write_text(json.dumps(
            cache_envelope(out, _WINDOW_NOTE.get("market") or "", _meta,
                           _LAST_MARKET_FAIL.get("detail") or ""),
            ensure_ascii=False))
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
    link}] 날짜 내림차순. 키 불필요, 디스크 캐시 `_MARKET_TTL_HOURS`(=1h)."""
    cache_key = (f"naver_industry_{client_sig()}_{date.today().isoformat()}"
                 f"_{days_back}_{limit}{_detail_tag(locals())}.json")
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _MARKET_TTL_HOURS:
                _rows, _note = cache_rows(json.loads(cache_file.read_text()))
                # 캐시 히트도 **창 절단 사실은 말해야** 한다 — 재시작 뒤 첫 렌더가
                # 절단된 캐시를 조용히 그리면 #43 이 그대로 재발한다. 행 수에서
                # 파생하면 '몇 쪽에서 왜 멈췄나'를 복원할 수 없으므로 저장할 때
                # 같이 굽고 여기서 **읽는다**(#342 의 캐시 층 교훈).
                _WINDOW_NOTE["industry"] = _note
                return _rows
        except Exception as exc:
            log.warning("naver_research: industry cache read failed: %s", exc)

    cutoff = date.today() - timedelta(days=days_back)
    # 2026-09-12: 쪽 이어받기 복원(사용자 "그전에는 한국은 일주일치 긁어왔어").
    # 세 탭이 `fetch_research_pages` **한 함수**를 쓴다 — 복제하면 한 탭만
    # 하루치로 남는다(#38·#147). 중단 조건·파라미터 판정은 그 안에 있다.
    cut = cutoff.isoformat() if hasattr(cutoff, "isoformat") else str(cutoff)
    rows, why, _meta = fetch_research_pages("industry", cutoff=cut, limit=limit)
    # 사유를 **계산만 하고 버리면 없는 것과 같다**(#123·#129·#189·#228 계열).
    _LAST_MARKET_FAIL["industry"] = why or ""
    # 창 절단은 실패가 아니라 **값과 같이** 말할 사실이다(독립 리뷰 H1).
    _WINDOW_NOTE["industry"] = window_note(_meta, days_back)

    # 독스트링이 '날짜 내림차순' 을 약속하는데 **정렬을 한 번도 안 했다** —
    # 원천 순서에 기대고 있었고, 쪽을 합치면 그 가정이 처음으로 부담을 진다.
    # 자르기(`[:limit]`)는 정렬 **뒤**에 와야 최신이 남는다.
    rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
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
            cache_file.write_text(json.dumps(
                cache_envelope(out, _WINDOW_NOTE.get("industry") or "", _meta),
                ensure_ascii=False))
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
    불필요, 디스크 캐시 `_MARKET_TTL_HOURS`(=1h)."""
    cache_key = (f"naver_strategy_{client_sig()}_{date.today().isoformat()}"
                 f"_{days_back}_{limit}{_detail_tag(locals())}.json")
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _MARKET_TTL_HOURS:
                _rows, _note = cache_rows(json.loads(cache_file.read_text()))
                # 캐시 히트도 **창 절단 사실은 말해야** 한다 — 재시작 뒤 첫 렌더가
                # 절단된 캐시를 조용히 그리면 #43 이 그대로 재발한다. 행 수에서
                # 파생하면 '몇 쪽에서 왜 멈췄나'를 복원할 수 없으므로 저장할 때
                # 같이 굽고 여기서 **읽는다**(#342 의 캐시 층 교훈).
                _WINDOW_NOTE["strategy"] = _note
                return _rows
        except Exception as exc:
            log.warning("naver_research: strategy cache read failed: %s", exc)

    cutoff = date.today() - timedelta(days=days_back)
    # 2026-09-12: 쪽 이어받기 복원(사용자 "그전에는 한국은 일주일치 긁어왔어").
    # 세 탭이 `fetch_research_pages` **한 함수**를 쓴다 — 복제하면 한 탭만
    # 하루치로 남는다(#38·#147). 중단 조건·파라미터 판정은 그 안에 있다.
    cut = cutoff.isoformat() if hasattr(cutoff, "isoformat") else str(cutoff)
    rows, why, _meta = fetch_research_pages("invest", cutoff=cut, limit=limit)
    # 사유를 **계산만 하고 버리면 없는 것과 같다**(#123·#129·#189·#228 계열).
    _LAST_MARKET_FAIL["strategy"] = why or ""
    # 창 절단은 실패가 아니라 **값과 같이** 말할 사실이다(독립 리뷰 H1).
    _WINDOW_NOTE["strategy"] = window_note(_meta, days_back)

    # 독스트링이 '날짜 내림차순' 을 약속하는데 **정렬을 한 번도 안 했다** —
    # 원천 순서에 기대고 있었고, 쪽을 합치면 그 가정이 처음으로 부담을 진다.
    # 자르기(`[:limit]`)는 정렬 **뒤**에 와야 최신이 남는다.
    rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    rows = rows[:limit]
    out = [{
        "broker": r["broker"], "title": r["title"], "date": r["date"],
        # 원천이 준 링크가 있으면 그것 — 옛 조립 주소는 SPA 전환으로 죽었다.
        "link": r.get("url") or f"{_STRATEGY_DETAIL_URL}?nid={r['nid']}",
    } for r in rows]

    if out:  # truthy-only — 빈 결과(일시 실패) 캐시 안 함
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(
                cache_envelope(out, _WINDOW_NOTE.get("strategy") or "", _meta),
                ensure_ascii=False))
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
    # 형제 표면도 **자기 수집 끝에** 굽는다 — 안 하면 여기서 읽은 상세가
    # 메모리에만 남아 프로세스와 함께 사라진다(#38 한 곳만 배선하면 갈린다).
    detail_cache_flush()

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
_CHECK_VER = 5        # 4 = 상세 수율 · 5 = 상세 JSON 사다리        # 진단은 버전을 찍는다(#21)


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
        # 경로는 반환값이 직접 말한다 — 전역을 비웠다 채우는 방식은 풀에서
        # 증가를 잃고 화면끼리 섞인다(#45·#114).
        # ⚠️ 진단은 **단마다 왜 실패했는지**까지 말한다 — 404 와 '200 인데 아는
        # 키가 없다' 는 처방이 정반대다(#82 · 독립 리뷰 2026-09-12 Low: 사유를
        # 계산해 놓고 버리면 운영자가 프로브를 한 번 더 돌려야 한다).
        _jt, _jr, _jrung, _whys = _detail_json(nid)
        for _w in _whys:
            print(f"   ↪ {_w}")
        # ⚠️ **캐시를 우회**한다 — 이 줄이 묻는 것은 "원천이 지금 답하나" 다.
        # 30일 캐시를 읽으면 원천이 오늘 죽어 있어도 옛 값으로 ✅ 가 찍힌다
        # (#35·#321·#345c 감사의 계약이 무엇인지 먼저 답할 것). 우회하면
        # 진단이 운영 캐시를 건드리지도 않는다(#264·#283).
        tgt, rating, _rung = _fetch_report_detail_via(nid, use_cache=False)
        _via = _rung or "—"
        if tgt or rating:
            print(f"④ 상세 수율: ✅ nid={nid} → 목표가 {tgt} · 의견 "
                  f"{rating or '—'} · 경로 {_via}")
        else:
            print(f"④ 상세 수율: ⚠️ nid={nid} 에서 목표가·투자의견을 못 읽었다 "
                  "— 그 리포트에 없을 수도, 상세 경로가 전부 죽었을 수도 "
                  "있다(한 건 관측이라 단정하지 않는다)")
            # **무엇을 시도했는지** 적는다 — 안 적으면 운영자가 같은 후보를
            # 다시 짚는다(#82·#279 진단의 모든 문장이 잰 것인지 물을 것).
            for _lbl, _tmpl in _DETAIL_API_RUNGS:
                print(f"   ↪ 시도: {_lbl} — {_tmpl.format(nid=nid)}")
            print(f"   ↪ 시도: 옛 HTML — {_DETAIL_URL}?nid={nid}")
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
