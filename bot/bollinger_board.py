"""🔋 Bollinger 보드 — 볼린저 밴드 상단 돌파 종목수로 보는 시장 에너지.

전략 출처: 사용자 2026-09-09 캡처("볼린저 밴드 상단 돌파 종목수를 활용한 시장
에너지 분석"). 설계·근거 전량은 `docs/plans/bollinger_board_plan.md`.

한 줄 요약 — 시장의 각 종목에 볼린저 밴드(20, 2σ)를 씌우고 **종가가 상단밴드
위에서 마감한 종목 수**를 매 거래일 세어 시계열로 쌓는다. 원문이 "절댓값보다
추이가 중요하다"고 못박았으므로 판정 1순위는 5일선의 방향이고, 원문 문턱
(20/10)은 예시로만 쓴다. 산식·판정은 전부 `bot/bollinger.py`(순수)에 있고 이
모듈은 **유니버스·수집·저장·렌더·진단**만 한다.

시장: KR·US·JP·HK·CN_A·TW — 계산·판정·저장·감사 경로는 6시장 **완전 동일**하고
시장 게이트가 없다. 다른 것은 두 원천뿐이다(원천이 시장별이라서) — 유니버스
원천(정의는 **카드 라벨이 항상 말한다**, #34)과 원천 응답에 기대 완결 세션이
없을 때 그 하루 종가를 채우는 벌크 원천(`_SESSION_FILL`, 현재 KR=KRX 만 — 채웠으면
표 위 `🧩` 줄이 말한다, #326).
"""
from __future__ import annotations

import io
import json
import logging
import re as _re
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.bollinger import (PHASE, _date_key, avg5, avg5_extremes, breakouts_by_date, breakouts_on,
                           count_extremes, coverage_by_date, prune_sparse_rows, sparse_dates,
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
# 돌파 종목 표는 **조건에 맞는 종목을 전부** 싣는다(사용자 2026-09-09 "조건에
# 맞는건 가능한 다 나오게"). 상한은 어느 유니버스보다 커야 한다 — 회귀가
# `_TABLE_ROWS >= max(_DEFAULT_CAP)` 를 강제하므로 유니버스를 키우면 여기도 같이
# 올려야 한다(#67 리터럴 대신 불변식). 초과분 '외 N종목' 표기는 안전망으로만(#45).
_TABLE_ROWS = 1000
# 차트가 그리는 세션 수 — 5일 평균 최저·최고 카드도 **같은 창**을 본다(#38·#51).
_CHART_ROWS = 120
# 돌파 종목 표를 날짜별로 저장해 두는 세션 수. 카드는 저장 시계열에서 오는데
# 표만 이번 실행의 원천에서 만들면 원천이 하루 늦는 날 카드는 21종목인데 표는
# 빈칸이 된다(2026-09-10 KR·JP 실측, #325) — 표도 마지막 거래일 기준으로 저장한다.
_ROWS_KEEP = 5

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


# 2026-09-10 VM 실측: `api.stock.naver.com` 이 KOSPI200·KQ150·KOSDAQ150 에
# `409 {"code":"StockConflict","message":"지수의 구성종목을 서비스하지 않는
# 지수입니다."}` 를 돌려준다 — **원천이 스스로 '안 준다'고 선언**한 것이라 우리가
# 고칠 게 없다. 그걸 매번 ❌ 로 찍으면 진짜 ❌ 를 가린다(#260) → 원천이 찍은
# 코드를 **구조로** 읽어 표식을 남기고 표시는 ⚠️ 로 내린다(#82·#19 문자열 냄새
# 맡기 금지). ① KIS 마스터가 채택되므로 화면엔 영향이 없다.
_NAVER_DECLINE_CODE = "StockConflict"


def _naver_declined(resp) -> bool:
    """원천이 '서비스하지 않는 지수' 라고 답했나 — 409 + 응답 JSON 의 `code`."""
    try:
        if int(resp.status_code) != 409:
            return False
        d = resp.json()
    except Exception:                                          # noqa: BLE001
        return False
    return isinstance(d, dict) and d.get("code") == _NAVER_DECLINE_CODE


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
                attempt["declined"] = _naver_declined(r)
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
        # 후보 하나라도 원천이 '안 준다'고 선언했으면 그 지수는 원천 미제공이다
        declined = any(a.get("declined") for c in detail.values()
                       for a in (c.get("tried") or []))
        diag["indices"][index_name] = {"tried": detail, "picked": best_code,
                                       "count": len(best), "declined": declined}
        for it in best:
            code, name, mcap = _naver_pick(it)
            if not code:
                continue
            uni[f"{code}{suffix}"] = {"name": name or code, "mcap": mcap,
                                      "index": index_name}
        notes.append(f"{index_name} {len(best)}({best_code or '—'})")
    # 지수 **전부**가 선언했을 때만 단 전체를 원천 미제공으로 본다 — 하나만
    # 선언했으면 나머지는 코드 표기 문제일 수 있어 여전히 우리 것(❌)이다.
    idx = diag["indices"]
    diag["source_declined"] = bool(idx) and all(i["declined"] for i in idx.values())
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
            # 52주 신고저 보드와 **같은 경로**(intl_universe.full_universe →
            # 시총상위 캡). 옛 판은 스크리너의 `_get_jp_universe` 를 거쳤는데 그
            # 함수는 결과를 자기 7일 캐시(jp_n225.json)에 굳혀서, 만료 캐시로
            # 살린 목록이 다음 실행부터 **원천을 안 물은 채** 서빙됐다 — stale
            # 깃발이 안 서고 화면이 "공식 상장목록 기준" 이라고만 말했다(2026-09-09
            # VM 실측: 9일 전 목록인데 캐시 문구가 사라짐. #43·#306 두 번째 캐시
            # 층이 첫 층의 사실을 지운다). intl_universe 의 7일 캐시가 있어 평소
            # 비용은 같고, 원천이 죽어 있을 땐 3시간마다 다시 묻는다(그래야
            # 목록 페이지 탐색·복구도 돈다).
            from bot.intl_universe import full_universe, full_universe_names
            from bot.stock_screener import _cap_by_liq
            full = list(full_universe(m) or [])
            tickers = _cap_by_liq(full, m) if len(full) > 100 else []
            names = full_universe_names(m) or {}
            meta["label"] = "시총상위 (공식 상장목록 기준)"
            from bot.intl_universe import stale_hours
            st = stale_hours(m)
            if st is not None:
                # 원천이 죽어 만료 캐시로 버티는 중 — 화면이 말한다(#43·#306).
                meta["stale_hours"] = st
                meta["label"] += (f" · 상장목록 {st / 24:.0f}일 전 캐시"
                                  "(원천 조회 실패 — 다음 주기 재시도)")
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


# ── 기대 세션 보강 ─────────────────────────────────────────────────────────
# 야후가 KR·JP 에서 종가 봉을 하루 늦게 주거나 저녁에 줬다가 자정 넘어 빼는
# 날(#301·#310·#325 실측), 기대 완결 세션이 이번 응답에 없어 카드는 "21종목"
# 인데 그날 표를 만들 재료가 없다. 저장분 폴백(#325)은 **그 날짜를 한 번이라도
# 만든 적이 있어야** 서는데, 배포 직후처럼 한 번도 못 만든 날은 영영 빈칸이다
# (2026-09-10 "종목은 아직 안나오네"). 그 하루를 시장의 **벌크 종가 원천**으로
# 채운다 — KR 은 KRX 공식값(pykrx `get_market_cap_by_ticker(date, market="ALL")`,
# 스크리너 `_fetch_kr_bulk` 가 이미 부르는 검증된 호출 · HTTP 1건, #141·#150
# 이미 부르는 호출이 무엇을 더 주는지 먼저 볼 것). 그 밖의 시장은 검증된 벌크
# 원천이 없어 채우지 않고 **그렇게 밝힌다**(#43 침묵이 최악).
# 붙이는 문턱은 희소 날짜 게이트와 **같은 상수**(`_MIN_SCAN_RATIO`)다 — 문턱이 둘이면
# "채웠다" 고 적고도 그 날짜가 희소로 걸러져 화면이 거짓말한다(독립 리뷰 실측: 0.5 로
# 채운 15/25 를 0.7 게이트가 버렸다). 판정은 벌크가 더한 수가 아니라 **채운 뒤 그
# 날짜의 커버리지**(원천이 이미 준 종목 + 벌크로 더한 종목)로 한다. 장전 KRX 0 값
# placeholder(`_fetch_kr_bulk` 실측)는 0 을 버려 걸러진다(#280).


def _kr_closes_on(date: str) -> tuple[dict, str]:
    """KRX 벌크 — `date`(YYYY-MM-DD) 의 전 종목 종가 `{6자리코드: float}`.
    (dict, 실패 사유). 함수 존재는 실호출 가능 여부로 판정한다(#151·#25)."""
    try:
        from bot.pykrx_client import krx_login_ready
        if not krx_login_ready():
            return {}, "KRX 자격증명 없음"
        from pykrx import stock
    except Exception as exc:                                   # noqa: BLE001
        return {}, f"pykrx 없음({type(exc).__name__})"
    fn = getattr(stock, "get_market_cap_by_ticker", None)
    if not callable(fn):
        return {}, "get_market_cap_by_ticker 없음(설치본)"
    ds = str(date).replace("-", "")[:8]
    try:
        try:
            df = fn(ds, market="ALL")
        except TypeError:
            # 옛 pykrx 는 market 인자가 없다 — KOSPI 만 온다(코스닥 누락).
            # 그러면 KOSDAQ 종목은 못 채우는데 그 사실은 filled/of 로 드러난다.
            df = fn(ds)
    except Exception as exc:                                   # noqa: BLE001
        return {}, f"KRX 조회 실패({type(exc).__name__})"
    if df is None or len(df) == 0:
        return {}, "KRX 응답 비어 있음"
    col = next((c for c in df.columns if "종가" in str(c)), None)
    if col is None:
        return {}, "KRX 응답에 종가 컬럼 없음"
    out: dict = {}
    for code in df.index:
        try:
            v = float(df.loc[code, col])
        except Exception:                                      # noqa: BLE001
            continue
        if v > 0:
            out[str(code).zfill(6)] = v
    if not out:
        return {}, "KRX 종가가 전부 0(장전 placeholder)"
    return out, ""


# 시장별 벌크 종가 원천 — 없는 시장은 채우지 않는다(사유는 fill 레코드가 말한다).
# 시장특정 예외 사유: KRX 가 KR 전 종목 종가를 한 호출로 주는 공식 원천이고
# 다른 시장엔 이 레포에 검증된 동급 원천이 없다(§UNIVERSAL 데이터소스 예외).
_SESSION_FILL: dict = {"KR": (_kr_closes_on, "KRX 벌크 종가(pykrx)")}


def _last_bar(closes: dict, min_ratio: float = 0.0) -> str | None:
    """원천이 **유니버스 대부분에게** 준 가장 늦은 봉 날짜. 한 종목에만 먼저 온
    봉(TW 09-09 1/225)을 '최신' 이라 부르면 보강이 불필요하다고 오판한다(#25
    있다 만 묻는 검사는 눈이 먼다) — `min_ratio` 미만 날짜는 뺀다."""
    if not closes:
        return None
    sparse = sparse_dates(closes, min_ratio) if min_ratio else {}
    dates = [_date_key(s.index[-1]) for s in closes.values() if len(s)]
    ok = [d for d in dates if d not in sparse]
    if ok:
        return max(ok)
    # 전부 희소면(유니버스 자체가 작을 때) 그냥 마지막 봉
    return max(dates) if dates else None


def fill_missing_session(market: str, closes: dict, expected) -> tuple[dict, dict]:
    """원천이 기대 완결 세션 `expected` 을 아직 안 줬으면 그 하루를 벌크 원천으로
    채운다. 반환 = (새 closes, 사실 레코드) — 입력은 손대지 않는다. 레코드는
    화면·진단이 그대로 적는다(#43·#136): {needed, date, newest, source, filled,
    of, applied, reason}.

    적용 규칙: 종목마다 마지막 봉이 `expected` 보다 앞일 때만 그 날짜 봉을 하나
    덧붙인다(이미 있는 종목은 손대지 않는다). 채운 뒤 그 날짜의 커버리지(원천이
    이미 준 종목 + 벌크로 더한 종목)가 유니버스의 `_MIN_SCAN_RATIO` 미만이면
    **하나도 붙이지 않는다** — 붙이는 결정은 전수를 센 뒤에 한다.
    ⚠️ 재지 않은 것(#165): `expected` 는 거래소 캘린더가 준 완결 세션이므로 거래일
    이지만, 캘린더 폴백 환경에서 휴장일이 넘어오면 벌크가 무엇을 돌려주는지는 실측이
    없다(pykrx 응답엔 날짜 칸이 없어 대조 불가)."""
    import pandas as pd
    m = (market or "").upper()
    newest = _last_bar(closes, _MIN_SCAN_RATIO)
    sparse = sparse_dates(closes, _MIN_SCAN_RATIO) if closes else {}
    rec = {"needed": False, "date": str(expected or ""), "newest": newest,
           "source": "", "filled": 0, "of": len(closes or {}), "applied": False,
           "reason": "",
           # 이번 응답에서 일부 종목에게만 온 날짜 — 세션으로 세지 않은 이유를
           # 화면이 말해야 한다(#43). {날짜: 종목 수}
           "sparse": {d: n for d, n in sparse.items() if d > (newest or "")}}
    if not expected or not closes or not newest or newest >= str(expected):
        return closes, rec
    rec["needed"] = True
    src = _SESSION_FILL.get(m)
    if not src:
        rec["reason"] = "이 시장엔 검증된 벌크 종가 원천이 없음 — 다음 갱신에서 원천이 따라잡기를 기다림"
        return closes, rec
    fn, label = src
    rec["source"] = label
    bulk, why = fn(str(expected))
    if not bulk:
        rec["reason"] = why or "벌크 응답 없음"
        return closes, rec
    ts = pd.Timestamp(str(expected))
    plan = []
    for tk, s in closes.items():
        if not len(s) or _date_key(s.index[-1]) >= str(expected):
            continue
        v = bulk.get(str(tk).split(".")[0].zfill(6))
        if v is None:
            continue
        plan.append((tk, s, v))
    rec["filled"] = len(plan)
    have = coverage_by_date(closes).get(str(expected), 0)
    rec["coverage"] = have + len(plan)
    if rec["coverage"] < len(closes) * _MIN_SCAN_RATIO:
        rec["reason"] = (f"벌크로 {len(plan)}종목을 더해도 {expected} 봉이 "
                         f"{rec['coverage']}/{len(closes)}종목뿐이라 쓰지 않음"
                         f"(하한 {int(_MIN_SCAN_RATIO * 100)}% — 그 아래는 세션으로 세지 않는다)")
        return closes, rec
    out = dict(closes)
    for tk, s, v in plan:
        t = ts.tz_localize(s.index.tz) if getattr(s.index, "tz", None) is not None else ts
        out[tk] = pd.concat([s, pd.Series([float(v)], index=[t])])
    rec["applied"] = True
    return out, rec


def fill_note(rec: dict | None) -> str:
    """fill 레코드 → 화면·진단 한 줄("" 이면 적을 것 없음). 순수(#41)."""
    r = rec or {}
    sp = r.get("sparse") or {}
    sp_lines = [f"{d} 봉은 {n}/{r.get('of')}종목에만 와 세션으로 세지 않음"
                for d, n in sorted(sp.items())]
    if not r.get("needed"):
        # 보강은 불필요해도 일부 종목에게만 온 뒷날짜는 화면이 말해야 한다(#43) —
        # 안 그러면 --why 만 알고 사용자는 "왜 오늘 봉이 없냐" 를 또 묻는다.
        return " · ".join(sp_lines)
    sp_txt = ("" if not sp_lines else " · " + ", ".join(sp_lines))
    head = (f"{r.get('date')} 종가가 이번 원천 응답에 없어(최신 봉 {r.get('newest')}{sp_txt})")
    if r.get("applied"):
        return (f"{head} {r.get('source')}로 채움 — {r.get('filled')}/{r.get('of')}종목 · "
                "그날 표·카드는 그 값으로 만들었습니다")
    return f"{head} 채우지 못함 — {r.get('reason')}"


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


def rows_path(market: str) -> Path:
    return _SERIES_DIR / f"rows_{(market or '').upper()}.json"


def load_rows(market: str) -> dict:
    """저장된 돌파 종목 표 {날짜: {"rows": [...], "saved_at": "YYYY-MM-DD HH:MM"}}.
    graceful {} — 파일이 없으면 표 폴백이 없을 뿐 카드·차트는 그대로다."""
    p = rows_path(market)
    try:
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return {str(k): v for k, v in d.items()
                        if isinstance(v, dict) and isinstance(v.get("rows"), list)}
    except Exception as exc:                                   # noqa: BLE001
        log.warning("bollinger: %s 종목 표 읽기 실패: %s", market, exc)
    return {}


def merge_rows(stored: dict, fresh: dict, saved_at: str,
               keep: int = _ROWS_KEEP) -> dict:
    """저장 표 + 이번 실행 표 → 최근 `keep` 세션만 남긴 새 dict(순수).
    이번 실행이 만든 날짜는 **덮어쓴다**(늦게 온 봉 정정 — `merge_series` 와
    같은 규율, #299). 저장돼 있던 옛 날짜는 그대로 두되 창 밖이면 버린다."""
    out = {str(k): dict(v) for k, v in (stored or {}).items()}
    for k, rows in (fresh or {}).items():
        out[str(k)] = {"rows": list(rows or []), "saved_at": saved_at}
    for k in sorted(out)[:max(0, len(out) - keep)]:
        out.pop(k, None)
    return out


def save_rows(market: str, rows_by_date: dict) -> None:
    """원자적 쓰기(#280) — 시계열과 같은 이유."""
    p = rows_path(market)
    try:
        _SERIES_DIR.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows_by_date, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("bollinger: %s 종목 표 저장 실패: %s", market, exc)


def pick_table_source(asof, count_dates, stored_rows, *, partial: bool,
                      provisional, expected, scan=None) -> tuple[str, str]:
    """기준일 표를 어디서 가져오나 — ("run"|"stored"|"none", 문구). 순수(#41).

    우선순위: ① 이번 실행이 그 날짜를 전수 스캔으로 만들었다 → run
    ② 저장분에 그 날짜 표가 있다 → stored(문구 = 왜 이번 실행 것을 못 썼는지 +
    저장분 수집 시각) ③ 둘 다 아니면 none(문구 = `rows_gap_reason` 또는 부분 스캔).
    ②가 없던 것이 2026-09-10 "왜 돌파종목이 안 나와" 의 원인이다(#325) — 카드는
    저장 시계열이 답했는데 표만 이번 원천에 매여 있었다."""
    in_run = asof in (count_dates or set())
    if in_run and not partial:
        return "run", ""
    if partial:
        sc = scan or {}
        why = (f"이번 수집이 부분 스캔({sc.get('kept', '?')}/{sc.get('universe', '?')})"
               "이라 이번 값을 쓰지 않았습니다")
    else:
        why = rows_gap_reason(asof, count_dates, provisional, expected)
    rec = (stored_rows or {}).get(str(asof))
    if rec is not None:
        return "stored", (f"{why} · 표는 저장분"
                          f"({rec.get('saved_at') or '수집 시각 미기록'} KST 수집)")
    if partial:
        return "none", (f"{why} — 종목 표를 만들지 않았습니다"
                        "(카드 수치는 저장된 전수 스캔 기준)")
    return "none", why


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
    stored_rows = load_rows(m)
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

    # 완결 / 잠정 — 오늘 장이 아직 안 끝났으면 마지막 봉은 **부분봉**이라
    # 시계열에 넣지 않는다. 넣으면 그날 값이 장중 스냅샷으로 굳는다(#40).
    try:
        from bot import market_timing as _mt
        expected, _grace = _mt._expected_session(m)
        closed = _mt._market_closed_today(m)
    except Exception as exc:                                   # noqa: BLE001
        log.debug("bollinger: %s 세션 판정 실패: %s", m, exc)
        expected, closed = None, None
    # 원천이 기대 완결 세션을 안 줬으면 그 하루를 벌크 원천으로 채운다 — 밴드를
    # 계산하기 **전에**(그래야 그날 count·표가 같은 재료에서 나온다, #45).
    closes, fill = fill_missing_session(m, closes, expected)
    scan["fill"] = fill
    if fill.get("needed"):
        log.info("bollinger: %s 기대 세션 보강 — %s", m, fill_note(fill))

    counts = breakouts_by_date(closes)
    # 일부 종목에게만 온 날짜는 시장 세션이 아니다 — 2026-09-10 TW 09-09 가 1/225
    # 종목으로 `0종목 · 스캔 1종목` 카드가 됐다. 세지 않고 사실만 남긴다(#280·#43).
    sparse = sparse_dates(closes, _MIN_SCAN_RATIO)
    scan["sparse_dropped"] = {d: n for d, n in sparse.items() if d in counts}
    for d in scan["sparse_dropped"]:
        counts.pop(d, None)
    if not counts:
        return _empty(m, "밴드를 만들 수 있는 종목이 없음(20봉 미만)", umeta)
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
    # 옛 판이 굽어 둔 희소 행(scanned 가 창 최대의 _MIN_SCAN_RATIO 미만)은 걷어낸다 — 그
    # 행이 남으면 카드가 `0종목 · 스캔 1종목` 을 마지막 거래일이라 부른다.
    merged, scan["sparse_pruned"] = prune_sparse_rows(merged, _MIN_SCAN_RATIO)
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

    # 표는 최근 몇 세션치를 만들어 **저장**한다 — 카드·차트는 저장 시계열(마지막
    # 거래일)에서 오는데 표만 이번 원천에 매여 있으면 원천이 하루 늦는 날
    # "21종목" 카드 아래 표가 비었다(2026-09-10 KR·JP, #325). 부분 스캔이면
    # 시계열과 같은 이유로 저장하지 않는다(#280).
    recent_dates = sorted(counts)[-_ROWS_KEEP:]
    want = recent_dates + ([provisional["date"]] if provisional else [])
    detail = breakouts_on(closes, want) if want else {}
    if write and not partial:
        stored_rows = merge_rows(
            stored_rows, {d: detail.get(d) or [] for d in recent_dates},
            _now_kst().strftime("%Y-%m-%d %H:%M"))
        save_rows(m, stored_rows)
    rows_basis, rows_note = pick_table_source(
        asof, set(counts), stored_rows, partial=partial,
        provisional=provisional, expected=expected, scan=scan)
    # ⚠️ 표를 못 만들면 '돌파 0건'이 아니라 **사유**를 적는다(#54·#43) — 그리고
    # 카드의 개수(저장 전수 스캔)와 표의 행수(이번 부분 스캔)를 나란히 놓지
    # 않는다(#33·#45 총계와 소계는 같은 모집단이어야 한다).
    rows_reason = rows_note if rows_basis == "none" else ""
    if rows_basis == "run":
        table = detail.get(asof) or []
    elif rows_basis == "stored":
        table = [dict(r) for r in (stored_rows.get(asof) or {}).get("rows") or []]
    else:
        table = []
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
        "chart": a5_hist[-_CHART_ROWS:],
        # 5일 평균 최저·최고 — 차트와 **같은 창**에서 고른다(#38·#51).
        "a5_ext": avg5_extremes(a5_hist[-_CHART_ROWS:]),
        # 돌파 종목수 자체의 최소·최다 — 같은 창·같은 규약(#38·#51).
        "cnt_ext": count_extremes(a5_hist[-_CHART_ROWS:]),
        "rows": table[:_TABLE_ROWS], "rows_total": len(table),
        "rows_reason": rows_reason,
        "rows_basis": rows_basis, "rows_note": rows_note if rows_basis == "stored" else "",
        "fill": fill, "fill_note": fill_note(fill),
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
.bb-mini{border-collapse:collapse;margin:6px 0 8px;font-size:12px}
.bb-mini th,.bb-mini td{border:1px solid var(--border);padding:3px 8px;text-align:center}
.bb-mini th{color:var(--muted);font-weight:600}
.bb-nm{color:var(--fg);text-decoration:none;border-bottom:1px dotted var(--border)}
.bb-nm:hover{color:var(--accent);border-bottom-color:var(--accent)}
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


_HAN_RE = _re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def has_han(name: str) -> bool:
    """한자가 남아 있으면 아직 한글화 안 된 이름이다(한국어엔 한자가 없다)."""
    return bool(_HAN_RE.search(name or ""))


def korean_names(rows: list, market: str, *, kick: bool = True) -> int:
    """비-KR/US 표의 종목명을 **신고저·급등락 보드와 같은 캐시**로 한글화한다
    (사용자 2026-09-09 "기존 신고가 신저가/급등급락처럼"). 렌더타임이라
    시계열에 구워진 옛 행도 따라온다(#270). 캐시에 없는 종목은 원문을 두고
    백그라운드로 워밍 — 렌더는 네트워크·LLM 을 한 번도 기다리지 않는다.

    ⚠️ 캐시가 **둘**이다(2026-09-10 사용자 "대만 한글화 안 된 것들" — `欣銓`
    3264.TWO 가 한자 그대로): `names_kr.json` 은 **티커** 키이고, 정작 대만
    급등락·52주 보드는 `chart_title_kr.json`(**원문명** 키, `translate_titles_kr`)
    로 번역한다. 옛 코드는 앞 캐시만 봐서 "같은 캐시" 라는 독스트링이 거짓
    이었다(#55) — 앞 캐시가 비면 뒤 캐시를 원문명으로 한 번 더 본다. 둘 다
    LLM 0. `kick=False` 는 진단용(과금 경로를 열지 않는다, #312).
    반환 = 한글로 바뀐 행 수."""
    if market not in ("CN_A", "TW", "HK"):
        return 0
    pairs = [(r.get("ticker"), r.get("name")) for r in rows or []
             if r.get("ticker")]
    if not pairs:
        return 0
    try:
        from bot.chart_translate import translate_names_kr, translate_titles_kr
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
    # 2차 — 원문명 키 캐시(급등락·52주 보드가 쓰는 그것). 한자가 남은 행만.
    left = [r for r in rows if r.get("ticker") not in knm and has_han(r.get("name"))]
    resolved: set = set()
    if left:
        try:
            ktl = translate_titles_kr([r["name"] for r in left], cache_only=True) or {}
        except Exception as exc:                               # noqa: BLE001
            log.debug("bollinger: 제목 캐시 조회 실패: %s", exc)
            ktl = {}
        for r in left:
            k = ktl.get(r.get("name"))
            if k and k != r.get("name"):
                r["name"] = k
                resolved.add(r.get("ticker"))
                hit += 1
    # 2차로 풀린 티커는 워밍에서 뺀다 — 화면이 이미 한글로 보여주는 이름을
    # 과금 경로(translate_names_kr)에 다시 보내지 않는다(독립 리뷰 2026-09-10).
    miss = [p for p in pairs if p[0] not in knm and p[0] not in resolved]
    if miss and kick:
        try:
            _NAME_KICK(miss)
        except Exception as exc:                               # noqa: BLE001
            log.debug("bollinger: 한글명 워밍 킥 실패: %s", exc)
    return hit


def name_diag(rows: list, market: str) -> dict:
    """진단용 — 한글명이 왜 안 붙었는지 **캐시별로** 센다(LLM 0 · 킥 0).

    {total, han_before, han_after, by_ticker(names_kr 적중), by_title(제목
    캐시 적중), samples(아직 한자인 티커·이름)}. '없음' 만 말하면 추측을
    부르므로(#82) 어느 캐시가 비었는지까지 갈라 말한다."""
    rows = [dict(r) for r in rows or []]
    out = {"total": len(rows), "han_before": sum(1 for r in rows if has_han(r.get("name"))),
           "by_ticker": 0, "by_title": 0, "han_after": 0, "samples": []}
    if market not in ("CN_A", "TW", "HK") or not rows:
        out["han_after"] = out["han_before"]
        return out
    try:
        from bot.chart_translate import translate_names_kr, translate_titles_kr
        knm = translate_names_kr([(r.get("ticker"), r.get("name")) for r in rows],
                                 cache_only=True) or {}
        out["by_ticker"] = sum(1 for r in rows if knm.get(r.get("ticker")))
        left = [r for r in rows if not knm.get(r.get("ticker")) and has_han(r.get("name"))]
        ktl = translate_titles_kr([r["name"] for r in left], cache_only=True) if left else {}
        out["by_title"] = sum(1 for r in left if (ktl or {}).get(r.get("name")))
    except Exception as exc:                                   # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    korean_names(rows, market, kick=False)
    out["han_after"] = sum(1 for r in rows if has_han(r.get("name")))
    out["samples"] = [f"{r.get('ticker')} {r.get('name')}" for r in rows
                      if has_han(r.get("name"))][:8]
    return out


def _phase_table_html() -> str:
    """국면 9개를 **PHASE 상수에서** 격자로 그린다 — 손으로 적으면 코드와 어긋난다
    (#55). 행 = 수준, 열 = 추이."""
    import html as _h
    dirs = ("up", "flat", "down")
    out = ["<table class='bb-mini'><tr><th></th>"]
    out += [f"<th>{_h.escape(DIR_LABEL[d])}</th>" for d in dirs]
    out.append("</tr>")
    for lv in ("strong", "neutral", "weak"):
        out.append(f"<tr><th>{_h.escape(LEVEL_LABEL[lv])}</th>")
        out += [f"<td>{_h.escape(PHASE[(lv, d)])}</td>" for d in dirs]
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


_UNIVERSE_DESC = {
    "KR": "코스피200 + 코스닥150(KIS 마스터파일, 실패 시 네이버·pykrx·시총상위 순)",
    "US": "S&P 500",
    "JP": "JPX 상장목록 중 시총상위 225",
    "HK": "HKEX 상장목록 중 시총상위 225",
    "CN_A": "CSI 300 구성종목",
    "TW": "TWSE/TPEx 거래대금 상위 225(시총 아님)",
}


def _universe_table_html() -> str:
    """시장별 유니버스 정의 — `_MARKET_META` 의 모든 시장이 한 줄씩(빠지면
    회귀가 잡는다, #24)."""
    import html as _h
    rows = "".join(
        f"<tr><th>{flag} {_h.escape(name)}</th>"
        f"<td>{_h.escape(_UNIVERSE_DESC.get(m, '—'))}</td></tr>"
        for m, (flag, name) in _MARKET_META.items())
    return f"<table class='bb-mini'>{rows}</table>"


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


def _name_link(row: dict) -> str:
    """종목명 → `/lookup/<ticker>` 종목분석 화면 링크(사용자 2026-09-09 "볼린저밴드
    보드에서 종목을 클릭하면 종목분석화면으로. 모든 나라 다 적용").

    상대 경로는 신고가·급등락(`highlow_render`)·미국·네이버 보드와 **같은 형태**
    (`lookup/{ticker}`)다 — 이 페이지들이 전부 ARCHIVE_ROOT 한 디렉터리에서
    서빙되므로 규약이 하나여야 한다(#38). 티커가 없으면 링크를 만들지 않는다
    (빈 href 는 그 페이지를 다시 여는 죽은 링크다, #144 필수 인자가 없으면
    부르지 말 것)."""
    import html as _h
    tk = str(row.get("ticker") or "").strip()
    label = _h.escape(str(row.get("name") or tk or "—"))
    if not tk:
        return label
    return f"<a class='bb-nm' href='lookup/{_h.escape(tk)}'>{label}</a>"


def rows_gap_reason(asof, count_dates, provisional, expected) -> str:
    """기준일 표를 못 만든 사유(만들 수 있으면 ""). 순수 함수라 값으로 고정한다(#41).

    2026-09-09 KR·JP 실측: 화면이 `{asof} 은 이번 수집 창 밖이라…` 하나로 적어
    사용자가 "왜 9/9 가 수집 밖이냐" 고 물었다 — **창 크기(3개월)는 원인이 아니고**
    갈래가 셋인데 처방이 다 다르다(#82·#292 틀린 라벨은 라벨이 없는 것보다 나쁘다):
      (a) 이번 실행이 그 날짜를 **잠정(미확정)** 으로 분류했다 → 기대 세션 판정을 볼 것
      (b) 이번 원천 데이터에 그 날짜가 **아예 없다**(야후가 KR·JP 에서 하루 늦는
          알려진 증상, #301·#310) → 다음 주기에 따라잡는다
      (c) 위 둘이 아닌데 없다 → 숫자를 그대로 적어 다음 라운드가 재게 한다
          (`count_dates` 가 빈 경우는 `build_market` 에선 도달하지 않는다 — 방어용)
    카드 수치는 저장분(마지막 거래일 기준)에서 오므로 그 사실도 같이 적는다(#45).
    """
    if asof in (count_dates or set()):
        return ""
    exp = f" · 기대 완결 세션 {expected}" if expected else ""
    if provisional and str(provisional.get("date") or "") == str(asof):
        return (f"{asof} 은 이번 실행에서 잠정(미확정) 봉으로 분류돼 확정 표를 "
                f"만들지 않았습니다{exp} — 카드 수치는 저장분(마지막 거래일) 기준")
    newest = max(count_dates) if count_dates else None
    got = f"이번 원천 최신 봉 {newest}" if newest else "이번 원천 데이터 없음"
    return (f"{asof} 이 이번 수집 데이터에 없어 종목 표를 만들지 못했습니다"
            f"({got}{exp}) — 카드 수치는 저장분(마지막 거래일) 기준")


def _rows_table(rows: list, total: int, scanned, title: str) -> str:
    import html as _h
    if not rows:
        return (f"<div class='bb-empty'>{_h.escape(title)}: 상단 돌파 종목 없음"
                f" (스캔 {_n(scanned, 0)}종목)</div>")
    body = "".join(
        "<tr>"
        f"<td>{i + 1}</td>"
        f"<td>{_name_link(r)}"
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


def _ext_card(label: str, e: dict | None, ext: dict | None,
              key: str = "avg5") -> str:
    """극값 카드 한 장 — 값·날짜·짝 지표·창을 한 칸에. `key="avg5"` 는 5일 평균
    최저/최고(짝 = 그날 돌파 종목수), `key="count"` 는 돌파 최소/최다(짝 = 그날
    5일 평균). 날짜 없이 값만 적으면 "언제냐"가 바로 온다(#43·#202)."""
    import html as _h
    if not e:
        what = "5일 평균이 있는" if key == "avg5" else "돌파 종목수가 있는"
        return (f"<div class='stat'><div class='k'>{_h.escape(label)}</div>"
                f"<div class='v'>—</div><div class='bb-note'>{what} "
                "세션이 없음</div></div>")
    win = (ext or {}).get("window")
    if key == "count":
        val = f"{_n(e.get('count'), 0)}종목"
        pair = f"그날 5일 평균 {_n(e.get('avg5'))}"
    else:
        val = _n(e.get("avg5"))
        pair = f"그날 돌파 {_n(e.get('count'), 0)}종목"
    return (f"<div class='stat'><div class='k'>{_h.escape(label)}</div>"
            f"<div class='v'>{val}</div>"
            f"<div class='bb-note'>{_h.escape(str(e.get('date')))} · {pair}"
            f" · 차트 구간 {_n(win, 0)}세션 중</div></div>")


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
        f"{_ext_card('5일 평균 최저', (d.get('a5_ext') or {}).get('min'), d.get('a5_ext'))}"
        f"{_ext_card('5일 평균 최고', (d.get('a5_ext') or {}).get('max'), d.get('a5_ext'))}"
        f"{_ext_card('돌파 최소', (d.get('cnt_ext') or {}).get('min'), d.get('cnt_ext'), key='count')}"
        f"{_ext_card('돌파 최다', (d.get('cnt_ext') or {}).get('max'), d.get('cnt_ext'), key='count')}"
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
    if d.get("rows_note"):
        # 표가 저장분이면 **그 사실과 시각**을 표 위에 적는다 — payload 가 밝힌
        # 원천을 화면이 따라야 한다(#136·#43). 로그로만 알리면 사용자는 모른다.
        table = (f"<div class='bb-warn'>💾 {_h.escape(str(d['rows_note']))}</div>"
                 + table)
    if d.get("fill_note"):
        # 기준일 종가를 벌크 원천으로 채웠으면(또는 못 채웠으면) 그 사실을 표 위에
        # 적는다 — 야후가 아닌 값으로 만든 표를 야후 것처럼 보이면 안 된다(#136).
        table = (f"<div class='bb-warn'>🧩 {_h.escape(str(d['fill_note']))}</div>"
                 + table)
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
<b>돌파 종목 표의 열</b> — 종가 · 등락(<b>전일 종가 대비</b> %) · 상단밴드(그날의
20일 이동평균 +2σ) · 돌파폭 = <b>종가 ÷ 상단밴드 − 1</b>(밴드를 얼마나 넘어
마감했나) · 시총(억·조 / $B). 티커 옆 태그는 거래소입니다.
<b>종목명을 누르면 그 종목의 분석 화면</b>(차트·재무·밸류에이션)으로 갑니다 —
전 시장 동일.<br>
<b>돌파 종목 표</b> — 유니버스 전체를 스캔해 조건에 맞는 종목을 <b>전부</b>
싣습니다(시총 내림차순). 카드의 "오늘 돌파 N종목"과 표의 행 수는 같은 목록에서
나오므로 항상 같습니다. 🆕 는 <b>전일엔 밴드 안이었다가 오늘 밖에서 마감</b>한
신규 돌파이고, 🆕 가 없는 행은 전일에도 이미 밖에 있던 <b>지속 돌파</b>입니다 —
카드의 "신규 N" 이 🆕 개수입니다. 상장 첫 20봉처럼 전일 밴드를 만들 수 없는
날은 "신규인지 알 수 없는" 것이라 🆕 를 붙이지 않습니다.<br>
<b>카드의 숫자</b> — "오늘 돌파 N종목"은 기준일 종가 기준 돌파 개수, 그 아래
"스캔 M종목 중 X%" 는 N ÷ M 입니다(유니버스 중 실제로 시계열을 받은 종목만
분모). "신규" 는 🆕 개수. "5일 평균" 은 최근 5거래일 돌파 개수의 평균입니다.<br>
<b>수준은 오늘 개수가 아니라 5일 평균으로 판정합니다</b> — 오늘 21종목이라
강세 문턱 20 을 넘었어도 5일 평균이 12.6 이면 수준은 "중립" 입니다. 하루 급등에
판정이 튀지 않게 하려는 것이고, 원문도 5일 평균선을 봅니다.<br>
<b>절댓값보다 추이</b> — 원문이 가장 강조한 점입니다. 그래서 이 보드의 판정
1순위는 <b>5일 평균선의 방향</b>이고, 수준(문턱)은 그다음입니다.
국면 이름은 <b>수준 × 추이</b>로 정합니다.<br>
<b>추이 판정 문턱</b> — 5일 평균의 5세션 전 대비 변화(Δ5)가 <b>유니버스의 1%
(최소 1종목)</b>를 넘어야 ↑/↓ 이고, 그 안이면 "→ 횡보" 입니다. 예: 문턱 ±2.2 인
시장에서 −1.2 종목은 하락이 아니라 횡보입니다. 옆의 "vs 20세션 전" 은 참고
값이고 판정엔 들어가지 않습니다.<br>
<b>국면 이름</b> — 수준(행) × 추이(열)로 정해지는 <b>현재 상태의 이름</b>입니다
(예측이 아닙니다). 약세에서 횡보·하락은 둘 다 "에너지 소진" 입니다.
{_phase_table_html()}
<b>세션</b> — 이 보드의 "세션"은 <b>그 시장의 거래일 하나</b>입니다(달력일이
아닙니다). 주말·휴장일은 세지 않으므로 "5세션 전"은 대략 1주 전, "20세션 전"은
대략 1개월 전입니다.<br>
<b>약세 연속 N세션</b> — 5일 평균이 약세 문턱 <b>이하</b>였던 거래일이 최근부터
거슬러 몇 번 <b>연속</b>인가. 누적이 아니라 연속이라 하루라도 문턱 위로 올라오면
0 으로 리셋됩니다. 0 은 "지금은 약세 아님"입니다. 원문의 "두 달 연속"을
40거래일로 환산해 두었고, <b>40세션에 닿았을 때만</b> 원문의 현금 비중 문구를
인용합니다.<br>
<b>이력 백분위</b> — 오늘의 5일 평균이 <b>이 시장 자기 과거 5일 평균들 중 몇
%보다 높은가</b>입니다(산식: 오늘보다 낮았던 세션 수 ÷ 전체 세션 수). 예를 들어
38% 는 지난 이력의 38% 가 오늘보다 낮았고 62% 는 오늘 이상이었다는 뜻 — 중하위권
입니다. 강세/약세 문턱은 코스피200+코스닥150 기준 예시라 다른 시장에 그대로 못
쓰지만, 백분위는 자기 이력과만 비교하므로 유니버스 크기와 무관해 <b>시장 간에 뜻이
같은 유일한 축</b>입니다. 60세션 이상 쌓여야 판정하고, 이력 대부분이 백필이라
생존편향이 있습니다(과거 돌파 수가 실제보다 적게 잡혀 오늘의 백분위가 <b>약간
높게</b> 나오는 방향). 백분위는 <b>수준</b>만 말하고 <b>추이</b>는 옆 칸이 말합니다.<br>
<b>차트</b> — 막대는 <b>일별 돌파 종목수</b>(옅은 막대 = 백필 구간), 주황
선은 <b>5일 평균</b>, 초록·빨강 점선은 강세·약세 <b>문턱</b>입니다. 판정은 주황
선이 점선의 어디에 있고 어느 방향으로 움직이는지로 읽으면 됩니다. 구간은
<b>최근 {_CHART_ROWS}세션</b>이고 갱신마다 한 세션씩 굴러갑니다(왼쪽 끝이 빠지고
오른쪽에 새 세션이 붙습니다).<br>
<b>5일 평균 최저 · 최고</b> — 차트에 그려진 <b>그 구간 안</b>에서 5일 평균이
가장 낮았던/높았던 날짜와 그 값, 그리고 <b>그날의 돌파 종목수</b>입니다. 구간이
굴러가므로 값·날짜도 같이 바뀝니다. 같은 값이 여러 날이면 가장 최근 날짜를 적습니다.<br>
<b>돌파 최소 · 최다</b> — 같은 구간에서 <b>그날 돌파 종목수 자체</b>가 가장 적었던/많았던
날짜와 그 개수(짝으로 그날의 5일 평균). 규약은 위와 같습니다(동률이면 최근 날짜).<br>
<b>일부 종목에게만 온 봉</b> — 원천 응답에 어떤 날짜의 봉이 유니버스의
{int(_MIN_SCAN_RATIO * 100)}% 미만 종목에만 있으면 그날은 세션으로 세지 않습니다(한
종목으로 "돌파 0종목" 을 만들지 않으려는 것). 그 날짜는 표 위 🧩 줄이 "N/M종목에만 와
세지 않음" 으로 밝힙니다.<br>
<b>표가 "💾 저장분"이면</b> — 카드·차트는 마지막 거래일까지 저장된 시계열에서
오는데, 이번 갱신의 원천 응답이 그 날짜 봉을 안 주면(야후가 하루 늦는 날) 표는
이번 응답으로 만들 수 없습니다. 그때는 그 날짜를 마지막으로 만들었을 때 저장해 둔
표를 보여 주고 수집 시각을 적습니다 — 카드의 종목수와 같은 목록입니다.<br>
<b>"🧩 …로 채움"이면</b> — 원천 응답에 기준일 봉이 없을 때 그 하루 종가를 시장의
벌크 원천(한국: KRX 공식 종가)으로 채워 그날 표·카드를 만든 것입니다. 벌크 원천이
없는 시장은 채우지 않고 그렇게 적습니다.<br>
<b>기준일 · 잠정 · 갱신</b> — 보드는 3시간마다 갱신되지만 <b>기록은 마지막 완결
세션(종가 확정)만</b> 남깁니다. 장중에는 "🕒 잠정" 줄에 그 시각까지의 개수를 따로
보여 주고, 종가가 확정된 다음 갱신에서 기록합니다(장중 값은 카드·차트에 섞이지
않습니다). "⚠️ 부분 스캔" 이 뜨면 시세 다운로드가 모자라 그 값을 기록하지 않고
표도 접은 것입니다 — 다음 갱신에서 다시 시도합니다.<br>
<b>시장별 유니버스</b> — 시장마다 정의가 다르므로 종목수를 시장 간에 직접 비교하지
마세요.
{_universe_table_html()}
<b>위험 관리</b> — 원문은 "5일 평균 10 이하가 두 달 연속"일 때 현금 비중
60~70% 를 <b>예로</b> 듭니다. 이 보드는 비중을 처방하지 않고 약세가 몇
세션 연속인지만 사실로 적으며, 그 조건을 채웠을 때만 원문을 인용합니다.<br>
<b>상장목록 N일 전 캐시</b> — 일본·홍콩 카드에 이 문구가 붙으면 공식 상장목록
원천(JPX/HKEX) 조회가 실패해 마지막으로 받아 둔 목록으로 계산한 것입니다. 상장
목록은 느리게 바뀌어 며칠 낡은 목록이 빈 목록보다 낫지만, 그 사이 신규 상장·
상장폐지는 반영되지 않습니다. 원천이 복구되면 문구가 사라집니다.<br>
<b>해석이 들어간 지점 넷</b> —
① 돌파는 <b>종가 &gt; 상단</b>(그날 밴드 밖에서 마감한 상태)으로 셉니다.
HTS 는 표준편차 정의가 다를 수 있어
종목수가 한두 개 차이 날 수 있습니다(우리가 재 보지는 않았습니다).
② 문턱 20/10 은 원문이 밝힌 <b>예시</b>이고 코스피200+코스닥150(350종목)
기준입니다 — 다른 시장은 유니버스 크기로 <b>비율</b> 환산했습니다. 이 종목수에
공식 문턱은 없고, 통용되는 폭(breadth) 규약은 유니버스 대비 비율과 자기 이력
백분위 둘입니다. 참고: 20일·2σ 밴드는 한 종목 가격의 약 88~89% 를 담는다는
Bollinger 규칙에서 상·하 대칭과 종목 간 독립을 가정하면 상단 밖 종목은 유니버스의
5~6%(350종목이면 ≈19~21)라는 <b>통계적 추정</b>이 나옵니다 — 측정이 아니며, 종목들은
같이 움직여 실제 분포는 치우칩니다. 실제 발화율은 <code>--why ⑧</code> 이 잽니다.
시장 간 종목수를 직접 비교하지 마세요(유니버스 크기·구성이 다릅니다).
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
_WHY_VER = 4   # 2026-09-10 ⑧ 문턱 검증(이력 발화율·밴드 통계 기대값)
_RUN_HINT = "cd ~/stock && .venv/bin/python -m bot.bollinger_board --why KR"


def _p(s: str = "") -> None:
    print(s, flush=True)


def rung_mark(r: dict) -> tuple[str, str]:
    """사다리 한 단의 표식 — (글리프, 꼬리표). 순수(#41).
    ✅ 충족 / ⚠️ 원천이 스스로 '안 준다'고 선언(우리가 고칠 것 없음, #260) /
    ❌ 그 밖(우리가 볼 것). 사실(개수·후보별 응답)은 어느 쪽이든 그대로 찍힌다(#41)."""
    if r.get("ok"):
        return "✅", ""
    if (r.get("diag") or {}).get("source_declined"):
        return "⚠️", ("원천이 '서비스하지 않는 지수' 로 답함(409 "
                      f"{_NAVER_DECLINE_CODE}) — 우리가 고칠 것 없음")
    return "❌", ""


def _why_universe_kr() -> None:
    """KR 사다리 4단을 **전부** 시도해 나란히 찍는다 — 어느 단이 왜 안 됐는지
    한 번에 보여야 다음 라운드가 엉뚱한 데를 고치지 않는다(#82·#109)."""
    _uni, meta = kr_universe(probe=True, use_cache=False)
    for r in meta["rungs"]:
        mark, tag = rung_mark(r)
        _p(f"   {'①②③④'[r['n'] - 1]} {r['name']:<18} {r['note']}  "
           f"{mark} {r['why']}{' · ' + tag if tag else ''}")
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
                   f"({'없음' if age is None else f'{age / 3600:.1f}시간 전 기록'})"
                   " — 이 파일이 오늘 다시 써졌으면 원천이 살아난 것")
    except Exception as exc:                                   # noqa: BLE001
        out.append(f"캐시 경로 확인 실패: {type(exc).__name__}: {exc}")
    try:
        import time as _t
        from bot import stock_screener as ss
        sc = ss._JP_UNIVERSE_CACHE if m == "JP" else ss._HK_UNIVERSE_CACHE
        sage = (_t.time() - sc.stat().st_mtime) / 3600 if sc.exists() else None
        out.append(f"스크리너 유니버스 캐시: {sc} "
                   f"({'없음' if sage is None else f'{sage:.1f}시간 전 기록'})"
                   " — Bollinger 는 이 층을 읽지 않는다(스크리너용, 만료 폴백분은 안 굳힘)")
    except Exception as exc:                                   # noqa: BLE001
        out.append(f"스크리너 캐시 확인 실패: {type(exc).__name__}: {exc}")
    try:
        full = iu.full_universe(m)
        st = iu.stale_hours(m)
        out.append(f"full_universe({m}) → {len(full):,}종목"
                   f"{' (100 이하 → 유니버스로 안 쓴다)' if len(full) <= 100 else ''}"
                   f"{f' · ⚠️ 원천 실패, {st:.0f}시간 전 캐시로 서빙 중' if st is not None else ''}")
    except Exception as exc:                                   # noqa: BLE001
        out.append(f"full_universe({m}) 예외: {type(exc).__name__}: {exc}")
        full, st = [], None
    if len(full) <= 100 or st is not None:
        spec = iu._SPEC.get(m)
        url = spec[0] if spec else "?"
        try:
            raw = iu._http_get(url)
            out.append(f"원천 HTTP: {url} → {len(raw):,}바이트 수신"
                       " (받았는데 파싱이 100종목 이하면 파서/서식 문제)")
        except Exception as exc:                               # noqa: BLE001
            out.append(f"원천 HTTP 실패: {url} → {type(exc).__name__}: {exc}")
            try:
                alt = iu._discover_url(m)
                out.append(f"목록 페이지 탐색: {'찾음 → ' + alt if alt else '링크 못 찾음'}"
                           + (" (하드코딩과 같은 URL — 이동 아님)" if alt == url else ""))
            except Exception as exc2:                          # noqa: BLE001
                out.append(f"목록 페이지 탐색 실패: {type(exc2).__name__}: {exc2}")
    return out


def _threshold_audit_lines(market: str, series: dict) -> str:
    """⑧ 문턱 검증 — 저장 이력에서 강세/약세가 실제로 얼마나 자주 발화했나를
    **값으로** 찍는다(사용자 2026-09-10 "이 기준이 맞는걸까"). 판정은
    `bollinger.threshold_audit` 순수 함수(화면·감사가 같은 값을 보게, #176).
    대조 0세션이면 ❓ 이지 ✅ 가 아니다(#54)."""
    from bot import bollinger as _b
    rows = _b.series_rows(series)
    a = _b.threshold_audit(rows)
    lines = ["⑧ 문턱 검증 — 저장 이력에서 실제 발화율(문턱은 바꾸지 않았다)"]
    strong, weak = _b.level_thresholds(
        next(((r or {}).get("scanned") for r in reversed(rows)
              if (r or {}).get("scanned")), None))
    if not a.get("n"):
        lines.append(f"   ❓ {a.get('reason') or '판정 불가'}")
        return "\n".join(lines)
    lines.append(f"   문턱 강세 ≥ {strong} · 약세 ≤ {weak} · 판정 세션 {a['n']}"
                 + (f" · ⚠️ {a['reason']}" if a.get("reason") else ""))
    lines.append(f"   발화율: 강세 {a['strong_share']*100:.0f}% · 중립 "
                 f"{a['neutral_share']*100:.0f}% · 약세 {a['weak_share']*100:.0f}%"
                 " (세션 비율 — 한쪽이 절반을 넘으면 그 문턱은 '평상시' 를 가리킨다)")
    if a.get("mean_pct") is not None and a.get("expected_lo") is not None:
        lines.append(f"   평균 돌파 비율 {a['mean_pct']:.1f}%(판정 세션 기준) — 통계적 추정"
                     f"(20일·2σ 밖 ≈{_b.BAND_OUTSIDE_SHARE[0]*100:.1f}~{_b.BAND_OUTSIDE_SHARE[1]*100:.0f}%, "
                     f"대칭·독립 가정)은 최근 분모 기준 {a['expected_lo']:.0f}~{a['expected_hi']:.0f}종목 "
                     "— 실측이 이 추정과 갈리면 실측이 맞다")
    if a.get("p20") is not None:
        lines.append(f"   5일선 이력 백분위: 하위 20% ≤ {a['p20']:.1f} · 상위 20% ≥ {a['p80']:.1f}"
                     " (통용 규약의 다른 한 축 — 이 값과 문턱을 나란히 보라)")
    return "\n".join(lines)


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
        _p(f"   {universe_label(m, meta)}"
           f"{' · ' + meta['reason'] if meta.get('reason') else ''}")
        if m in ("JP", "HK"):
            # 비었을 때만 찍으면 '만료 캐시로 살아 있는' 상태가 침묵한다 —
            # 2026-09-09 실측: 캐시 문구가 사라졌는데 원천을 물은 흔적이 없어
            # 복구인지 두 번째 캐시 층인지 출력만으론 못 갈랐다(#54·#82).
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
        # 문턱 검증은 저장 이력만 보므로 이번 실행이 실패해도 답할 수 있다.
        _p("")
        _p(_threshold_audit_lines(m, before))
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
       + (f" {_b}" if (_b := _session_badge(m, d.get('asof'))) else ""))
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
    if d.get("rows_reason"):
        # 표를 못 만든 사유는 갈래가 셋이고 처방이 다르다 — 화면과 같은 문구를
        # 진단도 찍어야 사용자가 본 것을 그대로 재현한다(#35).
        _p(f"   ↪ {d['rows_reason']}")
    if d.get("rows_note"):
        _p(f"   💾 {d['rows_note']}")
    _p(f"   표 출처 {d.get('rows_basis')} (run=이번 실행 · stored=저장분 · none=없음)")
    fl = (d.get("scan") or {}).get("fill") or {}
    if fl.get("needed"):
        # 화면과 같은 문구 + 숫자(#35·#202) — 채웠으면 몇 종목, 못 채웠으면 왜.
        _p(f"   🧩 {fill_note(fl)}")
    else:
        _p(f"   🧩 기대 세션 보강 불필요(원천 최신 봉 {fl.get('newest')} ≥ 기대 {d.get('expected')})")
    ext = d.get("a5_ext") or {}
    if ext:
        _p(f"   5일 평균 극값(차트 구간 {ext.get('window')}세션 · 판정 {ext.get('judged')}행): "
           f"최저 {_n(ext['min']['avg5'])} @ {ext['min']['date']}(돌파 {_n(ext['min']['count'], 0)}) · "
           f"최고 {_n(ext['max']['avg5'])} @ {ext['max']['date']}(돌파 {_n(ext['max']['count'], 0)})")
    else:
        _p("   5일 평균 극값: 판정할 행 없음")
    cx = d.get("cnt_ext") or {}
    if cx:
        _p(f"   돌파 종목수 극값(같은 구간): 최소 {_n(cx['min']['count'], 0)}종목 @ {cx['min']['date']} · "
           f"최다 {_n(cx['max']['count'], 0)}종목 @ {cx['max']['date']}")
    sd = sc.get("sparse_dropped") or {}
    sp = sc.get("sparse_pruned") or {}
    if sd or sp:
        _p("   ⚠️ 일부 종목에게만 온 날짜 — 세션으로 세지 않음: "
           + ", ".join(f"{k}({v}/{sc.get('kept')}종목)" for k, v in sorted(sd.items()))
           + (f" · 저장분에서 걷어냄: {', '.join(f'{k}(scanned {v})' for k, v in sorted(sp.items()))}" if sp else ""))
    if m in ("CN_A", "TW", "HK"):
        _p("")
        _p("⑦ 한글명 — 렌더타임 캐시 조회(LLM 0 · 워밍 킥 안 함)")
        # ⚠️ enrich=False 로 받은 행엔 `name` 이 없다(이름은 `_overlay` 가 붙인다)
        # — 그대로 재면 한자 0 으로 늘 ✅ 가 된다(독립 리뷰 2026-09-10 · #54).
        # `_overlay` 의 첫 단계(유니버스 맵 이름)만 그대로 적용해 화면이 보는
        # 원문명을 만든다. LLM·네트워크 0(맵은 ② 에서 이미 받았다).
        try:
            uni_names, _ = _universe(m)
        except Exception as exc:                               # noqa: BLE001
            uni_names = {}
            _p(f"   ⚠️ 유니버스 이름 맵 조회 실패 {type(exc).__name__}: {exc}")
        named = []
        for r in d.get("rows") or []:
            r = dict(r)
            info = (uni_names or {}).get(r.get("ticker")) or {}
            r["name"] = info.get("name") or r.get("name") or r.get("ticker")
            named.append(r)
        if not named:
            _p("   ❓ 표가 비어 판정 불가(대조 0행)")
        else:
            nd = name_diag(named, m)
            if nd.get("error"):
                _p(f"   ❌ 캐시 조회 실패 {nd['error']}")
            _p(f"   표 {nd['total']}행(이름 = 유니버스 맵) · 한자 남은 이름 {nd['han_before']} → "
               f"조회 후 {nd['han_after']} (티커 캐시 names_kr {nd['by_ticker']} · "
               f"제목 캐시 chart_title_kr {nd['by_title']})")
            if nd["han_after"]:
                from bot.env_keys import env_diag
                _p(f"   아직 한자: {', '.join(nd['samples'])}")
                why = env_diag("GOOGLE_API_KEY")
                _p(("   ↪ 캐시 조회가 실패해 판정 불가 — 위 ❌ 원문부터"
                    if nd.get("error") else
                    "   ↪ 두 캐시 모두 없음 — 3시간 빌드의 LLM 번역이 채운다")
                   + (f" · 번역 키 {why}" if why else " · 번역 키 GOOGLE_API_KEY 는 환경변수에 있음"))
            else:
                _p("   ✅ 전 행 한글(또는 한자 없음)")
    _p("")
    _p(_threshold_audit_lines(m, before))
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
