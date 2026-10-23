"""리포트 수치 대조 가드 감사 — 아카이브의 실제 리포트로 발화율을 잰다.

읽기 전용 · 네트워크 0 · LLM 0 · ₩0. **전 시장**.

⚠️ 왜 필요한가: `bot.report_numeric_guard` 의 허용 차이·건너뛰기 낱말은
샌드박스에서 만든 출발점이지 실측이 아니다(#12). 너무 느슨하면 틀린 값을
놓치고, 너무 빡빡하면 리포트마다 경고가 붙어 아무도 안 읽는다(#25·#260).
그래서 지금까지 쌓인 리포트 전부에 **제품과 같은 함수**(`scan`)를 태워
지표별·섹션별로 몇 번 판정했고 몇 번 어긋났는지, 어긋난 원문은 무엇인지를
찍는다. 그 표가 다음 조정의 근거다.

⚠️ 기준값이 제품과 다르다 — 못 보는 축(#274):
  · 제품은 분석 **시작** 때 FACTUAL ANCHOR 에 실린 `.info` 값으로 잰다.
  · 이 감사는 아카이브에 함께 저장된 `stock_info`(분석 **끝**, 저장 시점
    스냅샷 · 글리치 보정 반영)로 잰다. 두 시점 사이 가격이 움직였거나 보정이
    끼면 현재가·시총 축에서 제품보다 더 많이(또는 적게) 어긋날 수 있다.
  그래서 여기서 나온 수는 '제품이 붙였을 경고 수'의 근사다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.report_guard_audit
    .venv/bin/python -m bot.scripts.report_guard_audit --days 90 --samples 30
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

_AUDIT_VER = 1
_KST = timezone(timedelta(hours=9))

# stock_info 키 → yfinance `.info` 키(가드의 `canonical_from_info` 가 읽는 꼴).
_SI_TO_INFO = (
    ("current_price", "currentPrice"),
    ("market_cap", "marketCap"),
    ("trailingPE", "trailingPE"),
    ("priceToBook", "priceToBook"),
    ("trailingEps", "trailingEps"),
    ("fiftyTwoWeekHigh", "fiftyTwoWeekHigh"),
    ("fiftyTwoWeekLow", "fiftyTwoWeekLow"),
    ("fiftyDayAverage", "fiftyDayAverage"),
    ("twoHundredDayAverage", "twoHundredDayAverage"),
)


def canonical_from_record(rec: dict) -> dict:
    """아카이브 레코드 → 가드 기준값. 기준이 하나도 없으면 빈 dict. 순수.

    규칙은 제품과 **같은 함수**(`canonical_from_info`)다 — 감사가 기준을 따로
    만들면 제품과 다른 걸 잰다(#35). 통화는 제품처럼 시장 설정에서 읽는다."""
    si = rec.get("stock_info") or {}
    if not isinstance(si, dict):
        return {}
    from bot.report_numeric_guard import canonical_from_info
    out = canonical_from_info({dst: si.get(src) for src, dst in _SI_TO_INFO})
    if not out:
        return {}
    # 통화는 제품처럼 응답의 통화를 먼저, 없으면 시장 설정(analyzer 와 같은 순서).
    ccy = str(si.get("currency") or "").strip()
    if not ccy:
        try:
            from bot.market import get_market_config
            ccy = get_market_config(str(rec.get("ticker") or "")).get("currency", "")
        except Exception:                                   # noqa: BLE001
            ccy = ""
    out["currency"] = ccy
    return out


def _product_section_titles() -> tuple[str, ...]:
    """제품이 가드를 거는 섹션 제목 — `_format_full` 이 쓰는 그 목록(#35)."""
    try:
        from bot.analyzer import _REPORT_SECTIONS
        return tuple(label for _k, label, _a in _REPORT_SECTIONS) + ("✅ 최종 결정",)
    except Exception:                                       # noqa: BLE001
        return ("✅ 최종 결정",)


def split_sections(full_report: str, titles: tuple[str, ...] | None = None
                   ) -> list[tuple[str, str]]:
    """제품 섹션 제목(`## 📈 시장 분석` …)에서만 나눈다. 순수(titles 를 주면).

    ⚠️ LLM 이 본문 안에 쓴 `## 동종업계 비교` 같은 소제목에서 자르면 그 줄이
    청크 제목이 되어 `scan` 이 못 본다 — 그러면 제품은 건너뛰는 비교 문단을
    감사만 세어, 재려던 오탐을 감사가 부풀린다(독립 리뷰 2026-10-22)."""
    titles = titles if titles is not None else _product_section_titles()
    out: list[tuple[str, str]] = []
    title, buf = "(머리)", []
    for line in (full_report or "").split("\n"):
        if line.startswith("## ") and line[3:].strip() in titles:
            if buf:
                out.append((title, "\n".join(buf)))
            title, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if buf:
        out.append((title, "\n".join(buf)))
    return out


def audit(records: list[dict]) -> dict:
    """레코드 목록 → 집계. 순수(가드도 순수)."""
    from bot.report_numeric_guard import scan
    judged: Counter = Counter()
    missed: Counter = Counter()
    sec_judged: Counter = Counter()
    sec_missed: Counter = Counter()
    samples: list[tuple] = []
    n_rec = n_ref = n_flagged_rec = 0
    for rec in records:
        n_rec += 1
        can = canonical_from_record(rec)
        if not can:
            continue
        n_ref += 1
        rec_flag = False
        for title, body in split_sections(rec.get("full_report") or ""):
            if title == "(머리)":
                continue          # 제품은 리포트 머리(제목·지난 추천)에 가드를 안 건다(#35)
            for f, ok in scan(body, can):
                judged[f.metric] += 1
                sec_judged[title] += 1
                if not ok:
                    missed[f.metric] += 1
                    sec_missed[title] += 1
                    rec_flag = True
                    samples.append((rec.get("trade_date"), rec.get("ticker"),
                                    title, f))
        n_flagged_rec += int(rec_flag)
    return {"records": n_rec, "with_ref": n_ref, "flagged_records": n_flagged_rec,
            "judged": judged, "missed": missed,
            "sec_judged": sec_judged, "sec_missed": sec_missed,
            "samples": samples}


def _load(root: Path, days: int) -> list[dict]:
    cutoff = (datetime.now(_KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    out: list[dict] = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name < cutoff:
            continue
        for p in sorted(d.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:                        # noqa: BLE001
                print(f"  ⚠️ 읽기 실패 {p.name}: {type(exc).__name__}")
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _pct(a: int, b: int) -> str:
    return f"{a / b * 100:.1f}%" if b else "—"


def report_lines(res: dict, samples: int = 15) -> list[str]:
    from bot.report_numeric_guard import LABEL_KO
    lines: list[str] = []
    n_rec, n_ref = res["records"], res["with_ref"]
    lines.append(f"리포트 {n_rec}건 · 기준값(stock_info) 있는 리포트 {n_ref}건"
                 f" · 기준 없음 {n_rec - n_ref}건(판정 안 함)")
    total_j = sum(res["judged"].values())
    total_m = sum(res["missed"].values())
    if not n_ref or not total_j:
        lines.append("❓ 판정 불가 — 대조한 인용이 0건입니다"
                     " (기준값 없는 옛 레코드뿐이거나 라벨 붙은 값이 없음).")
        return lines
    lines.append("")
    lines.append("지표별 — 판정 · 어긋남 · 비율")
    for k in LABEL_KO:
        j, m = res["judged"].get(k, 0), res["missed"].get(k, 0)
        if j:
            lines.append(f"  {LABEL_KO[k]:<10} {j:>5}건 · {m:>4}건 · {_pct(m, j)}")
    lines.append(f"  {'합계':<10} {total_j:>5}건 · {total_m:>4}건 · {_pct(total_m, total_j)}")
    lines.append("")
    lines.append("섹션별 — 판정 · 어긋남")
    for t, j in res["sec_judged"].most_common():
        lines.append(f"  {t[:24]:<24} {j:>5}건 · {res['sec_missed'].get(t, 0):>4}건")
    rate = res["flagged_records"] / n_ref
    lines.append("")
    lines.append(f"경고가 붙었을 리포트: {res['flagged_records']}/{n_ref}"
                 f" ({rate * 100:.1f}%)")
    if rate > 0.3:
        # ❌ = 고칠 수 있는 결함(가드 조정) — 주간 감사 결산에 이 줄이 실린다
        # (`audit_sweep._findings` 는 ❌ 줄만 올리므로 줄 하나로 자족해야 한다, #356).
        lines.append(f"❌ 리포트 {res['flagged_records']}/{n_ref}건"
                     f"({rate * 100:.0f}%)에 수치 대조 경고가 붙습니다 — 셋 중 하나"
                     " 넘게 붙으면 아무도 안 읽습니다. 표본에서 오탐(동종사·과거값을"
                     " 현재값으로 읽음)인지 진짜 어긋남인지 가려 허용 차이·건너뛰기"
                     " 낱말을 조정할 것.")
    else:
        lines.append(f"✅ 경고 비율 {rate * 100:.1f}% (기준 30% 이하) —"
                     " 표본으로 진짜 어긋남인지 확인할 것.")
    if res["samples"]:
        lines.append("")
        lines.append(f"어긋난 표본 (최신 {min(samples, len(res['samples']))}"
                     f"/{len(res['samples'])}건)")
        for d, t, sec, f in sorted(res["samples"], key=lambda x: str(x[0]),
                                   reverse=True)[:samples]:
            # 원문 발췌에 '❌' 가 있으면 sweep 이 그 줄을 결함으로 센다(#250) — 바꿔 찍는다.
            lines.append((f"  {d} {t} [{sec[:14]}] {LABEL_KO[f.metric]}"
                          f" {f.quoted:,.4g} ↔ 기준 {f.reference:,.4g}"
                          f" ({f.gap_pct:+.0f}%) 「{f.quote_text}」").replace("❌", "✗"))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30, help="최근 N일 (기본 30)")
    ap.add_argument("--samples", type=int, default=15, help="어긋난 표본 수")
    args = ap.parse_args(argv)
    from bot.archive import ARCHIVE_ROOT
    from bot.report_numeric_guard import TOLERANCE
    print(f"report_guard_audit v{_AUDIT_VER} · 인터프리터 {sys.executable}")
    print(f"아카이브 {ARCHIVE_ROOT} · 최근 {args.days}일 · 허용 차이 "
          + ", ".join(f"{k} {v:.0%}" for k, v in TOLERANCE.items()))
    print("기준값 = 아카이브 stock_info(저장 시점) — 제품은 분석 시작 시점"
          " FACTUAL ANCHOR 를 쓴다. 근사치로 읽을 것.")
    recs = _load(ARCHIVE_ROOT, args.days)
    for line in report_lines(audit(recs), args.samples):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
