"""한국 부동산 실거래가 client — 국토교통부(MOLIT) 아파트 매매/전월세
실거래가 (data.go.kr 공공데이터, 무료 키 DATA_GO_KR_API_KEY).

부동산 Byte (bot/realestate_brief.py) 의 데이터 소스. 수치는 전부 MOLIT
공식 신고 데이터 (환각 0). 키 없으면 realestate_key_ready() gate 가 1회
경고 후 graceful skip (KRX creds 패턴 mirror).

엔드포인트 (data.go.kr 활용신청 후 자동 승인):
 • 아파트 매매 실거래: RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev
 • 아파트 전월세 실거래: RTMSDataSvcAptRentDev/getRTMSDataSvcAptRentDev
파라미터: serviceKey, LAWD_CD(5자리 법정동), DEAL_YMD(YYYYMM).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bot.realestate")

_KST = timezone(timedelta(hours=9))
# HTTPS 필수 — data.go.kr RTMS API 가 2025-07-04 갱신되며 평문 HTTP 는 403.
_BASE = "https://apis.data.go.kr/1613000"
_TIMEOUT = 20
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 대표 지역 (법정동 5자리) — 고가/중가/외곽 mix 로 전국 가격대 스펙트럼.
# 41465(용인 수지구) — 안정적 기존 행정구역.
# 41597(화성 동탄구) — 2026-02-01 화성시 4개구(만세/효행/병점/동탄) 분구로 신설된
# 코드. mois.go.kr/code.go.kr 등 공식 코드 조회 사이트가 전부 접근 불가해
# 숫자를 문서로 확정 못 함 → 실거래가 API 로 직접 라이브 조회해 응답의 법정동명
# (여울동·영천동·산척동 — 화성시 공보 2026-02-01 기준 동탄구 관할동과 일치)으로
# 실측 확정(추측 아님, 사용자 2026-08-08). 41595 는 같은 방식으로 병점구로 확인
# (병점동·반월동·진안동) — 화성시 구 분리 후 41590(舊 화성시 통합코드)은 폐지.
# 30200(대전 유성구)·36110(세종시) — VM 라이브 조회로 응답 법정동명(유성구:
# 궁동·구성동·노은동·도안동 등 / 세종: 도담동·반곡동·보람동·새롬동 등) 일치 확인.
# ⚠️ 광주 남구는 2026-04-28/7-1 광주광역시+전라남도 "전남광주통합특별시" 통합
# 출범으로 舊 시도코드(29xxx)가 전부 무효화된 것으로 실측 확인(29155 및 인접
# 29150~29159 전부 0건) — 새 시도코드 미확인 상태라 보류(REFERENCE 에 후속과제
# 기록, 사용자 2026-08-08 "광주는 보류" 결정).
_REGIONS = {
    "11680": "서울 강남구", "11650": "서울 서초구", "11710": "서울 송파구",
    "11440": "서울 마포구", "11350": "서울 노원구",
    "41135": "성남 분당구", "41117": "수원 영통구", "28245": "인천 서구",
    "26350": "부산 해운대구", "27110": "대구 중구",
    "41465": "용인 수지구", "41597": "화성 동탄구",
    "30200": "대전 유성구", "36110": "세종시",
}

_KEY_WARNED = False


def realestate_key_ready() -> bool:
    """DATA_GO_KR_API_KEY 존재 여부. 없으면 최초 1회 가입 안내 후 False."""
    global _KEY_WARNED
    ready = bool(os.environ.get("DATA_GO_KR_API_KEY"))
    if not ready and not _KEY_WARNED:
        log.warning(
            "realestate: DATA_GO_KR_API_KEY 미설정 — data.go.kr 무료 가입 후 "
            "'국토교통부 아파트 실거래가' 활용신청 → .env 에 DATA_GO_KR_API_KEY "
            "추가 필요. 그때까지 부동산 Byte skip."
        )
        _KEY_WARNED = True
    return ready


def _latest_deal_ymd() -> str:
    """가장 최근 신고 가능 월 YYYYMM. 실거래는 계약 후 ~30일 신고라 전월
    데이터가 가장 충실 — 직전 월 사용."""
    d = datetime.now(_KST).date().replace(day=1) - timedelta(days=1)
    return d.strftime("%Y%m")


def _result_msg(text: str) -> str:
    """data.go.kr 응답에서 resultCode/resultMsg/returnAuthMsg 추출 (진단용)."""
    bits = []
    for tag in ("resultCode", "resultMsg", "returnReasonCode",
                "returnAuthMsg", "errMsg", "cmmMsgHeader"):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
        if m and m.group(1).strip():
            bits.append(f"{tag}={m.group(1).strip()[:80]}")
    return " · ".join(bits) or text[:160].replace("\n", " ")


# 엔드포인트 후보 — 사용자가 승인한 서비스명이 non-Dev 일 수도 Dev 일 수도
# 있어 자동 탐색 (첫 HTTP 200 응답 경로를 캐시해 이후 재사용). 403 = 미승인/
# 잘못된 서비스 → 다음 후보 시도.
_TRADE_PATHS = [
    "RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
    "RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
]
_RENT_PATHS = [
    "RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
    "RTMSDataSvcAptRentDev/getRTMSDataSvcAptRentDev",
]
_OFFI_PATHS = [
    "RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
    "RTMSDataSvcOffiTradeDev/getRTMSDataSvcOffiTradeDev",
]
_RH_PATHS = [   # 연립다세대 매매
    "RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade",
    "RTMSDataSvcRHTradeDev/getRTMSDataSvcRHTradeDev",
]
_KIND_PATHS = {"trade": _TRADE_PATHS, "rent": _RENT_PATHS,
               "offi": _OFFI_PATHS, "rh": _RH_PATHS}
_RESOLVED: dict[str, str] = {}


def _call(path: str, lawd_cd: str, deal_ymd: str):
    """단일 호출 → (status_code, text). serviceKey 인코딩 자동 처리."""
    import httpx
    key = (os.environ.get("DATA_GO_KR_API_KEY") or "").strip()
    base_params = {"LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd,
                   "numOfRows": 1000, "pageNo": 1}
    _h = {"User-Agent": _UA, "Accept": "application/xml, text/xml, */*"}
    try:
        if "%" in key:
            from urllib.parse import urlencode
            r = httpx.get(f"{_BASE}/{path}?serviceKey={key}&{urlencode(base_params)}",
                          headers=_h, timeout=_TIMEOUT, follow_redirects=True)
        else:
            r = httpx.get(f"{_BASE}/{path}",
                          params={"serviceKey": key, **base_params},
                          headers=_h, timeout=_TIMEOUT, follow_redirects=True)
        return r.status_code, r.text
    except Exception as exc:
        log.warning("realestate: call %s %s failed: %s", path, lawd_cd, exc)
        return None, ""


def _fetch_xml(kind: str, lawd_cd: str, deal_ymd: str) -> str | None:
    """kind='trade'|'rent'. 후보 경로 자동 탐색 + 캐시. 첫 HTTP 200 경로
    확정. 200+item → text, 200+무데이터 → None(경로는 맞음), 전부 403 →
    None + 진단 로그."""
    candidates = [_RESOLVED[kind]] if kind in _RESOLVED else _KIND_PATHS.get(kind, [])
    last_status = None
    last_body = ""
    for path in candidates:
        status, text = _call(path, lawd_cd, deal_ymd)
        last_status = status
        last_body = text or ""
        if status == 200:
            _RESOLVED[kind] = path           # 경로 확정 (auth+endpoint OK)
            if "<item>" in text:
                return text
            log.warning("realestate[%s] %s %s item 0 — %s",
                        kind, lawd_cd, deal_ymd, _result_msg(text))
            return None
        # 403/404/500 → 다음 후보
    # 전부 실패 — 응답 본문 일부를 노출해 정확한 원인(잘못된 서비스명 /
    # 미승인 / 서버오류 메시지) 진단 가능케.
    log.warning("realestate[%s] %s 모든 엔드포인트 실패 (last HTTP %s) — body: %s",
                kind, lawd_cd, last_status, _result_msg(last_body))
    return None


def _parse_trades(xml: str) -> list[dict]:
    """매매 실거래 item 파싱 → [{거래금액(만원 int), 전용면적, 건축년도,
    법정동, 아파트}]. 컬럼명은 data.go.kr 응답 한글 태그."""
    out: list[dict] = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        def _t(tag: str) -> str:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
            return (m.group(1).strip() if m else "")
        amt_raw = _t("거래금액") or _t("dealAmount")
        try:
            amt = int(amt_raw.replace(",", "").strip())
        except Exception:
            continue
        try:
            area = float(_t("전용면적") or _t("excluUseAr") or 0)
        except Exception:
            area = 0.0
        out.append({
            "amount_manwon": amt, "area": area,
            "apt": _t("아파트") or _t("aptNm"),
            "dong": _t("법정동") or _t("umdNm"),
            "build_year": _t("건축년도") or _t("buildYear"),
        })
    return out


def _parse_rents(xml: str) -> list[dict]:
    """전월세 실거래 item 파싱 → [{deposit(보증금 만원), monthly(월세 만원),
    area, is_jeonse}]. 전세 = 월세 0."""
    out: list[dict] = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        def _t(tag: str) -> str:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
            return (m.group(1).strip() if m else "")
        try:
            dep = int((_t("보증금액") or _t("deposit")).replace(",", "") or 0)
        except Exception:
            continue
        try:
            mon = int((_t("월세금액") or _t("monthlyRent")).replace(",", "") or 0)
        except Exception:
            mon = 0
        try:
            area = float(_t("전용면적") or _t("excluUseAr") or 0)
        except Exception:
            area = 0.0
        out.append({"deposit": dep, "monthly": mon, "area": area,
                    "is_jeonse": mon == 0})
    return out


def collect_realestate_data() -> dict:
    """대표 지역별 직전월 아파트 매매 + 전월세 실거래 집계. 수치는 MOLIT
    정확값. {ymd, regions:{name:{n_deals, avg_manwon, avg_per_pyeong, max,
    jeonse_avg_manwon, jeonse_ratio}}}. 키 없으면 빈 dict."""
    if not realestate_key_ready():
        return {}
    ymd = _latest_deal_ymd()
    data: dict = {"ymd": ymd, "regions": {}}
    # API 별 per-run enable (미승인/오류 시 첫 실패 후 skip — 도배 방지)
    enabled = {"rent": True, "offi": True, "rh": True}

    def _avg_trade(kind: str, lawd: str) -> int | None:
        if not enabled[kind]:
            return None
        xml = _fetch_xml(kind, lawd, ymd)
        if not xml:
            enabled[kind] = False
            log.info("realestate: %s API 비활성 (활용신청/엔드포인트 확인)", kind)
            return None
        tr = _parse_trades(xml)
        amts = [t["amount_manwon"] for t in tr if t["amount_manwon"] > 0]
        return round(sum(amts) / len(amts)) if amts else None

    for lawd, name in _REGIONS.items():
        xml = _fetch_xml("trade", lawd, ymd)
        if not xml:
            continue
        trades = _parse_trades(xml)
        if not trades:
            continue
        amts = [t["amount_manwon"] for t in trades if t["amount_manwon"] > 0]
        ppp = [t["amount_manwon"] / (t["area"] / 3.3058)
               for t in trades if t["area"] > 0]
        if not amts:
            continue
        avg_sale = sum(amts) / len(amts)
        reg = {
            "n_deals": len(amts),
            "avg_manwon": round(avg_sale),
            "avg_per_pyeong": round(sum(ppp) / len(ppp)) if ppp else None,
            "max_manwon": max(amts),
        }
        # 오피스텔 / 연립다세대 매매 평균 (주거유형 확대)
        offi = _avg_trade("offi", lawd)
        if offi:
            reg["offi_avg_manwon"] = offi
        rh = _avg_trade("rh", lawd)
        if rh:
            reg["rh_avg_manwon"] = rh
        # 전월세 — 전세(월세 0) 평균 보증금 + 전세가율 (전세평균/매매평균).
        rxml = _fetch_xml("rent", lawd, ymd) if enabled["rent"] else None
        if enabled["rent"] and not rxml:
            enabled["rent"] = False
            log.info("realestate: rent API 비활성 (활용신청/엔드포인트 확인)")
        if rxml:
            rents = _parse_rents(rxml)
            jeonse = [r["deposit"] for r in rents if r["is_jeonse"] and r["deposit"] > 0]
            if jeonse:
                avg_j = sum(jeonse) / len(jeonse)
                reg["jeonse_avg_manwon"] = round(avg_j)
                reg["jeonse_n"] = len(jeonse)
                if avg_sale > 0:
                    reg["jeonse_ratio"] = round(avg_j / avg_sale * 100, 1)
        data["regions"][name] = reg
    return data
