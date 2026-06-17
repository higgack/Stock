"""G(회사맵핑) feasibility 프로브 — DART 사업보고서 '매출 구성/주요제품'
추출 가능성 측정 (읽기전용·봇 동작 변경 0·매핑 노출 0).

배경(사용자 2026-06-17): trade/ 산업트렌드 카드의 '관련기업'은 현재
`mti_companies._MAP`(수동 `품목명→회사`) + 채널 조인 두 출처. G 는 이를
**DART 매출구성에서 `회사→[제품, 매출비중]` 역인덱스**를 뽑아 관세청 품목명과
매칭해 후보를 자동 생성(정확도 우선 — operator 승인 전 미노출)하는 작업.

⚠️ 근본 제약: OpenDART 는 **제품별 매출을 구조화 API 로 주지 않는다**
(단일회사 주요계정/재무제표엔 제품 분해 없음). 제품별 매출은 **사업보고서
본문(document.xml)** 의 'II. 사업의 내용' 표에 들어 있어, 본문을 파싱해야
한다. 표 구조는 회사마다 천차만별 → **추출 성공률을 실데이터로 먼저 측정**
하는 것이 이 프로브의 목적. 샌드박스에선 DART 키/네트워크가 없어 검증 불가
(실수기록 #12: 검증 불가능한 외부 API 는 단정 보고 금지) → **VM 1회 실행**용.

이 프로브가 답하는 것:
  1. 최신 사업보고서 rcept_no 를 찾는가? (list.json)
  2. document.xml 다운로드되는가? (dart_feed 가 매일 하므로 거의 확실)
  3. **매출 구성/제품 표가 추출되는가?** ← 진짜 미지수
     - (A) 표 그리드 파싱(원문 마크업 <TABLE>/<TR>/<TD…> 보존)
     - (B) 평문 라벨-값 regex (이 코드베이스의 검증된 방식 = dart_feed)
  두 방식 결과 + 원문 샘플을 저장해, 그 출력으로 G1 파서 전략을 확정한다.

산출물(읽기전용):
  - stdout: 회사별 성공/카운트 + 추출 샘플 + 커버리지 요약
  - <DATA>/dart_revenue_probe/probe_result.json — 구조화 결과
  - <DATA>/dart_revenue_probe/raw/<code>_<rcept>.txt — 원문 매출 표 영역
    (마크업 보존) + 평문 영역 (파서 설계용)
  (<DATA> = $TRADE_DATA_DIR 또는 ~/.trade)

run (VM):
  cd ~/stock-trade && .venv/bin/python -m trade.scripts.probe_dart_revenue
  # 또는 종목 지정:
  .venv/bin/python -m trade.scripts.probe_dart_revenue --tickers 005930,000660,247540

다운로드/디코딩 로직은 bot/dart_feed._fetch_doc_text(검증됨) 패턴을 따르되,
표 구조를 보려고 태그를 보존한다(평문 view 도 함께 산출).
"""
from __future__ import annotations

import argparse
import html
import io
import json
import logging
import os
import re
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests

log = logging.getLogger("probe-dart-revenue")

# ── 표본 종목 (mega/large/mid/small + 산업 다양성; _MAP 과 겹쳐 정답 대조 가능) ──
# code → (표시명, 기대 제품 힌트) — 힌트는 추출 결과 eyeball 대조용 메모일 뿐.
_SAMPLE: list[tuple[str, str, str]] = [
    ("005930", "삼성전자", "DRAM/NAND/DX(가전·모바일) 멀티세그먼트"),
    ("000660", "SK하이닉스", "DRAM/NAND 메모리"),
    ("247540", "에코프로비엠", "양극재 단일성"),
    ("373220", "LG에너지솔루션", "이차전지"),
    ("005490", "POSCO홀딩스", "철강 멀티"),
    ("051910", "LG화학", "석유화학/첨단소재/생명과학 멀티"),
    ("010950", "S-Oil", "정유/석유화학/윤활"),
    ("068270", "셀트리온", "바이오시밀러"),
    ("161390", "한국타이어앤테크놀로지", "타이어"),
    ("097950", "CJ제일제당", "식품/바이오 멀티"),
    ("005290", "동진쎄미켐", "포토레지스트 소형주"),
    ("042700", "한미반도체", "반도체장비 중형주"),
]

_DART_BASE = "https://opendart.fss.or.kr/api"
_REVENUE_KEYWORDS = (
    "매출실적", "매출 실적", "주요 제품", "주요제품", "매출 구성", "매출구성",
    "제품별 매출", "사업부문별", "주요 제품 및 서비스", "매출 및 수주",
    "품목별", "제품등의 현황", "주요제품등의현황",
)
_TOTAL_NAMES = ("합계", "합 계", "총계", "소계", "계", "총 계", "기타")


# ─────────────────────────────────────────────────────────────────────
# 순수 헬퍼 (단위테스트 — test_probe_dart_revenue.py)
# ─────────────────────────────────────────────────────────────────────

def pick_business_report(rows: list[dict]) -> dict | None:
    """list.json rows → 가장 적합한 정기보고서 1건.
    우선순위: 사업보고서(연간, 제품표 최다) > 반기 > 분기, 각 그룹 내 최신
    rcept_dt. 정정본도 포함(최신이면 더 정확). 매칭 0 → None. 순수."""
    if not rows:
        return None
    for kind in ("사업보고서", "반기보고서", "분기보고서"):
        cand = [r for r in rows if kind in (r.get("report_nm") or "")]
        if cand:
            best = max(cand, key=lambda r: (r.get("rcept_dt") or ""))
            return {
                "rcept_no": (best.get("rcept_no") or "").strip(),
                "report_nm": (best.get("report_nm") or "").strip(),
                "rcept_dt": (best.get("rcept_dt") or "").strip(),
                "kind": kind,
            }
    return None


def _unescape(s: str) -> str:
    try:
        s = html.unescape(s)
    except Exception:
        pass
    return s.replace("\xa0", " ")


def flatten(markup: str) -> str:
    """원문 마크업 → 평문 (dart_feed._fetch_doc_text 와 동일 방식: 태그 제거
    + 공백 정규화). 순수."""
    txt = re.sub(r"<[^>]+>", " ", markup)
    return re.sub(r"\s+", " ", _unescape(txt)).strip()


def find_revenue_sections(flat: str) -> list[tuple[str, int]]:
    """평문에서 매출 섹션 키워드 위치 [(keyword, index)] (등장순). 순수."""
    hits: list[tuple[str, int]] = []
    for kw in _REVENUE_KEYWORDS:
        start = 0
        while True:
            i = flat.find(kw, start)
            if i < 0:
                break
            hits.append((kw, i))
            start = i + len(kw)
    hits.sort(key=lambda x: x[1])
    return hits


def extract_tables_raw(markup: str, cap: int = 60) -> list[str]:
    """원문 마크업 → <TABLE>…</TABLE> 블록 리스트 (대소문자 무시). 순수."""
    return re.findall(r"<TABLE\b.*?</TABLE>", markup, re.DOTALL | re.IGNORECASE)[:cap]


def parse_table_block(block: str) -> list[list[str]]:
    """<TABLE> 블록 → 행×셀 (TR + TD/TE/TU/TH 변형). 셀 태그를 모르는 경우를
    대비해 다중 후보 + 평문 폴백. 순수."""
    rows: list[list[str]] = []
    trs = re.findall(r"<TR\b.*?</TR>", block, re.DOTALL | re.IGNORECASE)
    for tr in trs:
        cells = re.findall(
            r"<(?:TD|TE|TU|TH)\b[^>]*>(.*?)</(?:TD|TE|TU|TH)>",
            tr, re.DOTALL | re.IGNORECASE,
        )
        if not cells:  # 셀 태그가 다른 변형 — 태그 전부 제거 후 1셀로
            flat = flatten(tr)
            cells = [flat] if flat else []
        rows.append([flatten(c) for c in cells])
    return [r for r in rows if any(c.strip() for c in r)]


def _to_pct(s: str) -> float | None:
    """'33.1%' / '33.1' / '33.1 %' → 33.1 (0~100 범위만). 순수."""
    if not s:
        return None
    m = re.search(r"(-?\d{1,3}(?:\.\d+)?)\s*%", s)
    if m:
        try:
            v = float(m.group(1))
            return v if -1 <= v <= 100 else None
        except ValueError:
            return None
    t = s.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d+)?", t):
        v = float(t)
        return v if 0 <= v <= 100 else None
    return None


def _to_amount(s: str) -> int | None:
    """'1,234,567' → 1234567 (콤마 포함 4자리+ 숫자만; 연도/소수 오인 방지). 순수."""
    if not s:
        return None
    m = re.search(r"\d{1,3}(?:,\d{3})+", s)  # 콤마 천단위 = 금액 신호
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _is_texty(s: str) -> bool:
    """이름다운가 — 한글/영문 2자+ 포함 & 순수 숫자/기호가 아님. 순수."""
    s = (s or "").strip()
    if not s or s in _TOTAL_NAMES:
        return False
    if re.fullmatch(r"[\d,.\s%()\-~]+", s):
        return False
    return len(re.findall(r"[가-힣A-Za-z]", s)) >= 2


def score_revenue_table(rows: list[list[str]]) -> int:
    """표가 매출구성-스러운 정도 (헤더/셀 키워드). 순수."""
    if not rows:
        return 0
    joined = " ".join(c for r in rows[:3] for c in r)
    score = 0
    for kw in ("매출", "비중", "구성", "품목", "제품", "금액", "구분", "주요"):
        if kw in joined:
            score += 1
    if any("%" in c for r in rows for c in r):
        score += 1
    return score


def products_from_rows(rows: list[list[str]]) -> list[dict]:
    """표 행 → [{name, share_pct, amount}] 휴리스틱 (헤더로 컬럼 탐지,
    없으면 셀 스캔 폴백). 총계행 제외. 순수."""
    if not rows:
        return []
    hdr_i, header = None, None
    for i, row in enumerate(rows[:4]):
        joined = " ".join(row)
        if any(k in joined for k in ("매출", "비중", "품목", "제품", "구성", "구분", "금액", "주요")):
            hdr_i, header = i, row
            break
    name_col = share_col = amt_col = None
    if header:
        # 세분 제품명(품목/제품) 우선 — 관세청 품목명 매칭 목적. 없으면
        # 세그먼트(구분/사업부문)로 폴백.
        for strong in ("품목", "제품", "상품", "명칭", "규격"):
            for j, c in enumerate(header):
                if strong in c:
                    name_col = j
                    break
            if name_col is not None:
                break
        if name_col is None:
            for j, c in enumerate(header):
                if any(k in c for k in ("구분", "구성", "사업", "부문")):
                    name_col = j
                    break
        for j, c in enumerate(header):
            if share_col is None and any(k in c for k in ("비중", "구성비", "점유", "비율")):
                share_col = j
            if amt_col is None and any(k in c for k in ("매출", "금액", "액")):
                amt_col = j
    data = rows[hdr_i + 1:] if hdr_i is not None else rows
    out: list[dict] = []
    for row in data:
        if not row:
            continue
        name = row[name_col] if (name_col is not None and name_col < len(row)) else ""
        if not _is_texty(name):
            name = next((c for c in row if _is_texty(c)), "")
        if not _is_texty(name):
            continue
        share = _to_pct(row[share_col]) if (share_col is not None and share_col < len(row)) else None
        if share is None:
            for c in row:
                if "%" in c:
                    share = _to_pct(c)
                    if share is not None:
                        break
        amount = _to_amount(row[amt_col]) if (amt_col is not None and amt_col < len(row)) else None
        if amount is None:
            nums = [n for n in (_to_amount(c) for c in row) if n is not None]
            amount = max(nums) if nums else None
        out.append({"name": name.strip()[:40], "share_pct": share, "amount": amount})
    return out


def products_from_flat(segment: str, cap: int = 12) -> list[dict]:
    """평문 세그먼트에서 'NN.N%' 앞 한글 토큰을 제품 후보로 (이 코드베이스의
    평문-regex 방식이 매출구성에 통하는지 가늠용, 거칠다). 순수."""
    out: list[dict] = []
    for m in re.finditer(r"([가-힣A-Za-z][가-힣A-Za-z0-9·\-() ]{1,24}?)\s*([\d]{1,3}(?:\.\d+)?)\s*%", segment):
        name = m.group(1).strip(" ·-()")
        if not _is_texty(name) or name in _TOTAL_NAMES:
            continue
        try:
            pct = float(m.group(2))
        except ValueError:
            continue
        if 0 <= pct <= 100:
            out.append({"name": name[:40], "share_pct": pct})
        if len(out) >= cap:
            break
    return out


# ─────────────────────────────────────────────────────────────────────
# 네트워크 (VM 전용 — 샌드박스 미검증)
# ─────────────────────────────────────────────────────────────────────

def _data_dir() -> Path:
    return Path(os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade"))


def _load_env() -> None:
    """DART_API_KEY 확보 — bare `python -m` 은 .env 자동 로드 안 함(실수기록
    #12a). cwd + ~/stock + ~/stock-trade .env 시도."""
    try:
        from dotenv import load_dotenv
    except Exception:
        load_dotenv = None
    candidates = [
        Path.cwd() / ".env",
        Path.home() / "stock" / ".env",
        Path.home() / "stock-trade" / ".env",
    ]
    for p in candidates:
        if not p.exists():
            continue
        if load_dotenv:
            load_dotenv(p, override=False)
        else:  # 수동 폴백 (dotenv 부재 venv 대비)
            for line in p.read_text("utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _fetch_report_list(api_key: str, corp_code: str, days: int = 600) -> list[dict]:
    """list.json 정기공시(A) — 최근 days 일. rows(report_nm/rcept_no/rcept_dt)."""
    end = date.today()
    bgn = end - timedelta(days=days)
    try:
        r = requests.get(
            f"{_DART_BASE}/list.json",
            params={
                "crtfc_key": api_key, "corp_code": corp_code,
                "bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": "A", "page_count": 100, "page_no": 1,
            },
            timeout=15,
        )
        payload = r.json()
    except Exception as exc:
        log.warning("list.json %s 실패: %s", corp_code, exc)
        return []
    if payload.get("status") != "000":
        log.info("list.json %s status=%s msg=%s", corp_code,
                 payload.get("status"), payload.get("message", ""))
        return []
    return payload.get("list") or []


def download_doc_raw(api_key: str, rcept_no: str) -> str | None:
    """document.xml zip → 원문 마크업(태그 보존). bot/dart_feed._fetch_doc_text
    의 zip/디코딩 패턴 재사용, 단 flatten 안 함(표 구조 보려고)."""
    try:
        r = requests.get(f"{_DART_BASE}/document.xml",
                         params={"crtfc_key": api_key, "rcept_no": rcept_no},
                         timeout=25)
        blob = r.content or b""
        if len(blob) < 200 or (blob[:1] in (b"{", b"<") and b"status" in blob[:200]):
            log.info("document.xml %s: 비-zip 응답 (len=%d)", rcept_no, len(blob))
            return None
        zf = zipfile.ZipFile(io.BytesIO(blob))
        names = [n for n in zf.namelist() if n.lower().endswith((".xml", ".html"))]
        if not names:
            return None
        name = max(names, key=lambda n: zf.getinfo(n).file_size)
        raw = zf.read(name)[:8_000_000]
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp949", errors="ignore")
    except Exception as exc:
        log.warning("document.xml %s 실패: %s", rcept_no, exc)
        return None


def _best_revenue_window(flat: str) -> tuple[str, int] | None:
    """매출 섹션 키워드 중 '비중/구성/제품' 밀집도가 가장 높은 위치."""
    hits = find_revenue_sections(flat)
    if not hits:
        return None
    best, best_score = None, -1
    for kw, i in hits:
        window = flat[i:i + 1500]
        score = window.count("%") + window.count("매출") + window.count("비중")
        if score > best_score:
            best, best_score = (kw, i), score
    return best


def probe_one(api_key: str, code: str, name: str, raw_dir: Path) -> dict:
    """단일 종목 프로브 결과 dict."""
    from bot.dart_client import get_dart
    res: dict = {"code": code, "name": name, "ok": False}
    corp_code = get_dart().stock_code_to_corp_code(code)
    if not corp_code:
        res["error"] = "corp_code 미해결"
        return res
    rows = _fetch_report_list(api_key, corp_code)
    rep = pick_business_report(rows)
    if not rep:
        res["error"] = "정기보고서 미발견"
        return res
    res["report"] = rep
    markup = download_doc_raw(api_key, rep["rcept_no"])
    if not markup:
        res["error"] = "document.xml 다운로드/해제 실패"
        return res
    res["doc_chars"] = len(markup)
    flat = flatten(markup)

    # (A) 표 그리드 파싱
    tables = extract_tables_raw(markup)
    res["table_count"] = len(tables)
    scored = sorted(
        ((score_revenue_table(parse_table_block(t)), t) for t in tables),
        key=lambda x: -x[0],
    )
    cand = [(s, t) for s, t in scored if s >= 4][:4]
    res["revenue_table_candidates"] = len(cand)
    grid_products: list[dict] = []
    for _, t in cand[:2]:
        grid_products.extend(products_from_rows(parse_table_block(t)))
    res["grid_products"] = grid_products[:15]
    res["grid_with_share"] = sum(1 for p in grid_products if p.get("share_pct") is not None)

    # (B) 평문 라벨-값 (코드베이스 검증 방식)
    win = _best_revenue_window(flat)
    flat_products: list[dict] = []
    flat_segment = ""
    if win:
        kw, i = win
        flat_segment = flat[max(0, i - 80):i + 1600]
        flat_products = products_from_flat(flat_segment)
        res["flat_keyword"] = kw
    res["flat_products"] = flat_products
    res["ok"] = bool(grid_products or flat_products)

    # 원문 샘플 저장 (파서 설계용) — 후보 표 마크업 + 평문 세그먼트
    try:
        raw_dir.mkdir(parents=True, exist_ok=True)
        out = [f"# {code} {name} | {rep['report_nm']} | rcept={rep['rcept_no']} "
               f"| doc_chars={len(markup)} | tables={len(tables)} "
               f"| revenue_candidates={len(cand)}\n"]
        out.append("\n===== (A) 매출 표 후보 마크업 (상위 2) =====\n")
        for s, t in cand[:2]:
            out.append(f"\n--- score={s} ---\n{t[:6000]}\n")
        out.append("\n===== (B) 평문 매출 세그먼트 =====\n")
        out.append(flat_segment[:3000])
        (raw_dir / f"{code}_{rep['rcept_no']}.txt").write_text(
            "\n".join(out), encoding="utf-8")
    except Exception as exc:
        log.warning("raw 저장 실패 %s: %s", code, exc)
    return res


def _fmt_products(ps: list[dict], n: int = 4) -> str:
    bits = []
    for p in ps[:n]:
        sh = f"{p['share_pct']}%" if p.get("share_pct") is not None else "—"
        bits.append(f"{p['name']}({sh})")
    return ", ".join(bits) if bits else "(없음)"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="DART 매출구성 추출 feasibility 프로브 (읽기전용)")
    p.add_argument("--tickers", default="",
                   help="쉼표구분 6자리 코드 (미지정 시 기본 12 표본)")
    p.add_argument("--sleep", type=float, default=0.4, help="종목간 대기(초)")
    args = p.parse_args(argv)

    _load_env()
    key = (os.environ.get("DART_API_KEY") or "").strip()
    if not key:
        print("⛔ DART_API_KEY 없음 — ~/stock/.env 에 DART_API_KEY 확인 필요.")
        print("   공유 시: cat ~/stock/.env | sed 's/=.*$/=***REDACTED***/'")
        return 2
    print(f"✅ DART_API_KEY 로드됨 (len={len(key)})")

    if args.tickers.strip():
        targets = [(c.strip(), c.strip(), "") for c in args.tickers.split(",") if c.strip()]
    else:
        targets = _SAMPLE

    raw_dir = _data_dir() / "dart_revenue_probe" / "raw"
    results: list[dict] = []
    print(f"\n{'코드':<7}{'회사':<22}{'보고서':<10}{'표':>4}{'매출후보':>7}"
          f"{'그리드(비중)':>11}{'평문':>5}  샘플")
    print("─" * 110)
    for code, name, _hint in targets:
        try:
            r = probe_one(key, code, name, raw_dir)
        except Exception as exc:
            r = {"code": code, "name": name, "ok": False, "error": f"예외: {exc}"}
        results.append(r)
        if r.get("error"):
            print(f"{code:<7}{name[:20]:<22}❌ {r['error']}")
        else:
            rep = r.get("report", {})
            grid = r.get("grid_products", [])
            sample = _fmt_products(grid if grid else r.get("flat_products", []))
            print(f"{code:<7}{name[:20]:<22}{rep.get('kind', '?')[:8]:<10}"
                  f"{r.get('table_count', 0):>4}{r.get('revenue_table_candidates', 0):>7}"
                  f"{r.get('grid_with_share', 0):>11}{len(r.get('flat_products', [])):>5}"
                  f"  {sample[:48]}")
        time.sleep(args.sleep)

    # 커버리지 요약
    n = len(results)
    got_report = sum(1 for r in results if r.get("report"))
    got_doc = sum(1 for r in results if r.get("doc_chars"))
    got_grid = sum(1 for r in results if r.get("grid_products"))
    got_grid_share = sum(1 for r in results if r.get("grid_with_share", 0) >= 2)
    got_flat = sum(1 for r in results if r.get("flat_products"))
    print("─" * 110)
    print(f"\n📊 커버리지 ({n} 종목):")
    print(f"  • 정기보고서 발견      : {got_report}/{n}")
    print(f"  • document.xml 다운로드: {got_doc}/{n}")
    print(f"  • 그리드 제품 추출(≥1) : {got_grid}/{n}")
    print(f"  • 그리드 비중 추출(≥2) : {got_grid_share}/{n}  ← G1 backbone 핵심 지표")
    print(f"  • 평문 제품 추출(≥1)   : {got_flat}/{n}")

    out_path = _data_dir() / "dart_revenue_probe" / "probe_result.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n💾 결과: {out_path}")
        print(f"💾 원문 샘플: {raw_dir}/  (회사별 매출 표 마크업 + 평문)")
    except Exception as exc:
        print(f"⚠️ 결과 저장 실패: {exc}")

    print("\n다음 단계 판단:")
    print("  - 그리드 비중 추출이 높으면(예 ≥8/12) → 룰 기반 표 파서로 G1 진행")
    print("  - 낮고 평문만 잡히면 → 평문 regex 또는 본문 LLM 추출(비용 게이트)로 G1'")
    print("  - 원문 샘플(raw/*.txt)을 보고 실제 표 구조에 맞춰 파서 확정")
    return 0


if __name__ == "__main__":
    sys.exit(main())
