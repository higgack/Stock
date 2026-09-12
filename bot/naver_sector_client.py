"""Naver 증권 시세 스크래퍼 — 업종/테마 등락 + 상한가·하한가.

- fetch_sector_movers(): sise_group.naver?type=upjong → 업종 상승/하락 TOP.
- fetch_themes(): sise/theme.naver → 테마별 등락(별도 페이지).
- fetch_upper_lower(): 상한가·하한가(sise_upper/lower). 52주 신고저 페이지는
  불안정해 제거(2026-06-10) — KR 신고저는 유니버스 스캔이 후속 과제.

무료·무키. euc-kr 디코딩. graceful — 실패/빈 결과 시 빈값. 사용자 정책
2026-06-10: 리스크 없는 한 가장 빠르게 → 장중 30초 캐시(session-aware, 장 밖 재fetch 0).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from bot import naver_diag as _nd

log = logging.getLogger("bot.naver_sector")

def _now_kst_label() -> str:
    """명시적 KST 라벨 — 서버 로컬타임 의존 제거 (사용자 정책: 모든 표기
    한국시간 기준, 2026-06-11)."""
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")


_BASE = "https://finance.naver.com/sise"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "naver_sector"
_CACHE_TTL_SEC = 30  # 30초(사용자 2026-06-15 '업종·테마 30초·네이버 문제없음') — 1 HTTP/요청

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                   " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://finance.naver.com/sise/",
}

_PCT_RE = re.compile(r'([+\-]?)(\d{1,3}\.\d{1,2})\s*%')
_ITEM_RE = re.compile(r'/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>', re.I)
# 업종 그룹 링크(번호+이름) — 업종맵 멤버 스캔용(옛 HTML 경로).
_UPJONG_NO_RE = re.compile(
    r'sise_group_detail\.naver\?type=upjong[^"]*?no=(\d+)"[^>]*>([^<]+)</a>', re.I)


_JSON_HEADERS = dict(_HEADERS, **{
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://stock.naver.com/",
})


def _get2(url: str, **kwargs) -> tuple[Optional[str], str]:
    """(본문, 실패 사유) — 갈래를 이름으로 돌려준다(#82). 성공이면 사유는 "".

    옛 `_get` 은 정지·403·타임아웃·빈본문을 전부 `None` 하나로 뭉쳐서, 위젯이
    사라진 이유를 화면도 로그도 말하지 못했다(사용자 2026-09-11 "갑자기 한국이
    메인대시보드에서 없어졌어? 또 왜그런거야?")."""
    try:
        from bot.finviz_client import naver_paused
        if naver_paused():       # NAVER_PAUSE → fetch skip(호출부 캐시 폴백)
            return None, _nd.PAUSED
    except Exception:
        pass
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15, **kwargs)
        resp.encoding = "euc-kr"
        if resp.status_code != 200 or not resp.text:
            log.warning("naver_sector: %s -> HTTP %s (%dB)", url,
                        resp.status_code, len(resp.content or b""))
            return None, _nd.http_reason(resp.status_code,
                                         len(resp.content or b""))
        return resp.text, ""
    except Exception as exc:
        log.warning("naver_sector: fetch failed %s: %s", url, exc)
        return None, _nd.http_reason(None, exc=exc)


def _get2_json(url: str, **kwargs) -> tuple[object, str]:
    """(파싱된 JSON, 실패 사유) — 공용 구현(`naver_diag.get_json`)에 위임한다.

    2026-09-11 리뷰 M6: 형제 클라이언트와 이 함수를 **복제**하고 있었고 이미
    갈라져 있었다(#38). 헤더·로거만 여기서 준다.
    """
    return _nd.get_json(url, headers=_JSON_HEADERS, log=log,
                        tag="naver_sector", **kwargs)


def _get(url: str, **kwargs) -> Optional[str]:
    """본문만 — 사유가 필요한 호출부는 `_get2` 를 쓴다."""
    return _get2(url, **kwargs)[0]


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&middot;", "·")
    return " ".join(s.split()).strip()


def _cell_texts(row_html: str) -> list[str]:
    out = []
    for inner in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.DOTALL | re.I):
        out.append(_clean(inner))
    return out


def _pct_from_row(row_html: str) -> Optional[float]:
    """행의 등락률(%) — 부호 텍스트 우선, 없으면 색 클래스(red/nv)로 추정."""
    m = _PCT_RE.search(_clean(row_html))
    if not m:
        return None
    num = float(m.group(2))
    sign = m.group(1)
    if sign == "-":
        return -num
    if sign != "+" and re.search(r"(nv01|nv02|blue|down)", row_html, re.I):
        return -num
    return num


_THEME_LINK_RE = re.compile(
    r'sise_group_detail\.naver\?type=theme[^"]*?no=(\d+)"[^>]*>([^<]+)</a>', re.I)
_SIGNED_PCT_RE = re.compile(r'([+\-]?)(\d{1,3}\.\d{1,2})\s*%')


def parse_themes_full(html: str) -> list[dict]:
    """테마 표 → [{name, no, pct, pct3, leaders}].

    pct=전일대비 등락률, pct3=최근3일 평균 등락률, leaders=주도주(최대 2)."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I):
        m = _THEME_LINK_RE.search(row)
        if not m:
            continue
        no, name = m.group(1), _clean(m.group(2))
        if not name or no in seen:
            continue
        # % 값들 — 순서대로 전일대비, 최근3일
        pcts: list[float] = []
        for pm in _SIGNED_PCT_RE.finditer(_clean(row)):
            v = float(pm.group(2))
            if pm.group(1) == "-":
                v = -v
            pcts.append(round(v, 2))
        pct = pcts[0] if pcts else None
        pct3 = pcts[1] if len(pcts) > 1 else None
        # 주도주 — /item/main.naver?code= 의 종목명+코드(최대 2) → 종목분석 링크용
        leaders = []
        seen_codes: set = set()
        for lm in re.finditer(
                r'/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>', row, re.I):
            code = lm.group(1)
            nm = _clean(lm.group(2))
            if nm and code not in seen_codes:
                seen_codes.add(code)
                leaders.append({"name": nm, "code": code})
        seen.add(no)
        out.append({"name": name, "no": no, "pct": pct,
                    "pct3": pct3, "leaders": leaders[:2]})
    return out


def _cached(name: str, ttl: float | None = None):
    """ttl=None(기본) = 세션-인지(KR 장중 30초/장 밖 재fetch 0) — 업종·테마·
    상한가/하한가처럼 '장 마감 후엔 더 바뀔 값이 없는' 실시간 시세 전용.

    ttl=<초> 명시 시 그 값으로 **평시간 TTL** 사용(장 개폐 무관) — 2026-08-03
    fix: 투자자예탁금(deposit.json)은 KOFIA 가 T-1 데이터를 자체 스케줄로
    공표(KR 장마감 15:30 이후·저녁에도 갱신될 수 있음, 사용자 실측 —
    TNBfolio 는 18:35 에 07/31 반영했는데 우리는 장중 스냅샷에 갇혀 07/30
    고정)해 KR 세션과 무관하다. 세션-frozen 캐시를 그대로 쓰면 장 마감 후
    새 공표본이 나와도 다음 장 개장까지 재fetch 자체가 안 됨 — fsc_client.
    _kofia_series 의 안쪽 3h 캐시(날짜-keyed, 하루종일 재확인)가 있어도 이
    바깥 캐시가 먼저 막아 무의미해짐."""
    cache_file = _CACHE_DIR / name
    if cache_file.exists():
        try:
            mt = cache_file.stat().st_mtime
            if ttl is not None:
                fresh = (time.time() - mt < ttl)
            else:
                # 세션-인지(사용자 2026-06-15 '업종 30초'): KR 장중 30초 / 장
                # 밖엔 마지막 산출본 fresh(재fetch 0). _session_fresh 부재 시
                # _CACHE_TTL_SEC 폴백.
                try:
                    from bot.finviz_client import _session_fresh
                    fresh = _session_fresh("KR", mt, _CACHE_TTL_SEC)
                except Exception:
                    fresh = (time.time() - mt < _CACHE_TTL_SEC)
            if fresh:
                return json.loads(cache_file.read_text())
        except Exception:
            pass
    return None


def _cache_write(name: str, obj) -> None:
    """원자적 저장 — `write_text` 는 truncate 후 버퍼 쓰기라, 배경 갱신
    스레드가 쓰는 중에 요청 스레드가 읽으면 **찢어진 JSON** 을 본다(둘 다
    None → 그 클릭이 전체 수집을 기다린다). 임시파일 + `os.replace`."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dst = _CACHE_DIR / name
        tmp = dst.with_suffix(dst.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False))
        os.replace(tmp, dst)
    except Exception:
        pass


# ── KR 종목코드 → 업종(한글) 맵 (사용자 2026-06-14 'KR 신고가·급등락에 업종 한글') ──
# 네이버 domestic 시세 객체엔 업종이 없음(_kr_row 'ind 미제공'). 업종 그룹
# (sise_group upjong) 의 각 그룹 상세 페이지 멤버를 스캔해 코드→업종 맵 구축.
# 업종 멤버십은 안정적 → 7일 캐시 + SWR 백그라운드(렌더 블로킹 0, world_upjong_name
# 패턴). 업종명은 네이버 한글 그대로(KR 은 번역 안 함 — 사용자 '그냥 한글로 당연히').
_KR_IND_CACHE = "kr_industry_map.json"
_KR_IND_TTL = 7 * 86400
_kr_ind_building = False
_kr_ind_lock = threading.Lock()


# 업종맵 빌드가 **왜** 비었나 — `--check` 가 읽는다. 조용한 실패는 몇 달 동안
# 기능 하나를 없는 셈 친다(#12·#43, 독립 리뷰 2026-09-11 M5).
_KR_IND_FAIL: dict = {"reason": ""}
# 실패를 **파일로** 남긴다. 이유 둘:
#  (a) `--check` 는 별도 프로세스라 모듈 전역만 보면 항상 초기값("")이다 —
#      그 줄은 사실상 죽은 코드였다(독립 리뷰 2026-09-12 실측, #291·#123 계열).
#  (b) 백오프가 **재시작을 넘어야** 한다 — watchdog 재시작마다 백오프가
#      풀리면 죽은 URL 을 다시 두드린다.
_KR_IND_FAIL_FILE = "kr_industry_fail.json"
# 빌드가 0건으로 끝난 뒤 **다시 시도하지 않을 시간**. 빌드는 in-flight 가드가
# `finally` 에서 즉시 풀리고 캐시 파일은 0건이라 안 써지므로, 옛 코드는
# **렌더마다** 죽은 URL 로 스레드를 띄웠다(사용자 2026-09-12 "비용 낭비").
# 리터럴로 못박는다(#66) — 15분이면 원천이 복구된 날 한 시간 안에 따라붙는다.
_KR_IND_BACKOFF_SEC = 900


def _kr_ind_fail_record(reason: str) -> None:
    """실패 사유와 시각을 디스크에 남긴다(백오프·진단 공용).

    성공(`reason=""`)이면 기록을 **지운다** — 남겨 두면 그 시각이 '마지막 실패'
    로 읽혀 정상 빌드까지 백오프가 잡아먹는다(독립 리뷰 2026-09-12).
    """
    _KR_IND_FAIL["reason"] = reason
    try:
        if reason:
            _cache_write(_KR_IND_FAIL_FILE, {"at": time.time(), "reason": reason})
        else:
            (_CACHE_DIR / _KR_IND_FAIL_FILE).unlink(missing_ok=True)
    except Exception:                                          # noqa: BLE001
        log.warning("kr industry map: 실패 기록을 못 남겼다 — 백오프가 안 걸린다")


def _kr_ind_fail_state() -> tuple[float, str]:
    """(마지막 실패 시각, 사유) — 없으면 (0.0, "").

    ⚠️ 옛 판은 이번 프로세스가 실패를 겪었으면 `time.time()` 을 돌려줬다 —
    그러면 `now - now = 0` 이라 **백오프가 영원히 안 풀리고** 한 번 실패한
    업종맵이 재시작 전까지 죽는다(독립 리뷰 2026-09-12 실측: +2h 시뮬에도
    재시도 0회). 주기적으로 발동하는 가드를 넣을 땐 "이게 매번 걸리면 계열이
    어떻게 되나"를 먼저 물을 것(#178). **시각은 기록이 말한다** — 전역은
    사유 문구의 폴백으로만 쓴다.
    """
    at, why = 0.0, ""
    try:
        d = json.loads((_CACHE_DIR / _KR_IND_FAIL_FILE).read_text())
        at, why = float(d.get("at") or 0.0), str(d.get("reason") or "")
    except Exception:                                          # noqa: BLE001
        pass
    return at, why or (_KR_IND_FAIL.get("reason") or "")


def kr_industry_fail_reason() -> str:
    """직전 업종맵 빌드가 0건이었던 사유("" = 사유 없음).

    ⚠️ 모듈 전역만 보면 **별도 프로세스**(`--check`·프로브)에서 항상 빈
    문자열이다 — 그래서 그 줄이 한 번도 안 찍혔다. 디스크 기록으로 폴백한다.
    """
    return _KR_IND_FAIL.get("reason") or _kr_ind_fail_state()[1]


def _build_kr_industry_map() -> None:
    """네이버 업종 그룹 멤버 스캔 → {6자리코드 → 업종명(한글)}. 백그라운드 1회.

    ⚠️ 이 경로는 아직 **옛 HTML**(`sise_group.naver`)을 훑는다 — 2026-09-11
    SPA 전환 뒤에는 정의상 0건이므로 맵이 영영 안 채워진다. 대체 엔드포인트를
    **추측해서 짜지 않는다**(#151 — `naver_spa_probe` ⑤ '업종 멤버(국내)' 가
    실재 여부를 재고, 그 실측 뒤에 갈아탄다). 그 사이 실패를 **말은 한다**.
    """
    global _kr_ind_building
    try:
        html = _get(f"{_BASE}/sise_group.naver", params={"type": "upjong"})
        groups: list = []
        seen: set = set()
        if html:
            for m in _UPJONG_NO_RE.finditer(html):
                no, name = m.group(1), _clean(m.group(2))
                if no not in seen and name:
                    seen.add(no)
                    groups.append((no, name))
        if not groups:
            # 조용히 return 하면 `kr_industry_map()` 이 매 렌더마다 스레드를
            # 띄워 같은 실패를 반복하는데 **아무도 모른다**(#12 silent-fail 금지).
            _kr_ind_fail_record(
                f"업종 그룹 0건 — 원문 {len(html or ''):,}자"
                + ("(응답은 왔다 = SPA 전환으로 표가 사라진 것, "
                   "`naver_spa_probe` ⑤ 로 대체 경로를 잰다)" if html
                   else "(응답 자체가 없다 = 도달 실패·정지 등)"))
            log.warning("kr industry map: %s", _KR_IND_FAIL["reason"])
            return
        _kr_ind_fail_record("")      # 성공 — 기록을 지운다(아래 참조)

        def _members(grp):
            no, name = grp
            dh = _get(f"{_BASE}/sise_group_detail.naver",
                      params={"type": "upjong", "no": no})
            res = []
            if dh:
                for im in _ITEM_RE.finditer(dh):
                    res.append((im.group(1), name))
            return res

        out: dict = {}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            for members in ex.map(_members, groups):
                for code, name in members:
                    out.setdefault(code, name)   # 한 종목 = 첫 업종(소속 1개)
        if out:
            _cache_write(_KR_IND_CACHE, out)
    except Exception as exc:                                   # noqa: BLE001
        # ⚠️ 옛 판은 예외 경로에서 **아무것도 기록하지 않아** 백오프가 안 걸렸다
        # — 원천이 계속 던지면 렌더마다 다시 나간다(독립 리뷰 2026-09-12).
        log.warning("kr industry map build: %s", exc)
        _kr_ind_fail_record(f"빌드 예외 — {type(exc).__name__}: {str(exc)[:80]}")
    finally:
        with _kr_ind_lock:
            _kr_ind_building = False


def kr_industry_map() -> dict:
    """{6자리 종목코드 → 업종명(한글)} — 네이버 업종 그룹 멤버 스캔. 7일 캐시 +
    SWR 백그라운드(렌더 블로킹 0). 첫 방문 시 빌드 kick 후 빈/스테일 반환, 이후
    방문부터 채워짐. graceful {}."""
    fp = _CACHE_DIR / _KR_IND_CACHE
    raw = None
    try:
        if fp.exists():
            raw = json.loads(fp.read_text())
            if time.time() - fp.stat().st_mtime < _KR_IND_TTL:
                return raw
    except Exception:
        raw = None
    # ⚠️ in-flight 가드(`_kr_ind_building`)는 `finally` 에서 **즉시** 풀리므로
    # 렌더 **사이**를 막지 못한다. 빌드가 0건이면 캐시 파일도 안 써져서
    # `fp.exists()` 가 영원히 거짓 — 그래서 렌더마다 죽은 URL 로 스레드가
    # 나갔다(사용자 2026-09-12). 실패 뒤에는 **백오프**를 둔다(#72 차단기와
    # 같은 처방 · #25 늘 도는 재시도는 아무것도 안 재는 것과 같다).
    _at, _why = _kr_ind_fail_state()
    # 사유가 **있을 때만** 막는다 — 성공 기록(`reason=""`)에도 시각이 찍히므로
    # 시각만 보면 정상 빌드까지 백오프가 잡아먹는다(독립 리뷰).
    if _why and _at and (time.time() - _at) < _KR_IND_BACKOFF_SEC:
        return raw or {}
    global _kr_ind_building
    with _kr_ind_lock:
        if not _kr_ind_building:
            _kr_ind_building = True
            threading.Thread(target=_build_kr_industry_map, daemon=True,
                             name="kr-industry-map").start()
    return raw or {}


def apply_kr_industry(items: list) -> list:
    """KR 항목 리스트에 ind(업종 한글) 백필 — ticker '005930.KS' → 코드 005930 →
    kr_industry_map. in-place·순수(맵 1회 조회)·graceful. 사용자 2026-06-14."""
    if not items:
        return items
    try:
        imap = kr_industry_map()
    except Exception:
        return items
    if not imap:
        return items
    for it in items:
        if it.get("ind"):
            continue
        code = str(it.get("ticker") or "").split(".")[0]
        ind = imap.get(code)
        if ind:
            it["ind"] = ind
    return items


def _cache_read_any(name: str) -> tuple[Optional[dict], Optional[float]]:
    """신선도와 무관하게 **마지막 산출본**과 그 나이(초) — 원천이 실패한 날의
    폴백(#136 폴백 조건은 '실패' 가 아니라 '요구를 충족했나'). 없으면 (None, None)."""
    f = _CACHE_DIR / name
    try:
        if not f.exists():
            return None, None
        return json.loads(f.read_text()), max(0.0, time.time() - f.stat().st_mtime)
    except Exception:                                        # noqa: BLE001
        return None, None


# ── 업종 등락: 네이버 JSON API (2026-09-11 SPA 전환) ────────────────────────
# finance.naver.com 이 Next.js SPA 로 바뀌어 서버 렌더 표가 사라졌다(VM 실측:
# 121,364B 응답에 `<table` 0건). CN·HK·JP 업종은 이미 JSON API 로 살고 있었고
# **한국만 HTML 스크래핑에 남아** 있어서 한국만 죽었다(#38 같은 것을 그리는
# 화면은 같은 경로로).
#
# VM 실측(2026-09-11 naver_spa_probe v2)으로 확정한 것:
#   · 무인자        → 20행   ← **기본 페이지 크기**다(전부가 아니다)
#   · ?pageSize=100 → 79행   ← 전 업종
#   · ?size=100 / ?perPage=100 / ?page=2 → 전부 20행(그 파라미터는 안 먹는다)
# 20행만 보고 상위·하위 10을 매기면 **화면이 조용히 틀린다**(#45 모집단) —
# 그래서 pageSize 는 필수이고, 20행이 오면 그 사실을 사유로 남긴다.
#
# 행 모양(실측): {"name":"손해보험","changeRate":"4.18","thistime":"20260911155908",
#   "riseCnt":"10","fallCnt":"2","totalMarketSum":"57204668","leadingItem":"...", …}
# `changeRate` 는 **문자열**이고 부호를 포함한다. `thistime` 은 원천 기준시각
# (렌더 시각이 아니다 — 규칙 10b).
_UPJONG_API = "https://stock.naver.com/api/domestic/market/upjong/list"
_UPJONG_PAGE_SIZE = 100          # 실측 79행 < 100. 리터럴로 못박는다(#66)
_UPJONG_DEFAULT_PAGE = 20        # 이 수가 오면 pageSize 가 안 먹은 것이다
# 2026-09-11 실측 79행. 상·하위 10 랭킹이라 전수의 일부만 와도 **값이 다 있어서**
# 조용히 틀린다(#45·#280) — '20 인가'만 묻는 검사는 원천이 40·50 을 주기 시작하면
# 눈이 먼다(#24 열거형). 하한을 두고 그 아래는 부분으로 본다(독립 리뷰 H3).
_UPJONG_MIN_GROUPS = 60          # 실측 79 에서 넉넉히 내린 하한


def parse_upjong_json(rows: object) -> list:
    """네이버 업종 JSON → [{name, pct, rise, fall, mcap}] (순수).

    값이 문자열로 오므로 숫자 변환에 실패한 행은 **버리되 조용히 버리지
    않는다** — 호출부가 개수를 대조한다(#54). 이름이나 등락률이 없으면
    그 행은 등락 랭킹에 쓸 수 없다.
    """
    out = []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        try:
            pct = float(str(r.get("changeRate")).replace(",", ""))
        except (TypeError, ValueError):
            continue
        item = {"name": name, "pct": pct}
        for src, dst in (("riseCnt", "rise"), ("fallCnt", "fall"),
                         ("totalMarketSum", "mcap")):
            try:
                item[dst] = float(str(r.get(src)).replace(",", ""))
            except (TypeError, ValueError):
                pass
        out.append(item)
    return out


def upjong_asof(rows: object) -> str:
    """원천이 찍은 기준시각 `thistime`(YYYYMMDDHHMMSS) → 'MM-DD HH:MM' (순수).

    렌더 시각을 쓰면 수집이 멈춘 날도 방금처럼 보인다(규칙 10b·#304).
    못 읽으면 빈 문자열 — 지어내지 않는다(#165).
    """
    if not isinstance(rows, list):
        return ""
    for r in rows:
        t = str((r or {}).get("thistime") or "") if isinstance(r, dict) else ""
        if len(t) >= 12 and t.isdigit():
            return f"{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}"
    return ""


def fetch_sector_movers(top_n: int = 10) -> dict:
    """업종 등락률 → {'up': [...], 'down': [...], 'ts': iso}. 장중 30초 캐시.

    실패하면 **빈 dict 를 주고 위젯이 사라지는 대신** 마지막 산출본을 `stale` 로
    돌려주고(형제 TW 업종 위젯과 같은 규약, #306) 어느 갈래로 실패했는지 `reason`
    에 싣는다 — 침묵이 최악이다(#43·#82). 저장분조차 없으면 rows 는 비지만
    `reason` 은 남아 화면이 사유를 적는다."""
    c = _cached("upjong.json")
    if c is not None:
        return c
    raw, why = _get2_json(_UPJONG_API, params={"pageSize": _UPJONG_PAGE_SIZE})
    groups = parse_upjong_json(raw)
    n_raw = len(raw) if isinstance(raw, list) else 0
    partial = bool(groups) and n_raw < _UPJONG_MIN_GROUPS
    if partial:
        # 전 업종이 아니므로 상위·하위 10 이 틀린다(#45). 값은 주되 사유를 남기고
        # **캐시하지는 않는다** — 부분을 완전본으로 구우면 TTL 내내 틀린 랭킹이
        # 서빙된다(#280 부분·빈 결과는 캐시하지 않는다).
        how = ("기본 페이지" if n_raw == _UPJONG_DEFAULT_PAGE
               else f"기대 하한 {_UPJONG_MIN_GROUPS}개 미만")
        why = (f"업종이 {n_raw}개만 왔습니다({how}) — 전 업종 랭킹이 "
               "아닐 수 있습니다. pageSize 파라미터 확인 필요")
        log.warning("naver upjong: 부분 수신 — %d행(%s)", n_raw, how)
    if not groups:
        if not why:
            # ⚠️ dict 가 오면 `n_raw` 는 0 이라 "원천 응답(0행)" 이라는 **거짓
            # 숫자**를 적고 운영자를 파서로 보낸다 — `--check` 는 같은 경우를
            # `shape_reason` 으로 맞게 말하고 있었다(형제가 갈린 것, #38·#147,
            # 독립 리뷰 M3). 계약 변경과 '행은 왔는데 못 읽음' 은 처방이 다르다.
            why = (_nd.parse_reason("업종 행", n_raw, unit="행")
                   if isinstance(raw, list)
                   else _nd.shape_reason("업종 목록", raw))
        prev, age = _cache_read_any("upjong.json")
        if prev and (prev.get("up") or prev.get("down")):
            return dict(prev, stale=True, stale_min=int((age or 0) // 60),
                        reason=why)
        return {"up": [], "down": [], "ts": "", "reason": why}
    ups = sorted([s for s in groups if s["pct"] > 0],
                 key=lambda x: x["pct"], reverse=True)[:top_n]
    downs = sorted([s for s in groups if s["pct"] < 0],
                   key=lambda x: x["pct"])[:top_n]
    if not ups and not downs:
        # 업종은 잡혔는데 상승·하락이 **둘 다 0** = 등락률을 못 읽은 것(전 업종
        # 정확히 보합은 실무상 없다). 이걸 성공으로 캐시하면 `_session_fresh` 가
        # 장 밖 내내 fresh 로 보아 **다음 개장까지 빈 위젯이 재시도 없이** 서빙된다
        # — 이 커밋이 고치려던 바로 그 증상이다(독립 리뷰 2026-09-11 · #54·#119).
        prev, age = _cache_read_any("upjong.json")
        why = _nd.parse_reason(f"업종 {len(groups)}개의 등락률", n_raw, unit="행")
        if prev and (prev.get("up") or prev.get("down")):
            return dict(prev, stale=True, stale_min=int((age or 0) // 60), reason=why)
        return {"up": [], "down": [], "ts": "", "reason": why}
    # 기준시각은 **원천이 찍은 것**을 쓴다(렌더 시각이 아니다, 규칙 10b·#304).
    # 원천이 안 주면 우리 수집 시각으로 떨어지되 그건 '값 수집' 이다.
    # ⚠️ `ts` 한 칸이 **두 의미를 대표**하고 있었다 — 원천이 `thistime` 을 빼면
    # 조용히 우리 수집 시각으로 떨어지는데 화면 라벨은 그대로 '기준' 이었다.
    # 그 순간 수집이 멈춰 있어도 화면은 방금처럼 보인다(규칙 10b·#34 한 라벨이
    # 두 계정을 대표하면 한쪽은 반드시 거짓말). 어느 쪽인지 **payload 가 밝히고**
    # 화면이 따른다(#136) — 레포에 이미 '값 수집' 규약이 있다(#304).
    _src_ts = upjong_asof(raw)
    out = {"up": ups, "down": downs,
           "ts": _src_ts or _now_kst_label(),
           "ts_kind": "source" if _src_ts else "collected",
           # ⚠️ 2026-09-12 에 `all` 키를 지웠다 — 그걸 그리던 `/theme` 의
           # '업종별 시세(전체)' 표가 사용자 요청으로 제거됐고(테마만 남김),
           # 남겨 두면 주석이 없는 화면을 가리킨다(#55 설명이 코드와 어긋나면
           # 버그 · §작업 원칙 죽은 경로는 삭제). 위젯은 상·하위 10만 쓴다.
           "scanned": len(groups)}
    if why:                            # 부분 페이지 등 — 값은 있지만 사유가 있다
        out["reason"] = why
    if partial:
        out["partial"] = True
        return out                     # 부분은 last-good 으로 굽지 않는다(#280)
    _cache_write("upjong.json", out)
    return out


def _first_big(cells: list) -> Optional[float]:
    """행에서 첫 큰 숫자(5자리+) — 고객예탁금 추정."""
    for cc in cells[1:]:
        m = re.search(r"[\d,]{5,}", cc)
        if m:
            try:
                return float(m.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


# 신용잔고 현실 범위(억원) — 잘못된 컬럼 매칭 값 차단 가드
_CRED_MIN, _CRED_MAX = 150000.0, 800000.0

# deposit.json 산출 스키마 버전 — 필드 추가/변경 시 +1 (구버전 캐시 1회 무효화)
_DEPOSIT_SCHEMA_V = 4   # 2=코스피/코스닥 신용 분리 · 3=예탁증권담보융자(2026-07-08)
                        # · 4=VKOSPI 시리즈(2026-08-08, KIS 소스)

# VKOSPI(코스피 200 변동성지수) KIS 업종상세코드. pykrx/ECOS 둘 다 미제공 확인
# 후(2026-08-08) KIS 공식 idxcode.mst(FAQ 종목정보 다운로드·업종코드) 실측
# 파싱으로 확정 — 시장구분 '0', 코드 '0503', 명칭 'VKOSPI' 정확히 일치
# (추측 아님, 다운로드한 마스터파일에서 실제 확인).
_KIS_VKOSPI_IDX_CODE = "0503"
_LAST_DEPOSIT_OK: dict = {}


def _fsc_date(d: str) -> str:
    return f"{d[:4]}.{d[4:6]}.{d[6:8]}" if d and len(d) >= 8 else (d or "")


def _fetch_deposit_fsc() -> dict:
    """FSC(금융투자협회) 공식 API → 고객예탁금·신용잔고 둘 다 견고하게.

    Naver 탭 구조가 불안정해 신용잔고가 안 나오던 문제 해소(사용자 2026-06-10).
    값=억원, 일별 시계열 ~6개월. 키(DATA_GO_KR_API_KEY) 부재/실패 시 {}."""
    try:
        from bot import fsc_client
        dser = fsc_client.deposit_series_eok(130)
        cser = fsc_client.credit_series_eok(130)
    except Exception as exc:
        log.warning("deposit FSC failed: %s", exc)
        return {}
    out: dict = {}
    if dser:
        out["date"] = _fsc_date(dser[-1][0])
        out["deposit"] = round(dser[-1][1], 1)
        if len(dser) >= 2:
            out["deposit_chg"] = round(dser[-1][1] - dser[-2][1], 1)
        out["deposit_series"] = [{"d": _fsc_date(d), "v": round(v, 1)} for d, v in dser]
    if cser:
        out["credit"] = round(cser[-1][1], 1)
        if len(cser) >= 2:
            out["credit_chg"] = round(cser[-1][1] - cser[-2][1], 1)
        out["credit_series"] = [{"d": _fsc_date(d), "v": round(v, 1)} for d, v in cser]
    # 시장별(코스피/코스닥) 신용잔고 — 같은 KOFIA 신용공여 응답의 시장 필드
    # (사용자 2026-07-06 '왼쪽처럼 코스피·코스닥 신용잔고 추가'). 필드명
    # 런타임 발견(fsc_client) — 미발견 시 키 자체가 없어 위젯 graceful 생략.
    try:
        split = fsc_client.credit_split_series_eok(130)
    except Exception as exc:
        log.warning("credit split fetch failed: %s", exc)
        split = {}
    for mkt, prefix in (("kospi", "credit_kospi"), ("kosdaq", "credit_kosdaq")):
        ser = split.get(mkt) or []
        if not ser:
            continue
        out[prefix] = round(ser[-1][1], 1)
        if len(ser) >= 2:
            out[f"{prefix}_chg"] = round(ser[-1][1] - ser[-2][1], 1)
        out[f"{prefix}_series"] = [{"d": _fsc_date(d), "v": round(v, 1)}
                                   for d, v in ser]
    # 예탁증권담보융자 — 6번째 지표(사용자 2026-07-08 '하나 더'). 같은 신용
    # 공여 응답 필드라 추가 호출 0. graceful.
    try:
        coll = fsc_client.collateral_loan_series_eok(130)
    except Exception as exc:
        log.warning("collateral loan fetch failed: %s", exc)
        coll = []
    if coll:
        out["collateral"] = round(coll[-1][1], 1)
        if len(coll) >= 2:
            out["collateral_chg"] = round(coll[-1][1] - coll[-2][1], 1)
        out["collateral_series"] = [{"d": _fsc_date(d), "v": round(v, 1)}
                                    for d, v in coll]
    if out:
        # 출처 정직화 (2026-06-10 사용자 review): 데이터는 FSC(금융투자협회
        # 종합통계)인데 위젯/차트 라벨이 'Naver' 하드코딩 — 06.05(FSC 최신)
        # vs Naver 페이지 06.08 비교 혼란의 절반이 이 라벨. 렌더가 이 필드로
        # 실제 출처 + 데이터 일자를 표기.
        out["source"] = "금융투자협회"
    return out


# 예탁금 기준일이 얼마나 뒤처졌나 — 판정을 재구현하지 않고 시장타이밍의
# '마지막 완결 세션' 을 그대로 쓴다(#35·#38 복제하면 두 화면이 갈라진다).
# ⚠️ KOFIA 의 실제 공표 시차(T+1 인지 T+2 인지)는 **재지 않았다** — 그래서
# 여기서 단정하지 않고 두 날짜를 나란히 보여 사용자가 검산하게 한다(#165·#202).
# ⚠️ 문턱의 **단위**가 기준과 같아야 한다(독립 리뷰 2026-09-08): 기준은
# '마지막 완결 **세션**' 인데 문턱을 **달력일**로 두면 연휴가 그대로 오차가
# 된다 — 실측(XKRX 2026 캘린더)으로 T+1 공표 가정에서도 9세션, T+2 면
# 103세션에서 ⚠️ 가 떴다(추석·설·대체공휴일). 늘 뜨는 배지는 아무것도 안
# 재는 것과 같다(#25·#260). 같은 커밋의 `sessions_behind` 로 센다(#29).
_DEPOSIT_LAG_WARN_SESSIONS = 3   # 공표 시차를 못 쟀으므로 넉넉히(#27·#165)


def deposit_lag(date_str: str, session: str | None) -> dict:
    """{"gap_d", "gap_sessions", "session", "stale"} — 기준일이 마지막 완결
    세션보다 얼마나 뒤인가.

    순수 함수. 판정을 인라인으로 두면 태워볼 수 없다(#176·#41).
    재료가 없으면 판정하지 않는다 — 판정 불가는 통과가 아니다(#54).
    ⚠️ 화면에 적는 '며칠 전'은 사용자가 달력으로 검산하는 값이라 **달력일**
    이고, 경보 판정만 **세션**으로 한다 — 둘을 한 이름으로 뭉치지 않는다(#34).
    """
    from datetime import date as _date
    d = str(date_str or "").replace(".", "").replace("-", "")[:8]
    none = {"gap_d": None, "gap_sessions": None,
            "session": session or "", "stale": False}
    if len(d) != 8 or not d.isdigit() or not session:
        return none
    try:
        obs = _date(int(d[:4]), int(d[4:6]), int(d[6:8]))
        ses = _date(int(session[:4]), int(session[5:7]), int(session[8:10]))
    except (ValueError, IndexError):
        return none
    gap = (ses - obs).days
    sessions = None
    try:
        from bot.market_calendar import sessions_behind
        sessions = sessions_behind("KR", obs.isoformat(), ses.isoformat())
    except Exception as exc:                                   # noqa: BLE001
        log.debug("deposit_lag: 세션 격차 측정 실패: %s", exc)
    # 캘린더가 없으면 세션을 못 세므로 **경보하지 않는다** — 달력일로
    # 대신 재면 고치려던 연휴 오탐이 그대로 돌아온다(#146).
    return {"gap_d": gap, "gap_sessions": sessions, "session": session,
            "stale": bool(sessions is not None
                          and sessions >= _DEPOSIT_LAG_WARN_SESSIONS)}


def fetch_deposit() -> dict:
    """고객예탁금·신용잔고 → {date, deposit, credit, deposit_chg, credit_chg,
    deposit_series, credit_series}. 억원. 1h TTL(세션-인지 아님, 2026-08-08
    '시장유동성 섹션 전체 1시간 단위로' — fsc_client._kofia_series 의 안쪽
    캐시도 같은 리듬(1h)으로 맞춤, 하루종일 재확인).

    1차 FSC(금융투자협회 공식 API — 둘 다 견고·일별 시계열), 실패 시 Naver
    sise_deposit 폴백(고객예탁금만 견고, 신용은 컬럼 가드)."""
    c = _cached("deposit.json", ttl=1 * 3600)
    # 스키마 버전 게이트(2026-07-08): 세션-인지 캐시는 장 밖에서 무기한
    # fresh 라 새 필드(코스피/코스닥 신용 분리)가 다음 장까지 안 나타남 —
    # 구버전 산출본이면 miss 취급해 1회 재생성(재생성분엔 _v 기록).
    if c is not None and c.get("_v") == _DEPOSIT_SCHEMA_V:
        if isinstance(c, dict) and c.get("deposit") is not None:
            global _LAST_DEPOSIT_OK
            _LAST_DEPOSIT_OK = dict(c)
        return c
    out = _fetch_deposit_fsc()
    if not out or out.get("deposit") is None:
        out = _fetch_deposit_naver()
    # 주식형펀드 — 네이버 trendDeposit/chart 보조(사용자 2026-06-14, 예탁금/신용은
    # FSC 유지). 신용잔고 옆 3번째 차트(렌더 스캐폴드는 이미 merged).
    try:
        ef = _fetch_equity_fund_naver()
        if ef and isinstance(out, dict):
            out["equity_fund"] = round(ef[-1][1], 1)
            if len(ef) >= 2:
                out["equity_fund_chg"] = round(ef[-1][1] - ef[-2][1], 1)
            out["equity_fund_series"] = [{"d": _fsc_date(d), "v": round(v, 1)}
                                         for d, v in ef]
            out.setdefault("source", "금융투자협회")
    except Exception as exc:
        log.warning("equity fund merge failed: %s", exc)
    # VKOSPI(코스피 변동성지수) — 예탁증권담보융자 추이 차트 자리 대체(사용자
    # 2026-08-06 요청, 2026-08-08 KIS API 로 재구현 — pykrx/ECOS 무료소스엔
    # 없어 1차 시도 롤백했었음). KIS 국내업종 기간별시세, 실패해도 이 위젯의
    # 나머지 지표엔 영향 없음(graceful).
    try:
        from bot.kis_client import get_kis
        vbars = get_kis().get_domestic_index_daily(_KIS_VKOSPI_IDX_CODE)
        if vbars and isinstance(out, dict):
            out["vkospi_series"] = [{"d": b["date"].replace("-", "."),
                                     "v": round(b["close"], 2)} for b in vbars]
            out["vkospi"] = round(vbars[-1]["close"], 2)
            if len(vbars) >= 2:
                out["vkospi_chg"] = round(vbars[-1]["close"] - vbars[-2]["close"], 2)
    except Exception as exc:
        log.warning("vkospi merge failed: %s", exc)
    if isinstance(out, dict) and out:
        out["_v"] = _DEPOSIT_SCHEMA_V
        if out.get("deposit") is not None:
            _LAST_DEPOSIT_OK.clear()
            _LAST_DEPOSIT_OK.update(out)
    if (not isinstance(out, dict) or out.get("deposit") is None) and _LAST_DEPOSIT_OK.get("deposit") is not None:
        out = dict(_LAST_DEPOSIT_OK)
        out.setdefault("source", "??????")
        out["_stale"] = True
    _cache_write("deposit.json", out)
    return out


def _fetch_equity_fund_naver() -> list:
    """네이버 trendDeposit/chart → 주식형펀드(beneficiaryCertificateStock) 시계열
    [(bizdate, 억원)] (사용자 2026-06-14, VM probe 구조 확정). 예탁금/신용도 같은
    응답에 있으나 위젯은 FSC 유지, 주식형펀드만 보조 추가. graceful []."""
    from bot.finviz_client import naver_paused
    if naver_paused():
        return []
    import requests
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=220)
    url = ("https://stock.naver.com/api/domestic/market/trendDeposit/chart"
           f"?startDate={start:%Y%m%d}&endDate={end:%Y%m%d}")
    try:
        r = requests.get(url, timeout=12, headers={
            "User-Agent": "Mozilla/5.0", "Accept": "application/json",
            "Referer": "https://stock.naver.com/"})
        rows = r.json() if r.status_code == 200 else []
    except Exception as exc:
        log.warning("naver equity fund fetch failed: %s", exc)
        return []
    out = []
    for it in rows if isinstance(rows, list) else []:
        d = str(it.get("bizdate") or "")
        try:
            v = float(str(it.get("beneficiaryCertificateStock")).replace(",", ""))
        except Exception:
            v = None
        if len(d) == 8 and v is not None:
            out.append((d, v))    # 억원
    return sorted(out)


def _fetch_deposit_naver() -> dict:
    """Naver sise_deposit 폴백 — 고객예탁금 견고(첫 큰 숫자), 신용은 가드."""
    out: dict = {}
    html = _get(f"{_BASE}/sise_deposit.naver")
    if html:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I)
        cred_idx = None
        for row in rows:                       # 헤더에서 신용잔고 컬럼 인덱스
            cells = _cell_texts(row)
            if any("고객예탁금" in cc for cc in cells):
                for i, cc in enumerate(cells):
                    if cred_idx is None and "신용" in cc:
                        cred_idx = i
                break

        def _num(cells: list, idx) -> Optional[float]:
            if idx is not None and idx < len(cells):
                m = re.search(r"-?[\d,]{4,}", cells[idx])
                if m:
                    try:
                        return float(m.group(0).replace(",", ""))
                    except ValueError:
                        return None
            return None

        data_rows = [c for row in rows
                     for c in [_cell_texts(row)]
                     if c and re.match(r"\d{2,4}[.\-/]\d{1,2}[.\-/]\d{1,2}", c[0])]
        if data_rows:
            cur = data_rows[0]
            prev = data_rows[1] if len(data_rows) > 1 else None
            dep = _first_big(cur)
            cred = _num(cur, cred_idx)
            if cred is not None and not (_CRED_MIN <= cred <= _CRED_MAX):
                cred = cred_idx = None          # 비현실 → 잘못된 컬럼, 생략
            out = {"date": cur[0], "deposit": dep, "credit": cred}
            if prev:
                pd = _first_big(prev)
                pc = _num(prev, cred_idx)
                if dep is not None and pd is not None:
                    out["deposit_chg"] = round(dep - pd, 1)
                if cred is not None and pc is not None:
                    out["credit_chg"] = round(cred - pc, 1)
            # 시계열(오래된→최신, ~6개월) — 그래프용
            dser, cser = [], []
            for cells in reversed(data_rows[:130]):
                dv = _first_big(cells)
                if dv is not None:
                    dser.append({"d": cells[0], "v": dv})
                cv = _num(cells, cred_idx)
                if cv is not None and _CRED_MIN <= cv <= _CRED_MAX:
                    cser.append({"d": cells[0], "v": cv})
            out["deposit_series"] = dser
            out["credit_series"] = cser
    if out:
        out["source"] = "Naver"   # 폴백 경로 — 라벨 정직화
    return out


# 테마 ~200개 = 페이지당 ~30 × 7. **리터럴로** 못박는다 — 상수로 검증하면
# 그 상수를 바꾸는 뮤테이션이 자기 자신을 통과시킨다(#66 tautology).
_THEME_PAGES = 7
# 낡은 캐시를 즉시 내줘도 되는 상한. 이보다 낡았으면 기다려서라도 새로 받는다
# — 낡은 값을 '현재'로 내보내지 않기 위해서다(#163). 화면은 어느 쪽이든
# 스냅샷 시각(ts)을 그대로 찍는다(#43).
_THEME_SWR_SEC = 600
_CHECK_VER = 7        # 5 = 테마 사다리 · 6 = 탐색 · 7 = 냉각·행동가능 사유

_BG_KEYS: set = set()
_BG_LOCK = threading.Lock()


def _read_cache(name: str):
    """신선도와 **무관하게** 저장분을 읽는다(SWR 용). 없으면 (None, None)."""
    f = _CACHE_DIR / name
    try:
        return json.loads(f.read_text()), f.stat().st_mtime
    except Exception:                                          # noqa: BLE001
        return None, None


def _refresh_async(name: str, fn, key: str) -> None:
    """낡은 값을 내준 뒤 뒤에서 채운다 — 같은 키는 한 번만(#113·#127).

    ⚠️ 배경과 전경이 **같은 single-flight 키**를 타야 한다. `_BG_KEYS` 만으로
    막으면 배경 수집이 도는 중에 SWR 창을 벗어난 요청이 들어와 두 벌(=14건)이
    동시에 네이버로 나가고 두 writer 가 경합한다(독립 리뷰 실측).

    ⚠️ **저장은 `fn` 이 한다** — 여기는 읽기만 한다. 배경이 따로 쓰면 `fn` 의
    저장 판정(부분·빈 결과 거부)이 우회된다(#38 쓰기는 단일 출처).
    """
    with _BG_LOCK:
        if name in _BG_KEYS:
            return
        _BG_KEYS.add(name)

    def _run() -> None:
        try:
            from bot.singleflight import once
            got = once(key, fn)
            # ⚠️ **여기서 쓰지 않는다.** 저장 판정은 `fn`(=_collect_and_store)
            # 하나가 한다 — 여기서 또 쓰면 fn 이 "부분이라 안 굽는다"고 거부한
            # 결과를 배경이 그대로 덮어 굽는다(독립 리뷰 실측: 4/7쪽 결과가
            # 완전본으로 캐시되고, 장 마감 뒤엔 `_session_fresh` 가 다음
            # 개장까지 그걸 fresh 로 본다). 쓰기는 단일 출처(#38·#119).
            if not (got and got.get("themes")):
                log.warning("naver_sector: 배경 갱신이 빈 결과 — %s 유지", name)
        except Exception as exc:                               # noqa: BLE001
            log.warning("naver_sector: 배경 갱신 실패 %s: %s", name, exc)
        finally:
            with _BG_LOCK:
                _BG_KEYS.discard(name)

    try:
        threading.Thread(target=_run, daemon=True, name=f"naver-{name}").start()
    except Exception as exc:                                   # noqa: BLE001
        # start() 가 던지면 `finally` 가 안 돌아 그 키의 배경 갱신이 프로세스
        # 수명 내내 죽는다 — 여기서 되돌린다.
        with _BG_LOCK:
            _BG_KEYS.discard(name)
        log.warning("naver_sector: 배경 갱신 스레드 시작 실패 %s: %s", name, exc)


# ── 테마 목록: JSON 경로(2026-09-12 SPA 전환) ──────────────────────────────
# `finance.naver.com/sise/theme.naver` 도 Next.js SPA 가 됐다 — VM 실측
# 121,896B 를 받는데 `parse_themes_full` 이 **0행**이고, 34시간 낡은 저장분이
# 서빙되고 있었다(사용자 2026-09-12 "이거 여전히 최신꺼 못가져오는데").
#
# ⚠️ 이 레포는 같은 자리에서 네 번 졌다(#151 죽은 이름 · #338 SPA 전환 ·
# #340 경로 이동 · 여기). 이름을 추측해 파서를 짜면 **죽은 경로를 배포**한다.
# 그래서 (a) 후보는 **실측으로 증명된 패턴**에서만 뽑고 (b) 어느 후보가 답했는지
# 결과에 싣고 (c) 전부 실패하면 **원천에게 물어본다**(`naver_spa_discover` 가
# 그 페이지의 Next.js 청크에서 API 경로 리터럴을 읽는다 — 추측이 아니라 측정).
#
# 실측으로 증명된 패턴(이 레포가 매일 쓰는 주소):
#   stock.naver.com/api/domestic/market/upjong/list      ← 업종(79행, 2026-09-11)
#   stock.naver.com/api/foreign/market/{NAT}/upjong/list  ← CN·HK·JP 업종
#   m.stock.naver.com/front-api/domestic/stock/list       ← 국내 종목 목록
# 옛 HTML 에서 업종·테마는 `sise_group*.naver?type=upjong|theme` 로 **같은 자리**
# 였고, 업종 JSON 행은 실제로 `"type": "upjong"` 을 싣는다 — 형제 자원이 있다는
# 원천 자신의 표시다. 첫 후보는 거기서 나온다(지어낸 호스트가 아니다).
_THEME_API_RUNGS = (
    ("domestic/theme",
     "https://stock.naver.com/api/domestic/market/theme/list"),
    ("domestic/theme(무접미)",
     "https://stock.naver.com/api/domestic/market/theme"),
    ("front-api/theme",
     "https://m.stock.naver.com/front-api/domestic/theme/list"),
)
# ⚠️ **VM 실측 2026-09-12**: 이 API 가족은 `page` 를 **무시하고 `pageSize` 만**
# 듣는다(업종 `?page=1&pageSize=100` → 79행 전부 · `?page=2` → 첫 행 동일).
# 업종은 79개라 100 으로 충분했지만 테마는 266개라 100 에서 **잘린다**. 그래서
# 쪽을 더 받는 게 아니라 **한도를 키워 다시 묻는다** — 어느 크기가 맞는지는
# 추측하지 말고 응답이 답하게 한다(#64 상태는 아는 쪽이 · #136 요구 충족 여부).
_THEME_PAGE_SIZES = (100, 300, 1000)
_THEME_PAGE_SIZE = _THEME_PAGE_SIZES[0]   # 형제·회귀가 참조하는 기본값
_THEME_MAX_PAGES = 5             # `page` 가 듣는 원천을 위한 이어받기 상한
_THEME_CAP_TRIES = 2             # 원천이 말한 상한으로 다시 묻는 횟수 상한(#71)
# 형제 업종 가드와 **같은 규약**(#38): 기본 페이지 수가 그대로 오면 `pageSize`
# 가 안 먹은 것이고, 하한 미만이면 부분이다. 실측 테마 수는 266개.
_THEME_DEFAULT_PAGE = 20         # 이 수가 오면 pageSize 가 안 먹은 것이다
_THEME_MIN_ROWS = 150            # 실측 266 에서 넉넉히 내린 하한
_THEME_MEMO = "theme_endpoint.json"
# 전 후보가 죽은 날의 **짧은 냉각**. 없으면 페이지를 열 때마다 JSON 사다리
# (3후보 × 한도 3)와 죽은 옛 HTML 7쪽을 **전부 다시** 걷는다 — 빈 결과는
# 완전본이 아니라 캐시에 굽지 않으므로(#280) 요청마다 순손실이다(사용자
# 2026-09-12 "테마별 시세 들어가는데 시간이 꽤 걸리는데"). 실패는 **짧게만**
# 믿는다 — 길게 믿으면 원천이 돌아온 뒤에도 빈 화면이 남는다(#152·#161·#303).
_THEME_FAIL_MEMO = "theme_fail.json"
_THEME_FAIL_TTL = 600
_THEME_DISCOVER_URL = "https://finance.naver.com/sise/theme.naver"
_THEME_DISCOVER_COOLDOWN = 6 * 3600


def theme_leaders(raw) -> list:
    """`leadingItem` 문자열 → [{name, code}] 최대 2(순수).

    실측 모양(업종 JSON): `"2,000810,삼성화재|2,005830,DB손해보험"` — `|` 로
    종목을 가르고 `,` 안에 6자리 코드가 들어 있다. 앞 숫자의 뜻은 **재지 않았으
    므로 쓰지 않는다**(#165 안 잰 것을 단정하지 말 것). 코드는 자리로 추정하지
    말고 **모양(6자리)으로 식별**한다(#46).
    """
    out: list = []
    seen: set = set()
    for part in str(raw or "").split("|"):
        bits = [b.strip() for b in part.split(",") if b.strip()]
        code = next((b for b in bits if len(b) == 6 and b.isdigit()), "")
        if not code or code in seen:
            continue
        i = bits.index(code)
        nm = bits[i + 1] if i + 1 < len(bits) else ""
        if not nm:
            continue
        seen.add(code)
        out.append({"name": nm, "code": code})
    return out[:2]


def parse_theme_json(rows: object) -> list:
    """네이버 테마 JSON → [{name, no, pct, pct3, leaders}] (순수).

    `parse_themes_full`(옛 HTML)과 **같은 계약**을 낸다 — 렌더러·저장 스키마를
    안 건드리려면 여기서 모양을 맞춰야 한다(#38 화면이 두 벌이 되면 갈린다).
    등락률을 못 읽는 행은 버리되 호출부가 개수를 대조한다(#54).
    """
    out: list = []
    if not isinstance(rows, list):
        return out
    seen: set = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or r.get("themeName") or "").strip()
        if not name:
            continue
        no = str(r.get("no") or r.get("themeCode") or r.get("code") or "").strip()
        key = no or name
        if key in seen:
            continue
        try:
            pct = round(float(str(r.get("changeRate")).replace(",", "")), 2)
        except (TypeError, ValueError):
            continue
        try:
            pct3 = round(float(str(r.get("recent3daysChangeRate")
                                   ).replace(",", "")), 2)
        except (TypeError, ValueError):
            pct3 = None
        seen.add(key)
        out.append({"name": name, "no": no, "pct": pct, "pct3": pct3,
                    "leaders": theme_leaders(r.get("leadingItem"))})
    return out


def wrong_resource(rows: object) -> str:
    """테마를 물었는데 **업종이 왔나** — 원천이 찍은 `type` 으로 판정(순수).

    같은 API 가족이라 경로 하나가 틀려도 200 + 그럴듯한 행이 온다. 그러면
    업종 79개가 '테마' 라는 이름으로 화면에 앉고, 값이 다 '있어서' 아무 감사도
    안 걸린다(#96). 원천이 스스로 밝히는 칸이 있으면 그걸 읽는다(#86).
    """
    if not isinstance(rows, list) or not rows:
        return ""
    kinds = {str(r.get("type") or "").strip().lower()
             for r in rows if isinstance(r, dict)}
    kinds.discard("")
    if kinds and not ({"theme"} & kinds):
        return f"테마를 물었는데 원천이 type={'/'.join(sorted(kinds))} 를 줬습니다"
    return ""


def _theme_json_rung(url: str) -> tuple:
    """한 후보 주소 → (행, 사유, 부분여부). **한도를 키워 가며** 묻는다.

    VM 실측(2026-09-12): `domestic/theme` 은 살아 있는데 `pageSize=100` 이 정확히
    100행을 주고 `page` 는 무시된다 — 즉 **상한에 닿은 것**이고 더 있다. 그때
    쪽을 더 받아 봐야 같은 목록이므로, 한도를 키워 다시 묻는 것이 답이다.
    상한에 안 닿는 크기를 만나면 그게 전부다(원천이 스스로 답한다, #64).

    마지막 크기에서도 상한에 닿으면 **값은 주되 `partial=True`** — 완전본으로
    굽지 않는다(#280·#343).
    """
    last: tuple = (None, "", False)
    queue = list(_THEME_PAGE_SIZES)
    tried: set = set()
    ok_size = 0                      # 실제로 값을 받아 온 가장 큰 한도
    cap_tries = 0                    # 원천이 말한 상한으로 다시 물은 횟수
    while queue:
        size = queue.pop(0)
        if size in tried:
            continue
        tried.add(size)
        got, why, partial, saturated = _theme_fetch_one_size(url, size)
        if got is None:
            # 원천이 **상한을 스스로 말하면** 그 값으로 한 번 더 묻는다 —
            # 추측해 이분 탐색하면 요청만 늘고, 다음 실행도 같은 자리에서
            # 막힌다(#64 상태는 아는 쪽이 말하게 · #86).
            # ⚠️ 비교 대상은 **성공한 가장 큰 한도**다 — 방금 거절당한 크기와
            # 비교하면(`max(tried)`) 상한이 늘 더 작아 재시도가 한 번도 안
            # 돈다(내 첫 판이 그랬고 테스트가 잡았다, #91b 재는 대상이 맞나).
            # ⚠️ **횟수 상한은 필수**다 — 원천이 거절할 때마다 더 큰 상한을
            # 말하면(우리가 못 재는 원천 버그·정책 변경) 재시도가 끝나지
            # 않는다. 상한 없는 반복은 이 레포에서 프로세스를 멈춰 세운 적이
            # 있다(#71) — 자기 리뷰가 잡았다.
            cap = _nd.size_cap_from(why)
            if (cap and cap not in tried and cap > ok_size
                    and cap_tries < _THEME_CAP_TRIES):
                cap_tries += 1
                queue.insert(0, cap)
                continue
            # ⚠️ 옛 판은 여기서 **무조건** `None` 을 돌려줬다("크기를 바꿔도
            # 같다"). 그 전제는 한도 거절에 성립하지 않는다 — `pageSize=100` 이
            # 100행을 주고 `pageSize=300` 이 거절되면 **이미 받은 100행을
            # 버리고** 그 단이 통째로 실패가 되어, 뒤 후보(추측 주소)의 404 만
            # 남고 화면은 40시간 낡은 스냅샷에 '갱신 실패' 를 적는다(사용자
            # 2026-09-12 캡처). 폴백 조건은 '실패했나' 가 아니라 **'요구를
            # 충족했나'** 다(#136·#345b 부분 성공이 폴백을 죽인다 · #148 없다고
            # 말하기 전에 우리가 버린 건 아닌가).
            # ⚠️ 사유는 **잰 것만** 적는다 — '한도를 거절당했다'로 단정하면
            # 타임아웃·일시정지·0행까지 "원천이 한도를 막는다"로 읽힌다(#165·
            # #82, 독립 리뷰 2026-09-12 실측). 무엇을 물었고 무엇이 왔는지만.
            if last[0]:
                # ⚠️ **직전 한도의 사유가 먼저다.** 처음엔 이 자리에 한도
                # 거절만 적었는데, 그게 "왜 100에서 멈췄나"(쪽이 안 먹는가 ·
                # 2쪽이 거절됐는가)라는 **더 행동 가능한 사실**을 덮었다
                # — 원천이 상한을 말해 준 실행에서도 다음 수를 못 정했다
                # (#275 가장 행동 가능한 것을 머리에 · #292 판정을 세면서
                # 어느 축인지 버리면 요약이 추측을 부른다).
                prior = last[1] or f"{len(last[0])}개까지만 받았습니다"
                # ⚠️ 같은 오류 blob 을 두 번 잇지 말 것 — 사유가 350자가 되어
                # 부제(`via = marks[-1]`)에서 정작 드러내려던 '2쪽에서 실패'가
                # 묻힌다(독립 리뷰 2026-09-12). 상한을 읽었으면 그 **사실만**
                # 짧게 적는다.
                # ⚠️ **'거절' 은 원천이 상한을 말했을 때만** 쓴다 — 타임아웃·
                # 일시정지·0행도 이 자리에 오므로 단정하면 거짓이 된다
                # (#165·#349, 그 계약을 회귀가 못박고 있다).
                _cap = _nd.size_cap_from(why)
                if _cap:
                    tail = f"한도 {size} 거절(원천 상한 {_cap})"
                else:
                    tail = f"한도 {size} 요청도 실패({why})"
                return last[0], f"{prior} · {tail}", True
            return None, why, False
        last = (got, why, partial)
        ok_size = max(ok_size, size)
        if not saturated:
            return last
    got, why, partial = last
    return got, (why or f"한도 {_THEME_PAGE_SIZES[-1]}에서도 가득 찼습니다 "
                        "— 더 있을 수 있습니다"), True


def _theme_fetch_one_size(url: str, page_size: int) -> tuple:
    """한 `pageSize` 로 받는다 → (행, 사유, 부분여부, **상한에 닿았나**).

    `saturated=True` 는 '원천에 그게 전부' 가 아니라 '우리가 상한에서 멈췄다'
    는 뜻이다 — 호출부가 한도를 키워 다시 묻는다(#136 요구를 충족했나).
    """
    got: list = []
    seen: set = set()
    n_first = 0
    n_raw_total = 0
    paging_ok = True
    for page in range(1, _THEME_MAX_PAGES + 1):
        # ⚠️ 1쪽은 **`pageSize` 만** 보낸다. VM 실측 ④ 가 이 가족은 `page` 를
        # 무시한다고 확정했으므로 얹어도 이득이 0인데, 2026-09-12 실측에서 같은
        # 주소가 `page` 를 얹은 요청에 **HTTP 400** 을 줬다(한 시간 전 같은
        # 주소가 100행을 줬다). 이득 없는 파라미터는 거절의 후보일 뿐이므로
        # **증명된 호출 모양**(업종 `?pageSize=100`)을 그대로 쓴다.
        # 2쪽부터는 얹는다 — 이 함수는 탐색이 찾아낸 **모르는 주소**도 검증
        # 하므로(`_discover_theme_endpoint`) `page` 가 듣는 원천이면 그때
        # 이어받는다. 리서치 형제는 여기를 안 쓴다(자기 페이징이 따로 있고
        # 거기도 1쪽은 `page` 없이 보낸다) — 귀속을 틀리게 적으면 다음 사람이
        # "리서치가 깨진다"는 헛걱정을 한다(#55, 독립 리뷰 2026-09-12 L2).
        params = {"pageSize": page_size}
        if page > 1:
            params["page"] = page
        raw, why = _get2_json(url, params=params)
        if raw is None:
            # ⚠️ **1쪽이 상한까지 찼는데 2쪽을 못 받았으면 포화다.** 그건 '목록
            # 끝'이 아니라 더 있는데 못 받은 것이므로, 여기서 `saturated=False`
            # 로 끝내면 한도 사다리(100→300→1000)가 멈춘다 — 이 변경이 스스로
            # 세운 가설(같은 주소가 `page` 를 얹은 요청에 400 을 준다)이 참일
            # 때 정확히 그렇게 되어, 266개 중 100개를 '전체 테마'로 그리고
            # 캐시도 거부해 **클릭마다 재수집**한다(독립 리뷰 2026-09-12 H1
            # 실측 · #136 요구를 충족했나 · #45 모집단).
            # 상한 판정은 파싱 뒤 행 수가 아니라 **원천이 준 원시 수**로 —
            # 못 읽은 행 하나가 경고를 끄면 안 된다(#342).
            # ⚠️ **몇 쪽에서 막혔는지 말한다** — 1쪽 실패와 2쪽 실패는 처방이
            # 다르다(주소·한도 vs 페이지네이션 규약). 사유가 그걸 안 적으면
            # 다음 실행도 같은 자리를 추측한다(#82·#275).
            _pw = f"{page}쪽({page_size}개 단위)에서 실패: {why}"
            return ((got, _pw, True, n_first >= page_size) if got
                    else (None, _pw, False, False))
        if not isinstance(raw, list):
            # dict 로 감싸 오는 가족도 있다 — 목록 자리를 찾아본다.
            inner = None
            if isinstance(raw, dict):
                for k in ("result", "datas", "list", "items", "stocks"):
                    if isinstance(raw.get(k), list):
                        inner = raw[k]
                        break
            if inner is None:
                _w = _nd.shape_reason("테마 목록", raw)
                return ((got, _w, True, False) if got
                        else (None, _w, False, False))
            raw = inner
        bad = wrong_resource(raw)
        if bad:
            return None, bad, False, False
        rows = parse_theme_json(raw)
        n_raw_total += len(raw)
        if page == 1:
            n_first = len(raw)
        fresh = [t for t in rows if (t["no"] or t["name"]) not in seen]
        for t in fresh:
            seen.add(t["no"] or t["name"])
        got.extend(fresh)
        if not fresh and page > 1 and len(raw) >= page_size:
            # 가득 찬 쪽을 받았는데 **새 행이 하나도 없다** = `page` 가 안 먹은
            # 것이다(원천이 매 쪽 같은 목록을 준다). '목록 끝' 이 아니라 **더
            # 있는데 못 받은 것**이므로 완전본으로 굽으면 안 된다(#280·#341).
            paging_ok = False
            break
        if not fresh or len(raw) < page_size:
            break
    else:
        # 쪽 상한까지 다 돌았는데도 매 쪽이 가득 찼다 = 더 있는데 멈춘 것(#343).
        return got, (f"쪽 상한({_THEME_MAX_PAGES})에서 멈췄습니다 "
                     "— 더 있을 수 있습니다"), True, True
    if not got:
        return None, _nd.parse_reason("테마 행", 0, unit="행"), False, False
    # ⚠️ 여기까지 왔다고 완전본이 아니다 — **행 수를 하한과 대조**한다(#54 대조
    # 없이 통과시키지 말 것 · 형제 `fetch_sector_movers` 와 같은 규약 #38).
    # 독립 리뷰 2026-09-12 실측: 원천에 266개가 있는데 `page` 를 무시하면 100개,
    # `pageSize` 를 무시하면 20개만 받고 `partial=False` 로 캐시에 구웠다. 그러면
    # 화면이 '전체 테마 100개' 라고 적고 등락률 상·하위 순위가 조용히 틀린다.
    if not paging_ok:
        # `page` 가 안 먹는다(실측된 이 API 가족의 거동) — 한도를 키워 다시
        # 묻는 것이 답이므로 **saturated** 로 돌려준다.
        return got, (f"원천이 쪽(page)을 무시해 한도 {page_size}에서 "
                     "멈췄습니다"), True, True
    if n_first == _THEME_DEFAULT_PAGE and len(got) <= _THEME_DEFAULT_PAGE:
        # 한도를 키워도 기본 20 만 온다 = `pageSize` 자체가 안 먹는 것이다.
        return got, (f"원천이 pageSize 를 무시해 기본 {_THEME_DEFAULT_PAGE}개만 "
                     "줬습니다 — 전체가 아닙니다"), True, False
    if len(got) < _THEME_MIN_ROWS:
        # ⚠️ 하한은 **한 번의 실측(266개)에서 온 휴리스틱**이다. 절단은 이제
        # 구조로 잡히므로(상한 도달 = `saturated`, `pageSize` 무시) 이 가드까지
        # 캐시를 막으면, 원천이 정당하게 150개 미만이 되는 날 **영원히 부분**이
        # 되어 요청마다 재수집한다(#171 가드가 '못 만든다' 로 끝나면 그 자리가
        # 영원히 빈다 · #146 증상이 아니라 원인으로 막을 것). 사실은 그대로
        # 말하되(#41·#43) 캐시는 막지 않는다.
        return got, (f"{len(got)}개 — 기대 하한 {_THEME_MIN_ROWS}개 미만입니다"
                     "(원천이 줄었거나 일부만 왔을 수 있습니다)"), False, False
    # `parse_theme_json` 독스트링이 "호출부가 개수를 대조한다" 고 약속했는데
    # 아무도 안 했다(독립 리뷰 2026-09-12 Low · #54·#55). 등락률을 못 읽어
    # 버린 행이 많으면 값은 '있어도' 순위가 틀린다 — 사실을 말한다.
    dropped = n_raw_total - len(got)
    if dropped > 0 and dropped * 10 >= n_raw_total:
        # 같은 이유로 캐시는 막지 않는다 — 값은 있고, 사실을 말한다(#43).
        return got, (f"원천 {n_raw_total}행 중 {dropped}행을 못 읽어 버렸습니다"
                     " — 등락률 형식이 바뀌었을 수 있습니다"), False, False
    return got, "", False, False


_THEME_DISCOVER_TRIES = 6        # 검증 호출 상한 — 리터럴로 못박는다(#66)


def theme_candidates(paths: list) -> list:
    """발굴한 경로 목록 → **실호출할 URL 후보**(순수).

    호스트를 하나로 정하지 않고 검증된 두 호스트를 모두 낸다 — 어느 쪽이
    맞는지는 응답이 답한다(#25 능력은 이름이 아니라 실측). 템플릿 구멍이 있는
    경로는 우리가 채우지 않는다(#165). `list` 로 끝나는 목록형을 먼저 본다.
    """
    out: list = []
    seen: set = set()
    for p in sorted(paths or [], key=lambda x: (0 if x.rstrip("/").endswith("list")
                                                else 1, x)):
        if "{" in p or "$" in p:
            continue
        path = p if p.startswith("/") else "/" + p
        for host in ("https://stock.naver.com", "https://m.stock.naver.com"):
            u = host + path
            if u not in seen:
                seen.add(u)
                out.append(u)
    return out


def theme_memo_url() -> str:
    """`naver_spa_discover` 가 찾아 둔 주소(있으면). 없으면 빈 문자열."""
    memo, _ = _cache_read_any(_THEME_MEMO)
    return str((memo or {}).get("url") or "")


def _discover_theme_endpoint() -> None:
    """전 후보가 죽었을 때 **원천에게 묻는다** — 배경에서 한 번(#116 예산).

    여기서 화면을 기다리게 하지 않는다. 찾으면 메모에 적고 **다음 수집**이
    1순위로 쓴다. 못 찾아도 메모에 시각을 남겨 냉각한다(같은 실패를 6시간마다
    한 번만 재시도 — 매 요청마다 청크 수 MB 를 받으면 안 된다).
    """
    from bot import naver_spa_discover as _disc

    def _fetch(u: str) -> tuple:
        try:
            resp = requests.get(u, headers=_HEADERS, timeout=8)
            if resp.status_code != 200:
                return None, _nd.http_reason(resp.status_code,
                                             len(resp.content or b""))
            # ⚠️ `_get2` 는 euc-kr 를 강제한다(옛 HTML 용) — SPA·JS 는 UTF-8 이라
            # 그걸 그대로 쓰면 본문이 깨진다. 여기서 필요한 건 ASCII 주소뿐이지만
            # 깨진 본문에선 정규식도 어긋난다(#155 원천이 실제로 보내는 모양).
            return resp.text, ""
        except Exception as exc:                               # noqa: BLE001
            return None, _nd.http_reason(None, exc=exc)

    paths, why = _disc.discover(_THEME_DISCOVER_URL, must_contain="theme",
                               fetch=_fetch, prefer="theme")
    # ⚠️ **호스트를 추측하지 않는다**(독립 리뷰 2026-09-12). 옛 판은
    # `p.startswith("/api/")` 면 stock, 아니면 m.stock 으로 정했는데 minified
    # 번들은 슬래시 없는 `"api/domestic/market/theme/list"` 를 흔히 낸다 —
    # 그러면 **검증된 호스트가 있는 경로가 엉뚱한 호스트로** 조립되고, 그걸
    # `✅ 탐색됨` 으로 6시간 못박는다('찾음' 을 '동작함' 으로 렌더 · #165·#25).
    # 대신 후보 × 두 호스트를 **실제로 호출해** 테마 행이 오는 것만 채택한다.
    cands = theme_candidates(paths)
    url, tried = "", []
    for cand in cands[:_THEME_DISCOVER_TRIES]:
        rows, rwhy, _partial = _theme_json_rung(cand)
        tried.append(f"{cand} — {'✅ %d개' % len(rows) if rows else (rwhy or '0건')}")
        if rows:
            url = cand
            break
    _cache_write(_THEME_MEMO, {"url": url, "found": paths[:8], "tried": tried,
                               "why": why, "at": time.time()})
    log.warning("naver_sector: 테마 엔드포인트 탐색 — 검증됨=%s · 후보=%s · %s",
                url or "없음", paths[:4], why or "")


def _maybe_discover_theme() -> None:
    """냉각을 지키며 배경 탐색을 건다 — 같은 키는 한 번만(#113).

    ⚠️ **일시정지 중에는 탐색하지 않는다**(자기검토 2026-09-12). 정지 마커가
    걸리면 사다리 전 단이 `PAUSED` 로 실패하는데 그건 "엔드포인트가 죽었다"는
    증거가 아니라 **우리가 안 물어본 것**이다(#79 그 경로가 실제로 실행됐나 ·
    #143 대조군 없이 '없음'을 말하지 말 것). 그대로 두면 (a) 정지시켜 놓은
    호스트를 탐색이 raw `requests` 로 두드리고 (b) 못 찾았다는 메모가 6시간
    냉각을 걸어 **정지가 풀린 뒤에도 진짜 탐색이 6시간 막힌다**.
    """
    try:
        from bot.finviz_client import naver_paused
        if naver_paused():
            return
    except Exception:                                          # noqa: BLE001
        pass
    # ⚠️ `_cache_read_any` 는 mtime 이 아니라 **나이(초)** 를 돌려준다 — 시각으로
    # 읽으면 `time.time() - 나이` 가 늘 거대해져 냉각이 한 번도 안 걸린다
    # (가드가 재는 대상을 틀리면 그냥 눈이 먼다, #91b).
    _memo, age = _cache_read_any(_THEME_MEMO)
    if age is not None and age < _THEME_DISCOVER_COOLDOWN:
        return
    with _BG_LOCK:
        if _THEME_MEMO in _BG_KEYS:
            return
        _BG_KEYS.add(_THEME_MEMO)

    def _run() -> None:
        try:
            _discover_theme_endpoint()
        except Exception as exc:                               # noqa: BLE001
            log.warning("naver_sector: 테마 엔드포인트 탐색 실패: %s", exc)
        finally:
            with _BG_LOCK:
                _BG_KEYS.discard(_THEME_MEMO)

    try:
        threading.Thread(target=_run, daemon=True,
                         name="naver-theme-discover").start()
    except Exception as exc:                                   # noqa: BLE001
        with _BG_LOCK:
            _BG_KEYS.discard(_THEME_MEMO)
        log.warning("naver_sector: 탐색 스레드 시작 실패: %s", exc)


def collect_themes_json() -> tuple:
    """후보 사다리를 **순서대로 실호출** → (행, 단 표시, 사유, 부분여부).

    단 표시는 화면·`--check` 가 **어느 후보가 답했는지** 그대로 적기 위한 것이다
    — 폴백을 로그로만 알리면 사용자는 영영 모른다(#42a·#136).
    """
    marks: list = []
    fails: list = []
    memo = theme_memo_url()
    rungs = ([("탐색됨", memo)] if memo else []) + list(_THEME_API_RUNGS)
    for label, url in rungs:
        _t = time.time()
        rows, why, partial = _theme_json_rung(url)
        # 재지 않으면 '느리다'는 진단이 아니다(#69·#110) — 단마다 소요를 싣는다.
        _dt = f" · {time.time() - _t:.1f}s"
        if rows:
            marks.append(f"{label} {'⚠️' if (partial or why) else '✅'} "
                         f"{len(rows)}개" + (f" · {why}" if why else "") + _dt)
            # ⚠️ 사유는 **부분일 때만** 싣지 않는다 — 캐시를 막지는 않지만
            # 화면이 말해야 하는 사실(하한 미만·버려진 행)이 있다(#43·#171).
            return rows, marks, why, partial
        why = why or "0건"
        marks.append(f"{label} ❌ {why}{_dt}")
        fails.append((_nd.reason_rank(why), len(fails), label, why))
    return [], marks, pick_theme_reason(fails), False


def pick_theme_reason(fails: list) -> str:
    """(순위, 차례, 라벨, 사유) 목록 → **가장 행동 가능한** 사유 한 줄(순수).

    옛 판은 `marks[-1]` 을 썼다 — 즉 **마지막 후보**의 사유다. 2026-09-12 실측
    에서 1순위(증명된 호스트)가 `HTTP 400`, 2·3순위(추측 후보)가 `HTTP 404`
    였는데 화면은 `⚠️ 원천이 HTTP 404` 라고 적어, 주소가 사라진 것처럼 읽혔다.
    같은 순위면 **앞 후보**가 이긴다 — 사다리 순서가 곧 증명된 순서다(#275·#82).
    """
    if not fails:
        return ""
    _rank, _order, label, why = sorted(fails, key=lambda f: (f[0], f[1]))[0]
    return f"{why} ({label})" if len(fails) > 1 else why


def _theme_page(page: int):
    """테마 한 페이지 → (행 목록, 실패 사유). **수신 실패는 None**(빈 `[]` 와 다르다).

    옛 직렬 판은 첫 실패에서 `break` 해 부분 결과를 만들지 않았다. 병렬로
    바꾸면 4쪽만 429 를 맞아도 나머지가 그대로 합쳐져 **~170개를 완전본으로**
    굽는다(독립 리뷰) — 갈래를 남겨 호출부가 캐시 여부를 정한다(#82·#119).
    """
    # ⚠️ `_get` 은 사유를 버린다 — 도달 실패(403·타임아웃·NAVER_PAUSE)와
    # 'SPA 로 표가 사라짐' 이 화면에서 같은 말이 된다(처방이 정반대인데, #82).
    # `_get2` 로 받아 갈래를 위로 올린다.
    html, why = _get2(f"{_BASE}/theme.naver", params={"page": page})
    if not html:
        return None, why or _nd.parse_reason("테마 페이지", 0, unit="B")
    rows = parse_themes_full(html)
    if not rows:
        # 응답은 왔는데 행이 0 = **표가 사라진 것**(SPA 전환). 도달 실패와
        # 다른 이름으로 말해야 운영자가 엔드포인트 교체로 간다.
        return [], _nd.parse_reason("테마 행", len(html), unit="B")
    return rows, ""


def theme_fail_memo() -> tuple:
    """직전 **전멸** 기록 → (사유, 단 표시, 남은 냉각초). 없거나 식었으면 ("", [], 0)."""
    memo, age = _cache_read_any(_THEME_FAIL_MEMO)
    if not isinstance(memo, dict) or age is None or age >= _THEME_FAIL_TTL:
        return "", [], 0
    return (str(memo.get("reason") or ""), list(memo.get("rungs") or []),
            int(_THEME_FAIL_TTL - age))


def _note_theme_fail(reason: str, marks: list) -> None:
    """전멸을 기록한다 — 단 **'안 물어본 것'은 기록하지 않는다**.

    일시정지(`/naverpause`)는 원천이 죽은 게 아니라 우리가 안 물어본 것이다
    (#79·#143·#345). 그걸 냉각으로 기억하면 정지를 푼 뒤에도 10분을 더 빈
    화면으로 산다.
    """
    if _nd.reason_rank(reason) == 0:
        return
    _cache_write(_THEME_FAIL_MEMO, {"reason": reason, "rungs": list(marks or [])})


def _clear_theme_fail() -> None:
    """식은 뒤 성공하면 죽은 기록 파일을 치운다.

    ⚠️ 옛 독스트링은 "남겨 두면 다음 실패의 나이가 옛 기록에서 계산돼 냉각이
    짧아진다" 고 적었는데 **성립하지 않는다**(독립 리뷰 2026-09-12 L1 실측):
    `_note_theme_fail` 은 `_cache_write`(tmp + `os.replace`)라 재기록마다
    mtime 이 새로 잡히고, 냉각이 살아 있으면 `_collect_and_store` 가 조기
    반환하므로 '살아 있는 메모 + 성공' 자체가 제품 경로에 없다. 정리 목적만
    남긴다 — 가드가 재는 범위를 넘는 주장을 독스트링에 적지 말 것(#55·#286).
    """
    try:
        (_CACHE_DIR / _THEME_FAIL_MEMO).unlink()
    except Exception:                                          # noqa: BLE001
        pass


def collect_themes() -> dict:
    """테마 목록 — **JSON 사다리 먼저**, 그다음 옛 HTML(원천이 되돌릴 경우).

    2026-09-12 SPA 전환으로 HTML 표가 사라졌다. 폴백은 지우지 않는다 — 이
    레포에서 폴백 사다리는 버그의 원인이 아니라 fix 였다(§작업 원칙 · #122·
    #136·#191). 대신 **어느 단이 답했는지 화면이 말한다**(#42a 폴백은 버그를
    숨긴다 · #136 payload 가 밝힌 원천을 화면이 따른다).

    ⚠️ 옛 HTML 판은 1→7 페이지를 병렬로 받았다 — 그 구조는 그대로 두고
    JSON 이 실패했을 때만 탄다.
    """
    from bot.pool import map_bounded

    t0 = time.time()
    rows, marks, why_json, partial_json = collect_themes_json()
    if rows:
        rows.sort(key=lambda x: (x.get("pct") is None, -(x.get("pct") or 0)))
        log.info("naver_sector: themes %d개 · %s · %.2fs",
                 len(rows), marks[-1], time.time() - t0)
        # ⚠️ 부분이면 값은 주되 **캐시하지 않는다**(`_collect_and_store` 가
        # `partial` 을 보고 거부한다, #280).
        return {"themes": rows, "ts": _now_kst_label(), "partial": partial_json,
                "reason": why_json, "rungs": marks, "via": marks[-1]}

    # 전 후보가 죽었다 — **원천에게 물어본다**(배경, 냉각 6시간). 이번 응답을
    # 기다리게 하지 않는다(#116 본 응답 경로엔 예산).
    _maybe_discover_theme()

    _res = map_bounded(_theme_page, list(range(1, _THEME_PAGES + 1)))
    pages = [(r[0] if isinstance(r, tuple) else r) for r in _res]
    whys = [str(r[1]) for r in _res if isinstance(r, tuple) and r[1]]
    failed = [i for i, rws in enumerate(pages, 1) if rws is None]
    themes: list[dict] = []
    seen: set[str] = set()
    for rws in pages or []:
        for t in rws or []:
            if t["no"] in seen:
                continue
            seen.add(t["no"])
            themes.append(t)
    themes.sort(key=lambda x: (x.get("pct") is None, -(x.get("pct") or 0)))
    marks.append(f"옛 HTML {'✅ %d개' % len(themes) if themes else '❌ ' + (whys[0] if whys else '0건')}")
    # 재지 않으면 '느리다'는 진단이 아니다(#69·#110) — 실측을 로그에 남긴다.
    log.info("naver_sector: themes %d개 · %d페이지(실패 %s) · %.2fs · 단 %s",
             len(themes), _THEME_PAGES, failed or "없음", time.time() - t0, marks)
    if failed:
        log.warning("naver_sector: 테마 페이지 수신 실패 %s — 부분 스냅샷", failed)
    # 사유는 **계산해 놓고 버리면 없는 것과 같다**(#123·#129·#189·#228 계열).
    return {"themes": themes, "ts": _now_kst_label() if themes else "",
            "partial": bool(failed),
            "rungs": marks,
            "via": marks[-1] if themes else "",
            "reason": ("" if themes else (why_json or (whys[0] if whys else "")))}


def _collect_and_store() -> dict:
    """수집 → **완전한 결과만** 저장.

    ⚠️ 옛 판은 결과를 무조건 캐시했다. 전 페이지가 실패하면 `{"themes": []}`
    가 저장되고, `_session_fresh` 는 장 마감 뒤 스냅샷을 무조건 fresh 로 보므로
    **다음 개장까지 빈 화면이 재시도 없이** 서빙된다(독립 리뷰 실측). 부분·빈
    결과는 굽지 않는다(#119 예외로 끝난 실행은 캐시하지 말 것).
    """
    why_cool, marks_cool, cool = theme_fail_memo()
    if cool:
        # ⚠️ 냉각은 **제품 경로에만** 건다 — `--check`·프로브는 `collect_themes`
        # 를 직접 부르므로 재는 일이 막히지 않는다(#35 의 경계, #345c).
        log.info("naver_sector: 테마 냉각 중(%d초 남음) — 재수집 생략 · %s",
                 cool, why_cool)
        return {"themes": [], "ts": "", "partial": False, "reason": why_cool,
                "rungs": list(marks_cool) + [f"냉각 {cool}초 — 직전 전멸 기록"],
                "via": "", "cooldown": cool}
    out = collect_themes()
    if out.get("themes") and not out.get("partial"):
        _cache_write("theme.json", out)
        _clear_theme_fail()
    else:
        log.warning("naver_sector: 테마 수집 불완전(themes=%d partial=%s) — "
                    "캐시를 덮지 않는다",
                    len(out.get("themes") or []), out.get("partial"))
        if not out.get("themes"):
            # 전멸했다 — 짧게 기억해 다음 클릭이 같은 순손실을 되풀이하지
            # 않게 한다(사다리 3후보 × 한도 3 + 죽은 옛 HTML 7쪽).
            # ⚠️ **부분은 기록하지 않는다.** 한 번 기록해 봤다가 독립 리뷰
            # 실측이 새 실패모드 둘을 잡았다(2026-09-12): (a) 저장분이 있으면
            # 화면이 `100개(오늘)` ↔ `266개(어제)` 를 10분 주기로 오간다
            # (#45 모집단이 새로고침마다 바뀐다) (b) 저장분이 없으면 캐시도
            # 냉각도 안 생겨 **수렴 지점이 없다**(#171). 그리고 부분은 값을
            # 돌려주므로 사다리가 **1단에서 끝나고** 옛 HTML 7쪽을 아예 안
            # 걷는다 — 전멸만큼 비싸지 않다(#61 비용의 어느 단계인가).
            _note_theme_fail(str(out.get("reason") or ""),
                             list(out.get("rungs") or []))
    return out


def fetch_themes() -> dict:
    """테마별 시세 → {'themes': [...] 등락률 내림차순, 'ts'}. 장중 30초 캐시.

    캐시가 낡았어도 `_THEME_SWR_SEC` 안이면 **그걸 즉시 주고** 뒤에서
    갱신한다(`stale`·`stale_age` — 화면이 얼마나 낡았는지 말한다, #43·#163).
    """
    c = _cached("theme.json")
    if c is not None:
        return c
    old, mt = _read_cache("theme.json")
    have_old = bool(old and old.get("themes"))
    age = None if mt is None else time.time() - mt
    why_cool, _mk, cool = theme_fail_memo()
    if cool and have_old:
        # 직전에 전 후보가 죽었고 아직 냉각 중이다 — 배경 갱신을 띄워 봐야
        # 즉시 빈손으로 끝난다. '갱신 중' 은 **진짜 진행 중일 때만** 적는다
        # (#25 늘 뜨는 배지는 아무것도 안 재는 것과 같다 · #345).
        return dict(old, stale=True, stale_age=int(age or 0), refreshing=False,
                    reason=why_cool, cooldown=cool)
    if have_old and age is not None and age < _THEME_SWR_SEC:
        _refresh_async("theme.json", _collect_and_store, "naver:themes")
        # 여기만 **실제로 배경 갱신이 떠 있다** — 화면이 '갱신 중' 이라고 말할
        # 자격이 있는 유일한 갈래다.
        return dict(old, stale=True, stale_age=int(age), refreshing=True)
    from bot.singleflight import once
    out = once("naver:themes", _collect_and_store)
    if not out.get("themes") and have_old:
        # 수집이 **이미 끝나고 빈손**이다 — 옛 판은 이 경우에도 화면이
        # '갱신 중' 이라고 적어, 기다리면 채워질 것처럼 읽혔다(사용자 2026-09-12
        # "어제기준인데?" · #25 늘 뜨는 배지는 아무것도 안 재는 것과 같다).
        # 진행 중인 갱신은 없다 — 사실대로 '수집 실패' 라고 말한다.
        return dict(old, stale=True, stale_age=int(age or 0), refreshing=False,
                    reason=out.get("reason") or "")
    return out


# ── 상한가·하한가 (신고가/신저가 페이지가 불안정해 대체 — 사용자 2026-06-10) ──
# Naver: sise_upper.naver(상한가) / sise_lower.naver(하한가) — 표준 시세 페이지.
def _parse_stock_rows(html: str, limit: int) -> list[dict]:
    """종목 표(상한/하한/등락 공통) → [{code, name, price, pct}]."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I):
        a = _ITEM_RE.search(row)
        if not a:
            continue
        code, name = a.group(1), _clean(a.group(2))
        if code in seen:
            continue
        cells = _cell_texts(row)
        price = ""
        for cterm in cells:
            if re.fullmatch(r"[\d,]{3,}", cterm):
                price = cterm
                break
        pct = _pct_from_row(row)
        # 거래량(사용자 2026-06-13) = 등락률(%) 셀 다음의 콤마 정수 (Naver
        # sise_upper 컬럼순서: 현재가·전일비·등락률·거래량·시가·고가·저가).
        # %가 셀 텍스트에 없을 수 있어 graceful None (못 찾으면 '—').
        vol = None
        after_pct = False
        for cterm in cells:
            if "%" in cterm:
                after_pct = True
                continue
            if after_pct and re.fullmatch(r"[\d,]{4,}", cterm):
                try:
                    vol = int(cterm.replace(",", ""))
                except ValueError:
                    vol = None
                break
        seen.add(code)
        out.append({"code": code, "name": name, "price": price,
                    "pct": round(pct, 2) if pct is not None else None,
                    "vol": vol})
        if len(out) >= limit:
            break
    return out


def fetch_upper_lower(limit: int = 50) -> dict:
    """상한가·하한가 → {'upper': [...], 'lower': [...], 'ts'}. 장중 30초 캐시. graceful."""
    c = _cached("upper_lower.json")
    if c is not None:
        return c
    out = {"upper": [], "lower": [], "ts": ""}
    for key, page in (("upper", "sise_upper.naver"), ("lower", "sise_lower.naver")):
        html = _get(f"{_BASE}/{page}")
        out[key] = _parse_stock_rows(html, limit) if html else []
    if out["upper"] or out["lower"]:
        out["ts"] = _now_kst_label()
    _cache_write("upper_lower.json", out)
    return out


# 원문 표본에 섞여 나올 수 있는 비밀값을 **모양으로** 가린다(이름 열거 금지 #24).
# 값은 지우되 키 이름은 남긴다 — 어느 파라미터가 왔는지는 진단에 필요하다(#82).
import re as _re

# 마스킹 규약은 공용 헬퍼가 단일 출처다(#38) — 복제하면 한쪽만 고쳐진다.
_SECRET_IN_MARKUP = _nd.SECRET_IN_MARKUP


def _mask_secrets(text: str) -> str:
    return _nd.mask_secrets(text)


def markup_sample(html: str | None, anchors: tuple, width: int = 220,
                  max_lines: int = 8) -> list[str]:
    """0건일 때 **원문 표본**을 사람이 읽을 수 있게 몇 줄로(순수).

    "구조 변경 의심" 까지만 말하면 다음 라운드가 추측으로 시작한다 — 원문을 안
    찍었기 때문에 세 라운드를 쓴 적이 있다(#109·#54·#155). 앵커별로 '있나/몇 건'
    을 세고, 첫 출현 주변을 잘라 보여준다.

    ⚠️ 비밀값은 **함수가** 가린다 — "공개 페이지에만 쓴다" 는 규율이었고 이
    레포에서 규율은 매번 진다(#119). 원문에 섞인 키·토큰 모양을 마스킹한다.
    """
    if not html:
        return ["원문 없음 — 도달 실패"]
    counts = []
    for a in anchors:
        n = html.count(a)
        counts.append(f"   · `{a}` {n}건" + ("" if n else "  ← 사라짐"))
    hit = next((a for a in anchors if a in html), None)
    if hit:
        i = html.index(hit)
        seg = _mask_secrets(" ".join(html[max(0, i - width // 2): i + width].split()))
        sample = f"   ↪ `{hit}` 주변: {seg}"
    else:
        sample = ("   ↪ 앵커가 하나도 없다 — 머리 320자: "
                  + _mask_secrets(" ".join(html[:320].split())))
    # ⚠️ 자르는 건 **계수 줄**이다. 예전엔 `out[:max_lines+2]` 라 앵커가 9개
    # 이상이면 `↪ 표본` 이 잘렸다 — 이 함수가 존재하는 이유가 그 줄인데(#109
    # 원문 표본을 같이 찍을 것) 호출부가 이미 갖고 있는 계수만 남았다.
    keep = max(1, max_lines)
    head = [f"원문 {len(html):,}자 · 표본:"]
    if len(counts) > keep:
        shown, hidden = counts[:keep], len(counts) - keep
        shown.append(f"   · … 외 {hidden}종 생략")     # 자른 사실을 말한다(#45)
        counts = shown
    return head + counts + [sample]


def json_sample(raw: object, max_keys: int = 14) -> list[str]:
    """JSON 0건일 때 **원문 표본** — `markup_sample` 의 JSON 판(#109).

    "구조 변경 의심" 까지만 말하면 다음 라운드가 추측으로 시작한다. 키 이름을
    **자르지 않고** 보여줄 것 — 내 옛 프로브가 키를 잘라 매핑 근거가 '외 2종'
    안에 숨는 바람에 라운드를 하나 더 썼다(#156).
    """
    if raw is None:
        return ["원문 없음 — 도달 실패"]
    if isinstance(raw, list):
        if not raw:
            return ["원문: 빈 목록([]) — 도달·파싱은 됐고 행이 0건"]
        first = raw[0]
        if not isinstance(first, dict):
            return [f"원문 {len(raw)}행 · 첫 원소가 dict 가 아님({type(first).__name__}): "
                    + _mask_secrets(repr(first)[:220])]
        keys = list(first)
        shown = keys[:max_keys]
        line = f"원문 {len(raw)}행 · 첫 행 키 {len(keys)}종: " + ", ".join(shown)
        if len(keys) > len(shown):
            line += f" … 외 {len(keys) - len(shown)}종"   # 자른 사실을 말한다(#45)
        return [line, "   ↪ 첫 행: " + _mask_secrets(repr(first)[:320])]
    return [f"원문이 목록이 아님({type(raw).__name__}): "
            + _mask_secrets(repr(raw)[:320])]


def check(fetch: bool = False) -> int:
    """`--check` — 업종별 시세(전체)가 왜 그 속도인지 **갈래로** 말한다.

    ⚠️ 로그 한 줄(`naver_sector: themes …`)은 **수집이 실제로 돌 때만** 찍힌다.
    장 마감 뒤엔 `_session_fresh` 가 마지막 마감 이후 스냅샷을 fresh 로 보므로
    수집이 아예 안 돌고, 그러면 `journalctl | grep` 이 **비어 있는 게 정상**
    이다 — 그 침묵은 '배포 안 됨'·'재시작 안 됨'·'페이지를 안 눌렀음'과
    구별되지 않는다(#274 이상 없음도 말해야 한다 · #82 갈래로 말하라).
    읽기 전용이며, `--fetch` 를 줘야 실제 수집을 1회 재 본다(#264 진단이
    운영 상태를 바꾸지 않게 기본은 조회만).
    """
    import sys
    from datetime import datetime, timedelta, timezone

    print(f"[naver_sector --check v{_CHECK_VER}]")
    print(f"① 인터프리터: {sys.executable}")
    rc = 0
    f = _CACHE_DIR / "theme.json"
    if not f.exists():
        print(f"② 캐시 없음: {f}")
        print("   ↪ 다음 클릭이 전 페이지를 수집한다(그때 로그가 찍힌다)")
    else:
        mt = f.stat().st_mtime
        age = time.time() - mt
        obj, _ = _read_cache("theme.json")
        n = len((obj or {}).get("themes") or [])
        when = datetime.fromtimestamp(mt, timezone(timedelta(hours=9)))
        ago = (f"{age / 60:.1f}분 전" if age < 5400
               else f"{age / 3600:.1f}시간 전")
        print(f"② 캐시: 테마 {n}개 · {when:%Y-%m-%d %H:%M} KST ({ago})")
        # 판정은 화면이 쓰는 그 술어·그 조건으로(#35) — 다시 계산하면 갈라진다.
        # `have_old` 는 `fetch_themes` 와 같은 뜻이어야 한다: 이게 거짓이면
        # SWR 창 안이어도 **동기 수집**을 기다린다(독립 리뷰 실측).
        fresh = _cached("theme.json") is not None
        have_old = bool(obj and obj.get("themes"))
        if fresh and not have_old:
            # 빈 스냅샷이 fresh 로 굳으면 다음 개장까지 빈 화면이 재시도 없이
            # 서빙된다 — 대조 0건은 통과가 아니다(#54·#119).
            print("   ❌ 테마 0개인데 신선 판정 — 빈 스냅샷이 굳었다. "
                  "`--check --fetch` 로 원천을 확인할 것")
            rc = 1
        elif fresh:
            print("   ✅ 신선 판정 — 클릭은 캐시 히트(수집 안 돎 = 로그도 안 찍힘)")
        elif have_old and age < _THEME_SWR_SEC:
            print(f"   ⏳ 낡음이지만 SWR 창({_THEME_SWR_SEC // 60}분) 안 — "
                  "즉시 응답 + 배경 갱신(로그는 배경에서 찍힌다)")
        elif have_old:
            print("   ⚠️ SWR 창 밖 — 다음 클릭이 수집을 기다린다(로그가 찍힌다)")
        else:
            print("   ⚠️ 쓸 수 있는 저장분이 없다 — 다음 클릭이 동기 수집을 "
                  "기다린다(로그가 찍힌다)")
        if (obj or {}).get("partial"):
            # 부분 스냅샷이 디스크에 있으면 화면이 그걸 완전본처럼 그린다(#280).
            print("   ⚠️ 저장분이 partial=True — 일부 페이지가 빠진 스냅샷이다")
        # 어느 **단**이 답했나 — 폴백을 로그로만 알리면 사용자는 영영 모른다
        # (#42a·#136 payload 가 밝힌 원천을 화면이 따른다).
        if (obj or {}).get("via"):
            print(f"   ↪ 원천: {obj['via']}")
        for _m in (obj or {}).get("rungs") or []:
            print(f"      · {_m}")
    # ── ②-e 전멸 냉각 — 그동안 **제품 경로는 수집을 건너뛴다**. 안 적으면
    # 아래 ③ 실측(냉각을 우회해 직접 잰다)과 화면이 왜 다른지 운영자가
    # 짐작하게 된다(#82·#35 감사는 화면이 쓰는 경로를 알아야 한다).
    _why_cool, _marks_cool, _cool = theme_fail_memo()
    if _cool:
        print(f"②-e 테마 냉각 {_cool}초 남음 — 그동안 클릭은 재수집하지 않는다 "
              f"(직전 사유: {_why_cool or '미기록'})")
        for _m in _marks_cool:
            print(f"      · {_m}")
    else:
        print("②-e 테마 냉각: 없음(직전 수집이 전멸하지 않았거나 이미 식었다)")
    # ── ②-d 테마 엔드포인트 탐색 메모 — 사다리가 전멸했을 때 배경이 원천의
    # Next.js 청크에서 읽어 둔 것(#151 추측 금지: 이름을 짓지 않고 잰다).
    _memo, _memo_age = _cache_read_any(_THEME_MEMO)
    if _memo:
        _u = str(_memo.get("url") or "")
        # '찾음' 과 '동작함' 은 다르다 — 메모에 남는 url 은 **실호출로 테마 행이
        # 온 것만**이다(#25·#79).
        print(f"②-d 테마 엔드포인트 탐색({_nd.stale_label(_memo_age) or '나이 미상'}): "
              + (f"✅ 검증됨 {_u}" if _u
                 else f"❌ 검증된 후보 없음 — {_memo.get('why') or '사유 미기록'}"))
        if _memo.get("found"):
            print(f"   ↪ 청크에서 본 경로: {_memo['found']}")
        for _t in _memo.get("tried") or []:
            print(f"   ↪ 시도: {_t}")
    else:
        # 탐색이 안 돌았다는 것은 **사다리가 답했다**는 뜻이거나, 아직 한 번도
        # 전멸한 적이 없다는 뜻이다 — 둘 다 정상이므로 ❌ 가 아니다(#260).
        print("②-d 테마 엔드포인트 탐색: 기록 없음(사다리가 답했거나 전멸한 적 없음)")
    # ── 업종 등락 TOP 10 위젯(메인 대시보드) — 테마와 **다른 캐시**(upjong.json)다.
    # 2026-09-11 사용자 "갑자기 한국이 메인대시보드에서 없어졌어?" 때 ② 는 테마만
    # 보고 이 위젯은 한 줄도 말하지 않았다(#24 열거형 점검은 목록 밖을 못 잡는다).
    prev, age = _cache_read_any("upjong.json")
    if not prev:
        print("②-b 업종 TOP 캐시 없음 — 위젯이 사라졌다면 원천 실패가 처음이 아니다")
    else:
        n_up, n_dn = len(prev.get("up") or []), len(prev.get("down") or [])
        print(f"②-b 업종 TOP 저장분: 상승 {n_up} · 하락 {n_dn} · "
              f"{prev.get('ts', '?')} KST ({_nd.stale_label(age) or '나이 미상'})")
        if _cached("upjong.json") is None:
            print("   ⚠️ 신선 판정 아님 — 다음 렌더가 수집을 시도하고, "
                  "실패하면 이 저장분을 '저장분 ⚠️' 라벨로 서빙한다")
    # ── 업종맵(종목코드 → 업종 한글) — 옛 HTML 경로라 SPA 전환 뒤 0건이다.
    # 조용히 죽으면 KR 항목의 '업종' 칸이 영영 빈다(#12·#43, 리뷰 M5).
    # ⚠️ `kr_industry_map()` 을 부르면 안 된다 — 캐시가 없거나 낡으면 **빌드
    # 스레드를 띄워** 네이버를 친다. 기본 `--check` 는 읽기 전용이어야 한다
    # (#264·#321 진단이 운영 상태를 바꾸거나 원천을 치면 안 된다). 화면이 쓴
    # 그 파일을 그대로 읽는다(#35).
    _imf = _CACHE_DIR / _KR_IND_CACHE
    _im = {}
    if _imf.exists():
        try:
            _im = json.loads(_imf.read_text()) or {}
        except Exception as exc:                             # noqa: BLE001
            print(f"②-c 업종맵 캐시를 못 읽음 — {type(exc).__name__}: {exc}")
    if _im:
        _age = time.time() - _imf.stat().st_mtime
        _fresh = "" if _age < _KR_IND_TTL else " ⚠️ TTL 초과(다음 렌더가 재빌드 시도)"
        print(f"②-c 업종맵: {len(_im):,}종목 "
              f"({_nd.stale_label(_age) or '나이 미상'}){_fresh}")
    else:
        # ⚠️ rc 는 올리지 않는다 — 빌드 경로가 SPA 로 죽어 **지금은 우리가 못
        # 고치는** 상태이고, 매번 ❌ 를 내면 진짜 ❌ 를 가린다(#260·#41 사실은
        # 그대로 말하되 기호는 갈래에 맞춘다). 대신 **다음 행동**을 적는다.
        print("②-c 업종맵: ⚠️ 캐시 0종목 — KR 항목의 업종 칸이 빈다")
        print("   ↪ 빌드 경로가 옛 HTML(sise_group.naver)이라 SPA 전환 뒤 0건이다 "
              "— `python -m bot.scripts.naver_spa_probe` ⑤ '업종 멤버(국내)' 로 "
              "대체 경로를 **재고 나서** 갈아탈 것(#151 추측 금지)")
    if kr_industry_fail_reason():
        print(f"   ↪ {kr_industry_fail_reason()}")
    try:
        from bot.finviz_client import naver_paused
        if naver_paused():
            # 운영자가 일부러 끈 상태다 — 우리 결함이 아니므로 ⏸ 로 말하되 rc 는
            # 올리지 않는다(#260 고칠 게 없는 것과 고칠 수 있는 것을 같은 기호로
            # 세면 진짜 결함이 가려진다). 위젯이 빈 **답**은 이 한 줄이다.
            print("   ⏸ NAVER_PAUSE 켜짐 — 수집을 아예 안 한다(/naverpause 로 해제)")
    except Exception:                                        # noqa: BLE001
        pass
    if not fetch:
        print("③ 실제 수집은 안 했다 — 재려면 `--check --fetch`")
        return rc
    try:
        from bot.finviz_client import naver_paused as _np
        if _np():
            # 정지 중엔 수집이 **설계상** 안 된다 — 그걸 '도달 실패' 로 찍으면
            # 운영자가 원천·네트워크를 보러 간다(#82·#260, 독립 리뷰 2026-09-11).
            print("③ 정지 중이라 실측을 건너뛴다 — /naverpause 로 해제한 뒤 다시 볼 것")
            return rc
    except Exception:                                        # noqa: BLE001
        pass
    t1 = time.time()
    # ⚠️ 여기서 네트워크가 나간다 — `--fetch` 를 태우는 테스트는 반드시 스텁할 것
    # (#312 테스트가 원천을 치면 안 된다). 기본(`--check`)은 조회만이라 안전하다.
    # ⚠️ **화면이 쓰는 그 경로**를 태운다(#35) — 2026-09-11 SPA 전환 뒤에도 옛
    # HTML(`sise_group.naver`)을 재고 있어 고친 뒤에도 영원히 ❌ 였다(독립 리뷰 M1).
    raw, why = _get2_json(_UPJONG_API, params={"pageSize": _UPJONG_PAGE_SIZE})
    n_raw = len(raw) if isinstance(raw, list) else 0
    groups = parse_upjong_json(raw)
    if groups:
        asof = upjong_asof(raw) or "원천 미기록"
        print(f"③-b 업종 TOP 실측: {len(groups)}개 / 원천 {n_raw}행 · "
              f"기준 {asof} · {time.time() - t1:.2f}초")
        if n_raw < _UPJONG_MIN_GROUPS:
            # 값이 다 있어서 조용히 틀린다 — 세어 보지 않으면 안 보인다(#45).
            print(f"   ⚠️ 부분 수신(기대 하한 {_UPJONG_MIN_GROUPS}개) — 상·하위 "
                  "10 랭킹이 전 업종 기준이 아니다. 캐시에는 굽지 않는다")
            rc = 1
    elif not isinstance(raw, list) and raw is not None:
        print(f"③-b 업종 TOP 실측 0개 — {_nd.shape_reason('업종 목록', raw)}")
        rc = 1
    else:
        print(f"③-b 업종 TOP 실측 0개 — {why or _nd.parse_reason('업종 행', n_raw, unit='행')}")
        for ln in json_sample(raw):
            print(f"   {ln}")
        rc = 1
    t0 = time.time()
    out = collect_themes()
    print(f"③ 실측 수집: 테마 {len(out['themes'])}개 · "
          f"{time.time() - t0:.2f}초 · partial={out.get('partial')}")
    if not out["themes"]:
        # 대조 0건은 통과가 아니다(#54).
        print("   ❌ 0개 — 네이버 도달 실패이거나 파싱이 깨졌다")
        # ⚠️ 갈래를 사람이 짐작하게 두지 말 것(#82). 테마는 **JSON 사다리 →
        # 옛 HTML** 순이고(2026-09-12) 여기까지 왔다는 건 둘 다 죽었다는 뜻이다.
        # 옛 HTML 원문 표본을 찍으면 '도달 실패'와 '표가 사라짐'이 갈린다(#109).
        for ln in markup_sample(_get(f"{_BASE}/theme.naver", params={"page": 1}),
                                ("sise_group_detail", "type=theme", "<table")):
            print(f"   {ln}")
        return 1
    # ⚠️ `return 0` 로 끝내면 위에서 세운 rc(업종 TOP 실측 실패·정지)가 버려져
    # 초록불이 된다 — 판정을 계산해 놓고 표시(여기선 종료코드)에 안 쓰면 없는
    # 것과 같다(#123·#292 계열. 회귀가 --fetch 경로를 실제로 태운다).
    return rc


def main(argv: list | None = None) -> int:
    """CLI 진입점. `--check` 면 진단만, 아니면 세 수집을 스모크한다.

    ⚠️ 디스패치를 `if __name__` 블록에 인라인으로 두면 테스트가 못 태워
    '게이트만 꺼도 통과'하는 눈먼 회귀가 된다(#252 실측).
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO)
    if "--check" in args:
        return check(fetch="--fetch" in args)
    mv = fetch_sector_movers()
    print(f"업종 상승 {len(mv['up'])} / 하락 {len(mv['down'])}")
    th = fetch_themes()
    print(f"테마 {len(th['themes'])}")
    for t in th["themes"][:5]:
        print("  ", t["name"], t["pct"])
    ul = fetch_upper_lower()
    print(f"상한가 {len(ul['upper'])} / 하한가 {len(ul['lower'])}")
    return 0


# 엔트리포인트는 **항상 파일 끝**(#276 — 위에 두면 아래 정의에 영영 못 닿는다)
if __name__ == "__main__":
    raise SystemExit(main())
