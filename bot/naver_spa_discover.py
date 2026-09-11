"""네이버 SPA 가 **어느 JSON 주소를 부르는지 재는** 도구(#151·#338·#340).

finance.naver.com 이 Next.js 로 갈아엎히면서 서버 렌더 표가 사라질 때마다
우리는 "엔드포인트 이름을 추측 → 배포 → 여전히 빈칸 → 다음 라운드" 를
반복했다(업종 2026-09-11 · 리서치 목록 2026-09-11 · 테마·리서치 상세
2026-09-12). 추측을 줄이는 방법은 하나뿐이다 — **원천이 스스로 답하게**
하는 것이다(#28·#86).

Next.js 는 라우트마다 JS 청크를 내려보내고 그 청크 안에 API 경로가 **문자열
리터럴**로 들어 있다. 그래서 여기서는 (a) 페이지 HTML 에서 청크 URL 을 뽑고
(b) 청크를 받아 `/api/...` 리터럴을 **세어서** 돌려준다. 이름을 짓지 않는다.

세 함수 모두 **순수**하거나 `fetch` 를 주입받는다 — 샌드박스(네트워크 차단)
에서도 픽스처로 태울 수 있어야 회귀가 계약을 잡는다(#19 소스 문자열 금지).

⚠️ 비용은 **유계**여야 한다. 청크가 수십 개라 전부 받으면 수 MB 다 — 라우트
이름이 붙은 청크를 먼저 보고(`prefer`), 개수·바이트 상한에서 멈춘다(#116
본 응답 경로의 무거운 값엔 예산과 캐시를 같이).
⚠️ 읽기 전용 — 운영 캐시를 건드리지 않는다(#264·#283·#321). 저장은 호출부가.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlsplit

log = logging.getLogger("bot.naver_spa_discover")

# 청크 URL — `<script src=…>` / `<link href=…>` 속성에 절대·상대로 온다.
_CHUNK_ATTR = re.compile(
    r'(?:src|href)=["\']([^"\']*_next/static/chunks/[^"\']+\.js)["\']', re.I)
# App Router 의 flight 페이로드는 **접두 없는** 상대 이름만 준다
# (`"static/chunks/8201-….js"`) — 속성에서 구한 접두를 붙여야 주소가 된다.
# ⚠️ 그 페이로드는 JS 문자열 **안의** JSON 이라 따옴표가 `\"` 로 이스케이프돼
# 온다 — 맨 따옴표만 보는 정규식은 여는 쪽은 맞히고 **닫는 쪽에서 진다**
# (내가 지어낸 픽스처로는 통과했고, 실제 바이트 모양으로 바꾸자 0건이 됐다.
# 픽스처는 원천이 실제로 보내는 모양대로, #155).
_ESCQ = r'(?:\\)?["\']'
_CHUNK_REL = re.compile(_ESCQ + r'(static/chunks/[^"\'\\]+\.js)' + _ESCQ)

# JS 안의 API 경로 리터럴. 호스트가 붙은 절대 주소와 `/api/...` 상대 경로 둘 다.
# `${...}` 같은 템플릿 구멍도 그대로 잡아 **보이는 대로** 보고한다(#165 우리가
# 채워 넣지 않는다 — 구멍이 있으면 사람이 보고 판단한다).
_API_LITERAL = re.compile(
    r'["\'`]((?:https?://[a-z0-9.\-]*naver\.com)?/?(?:front-)?api/'
    r'[A-Za-z0-9/_\-{}$.]{3,90})["\'`]')

_CHUNK_CAP = 12                # 받아 볼 청크 수 상한
_BYTE_CAP = 3_000_000          # 누적 바이트 상한
_FETCH_TIMEOUT = 8


def chunk_urls(html: str, page_url: str, *, cap: int = 60,
               prefer: str = "") -> list:
    """페이지 HTML → 청크 URL 목록(순수). `prefer` 를 이름에 품은 것이 앞으로.

    라우트 청크(`app/sise/theme/page-….js`)에 그 화면이 부르는 주소가 있다 —
    공용 청크부터 받으면 상한에 걸려 정작 필요한 것을 못 본다(#45 모집단).
    """
    out: list = []
    seen: set = set()
    prefix = ""
    for m in _CHUNK_ATTR.finditer(html or ""):
        u = urljoin(page_url, m.group(1))
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if not prefix:
            i = u.find("_next/static/chunks/")
            if i > 0:
                prefix = u[:i]
    if not prefix:
        # ⚠️ 속성(`<script src=…>`)이 하나도 안 잡히면 이 분기가 **영영 안
        # 돈다** — 이스케이프된 이름만 실려 오는 경우를 위해 만든 폴백인데,
        # 정확히 그때 못 쓰게 되어 있었다(독립 리뷰 2026-09-12 Low).
        # 그런 페이지에선 표준 위치(`{origin}/_next/...`)를 쓴다.
        pr = urlsplit(page_url)
        if pr.scheme and pr.netloc:
            prefix = f"{pr.scheme}://{pr.netloc}/"
    if prefix:
        for m in _CHUNK_REL.finditer(html or ""):
            u = prefix + "_next/" + m.group(1)
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
    if prefer:
        key = prefer.lower()
        out.sort(key=lambda u: 0 if key in u.lower() else 1)
    return out[:cap]


def api_paths(js: str, *, must_contain: str = "") -> list:
    """JS 본문 → 그 안에 **리터럴로 적힌** API 경로들(순수, 중복 제거).

    `must_contain` 을 주면 그 조각을 품은 것만 — 없으면 전부. 정렬해 돌려주므로
    같은 청크를 두 번 재도 순서가 같다(회귀가 값으로 고정할 수 있게).
    """
    got = {m.group(1) for m in _API_LITERAL.finditer(js or "")}
    key = (must_contain or "").lower()
    return sorted(p for p in got if key in p.lower())


def absolute(path: str, *, host: str) -> str:
    """발견한 경로 → 호출 가능한 절대 주소(순수). 이미 절대면 그대로."""
    p = str(path or "").strip()
    if p.startswith("http://") or p.startswith("https://"):
        return p
    return host.rstrip("/") + "/" + p.lstrip("/")


def discover(page_url: str, *, must_contain: str, fetch,
             chunk_cap: int = _CHUNK_CAP, byte_cap: int = _BYTE_CAP,
             prefer: str = "") -> tuple:
    """(발견한 경로들, 사유) — `fetch(url) -> (본문|None, 사유)` 를 주입받는다.

    주입이 계약인 이유: 샌드박스는 네트워크가 막혀 있어(#336 회귀가 소켓을
    끊는다) 실호출로는 회귀를 못 쓴다. 픽스처로 태워 **값으로** 고정한다.

    빈 목록은 실패가 아니다 — 청크는 받았는데 그 조각이 없을 수도 있다.
    그래서 사유를 같이 돌려주고 호출부가 갈래를 말한다(#54·#82).
    """
    html, why = fetch(page_url)
    if not html:
        return [], (why or "페이지를 받지 못했습니다")
    urls = chunk_urls(html, page_url, prefer=prefer or must_contain)
    if not urls:
        return [], (f"페이지({len(html):,}B)에서 Next.js 청크 URL 을 "
                    "한 건도 찾지 못했습니다 — SPA 가 아닐 수 있습니다")
    found: list = []
    seen: set = set()
    used = 0
    scanned = 0
    for u in urls[:chunk_cap]:
        js, jw = fetch(u)
        if not js:
            log.info("spa_discover: 청크 수신 실패 %s — %s", u, jw)
            continue
        scanned += 1
        used += len(js)
        for p in api_paths(js, must_contain=must_contain):
            if p not in seen:
                seen.add(p)
                found.append(p)
        if used >= byte_cap:
            log.info("spa_discover: 바이트 예산 소진(%d/%d) — %d청크에서 멈춤",
                     used, byte_cap, scanned)
            break
    if not scanned:
        return [], (f"청크 {len(urls)}개를 찾았지만 한 건도 받지 못했습니다")
    if not found:
        return [], (f"청크 {scanned}개({used:,}B)를 훑었는데 "
                    f"'{must_contain}' 을 품은 API 경로가 없습니다")
    return found, ""
