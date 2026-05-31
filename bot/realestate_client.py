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
_BASE = "http://apis.data.go.kr/1613000"
_TIMEOUT = 20

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


def _fetch_xml(path: str, lawd_cd: str, deal_ymd: str) -> str | None:
    import httpx
    key = os.environ.get("DATA_GO_KR_API_KEY", "")
    try:
        r = httpx.get(
            f"{_BASE}/{path}",
            params={"serviceKey": key, "LAWD_CD": lawd_cd,
                    "DEAL_YMD": deal_ymd, "numOfRows": 1000, "pageNo": 1},
            timeout=_TIMEOUT,
        )
        if r.status_code != 200 or "<item>" not in r.text:
            # data.go.kr 는 키 오류 시에도 200 + resultCode 로 응답
            if "SERVICE_KEY_IS_NOT_REGISTERED" in r.text or "SERVICE ERROR" in r.text:
                log.warning("realestate: data.go.kr 키 미승인 (활용신청 확인): %s", r.text[:160])
            return None
        return r.text
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


def collect_realestate_data() -> dict:
    """대표 지역별 직전월 아파트 매매 실거래 집계. 수치는 MOLIT 정확값.
    {ymd, regions:{name:{n_deals, avg_manwon, avg_per_pyeong}}}. 키 없으면
    빈 dict."""
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
        # 평당가 (만원/평) — 전용면적 → 평(÷3.3058)
        ppp = [t["amount_manwon"] / (t["area"] / 3.3058)
               for t in trades if t["area"] > 0]
        if not amts:
            continue
        data["regions"][name] = {
            "n_deals": len(amts),
            "avg_manwon": round(sum(amts) / len(amts)),
            "avg_per_pyeong": round(sum(ppp) / len(ppp)) if ppp else None,
            "max_manwon": max(amts),
        }
    return data
