"""포트폴리오 저장·집계·요약 (자산관리 P1 증분3).

흐름: parse_export(뱅샐 zip/xlsx) → resolve_ticker(종목명→티커) → 집계 모델 →
저장(JSON). 대시보드(증분4)·텔레그램 핸들러가 이 모델을 소비. 라이브 가격·
NOAH 분석 오버레이는 증분4·5에서 모델 위에 얹는다.

저장: ~/.tradingagents/portfolio.json (atomic). 파서가 1.고객정보·가계부를 애초에
제외하므로 저장 데이터엔 이름/이메일/소비내역이 없다 — 보유종목·자산군·순자산·
대출·보험만. (PII 최소화.)

순수 함수(build_model/format/_won)는 단위테스트 가능. ingest 만 I/O.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from bot.portfolio_parser import parse_export, is_banksalad_export
from bot.portfolio_resolve import resolve_ticker

PORTFOLIO_PATH = Path.home() / ".tradingagents" / "portfolio.json"


class NotBanksaladExport(ValueError):
    """업로드된 .zip/.xlsx 가 뱅크샐러드 자산 export 가 아님.

    RAG 채널('무엇이든 포워드')의 비-자산 파일이 확장자만으로 자산 업데이트로
    오인되는 것을 막기 위해 ingest 가 저장 전에 던진다 — watcher 는 조용히
    skip 해 기존 portfolio.json 을 보존(2026-06-08 사용자 리포트)."""


def _won(v) -> str:
    """₩ 금액 → 억/만 약식(가독). None→'-'. (차트 fmtAxis 와 동일 철학.)"""
    if v is None:
        return "-"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    neg = n < 0
    n = abs(n)
    if n >= 1e8:
        s = f"{n / 1e8:.1f}억"
    elif n >= 1e4:
        s = f"{n / 1e4:,.0f}만"
    else:
        s = f"{n:,.0f}"
    return ("-" if neg else "") + s + "원"


def _distinct_stock_keys(holdings: list[dict]) -> set:
    """고유 종목 식별 키 집합.

    같은 종목을 여러 증권사에 보유하면 뱅샐 export 가 행을 분리해(중복) 단순
    포지션 건수는 같은 종목을 두 번 센다(사용자 2026-06-05: "타 증권사에 같은
    종목 두개는 하나로"). 매칭된 종목은 **ticker** 로 dedup(증권사 간 종목명
    표기 차이까지 흡수), 미매칭은 **종목명** 으로 dedup. '보유 종목' 카운트가
    포지션 건수가 아닌 실제 종목 수를 뜻하게 하기 위함. 보유 테이블 자체는
    원본 행을 유지(증권사 필터 보존) — 카운트만 고유 기준."""
    keys = set()
    for h in holdings:
        if h.get("matched") and h.get("ticker"):
            keys.add(("t", h["ticker"]))
        else:
            keys.add(("n", (h.get("상품명") or "").strip()))
    return keys


def build_model(parsed: dict, resolve=resolve_ticker) -> dict:
    """파서 결과 → 대시보드/요약용 집계 모델.

    - holdings: 각 보유에 ticker/market/matched + 평가손익(평가금액−투자원금) 부착.
    - by_broker: 증권사별 평가금액·원금·손익·종목수.
    - asset_allocation: 재무현황 자산 카테고리별 합(예적금/투자/부동산/동산/연금…).
    - net_worth: 총자산/총부채/순자산(파서가 항목 합으로 산출).
    - top_gainers/losers: 수익률 정렬 상·하위 5.
    """
    holdings = []
    for h in parsed.get("holdings", []):
        r = resolve(h.get("상품명") or "")
        ev, cost = h.get("평가금액"), h.get("투자원금")
        pnl = (ev - cost) if (ev is not None and cost is not None) else None
        holdings.append({
            **h, "ticker": r["ticker"], "market": r["market"],
            "matched": r["matched"], "평가손익": pnl,
        })
    by_broker: dict[str, dict] = {}
    for h in holdings:
        b = h.get("금융사") or "?"
        d = by_broker.setdefault(b, {"평가금액": 0.0, "투자원금": 0.0, "종목수": 0})
        d["평가금액"] += h.get("평가금액") or 0.0
        d["투자원금"] += h.get("투자원금") or 0.0
        d["종목수"] += 1
    for d in by_broker.values():
        d["평가손익"] = d["평가금액"] - d["투자원금"]
    fin = parsed.get("finance", {})
    alloc: dict[str, float] = {}
    for cat, items in fin.get("assets", {}).items():
        tot = sum(it.get("amount") or 0.0 for it in items)
        if tot:
            alloc[cat] = tot
    # 수익률 TOP/WORST 용 종목명 dedup — 같은 종목을 여러 증권사에 보유하면
    # export 에 행이 중복되어 상/하위에 같은 종목이 두 번 뜬다(비보심 랩스
    # 2026-06-04). |수익률| 가장 큰 1개만 남겨('더 두드러지는것') 중복·상하위
    # 동시노출 방지. (보유 테이블·집계는 원본 전체 유지 — dedup 은 랭킹 표시용만.)
    _by_name: dict = {}
    for h in holdings:
        if h.get("수익률") is None:
            continue
        nm = h.get("상품명")
        cur = _by_name.get(nm)
        if cur is None or abs(h["수익률"]) > abs(cur["수익률"]):
            _by_name[nm] = h
    rated = list(_by_name.values())
    return {
        "as_of": parsed.get("as_of"),
        "net_worth": {
            "총자산": fin.get("총자산"), "총부채": fin.get("총부채"),
            "순자산": fin.get("순자산"),
        },
        "holdings": holdings,
        "by_broker": by_broker,
        "asset_allocation": alloc,
        "liabilities": fin.get("liabilities", {}),
        "loans": parsed.get("loans", []),
        "insurance": parsed.get("insurance", []),
        "top_gainers": sorted(rated, key=lambda h: h["수익률"], reverse=True)[:5],
        "top_losers": sorted(rated, key=lambda h: h["수익률"])[:5],
        "holding_count": len(holdings),
        # 고유 종목 수(증권사 중복 제외) — '보유 종목' 카운트의 canonical 값.
        # holding_count(포지션 건수)는 증권사별 합/스냅샷 비교용으로 유지.
        "distinct_count": len(_distinct_stock_keys(holdings)),
        "matched_count": sum(1 for h in holdings if h["matched"]),
        # 증분(자산 변화) 비교용 압축 스냅샷 — ingest 가 다음 업로드 시 prev 로 사용.
        "snapshot": {
            "총자산": fin.get("총자산"), "총부채": fin.get("총부채"),
            "순자산": fin.get("순자산"),
            "주식평가": sum(h.get("평가금액") or 0 for h in holdings),
            "주식원금": sum(h.get("투자원금") or 0 for h in holdings),
            "종목수": len(holdings),
            "holdings_pnl": {
                f"{h.get('상품명', '')}|{h.get('금융사', '')}": h.get("평가손익") or 0
                for h in holdings
            },
        },
    }


def format_summary_text(model: dict) -> str:
    """텔레그램 회신용 한 화면 요약 (증권사별·자산배분)."""
    nw = model.get("net_worth", {})
    lines = [
        "📂 자산 요약 (뱅크샐러드 기준)",
        f"순자산 {_won(nw.get('순자산'))}  "
        f"(자산 {_won(nw.get('총자산'))} − 부채 {_won(nw.get('총부채'))})",
        f"주식 {model.get('distinct_count', model.get('holding_count', 0))}종목 · "
        f"티커매칭 {model.get('matched_count', 0)}",
    ]
    if model.get("by_broker"):
        lines.append("— 증권사별 —")
        for b, d in sorted(model["by_broker"].items(), key=lambda kv: -kv[1]["평가금액"]):
            lines.append(f"• {b}: {_won(d['평가금액'])} ({d['종목수']}종목, 손익 {_won(d['평가손익'])})")
    if model.get("asset_allocation"):
        lines.append("— 자산 배분 —")
        for cat, amt in sorted(model["asset_allocation"].items(), key=lambda kv: -kv[1]):
            lines.append(f"• {cat}: {_won(amt)}")
    if model.get("loans"):
        lines.append(f"— 대출 {len(model['loans'])}건 · 보험 {len(model.get('insurance', []))}건 —")
    return "\n".join(lines)


def save(model: dict) -> None:
    """atomic write to portfolio.json (+ 직전 1개 .bak 백업).

    덮어쓰기 전 기존 portfolio.json 을 portfolio.json.bak 으로 복사 → 어떤
    사고(잘못된 ingest 등)로 손상돼도 1회 롤백 가능. 백업 실패는 비치명적
    (메인 저장은 그대로 진행)."""
    PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PORTFOLIO_PATH.exists():
        try:
            bak = PORTFOLIO_PATH.with_suffix(".json.bak")
            bak.write_text(PORTFOLIO_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    payload = {**model, "_saved_ts": time.time()}
    tmp = PORTFOLIO_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PORTFOLIO_PATH)


def load() -> dict | None:
    try:
        return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def ingest(data, password=None) -> dict:
    """zip/xlsx(바이트 또는 경로) → 파싱·resolve·집계 → 저장. 모델 반환.

    텔레그램 핸들러(증분3 wiring)·CLI 가 호출. password 기본은 호출부가 .env
    BANKSALAD_ZIP_PW 에서 주입(코드/깃에 비번 박지 않음)."""
    prev = load()  # 직전 저장 모델 (증분 비교용) — 첫 업로드면 None
    parsed = parse_export(data, password=password)
    # 진짜 뱅샐 export 인지 게이트 — 아니면 저장·집계 없이 즉시 중단해 기존
    # portfolio.json 을 보존(비-자산 RAG 파일이 빈 모델로 덮어쓰는 사고 차단).
    if not is_banksalad_export(parsed):
        raise NotBanksaladExport("뱅크샐러드 자산 export 아님 (재무/투자 섹션 미검출)")
    model = build_model(parsed)
    baseline = None
    if isinstance(prev, dict) and prev.get("snapshot"):
        import datetime as _dt

        def _d(ts):
            return (_dt.datetime.fromtimestamp(
                ts, _dt.timezone(_dt.timedelta(hours=9))).date() if ts else None)

        # 같은 날짜 재업로드면 기준(baseline)을 리셋하지 않고 직전 baseline 을
        # 승계 — '같은 날짜는 마지막 업로드가 현재, 비교는 직전 다른 날짜 기준'
        # (사용자 정책 2026-06-04). 다른 날짜면 직전 업로드 스냅샷이 새 baseline.
        if _d(prev.get("_saved_ts")) == _d(time.time()):
            baseline = prev.get("prev")
        else:
            baseline = {
                **prev["snapshot"],
                "as_of": prev.get("as_of"),
                "_saved_ts": prev.get("_saved_ts"),
            }
    if isinstance(baseline, dict):
        model["prev"] = baseline
    save(model)
    # 가계부(현금흐름) — 같은 export 에서 별도 모델·대시보드 (P2, 2026-06-04).
    # 실패해도 자산 ingest 는 그대로 반환(비치명적).
    try:
        from bot.budget import build_budget_model, save_budget
        save_budget(build_budget_model(parsed))
    except Exception:
        pass
    return model


def main(argv=None) -> int:
    """CLI 검증: ``python -m bot.portfolio <export.zip|.xlsx> [--pw PW] [--no-regen]``.

    RAG 채널 없이 로컬 파일로 즉시 파싱·집계·저장·대시보드 갱신 — 실제 zip
    테스트용(사용자 요청 2026-06-04). 비번은 ``--pw`` 또는 .env BANKSALAD_ZIP_PW.
    PII(고객정보/가계부)는 파서가 애초에 제외하므로 저장물엔 들어가지 않는다.
    ⚠️ ingest 는 ~/.tradingagents/portfolio.json 을 덮어쓴다(실데이터 갱신)."""
    import argparse
    import sys
    p = argparse.ArgumentParser(prog="bot.portfolio",
                                description="뱅크샐러드 export 로컬 검증·대시보드 갱신")
    p.add_argument("path", help="뱅크샐러드 export .zip 또는 .xlsx 경로")
    p.add_argument("--pw", default=None, help="zip 비밀번호(미지정 시 .env BANKSALAD_ZIP_PW)")
    p.add_argument("--no-regen", action="store_true", help="대시보드 재생성 생략(저장만)")
    args = p.parse_args(argv)
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass
    pw = args.pw or os.environ.get("BANKSALAD_ZIP_PW") or None
    try:
        data = Path(args.path).read_bytes()
    except OSError as exc:
        print(f"파일 읽기 실패: {exc}", file=sys.stderr)
        return 2
    try:
        model = ingest(data, password=pw)
    except RuntimeError as exc:
        print(f"🔒 비밀번호 오류 또는 zip 해제 실패: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI 친절 메시지
        print(f"파싱 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(format_summary_text(model))
    if not args.no_regen:
        try:
            from bot.dashboard import regenerate_portfolio_index
            regenerate_portfolio_index()
            print("\n✅ 대시보드 갱신됨 → portfolio.html")
        except Exception as exc:  # noqa: BLE001
            print(f"\n⚠️ 대시보드 재생성 실패(저장은 완료): {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
