"""금융위원회(FSC) 금융공공데이터 client — KR 증권 데이터 (KRX 로그인 불필요).

data.go.kr 금융위원회 OpenAPI (base apis.data.go.kr/1160100/service, 동일
무료 키 DATA_GO_KR_API_KEY 공유, 활용신청 별도). pykrx 가 2025-12 KRX
유료화로 KRX_ID 의존이 된 뒤의 **KRX-login-free fallback 백본** + corp
action 권리일정.

Phase 1 대상 3종:
  주식시세정보      (15094808) — 전종목 일별 OHLCV·시총·거래량
  KRX상장종목정보   (15094775) — 종목 master (코드·명·시장·업종)
  주식권리일정정보  (15059609) — 증자·배당락·액면분할·감자 ex-date

⚠️ FSC 데이터는 실시간 아님 — 기준일 익영업일 13시 이후 갱신(금→월).
5거래일 horizon 엔 충분, intraday 엔 부적합.

엔드포인트/필드는 응답으로 확인 (discovery-first):
    cd ~/stock && .venv/bin/python -m bot.fsc_client      # probe
키 없으면 fsc_key_ready() gate 가 graceful skip.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("bot.fsc")

_BASE = "https://apis.data.go.kr/1160100/service"
_TIMEOUT = 20
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_KEY_WARNED = False

# (라벨, service, op) 후보 — discovery 가 200+items 첫 매치 확정.
# FSC 금융공공데이터 명명 규칙 추정 + 대체 후보. probe 로 실제 경로 확인.
_PRICE_CANDIDATES = (
    ("GetStockSecuritiesInfoService", "getStockPriceInfo"),
)
_KRX_CANDIDATES = (
    ("GetKrxListedInfoService", "getItemInfo"),
)
_RIGHT_CANDIDATES = (
    ("GetStockRightScheduleInfoService", "getStockRightScheduleInfo"),
    ("GetStockRightsScheduleInfoService", "getStockRightsScheduleInfo"),
    ("GetStockRightScheduleService", "getStockRightScheduleInfo"),
)


def fsc_key_ready() -> bool:
    global _KEY_WARNED
    ready = bool(os.environ.get("DATA_GO_KR_API_KEY"))
    if not ready and not _KEY_WARNED:
        log.warning("fsc: DATA_GO_KR_API_KEY 미설정 — 금융위 증권데이터 skip.")
        _KEY_WARNED = True
    return ready


def _http(service: str, op: str, params: dict):
    """FSC GET → (status, text). serviceKey 인코딩 자동(% raw URL / 그 외 params).
    resultType=json 강제."""
    import httpx
    key = (os.environ.get("DATA_GO_KR_API_KEY") or "").strip()
    q = {"resultType": "json", "numOfRows": params.pop("numOfRows", 10),
         "pageNo": 1, **params}
    url = f"{_BASE}/{service}/{op}"
    h = {"User-Agent": _UA, "Accept": "application/json, */*"}
    try:
        if "%" in key:
            from urllib.parse import urlencode
            r = httpx.get(f"{url}?serviceKey={key}&{urlencode(q)}",
                          headers=h, timeout=_TIMEOUT, follow_redirects=True)
        else:
            r = httpx.get(url, params={"serviceKey": key, **q},
                          headers=h, timeout=_TIMEOUT, follow_redirects=True)
        return r.status_code, r.text
    except Exception as exc:
        log.warning("fsc: %s/%s 호출 실패: %s", service, op, exc)
        return None, ""


def _probe(label: str, candidates: tuple, extra: dict | None = None) -> None:
    """후보 (service, op) 별 status + body head 출력 — 경로/필드 확정용."""
    print(f"\n=== {label} ===")
    for service, op in candidates:
        params = {"numOfRows": 2, **(extra or {})}
        status, body = _http(service, op, params)
        print(f"--- {service}/{op}\n    HTTP {status} · {(body or '')[:500]}\n")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception:
        pass
    if not fsc_key_ready():
        print("DATA_GO_KR_API_KEY 미설정 — .env 확인")
        raise SystemExit(1)

    # 최근 영업일(기준일) — 일부 op 는 basDt 필수일 수 있어 함께 시도
    from datetime import datetime, timedelta, timezone
    d = datetime.now(timezone(timedelta(hours=9))).date()
    while d.weekday() >= 5:  # 주말 walk-back
        d -= timedelta(days=1)
    bas = d.strftime("%Y%m%d")
    print(f"기준일(basDt) 시도값: {bas}")

    # 삼성전자(005930) 로 시세/권리일정 필터 시도
    _probe("주식시세정보 15094808 (basDt 없이)", _PRICE_CANDIDATES, {"likeSrtnCd": "005930"})
    _probe("주식시세정보 15094808 (basDt)", _PRICE_CANDIDATES, {"basDt": bas, "likeSrtnCd": "005930"})
    _probe("KRX상장종목정보 15094775", _KRX_CANDIDATES, {"likeSrtnCd": "005930"})
    _probe("주식권리일정정보 15059609", _RIGHT_CANDIDATES, {"numOfRows": 5})

    print("→ HTTP 200 + items 나오는 (service/op) + 첫 행 필드명을 붙여주세요.")
    print("  401 '인증키' = 해당 API 활용신청 필요 / 03 NODATA = 경로 OK·인자 조정.")
