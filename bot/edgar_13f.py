"""SEC EDGAR 13F 기관투자자 보유내역 트래커 (2026-07-26, 미국전용 예외).

claude-trading-skills 리뷰 갭 — 13F 기관 자금흐름(신규편입/증편/감편/청산)을
개별 미국종목 분석에 패널로 제공. 유료 FMP institutional-ownership 대신
무료 SEC EDGAR 13F-HR 제출 XML 직접 파싱(bot/edgar_client.py 의 기존
CIK조회·submissions·XML네임스페이스 헬퍼 재사용 — Form4 트래커와 동일 패턴,
추가 API 클라이언트 신규작성 없음).

⚠️ 시장특정 예외(US 전용, CLAUDE.md UNIVERSAL CHANGES 원칙의 문서화된
예외): 13F 는 미국 증권거래법(Securities Exchange Act §13(f))상 미국
상장주식 1억달러+ 보유 기관투자자 전용 제도 — KR/JP 대응 개념 자체가
없음(데이터소스 공백이지 market gate 코드가 아님). 호출부(bot/stock_
snapshot.py _enrich_us)만 이 모듈을 부르고, KR/JP enrich 경로는 애초에
호출하지 않는다.

설계:
- 대상 종목의 CIK 가 아니라 **기관투자자(filer)의 CIK** 로 그 기관의 13F
  제출이력을 조회 — 최근 2개 분기 13F-HR 을 비교해 특정 종목 보유수량
  증감(delta) 산출.
- 종목 매칭은 CUSIP 아닌 회사명 substring(13F XML 은 CUSIP 만 제공하고
  ticker→CUSIP 매핑 소스가 시스템에 없어 이름매칭으로 근사 — 문서화된
  한계, 다중 클래스(A/B 등) 매치 시 shares 합산).
- 13F 인포테이블 XML 파일명은 필자마다 달라(SEC 파일명 규격 없음) Form4
  트래커(_parse_form4_xml)와 동일 resilience 패턴 — 제출건의 index JSON 을
  스캔해 <informationTable> 콘텐츠 마커로 실제 문서를 판별(파일명 추측 없음).
- 필자 CIK 는 재무데이터에서 가장 널리 인용되는(고신뢰) 것만 등재
  (_DEFAULT_FILERS) — 프로젝트 '추측보고 금지' 원칙상 불확실한 숫자 ID
  암기를 피함. 신규 필자 추가는 검증 후 이 dict 에 등재.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Optional

log = logging.getLogger("bot.edgar_13f")

_DEFAULT_FILERS = {
    "BERKSHIRE": {"cik": "0001067983", "name": "Berkshire Hathaway Inc"},
}


def parse_infotable_xml(xml_bytes: bytes) -> list:
    """13F INFOTABLE XML(SEC 표준 스키마) 파싱 — 순수함수(테스트용, 네트워크
    무관). [{issuer, cusip, value_usd_thousands, shares, share_type}, ...].
    파싱 실패(빈 바이트열/스키마 불일치) → []."""
    from bot.edgar_client import _find_all, _find_text, _strip_ns
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    rows = []
    for tbl in _find_all(root, "infoTable"):
        issuer = _find_text(tbl, "nameOfIssuer") or ""
        cusip = _find_text(tbl, "cusip") or ""
        value_txt = _find_text(tbl, "value")
        shares_txt = None
        share_type = None
        for el in tbl.iter():
            if _strip_ns(el.tag) == "shrsOrPrnAmt":
                shares_txt = _find_text(el, "sshPrnamt")
                share_type = _find_text(el, "sshPrnamtType")
                break
        try:
            value = float(value_txt) if value_txt else None
        except ValueError:
            value = None
        try:
            shares = float(shares_txt) if shares_txt else None
        except ValueError:
            shares = None
        if not issuer or shares is None:
            continue
        rows.append({"issuer": issuer, "cusip": cusip,
                     "value_usd_thousands": value, "shares": shares,
                     "share_type": share_type or "SH"})
    return rows


def compute_holding_delta(prev_holdings: list, curr_holdings: list,
                          name_substring: str) -> dict:
    """이전분기/이번분기 보유내역에서 name_substring(회사명 일부, 대소문자
    무관) 매칭 종목의 수량증감 산출 — 순수함수(테스트용). 다중매치(클래스
    A/B 등)는 shares 합산. action: NEW/INCREASED/DECREASED/EXITED/
    UNCHANGED/NOT_HELD."""
    needle = name_substring.lower()

    def _sum_shares(holdings):
        return sum(h["shares"] for h in holdings if needle in h["issuer"].lower())

    prev_shares = _sum_shares(prev_holdings)
    curr_shares = _sum_shares(curr_holdings)
    delta = curr_shares - prev_shares

    if prev_shares == 0 and curr_shares == 0:
        action = "NOT_HELD"
    elif prev_shares == 0 and curr_shares > 0:
        action = "NEW"
    elif prev_shares > 0 and curr_shares == 0:
        action = "EXITED"
    elif delta > 0:
        action = "INCREASED"
    elif delta < 0:
        action = "DECREASED"
    else:
        action = "UNCHANGED"

    delta_pct = (delta / prev_shares * 100.0) if prev_shares > 0 else None
    return {
        "prev_shares": prev_shares, "curr_shares": curr_shares,
        "delta_shares": delta,
        "delta_pct": round(delta_pct, 1) if delta_pct is not None else None,
        "action": action,
    }


def find_filer_cik(name_substring: str) -> Optional[str]:
    """등재된 대표 기관투자자(_DEFAULT_FILERS)에서 이름으로 CIK 조회.
    SEC company_tickers.json 은 상장기업 전용이라 자산운용사는 안 잡혀 —
    숫자 CIK 암기 위험을 피하려 고신뢰 목록만 검색(신규 필자는 검증 후
    _DEFAULT_FILERS 에 등재)."""
    needle = name_substring.lower()
    for key, info in _DEFAULT_FILERS.items():
        if needle in info["name"].lower() or needle in key.lower():
            return info["cik"]
    return None


def _recent_13f_filings(cik: str, limit: int = 2) -> list:
    """필자 CIK 의 최근 13F-HR 제출이력(최신순) — [{date, accession}, ...]."""
    from bot.edgar_client import _submissions
    subs = _submissions(cik)
    if not subs:
        return []
    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    out = []
    for i, form in enumerate(forms):
        if form not in ("13F-HR", "13F-HR/A"):
            continue
        out.append({"date": dates[i] if i < len(dates) else "",
                    "accession": accs[i] if i < len(accs) else ""})
        if len(out) >= limit:
            break
    return out


def _fetch_infotable_for_filing(cik: str, accession: str) -> list:
    """한 13F-HR 제출건의 INFOTABLE XML 을 찾아 fetch+파싱. 파일명이 필자마다
    달라(SEC 파일명 규격 없음) index JSON 스캔 + <informationTable> 콘텐츠
    마커로 실제 문서 판별(Form4 트래커 _parse_form4_xml 과 동일 resilience
    패턴). 영구 캐시(확정 제출건은 재변경 없음)."""
    from bot.edgar_client import _ARCH_BASE, _get, _load_cache, _save_cache
    acc_plain = accession.replace("-", "")
    key = f"13f_infotable_{acc_plain}"
    cached = _load_cache(key, max_age_h=24 * 365)
    if cached is not None:
        return cached
    cik_int = int(cik)
    base = _ARCH_BASE.format(cik=cik_int, acc=acc_plain)
    rows: list = []
    try:
        idx_url = f"{base}/{accession}-index.json"
        r = _get(idx_url, timeout=15)
        idx = r.json()
        for item in (idx.get("directory", {}).get("item", []) or []):
            name = item.get("name", "")
            if not name.lower().endswith(".xml"):
                continue
            try:
                r2 = _get(f"{base}/{name}", timeout=20)
            except Exception:
                continue
            if b"informationTable" in r2.content or b"infoTable" in r2.content:
                rows = parse_infotable_xml(r2.content)
                if rows:
                    break
    except Exception as exc:
        log.debug("edgar_13f: infotable fetch failed for %s/%s: %s", cik, accession, exc)
        return []
    if rows:
        _save_cache(key, rows)
    return rows


def get_13f_flow(company_name: str, filer_key: str = "BERKSHIRE") -> Optional[dict]:
    """개별 종목 분석 진입점 — filer_key(_DEFAULT_FILERS)의 최근 2개 분기
    13F 를 비교해 company_name(부분일치) 종목 보유변화 반환. ⚠️ US 전용 —
    market != 'US' 호출부는 애초에 이 함수를 부르지 말 것(모듈 독스트링
    참조, market gate 는 호출부 책임). 데이터없음/필자미등록/네트워크실패
    → None(graceful)."""
    info = _DEFAULT_FILERS.get(filer_key.upper())
    if not info or not company_name:
        return None
    filings = _recent_13f_filings(info["cik"], limit=2)
    if len(filings) < 2:
        return None
    curr = _fetch_infotable_for_filing(info["cik"], filings[0]["accession"])
    prev = _fetch_infotable_for_filing(info["cik"], filings[1]["accession"])
    if not curr and not prev:
        return None
    delta = compute_holding_delta(prev, curr, company_name)
    return {**delta, "filer": info["name"],
            "curr_filing_date": filings[0]["date"],
            "prev_filing_date": filings[1]["date"]}


def format_13f_block(flow: Optional[dict]) -> str:
    """분석 프롬프트 삽입용 텍스트 블록. flow=None/NOT_HELD 이면 빈
    문자열(참고할 신호가 없음 — graceful)."""
    if not flow or flow.get("action") == "NOT_HELD":
        return ""
    action_kr = {"NEW": "신규 편입", "INCREASED": "증편", "DECREASED": "감편",
                "EXITED": "전량 청산", "UNCHANGED": "변동 없음"}.get(
        flow["action"], flow["action"])
    lines = [f"🏦 13F 기관플로우 — {flow['filer']} "
             f"({flow['prev_filing_date']} → {flow['curr_filing_date']})",
             f"  {action_kr}: {flow['prev_shares']:,.0f}주 → {flow['curr_shares']:,.0f}주"]
    if flow.get("delta_pct") is not None:
        lines.append(f"  변동률 {flow['delta_pct']:+.1f}%")
    return "\n".join(lines)
