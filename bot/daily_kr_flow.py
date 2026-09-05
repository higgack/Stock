"""Daily Byte — 장 마감 후 KR 수급 인포그래픽 브리프 (텔레그램, 19:00 KST).

설계 (사용자 결정 2026-05-29):
 • A=pykrx 단일 (무료·안정, KIS 종목별 fan-out 회피)
 • B=Gemini Pro + google_search grounding ON (catalyst / 공시 맥락)
 • C=시총 상위 universe (KOSPI+KOSDAQ 전체에서 net-buy 랭킹 추출)
 • D=기존 NOAH 채널 push (SV pusher 패턴 mirror)
 • E=구조화 long-form (예시 수준: 시장 총평 / 강세 / 로테이션 / TOP / 패턴 / 주목종목 / 경고)
 • F=수급 분석 중심, "주목 종목" 중립 표현 (BUY/SELL 권고 아님 — 교육·정보)

원칙: **수치는 Python 이 pykrx 에서 정확 계산 (환각 0)**, Pro 는 섹터
그룹핑 + 로테이션 narrative + catalyst 맥락 (web search) 만 담당하며
수치는 anchor 그대로 copy. 오늘 audit 의 FUTURE FABRICATION / future-
date citation strip / calendar validator 가드를 post-process 로 재사용.

systemd: daily-byte.timer (19:00 KST) → daily-byte.service oneshot.
수동 실행: cd ~/stock && .venv/bin/python -m bot.daily_kr_flow
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
import os
from bot.genai_factory import effective_key as _effective_key
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bot.daily_kr_flow")

_KST = timezone(timedelta(hours=9))
_EOK = 1e8  # 1억원

# 시장 총평 + per-stock 랭킹에 쓰는 투자 주체. per-stock fetch 는 각각
# try/except 로 감싸 pykrx 가 라벨 미지원 시 graceful skip.
_PER_STOCK_INVESTORS = ["외국인", "기관합계", "연기금", "투신", "사모"]
_TOP_N_BUY = 15
_TOP_N_SELL = 10


def _now_kst() -> datetime:
    return datetime.now(_KST)


def _resolve_trading_date() -> str:
    """오늘 (KST) 기준 가장 최근 거래일 YYYYMMDD. 주말이면 직전 금요일.
    공휴일은 pykrx fetch 가 빈 결과면 호출측에서 하루씩 walk-back."""
    d = _now_kst().date()
    while d.weekday() >= 5:  # 5=토, 6=일
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _prev_bday(yyyymmdd: str, n: int) -> str:
    d = datetime.strptime(yyyymmdd, "%Y%m%d").date()
    cnt = 0
    while cnt < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            cnt += 1
    return d.strftime("%Y%m%d")


def _fetch_market_totals(date: str) -> dict:
    """시장 전체 투자주체별 순매수 (KOSPI+KOSDAQ 합산), 단위 억원.
    pykrx get_market_trading_value_by_investor 사용 — 라벨을 substring
    매칭해 외국인/기관/개인/투신/연기금/사모/금융투자/보험/은행 추출."""
    try:
        from pykrx import stock
    except Exception as exc:
        log.warning("daily_byte: pykrx import failed: %s", exc)
        return {}

    want = {
        "외국인": "외국인", "개인": "개인", "기관": "기관합계",
        "투신": "투신", "연기금": "연기금", "사모": "사모",
        "금융투자": "금융투자", "보험": "보험", "은행": "은행",
    }
    totals: dict[str, float] = {}
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_trading_value_by_investor(date, date, market)
        except Exception as exc:
            log.warning("daily_byte: totals %s %s failed: %s", market, date, exc)
            continue
        if df is None or df.empty:
            continue
        net_col = next((c for c in df.columns if "순매수" in str(c)), None)
        if net_col is None:
            continue
        for idx in df.index:
            label = str(idx)
            for key, _canon in want.items():
                if key in label or _canon in label:
                    try:
                        totals[key] = totals.get(key, 0.0) + float(df.loc[idx, net_col]) / _EOK
                    except Exception:
                        pass
                    break
    return totals


def _fetch_stock_net(date_from: str, date_to: str, investor: str) -> dict:
    """per-stock 순매수거래대금 (억원) for one investor over [from,to].
    KOSPI+KOSDAQ 합산. {ticker: {'name':..., 'net':억원}}. 라벨 미지원/
    실패 시 빈 dict (graceful)."""
    try:
        from pykrx import stock
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_net_purchases_of_equities(
                date_from, date_to, market, investor,
            )
        except Exception as exc:
            log.debug("daily_byte: net %s %s %s failed: %s", investor, market, date_to, exc)
            continue
        if df is None or df.empty:
            continue
        name_col = next((c for c in df.columns if "종목명" in str(c)), None)
        net_col = next(
            (c for c in df.columns if "순매수" in str(c) and "대금" in str(c)), None
        )
        if net_col is None:
            net_col = next((c for c in df.columns if "순매수" in str(c)), None)
        if net_col is None:
            continue
        for tkr in df.index:
            try:
                net_eok = float(df.loc[tkr, net_col]) / _EOK
            except Exception:
                continue
            name = str(df.loc[tkr, name_col]) if name_col else str(tkr)
            out[str(tkr)] = {"name": name, "net": net_eok}
    return out


def _top(flows: dict, n: int, reverse: bool = True) -> list:
    """flows {ticker:{name,net}} → [(ticker, name, net)] sorted by net."""
    items = [(t, v["name"], v["net"]) for t, v in flows.items()]
    items.sort(key=lambda x: x[2], reverse=reverse)
    return items[:n]


def _fetch_price_change(date: str) -> dict:
    """per-ticker 등락률(%) {ticker: pct}. KOSPI+KOSDAQ 합산. 실패 시 {}."""
    try:
        from pykrx import stock
    except Exception:
        return {}
    out: dict[str, float] = {}
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_price_change(date, date, market=market)
        except Exception as exc:
            log.debug("daily_byte: price_change %s %s failed: %s", market, date, exc)
            continue
        if df is None or df.empty:
            continue
        col = next((c for c in df.columns if "등락" in str(c)), None)
        if col is None:
            continue
        for tkr in df.index:
            try:
                out[str(tkr)] = float(df.loc[tkr, col])
            except Exception:
                pass
    return out


def _fetch_mcap_eok(date: str) -> dict:
    """per-ticker 시가총액(억원) {ticker: 억}. KOSPI+KOSDAQ. 실패 시 {}."""
    try:
        from pykrx import stock
    except Exception:
        return {}
    out: dict[str, float] = {}
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_cap_by_ticker(date, market=market)
        except Exception as exc:
            log.debug("daily_byte: mcap %s %s failed: %s", market, date, exc)
            continue
        if df is None or df.empty:
            continue
        col = next((c for c in df.columns if "시가총액" in str(c)), None)
        if col is None:
            continue
        for tkr in df.index:
            try:
                out[str(tkr)] = float(df.loc[tkr, col]) / _EOK
            except Exception:
                pass
    return out


def collect_flow_data(date: str) -> dict:
    """모든 pykrx fetch 를 모아 구조화 dict 반환. 수치는 전부 정확값."""
    d5 = _prev_bday(date, 4)  # 5거래일 윈도 (당일 포함)
    data: dict = {
        "date": date,
        "date_5d_from": d5,
        "totals": _fetch_market_totals(date),
        "today": {},   # investor → {top_buy:[...], top_sell:[...]}
        "cum5d": {},    # investor → top_buy:[...]
    }
    d20 = _prev_bday(date, 19)  # 20거래일 윈도
    data["date_20d_from"] = d20
    data["cum20d"] = {}
    for inv in _PER_STOCK_INVESTORS:
        today_flows = _fetch_stock_net(date, date, inv)
        if today_flows:
            data["today"][inv] = {
                "top_buy": _top(today_flows, _TOP_N_BUY, reverse=True),
                "top_sell": _top(today_flows, _TOP_N_SELL, reverse=False),
                "_raw": today_flows,
            }
        cum_flows = _fetch_stock_net(d5, date, inv)
        if cum_flows:
            data["cum5d"][inv] = {"top_buy": _top(cum_flows, _TOP_N_BUY, reverse=True),
                                  "_raw": cum_flows}
    # 20일 누적 — 핵심 2주체(외국인·기관합계)만 (call 볼륨 절약)
    for inv in ("외국인", "기관합계"):
        cum20 = _fetch_stock_net(d20, date, inv)
        if cum20:
            data["cum20d"][inv] = {"top_buy": _top(cum20, _TOP_N_BUY, reverse=True)}
    # 양→음 전환: 5일 누적 강매수인데 당일 순매도인 종목 (외국인/기관)
    reversals = []
    for inv in ("외국인", "기관합계", "투신"):
        today_raw = data["today"].get(inv, {}).get("_raw", {})
        cum = dict((t, n) for t, nm, n in data["cum5d"].get(inv, {}).get("top_buy", []))
        for tkr, cum_net in cum.items():
            t_net = today_raw.get(tkr, {}).get("net")
            if cum_net > 50 and t_net is not None and t_net < -30:
                reversals.append((inv, tkr, today_raw[tkr]["name"], cum_net, t_net))
    data["reversals"] = reversals[:8]

    # 등락률 + 시총 (per-ticker enrich) — 실패 시 빈 dict (graceful)
    data["chg"] = _fetch_price_change(date)
    data["mcap_eok"] = _fetch_mcap_eok(date)

    # breadth: 외국인+기관합계 합산 net 기준 순매수종목 비율
    fg_raw = data["today"].get("외국인", {}).get("_raw", {})
    in_raw = data["today"].get("기관합계", {}).get("_raw", {})
    if fg_raw or in_raw:
        combined: dict[str, float] = {}
        for t, v in fg_raw.items():
            combined[t] = combined.get(t, 0.0) + v["net"]
        for t, v in in_raw.items():
            combined[t] = combined.get(t, 0.0) + v["net"]
        total_n = len(combined)
        net_buy_n = sum(1 for n in combined.values() if n > 0)
        if total_n:
            data["breadth"] = {
                "pct": round(net_buy_n / total_n * 100, 1),
                "net_buy_n": net_buy_n, "total_n": total_n,
            }
    return data


def _fmt_top(rows: list, data: dict | None = None) -> str:
    """각 행에 등락률 + 시총 + net/시총 비중을 가능 시 병기 (강화)."""
    chg = (data or {}).get("chg") or {}
    mcap = (data or {}).get("mcap_eok") or {}
    out = []
    for t, nm, net in rows:
        line = f"    {nm} ({t}) {('+' if net >= 0 else '')}{net:,.0f}억"
        c = chg.get(str(t))
        if isinstance(c, (int, float)):
            line += f"  등락 {c:+.1f}%"
        mc = mcap.get(str(t))
        if isinstance(mc, (int, float)) and mc > 0:
            wt = abs(net) / mc * 100
            mc_disp = f"{mc/10000:.1f}조" if mc >= 10000 else f"{mc:,.0f}억"
            line += f"  시총 {mc_disp} (net/시총 {wt:.2f}%)"
        out.append(line)
    return "\n".join(out) or "    (데이터 없음)"


def build_data_summary(data: dict) -> str:
    """Pro 에 주입할 구조화 데이터 텍스트. 모든 수치 = pykrx 정확값."""
    lines = [f"[거래일] {data['date']}  ([5일 윈도] {data['date_5d_from']}~{data['date']}"
             f"  [20일 윈도] {data.get('date_20d_from','?')}~{data['date']})"]
    b = data.get("breadth")
    if b:
        lines.append(f"[시장 폭] 순매수종목 비율(외인+기관 합산) {b['pct']}% "
                     f"({b['net_buy_n']}/{b['total_n']}종목 순매수)")
    # 시장 유동성 (예탁금·신용융자) — 금융투자협회 종합통계, 실패 시 생략.
    try:
        from bot.fsc_client import market_liquidity_line
        _liq = market_liquidity_line()
        if _liq:
            lines.append(f"[시장 유동성 (금융투자협회 종합통계)] {_liq} "
                         "— 예탁금↑=대기매수, 신용융자↑=레버리지 과열")
    except Exception as exc:
        log.debug("daily_byte: liquidity line skipped: %s", exc)
    lines.append("\n[시장 전체 투자주체별 순매수 (억원, KOSPI+KOSDAQ 합산)]")
    t = data.get("totals", {})
    if t:
        for k in ("외국인", "개인", "기관", "투신", "연기금", "사모", "금융투자", "보험", "은행"):
            if k in t:
                v = t[k]
                lines.append(f"  {k}: {('+' if v >= 0 else '')}{v:,.0f}억")
    else:
        lines.append("  (시장 총평 데이터 미수집)")
    for inv in _PER_STOCK_INVESTORS:
        td = data["today"].get(inv)
        if td:
            lines.append(f"\n[{inv} 당일 순매수 상위]")
            lines.append(_fmt_top(td["top_buy"], data))
            lines.append(f"[{inv} 당일 순매도 상위]")
            lines.append(_fmt_top(td["top_sell"], data))
        cd = data["cum5d"].get(inv)
        if cd:
            lines.append(f"[{inv} 5일 누적 순매수 상위]")
            lines.append(_fmt_top(cd["top_buy"], data))
        c20 = data.get("cum20d", {}).get(inv)
        if c20:
            lines.append(f"[{inv} 20일 누적 순매수 상위]")
            lines.append(_fmt_top(c20["top_buy"], data))
    if data.get("reversals"):
        lines.append("\n[양→음 전환 의심 (5일 누적 매수 vs 당일 매도)]")
        for inv, tkr, nm, cum_net, t_net in data["reversals"]:
            lines.append(f"  {nm} ({tkr}) [{inv}] 5일 +{cum_net:,.0f}억 → 당일 {t_net:,.0f}억")
    return "\n".join(lines)


_PROMPT = """당신은 한국 주식시장 수급 전문 buy-side 애널리스트입니다. 아래는
{date} 장 마감 후 pykrx 에서 직접 산출한 **정확한 투자주체별 수급 수치**
입니다. 이 수치를 바탕으로 'Daily Byte' 일일 수급 브리프를 작성하세요.

{data_summary}

---

작성 규칙:
1. **수치는 위 데이터를 글자 그대로 인용** — 재계산·반올림·창작 절대 금지.
   위에 없는 종목/수치를 지어내지 말 것. 등락률·시총·net/시총 비중·breadth
   도 위 값 그대로 사용.
2. **다중 시간축 해석 (당일 vs 5일 vs 20일)**: 같은 종목이 당일·5일·20일
   누적에서 모두 상위면 "**가속 국면**", 20일엔 상위였으나 당일 빠지면
   "차익실현/둔화"로 구분. 단순 당일 나열이 아니라 시간축 추세로 서술.
3. **섹터 그룹핑 + 로테이션**: 위 종목들을 산업/테마로 묶어 (반도체/AI·SW/
   2차전지/전기차/원전·전력/바이오 등) 자금 흐름 방향을 서술. 유출→유입
   섹터를 명시. 종목→섹터 분류는 당신의 지식 + web search 로.
4. **시총 대비 비중 강조**: net/시총 비중이 높은 소형·중형주(시총 대비 큰
   매집)는 "시총 대비 압도적 매집"으로 별도 표시 — 대형주 절대금액과 구분.
5. **catalyst 맥락 (web search 활용)**: 상위 종목의 최근 공시/실적/뉴스
   (수주·실적·M&A)를 web search 로 확인해 "왜 이 자금이 들어왔나" 맥락 +
   구체 날짜 제공. **출처 날짜는 반드시 {date} 이하** — 미래 날짜 citation
   금지. 확인 안 되면 '맥락 미확인' 명시, 추측 catalyst 창작 금지.
6. **중립 표현**: "주목 종목" 은 수급 관찰일 뿐 BUY/SELL 권고가 아님.
   '매수 추천' / '목표가' 같은 직접 투자권유 표현 금지.
7. **구조** (각 섹션 지정 이모지로 시작하는 헤더 한 줄):
   📊 시장 수급 총평 (외인 vs 기관 디커플링 + breadth % 해석)
   🔥 지금 강한 섹터·종목 (4대 주체 동시매수 + 당일/5일/20일 가속 우선)
   🔄 섹터 로테이션 (유출 섹터 → 유입 섹터, 규모 명시)
   💰 당일 기관(투신+사모) 합산 상위 (등락률 병기)
   📈 주목할 수급 패턴 (꾸준한 매집 / 첫 대량출현 / 시총대비 강한 매집)
   🏆 주목 종목 5선 (각 종목: 주체별 net + 등락률 + catalyst, 중립)
   ⚠️ 경고 시그널 (양→음 전환 + 가격·수급 다이버전스)
   🎯 한 줄 결론 (포지셔닝 관점, 중립)
8. **본문은 일반 텍스트** + 섹션마다 위 지정 이모지로 시작하는 헤더 한 줄.
   **굵게(`**`)는 섹션 헤더·핵심 수치에만 최소 사용** — catalyst·맥락·설명
   문장은 일반 텍스트(굵게 금지). `---` 같은 수평선/구분선 절대 쓰지 말 것
   (섹션 이모지 헤더로 구분). HTML 태그·`<br>`·`<ul>` 금지. 각 항목 줄바꿈.
9. 분량: 벤치마크 수준의 정보 밀도로 충실하게. 데이터에 있는 종목만 다룰 것.

제목: 첫 줄은 정확히 "{date_kr} 한국 증시 데일리 브리프" 한 줄 (다른 형식
금지). 면책·디스클레이머("투자 권유 아님"·"교육·정보 목적" 등) 문구는 본문
어디에도 포함하지 말 것 — 채널·페이지 차원에서 별도 표기됨 (사용자 정책
2026-06-11).
"""


def build_prompt(data: dict) -> str:
    d = str(data["date"])
    date_kr = (f"{int(d[:4])}년 {int(d[4:6])}월 {int(d[6:8])}일"
               if len(d) == 8 and d.isdigit() else d)
    return _PROMPT.format(date=data["date"], date_kr=date_kr,
                          data_summary=build_data_summary(data))


def _post_process(text: str, date_iso: str) -> str:
    """오늘 audit 가드(미래 날짜 citation strip + invalid 날짜 validator)
    재사용 후 Telegram-safe HTML 로 변환.

    Gemini 가 내는 HTML 은 이스케이프 안 된 &/< 나 미지원 태그(<br>,<ul>,
    <li>)를 섞어 sendMessage 가 400 → 태그 통째로 strip 한 plain-text
    fallback 으로 전송되며 <b> 제목·헤더 서식이 전부 날아간다 (2026-05-29
    첫 실데이터 실행 surfaced). 따라서 (1) 모든 태그/엔티티를 먼저
    중화(strip+escape)해 400 을 구조적으로 차단하고, (2) 프롬프트가 정의한
    이모지 섹션 헤더 8개 + **bold** 만 <b> 로 재적용한다."""
    try:
        from bot.screener import _strip_future_dated_citations, _strip_invalid_dates
        text, _ = _strip_future_dated_citations(text, date_iso)
        text, _ = _strip_invalid_dates(text)
    except Exception as exc:
        log.debug("daily_byte: post-process guard failed: %s", exc)
    import html as _html
    import re
    # 1) Gemini emit 태그 제거 + 엔티티 정규화 → escape (이후 400 불가).
    #    실제 태그(<b> <br> <ul> ...)만 제거 — 'P<10' 같은 부등호는 보존.
    text = _html.unescape(text)
    text = re.sub(r"</?[a-zA-Z][^>\n]*?>", "", text)
    text = _html.escape(text, quote=False)
    # 2) markdown 강조/리스트/헤더 정리
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)         # **x** → 볼드
    text = re.sub(r"(?m)^\s*[\*\-]\s+", "• ", text)                 # 불릿 → •
    text = re.sub(r"(?m)^\s*#{1,6}\s*([^\n]+)$", r"<b>\1</b>", text)  # ## 헤더
    # 수평선/구분선 줄 제거 — 단어문자(한글·영문·숫자) 없이 대시류(2+연속)만
    # 있는 줄. '---', '***', '___', '--- / ---' 등 LLM 이 내는 모든 separator
    # 변형 차단 (사용자 2026-05-29 '--- 거슬림', 슬래시 혼합형 포함).
    text = re.sub(r"(?m)^[^\w\n]*[-*_]{2,}[^\w\n]*$", "", text)
    # 면책/디스클레이머 줄 제거 — 프롬프트 금지 directive 의 Python 백스톱
    # (사용자 정책 2026-06-11: 시장과 무관한 보일러플레이트 본문 포함 금지).
    # 공유 호출처(Daily Byte 일/주간 + 부동산 + 청약) 전부 universal 적용.
    text = re.sub(r"(?m)^(?=.*투자\s*권유)(?=.*아(?:님|닙)).*$", "", text)
    text = re.sub(r"(?m)^\s*면책.*$", "", text)
    # 2.5) Gemini 제목 줄 (첫 줄, 이모지 없는 "Daily Byte ..." 등) 뒤에
    #      빈 줄 보장 — 제목과 첫 섹션 헤더가 붙지 않게 (사용자 2026-06-08).
    lines = text.split("\n")
    if lines and lines[0].strip() and not any(lines[0].startswith(e) for e in
            ("📊", "🔥", "🔄", "💰", "📈", "🏆", "⚠️", "🎯", "🏠", "📍", "🏗️", "📐", "🎟️")):
        first = lines[0]
        rest = "\n".join(lines[1:]).lstrip("\n")
        text = f"<b>{first}</b>\n\n{rest}"
    # 3) 이모지 섹션 헤더 줄 → 앞 빈 줄 보장 + 볼드. Daily Byte 8개 +
    #    부동산(🏠📍🏗️📐) + 청약(🎟️) union — 헤더가 본문에 딱 붙지 않게
    #    헤더 앞 한 줄 띄움 (사용자 2026-05-31). universal: 세 브리프 공통.
    _hdr = "|".join(("📊", "🔥", "🔄", "💰", "📈", "🏆", "⚠️", "🎯",
                     "🏠", "📍", "🏗️", "📐", "🎟️"))
    # 헤더 줄 leading whitespace 제거
    text = re.sub(rf"(?m)^[ \t]+(?=(?:{_hdr}))", "", text)
    # 헤더 줄 앞에 빈 줄 삽입 (이미 비어있으면 아래 collapse 가 정리).
    text = re.sub(rf"(?m)^(?=(?:{_hdr}))", "\n", text)
    text = re.sub(rf"(?m)^((?:{_hdr})[^\n]*)$", r"<b>\1</b>", text)
    # 헤더 다음 빈 줄 제거 → 제목을 본문에 붙임 (다음 줄도 헤더면 보존)
    text = re.sub(rf"(?m)^(<b>(?:{_hdr})[^\n]*</b>)\n+(?=\S)(?!<b>(?:{_hdr}))", r"\1\n", text)
    # 연속 빈 줄 정리 (--- 제거 + 헤더 빈 줄 삽입으로 생긴 공백 포함)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _iso_dot(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:]}"


# ── 비용 로깅 + 아카이브 (screener 패턴 mirror) ───────────────────────────
_HOME = os.path.expanduser("~")
_DAILY_BYTE_ARCHIVE_DIR = os.path.join(_HOME, ".tradingagents", "daily_byte_archive")
_DAILY_BYTE_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "daily_byte_usage.jsonl")
_NOAH_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "usage.jsonl")
# 인포그래픽 PNG 는 대시보드 HTTP 서버가 서빙하는 archive/ 아래에 저장 →
# daily_byte.html 카드에서 <img src="daily_byte_img/..."> 로 임베드.
_DASH_ARCHIVE_ROOT = os.path.join(_HOME, ".tradingagents", "archive")
_DBYTE_IMG_DIR = os.path.join(_DASH_ARCHIVE_ROOT, "daily_byte_img")
_USD_TO_KRW_FALLBACK = 1330.0


def _log_daily_byte_usage(pt: int, ot: int, cost_krw: float) -> None:
    """Dual-log: daily_byte_usage.jsonl (KST date/month tagged — /daily_byte_
    cost 용) + ~/.tradingagents/usage.jsonl (NOAH llm_call, **subsystem=
    'daily_byte'**). 후자로 /usage 합산 + 메인 대시보드 cost 카드 subsystem
    분포가 자동 갱신된다. screener._log_usage 는 subsystem='screener' 하드
    코딩이라 재사용 불가 — 별도 로거."""
    import json as _json
    import time as _time
    try:
        os.makedirs(os.path.dirname(_DAILY_BYTE_USAGE_LOG), exist_ok=True)
        now = _now_kst()
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "date": now.date().isoformat(),
            "month": now.date().isoformat()[:7],
            "prompt_tok": pt, "output_tok": ot,
            "cost_krw": round(cost_krw, 4),
        }
        with open(_DAILY_BYTE_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("daily_byte: usage log write failed: %s", exc)
    try:
        os.makedirs(os.path.dirname(_NOAH_USAGE_LOG), exist_ok=True)
        try:
            from bot.screener import _USD_TO_KRW as _fx
        except Exception:
            _fx = _USD_TO_KRW_FALLBACK
        rec_noah = {
            "ts": _time.time(), "type": "llm_call", "model": "gemini-2.5-pro",
            "prompt_tokens": pt, "completion_tokens": ot,
            "cost_usd": round(cost_krw / _fx, 6), "subsystem": "daily_byte",
        }
        with open(_NOAH_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec_noah, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("daily_byte: NOAH usage log write failed: %s", exc)


def _save_daily_byte_archive(body: str, cost_krw: float, date_yyyymmdd: str,
                             elapsed_sec: float = 0.0, kind: str = "daily",
                             png_rel: str | None = None) -> str | None:
    """Write run → ~/.tradingagents/daily_byte_archive/YYYY-MM-DD/HHMMSS_
    daily_byte[_weekly].json (screener archive mirror) → regenerate
    daily_byte.html. body = post-processed 브리프 (Python 제목/부제 제외 —
    카드 헤더가 date·kind 표시). png_rel = archive/ 기준 인포그래픽 상대경로
    (대시보드 카드 <img> 임베드용). 실패 시 None."""
    import json as _json
    try:
        now = _now_kst()
        date_iso = f"{date_yyyymmdd[:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:]}"
        day_dir = os.path.join(_DAILY_BYTE_ARCHIVE_DIR, date_iso)
        os.makedirs(day_dir, exist_ok=True)
        slug = "daily_byte_weekly" if kind == "weekly" else "daily_byte"
        path = os.path.join(day_dir, f"{now:%H%M%S}_{slug}.json")
        rec = {
            "ts": now.isoformat(timespec="seconds"), "date": date_iso,
            "kind": kind, "body": body, "cost_krw": round(cost_krw, 4),
            "elapsed_sec": round(elapsed_sec, 1), "png": png_rel,
        }
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(rec, f, ensure_ascii=False)
    except Exception as exc:
        log.warning("daily_byte: archive write failed: %s", exc)
        return None
    try:
        from bot.dashboard import regenerate_daily_byte_index
        regenerate_daily_byte_index()
    except Exception as exc:
        log.warning("daily_byte: dashboard regen failed: %s", exc)
    return path


def generate(*, archive: bool = True) -> tuple[str, float, str | None] | None:
    """수급 fetch → Pro narrate → guard → archive + 인포그래픽 →
    (제목 포함 본문, cost_krw, png_path|None). 데이터 전무 시 None."""
    import time as _time
    _t0 = _time.monotonic()
    api_key = _effective_key()
    if not api_key:
        log.error("daily_byte: GOOGLE_API_KEY missing")
        return None

    # KRX 로그인 자격증명 preflight — KRX 가 2025-12-27 부터 'KRX Data
    # Marketplace' 로그인 필수로 전환. KRX_ID/KRX_PW 미설정 시 모든 pykrx
    # fetch 가 JSONDecodeError + 내부 logging 버그로 실패·로그 폭주하므로
    # 호출 전 차단하고 조용히 skip (creds 추가되면 즉시 작동).
    try:
        from bot.pykrx_client import krx_login_ready, _quiet_pykrx_logging
        _quiet_pykrx_logging()
        if not krx_login_ready():
            log.warning(
                "daily_byte: KRX_ID/KRX_PW 미설정 — pykrx 수급 fetch 불가, "
                "Daily Byte skip. KRX Data Marketplace 무료 가입(Naver/Kakao) "
                "후 .env 에 KRX_ID/KRX_PW 추가 필요."
            )
            return None
    except Exception:
        pass

    date = _resolve_trading_date()
    data = collect_flow_data(date)
    # 데이터 부재 (공휴일 등) → 하루씩 walk-back 최대 4회
    tries = 0
    while not data.get("totals") and not data.get("today") and tries < 4:
        date = _prev_bday(date, 1)
        data = collect_flow_data(date)
        tries += 1
    if not data.get("totals") and not data.get("today"):
        log.warning("daily_byte: no flow data for %s (after walk-back)", date)
        return None

    from bot.screener import _call_pro, _USD_TO_KRW
    _PRO_IN, _PRO_OUT = 1.25, 10.00
    prompt = build_prompt(data)
    try:
        raw, pt, ot = _call_pro(api_key, prompt, enable_grounding=True)
    except Exception as exc:
        log.exception("daily_byte: Pro call failed: %s", exc)
        return None
    if not raw:
        return None

    body = _post_process(raw, _iso_dot(date).replace(".", "-"))
    cost_krw = (pt * _PRO_IN + ot * _PRO_OUT) / 1e6 * _USD_TO_KRW
    _log_daily_byte_usage(pt, ot, cost_krw)

    # 인포그래픽 PNG (수급 데이터 = pykrx 정확값 직접 주입, 환각 0).
    # 대시보드가 서빙하는 archive/daily_byte_img/ 에 저장 → 카드 <img> 임베드
    # + 텔레그램 사진 push. NanumGothic 부재 시 None → 텍스트만.
    png_path = None
    png_rel = None
    try:
        from bot.daily_byte_infographic import render_infographic
        fname = f"{date}_{_now_kst():%H%M%S}.png"
        # ⚠️ `archive=False`(진단) 면 대시보드가 **서빙하는** 디렉터리에
        # 파일을 남기지 않는다 — 렌더가 되는지는 임시 경로로도 증명된다.
        out = os.path.join(
            _DBYTE_IMG_DIR if archive else tempfile.mkdtemp(prefix="dbyte-dry-"),
            fname)
        png_path = render_infographic(data, _iso_dot(date), out)
        if png_path and archive:
            png_rel = f"daily_byte_img/{fname}"
    except Exception as exc:
        log.warning("daily_byte: infographic render failed: %s", exc)

    # 아카이브 (대시보드 카드용 — Python 제목/부제 제외 본문 + 인포그래픽
    # 상대경로) + daily_byte.html regenerate. **푸시 성공 여부와 무관하게**
    # 기록한다(푸시가 실패해도 화면엔 남는다).
    # ⚠️ 단 **진단(`--dry-run`)에서는 쓰지 않는다** — 2026-09-05 실측:
    # dry-run 이 09-04 아카이브를 남기고 daily_byte.html 을 다시 그려,
    # 텔레그램엔 안 간 브리프가 화면엔 있고 `--why` ⑤ 가 '마지막 기록
    # 2026-09-04' 로 **공백이 메워진 것처럼** 보고하게 됐다. 진단이 자기가
    # 읽을 신호를 오염시킨 것이다(#30 의 진단판 · #264).
    if archive:
        _save_daily_byte_archive(body, cost_krw, date,
                                 elapsed_sec=_time.monotonic() - _t0,
                                 kind="daily", png_rel=png_rel)

    title = f"📰 <b>한국 Daily Byte - {_iso_dot(date)}</b>"
    full = f"{title}\n<i>장 마감 후 KR 수급 브리프 · 생성 {_now_kst():%H:%M} KST</i>\n\n{body}"
    return full, cost_krw, png_path


# ── Telegram push (SV pusher 패턴 mirror) ─────────────────────────────────

_TG_LIMIT = 4096
_CHUNK = 3800


def _chunk(text: str) -> list[str]:
    if len(text) <= _CHUNK:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > _CHUNK and cur:
            out.append(cur.rstrip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur.rstrip())
    return out


def push_telegram(text: str) -> bool:
    import httpx
    token = (
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        or os.environ.get("STANDARDVIEW_TELEGRAM_TOKEN", "").strip()
    )
    raw_ids = os.environ.get("CHANNEL_CHAT_IDS", "").strip()
    chat_ids = [c.strip() for c in raw_ids.split(",") if c.strip()]
    if not token or not chat_ids:
        log.error("daily_byte: TELEGRAM_BOT_TOKEN / CHANNEL_CHAT_IDS missing")
        return False
    api = f"https://api.telegram.org/bot{token}"
    chunks = _chunk(text)
    ok_all = True
    for chat in chat_ids:
        for i, msg in enumerate(chunks):
            params = {"chat_id": chat, "text": msg,
                      "parse_mode": "HTML", "disable_web_page_preview": True}
            try:
                r = httpx.post(f"{api}/sendMessage", json=params, timeout=20)
                if r.status_code != 200:
                    # HTML parse 실패 → plain-text fallback (SV 패턴)
                    params.pop("parse_mode", None)
                    import re
                    params["text"] = re.sub(r"<[^>]+>", "", msg)
                    r = httpx.post(f"{api}/sendMessage", json=params, timeout=20)
                    if r.status_code != 200:
                        log.warning("daily_byte: chunk %d → %d %s",
                                    i + 1, r.status_code, r.text[:160])
                        ok_all = False
            except Exception as exc:
                log.warning("daily_byte: push chunk %d failed: %s", i + 1, exc)
                ok_all = False
    return ok_all


def push_telegram_photo(png_path: str, caption: str = "") -> bool:
    """인포그래픽 PNG 를 채널에 사진으로 push (sendPhoto). 실패해도 텍스트
    push 는 별개로 진행되므로 best-effort."""
    if not png_path or not os.path.exists(png_path):
        return False
    import httpx
    token = (
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        or os.environ.get("STANDARDVIEW_TELEGRAM_TOKEN", "").strip()
    )
    raw_ids = os.environ.get("CHANNEL_CHAT_IDS", "").strip()
    chat_ids = [c.strip() for c in raw_ids.split(",") if c.strip()]
    if not token or not chat_ids:
        return False
    api = f"https://api.telegram.org/bot{token}"
    ok_all = True
    for chat in chat_ids:
        try:
            with open(png_path, "rb") as f:
                r = httpx.post(
                    f"{api}/sendPhoto",
                    data={"chat_id": chat, "caption": caption[:1024],
                          "parse_mode": "HTML"},
                    files={"photo": ("daily_byte.png", f, "image/png")},
                    timeout=40,
                )
            if r.status_code != 200:
                log.warning("daily_byte: sendPhoto → %d %s", r.status_code, r.text[:160])
                ok_all = False
        except Exception as exc:
            log.warning("daily_byte: sendPhoto failed: %s", exc)
            ok_all = False
    return ok_all


def main() -> int:
    import sys as _sys
    if "--why" in _sys.argv[1:]:
        return why(_sys.argv[1:])
    if "--dry-run" in _sys.argv[1:]:
        return dry_run()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    # httpx 가 sendMessage URL(봇 토큰 포함)을 INFO 로 찍어 journald 에
    # 토큰이 평문으로 쌓이는 상시 노출 차단 (2026-05-29 surfaced).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception:
        pass
    # 실행됐다는 도장 — 화면이 '타이머가 안 돌았다' 와 '돌았는데 재료가
    # 없었다' 를 구별할 수 있게(#52). ⚠️ **거래일 게이트보다 앞**에 찍는다:
    # 뒤에 두면 금요일 공휴일 + 주말에 96시간 공백이 생겨 멀쩡한 타이머가
    # ⚠️ 로 뜬다(독립 리뷰 실측). 이 도장의 뜻은 '유닛이 돌았다' 뿐이고,
    # 브리프가 나왔는지는 아카이브 날짜가 말한다 — 둘을 합쳐야 갈래가
    # 갈린다(#82). 형제(US)도 조건 없이 찍으므로 판정 기준이 같다(#38).
    try:
        from bot import feed_health
        feed_health.mark("daily_byte_kr")
    except Exception:                                          # noqa: BLE001
        pass
    # 한국 거래일에만 — 주말 + 한국 공휴일이면 graceful skip (사용자 정책
    # 2026-06-06). is_trading_day 가 주말·공휴일 모두 커버. 캘린더 라이브러리
    # 부재 시 None → 기존 동작 폴백(진행, 회귀 0). 주간브리프(daily_kr_weekly.py,
    # 일 22시)는 별도 스크립트라 이 게이트가 안 닿아 그대로 발송.
    try:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        from bot.market_calendar import is_trading_day
        _today_kst = _dt.now(_tz(_td(hours=9))).strftime("%Y-%m-%d")
        if is_trading_day("KR", _today_kst) is False:
            log.info("daily_byte: KR 휴장일(%s) — skip (주말/공휴일)", _today_kst)
            return 0
    except Exception as exc:
        log.debug("daily_byte: trading-day gate skipped (%s)", exc)
    result = generate()
    if result is None:
        log.error("daily_byte: generation failed / no data — skipping push")
        return 1
    body, cost, png = result
    log.info("daily_byte: generated (₩%.1f, infographic=%s) — pushing",
             cost, "yes" if png else "no")
    # 인포그래픽 먼저(사진) → 텍스트 브리프. 사진 실패해도 텍스트는 진행.
    if png:
        if push_telegram_photo(png, "📰 Daily Byte — KR 수급 인포그래픽"):
            log.info("daily_byte: infographic photo pushed")
    ok = push_telegram(body)
    log.info("daily_byte: push %s", "OK" if ok else "with failures")
    return 0 if ok else 1




# ── 진단: 지금 만들면 되나 (사용자 2026-09-05) ────────────────────────
# `--why` 는 **재료**(자격증명·KRX 실호출·아카이브)까지만 잰다. 재료가
# 멀쩡해도 생성(LLM)·푸시는 다른 층이고, 그 층은 09-04 19:06 실패 이후 한
# 번도 안 돌았다 — 그런데 다음 거래일 저녁까지 기다려야만 알 수 있으면
# "제대로 오는거야?" 에 매번 '기다려 보세요' 로 답하게 된다(#79 그 경로가
# 실제로 실행됐나를 먼저 답할 것).
# 그래서 **푸시 없이** 생성만 태워 본다. 휴장일에도 돈다(그게 요점이다).
# ⚠️ LLM 을 실제로 부르므로 **요금이 든다** — 그래서 자동 실행이 아니라
# 사람이 명시적으로 부르는 플래그다(에이전트가 임의 과금 유발 금지).
def dry_run() -> int:
    """`--dry-run` — 생성만 태워 보고 **푸시는 안 한다**. 요금이 든다."""
    import sys

    print(f"[Daily Byte KR dry-run v{_WHY_VER}]")
    print(f"① 인터프리터: {sys.executable}")
    print("   ⚠️ LLM 을 실제로 부릅니다(요금 발생) · 텔레그램 푸시는 **안 합니다**")
    print("   ⚠️ 아카이브·대시보드에도 **안 씁니다** — 진단이 자기가 읽을 "
          "신호를 오염시키면 안 된다(#264)")
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception as exc:                                   # noqa: BLE001
        print(f"   ⚠️ .env 로드 실패: {exc}")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # 거래일 게이트는 **일부러 건너뛴다** — 휴장일에 '되는지' 를 묻는 게
    # 이 플래그의 존재 이유다. 그 사실을 밝힌다(#43 침묵이 최악).
    try:
        from bot.market_calendar import is_trading_day
        td = is_trading_day("KR", _now_kst().strftime("%Y-%m-%d"))
        if td is False:
            print("② 오늘은 휴장 — 정기 실행이라면 skip 이지만 dry-run 은 "
                  "게이트를 건너뛴다(마지막 거래일 재료로 생성)")
    except Exception as exc:                                   # noqa: BLE001
        print(f"② 거래일 판정 불가({exc}) — 그대로 진행")
    t0 = time.time()
    try:
        result = generate(archive=False)
    except Exception as exc:                                   # noqa: BLE001
        # 예외를 삼키면 '왜 없는지' 를 다시 못 묻는다(#12 silent-fail 금지).
        print(f"③ ❌ 생성 중 예외: {type(exc).__name__}: {exc}")
        return 1
    took = time.time() - t0
    if result is None:
        print(f"③ ❌ 생성 실패 — generate() 가 None ({took:.1f}초). "
              f"사유는 위 로그에, 재료 갈래는 `--why` 가 말한다")
        return 1
    body, cost, png = result
    print(f"③ ✅ 생성 성공 ({took:.1f}초) · 비용 ₩{cost:.1f} · "
          f"인포그래픽 {'있음' if png else '없음'} · 본문 {len(body)}자")
    head = " / ".join(body.splitlines()[:3])[:200]
    print(f"   | {head}")
    print("④ 푸시·아카이브 둘 다 안 했다 — 정기 실행(daily-byte.timer)이 "
          "같은 경로로 보내고 기록한다")
    return 0


# ── 진단: 왜 오늘 브리프가 안 나왔나 (사용자 2026-09-04) ──────────────────
# `generate()` 는 **다섯 자리에서 조용히 None** 을 돌려주고 화면은 그저
# "아카이브가 아직 없습니다" 라고만 적는다 — 사용자가 며칠 뒤 물어야 알았다
# (#43 침묵이 최악 · #52 조용한 것과 죽은 것을 화면이 구별 못 함). 갈래마다
# 처방이 완전히 다르므로 이름을 대서 말한다(#82). 반복 확인은 제품에
# 심는다(#252 — 손으로 조립한 명령은 제품 코드로 검증되지 않는다).
_WHY_VER = 7

# pykrx 로그인은 우리 코드가 아니라 **라이브러리가 stdout 으로** 사유를
# 찍는다. 그 원문을 잡아 갈래를 읽는다 — 갈래마다 처방이 다르다(#82).
# 순서 = 우선순위. 같은 실행에 '패스워드 변경 필요' 와 그 결과인 '자격
# 증명을 확인하세요' 가 **둘 다** 찍히므로(2026-09-04 VM 실측), 더
# **행동 가능한** 쪽을 머리에 둔다(#275).
_LOGIN_BRANCHES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("pw_change", ("비밀번호 변경", "패스워드 변경"),
     "KRX 계정이 비밀번호 변경 필요 상태다 — .env 의 키를 고쳐도 안 풀린다. "
     "https://www.krx.co.kr 에서 비밀번호를 바꾼 뒤 .env 의 KRX_PW 갱신"),
    ("env_unset", ("환경 변수가 설정되지 않았",),
     "로그인 시점에 KRX_ID/KRX_PW 가 환경에 없었다 — .env 로드 순서 확인"),
    ("bad_cred", ("자격 증명을 확인", "로그인 실패"),
     "KRX 가 로그인을 거부했다 — .env 의 KRX_ID/KRX_PW 값 확인"),
)
_LOGIN_FAIL_HINTS = ("실패", "오류", "⚠️")
# 사유를 **확정**한 갈래 — 여기 들어야 원인이라고 말하고 재조회를 멈춘다.
# 'unknown' 은 확정이 아니므로 힌트로만 쓴다(독립 리뷰: 넓은 힌트가 휴장·
# LLM 분기를 통째로 덮어썼다).
_LOGIN_CONFIRMED = ("pw_change", "env_unset", "bad_cred")

# 서로 **대체 가능한** 자격증명 — push_telegram 은 `A or B` 라 둘 중 하나면
# 된다. 한 방향으로만 적으면 B 만 있는 호스트에서 A 가 ❌ 로 뜬다(리뷰 실측).
_CRED_EITHER = (("TELEGRAM_BOT_TOKEN", "STANDARDVIEW_TELEGRAM_TOKEN"),)

# ⑤ 의 ✅ 기준과 ⑥ 의 판정이 **같은 상수**를 봐야 한 화면이 두 말을 안 한다
# (리뷰 실측: kr_age 2~3 이 ⑤ 는 ✅ 인데 ⑥ 은 '기록이 없다' 였다 — #38).
_FRESH_DAYS = 3


def krx_login_verdict(captured: str) -> tuple[str, str]:
    """로그인 원문에서 (갈래, 처방)을 읽는다. 실패 흔적이 없으면 ("ok", "").

    ⚠️ 아는 문구가 하나도 안 걸리면 단정하지 말고 **원문 표본**을 돌려준다
    — 원천이 문구를 바꾸면 '로그인 만료/차단 의심' 같은 **틀린 사유**가
    다음 라운드를 엉뚱한 데로 보낸다(#109·#165·#187b).
    """
    text = captured or ""
    for kind, needles, fix in _LOGIN_BRANCHES:
        if any(n in text for n in needles):
            return kind, fix
    # ⚠️ '실패·오류' 만 보면 **아무 한글 오류**나 로그인 실패로 읽는다 —
    # 그러면 진짜 원인(휴장·LLM 키)이 덮인다(독립 리뷰 실측). 로그인 문맥이
    # 같이 있을 때만 'unknown' 이고, 그마저 **확정이 아니라 힌트**다.
    if "로그인" in text and any(h in text for h in _LOGIN_FAIL_HINTS):
        tail = [ln.strip() for ln in text.splitlines() if ln.strip()][-3:]
        return "unknown", "로그인 실패 사유를 모르겠다 — 원문: " + " / ".join(tail)
    return "ok", ""


_TIMER_UNIT = "daily-byte.timer"
_SERVICE_UNIT = "daily-byte.service"
_TIMER_CMDS = (f"`systemctl status {_TIMER_UNIT}` · "
               f"`journalctl -u {_SERVICE_UNIT} -n 50`")


def systemd_facts(timer: str = _TIMER_UNIT, service: str = _SERVICE_UNIT) -> dict:
    """systemd 에 **물어서** 타이머 상태를 재 온다(읽기 전용).

    도장(`feed_health`)이 없다는 사실만으로 '타이머가 안 돌았을 수 있다' 고
    적으면 그건 안 잰 문장이다(#165) — 실제로 2026-09-04 실행에서 그 줄이
    떴는데, 도장을 찍는 코드가 **마지막 발화 뒤에 배포**된 것뿐이었다.
    상태는 **아는 쪽에 묻는다**(#86 systemctl · #64).

    ⚠️ `LoadState` 는 '유닛 파일이 파싱됐다' 까지만 말한다 — 중지·비활성
    타이머도 `loaded` 이고 `LastTriggerUSec` 는 `Persistent=true` 로 남는다.
    그래서 **`ActiveState` 를 같이 묻지 않으면** '재배포 뒤 타이머가 조용히
    멈춤'(이 도구의 존재 이유)이 '정상'으로 보고된다(독립 리뷰 실측).
    서비스도 `Type=oneshot` 이라 **실행 중**과 **끝남**을 `ActiveState`/
    `SubState` 없이는 못 가른다.

    `ok=False` 면 판정 불가 — 단정하지 않는다.
    """
    import subprocess

    out: dict = {"ok": False}
    try:
        for unit, keys in (
                (timer, ("LoadState", "ActiveState", "SubState",
                         "LastTriggerUSec", "NextElapseUSecRealtime")),
                (service, ("ActiveState", "SubState",
                           "ExecMainStartTimestamp", "ExecMainStatus", "Result"))):
            r = subprocess.run(
                ["systemctl", "show", unit, *[f"-p{k}" for k in keys]],
                capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                out["err"] = (r.stderr or "").strip()[:120] or f"rc={r.returncode}"
                return out
            pre = "t_" if unit == timer else "s_"
            for ln in (r.stdout or "").splitlines():
                if "=" in ln:
                    k, _, v = ln.partition("=")
                    out[pre + k.strip()] = v.strip()
    except Exception as exc:                                   # noqa: BLE001
        out["err"] = f"{type(exc).__name__}: {exc}"[:120]
        return out
    out["ok"] = True
    return out


# LoadState 갈래마다 처방이 다르다 — 하나로 뭉뚱그리면 `masked` 에
# `enable --now` 를 시켜 실패한다(독립 리뷰 실측, #82).
_LOAD_FIX = {
    "not-found": f"유닛 파일이 없다(설치 안 됨): `systemctl enable --now {_TIMER_UNIT}`",
    "masked": f"마스크 해제부터: `systemctl unmask {_TIMER_UNIT}` 뒤 "
              f"`systemctl enable --now {_TIMER_UNIT}`",
    "error": f"유닛 파일을 읽지 못했다: `systemctl status {_TIMER_UNIT}` · "
             f"`systemctl daemon-reload`",
    "bad-setting": f"유닛 파일 설정 오류: `systemctl status {_TIMER_UNIT}` · "
                   f"`systemctl daemon-reload`",
}


def _timer_line(facts: dict) -> str:
    """systemd 가 말한 사실 한 줄(↪ 없이). 갈래마다 처방이 다르다(#82)."""
    if not facts.get("ok"):
        why = facts.get("err") or "사유 미상"
        return f"systemd 에 못 물었다({why}) — 직접 확인: {_TIMER_CMDS}"
    load = facts.get("t_LoadState") or "?"
    if load != "loaded":
        # ⚠️ systemd 판에 따라 없는 유닛에 `show` 가 rc != 0 을 줄 수 있고,
        # 그러면 이 갈래 대신 위의 '못 물었다'(사유 원문 포함)가 나간다 —
        # 어느 쪽이든 운영자가 할 일은 출력에 적혀 있다.
        fix = _LOAD_FIX.get(load, f"`systemctl status {_TIMER_UNIT}` 로 확인")
        return f"타이머 유닛 상태가 `{load}` 다 — {fix}"
    trig = facts.get("t_LastTriggerUSec") or ""
    active = facts.get("t_ActiveState") or "?"
    if active != "active":
        # ⚠️ 여기가 이 도구의 존재 이유다 — 중지된 타이머도 LoadState=loaded
        # 이고 LastTriggerUSec 가 남아 '정상'처럼 보인다(독립 리뷰 실측).
        return (f"타이머가 **멈춰 있다**(ActiveState={active}) — 다음 발화가 "
                f"없다. `systemctl enable --now {_TIMER_UNIT}`")
    if not trig or trig in ("n/a", "0"):
        return (f"타이머는 켜져 있는데 **한 번도 발화하지 않았다** — "
                f"`systemctl status {_TIMER_UNIT}`")
    nxt = facts.get("t_NextElapseUSecRealtime") or "?"
    s_active = facts.get("s_ActiveState") or ""
    s_sub = facts.get("s_SubState") or ""
    if s_active in ("activating", "reloading") or s_sub in ("start", "running"):
        # Type=oneshot 은 실행 중에도 Result=success 라 상태를 안 보면
        # '정상 종료'로 오보한다(독립 리뷰 실측).
        return (f"지금 **실행 중**이다(ActiveState={s_active or '?'} "
                f"SubState={s_sub or '?'}, 마지막 발화 {trig}) — 끝난 뒤 다시 볼 것")
    status = facts.get("s_ExecMainStatus") or ""
    result = facts.get("s_Result") or ""
    started = facts.get("s_ExecMainStartTimestamp") or "?"
    if (result and result != "success") or (status and status != "0"):
        # ⚠️ 타이머의 마지막 발화와 서비스의 마지막 **실행**은 다를 수 있다
        # (손으로 `systemctl start` 하면 갈린다) — 저널을 맞춰 보려면
        # 실행 시각이 필요하다(#114 감사는 무엇을 보고 말하는지 밝힐 것).
        return (f"타이머는 발화했지만(마지막 {trig}) 서비스가 실패했다"
                f"(마지막 실행 {started} · Result={result or '?'} "
                f"exit={status or '?'}) — 아래 ⑦ 참고")
    return (f"타이머는 켜져 있고 발화했으며(마지막 {trig}, 다음 {nxt}) 서비스도 "
            f"정상 종료했다(마지막 실행 {started}) — 실행은 됐다는 뜻이므로 "
            f"위 사유가 원인이다")


def timer_verdict(facts: dict, stamp: str, stamp_stale: bool = False) -> str:
    """↪ 한 줄을 **값으로** 만든다 — 갈래마다 처방이 다르다(#82).

    도장이 없다는 것과 타이머가 안 돌았다는 것은 다른 사실이다(도장 코드가
    마지막 발화보다 나중에 배포됐어도 도장은 없다). 그리고 **도장이 있다고
    현재형으로 말해서도 안 된다** — 도장은 실행 **시작**에 찍히므로 옛
    도장은 '지난주에 돌았다'는 뜻이다. `stamp_stale` 이면 systemd 가 말한
    사실을 대신 싣는다(#281 독립 리뷰 실측).
    """
    if stamp and not stamp_stale:
        return (f"↪ 타이머는 돌았다(마지막 점검 {stamp}) — 실행은 됐는데 "
                f"생성이 실패한 것이므로 위 사유가 원인이다")
    head = (f"↪ 점검 도장이 낡았다(마지막 {stamp}). " if stamp
            else "↪ 점검 도장이 없다. ")
    return head + _timer_line(facts)



# 저널에 토큰이 섞여 들어올 수 있다 — 값은 **절대** 찍지 않는다(§Secrets).
# httpx 로거는 이미 WARNING 으로 눌러 뒀지만(2026-05-29) 그건 이 프로세스
# 얘기이고, 저널은 옛 줄·다른 유닛의 줄을 담는다. 마스킹은 읽는 쪽에서.
_SECRET_RE = re.compile(
    r"(\d{8,10}:[A-Za-z0-9_-]{30,})"                       # 텔레그램 봇 토큰
    r"|((?i:api[_-]?key|token|passwd|password|secret)=)[^\s&\"']+")
_ERR_HINTS = ("ERROR", "Traceback", "Error", "error", "실패", "없습니다", "변경")


def redact(line: str) -> str:
    """저널 한 줄에서 비밀값을 지운다. 값은 안 찍고 **자리만** 남긴다."""
    return _SECRET_RE.sub(
        lambda m: (m.group(2) + "***REDACTED***") if m.group(2) else "***REDACTED***",
        line)


def service_failed(facts: dict) -> bool:
    """마지막 **실행**이 실패로 끝났나. 판정 불가·실행 중이면 False.

    ⚠️ `Type=oneshot` 은 실행 중에 `Result=success` 라 그때 '실패 아님' 이
    맞고, 물어보지 못했으면(`ok=False`) 단정하지 않는다(#12·#54).
    """
    if not facts.get("ok"):
        return False
    if facts.get("s_ActiveState") in ("activating", "reloading"):
        return False
    result = facts.get("s_Result") or ""
    status = facts.get("s_ExecMainStatus") or ""
    return bool((result and result != "success") or (status and status != "0"))


def journal_tail(unit: str = _SERVICE_UNIT, n: int = 60) -> tuple:
    """유닛 저널 마지막 n줄(읽기 전용). 실패면 ([], 사유).

    ⚠️ 진단이 "`journalctl` 로 확인하세요" 로 끝나면 라운드가 하나 더
    든다 — 반복 확인은 제품에 심는다(#252·Automation-first). 읽기만 하고
    운영 상태를 바꾸지 않는다(#264). 권한이 없으면(저널 그룹 밖) 그
    사실을 갈래로 말한다(#82).
    """
    import subprocess

    try:
        r = subprocess.run(
            ["journalctl", "--no-pager", "-n", str(n), "-u", unit,
             "-o", "short-iso"],
            capture_output=True, text=True, timeout=15)
    except Exception as exc:                                   # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:120]
    if r.returncode != 0:
        return [], (r.stderr or "").strip()[:160] or f"rc={r.returncode}"
    # ⚠️ journalctl 은 빈 결과에 `-- No entries --` 배너를 준다 — 그걸
    # 내용으로 세면 '읽었다'가 거짓이 된다(#54 대조 0건은 통과가 아니다).
    lines = [ln for ln in (r.stdout or "").splitlines()
             if ln.strip() and not ln.strip().startswith("-- ")]
    if not lines:
        # 대조 0건은 통과가 아니다(#54) — 없는 것도 갈래다.
        return [], "저널에 이 유닛의 줄이 없다(로테이션·권한 확인)"
    return lines, ""


def error_lines(lines: list, keep: int = 12) -> list:
    """저널 줄에서 **사유로 읽히는 것**만. 없으면 마지막 몇 줄을 그대로.

    아무 줄이나 걸러 놓고 '이게 원인' 이라 하면 헛걸음이다(#187b) —
    걸러진 게 없으면 걸렀다고 말하지 말고 꼬리를 준다.
    """
    hits = [ln for ln in lines if any(h in ln for h in _ERR_HINTS)]
    return [redact(ln) for ln in (hits or lines)[-keep:]]


def why_verdict(*, td, llm_ok: bool, login_kind: str, login_fix: str,
                krx_ok, kr_age, missed_sessions=None, stamp: str = "",
                timer: str = "") -> list[str]:
    """⑥ 판정을 **값으로** 만든다 — 휴장이 확정된 실패를 덮지 못하게.

    ⚠️ 옛 판은 휴장을 가장 먼저 보고 '이상 없음' 으로 끝냈다. 그래서
    로그인이 확정 실패(비밀번호 변경 필요)이고 한국 아카이브가 9일 낡은
    실행이 `⏸ 오늘은 휴장 — 이상 없음` 으로 나왔다(2026-09-04 VM 실측
    · #41 여유로 사실을 덮지 말 것 · #260 ❌ 는 고칠 수 있는 것을
    가리켜야 한다). 휴장이 정당하게 설명하는 것은 **빈 수급 응답**뿐이고,
    로그인 실패와 낡은 아카이브는 휴장과 무관하다.

    `missed_sessions` = 마지막 기록 이후 **놓친 거래일 수**(캘린더를 못
    쓰면 None). 달력 일수로 재면 설·추석 연휴(4~6일 비거래일)마다 오탐이
    난다 — 늘 뜨는 경고는 아무것도 안 재는 것과 같다(#25·#260).
    `stamp` = `feed_health.last('daily_byte_kr')` — 타이머가 **돌긴 했는지**
    를 재서 말한다. 안 재고 '타이머가 안 돌았을 수 있다' 고 적으면 그게
    헛걸음을 만든다(#165·#187b). `timer` = `timer_verdict(systemd_facts(), stamp)`
    — 도장이 없을 때 systemd 가 말한 사실(발화 시각·종료 상태)을 싣는다.
    안 주면 systemd 에 못 물은 것으로 보고 판정 보류 문구가 나간다.
    도장이 **낡았으면** 그것도 systemd 에 묻는다(옛 도장 ≠ 지금 돈다).
    """
    out: list[str] = []
    confirmed = login_kind in _LOGIN_CONFIRMED
    if krx_ok:
        # ⚠️ 재료를 받았으면 로그인은 정의상 됐다 — 버퍼에 남은 옛 실패
        # 문구로 '로그인 실패가 원인' 이라고 말하면 ④ 의 ✅ 와 모순된다.
        if kr_age is not None and kr_age <= _FRESH_DAYS:
            out.append("✅ 재료·기록 둘 다 정상 — 화면이 안 보이면 렌더/창 문제")
    elif confirmed:
        out.append(f"❌ KRX 로그인 실패 [{login_kind}] — 이게 원인이다. {login_fix}")
    elif not llm_ok:
        out.append("❌ LLM 키 없음(AI Studio·Vertex 둘 다) — 브리프 생성 불가")
    elif krx_ok is False and td is not True:
        # 휴장이거나 캘린더 판정 불가 — 빈 수급이 정상일 수 있다. 단정 금지.
        why = "휴장이라" if td is False else "거래일인지 판정 못 해"
        out.append(f"⏸ {why} 수급이 비는 것은 정상일 수 있다 — 오늘 자체는 판정 보류")
    elif krx_ok is False:
        out.append("❌ 거래일인데 시장 수급(totals)이 비었다 — KRX Data "
                   "Marketplace 자격증명 확인 후 "
                   "`systemctl start daily-byte.service`")
    if login_kind == "unknown" and not krx_ok:
        out.append(f"⚠️ 로그인 원문에 실패 흔적은 있는데 갈래를 못 정했다. {login_fix}")

    # ── 아카이브 공백은 휴장과 무관한 별개의 사실이다 ──
    if kr_age is None:
        out.append("❌ 한국 기록이 아예 없다")
    elif missed_sessions is not None:
        if missed_sessions >= 2:
            out.append(f"⚠️ 마지막 기록({kr_age}일 전) 이후 거래일이 "
                       f"{missed_sessions}일 지났는데 브리프가 없다 — "
                       f"연휴로는 설명되지 않는다")
    elif kr_age > _FRESH_DAYS:
        out.append(f"⚠️ 한국 마지막 기록이 {kr_age}일 전 — 거래일 판정을 "
                   f"못 해 연휴인지까지는 못 가른다")

    # ── '타이머가 안 돌았다' 는 재서 말한다(#165) ──
    if any(l.startswith(("⚠️", "❌")) for l in out):
        out.append(timer or timer_verdict({}, stamp))
    if not out:
        out.append("❓ 재료도 기록도 이상 없어 보인다 — 화면이 비면 렌더/창 확인")
    return out


def _missed_sessions(last_day: str, today: str) -> "int | None":
    """`last_day` **다음날**부터 `today` 까지의 거래일 수. 캘린더를 못 쓰면
    None(판정 불가 — 달력 일수로 대신 우기지 않는다, #12)."""
    try:
        from bot.market_calendar import is_trading_day
    except Exception:                                          # noqa: BLE001
        return None
    try:
        cur = datetime.strptime(last_day, "%Y-%m-%d").date() + timedelta(days=1)
        end = datetime.strptime(today, "%Y-%m-%d").date()
    except Exception:                                          # noqa: BLE001
        return None
    n = 0
    while cur <= end:
        try:
            if is_trading_day("KR", cur.strftime("%Y-%m-%d")):
                n += 1
        except Exception:                                      # noqa: BLE001
            return None
        cur += timedelta(days=1)
    return n


def _archive_scan(kind: str, days: int = 90) -> tuple[str, int]:
    """(마지막 기록 날짜, 스캔한 날짜 디렉터리 수). 없으면 ("", n).

    ⚠️ 날짜 판정은 **화면이 쓰는 그 함수**를 부른다(#35·#38) — 여기에 같은
    스캔을 복제하면 언젠가 프로브와 카드가 다른 '마지막 기록'을 말한다."""
    import os as _os
    from bot.dashboard import _last_market_daily_date
    root = _DAILY_BYTE_ARCHIVE_DIR
    n = 0
    if _os.path.isdir(root):
        n = len([d for d in _os.listdir(root)
                 if _os.path.isdir(_os.path.join(root, d))])
    return _last_market_daily_date("daily_byte_archive", kind, days), min(n, days)


def why(argv: list[str] | None = None) -> int:
    """`--why` — 오늘 브리프가 왜 없는지 **갈래로** 말한다. Pro 호출 0건.

    ⚠️ 배너에 인터프리터·필수 모듈을 먼저 찍는다 — venv 밖에서 돌면 pykrx 가
    없어 '원천 실패' 처럼 보인다(#132 를 이 도구에서 반복하지 않기 위해).
    자격증명은 **출처와 길이까지만**(값 금지, §Secrets · #23).
    """
    import io
    import sys
    from contextlib import redirect_stderr, redirect_stdout

    from bot.env_keys import env_source, env_why

    # ⚠️ 아래에서 stdout/stderr 를 잠깐 가로채는데, 그 사이에 로깅 핸들러가
    # 처음 만들어지면 **StringIO 에 영구히 묶인다**(StreamHandler 는 생성
    # 시점의 스트림을 잡는다) — 그다음부터 모든 로그가 조용히 사라진다.
    # 진짜 stderr 로 먼저 붙여 두면 이후 basicConfig 는 no-op 이다.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING,
                        format="%(levelname)s:%(name)s:%(message)s")
    print(f"[Daily Byte KR 진단 v{_WHY_VER}]")
    print(f"① 인터프리터: {sys.executable}")
    missing, noise = [], io.StringIO()
    for mod in ("pykrx", "dotenv"):
        try:
            # ⚠️ pykrx 는 **import 시점에** 로그인을 시도해 stdout 으로
            # 'KRX_ID 또는 KRX_PW 환경 변수가 설정되지 않았습니다' 를 찍는다
            # — load_dotenv 前이라 키가 멀쩡해도 늘 그렇게 나오고, 바로
            # 아래 ② 의 ✅ 와 정면으로 모순돼 사용자가 그 첫 줄을 원인으로
            # 읽는다(2026-09-04 VM 실측 · #187b).
            with redirect_stdout(noise), redirect_stderr(noise):
                __import__(mod)
        except Exception:                                      # noqa: BLE001
            missing.append(mod)
    if missing:
        # ⚠️ `python -m` 은 **cwd 에서** 패키지를 찾는다 — `cd` 를 빼면 홈에서
        # 돌렸을 때 `No module named 'bot'` 이다(2026-09-04 사용자 실측).
        print(f"   ❌ 필수 모듈 없음: {', '.join(missing)} — 레포에서 venv 로:")
        print("      cd ~/stock && .venv/bin/python -m bot.daily_kr_flow --why")
        return 1
    print("   ✅ pykrx·dotenv 로드됨")

    # ⚠️ 출처 판정을 **먼저** 한다 — `load_dotenv` 가 .env 를 os.environ 으로
    # 옮기면 `env_source` 가 전부 '환경변수' 라고 답해 갈래가 사라진다
    # (독립 리뷰 실측 — #23 이 출처를 찍게 한 이유가 통째로 무력화된다).
    print("② 자격증명 (값은 안 찍습니다)")
    creds = {}
    for k in ("GOOGLE_API_KEY", "KRX_ID", "KRX_PW",
              "TELEGRAM_BOT_TOKEN", "STANDARDVIEW_TELEGRAM_TOKEN",
              "CHANNEL_CHAT_IDS"):
        src = env_source(k)
        creds[k] = src != "없음"
        if src != "없음":
            mark, extra = "✅", ""
        elif (alt := next((o for g in _CRED_EITHER if k in g for o in g
                           if o != k and creds.get(o)), "")):
            # ⚠️ 대체 가능한 키가 있으면 없는 게 정상인데 ❌ 로 찍어 사용자
            # 눈을 끌었다(2026-09-04 VM 실측 · #260 ❌ 는 고칠 것만).
            mark, extra = "ℹ️", f" — {alt} 로 대체되므로 없어도 정상"
        else:
            mark, extra = "❌", f" — {env_why(k)}"
        print(f"   {mark} {k}: {src}{extra}")

    try:
        from dotenv import load_dotenv
        from pathlib import Path as _P
        load_dotenv(_P.home() / "stock" / ".env")
    except Exception:                                          # noqa: BLE001
        pass
    # ⚠️ 생성 가능 여부는 raw 키가 아니라 **제품이 쓰는 판정**으로 — Vertex
    # 모드에선 GOOGLE_API_KEY 가 없어도 정상이다(#35 화면이 쓰는 그 경로).
    llm_ok = bool(_effective_key())
    print(f"   {'✅' if llm_ok else '❌'} LLM 키(genai_factory.effective_key): "
          f"{'사용 가능' if llm_ok else '없음 — AI Studio 키도 Vertex 설정도 없음'}")

    print("③ 거래일 게이트 (아니면 그날은 정상 skip)")
    today = _now_kst().strftime("%Y-%m-%d")
    try:
        from bot.market_calendar import is_trading_day
        td = is_trading_day("KR", today)
    except Exception as exc:                                   # noqa: BLE001
        td = None
        print(f"   ❓ 캘린더 판정 불가({exc}) — 폴백으로 진행됨")
    if td is False:
        print(f"   ⏸ {today} KR 휴장 — 오늘은 생성 안 하는 게 정상")
    elif td is True:
        print(f"   ✅ {today} KR 거래일 — 생성됐어야 함")

    print("④ KRX 로그인 (env 유무가 아니라 **실호출**로 판정, #25)")
    krx_ok, login_kind, login_fix = None, "", ""
    try:
        from bot.pykrx_client import krx_login_ready, _quiet_pykrx_logging
        _quiet_pykrx_logging()
        if not krx_login_ready():
            print("   ❌ KRX_ID/KRX_PW 미설정 — pykrx 수급 fetch 불가라 "
                  "generate() 가 여기서 return None (조용한 skip)")
            krx_ok = False
            login_kind, login_fix = "env_unset", ".env 에 KRX_ID/KRX_PW 추가"
        else:
            # generate() 는 빈 응답이면 **4영업일까지 거슬러** 다시 묻는다 —
            # 프로브가 오늘 하루만 보면 장중·휴장에 '조회 실패' 로 오보한다
            # (독립 리뷰 실측 — #56 프로브는 제품의 루프 구조까지 베낄 것).
            # ⚠️ 단 **로그인이 확정 실패면 거기서 멈춘다** — 옛 판은 그대로
            # 4일 × 2시장을 돌아 실패한 로그인을 10회 반복했고, 이미 답이
            # 나온 뒤의 9회는 계정만 두드린다(2026-09-04 VM 실측).
            buf = io.StringIO()
            d, tries, tot = _resolve_trading_date(), 0, None
            while True:
                with redirect_stdout(buf), redirect_stderr(buf):
                    tot = _fetch_market_totals(d)
                login_kind, login_fix = krx_login_verdict(buf.getvalue())
                if tot or login_kind in _LOGIN_CONFIRMED or tries >= 4:
                    break
                d, tries = _prev_bday(d, 1), tries + 1
            krx_ok = bool(tot)
            # ⚠️ 여기서 재는 것은 **시장 수급(totals)** 하나다 — 제품은
            # totals 든 종목별이든 하나만 있어도 진행하므로(generate 의
            # walk-back 조건), '수급 전체가 죽었다' 고 말하면 과장이다(#165).
            print(f"   {'✅' if krx_ok else '❌'} 시장 수급(totals) 실조회"
                  f"({d}, {tries}회 거슬러봄): "
                  f"{'수신' if krx_ok else '빈 응답'}")
            if login_kind in _LOGIN_CONFIRMED:
                print(f"   ❌ 로그인 갈래 [{login_kind}] {login_fix}")
            elif not krx_ok:
                # 로그인은 멀쩡한데 비었다 — 가로챈 원문을 보여준다(#109).
                tail = [ln.strip() for ln in buf.getvalue().splitlines()
                        if ln.strip()][-2:]
                if tail:
                    print("   ↪ 원문: " + " / ".join(tail))
    except Exception as exc:                                   # noqa: BLE001
        krx_ok = False
        print(f"   ❌ pykrx 조회 예외: {type(exc).__name__}: {exc}")

    print("⑤ 아카이브 (미국은 **대조군** — 둘 다 비면 공통 원인, #143)")
    kr_d, scanned = _archive_scan("daily")
    us_d, _ = _archive_scan("us_daily")

    def _age(d: str | None):
        if not d:
            return None
        return (_now_kst().date()
                - datetime.strptime(d, "%Y-%m-%d").date()).days

    kr_age, us_age = _age(kr_d), _age(us_d)
    for lab, d, age in (("한국", kr_d, kr_age), ("미국", us_d, us_age)):
        if d:
            print(f"   {'✅' if age <= 3 else '⚠️'} {lab} 마지막 기록 {d}"
                  f" ({age}일 전)")
        else:
            print(f"   ❌ {lab} 기록 없음 (최근 {scanned}개 날짜 확인)")
    if kr_d and us_d and kr_d < us_d:
        print(f"   ↪ 미국은 {us_d} 까지 정상 — 공통 인프라가 아니라 "
              f"**KR 경로**(KRX 로그인·pykrx) 문제")

    missed = _missed_sessions(kr_d, today) if kr_d else None
    try:
        from bot import feed_health
        stamp = feed_health.last("daily_byte_kr")
        # 도장 나이를 **재서** 쓴다 — 옛 도장을 현재형으로 말하면 systemd
        # 조회를 통째로 건너뛰고 엉뚱한 곳을 짚게 한다(#281).
        fresh_stamp = bool(stamp) and feed_health.overdue("daily_byte_kr") is False
    except Exception:                                          # noqa: BLE001
        stamp, fresh_stamp = "", False

    facts = {} if fresh_stamp else systemd_facts()
    print("⑥ 판정")
    for line in why_verdict(td=td, llm_ok=llm_ok, login_kind=login_kind,
                            login_fix=login_fix, krx_ok=krx_ok, kr_age=kr_age,
                            missed_sessions=missed, stamp=stamp,
                            timer=timer_verdict(
                                facts, stamp,
                                stamp_stale=bool(stamp and not fresh_stamp))):
        print(f"   {line}")

    # ⑦ 서비스가 실패했으면 **저널까지 보여준다** — "journalctl 로 확인
    # 하세요" 로 끝나면 라운드가 하나 더 든다(#252·Automation-first).
    if service_failed(facts):
        # ⚠️ 헤더가 '사유' 라고 단정하면 안 된다 — 우리가 고른 건 '사유로
        # 읽히는 줄' 이고, `-n` 은 실행 경계를 모르므로 옛 실행 줄이 섞일 수
        # 있다. 무엇을 보여주는지 그대로 적는다(#165·#187b).
        print(f"⑦ 서비스 마지막 실행이 실패했다 — `journalctl -u "
              f"{_SERVICE_UNIT}` 발췌(여러 실행이 섞일 수 있음 · 비밀값 가림)")
        lines, err = journal_tail()
        if err:
            print(f"   ❓ 저널을 못 읽었다({err}) — "
                  f"`journalctl -u {_SERVICE_UNIT} -n 50` 를 직접 볼 것")
        else:
            for ln in error_lines(lines):
                print(f"   | {ln}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
