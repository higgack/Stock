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
            data["cum5d"][inv] = {"top_buy": _top(cum_flows, _TOP_N_BUY, reverse=True)}
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
    return data


def _fmt_top(rows: list) -> str:
    return "\n".join(
        f"    {nm} ({t}) {('+' if net >= 0 else '')}{net:,.0f}억"
        for t, nm, net in rows
    ) or "    (데이터 없음)"


def build_data_summary(data: dict) -> str:
    """Pro 에 주입할 구조화 데이터 텍스트. 모든 수치 = pykrx 정확값."""
    lines = [f"[거래일] {data['date']}  ([5일 윈도] {data['date_5d_from']}~{data['date']})"]
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
            lines.append(_fmt_top(td["top_buy"]))
            lines.append(f"[{inv} 당일 순매도 상위]")
            lines.append(_fmt_top(td["top_sell"]))
        cd = data["cum5d"].get(inv)
        if cd:
            lines.append(f"[{inv} 5일 누적 순매수 상위]")
            lines.append(_fmt_top(cd["top_buy"]))
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
   위에 없는 종목/수치를 지어내지 말 것.
2. **섹터 그룹핑 + 로테이션**: 위 종목들을 산업/테마로 묶어 (반도체/AI·SW/
   2차전지/전기차/원전·전력/바이오 등) 자금 흐름 방향을 서술. 종목→섹터
   분류는 당신의 지식 + web search 로.
3. **catalyst 맥락 (web search 활용)**: 상위 종목의 최근 공시/실적/뉴스
   (예: 수주, 실적 발표, M&A) 를 web search 로 확인해 "왜 이 자금이
   들어왔나" 맥락 제공. **출처 날짜는 반드시 {date} 이하** — 미래 날짜
   citation 금지. 확인 안 되면 '맥락 미확인' 명시, 추측 catalyst 창작 금지.
4. **중립 표현**: "주목 종목" 은 수급 관찰일 뿐 BUY/SELL 권고가 아님.
   '매수 추천' / '목표가' 같은 직접 투자권유 표현 금지.
5. **구조** (각 섹션 <b> 헤더):
   📊 시장 수급 총평 (외인 vs 기관 디커플링, breadth)
   🔥 지금 강한 섹터·종목 (4대 주체 동시매수 우선)
   🔄 섹터 로테이션 (유출 섹터 → 유입 섹터)
   💰 당일 기관(투신+사모) 합산 상위
   📈 주목할 수급 패턴 (꾸준한 매집 / 첫 대량출현 / 5일누적 강도)
   🏆 주목 종목 (수급 근거 + catalyst, 중립)
   ⚠️ 경고 시그널 (양→음 전환 종목)
   🎯 한 줄 결론
6. **HTML 형식만** (`<b>`, `<i>`) — markdown `**`/`##` 금지. 모바일 가독성
   위해 각 항목 줄바꿈.
7. 분량: 핵심 위주 간결하게. 데이터에 있는 종목만 다룰 것.

면책: 출력 끝에 "본 브리프는 수급 데이터 관찰 (교육·정보 목적), 투자 권유
아님" 1줄.
"""


def build_prompt(data: dict) -> str:
    return _PROMPT.format(date=data["date"], data_summary=build_data_summary(data))


def _post_process(text: str, date_iso: str) -> str:
    """오늘 audit 가드 재사용: 미래 날짜 citation strip + invalid 날짜
    validator + markdown noise 제거."""
    try:
        from bot.screener import _strip_future_dated_citations, _strip_invalid_dates
        text, _ = _strip_future_dated_citations(text, date_iso)
        text, _ = _strip_invalid_dates(text)
    except Exception as exc:
        log.debug("daily_byte: post-process guard failed: %s", exc)
    import re
    # markdown bold/header leak → strip (HTML 만 사용)
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
    text = text.replace("**", "")
    return text.strip()


def _iso_dot(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:]}"


def generate() -> tuple[str, float] | None:
    """수급 fetch → Pro narrate → guard → (제목 포함 본문, cost_krw).
    데이터 전무 시 None (graceful skip + 호출측 알림)."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("daily_byte: GOOGLE_API_KEY missing")
        return None

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
    try:
        from bot.screener import _log_usage
        _log_usage(pt, ot, cost_krw, "daily_byte")
    except Exception:
        pass

    title = f"📊 <b>Daily Byte - {_iso_dot(date)}</b>"
    full = f"{title}\n<i>장 마감 후 KR 수급 브리프 · 생성 {_now_kst():%H:%M} KST</i>\n\n{body}"
    return full, cost_krw


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


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception:
        pass
    result = generate()
    if result is None:
        log.error("daily_byte: generation failed / no data — skipping push")
        return 1
    body, cost = result
    log.info("daily_byte: generated (₩%.1f) — pushing", cost)
    ok = push_telegram(body)
    log.info("daily_byte: push %s", "OK" if ok else "with failures")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
