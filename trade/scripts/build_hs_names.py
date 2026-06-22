"""관세청 'HS부호 단위별 품목명' 공공데이터 → trade/data/hs_names.tsv 변환
(사용자 2026-06-22 '무실적 코드까지 전부 이름').

data.go.kr '관세청_HS부호 단위별 품목명'(15130660) 파일(CSV/Excel)을 받아,
HS코드 열 + 한글품목명 열만 뽑아 `code<TAB>한글명` TSV 로 저장한다. 헤더명이
판마다 달라도 자동 탐지(코드열=값이 대부분 숫자 / 한글명열=한글 비중 최다).

실행:
    .venv/bin/python -m trade.scripts.build_hs_names <받은파일.csv|.xlsx>
    .venv/bin/python -m trade.scripts.build_hs_names <파일> --out <경로>
    .venv/bin/python -m trade.scripts.build_hs_names <파일> --code-col HS부호 --name-col 한글품목명

graceful: 열 자동탐지 실패 시 --code-col/--name-col 로 지정. .xlsx 는 openpyxl
있을 때만(없으면 CSV 로 받아 재실행). 기존 hs_names.tsv 는 덮어씀.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

_DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "data", "hs_names.tsv")
_HANGUL = re.compile(r"[가-힣]")


def _read_rows(path: str) -> list[list[str]]:
    """파일 → 행 리스트(헤더 포함). .csv/.tsv = csv 모듈, .xlsx = openpyxl."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook
        except ImportError:
            sys.exit("openpyxl 미설치 — data.go.kr 에서 CSV 로 받아 재실행하세요.")
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        return [["" if c is None else str(c) for c in row]
                for row in ws.iter_rows(values_only=True)]
    # CSV/TSV — 인코딩 폴백(utf-8-sig → cp949, 정부 파일 cp949 흔함)
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            with open(path, encoding=enc, newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                delim = "\t" if sample.count("\t") > sample.count(",") else ","
                return [list(r) for r in csv.reader(f, delimiter=delim)]
        except (UnicodeDecodeError, UnicodeError):
            continue
    sys.exit(f"인코딩 판독 실패: {path}")


def _digit_ratio(s: str) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0
    return sum(c.isdigit() for c in s) / len(s)


def _detect_cols(rows: list[list[str]]) -> tuple[int, int]:
    """(코드열 idx, 한글명열 idx) 자동탐지. 헤더명 힌트 우선, 없으면 데이터 통계."""
    if not rows:
        sys.exit("빈 파일")
    header = [(_h or "").strip() for _h in rows[0]]
    code_i = name_i = -1
    for i, h in enumerate(header):
        hl = h.replace(" ", "")
        if code_i < 0 and ("HS" in hl.upper() or "부호" in hl or "코드" in hl):
            code_i = i
        if name_i < 0 and (("한글" in hl and "명" in hl) or hl == "품목명"
                           or ("국문" in hl and "명" in hl)):
            name_i = i
    # 데이터 통계 폴백(헤더 힌트 실패 시): 샘플 행에서 숫자비율 최대=코드, 한글 최다=명
    body = rows[1:200] or rows
    ncol = max((len(r) for r in body), default=0)
    if code_i < 0:
        code_i = max(range(ncol),
                     key=lambda c: sum(_digit_ratio(r[c]) for r in body if c < len(r)),
                     default=0)
    if name_i < 0:
        name_i = max((c for c in range(ncol) if c != code_i),
                     key=lambda c: sum(len(_HANGUL.findall(r[c])) for r in body if c < len(r)),
                     default=0)
    return code_i, name_i


def build(path: str, out: str = _DEFAULT_OUT,
          code_col: str | None = None, name_col: str | None = None) -> int:
    rows = _read_rows(path)
    header = [(_h or "").strip() for _h in rows[0]] if rows else []
    if code_col and code_col in header:
        code_i = header.index(code_col)
    elif name_col and name_col in header:
        code_i, _ = _detect_cols(rows)
    else:
        code_i, name_i = _detect_cols(rows)
    if name_col and name_col in header:
        name_i = header.index(name_col)
    elif code_col and code_col in header:
        _, name_i = _detect_cols(rows)
    print(f"열 매핑: 코드='{header[code_i] if code_i < len(header) else code_i}' "
          f"명='{header[name_i] if name_i < len(header) else name_i}'")
    seen: dict[str, str] = {}
    for r in rows[1:]:
        if code_i >= len(r) or name_i >= len(r):
            continue
        code = re.sub(r"\D", "", r[code_i] or "")
        name = (r[name_i] or "").strip()
        if not code or not name or not _HANGUL.search(name):
            continue
        # 더 구체적(긴 코드)·동일 코드 최신만 — 마지막 승(정렬 가정 안 함)
        seen[code] = name
    if not seen:
        sys.exit("추출 0행 — --code-col/--name-col 로 열을 지정해 재실행하세요.")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for code in sorted(seen):
            f.write(f"{code}\t{seen[code]}\n")
    print(f"✅ {len(seen):,}개 HS 한글품목명 → {out}")
    return len(seen)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", help="관세청 HS부호 단위별 품목명 파일(.csv/.xlsx)")
    ap.add_argument("--out", default=_DEFAULT_OUT)
    ap.add_argument("--code-col", default=None, help="코드 열 헤더명(자동탐지 실패 시)")
    ap.add_argument("--name-col", default=None, help="한글명 열 헤더명")
    a = ap.parse_args(argv)
    build(a.file, a.out, a.code_col, a.name_col)
    return 0


if __name__ == "__main__":
    sys.exit(main())
