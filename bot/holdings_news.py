"""🗞️ 보유종목 News — 자산 대시보드 보유 종목별 하루 뉴스 + Pros/Cons (사용자 2026-10-12).

무엇을 하나
- 종목: 자산 대시보드와 **같은 기록**(`bot.portfolio.load()` = portfolio.json)의 보유 종목. 증권사가
  달라도 티커가 같으면 한 종목으로 합치고 평가금액을 더한다(화면 정렬 = 평가금액 큰 순). 티커를
  못 찾은 상품·ETF(국내 = 자산 기록 판정 · 미국 = 거래소 목록의 ETF 칸) 는 뉴스로 가를 '회사' 가 아니라
  빼고, 몇 개를 왜 뺐는지 화면이 적는다(#43·#45).
- 뉴스(매일 `RUN_HOUR`:`RUN_MIN` KST, 직전 집계 `WINDOW_OVERLAP_H` 시간 전 ~ 지금 · 상한 `MAX_WINDOW_H`
  시간 — 겹친 구간의 이미 실린 항목은 뺀다):
  · 한국: 네이버 검색 API(`naver_news_client`, 키 없으면 구글 뉴스) — 이름 검색이라 제목·요약에
    그 이름이 실제로 나온 기사만 남긴다. 영문 이름(NAVER·HYBE)은 DART 한글 이름으로 찾고(`with_ko`) 영문
    이름으로도 한 번 더 찾는다(같은 원천 — 음역 한글명 '케이티' 로는 'KT' 기사를 놓친다).
    네이버가 호스팅한 기사(n.news.naver.com)는 본문 앞부분을 읽어 AI 에 같이 넘긴다(종목당
    `MAX_BODY_PER_STOCK` · 실행당 `MAX_BODY_PER_RUN` · 화면엔 싣지 않는다 — 판단 재료일 뿐 재게시가 아니다).
  · 해외: Yahoo Finance 종목 뉴스(yfinance) — 못 받거나 0건이면 구글 뉴스(시장 locale · 검색어가 한글이면
    한국어 locale).
  · 텔레그램: 'Noah의 RAG' 채널(`TG_RAG_CHANNEL`)에 올라온 글 — `bot.portfolio_watch` 가 2분마다
    이미 그 채널을 읽고 있으므로 거기서 텍스트를 `TG_INBOX` 에 남기게 했다(추가 텔레그램 요청 0,
    해석성 요청 0 — #258). 이 모듈은 그 파일에서 종목 이름·코드가 **낱말로** 나온 글만 고른다.
- Pros/Cons: 종목마다 모은 항목을 Gemini flash-lite 에 묶어 보내 **그 항목에 쓰인 사실만** 근거로
  호재 관점(Pros)·악재·리스크 관점(Cons)을 최대 3줄씩 받는다. 줄마다 근거 번호가 붙고, 근거 번호가
  없거나 틀린 줄은 버린다(데이터 vs 환각 — 지어낸 문장을 싣지 않는다). 그 회사 얘기가 아닌 항목은
  AI 가 relevant 에서 빼고, 화면은 뺀 항목도 따로 보여 준다(#43). 비용은 usage.jsonl 에
  subsystem `holdings_news` 로 남겨 대시보드 비용 카드·/usage 가 합산한다.

저장: ~/.tradingagents/holdings_news/ — days/YYYY-MM-DD.json(하루 기록) · days/bodies_YYYY-MM-DD.json(아직
정리 안 된 종목의 본문 — AI 재료, 주기가 이어서 정리하는 날에만 · 화면엔 안 실림) · state.json · tg_inbox.jsonl.
진단: `python -m bot.holdings_news --why`(읽기 전용 · 네트워크·LLM 0) · `--probe [티커…]`(원천만
받아 보기 — LLM 0 · 이 보드의 기록은 안 쓴다, 뉴스 클라이언트 공용 캐시는 씀) · `--run`(지금 한 주기 —
LLM 비용이 든다).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.holdings_news")

_KST = timezone(timedelta(hours=9))
_DIR = Path.home() / ".tradingagents" / "holdings_news"
_DAYS = _DIR / "days"
_STATE = _DIR / "state.json"
TG_INBOX = _DIR / "tg_inbox.jsonl"
_TG_LOCK = _DIR / ".tg_inbox.lock"
_RUN_LOCK = _DIR / ".run.lock"

TICK_MIN = 15                      # 봇이 깨어 확인하는 주기(분) — telegram_bot 주기가 이 값이다
RUN_HOUR, RUN_MIN = 8, 0           # 매일 이 시각(KST) 이후 하루치를 모은다 — 장 시작 전에 읽게
DEFAULT_WINDOW_H = 24              # 직전 기록이 없을 때 거슬러 보는 시간
MAX_WINDOW_H = 72                  # 봇이 며칠 멈췄어도 이보다 오래된 뉴스는 '오늘 뉴스' 로 싣지 않는다
WINDOW_OVERLAP_H = 6               # 다음 창은 직전 창 끝보다 이만큼 앞에서 연다 — 입력이 늦게 닿는다(수신함은 2분
                                   # 폴링·세션 공유로 밀리고 뉴스 캐시는 몇 시간 묵는다). 겹친 항목은 앞선 기록과
                                   # 대조해 뺀다(`_seen_keys`).
RENDER_DAYS = 30                   # 화면에 싣는 날 수(그 앞은 보관만 — 화면이 그렇게 말한다)
FRAG_PREFIX = "hn_d_"              # 지난 날 조각 파일 접두(ARCHIVE_ROOT/hn_d_YYYY-MM-DD.html)
KEEP_DAYS = 120                    # 하루 기록 보관
TG_KEEP_DAYS = 14                  # 텔레그램 수신함 보관
TG_TEXT_CAP = 4000                 # 수신함에 남기는 글자 상한(한 메시지)
MAX_NEWS_PER_STOCK = 4
MAX_TG_PER_STOCK = 4
MAX_BODY_PER_STOCK = 2
MAX_BODY_PER_RUN = 200
SNIPPET_CHARS = 420                # AI 에 넘기는 항목 하나의 글자 상한(본문 빼고 — 텔레그램 발췌도 이 길이)
BODY_CHARS = 900                   # 항목에 덧붙이는 본문 앞부분 상한(위 상한과 따로 — 줄 상한에 잘리지 않게)
BODY_MIN = 80                      # 이보다 짧게 뽑히면 본문으로 세지 않는다(사진 설명만·화면 구조 변경)
FOOT_MIN = 200                     # 저작권 꼬리는 이 글자 뒤에서만 자른다 — 위쪽 사진 출처(ⓒ 뉴스1)에서 안 자르게
BATCH_CHARS = 12000                # 한 번의 AI 호출에 묶는 글자 상한(본문이 다 실려도 묶음 수가 늘지 않게)
BATCH_MAX = 10                     # 한 번의 AI 호출에 묶는 종목 상한
MAX_LLM_CALLS = 40                 # 한 주기에 부르는 AI 상한(남으면 다음 주기 — 화면이 '대기' 로 적는다)
MAX_ANALYZE_TRIES = 3              # 같은 종목 분석 실패 상한(넘으면 그날은 뉴스만 보여 준다)
MAX_PER_SIDE = 3                   # Pros·Cons 각각 줄 상한
LINE_CHARS = 90                    # 한 줄 글자 상한(넘으면 자른다 — 프롬프트는 70자를 요구)
TICK_TIMEOUT_SEC = 1800            # 봇 주기 태스크의 기다림 상한
STALE_TICK_MIN = 45                # 집계 시각이 지났는데 마지막 주기가 이보다 오래면 '봇 주기 멈춤'
BEAT_SEC = 60                      # 긴 수집 중 '주기가 살아 있다' 를 남기는 간격(아래 판정이 그걸 본다)
ALT_PACE = 0.25                    # 같은 종목을 구글에 두 번째로 물을 때 사이 쉬는 초(`alt_query`)
SRC_FAIL_SHARE = 0.25              # 원천을 못 받은 종목이 보유의 이 비율 이상이면 장애(❌)
SRC_MARKET_MIN = 3                 # 한 시장 종목이 이 수 이상인데 전부 실패·전부 0건이면 장애(❌)
_MODEL = "gemini-2.5-flash-lite"   # 주어진 문장에서 근거를 골라 요약하는 일 — 판단 티어가 아니다
SUBSYSTEM = "holdings_news"        # usage.jsonl subsystem 태그(비용 카드·/usage 버킷)
_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")}


def _now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(_KST)
    return now if now.tzinfo else now.replace(tzinfo=_KST)


def _parse(s) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=_KST)


def _exc_msg(exc: BaseException, n: int) -> str:
    """예외 문구 앞 `n` 자 — 비밀값을 **자르기 전에** 가린다. 자른 뒤 가리면 경계에 걸린 키 조각이 남는다
    (#350 · 리뷰 2026-10-13 실측 'AIzaSyQQQQ'). 사유는 화면·기록·감사 줄에 실린다(§Secrets)."""
    try:
        from bot.env_keys import redact
        return redact(str(exc))[:n]
    except Exception:                                          # noqa: BLE001
        return type(exc).__name__


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _flock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ── 텔레그램 수신함 (쓰는 쪽 = bot.portfolio_watch · 읽는 쪽 = 이 모듈) ──────────
def tg_record(msg, chan: str) -> dict | None:
    """Telethon 메시지 → 수신함 한 줄. 글이 없으면(사진만·파일만) None.

    원 출처 채널 이름은 **묻지 않는다** — 포워드 출처를 풀려면 엔티티 해석 요청이 들고, 그 요청은
    계정 단위로 강하게 제한된다(#258 FloodWait 8073초). 포워드였는지만 남긴다."""
    text = (getattr(msg, "message", None) or "").strip()
    if not text:
        return None
    dt = getattr(msg, "date", None)
    try:
        ts = dt.astimezone(_KST) if dt else datetime.now(_KST)
    except Exception:                                          # noqa: BLE001
        ts = datetime.now(_KST)
    return {"id": int(getattr(msg, "id", 0) or 0), "ts": ts.isoformat(timespec="seconds"),
            "chan": str(chan), "fwd": getattr(msg, "fwd_from", None) is not None,
            "text": text[:TG_TEXT_CAP]}


def tg_append(recs: list[dict]) -> int:
    """수신함에 덧붙인다(파일 잠금 — 정리(prune)가 같은 파일을 다시 쓰므로, #379)."""
    recs = [r for r in recs if r]
    if not recs:
        return 0
    with _flock(_TG_LOCK):
        with open(TG_INBOX, "a", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(recs)


def _tg_read() -> tuple[list[dict], int]:
    """(수신함 전부 — (채널, id) 중복 제거 · 시각순, 못 읽은 줄 수)."""
    out: dict = {}
    bad = 0
    try:
        with open(TG_INBOX, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    if _parse(r["ts"]) is None:
                        raise ValueError("ts")
                    out[(str(r.get("chan")), int(r["id"]))] = r
                except Exception:                              # noqa: BLE001
                    bad += 1
    except FileNotFoundError:
        return [], 0
    except OSError as exc:
        log.warning("holdings_news: 텔레그램 수신함 읽기 실패: %s", exc)
        return [], 0
    return sorted(out.values(), key=lambda r: r["ts"]), bad


def tg_prune(now: datetime | None = None) -> int:
    """`TG_KEEP_DAYS` 보다 오래된 줄을 걷어낸다(잠금 안에서 읽고 바꿔 쓴다). 걷어낸 수."""
    cut = _now(now) - timedelta(days=TG_KEEP_DAYS)
    with _flock(_TG_LOCK):
        recs, _bad = _tg_read()
        keep = [r for r in recs if (_parse(r["ts"]) or cut) >= cut]
        if len(keep) == len(recs) and not _bad:
            return 0
        _atomic_write(TG_INBOX, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep))
        return len(recs) - len(keep)


# ── 종목 ─────────────────────────────────────────────────────────────────────
_PREF_RE = re.compile(r"^(.{2,}?)(?:\d?우[A-C]?|우선주)$")
_ETF_SOURCES = ("etf_meta", "etf_brand")


def _code(ticker: str, market: str) -> str:
    t = str(ticker or "").strip().upper()
    if market == "KR":
        head = t.split(".")[0]
        return head if re.fullmatch(r"\d{6}|[0-9A-Z]{6}", head) else t
    return t


def universe(model: dict | None = None, *, us_etf: set | None = None) -> tuple[list[dict], dict]:
    """보유 종목(티커 단위 · 평가금액 합 · 큰 순) + 뺀 것({unmatched, etf} 이름 목록).

    자산 대시보드와 같은 기록을 읽는다(#38) — 여기서 따로 정한 목록이면 두 화면이 다른 종목을 센다.
    ETF 판정: 국내는 자산 기록이 ETF 로 알아본 상품(etf_meta·etf_brand), 미국은 `us_etf`(거래소 목록의
    ETF 칸 — `bot.us_symbol_names.us_etf_symbols`). 자산 기록은 미국 ETF 도 alias·네이버·야후로 풀어
    '미국 종목' 으로만 남긴다(리뷰 2026-10-12: AIRR·DRAM 이 회사로 셌다). ⚠️ 그 밖 시장(일본·홍콩·대만)
    ETF 는 가를 목록이 없어 회사로 센다(못 보는 축)."""
    if model is None:
        try:
            from bot.portfolio import load
            model = load() or {}
        except Exception as exc:                               # noqa: BLE001
            log.warning("holdings_news: portfolio 읽기 실패: %s", exc)
            model = {}
    out: dict[str, dict] = {}
    unmatched, etf = set(), set()
    for h in model.get("holdings") or []:
        name = str(h.get("상품명") or "").strip()
        tk, mk = h.get("ticker"), str(h.get("market") or "").upper()
        # ETF 판정이 먼저 — 브랜드로만 알아본 ETF(etf_brand)는 티커가 없어도 ETF 다. 티커 미확인으로
        # 세면 화면이 그 상품을 '못 찾은 상품' 이라 적는다(#34).
        if h.get("source") in _ETF_SOURCES:
            if name or tk:
                etf.add(name or str(tk))
            continue
        if not tk or not h.get("matched") or not mk:
            if name:
                unmatched.add(name)
            continue
        code = _code(tk, mk)
        if mk == "US" and code in (us_etf or ()):
            etf.add(name or code)
            continue
        key = f"{mk}:{code}"
        s = out.setdefault(key, {"key": key, "market": mk, "ticker": code, "name": name or code,
                                 "sym": str(tk).strip(), "value": 0.0})
        try:
            s["value"] += float(h.get("평가금액") or 0)
        except (TypeError, ValueError):
            pass
    stocks = sorted(out.values(), key=lambda s: (-s["value"], s["key"]))
    return stocks, {"unmatched": sorted(unmatched), "etf": sorted(etf),
                    "as_of": _as_of(model), "us_etf_n": None if us_etf is None else len(us_etf)}


def _as_of(model: dict) -> str | None:
    """자산 기록의 기준 — 뱅크샐러드 파서는 `as_of` 를 채우지 않아 늘 비어 있었다(2026-10-13 VM `--why`
    '자산 기록 기준 —'). 자산 대시보드의 '마지막 업데이트' 와 같은 `_saved_ts`(업로드 저장 시각)를 KST 로 쓴다(#38)."""
    if model.get("as_of"):
        return str(model["as_of"])
    try:
        ts = float(model.get("_saved_ts"))
        return "업로드 " + datetime.fromtimestamp(ts, _KST).strftime("%Y-%m-%d %H:%M KST")
    except (TypeError, ValueError, OverflowError, OSError):   # 깨진 값이 tick 전체를 멈추지 않게(#424)
        return None


def _us_etf(*, cache_only: bool = False) -> set:
    try:
        from bot.us_symbol_names import us_etf_symbols
        return us_etf_symbols(cache_only=cache_only)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: 미국 ETF 목록 조회 실패: %s", exc)
        return set()


def search_name(stock: dict) -> str:
    """뉴스 검색어 — 법인 꼬리('(주)')를 떼고, 우선주는 본주 이름으로(뉴스는 본주 이름으로 쓴다). 국내
    종목 이름에 한글이 없으면('NAVER'·'HYBE') DART 한글 이름(`ko`, `with_ko`)이 먼저다(종목분석 화면과 같은
    처방, 2026-06-08 NAVER 0-news). 영문 이름도 같이 찾는다 — DART 한글명이 음역('케이티'·'에이치엠엠')인
    회사는 기사가 영문('KT'·'HMM')으로 쓴다(`fetch_stock_news`, 리뷰 2026-10-13)."""
    from bot.market import kr_news_query_name
    kr = stock.get("market") == "KR"
    nm = ((stock.get("ko") if kr else "") or kr_news_query_name(stock.get("name"))
          or str(stock.get("name") or ""))
    m = _PREF_RE.match(nm) if kr else None
    return m.group(1) if m else nm


def _dart_ko(code: str) -> str:
    """국내 종목코드 → DART 한글 회사명('네이버'). 못 얻으면 ''(키 없음·원천 실패 — 부른 쪽이 센다)."""
    try:
        from bot.dart_client import get_dart
        from bot.market import has_hangul
        ko = get_dart().news_search_name(code)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: DART 한글명 조회 실패 %s: %s", code, exc)
        return ""
    return ko if ko and has_hangul(ko) else ""


def with_ko(stock: dict, ko=None) -> dict:
    """국내 종목인데 이름에 한글이 없으면 DART 한글 이름을 `ko` 로 붙인 사본(조회 실패면 ''). 이미
    한글이거나 해외면 그대로 — 조회 0. 수집과 `--probe` 가 같이 쓴다(#145 프로브는 인자도 제품과 같게)."""
    from bot.market import has_hangul
    if stock.get("market") != "KR" or "ko" in stock or has_hangul(str(stock.get("name") or "")):
        return stock
    return {**stock, "ko": (ko or _dart_ko)(str(stock.get("ticker") or ""))}


def aliases(stock: dict) -> list[tuple[str, str]]:
    """글에서 이 종목을 가리키는 낱말들 — [(낱말, 종류)] · 종류 name(이름·본주 이름) / code(국내 6자리)
    / ticker(해외 티커). 종류를 같이 넘기는 이유: 두 글자 영문은 **티커**일 때만 `$` 를 요구한다 —
    이름이면 KT·LS·CJ 같은 실제 국내 회사가 뉴스를 전부 잃는다. 영문 **이름**은 대문자·첫 글자 대문자
    꼴도 같이 찾는다('S-Oil' ↔ 'S-OIL', 'NAVER' ↔ 'Naver' — 리뷰 2026-10-12) · 티커는 그대로만."""
    from bot.market import kr_news_query_name
    nm = kr_news_query_name(stock.get("name")) or str(stock.get("name") or "")
    names: list[str] = []
    for a in (nm, search_name(stock)):
        if not a.isascii():
            names.append(a)
            continue
        # 첫 글자 대문자 꼴은 네 글자 이상만 — 'HMM' → 'Hmm' 은 영어 감탄사라 텔레그램 잡담이 그 회사로
        # 잡힌다(대소문자 그대로 비교라 짧은 꼴일수록 일반 낱말과 겹친다, 리뷰 2026-10-13).
        names += [a, a.upper()] + ([a.title()] if len(a) >= 4 else [])
    out = [(a, "name") for a in dict.fromkeys(names) if len(a) >= 2]
    tk = str(stock.get("ticker") or "")
    if stock.get("market") == "KR":
        if re.fullmatch(r"\d{6}", tk):
            out.append((tk, "code"))
    elif tk:
        out.append((tk.split(".")[0] if re.fullmatch(r"[A-Z0-9]+\.[A-Z]{1,3}", tk) else tk, "ticker"))
    return out


# 한국어 조사·꼬리 — 이름 바로 뒤에 붙어도 그 이름을 가리킨다. 붙은 뒤엔 낱말이 끝나야 한다
# ('두산에너빌리티' 의 '두산' 은 '에' 다음에 '너' 가 이어지므로 '두산' 이 아니다).
_PARTICLES = sorted({
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "로", "으로", "에서", "에게",
    "만", "까지", "부터", "처럼", "보다", "이나", "나", "랑", "이랑", "하고", "에는", "에도", "에서는",
    "으로는", "까지는", "부터는", "과의", "와의", "에서의", "으로의", "이란", "이라는", "라는",
    "이며", "이고", "주가", "측", "株", "社",
}, key=len, reverse=True)


def _word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def find_mentions(text: str, alias: str, kind: str = "name") -> list[tuple[int, int]]:
    """`text` 에서 `alias` 가 **낱말로** 나온 자리들.

    - 앞: 글 처음이거나 낱말 글자가 아니어야 한다('외인삼성전자' 같은 붙여 쓰기는 놓친다 — 넓히면
      'LG' 가 'LG에너지솔루션' 에 걸린다).
    - 뒤: 글 끝·낱말 아닌 글자(영문이면 '&' 도 낱말로 본다 — 'KT' 가 'KT&G' 에 걸리지 않게), 또는
      조사·꼬리(한글 조사 · '株'·'社') 하나 뒤에 낱말이 끝나야 한다('KT는'·'삼성전자가' 는 걸리고
      '두산에너빌리티' 의 '두산' 은 '에' 뒤에 '너' 가 이어져 안 걸린다).
    - 영문은 대문자 그대로만, 두 글자 이하 **티커**는 `$` 가 붙은 것만(ON·IT·AI 같은 낱말에 걸리지 않게).
    - 한글·혼합 이름은 대소문자를 가리지 않는다('SK하이닉스' ↔ 'Sk하이닉스')."""
    if not text or not alias:
        return []
    is_ascii = alias.isascii()
    short_ticker = kind == "ticker" and len(alias) <= 2
    hay, needle = (text, alias) if is_ascii else (text.lower(), alias.lower())
    out, i = [], 0
    while True:
        i = hay.find(needle, i)
        if i < 0:
            return out
        j = i + len(needle)
        prev = text[i - 1] if i > 0 else ""
        left_ok = (not prev or not (_word(prev) or (is_ascii and prev == "&"))) and \
            (not short_ticker or prev == "$")
        nxt = text[j] if j < len(text) else ""
        right_ok = not nxt or not (_word(nxt) or (is_ascii and nxt == "&"))
        if not right_ok:          # 다음 글자가 한글일 때만 보면 '株'·'社' 꼬리가 죽는다(리뷰 2026-10-12)
            for p in _PARTICLES:
                if text.startswith(p, j):
                    k = j + len(p)
                    if k >= len(text) or not _word(text[k]):
                        right_ok = True
                        break
        if left_ok and right_ok:
            out.append((i, j))
        i = j


def mentioned(text: str, als) -> list[tuple[int, int]]:
    """`als` = aliases() 결과([(낱말, 종류)]) 또는 낱말 목록."""
    spans: list[tuple[int, int]] = []
    for a in als:
        word, kind = a if isinstance(a, tuple) else (a, "name")
        spans.extend(find_mentions(text, word, kind))
    return sorted(spans)


def excerpt(text: str, span: tuple[int, int], cap: int = SNIPPET_CHARS) -> str:
    """언급이 있는 줄(짧으면 앞뒤 줄까지)에서 그 언급을 **가운데** 두고 `cap` 자 — 여러 종목을 늘어놓은 글에서
    그 종목 대목만 AI 에 넘긴다. 앞에서부터 자르면 긴 머리말 뒤에 나온 종목이 통째로 잘린다(리뷰 2026-10-12
    실측: 800자 뒤 언급 → 이름도 내용도 없는 발췌). 잘린 쪽엔 '…'."""
    lines = text.split("\n")
    pos, idx = 0, 0
    for n, ln in enumerate(lines):
        if pos + len(ln) >= span[0]:
            idx = n
            break
        pos += len(ln) + 1
    lo, hi = idx, idx + 1
    while len(" ".join(lines[lo:hi])) < 160 and (lo > 0 or hi < len(lines)):
        lo, hi = max(0, lo - 1), min(len(lines), hi + 1)
    raw = "\n".join(lines[lo:hi])
    s = " ".join(raw.split())
    if len(s) <= cap:
        return s
    head = raw[:max(0, span[0] - sum(len(ln) + 1 for ln in lines[:lo]))]
    at = len(" ".join(head.split())) + (1 if head.strip() and head[-1].isspace() else 0)
    a = max(0, min(at - cap // 3, len(s) - cap))           # 언급 앞 1/3 · 뒤 2/3(내용은 대개 이름 뒤에 온다)
    out = s[a:a + cap]
    if a > 0:
        out = "…" + out[1:]
    if a + cap < len(s):
        out = out[:-1] + "…"
    return out


# ── 원천 ─────────────────────────────────────────────────────────────────────
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _item_ts(it: dict) -> datetime | None:
    """항목 시각 — 원천이 시각을 주면 그것, 날짜만 주면 그날 00:00 KST(창 판정은 날짜로 넉넉히 —
    다음 날 겹침은 지난 기록과의 중복 제거가 막는다)."""
    d = _parse(it.get("ts")) if it.get("ts") else None
    if d:
        return d.astimezone(_KST)
    ds = str(it.get("date") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ds):
        return datetime.fromisoformat(ds).replace(tzinfo=_KST)
    return None


def _in_window(it: dict, since: datetime, until: datetime) -> bool:
    d = _item_ts(it)
    if d is None:
        return False
    if not it.get("ts"):
        return since.date() <= d.date() <= until.date()
    return since <= d < until


def _yf_item(a) -> dict | None:
    """yfinance 뉴스 한 건 → 공용 모양. 옛 모양(최상위 title·providerPublishTime)과 새 모양
    (content{title·summary·pubDate·provider·canonicalUrl}) 둘 다 받는다 — 샌드박스에선 원천을 못 재
    어느 쪽인지 단정하지 않는다(#165, `--probe` 가 잰다)."""
    if not isinstance(a, dict):
        return None
    c = a.get("content") if isinstance(a.get("content"), dict) else a
    title = str(c.get("title") or "").strip()
    if not title:
        return None
    link = ""
    for k in ("canonicalUrl", "clickThroughUrl"):
        v = c.get(k)
        if isinstance(v, dict) and v.get("url"):
            link = str(v["url"])
            break
    link = link or str(c.get("link") or a.get("link") or "")
    ts = ""
    pub = c.get("pubDate") or c.get("displayTime") or c.get("providerPublishTime")
    if isinstance(pub, (int, float)):
        ts = datetime.fromtimestamp(pub, tz=timezone.utc).isoformat()
    elif pub:
        d = _parse(str(pub).replace("Z", "+00:00"))
        ts = d.isoformat() if d else ""
    prov = c.get("provider")
    src = (prov.get("displayName") if isinstance(prov, dict) else "") or c.get("publisher") or ""
    return {"title": title, "summary": str(c.get("summary") or c.get("description") or "")[:600],
            "link": link, "source": str(src), "ts": ts,
            "date": ts[:10] if ts else ""}


def _fetch_yf(ticker: str) -> tuple[list | None, str]:
    from bot.finviz_client import yf_paused
    if yf_paused():
        return None, "yfinance 일시정지(YF_PAUSE)"
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).get_news(count=10) or []
    except Exception as exc:                                   # noqa: BLE001
        return None, f"yfinance 실패({type(exc).__name__}: {_exc_msg(exc, 60)})"
    items = [x for x in (_yf_item(a) for a in raw) if x]
    if raw and not items:
        return None, f"yfinance 응답 {len(raw)}건을 하나도 못 읽음(형식 변경?)"
    return items, ""


def _fetch_google(query: str, market: str) -> tuple[list | None, str]:
    try:
        from bot.google_news_client import fetch_news, locale_for_market
        hl, gl, ceid = locale_for_market(market)
        # 종목분석 화면(stock_snapshot)과 같은 인자 — 캐시 키가 (검색어, 언어, 날짜)라 인자를 줄이면
        # 그 화면이 4시간 동안 짧은 목록을 받는다(#38).
        items = fetch_news(query, days_back=28, max_items=10, lang=hl, country=gl, ceid=ceid,
                           empty_ok=True)
    except Exception as exc:                                   # noqa: BLE001
        return None, f"구글 뉴스 실패({type(exc).__name__})"
    if items is None:
        # empty_ok — 0건은 [] 로 온다. None 은 받지 못한 것(로그에 사유) 또는 빈 검색어다.
        return None, "구글 뉴스 실패(로그 참조)"
    return items, ""


def _fetch_naver(query: str) -> tuple[list | None, str]:
    from bot.env_keys import env_key
    if not (env_key("NAVER_CLIENT_ID") and env_key("NAVER_CLIENT_SECRET")):
        return None, "네이버 검색 키(NAVER_CLIENT_ID/SECRET) 없음"
    try:
        from bot.naver_news_client import fetch_news
        items = fetch_news(query, days_back=28, max_items=10)   # 종목분석과 같은 인자(캐시 공유, #38)
    except Exception as exc:                                   # noqa: BLE001
        return None, f"네이버 검색 실패({type(exc).__name__})"
    if items is None:
        return None, "네이버 검색 실패(로그 참조)"
    return items, ""


def alt_query(stock: dict) -> str:
    """DART 한글명으로 찾는 국내 종목의 두 번째 검색어(영문 이름) — 같은 검색어면 ''(두 번 묻지 않는다). 해외
    종목은 `search_name` 이 `ko` 를 안 쓰므로 두 검색어가 같아 저절로 ''."""
    if not stock.get("ko"):
        return ""
    alt = search_name({k: v for k, v in stock.items() if k != "ko"})
    return alt if alt and alt != search_name(stock) else ""


def fetch_stock_news(stock: dict) -> tuple[list[dict], str, str]:
    """(항목들, 실제로 답한 원천 이름, 실패·대체 사유) — 원천이 0건이면 ([], 원천, '').

    해외는 yfinance 가 **0건이어도** 구글 뉴스로 한 번 더 묻는다 — yfinance(1.7)는 깨진 응답을 예외 대신 빈
    목록으로 돌려준다(`hide_exceptions`, 리뷰 2026-10-12). 둘 다 0건이면 yfinance 의 답(0건)으로 둔다(그날
    뉴스가 정말 없을 수 있다 — 시장 전부가 0건이면 판정이 따로 잡는다, `_outage`)."""
    from bot.market import has_hangul
    mk = stock.get("market")
    q = search_name(stock)
    if mk == "KR":
        items, why = _fetch_naver(q)
        if items is not None:
            items, used, why = items, "naver", ""
        else:
            g, gwhy = _fetch_google(q, "KR")
            if g is None:
                return [], "", f"{why} · {gwhy}"
            items, used = g, "google"
        alt = alt_query(stock)
        if alt:
            # DART 한글명으로 찾은 종목은 영문 이름으로도 한 번 더(같은 원천) — '케이티' 로만 찾으면 'KT'
            # 기사를 거의 다 놓친다. 합친 뒤 최근 순(수집이 종목당 상한에서 끊는다).
            if used == "google":
                time.sleep(ALT_PACE)                 # 구글은 몰아 치면 막힌다(수집의 `pace` 와 같은 이유)
            more, awhy = _fetch_naver(alt) if used == "naver" else _fetch_google(alt, "KR")
            if more is None:
                # 1순위는 받았으니 그 종목을 실패로 세지 않되 조용히 넘기지도 않는다(#43).
                log.warning("holdings_news: %s 영문 이름 %r 검색 실패: %s", stock.get("key"), alt, awhy)
            if more:
                links = {x.get("link") for x in items if x.get("link")}
                items = items + [x for x in more if not x.get("link") or x.get("link") not in links]
                items.sort(key=lambda x: (_item_ts(x) or _EPOCH).timestamp(), reverse=True)
        return items, used, why
    tk = str(stock.get("ticker") or "")
    items, why = _fetch_yf(tk)
    if items:
        return items, "yfinance", ""
    # 해외 비미국 종목의 검색어는 자산 기록의 한글 이름이다 — 그 시장 언어로 물으면 늘 0건이라 한국어로
    # 묻는다(한국 투자자가 보는 기사가 그쪽이다, 리뷰 2026-10-13).
    gq = f"{tk.split('.')[0]} stock" if mk == "US" else (q or tk)
    g, gwhy = _fetch_google(gq, "KR" if mk != "US" and has_hangul(gq) else (mk or "US"))
    if items is not None:
        return (g, "google", "yfinance 0건") if g else ([], "yfinance", "")
    return (g, "google", why) if g is not None else ([], "", f"{why} · {gwhy}")


_DIC_ARTICLE = re.compile(r"<article[^>]*\bid=[\"']dic_area[\"'][^>]*>(.*?)</article>", re.S | re.I)
_DIC_START = re.compile(r"\bid=[\"']dic_area[\"'][^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_FOOT = re.compile(r"(Copyright|ⓒ|©|무단\s*전재|저작권자)")


def extract_naver_body(html: str) -> str:
    """네이버 뉴스 기사 화면 → 본문 앞부분. 못 찾거나 `BODY_MIN` 보다 짧으면 ''. 저작권 꼬리(ⓒ·Copyright·
    무단 전재)는 `FOOT_MIN` 글자 뒤에서만 자른다 — 맨 위 사진 출처('ⓒ 뉴스1 DB')에서 자르면 본문이 사진
    설명 한 줄로 줄어든 채 '읽음' 으로 세졌다(리뷰 2026-10-12 합성 화면 실측). ⚠️ `dic_area` 는 우리가 재지
    않은 화면 구조다 — 바뀌면 조용히 '' 가 되므로 실행이 '본문 읽음 n/m' 을 세어 화면·`--probe` 가 말한다(#165)."""
    if not html:
        return ""
    m = _DIC_ARTICLE.search(html)
    if m:
        raw = m.group(1)
    else:
        s = _DIC_START.search(html)
        if not s:
            return ""
        raw = html[s.end():s.end() + 20000]
    import html as _h
    txt = " ".join(_h.unescape(_TAG.sub(" ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw,
                                                    flags=re.S | re.I))).split())
    for f in _FOOT.finditer(txt):
        if f.start() >= FOOT_MIN:
            txt = txt[:f.start()].rstrip()
            break
    txt = txt[:BODY_CHARS]
    return txt if len(txt) >= BODY_MIN else ""


def _fetch_body(link: str) -> str:
    from urllib.parse import urlparse
    host = (urlparse(link).netloc or "").lower()
    if not host.endswith("news.naver.com"):
        return ""
    from bot.finviz_client import naver_paused
    if naver_paused():
        return ""
    try:
        import requests
        r = requests.get(link, headers=_UA, timeout=8)
        if r.status_code != 200:
            return ""
        return extract_naver_body(r.text)
    except Exception:                                          # noqa: BLE001
        return ""


def _norm_title(t: str) -> str:
    return re.sub(r"[\W_]+", "", str(t or "").lower())


def _seen_keys(records: list[dict], date: str) -> set:
    """이 날보다 앞선 기록 둘에 이미 실린 항목 — 날짜만 주는 원천의 창 겹침이 같은 기사를 이틀
    연속 싣지 않게."""
    out = set()
    prev = sorted((r for r in records if r.get("date", "") < date), key=lambda r: r["date"])[-2:]
    for r in prev:
        for s in r.get("stocks") or []:
            for it in s.get("items") or []:
                out.add((s.get("key"), it.get("k")))
    return out


def _item_key(it: dict) -> str:
    if it.get("src") == "telegram":
        return f"tg:{it.get('chan')}:{it.get('id')}"
    return it.get("link") or ("t:" + _norm_title(it.get("title")))


def collect(now: datetime | None = None, *, model: dict | None = None,
            records: list[dict] | None = None, fetch=fetch_stock_news,
            body=_fetch_body, pace: float = 0.25, us_etf: set | None = None, ko=None,
            beat=None) -> dict | None:
    """하루 기록 하나를 만든다(분석 전 — 종목마다 status 'pending' 또는 'empty'). 보유 종목이 없으면
    None(쓸 것이 없다). 저장은 부른 쪽이. `pace` = 원천 요청 사이 쉬는 초(구글 뉴스는 데이터센터 IP
    에서 몰아 치면 막힌다 — 네이버 키가 없으면 국내 전부가 구글로 간다). `us_etf`·`ko` = 미국 ETF 목록·
    DART 한글명 조회(테스트·진단이 갈아 끼운다 — 없으면 실제로 묻는다). `beat` = 종목마다 부르는 '아직 도는 중'
    표시(긴 수집이 `STALE_TICK_MIN` 을 넘어도 판정이 '봇 주기 멈춤' 으로 읽지 않게, 리뷰 2026-10-13).

    창: 직전 기록의 끝보다 `WINDOW_OVERLAP_H` 시간 앞에서 연다 — 직전 집계 뒤에 늦게 닿은 글(07:14 글이
    07:15 집계 뒤에 수신함에 들어온 경우)이 어느 날에도 안 실리던 구멍(리뷰 2026-10-12 실측). 겹친 구간의
    이미 실린 항목은 앞선 기록 둘과 대조해 뺀다."""
    now = _now(now)
    stocks, skipped = universe(model, us_etf=_us_etf() if us_etf is None else us_etf)
    if not stocks:
        return None
    recs = load_records(limit=3) if records is None else records
    date = now.date().isoformat()
    prev_until = None
    for r in sorted(recs, key=lambda r: r["date"], reverse=True):
        if r.get("date", "") < date:
            prev_until = _parse((r.get("window") or {}).get("until"))
            break
    since = (prev_until - timedelta(hours=WINDOW_OVERLAP_H)) if prev_until else \
        (now - timedelta(hours=DEFAULT_WINDOW_H))
    since = max(since, now - timedelta(hours=MAX_WINDOW_H))
    until = now
    seen = _seen_keys(recs, date)
    tg_all, tg_bad = _tg_read()
    tg_win = [m for m in tg_all if since <= _parse(m["ts"]) < until]   # _tg_read 가 시각을 이미 검사
    src = {"naver": 0, "google": 0, "yfinance": 0, "fail": 0, "fail_reasons": {},
           "fallback": {}, "dropped_name": 0, "body_ok": 0, "body_try": 0, "ko_try": 0, "ko_ok": 0}
    tg_hits = 0
    out_stocks = []
    bodies = 0
    for st0 in stocks:
        if beat:
            beat()
        st = with_ko(st0, ko)
        if "ko" in st and "ko" not in st0:
            src["ko_try"] += 1
            src["ko_ok"] += bool(st["ko"])
        als = aliases(st)
        name_als = [a for a in als if a[1] == "name"]
        items: list[dict] = []
        try:
            raw, used, why = fetch(st)
        except Exception as exc:                               # noqa: BLE001
            # 한 종목의 예외가 그날 수집 전체를 멈추지 않게 — 그 종목만 실패로 센다(#315).
            log.warning("holdings_news: %s 수집 예외: %s", st["key"], exc)
            raw, used, why = [], "", f"수집 예외({type(exc).__name__}: {_exc_msg(exc, 60)})"
        if pace:
            time.sleep(pace)
        if used:
            src[used] = src.get(used, 0) + 1
            if why:                     # 앞 원천이 실패해 뒤 원천으로 왔다 — 그 사유를 센다(#43)
                src["fallback"][why] = src["fallback"].get(why, 0) + 1
        elif why:
            src["fail"] += 1
            src["fail_reasons"][why] = src["fail_reasons"].get(why, 0) + 1
        dropped = 0
        raw_n = len(raw or [])
        for it in raw or []:
            if not _in_window(it, since, until):
                continue
            if used in ("naver", "google") and st["market"] == "KR":
                # 이름 검색이라 다른 회사 기사가 섞인다 — 제목·요약에 그 이름이 **낱말로** 있어야 한다.
                if not mentioned(f"{it.get('title', '')} {it.get('summary', '')}", name_als):
                    dropped += 1
                    continue
            item = {"src": used or "news", "title": str(it.get("title") or "")[:300],
                    "summary": str(it.get("summary") or "")[:400],
                    "link": str(it.get("link") or ""), "source": str(it.get("source") or ""),
                    "ts": (_item_ts(it) or now).isoformat(timespec="minutes"),
                    "dated_only": not it.get("ts")}
            nl = str(it.get("naver_link") or "")
            if "news.naver.com" in nl:
                item["naver_link"] = nl          # 본문을 읽을 사본(원문 링크는 언론사마다 구조가 다르다)
            item["k"] = _item_key(item)
            if (st["key"], item["k"]) in seen or any(_norm_title(x["title"]) == _norm_title(item["title"])
                                                     for x in items):
                continue
            items.append(item)
            if len(items) >= MAX_NEWS_PER_STOCK:
                break
        src["dropped_name"] += dropped
        n_tg = 0
        for m in reversed(tg_win):                              # 최근 글 먼저
            spans = mentioned(m["text"], als)
            if not spans:
                continue
            item = {"src": "telegram", "chan": m.get("chan"), "id": m.get("id"),
                    "title": excerpt(m["text"], spans[0], 120), "summary": excerpt(m["text"], spans[0]),
                    "link": "", "source": "텔레그램" + (" · 포워드" if m.get("fwd") else ""),
                    "ts": (_parse(m["ts"]) or now).isoformat(timespec="minutes"), "dated_only": False}
            item["k"] = _item_key(item)
            if (st["key"], item["k"]) in seen:
                continue
            items.append(item)
            n_tg += 1
            if n_tg >= MAX_TG_PER_STOCK:
                break
        tg_hits += n_tg
        # 본문(네이버 호스팅 기사만) — AI 재료일 뿐 화면엔 싣지 않는다. 길이만 남긴다.
        nb = 0
        for item in items:
            if nb >= MAX_BODY_PER_STOCK or bodies >= MAX_BODY_PER_RUN:
                break
            blink = item.get("naver_link") or (item["link"] if "news.naver.com" in item["link"] else "")
            if item["src"] == "telegram" or not blink:
                continue
            src["body_try"] += 1
            b = body(blink)
            bodies += 1
            nb += 1
            if pace:
                time.sleep(pace)
            if b:
                item["_body"] = b
                item["body_chars"] = len(b)
                src["body_ok"] += 1
        items.sort(key=lambda x: x["ts"], reverse=True)
        for n, item in enumerate(items, 1):
            item["n"] = n
        out_stocks.append({**{k: st[k] for k in ("key", "market", "ticker", "name", "value")},
                           **({"ko": st["ko"]} if st.get("ko") else {}),
                           "items": items, "via": used or "", "via_why": why, "raw_n": raw_n,
                           "status": "pending" if items else "empty", "tries": 0,
                           "pros": [], "cons": [], "relevant": None, "reason": ""})
    return {"date": date, "window": {"since": since.isoformat(timespec="seconds"),
                                     "until": until.isoformat(timespec="seconds")},
            "collected_at": now.isoformat(timespec="seconds"),
            "universe": {"n": len(stocks), "unmatched": skipped["unmatched"],
                         "etf": skipped["etf"], "as_of": skipped.get("as_of"),
                         "us_etf_n": skipped.get("us_etf_n")},
            "sources": {**src, "telegram": {"inbox": len(tg_all), "window": len(tg_win),
                                            "matched": tg_hits, "bad_lines": tg_bad}},
            "stocks": out_stocks,
            "llm": {"calls": 0, "pt": 0, "ot": 0, "cost_usd": 0.0}}


# ── Pros/Cons (AI) ───────────────────────────────────────────────────────────
def _stock_block(s: dict) -> str:
    mk = {"KR": "한국", "US": "미국"}.get(s["market"], s["market"])
    nm = s["name"] + (f" / {s['ko']}" if s.get("ko") else "")      # 'NAVER / 네이버' — 한글 기사도 그 회사다
    lines = [f"### key={s['key']} · {nm}({s['ticker']}, {mk})"]
    for it in s["items"]:
        kind = "텔레그램" if it["src"] == "telegram" else f"뉴스·{it.get('source') or it['src']}"
        summ = it.get("summary") or ""
        head = f"[{it['n']}] ({kind} {it['ts'][5:10]}) "
        main = f"제목: {it['title']}" if it["src"] != "telegram" else ""
        line = (head + main + (f" / 내용: {summ}" if summ else ""))[:SNIPPET_CHARS + 120]
        body = it.get("_body") or ""
        if body:                # 줄 상한과 따로 — 한 상한에 묶으면 본문 900자 중 1/3 만 닿았다(리뷰 2026-10-12)
            line += f" / 본문: {body[:BODY_CHARS]}"
        lines.append(line)
    return "\n".join(lines)


def build_prompt(stocks: list[dict]) -> str:
    return (
        "너는 개인 투자자의 보유 종목 뉴스를 정리하는 보조다. 아래 규칙을 어기면 실패다.\n"
        "1) 각 종목 아래 [번호] 항목에 실제로 쓰인 사실만 근거로 쓴다. 항목에 없는 사실·수치·전망을\n"
        "   지어내지 않는다. 수치는 항목에 있는 것만 인용한다.\n"
        "2) pros = 그 종목 주주에게 긍정적으로 읽힐 수 있는 점(호재 관점). cons = 부정적이거나 위험이\n"
        "   될 수 있는 점(악재·리스크 관점). 같은 사실이 양면이면 양쪽에 다른 관점으로 써도 된다.\n"
        "3) 각 줄은 한국어 1문장 70자 이내, 근거 항목 번호를 refs 에 단다(예: [1,3]).\n"
        f"4) 근거가 없으면 빈 배열로 둔다. 개수를 맞추려고 일반론('변동성 주의' 등)을 넣지 않는다.\n"
        f"   pros·cons 각각 최대 {MAX_PER_SIDE}개.\n"
        "5) 그 회사에 관한 내용이 아닌 항목(이름이 같은 다른 회사·여러 종목 시세 나열·광고)은\n"
        "   relevant 에 넣지 않는다. relevant 에 없는 항목은 근거로 쓰지 않는다.\n"
        "6) 매수·매도 권유, 목표주가, 주가 예측을 쓰지 않는다.\n\n"
        "출력은 아래 JSON 하나만(설명·코드펜스 금지):\n"
        '{"stocks":[{"key":"종목 key 그대로","relevant":[1,2],'
        '"pros":[{"t":"한 문장","refs":[1]}],"cons":[{"t":"한 문장","refs":[2]}]}]}\n\n'
        "종목:\n" + "\n\n".join(_stock_block(s) for s in stocks))


def _parse_json(text: str) -> dict | None:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        v = json.loads(t[a:b + 1])
    except Exception:                                          # noqa: BLE001
        return None
    return v if isinstance(v, dict) else None


def _clean_side(v, valid: set) -> tuple[list[dict], int]:
    """(근거 번호가 실재하는 줄, 버린 줄 수) — 번호가 없거나 틀린 줄은 지어낸 문장일 수 있어 버린다. 버린
    수는 화면이 말한다 — 다 버리면 '가를 내용이 없음' 과 구별이 안 된다(리뷰 2026-10-12)."""
    out, dropped = [], 0
    for x in v if isinstance(v, list) else []:
        if not isinstance(x, dict):
            dropped += 1
            continue
        t = " ".join(str(x.get("t") or "").split())
        refs = []
        for r in x.get("refs") if isinstance(x.get("refs"), list) else []:
            try:
                r = int(r)
            except (TypeError, ValueError):
                continue
            if r in valid and r not in refs:
                refs.append(r)
        if not t or not refs:
            dropped += 1
            continue
        out.append({"t": t[:LINE_CHARS] + ("…" if len(t) > LINE_CHARS else ""), "refs": refs})
        if len(out) >= MAX_PER_SIDE:
            break
    return out, dropped


def apply_result(stock: dict, res: dict) -> None:
    """AI 결과 한 종목을 기록에 얹는다(검증 포함)."""
    nums = {it["n"] for it in stock["items"]}
    rel = res.get("relevant")
    relevant = sorted({int(r) for r in rel if str(r).lstrip("-").isdigit() and int(r) in nums}) \
        if isinstance(rel, list) else sorted(nums)
    valid = set(relevant)
    stock["relevant"] = relevant
    stock["pros"], dp = _clean_side(res.get("pros"), valid)
    stock["cons"], dc = _clean_side(res.get("cons"), valid)
    stock["dropped_lines"] = dp + dc
    stock["status"] = "ok"
    stock["reason"] = ""
    stock["retry"] = False


def batches(stocks: list[dict]) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for s in stocks:
        n = len(_stock_block(s))
        if cur and (size + n > BATCH_CHARS or len(cur) >= BATCH_MAX):
            out.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += n
    if cur:
        out.append(cur)
    return out


def _log_usage(pt: int, ot: int) -> float:
    """비용 한 줄을 usage.jsonl 에 — `usage_tracker._append` 를 쓴다(같은 파일·같은 잠금). 봇 프로세스
    안에서 `/usage` 가 그 파일을 로테이션하며 다시 쓰는데, 잠금 밖에서 따로 열어 쓰면 그 사이의 줄이
    사라질 수 있다(#38)."""
    cost = 0.0
    try:
        from bot import usage_tracker as ut
        cost = round(ut.estimate_cost_usd(_MODEL, pt, ot), 6)
        ut._append({"ts": time.time(), "type": "llm_call", "model": _MODEL, "prompt_tokens": pt,
                    "completion_tokens": ot, "cost_usd": cost, "subsystem": SUBSYSTEM})
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: 비용 기록 실패: %s", exc)
    return cost


def _llm_fail_kind(exc) -> str:
    """AI 호출 예외의 갈래 — 'transient'(기다리면 풀린다: 429·5xx·시간 초과·연결) / 'rejected'(요청이
    거부됐거나 우리 쪽 오류 — 같은 요청을 되풀이해도 같다). google.genai 오류는 `code`(HTTP 상태)를 싣는다."""
    code = getattr(exc, "code", None)
    if isinstance(code, int) and not isinstance(code, bool):
        return "transient" if code == 429 or code >= 500 else "rejected"
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(
            w in type(exc).__name__ for w in ("Timeout", "Connect", "Network", "RemoteProtocol")):
        return "transient"
    return "rejected"


def call_llm(prompt: str) -> tuple[str | None, int, int, str, str]:
    """(응답 글, 입력 토큰, 출력 토큰, 실패 사유, 갈래). 갈래 = '' · 'no_key' · 'transient' · 'rejected'.
    JSON 설정을 빼고 다시 부르는 것은 **그 설정이 거부됐을 때(400)만** — 429·503 을 같은 주기에 두 번
    두드리지 않는다(리뷰 2026-10-12: 장애 중 한 주기 80회). 사유는 화면에 실리므로 키를 가린다(§Secrets)."""
    from bot.env_keys import remember_secret
    try:
        from bot.genai_factory import effective_key, make_client
        key = effective_key()
        if not key:
            return None, 0, 0, "no_key", "no_key"
        remember_secret(str(key))
        client = make_client()
        try:
            from google.genai import types as _t
            cfg = _t.GenerateContentConfig(response_mime_type="application/json")
        except Exception:                                      # noqa: BLE001
            cfg = None
        try:
            resp = client.models.generate_content(model=_MODEL, contents=prompt,
                                                  **({"config": cfg} if cfg is not None else {}))
        except Exception as exc:                               # noqa: BLE001
            if cfg is None or getattr(exc, "code", None) != 400:
                raise
            resp = client.models.generate_content(model=_MODEL, contents=prompt)
    except Exception as exc:                                   # noqa: BLE001
        kind = _llm_fail_kind(exc)
        label = "AI 일시 오류" if kind == "transient" else "AI 호출 실패"
        return None, 0, 0, f"{label}({type(exc).__name__}: {_exc_msg(exc, 80)})", kind
    pt = ot = 0
    try:
        um = getattr(resp, "usage_metadata", None)
        if um:
            pt = int(getattr(um, "prompt_token_count", 0) or 0)
            ot = int(getattr(um, "candidates_token_count", 0) or 0)
    except Exception:                                          # noqa: BLE001
        pass
    return (getattr(resp, "text", None) or ""), pt, ot, "", ""


def analyze(rec: dict, *, llm=call_llm, max_calls: int = MAX_LLM_CALLS,
            on_progress=None) -> dict:
    """대기 중인 종목을 묶어 AI 에 보낸다(한 주기 `max_calls` 회까지). 묶음마다 `on_progress(rec)`
    로 저장 — 중간에 멈춰도 받은 결과는 남는다. 반환 = {calls, analyzed, failed, no_key, transient}.

    갈래(`call_llm`): 키 없음 → 사유만 적고 멈춘다 · 일시 오류(429·5xx·시간 초과) → 그 묶음에 사유만 적고
    **멈춘다** — 실패로 세지 않는다(30분 장애가 그날 분석을 통째로 '실패' 로 굳히던 구멍, 리뷰 2026-10-12)
    · 거부·못 읽음·빠짐 → 그 묶음 종목의 시도 수를 센다(같은 요청을 되풀이해도 같다)."""
    pend = [s for s in rec.get("stocks") or [] if s.get("status") == "pending"]
    done = {"calls": 0, "analyzed": 0, "failed": 0, "no_key": False, "transient": False}
    _attach_bodies(rec, pend)
    for grp in batches(pend):
        if done["calls"] >= max_calls:
            break
        text, pt, ot, why, kind = llm(build_prompt(grp))
        if kind == "no_key":
            for s in pend:
                s["reason"] = "AI 키(GOOGLE_API_KEY) 미설정 — 뉴스만 보여 줍니다"
                s["retry"] = False
            done["no_key"] = True
            break
        done["calls"] += 1
        llm_rec = rec.setdefault("llm", {"calls": 0, "pt": 0, "ot": 0, "cost_usd": 0.0})
        llm_rec["calls"] += 1
        if pt or ot:
            llm_rec["pt"] += pt
            llm_rec["ot"] += ot
            llm_rec["cost_usd"] = round(llm_rec["cost_usd"] + _log_usage(pt, ot), 6)
        if kind == "transient":
            for s in grp:
                # 사유만 남기고 '다시 한다' 는 화면이 그날이 아직 이어서 정리되는 날일 때만 붙인다 — 저장해
                # 두면 그날이 `_live_dates` 밖으로 나간 뒤에도 지킬 수 없는 약속이 남는다(리뷰 2026-10-13).
                s["reason"] = why
                s["retry"] = True
            done["transient"] = True
            if on_progress:
                on_progress(rec)
            break
        data = _parse_json(text) if text else None
        got = {}
        if data and isinstance(data.get("stocks"), list):
            for r in data["stocks"]:
                if isinstance(r, dict) and r.get("key"):
                    got[str(r["key"])] = r
        for s in grp:
            r = got.get(s["key"])
            if r is not None:
                apply_result(s, r)
                for it in s["items"]:
                    if it.get("_body"):
                        it["body_used"] = True      # 화면 표시는 '보냈다' 는 사실에서(본문 길이가 아니라)
                done["analyzed"] += 1
                continue
            s["retry"] = False
            s["tries"] = int(s.get("tries") or 0) + 1
            s["reason"] = why or ("AI 응답을 못 읽음" if not data else "AI 응답에 이 종목이 없음")
            if s["tries"] >= MAX_ANALYZE_TRIES:
                s["status"] = "failed"
                done["failed"] += 1
        if on_progress:
            on_progress(rec)
    return done


# ── 저장 ─────────────────────────────────────────────────────────────────────
def _day_path(date: str) -> Path:
    return _DAYS / f"{date}.json"


def _bodies_path(date: str) -> Path:
    """본문 사이드카 — 아직 정리 안 된 종목의 본문(AI 재료)만. 화면·하루 기록엔 안 실린다. 수집 주기 안에서만
    메모리에 있던 본문이 일시 오류·상한·재시작으로 다음 주기로 밀린 종목에서 통째로 빠지던 구멍(리뷰
    2026-10-13 실측: 07:10 503 한 번 → 그날 전 종목이 본문 없이 정리됐는데 카드는 '읽음')."""
    return _DAYS / f"bodies_{date}.json"


def _save_bodies(rec: dict) -> None:
    keep: dict = {}
    lost = []
    for s in rec.get("stocks") or []:
        if s.get("status") != "pending":
            continue
        got = {it["k"]: it["_body"] for it in s.get("items") or [] if it.get("_body") and it.get("k")}
        if got:
            keep[s["key"]] = got
        elif any(it.get("body_chars") for it in s.get("items") or []):
            lost.append(s["key"])        # 본문이 있었는데 메모리엔 없다(다시 읽은 기록) — 파일의 것을 지우지 않는다
    p = _bodies_path(rec["date"])
    if lost:
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            old = {}
        except Exception as exc:                               # noqa: BLE001
            # 못 읽은 파일을 덮어쓰거나 지우면 멀쩡한 본문을 잃는다(반영분 리뷰) — 이번엔 손대지 않는다.
            log.warning("holdings_news: 본문 사이드카 %s 를 못 읽어 이번엔 갱신하지 않음: %s", p.name, exc)
            return
        for k in lost:
            if isinstance(old, dict) and isinstance(old.get(k), dict) and old[k]:
                keep[k] = old[k]
    try:
        if keep:
            _atomic_write(p, json.dumps(keep, ensure_ascii=False))
        else:
            p.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("holdings_news: 본문 사이드카 저장 실패 %s: %s", p.name, exc)


def _attach_bodies(rec: dict, pend: list[dict]) -> None:
    """기록에서 다시 읽은 대기 종목에 사이드카의 본문을 붙인다(이미 메모리에 있으면 그대로). 못 읽으면 본문
    없이 간다 — 화면은 실제로 보낸 것만 '읽음' 으로 적으므로(`body_used`) 거짓말은 안 한다."""
    need = [s for s in pend if any(it.get("body_chars") and not it.get("_body") for it in s.get("items") or [])]
    if not need or not rec.get("date"):
        return
    p = _bodies_path(rec["date"])
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: 본문 사이드카 %s 읽기 실패: %s", p.name, exc)
        return
    if not isinstance(data, dict):
        return
    for s in need:
        got = data.get(s["key"]) if isinstance(data.get(s["key"]), dict) else {}
        for it in s.get("items") or []:
            b = got.get(it.get("k"))
            if isinstance(b, str) and b and not it.get("_body"):
                it["_body"] = b


def _public(rec: dict) -> dict:
    """저장본 — 본문(`_body`)은 남기지 않는다(AI 재료일 뿐 재게시가 아니다)."""
    out = dict(rec)
    out["stocks"] = [{**s, "items": [{k: v for k, v in it.items() if not k.startswith("_")}
                                     for it in s.get("items") or []]}
                     for s in rec.get("stocks") or []]
    return out


_DAY_FILE = re.compile(r"\d{4}-\d{2}-\d{2}\.json")


def _day_files() -> list[Path]:
    """하루 기록 파일 — 이름이 날짜인 것만, 날짜순(파싱 없이 이름으로)."""
    try:
        return sorted(p for p in _DAYS.glob("*.json") if _DAY_FILE.fullmatch(p.name))
    except OSError:
        return []


def count_records() -> int:
    """보관 중인 날 수(파일 이름으로 — 읽지 않는다)."""
    return len(_day_files())


def save_record(rec: dict) -> None:
    _atomic_write(_day_path(rec["date"]), json.dumps(_public(rec), ensure_ascii=False))
    _save_bodies(rec)
    try:
        files = _day_files()
        for f in files[:-KEEP_DAYS]:
            f.unlink()
        # 본문 사이드카는 주기가 아직 이어서 정리하는 날(최근 두 기록, `_live_dates`)에만 쓸모가 있다.
        live = {f.stem for f in files[-2:]} | {rec["date"]}
        for b in _DAYS.glob("bodies_*.json"):
            if b.stem[len("bodies_"):] not in live:
                b.unlink()
    except OSError as exc:
        log.warning("holdings_news: 옛 기록 정리 실패: %s", exc)


def load_records(limit: int | None = None) -> list[dict]:
    """하루 기록들(날짜순). `limit` = 최근 그만큼만 읽는다 — 봇이 15분마다 부르는데 보관분(`KEEP_DAYS`)을
    통째로 읽으면 한 번에 수 초·수백 MB 다(리뷰 2026-10-12 실측: 120일 × 2.75MB = 15초·613MB). 파일
    이름과 안의 날짜가 다르면 건너뛴다(같은 날이 둘로 세지지 않게)."""
    files = _day_files()
    if limit is not None:
        files = files[-limit:] if limit > 0 else []
    out = []
    for f in files:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:                               # noqa: BLE001
            log.warning("holdings_news: 기록 %s 읽기 실패: %s", f.name, exc)
            continue
        if isinstance(r, dict) and r.get("date") == f.stem:
            out.append(r)
        else:
            log.warning("holdings_news: 기록 %s 의 날짜가 파일 이름과 달라 건너뜀", f.name)
    return out


def load_state() -> dict:
    try:
        v = json.loads(_STATE.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except Exception:                                          # noqa: BLE001
        return {}


def _save_state(st: dict) -> None:
    try:
        _atomic_write(_STATE, json.dumps(st, ensure_ascii=False))
    except OSError as exc:
        log.warning("holdings_news: 상태 저장 실패: %s", exc)


def due(now: datetime) -> bool:
    return (now.hour, now.minute) >= (RUN_HOUR, RUN_MIN)


def _live_dates(dates, today: str) -> set:
    """주기가 아직 이어서 정리하는 날 — 오늘과, 오늘 앞의 가장 최근 기록 하나. 그보다 앞선 날의 '대기' 는
    다시 안 돈다(화면이 '마치지 못함' 으로 적는다). `tick` 과 화면이 같은 규칙을 쓴다(#38)."""
    prev = max((d for d in dates if d < today), default=None)
    return {today} | ({prev} if prev else set())


# ── 한 주기 ──────────────────────────────────────────────────────────────────
_TICK = threading.Lock()


def tick(now: datetime | None = None, *, llm=call_llm, fetch=fetch_stock_news,
         body=_fetch_body, pace: float = 0.25) -> dict:
    """오늘 기록이 없고 집계 시각이 지났으면 모으고, 대기 종목이 있으면 AI 에 보낸다 — 오늘 먼저, 남은 상한으로
    직전 기록의 대기분도(자정 무렵 늦은 집계·장애로 남은 것 — 오늘만 보면 영영 '대기' 다, 리뷰 2026-10-12).
    `last_tick` 은 **시작할 때** 남긴다 — 끝에만 남기면 긴 수집 중에 진단·감사가 '봇 주기 멈춤' 으로 읽는다."""
    fixed = now is not None
    now = _now(now)
    if not _TICK.acquire(blocking=False):
        return {"status": "busy"}
    try:
        with _flock(_RUN_LOCK):
            st = load_state()
            st["last_tick"] = now.isoformat(timespec="seconds")
            _save_state(st)
            beat_at = [time.monotonic()]

            def beat() -> None:
                # 같은 스레드·같은 상태 사전이라 다른 쓰기를 덮지 않는다(바쁜 주기가 따로 쓰면 그게 덮는다).
                if time.monotonic() - beat_at[0] < BEAT_SEC:
                    return
                beat_at[0] = time.monotonic()
                st["last_tick"] = (now if fixed else _now()).isoformat(timespec="seconds")
                _save_state(st)

            date = now.date().isoformat()
            recs = load_records(limit=3)
            rec = next((r for r in recs if r["date"] == date), None)
            if rec is None and due(now):
                try:
                    tg_prune(now)
                except Exception as exc:                       # noqa: BLE001
                    log.warning("holdings_news: 수신함 정리 실패: %s", exc)
                # 지난 주기의 실패 사유는 지금 다시 모으기 시작한 순간 낡는다 — 남겨 두면 판정이 사유를 먼저 봐
                # 정상 수집 내내 '오늘 집계 못 함' 이다(반영분 리뷰). 실패하면 `tick_and_render` 가 다시 적는다.
                st["reason"] = ""
                _save_state(st)
                rec = collect(now, records=recs, fetch=fetch, body=body, pace=pace, beat=beat)
                if rec is None:
                    st["reason"] = "자산 대시보드에 보유 종목이 없음(portfolio.json)"
                    st["no_holdings"] = True
                    _save_state(st)
                    return {"status": "none"}
                save_record(rec)
                st["last_collect"] = now.isoformat(timespec="seconds")
            live = _live_dates([r["date"] for r in recs], date)
            todo = ([rec] if rec else []) + [r for r in sorted(recs, key=lambda r: r["date"], reverse=True)
                                             if r["date"] != date and r["date"] in live]
            res = {"calls": 0, "analyzed": 0, "failed": 0, "no_key": False, "transient": False}
            for r in todo:
                if not any(s.get("status") == "pending" for s in r.get("stocks") or []):
                    continue
                d = analyze(r, llm=llm, max_calls=MAX_LLM_CALLS - res["calls"], on_progress=save_record)
                save_record(r)
                for k in ("calls", "analyzed", "failed"):
                    res[k] += d[k]
                res["no_key"] |= d["no_key"]
                res["transient"] |= d["transient"]
                if d["no_key"] or d["transient"] or res["calls"] >= MAX_LLM_CALLS:
                    break
            if rec is None:
                return {"status": "wait", **res}
            st["reason"] = ""
            st["no_holdings"] = False
            st["last_analyze"] = now.isoformat(timespec="seconds")
            _save_state(st)
            return {"status": "ok", **res}
    finally:
        _TICK.release()


def tick_and_render(now: datetime | None = None) -> dict:
    res = {}
    try:
        res = tick(now)
    except Exception as exc:                                   # noqa: BLE001
        log.exception("holdings_news: 주기 실패")
        st = load_state()
        st["reason"] = f"주기 실패({type(exc).__name__}: {_exc_msg(exc, 80)})"
        st["no_holdings"] = False
        _save_state(st)
        res = {"status": "error"}
    regenerate(now)
    return res


# ── 판정(화면·`--why`·감사가 같이 쓴다, #35) ─────────────────────────────────
def _src_failed(s: dict) -> bool:
    return s.get("via") == "" and bool(s.get("via_why"))


def _outage(rec: dict) -> str:
    """원천 장애 한 줄(없으면 '') — 몇 종목이 원천을 못 받았나 · 한 시장이 통째로 실패·0건인가.

    전멸만 잡으면 국내 312종목이 다 실패하고 해외 67종목이 받은 날이 ✅ 였다(리뷰 2026-10-12 실측). 종목
    단위로 세되 몇 종목의 일시 실패는 장애로 치지 않는다(`SRC_FAIL_SHARE`·`SRC_MARKET_MIN` — 그 수는 ✅ 줄이
    말한다). 시장 전부가 **0건**인 것도 장애다 — yfinance 는 깨진 응답을 빈 목록으로 숨긴다."""
    ss = rec.get("stocks") or []
    src = rec.get("sources") or {}
    n_fail = int(src.get("fail") or 0)
    why = next(iter(src.get("fail_reasons") or {}), "원천 실패")
    if n_fail and not any(src.get(k) for k in ("naver", "google", "yfinance")):
        return f"뉴스 원천을 하나도 못 받음 — {why}"
    if n_fail >= SRC_MARKET_MIN and n_fail >= SRC_FAIL_SHARE * len(ss):
        return f"원천 실패 {n_fail}/{len(ss)}종목 — {why}"
    by_mk: dict[str, list] = {}
    for s in ss:
        by_mk.setdefault(str(s.get("market") or "?"), []).append(s)
    for mk, xs in sorted(by_mk.items()):
        if len(xs) < SRC_MARKET_MIN:
            continue
        if all(_src_failed(s) for s in xs):
            return f"{mk} 보유 {len(xs)}종목 전부 원천 실패 — {xs[0].get('via_why')}"
        if all(s.get("raw_n") == 0 for s in xs):
            return (f"{mk} 보유 {len(xs)}종목 전부 원천이 0건을 줌 — 원천 장애를 빈 목록으로 숨겼을 수 "
                    "있음(정상이면 이럴 수 없다)")
    return ""


def status_verdict(records: list[dict], now: datetime | None = None,
                   state: dict | None = None) -> tuple[str, str]:
    """(갈래, 한 줄) — ok · wait · pending · fail · none."""
    now = _now(now)
    st = state if state is not None else load_state()
    today = now.date().isoformat()
    rec = next((r for r in records if r.get("date") == today), None)
    last = max((r["date"] for r in records), default=None)
    if rec is None:
        if st.get("no_holdings"):
            return "none", f"집계할 보유 종목이 없음 — {st.get('reason') or '자산 기록 없음'}"
        if not due(now):
            # 집계 시각 전 — 직전 주기의 실패 사유는 '오늘 못 했다' 가 아니라 지난 일이다(같이 적는다).
            prev = f" · 직전 주기 실패: {st['reason']}" if st.get("reason") else ""
            return "wait", (f"오늘 {RUN_HOUR:02d}:{RUN_MIN:02d} KST 이후 집계 · 마지막 기록 "
                            f"{last or '없음'}{prev}")
        if st.get("reason"):
            return "fail", f"오늘 집계 못 함 — {st['reason']}"
        tick_at = _parse(st.get("last_tick"))
        if tick_at is None:
            # 주기가 한 번도 안 돌았다 — '집계 중' 이라 적으면 재지 않은 주장이다(#165).
            return "wait", ("봇이 이 보드의 주기를 아직 한 번도 돌리지 않음 — 봇이 새 판으로 뜨면 약 3분 뒤 첫 "
                            f"주기 · 마지막 기록 {last or '없음'}")
        if now - tick_at > timedelta(minutes=STALE_TICK_MIN):
            return "fail", (f"{RUN_HOUR:02d}:{RUN_MIN:02d} 이 지났는데 오늘 기록이 없고 마지막 주기가 "
                            f"{tick_at.strftime('%m-%d %H:%M')} — 봇 주기가 멈췄을 수 있음")
        return "wait", f"오늘 집계 중(또는 다음 주기) · 마지막 기록 {last or '없음'}"
    ss = rec.get("stocks") or []
    pend = [s for s in ss if s.get("status") == "pending"]
    failed = [s for s in ss if s.get("status") == "failed"]
    news = [s for s in ss if s.get("items")]
    src = rec.get("sources") or {}
    head = (f"{rec['date']} · 보유 {len(ss)}종목 중 뉴스 {len(news)}종목 · 텔레그램 언급 "
            f"{(src.get('telegram') or {}).get('matched', 0)}건")
    out = _outage(rec)
    if out:                      # 장애(❌)가 대기(⚠️)보다 먼저 — 대기가 오래 가면 장애가 안 보였다
        return "fail", f"{head} · {out}" + (f" · Pros/Cons 대기 {len(pend)}종목" if pend else "")
    if pend:
        reason = _pending_reason(pend[0]) if pend[0].get("reason") else "다음 주기에 이어서"
        return "pending", f"{head} · Pros/Cons 대기 {len(pend)}종목 — {reason}"
    if failed:
        return "fail", (f"{head} · Pros/Cons 실패 {len(failed)}종목(뉴스만 표시) — "
                        f"{failed[0].get('reason') or '사유 미기록'}")
    if src.get("fail"):          # 장애 기준 밑 — 판정은 ✅ 이되 사실은 말한다(#41)
        return "ok", f"{head} · 원천 못 받은 종목 {src['fail']}"
    return "ok", head


# ── 화면 ─────────────────────────────────────────────────────────────────────
_WD = "월화수목금토일"
_GEN = "\x00GEN\x00"   # 생성 시각 자리 — 내용 비교에서 빼려고 맨 마지막에 채운다(#442)

_HN_CSS = """
<style>
:root{--bg:#f7f8f9;--card:#ffffff;--hn-pro:#166534;--hn-con:#b91c1c;--hn-up:#c62828;--hn-dn:#1d4ed8}
:root[data-theme="dark"]{--bg:#0f1117;--card:#1a1d27;--hn-pro:#4ade80;--hn-con:#f87171;--hn-up:#f87171;--hn-dn:#60a5fa}
.hn-status{background:var(--surface2);border-left:3px solid var(--accent);border-radius:8px;padding:10px 12px;font-size:13px;color:var(--fg);margin:10px 0}
.hn-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0}
.hn-tools input{flex:1;min-width:200px;max-width:420px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--fg);font-size:13px}
.hn-day{background:var(--card);border:1px solid var(--border);border-radius:12px;margin:10px 0;padding:4px 14px}
.hn-day>summary{cursor:pointer;font-weight:600;padding:8px 4px;font-size:14px}
.hn-meta{font-weight:400;font-size:12px;color:var(--muted);margin-left:8px}
.hn-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:4px 0 12px}
@media (max-width:960px){.hn-grid{grid-template-columns:1fr}}
.hn-card{border:1px solid var(--border);border-radius:12px;padding:10px 12px;background:var(--card)}
.hn-h{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 10px;margin-bottom:6px}
.hn-nm{font-size:14.5px;font-weight:700;color:var(--accent);text-decoration:none}
.hn-chg{font-size:12.5px;font-weight:600;color:var(--muted);margin-left:2px}
.hn-chg.hn-up{color:var(--hn-up)}.hn-chg.hn-dn{color:var(--hn-dn)}  /* 한국 관례: 상승 빨강 · 하락 파랑(심리지도와 같다) */
.hn-nm:hover{text-decoration:underline}
.hn-tk{font-size:12px;color:var(--muted)}
.hn-pc{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
@media (max-width:560px){.hn-pc{grid-template-columns:1fr}}
.hn-side{border-radius:8px;padding:6px 8px;background:var(--surface2)}
.hn-lbl{font-size:12px;font-weight:700;margin-bottom:2px}
.hn-pro .hn-lbl{color:var(--hn-pro)}
.hn-con .hn-lbl{color:var(--hn-con)}
.hn-side ul{margin:2px 0 0 16px;padding:0;font-size:13px;line-height:1.55;color:var(--fg)}
.hn-ref{font-size:11px;color:var(--muted);margin-left:3px}
.hn-none{font-size:12.5px;color:var(--muted)}
.hn-why{font-size:12.5px;color:var(--fg);opacity:.85;margin:4px 0}
.hn-src{margin-top:6px;font-size:12.5px}
.hn-src>summary{cursor:pointer;color:var(--muted)}
.hn-src ol{margin:4px 0 0 18px;padding:0;line-height:1.55;color:var(--fg)}
.hn-src a{color:var(--fg)}
.hn-off{opacity:.62}
.hn-sum{color:var(--muted);font-size:12px}
.hn-quiet{font-size:12.5px;color:var(--muted);margin:2px 0 10px;line-height:1.6}
.hn-hit{outline:2px solid var(--accent);outline-offset:1px}
.hn-lazy{font-size:12.5px;color:var(--muted);padding:8px 4px}
</style>
"""

_HN_JS = r"""
(function(){
  var q=document.getElementById('hn-q'), st=document.getElementById('hn-st');
  var pills=[].slice.call(document.querySelectorAll('[data-hnf]'));
  var days=[].slice.call(document.querySelectorAll('details[data-hnday]'));
  if(!q) return;
  var base=st?st.textContent:'', mode='all', wasOn=false;
  function norm(s){return String(s||'').toLowerCase().replace(/\s+/g,'');}
  function keep(c){
    if(mode==='KR') return c.getAttribute('data-mk')==='KR';
    if(mode==='XX') return c.getAttribute('data-mk')!=='KR';
    if(mode==='pro') return c.getAttribute('data-pro')==='1';
    if(mode==='con') return c.getAttribute('data-con')==='1';
    if(mode==='tg') return c.getAttribute('data-tg')==='1';
    return true;
  }
  function lazy(d){return !!d.getAttribute('data-lazy') && !d.__loaded;}
  function run(){
    var v=norm(q.value), on=!!v || mode!=='all', shown=0;
    [].slice.call(document.querySelectorAll('[data-hncard]')).forEach(function(c){
      var ok=keep(c) && (!v || norm(c.getAttribute('data-q')).indexOf(v)>=0);
      c.style.display=ok?'':'none';
      if(ok) shown++;
      if(v && ok) c.classList.add('hn-hit'); else c.classList.remove('hn-hit');
    });
    var wait=0;
    days.forEach(function(d){
      // 기본 접힘으로 되돌리는 건 필터를 **지울 때만** — 조각을 불러온 뒤의 run() 이 방금 펼친 날을 닫았다
      if(!on){d.style.display='';if(wasOn) d.open=d.getAttribute('data-open')==='1';return;}
      if(lazy(d)){d.style.display='';wait++;return;}
      var any=[].slice.call(d.querySelectorAll('[data-hncard]')).some(function(c){return c.style.display!=='none';});
      d.style.display=any?'':'none';
      if(any) d.open=true;
    });
    wasOn=on;
    if(st) st.textContent=on?('조건에 맞는 카드 '+shown+'개'+(wait?(' · 아직 안 연 날 '+wait+'일은 펼치면 같이 걸러집니다'):'')):base;
  }
  function load(d){
    if(d.__loaded||d.__loading) return;
    var box=d.querySelector('.hn-lazy');
    if(!box) return;
    d.__loading=true;
    fetch(d.getAttribute('data-lazy'),{cache:'no-store'})
      .then(function(r){if(!r.ok) throw new Error('HTTP '+r.status); return r.text();})
      .then(function(h){box.outerHTML=h; d.__loaded=true; d.__loading=false; run();})
      .catch(function(){d.__loading=false; if(!d.__loaded) box.textContent='불러오기 실패 — 접었다 다시 펼쳐 보세요.';});
  }
  days.forEach(function(d){
    if(d.getAttribute('data-lazy')) d.addEventListener('toggle',function(){if(d.open) load(d);});
  });
  pills.forEach(function(p){p.addEventListener('click',function(){
    mode=p.getAttribute('data-hnf');
    pills.forEach(function(x){x.classList.toggle('active',x===p);});
    run();
  });});
  q.addEventListener('input',run);
})();
"""


def _krw(v: float) -> str:
    if v >= 1e8:
        return f"{v / 1e8:,.1f}억원"
    if v >= 1e4:
        return f"{v / 1e4:,.0f}만원"
    return f"{v:,.0f}원"


def _lookup(s: dict) -> str:
    """종목 화면 링크. 국내 6자리는 **접미 없이** — 리졸버가 KRX 목록으로 KS/KQ 를 가른다. `.KS` 를
    붙여 보내면 '이미 티커 모양' 으로 그대로 쓰여 코스닥 종목이 빈 화면이 된다(#399). 그 밖은 자산
    기록의 티커 그대로(해외 접미·국내 영숫자 코드의 접미를 지우지 않는다)."""
    t = s["ticker"]
    if s["market"] == "KR" and re.fullmatch(r"\d{6}", t):
        return f"lookup/{t}"
    return f"lookup/{s.get('sym') or t}"


def _safe_link(u: str) -> str:
    """원천이 준 링크 중 http(s) 만 href 로 — `javascript:` 같은 스킴은 escape 로 막히지 않는다."""
    u = str(u or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else ""


def _item_li(it: dict, off: bool) -> str:
    import html as _h
    src = "📨 텔레그램" if it["src"] == "telegram" else f"📰 {it.get('source') or it['src']}"
    when = it["ts"][5:10] if it.get("dated_only") else it["ts"][5:16].replace("T", " ")
    title = _h.escape(it.get("title") or "")
    link = _safe_link(it.get("link"))
    if link:
        title = f"<a href='{_h.escape(link)}' target='_blank' rel='noopener'>{title}</a>"
    summ = it.get("summary") or ""
    extra = ""
    if it["src"] == "telegram":
        extra = f"<div class='hn-sum'>{_h.escape(summ)}</div>" if summ and summ != it.get("title") else ""
    elif summ:
        extra = f"<div class='hn-sum'>{_h.escape(summ[:180])}{'…' if len(summ) > 180 else ''}</div>"
    body = " · 본문 앞부분을 AI 가 읽음" if it.get("body_used") else ""
    cls = " class='hn-off'" if off else ""
    return (f"<li{cls}>[{it['n']}] {_h.escape(src)} · {when}{body}"
            f"<br>{title}{extra}</li>")


def _side(label: str, cls: str, lines: list[dict]) -> str:
    import html as _h
    if lines:
        body = "<ul>" + "".join(
            f"<li>{_h.escape(x['t'])}<span class='hn-ref'>[{','.join(map(str, x['refs']))}]</span></li>"
            for x in lines) + "</ul>"
    else:
        body = "<div class='hn-none'>해당 내용 없음</div>"
    return f"<div class='hn-side {cls}'><div class='hn-lbl'>{label}</div>{body}</div>"


# 보유 시장 → 국가별 보드(`world_sectors`)의 시장 이름. 표에 없는 시장(홍콩 등)은 하루치가 없다.
_PREV_MK = {"KR": "KR", "US": "US", "JP": "JP", "TW": "TW", "CN": "CN_A", "CN_A": "CN_A"}


_PREV_CACHE: dict = {}     # (시장, 날짜) → (파일 mtime, {티커: 등락}) — 15분마다 30일치를 다시 풀지 않게
# 화면에 싣는 날 × 시장 전부 + 여유 — 재생성은 30일을 매번 같은 순서로 훑으므로 상한이 그보다 작으면 FIFO 가
# 매 패스 전부 밀어내 15분마다 모두 다시 푼다(리뷰 2026-10-13).
_PREV_CACHE_MAX = RENDER_DAYS * len(set(_PREV_MK.values())) + 10


def _prev_table(ws, lookup_ticker, mk: str, d: str) -> dict | None:
    """그 날 하루치의 {티커: 등락%} — 파일이 없으면 None. 하루치 원본은 미국 한 장이 수 MB 라 재생성마다
    (지난 날 조각까지 30일 × 시장) 다시 풀면 낭비다 → mtime 으로 무효화하는 작은 표만 남긴다(#61). 국가별
    보드의 원본 캐시(`world_sectors._cached_load`, 6장)는 쓰지 않는다 — 30일을 훑으면 그 보드의 캐시를 밀어낸다."""
    path = ws.snap_path(mk, d)
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        return None
    hit = _PREV_CACHE.get((mk, d))
    if hit and hit[0] == mt:
        return hit[1]
    snap = ws._gz_load(path)
    rows = snap.get("rows") if isinstance(snap, dict) else None
    if not isinstance(rows, list):
        return None
    tbl = {}
    for r in rows:
        try:
            k = lookup_ticker(r[0]).upper()
            v = r[4]
        except Exception:                                      # noqa: BLE001
            continue
        if k and isinstance(v, (int, float)) and v == v:
            tbl[k] = float(v)
    while len(_PREV_CACHE) >= _PREV_CACHE_MAX:
        _PREV_CACHE.pop(next(iter(_PREV_CACHE)))
    _PREV_CACHE[(mk, d)] = (mt, tbl)
    return tbl


def prev_changes(date: str, now: datetime | None = None):
    """전날 등락률 조회기 — `look(stock) -> (등락%, 세션 날짜, 사유)`.

    값은 「국가별 심리지도」가 이미 받아 둔 TradingView 하루치(`world_sectors` 스냅샷)에서 읽는다 —
    디스크만, 네트워크 0(#38 같은 하루치를 두 번 받지 않는다). '전날' = 기록 날짜 **앞** 그 시장의 마지막
    거래일(`prev_sessions` — 휴장을 따른다). 그 하루치가 아직 없으면(미국은 한국 시각 이튿날 아침에
    받는다) 값 대신 사유를 낸다 — 다음 재생성이 채운다(#43). 실패는 이 칸만 비운다(#315·#424)."""
    memo: dict = {}

    def _table(mk: str):
        if mk in memo:
            return memo[mk]
        out = (None, None, "")
        try:
            from bot import world_sectors as ws
            from bot.world_sector_pages import lookup_ticker
            ss = ws.prev_sessions(mk, date, 1)
            if not ss:
                out = (None, None, "전날 거래일을 달력에서 못 찾음")
            else:
                tbl = _prev_table(ws, lookup_ticker, mk, ss[0])
                out = (tbl, ss[0], "") if tbl is not None else (None, ss[0], _prev_missing_why(ws, mk, ss[0], now))
        except Exception as exc:                               # noqa: BLE001
            log.warning("holdings_news: 전날 등락 조회 실패(%s %s): %s", mk, date, exc)
            out = (None, None, "전날 등락 조회 실패")
        memo[mk] = out
        return out

    def look(st: dict) -> tuple[float | None, str | None, str]:
        mk = _PREV_MK.get(str(st.get("market") or "").upper())
        if not mk:
            return None, None, f"{st.get('market')} 시장은 전날 등락 원천이 없음"
        tbl, d, why_ = _table(mk)
        if tbl is None:
            return None, d, why_
        key = (st["ticker"] if mk == "KR" else (st.get("sym") or st["ticker"])).upper()
        if mk == "US":
            key = key.replace(".", "-")        # 표 키는 `lookup_ticker` 꼴(BRK.B → BRK-B)
        v = tbl.get(key)
        if v is None:
            return None, d, f"{d} 하루치에 이 종목이 없음"
        return v, d, ""

    return look


def _prev_missing_why(ws, mk: str, d: str, now) -> str:
    """하루치가 없는 갈래 — 국가별 보드의 판정(`market_day_status`)을 그대로 쓴다(#35·#38). '아직' 은 정말
    받을 창이 남았을 때만 — 창을 놓친 날·보관 기간 밖 날에 '다음 갱신에 채워짐' 이라 적으면 영원히 안 지켜지는
    약속이다(#380, 리뷰 2026-10-13)."""
    try:
        st = ws.market_day_status(mk, d, _now(now))
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: 전날 하루치 상태 판정 실패(%s %s): %s", mk, d, exc)
        st = ""
    if st == "pending":
        return f"{d} 하루치를 아직 받지 않음(장 마감 {ws.READY_MIN // 60}시간 뒤 수집 — 받으면 다음 갱신에 채워짐)"
    if st == "missing":
        return f"{d} 하루치를 받을 창을 놓쳐 이 날은 비어 있음(원천은 '지금' 값만 줌)"
    if st in ("holiday", "closed"):
        return f"{d} 휴장(추정)이라 하루치가 없음"
    if st == "ok":
        return f"{d} 원본 하루치가 보관 기간({ws.KEEP_SNAP_DAYS}일) 밖이거나 없음"
    return f"{d} 하루치가 없음"


def _prev_html(prev, live: str = "") -> str:
    """종목명 옆 `(+1.23%)` — 값이 없으면 `(—)` 에 사유를 툴팁으로(빈칸이 '0' 으로 읽히지 않게, #43).

    `live` = 실시간 등락을 받을 티커(최신일 카드만, 사용자 2026-10-13 "가능한 실시간으로"). 화면이
    `live_pct.LIVE_JS` 로 1분마다 덮어쓰고, 못 받으면 서버가 그린 전날 등락이 그대로 남는다."""
    import html as _h
    lv = (f" js-live-pct' data-t='{_h.escape(live)}' data-cu='hn-up' data-cd='hn-dn" if live else "")
    if not prev:
        return f"<span class='hn-chg{lv}' title='실시간 등락 대기'></span>" if live else ""
    v, d, why_ = prev
    if v is None:
        return f"<span class='hn-chg{lv}' title='{_h.escape(why_ or '전날 등락 없음')}'>(—)</span>"
    cls = "hn-up" if v > 0 else "hn-dn" if v < 0 else ""
    sign = "+" if v > 0 else ""
    tip = f"{d} 종가 기준 등락률 · TradingView(「국가별 심리지도」와 같은 하루치)"
    return (f"<span class='hn-chg {cls}{lv}' title='{_h.escape(tip)}'>({sign}{v:.2f}%)</span>")


def live_ticker(s: dict) -> str:
    """실시간 등락을 물을 티커 — 카드 링크와 같은 꼴(`_lookup`, 국내 6자리는 접미 없이 — `live_pct.market_of`
    가 국내로 읽는다)."""
    return _lookup(s).split("/", 1)[1]


def _hm_after(minutes: int) -> str:
    """`RUN` 시각 ~ 그 뒤 `minutes` 분 — 가이드의 '실제 시작' 구간(리터럴로 적으면 RUN 을 바꿀 때 갈린다)."""
    a = RUN_HOUR * 60 + RUN_MIN
    b = a + minutes
    return f"{a // 60:02d}:{a % 60:02d}~{b // 60 % 24:02d}:{b % 60:02d}"


def _pending_reason(s: dict) -> str:
    """아직 이어서 정리하는 날의 대기 사유 — 일시 오류면 '다음 주기에 다시' 를 여기서만 붙인다(저장본엔 없다)."""
    r = s.get("reason") or ""
    if not r:
        return "다음 주기에 정리"
    return f"{r} — 다음 주기에 다시" if s.get("retry") else r


def _card(s: dict, *, final: bool = False, prev=None, live: bool = False) -> str:
    """종목 카드. `final` = 주기가 더는 이어서 정리하지 않는 날(`_live_dates` 밖) — 그날의 '대기' 를 '다음
    주기에' 라고 적으면 지킬 수 없는 약속이다(리뷰 2026-10-12)."""
    import html as _h
    items = s.get("items") or []
    rel = s.get("relevant")
    relset = set(rel) if isinstance(rel, list) else {it["n"] for it in items}
    n_tg = sum(1 for it in items if it["src"] == "telegram")
    st = s.get("status")
    drop = int(s.get("dropped_lines") or 0)
    if st == "ok":
        if not s.get("pros") and not s.get("cons"):
            why = (f"AI 가 쓴 {drop}줄을 근거 번호가 없거나 맞지 않아 버림 — 아래 근거 목록에서 확인하세요"
                   if drop else
                   "관련 있다고 본 항목이 없음 — 아래 근거 목록에서 확인하세요" if not relset else
                   "뉴스는 있지만 호재·악재로 가를 내용이 없음")
            pc = f"<div class='hn-why'>{_h.escape(why)}</div>"
        else:
            pc = ("<div class='hn-pc'>" + _side("👍 Pros · 호재 관점", "hn-pro", s.get("pros") or [])
                  + _side("👎 Cons · 악재·리스크 관점", "hn-con", s.get("cons") or []) + "</div>"
                  + (f"<div class='hn-why'>근거 번호가 없거나 맞지 않아 버린 줄 {drop}개</div>" if drop else ""))
    elif st == "failed":
        pc = (f"<div class='hn-why'>Pros/Cons 못 만듦({MAX_ANALYZE_TRIES}회 실패) — "
              f"{_h.escape(s.get('reason') or '사유 미기록')} · 뉴스만 표시</div>")
    elif final:
        pc = (f"<div class='hn-why'>Pros/Cons 정리를 마치지 못함 — "
              f"{_h.escape(s.get('reason') or '사유 미기록')} · 뉴스만 표시</div>")
    else:
        pc = f"<div class='hn-why'>Pros/Cons 대기 — {_h.escape(_pending_reason(s))}</div>"
    lis = "".join(_item_li(it, it["n"] not in relset) for it in items)
    off = len(items) - len([it for it in items if it["n"] in relset])
    offnote = f" · 그 회사 얘기가 아니라고 본 {off}건은 흐리게" if off and st == "ok" else ""
    q = f"{s['name']} {s['ticker']}" + (f" {s['ko']}" if s.get("ko") else "")
    return (f"<div class='hn-card' data-hncard='1' data-mk='{_h.escape(s['market'])}' "
            f"data-pro='{1 if s.get('pros') else 0}' data-con='{1 if s.get('cons') else 0}' "
            f"data-tg='{1 if n_tg else 0}' data-q='{_h.escape(q)}'>"
            f"<div class='hn-h'><a class='hn-nm' href='{_h.escape(_lookup(s))}' title='종목분석 화면으로'>"
            f"{_h.escape(s['name'])}</a>{_prev_html(prev, live_ticker(s) if live else '')}"
            f"<span class='hn-tk'>{_h.escape(s['ticker'])} · 보유 {_krw(float(s.get('value') or 0))}"
            f" · 뉴스 {len(items) - n_tg}건{f' · 텔레그램 {n_tg}건' if n_tg else ''}</span></div>"
            f"{pc}<details class='hn-src'><summary>근거 {len(items)}건{offnote}</summary>"
            f"<ol>{lis}</ol></details></div>")


def _day_meta(rec: dict) -> str:
    w = rec.get("window") or {}
    a, b = _parse(w.get("since")), _parse(w.get("until"))
    parts = []
    if a and b:
        parts.append(f"뉴스 창 {a.strftime('%m-%d %H:%M')} ~ {b.strftime('%m-%d %H:%M')}")
    src = rec.get("sources") or {}
    got = [f"{k} {src[k]}" for k in ("naver", "yfinance", "google") if src.get(k)]
    if got:
        parts.append("원천 " + " · ".join(got))
    for why, n in (src.get("fallback") or {}).items():
        parts.append(f"대체 원천으로 받은 종목 {n}({why})")
    if src.get("fail"):
        why = next(iter(src.get("fail_reasons") or {}), "")
        parts.append(f"원천 실패 {src['fail']}종목({why})" if why else f"원천 실패 {src['fail']}종목")
    if src.get("body_try"):
        parts.append(f"본문 읽음 {src.get('body_ok', 0)}/{src['body_try']}")
    if src.get("dropped_name"):
        parts.append(f"제목·요약에 이름이 없어 뺀 검색 결과 {src['dropped_name']}건")
    if src.get("ko_try"):
        parts.append(f"영문 이름 국내 종목 한글명 확인 {src.get('ko_ok', 0)}/{src['ko_try']}")
    u = rec.get("universe") or {}
    if u.get("us_etf_n") == 0 and any(s.get("market") == "US" for s in rec.get("stocks") or []):
        parts.append("미국 ETF 목록을 못 받아 ETF 가 회사로 섞였을 수 있음")
    tg = src.get("telegram") or {}
    parts.append(f"텔레그램 창 안 {tg.get('window', 0)}건 중 보유 종목 언급 {tg.get('matched', 0)}건")
    llm = rec.get("llm") or {}
    if llm.get("calls"):
        parts.append(f"AI {llm['calls']}회 · ${llm.get('cost_usd', 0):.4f}")
    return " · ".join(parts)


def _day_summary(rec: dict) -> str:
    import html as _h
    d = datetime.fromisoformat(rec["date"])
    ss = rec.get("stocks") or []
    n_news = len([s for s in ss if s.get("items")])
    return (f"<summary>📅 {rec['date']} ({_WD[d.weekday()]}) · 뉴스 {n_news}/{len(ss)}종목"
            f"<span class='hn-meta'>{_h.escape(_day_meta(rec))}</span></summary>")


def _day_body(rec: dict, *, final: bool = False, now: datetime | None = None, live: bool = False) -> str:
    """하루 칸의 본문(카드 · 새 뉴스 없음 · 뺀 상품) — 최신일은 페이지에, 지난 날은 조각 파일에 같은
    함수로 싣는다(#38). `final` → `_card`."""
    import html as _h
    ss = rec.get("stocks") or []
    with_news = [s for s in ss if s.get("items")]
    quiet = [s for s in ss if not s.get("items")]
    look = prev_changes(rec["date"], now)
    cards = "".join(_card(s, final=final, prev=look(s), live=live) for s in with_news)
    quiet_html = ""
    if quiet:
        names = ", ".join(_h.escape(s["name"]) for s in quiet[:60])
        more = f" 외 {len(quiet) - 60}종목" if len(quiet) > 60 else ""
        quiet_html = (f"<div class='hn-quiet'>새 뉴스 없음 {len(quiet)}종목: {names}{more}</div>")
    u = rec.get("universe") or {}
    skip = []
    if u.get("etf"):
        skip.append(f"ETF {len(u['etf'])}개")
    if u.get("unmatched"):
        skip.append(f"티커 미확인 {len(u['unmatched'])}개")
    skip_html = (f"<div class='hn-quiet'>뉴스로 가를 회사가 아니라 뺀 보유 상품: {', '.join(skip)}"
                 f" — {_h.escape(', '.join((u.get('etf') or []) + (u.get('unmatched') or []))[:300])}"
                 "</div>") if skip else ""
    body = (f"<div class='hn-grid'>{cards}</div>" if cards else
            "<div class='hn-quiet'>이 창에 새 뉴스가 있는 보유 종목이 없습니다.</div>")
    return f"{body}{quiet_html}{skip_html}"


def _day_html(rec: dict, *, open_: bool, final: bool = False, now: datetime | None = None,
              live: bool = False) -> str:
    return (f"<details class='hn-day' data-hnday='1' data-open='{1 if open_ else 0}'"
            f"{' open' if open_ else ''}>{_day_summary(rec)}"
            f"{_day_body(rec, final=final, now=now, live=live)}</details>")


def frag_name(date: str) -> str:
    return f"{FRAG_PREFIX}{date}.html"


def _day_lazy(rec: dict) -> str:
    """지난 날 — 머리만 싣고 본문은 펼칠 때 조각 파일에서 불러온다. 보유 300여 종목이면 하루 칸이 약
    0.7MB 라 30일을 한 페이지에 싣으면 약 21MB 가 된다(2026-10-12 합성 기록으로 실측 — 종목분석
    index 의 지난 달 조각과 같은 처방)."""
    return (f"<details class='hn-day' data-hnday='1' data-open='0' data-lazy='{frag_name(rec['date'])}'>"
            f"{_day_summary(rec)}<div class='hn-lazy'>펼치면 불러옵니다…</div></details>")


def day_fragments(records: list[dict], now: datetime) -> dict[str, str]:
    """{조각 파일 이름: 본문} — 화면에 싣는 날 중 최신일을 뺀 나머지(최신일은 페이지 본문에 있다). `now` 는
    필수 — 어느 날이 '마치지 못함' 인지 오늘에 달렸다(기본값을 두면 테스트가 날짜에 묶인다, #425)."""
    recs = sorted(records, key=lambda r: r["date"], reverse=True)[:RENDER_DAYS]
    live = _live_dates([r["date"] for r in records], _now(now).date().isoformat())
    return {frag_name(r["date"]): _day_body(r, final=r["date"] not in live, now=now) for r in recs[1:]}


def _cost_line(now: datetime) -> str:
    """오늘 / 이번 달 / 누적(KST) — 비용 카드와 같은 3창·같은 환율(`usage_tracker.KRW_PER_USD`).
    usage.jsonl 을 **한 번만** 읽는다(15분마다 재생성한다). 누적 = 파일 합 + 30일 로테이션으로 빠진 이
    보드 몫(`rollup_cost_by_subsystem`) — 파일 합만이면 '누적' 이 조용히 '최근 30일' 이 된다(리뷰 2026-10-12,
    메인 비용 카드와 같은 정의)."""
    try:
        from bot.usage_tracker import KRW_PER_USD, USAGE_LOG, rollup_cost_by_subsystem
        t0 = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        m0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        tot = [0.0, 0.0, rollup_cost_by_subsystem().get(SUBSYSTEM, 0.0)]
        with open(USAGE_LOG, encoding="utf-8") as fh:
            for line in fh:
                if SUBSYSTEM not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") != "llm_call" or r.get("subsystem") != SUBSYSTEM:
                    continue
                c, ts = float(r.get("cost_usd") or 0), float(r.get("ts") or 0)
                tot[2] += c
                tot[1] += c if ts >= m0 else 0
                tot[0] += c if ts >= t0 else 0
        a, b, c = (int(round(x * KRW_PER_USD)) for x in tot)
        return f"₩{a:,} / ₩{b:,} / ₩{c:,}"
    except FileNotFoundError:
        try:
            from bot.usage_tracker import KRW_PER_USD, rollup_cost_by_subsystem
            return f"₩0 / ₩0 / ₩{int(round(rollup_cost_by_subsystem().get(SUBSYSTEM, 0.0) * KRW_PER_USD)):,}"
        except Exception:                                      # noqa: BLE001
            return "₩0 / ₩0 / ₩0"
    except Exception:                                          # noqa: BLE001
        return "—"


def render_page(records: list[dict], state: dict | None = None,
                now: datetime | None = None, *, gen: str | None = None, total: int | None = None) -> str:
    """`records` = 화면에 실을 날들(최근 `RENDER_DAYS` 일이면 충분 — 그 앞은 안 그린다) · `total` = 보관 중인
    날 수(`count_records`, 없으면 `records` 수)."""
    from bot.live_pct import LIVE_JS as _LIVE_JS
    import html as _h

    from bot.fred_boards import _BOARD_CSS, _theme_head
    now = _now(now)
    allrecs = sorted(records, key=lambda r: r["date"], reverse=True)
    recs = allrecs[:RENDER_DAYS]
    n_total = max(len(allrecs), total or 0)
    live = _live_dates([r["date"] for r in allrecs], now.date().isoformat())
    kind, line = status_verdict(allrecs, now, state)
    latest = recs[0] if recs else None
    n_news = len([s for s in (latest or {}).get("stocks") or [] if s.get("items")])
    n_all = len((latest or {}).get("stocks") or [])
    tg = ((latest or {}).get("sources") or {}).get("telegram") or {}
    stats = (
        "<div class='stat-grid'>"
        f"<div class='stat'><div class='k'>최신 기준일</div><div class='v'>"
        f"{latest['date'] if latest else '—'}</div></div>"
        f"<div class='stat'><div class='k'>뉴스 있는 종목 / 보유 종목</div><div class='v'>"
        f"{n_news} / {n_all}</div></div>"
        f"<div class='stat'><div class='k'>텔레그램 언급(최신일)</div><div class='v'>"
        f"{tg.get('matched', 0)}건</div></div>"
        f"<div class='stat'><div class='k'>AI 비용 (오늘 / 이번 달 / 누적)</div><div class='v'>"
        f"{_h.escape(_cost_line(now))}</div></div>"
        "</div>")
    days = "".join(_day_html(r, open_=True, final=r["date"] not in live, now=now, live=True) if i == 0
                   else _day_lazy(r)
                   for i, r in enumerate(recs)) or (
        f"<div class='hn-status'>아직 기록이 없습니다 — 매일 {RUN_HOUR:02d}:{RUN_MIN:02d} KST 이후 "
        "자동으로 모읍니다.</div>")
    shown = (f"총 {n_total}일 기록" if n_total <= RENDER_DAYS else
             f"총 {n_total}일 기록 중 최근 {RENDER_DAYS}일 표시(그 앞은 보관만)")
    gen_s = now.strftime("%Y-%m-%d %H:%M KST") if gen is None else gen
    pills = "".join(
        f"<span class='pill{' active' if k == 'all' else ''}' data-hnf='{k}'>{lab}</span>"
        for k, lab in (("all", "전체"), ("KR", "🇰🇷 국내"), ("XX", "🌐 해외"), ("pro", "👍 Pros 있음"),
                       ("con", "👎 Cons 있음"), ("tg", "📨 텔레그램 언급")))
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>보유종목 News</title>{_BOARD_CSS}{_HN_CSS}
{_theme_head()}
</head><body><div class="wrap">
<div class="nav"><a href="market.html">🌍 홈</a> · <a href="asia.html">🏯 ASIA</a> · <a href="portfolio.html">💼 자산</a> · <a href="index.html">🦉 종목분석</a> · <a href="dart_feed.html">📋 DART</a> · <a href="daily_byte.html">📰 Daily Byte</a></div>
<h1>🗞️ <em>보유종목 News</em> — 보유 종목 뉴스 · Pros/Cons</h1>
<div class="sub">자산 대시보드 보유 종목 × 네이버 뉴스(국내) · Yahoo Finance(해외) · 텔레그램 'Noah의 RAG'
채널 · 매일 {RUN_HOUR:02d}:{RUN_MIN:02d} KST 이후 직전 집계 이후 뉴스를 모아 AI(Gemini flash-lite)가 그
문장만 근거로 Pros/Cons 를 정리 · 내용이 바뀐 시각 {_h.escape(gen_s)}</div>
<div class="hn-status" data-kind="{kind}">{_h.escape(line)}</div>
{stats}
<details class="guide"><summary>ℹ️ 이 보드 읽는 법</summary>
<b>종목</b> — 💼 자산 대시보드와 같은 보유 기록입니다. 여러 증권사에 나눠 가진 종목은 하나로 합치고,
평가금액이 큰 종목부터 보여 줍니다. ETF(국내는 자산 기록이 ETF 로 알아본 상품, 미국은 거래소 목록의 ETF
표시)와 티커를 못 찾은 상품은 '회사' 뉴스로 가를 수 없어 빼고, 그 이름을 날짜 칸 맨 아래에 적습니다(일본·홍콩·
대만 ETF 는 가를 목록이 없어 회사로 셉니다).<br>
<b>카드 머리</b> — 종목명을 누르면 종목분석 화면으로 갑니다. 옆 괄호는 맨 위(최신) 날짜 카드에서
<b>실시간 등락률</b>(정규장 · 직전 종가 대비 — 국내 네이버 · 대만 TWSE · 미·일·홍·중 네이버 해외 · 장 중이면 지금 값,
장이 끝났으면 마지막 세션 값 · 장전·장후 시간외 등락은 넣지 않습니다)이고 화면을 열어 둔 동안 1분마다
바뀝니다. 실시간을 못 받은 종목은 <b>전날(그 시장의 직전 거래일) 등락률</b>이 그대로 남습니다 — 「국가별
심리지도」가 받아 둔 TradingView 하루치(장 마감 3시간 뒤 수집)라 미국은 한국 시각 아침 8~9시에 받으므로 이른
아침엔 '(—)' 일 수 있고, 휴장이거나 그날을 받지 못했어도 '(—)' 입니다. 지난 날 카드의 괄호는 그날 기준
전날 등락 그대로입니다(마우스를 올리면 무엇의 값인지와 기준 시각).<br>
<b>뉴스</b> — 종목마다 최대 {MAX_NEWS_PER_STOCK}건입니다(원천이 최신순으로 주는 10건 안에서 — 두 이름으로
찾는 종목은 두 검색 결과를 합쳐서 — 기사가 많은 날은 최근 것만 담깁니다). 국내는 네이버 뉴스 검색(키가 없으면 구글 뉴스)으로 이름을 찾되, 제목·요약에
그 이름이 <b>낱말로</b> 나온 기사만 남깁니다(이름이 짧거나 흔하면 다른 회사 기사가 섞일 수 있어 AI 가 한 번 더
거릅니다). 자산 기록 이름이 영문인 국내 종목(NAVER·HYBE 등)은 DART 의 한글 이름과 영문 이름 둘 다로
검색합니다(DART 한글명이 '케이티' 같은 음역이면 기사는 영문으로 씁니다). 해외는
Yahoo Finance 의 그 종목 뉴스이고, 못 받거나 0건이면 구글 뉴스로 한 번 더 찾습니다. 같은 기사가 전날 기록에
이미 있으면 다시 싣지 않습니다. 네이버가 호스팅한 기사는 본문 앞부분({BODY_CHARS}자까지)을 읽어 AI 에 같이
넘기지만, 화면에는 원문 링크와 원천이 준 요약만 싣습니다.<br>
<b>텔레그램</b> — 'Noah의 RAG' 채널(무엇이든 포워드하면 Second Brain 이 학습하는 그 채널)에 올라온 글 중
보유 종목 이름이나 코드·티커가 <b>낱말로</b> 나온 글입니다. 여러 종목을 늘어놓은 글은 그 종목이 나온
대목만 AI 에 넘깁니다. 원 출처 채널 이름은 묻지 않습니다(텔레그램 요청 제한 때문) — 포워드였는지만
적습니다.<br>
<b>Pros/Cons</b> — AI 가 그 종목 항목에 <b>쓰인 사실만</b> 근거로 호재 관점·악재/리스크 관점을 최대
{MAX_PER_SIDE}줄씩 씁니다. 줄 끝 [번호]는 근거 항목이고, 근거 번호가 없거나 틀린 줄은 버립니다. 그 회사
얘기가 아니라고 AI 가 판단한 항목은 근거 목록에서 흐리게 보입니다. 이것은 뉴스 요약이지 투자 판단이
아닙니다.<br>
<b>언제 갱신되나</b> — 매일 {RUN_HOUR:02d}:{RUN_MIN:02d} KST 이후 한 번 모읍니다(장 시작 전에 읽도록 · 봇이 {TICK_MIN}분마다
확인하므로 보통 {_hm_after(TICK_MIN)} 사이에 시작합니다 — 앞선 정리 작업이 길면 그보다 늦을 수 있습니다).
창은 직전 집계 {WINDOW_OVERLAP_H}시간 전부터 지금까지입니다(직전 집계 뒤에 늦게 들어온 글을 놓치지 않으려고
겹치며, 이미 실린 항목은 빼고 싣습니다). 봇이 며칠 멈췄어도 {MAX_WINDOW_H}시간보다 오래된 뉴스는 싣지 않습니다.
AI 정리는 한 주기에 {MAX_LLM_CALLS}번까지 부르고 남으면 15분 뒤 이어서 합니다('대기' — 바로 전 날의 대기분도
이어서 정리하고, 그보다 앞선 날은 '마치지 못함' 으로 적습니다). AI 의 일시 오류(요청 한도·서버 장애·시간 초과)는
실패로 세지 않고 다음 주기에 다시 합니다. AI 가 답했는데 쓸 수 없는 경우가 같은 종목에서
{MAX_ANALYZE_TRIES}번이면 그날은 뉴스만 보여 줍니다.<br>
<b>표시 범위</b> — 최근 {RENDER_DAYS}일만 보여 주고 그 앞은 보관만 합니다. 가장 최근 날만 바로 보이고
지난 날은 펼칠 때 불러옵니다(보유 종목이 많으면 하루 칸이 무거워서). 검색·필터는 불러온 날에 걸리고, 아직
안 연 날은 펼치면 같이 걸러집니다(상태 줄이 몇 날이 남았는지 적습니다).
</details>
<div class="hn-tools"><input id="hn-q" type="text" placeholder="종목명·티커 검색" autocomplete="off" spellcheck="false"></div>
<div class="pills">{pills}</div>
<div id="hn-st" class="sub">{shown}</div>
{days}
<div class="footer">보유종목 News — 뉴스 정리(교육·정보), 투자 권유 아님 · NOAH</div>
</div>
<script>{_HN_JS}</script><script>{_LIVE_JS}</script>
</body></html>"""


def regenerate(now: datetime | None = None) -> bool:
    """holdings_news.html + 지난 날 조각 — 저장된 기록만 읽는다(네트워크 0). 페이지는 **내용이 바뀔
    때만** 쓴다(#442). 조각을 먼저 쓴다 — 새 페이지가 아직 없는 조각을 가리키지 않게. 조각도 내용이
    같으면 다시 쓰지 않고(#271), 화면 창 밖으로 밀려난 날의 조각은 지운다."""
    try:
        import hashlib

        from bot.dashboard import ARCHIVE_ROOT, _inject_update_banner
        now = _now(now)
        recs = load_records(limit=RENDER_DAYS)
        frags = day_fragments(recs, now)
        for name, html in frags.items():
            fp = ARCHIVE_ROOT / name
            try:
                if fp.read_text(encoding="utf-8") == html:
                    continue
            except OSError:
                pass
            _atomic_write(fp, html)
        for fp in ARCHIVE_ROOT.glob(f"{FRAG_PREFIX}*.html"):
            if fp.name not in frags:
                fp.unlink(missing_ok=True)
        body = _inject_update_banner(render_page(recs, load_state(), now, gen=_GEN, total=count_records()))
        sig = hashlib.sha1(body.encode("utf-8")).hexdigest()
        out = ARCHIVE_ROOT / "holdings_news.html"
        sig_path = ARCHIVE_ROOT / "holdings_news.html.sig"
        try:
            same = out.exists() and sig_path.read_text(encoding="utf-8").strip() == sig
        except Exception:                                      # noqa: BLE001
            same = False
        if same:
            return False
        _atomic_write(out, body.replace(_GEN, now.strftime("%Y-%m-%d %H:%M KST")))
        sig_path.write_text(sig, encoding="utf-8")
        return True
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: 페이지 재생성 실패: %s", exc)
        return False


# ── 진단 ─────────────────────────────────────────────────────────────────────
def _src_sig() -> str:
    try:
        import hashlib
        return hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:10]
    except Exception:                                          # noqa: BLE001
        return "지문불가"


def why(now: datetime | None = None, out=print) -> int:
    """읽기 전용(네트워크·LLM 0, 아무것도 안 씀). 이상 없을 때의 끝 줄: `판정: ✅ …`."""
    now = _now(now)
    out(f"# holdings_news --why · 지문 {_src_sig()} · {now.strftime('%Y-%m-%d %H:%M KST')}")
    etfs = _us_etf(cache_only=True)          # 캐시만 — 읽기 전용(네트워크 0)
    stocks, skipped = universe(us_etf=etfs)
    n_kr = sum(1 for s in stocks if s["market"] == "KR")
    out(f"① 보유 종목: {len(stocks)}종목(국내 {n_kr} · 해외 {len(stocks) - n_kr}) · 자산 기록 기준 "
        f"{skipped.get('as_of') or '—'} · 뺀 상품 ETF {len(skipped['etf'])} · 티커 미확인 "
        f"{len(skipped['unmatched'])} · 미국 ETF 목록 "
        + (f"{len(etfs):,}종(캐시)" if etfs else "캐시 없음(다음 수집이 받는다 — 그 전엔 미국 ETF 를 못 가름)"))
    from bot.env_keys import env_key
    from bot.genai_factory import effective_key
    out(f"② 원천 준비: 네이버 검색 키 {'있음' if env_key('NAVER_CLIENT_ID') and env_key('NAVER_CLIENT_SECRET') else '없음 → 국내도 구글 뉴스'}"
        f" · AI 키 {'있음' if effective_key() else '없음 → Pros/Cons 없이 뉴스만'}")
    recs_tg, bad = _tg_read()
    last = recs_tg[-1]["ts"][:16].replace("T", " ") if recs_tg else "—"
    chan = (os.environ.get("TG_RAG_CHANNEL") or "").strip()
    out(f"③ 텔레그램 수신함: {len(recs_tg)}건(최근 {TG_KEEP_DAYS}일 보관) · 마지막 {last}"
        + (f" · 못 읽은 줄 {bad}" if bad else "")
        + ("" if chan else " · ⚠️ TG_RAG_CHANNEL 미설정(portfolio_watch 가 채널을 못 읽는다)")
        + ("" if recs_tg else " — 비었으면 portfolio_watch 타이머가 새 판으로 돌았는지 확인"
           "(systemctl status portfolio-watch.timer)"))
    recs = load_records(limit=3)
    st = load_state()
    if recs:
        r = max(recs, key=lambda r: r["date"])
        llm = r.get("llm") or {}
        out(f"④ 마지막 기록 {r['date']} · 수집 {str(r.get('collected_at', ''))[:16]} · {_day_meta(r)}")
        out(f"   AI {llm.get('calls', 0)}회 · 입력 {llm.get('pt', 0):,} · 출력 {llm.get('ot', 0):,} 토큰 · "
            f"${llm.get('cost_usd', 0):.4f}")
    else:
        out("④ 기록 없음 — 봇이 아직 한 번도 모으지 않음")
    kind, line = status_verdict(recs, now, st)
    mark = {"ok": "✅", "wait": "⏳", "pending": "⏳", "fail": "❌", "none": "❓"}.get(kind, "❓")
    out(f"판정: {mark} {line}")
    return 1 if kind == "fail" else 0


def probe(tickers: list[str] | None = None, out=print) -> int:
    """원천만 받아 본다(LLM 0 · 이 보드의 기록·상태·수신함은 안 쓴다 — 네이버·구글 뉴스 클라이언트의
    공용 디스크 캐시는 평소처럼 쓴다) — 종목 3개(인자 없으면 보유 상위 국내 2 · 해외 1)."""
    out(f"# holdings_news --probe · 지문 {_src_sig()} · 이 보드 기록·AI 없음(뉴스 공용 캐시는 씀)")
    stocks, _ = universe(us_etf=_us_etf())
    pick = []
    if tickers:
        want = {t.upper() for t in tickers}
        pick = [s for s in stocks if s["ticker"].upper() in want]
        for t in want - {s["ticker"].upper() for s in pick}:
            mk = "KR" if re.fullmatch(r"\d{6}", t) else "US"
            pick.append({"key": f"{mk}:{t}", "market": mk, "ticker": t, "name": t, "value": 0.0})
    else:
        pick = [s for s in stocks if s["market"] == "KR"][:2] + [s for s in stocks if s["market"] != "KR"][:1]
    if not pick:
        out("❓ 보유 종목이 없어 잴 대상이 없음(portfolio.json)")
        return 2
    ok = 0
    for s in pick:
        s = with_ko(s)                              # 수집과 같은 검색어(#145)
        items, used, why_ = fetch_stock_news(s)
        out(f"── {s['name']} ({s['key']}) · 검색어 {search_name(s)!r}"
            + (f"(DART 한글명 {s['ko']!r})" if s.get("ko") else
               " · ⚠️ 영문 이름인데 DART 한글명을 못 얻음" if "ko" in s else "")
            + (f" + 영문 {alt_query(s)!r}" if alt_query(s) else "")
            + f" · 원천 {used or '없음'}"
            + (f" · 실패 {why_}" if why_ else "") + f" · {len(items)}건")
        for it in items[:3]:
            out(f"   {it.get('ts') or it.get('date') or '?'} | {it.get('source', '')} | "
                f"{str(it.get('title', ''))[:70]} | {str(it.get('link', ''))[:80]}")
        nv = [it.get("naver_link") or it.get("link") for it in items
              if "news.naver.com" in str(it.get("naver_link") or it.get("link") or "")]
        if nv:
            b = _fetch_body(nv[0])
            out(f"   본문(네이버 호스팅 1건): {len(b)}자" + (f" · {b[:80]}…" if b else
                                                       " — dic_area 를 못 찾음(화면 구조 변경?)"))
        ok += bool(used)
    out(("✅" if ok == len(pick) else "⚠️") + f" {ok}/{len(pick)}종목 원천 응답")
    return 0 if ok == len(pick) else 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="python -m bot.holdings_news")
    p.add_argument("--why", action="store_true", help="상태 진단(읽기 전용)")
    p.add_argument("--probe", nargs="*", metavar="TICKER",
                   help="원천만 받아 보기(이 보드 기록·AI 없음 — 뉴스 공용 캐시는 씀)")
    p.add_argument("--run", action="store_true", help="지금 한 주기(AI 비용이 든다)")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    except Exception:                                          # noqa: BLE001
        pass
    if a.probe is not None:
        return probe(a.probe)
    if a.run:
        print("⚠️ AI(Gemini) 호출 비용이 듭니다 — 오늘 기록이 이미 있으면 대기 종목만 정리합니다.")
        print(json.dumps(tick_and_render(), ensure_ascii=False))
        return 0
    return why()


if __name__ == "__main__":
    sys.exit(main())
