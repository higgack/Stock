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

_BASE_HOST = "https://apis.data.go.kr/1160100"
_TIMEOUT = 20
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_KEY_WARNED = False

# (라벨 → (full_base, op)). FSC 는 구형 '/service/GetXxxService' 와 신형
# '/GetXxxService_V2' 두 패턴 혼재 — Swagger 'Base URL' 로 확정.
#  권리일정: discovery 2026-05-31 확정 (Swagger Base + GET op).
#  시세/KRX: 구형 후보 (403=활용신청 필요. 승인 후에도 404 면 V2 경로 확인).
_RIGHT = (f"{_BASE_HOST}/GetStocRighScheService_V2", "getRighExerReasSche_V2")
_PRICE = (f"{_BASE_HOST}/service/GetStockSecuritiesInfoService", "getStockPriceInfo")
_KRX = (f"{_BASE_HOST}/service/GetKrxListedInfoService", "getItemInfo")


def fsc_key_ready() -> bool:
    global _KEY_WARNED
    ready = bool(os.environ.get("DATA_GO_KR_API_KEY"))
    if not ready and not _KEY_WARNED:
        log.warning("fsc: DATA_GO_KR_API_KEY 미설정 — 금융위 증권데이터 skip.")
        _KEY_WARNED = True
    return ready


def _http(base: str, op: str, params: dict):
    """FSC GET → (status, text). serviceKey 인코딩 자동(% raw URL / 그 외 params).
    resultType=json 강제. base = full service base (호스트+서비스경로)."""
    import httpx
    key = (os.environ.get("DATA_GO_KR_API_KEY") or "").strip()
    q = {"resultType": "json", "numOfRows": params.pop("numOfRows", 10),
         "pageNo": 1, **params}
    url = f"{base}/{op}"
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


def _probe(label: str, base: str, op: str, extra: dict | None = None) -> None:
    """(base, op) status + body head 출력 — 경로/필드 확정용."""
    print(f"\n=== {label} ===")
    params = {"numOfRows": 3, **(extra or {})}
    status, body = _http(base, op, params)
    print(f"--- {base.split('/1160100')[-1]}/{op}\n    HTTP {status} · {(body or '')[:600]}\n")


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

    # 삼성전자(005930) 로 시세 필터, 권리일정은 기준일 기준
    _probe("주식시세정보 15094808", _PRICE[0], _PRICE[1], {"likeSrtnCd": "005930", "basDt": bas})
    _probe("KRX상장종목정보 15094775", _KRX[0], _KRX[1], {"likeSrtnCd": "005930"})
    _probe("주식권리일정정보 15059609 (확정 V2)", _RIGHT[0], _RIGHT[1], {"basDt": bas})

    print("→ HTTP 200 + items 나오는 것 + 첫 행 필드명을 붙여주세요.")
    print("  권리일정 200 = corp action 가드 통합 준비 완료.")
    print("  시세/KRX 403 = 활용신청 필요 / 404 = V2 경로(Swagger Base URL) 확인 필요.")
