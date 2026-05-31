"""부동산 Byte — 한국 아파트 실거래가 주간 브리프 (텔레그램, 금 09:00 KST).

ticker·5거래일과 완전 독립한 standalone 분석. 수치는 MOLIT 실거래가에서
Python 이 정확 산출(환각 0), Gemini Pro 는 지역별 가격대·거래량·전세 흐름
+ 금리 연계 + 건설/부동산/REIT/은행 섹터 함의 narrative (중립, 투자 권유
아님). Daily Byte 인프라 mirror.

DATA_GO_KR_API_KEY 없으면 realestate_key_ready() gate 가 graceful skip.
systemd: realestate-byte.timer (금 09:00 KST). 주간 cadence (부동산은 월/주
단위로 느림 — R-ONE 주간동향 발표 후).
수동: cd ~/stock && .venv/bin/python -m bot.realestate_brief
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bot.realestate_brief")

_KST = timezone(timedelta(hours=9))
_HOME = os.path.expanduser("~")
_ARCHIVE_DIR = os.path.join(_HOME, ".tradingagents", "realestate_archive")
_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "realestate_usage.jsonl")
_NOAH_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "usage.jsonl")
# 인포그래픽 PNG — 대시보드가 서빙하는 archive/ 아래 (카드 <img> 임베드)
_IMG_DIR = os.path.join(_HOME, ".tradingagents", "archive", "realestate_img")
_USD_TO_KRW = 1330.0
_PRO_IN, _PRO_OUT = 1.25, 10.00


def _now_kst() -> datetime:
    return datetime.now(_KST)


def _fmt_eok(manwon: float) -> str:
    """만원 → 억 표기."""
    return f"{manwon / 10000:.2f}억" if manwon >= 10000 else f"{manwon:,.0f}만원"


def build_summary(data: dict) -> str:
    ymd = data.get("ymd", "?")
    lines = [f"[기준월] {ymd[:4]}년 {ymd[4:6]}월 아파트 매매 실거래 (MOLIT 공식)"]
    regs = data.get("regions", {})
    if not regs:
        return "\n".join(lines + ["  (실거래 데이터 미수집)"])
    lines.append("\n[지역별 평균 매매가 / 거래량 / 평당가 / 전세가율]")
    for name, r in regs.items():
        ppp = f" · 평당 {r['avg_per_pyeong']:,}만원" if r.get("avg_per_pyeong") else ""
        jeonse = ""
        if r.get("jeonse_avg_manwon"):
            jr = f", 전세가율 {r['jeonse_ratio']}%" if r.get("jeonse_ratio") else ""
            jeonse = f" · 전세평균 {_fmt_eok(r['jeonse_avg_manwon'])}{jr}"
        extra = ""
        if r.get("offi_avg_manwon"):
            extra += f" · 오피스텔 {_fmt_eok(r['offi_avg_manwon'])}"
        if r.get("rh_avg_manwon"):
            extra += f" · 연립다세대 {_fmt_eok(r['rh_avg_manwon'])}"
        lines.append(
            f"  {name}: 아파트 매매평균 {_fmt_eok(r['avg_manwon'])} ({r['n_deals']}건"
            f"{ppp} · 최고 {_fmt_eok(r['max_manwon'])}){jeonse}{extra}"
        )
    return "\n".join(lines)


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else ''}{v:.2f}%"


def build_trend_block(trend: dict) -> str:
    """R-ONE 월간 지수 추세 → 텍스트 블록. 비어있으면 ''."""
    if not trend:
        return ""
    order = ("전국", "수도권", "지방", "서울")
    lines = ["[R-ONE 월간 아파트 가격지수 추세 (한국부동산원 공식 · 개별 실거래 "
             "노이즈와 별개의 매끄러운 시장 추세)]"]
    for key, ko in (("sale", "매매지수"), ("jeonse", "전세지수")):
        regs = trend.get(key) or {}
        if not regs:
            continue
        parts, period = [], ""
        for reg in order:
            t = regs.get(reg)
            if not t:
                continue
            period = t.get("latest_period") or period
            parts.append(f"{reg} {_fmt_pct(t.get('mom_pct'))} MoM/"
                         f"{_fmt_pct(t.get('m3_pct'))} 3M")
        if parts:
            pstr = f" ({period[:4]}.{period[4:6]})" if len(period) >= 6 else ""
            lines.append(f"  {ko}{pstr}: " + " · ".join(parts))
    return "\n".join(lines) if len(lines) > 1 else ""


def build_permit_block(agg: dict) -> str:
    """건축인허가 집계 → 공급 파이프라인 텍스트 블록. 비어있으면 ''."""
    if not agg or not agg.get("by_region"):
        return ""
    tot = agg.get("total", {})
    months = agg.get("months", 12)
    since = agg.get("since", "")
    since_fmt = f"{since[:4]}.{since[4:6]}" if len(since) >= 6 else ""
    lines = [f"[건축인허가 — 공급 파이프라인 선행지표] 최근 {months}개월"
             f"({since_fmt}~) · 표본 법정동 {len(agg['by_region'])}곳",
             f"  합계: 주거 인허가 {tot.get('n_permits',0)}건 · "
             f"{tot.get('hhld_sum',0):,}세대 · 연면적 {tot.get('tot_area',0):,}㎡"]
    for name, r in sorted(agg["by_region"].items(),
                          key=lambda kv: kv[1].get("hhld_sum", 0), reverse=True):
        lines.append(f"  {name}: {r['n_permits']}건 · {r['hhld_sum']:,}세대 · "
                     f"연면적 {r['tot_area']:,}㎡")
    return "\n".join(lines)


_PROMPT = """당신은 한국 부동산 시장 전문 애널리스트입니다. 아래는 {ymd} 국토
교통부(MOLIT) 아파트 매매 실거래가에서 직접 집계한 **정확한 수치**이며,
한국부동산원(R-ONE) 월간 가격지수 추세가 함께 제공됩니다 (있는 경우).
이를 바탕으로 '부동산 Byte' 주간 브리프를 작성하세요.

{summary}

---
작성 규칙:
1. **수치는 위 데이터 글자 그대로 인용** — 재계산·창작 금지.
2. 지역별 가격대 비교 (강남권 고가 vs 외곽/지방), 거래량으로 본 매수
   심리(거래 활발/위축), 평당가 격차, **전세가율**(데이터에 있으면) 서술.
   전세가율 높음(>70%) = 갭투자 유인·매매 전환 압력, 낮음 = 매매 관망.
3. **R-ONE 지수 추세 우선 인용** (블록 있으면): 개별 실거래 평균은 표본·
   단지 구성에 따라 출렁이므로, **방향성 시그널은 R-ONE 월간 지수의
   MoM/3M 변화율을 1차 근거로** 삼는다 (매매·전세 / 전국·수도권·지방).
   실거래 평균과 지수 추세가 어긋나면 그 괴리를 짚어준다.
4. **공급 파이프라인** ([건축인허가] 블록 있으면): 인허가는 2-3년 후
   입주로 이어지는 **공급 선행지표**. 최근 N개월 주거 인허가 세대수·
   연면적이 늘면 미래 공급↑(중장기 가격 하방), 줄면 공급 절벽(상방).
   현재 실거래·지수와 시간축이 다름을 명시 (지금 가격 ≠ 미래 공급).
   표본 법정동 기준임을 1줄 언급.
5. **매크로 연계**: 기준금리·주담대 금리를 web search 로 확인해 가격
   방향성 맥락 제공 (출처 날짜 {ymd} 이하, 미확인 시 명시). 전세가율은
   위 데이터의 정확값을 우선 사용 (web search 추정치로 덮지 말 것).
6. **섹터 함의 (중립)**: 건설(시공)·부동산(디벨로퍼/리츠)·은행(부동산 PF
   부실) 에 미치는 영향을 정보 차원에서 서술. 특정 종목 매수 권유 금지.
7. **구조** (각 섹션 지정 이모지 헤더 한 줄):
   🏠 시장 총평 (거래량·가격 방향 · R-ONE 지수 추세)
   📍 지역별 온도차 (고가권 vs 외곽/지방)
   📐 공급 파이프라인 (건축인허가 선행지표, 블록 있을 때만)
   💰 매크로 연계 (금리·전세가율)
   🏗️ 섹터 함의 (건설/부동산/은행, 중립)
   🎯 한 줄 결론
8. **본문 일반 텍스트** + 이모지 헤더. 강조는 **굵게**. HTML 태그·`---`
   수평선 금지. 각 항목 줄바꿈.
면책: 끝에 "본 브리프는 공공데이터 관찰 (교육·정보 목적), 투자 권유 아님" 1줄.
"""


def _post_process(text: str) -> str:
    try:
        from bot.daily_kr_flow import _post_process as _pp
        return _pp(text, _now_kst().date().isoformat())
    except Exception:
        import re
        text = re.sub(r"(?m)^[^\w\n]*[-*_]{2,}[^\w\n]*$", "", text)
        return text.strip()


def _log_usage(pt: int, ot: int, cost_krw: float) -> None:
    import time as _time
    for path, rec in (
        (_USAGE_LOG, {"ts": _now_kst().isoformat(timespec="seconds"),
                      "date": _now_kst().date().isoformat(),
                      "month": _now_kst().date().isoformat()[:7],
                      "prompt_tok": pt, "output_tok": ot, "cost_krw": round(cost_krw, 4)}),
        (_NOAH_USAGE_LOG, {"ts": _time.time(), "type": "llm_call",
                           "model": "gemini-2.5-pro", "prompt_tokens": pt,
                           "completion_tokens": ot,
                           "cost_usd": round(cost_krw / _USD_TO_KRW, 6),
                           "subsystem": "realestate"}),
    ):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warning("realestate: usage log failed: %s", exc)


def _save_archive(body: str, cost_krw: float, ymd: str, elapsed: float,
                  kind: str = "weekly", png_rel: str | None = None) -> None:
    try:
        now = _now_kst()
        date_iso = now.date().isoformat()
        day_dir = os.path.join(_ARCHIVE_DIR, date_iso)
        os.makedirs(day_dir, exist_ok=True)
        slug = "realestate_monthly" if kind == "monthly" else "realestate"
        path = os.path.join(day_dir, f"{now:%H%M%S}_{slug}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": now.isoformat(timespec="seconds"), "date": date_iso,
                       "ymd": ymd, "kind": kind, "body": body,
                       "cost_krw": round(cost_krw, 4),
                       "elapsed_sec": round(elapsed, 1), "png": png_rel},
                      f, ensure_ascii=False)
    except Exception as exc:
        log.warning("realestate: archive write failed: %s", exc)
        return
    try:
        from bot.dashboard import regenerate_realestate_index
        regenerate_realestate_index()
    except Exception as exc:
        log.warning("realestate: dashboard regen failed: %s", exc)


def generate() -> tuple[str, float, str | None] | None:
    import time as _time
    _t0 = _time.monotonic()
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("realestate: GOOGLE_API_KEY missing")
        return None
    from bot.realestate_client import collect_realestate_data, realestate_key_ready
    if not realestate_key_ready():
        return None
    data = collect_realestate_data()
    if not data.get("regions"):
        log.warning("realestate: 실거래 데이터 0건 — skip")
        return None

    # R-ONE 월간 지수 추세 (선행지표, 키 없으면 graceful skip)
    trend = {}
    try:
        from bot.rone_client import realestate_trend, rone_key_ready
        if rone_key_ready():
            trend = realestate_trend()
    except Exception as exc:
        log.warning("realestate: R-ONE trend fetch failed: %s", exc)

    # 건축인허가 공급 파이프라인 (선행지표, 키 없으면 graceful skip)
    permit_agg = {}
    try:
        from bot.buildperm_client import (permits_aggregate, _PERMIT_REGIONS,
                                          buildperm_key_ready)
        if buildperm_key_ready():
            permit_agg = permits_aggregate(_PERMIT_REGIONS, months=12)
    except Exception as exc:
        log.warning("realestate: 건축인허가 fetch failed: %s", exc)

    summary = build_summary(data)
    trend_block = build_trend_block(trend)
    if trend_block:
        summary = f"{summary}\n\n{trend_block}"
    permit_block = build_permit_block(permit_agg)
    if permit_block:
        summary = f"{summary}\n\n{permit_block}"

    from bot.screener import _call_pro
    prompt = _PROMPT.format(ymd=data["ymd"], summary=summary)
    try:
        raw, pt, ot = _call_pro(api_key, prompt, enable_grounding=True)
    except Exception as exc:
        log.exception("realestate: Pro call failed: %s", exc)
        return None
    if not raw:
        return None
    body = _post_process(raw)
    cost_krw = (pt * _PRO_IN + ot * _PRO_OUT) / 1e6 * _USD_TO_KRW
    _log_usage(pt, ot, cost_krw)

    # 인포그래픽 PNG (MOLIT 정확값 직접 주입, 환각 0) — archive/realestate_img/
    png_path = None
    png_rel = None
    try:
        from bot.realestate_infographic import render_infographic
        fname = f"{data['ymd']}_{_now_kst():%H%M%S}.png"
        png_path = render_infographic(data, os.path.join(_IMG_DIR, fname))
        if png_path:
            png_rel = f"realestate_img/{fname}"
    except Exception as exc:
        log.warning("realestate: infographic render failed: %s", exc)

    _save_archive(body, cost_krw, data["ymd"], _time.monotonic() - _t0,
                  kind="weekly", png_rel=png_rel)
    ymd = data["ymd"]
    title = f"🏠 <b>부동산 Byte - {ymd[:4]}.{ymd[4:6]}</b>"
    full = f"{title}\n<i>아파트 실거래 주간 브리프 · 생성 {_now_kst():%m.%d %H:%M} KST</i>\n\n{body}"
    return full, cost_krw, png_path


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception:
        pass
    result = generate()
    if result is None:
        log.error("realestate: generation failed / no data — skipping push")
        return 1
    body, cost, png = result
    log.info("realestate: generated (₩%.1f, infographic=%s) — pushing",
             cost, "yes" if png else "no")
    from bot.daily_kr_flow import push_telegram, push_telegram_photo
    if png:
        push_telegram_photo(png, "🏠 부동산 Byte — 지역별 실거래 인포그래픽")
    ok = push_telegram(body)
    log.info("realestate: push %s", "OK" if ok else "with failures")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
