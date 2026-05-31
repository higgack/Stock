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
_REGIONS = {
    "11680": "서울 강남구", "11650": "서울 서초구", "11710": "서울 송파구",
    "11440": "서울 마포구", "11350": "서울 노원구",
    "41135": "성남 분당구", "41117": "수원 영통구", "28245": "인천 서구",
    "26350": "부산 해운대구", "27110": "대구 중구",
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


def _fetch_xml(path: str, lawd_cd: str, deal_ymd: str) -> str | None:
    """data.go.kr 호출. serviceKey 인코딩 자동 처리 (Encoding 키는 '%' 포함
    → URL 직삽입·재인코딩 금지, Decoding 키는 httpx params 로 인코딩). 실패
    시 resultMsg 를 로그로 노출 (진단)."""
    import httpx
    key = (os.environ.get("DATA_GO_KR_API_KEY") or "").strip()
    base_params = {"LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd,
                   "numOfRows": 1000, "pageNo": 1}
    try:
        _h = {"User-Agent": _UA, "Accept": "application/xml, text/xml, */*"}
        if "%" in key:
            # Encoding 키 (이미 URL-encoded) → 직접 URL 에 넣고 재인코딩 방지
            from urllib.parse import urlencode
            qs = urlencode(base_params)
            r = httpx.get(f"{_BASE}/{path}?serviceKey={key}&{qs}",
                          headers=_h, timeout=_TIMEOUT, follow_redirects=True)
        else:
            # Decoding 키 (raw) → httpx 가 한 번만 인코딩
            r = httpx.get(f"{_BASE}/{path}",
                          params={"serviceKey": key, **base_params},
                          headers=_h, timeout=_TIMEOUT, follow_redirects=True)
        if r.status_code != 200:
            log.warning("realestate: %s %s HTTP %d", path, lawd_cd, r.status_code)
            return None
        if "<item>" in r.text:
            return r.text
        # 200 인데 item 없음 → 인증 실패 / totalCount 0 / 잘못된 service.
        # 실제 사유를 로그로 (다음 run 에서 원인 즉시 파악).
        log.warning("realestate: %s %s %s item 0 — %s",
                    path.split('/')[-1], lawd_cd, deal_ymd, _result_msg(r.text))
        return None
    except Exception as exc:
        log.warning("realestate: fetch %s %s %s failed: %s", path, lawd_cd, deal_ymd, exc)
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
    for lawd, name in _REGIONS.items():
        xml = _fetch_xml(
            "RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev", lawd, ymd)
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
        # 전월세 — 전세(월세 0) 평균 보증금 + 전세가율 (전세평균/매매평균)
        rxml = _fetch_xml(
            "RTMSDataSvcAptRentDev/getRTMSDataSvcAptRentDev", lawd, ymd)
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
