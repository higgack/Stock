"""OECD 선행지수·장기금리 + IMF PortWatch 해협 통항량 — 직접 호출 경량 클라이언트.

사용자 2026-10-22: 외부 레포(OpenBB) 검토 뒤 "④ 적용". OpenBB 를 통째로 깔지
않고 **주소와 응답 형식만** 가져왔다 — `openbb` 패키지는 import 때 site-packages
에 코드를 다시 생성하고(`OPENBB_AUTO_BUILD` 기본 켜짐) 자체 디스크 캐시에 코드
지문이 없어 "캐시가 fix 를 가린다"(#18·#21b) 병을 그대로 들여온다.

원천(전부 무키·무료):
  · OECD SDMX v2 (sdmx.oecd.org) — 종합선행지수 CLI(`DSD_STES@DF_CLI` 4.1,
    진폭조정·OECD 조화) · 장기금리(`DSD_KEI@DF_KEI` 4.0 `IRLT`, 연 %).
    우리 FRED 의 OECD 미러 일부가 멈춘 전례가 있어(`fred_boards_catalog` —
    M2 2023-10) **원천을 직접** 묻는다.
  · IMF PortWatch (ArcGIS FeatureServer `Daily_Chokepoints_Data`) — 해협별 일별
    통항 선박 수·적재능력. 수출입 보드와 같은 질문(물동량)에 답한다.
주소·열 이름은 OpenBB 의 **녹화된 실제 응답**(tests/record cassettes, Apache-2.0)
으로 확인했다. ⚠️ 그 녹화는 과거 시점이라 지금도 같은지는 VM 에서
`python -m bot.global_sources --check` 로 잰다(#12 — 샌드박스는 두 원천 모두
연결이 막혀 있다).

규칙:
  · 실패는 갈래로 기록하고(HTTP 상태·시간 초과·연결·형식) 30분만 믿는다 —
    빈 결과를 길게 굽지 않는다(#280·#303).
  · 성공은 12시간 디스크 캐시. 캐시 레코드에 이 모듈의 판(`_VER`)을 찍어
    파서를 고치면 옛 캐시를 안 믿는다(#21b).
  · 값이 없으면 None + 사유. 0 으로 채우지 않는다(#235).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

_VER = 1
_TTL_OK = 12 * 3600
_TTL_FAIL = 30 * 60
_TIMEOUT = 10
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "global_sources"

OECD_BASE = "https://sdmx.oecd.org/public/rest/v2/data/dataflow"
OECD_PRESETS = {
    # 이름: (기관, 데이터흐름, 판, 키 틀, 단위, 설명)
    "cli": ("OECD.SDD.STES", "DSD_STES@DF_CLI", "4.1",
            "{area}.M.LI.*.*.AA.IX.*.H", "지수(100=장기추세)",
            "OECD 종합선행지수(CLI, 진폭조정)"),
    "lt_rate": ("OECD.SDD.STES", "DSD_KEI@DF_KEI", "4.0",
                "{area}.M.IRLT.PA._Z._Z._Z", "연 %", "OECD 장기금리(국채 10년 근사)"),
}
OECD_AREAS = {"KOR": "한국", "JPN": "일본", "USA": "미국", "DEU": "독일",
              "CHN": "중국", "G20": "G20"}

PORTWATCH_URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/"
                 "services/Daily_Chokepoints_Data/FeatureServer/0/query")
CHOKEPOINTS = {
    "chokepoint6": "호르무즈 해협", "chokepoint5": "말라카 해협",
    "chokepoint1": "수에즈 운하", "chokepoint4": "바브엘만데브 해협",
    "chokepoint11": "대만 해협", "chokepoint12": "대한 해협",
    "chokepoint2": "파나마 운하",
}

_FAIL: dict[str, tuple[float, str]] = {}


def _cache_path(key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    return _CACHE_DIR / f"{safe}.json"


def _read_cache(key: str):
    try:
        rec = json.loads(_cache_path(key).read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return None
    if not isinstance(rec, dict) or rec.get("ver") != _VER:
        return None
    if time.time() - float(rec.get("ts") or 0) > _TTL_OK:
        return None
    return rec.get("data")


def _write_cache(key: str, data) -> None:
    try:
        p = _cache_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ver": _VER, "ts": time.time(), "data": data},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as exc:                            # noqa: BLE001
        log.debug("global_sources: cache write %s: %s", key, exc)


def failure_reason(key: str) -> str:
    """최근 실패 사유(30분 안) — 없으면 빈 문자열."""
    ent = _FAIL.get(key)
    if not ent or time.time() - ent[0] > _TTL_FAIL:
        return ""
    return ent[1]


def _head(text: str, n: int = 200) -> str:
    """원천이 적어 보낸 본문 머리 — 무키 원천이라 가릴 것이 없다. 사유를
    타입명만 남기면 VM 첫 실행에서 가장 필요한 정보를 버린다(#82·#350)."""
    return " ".join((text or "").split())[:n]


def _classify(exc: Exception | None = None, status: int | None = None,
              body: str = "") -> str:
    tail = f" · 원문: {_head(body)}" if body and _head(body) else ""
    if status is not None:
        if status == 404:
            # SDMX 는 '결과 없음(NoRecordsFound)' 에도 404 를 준다 — 단정하지 않는다.
            return f"HTTP 404 — 주소·키가 틀렸거나 그 조건의 관측이 없음{tail}"
        if status == 429:
            return f"요청 한도 초과(HTTP 429) — 30분 뒤 다시 묻습니다{tail}"
        if status >= 500:
            return f"원천 서버 오류(HTTP {status}) — 30분 뒤 다시 묻습니다{tail}"
        return f"원천이 거절(HTTP {status}){tail}"
    try:
        import requests
        if isinstance(exc, requests.exceptions.Timeout):
            return "응답 시간 초과 — 30분 뒤 다시 묻습니다"
        if isinstance(exc, requests.exceptions.ConnectionError):   # Proxy·SSL 포함
            return f"연결 실패({type(exc).__name__}) — 네트워크·방화벽 확인"
    except Exception:                                   # noqa: BLE001
        pass
    name = type(exc).__name__ if exc else "알 수 없음"
    return f"형식을 읽지 못함({name}: {_head(str(exc), 160)})"


def _host_down(url: str) -> str:
    """같은 호스트가 이 프로세스에서 30분 안에 **연결 단계**에서 실패했으면 그
    사유 — 9행이 차례로 10초씩 기다리지 않게 한다(리뷰 M5). 응답이 온 실패
    (HTTP 상태·형식)는 호스트 전체를 막지 않는다."""
    from urllib.parse import urlparse
    return failure_reason("host:" + urlparse(url).netloc)


def _mark_host(url: str, why: str) -> None:
    from urllib.parse import urlparse
    if why.startswith(("응답 시간 초과", "연결 실패")):
        _FAIL["host:" + urlparse(url).netloc] = (time.time(), why)


def _get(url: str, *, params=None, headers=None):
    import requests
    return requests.get(url, params=params, headers=headers or {}, timeout=_TIMEOUT)


def parse_oecd_csv(text: str) -> list[tuple[str, float]]:
    """SDMX CSV(labels=both) → [(기간, 값)] 기간 오름차순. 값 없는 행은 뺀다. 순수.

    열은 이름으로 찾는다 — 'TIME_PERIOD: Time period' 처럼 라벨이 붙어 온다."""
    rd = csv.reader(io.StringIO(text))
    try:
        head = next(rd)
    except StopIteration:
        return []
    def col(name):
        for i, h in enumerate(head):
            if h == name or h.startswith(name + ":"):
                return i
        return None
    it, iv = col("TIME_PERIOD"), col("OBS_VALUE")
    if it is None or iv is None:
        raise ValueError("TIME_PERIOD/OBS_VALUE 열 없음")
    out = {}
    for row in rd:
        if len(row) <= max(it, iv):
            continue
        p, v = row[it].strip(), row[iv].strip()
        try:
            fv = float(v)
        except ValueError:
            continue
        if p in out and out[p] != fv:
            # 와일드카드 키가 여러 계열을 돌려주면 섞이지 않게 멈춘다(리뷰 Low).
            raise ValueError(f"같은 기간 {p} 에 값이 둘({out[p]} · {fv}) — 계열이 여러 개 옴")
        out[p] = fv
    return sorted(out.items())


def oecd_series(preset: str, area: str, start: str = "2018-01") -> dict:
    """→ {"ok", "rows", "why", "source"}. 캐시·실패 기억 포함."""
    org, flow, ver, keyfmt, unit, desc = OECD_PRESETS[preset]
    key = f"oecd_{preset}_{area}_{start}"
    src = f"OECD SDMX {flow} {ver}"
    hit = _read_cache(key)
    if hit is not None:
        return {"ok": bool(hit), "rows": [tuple(r) for r in hit], "why": "", "source": src}
    why = failure_reason(key)
    if why:
        return {"ok": False, "rows": [], "why": why + " (직전 기록)", "source": src}
    from urllib.parse import quote
    url = f"{OECD_BASE}/{org}/{quote(flow, safe='')}/{ver}/{keyfmt.format(area=area)}"
    params = {"c[TIME_PERIOD]": f"ge:{start}", "detail": "full",
              "dimensionAtObservation": "TIME_PERIOD"}
    hdr = {"Accept": "application/vnd.sdmx.data+csv; version=2.0.0; labels=both"}
    down = _host_down(url)
    if down:
        return {"ok": False, "rows": [], "why": down + " (같은 원천 직전 실패)",
                "source": src}
    try:
        r = _get(url, params=params, headers=hdr)
        if r.status_code != 200:
            why = _classify(status=r.status_code, body=r.text)
        else:
            rows = parse_oecd_csv(r.text)
            if not rows:
                why = "응답에 관측값이 없습니다(키·기간 확인)"
            else:
                _FAIL.pop(key, None)
                _write_cache(key, rows)
                return {"ok": True, "rows": rows, "why": "", "source": src}
    except Exception as exc:                            # noqa: BLE001
        why = _classify(exc)
    _FAIL[key] = (time.time(), why)
    _mark_host(url, why)
    log.info("global_sources: %s %s %s — %s", preset, area, src, why)
    return {"ok": False, "rows": [], "why": why, "source": src}


def parse_portwatch(payload: dict) -> list[dict]:
    """ArcGIS JSON → [{date, n_total, n_tanker, n_container, capacity}] 날짜 오름차순."""
    feats = (payload or {}).get("features")
    if not isinstance(feats, list):
        err = (payload or {}).get("error")
        raise ValueError(f"features 없음{f': {err}' if err else ''}")
    out = []
    for f in feats:
        a = (f or {}).get("attributes") or {}
        d = a.get("date")
        if not d:
            continue
        if isinstance(d, (int, float)):        # 에포크 ms 로 오는 판 대비
            d = time.strftime("%Y-%m-%d", time.gmtime(d / 1000))
        row = {"date": str(d)[:10]}
        for k in ("n_total", "n_tanker", "n_container", "capacity"):
            v = a.get(k)
            row[k] = float(v) if isinstance(v, (int, float)) else None
        out.append(row)
    out.sort(key=lambda r: r["date"])
    return out


PORTWATCH_DAYS = 800      # 7일 평균 + 월간 YoY(12개월) + 여유


def chokepoint_daily(cid: str, days: int = PORTWATCH_DAYS) -> dict:
    """해협 일별 통항 — 최근 `days` 일. → {"ok","rows","why","source","note"}.

    캐시 키에 날짜를 넣지 않는다 — 넣으면 날마다 새 파일이 쌓인다(#430)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    end = datetime.now(ZoneInfo("Asia/Seoul")).date()          # 규칙 10a KST
    start = end - timedelta(days=days)
    key = f"portwatch_{cid}_{days}"
    src = "IMF PortWatch Daily_Chokepoints_Data"
    hit = _read_cache(key)
    if isinstance(hit, dict):
        # 잘림 사실(note)도 같이 굽는다 — 캐시 히트에서 사라지면 12시간 조용하다(#45).
        rows = hit.get("rows") or []
        return {"ok": bool(rows), "rows": rows, "why": "", "source": src,
                "note": str(hit.get("note") or "")}
    why = failure_reason(key) or _host_down(PORTWATCH_URL)
    if why:
        return {"ok": False, "rows": [], "why": why + " (직전 기록)", "source": src,
                "note": ""}
    params = {
        "where": (f"portid = '{cid.upper()}' AND date >= TIMESTAMP '{start} 00:00:00'"
                  f" AND date <= TIMESTAMP '{end} 00:00:00'"),
        "outFields": "date,portid,n_total,n_tanker,n_container,capacity",
        # ⚠️ **내림차순**으로 받는다 — 서버 상한(maxRecordCount)에 걸리면
        # 오름차순은 **최신**이 잘린다(#251 의 ECOS 판과 같은 함정). 내림차순이면
        # 잘리는 쪽이 오래된 끝이고, 잘렸다는 사실은 note 로 말한다(#45).
        "orderByFields": "date DESC", "resultRecordCount": 2000, "f": "json",
    }
    try:
        r = _get(PORTWATCH_URL, params=params)
        if r.status_code != 200:
            why = _classify(status=r.status_code, body=r.text)
        else:
            payload = r.json()
            rows = parse_portwatch(payload)
            if not rows:
                why = "해당 기간 통항 기록이 없습니다"
            else:
                note = (f"원천 상한에 걸려 최근 {len(rows)}일만 받았습니다"
                        if payload.get("exceededTransferLimit") else "")
                _FAIL.pop(key, None)
                _write_cache(key, {"rows": rows, "note": note})
                return {"ok": True, "rows": rows, "why": "", "source": src,
                        "note": note}
    except Exception as exc:                            # noqa: BLE001
        why = _classify(exc)
    _FAIL[key] = (time.time(), why)
    _mark_host(PORTWATCH_URL, why)
    log.info("global_sources: %s %s — %s", cid, src, why)
    return {"ok": False, "rows": [], "why": why, "source": src, "note": ""}


def rolling_mean(rows: list[dict], key: str = "n_total",
                 window: int = 7) -> list[tuple[str, float]]:
    """일별 행 → 7일 이동평균 [(날짜, 값)]. 창이 덜 찬 앞부분은 버린다. 순수.

    해협 통항은 요일·기상에 따라 하루 값이 크게 흔들려 월말 하루로 월을 대표하면
    잡음이 된다 — 보드는 월말 값을 쓰므로(`_monthly`) 7일 평균을 넘긴다."""
    vals = [(r["date"], r.get(key)) for r in rows if isinstance(r.get(key), (int, float))]
    out = []
    for i in range(window - 1, len(vals)):
        chunk = [v for _, v in vals[i - window + 1:i + 1]]
        out.append((vals[i][0], sum(chunk) / window))
    return out


def check() -> int:
    """VM 에서 원천이 지금 답하는지 잰다 — 읽기만, 캐시도 쓰지 않는다(#264).

    이상 없을 때의 출력: 항목마다 `✅ … 최신 YYYY-MM … 값`, 마지막 줄 `✅ 전부 응답`.
    """
    import sys
    print(f"global_sources --check v{_VER} · 인터프리터 {sys.executable}")
    bad = 0
    global _read_cache, _write_cache
    rc, wc = _read_cache, _write_cache
    _FAIL.clear()                                      # 지금 원천을 잰다(직전 실패 기억 무시)
    _read_cache = lambda key: None                     # noqa: E731 — 캐시 우회(지금 원천을 잰다)
    _write_cache = lambda key, data: None              # noqa: E731 — 운영 캐시를 건드리지 않는다
    try:
        # 대상은 **보드가 실제로 쓰는 행**에서 파생한다 — 손으로 적으면 화면이
        # 안 쓰는 것을 재고 쓰는 것을 놓친다(#35·#24, 리뷰 M4). 조회 범위도 같다.
        from bot.fred_boards import _OECD_START
        from bot.fred_boards_catalog import LIQ_SERIES
        srcs = [str(s.get("src") or "") for s in LIQ_SERIES]
        oecd = [x.split(":")[1:] for x in srcs if x.startswith("oecd:")]
        pws = [x.split(":", 1)[1] for x in srcs if x.startswith("pw:")]
        print(f"  대상: 보드 행 OECD {len(oecd)} · PortWatch {len(pws)}")
        for preset, area in oecd:
            r = oecd_series(preset, area, start=_OECD_START)
            if r["ok"]:
                p, v = r["rows"][-1]
                print(f"  ✅ OECD {preset:7} {area}: 최신 {p} = {v:g} ({len(r['rows'])}개월)")
            else:
                bad += 1
                print(f"  ❌ OECD {preset:7} {area}: {r['why']}")
        for cid in pws:
            name = CHOKEPOINTS.get(cid, cid)
            r = chokepoint_daily(cid)
            if r["ok"]:
                last = r["rows"][-1]
                n = last.get("n_total")
                print(f"  ✅ PortWatch {name}: 최신 {last['date']} · 통항 "
                      f"{'—' if n is None else f'{n:g}'}척 ({len(r['rows'])}일)"
                      + (f" · {r['note']}" if r.get("note") else ""))
            else:
                bad += 1
                print(f"  ❌ PortWatch {name}: {r['why']}")
    finally:
        _read_cache, _write_cache = rc, wc
    print("✅ 전부 응답" if not bad else f"❌ {bad}개 항목이 응답하지 않았습니다 — 위 사유 참고")
    return 0 if not bad else 1


if __name__ == "__main__":
    import sys as _sys
    if "--check" in _sys.argv:
        _sys.exit(check())
    print("사용: python -m bot.global_sources --check")
