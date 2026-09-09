"""🔋 Bollinger 보드 — 볼린저 밴드 상단 돌파 종목수로 보는 시장 에너지.

전략 출처: 사용자 2026-09-09 캡처("볼린저 밴드 상단 돌파 종목수를 활용한 시장
에너지 분석"). 설계·근거 전량은 `docs/plans/bollinger_board_plan.md`.

한 줄 요약 — 시장의 각 종목에 볼린저 밴드(20, 2σ)를 씌우고 **종가가 상단밴드
위에서 마감한 종목 수**를 매 거래일 세어 시계열로 쌓는다. 원문이 "절댓값보다
추이가 중요하다"고 못박았으므로 판정 1순위는 5일선의 방향이고, 원문 문턱
(20/10)은 예시로만 쓴다. 산식·판정은 전부 `bot/bollinger.py`(순수)에 있고 이
모듈은 **유니버스·수집·저장·렌더·진단**만 한다.

시장: KR·US·JP·HK·CN_A·TW — 계산·판정·저장·감사 경로는 6시장 **완전 동일**하고
시장 게이트가 없다. 다른 것은 유니버스 원천뿐이고(원천이 시장별이라서), 그
정의는 **카드 라벨이 항상 말한다**(#34).
"""
from __future__ import annotations

import io
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.bollinger import (avg5, breakouts_by_date, breakouts_on,
                           energy_phase, history_pct_rank, level_of,
                           level_thresholds, merge_series, series_rows,
                           trend, weak_streak, weak_streak_note,
                           DIR_LABEL, LEVEL_LABEL)

log = logging.getLogger("bot.bollinger_board")

_KST = timezone(timedelta(hours=9))
_HOME = Path(os.environ.get("TRADINGAGENTS_HOME") or Path.home())
_SERIES_DIR = _HOME / ".tradingagents" / "bollinger"

MARKETS = ("KR", "US", "JP", "HK", "CN_A", "TW")

# 배치 크기·간격은 52주 신고저 스캐너(`finviz_client._compute_highlow_from`)에서
# 검증된 값 그대로. 이 보드는 3시간에 한 번 ~1,800종목을 도므로 그 스캐너
# (:00 슬롯 9,358종목)의 1/5 부하다.
_CHUNK = 120
_BATCH_GAP_SEC = 0.2
# 평소엔 3개월(≈60봉)이면 충분하다 — 밴드 20봉 + 5일 평균 + 늦은 봉 정정 창 25.
# 시계열이 비어 있는 첫 실행에만 1년치를 받아 백필한다.
_PERIOD_LIVE = "3mo"
_PERIOD_BACKFILL = "1y"
_BACKFILL_IF_ROWS_UNDER = 60
# 스캔이 유니버스의 이 비율에 못 미치면 **부분 스캔**으로 보고 저장하지 않는다.
# 부분 결과를 완전본으로 구우면 그날 값이 영구히 낮게 남는다(#280).
_MIN_SCAN_RATIO = 0.7
_TABLE_ROWS = 50           # 돌파 종목 표 표시 상한(초과분은 '외 N종목', #45)

# 유니버스 기본 크기. KR·US·CN_A 는 지수 구성종목이라 이 값이 상한이 아니라
# 사실상 그 지수의 크기이고, JP/HK/TW 는 그 시장에 검증된 지수 구성종목 원천이
# 없어 시총/거래대금 상위로 자른 값이다(계획서 §3.2).
_DEFAULT_CAP = {"KR": 400, "US": 600, "JP": 225, "HK": 225, "CN_A": 320,
                "TW": 225}


def _now_kst() -> datetime:
    return datetime.now(_KST)


def _universe_cap(market: str, default: int) -> int:
    """유니버스 상한 — `BOLLINGER_UNIVERSE_CAP_<MKT>` > `BOLLINGER_UNIVERSE_CAP`
    > 기본값. 52주 신고저(`intl_highlow._universe_cap`)와 **같은 규칙**이라
    운영자가 한 시장만 넓히고 나머지는 그대로 둘 수 있다."""
    for key in (f"BOLLINGER_UNIVERSE_CAP_{(market or '').upper()}",
                "BOLLINGER_UNIVERSE_CAP"):
        raw = (os.getenv(key) or "").strip()
        if raw:
            try:
                val = int(raw)
            except ValueError:
                log.warning("bollinger: %s=%r 정수 아님 — 무시", key, raw)
                continue
            if val > 0:
                return val
    return default


# ── KR 유니버스 ① KIS 종목 마스터파일 ──────────────────────────────────────
# 키·로그인 없이 **구성종목 + 시가총액 + 종목명**을 한 번에 주는 유일한 공식
# 원천이다(pykrx 는 KRX 로그인이 필요하고 실제로 `LOGOUT` 로 죽어 있던 이력이
# 있다 — #187c). 이 레포는 이미 KIS `idxcode.mst` 를 실측 파싱해 VKOSPI 코드를
# 확정한 선례가 있다(`naver_sector_client.py` _KIS_VKOSPI_IDX_CODE).
_KIS_URL = "https://new.real.download.dws.co.kr/common/master/{}_code.mst.zip"
_KIS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ⚠️ 아래 두 쌍은 KIS 공식 샘플(koreainvestment/open-trading-api ·
# `stocks_info/kis_kospi_code_mst.py`·`kis_kosdaq_code_mst.py`)의 `field_specs`·
# `part2_columns` 를 **기계로 복사한 것**이다. 손으로 다시 세지 말 것(#155) —
# 오프셋은 이 목록에서 계산하고, 아래 `_KIS_TAIL` 과 합이 다르면 import 시점에
# 즉시 실패한다(원천 서식이 바뀌면 조용히 어긋나는 대신 큰 소리로 죽는다).
_KOSPI_WIDTHS = [
    2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12,
    12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1,
]
_KOSPI_COLS = [
    '그룹코드', '시가총액규모', '지수업종대분류', '지수업종중분류', '지수업종소분류', '제조업', '저유동성',
    '지배구조지수종목', 'KOSPI200섹터업종', 'KOSPI100', 'KOSPI50', 'KRX', 'ETP',
    'ELW발행', 'KRX100', 'KRX자동차', 'KRX반도체', 'KRX바이오', 'KRX은행', 'SPAC',
    'KRX에너지화학', 'KRX철강', '단기과열', 'KRX미디어통신', 'KRX건설', 'Non1', 'KRX증권',
    'KRX선박', 'KRX섹터_보험', 'KRX섹터_운송', 'SRI', '기준가', '매매수량단위', '시간외수량단위',
    '거래정지', '정리매매', '관리종목', '시장경고', '경고예고', '불성실공시', '우회상장', '락구분', '액면변경',
    '증자구분', '증거금비율', '신용가능', '신용기간', '전일거래량', '액면가', '상장일자', '상장주수', '자본금',
    '결산월', '공모가', '우선주', '공매도과열', '이상급등', 'KRX300', 'KOSPI', '매출액', '영업이익',
    '경상이익', '당기순이익', 'ROE', '기준년월', '시가총액', '그룹사코드', '회사신용한도초과', '담보대출가능',
    '대주가능',
]
_KOSDAQ_WIDTHS = [
    2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21,
    2, 7, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1,
]
_KOSDAQ_COLS = [
    '증권그룹구분코드', '시가총액 규모 구분 코드 유가', '지수업종 대분류 코드', '지수 업종 중분류 코드',
    '지수업종 소분류 코드', '벤처기업 여부 (Y/N)', '저유동성종목 여부', 'KRX 종목 여부', 'ETP 상품구분코드',
    'KRX100 종목 여부 (Y/N)', 'KRX 자동차 여부', 'KRX 반도체 여부', 'KRX 바이오 여부',
    'KRX 은행 여부', '기업인수목적회사여부', 'KRX 에너지 화학 여부', 'KRX 철강 여부', '단기과열종목구분코드',
    'KRX 미디어 통신 여부', 'KRX 건설 여부', '(코스닥)투자주의환기종목여부', 'KRX 증권 구분',
    'KRX 선박 구분', 'KRX섹터지수 보험여부', 'KRX섹터지수 운송여부', 'KOSDAQ150지수여부 (Y,N)',
    '주식 기준가', '정규 시장 매매 수량 단위', '시간외 시장 매매 수량 단위', '거래정지 여부', '정리매매 여부',
    '관리 종목 여부', '시장 경고 구분 코드', '시장 경고위험 예고 여부', '불성실 공시 여부', '우회 상장 여부',
    '락구분 코드', '액면가 변경 구분 코드', '증자 구분 코드', '증거금 비율', '신용주문 가능 여부', '신용기간',
    '전일 거래량', '주식 액면가', '주식 상장 일자', '상장 주수(천)', '자본금', '결산 월', '공모 가격',
    '우선주 구분 코드', '공매도과열종목여부', '이상급등종목여부', 'KRX300 종목 여부 (Y/N)', '매출액',
    '영업이익', '경상이익', '단기순이익', 'ROE(자기자본이익률)', '기준년월', '전일기준 시가총액 (억)',
    '그룹사 코드', '회사신용한도초과여부', '담보대출가능여부', '대주가능여부',
]
# 공식 샘플이 `row[0:len(row)-228]` 로 자를 때 그 228 에는 **줄바꿈 1자가
# 포함**돼 있다(그래서 실제 고정폭 payload 는 227·221 이다). 아래 합 검사가
# 그 사실을 못박는다.
_KIS_TAIL = {"kospi": 227, "kosdaq": 221}
assert sum(_KOSPI_WIDTHS) == _KIS_TAIL["kospi"], "KOSPI 컬럼 폭 합 불일치"
assert sum(_KOSDAQ_WIDTHS) == _KIS_TAIL["kosdaq"], "KOSDAQ 컬럼 폭 합 불일치"
assert len(_KOSPI_WIDTHS) == len(_KOSPI_COLS)
assert len(_KOSDAQ_WIDTHS) == len(_KOSDAQ_COLS)

_KIS_SPEC = {
    "kospi": (_KOSPI_WIDTHS, _KOSPI_COLS, ".KS", "KOSPI200",
              "KOSPI200섹터업종", "그룹코드", "시가총액"),
    "kosdaq": (_KOSDAQ_WIDTHS, _KOSDAQ_COLS, ".KQ", "KOSDAQ150",
               "KOSDAQ150지수여부 (Y,N)", "증권그룹구분코드",
               "전일기준 시가총액 (억)"),
}


def _field_slice(widths, cols, name: str) -> tuple[int, int]:
    """컬럼명 → 고정폭 payload 안의 (시작, 끝). 이름이 없으면 KeyError.

    오프셋을 상수로 박아 두면 위 폭 목록을 갱신할 때 한쪽만 바뀌어 조용히
    어긋난다 — **이름으로 찾아 계산**한다(#38)."""
    idx = cols.index(name)
    start = sum(widths[:idx])
    return start, start + widths[idx]


def _kis_master_rows(book: str, *, raw: bytes | None = None) -> tuple[list, str]:
    """KIS 마스터파일 → 행 목록. (행, 사유) — 실패해도 예외를 올리지 않는다.

    행 = {code, ticker, name, group, member(bool), mcap_eok}.
    `raw` 를 주면 다운로드를 건너뛴다(테스트가 원천 바이트 모양을 그대로
    태우기 위한 것 — 픽스처는 원천이 실제로 보내는 모양이어야 한다 #155).
    """
    widths, cols, suffix, index_name, flag_col, group_col, mcap_col = \
        _KIS_SPEC[book]
    tail_len = _KIS_TAIL[book]
    if raw is None:
        try:
            import requests
            resp = requests.get(_KIS_URL.format(book), timeout=30,
                                headers={"User-Agent": _KIS_UA})
            if resp.status_code != 200:
                return [], f"HTTP {resp.status_code}"
            raw = resp.content
        except Exception as exc:                               # noqa: BLE001
            return [], f"{type(exc).__name__}: {exc}"
    try:
        import zipfile
        zf = zipfile.ZipFile(io.BytesIO(raw))
        names = [n for n in zf.namelist() if n.lower().endswith(".mst")]
        if not names:
            return [], f"zip 안에 .mst 없음(파일 {zf.namelist()[:3]})"
        text = zf.read(names[0]).decode("cp949", errors="replace")
    except Exception as exc:                                   # noqa: BLE001
        return [], f"압축/디코드 실패 {type(exc).__name__}: {exc}"

    f_flag = _field_slice(widths, cols, flag_col)
    f_group = _field_slice(widths, cols, group_col)
    f_mcap = _field_slice(widths, cols, mcap_col)
    rows: list = []
    short = 0
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if len(line) < tail_len + 22:
            if line.strip():
                short += 1
            continue
        head, tail = line[:len(line) - tail_len], line[-tail_len:]
        code = head[0:9].strip()
        if not code:
            continue
        flag = tail[f_flag[0]:f_flag[1]].strip()
        # KOSPI200 은 '섹터업종' 코드(0=미편입), KOSDAQ150 은 'Y/N' 이라
        # 판정이 다르다 — 원천이 그렇게 준다(우리 규약이 아니다).
        member = (flag not in ("", "0")) if book == "kospi" else (flag == "Y")
        try:
            mcap = float(tail[f_mcap[0]:f_mcap[1]].strip() or 0) or None
        except ValueError:
            mcap = None
        rows.append({
            "code": code, "ticker": f"{code}{suffix}",
            "name": head[21:].strip() or code,
            "group": tail[f_group[0]:f_group[1]].strip(),
            "index": index_name, "member": member, "mcap": mcap,
        })
    note = f"{len(rows):,}행"
    if short:
        note += f" · 형식 미달 {short}행"
    return rows, note


def _rung_kis(*, raw: dict | None = None) -> tuple[dict, str, dict]:
    """① KIS 마스터파일 → {ticker: {...}}. (유니버스, 사유, 진단)

    ⚠️ 지수 구성종목이면 그 자체로 보통주다 — `그룹코드=='ST'` 같은 2차 필터를
    걸면 그 값에 대한 **내 가정**이 틀렸을 때 최고 원천이 통째로 죽는다(#50).
    걸지 않고 대신 그룹코드 분포를 진단에 찍어 다음 라운드가 보게 한다."""
    uni: dict = {}
    diag: dict = {"books": {}}
    notes = []
    for book in ("kospi", "kosdaq"):
        rows, note = _kis_master_rows(
            book, raw=(raw or {}).get(book) if raw else None)
        members = [r for r in rows if r["member"]]
        groups: dict = {}
        for r in members:
            groups[r["group"]] = groups.get(r["group"], 0) + 1
        diag["books"][book] = {
            "rows": len(rows), "members": len(members), "note": note,
            "groups": groups,
            "sample": [(r["code"], r["name"], r["mcap"]) for r in members[:3]],
            "max_mcap": max((r["mcap"] or 0) for r in members) if members else 0,
        }
        notes.append(f"{book} {note} · 구성종목 {len(members)}")
        for r in members:
            uni[r["ticker"]] = {"name": r["name"], "mcap": r["mcap"],
                                "index": r["index"]}
    # 두 파일의 시총 단위가 같은지 **값으로** 대조한다 — KOSDAQ 은 컬럼명이
    # '(억)' 이라 단위가 확정이고, KOSPI 최대가 KOSDAQ 최대보다 작으면 단위가
    # 갈린 것이다(같은 표에 섞이므로 정렬이 통째로 거짓말이 된다, #34).
    ks = diag["books"].get("kospi", {}).get("max_mcap") or 0
    kq = diag["books"].get("kosdaq", {}).get("max_mcap") or 0
    if ks and kq and ks < kq:
        diag["unit_warn"] = (f"KOSPI 최대 시총 {ks:,.0f} < KOSDAQ {kq:,.0f} — "
                             "두 파일의 시총 단위가 다를 수 있다(정렬 주의)")
        log.warning("bollinger: %s", diag["unit_warn"])
    return uni, " · ".join(notes), diag


# ── KR 유니버스 ② 네이버 지수 구성종목(무키) ───────────────────────────────
# 지수 코드 표기도 **호스트·경로**도 확실하지 않아 후보를 전부 시도하고 각
# 후보가 무엇을 돌려줬는지 진단에 남긴다 — 이름을 추측해 하나만 걸면 그 표기가
# 아닐 때 원천이 통째로 죽는다(#25 능력은 실측).
#
# 2026-09-09 VM 실측: `m.stock.naver.com` 판은 네 표기 모두 **HTTP 400 · 빈
# 본문**이라 이 단이 죽어 있었다(#42a 폴백은 안 타면 죽은 줄도 모른다). 레포의
# 다른 모든 네이버 호출이 `api.stock.naver.com` 을 쓰므로 그 호스트를 후보에
# 넣었으나 **아직 실측 전이다** — 어느 후보가 답하는지는 `--why` 가 원문으로
# 말한다(#12 재기 전에 단정하지 않는다). 파싱은 기존 JSON 봉투만 하고, 다른
# 모양이 오면 원문을 남겨 다음 라운드가 실측으로 파서를 짠다(#155).
_NAVER_INDEX_CODES = {
    "KOSPI200": (("KPI200", "KOSPI200"), ".KS"),
    "KOSDAQ150": (("KQ150", "KOSDAQ150"), ".KQ"),
}
_NAVER_INDEX_URLS = (
    "https://api.stock.naver.com/index/{code}/enrollStocks"
    "?page={page}&pageSize={size}",
    "https://m.stock.naver.com/api/index/{code}/enrollStocks"
    "?page={page}&pageSize={size}",
)


def _body_note(resp) -> str:
    """응답 본문을 **사실대로** 한 줄로. 빈 본문은 '못 받음'과 다른 사실이므로
    그렇게 말한다 — '없음'만 말하는 진단은 추측을 부른다(#82). 형식·길이를
    같이 적어 다음 라운드가 파서를 실측으로 짠다(#109·#155)."""
    try:
        body = resp.text or ""
    except Exception as exc:                                   # noqa: BLE001
        return f"본문 읽기 실패: {type(exc).__name__}: {exc}"
    ctype = (resp.headers.get("Content-Type") or "?").split(";")[0].strip()
    if not body:
        return f"빈 본문 (Content-Type {ctype})"
    return f"{ctype} {len(body)}자: {body[:300]}"


def _naver_index_stocks(code: str, size: int = 100,
                        max_pages: int = 10) -> tuple[list, dict]:
    """(항목 목록, 진단). 후보 URL 을 순서대로 시도하고 **각 후보가 무엇을
    돌려줬는지**(상태·형식·길이·원문 앞머리)를 전부 남긴다 — 0건일 때
    '원천에 없음'과 '우리가 못 읽음'을 가르는 유일한 재료다(#109·#143).
    항목을 얻은 후보에서 멈춘다: 뒤 후보는 순손실 요청이다."""
    diag: dict = {"http": None, "pages": 0, "raw": "", "keys": [], "tried": []}
    try:
        import requests
    except Exception as exc:                                   # noqa: BLE001
        diag["raw"] = f"requests import 실패: {exc}"
        return [], diag
    for tmpl in _NAVER_INDEX_URLS:
        host = tmpl.split("/")[2]
        attempt: dict = {"host": host, "http": None, "body": "", "items": 0}
        items: list = []
        for page in range(1, max_pages + 1):
            url = tmpl.format(code=code, page=page, size=size)
            try:
                r = requests.get(url, timeout=12,
                                 headers={"User-Agent": _KIS_UA,
                                          "Referer": f"https://{host}/"})
            except Exception as exc:                           # noqa: BLE001
                attempt["body"] = f"{type(exc).__name__}: {exc}"
                break
            attempt["http"] = r.status_code
            if r.status_code != 200:
                attempt["body"] = _body_note(r)
                break
            try:
                d = r.json()
            except Exception:                                  # noqa: BLE001
                attempt["body"] = _body_note(r)
                break
            got = _naver_items(d)
            if not got:
                attempt["body"] = _body_note(r)
                break
            items += got
            diag["pages"] = page
            if len(got) < size:
                break
        attempt["items"] = len(items)
        diag["tried"].append(attempt)
        diag["http"] = attempt["http"]
        diag["raw"] = diag["raw"] or attempt["body"]
        if items:
            diag["keys"] = sorted(items[0].keys())[:20]
            diag["host"] = host
            return items, diag
    return [], diag


def _naver_items(payload) -> list:
    """네이버 응답 봉투에서 항목 리스트를 꺼낸다. 봉투가 여러 벌이라
    (`result.stocks` / 최상위 `stocks` / `stockList`) 구조로 훑는다."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    if payload.get("isSuccess") is False:
        return []
    for holder in (payload, payload.get("result") or {}):
        if not isinstance(holder, dict):
            continue
        for key in ("stocks", "stockList", "enrollStocks", "list", "items"):
            v = holder.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _naver_pick(item: dict) -> tuple[str, str, float | None]:
    """항목 → (6자리 코드, 이름, 시총 억). 키 이름이 원천마다 달라 후보를
    순서대로 본다 — 못 찾으면 코드가 빈 문자열이라 호출부가 0건으로 처리하고
    진단이 실제 키 목록을 찍는다(추측으로 파서를 짜지 않는다, #12)."""
    code = ""
    for k in ("itemCode", "code", "stockCode", "symbolCode", "reutersCode"):
        v = str(item.get(k) or "").strip()
        if v and v[:6].isdigit():
            code = v[:6]
            break
    name = ""
    for k in ("stockName", "name", "itemName", "stockNameEng"):
        v = str(item.get(k) or "").strip()
        if v:
            name = v
            break
    mcap = None
    for k in ("marketValue", "marketCap", "marketValueKrw"):
        try:
            raw = float(str(item.get(k) or "").replace(",", ""))
        except (TypeError, ValueError):
            continue
        if raw > 0:
            # 네이버는 원 단위로 준다 — 억으로 맞춰야 KIS 마스터(억)와 같은
            # 표에서 정렬이 뜻을 갖는다(#34).
            mcap = raw / 1e8 if raw > 1e7 else raw
            break
    return code, name, mcap


def _rung_naver() -> tuple[dict, str, dict]:
    """② 네이버 지수 구성종목. (유니버스, 사유, 진단)"""
    uni: dict = {}
    diag: dict = {"indices": {}}
    notes = []
    for index_name, (codes, suffix) in _NAVER_INDEX_CODES.items():
        best: list = []
        best_code = ""
        detail: dict = {}
        for code in codes:
            items, d = _naver_index_stocks(code)
            detail[code] = {"http": d["http"], "items": len(items),
                            "keys": d["keys"], "raw": d["raw"][:500],
                            "tried": d.get("tried") or []}
            if len(items) > len(best):
                best, best_code = items, code
        diag["indices"][index_name] = {"tried": detail, "picked": best_code,
                                       "count": len(best)}
        for it in best:
            code, name, mcap = _naver_pick(it)
            if not code:
                continue
            uni[f"{code}{suffix}"] = {"name": name or code, "mcap": mcap,
                                      "index": index_name}
        notes.append(f"{index_name} {len(best)}({best_code or '—'})")
    return uni, " · ".join(notes), diag


# ── KR 유니버스 ③ pykrx · ④ 시총 폴백 ──────────────────────────────────────
def _kr_bulk() -> dict:
    """`{6자리코드: {종목명, 시가총액(억), ...}}` — 스크리너 벌크 재사용."""
    try:
        from bot.stock_screener import _fetch_kr_bulk
        return _fetch_kr_bulk() or {}
    except Exception as exc:                                   # noqa: BLE001
        log.debug("bollinger: KR 벌크 실패: %s", exc)
        return {}


def _rung_pykrx() -> tuple[dict, str, dict]:
    """③ pykrx 지수 구성종목. 함수 존재는 **실호출 가능 여부**로 판정한다 —
    이름이 `__init__` 에 보인다고 부를 수 있는 건 아니다(#151·#25)."""
    diag: dict = {}
    try:
        from bot.pykrx_client import krx_login_ready
        if not krx_login_ready():
            return {}, "KRX 자격증명 없음", {"login": False}
        from pykrx import stock
    except Exception as exc:                                   # noqa: BLE001
        return {}, f"pykrx 없음({type(exc).__name__})", {"import": False}
    fn = getattr(stock, "get_index_portfolio_deposit_file", None)
    diag["hasattr"] = callable(fn)
    if not callable(fn):
        return {}, "get_index_portfolio_deposit_file 없음(설치본)", diag
    bulk = _kr_bulk()
    uni: dict = {}
    notes = []
    for index_name, idx_code, suffix in (("KOSPI200", "1028", ".KS"),
                                         ("KOSDAQ150", "2203", ".KQ")):
        try:
            codes = list(fn(idx_code) or [])
        except Exception as exc:                               # noqa: BLE001
            notes.append(f"{index_name} 실패({type(exc).__name__})")
            continue
        notes.append(f"{index_name} {len(codes)}")
        for c in codes:
            code = str(c).zfill(6)
            b = bulk.get(code) or {}
            uni[f"{code}{suffix}"] = {"name": b.get("종목명") or code,
                                      "mcap": b.get("시가총액"),
                                      "index": index_name}
    diag["counts"] = notes
    return uni, " · ".join(notes), diag


def _rung_mcap() -> tuple[dict, str, dict]:
    """④ 시총 상위 200/150 폴백 — 지수 구성종목이 **아니다**. 화면·진단이
    그렇게 밝힌다(조용히 다른 모집단을 쓰면 라벨이 거짓말이다 #34·#43)."""
    bulk = _kr_bulk()
    if not bulk:
        return {}, "KR 벌크 없음", {}
    try:
        from bot.intl_highlow import _kr_full_universe
        tickers, names = _kr_full_universe()
    except Exception as exc:                                   # noqa: BLE001
        return {}, f"전종목 목록 실패({type(exc).__name__})", {}
    if not tickers:
        return {}, "전종목 목록 비어 있음", {}
    uni: dict = {}
    for suffix, index_name, top in ((".KS", "KOSPI200(폴백)", 200),
                                    (".KQ", "KOSDAQ150(폴백)", 150)):
        rows = []
        for t in tickers:
            if not t.endswith(suffix):
                continue
            b = bulk.get(t[:6]) or {}
            mc = b.get("시가총액")
            if not mc or not (b.get("거래량") or 0):
                continue
            rows.append((mc, t, names.get(t) or t))
        rows.sort(reverse=True)
        for mc, t, nm in rows[:top]:
            uni[t] = {"name": nm, "mcap": mc, "index": index_name}
    return uni, f"시총상위 {len(uni)}", {"bulk": len(bulk)}


# ── KR 유니버스 사다리 ─────────────────────────────────────────────────────
# 각 단은 '요구를 충족했나'로 판정한다 — '실패했나'로 잡으면 200 종목을
# 기대한 자리에 2,600 종목이 와도 통과한다(#136·#191·#25 반대 증거).
_KR_EXPECT = {".KS": (190, 210), ".KQ": (140, 160)}
_KR_RUNGS = (("KIS 마스터파일", _rung_kis),
             ("네이버 지수 구성종목", _rung_naver),
             ("pykrx 지수 구성종목", _rung_pykrx),
             ("시총상위 폴백", _rung_mcap))


def kr_universe_ok(uni: dict) -> tuple[bool, str]:
    """(요구 충족 여부, 사유). KOSPI200·KOSDAQ150 이 각각 기대 범위 안이어야
    '구성종목'이다."""
    counts = {suf: sum(1 for t in uni or {} if t.endswith(suf))
              for suf in _KR_EXPECT}
    bad = [f"{suf} {n}개(기대 {_KR_EXPECT[suf][0]}~{_KR_EXPECT[suf][1]})"
           for suf, n in counts.items()
           if not (_KR_EXPECT[suf][0] <= n <= _KR_EXPECT[suf][1])]
    label = " · ".join(f"{suf} {n}" for suf, n in counts.items())
    return (not bad), (" / ".join(bad) if bad else label)


_KR_UNIVERSE_VER = 1
_KR_UNIVERSE_TTL = 7 * 86400


def _kr_universe_sig() -> str:
    """유니버스 캐시 지문 — **파서를 정하는 것들**의 해시.

    버전 상수를 손으로 올리는 방식은 이 레포에서 여섯 번 졌다(#18·#21b·#95·
    #124·#198·#216). 컬럼 폭·컬럼명·지수 코드·기대 개수가 바뀌면 다음 조회에서
    자동으로 다시 받는다(규율이 아니라 구조로, #119). 원천 **URL** 도
    파서를 정한다 — 후보를 바꿔 놓고 옛 캐시를 서빙하면 fix 가 화면에 한 글자도
    안 닿는다(2026-09-09 네이버 후보 교체 때 실제로 빠뜨렸다가 셀프리뷰가 잡음)."""
    import hashlib
    blob = repr((_KR_UNIVERSE_VER, _KOSPI_WIDTHS, _KOSPI_COLS, _KOSDAQ_WIDTHS,
                 _KOSDAQ_COLS, _KIS_TAIL, _NAVER_INDEX_CODES,
                 _NAVER_INDEX_URLS, _KR_EXPECT))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _kr_cache_path() -> Path:
    return _SERIES_DIR / "kr_universe.json"


def _kr_cache_read() -> tuple[dict, dict] | None:
    try:
        p = _kr_cache_path()
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        if (d.get("sig") != _kr_universe_sig()
                or time.time() - float(d.get("ts") or 0) > _KR_UNIVERSE_TTL):
            return None
        uni = d.get("uni") or {}
        if not uni:
            return None
        return uni, dict(d.get("meta") or {}, cached=True)
    except Exception as exc:                                   # noqa: BLE001
        log.debug("bollinger: KR 유니버스 캐시 읽기 실패: %s", exc)
    return None


def _kr_cache_write(uni: dict, meta: dict) -> None:
    try:
        _SERIES_DIR.mkdir(parents=True, exist_ok=True)
        p = _kr_cache_path()
        tmp = p.with_suffix(".json.tmp")
        # 진단(diag)은 크고 화면이 안 쓰므로 캐시엔 안 남긴다.
        slim = {k: v for k, v in meta.items() if k != "rungs"}
        tmp.write_text(json.dumps({"ts": time.time(),
                                   "sig": _kr_universe_sig(),
                                   "uni": uni, "meta": slim},
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:                                   # noqa: BLE001
        log.debug("bollinger: KR 유니버스 캐시 저장 실패: %s", exc)


def kr_universe(*, probe: bool = False,
                use_cache: bool = True) -> tuple[dict, dict]:
    """KOSPI200 + KOSDAQ150. (유니버스, 메타)

    `probe=True` 면 **모든 단을 시도해** 나란히 기록한다(진단 전용) — 평소엔
    첫 충족 단에서 멈춘다. 그래야 '① 이 되면 ②③④ 는 부르지 않는다'는 반대
    증거가 성립한다(#191·#25)."""
    if use_cache and not probe:
        hit = _kr_cache_read()
        if hit:
            return hit
    meta: dict = {"rungs": [], "rung": None, "source": "", "reason": "",
                  "label": "KOSPI200 + KOSDAQ150"}
    picked: dict = {}
    tried: dict = {}
    for n, (name, fn) in enumerate(_KR_RUNGS, start=1):
        if picked and not probe:
            break
        try:
            uni, note, diag = fn()
        except Exception as exc:                               # noqa: BLE001
            uni, note, diag = {}, f"{type(exc).__name__}: {exc}", {}
        ok, why = kr_universe_ok(uni)
        meta["rungs"].append({"n": n, "name": name, "count": len(uni),
                              "ok": ok, "note": note, "why": why,
                              "diag": diag})
        tried[n] = uni
        if ok and not picked:
            picked, meta["rung"], meta["source"] = uni, n, name
    if probe and meta["rungs"][:2] and len(meta["rungs"]) >= 2:
        a, b = tried.get(1) or {}, tried.get(2) or {}
        if a and b:
            meta["overlap"] = len(set(a) & set(b))
    if not picked:
        meta["reason"] = ("지수 구성종목 조회 실패 — "
                          + " / ".join(f"{r['name']}: {r['why']}"
                                       for r in meta["rungs"]))
        return {}, meta
    if meta["rung"] == 4:
        meta["label"] = "시총상위 200/150 (지수 구성종목 조회 실패 폴백)"
    if use_cache and not probe:
        _kr_cache_write(picked, meta)
    return picked, meta


# ── 시장별 유니버스 ────────────────────────────────────────────────────────
# ⚠️ 시장 게이트가 아니다 — **원천이 시장별**이라 여기만 갈린다. 계산·판정·
# 저장·감사는 아래에서 6시장 완전히 같은 경로를 탄다(UNIVERSAL CHANGES ONLY).
def _universe(market: str) -> tuple[dict, dict]:
    """(유니버스 {ticker: {name, mcap, index}}, 메타 {label, reason, ...})."""
    m = (market or "").upper()
    if m == "KR":
        uni, meta = kr_universe()
        meta["count"] = len(uni)
        return uni, meta
    meta: dict = {"label": "", "reason": "", "rung": None, "source": ""}
    try:
        if m == "US":
            from bot.finviz_client import _sp500_names, _us_universe_robust
            tickers = list(_us_universe_robust() or [])
            names = _sp500_names() or {}
            meta["label"] = "S&P 500"
        elif m in ("JP", "HK"):
            from bot.intl_universe import full_universe_names
            from bot.stock_screener import (_get_hk_universe,
                                            _get_jp_universe)
            tickers = list((_get_jp_universe() if m == "JP"
                            else _get_hk_universe()) or [])
            names = full_universe_names(m) or {}
            meta["label"] = "시총상위 (공식 상장목록 기준)"
        elif m == "CN_A":
            from bot.akshare_client import list_csi300_500
            names = list_csi300_500(symbols=("000300",)) or {}
            tickers = list(names)
            meta["label"] = "CSI 300 구성종목"
            if not tickers:
                from bot.stock_screener import _get_cn_universe
                tickers = list(_get_cn_universe() or [])
                names = {}
                meta["label"] = "시총상위 (CSI300 조회 실패 폴백)"
        elif m == "TW":
            from bot.stock_screener import _get_tw_universe
            tickers, names = _get_tw_universe()
            tickers, names = list(tickers or []), names or {}
            # 시총이 아니라 **거래대금** 상위다 — 라벨이 그렇게 말해야 한다(#34).
            meta["label"] = "거래대금 상위 (TWSE/TPEx)"
        else:
            return {}, {"label": "", "reason": f"미지원 시장 {market}"}
    except Exception as exc:                                   # noqa: BLE001
        return {}, {"label": "", "reason": f"{type(exc).__name__}: {exc}"}
    if not tickers:
        # JP/HK 는 빈 목록의 갈래가 코드로 확정돼 있다(`_get_jp_universe`:
        # 공식 상장목록 조회 실패 **또는** 100종목 이하) — 그만큼만 말한다(#82·#165).
        why = "유니버스 원천이 빈 목록"
        if m in ("JP", "HK"):
            why += (" — 공식 상장목록(JPX/HKEX) 조회 실패 또는 100종목 이하, "
                    f"다음 3시간 주기에 재시도 · `--why {m}` 로 갈래 확인")
        return {}, {"label": meta["label"], "reason": why}
    cap = _universe_cap(m, _DEFAULT_CAP.get(m, 300))
    if len(tickers) > cap:
        # ⚠️ 원천이 준 **순서 그대로** 앞 N 개다. JP/HK/TW/CN 은 이미 시총·
        # 거래대금 내림차순이라 곧 상위 N 이지만, US(S&P500 CSV)는 알파벳
        # 순이라 상한을 기본값 아래로 내리면 임의의 N 이 된다. 기본값은 원천
        # 크기보다 크므로 평소엔 이 분기를 안 탄다 — 내릴 때만 주의(.env.example).
        tickers = tickers[:cap]
        meta["capped"] = cap
    meta["count"] = len(tickers)
    return ({t: {"name": names.get(t) or t, "mcap": None, "index": ""}
             for t in tickers}, meta)


def universe_label(market: str, meta: dict, scanned=None) -> str:
    """카드에 찍는 유니버스 한 줄 — 정의·개수·스캔수·(KR)어느 단에서 왔는지.

    유니버스 정의가 시장마다 다르므로 **화면이 항상 말해야** 사용자가 시장 간
    숫자를 잘못 비교하지 않는다(#34)."""
    parts = [meta.get("label") or "—"]
    n = meta.get("count")
    if n:
        parts.append(f"{n:,}종목" if scanned is None
                     else f"{scanned:,}/{n:,} 스캔")
    if meta.get("source"):
        parts.append(meta["source"])
    if meta.get("capped"):
        parts.append(f"상한 {meta['capped']:,}")
    return " · ".join(parts)


# ── 수집 ───────────────────────────────────────────────────────────────────
def _download_closes(tickers: list, period: str) -> tuple[dict, dict]:
    """(종가 시계열 {ticker: Series}, 통계). 52주 신고저 스캐너와 같은 벌크
    패턴 — 120 배치 · 배치 간 0.2초 · 누락분 40 배치 1회 재시도.

    ⚠️ 신선도 가드는 **전 배치를 받은 뒤** 한 번에 건다. 배치마다 걸면 정지
    종목만 모인 재시도 배치가 자기들끼리 '최근'을 만들어 가드가 무력해진다."""
    stats = {"universe": len(tickers), "batches": 0, "batch_fail": 0,
             "retry_batches": 0, "stale_skipped": 0, "short": [],
             "bad_shape": 0, "period": period}
    raw: dict = {}
    try:
        import yfinance as yf
    except Exception as exc:                                   # noqa: BLE001
        stats["error"] = f"yfinance 없음({type(exc).__name__})"
        return {}, stats

    def _proc(df, chunk):
        multi = getattr(df.columns, "nlevels", 1) > 1
        lv0 = set(df.columns.get_level_values(0)) if multi else set()
        for tk in chunk:
            try:
                if multi:
                    if tk not in lv0:
                        continue
                    sub = df[tk]
                else:
                    sub = df
                # 행 단위로 거른다 — 컬럼별 dropna 는 길이를 갈라 놓아
                # `.iloc[-1]` 이 서로 다른 날짜를 가리킬 수 있다.
                sub = sub.dropna(subset=["Close"])
                if len(sub) == 0:
                    continue
                raw[tk] = sub["Close"].astype(float)
            except Exception:                                  # noqa: BLE001
                # 조용히 넘기면 '원천이 안 줬다'와 '우리가 못 읽었다'가
                # 구별되지 않는다(#12·#143) — 세어서 진단이 말하게 한다.
                stats["bad_shape"] += 1
                continue

    def _batch(items, size):
        nonlocal stats
        for i in range(0, len(items), size):
            chunk = items[i:i + size]
            try:
                df = yf.download(chunk, period=period, interval="1d",
                                 group_by="ticker", threads=True,
                                 progress=False, auto_adjust=False)
            except Exception as exc:                           # noqa: BLE001
                log.warning("bollinger: 배치 다운로드 실패: %s", exc)
                stats["batch_fail"] += 1
                continue
            stats["batches"] += 1
            if df is None or getattr(df, "empty", True):
                stats["batch_fail"] += 1
                continue
            time.sleep(_BATCH_GAP_SEC)
            _proc(df, chunk)

    _batch(list(tickers), _CHUNK)
    dropped = [t for t in tickers if t not in raw]
    if dropped:
        # yfinance 벌크가 일부 티커를 통째로 빠뜨리는 간헐 현상 — 누락분만
        # 작은 배치로 1회 재시도(전수 재스캔이 아니라 부하 유계).
        before = stats["batches"]
        _batch(dropped, 40)
        stats["retry_batches"] = stats["batches"] - before
    stats["received"] = len(raw)
    if not raw:
        stats["kept"] = 0
        stats["ratio"] = 0.0
        return {}, stats

    all_dates = sorted({d for s in raw.values() for d in s.index})
    recent = set(all_dates[-2:])
    out: dict = {}
    for tk, s in raw.items():
        if len(s) < 20:
            stats["short"].append(tk)
            continue
        # 정지·상장폐지·휴면 종목은 마지막 봉이 몇 주 전이라, 그때 찍은 값이
        # 매일 '오늘의 돌파'로 새어 나온다. 최근 2세션 안에 봉이 있어야 한다.
        if s.index[-1] not in recent:
            stats["stale_skipped"] += 1
            continue
        out[tk] = s
    stats["kept"] = len(out)
    stats["ratio"] = (len(out) / len(tickers)) if tickers else 0.0
    return out, stats


# ── 시계열 저장 ────────────────────────────────────────────────────────────
def series_path(market: str) -> Path:
    return _SERIES_DIR / f"series_{(market or '').upper()}.json"


def load_series(market: str) -> dict:
    """저장된 일별 계수 {날짜(str): {count,new,scanned,basis}}. graceful {}."""
    p = series_path(market)
    try:
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return {str(k): v for k, v in d.items() if isinstance(v, dict)}
    except Exception as exc:                                   # noqa: BLE001
        log.warning("bollinger: %s 시계열 읽기 실패: %s", market, exc)
    return {}


def save_series(market: str, series: dict) -> None:
    """원자적 쓰기 — 읽는 쪽이 **찢어진 JSON** 을 보면 안 된다(#280)."""
    p = series_path(market)
    try:
        _SERIES_DIR.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("bollinger: %s 시계열 저장 실패: %s", market, exc)


# ── 시총·종목명 ────────────────────────────────────────────────────────────
def _overlay(rows: list, market: str, uni: dict) -> None:
    """돌파 종목 행에 시총·표시명을 채운다(in-place).

    시총 단위는 전 시장 **억(현지통화, US 는 억$)** 으로 통일한다 —
    `highlow_render.fmt_mcap` 이 그 규약으로 표기하므로 그대로 재사용한다(#38).
    """
    m = (market or "").upper()
    for r in rows:
        info = uni.get(r["ticker"]) or {}
        r["name"] = info.get("name") or r.get("name") or r["ticker"]
        if info.get("mcap") is not None:
            r["mcap"] = info["mcap"]
    if m == "KR":
        return                       # KIS 마스터가 이름·시총을 이미 줬다
    try:
        from bot.finviz_client import (_backfill_korean_names, _fetch_mcaps,
                                       _naver_worldstock_overlay,
                                       _persist_mcap_overlay, fast_info_ok)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("bollinger: overlay 헬퍼 import 실패: %s", exc)
        return
    if m in ("US", "JP", "HK", "CN_A"):
        # 52주 신고저와 **같은 규약**: HK/JP 는 네이버 종목명까지 쓰고,
        # US/CN 은 기존 이름 파이프라인을 보존한다.
        _naver_worldstock_overlay(rows, m, overlay_name=(m in ("HK", "JP")))
    elif m == "TW":
        # 네이버 worldstock 이 TW 를 안 줘서 yfinance fast_info → 실패 시
        # 직전 성공값(디스크 persist)으로 채운다.
        if fast_info_ok():
            mcaps = _fetch_mcaps([r["ticker"] for r in rows]) or {}
            for r in rows:
                mc = mcaps.get(r["ticker"])
                if mc:
                    r["mcap"] = round(float(mc) / 1e8, 2)
        _persist_mcap_overlay(rows, "TW")
    _backfill_korean_names(rows, m)


def _sorted_rows(rows: list) -> list:
    """시총 내림차순 — 시총을 못 받은 행은 **맨 뒤**(0 으로 섞지 않는다)."""
    return sorted(rows, key=lambda r: (r.get("mcap") is None,
                                       -(r.get("mcap") or 0.0)))


# ── 빌드 ───────────────────────────────────────────────────────────────────
def _empty(market: str, reason: str, meta: dict | None = None) -> dict:
    """비어 있는 시장 카드 — **사유를 갈래 이름으로** 말한다(#43·#82)."""
    return {"market": market, "reason": reason, "rows": [], "chart": [],
            "universe_label": universe_label(market, meta or {}),
            "universe_meta": meta or {}}


def build_market(market: str, *, write: bool = True,
                 enrich: bool = True) -> dict:
    """한 시장의 payload. 조각 실패는 그 시장만 비운다(다른 시장은 그대로).

    `write=False` — 시계열 파일을 안 쓴다. `enrich=False` — 시총·한글명
    overlay 를 건너뛴다. ⚠️ 그 overlay 는 네이버·yfinance 를 치고 비-KR 은
    `_backfill_korean_names` 를 거쳐 **과금되는 LLM 번역**까지 부른다
    (그 비용이 운영 원장 → 대시보드 비용 카드에 쌓인다, #30·#284·#312).
    진단(`--why`)은 둘 다 끄고 부른다 — 표의 이름·시총을 보고하지 않으므로
    잃는 정보가 없다(#264 진단은 상태를 바꾸지 않는다)."""
    m = (market or "").upper()
    uni, umeta = _universe(m)
    if not uni:
        return _empty(m, umeta.get("reason") or "유니버스 조회 실패", umeta)
    try:
        from bot.finviz_client import yf_paused
        if yf_paused():
            return _empty(m, "yfinance 정지(YF_PAUSE) — 다음 사이클에 재시도",
                          umeta)
    except Exception:                                          # noqa: BLE001
        pass

    stored = load_series(m)
    period = (_PERIOD_LIVE if len(stored) >= _BACKFILL_IF_ROWS_UNDER
              else _PERIOD_BACKFILL)
    closes, scan = _download_closes(list(uni), period)
    if not closes:
        # 갈래를 이름으로 부른다 — '전멸' 과 '받았지만 전부 걸러졌다' 는
        # 처방이 다르다(#82). 재 놓고 안 쓰면 없는 것과 같다(#123).
        if scan.get("error"):
            reason = scan["error"]
        elif not scan.get("received"):
            reason = (f"다운로드 전멸(배치 {scan['batches']}건 성공 · "
                      f"{scan['batch_fail']}건 실패)")
        else:
            reason = (f"받은 {scan['received']}종목이 전부 걸러짐 — "
                      f"20봉 미만 {len(scan.get('short') or [])} · "
                      f"정지·휴면 {scan.get('stale_skipped')} · "
                      f"응답 모양 이상 {scan.get('bad_shape')}")
        return _empty(m, reason, umeta)

    counts = breakouts_by_date(closes)
    if not counts:
        return _empty(m, "밴드를 만들 수 있는 종목이 없음(20봉 미만)", umeta)

    # 완결 / 잠정 — 오늘 장이 아직 안 끝났으면 마지막 봉은 **부분봉**이라
    # 시계열에 넣지 않는다. 넣으면 그날 값이 장중 스냅샷으로 굳는다(#40).
    try:
        from bot import market_timing as _mt
        expected, _grace = _mt._expected_session(m)
        closed = _mt._market_closed_today(m)
    except Exception as exc:                                   # noqa: BLE001
        log.debug("bollinger: %s 세션 판정 실패: %s", m, exc)
        expected, closed = None, None
    d_last = max(counts)
    # ⚠️ **하나만** 걷어내면 안 된다 — 캘린더와 원천이 어긋나 기대 세션보다
    # 뒤인 봉이 둘 이상일 수 있고, 그러면 나머지가 '완결'로 기록된다.
    future = sorted(d for d in counts if expected and d > expected)
    provisional = None
    if future:
        provisional = dict(counts[future[-1]])
        provisional["date"] = future[-1]
        provisional["as_of"] = _now_kst().strftime("%H:%M")
        provisional["held"] = len(future)
        for d in future:
            counts.pop(d, None)
    if not counts:
        return _empty(m, f"완결 세션 없음(마지막 봉 {d_last} · 기대 {expected})",
                      umeta)

    partial = scan.get("ratio", 0.0) < _MIN_SCAN_RATIO
    newest = max(counts)
    # ⚠️ 'live' 는 **그날 실제로 관측한** 행에만 붙인다. 3개월 창을 받으면
    # 과거 40여 세션이 같이 계산되는데 그건 **오늘 유니버스로 되짚은 것**
    # (생존편향)이라 live 라고 부르면 화면의 '옅은 막대 = 백필' 설명이
    # 거짓말이 된다(#43·#165). 한 번 live 로 관측된 행은 나중 실행이 다시
    # 계산해도 live 로 남는다 — 그 보존은 `merge_series` 가 한다.
    fresh = {d: dict(v, basis=("live" if d == newest else "backfill"))
             for d, v in counts.items()}
    merged = merge_series(stored, fresh, partial=partial)
    if write and not partial:
        save_series(m, merged)

    rows_hist = series_rows(merged)
    asof = rows_hist[-1]["date"] if rows_hist else newest
    latest = rows_hist[-1] if rows_hist else {}
    scanned = latest.get("scanned")
    a5, a5_reason = avg5(rows_hist)
    tr = trend(rows_hist)
    lv, lv_reason = level_of(a5, scanned)
    strong_th, weak_th = level_thresholds(scanned)
    rank, rank_reason = history_pct_rank(rows_hist)
    streak = weak_streak(rows_hist)

    # ⚠️ 기준일이 이번 수집 창 **밖**이면(부분 스캔이라 저장분을 그대로 쓰는
    # 경우 등) 표를 만들 수 없다 — 그걸 '돌파 0건'이라 적으면 재지 않은 것을
    # 사실로 말하는 것이다(#54·#43).
    rows_reason = ("" if asof in counts else
                   f"{asof} 은 이번 수집 창 밖이라 종목 표를 만들지 못했습니다")
    if partial and not rows_reason:
        # ⚠️ 카드의 개수는 **저장된 전수 스캔**에서 오고 표는 이번 **부분**
        # 스캔에서 나온다 — 나란히 놓으면 "17종목" 위에 9행짜리 표가 앉는다
        # (#33·#45 총계와 소계는 같은 모집단이어야 한다).
        rows_reason = ("이번 수집이 부분 스캔이라 종목 표를 만들지 "
                       "않았습니다(카드 수치는 저장된 전수 스캔 기준)")
    want = ([asof] if asof in counts else []) + (
        [provisional["date"]] if provisional else [])
    detail = breakouts_on(closes, want) if want else {}
    table = detail.get(asof) or []
    prov_rows = (detail.get(provisional["date"]) or []) if provisional else []
    if enrich:
        _overlay(table, m, uni)
        _overlay(prov_rows, m, uni)
    table, prov_rows = _sorted_rows(table), _sorted_rows(prov_rows)

    a5_hist = _avg5_chart(rows_hist)
    return {
        "market": m, "asof": asof, "reason": "",
        "count": latest.get("count"), "new": latest.get("new"),
        "scanned": scanned,
        "pct": ((latest.get("count") or 0) / scanned * 100.0
                if scanned else None),
        "avg5": a5, "avg5_reason": a5_reason,
        "trend": tr, "level": lv, "level_reason": lv_reason,
        "strong_th": strong_th, "weak_th": weak_th,
        "phase": energy_phase(lv, tr.get("dir")),
        "pct_rank": rank, "pct_rank_reason": rank_reason,
        "streak": streak, "streak_note": weak_streak_note(streak),
        "chart": a5_hist[-120:],
        "rows": table[:_TABLE_ROWS], "rows_total": len(table),
        "rows_reason": rows_reason,
        "provisional": (dict(provisional, rows=prov_rows[:_TABLE_ROWS],
                             rows_total=len(prov_rows))
                        if provisional else None),
        "partial": partial, "scan": scan, "closed": closed,
        "expected": expected,
        "universe_label": universe_label(m, umeta, scanned=scan.get("kept")),
        "universe_meta": umeta,
    }


def _avg5_chart(rows: list) -> list:
    """차트용 [{date, count, avg5, basis}] — 카드의 5일선과 **같은 함수**에서
    파생시킨다(복제하면 차트와 카드가 갈라진다, #38)."""
    from bot.bollinger import avg5_series
    a5 = avg5_series(rows)
    return [{"date": r.get("date"), "count": r.get("count"),
             "new": r.get("new"), "scanned": r.get("scanned"), "avg5": a5[i],
             "basis": r.get("basis") or "live"}
            for i, r in enumerate(rows)]


def build_all() -> dict:
    """6시장 payload. 한 시장이 실패해도 나머지는 그대로 만든다."""
    out: dict = {}
    for m in MARKETS:
        try:
            out[m] = build_market(m)
        except Exception as exc:                               # noqa: BLE001
            log.warning("bollinger: %s 빌드 실패: %s", m, exc)
            out[m] = _empty(m, f"빌드 예외 {type(exc).__name__}: {exc}")
    return out


# ── 렌더 ───────────────────────────────────────────────────────────────────
# ⚠️ 여기서 쓰는 클래스는 **전부 이 번들에 정의**한다. 대시보드에 같은 이름이
# 있어도 이 페이지는 그 번들을 안 쓴다 — 정의 없이 쓰면 각주가 본문 크기로
# 떠서 표보다 커 보인다(#201·#273).
_BB_CSS = """
<style>
.bb-phase{font-size:15px;font-weight:700}
.bb-note{font-size:11.5px;color:var(--muted);line-height:1.55;margin-top:3px}
.bb-warn{font-size:12px;color:#f59e0b;font-weight:600;margin-top:6px}
.bb-new{display:inline-block;margin-left:5px;font-size:10.5px;font-weight:700;
 color:var(--accent)}
.bb-flag{font-size:16px;margin-right:5px}
.bb-empty{font-size:12.5px;color:var(--muted);padding:10px 2px}
.bb-links{font-size:12px;margin-top:10px}
.bb-links a{color:var(--accent);text-decoration:none}
.bb-links a:hover{text-decoration:underline}
.num{text-align:right;font-variant-numeric:tabular-nums}
</style>
"""

_MARKET_META = {
    "KR": ("🇰🇷", "한국"), "US": ("🇺🇸", "미국"), "JP": ("🇯🇵", "일본"),
    "HK": ("🇭🇰", "홍콩"), "CN_A": ("🇨🇳", "중국 A주"), "TW": ("🇹🇼", "대만"),
}

# 한 유니버스에 거래소가 둘 이상 섞이는 시장은 티커 접미사로 어느 거래소인지
# 적는다(사용자 2026-09-09 "코스피인지 코스닥인지"). KR 만 고치면 같은 구조의
# TW(.TW/.TWO)·CN(.SS/.SZ)이 조용히 남는다(§UNIVERSAL) — 접미사 → 라벨 한 표.
# 단일 거래소 시장(US·JP·HK)은 표에 없어 자연히 빈칸이다(시장 게이트 없음).
_EXCHANGE_TAG = {
    ".KS": "코스피", ".KQ": "코스닥",
    ".TW": "TWSE", ".TWO": "TPEx",
    ".SS": "상해", ".SZ": "심천",
}


def exchange_tag(ticker: str) -> str:
    """'000660.KS' → '코스피'. 접미사가 표에 없으면 빈 문자열(지어내지 않는다)."""
    t = str(ticker or "")
    dot = t.rfind(".")
    return _EXCHANGE_TAG.get(t[dot:], "") if dot >= 0 else ""


def _kick_name_fill(pairs: list) -> None:
    """미캐시 종목명 백그라운드 워밍 — 52주 신고저 보드의 그 함수(#38 복제 금지).
    ⚠️ 과금되는 LLM 경로다(#312) — 테스트·진단은 `_NAME_KICK` 을 갈아끼운다."""
    from bot.highlow_render import _kick_name_fill as _hl_kick
    _hl_kick(pairs)


_NAME_KICK = _kick_name_fill


def korean_names(rows: list, market: str) -> int:
    """비-KR/US 표의 종목명을 **신고저·급등락 보드와 같은 캐시**(names_kr.json,
    `translate_names_kr cache_only`)로 한글화한다(사용자 2026-09-09 "기존 신고가
    신저가/급등급락처럼"). 렌더타임이라 시계열에 구워진 옛 행도 따라온다(#270).
    캐시에 없는 종목은 원문을 두고 백그라운드로 워밍 — 렌더는 네트워크·LLM 을
    한 번도 기다리지 않는다. 반환 = 한글로 바뀐 행 수."""
    if market not in ("CN_A", "TW", "HK"):
        return 0
    pairs = [(r.get("ticker"), r.get("name")) for r in rows or []
             if r.get("ticker")]
    if not pairs:
        return 0
    try:
        from bot.chart_translate import translate_names_kr
        knm = translate_names_kr(pairs, cache_only=True) or {}
    except Exception as exc:                                   # noqa: BLE001
        log.debug("bollinger: 한글명 캐시 조회 실패: %s", exc)
        return 0
    hit = 0
    for r in rows:
        k = knm.get(r.get("ticker"))
        if k and k != r.get("name"):
            r["name"] = k
            hit += 1
    miss = [p for p in pairs if p[0] not in knm]
    if miss:
        try:
            _NAME_KICK(miss)
        except Exception as exc:                               # noqa: BLE001
            log.debug("bollinger: 한글명 워밍 킥 실패: %s", exc)
    return hit


def _n(v, digits: int = 1) -> str:
    """숫자 한 칸 — 원시 float 를 화면에 그대로 흘리지 않는다(규칙 10)."""
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _session_badge(market: str, asof: str) -> str:
    """기준일 옆 세션 배지 — 시장타이밍의 판정을 **그대로** 쓴다. 복제하면
    같은 날짜를 두 화면이 다르게 말한다(#38·Breadth `_session_badge` 와 동일)."""
    if not (market and asof):
        return ""
    try:
        from bot.market_timing import _idx_stale
        return _idx_stale(str(market), str(asof))
    except Exception as exc:                                   # noqa: BLE001
        log.debug("bollinger: 세션 배지 실패: %s", exc)
        return ""


def _trend_line(d: dict) -> str:
    tr = d.get("trend") or {}
    if not tr.get("dir"):
        return f"판정 불가 — {tr.get('reason') or '재료 없음'}"
    bits = [f"{_n(tr.get('d5'))} 종목"]
    if tr.get("pct5") is not None:
        bits.append(f"({_n(tr.get('pct5'))}%)")
    bits.append("vs 5세션 전")
    if tr.get("d20") is not None:
        bits.append(f"· {_n(tr.get('d20'))} 종목 vs 20세션 전")
    bits.append(f"· 문턱 ±{_n(tr.get('th'))}")
    return " ".join(bits)


def _level_line(d: dict) -> str:
    strong, weak = d.get("strong_th"), d.get("weak_th")
    if strong is None:
        return d.get("level_reason") or "판정 불가"
    base = f"강세 ≥ {strong} · 약세 ≤ {weak}"
    if (d.get("market") or "") == "KR":
        return base + " (원문 예시 기준)"
    return base + " (원문 예시 20/10 을 유니버스 크기로 환산 — 검증된 기준 아님)"


def _rows_table(rows: list, total: int, scanned, title: str) -> str:
    import html as _h
    if not rows:
        return (f"<div class='bb-empty'>{_h.escape(title)}: 상단 돌파 종목 없음"
                f" (스캔 {_n(scanned, 0)}종목)</div>")
    body = "".join(
        "<tr>"
        f"<td>{i + 1}</td>"
        f"<td>{_h.escape(str(r.get('name') or r.get('ticker')))}"
        f"<div class='bb-note'>{_h.escape(str(r.get('ticker')))}"
        f"{' · ' + _h.escape(exchange_tag(r.get('ticker'))) if exchange_tag(r.get('ticker')) else ''}"
        "</div></td>"
        f"<td class='num'>{_n(r.get('close'), 2)}</td>"
        f"<td class='num'>{_pct_cell(r.get('pct_chg'))}</td>"
        f"<td class='num'>{_n(r.get('upper'), 2)}</td>"
        f"<td class='num'>{_n(r.get('over_pct'), 2)}%</td>"
        f"<td class='num'>{_h.escape(_mcap_cell(r))}</td>"
        f"<td>{_new_cell(r)}</td>"
        "</tr>"
        for i, r in enumerate(rows))
    more = (f"<div class='bb-note'>표시 {len(rows)}종목 · 전체 {total}종목"
            f" (시총 상위만 표시)</div>" if total > len(rows) else "")
    return (f"<div class='bb-note'>{_h.escape(title)} — 시총 내림차순</div>"
            "<div class='tbl-wrap'><table><tr><th>#</th><th>종목</th>"
            "<th class='num'>종가</th><th class='num'>등락</th>"
            "<th class='num'>상단밴드</th><th class='num'>돌파폭</th>"
            "<th class='num'>시총</th><th></th></tr>"
            f"{body}</table></div>{more}")


def _new_cell(r: dict) -> str:
    """🆕 = 전일엔 밴드 안이었던 **신규** 돌파. 상태 계수(count)와 뜻이 다르므로
    표가 둘을 구분해 보여 준다(§1)."""
    return "<span class='bb-new'>🆕</span>" if r.get("new") else ""


def _pct_cell(v) -> str:
    if v is None:
        return "<span class='flat'>—</span>"
    cls = "pos" if v >= 0 else "neg"
    return f"<span class='{cls}'>{v:+.2f}%</span>"


def _mcap_cell(r: dict) -> str:
    """시총 표기 — 52주 신고저 보드와 **같은 포맷터**(억 단위 규약, #38)."""
    try:
        from bot.highlow_render import fmt_mcap
        return fmt_mcap(r.get("mcap"), r.get("_market") or "")
    except Exception:                                          # noqa: BLE001
        return _n(r.get("mcap"), 0)


def _market_section(d: dict) -> str:
    import html as _h
    if not d:
        return ""
    m = d.get("market", "")
    flag, kname = _MARKET_META.get(m, ("", m))
    for r in d.get("rows") or []:
        r["_market"] = m
    for r in ((d.get("provisional") or {}).get("rows") or []):
        r["_market"] = m
    korean_names(d.get("rows") or [], m)
    korean_names(((d.get("provisional") or {}).get("rows") or []), m)
    phase = d.get("phase") or "판정 불가"
    head = (f"<div class='panel-title'><span class='bb-flag'>{flag}</span>"
            f"{_h.escape(kname)} — <span class='bb-phase'>"
            f"{_h.escape(phase)}</span></div>")
    if d.get("reason"):
        return (f"<div class='panel'>{head}"
                f"<div class='bb-empty'>{_h.escape(str(d['reason']))}</div>"
                f"<div class='bb-note'>{_h.escape(d.get('universe_label') or '')}"
                "</div></div>")
    badge = _session_badge(m, d.get("asof"))
    sub = (f"기준일 {_h.escape(str(d.get('asof') or '—'))}"
           f"{' ' + badge if badge else ''}"
           f" · {_h.escape(d.get('universe_label') or '')}")
    lvl = LEVEL_LABEL.get(d.get("level") or "", "판정 불가")
    dirl = DIR_LABEL.get((d.get("trend") or {}).get("dir") or "", "판정 불가")
    rank = (f"{_n(d.get('pct_rank'), 0)}%" if d.get("pct_rank") is not None
            else "—")
    stats = (
        "<div class='stat-grid'>"
        f"<div class='stat'><div class='k'>오늘 돌파</div>"
        f"<div class='v'>{_n(d.get('count'), 0)}종목</div>"
        f"<div class='bb-note'>스캔 {_n(d.get('scanned'), 0)}종목 중 "
        f"{_n(d.get('pct'))}% · 신규 {_n(d.get('new'), 0)}</div></div>"
        f"<div class='stat'><div class='k'>5일 평균</div>"
        f"<div class='v'>{_n(d.get('avg5'))}</div>"
        f"<div class='bb-note'>{_h.escape(d.get('avg5_reason') or '')}"
        f"{_h.escape(_level_line(d))}</div></div>"
        f"<div class='stat'><div class='k'>추이 · 수준</div>"
        f"<div class='v'>{_h.escape(dirl)} / {_h.escape(lvl)}</div>"
        f"<div class='bb-note'>{_h.escape(_trend_line(d))}</div></div>"
        f"<div class='stat'><div class='k'>이력 백분위</div>"
        f"<div class='v'>{rank}</div>"
        f"<div class='bb-note'>"
        f"{_h.escape(d.get('pct_rank_reason') or '이 시장 자기 이력 대비')}"
        f" · 약세 연속 {d.get('streak', 0)}세션</div></div>"
        "</div>")
    warn = ""
    if d.get("partial"):
        sc = d.get("scan") or {}
        warn += ("<div class='bb-warn'>⚠️ 부분 스캔 "
                 f"{_n(sc.get('kept'), 0)}/{_n(sc.get('universe'), 0)} — "
                 "이번 값은 시계열에 기록하지 않았습니다</div>")
    prov = d.get("provisional")
    if prov:
        warn += ("<div class='bb-warn'>🕒 잠정(장중 "
                 f"{_h.escape(str(prov.get('as_of') or ''))} KST 기준) "
                 f"{_n(prov.get('count'), 0)}종목 — "
                 f"{_h.escape(str(prov.get('date')))} 종가가 확정되면 "
                 "기록합니다</div>")
    if d.get("streak_note"):
        warn += (f"<div class='note'>{_h.escape(d['streak_note'])}</div>")
    chart = (f"<div class='chartbox'><canvas id='bbc-{_h.escape(m)}'></canvas>"
             "</div>")
    table = (f"<div class='bb-empty'>{_h.escape(d['rows_reason'])}</div>"
             if d.get("rows_reason") else
             _rows_table(d.get("rows") or [], d.get("rows_total") or 0,
                         d.get("scanned"), f"{d.get('asof')} 종가 기준 돌파 종목"))
    if prov:
        table += _rows_table(prov.get("rows") or [],
                             prov.get("rows_total") or 0, prov.get("scanned"),
                             f"{prov.get('date')} 잠정(장중) 돌파 종목")
    links = ("<div class='bb-links'>함께 볼 것: "
             "<a href='market_timing.html'>🚦 시장타이밍</a> · "
             "<a href='breadth_strategy.html'>🧭 Breadth전략</a></div>")
    return (f"<div class='panel'>{head}<div class='sub'>{sub}</div>"
            f"{stats}{warn}{chart}{table}{links}</div>")


_BB_JS = """
function bbChart(id, rows, strong, weak){
  var el = document.getElementById(id);
  if(!el || typeof Chart === 'undefined' || !rows || !rows.length) return;
  var labels = rows.map(function(r){return r.date;});
  var bars = rows.map(function(r){return r.count;});
  var line = rows.map(function(r){return r.avg5;});
  var colors = rows.map(function(r){
    return r.basis === 'backfill' ? 'rgba(59,120,231,.28)' : 'rgba(59,120,231,.75)';});
  var ds = [
    {type:'bar', label:'일별 돌파 종목수', data:bars, backgroundColor:colors,
     borderWidth:0, order:2},
    {type:'line', label:'5일 평균', data:line, borderColor:'#f59e0b',
     backgroundColor:'transparent', borderWidth:2, pointRadius:0, order:1}
  ];
  if(strong != null) ds.push({type:'line', label:'강세 문턱 '+strong,
    data:labels.map(function(){return strong;}), borderColor:'#16a34a',
    borderDash:[5,4], borderWidth:1, pointRadius:0, order:0});
  if(weak != null) ds.push({type:'line', label:'약세 문턱 '+weak,
    data:labels.map(function(){return weak;}), borderColor:'#ef4444',
    borderDash:[5,4], borderWidth:1, pointRadius:0, order:0});
  new Chart(el.getContext('2d'), {data:{labels:labels, datasets:ds},
    options:{responsive:true, maintainAspectRatio:false,
      plugins:{legend:{labels:{boxWidth:10, font:{size:10}}}},
      scales:{x:{ticks:{maxTicksLimit:10, font:{size:10}}},
              y:{beginAtZero:true, ticks:{font:{size:10}}}}}});
}
"""


def _js(obj) -> str:
    """`<script>` 안에 박는 JSON — `</` 를 이스케이프해 데이터가 태그를 닫고
    나가지 못하게 한다. 생성 JS 는 파이썬이 문법을 안 봐주므로(#26) 회귀가
    `node --check` 로 파싱까지 태운다."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def render_page(data: dict, now=None) -> str:
    """bollinger.html — 6시장 카드 + 일별 차트 + 돌파 종목 표."""
    import html as _h

    from bot.fred_boards import _BOARD_CSS, _CHARTJS_NAME, _NAV, _theme_head
    ts = (now or _now_kst()).strftime("%Y-%m-%d %H:%M KST")
    sections = "".join(_market_section(data.get(m) or {}) for m in MARKETS)
    if not sections:
        sections = ("<div class='panel'><div class='bb-empty'>데이터를 받지 "
                    "못했습니다 — 유니버스·yfinance 조회를 확인하세요.</div></div>")
    calls = "".join(
        f"bbChart('bbc-{m}', {_js((data.get(m) or {}).get('chart') or [])},"
        f" {_js((data.get(m) or {}).get('strong_th'))},"
        f" {_js((data.get(m) or {}).get('weak_th'))});"
        for m in MARKETS if (data.get(m) or {}).get("chart"))
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bollinger</title>{_BOARD_CSS}{_BB_CSS}
{_theme_head()}
</head><body><div class="wrap">
{_NAV}
<h1>🔋 <em>Bollinger</em> — 상단 돌파 종목수로 보는 시장 에너지</h1>
<div class="sub">각 종목에 볼린저 밴드(20일, 2σ)를 씌워 <b>종가가 상단밴드 위에서
마감한 종목 수</b>를 매 거래일 셉니다 · 갱신 {_h.escape(ts)} · 3시간 주기 재계산
(값은 거래일당 한 번, 종가가 확정된 뒤 바뀝니다)</div>
<details class="guide"><summary>ℹ️ 이 보드 읽는 법</summary>
<b>선행 지표가 아닙니다</b> — 상단 돌파 종목수는 <b>지금 시장의 에너지</b>를
나타내는 지표입니다(원문 주의사항). 앞날을 맞히는 값이 아닙니다.<br>
<b>밴드</b> — 20일 이동평균 ±2σ. 종가의 약 95.4% 가 이 안에 들어오므로 상단 밖
마감은 드문 사건(대략 2.3%)이고, 실적·재료로 강하게 오른 종목에서 나옵니다.<br>
<b>절댓값보다 추이</b> — 원문이 가장 강조한 점입니다. 그래서 이 보드의 판정
1순위는 <b>5일 평균선의 방향</b>이고, 수준(문턱)은 그다음입니다.
국면 이름은 <b>수준 × 추이</b>로 정합니다.<br>
<b>이력 백분위</b> — 그 시장 자기 이력에서 오늘 5일선이 어디쯤인지. 유니버스
크기와 무관해 시장마다 뜻이 같습니다(60세션 이상 쌓여야 판정합니다).<br>
<b>위험 관리</b> — 원문은 "5일 평균 10 이하가 두 달 연속"일 때 현금 비중
60~70% 를 <b>예로</b> 듭니다. 이 보드는 비중을 처방하지 않고 약세가 몇
세션 연속인지(거래일 기준 세션 수)만 사실로 적으며, 그 조건을 채웠을 때만
원문을 인용합니다.<br>
<b>해석이 들어간 지점 넷</b> —
① 돌파는 <b>종가 &gt; 상단</b>(그날 밴드 밖에서 마감한 상태)으로 셉니다.
🆕 는 전일엔 밴드 안이었던 신규 돌파입니다. HTS 는 표준편차 정의가 다를 수 있어
종목수가 한두 개 차이 날 수 있습니다(우리가 재 보지는 않았습니다).
② 문턱 20/10 은 원문이 밝힌 <b>예시</b>이고 코스피200+코스닥150(350종목)
기준입니다 — 다른 시장은 유니버스 크기로 환산했을 뿐 <b>검증된 기준이
아닙니다</b>. 시장 간 종목수를 직접 비교하지 마세요(유니버스 크기·구성이
다릅니다).
③ 차트에서 <b>옅은 막대</b>는 백필 구간입니다 — 오늘 유니버스로 과거를 계산한
것이라 생존편향이 있습니다.
④ 대만 유니버스는 시총이 아니라 <b>거래대금</b> 상위입니다.
⑤ 티커 옆 <b>코스피/코스닥·TWSE/TPEx·상해/심천</b>은 접미사로 정한 거래소이고,
중국·대만·홍콩 종목명은 52주 신고저 보드와 <b>같은 한글명 캐시</b>를 씁니다 —
아직 번역되지 않은 종목은 원문 그대로 두고 다음 갱신에서 채웁니다.<br>
자동 신호이므로 참고용 — 확정 판단 금지.
</details>
{sections}
<div class="footer">볼린저 상단 돌파 종목수 — 참고용(투자 판단 아님) · NOAH</div>
</div>
<script src="{_CHARTJS_NAME}"></script>
<script>{_BB_JS}
{calls}</script>
</body></html>"""


def regenerate() -> None:
    """bollinger.html 재생성. 실패해도 기존 파일 유지(graceful)."""
    from bot.dashboard import ARCHIVE_ROOT
    try:
        from bot.fred_boards import _ensure_chartjs
        _ensure_chartjs()
    except Exception as exc:                                   # noqa: BLE001
        log.warning("bollinger: chart.js 벤더링 실패: %s", exc)
    try:
        html = render_page(build_all())
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "bollinger.html").write_text(html, encoding="utf-8")
        log.info("bollinger: 페이지 재생성 완료")
    except Exception as exc:                                   # noqa: BLE001
        log.warning("bollinger: 재생성 실패: %s", exc)


# ── 진단 CLI ───────────────────────────────────────────────────────────────
# ⚠️ **읽기 전용**이다 — `build_market(write=False)` 로 화면이 쓰는 그 경로를
# 그대로 태우되 시계열 파일은 건드리지 않는다. 진단이 자기가 읽을 신호를
# 오염시키면 다음 라운드가 통째로 거짓이 된다(#30·#264·#283). 그리고 판정을
# 여기서 재구현하면 화면과 갈라지므로(#35·#169) 제품 함수만 부른다.
_WHY_VER = 1
_RUN_HINT = "cd ~/stock && .venv/bin/python -m bot.bollinger_board --why KR"


def _p(s: str = "") -> None:
    print(s, flush=True)


def _why_universe_kr() -> None:
    """KR 사다리 4단을 **전부** 시도해 나란히 찍는다 — 어느 단이 왜 안 됐는지
    한 번에 보여야 다음 라운드가 엉뚱한 데를 고치지 않는다(#82·#109)."""
    _uni, meta = kr_universe(probe=True, use_cache=False)
    for r in meta["rungs"]:
        mark = "✅" if r["ok"] else "❌"
        _p(f"   {'①②③④'[r['n'] - 1]} {r['name']:<18} {r['note']}  "
           f"{mark} {r['why']}")
        diag = r.get("diag") or {}
        for book, b in (diag.get("books") or {}).items():
            _p(f"      {book}: 그룹코드 {b.get('groups')} · 표본 "
               f"{b.get('sample')}")
        if diag.get("unit_warn"):
            _p(f"      ⚠️ {diag['unit_warn']}")
        for idx, info in (diag.get("indices") or {}).items():
            for code, t in (info.get("tried") or {}).items():
                _p(f"      {idx}/{code}: HTTP {t.get('http')} · "
                   f"항목 {t.get('items')} · 키 {t.get('keys')}")
                for a in (t.get("tried") or []):
                    _p(f"        {a.get('host')}: HTTP {a.get('http')} · "
                       f"항목 {a.get('items')} · {a.get('body') or '—'}")
                if not (t.get("tried") or t.get("items")) and t.get("raw"):
                    _p(f"        원문: {t['raw'][:300]}")
    if "overlap" in meta:
        n = meta["overlap"]
        _p(f"   ①∩② {n}종목 "
           f"{'✅ 교차 확인' if n >= 190 else '❓ 겹침이 적다 — 한쪽이 다른 것을 본다'}")
    _p(f"   채택: {'①②③④'[meta['rung'] - 1] + ' ' + meta['source']}"
       if meta.get("rung") else f"   채택 실패 — {meta.get('reason')}")


def _why_universe_intl(m: str) -> list[str]:
    """JP/HK 유니버스가 비었을 때 **어느 층**에서 비었는지 — 화면이 쓰는 그
    경로(`_get_jp_universe` → `full_universe` → JPX/HKEX HTTP)를 층별로 되짚는다
    (#82 갈래는 이름으로 · #35 감사는 화면 경로를). 원천 원문·예외를 그대로
    싣는다 — '빈 목록'만 말하면 운영자가 짐작한다."""
    out: list[str] = []
    try:
        from bot import intl_universe as iu
    except Exception as exc:                                   # noqa: BLE001
        return [f"intl_universe import 실패: {type(exc).__name__}: {exc}"]
    try:
        from bot.finviz_client import _CACHE_DIR, cache_age_sec
        name = f"full_universe_{m}_v2.json"
        age = cache_age_sec(name)
        out.append(f"공식 상장목록 7일 캐시: {_CACHE_DIR / name} "
                   f"({'없음' if age is None else f'{age / 3600:.1f}시간 전 기록'})")
    except Exception as exc:                                   # noqa: BLE001
        out.append(f"캐시 경로 확인 실패: {type(exc).__name__}: {exc}")
    try:
        full = iu.full_universe(m)
        out.append(f"full_universe({m}) → {len(full):,}종목"
                   f"{' (100 이하 → 유니버스로 안 쓴다)' if len(full) <= 100 else ''}")
    except Exception as exc:                                   # noqa: BLE001
        out.append(f"full_universe({m}) 예외: {type(exc).__name__}: {exc}")
        full = []
    if len(full) <= 100:
        spec = iu._SPEC.get(m)
        url = spec[0] if spec else "?"
        try:
            raw = iu._http_get(url)
            out.append(f"원천 HTTP: {url} → {len(raw):,}바이트 수신"
                       " (받았는데 파싱이 100종목 이하면 파서/서식 문제)")
        except Exception as exc:                               # noqa: BLE001
            out.append(f"원천 HTTP 실패: {url} → {type(exc).__name__}: {exc}")
    return out


def _why(market: str) -> int:
    import sys

    from bot.scripts.probe_progress import stream_stdout
    stream_stdout()
    m = (market or "KR").upper()
    _p(f"🔋 bollinger_board --why v{_WHY_VER} · {m} · "
       "시계열 미기록 · 보강(과금) 미호출")
    _p(f"① 인터프리터: {sys.executable}")
    _p(f"   시계열: {series_path(m)} "
       f"({'있음' if series_path(m).exists() else '없음 — 첫 실행은 1년 백필'})")
    before = load_series(m)
    _p("")
    _p("② 유니버스")
    if m == "KR":
        _why_universe_kr()
    else:
        uni, meta = _universe(m)
        _p(f"   {universe_label(m, meta)} · {len(uni):,}종목"
           f"{' · ' + meta['reason'] if meta.get('reason') else ''}")
        if not uni and m in ("JP", "HK"):
            for line in _why_universe_intl(m):
                _p(f"   {line}")
    _p("")
    _p("③ 수집 — 화면이 쓰는 그 경로를 태웁니다(몇 분 걸립니다)")
    _p("   시계열 파일과 시총·한글명 보강(네이버·yfinance·LLM 번역)은 "
       "건너뜁니다 — 유니버스 캐시는 평소처럼 갱신됩니다")
    t0 = time.time()
    d = build_market(m, write=False, enrich=False)
    _p(f"   소요 {time.time() - t0:.1f}초")
    if d.get("reason"):
        _p(f"   ❌ {d['reason']}")
        _p(f"   유니버스: {d.get('universe_label')}")
        return 1
    sc = d.get("scan") or {}
    _p(f"   기간 {sc.get('period')} · 배치 {sc.get('batches')}건(실패 "
       f"{sc.get('batch_fail')}) · 누락 재시도 {sc.get('retry_batches')}배치")
    _p(f"   응답 모양 이상 {sc.get('bad_shape')}종목")
    _p(f"   스캔 {sc.get('kept')}/{sc.get('universe')} "
       f"({(sc.get('ratio') or 0) * 100:.1f}%) · 정지·휴면 제외 "
       f"{sc.get('stale_skipped')} · 20봉 미만 {len(sc.get('short') or [])}"
       f" · partial={d.get('partial')}")
    short = sc.get("short") or []
    if len(short) > (sc.get("universe") or 1) * 0.1:
        _p(f"   ⚠️ 20봉 미만이 10% 초과 — 표본: {short[:10]}")
    _p("")
    _p("④ 최근 10세션")
    _p("   date        count  new  scanned    pct    avg5   basis")
    for r in (d.get("chart") or [])[-10:]:
        sc_n = r.get("scanned")
        pct = (r.get("count") or 0) / sc_n * 100 if sc_n else None
        _p(f"   {r['date']}  {_n(r.get('count'), 0):>5}  {_n(r.get('new'), 0):>3}"
           f"  {_n(sc_n, 0):>7}  {_n(pct, 1):>5}  {_n(r.get('avg5')):>6}"
           f"   {r.get('basis')}")
    _p("")
    _p("⑤ 완결 / 잠정")
    _p(f"   기준일 {d.get('asof')} · 마지막 완결 세션 {d.get('expected')} · "
       f"현지 {'마감' if d.get('closed') else '장중/개장전' if d.get('closed') is False else '판정 불가'}"
       f" {_session_badge(m, d.get('asof')) or ''}")
    if d.get("provisional"):
        pv = d["provisional"]
        _p(f"   🕒 잠정 {pv.get('date')} {pv.get('count')}종목 — 기록하지 않음")
    else:
        _p("   ✅ 잠정 봉 없음(마지막 봉이 완결 세션 이하)")
    _p("")
    _p("⑥ 판정")
    _p(f"   국면 {d.get('phase') or '판정 불가'} · 수준 "
       f"{LEVEL_LABEL.get(d.get('level') or '', '판정 불가')} "
       f"({_level_line(d)}) · 추이 "
       f"{DIR_LABEL.get((d.get('trend') or {}).get('dir') or '', '판정 불가')} "
       f"({_trend_line(d)})")
    _p(f"   오늘 {d.get('count')}종목 · 5일 평균 {_n(d.get('avg5'))} · "
       f"이력 백분위 {_n(d.get('pct_rank'), 0)}"
       f"{' (' + d['pct_rank_reason'] + ')' if d.get('pct_rank_reason') else '%'}"
       f" · 약세 연속 {d.get('streak')}세션")
    _p(f"   돌파 종목 표 {len(d.get('rows') or [])}행 / 전체 "
       f"{d.get('rows_total')}종목")
    after = load_series(m)
    _p("")
    _p(f"✅ 시계열 불변 확인 — {len(before)}행 → {len(after)}행"
       f"{' (변화 없음)' if before == after else ' ⚠️ 변했다!'}")
    return 0 if before == after else 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description=f"🔋 Bollinger 보드 진단. 사용법: {_RUN_HINT}")
    ap.add_argument("--why", nargs="?", const="KR", metavar="MARKET",
                    help="시장 하나를 읽기 전용으로 진단 (KR/US/JP/HK/CN_A/TW)")
    ap.add_argument("--regenerate", action="store_true",
                    help="bollinger.html 재생성(운영 경로와 동일)")
    args = ap.parse_args(argv)
    if args.why is not None:
        return _why(args.why)
    if args.regenerate:
        regenerate()
        return 0
    print(f"할 일을 고르세요. 예: {_RUN_HINT}")
    return 2


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
