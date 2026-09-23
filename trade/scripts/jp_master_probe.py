"""일본 상장 마스터 — 어떤 원천이 **(코드 → 이름)** 을 주는지 재기만 한다.

배경 — 혼합시장 보드(중국·말레이시아 등)에 일본 상장사가 실린다. 보드의
나라는 **교역 상대국**이지 상장 시장이 아니므로(#400) 4자리 코드 하나로는
도쿄(6976 = Taiyo Yuden)인지 대만(2330 = TSMC)인지 갈리지 않는다. 그래서
`stock_link` 은 확인 수단이 없으면 평문으로 둔다(#25·#171). 한국은
`build_krx_codes` 가 만든 로컬 마스터가 그 확인을 한다. 일본은 이 프로브 v2 의
VM 실측(2026-09-23)으로 원천을 골라 `build_jpx_codes`(JPX 영문 `data_e.xlsx`)가
그 마스터가 됐고, v3 의 ⑦ 이 **그 마스터로 제품 규칙을 태운 결과**를 잰다.

이 프로브는 **아무것도 배선하지 않는다.** 이름을 추측해 엔드포인트를 박으면
죽은 경로를 배포하므로(#151·#345), 후보를 **실호출해** 상태와 모양을 찍고
판정은 사람이 한다.

⚠️ ③ 이 모으는 것은 "일본 코드" 가 아니라 **도쿄 코드 모양**이다 — 대만
보드의 2330 도 같은 모양이라 같이 잡힌다. 그 구별이 안 된다는 것이 이
프로브가 답하려는 질문 자체다(#34 라벨에 기준을 박는다).

**무엇을 안 쓰나**(#264·#283·#321 — '안 쓴다' 는 무엇을 안 쓰는지까지 적어야
참이 된다, #284): 보드 DB 는 `mode=ro` 로 열어 **내용·mtime 을 한 바이트도**
안 바꾸고, 캐시·마커·마스터를 하나도 만들지 않으며, 내려받는 것은 전부 임시
디렉터리로 간다. ⚠️ 다만 SQLite 는 WAL DB 를 **읽을 때도** `-shm`/`-wal`
곁파일을 만들고 읽기 연결은 그걸 지우지 못한다(실측) — 그건 제품 쓰기 경로가
늘 만드는 것과 같은 파일이고 DB 내용과 무관하다. 회귀가 그 경계를 잰다.

Run:
    cd ~/stock && .venv/bin/python -m trade.scripts.jp_master_probe
    cd ~/stock-trade && .venv/bin/python -m trade.scripts.jp_master_probe

  ⚠️ 두 체크아웃 모두 레포 전체를 들고 있고 DB 는 `~/.trade` 로 공용이라
  어느 쪽에서 돌려도 ①③⑤⑥ 은 같다. 다른 것은 **venv 에 무엇이 깔렸나**
  뿐이고 ② 가 그걸 양쪽 다 물어 본다.

이상 없을 때의 출력(#274·#281) — ① 인터프리터·모듈 가용성 한 줄, ② 후보
인터프리터마다 한 줄, ③ 보드에서 찾은 도쿄 코드 모양 **전수**(자르지
않는다 — #156), ③-b 그 코드를 JP 선언 보드가 아는가, ④ yfinance 이름을
캡션 이름과 **나란히**, ⑤ JPX 후보 페이지마다 상태 + 파일 링크, ⑥ 그중
파일들의 헤더 + 데이터 2행, ⑦ 로컬 JPX 마스터(`build_jpx_codes` 가 만든 것)로
혼합 보드의 도쿄 코드를 **제품 규칙 그대로**(`lookup_query`) 태운 결과 — 링크가
붙는 행은 `✅ ../lookup/<코드>.T`, 안 붙는 행은 그 사유(목록에 없음 / 이름이
안 맞음). 어느 단계든 못 쟀으면 ❌/⏭ 로 적고, 하나도 못 쟀으면 ✅ 가 아니라
rc=1 이다(#54).
"""

from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from trade.stock_link import _JP_CODE          # 제품이 링크하는 그 모양(#35·#38)

_PROBE_VER = 3

_REPO = Path(__file__).resolve().parents[2]
# 이 출력을 만드는 코드(⑦ 은 제품 판정을 부른다 — `banner` 참조).
_FINGERPRINT_FILES = (Path(__file__).resolve(),
                      _REPO / "trade" / "stock_link.py",
                      _REPO / "trade" / "jpx_master.py")

# 후보 — **후보일 뿐**이다. 200 + 파일 링크가 나와야 증거가 된다(#151·#345).
# 마지막 항목은 대조군(#143): 이 레포가 운영에서 실제로 치는 페이지라,
# 후보가 전부 실패했을 때 "호스트가 막혔나 / 경로가 틀렸나"를 가른다.
_JPX_CANDIDATES = (
    ("JA 기타통계", "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"),
    ("EN 기타통계", "https://www.jpx.co.jp/english/markets/statistics-equities/misc/01.html"),
    ("대조군(운영 중)",
     "https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html"),
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NOAH-probe/1.0)"}
_TIMEOUT = 25
_YF_CHUNK = 8                   # 한 자식에 8종목 — 진행이 보이고 실패가 유계다(#103)
_YF_CAP = 48                    # 상한. 넘으면 **자른 사실을 말한다**(#45)
_SAMPLE_PER_PAGE = 2            # 한 페이지가 日/英 둘을 걸면 둘 다 본다(#143)


def banner() -> str:
    """이 출력을 **어느 코드가 만들었는지** 한 줄로(#21·#364).

    v3 부터 ⑦ 의 판정은 **제품 모듈**(`stock_link.jp_confirms` ·
    `jpx_master.load_envelope`)이 내리므로 지문이 그 둘을 같이 덮는다(이 함수가
    v2 에 적어 둔 "판정을 다른 모듈로 빼면 범위를 같이 넓힐 것" 그대로).
    ⚠️ **못 보는 축**(#274): 그 둘이 부르는 더 아래 모듈(`price_provider`)은
    안 덮는다 — ⑦ 이 읽는 것은 경로 상수 하나뿐이다.
    """
    import hashlib
    h = hashlib.sha1()
    try:
        for f in _FINGERPRINT_FILES:
            h.update(f.read_bytes())
        sig = h.hexdigest()[:10]
    except Exception as exc:                                   # noqa: BLE001
        sig = f"지문불가({type(exc).__name__})"
    return (f"■ 일본 마스터 프로브 v{_PROBE_VER} · 코드 지문 {sig} · "
            f"인터프리터 {sys.executable}")


def candidate_interpreters() -> list[str]:
    """이 VM 이 실제로 쓰는 파이썬 — **유닛 파일에서 파생**한다(#24·#316).

    손으로 적으면 새 체크아웃이 생겼을 때 조용히 빠진다. `ExecStart=` 의
    `*/bin/python` 을 전부 긁고 현재 인터프리터를 더해 dedup.
    """
    found: list[str] = [sys.executable]
    for svc in sorted((_REPO / "deploy").glob("*.service")):
        try:
            txt = svc.read_text(encoding="utf-8")
        except OSError:
            continue
        found.extend(m.group(1) for m in re.finditer(r"(/\S*/bin/python\d?)\b", txt))
    out: list[str] = []
    for p in found:
        if p not in out:
            out.append(p)
    return out


def module_report(py: str, mods: tuple[str, ...]) -> dict:
    """`py` 인터프리터에서 각 모듈이 **실제로 import 되나**(#25 이름이 아니라 실측).

    서브프로세스라 이 프로세스의 sys.modules 를 안 건드린다(#397).
    """
    code = ("import json,sys\n"
            "out={}\n"
            "for m in sys.argv[1:]:\n"
            "    try:\n"
            "        mod=__import__(m)\n"
            "        out[m]=getattr(mod,'__version__','?')\n"
            "    except Exception as e:\n"
            "        out[m]='❌ '+type(e).__name__\n"
            "print(json.dumps(out))\n")
    try:
        r = subprocess.run([py, "-c", code, *mods], capture_output=True,
                           text=True, timeout=90)
    except Exception as exc:                                   # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {exc}"}
    if r.returncode != 0:
        return {"_error": (r.stderr or "").strip()[:200] or f"rc={r.returncode}"}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as exc:                                   # noqa: BLE001
        return {"_error": f"출력 해석 실패 {type(exc).__name__}"}


def board_jp_candidates(data_dir: Path) -> tuple[list[tuple[str, str, str]],
                                                 list[str]]:
    """(행, 읽기실패) — 행은 (보드키, 코드, 캡션이름).

    소스를 열거하지 않는다(#24): 레지스트리에서 `basis=="company"` 를 고르고,
    표는 `sqlite_master` 에서 `ticker`·`stock_name` 을 **둘 다 가진 것**만
    본다(표 이름을 박으면 새 보드가 조용히 빠진다). 외부 호출 0.

    ⚠️ 읽기 실패를 삼키면 '보드에 없다' 와 '못 읽었다' 가 같은 화면이 된다
    (#82·#143) — 두 번째 값으로 **따로** 돌려주고 호출부가 말한다.
    """
    from trade.badonion_sources import SOURCES

    rows: list[tuple[str, str, str]] = []
    errs: list[str] = []
    for s in SOURCES:
        if s.basis != "company":
            continue
        db = data_dir / s.db_file
        if not db.exists():
            continue
        conn = None
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables:
                cols = {c[1] for c in conn.execute(f"PRAGMA table_info({t})")}
                if not {"ticker", "stock_name"} <= cols:
                    continue
                for r in conn.execute(
                        f"SELECT DISTINCT ticker, stock_name FROM {t}"):
                    tk = str(r["ticker"] or "").strip()
                    if _JP_CODE.match(tk):
                        rows.append((s.key, tk, str(r["stock_name"] or "")))
        except sqlite3.Error as exc:
            errs.append(f"{s.key}({s.db_file}): {type(exc).__name__}: {exc}")
        finally:
            if conn is not None:
                conn.close()
    return sorted(set(rows)), errs


def declaring_markets() -> dict[str, str]:
    """소스 키 → 그 보드가 **선언하는** 상장시장(`local="XX"`). 없으면 미등재.

    열거하지 않는다(#24) — 레지스트리의 각 모듈에서 읽는다. 다만 **소스
    문자열을 훑으면 독스트링·주석의 `local="XX"` 가 대신 만족시킨다**(#59b)
    → AST 로 **실제 호출의 키워드 인자**만 본다.

    선언하는 보드는 확인 없이 링크하므로(#400 `local` 경로) 그 보드의
    (이름, 코드)는 우리가 **이미 화면에 내보내고 있는** 쌍이다. 즉 외부
    원천을 새로 붙이지 않고도 확인이 되는지 여기서 잴 수 있다.
    """
    import importlib

    from trade.badonion_sources import SOURCES

    out: dict[str, str] = {}
    for s in SOURCES:
        try:
            mod = importlib.import_module(s.regenerate.__module__)
            src = Path(mod.__file__ or "").read_text(encoding="utf-8")
        except Exception:                                      # noqa: BLE001
            continue
        got = declared_local(src)
        if got:
            out[s.key] = got
    return out


def declared_local(src: str) -> str | None:
    """모듈 소스에서 `local="XX"` **키워드 인자**를 찾는다(없으면 None).

    순수 함수로 둔 이유 — 판정을 `declaring_markets` 안에 인라인으로 두면
    테스트가 그걸 **다시 구현해** 재게 되고 그건 동어반복이다(#176·#286).
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (kw.arg == "local" and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                    and re.fullmatch(r"[A-Z]{2}", kw.value.value)):
                return kw.value.value
    return None


def yf_names(py: str, tickers: list[str], *, chunk: int = _YF_CHUNK) -> dict:
    """`<코드>.T` 의 longName/shortName — 레포가 JP 이름에 쓰는 그 경로(#35).

    `bot/analyzer.py` JP 분기와 `bot/highlow_render.py` 가 이미 이 필드를
    쓴다. 판정 규칙(어디까지 같으면 같은 회사인가)은 **여기서 정하지
    않는다** — 캡션 이름과 나란히 찍어 사람이 본다(#165·#202).

    ⚠️ 전부를 한 자식에 몰면 마지막에야 출력이 나오고 상한에 걸리면
    **받은 것까지 통째로 버린다**(#103·#116) — 조각으로 나눠 진행을 찍는다.
    """
    code = ("import json,sys,yfinance as yf\n"
            "out={}\n"
            "for t in sys.argv[1:]:\n"
            "    try:\n"
            "        i=yf.Ticker(t+'.T').info or {}\n"
            "        out[t]={'long':i.get('longName'),'short':i.get('shortName')}\n"
            "    except Exception as e:\n"
            "        out[t]={'error':type(e).__name__+': '+str(e)[:120]}\n"
            "print(json.dumps(out,ensure_ascii=False))\n")
    got: dict = {}
    for i in range(0, len(tickers), max(1, chunk)):
        part = tickers[i:i + max(1, chunk)]
        print(f"    … {i + 1}–{i + len(part)}/{len(tickers)} 조회 중", flush=True)
        try:
            r = subprocess.run([py, "-c", code, *part], capture_output=True,
                               text=True, timeout=180)
        except Exception as exc:                               # noqa: BLE001
            for t in part:
                got[t] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if r.returncode != 0:
            msg = (r.stderr or "").strip()[-160:] or f"rc={r.returncode}"
            for t in part:
                got[t] = {"error": msg}
            continue
        parsed = None
        for line in reversed(r.stdout.strip().splitlines()):
            try:
                parsed = json.loads(line)
                break
            except Exception:                                  # noqa: BLE001
                continue
        if parsed is None:
            for t in part:
                got[t] = {"error": "JSON 출력을 못 찾음"}
        else:
            got.update(parsed)
    return got


def _fetch(url: str):
    import requests
    return requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)


def _file_links(html: str, base: str) -> tuple[list[str], int]:
    """(표·CSV 링크, 페이지의 전체 href 수).

    상대경로는 **그 페이지의 origin** 으로 잇는다 — 호스트를 박으면 후보를
    다른 도메인으로 넓히는 순간 조용히 엉뚱한 주소가 된다(#38·#399).

    ⚠️ 링크 0건은 "파일이 없다" 와 "정규식이 이 마크업을 못 읽는다" 가
    구별되지 않는다 — 전체 href 수를 같이 돌려줘 호출부가 가른다(#54·#82).
    """
    from urllib.parse import urljoin

    total = len(re.findall(r"href=", html, re.I))
    seen: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+\.(?:xls|xlsx|csv))["\']',
                           html, re.I):
        u = urljoin(base, href)
        if u not in seen:
            seen.append(u)
    return seen, total


def _sample_rows(path: Path) -> list[str]:
    """엑셀/CSV 의 머리 3행 — 못 읽으면 **그렇다고 말한다**(#54)."""
    suf = path.suffix.lower()
    if suf == ".csv":
        try:
            raw = path.read_bytes()[:4000]
        except OSError as exc:
            return [f"❌ {type(exc).__name__}"]
        # ⚠️ 바이트로 자르면 멀티바이트 문자가 잘려 멀쩡한 CSV 가 '인코딩
        # 판정 불가' 가 된다(리뷰 실측). 그렇다고 `errors="ignore"` 로 풀면
        # **utf-8 이 cp932 바이트를 쓰레기로 '성공'시켜** 다음 후보를 영영
        # 안 시도한다(내 첫 fix 가 그랬다 — 회귀가 잡았다). 증분 디코더는
        # 엄격하게 읽으면서 **꼬리의 미완성 문자만** 보류한다.
        import codecs
        for enc in ("utf-8", "cp932", "shift_jis"):
            try:
                txt = codecs.getincrementaldecoder(enc)().decode(raw, False)
            except (UnicodeDecodeError, LookupError):
                continue
            if txt.strip():
                return txt.splitlines()[:3] + [f"(인코딩 {enc}, 꼬리 절단)"]
        return ["❌ CSV 인코딩 판정 불가"]
    try:
        import pandas as pd
        df = pd.read_excel(path, nrows=2)
        return [f"컬럼: {list(df.columns)}"] + [str(r) for r in
                                                df.to_dict("records")]
    except Exception as exc:                                   # noqa: BLE001
        return [f"❌ 표본 파싱 불가 {type(exc).__name__}: {str(exc)[:160]}",
                "   (pandas/xlrd/openpyxl 가용성은 ① 을 볼 것)"]


def step_seven(cands, jp_boards) -> int:
    """⑦ 로컬 JPX 마스터로 혼합 보드의 도쿄 코드를 **제품 규칙 그대로** 태운다.

    판정을 여기서 다시 짜지 않는다 — 렌더가 부르는 `jp_names` → `lookup_query`
    를 똑같이 부른다(#35 감사는 화면이 쓰는 그 경로 · #169). 잰 것이 있으면 1.
    읽기만 한다: 마스터·실패 곁파일을 열 뿐 쓰지 않는다(#264).
    """
    from trade import jpx_master as jm
    from trade import stock_link as sl

    print("\n⑦ 마스터 대조 — 혼합 보드의 도쿄 코드가 제품 규칙으로 링크되나")
    master, meta = jm.load_envelope()
    if not master:
        why = meta.get("error") or "비어 있음"
        print(f"  ⏭ 마스터 {why} ({jm.PATH}) — 판정 불가(#54)")
        try:
            rec = json.loads(jm.fail_path().read_text(encoding="utf-8"))
            print(f"    마지막 빌드 실패: {rec.get('reason')!r}")
        except OSError:
            # 파일이 **있는데** 못 읽은 것과 아예 없는 것은 처방이 다르다(#82) —
            # 있으면 빌더는 돈 것이고, 다시 돌리면 덮어쓴다.
            if why == "없음":
                print("    빌드 실패 기록도 없다 — 빌더가 아직 안 돌았다.")
            print("    만들려면: cd ~/stock-trade && .venv/bin/python -m "
                  "trade.scripts.build_jpx_codes")
        except Exception as exc:                               # noqa: BLE001
            print(f"    실패 기록을 못 읽음({type(exc).__name__})")
        return 0
    print(f"  마스터 {jm.PATH} · {len(master)}코드 · JPX 기준 "
          f"{meta.get('effective') or '미상'} · 만든 시각 "
          f"{meta.get('built_at') or '미상'} · via {meta.get('via') or '미상'}")
    rows = [(k, t, n) for k, t, n in cands if k not in jp_boards]
    if not rows:
        print("  (JP 선언 보드 밖의 도쿄 코드 모양 행 0건 — 대조할 것 없음)")
        return 1
    names = sl.jp_names(t for _, t, _ in rows)            # 렌더와 같은 호출
    linked = 0
    for key, tk, nm in rows:
        q = sl.lookup_query(tk, nm, jp_master=names)
        listed = master.get(tk.strip().upper())
        if q.endswith(".T"):
            linked += 1
            print(f"  [{key}] {tk} {nm!r} → JPX {listed!r} ✅ ../lookup/{q}")
        elif q:
            print(f"  [{key}] {tk} {nm!r} → 도쿄 대조 전에 다른 규칙이 "
                  f"질의를 만들었다: {q}")
        elif listed:
            print(f"  [{key}] {tk} {nm!r} → JPX {listed!r} ❌ 이름이 안 맞아 평문")
        else:
            print(f"  [{key}] {tk} {nm!r} → JPX 목록에 없는 코드 — 평문")
    print(f"  → 도쿄 링크 {linked} / 대상 {len(rows)}행")
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    print(banner())
    measured = 0

    # ① 환경 ------------------------------------------------------------
    print("\n① 환경")
    here = module_report(sys.executable,
                         ("requests", "yfinance", "pandas", "xlrd", "openpyxl"))
    print(f"  {sys.executable}: {here}")
    if "_error" not in here:            # 제 인터프리터조차 못 물으면 **안 잰 것**이다
        measured += 1

    # ② 후보 인터프리터 --------------------------------------------------
    print("\n② 후보 인터프리터 — 어느 venv 가 마스터를 만들 수 있나")
    yf_py = ""                       # ④ 가 쓸 인터프리터 — 여기서 **한 번만** 고른다
    answered = 0
    for py in candidate_interpreters():
        if not Path(py).exists():
            print(f"  {py}: ❌ 없음")
            continue
        rep = module_report(py, ("requests", "yfinance"))
        print(f"  {py}: {rep}")
        if "_error" in rep:
            continue
        answered += 1
        v = str(rep.get("yfinance", ""))
        if v and not v.startswith("❌") and not yf_py:
            yf_py = py
    if answered:
        measured += 1
    else:
        print("  ❌ 어떤 후보 인터프리터도 답하지 않았다 — 판정 불가(#54)")

    # ③ 보드 실측 --------------------------------------------------------
    data_dir = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
    print(f"\n③ 보드 DB 의 도쿄 코드 모양 ({_JP_CODE.pattern}) · 디렉터리 {data_dir}")
    cands, db_errs = board_jp_candidates(data_dir)
    for e in db_errs:                                          # 삼키지 않는다(#82)
        print(f"  ❌ DB 읽기 실패 {e}")
    if cands:
        for key, tk, nm in cands:                              # 자르지 않는다(#156)
            print(f"  [{key}] {tk}  {nm}")
        print(f"  → 고유 코드 {len({t for _, t, _ in cands})}개 / 행 {len(cands)}건")
        measured += 1
    elif db_errs:
        print("  ❓ 읽기 실패가 있어 '보드에 없다' 고 말할 수 없다(#143)")
    else:
        print("  (해당 모양의 행 0건 — 보드에 아직 없거나 DB 부재)")

    # ③-b 외부 원천 없이 확인되나 ----------------------------------------
    decl = declaring_markets()
    jp_boards = {k for k, v in decl.items() if v == "JP"}
    print(f"\n③-b 시장을 선언하는 보드 {decl} → JP 선언 보드 {sorted(jp_boards) or '없음'}")
    if cands and jp_boards:
        known: dict[str, set[str]] = {}
        for key, tk, nm in cands:
            if key in jp_boards:
                known.setdefault(tk, set()).add(nm)
        for key, tk, nm in cands:
            if key in jp_boards:
                continue
            hit = known.get(tk)
            print(f"  [{key}] {tk} {nm!r} → JP 보드에 "
                  f"{'있음 ' + repr(sorted(hit)) if hit else '없음'}")
        print(f"  → JP 보드가 아는 코드 {len(known)}개")
        measured += 1
    else:
        print("  ⏭ 대조할 재료가 없다 — 판정 불가(#54)")

    # ④ yfinance 대조 ----------------------------------------------------
    print("\n④ yfinance 코드→이름 (캡션 이름과 나란히)")
    tickers = sorted({t for _, t, _ in cands})
    if len(tickers) > _YF_CAP:                                 # 자른 사실을 말한다(#45)
        print(f"  ⚠️ {len(tickers)}개 중 앞 {_YF_CAP}개만 조회한다(상한 {_YF_CAP})")
        tickers = tickers[:_YF_CAP]
    if not tickers:
        print("  ⏭ ③ 이 0건이라 대조할 것이 없다 — 판정 불가(#54)")
    elif not yf_py:
        print("  ❌ yfinance 를 가진 인터프리터가 없다 — ② 를 볼 것")
    else:
        print(f"  (인터프리터 {yf_py} · {len(tickers)}종목)")
        got = yf_names(yf_py, tickers)
        cap = {}
        for _, tk, nm in cands:
            cap.setdefault(tk, nm)
        ok = 0
        for tk in tickers:
            g = got.get(tk) or {}
            if g.get("error") or not g:
                print(f"  {tk}: ❌ {g.get('error', '응답 없음')}   (캡션 {cap.get(tk)!r})")
            else:
                ok += 1
                print(f"  {tk}: long={g.get('long')!r} short={g.get('short')!r}"
                      f"   ← 캡션 {cap.get(tk)!r}")
        print(f"  → 이름을 받은 종목 {ok}/{len(tickers)}")
        if ok:                          # 전부 실패했으면 **안 잰 것**이다(#54)
            measured += 1

    # ⑤ JPX 후보 페이지 --------------------------------------------------
    print("\n⑤ JPX 상장종목 목록 파일 — 후보를 실호출해 링크를 찍는다")
    found: list[tuple[str, str]] = []
    answered_http = 0
    try:
        import requests                                        # noqa: F401
    except Exception as exc:                                   # noqa: BLE001
        print(f"  ❌ requests 없음({type(exc).__name__}) — 이 인터프리터로는 못 잰다")
    else:
        for label, url in _JPX_CANDIDATES:
            try:
                r = _fetch(url)
            except Exception as exc:                           # noqa: BLE001
                print(f"  [{label}] ❌ {type(exc).__name__}: {str(exc)[:140]}")
                continue
            answered_http += 1
            links, hrefs = ([], 0)
            if r.status_code == 200:
                links, hrefs = _file_links(r.text, url)
            print(f"  [{label}] status={r.status_code} bytes={len(r.content)} "
                  f"href={hrefs} 파일링크={len(links)}")
            if r.status_code == 200 and hrefs and not links:
                print("      ⚠️ 링크는 있는데 표·CSV 를 하나도 못 골랐다 — "
                      "파일이 없는 것과 정규식이 못 읽는 것이 갈린다(#54)")
            for u in links:                                    # 자르지 않는다
                print(f"      {u}")
            if label.startswith("대조군"):
                continue
            found.extend((label, u) for u in links)
        if answered_http:
            measured += 1
        else:
            print("  ❌ 후보·대조군 전부 도달 실패 — 경로가 아니라 "
                  "**호스트가 막힌 것**이다(#143). 판정 불가(#54)")

    # ⑥ 표본 -------------------------------------------------------------
    print("\n⑥ 파일 표본 — 이름이 日本語인가 영문인가 (임시 디렉터리, 운영 무변경)")
    if not found:
        print("  ⏭ ⑤ 에서 후보 파일을 못 찾아 표본이 없다 — 판정 불가(#54)")
    else:
        # 후보 **페이지마다** 최대 _SAMPLE_PER_PAGE 개 — 한 페이지가 日/英 을
        # 같이 걸면 하나만 봐서는 "영문 목록이 있나"에 답이 안 난다(#143·#156).
        picked: list[tuple[str, str]] = []
        per: dict[str, int] = {}
        for label, url in found:
            if per.get(label, 0) >= _SAMPLE_PER_PAGE:
                continue
            per[label] = per.get(label, 0) + 1
            picked.append((label, url))
        for label, url in picked:
            print(f"  [{label}] {url}")
            try:
                r = _fetch(url)
            except Exception as exc:                           # noqa: BLE001
                print(f"  ❌ 도달 실패 {type(exc).__name__}: {str(exc)[:160]}")
                continue
            if r.status_code != 200:                           # 파싱 탓으로 돌리지 않는다(#82)
                print(f"  ❌ HTTP {r.status_code} — 받지 못했다(파싱 문제가 아니다)")
                continue
            with tempfile.TemporaryDirectory() as tmp:
                f = Path(tmp) / Path(url).name
                f.write_bytes(r.content)
                print(f"  받음: {len(r.content)}바이트 "
                      f"content-type={r.headers.get('content-type')!r}")
                for line in _sample_rows(f):
                    print(f"      {line}")
            measured += 1

    # ⑦ 마스터 대조(제품 규칙) --------------------------------------------
    measured += step_seven(cands, jp_boards)

    print(f"\n■ 쟀다: {measured}단계. "
          "못 잰 것은 위에 ❌/⏭/❓ 로 적혀 있다 — 그 자리는 판정 불가다(#54·#274).")
    return 0 if measured else 1


if __name__ == "__main__":
    sys.exit(main())
