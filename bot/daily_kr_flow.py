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


def generate() -> tuple[str, float, str | None] | None:
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
        out = os.path.join(_DBYTE_IMG_DIR, fname)
        png_path = render_infographic(data, _iso_dot(date), out)
        if png_path:
            png_rel = f"daily_byte_img/{fname}"
    except Exception as exc:
        log.warning("daily_byte: infographic render failed: %s", exc)

    # 아카이브 (대시보드 카드용 — Python 제목/부제 제외 본문 + 인포그래픽
    # 상대경로) + daily_byte.html regenerate. push 와 무관하게 항상 기록.
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




# ── 진단: 왜 오늘 브리프가 안 나왔나 (사용자 2026-09-04) ──────────────────
# `generate()` 는 **다섯 자리에서 조용히 None** 을 돌려주고 화면은 그저
# "아카이브가 아직 없습니다" 라고만 적는다 — 사용자가 며칠 뒤 물어야 알았다
# (#43 침묵이 최악 · #52 조용한 것과 죽은 것을 화면이 구별 못 함). 갈래마다
# 처방이 완전히 다르므로 이름을 대서 말한다(#82). 반복 확인은 제품에
# 심는다(#252 — 손으로 조립한 명령은 제품 코드로 검증되지 않는다).
_WHY_VER = 1


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
    import sys
    from bot.env_keys import env_source, env_why

    print(f"[Daily Byte KR 진단 v{_WHY_VER}]")
    print(f"① 인터프리터: {sys.executable}")
    missing = []
    for mod in ("pykrx", "dotenv"):
        try:
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
        extra = f" — {env_why(k)}" if src == "없음" else ""
        print(f"   {'✅' if src != '없음' else '❌'} {k}: {src}{extra}")

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
    krx_ok = None
    try:
        from bot.pykrx_client import krx_login_ready, _quiet_pykrx_logging
        _quiet_pykrx_logging()
        if not krx_login_ready():
            print("   ❌ KRX_ID/KRX_PW 미설정 — pykrx 수급 fetch 불가라 "
                  "generate() 가 여기서 return None (조용한 skip)")
            krx_ok = False
        else:
            # generate() 는 빈 응답이면 **4영업일까지 거슬러** 다시 묻는다 —
            # 프로브가 오늘 하루만 보면 장중·휴장에 '조회 실패' 로 오보한다
            # (독립 리뷰 실측 — #56 프로브는 제품의 루프 구조까지 베낄 것).
            d = _resolve_trading_date()
            tot, tries = _fetch_market_totals(d), 0
            while not tot and tries < 4:
                d = _prev_bday(d, 1)
                tot, tries = _fetch_market_totals(d), tries + 1
            krx_ok = bool(tot)
            print(f"   {'✅' if krx_ok else '❌'} 시장 수급 실조회"
                  f"({d}, {tries}회 거슬러봄): "
                  f"{'수신' if krx_ok else '전부 빈 응답 — 로그인 만료/차단 의심'}")
    except Exception as exc:                                   # noqa: BLE001
        krx_ok = False
        print(f"   ❌ pykrx 조회 예외: {type(exc).__name__}: {exc}")

    print("⑤ 아카이브 (미국은 **대조군** — 둘 다 비면 공통 원인, #143)")
    kr_d, scanned = _archive_scan("daily")
    us_d, _ = _archive_scan("us_daily")
    for lab, d in (("한국", kr_d), ("미국", us_d)):
        if d:
            age = (_now_kst().date()
                   - datetime.strptime(d, "%Y-%m-%d").date()).days
            print(f"   {'✅' if age <= 3 else '⚠️'} {lab} 마지막 기록 {d}"
                  f" ({age}일 전)")
        else:
            print(f"   ❌ {lab} 기록 없음 (최근 {scanned}개 날짜 확인)")
    if kr_d and us_d and kr_d < us_d:
        print(f"   ↪ 미국은 {us_d} 까지 정상 — 공통 인프라가 아니라 "
              f"**KR 경로**(KRX 로그인·pykrx) 문제")

    print("⑥ 판정")
    # ⚠️ 휴장을 **가장 먼저** 본다 — 휴장일엔 수급 조회가 정상적으로 비어
    # krx_ok=False 가 되는데, 그걸 먼저 보면 멀쩡한 자격증명을 범인으로
    # 지목한다(독립 리뷰 실측 · #187b 틀린 사유가 헛걸음을 만든다).
    if td is False:
        print("   ⏸ 오늘은 휴장 — 이상 없음. 마지막 거래일 기록을 ⑤ 로 확인")
    elif not llm_ok:
        print("   ❌ LLM 키 없음(AI Studio·Vertex 둘 다) — 브리프 생성 불가")
    elif krx_ok is False:
        print("   ❌ KRX 수급 조회 실패 — 이게 원인이다. KRX Data Marketplace "
              "자격증명 확인 후 `systemctl start daily-byte.service`")
    elif kr_d and (_now_kst().date()
                   - datetime.strptime(kr_d, "%Y-%m-%d").date()).days <= 1:
        print("   ✅ 최근 기록 존재 — 화면이 안 보이면 렌더/창 문제")
    else:
        print("   ❓ 재료는 정상인데 기록이 없다 — 타이머가 안 돌았을 수 있다: "
              "`systemctl status daily-byte.timer` · "
              "`journalctl -u daily-byte.service -n 50`")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
