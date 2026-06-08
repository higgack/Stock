"""뱅크샐러드 자산 export(.xlsx, 보통 비밀번호 .zip 안에 동봉) 파서.

뱅크샐러드 앱 → '데이터 내보내기' → 메일로 받은 zip 을 풀면 나오는
'YYYY-MM-DD~YYYY-MM-DD.xlsx' 의 **'뱅샐현황' 시트(Sheet1)** 를 구조화한다.
('가계부 내역' Sheet2 는 다음 단계 — 여기선 안 건드림.)

시트 섹션 (B열 'N.제목' 마커로 구분):
  1.고객정보   — 이름/이메일/신용점수 = PII → **파싱하지 않음** (개인정보 보호)
  2.현금흐름현황 — 월별 수입/지출 매트릭스
  3.재무현황    — 자산(예적금/전자금융/투자/부동산/동산/연금/보험) + 부채 → 순자산
  4.보험현황    — 보험 계약
  5.투자현황    — 주식 보유 (금융사/상품명/투자원금/평가금액/수익률) ★ 종목 상세
  6.대출현황    — 대출 (원금/잔액/금리/만기)

설계 메모:
- **stdlib only** (zipfile + xml.etree). pandas/openpyxl/pyzipper 불필요.
- 비번 zip 은 구식 ZipCrypto 라 `zipfile(pwd=...)` 로 충분(AES 아님 — 2026-06-04
  실파일 확인). 향후 AES 로 바뀌면 pyzipper 로 교체.
- 금액 단위는 원(KRW) — 해외주식 평가금액도 export 가 이미 원화 환산해 넣음.
- 순수 함수 위주라 단위테스트 가능. 핵심 로직은 `parse_banksalad(rows)` 에 있고
  xlsx/zip 읽기는 얇은 wrapper.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET


# ─────────────────────────── xlsx / zip 읽기 ───────────────────────────

def _local(tag: str) -> str:
    """'{ns}tag' → 'tag' (namespace 제거)."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def extract_xlsx_from_zip(zip_bytes: bytes, password: str | bytes | None = None) -> bytes:
    """비밀번호(선택) zip 에서 첫 .xlsx 멤버의 바이트를 돌려준다.

    뱅크샐러드 zip 은 ZipCrypto(legacy) 라 stdlib zipfile 로 복호 가능.
    password 가 틀리면 zipfile 이 RuntimeError('Bad password') 를 던진다 —
    호출부가 잡아 사용자에게 비번 재요청하도록."""
    pwd = password.encode() if isinstance(password, str) else password
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        xlsx = [n for n in z.namelist() if n.lower().endswith(".xlsx")]
        if not xlsx:
            raise ValueError("zip 안에 .xlsx 파일이 없습니다")
        return z.read(xlsx[0], pwd=pwd)


_COL_RE = re.compile(r"[A-Z]+")


def _col_letter(ref: str) -> str:
    m = _COL_RE.match(ref or "")
    return m.group(0) if m else "?"


def _sheet_names(zf: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(zf.read("xl/workbook.xml"))
    names: list[str] = []
    for el in root.iter():
        if _local(el.tag) == "sheet":
            names.append(el.attrib.get("name") or f"sheet{len(names) + 1}")
    return names


def _parse_sheet(sroot: ET.Element, sst: list[str]) -> list[dict]:
    rows: list[dict] = []
    for row in sroot.iter():
        if _local(row.tag) != "row":
            continue
        cells: dict[str, str] = {}
        for c in row:
            if _local(c.tag) != "c":
                continue
            ref = c.attrib.get("r", "")
            t = c.attrib.get("t")
            val = None
            for ch in c:
                lt = _local(ch.tag)
                if lt == "v":
                    val = ch.text
                elif lt == "is":  # inline string
                    val = "".join(x.text or "" for x in ch.iter() if _local(x.tag) == "t")
            if t == "s" and val is not None:  # shared-string index
                try:
                    val = sst[int(val)]
                except (ValueError, IndexError):
                    pass
            if val is not None and val != "":
                cells[_col_letter(ref)] = val
        rows.append(cells)
    return rows


def read_xlsx(data: bytes | str) -> dict[str, list[dict]]:
    """xlsx(바이트 또는 경로) → {시트이름: [row dict {열문자: 값}]}.

    문자열 셀은 sharedStrings 로 해석, 숫자는 원문 그대로. 빈 셀 생략.
    시트 순서는 workbook.xml 순서 ↔ sheet{N}.xml (뱅샐 export 에서 일치 확인)."""
    zf = zipfile.ZipFile(io.BytesIO(data)) if isinstance(data, (bytes, bytearray)) else zipfile.ZipFile(data)
    with zf:
        sst: list[str] = []
        try:
            sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in sroot:
                if _local(si.tag) == "si":
                    sst.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
        except KeyError:
            pass
        out: dict[str, list[dict]] = {}
        for idx, name in enumerate(_sheet_names(zf), start=1):
            try:
                sroot = ET.fromstring(zf.read(f"xl/worksheets/sheet{idx}.xml"))
            except KeyError:
                continue
            out[name] = _parse_sheet(sroot, sst)
        return out


# ─────────────────────────── 뱅샐현황 섹션 파서 ───────────────────────────

_SECTION_RE = re.compile(r"^\s*(\d+)\.\s*(.+?)\s*$")


def _num(v) -> float | None:
    """문자/숫자 → float. 콤마·공백·'-' 처리. 변환 불가 시 None."""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _sections(rows: list[dict]) -> dict[int, tuple[str, int, int]]:
    """B열 'N.제목' 마커로 섹션 경계 → {번호: (제목, start_idx, end_idx)}."""
    marks: list[tuple[int, str, int]] = []
    for i, r in enumerate(rows):
        b = r.get("B")
        if b:
            m = _SECTION_RE.match(str(b))
            if m:
                marks.append((int(m.group(1)), m.group(2), i))
    out: dict[int, tuple[str, int, int]] = {}
    for k, (num, title, start) in enumerate(marks):
        end = marks[k + 1][2] if k + 1 < len(marks) else len(rows)
        out[num] = (title, start, end)
    return out


def _header_idx(rows, start, end, key: str) -> int | None:
    """섹션 내에서 B열 == key 인 헤더 행 인덱스 (없으면 None)."""
    for i in range(start, end):
        if str(rows[i].get("B") or "").strip() == key:
            return i
    return None


def _parse_holdings(rows, sec) -> list[dict]:
    """5.투자현황: B종류 | C금융사 | D상품명 | F투자원금 | G평가금액 | H수익률 | I가입 | J만기."""
    if not sec:
        return []
    _, start, end = sec
    h = _header_idx(rows, start, end, "투자상품종류")
    out = []
    for i in range((h + 1) if h is not None else start, end):
        r = rows[i]
        b, d = r.get("B"), r.get("D")
        if not b or not d or b in ("투자상품종류", "총계"):
            continue
        out.append({
            "종류": b, "금융사": r.get("C"), "상품명": d,
            "투자원금": _num(r.get("F")), "평가금액": _num(r.get("G")),
            "수익률": _num(r.get("H")), "가입일": r.get("I"), "만기일": r.get("J"),
        })
    return out


def _parse_loans(rows, sec) -> list[dict]:
    """6.대출현황: B대출종류 | C금융사 | D상품명 | F원금 | G잔액 | H금리 | I신규 | J만기."""
    if not sec:
        return []
    _, start, end = sec
    h = _header_idx(rows, start, end, "대출종류")
    out = []
    for i in range((h + 1) if h is not None else start, end):
        r = rows[i]
        b = r.get("B")
        if not b or b in ("대출종류", "총계"):
            continue
        out.append({
            "대출종류": b, "금융사": r.get("C"), "상품명": r.get("D"),
            "대출원금": _num(r.get("F")), "대출잔액": _num(r.get("G")),
            "대출금리": _num(r.get("H")), "신규일": r.get("I"), "만기일": r.get("J"),
        })
    return out


def _parse_insurance(rows, sec) -> list[dict]:
    """4.보험현황: B금융사 | C보험명 | E계약상태 | F총납입금 | G계약일 | H만기일."""
    if not sec:
        return []
    _, start, end = sec
    h = _header_idx(rows, start, end, "금융사")
    out = []
    for i in range((h + 1) if h is not None else start, end):
        r = rows[i]
        b, c = r.get("B"), r.get("C")
        if not b or b in ("금융사", "총계"):
            continue
        out.append({
            "금융사": b, "보험명": c, "계약상태": r.get("E"),
            "총납입금": _num(r.get("F")), "계약일": r.get("G"), "만기일": r.get("H"),
        })
    return out


def _parse_finance(rows, sec) -> dict:
    """3.재무현황 — 2단(자산 B/C/E, 부채 F/G/I). 카테고리 라벨이 B/F 에 한 번 뜨고
    아래 항목으로 carry-down. 총자산/순자산/총부채는 summary 로 분리."""
    empty = {"assets": {}, "liabilities": {}, "총자산": None, "총부채": None, "순자산": None}
    if not sec:
        return empty
    _, start, end = sec
    h = _header_idx(rows, start, end, "항목")  # 'B:항목 ... F:항목' 헤더
    if h is None:
        return empty
    assets: dict[str, list] = {}
    liabs: dict[str, list] = {}
    totals: dict[str, float | None] = {}
    cur_a = cur_l = None
    for i in range(h + 1, end):
        r = rows[i]
        b, c, e = r.get("B"), r.get("C"), r.get("E")
        f, g, ii = r.get("F"), r.get("G"), r.get("I")
        # ── 자산 측 (좌) ──
        if b and b not in ("항목", "자산", "부채"):
            if b in ("총자산", "순자산"):
                totals[b] = _num(e)
                cur_a = None
            else:
                cur_a = b
                assets.setdefault(cur_a, [])
        if c and cur_a:
            assets.setdefault(cur_a, []).append({"name": c, "amount": _num(e)})
        # ── 부채 측 (우) ──
        if f and f not in ("항목", "자산", "부채"):
            if f in ("총부채",):
                totals[f] = _num(ii)
                cur_l = None
            else:
                cur_l = f
                liabs.setdefault(cur_l, [])
        if g and cur_l:
            liabs.setdefault(cur_l, []).append({"name": g, "amount": _num(ii)})
    # 뱅샐 export 는 총자산/순자산/총부채 summary 셀을 비워둠(0) — 항목 합으로
    # 산출하는 게 canonical (현금흐름 총계도 동일하게 빈 것 확인, 2026-06-04).
    asset_total = sum(it["amount"] for cat in assets.values() for it in cat if it.get("amount"))
    liab_total = sum(it["amount"] for cat in liabs.values() for it in cat if it.get("amount"))
    return {
        "assets": assets, "liabilities": liabs,
        "총자산": asset_total, "총부채": liab_total, "순자산": asset_total - liab_total,
        # export 원본 summary 셀(보통 비어있음) — 투명성/디버깅용 보존.
        "총자산_export": totals.get("총자산"), "총부채_export": totals.get("총부채"),
        "순자산_export": totals.get("순자산"),
    }


def _parse_cashflow(rows, sec) -> dict:
    """2.현금흐름현황 — B항목 | C총계 | D월평균 | E..(월별). 매트릭스 그대로 캡처."""
    if not sec:
        return {"months": [], "rows": []}
    _, start, end = sec
    h = _header_idx(rows, start, end, "항목")
    if h is None:
        return {"months": [], "rows": []}
    hdr = rows[h]
    # 월 컬럼 = E 이후, 값이 'YYYY-MM' 형태
    month_cols = []
    months = []
    for col in sorted((c for c in hdr if len(c) <= 2), key=lambda c: (len(c), c)):
        v = str(hdr[col])
        if re.match(r"^\d{4}-\d{2}$", v):
            month_cols.append(col)
            months.append(v)
    out_rows = []
    for i in range(h + 1, end):
        r = rows[i]
        item = r.get("B")
        if not item:
            continue
        out_rows.append({
            "항목": item, "총계": _num(r.get("C")), "월평균": _num(r.get("D")),
            "monthly": [_num(r.get(col)) for col in month_cols],
        })
    return {"months": months, "rows": out_rows}


def parse_banksalad(rows: list[dict]) -> dict:
    """뱅샐현황 시트 rows → 구조화 dict. 1.고객정보(PII)는 의도적으로 제외."""
    sec = _sections(rows)
    return {
        "cashflow": _parse_cashflow(rows, sec.get(2)),
        "finance": _parse_finance(rows, sec.get(3)),
        "insurance": _parse_insurance(rows, sec.get(4)),
        "holdings": _parse_holdings(rows, sec.get(5)),
        "loans": _parse_loans(rows, sec.get(6)),
        # 검출된 뱅샐 섹션 번호 — 진짜 export 인지 검증용(is_banksalad_export).
        # RAG 채널엔 비-자산 .zip/.xlsx 도 올라오므로(무엇이든 포워드) 오인 차단.
        "_sections": sorted(sec.keys()),
    }


# 진짜 뱅크샐러드 자산 export 는 '재무현황(3)' / '투자현황(5)' 섹션 마커를
# 항상 포함한다(보유 0건이어도 헤더는 존재). 비-뱅샐 xlsx 는 이 마커가 없어
# _sections() 가 비고, parse_banksalad 가 전부 빈/None 모델을 낸다 — 그걸
# 그대로 저장·알림하면 'RAG 채널에 올린 무관한 파일'이 거짓 자산 업데이트로
# 둔갑한다(2026-06-08 사용자 리포트). 저장·푸시 전 이 게이트로 차단.
_BANKSALAD_CORE_SECTIONS = frozenset({3, 5})


def is_banksalad_export(parsed: dict) -> bool:
    """파싱 결과가 실제 뱅크샐러드 자산 export 인지 — 섹션 마커 기반 판정.

    재무현황(3) 또는 투자현황(5) 섹션이 하나라도 검출되면 진짜 export 로 본다.
    둘 다 없으면 비-자산 파일(RAG 채널의 임의 .zip/.xlsx)로 간주 → 호출부가
    저장·알림을 건너뛰어 기존 portfolio.json 을 보존."""
    secs = set(parsed.get("_sections") or [])
    return bool(secs & _BANKSALAD_CORE_SECTIONS)


def _pick_status_sheet(sheets: dict[str, list[dict]]) -> list[dict]:
    """뱅샐현황 시트 선택 — 이름 우선, 없으면 섹션 마커('5.투자현황')가 있는 시트,
    그래도 없으면 첫 시트. (시트 순서/이름 변형에 견고)."""
    if "뱅샐현황" in sheets:
        return sheets["뱅샐현황"]
    for rows in sheets.values():
        if _sections(rows).get(5):
            return rows
    return next(iter(sheets.values()), [])


def parse_export(data: bytes | str, password: str | bytes | None = None) -> dict:
    """zip(비번) 또는 xlsx 바이트/경로 → parse_banksalad 결과.

    bytes 가 zip(PK\\x03\\x04 + 암호화 플래그)인지 xlsx 인지 구분이 까다로우니,
    먼저 zip 으로 열어 .xlsx 멤버가 있으면 추출, 아니면 그 자체를 xlsx 로 읽는다."""
    if isinstance(data, (bytes, bytearray)):
        try:
            xlsx_bytes = extract_xlsx_from_zip(bytes(data), password)
        except (ValueError, zipfile.BadZipFile):
            xlsx_bytes = bytes(data)  # 이미 xlsx
        sheets = read_xlsx(xlsx_bytes)
    else:
        # 경로: .zip 이면 추출, 아니면 직접
        if str(data).lower().endswith(".zip"):
            with open(data, "rb") as fh:
                xlsx_bytes = extract_xlsx_from_zip(fh.read(), password)
            sheets = read_xlsx(xlsx_bytes)
        else:
            sheets = read_xlsx(data)
    return parse_banksalad(_pick_status_sheet(sheets))


def summarize(parsed: dict) -> dict:
    """대시보드/검증용 합계 — 금액 미노출 설계에 맞게 '구조 카운트 + 총액'만."""
    fin = parsed.get("finance", {})
    holdings = parsed.get("holdings", [])
    n_by_broker: dict[str, int] = {}
    eval_sum = 0.0
    cost_sum = 0.0
    for hld in holdings:
        n_by_broker[hld.get("금융사") or "?"] = n_by_broker.get(hld.get("금융사") or "?", 0) + 1
        if hld.get("평가금액") is not None:
            eval_sum += hld["평가금액"]
        if hld.get("투자원금") is not None:
            cost_sum += hld["투자원금"]
    asset_cat_counts = {k: len(v) for k, v in fin.get("assets", {}).items()}
    return {
        "총자산": fin.get("총자산"), "총부채": fin.get("총부채"), "순자산": fin.get("순자산"),
        "주식_종목수": len(holdings),
        "주식_평가합": eval_sum, "주식_원금합": cost_sum,
        "주식_평가손익": (eval_sum - cost_sum) if holdings else None,
        "브로커별_종목수": n_by_broker,
        "자산카테고리": asset_cat_counts,
        "부채_건수": sum(len(v) for v in fin.get("liabilities", {}).values()),
        "보험_건수": len(parsed.get("insurance", [])),
        "대출_건수": len(parsed.get("loans", [])),
        "현금흐름_월수": len(parsed.get("cashflow", {}).get("months", [])),
    }
