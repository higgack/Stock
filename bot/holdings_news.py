"""🗞️ 보유종목 News — 자산 대시보드 보유 종목별 하루 뉴스 + Pros/Cons (사용자 2026-10-12).

무엇을 하나
- 종목: 자산 대시보드와 **같은 기록**(`bot.portfolio.load()` = portfolio.json)의 보유 종목. 증권사가
  달라도 티커가 같으면 한 종목으로 합치고 평가금액을 더한다(화면 정렬 = 평가금액 큰 순). 티커를
  못 찾은 상품·ETF(국내 = 자산 기록 판정 · 미국 = 거래소 목록의 ETF 칸) 는 뉴스로 가를 '회사' 가 아니라
  빼고, 몇 개를 왜 뺐는지 화면이 적는다(#43·#45).
- 뉴스(매일 `RUN_HOUR`:`RUN_MIN` KST, 직전 집계 `WINDOW_OVERLAP_H` 시간 전 ~ 지금 · 상한 `MAX_WINDOW_H`
  시간 — 겹친 구간의 이미 실린 항목은 뺀다. 같은 날 `TOPUP_HOUR`:`TOPUP_MIN` 에 한 번 더 모아 새 항목만 그날 기록에
  붙인다 — `top_up`):
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
  없거나 틀린 줄은 버린다(데이터 vs 환각 — 지어낸 문장을 싣지 않는다). 그 회사 얘기가 아니거나 회사
  이름만 나오고 투자와 무관한 항목(이름을 단 스포츠 구단의 경기 소식 등)은 AI 가 relevant 에서 빼고,
  화면은 뺀 항목도 흐리게 따로 보여 준다(#43). 정리 규칙(프롬프트 틀)의 지문을 종목에 찍어, 규칙이 바뀌면
  아직 이어서 정리하는 날의 종목은 다시 정리한다(`requeue_stale`). 비용은 usage.jsonl 에
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
import math
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
RUN_HOUR, RUN_MIN = 7, 0           # 매일 이 시각(KST) 이후 하루치를 모은다 — 장 시작 전에 읽게(사용자 2026-10-15 08:00→07:00
#                                     "8시면 9시 개장에 너무 촉박")
TOPUP_HOUR, TOPUP_MIN = 19, 0      # 같은 날 이 시각(KST) 이후 한 번 더 모아 그날 기록에 붙인다(사용자 2026-10-19 "하루에 두번
#                                     12시간 간격") — 아침 창 끝 이후의 새 뉴스만, 새 뉴스가 붙은 종목만 다시 정리
TOPUP_RETRY_MIN = 60               # 저녁 보강이 예외·원천 장애로 끝났을 때 다시 시도하기까지(매 주기 전 종목을 다시 받지 않게)
TOPUP_MAX_TRIES = 3                # 원천 장애로 끝난 저녁 보강을 그날 몇 번까지 하나(넘으면 다음 날 아침 수집이 그 시간대를 덮는다)
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
        out |= _rec_keys(r)
    return out


def _rec_keys(r: dict) -> set:
    """한 기록에 이미 실린 항목의 대조 키 — 앞선 날(`_seen_keys`)과 같은 날 저녁 보강(`top_up`)이 같이 쓴다(#38)."""
    out = set()
    for s in r.get("stocks") or []:
        for it in s.get("items") or []:
            out.add((s.get("key"), it.get("k")))
            if it.get("src") != "telegram" and _norm_title(it.get("title")):
                # 같은 기사라도 원천마다 링크가 달라 `k` 가 다르다(네이버·구글 리다이렉트) — 제목으로도 맞춘다.
                out.add((s.get("key"), "t:" + _norm_title(it.get("title"))))
    return out


def _item_key(it: dict) -> str:
    if it.get("src") == "telegram":
        return f"tg:{it.get('chan')}:{it.get('id')}"
    return it.get("link") or ("t:" + _norm_title(it.get("title")))


def collect(now: datetime | None = None, *, model: dict | None = None,
            records: list[dict] | None = None, fetch=fetch_stock_news,
            body=_fetch_body, pace: float = 0.25, us_etf: set | None = None, ko=None,
            beat=None, only: set | None = None, since_at: datetime | None = None,
            seen_extra: set | None = None, since_by_key: dict | None = None) -> dict | None:
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
    if only is not None:                 # 낮에 새로 생긴 보유만(`add_new_holdings`) — 창·규칙은 아침 수집과 같다
        stocks = [s for s in stocks if s["key"] in only]
    if not stocks:
        return None
    recs = load_records(limit=3) if records is None else records
    date = now.date().isoformat()
    prev_until, prev_miss = None, {}
    for r in sorted(recs, key=lambda r: r["date"], reverse=True):
        if r.get("date", "") < date:
            prev_until = _parse((r.get("window") or {}).get("until"))
            pm = (r.get("window") or {}).get("miss")
            prev_miss = pm if isinstance(pm, dict) else {}
            break
    since = (prev_until - timedelta(hours=WINDOW_OVERLAP_H)) if prev_until else \
        (now - timedelta(hours=DEFAULT_WINDOW_H))
    since = max(since, now - timedelta(hours=MAX_WINDOW_H))
    if since_at is not None:             # 낮 추가 수집·저녁 보강 — 부른 쪽이 창 시작을 정한다
        since = since_at
    # 앞선 수집에서 원천을 못 받은 종목(`window.miss` = {키: 그때 열려던 창 시작}) — 그 종목만 거기서 연다(독립 리뷰
    # H1). 아침 수집은 직전 기록의 것을, 저녁 보강은 오늘 기록의 것을 부른 쪽이 넘긴다. 이번에도 못 받으면 같은 시작을
    # 이 기록의 `window.miss` 로 넘긴다 — 두 번 연달아 실패해도 구간이 사라지지 않는다(상한 `MAX_WINDOW_H`).
    if since_by_key is None:
        since_by_key = {}
        for k, v in (prev_miss.items() if since_at is None else ()):
            d = _parse(v)
            if d is not None:
                since_by_key[k] = d
    floor = now - timedelta(hours=MAX_WINDOW_H)
    since_by_key = {k: max(min(since, d), floor) for k, d in since_by_key.items() if isinstance(d, datetime)}
    miss_out: dict[str, str] = {}
    until = now
    seen = _seen_keys(recs, date) | (seen_extra or set())     # 저녁 보강 — 같은 날 아침에 실린 항목도 뺀다
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
        since_k = since_by_key.get(st["key"], since)
        for it in raw or []:
            if not _in_window(it, since_k, until):
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
            if _norm_title(item["title"]) and (st["key"], "t:" + _norm_title(item["title"])) in seen:
                continue                                        # 앞선 기록에 같은 제목 — 링크만 다른 재배포
            items.append(item)
            if len(items) >= MAX_NEWS_PER_STOCK:
                break
        src["dropped_name"] += dropped
        if not used and why:             # 원천을 못 받았다 — 다음 수집이 이 종목만 이 창 시작에서 다시 연다
            miss_out[st["key"]] = since_k.isoformat(timespec="seconds")
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
                                     "until": until.isoformat(timespec="seconds"),
                                     **({"miss": miss_out} if miss_out else {})},
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
    ind = f" · 업종: {s['ind']}" if s.get("ind") else ""
    lines = [f"### key={s['key']} · {nm}({s['ticker']}, {mk}){ind}"]
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


# 투자 판단에 닿는 주제 — 규칙 2(Pros·Cons)와 규칙 5(relevant)·가이드가 같이 쓴다. 두 규칙이 목록을 따로 들면
# relevant 에서 빠진 항목을 근거로 단 줄이 `_clean_side` 에서 조용히 버려진다(리뷰 2026-10-13, #38).
INVEST_TOPICS = "주가·실적·사업·재무·지배구조·규제·소송·업황"


def build_prompt(stocks: list[dict]) -> str:
    return (
        "너는 개인 투자자의 보유 종목 뉴스를 정리하는 보조다. 아래 규칙을 어기면 실패다.\n"
        "1) 각 종목 아래 [번호] 항목에 실제로 쓰인 사실만 근거로 쓴다. 항목에 없는 사실·수치·전망을\n"
        "   지어내지 않는다. 수치는 항목에 있는 것만 인용한다.\n"
        "2) pros = 그 종목 주주에게 긍정적으로 읽힐 수 있는 점(호재 관점). cons = 부정적이거나 위험이\n"
        "   될 수 있는 점(악재·리스크 관점). 같은 사실이 양면이면 양쪽에 다른 관점으로 써도 된다.\n"
        f"   둘 다 규칙 5 의 relevant 항목만 근거로, 그 회사의 {INVEST_TOPICS}처럼 투자 판단에 닿는 내용만 쓴다.\n"
        "3) 각 줄은 한국어 1문장 70자 이내, 근거 항목 번호를 refs 에 단다(예: [1,3]).\n"
        f"4) 근거가 없으면 빈 배열로 둔다. 개수를 맞추려고 일반론('변동성 주의' 등)을 넣지 않는다.\n"
        f"   pros·cons 각각 최대 {MAX_PER_SIDE}개.\n"
        f"5) relevant 에는 그 회사의 {INVEST_TOPICS}처럼 투자 판단에 닿는 항목만 넣는다. 넣지 않는 것:\n"
        "   이름이 같은 다른 회사 · 여러 종목 시세 나열 · 광고 · 회사 이름이 나오지만 투자와 무관한 소식(그 이름을\n"
        "   단 스포츠 구단의 경기 결과·순위·선수 기록, 연예·문화 행사 등). 단 스포츠·연예·콘텐츠·게임이 그 회사의\n"
        "   본업이거나, 회사의 수입·지출에 닿는 일(후원·마케팅 계약, 흥행 수입, 구단 매각·운영비)이면 관련 있다.\n"
        "   종목 머리의 '업종' 은 같은 이름을 가르는 단서다 — 그 회사가 아닌 **이름만 같은** 다른 회사·남의 게임·\n"
        "   작품·상품을 다루는 항목(예: 반도체 장비 회사와 이름이 같은 다른 회사의 게임)은 넣지 않는다. 같은 회사의\n"
        "   다른 사업부·자회사 소식은 업종과 달라도 관련 있다.\n"
        "   relevant 에 없는 항목은 근거로 쓰지 않는다.\n"
        "6) 매수·매도 권유, 목표주가, 주가 예측을 쓰지 않는다.\n"
        "7) 아래 종목을 **하나도 빠뜨리지 않고** 종목마다 한 번씩 출력한다. key 는 '### key=' 뒤의 값을 그대로\n"
        "   쓴다(예: KR:005930). 투자와 관련된 항목이 하나도 없으면 relevant·pros·cons 를 빈 배열로 둔다.\n\n"
        "출력은 아래 JSON 하나만(설명·코드펜스 금지):\n"
        '{"stocks":[{"key":"종목 key 그대로","relevant":[1,2],'
        '"pros":[{"t":"한 문장","refs":[1]}],"cons":[{"t":"한 문장","refs":[2]}]}]}\n\n'
        "종목:\n" + "\n\n".join(_stock_block(s) for s in stocks))


# ── 무관 소식 확인(사용자 2026-10-14 'A') ─────────────────────────────────────
# 규칙 5 를 프롬프트에 넣었는데도 flash-lite 가 두산우 Cons 에 '야구팀 선수 부상' 을 남겼다(10-14 09:12 정리분 —
# 새 규칙으로 정리된 기록이었다). 프롬프트만으로는 확실히 못 막으므로, 스포츠·연예 문맥으로 **보이는** 줄만
# 골라 AI 에 한 번 더 묻는다. 이 낱말 목록은 **후보를 고르는 데만** 쓰고 빼는 판정은 AI 가 한다 — 낱말로 바로
# 빼면 구단 매각·후원 계약처럼 회사 돈에 닿는 기사까지 빠진다(#449·#146). 후보가 넓으면 묻는 비용이 조금 늘
# 뿐이고 좁으면 놓치므로 넓게 잡는다(`경기`·`부상` 은 경제 '경기'·'부상하다' 와 겹쳐 뺐다 — 그 문맥은 '선수'·
# 구기 종목명이 같이 잡는다).
OFFTOPIC_HINT = re.compile(
    r"야구|축구|농구|배구|골프|테니스|e스포츠|이스포츠|구단|선수|감독|리그|시즌|홈런|투수|타자|득점|우승|"
    r"KBO|K리그|MLB|NBA|EPL|올림픽|월드컵|콘서트|공연|드라마|예능|아이돌|팬미팅|배우|가수|시상식|영화제|"
    # 게임·콘텐츠 — 같은 이름의 게임·작품이 섞인다(제우스 079370 반도체 장비 ↔ 컴투스 신작 게임 '제우스', 2026-10-14).
    # 게임이 본업인 회사의 줄은 확인 단계가 업종을 보고 남긴다.
    # 경제 기사의 관용어(게임체인저·치킨게임·제로섬 게임)는 빼고, 영문 약어는 낱말로만(SCOTT 의 OTT 를 안 잡게).
    r"(?<!치킨)(?<!제로섬 )(?<!제로섬)게임(?!체인저|\s?체인저)|(?i:(?<![A-Za-z])(?:MMORPG|RPG|OTT)(?![A-Za-z]))|"
    r"구글 ?플레이|앱스토어|웹툰")
CHECK_MAX = 40                     # 한 번의 확인 호출에 묻는 줄 상한(넘으면 그 묶음의 다음 호출로)
MAX_CHECK_REVERTS = 3              # 확인 일시 오류로 '대기' 로 되돌리는 상한 — 넘으면 줄을 남기고 그렇게 적는다


def offtopic_candidates(stock: dict) -> list[tuple[str, int]]:
    """(쪽, 줄 번호) — 줄 글이나 그 줄의 근거 항목(제목·요약)에 스포츠·연예 낱말이 있는 줄. 본문은 보지 않는다
    (긴 본문의 지나가는 낱말까지 세면 거의 모든 줄이 후보가 된다)."""
    by_n = {it["n"]: it for it in stock.get("items") or []}
    out = []
    for side in ("pros", "cons"):
        for i, ln in enumerate(stock.get(side) or []):
            txt = ln.get("t", "") + " " + " ".join(
                f"{by_n[r].get('title', '')} {by_n[r].get('summary', '')}" for r in ln.get("refs") or []
                if r in by_n)
            if OFFTOPIC_HINT.search(txt):
                out.append((side, i))
    return out


def build_check_prompt(cands: list[tuple[str, dict, str, int]]) -> str:
    """`cands` = [(id, 종목, 쪽, 줄 번호)]. 종목 칸을 비우면 규칙 틀만 남는다(`prompt_sig` 가 그걸 잰다)."""
    rows = []
    for cid, st, side, i in cands:
        ln = st[side][i]
        by_n = {it["n"]: it for it in st.get("items") or []}
        refs = " / ".join(f"[{r}] {by_n[r].get('title', '')} {by_n[r].get('summary', '')}".strip()
                          for r in ln.get("refs") or [] if r in by_n)
        nm = st["name"] + (f" / {st['ko']}" if st.get("ko") else "")
        kind = "Pros(호재 관점)" if side == "pros" else "Cons(악재·리스크 관점)"
        ind = f" · 업종: {st['ind']}" if st.get("ind") else ""
        rows.append(f"### id={cid} · {nm}({st['ticker']}){ind} · {kind}\n줄: {ln.get('t', '')}\n근거: {refs[:600]}")
    return (
        "너는 개인 투자자의 보유 종목 Pros/Cons 를 검수하는 보조다. 아래 각 줄이 그 회사 주주의 투자 판단에\n"
        f"닿는지({INVEST_TOPICS}) 판정한다.\n"
        "- keep=false: 회사 이름만 나오고 회사의 사업·수입·지출과 이어지지 않는 소식. 그 이름을 단 스포츠\n"
        "  구단의 경기 결과·순위·선수 기록·선수 부상·감독 교체, 연예·문화 행사 소식 등. 그리고 그 회사가 아닌\n"
        "  **이름만 같은** 다른 회사·남의 게임·작품·상품의 소식(예: 업종이 반도체 장비인데 근거가 다른 회사가 낸\n"
        "  같은 이름의 모바일 게임 순위). 머리의 '업종' 은 이 동명 판별의 단서일 뿐 그 회사 사업의 범위가 아니다.\n"
        "- keep=true: 같은 회사의 다른 사업부·자회사 소식(업종과 달라도), 스포츠·연예·콘텐츠·게임이 그 회사의\n"
        "  본업인 경우, 또는 회사 돈에 닿는 일(후원·마케팅\n"
        "  계약의 금액·조건, 흥행 수입, 구단 매각·운영비)이 근거에 **쓰여 있는** 경우.\n"
        "- 근거에 쓰이지 않은 연결(예: '선수 부상이 브랜드 이미지에 영향')을 지어내 keep=true 로 하지 않는다.\n\n"
        "출력은 아래 JSON 하나만(설명·코드펜스 금지):\n"
        '{"checks":[{"id":"줄 id 그대로","keep":true}]}\n\n'
        "줄:\n" + "\n\n".join(rows))


REQUEUE_REASON = "AI 정리 기준이 바뀜"   # 약속('다시 정리')은 저장하지 않는다 — 화면이 붙인다(#447)


def prompt_sig() -> str:
    """정리 규칙(종목 칸을 뺀 프롬프트 틀)의 지문 — 정리한 종목에 `pv` 로 찍는다. 규칙을 고쳐도 이미 정리된
    Pros/Cons 는 안 바뀌므로(#18) 아직 이어서 정리하는 날의 종목은 지문이 다르면 다시 정리한다(`requeue_stale`).
    손으로 올리는 판 번호가 아니라 틀에서 파생한다 — 손 bump 는 이 레포에서 여섯 번 졌다(#119)."""
    import hashlib
    return hashlib.sha1((build_prompt([]) + build_check_prompt([])).encode("utf-8")).hexdigest()[:10]


def requeue_note(rec: dict, now: datetime | None = None) -> str:
    """`--why` 한 줄 — 이 기록(가장 최근 기록)에서 지금 기준(지문)으로 정리되지 않은 종목 수와 그게 어떻게 되나.
    '다시 정리' 는 그 기록이 다음 주기에도 가장 최근 기록일 때만 약속한다 — 집계 시각이 지났는데 오늘 기록이
    없으면 다음 주기가 오늘 기록을 모아 그게 가장 최근이 된다(델타 리뷰 2026-10-13)."""
    now = _now(now)
    sig = prompt_sig()
    oks = [s for s in rec.get("stocks") or [] if s.get("status") == "ok"]
    if not oks:
        return f"정리 기준 지문 {sig} · 정리된 종목 없음"          # 대조 0건을 '전부 이 기준' 이라 적지 않는다
    old = sum(1 for s in oks if s.get("pv") != sig)
    if not old:
        return f"정리 기준 지문 {sig} · 정리된 종목 {len(oks)} 전부 이 기준"
    if sig in (rec.get("requeued_for") or []):
        tail = ("이 기록은 이 기준으로 이미 한 번 되돌렸음 — 다른 판의 프로세스가 다시 정리했거나 이 판으로 "
                "되돌아온 배포(옛 지문이 목록에 이미 있음)일 수 있음")
    elif rec.get("date") == now.date().isoformat() or not due(now):
        tail = "AI 키가 있으면 다음 주기에 새 기준으로 다시 정리"
    else:
        tail = "다음 주기가 오늘 기록을 새 기준으로 정리하고, 이 기록은 옛 정리 그대로"
    return f"정리 기준 지문 {sig} · 옛 기준으로 정리된 종목 {old}/{len(oks)} — {tail}"


def _hm(v) -> str:
    """저장된 ISO 시각 → KST 'HH:MM'(못 읽으면 '?' — 잘라 쓰지 않는다, 리뷰 2026-10-16)."""
    d = _parse(v)
    return d.astimezone(_KST).strftime("%H:%M") if d else "?"


def holdings_diff_line(rec: dict, stocks: list[dict], now: datetime | None = None,
                       st: dict | None = None) -> str:
    """`--why` 한 줄 — 가장 최근 기록과 **지금** 자산 기록의 보유 차이, 그리고 그게 어떻게 되나(사용자 2026-10-16
    "업로드하면 여기도 업데이트되는거지?"). 새로 생긴 보유는 `add_new_holdings` 가 붙이고 빠진 보유는 그 날 카드에
    남으므로, 둘을 갈라 적는다(#82). 대조는 `add_new_holdings` 와 같은 키·같은 목록으로 한다(#35).
    '다음 주기에 붙음' 은 그 경로가 실제로 다시 돌 때만 적는다(#380) — 주기가 멈췄거나, 직전 주기의 붙이기가
    실패했거나(`add_fail`), 다음 주기가 자정을 넘어 날짜가 바뀌면(그 주기엔 오늘 기록이 없다) 그렇게 말한다.
    지금 목록이 비었으면(자산 기록 못 읽음 · 보유 0) 대조하지 않는다 — '전부 팔았다' 로 읽힌다(#54).
    ⚠️ 못 보는 축(#274): 붙이기 실패는 이 줄에만 실린다 — 판정 줄(`status_verdict`)·일일 감사는 안 본다.
    `add_new_holdings` 가 예외 없이 0종목을 돌려준 경우(그 사이 목록이 바뀌어 `collect` 가 빈손)도 성공으로 센다."""
    now = _now(now)
    st = st if isinstance(st, dict) else {}
    if not stocks:
        return "보유 대조: ❓ 지금 자산 기록에서 보유 종목을 못 읽어 대조하지 않음"
    rs = [x for x in rec.get("stocks") or [] if isinstance(x, dict) and x.get("key")]
    have = {x["key"]: x for x in rs}
    cur = {x["key"]: x for x in stocks}
    new = [k for k in cur if k not in have]
    gone = [k for k in have if k not in cur]

    def names(keys: list[str], src: dict) -> str:
        nm = [str(src[k].get("name") or k) for k in keys[:5]]
        return ", ".join(nm) + (f" 외 {len(keys) - 5}" if len(keys) > 5 else "")

    today = now.date().isoformat()
    added = rec.get("added")
    added = [a for a in added if isinstance(a, dict)] if isinstance(added, list) else []
    hist = (" · 낮에 붙인 이력 " + ", ".join(f"{_hm(a.get('at'))} {a.get('n', 0)}종목" for a in added)
            if added else "")
    waiting = sum(1 for x in rs if x.get("added_at") and x.get("status") == "pending")
    if waiting:
        hist += f" · 그중 AI 정리 대기 {waiting}종목"
    head = f"보유 대조({rec.get('date')} 기록 {len(have)} ↔ 지금 {len(cur)}): "
    if not new and not gone:
        return head + "차이 없음" + hist
    parts = []
    if new:
        tick_at = _parse(st.get("last_tick"))
        fail = st.get("add_fail") if isinstance(st.get("add_fail"), dict) else None
        # 다음 주기 시각. 예정 시각이 이미 지났으면(주기를 놓침) 언제 돌지 모르므로 늦은 쪽 상한(지금 + 간격)으로
        # 잰다 — 지난 시각으로 자정을 재면 넘는 걸 못 본다(리뷰)
        nxt = (tick_at or now) + timedelta(minutes=TICK_MIN)
        if nxt < now:
            nxt = now + timedelta(minutes=TICK_MIN)
        if fail and str((_parse(fail.get("at")) or now - timedelta(days=1)).astimezone(_KST).date()) != today:
            fail = None                  # 어제 기록이 남은 것 — 오늘 실패로 적지 않는다
        n_ticks = math.ceil(len(new) / MAX_ADD_PER_TICK)
        last_nxt = nxt + timedelta(minutes=TICK_MIN * (n_ticks - 1))
        if tick_at is not None and now - tick_at > timedelta(minutes=STALE_TICK_MIN):
            when = (f"⚠️ 마지막 주기가 {tick_at.astimezone(_KST).strftime('%m-%d %H:%M')} — 봇 주기가 멈췄으면 "
                    "붙지 않음")
        elif rec.get("date") == today and nxt.astimezone(_KST).date().isoformat() != today:
            # 자정을 넘긴 주기엔 오늘 기록이 없어 붙이기가 안 돈다 — 실패 재시도 약속보다 먼저 본다(리뷰)
            when = (f"다음 주기가 자정을 넘어 오늘 기록엔 안 붙음 — 내일 {RUN_HOUR:02d}:{RUN_MIN:02d} 수집이 "
                    "지금 목록으로 모음")
        elif rec.get("date") == today and fail:
            when = f"⚠️ {_hm(fail.get('at'))} 주기의 붙이기가 실패({str(fail.get('why') or '?')[:100]}) — 다음 주기가 다시 시도"
        elif rec.get("date") == today:
            when = (f"다음 주기에 붙음({TICK_MIN}분 간격"
                    + (f" · 마지막 주기 {tick_at.astimezone(_KST).strftime('%H:%M')}" if tick_at else "")
                    + (f" · 주기당 {MAX_ADD_PER_TICK}종목이라 {n_ticks}주기" if n_ticks > 1 else "")
                    + (f" — 자정 전에 다 못 붙은 종목은 내일 {RUN_HOUR:02d}:{RUN_MIN:02d} 수집이 모음"
                       if last_nxt.astimezone(_KST).date().isoformat() != today else "") + ")")
        elif due(now):
            when = "다음 주기의 오늘 수집이 지금 목록으로 모음"
        else:
            when = f"오늘 {RUN_HOUR:02d}:{RUN_MIN:02d} 수집이 지금 목록으로 모음"
        parts.append(f"새로 생긴 {len(new)}종목({names(new, cur)}) — {when}")
    if gone:
        # '팔았다' 로 단정하지 않는다 — 티커 확인·ETF 판정이 바뀌어도 빠진다(#165, 리뷰 2026-10-16)
        tail = ("오늘 카드에 남음 · 내일 수집부터 빠짐" if rec.get("date") == today
                else "이 기록엔 남음 · 다음 수집부터 빠짐")
        parts.append(f"빠진 {len(gone)}종목({names(gone, have)}) — {tail}")
    return head + " · ".join(parts) + hist


def requeue_stale(rec: dict, *, body=None, pace: float = 0.25, beat=None) -> int:
    """지금 규칙(`prompt_sig`)으로 정리하지 않은 'ok' 종목을 대기로 되돌린다 — 부른 쪽이 **가장 최근 기록**에만
    부른다(`tick` — 그날은 적어도 24 − `RUN_HOUR` 시간 더 이어서 정리되므로(`RUN_HOUR` 뒤엔 오늘 기록을 먼저
    모으니 가장 최근 기록이 곧 오늘 기록이다), 다시 정리를 마치기 전에 '마치지 못함' 으로 굳지 않는다). 한 기록을
    같은 지문으로는 한 번만 되돌린다(`requeued_for`) — 판이 다른 두 프로세스가 겹치면 서로의 정리를 끝없이
    되돌린다(리뷰 2026-10-13 · 못 보는 축: 옛 판으로 되돌린 배포는 그 지문이 목록에 이미 있어 다시 안 돈다).
    옛 Pros/Cons·relevant 는 카드에서 지우되 `prev_pc` 에 보관한다 — 대기 카드에 남기면 필터(`data-pro`)가 옛
    정리로 걸리고(#45), 버리면 다시 정리가 실패하거나 그날 안에 못 끝났을 때 멀쩡하던 정리가 흔적 없이 사라진다
    (그때 카드가 '옛 기준 정리' 라벨을 달고 보여 준다, 델타 리뷰 2026-10-13). 본문은 정리가 끝나면 사이드카에서 지워지므로(`_save_bodies`)
    네이버 사본을 다시 읽어 붙인다(이 호출당 `MAX_BODY_PER_RUN`). 못 읽으면 본문 없이 가고 '읽음' 표시는
    지운다 — 화면은 이번에 실제로 보낸 것만 '읽음' 으로 적는다(`body_used`). 반환 = 되돌린 종목 수."""
    body = body or _fetch_body
    sig = prompt_sig()
    if sig in (rec.get("requeued_for") or []):
        return 0
    n = reads = 0
    for s in rec.get("stocks") or []:
        # 실패로 굳은 종목도 규칙이 바뀌면 한 번 더 — 그 실패가 옛 규칙 탓일 수 있다(2026-10-16: 응답에서 빠진 종목을
        # '빠뜨리지 말 것' 규칙으로 고친 뒤에도 그날 실패 3종목이 그대로 남던 구멍). 지문을 기록이 한 번만 되돌린다.
        # 실패 지문(`fpv`)이 지금 규칙이면 되돌리지 않는다 — 같은 규칙으로 실패한 종목을 매 주기 다시 돌리면 비용만
        # 두 배이고 카드가 '기준이 바뀜' 이라는 거짓 사유를 단다(독립 리뷰 2026-10-16). 지문 없는 옛 실패는 되돌린다.
        failed = s.get("status") == "failed" and s.get("fpv") != sig
        if not (failed or (s.get("status") == "ok" and s.get("pv") != sig)):
            continue
        if beat:
            beat()
        reads += _reload_bodies(s, body, pace, max(0, MAX_BODY_PER_RUN - reads))
        if not failed:                   # 실패 종목은 보관할 새 정리가 없다(옛 `prev_pc` 가 있으면 그대로 둔다)
            s["prev_pc"] = {**{k: s.get(k) for k in ("pros", "cons", "relevant", "dropped_lines", "pv")},
                            "kind": "requeue",
                            "max_n": max((int(it.get("n") or 0) for it in s.get("items") or []), default=0)}
        s.update(status="pending", tries=0, retry=False, reason=REQUEUE_REASON, pros=[], cons=[],
                 relevant=None, dropped_lines=0, offtopic_lines=0, check_why="")
        s.pop("check_reverts", None)
        n += 1
    if n:
        rec["requeued_for"] = [*(rec.get("requeued_for") or []), sig]
    return n


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
    stock["offtopic_lines"] = 0
    stock["check_why"] = ""
    stock["pv"] = prompt_sig()
    # 옛 정리(`prev_pc`)는 여기서 버리지 않는다 — 무관 소식 확인이 일시 오류로 이 종목을 '대기' 로 되돌리면 그날
    # 다시 정리하지 못했을 때 보여 줄 옛 정리가 사라진다(독립 리뷰 2026-10-14). 확인까지 끝난 뒤 `analyze` 가 버린다.
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


def _revert_pending(st: dict, why: str) -> None:
    """정리를 되돌려 다음 주기에 다시 — 확인 단계가 일시 오류로 못 돈 종목(확인 안 된 줄을 '확인했다' 로 남기지
    않는다). 일시 오류라 시도로 세지 않는다(`analyze` 의 일시 오류 갈래와 같다)."""
    st.update(status="pending", retry=True, reason=why, pros=[], cons=[], relevant=None,
              dropped_lines=0, offtopic_lines=0, check_why="")
    st.pop("pv", None)
    for it in st.get("items") or []:
        it.pop("body_used", None)


def check_offtopic(grp: list[dict], rec: dict, *, llm=call_llm) -> dict:
    """정리가 끝난 묶음에서 스포츠·연예 문맥으로 보이는 줄(`offtopic_candidates`)만 AI 에 다시 묻고, 투자와
    무관하다고 답한 줄을 뺀다. 반환 = {calls, reverted, transient}.

    갈래(#82): 일시 오류 → 후보가 있던 종목을 '대기' 로 되돌린다(다음 주기에 정리부터 다시 — 확인 못 한 줄을
    확인한 것처럼 남기지 않는다 · 같은 종목 `MAX_CHECK_REVERTS` 번째면 줄을 남기고 사유를 적는다) · 거부·못 읽음·그 줄이 응답에 없음 → 줄은 **남기고** 카드가 '다시 확인하지
    못함' 을 사유와 함께 말한다(빼는 쪽이 틀리면 진짜 호재·악재가 사라지므로 모를 때는 남긴다).
    호출은 `analyze` 의 주기 상한(`MAX_LLM_CALLS`)에 넣지 않는다 — 묶음 하나에 많아야 2번이고(묶음 종목
    `BATCH_MAX` × 줄 2·`MAX_PER_SIDE` ÷ `CHECK_MAX`), 상한에 넣으면 정리한 묶음이 확인 없이 남는다. 대신
    호출 수는 기록의 `llm.checks` 로 따로 센다(비용은 같은 원장·같은 카드)."""
    cands = []
    for st in grp:
        if st.get("status") != "ok":
            continue
        for side, i in offtopic_candidates(st):
            cands.append((f"c{len(cands) + 1}", st, side, i))
    done = {"calls": 0, "reverted": 0, "transient": False}
    if not cands:
        return done
    llm_rec = rec.setdefault("llm", {"calls": 0, "pt": 0, "ot": 0, "cost_usd": 0.0})
    drop: dict[int, set] = {}            # id(종목) → {(쪽, 줄 번호)}
    for k in range(0, len(cands), CHECK_MAX):
        chunk = cands[k:k + CHECK_MAX]
        text, pt, ot, why, kind = llm(build_check_prompt(chunk))
        if kind != "no_key":
            done["calls"] += 1
            llm_rec["calls"] += 1
            llm_rec["checks"] = int(llm_rec.get("checks") or 0) + 1
            if pt or ot:
                llm_rec["pt"] += pt
                llm_rec["ot"] += ot
                llm_rec["cost_usd"] = round(llm_rec["cost_usd"] + _log_usage(pt, ot), 6)
        if kind == "transient":
            reverted = set()
            # 되돌리면 다음 주기에 정리(AI 1회)부터 다시 — 장애가 이어지면 매 주기 두 번 내고 앞 묶음이 차례를
            # 막는다(독립 리뷰 2026-10-14). 같은 종목 `MAX_CHECK_REVERTS` 번째면 되돌리지 않고 줄을 남긴다.
            for st in {id(c[1]): c[1] for c in cands}.values():
                st["check_reverts"] = int(st.get("check_reverts") or 0) + 1
                if st["check_reverts"] >= MAX_CHECK_REVERTS:
                    st["check_why"] = f"무관 소식 확인이 {st['check_reverts']}번 일시 오류({why})"
                    continue
                _revert_pending(st, f"무관 소식 확인 중 {why}")
                reverted.add(id(st))
                done["reverted"] += 1
            # 앞 묶음에서 이미 받은 판정은 되돌리지 않은 종목에 적용한다(버리면 AI 가 뺀 줄이 카드에 남는다)
            _apply_drops([st for st in grp if id(st) not in reverted], drop)
            done["transient"] = True
            return done
        data = _parse_json(text) if text and not kind else None
        got = {}
        if data and isinstance(data.get("checks"), list):
            for r in data["checks"]:
                if isinstance(r, dict) and isinstance(r.get("keep"), bool):
                    got[str(r.get("id"))] = r["keep"]
        for cid, st, side, i in chunk:
            keep = got.get(cid)
            if keep is False:
                drop.setdefault(id(st), set()).add((side, i))
            elif keep is None:
                st["check_why"] = ("AI 키 없음" if kind == "no_key" else
                                   f"무관 소식 확인 실패({why})" if why else
                                   "무관 소식 확인 응답을 못 읽음" if not data else "무관 소식 확인 응답에 이 줄이 없음")
    _apply_drops(grp, drop)
    for st in grp:
        if st.get("status") == "ok":
            st.pop("check_reverts", None)     # 확인을 마쳤다 — 다음 재정리(기준 바뀜)의 일시 오류는 처음부터 센다
    return done


def _apply_drops(stocks: list[dict], drop: dict) -> None:
    """확인 응답이 keep=false 라 한 줄을 뺀다 — 그 줄만 근거로 쓰던 항목은 `relevant` 에서 빼 흐리게."""
    for st in stocks:
        gone = drop.get(id(st))
        if not gone:
            continue
        cut_refs = set()
        for side in ("pros", "cons"):
            kept = []
            for i, ln in enumerate(st.get(side) or []):
                if (side, i) in gone:
                    cut_refs.update(ln.get("refs") or [])
                else:
                    kept.append(ln)
            st[side] = kept
        still = {r for side in ("pros", "cons") for ln in st[side] for r in ln.get("refs") or []}
        # 뺀 줄만 근거로 삼던 항목은 '투자와 무관' 으로 흐리게 — 남기면 그 기사가 근거처럼 보인다
        st["relevant"] = [r for r in st.get("relevant") or [] if r not in cut_refs or r in still]
        st["offtopic_lines"] = len(gone)


def _attach_industry(rec: dict, stocks: list[dict]) -> None:
    """업종을 종목에 적는다(프롬프트·확인 단계·카드가 같이 쓴다). 이미 있으면 그대로 · 모르면 비워 둔다(지어내지
    않는다 — 프롬프트도 업종 줄을 빼고 보낸다, #165). 실패는 업종만 비운다(#315)."""
    if not stocks:
        return
    try:
        ind = industry_lookup(rec.get("date") or _now(None).date().isoformat())
        for s in stocks:
            if not s.get("ind"):
                v = ind(s)
                if v:
                    s["ind"] = v
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: 업종 붙이기 실패: %s", exc)


def _norm_key(k) -> str:
    """AI 가 돌려준 key 를 대조용으로 — 프롬프트 머리(`### key=KR:005930`)의 `key=` 를 같이 베끼거나 대소문자·
    공백을 바꿔도 같은 종목으로 본다."""
    t = re.sub(r"\s+", "", str(k or "")).upper()
    return t[4:] if t.startswith("KEY=") else t


def match_results(grp: list[dict], data) -> tuple[dict, list[str]]:
    """({우리 key: 그 종목 결과}, 응답에 온 key 원문 목록). 세 단으로 맞추고 **앞 단이 이긴다**(뒤 단의 빈 줄이 정확히
    맞은 정리를 덮지 않게): ① 정확한 key ② `key=`·대소문자·공백을 걷은 key ③ **묶음 안에서 하나뿐인** 티커 —
    응답 key 에 시장이 없거나 그 시장이 그 종목의 시장일 때만(`US:SAME` 은 `KR:SAME` 이 아니다 · 같은 티커가 시장만
    달리 묶음에 둘이면 받지 않는다, #46). 빈 key·빈 티커는 맞추지 않는다. 원문 목록은 빠진 종목의 사유에 싣는다 —
    'AI 응답에 이 종목이 없음' 만으로는 키를 바꿔 쓴 것인지 아예 뺀 것인지 못 가른다(2026-10-16 감사 ❌ 3종목, #82)."""
    rows = data.get("stocks") if isinstance(data, dict) else None
    rows = [r for r in rows if isinstance(r, dict) and r.get("key")] if isinstance(rows, list) else []
    seen = [str(r["key"]) for r in rows]
    exact = {s["key"] for s in grp}
    by_norm = {_norm_key(s["key"]): s["key"] for s in grp}
    mkt = {s["key"]: _norm_key(s.get("market")) for s in grp}
    tick: dict[str, list[str]] = {}
    for s in grp:
        t = _norm_key(s.get("ticker"))
        if t:
            tick.setdefault(t, []).append(s["key"])
    got: dict[str, tuple[int, dict]] = {}
    for r in rows:
        k = str(r["key"])
        nk = _norm_key(k)
        if k in exact:
            ours, tier = k, 0
        elif nk in by_norm:
            ours, tier = by_norm[nk], 1
        else:
            pre, _, code = nk.rpartition(":")
            cand = [c for c in tick.get(code) or [] if not pre or mkt.get(c) == pre]
            ours, tier = (cand[0], 2) if len(cand) == 1 else (None, 3)
        if ours is not None and (ours not in got or tier < got[ours][0]):
            got[ours] = (tier, r)
    return {k: v[1] for k, v in got.items()}, seen


def missing_reason(seen: list[str]) -> str:
    """응답은 읽었는데 이 종목이 없을 때의 사유 — 응답에 온 key 를 같이 적는다(몇 개만 · 자른 사실도, #45)."""
    if not seen:
        return "AI 응답에 이 종목이 없음(응답에 종목이 하나도 없음)"
    def _one(k: str) -> str:            # AI 가 쓴 글이다 — 제어문자·줄바꿈을 걷고 짧게(사유는 카드·결산 줄에 실린다)
        k = " ".join("".join(ch if ch.isprintable() else " " for ch in k).split())
        return k[:40] + ("…" if len(k) > 40 else "")
    shown = ", ".join(_one(k) for k in seen[:4]) + (f" 외 {len(seen) - 4}" if len(seen) > 4 else "")
    return f"AI 응답에 이 종목이 없음(응답 key {len(seen)}개: {shown})"


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
    _attach_industry(rec, pend)
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
        got, seen = match_results(grp, data)
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
            s["reason"] = why or ("AI 응답을 못 읽음" if not data else missing_reason(seen))
            if s["tries"] >= MAX_ANALYZE_TRIES:
                s["status"] = "failed"
                s["fpv"] = prompt_sig()           # 어느 규칙으로 실패했나 — `requeue_stale` 이 규칙이 바뀔 때만 되돌린다
                done["failed"] += 1
        chk = check_offtopic(grp, rec, llm=llm)
        done["analyzed"] -= chk["reverted"]
        for s in grp:
            if s.get("status") == "ok":
                s.pop("prev_pc", None)      # 새 기준 정리·확인이 끝났다 — 옛 정리는 더 쓸 데가 없다
        if on_progress:
            on_progress(rec)
        if chk["transient"]:
            done["transient"] = True
            break
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


def _llm_ready() -> bool:
    """AI 키가 있나(조회 실패 = 없음 — 그때 옛 정리를 지우면 되살릴 길이 없다)."""
    try:
        from bot.genai_factory import effective_key
        return bool(effective_key())
    except Exception:                                          # noqa: BLE001
        return False


# 낮에 자산 기록이 바뀌면(사용자 2026-10-14 "자산 기록이 바뀌었을 때 새로 생긴 종목만 바로 수집해 오늘 카드에")
# 새로 생긴 보유만 모아 오늘 기록에 붙인다. 종목 목록은 아침 수집 한 번에 정해져, 낮에 산 종목은 다음 날까지
# 카드가 없었다. 판 종목은 그대로 둔다(그날 아침에 들고 있던 종목의 뉴스는 사실이다). 한 주기 상한을 두어
# 자산 기록을 통째로 바꿔도 한 주기가 길어지지 않는다(남은 종목은 다음 주기에).
MAX_ADD_PER_TICK = 20


def add_new_holdings(rec: dict, now: datetime | None = None, *, records: list[dict] | None = None,
                     model: dict | None = None, fetch=fetch_stock_news, body=_fetch_body, pace: float = 0.25,
                     us_etf: set | None = None, ko=None, beat=None) -> int:
    """오늘 기록(`rec`)에 없는 지금의 보유 종목을 모아 붙인다(대기 상태 — 같은 주기의 `analyze` 가 정리한다).
    반환 = 붙인 종목 수. 같은 날 다시 불러도 이미 붙은 종목은 다시 모으지 않는다(키로 대조)."""
    now = _now(now)
    # 캐시만 — 15분마다 목록 원천을 두드리지 않는다(아침 수집이 이미 받아 둔다 · 원천 장애 땐 매 주기 시간 초과, 리뷰)
    etfs = _us_etf(cache_only=True) if us_etf is None else us_etf
    stocks, _ = universe(model, us_etf=etfs)
    have = {s.get("key") for s in rec.get("stocks") or []}
    new = [s["key"] for s in stocks if s["key"] not in have][:MAX_ADD_PER_TICK]
    if not new:
        return 0
    part = collect(now, model=model, records=records, fetch=fetch, body=body, pace=pace, us_etf=etfs, ko=ko,
                   beat=beat, only=set(new), since_at=_parse((rec.get("window") or {}).get("since")))
    if not part:
        return 0
    at = now.isoformat(timespec="minutes")
    for s in part["stocks"]:
        s["added_at"] = at
        rec.setdefault("stocks", []).append(s)
    u = rec.setdefault("universe", {})
    u["n"] = int(u.get("n") or 0) + len(part["stocks"])
    # 원천 계수도 합친다 — 안 합치면 붙인 종목의 실패가 판정·통계에서 빠진다(리뷰 2026-10-14)
    _merge_sources(rec, part)
    rec.setdefault("added", []).append({"at": at, "n": len(part["stocks"])})
    return len(part["stocks"])


def _merge_sources(rec: dict, part: dict, *, counts: bool = True) -> None:
    """부분 수집(`part`)의 원천 계수를 그날 기록에 더한다. `counts=False` 면 텔레그램 언급 수만 — 저녁 보강은
    같은 종목을 한 번 더 받은 것이라 원천 계수를 더하면 '원천 naver 596' 처럼 종목 수의 두 배가 된다(#45 —
    보강의 원천 계수는 `topups` 항목이 따로 든다)."""
    srcs, ps = rec.setdefault("sources", {}), part.get("sources") or {}
    for k, v in ps.items() if counts else ():
        if isinstance(v, int) and not isinstance(v, bool):
            srcs[k] = int(srcs.get(k) or 0) + v
        elif isinstance(v, dict) and k in ("fail_reasons", "fallback"):
            d = srcs.setdefault(k, {})
            for kk, vv in v.items():
                if isinstance(vv, int):
                    d[kk] = int(d.get(kk) or 0) + vv
    tg = (ps.get("telegram") or {}).get("matched")
    if isinstance(tg, int) and isinstance(srcs.get("telegram"), dict):
        srcs["telegram"]["matched"] = int(srcs["telegram"].get("matched") or 0) + tg


TOPUP_REASON = "저녁 수집에서 새 뉴스가 붙음"     # 약속('다시 정리')은 저장하지 않는다 — 화면이 붙인다(#447)


def topup_slot(now: datetime) -> datetime:
    return _now(now).replace(hour=TOPUP_HOUR, minute=TOPUP_MIN, second=0, microsecond=0)


def _topups_since_slot(rec: dict, slot: datetime) -> list[dict]:
    return [t for t in rec.get("topups") or [] if isinstance(t, dict) and (_parse(t.get("at")) or _EPOCH) >= slot]


def topup_due(rec: dict | None, now: datetime | None = None) -> bool:
    """오늘 기록에 저녁 보강이 남았나 — 저녁 시각이 지났고 그 기록의 창이 그 시각 전에 끝났다. 아침 수집 자체가 저녁
    시각 뒤에 돌았으면(봇이 낮 내내 멈춤) 그 창이 이미 저녁까지 덮으므로 다시 하지 않는다. 원천 장애로 끝난 보강은 창
    끝을 옮기지 않으므로(`top_up`) 시도 기록으로 가른다 — `TOPUP_RETRY_MIN` 뒤 다시, 그날 `TOPUP_MAX_TRIES` 번까지."""
    now = _now(now)
    if not rec or rec.get("date") != now.date().isoformat():
        return False
    slot = topup_slot(now)
    if now < slot:
        return False
    tps = _topups_since_slot(rec, slot)
    if tps:
        last = tps[-1]
        if not last.get("retry", bool(last.get("outage"))) or len(tps) >= TOPUP_MAX_TRIES:
            return False
        at = _parse(last.get("at"))
        return at is None or now - at >= timedelta(minutes=TOPUP_RETRY_MIN)
    until = _parse((rec.get("window") or {}).get("until"))
    return until is not None and until < slot


def _reload_bodies(s: dict, body, pace: float, budget: int) -> int:
    """다시 정리할 종목의 옛 항목 본문을 다시 읽어 붙인다 — 정리가 끝나면 사이드카에서 지워지므로(`_save_bodies`).
    '읽음' 표시는 지운다(이번에 실제로 보낸 것만 `analyze` 가 다시 단다). 반환 = 읽은 수(`budget` 까지)."""
    reads = 0
    for it in s.get("items") or []:
        it.pop("body_used", None)
        link = str(it.get("link") or "")
        blink = it.get("naver_link") or (link if "news.naver.com" in link else "")
        if (it.get("src") == "telegram" or not it.get("body_chars") or it.get("_body") or not blink
                or reads >= budget):
            continue
        reads += 1
        b = body(blink)
        if pace:
            time.sleep(pace)
        if b:
            it["_body"] = b
    return reads


def top_up(rec: dict, now: datetime | None = None, *, records: list[dict] | None = None,
           model: dict | None = None, fetch=fetch_stock_news, body=_fetch_body, pace: float = 0.25,
           us_etf: set | None = None, ko=None, beat=None) -> dict | None:
    """저녁 보강(사용자 2026-10-19 "19:00 에도") — 오늘 기록의 종목을 아침 창 끝 `WINDOW_OVERLAP_H` 시간 전부터 지금까지
    다시 모아 **새 항목만** 그 종목에 붙이고(오늘 아침·앞선 날에 실린 항목은 `_rec_keys` 로 뺀다) 그 종목만 대기로
    되돌린다. 옛 항목의 번호는 그대로 두고 새 항목에 다음 번호를 준다(옛 정리의 근거 번호가 바뀌지 않게). 아침 정리는
    `prev_pc`(kind='topup' · `max_n` · 앞선 보강까지 본 정리면 `evening`)에 보관 — 다시 정리가 실패하거나 그날 안에 못
    끝나면 카드가 '아침 정리'(또는 '직전 정리') 라벨로 보여 준다. 원천을 못 받은 종목은 `window.miss` 로 넘기고, 장애면
    창 끝을 옮기지 않는다(`topup_due` 가 다시 연다).
    창 끝(`window.until`)을 지금으로 옮겨 다음 날 아침 창이 여기서 이어지게 한다. 지금 목록에 없는 종목(판 종목)은 다시
    모으지 않는다. 반환 = `topups` 에 남긴 항목(보유를 못 읽어 아무것도 안 했으면 None — 다음 주기에 다시)."""
    now = _now(now)
    until = _parse((rec.get("window") or {}).get("until"))
    if until is None:
        return None
    etfs = _us_etf(cache_only=True) if us_etf is None else us_etf
    have = {s.get("key") for s in rec.get("stocks") or []}
    w = rec.setdefault("window", {})
    rec_miss = w.get("miss") if isinstance(w.get("miss"), dict) else {}
    part = collect(now, model=model, records=records, fetch=fetch, body=body, pace=pace, us_etf=etfs, ko=ko,
                   beat=beat, only=have, since_at=until - timedelta(hours=WINDOW_OVERLAP_H),
                   seen_extra=_rec_keys(rec), since_by_key={k: d for k, v in rec_miss.items() if (d := _parse(v))})
    if not part:
        return None
    at = now.isoformat(timespec="minutes")
    by = {s.get("key"): s for s in rec.get("stocks") or []}
    n_items = n_st = reads = 0
    for ps in part["stocks"]:
        s = by.get(ps["key"])
        new = ps.get("items") or []
        if s is None or not new:
            continue
        if beat:
            beat()
        st0 = s.get("status")
        if st0 == "pending":
            # 아직 정리 전 — 그 본문은 사이드카에만 있다. 메모리에 안 붙이면 저장이 새 항목 본문만 남기고 덮는다
            try:
                _attach_bodies(rec, [s])
            except Exception as exc:                           # noqa: BLE001 — 본문 없이 정리한다(#315)
                log.warning("holdings_news: %s 본문 사이드카 붙이기 실패: %s", s.get("key"), exc)
        base_n = max((int(it.get("n") or 0) for it in s.get("items") or []), default=0)
        for i, it in enumerate(new, 1):
            it["n"] = base_n + i
            it["topup"] = at
        # 상태 바꾸기와 항목 붙이기는 던질 것이 없는 한 덩어리 — 본문 다시 읽기(네트워크)는 그 뒤. 사이에 예외가 나면
        # 항목만 붙고 'ok' 로 남아(다음 재시도는 `_rec_keys` 로 그 항목을 빼므로) 영영 다시 정리되지 않는다(독립 리뷰 L8)
        if st0 != "pending":             # 아직 정리 전이면 항목만 늘어난다(사유·시도 수는 그대로)
            if st0 == "ok":
                # `evening` = 보관하는 정리가 이미 앞선 저녁 보강 항목까지 본 것(재시도로 둘째 보강) — 라벨이 '아침
                # 정리' 라고 하면 거짓이다(델타 리뷰 · #34)
                s["prev_pc"] = {**{k: s.get(k) for k in ("pros", "cons", "relevant", "dropped_lines", "pv")},
                                "kind": "topup", "max_n": base_n,
                                "evening": any(it.get("topup") for it in s.get("items") or [])}
            s.update(status="pending", tries=0, retry=False, reason=TOPUP_REASON if st0 != "empty" else "",
                     pros=[], cons=[], relevant=None, dropped_lines=0, offtopic_lines=0, check_why="")
            s.pop("check_reverts", None)
        s["items"] = new + list(s.get("items") or [])     # 새 항목이 위(최신순 화면)
        n_items += len(new)
        n_st += 1
        if st0 != "pending":
            try:
                reads += _reload_bodies(s, body, pace, max(0, MAX_BODY_PER_RUN - reads))
            except Exception as exc:                           # noqa: BLE001 — 본문 없이 정리한다(#315)
                log.warning("holdings_news: %s 저녁 보강 본문 다시 읽기 실패: %s", s.get("key"), exc)
    out = _outage(part)
    failed = [ps["key"] for ps in part["stocks"] if _src_failed(ps)]
    # 장애로 재시도할 것은 원천을 **못 받은** 장애뿐이다 — '시장 전부 0건' 은 늘 그런 시장이면 매일 세 번 전 종목을 다시
    # 받게 되므로 사실로만 적는다(델타 리뷰)
    hard = bool(out) and bool(failed)
    if not hard:
        # 원천을 못 받은 종목은 창 끝을 옮기면 아침 창 끝 ~ 지금 기사를 영영 못 모은다(독립 리뷰 H1) — 그 종목만 이번에
        # 열려던 창 시작을 남겨 다음 수집이 거기서 연다(`collect` 의 `since_by_key`). 이번에 받은 종목은 지운다.
        # 장애(종목 다수·시장 전체)면 창 끝을 아예 옮기지 않고 `TOPUP_RETRY_MIN` 뒤 다시 한다(`topup_due`).
        miss = {k: v for k, v in rec_miss.items() if k in failed or k not in have}
        for k in failed:                 # 이미 넘겨받은 종목은 그 시작을 그대로(`rec_miss`), 처음이면 이번 창 시작
            miss.setdefault(k, (until - timedelta(hours=WINDOW_OVERLAP_H)).isoformat(timespec="seconds"))
        if miss:
            w["miss"] = miss
        else:
            w.pop("miss", None)
        w["until"] = part["window"]["until"]
    _merge_sources(rec, part, counts=False)
    psrc = part.get("sources") or {}
    ent = {"at": at, "items": n_items, "stocks": n_st, "asked": len(part["stocks"]),
           "fail": int(psrc.get("fail") or 0), "outage": out, "retry": hard, "miss": 0 if hard else len(failed),
           "tg": int((psrc.get("telegram") or {}).get("matched") or 0)}
    rec.setdefault("topups", []).append(ent)
    return ent


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
                st.pop("add_fail", None)         # 오늘 수집은 지금 목록 전부를 모은다 — 어제 붙이기 실패는 낡았다(리뷰)
                st.pop("topup_fail", None)       # 어제 저녁 보강 실패도 낡았다
                _save_state(st)
                rec = collect(now, records=recs, fetch=fetch, body=body, pace=pace, beat=beat)
                if rec is None:
                    st["reason"] = "자산 대시보드에 보유 종목이 없음(portfolio.json)"
                    st["no_holdings"] = True
                    _save_state(st)
                    return {"status": "none"}
                save_record(rec)
                st["last_collect"] = now.isoformat(timespec="seconds")
            elif rec is not None:
                # 저녁 보강(사용자 2026-10-19) — 실패는 이 일만 건너뛰고 `TOPUP_RETRY_MIN` 뒤 다시(#315). 아침
                # 수집과 같은 크기의 일이라 매 주기 다시 하면 원천을 15분마다 전 종목 두드린다.
                tf = st.get("topup_fail") if isinstance(st.get("topup_fail"), dict) else None
                tf_at = _parse((tf or {}).get("at"))
                if topup_due(rec, now) and (tf_at is None or now - tf_at >= timedelta(minutes=TOPUP_RETRY_MIN)):
                    try:
                        ent = top_up(rec, now, records=recs, fetch=fetch, body=body, pace=pace, beat=beat)
                        if ent is not None:
                            save_record(rec)
                            log.info("holdings_news: %s 저녁 보강 — 새 항목 %d건 · %d종목", date, ent["items"],
                                     ent["stocks"])
                        if st.pop("topup_fail", None) is not None:
                            _save_state(st)
                    except Exception as exc:                   # noqa: BLE001
                        log.warning("holdings_news: 저녁 보강 수집 실패: %s", exc)
                        st["topup_fail"] = {"at": now.isoformat(timespec="seconds"),
                                            "why": f"{type(exc).__name__}: {_exc_msg(exc, 80)}"}
                        _save_state(st)
                # 낮에 자산 기록이 바뀌어 새로 생긴 보유 — 실패는 이 일만 건너뛴다(본체를 멈추지 않게, #315)
                try:
                    na = add_new_holdings(rec, now, records=recs, fetch=fetch, body=body, pace=pace, beat=beat)
                    if na:
                        log.info("holdings_news: %s — 자산 기록에 새로 생긴 %d종목을 모아 붙임", date, na)
                        save_record(rec)
                    if st.pop("add_fail", None) is not None:
                        _save_state(st)
                except Exception as exc:                       # noqa: BLE001
                    log.warning("holdings_news: 새 보유 추가 수집 실패: %s", exc)
                    # `--why` 보유 대조가 '다음 주기에 붙음' 을 약속하지 않게 남긴다(#380 — 로그만으론 진단이 모른다)
                    st["add_fail"] = {"at": now.isoformat(timespec="seconds"),
                                      "why": f"{type(exc).__name__}: {_exc_msg(exc, 80)}"}
                    _save_state(st)
            live = _live_dates([r["date"] for r in recs], date)
            todo = ([rec] if rec else []) + [r for r in sorted(recs, key=lambda r: r["date"], reverse=True)
                                             if r["date"] != date and r["date"] in live]
            res = {"calls": 0, "analyzed": 0, "failed": 0, "no_key": False, "transient": False,
                   "requeued": 0}
            # 가장 최근 기록만 — 직전 기록까지 되돌리면 밤늦은 배포가 자정에 그날을 이어서 정리하는 날 밖으로 밀어
            # 옛 정리만 잃고 '마치지 못함' 으로 굳는다(리뷰 2026-10-13). AI 키가 없으면 다시 정리할 수 없으니
            # 지우지 않는다(지우면 멀쩡한 카드가 '키 미설정 — 뉴스만' 이 된다).
            if todo and _llm_ready():
                nq = requeue_stale(todo[0], body=body, pace=pace, beat=beat)
                if nq:
                    log.info("holdings_news: %s — 정리 기준이 바뀌어 %d종목을 다시 정리", todo[0]["date"], nq)
                    res["requeued"] = nq
                    save_record(todo[0])        # 다시 읽은 본문을 사이드카에 — 일시 오류로 밀려도 남는다
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
    try:                                 # 하루치에 없는 보유의 종가 — 실패는 그 칸만 비운다(#315)
        cr = refresh_closes(now)
        if cr["asked"]:
            log.info("holdings_news: Yahoo 일봉 종가 %d/%d종목%s", cr["got"], cr["asked"],
                     f" — {cr['why']}" if cr["why"] else "")
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: 종가 보충 실패: %s", exc)
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
    tps = [t for t in rec.get("topups") or [] if isinstance(t, dict)]
    if tps:
        # 재시도로 보강이 여러 번이면 합한다(마지막 것만 적으면 앞서 붙은 항목이 빠진다, 델타 리뷰 · #45)
        n_it, n_st = sum(int(t.get("items") or 0) for t in tps), sum(int(t.get("stocks") or 0) for t in tps)
        head += (f" · {_hm(tps[-1].get('at'))} 저녁 보강 새 항목 {n_it}건/{n_st}종목"
                 + (f"({len(tps)}회)" if len(tps) > 1 else ""))
        nm = len((rec.get("window") or {}).get("miss") or {})
        if nm and tps[-1].get("miss"):
            head += f" · 원천을 못 받은 {nm}종목은 다음 수집이 놓친 구간부터 다시 모음"
    if topup_due(rec, now):
        tick_at = _parse(st.get("last_tick"))
        tf = st.get("topup_fail") if isinstance(st.get("topup_fail"), dict) else None
        if tick_at is not None and now - tick_at > timedelta(minutes=STALE_TICK_MIN):
            return "fail", (f"{head} · {TOPUP_HOUR:02d}:{TOPUP_MIN:02d} 이 지났는데 저녁 보강이 안 됐고 마지막 주기가 "
                            f"{tick_at.strftime('%m-%d %H:%M')} — 봇 주기가 멈췄을 수 있음")
        if tf and str((_parse(tf.get("at")) or now).date()) == today:
            head += (f" · ⚠️ 저녁 보강 실패({str(tf.get('why') or '?')[:80]}) — {TOPUP_RETRY_MIN}분 뒤 다시 시도")
        else:
            head += f" · {TOPUP_HOUR:02d}:{TOPUP_MIN:02d} 저녁 보강 대기(다음 주기)"
    out = _outage(rec)
    if not out and tps and tps[-1].get("outage"):   # 저녁 보강의 원천 장애도 그날의 장애다
        if tps[-1].get("retry", True):
            left = TOPUP_MAX_TRIES - len(_topups_since_slot(rec, topup_slot(now)))
            out = (f"저녁 보강 {tps[-1]['outage']} — 창을 옮기지 않음("
                   + (f"{TOPUP_RETRY_MIN}분 뒤 다시 · 남은 시도 {left}" if left > 0 else
                      "오늘 시도를 다 씀 — 다음 날 아침 수집이 이 시간대를 모음") + ")")
        else:                            # 0건 갈래 — 다시 묻지 않는다(사실로만)
            out = f"저녁 보강 {tps[-1]['outage']}"
    if out:                      # 장애(❌)가 대기(⚠️)보다 먼저 — 대기가 오래 가면 장애가 안 보였다
        return "fail", f"{head} · {out}" + (f" · Pros/Cons 대기 {len(pend)}종목" if pend else "")
    if pend:
        reason = _pending_reason(pend[0]) if pend[0].get("reason") else "다음 주기에 이어서"
        return "pending", f"{head} · Pros/Cons 대기 {len(pend)}종목 — {reason}"
    if failed:
        # 카드가 보관 정리를 보여 준다 — 까닭이 둘이라 갈라 적는다(옛 기준 · 저녁 보강 전 아침 정리, 독립 리뷰 L1 · #34)
        kinds = [s["prev_pc"].get("kind") for s in failed if isinstance(s.get("prev_pc"), dict)]
        n_am = sum(1 for k in kinds if k == "topup")
        parts = [x for x in (f"{len(kinds) - n_am}종목은 옛 기준 정리" if len(kinds) - n_am else "",
                             f"{n_am}종목은 저녁 보강 전 정리" if n_am else "") if x]
        shown = "뉴스만 표시" if not kinds else "그중 " + " · ".join(parts) + " 표시"
        # 어느 종목인지 판정 줄이 말한다 — 결산은 이 한 줄만 올린다(#356 · 2026-10-16 감사 ❌ 에 종목이 없었다)
        names = ", ".join(str(s.get("name") or s.get("key")) for s in failed[:3]) + \
            (f" 외 {len(failed) - 3}" if len(failed) > 3 else "")
        return "fail", (f"{head} · Pros/Cons 실패 {len(failed)}종목[{names}]({shown}) — "
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
.hn-reset{margin-left:auto}
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
.hn-ind{font-weight:600}
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
.hn-pnl .hn-up{color:var(--hn-up)}.hn-pnl .hn-dn{color:var(--hn-dn)}
.hn-pnl-sub{font-size:11.5px;color:var(--muted);margin-top:2px}
.hn-pnl-note{font-size:11.5px;color:var(--muted);margin:-4px 0 10px}
.stat .v.hn-v-sm{font-size:14px;line-height:1.45}  /* '시세 반영' — 시장 여럿을 한 칸에 적는다 */
</style>
"""

# 전일대비 요약·최신일 카드를 화면이 다시 센다 — 등락은 서버가 구운 장종료가 누적이고, 두 가지만 화면이 받는다:
# ① 해외 원화 환율(해외 보유가 있을 때 1시간마다 하나 · `api/live_pct?fx=1`, 사용자 2026-10-14 "부하가 크면 장종료가")
# ② 실시간 등락(사용자 2026-10-19 "10분에 한번씩") — 장 중이거나 마감했지만 종가를 아직 안 받은 세션이 있는 종목만
#    `LIVE_POLL_SEC` 마다(`api/live_pct?t=…&fresh=` — 서버 표가 그 간격으로만 다시 받아 탭이 몇 개든 한 종목은 한 번).
#    원천이 준 세션 날짜(`d`)가 서버가 정한 그 세션(`ld`)일 때만 종가 누적에 곱해 이어 붙인다 — 다르면(장이 바뀌었는데
#    화면이 낡음 · 원천 날짜를 못 읽음) 종가 누적 그대로 둔다(지어내지 않는다, #43). 규칙은 서버 `pnl_summary`·
#    `_prev_html`·`pnl_span_head(live=True)` 와 같다(#38).
_PNL_JS = r"""
(function(){
  // 파이썬 `_signed_krw` 와 같은 반올림(절댓값 반올림 올림 · 반올림해 0 이면 부호 없음)
  function amt(a){var x=isFinite(a)?Math.abs(a):0, n, body;
    if(x>=1e8){n=Math.floor(x/1e7+0.5); body=Math.floor(n/10).toLocaleString('en-US')+'.'+(n%10)+'억원';}
    else if(x>=1e4){n=Math.floor(x/1e4+0.5); body=n.toLocaleString('en-US')+'만원';}
    else{n=Math.floor(x+0.5); body=n.toLocaleString('en-US')+'원';}
    return (n===0?'':a>0?'+':'-')+body;}
  window.hnAmt=amt;    // 회귀가 파이썬 `_signed_krw` 와 값으로 대조한다
  // 파이썬 `quarterly_series.fmt_price` 와 같은 자리수(원·엔 정수 · 그 밖은 1000 이상 정수, 아니면 소수 2자리) · 반올림은
  // 정확히 반이면 짝수 쪽(1234.5 → 1,234 — 파이썬 서식과 같다). ⚠️ '정확히 반' 은 JS 곱셈 기준이라 1.115×100 처럼 이진
  // 표현이 반 아래인 값은 파이썬(1.11)과 갈릴 수 있다(독립 리뷰 L6 — 실시간 가격엔 서버 대응값이 없어 대조할 칸은 없다)
  function fmtPx(n,cu,sym){var dec=(cu==='KRW'||cu==='JPY'||Math.abs(n)>=1000)?0:2, m=Math.pow(10,dec),
      y=n*m, f=Math.floor(y), r=(y-f===0.5)?(f%2===0?f:f+1):Math.round(y);
    return (sym||'')+(r/m).toLocaleString('en-US',{minimumFractionDigits:dec,maximumFractionDigits:dec});}
  window.hnPx=fmtPx;
  // 원화 환율 전일대비(통화 → %) — 서버가 넣은 값으로 시작하고 1시간마다 `fx=1` 로 갱신. 파이썬 `krw_pct` 와 같은 식.
  var fx={}; try{var fe=document.getElementById('hn-fx-data'); if(fe) fx=JSON.parse(fe.textContent)||{};}catch(e){fx={};}
  // `fb` = 그 종목 기준 업로드 무렵의 원화 환율 수준 — 있으면 환율 변동도 기준 대비 누적(지금 ÷ 기준), 없으면 오늘 하루치
  var fxNow={};
  function krw(p,c,fb){var fac=null, day=false;
    if(fb&&fxNow[c]) fac=fxNow[c]/fb;
    else if(fx[c]!==undefined&&fx[c]!==null){fac=1+fx[c]/100; day=true;}
    if(fac===null) return {pct:p,fx:null,day:false};
    return {pct:((1+p/100)*fac-1)*100,fx:(fac-1)*100,day:day};}
  var el=document.getElementById('hn-pnl-data'), pos=[], basis='', pollSec=600;
  if(el){basis=el.getAttribute('data-basis')||''; pollSec=+(el.getAttribute('data-poll'))||600;
    try{pos=JSON.parse(el.textContent)||[];}catch(e){pos=[];}}
  var asofData=[]; try{var ae=document.getElementById('hn-asof-data'); if(ae) asofData=JSON.parse(ae.textContent)||[];}catch(e){asofData=[];}
  // 실시간 — 종목 키 → {pct, price, at(받은 시각 ms), src, ld}. 세션 날짜가 맞은 것만 담는다.
  var live={}, byKey={};
  pos.forEach(function(p){byKey[p.k]=p;});
  function eff(p,L){return L?((1+p/100)*(1+L.pct/100)-1)*100:p;}   // 종가 누적 × 그 세션 실시간 등락
  function kst(ms){try{return new Date(ms).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',
    minute:'2-digit',hour12:false});}catch(e){return '';}}
  function draw(){
    var G={all:[0,0,0,0,0,0,0],KR:[0,0,0,0,0,0,0],XX:[0,0,0,0,0,0,0]};   // 금액·분모·값 있음·전체·환율 미확인·실시간·실시간 대상
    pos.forEach(function(p){
      ['all',p.g].forEach(function(g){var o=G[g]; o[3]++;
        if(p.p===null||p.p===undefined) return;
        var L=live[p.k], pp=eff(p.p,L);
        var k=p.c?krw(pp,p.c,p.fb):{pct:pp,fx:0};
        o[0]+=p.v*k.pct/100; o[1]+=p.v; o[2]++; if(k.fx===null) o[4]++;
        if(p.ld){o[6]++; if(L) o[5]++;}});
    });
    Object.keys(G).forEach(function(g){
      var o=G[g], v=document.getElementById('hn-pnl-'+g), s=document.getElementById('hn-pnl-'+g+'-sub');
      if(!v||!s) return;
      var box=v.parentNode, h=box&&box.querySelector?box.querySelector('.k'):null;
      if(h){if(h.getAttribute('data-h0')===null) h.setAttribute('data-h0',h.textContent);
        // 칸 이름(세션 수) — 그 그룹의 이어 붙일 대상 전부에 붙였을 때만 서버가 만든 '실시간' 이름으로(일부면 옛 이름)
        var lh=box.getAttribute('data-lh');
        h.textContent=(lh&&o[6]&&o[5]===o[6])?lh:h.getAttribute('data-h0');}
      if(!o[3]){v.textContent='—'; s.textContent='보유 없음'; return;}
      if(!o[1]){v.textContent='—';}
      else{var pc=o[0]/o[1]*100, sp=document.createElement('span');
        sp.className=o[0]>0?'hn-up':o[0]<0?'hn-dn':'';
        sp.textContent=amt(o[0])+' ('+(pc>0?'+':'')+pc.toFixed(2)+'%)';
        v.textContent=''; v.appendChild(sp);}
      s.textContent=basis+' '+o[2]+'/'+o[3]+'종목'+(o[5]?' · 실시간 '+o[5]+'종목':'')
        +(o[4]?' · 환율 미확인 '+o[4]:'');   // 서버 칸과 같은 꼴 + 실시간을 이어 붙인 수
    });
    asof();
  }
  // 맨 위 '시세 반영' 칸 — 실시간을 이어 붙인 시장은 그 세션 날짜와 '실시간', 받은 시각(가장 이른 것)
  function asof(){
    var box=document.getElementById('hn-st-asof'); if(!box||!asofData.length) return;
    var vv=box.querySelector('.v'), kk=box.querySelector('.k'), mk={}, cnt={}, first=0;
    // 그 시장의 이어 붙일 대상 **전부**에 붙였을 때만 그 시장을 '실시간' 으로 적는다 — 요약 칸 이름과 같은 규칙(독립 리뷰 L4 · #38)
    pos.forEach(function(p){if(!(p.ld&&p.t&&p.p!==null&&p.p!==undefined)) return;
      var c=cnt[p.m||'']=cnt[p.m||'']||{n:0,got:0,ld:'',at:0}; c.n++; var L=live[p.k];
      if(L){c.got++; c.ld=L.ld; if(!c.at||L.at<c.at) c.at=L.at;}});
    Object.keys(cnt).forEach(function(m){var c=cnt[m]; if(m&&c.got&&c.got===c.n){mk[m]=c.ld; if(!first||c.at<first) first=c.at;}});
    if(vv.getAttribute('data-v0')===null) vv.setAttribute('data-v0',vv.textContent);
    if(kk&&kk.getAttribute('data-k0')===null) kk.setAttribute('data-k0',kk.textContent);
    if(!first){vv.textContent=vv.getAttribute('data-v0'); if(kk) kk.textContent=kk.getAttribute('data-k0'); return;}
    vv.textContent=asofData.map(function(a){return a.n+' '+(mk[a.m]?mk[a.m].slice(5)+' 실시간':(a.d||'—'));}).join(' · ')
      +' · 실시간 '+kst(first)+' KST 받음';
    if(kk) kk.textContent='시세 반영(마지막 종가 · 실시간 '+Math.round(pollSec/60)+'분 간격)';
  }
  // 최신일 카드(`js-hn-amt`) — 서버가 구운 종가 누적 등락·기준 금액에 실시간 등락·환율을 곱해 다시 쓴다
  function cards(){
    [].slice.call(document.querySelectorAll('.js-hn-amt')).forEach(function(e){
      var p=parseFloat(e.getAttribute('data-p')), v=parseFloat(e.getAttribute('data-v')), c=e.getAttribute('data-c')||'';
      if(!isFinite(p)||!isFinite(v)) return;
      var L=live[e.getAttribute('data-k')||''], pp=eff(p,L);
      var k=c?krw(pp,c,parseFloat(e.getAttribute('data-fb')||'')||null):{pct:pp,fx:0,day:false};
      // 가격 — 실시간을 붙였으면 그 현재가, 아니면 서버가 통화 표기까지 만든 종가
      var px=(L&&L.price)?fmtPx(L.price,e.getAttribute('data-cu')||'KRW',e.getAttribute('data-sym')||''):
        (e.getAttribute('data-px')||'');
      e.textContent='('+(px?px+' · ':'')+(pp>0?'+':'')+pp.toFixed(2)+'% · '+(c&&k.fx!==null?'원화 ':'')+amt(v*k.pct/100)+')';
      e.classList.toggle('hn-up',pp>0); e.classList.toggle('hn-dn',pp<0);
      var lt=L?(px+' = '+L.ld+' 실시간(정규장 · '+L.src+' · '+kst(L.at)+' KST 받음 · '+Math.round(pollSec/60)
        +'분마다) — 종가 누적 '+(p>0?'+':'')+p.toFixed(2)+'% × 그 세션 '+(L.pct>0?'+':'')+L.pct.toFixed(2)+'% · '):'';
      e.setAttribute('title',lt+(e.getAttribute('data-tip')||'')+(!c?'':k.fx===null?' · '+c+' 환율 미확인 — 현지통화 등락만'
        :' · 원화 기준(주가 '+(pp>0?'+':'')+pp.toFixed(2)+'% · 원/'+c+(k.day?' 오늘 하루 ':' 기준 대비 ')+(k.fx>0?'+':'')+k.fx.toFixed(2)+'%)'));
    });
  }
  // 실시간 등락 — 이어 붙일 세션(`ld`)이 있는 종목만 묻는다. 서버는 값을 뒤에서 받으므로(요청 경로는 메모리만)
  // 낡은 값이 있었으면(pending) 15초 뒤 몇 번만 다시 묻고 평소엔 `pollSec` 마다.
  var targets={}; pos.forEach(function(p){if(p.ld&&p.t&&p.p!==null&&p.p!==undefined) (targets[p.t]=targets[p.t]||[]).push(p);});
  var tlist=Object.keys(targets), lquick=0;
  // 그 세션의 종가를 받고 페이지가 다시 구워질 시각(`lx`)이 지난 종목은 더 묻지 않는다 — 열어 둔 탭이 닫힌 장을 밤새
  // 묻지 않게(이미 받은 값은 그대로 둔다 — 마감 뒤 실시간 = 그 세션 등락이라 맞다). 다 지나면 멈춘다.
  function active(){var n=Date.now();
    return tlist.filter(function(t){return targets[t].some(function(p){return !p.lx||p.lx*1000>n;});});}
  function lpoll(){
    var act=active(); if(!act.length) return;
    fetch('api/live_pct?t='+encodeURIComponent(act.slice(0,400).join(','))+'&fresh='+pollSec,{cache:'no-store'})
      .then(function(r){if(!r.ok) throw new Error('HTTP '+r.status); return r.json();})
      .then(function(js){
        var q=(js&&js.q)||{}, now=Date.now();
        act.forEach(function(t){var v=q[t];
          targets[t].forEach(function(p){
            if(v&&typeof v.pct==='number'&&v.d===p.ld)
              live[p.k]={pct:v.pct,price:(typeof v.price==='number'?v.price:null),at:now-(+v.age||0)*1000,
                         src:v.src||'',ld:p.ld};
            else delete live[p.k];});});
        draw(); cards();
        if(js&&js.pending&&lquick<4){lquick++; return 'soon';}
        lquick=0;})
      .catch(function(){})
      .then(function(r){setTimeout(lpoll,r==='soon'?15000:pollSec*1000);});
  }
  if(tlist.length){if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',lpoll); else lpoll();}
  // 해외 카드(`js-hn-amt` 의 data-c)는 값이 있는 해외 보유에만 붙으므로 요약 재료로 충분하다 — 값이 없는 해외(전부 '(—)')면
  // 다시 그릴 것이 없으니 환율은 묻지 않는다
  var need=pos.some(function(p){return !!p.c&&p.p!==null&&p.p!==undefined;});
  var quick=0, again=0;
  function poll(){
    fetch('api/live_pct?fx=1',{cache:'no-store'})
      .then(function(r){if(!r.ok) throw new Error('HTTP '+r.status); return r.json();})
      .then(function(js){
        // 응답이 환율을 실었으면 통째로 바꾼다 — 서버가 낡아서 버린 통화가 화면에 남지 않게(리뷰 2026-10-14)
        var f=js&&js.fx; if(!f||typeof f!=='object') return;
        var pend=js.fx_pending&&!Object.keys(f).length;
        if(pend&&quick<12){quick++; return 'soon';}
        if(pend&&Object.keys(fx).length) return;       // 상한 뒤에도 받는 중 — 갖고 있던 환율은 둔다
        if(!pend) quick=0;
        var ages=Object.keys(f).map(function(c){return +(f[c]&&f[c].age)||0;});
        var stale=!ages.length||Math.max.apply(null,ages)>60;
        var follow=!pend&&stale&&!again;      // 이번 주기의 후속 확인을 아직 안 썼다
        if(follow) again=1;
        if(follow&&!ages.length) return 'again';   // 빈 표 — 갖고 있던 환율을 둔 채 10초 뒤 한 번 더
        var nf={}, nl={};
        for(var cc in f){ if(f[cc]&&typeof f[cc].pct==='number') nf[cc]=f[cc].pct;
          if(f[cc]&&typeof f[cc].close==='number') nl[cc]=f[cc].close; }
        fx=nf; fxNow=nl; draw(); cards();
        if(follow) return 'again';})
      .catch(function(){})
      // 평소 주기 = 1시간(사용자 2026-10-14 "환율정보를 받는건 1분말고 1시간으로 — 부하안가게")
      .then(function(r){if(r!=='again') again=0;     // 다음 주기는 후속 확인을 다시 한 번 쓸 수 있다
        setTimeout(poll,r==='soon'?5000:r==='again'?10000:3600000);});
  }
  if(!need) return;                    // 국내만 들고 있으면 환율도 묻지 않는다
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',poll); else poll();
})();
"""

_HN_JS = r"""
(function(){
  var q=document.getElementById('hn-q'), st=document.getElementById('hn-st');
  var pills=[].slice.call(document.querySelectorAll('[data-hnf]'));
  var days=[].slice.call(document.querySelectorAll('details[data-hnday]'));
  if(!q) return;
  var reset=document.getElementById('hn-reset');
  var base=st?st.textContent:'', sel={}, wasOn=false;
  // 칩은 여러 개 고를 수 있다 — 시장(국내·해외)끼리는 '또는', 나머지(Pros·Cons·텔레그램)는 '그리고'
  // (둘 다 고르면 시장은 전부, 표식은 모두 갖춘 카드만). 아무것도 안 고르면 '전체'.
  function picked(){return pills.map(function(p){return p.getAttribute('data-hnf');}).filter(function(k){return sel[k];});}
  function norm(s){return String(s||'').toLowerCase().replace(/\s+/g,'');}
  function keep(c){
    var mk=c.getAttribute('data-mk')==='KR'?'KR':'XX';
    if((sel.KR||sel.XX) && !sel[mk]) return false;
    if(sel.pro && c.getAttribute('data-pro')!=='1') return false;
    if(sel.con && c.getAttribute('data-con')!=='1') return false;
    if(sel.tg && c.getAttribute('data-tg')!=='1') return false;
    return true;
  }
  function paint(){
    var any=picked().length>0;
    pills.forEach(function(x){var k=x.getAttribute('data-hnf');x.classList.toggle('active',k==='all'?!any:!!sel[k]);});
  }
  function lazy(d){return !!d.getAttribute('data-lazy') && !d.__loaded;}
  function run(){
    var names=picked().map(function(k){var p=pills.filter(function(x){return x.getAttribute('data-hnf')===k;})[0];
      return String(p.textContent||'').replace(/^[^0-9A-Za-z가-힣]+/,'');});
    var v=norm(q.value), on=!!v || names.length>0, shown=0;
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
    if(st) st.textContent=on?((names.length?('['+names.join(' · ')+'] '):'')+'조건에 맞는 카드 '+shown+'개'+(wait?(' · 아직 안 연 날 '+wait+'일은 펼치면 같이 걸러집니다'):'')):base;
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
    var k=p.getAttribute('data-hnf');
    if(k==='all') sel={}; else sel[k]=!sel[k];
    paint(); run();
  });});
  // 초기화 — 고른 칩과 검색어를 함께 지운다('전체' 칩은 칩만 푼다)
  if(reset) reset.addEventListener('click',function(){sel={}; q.value=''; paint(); run();});
  q.addEventListener('input',run);
})();
"""


def _krw(v: float) -> str:
    if v >= 1e8:
        return f"{v / 1e8:,.1f}억원"
    if v >= 1e4:
        return f"{v / 1e4:,.0f}만원"
    return f"{v:,.0f}원"


def _signed_krw(a: float) -> str:
    """전일대비 금액 — 화면 JS `hnAmt` 와 **같은 반올림**(절댓값 기준 반올림 올림 · 반올림해 0 이면 부호 없음).
    파이썬 `format` 은 짝수 반올림이라 경계에서 JS 와 1만원씩 갈렸다(독립 리뷰 2026-10-14, #33)."""
    x = abs(a) if math.isfinite(a) else 0.0
    if x >= 1e8:
        n = math.floor(x / 1e7 + 0.5)
        body = f"{n // 10:,}.{n % 10}억원"
    elif x >= 1e4:
        n = math.floor(x / 1e4 + 0.5)
        body = f"{n:,}만원"
    else:
        n = math.floor(x + 0.5)
        body = f"{n:,}원"
    return ("" if n == 0 else "+" if a > 0 else "-") + body


# ── 전일대비 금액(사용자 2026-10-14 "전일대비 얼마나 금액이 오르거나 내렸는지") ─────────────────────────
# 자산 기록에는 **수량이 없다**(뱅크샐러드 내보내기 = 평가금액·원금·수익률뿐, `portfolio_parser`). 그래서 금액은
# '보유 평가금액 × 등락률' 로 **추정**하고 화면이 그렇게 말한다(#165 — 평가금액은 업로드 시점 가격 기준이라 오늘
# 주가와 다를 수 있다). 해외 평가금액은 내보내기가 이미 원화로 환산한 값이다.
def pnl_positions(model: dict | None = None) -> tuple[list[dict], dict]:
    """전일대비 요약의 대상 — 자산 기록의 **값을 매길 수 있는** 보유 전부(티커·시장이 있는 것 · ETF 포함:
    뉴스 카드와 달리 '전체' 손익이므로 ETF 를 빼면 합이 작아진다). 반환 = (종목 목록, {n, value} 티커가 없어
    못 매긴 상품 · names = {상품명: 평가금액} — 화면이 수만 말하면 '무엇이' 빠졌는지 다시 물어야 한다, #43). 여러
    증권사에 나눠 가진 종목은 합친다(`universe` 와 같은 키)."""
    if model is None:
        try:
            from bot.portfolio import load
            model = load() or {}
        except Exception as exc:                               # noqa: BLE001
            log.warning("holdings_news: portfolio 읽기 실패(전일대비): %s", exc)
            model = {}
    out: dict[str, dict] = {}
    skip = {"n": 0, "value": 0.0, "names": {}}
    for h in model.get("holdings") or []:
        try:
            v = float(h.get("평가금액") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if not math.isfinite(v):           # 'NaN' 문자열도 float 로 읽힌다 — 카드 렌더가 round() 에서 죽지 않게
            v = 0.0
        tk, mk = h.get("ticker"), str(h.get("market") or "").upper()
        if not tk or not mk or not h.get("matched"):
            skip["n"] += 1
            skip["value"] += v
            nm = str(h.get("상품명") or tk or "이름 없음").strip() or "이름 없음"
            skip["names"][nm] = skip["names"].get(nm, 0.0) + v
            continue
        code = _code(tk, mk)
        key = f"{mk}:{code}"
        pos = out.setdefault(key, {"key": key, "market": mk, "ticker": code, "sym": str(tk).strip(),
                                   "name": str(h.get("상품명") or code), "value": 0.0})
        pos["value"] += v
    return sorted(out.values(), key=lambda p: (-p["value"], p["key"])), skip


SKIP_NAMES_MAX = 30


def skip_names_line(skip: dict) -> str:
    """요약에서 뺀 '티커를 못 찾은 상품' 의 이름 — 평가금액 큰 순(사용자 2026-10-14 "티커못찾은 11개는 뭐야?"). 줄이
    끝없이 길어지지 않게 `SKIP_NAMES_MAX` 개까지 적고 나머지는 수를 말한다(#45). 이름이 늘 상품 수와 같지는 않다 —
    같은 이름을 여러 증권사에 나눠 가지면 한 번만 적으므로 '상품 N개' 와 이름 수가 다를 수 있어 그 사실도 적는다."""
    names = (skip or {}).get("names") or {}
    if not names:
        return ""
    items = sorted(names.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = " · ".join(f"{n}({_krw(v)})" for n, v in items[:SKIP_NAMES_MAX])
    more = f" 외 {len(items) - SKIP_NAMES_MAX}개" if len(items) > SKIP_NAMES_MAX else ""
    dup = (f" — 같은 이름을 여러 계좌에 나눠 가진 것은 하나로 적어 이름 {len(items)}개"
           if len(items) != int(skip.get("n") or 0) else "")
    return (f"티커를 못 찾은 상품(자산 기록에 티커·시장이 없거나 확인되지 않음 — 💼 자산 대시보드에서 고치면 다음 갱신에 "
            f"들어갑니다): {shown}{more}{dup}")


PNL_GROUPS = (("all", "전체"), ("KR", "국내"), ("XX", "해외"))

# ── 전일대비의 기준 금액(사용자 2026-10-14 "자산대시보드 전번 업데이트 대비가 아니라 한국시간 전일 마지막 업데이트
# 대비로") — 자산 기록은 하루에도 여러 번 올라온다. 그날 낮에 올린 평가금액은 이미 오늘 등락을 일부 품어서, 그걸
# 기준으로 오늘 등락을 곱하면 두 번 센다. 그래서 KST 날짜마다 **그날 마지막 업로드**의 종목별 평가금액을 이 장부에
# 남기고, 전일대비는 오늘보다 앞선 가장 최근 날의 값 × 오늘 등락으로 센다. 자산 기록은 최신 1개 + 직전 1개(.bak)만
# 들고 있어(`portfolio.save`) 히스토리가 없으므로 여기서 쌓는다. 오늘 처음 생긴 종목은 기준이 없어 지금 금액을 쓰고
# 화면이 그 수를 말한다(#43).
BASE_KEEP_DAYS = 10
FX_BASE_FRESH_SEC = 3600
FX_BASE_SLACK_SEC = 600            # 값 풀이 업로드보다 이만큼 먼저 받은 것까지는 '그 무렵' 으로 본다


def _base_path() -> Path:
    return _DIR / "pnl_base.json"


def _saved_kst(model) -> tuple[str, float] | None:
    try:
        ts = float((model or {}).get("_saved_ts"))
        return datetime.fromtimestamp(ts, _KST).date().isoformat(), ts
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def pnl_base(now: datetime | None = None, model: dict | None = None, *, bak: dict | None = None,
             write: bool = True) -> dict:
    """전일대비 기준 — {vals: {key: 평가금액} | None, at: 'MM-DD HH:MM', ts: 업로드 epoch | None, fx: {통화: 그 무렵 원화
    환율 수준}, cur_ts: 지금 기록의 업로드 epoch | None, cur_fx: 지금 기록 무렵 환율 수준}.

    기준은 오늘(KST)보다 앞선 가장 최근 **날짜**의 마지막 업로드다(오늘이 10/9 이고 그 앞 기록이 10/5 뿐이면 10/5 의
    마지막 업로드). 지금 자산 기록과 직전 기록(.bak)을 장부에 넣고(같은 날은 더 늦은 업로드가 이긴다) 오래된 날은 지운다
    — 가장 최근 날은 오래돼도 남긴다(지우면 매 렌더 다시 넣고 지우며 기준을 잃는다)."""
    now = _now(now)
    if model is None:
        model = _load_model_safe()
        if bak is None:
            try:
                from bot.portfolio import PORTFOLIO_PATH
                bak = json.loads(PORTFOLIO_PATH.with_suffix(".json.bak").read_text(encoding="utf-8"))
            except Exception:                                  # noqa: BLE001 — 없으면 없는 것
                bak = None
    path = _base_path()
    readable = True
    try:
        led = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(led, dict):
            led = {}
    except FileNotFoundError:
        led = {}
    except (OSError, ValueError) as exc:
        # 못 읽은 장부를 빈 것으로 보고 덮어쓰면 쌓은 날을 잃는다 — 이번엔 쓰지 않는다(리뷰 2026-10-14)
        log.warning("holdings_news: 전일 기준 장부 읽기 실패 — 이번 렌더는 쓰지 않음: %s", exc)
        led, readable = {}, False
    changed = False
    for m in (bak, model):
        sk = _saved_kst(m)
        if not sk or not isinstance(m, dict):
            continue
        d, ts = sk
        cur = led.get(d) if isinstance(led.get(d), dict) else None
        rev = m.get("_rematch_ts")      # 업로드 없이 티커가 붙은 재매칭 판(`portfolio.rematch_unmatched`)
        same = cur and float(cur.get("ts") or 0) == ts
        if cur and float(cur.get("ts") or 0) >= ts and (not same or cur.get("rev") == rev):
            continue
        pos, _ = pnl_positions(m)
        led[d] = {"ts": ts, "vals": {p["key"]: round(p["value"]) for p in pos}}
        if rev is not None:
            led[d]["rev"] = rev
        if same and isinstance(cur.get("fx"), dict):
            led[d]["fx"] = cur["fx"]    # 같은 업로드를 다시 적을 뿐 — 그 무렵 환율은 그대로 둔다
        # 그 업로드 무렵의 원화 환율 수준 — 여러 날 기준의 해외 원화 손익(지금 ÷ 기준)에 쓴다. 업로드 뒤 한 시간
        # 안에 처음 보는 기록에만, 그리고 값 풀이 **그 업로드 무렵**에 받은 것일 때만 적는다(새벽 값 풀을 아침 업로드의
        # 환율로 적으면 그날 환율인 척한다, #165 · 리뷰 2026-10-14).
        since = now.timestamp() - ts
        if since <= FX_BASE_FRESH_SEC:
            try:
                from bot.live_pct import fx_levels_cached
                lv = fx_levels_cached(max_age=since + FX_BASE_SLACK_SEC)
                if lv:
                    led[d]["fx"] = lv
            except Exception as exc:                           # noqa: BLE001
                log.warning("holdings_news: 기준 환율 수준 읽기 실패: %s", exc)
        changed = True
    keep = (now.date() - timedelta(days=BASE_KEEP_DAYS)).isoformat()
    newest = max(led) if led else ""
    for d in [d for d in led if d < keep and d != newest]:
        led.pop(d)
        changed = True
    if changed and write and readable:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(led, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("holdings_news: 전일 기준 장부 저장 실패: %s", exc)

    def _fx(e) -> dict:
        f = e.get("fx") if isinstance(e, dict) and isinstance(e.get("fx"), dict) else {}
        return {k: float(v) for k, v in f.items() if isinstance(v, (int, float)) and v > 0}

    sk = _saved_kst(model)
    out = {"vals": None, "at": "", "ts": None, "fx": {}, "cur_ts": sk[1] if sk else None,
           "cur_fx": _fx(led.get(sk[0])) if sk and isinstance(led.get(sk[0]), dict)
           and float(led[sk[0]].get("ts") or 0) == sk[1] else {}}
    today = now.date().isoformat()
    prior = sorted(d for d in led if d < today and isinstance(led[d], dict))
    if not prior:
        return out
    e = led[prior[-1]]
    ts = float(e.get("ts") or 0)
    vals = e.get("vals") if isinstance(e.get("vals"), dict) else {}
    out.update(vals={k: float(v) for k, v in vals.items() if isinstance(v, (int, float))},
               at=datetime.fromtimestamp(ts, _KST).strftime("%m-%d %H:%M"), ts=ts, fx=_fx(e))
    return out


# 해외 원화 손익(사용자 2026-10-14 "환율 반영 원화손익으로") — 원화 기준 등락 = (1+주가 등락)(1+원화 환율 등락) − 1.
# 환율 = 네이버 원화 환율 전일대비(`live_pct.fx_changes`). 통화는 시장 설정(`market.MARKET_CONFIG`) 한 곳에서(#38).
def currency_of(market: str) -> str | None:
    """해외 보유의 통화(국내·모르는 시장 = None — 국내는 환율이 없고, 모르는 시장은 지어내지 않는다)."""
    if market == "KR":
        return None
    try:
        from bot.market import MARKET_CONFIG
        return (MARKET_CONFIG.get(market) or {}).get("currency")
    except Exception:                                          # noqa: BLE001
        return None


def krw_pct(pct: float, fxp: float | None) -> float:
    """원화 기준 등락률 % — 환율 등락을 모르면 주가 등락 그대로(호출부가 '환율 미확인' 으로 센다)."""
    if fxp is None:
        return pct
    return ((1 + pct / 100) * (1 + fxp / 100) - 1) * 100


def pnl_summary(positions: list[dict], look, fx: dict | None = None) -> dict:
    """그룹(전체·국내·해외)별 {amt, base, n, n_priced, n_nofx} — `look(pos)` = (등락%, …). 등락률이 없는 종목은
    금액에도 분모에도 넣지 않는다(빈칸을 0% 로 세면 그룹 등락률이 조용히 묽어진다, #43). 그룹 % = Σ금액 ÷ Σ그
    평가금액. 해외는 `fx`(통화 → 원화 환율 전일대비 %)로 원화 기준 금액을 세고, 그 통화를 모르면 주가 등락만으로
    세되 `n_nofx` 로 센다(화면이 '환율 미확인' 으로 말한다)."""
    fx = fx or {}
    out = {g: {"amt": 0.0, "base": 0.0, "n": 0, "n_priced": 0, "n_nofx": 0} for g, _ in PNL_GROUPS}
    for p in positions:
        try:
            pct = look(p)[0]
        except Exception:                                      # noqa: BLE001
            pct = None
        ccy = currency_of(p["market"])
        fxp = fx.get(ccy) if ccy else None
        nofx = pct is not None and ccy is not None and fxp is None      # JS 와 같은 규칙(통화를 아는 해외만)
        for g in ("all", "KR" if p["market"] == "KR" else "XX"):
            o = out[g]
            o["n"] += 1
            if pct is not None:
                o["amt"] += p["value"] * krw_pct(pct, fxp) / 100
                o["base"] += p["value"]
                o["n_priced"] += 1
                o["n_nofx"] += nofx
    return out


def pnl_context(pos: list[dict], now: datetime | None = None, model: dict | None = None) -> dict:
    """전일대비의 기준과 누적 등락 — 요약·최신일 카드·화면 JS 가 같은 값을 쓴다(#38).
    {pos: 기준 금액으로 바꾼 보유, look: 장종료가 누적 등락 조회(pnl_summary 꼴), cum: key→`cum_lookup` 결과(None =
     거래소 달력을 몰라 장종료가를 못 세는 시장), base: key→기준 금액, fb: key→기준 환율 수준, at: 기준 라벨, today: 기준이 오늘 기록인가,
     new: 기준 날 뒤 새로 생긴 종목 수, newk: 그 종목 키, asof: 시장별 반영한 마지막 종가(`close_asof`),
     cur_ts: 지금 자산 기록의 업로드 epoch(맨 위 '자산 기록' 칸 — 없으면 None)}
    기준 날 뒤 새로 생긴 종목은 **지금 기록의 업로드 시각**부터 센다 — 기준 날부터 세면 들고 있지 않던 날의 손익을
    지어낸다(리뷰 2026-10-14 High)."""
    now = _now(now)
    pb = pnl_base(now, model)
    today = pb["vals"] is None
    cur_ts = pb["cur_ts"] or now.timestamp()
    if today:                            # 앞선 날짜의 기록이 없다(처음 배포 직후 등) — 지금 기록 시각을 기준으로
        base_ts, at, base, fxb = cur_ts, datetime.fromtimestamp(cur_ts, _KST).strftime("%m-%d %H:%M"), {}, pb["cur_fx"]
    else:
        base_ts, at, base, fxb = pb["ts"], pb["at"], pb["vals"], pb["fx"]
    cum_b = cum_lookup(base_ts, now)
    cum_n = cum_lookup(cur_ts, now) if not today else cum_b
    cmap, bmap, fbmap, bpos, new, newk = {}, {}, {}, [], 0, set()
    for p in pos:
        b = base.get(p["key"])
        ccy = currency_of(p["market"])
        if b is None:
            new += 0 if today else 1
            if not today:
                newk.add(p["key"])
            b = p["value"]
            cmap[p["key"]] = cum_n(p)
            fl = pb["cur_fx"].get(ccy) if ccy else None
        else:
            cmap[p["key"]] = cum_b(p)
            fl = fxb.get(ccy) if ccy else None
        if fl:
            fbmap[p["key"]] = fl
        bmap[p["key"]] = b
        bpos.append({**p, "value": b})

    def look(p):
        c = cmap.get(p["key"])
        if c is None:
            return None, None, no_close_why(p.get("market"))
        ka = c.get("ka")
        return ((ka - 1) * 100 if ka is not None else None), None, c.get("why") or ""

    return {"pos": bpos, "look": look, "cum": cmap, "base": bmap, "fb": fbmap, "at": at, "today": today,
            "new": new, "newk": newk, "asof": close_asof(pos, cmap), "cur_ts": pb["cur_ts"]}


def no_close_why(market) -> str:
    """장종료가 등락을 못 세는 시장(거래소 달력을 모르는 시장)의 사유 — 요약·카드가 같은 문장을 쓴다(#38)."""
    return f"{_mk_name(market)} 시장은 거래소 달력을 몰라 장종료가 기준 등락을 못 셈"


def _mk_name(market) -> str:
    try:
        from bot.market import MARKET_CONFIG
        return (MARKET_CONFIG.get(str(market or "")) or {}).get("name") or str(market or "?")
    except Exception:                                          # noqa: BLE001
        return str(market or "?")


def close_asof(pos: list[dict], cmap: dict) -> dict:
    """시장별 {asof: 반영한 마지막 종가 세션, wait: 마감했지만 아직 안 받은 세션} — 맨 위 '시세 반영' 칸이 시장마다 '어느 날
    종가까지인가' 를 말한다(장이 닫히는 시각이 시장마다 달라 한 칸에 섞인다, #34·#43). 새로 생긴 종목은 기준이 달라
    asof 가 다를 수 있다 — 시장마다 **가장 이른** 것을 적는다(새 종목은 그 업로드 무렵 종가가 asof 라, 막 닫혀 아직
    안 받은 세션 날짜가 될 수 있다 — 늦은 것을 적으면 '10-08 종가 반영 · 10-08 종가는 수집 대기' 처럼 한 줄이 제
    말을 뒤집는다, 독립 리뷰 2026-10-14). 값을 못 센 종목(`ka` None · 달력을 모르는 시장)은 날짜에 넣지 않는다 —
    한 종목도 못 센 시장은 '—' 로 남긴다(날짜를 적으면 그날 종가를 반영한 척하고, 빼면 그 시장이 조용히 사라진다)."""
    out: dict = {}
    for p in pos:
        o = out.setdefault(p["market"], {"asof": None, "wait": []})
        c = cmap.get(p["key"])
        if not c or c.get("ka") is None:
            continue
        if c.get("asof") and (o["asof"] is None or c["asof"] < o["asof"]):
            o["asof"] = c["asof"]
        o["wait"] = sorted(set(o["wait"]) | set(c.get("wait") or []))
        if c.get("wait"):                # 그 시장의 종가를 마감 뒤 몇 분에 받나(하루치 3시간 · Yahoo 만 받는 시장 30분)
            o["lag"] = max(int(o.get("lag") or 0), int(c.get("lag") or 0))
    return out


def _load_model_safe() -> dict:
    try:
        from bot.portfolio import load
        return load() or {}
    except Exception:                                          # noqa: BLE001
        return {}


_MK_ORDER = ("KR", "US", "JP", "TW", "CN_A", "HK")


def _asof_order(asof: dict) -> list:
    return sorted(asof, key=lambda m: (_MK_ORDER.index(m) if m in _MK_ORDER else 99, m))


def asof_line(asof: dict) -> str:
    """맨 위 '시세 반영' 칸 — '한국 10-14 · 미국 10-13 · 홍콩 —'. 장이 닫히는 시각이 시장마다 달라 요약 칸에 서로 다른 날의
    종가가 섞인다 — 자산 기록(업로드)과 따로, 금액을 **어느 날 종가까지** 굴렸는지 시장마다 적는다(사용자 2026-10-15
    "자산 업데이트 여부로도 나눠야" · #34·#43·#228). 한 종목도 못 센 시장은 '—'."""
    return " · ".join(f"{_mk_name(mk)} {asof[mk]['asof'][5:] if asof[mk].get('asof') else '—'}"
                      for mk in _asof_order(asof))


def asof_wait_note(asof: dict) -> str:
    """'아직 안 받은 종가: 한국 10-15(마감 3시간 뒤 수집 — 받으면 다음 갱신에 반영)' — 마감은 했지만 아직 안 받은 세션만
    (요약 아래 줄). 반영한 날짜는 맨 위 '시세 반영' 칸이 말하므로 여기서 되풀이하지 않는다(#395). 없으면 ''."""
    parts = [f"{_mk_name(mk)} {', '.join(d[5:] for d in asof[mk]['wait'])}(마감 {_lag_txt(asof[mk].get('lag'))} 뒤 "
             "수집 — 받으면 다음 갱신에 반영)" for mk in _asof_order(asof) if asof[mk].get("wait")]
    return ("아직 안 받은 종가: " + " · ".join(parts)) if parts else ""


def pnl_span_head(ctx: dict | None, positions: list[dict], g: str, *, live: bool = False) -> str:
    """요약 칸 이름의 앞머리 — 그 칸이 **실제로 몇 세션의 등락**을 담았나(사용자 2026-10-15 "알아서 맞는 방향으로").
    기준은 '오늘보다 앞선 가장 최근 날짜의 마지막 업로드'(사용자 2026-10-14)라, 며칠 업로드를 안 했거나 기준 업로드가 장
    마감 전이었으면 칸에 두 세션 이상이 쌓인다 — 그때 '전일대비' 라고 적으면 거짓이다(#34). 값은 바꾸지 않고 이름이 사실을
    말한다: 반영한 세션이 모두 하나면 '전일대비', 둘 이상이면 'MM-DD 기록 이후(N세션)', 아직 없으면 '(반영한 세션 없음)'.
    기준 날 뒤 새로 생긴 종목은 기준이 달라 세지 않는다(그것뿐이면 세되 날짜도 그 종목의 기준인 지금 기록 업로드로).
    한 세션이어도 그 뒤 마감한 세션을 아직 받는 중이면 '전일대비' 가 아니다. 값을 못 센 종목·거래소 달력을 모르는 시장은
    빼고, 아무것도 못 셌으면 'MM-DD 기록 대비'. 재료가 없으면(ctx 없음) 옛 이름 '전일대비'.
    `live=True` = 화면이 실시간 등락을 이어 붙였을 때의 이름 — 이어 붙일 세션(`live_d`)이 있는 종목은 세션이 하나 늘고
    그 세션은 더는 '받는 중' 이 아니다(화면 JS 가 그 그룹의 모든 대상에 붙였을 때만 이 이름으로 바꾼다)."""
    if not ctx or not ctx.get("cum") or not ctx.get("at"):
        return "전일대비"
    if ctx.get("today") and ctx.get("cur_ts") is None:   # 업로드 시각이 없는 기록 — 기준 날짜가 지금 시각이라 적을 날이 없다
        return "전일대비"
    md, cmap, newk = ctx["at"][:5], ctx["cum"], ctx.get("newk") or set()

    def _ns(skip_new: bool) -> list:
        return [int(c.get("sess") or 0) + (1 if live and c.get("live_d") else 0) for p in positions
                if (g == "all" or (p["market"] == "KR") == (g == "KR"))
                and not (skip_new and p["key"] in newk)
                for c in (cmap.get(p["key"]),) if c and c.get("ka") is not None]
    ns = _ns(True)
    if not ns and newk:
        # 값을 센 것이 기준 날 뒤 새로 생긴 종목뿐 — 그 종목은 **지금 기록** 업로드부터 셌다. 기준 날짜를 적으면 다른 기준의
        # 세션 수를 그 날짜에 붙이게 된다(독립 리뷰 2026-10-15) → 날짜도 지금 기록의 것
        ns = _ns(False)
        try:
            md = datetime.fromtimestamp(float(ctx["cur_ts"]), _KST).strftime("%m-%d")
        except (TypeError, ValueError, OverflowError, OSError, KeyError):
            pass
    if not ns:
        return f"{md} 기록 대비"
    lo, hi = min(ns), max(ns)
    # 마감했지만 아직 안 받은 세션이 뒤에 있으면 그 칸은 '어제→오늘' 이 아니다 — 한 세션이어도 '전일대비' 라 적지 않는다
    def _waits(c: dict) -> bool:
        w = set(c.get("wait") or [])
        return bool(w) and not (live and c.get("live_d") and w <= {c["live_d"]})
    waiting = any(_waits(cmap.get(p["key"]) or {}) for p in positions
                  if g == "all" or (p["market"] == "KR") == (g == "KR"))
    if lo == hi == 1 and not waiting:
        return "전일대비"
    if hi == 0:
        return f"{md} 기록 이후(반영한 세션 없음)"
    return f"{md} 기록 이후({hi if lo == hi else f'{lo}~{hi}'}세션)"


PNL_BASIS = "종가 기준"
LIVE_POLL_SEC = 600                # 실시간 등락을 다시 묻는 간격(사용자 2026-10-19 "10분에 한번씩") — 화면 주기이자 서버
#                                    표(`live_pct.lookup(fresh=)`)의 신선도라 탭이 몇 개든 한 종목은 이 간격에 한 번만 받는다


def live_ticker(p: dict) -> str:
    """`live_pct` 가 받는 티커 — 국내는 6자리 코드(원천이 숫자 코드만 받는다), 그 밖은 자산 기록의 티커(접미 포함)."""
    if p.get("market") == "KR":
        return str(p.get("ticker") or "")
    return str(p.get("sym") or p.get("ticker") or "").upper()


def _pnl_html(positions: list[dict], skip: dict, look, *, fx: dict | None = None, ctx: dict | None = None) -> str:
    """요약 칸 3개 + 화면 JS 가 다시 셀 재료(JSON). 등락은 장종료가 기준(`cum_lookup`)이고 서버가 굽는다. 화면은 해외
    원화 금액에 곱할 환율을 1시간마다, 이어 붙일 세션(`live_d`)이 있는 종목의 실시간 등락을 `LIVE_POLL_SEC` 마다 받는다
    (`_PNL_JS` — 사용자 2026-10-14 장종료가 기준 → 2026-10-19 "10분에 한번씩")."""
    import html as _h
    if not positions:
        return ""
    wait = fx is None                    # 서버가 환율을 안 받았다 — 화면이 곧 받는다('미확인' 이 아니라 '대기')
    sm = pnl_summary(positions, look, fx)
    cells = []
    for g, lab in PNL_GROUPS:
        lab = lab + ("(원화 · 환율 반영)" if g == "XX" else "")
        o = sm[g]
        if o["n_priced"] and o["base"]:
            pct = o["amt"] / o["base"] * 100
            cls = "hn-up" if o["amt"] > 0 else "hn-dn" if o["amt"] < 0 else ""
            v = f"<span class='{cls}'>{_signed_krw(o['amt'])} ({'+' if pct > 0 else ''}{pct:.2f}%)</span>"
        else:
            v = "—"
        sub = (f"{PNL_BASIS} {o['n_priced']}/{o['n']}종목" + (f" · 환율 {'대기' if wait else '미확인'} {o['n_nofx']}" if o["n_nofx"] else "")
               if o["n"] else "보유 없음")
        head, lhead = pnl_span_head(ctx, positions, g), pnl_span_head(ctx, positions, g, live=True)
        lh = f" data-lh='{_h.escape(lhead + ' · ' + lab)}'" if lhead != head else ""
        cells.append(f"<div class='stat'{lh}><div class='k'>{_h.escape(head)} · {lab}</div>"
                     f"<div class='v' id='hn-pnl-{g}'>{v}</div>"
                     f"<div class='hn-pnl-sub' id='hn-pnl-{g}-sub'>{_h.escape(sub)}</div></div>")
    fbm = (ctx or {}).get("fb") or {}
    cum = (ctx or {}).get("cum") or {}

    def _ld(p) -> dict:
        # 실시간 등락을 이어 붙일 세션과 그 시세를 물을 티커(`live_pct` 꼴) — 종가 누적이 있는 종목만(#43 지어내지 않는다)
        c = cum.get(p["key"]) or {}
        if not c.get("live_d") or c.get("ka") is None:
            return {}
        return {"t": live_ticker(p), "ld": c["live_d"], "m": p["market"],
                **({"lx": c["live_x"]} if c.get("live_x") else {})}
    data = [{"k": p["key"], "v": round(p["value"]), "g": "KR" if p["market"] == "KR" else "XX",
             "p": look(p)[0], **({"c": currency_of(p["market"])} if currency_of(p["market"]) else {}),
             **({"fb": fbm[p["key"]]} if p["key"] in fbm else {}), **_ld(p)}
            for p in positions]
    am = (ctx or {}).get("asof") or {}
    asof_data = [{"m": mk, "n": _mk_name(mk), "d": (am[mk].get("asof") or "")[5:]} for mk in _asof_order(am)]
    if ctx:
        bl = (f"기준 = 오늘 자산 기록({ctx['at']} 업로드 — 앞선 날짜의 기록이 아직 없음)" if ctx.get("today") else
              f"기준 = 오늘보다 앞선 가장 최근 날짜의 마지막 자산 기록({ctx['at']} 업로드 · KST 날짜 기준 — 하루에 "
              "여러 번 올려도 그날 마지막 것)")
        bl += (" · 그 평가금액 × 그 뒤 장종료가 누적 등락(그 뒤 마감한 세션마다의 종가 등락을 곱함 · 하루치에 없는 ETF·"
               "우선주·홍콩은 Yahoo 일봉 종가) — 장 중이거나 마감했지만 종가를 아직 안 받은 세션은 화면이 "
               f"{LIVE_POLL_SEC // 60}분마다 받는 실시간 등락(정규장 · 직전 종가 대비)을 이어 붙임")
        if ctx.get("new"):
            bl += f" · 기준 날 이후 새로 생긴 {ctx['new']}종목은 지금 기록 금액"
    else:
        bl = "평가금액 × 등락률"
    note = (bl + "로 추정한 금액입니다 — 자산 기록에 수량이 없어 평가금액을 씁니다 · 해외는 "
            "원화 기준(주가 등락에 그 통화의 원화 환율 변동을 곱함 · 네이버 원화 환율 · 화면이 1시간마다 받음 — 종가와 "
            "환율의 시각이 달라 추정치입니다)"
            + (f" · 티커를 못 찾은 상품 {skip['n']}개({_krw(skip['value'])})는 뺌" if skip.get("n") else ""))
    an = asof_wait_note((ctx or {}).get("asof") or {})
    sk = skip_names_line(skip)
    return (f"<div class='stat-grid hn-pnl'>{''.join(cells)}</div>"
            f"<div class='hn-pnl-note'>{_h.escape(note)}</div>"
            + (f"<div class='hn-pnl-note' id='hn-pnl-skip'>{_h.escape(sk)}</div>" if sk else "")
            + (f"<div class='hn-pnl-note' id='hn-pnl-wait'>{_h.escape(an)}</div>" if an else "")
            + f"<script type='application/json' id='hn-pnl-data' data-basis='{_h.escape(PNL_BASIS)}' "
            f"data-poll='{LIVE_POLL_SEC}'>"
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + "</script>"
            + "<script type='application/json' id='hn-asof-data'>"
            + json.dumps(asof_data, ensure_ascii=False).replace("</", "<\\/") + "</script>"
            + ("" if wait else "<script type='application/json' id='hn-fx-data'>"
               + json.dumps(fx, ensure_ascii=False).replace("</", "<\\/") + "</script>"))


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


def _item_li(it: dict, off: bool, *, unseen: bool = False) -> str:
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
    if it.get("topup"):                  # 저녁 보강으로 붙은 항목 — 아침 수집분과 모은 시각이 다르다(#43)
        body += f" · 저녁 보강({_hm(it['topup'])})"
    if unseen:                           # 보관 정리 뒤에 붙은 항목 — AI 가 아직 안 봤다(무관 판정이 아니다)
        body += " · 아직 정리 안 됨"
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


def _snap_tables(ws, lookup_ticker, mk: str, d: str) -> tuple[dict, dict, dict] | None:
    """그 날 하루치의 ({티커: 등락%}, {티커: 업종 한국어}, {티커: 종가}) — 파일이 없으면 None. 하루치 원본은 미국 한 장이 수 MB
    라 재생성마다(지난 날 조각까지 30일 × 시장) 다시 풀면 낭비다 → mtime 으로 무효화하는 작은 표만 남긴다(#61).
    국가별 보드의 원본 캐시(`world_sectors._cached_load`, 6장)는 쓰지 않는다 — 30일을 훑으면 그 보드의 캐시를
    밀어낸다. 업종 이름은 하루치가 실은 원천 한국어 라벨(`labels.ind` — 「국가별 섹터혼합」 과 같은 이름, #38)이고
    없으면 원천 영문 그대로다."""
    path = ws.snap_path(mk, d)
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        return None
    hit = _PREV_CACHE.get((mk, d))
    if hit and hit[0] == mt and isinstance(hit[1], tuple):
        return hit[1]
    snap = ws._gz_load(path)
    rows = snap.get("rows") if isinstance(snap, dict) else None
    if not isinstance(rows, list):
        return None
    lab = snap.get("labels") if isinstance(snap.get("labels"), dict) else {}
    lab = lab.get("ind") if isinstance(lab.get("ind"), dict) else {}
    tbl, ind, px = {}, {}, {}
    for r in rows:
        try:
            k = lookup_ticker(r[0]).upper()
            v = r[4]
            en = r[3]
        except Exception:                                      # noqa: BLE001
            continue
        if not k:
            continue
        if isinstance(v, (int, float)) and v == v:
            tbl[k] = float(v)
        c = r[8] if len(r) > 8 else None   # 종가(`world_sectors.ROW`) — 카드 괄호의 '그 종가'(사용자 2026-10-14)
        if isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and c > 0:
            px[k] = float(c)
        if isinstance(en, str) and en:          # 업종 칸이 깨져도 등락 표는 산다(곁들이가 본체를 지우지 않게, #315)
            v2 = lab.get(en)
            ind[k] = v2 if isinstance(v2, str) and v2 else en
    while len(_PREV_CACHE) >= _PREV_CACHE_MAX:
        _PREV_CACHE.pop(next(iter(_PREV_CACHE)))
    _PREV_CACHE[(mk, d)] = (mt, (tbl, ind, px))
    return tbl, ind, px


def prev_changes(date: str, now: datetime | None = None):
    """전날 등락률 조회기 — `look(stock) -> (등락%, 세션 날짜, 사유)` · `look.px(stock) -> (그 세션 종가, 날짜) | None`.

    값은 「국가별 심리지도」가 이미 받아 둔 TradingView 하루치(`world_sectors` 스냅샷)에서 읽는다 —
    디스크만, 네트워크 0(#38 같은 하루치를 두 번 받지 않는다). '전날' = 기록 날짜 **앞** 그 시장의 마지막
    거래일(`prev_sessions` — 휴장을 따른다). 그 하루치가 아직 없으면(미국은 한국 시각 이튿날 아침에
    받는다) 값 대신 사유를 낸다 — 다음 재생성이 채운다(#43). 실패는 이 칸만 비운다(#315·#424)."""
    memo: dict = {}

    def _table(mk: str):
        if mk in memo:
            return memo[mk]
        out = (None, None, "", {})
        try:
            from bot import world_sectors as ws
            from bot.world_sector_pages import lookup_ticker
            ss = ws.prev_sessions(mk, date, 1)
            if not ss:
                out = (None, None, "전날 거래일을 달력에서 못 찾음", {})
            else:
                t = _snap_tables(ws, lookup_ticker, mk, ss[0])
                out = (t[0], ss[0], "", t[2]) if t is not None else \
                    (None, ss[0], _prev_missing_why(ws, mk, ss[0], now), {})
        except Exception as exc:                               # noqa: BLE001
            log.warning("holdings_news: 전날 등락 조회 실패(%s %s): %s", mk, date, exc)
            out = (None, None, "전날 등락 조회 실패", {})
        memo[mk] = out
        return out

    def look(st: dict) -> tuple[float | None, str | None, str]:
        mk = _PREV_MK.get(str(st.get("market") or "").upper())
        if not mk:
            return None, None, f"{st.get('market')} 시장은 전날 등락 원천이 없음"
        tbl, d, why_, _ = _table(mk)
        if tbl is None:
            return None, d, why_
        v = tbl.get(_snap_key(st, mk))
        if v is None:
            return None, d, f"{d} 하루치에 이 종목이 없음"
        return v, d, ""

    def px(st: dict) -> tuple[float, str] | None:
        """그 등락의 세션 종가 — 카드 괄호가 등락률 앞에 싣는다(사용자 2026-10-14 "실제 종가를 추가"). 모르면 None."""
        mk = _PREV_MK.get(str(st.get("market") or "").upper())
        if not mk:
            return None
        _, d, _, pxs = _table(mk)
        c = pxs.get(_snap_key(st, mk))
        return (c, d) if c is not None else None

    look.px = px
    return look


# ── 하루치에 없는 종목의 종가(독립 리뷰 2026-10-14 High) ───────────────────────────────────────────────────
# TradingView 하루치(`tv_scanner`)는 **보통주·DR 만** 싣는다 — ETF·우선주는 구조적으로 없고, 홍콩은 하루치를 아예 받지
# 않는다. 그 종목들은 Yahoo 일봉 종가로 같은 식('기준 무렵 마지막 종가 → 같은 asof 세션 종가')을 센다. 받는 일은 봇
# 15분 주기(`refresh_closes`)가 하고 디스크(`closes.json`)에 남긴다 — 렌더는 네트워크 0 그대로다.
YCLOSE_RETRY_MIN = 30              # 못 받았거나 그날 봉이 아직 없을 때 다시 묻는 간격 — 실패마다 두 배(상한 `YCLOSE_RETRY_CAP`)
YCLOSE_RETRY_CAP = 8 * 60
YCLOSE_MARGIN_MIN = 30             # 하루치가 없는 시장의 세션 종가를 '받을 수 있다' 고 볼 마감 뒤 여유 — 장중 봉을 종가로 굽지 않게
YCLOSE_KEEP_DAYS = 60
YCLOSE_CHUNK = 50                  # 한 번에 묻는 종목 수
YCLOSE_BASE_SLACK_DAYS = 7         # 기준 세션 날 봉이 없을 때 그 앞 봉을 쓸 수 있는 거리(연휴 한 번은 넘는다)


def _closes_path() -> Path:
    return _DIR / "closes.json"


def load_closes() -> dict:
    """{보유 키: {y: Yahoo 심볼, bars: {날짜: 종가}, at: 받은 epoch, tried: 물은 epoch, fails, why}} — 디스크만."""
    try:
        d = json.loads(_closes_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("holdings_news: 종가 캐시 읽기 실패: %s", exc)
        return {}


def yahoo_symbol(st: dict, *, alt: bool = False) -> str:
    """Yahoo 일봉 심볼 — 국내는 자산 기록이 준 접미(.KS/.KQ)가 있으면 그대로, 없으면 .KS(ETF 는 전부 유가증권 시장)
    → 못 받으면 `alt` 로 .KQ 를 한 번 더. 미국은 BRK.B → BRK-B. 그 밖 시장은 자산 기록 심볼 그대로(Yahoo 꼴로 푼다)."""
    mk = str(st.get("market") or "").upper()
    tk = str(st.get("ticker") or "").upper()
    sym = str(st.get("sym") or tk).strip().upper()
    if mk == "KR":
        return sym if sym.endswith((".KS", ".KQ")) else f"{tk}.{'KQ' if alt else 'KS'}"
    return sym.replace(".", "-") if mk == "US" else sym


def yahoo_ratio(ent, need: dict) -> tuple[float | None, str]:
    """(지금 종가 ÷ 기준 무렵 종가, 사유). `need` = {from: 기준 무렵 세션, to: asof 세션, ready: 그 세션 종가가 확정된
    epoch}. to 세션 봉은 **그 세션이 끝난 뒤에 받은 것**만 쓴다 — 장중에 받아 둔 봉을 종가로 쓰면 지어낸 값이다."""
    if not isinstance(ent, dict) or not ent.get("bars"):
        return None, (str(ent.get("why")) if isinstance(ent, dict) and ent.get("why") else "아직 안 받음(봇 주기가 받는다)")
    bars = ent["bars"]
    if need["to"] not in bars or float(ent.get("at") or 0) < float(need.get("ready") or 0):
        if ent.get("why"):               # 마지막 물음이 실패했다(받기 실패·일시정지) — 그 사유가 '아직' 보다 행동 가능하다(#82)
            return None, str(ent["why"])
        return None, (f"Yahoo 일봉에 {need['to']} 종가가 아직 없음(봇 주기가 다시 묻는다 — 못 받으면 "
                      f"{YCLOSE_RETRY_MIN}분부터 두 배씩 쉬었다가)")
    prior = [d for d in bars if d <= need["from"]]
    # 기준 세션 날 봉이 없으면(원천 결측) 그 앞 가장 늦은 봉 — 단 `YCLOSE_BASE_SLACK_DAYS` 안에서만. 더 옛 봉이면 그
    # 사이 등락이 섞인 값을 지어낸다(델타 리뷰 2026-10-14 High: 짧은 응답 뒤 09-25 봉으로 +102%)
    if not prior or (datetime.fromisoformat(need["from"]) - datetime.fromisoformat(max(prior))).days > YCLOSE_BASE_SLACK_DAYS:
        return None, f"Yahoo 일봉에 {need['from']} 무렵 종가가 없음"
    b, t = bars[max(prior)], bars[need["to"]]
    if not (isinstance(b, (int, float)) and isinstance(t, (int, float)) and b > 0 and t > 0):
        return None, "Yahoo 종가가 0 이하"
    return t / b, ""


def cum_lookup(base_ts: float, now: datetime | None = None, *, closes: dict | None = None):
    """기준 시각(전일 자산 기록의 업로드 시각) 뒤의 **장종료가** 누적 등락 조회기 — `cum(stock) -> dict | None`.

    사용자 2026-10-14 "오늘이 10/9 이고 그전 자산이 10/5 기준이면 10/5 와 비교" + 같은 날 "실시간으로 가져오는게 부하가
    크면 기준을 장종료가격으로" — 기준 업로드 뒤에 **마감한** 세션마다 그날 종가 등락(「국가별 심리지도」 하루치 = 직전
    종가 대비 종가)을 곱한다 = '기준 무렵 마지막 종가 → 받아 둔 마지막 종가'. 하루치에 없는 종목(ETF·우선주 등 —
    하루치는 보통주·DR 만)과 하루치를 받지 않는 시장(홍콩)은 같은 식을 Yahoo 일봉 종가(`closes` — 봇 주기가 받아 둔
    디스크 캐시)로 센다. 렌더는 디스크만 읽는다(네트워크 0 — 옛 화면은 열려 있는 동안 1분마다 보유 전 종목 시세를
    물었다 · 2026-10-19 부터 화면이 `live_d` 세션만 10분마다 실시간으로 잇는다). 반환 키:
      ka   = 곱(1+r) 또는 종가 비 — 빠진 세션·종가가 있으면 None(지어내지 않는다)
      n    = 기준 뒤 마감해 센 세션 수 · asof = 반영한 마지막 종가 세션(센 세션이 없으면 기준 무렵 마지막 종가 세션)
      wait = 마감했지만 아직 안 받은 세션(하루치는 장 마감 `READY_MIN` 뒤 · 하루치 없는 시장은 `YCLOSE_MARGIN_MIN` 뒤) —
             그 종가는 빼고 앞까지만 센다
      hol  = 휴장 추정으로 0 으로 센 세션(카드 툴팁이 이름을 댄다) · src = 'tv'(하루치) / 'yahoo'(일봉 종가)
      live_d = 화면이 실시간 등락으로 이어 붙일 세션(안 센 세션이 딱 하나 — 지금 열린 세션 또는 종가 수집 전 세션) · 없으면 None
      need = Yahoo 종가가 필요하면 {key, y, from, to, ready}(봇 주기 `refresh_closes` 가 모아 받는다) · why = 못 셀 때 사유
    휴장 추정 세션(`market_day_status` 'holiday' — 받을 창 내내 원천 값이 직전 세션과 같았다)은 등락 0 으로 센다.
    ⚠️ 원천이 그 세션을 끝내 안 실은 경우도 같은 갈래라 그때는 그날 등락이 빠진다(재지 못함, #165 — 그래서 이름을 댄다).
    ⚠️ 기준 업로드가 장중이었으면 그 세션 종가까지를 통째로 센다(업로드 시각의 가격을 모른다 — 화면이 추정이라 밝힌다)."""
    now = _now(now)
    memo: dict = {}
    now_utc = now.astimezone(timezone.utc)
    base_utc = datetime.fromtimestamp(float(base_ts), timezone.utc)
    cache = {"v": closes}

    def _closes() -> dict:
        if cache["v"] is None:
            cache["v"] = load_closes()
        return cache["v"]

    def _plan(cal: str, snap: bool):
        """(기준 무렵 세션, [(세션, 표 | None=휴장 0 | '' = Yahoo 로 셀 세션)], 대기, 깨진 사유, {세션: 마감 epoch},
        {세션: {티커: 종가}} — 하루치가 있는 세션과 기준 무렵 세션만)."""
        if (cal, snap) in memo:
            return memo[(cal, snap)]
        out = None
        try:
            from bot import world_sectors as ws
            from bot.world_sector_pages import lookup_ticker
            state = ws.load_state() if snap else None
            d0 = base_utc.astimezone(_KST).date() - timedelta(days=14)
            bsess, days, wait, broken, ctime, pxs, opn = None, [], [], [], {}, {}, None
            for i in range((now.date() - d0).days + 2):
                d = (d0 + timedelta(days=i)).isoformat()
                t = ws.session_times(cal, d)
                if t and t[0] <= now_utc < t[1] and t[1] > base_utc:
                    opn = d                  # 지금 열려 있는 세션 — 실시간 등락이 이어 붙을 수 있는 자리(`live_d`)
                    ctime[d] = t[1].timestamp()          # 그 세션의 마감(`live_x` — 화면이 언제 그만 묻나)
                if not t or t[1] > now_utc:
                    continue
                if t[1] <= base_utc:
                    bsess = d
                    continue
                ctime[d] = t[1].timestamp()
                if wait:                     # 아직 안 받은 세션 뒤는 하루치가 있어도 곱하지 않는다 — 가운데 하루를 빼먹은
                    # 값이 된다. 평소엔 날짜 순으로 받아 이 상태가 안 나오지만 조용히 틀리지 않게(회귀가 스텁으로 잰다)
                    wait.append(d)
                    continue
                if not snap:                 # 하루치를 받지 않는 시장 — 마감 뒤 여유가 지나면 Yahoo 종가로 센다
                    if t[1] + timedelta(minutes=YCLOSE_MARGIN_MIN) <= now_utc:
                        days.append((d, ""))
                    else:
                        wait.append(d)
                    continue
                tb = _snap_tables(ws, lookup_ticker, cal, d)
                if tb is not None:
                    days.append((d, tb[0]))
                    pxs[d] = tb[2]
                    continue
                st = ws.market_day_status(cal, d, now, state)
                if st == "pending":
                    wait.append(d)
                elif st == "holiday":
                    days.append((d, None))   # 휴장 추정 — 등락 0(원천 값이 직전 세션과 같았다)
                else:
                    broken.append((d, _prev_missing_why(ws, cal, d, now)))
            if snap and bsess:               # 기준 뒤 센 세션이 없으면 카드의 종가는 기준 무렵 종가다
                tb = _snap_tables(ws, lookup_ticker, cal, bsess)
                if tb is not None:
                    pxs[bsess] = tb[2]
            out = (bsess, days, wait, broken, ctime, pxs, opn)
        except Exception as exc:                               # noqa: BLE001
            log.warning("holdings_news: 누적 등락 계획 실패(%s): %s", cal, exc)
        memo[(cal, snap)] = out
        return out

    def cum(st: dict) -> dict | None:
        """None = 이 시장은 거래소 달력을 몰라 장종료가 등락을 셀 수 없다(부른 쪽이 '(—)' 에 `no_close_why` 를 단다).
        하루치를 받는 시장인데 계산이 깨지면 None 이 아니라 `ka` None + 사유(두 갈래의 처방이 다르다, #82)."""
        market = str(st.get("market") or "").upper()
        snap = _PREV_MK.get(market)
        plan = _plan(snap or market, bool(snap))
        if plan is None:
            if not snap:
                return None
            # 하루치를 받는 시장인데 계산이 깨졌다 — '받지 않는 시장' 이라 적으면 거짓 사유다
            return {"ka": None, "n": 0, "asof": None, "wait": [], "hol": [], "src": "tv", "need": None,
                    "why": "장종료가 등락 계산 실패(봇 로그 참고)"}
        bsess, days, wait, broken, ctime, pxs, opn = plan
        lag = _ws_ready_min() if snap else YCLOSE_MARGIN_MIN         # 마감 뒤 몇 분에 그 세션 종가를 받나
        # 실시간 등락(10분 간격 · 사용자 2026-10-19)을 이어 붙일 세션 — 아직 안 센 세션이 **딱 하나**(지금 열린 세션이거나
        # 마감했지만 종가를 아직 안 받은 세션)일 때만. 실시간 등락은 '직전 종가 대비' 하루치라, 안 센 세션이 둘이면
        # 하나를 빼먹은 값이 된다(그때는 종가 누적 그대로 · 화면이 세션 날짜를 맞춰 보고 붙인다)
        unc = list(wait) + ([opn] if opn and opn not in wait else [])
        live_d = unc[0] if len(unc) == 1 else None
        # 화면이 그 세션 시세를 그만 묻는 시각 — 마감 뒤 그 종가를 받고(`lag`) 페이지가 한 번 더 구워질 때까지(`TICK_MIN`).
        # 열어 둔 탭은 다시 읽기 전엔 페이지 재료가 그대로라, 이게 없으면 닫힌 장을 밤새 10분마다 묻는다(독립 리뷰 M1)
        live_x = int(ctime[live_d] + (lag + TICK_MIN) * 60) if live_d and live_d in ctime else None
        if live_x is not None and live_x <= now_utc.timestamp():
            # 그 시각이 지났는데 종가가 아직 안 왔다(원천이 늦음 · 렌더가 늦음) — 지난 기한을 실으면 지금 연 탭은 한 번도
            # 묻지 않는다(델타 리뷰). 지금부터 한 바퀴(수집 여유 + 재생성) 더 묻게 민다
            live_x = int(now_utc.timestamp() + (lag + TICK_MIN) * 60)
        out = {"ka": 1, "n": len(days), "asof": days[-1][0] if days else bsess, "wait": list(wait), "live_d": live_d,
               "live_x": live_x,
               "hol": [d for d, tbl in days if tbl is None], "src": "tv", "need": None, "lag": lag,
               "nosnap": not snap, "why": "",
               # 실제로 등락을 반영한 세션 수 — 휴장 추정(등락 0)은 빼고 놓친 세션(Yahoo 종가 비가 덮는다)은 넣는다.
               # 요약 칸 이름이 '전일대비' 인지 'N세션' 인지를 이 값이 정한다(`pnl_span_head`, 사용자 2026-10-15)
               "sess": sum(1 for _, tbl in days if tbl is not None) + len(broken)}
        if not days and not broken:      # 기준 뒤 마감해 센 세션이 없다 — 기준 무렵 종가 그대로(0)
            c = (pxs.get(bsess) or {}).get(_snap_key(st, snap)) if snap and bsess else None
            if c is not None:
                out["px"], out["px_d"] = c, bsess
            return out
        fac, miss_tk = [], []
        if snap and not broken:
            key = _snap_key(st, snap)
            for d, tbl in days:
                v = 0.0 if tbl is None else tbl.get(key)
                if v is None:
                    miss_tk.append(d)
                else:
                    fac.append(1 + v / 100)
            if not miss_tk:
                out["ka"] = math.prod(fac)
                # 카드의 종가 = 곱한 마지막 실제 세션의 종가(휴장 추정 세션은 직전 종가 그대로라 그 앞 세션) —
                # 센 세션이 없으면 기준 무렵 종가
                pd_ = next((d for d, tbl in reversed(days) if tbl is not None), bsess)
                c = (pxs.get(pd_) or {}).get(key) if pd_ else None
                if c is not None:
                    out["px"], out["px_d"] = c, pd_
                return out
        # 하루치에 없는 종목(ETF·우선주 등) · 하루치를 놓친 세션이 있는 시장 · 하루치를 받지 않는 시장(홍콩) —
        # Yahoo 일봉 종가로 같은 asof 까지. 휴장 추정 세션엔 Yahoo 봉이 없으므로 그 앞 실제 세션까지(독립 리뷰 2026-10-14)
        if not bsess:
            out.update(ka=None, why="기준 무렵 세션을 달력에서 못 찾음(14일 안)")
            return out
        # 비지 않는다 — 여기 오려면 놓친 세션이 있거나(`broken`), 하루치에 이 종목이 없는 실제 세션이 있거나(`miss_tk`),
        # 하루치를 받지 않는 시장의 세션('' 표)이 있다
        to = max([d for d, tbl in days if tbl is not None] + [d for d, _ in broken])
        k = st.get("key") or f"{market}:{st.get('ticker')}"
        ent = _closes().get(k)
        need = {"key": k, "y": (ent or {}).get("y") or yahoo_symbol(st), "from": bsess, "to": to,
                "ready": ctime.get(to, 0) + YCLOSE_MARGIN_MIN * 60}
        r, why = yahoo_ratio(ent, need)
        if r is not None:                # 종가 비를 셀 수 있었으면 그 끝 종가가 카드의 종가다
            out["px"], out["px_d"] = float(ent["bars"][to]), to
        if not snap:
            head = f"{_mk_name(market)} 시장은 TradingView 하루치를 받지 않음"
        elif broken:
            ws_ = [w for _, w in broken]  # 기준이 오래되면 수십 세션이 놓친 세션이 된다 — 둘만 적고 수를 말한다(#45)
            head = ("TradingView 하루치를 놓친 세션이 있음(" + " · ".join(ws_[:2])
                    + (f" 외 {len(ws_) - 2}세션" if len(ws_) > 2 else "") + ")")
        else:
            head = (f"이 종목이 {', '.join(miss_tk)} TradingView 하루치에 없음(하루치는 보통주·DR 만 — ETF·우선주 등 · "
                    "거래정지·신규상장도)")
        out.update(src="yahoo", need=need, ka=r, asof=to, hol=[], broken=[d for d, _ in broken],  # 휴장 0 은 하루치 곱의 일
                   why="" if r is not None else f"{head} — Yahoo 일봉 종가로 채우는데 {why}")
        return out

    return cum


def _yf_daily(syms: list[str], start: str, end: str) -> tuple[dict, str]:
    """({심볼: {날짜: 종가}}, 사유) — Yahoo 일봉 한 묶음(`auto_adjust=False` — 배당·분할 소급 조정을 섞지 않은 그날
    종가). 행 단위로 거른다(컬럼별 dropna 는 날짜를 갈라 놓는다 — `bollinger_board` 와 같은 처방)."""
    try:
        import yfinance as yf
        df = yf.download(syms, start=start, end=end, interval="1d", group_by="ticker", auto_adjust=False,
                         threads=True, progress=False, timeout=20)
    except Exception as exc:                                   # noqa: BLE001
        return {}, f"Yahoo 일봉 실패({type(exc).__name__}: {_exc_msg(exc, 60)})"
    if df is None or getattr(df, "empty", True):
        return {}, "Yahoo 일봉 응답 비어 있음"
    multi = getattr(df.columns, "nlevels", 1) > 1
    lv0 = set(df.columns.get_level_values(0)) if multi else set()
    out: dict = {}
    for y in syms:
        try:
            if multi and y not in lv0:
                continue
            sub = (df[y] if multi else df).dropna(subset=["Close"])
            bars = {ix.strftime("%Y-%m-%d"): round(float(v), 6) for ix, v in sub["Close"].items() if float(v) > 0}
        except Exception:                                      # noqa: BLE001
            continue
        if bars:
            out[y] = bars
    return out, ""


def refresh_closes(now: datetime | None = None, *, model: dict | None = None, download=None) -> dict:
    """하루치에 없는 보유(ETF·우선주 등)·하루치 없는 시장(홍콩)의 Yahoo 일봉 종가를 받아 `closes.json` 에 둔다 — 봇
    15분 주기가 렌더 **앞에** 부른다(네트워크는 여기만 · 렌더는 디스크만). 렌더와 같은 `pnl_context` 로 필요한 종목과
    날짜를 정하고(#35), 이미 그 종가가 있으면 묻지 않는다. 못 받으면 `YCLOSE_RETRY_MIN` 부터 두 배씩 쉬었다 묻는다
    (상한 `YCLOSE_RETRY_CAP`, #303) — 쉬는 간격은 **그 목표 세션**의 실패만 센다(목표가 다음 세션으로 넘어가면 처음부터:
    어제의 실패가 오늘 종가를 8시간 붙잡지 않게, 독립 리뷰 2026-10-14). 반환 = {asked, got, why}.

    봉은 종가가 확정된 것(`sure` — 그 물음의 목표 세션까지: 목표는 마감 뒤에만 정해진다)만 다음 물음에 남긴다. 그 뒤의
    봉(장중에 받았을 수 있다)은 다음 응답이 갈아 끼우거나 버린다 — 응답에 그날 봉이 없다고 옛 장중 값이 종가로 남으면
    안 되고(3차 리뷰 High), 응답이 짧다고 확정된 종가까지 버리면 기준 무렵 종가가 사라진다(델타 리뷰 2026-10-14 High)."""
    now = _now(now)
    pos, _ = pnl_positions(model)
    if not pos:
        return {"asked": 0, "got": 0, "why": ""}
    ctx = pnl_context(pos, now, model)
    needs = {n["key"]: n for n in ((c or {}).get("need") for c in (ctx.get("cum") or {}).values()) if n}
    cache = load_closes()
    t = now.timestamp()
    old = t - YCLOSE_KEEP_DAYS * 86400   # 판 종목의 줄은 60일 뒤 버린다 — 파일이 보유 이력만큼 자라지 않게
    gone = [k for k, e in cache.items() if k not in needs and float((e or {}).get("tried") or 0) < old]

    def _due(k, n):
        ent = cache.get(k)
        if yahoo_ratio(ent, n)[0] is not None:
            return False
        if not isinstance(ent, dict) or not ent.get("tried") or ent.get("for") != n["to"]:
            return True
        wait_min = min(YCLOSE_RETRY_MIN * 2 ** min(max(int(ent.get("fails") or 0), 1) - 1, 8), YCLOSE_RETRY_CAP)
        return t - float(ent["tried"]) >= wait_min * 60

    def _save():
        for k in gone:
            cache.pop(k, None)
        try:
            path = _closes_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("holdings_news: 종가 캐시 저장 실패: %s", exc)

    due = {k: n for k, n in needs.items() if _due(k, n)}
    if not due:
        if gone:                         # 물을 게 없어도 판 종목 줄은 정리한다(쓰는 건 그때만)
            _save()
        return {"asked": 0, "got": 0, "why": ""}
    try:
        from bot.finviz_client import yf_paused
        paused = yf_paused()
    except Exception:                                          # noqa: BLE001
        paused = False
    if paused:                           # 묻지 않았다는 사실을 카드가 말하게 — 안 남기면 '아직 안 받음' 이 이유를 숨긴다
        why = "yfinance 일시정지(YF_PAUSE) — 종가를 묻지 않음"
        for k, n in needs.items():       # 쉬는 중인 종목도 — 옛 실패 사유가 지금 상태를 가리지 않게
            if yahoo_ratio(cache.get(k), n)[0] is None:
                cache.setdefault(k, {})["why"] = why
        _save()
        return {"asked": 0, "got": 0, "why": why}
    fetch = download or _yf_daily
    start = (datetime.fromisoformat(min(n["from"] for n in due.values())) - timedelta(days=10)).date().isoformat()
    end = (now.date() + timedelta(days=1)).isoformat()
    # 기준은 장부의 가장 최근 앞선 날이라 오래될 수 있다(그날이 60일 전이어도 남긴다) — 창 시작보다 앞은 버리지 않는다
    keep = min((now.date() - timedelta(days=YCLOSE_KEEP_DAYS)).isoformat(), start)
    got, why_all = 0, ""

    def _ask(items: dict, *, count: bool = True) -> list:
        """`count` = 이번 바퀴의 실패로 세나 — 코스닥 접미로 한 번 더 묻는 건 같은 바퀴라 두 번 세지 않는다(그 사유는
        앞 사유 뒤에 붙인다 — .KS 가 일시 오류로 못 받은 사유를 '.KQ 가 없음' 이 덮지 않게)."""
        nonlocal got, why_all
        miss = []
        keys = list(items)
        for i in range(0, len(keys), YCLOSE_CHUNK):
            chunk = keys[i:i + YCLOSE_CHUNK]
            bars_by, why = fetch([items[k] for k in chunk], start, end)
            why_all = why or why_all
            for k in chunk:
                ent = cache.setdefault(k, {})
                if ent.get("for") != due[k]["to"]:
                    ent.update({"fails": 0, "for": due[k]["to"]})
                ent["tried"] = t
                bars = bars_by.get(items[k])
                if bars:
                    sure = str(ent.get("sure") or "")
                    merged = {**{d: v for d, v in (ent.get("bars") or {}).items() if d < start or d <= sure}, **bars}
                    ent.update(y=items[k], at=t, bars={d: v for d, v in merged.items() if d >= keep}, why="",
                               sure=max(sure, due[k]["to"]))
                    ok = yahoo_ratio(ent, {**due[k], "y": items[k]})[0] is not None
                    if not ok:           # 받았지만 그날 봉이 아직 없다 — 같은 목표의 실패로 센다(받으면 목표가 바뀔 때 0 으로)
                        ent["fails"] = int(ent.get("fails") or 0) + count
                    got += ok
                else:
                    miss.append(k)
                    ent["fails"] = int(ent.get("fails") or 0) + count
                    w = why or f"Yahoo 일봉에 {items[k]} 가 없음"
                    ent["why"] = w if count or not ent.get("why") else f"{ent['why']} · {w}"
        return miss

    miss = _ask({k: n["y"] for k, n in due.items()})
    # 국내는 접미를 모르면 .KS 로 먼저 묻는다 — 없으면 코스닥(.KQ)으로 한 번 더. 자산 기록이 접미를 줬거나 한 번이라도
    # 받아 심볼을 배운 종목은 다시 갈아 보지 않는다(그때 못 받은 건 날짜 문제다 — 다른 심볼을 물으면 헛요청)
    alt = {k: f"{k.split(':', 1)[1]}.KQ" for k in miss if k.startswith("KR:") and due[k]["y"].endswith(".KS")
           and not (cache.get(k) or {}).get("y")
           and not str(next((p.get("sym") for p in pos if p["key"] == k), "")).upper().endswith(".KS")}
    if alt:
        _ask(alt, count=False)
    _save()
    if why_all:
        log.warning("holdings_news: Yahoo 일봉 %s", why_all)
    return {"asked": len(due), "got": got, "why": why_all}


def _snap_key(st: dict, mk: str) -> str:
    """하루치 표의 키 — 표는 `lookup_ticker` 꼴(국내 6자리 · 미국 BRK.B → BRK-B)."""
    key = (st["ticker"] if mk == "KR" else (st.get("sym") or st["ticker"])).upper()
    return key.replace(".", "-") if mk == "US" else key


# 업종(사용자 2026-10-14 "종목코드에 따른 업종을 같이 명시하고 그거에 맞게 뉴스를") — 제우스(079370, 반도체
# 장비) 카드에 컴투스 신작 게임 '제우스' 기사가 실렸다. 이름 검색이라 같은 이름의 게임·작품·다른 회사가 섞인다.
# 업종은 「국가별 심리지도」가 받아 둔 TradingView 하루치에서 읽는다(디스크만 · 전시장 · #38). 업종은 거의 안
# 바뀌므로 기록 날짜 앞 거래일 하루치가 없으면 몇 거래일 앞까지 본다.
IND_LOOKBACK = 5


def industry_lookup(date: str):
    """업종 조회기 — `ind(stock) -> 업종 한국어 이름` (모르면 ''). 실패는 빈칸만 남긴다(#315)."""
    memo: dict = {}

    def _tbl(mk: str) -> dict:
        if mk in memo:
            return memo[mk]
        out: dict = {}
        try:
            from bot import world_sectors as ws
            from bot.world_sector_pages import lookup_ticker
            # 가까운 날이 이긴다 — 그 날 하루치에 없는 종목(거래정지 등)은 더 앞 날에서 찾는다(리뷰 2026-10-14)
            for d in reversed(ws.prev_sessions(mk, date, IND_LOOKBACK) or []):
                t = _snap_tables(ws, lookup_ticker, mk, d)
                if t is not None:
                    out.update(t[1])
        except Exception as exc:                               # noqa: BLE001
            log.warning("holdings_news: 업종 조회 실패(%s %s): %s", mk, date, exc)
        memo[mk] = out
        return out

    def ind(st: dict) -> str:
        mk = _PREV_MK.get(str(st.get("market") or "").upper())
        return _tbl(mk).get(_snap_key(st, mk), "") if mk else ""

    return ind


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


def _close_tip(d: dict) -> str:
    """최신일 카드 괄호의 툴팁 — 무엇의 값인지(기준 시각·반영한 마지막 종가·아직 안 받은 종가)."""
    at, n, asof, wait = d.get("at") or "", int(d.get("n") or 0), d.get("asof"), d.get("wait") or []
    base = f"{asof} 종가 그대로" if asof else "기준 무렵 종가 그대로"
    if d.get("src") == "yahoo":          # 종가 비 — 기준 뒤 실제 세션이 있을 때만 이 갈래다(놓친 세션 하나뿐이면 n 은 0)
        tip = (f"{at}(KST) 자산 기록 대비 장종료가 누적 등락 — {(d.get('need') or {}).get('from', '기준 무렵')} 종가 → "
               f"{asof} 종가(Yahoo 일봉 · {_yahoo_reason(d)})")
    elif n:
        tip = (f"{at}(KST) 자산 기록 대비 장종료가 누적 등락 — 그 뒤 마감한 세션 {n}개의 종가 등락을 곱함"
               f"({asof} 종가까지 · TradingView 하루치 · 「국가별 심리지도」와 같은 하루치)")
    elif wait:                           # 마감은 했지만 하루치를 아직 안 받았다 — '마감한 세션이 없다' 가 아니다
        tip = f"{at}(KST) 자산 기록 뒤 마감한 세션의 종가를 아직 안 받아 0 — {base}"
    else:
        tip = f"{at}(KST) 자산 기록 뒤 마감한 세션이 아직 없어 0 — {base}"
    if d.get("hol"):
        tip += f" · {', '.join(d['hol'])} 휴장 추정(원천 값이 직전 세션과 같았음) — 등락 0 으로 셈"
    if wait:
        tip += f" · {', '.join(wait)} 종가는 아직 수집 전(장 마감 {_lag_txt(d.get('lag'))} 뒤) — 받으면 다음 갱신에 반영"
    return tip


def _px_txt(px, ccy: str | None) -> str:
    """카드 괄호 맨 앞의 종가 — 통화 기호 + 그 통화의 자리수(`quarterly_series.fmt_price` 공용 포맷터, #38).
    `px` = (종가, 세션 날짜) 또는 None."""
    if not px:
        return ""
    try:
        from bot.quarterly_series import fmt_price
        return fmt_price(px[0], ccy or "KRW")
    except Exception as exc:                                   # noqa: BLE001 — 곁들이가 본체를 지우지 않게(#315)
        log.warning("holdings_news: 종가 표기 실패: %s", exc)
        return ""


def _prev_html(prev, value: float | None = None, *, ccy: str | None = None, fx: dict | None = None,
               fb: float | None = None, px=None, key: str | None = None) -> str:
    """종목명 옆 `(종가 · +1.23% · 금액)` — 값이 없으면 `(—)` 에 사유를 툴팁으로(빈칸이 '0' 으로 읽히지 않게, #43).
    `px` = (그 등락의 끝 종가, 세션 날짜) — 사용자 2026-10-14 "가격, 등락률, 내 자산의 그 종목변동, 즉 실제 종가".

    `prev` = (등락%, 날짜 또는 최신일 재료 dict, 사유). 최신일 카드(dict)는 기준 날 자산 기록 대비 **장종료가 누적
    등락**(`_cum_prev`, 요약과 같은 값)이고 지난 날 카드(날짜)는 그날 기준 전날 하루 등락이다. 서버는 실시간 시세를
    묻지 않는다(사용자 2026-10-14 "기준을 장종료가격으로"). 최신일 카드는 화면이 다시 그린다(`js-hn-amt` — `_PNL_JS`):
    해외는 환율을 받아 원화 금액으로, 장 중·종가 수집 전 세션이 있으면 10분마다 실시간 등락을 이어 붙여(2026-10-19). 지난 날 카드엔 오늘 환율을 곱하지 않는다(다른 날의 등락이 된다)."""
    import html as _h
    if not prev:
        return ""
    v, d, why_ = prev
    latest = isinstance(d, dict)
    if v is None:                        # 등락을 못 셌으면 종가도 싣지 않는다 — 사유('하루치에 없음')와 모순되지 않게
        return f"<span class='hn-chg' title='{_h.escape(why_ or '전날 등락 없음')}'>(—)</span>"
    cls = "hn-up" if v > 0 else "hn-dn" if v < 0 else ""
    sign = "+" if v > 0 else ""
    pt = _px_txt(px, ccy)
    ptip = f"{pt} = {px[1]} 종가" if pt else ""
    tip = _close_tip(d) if latest else f"{d} 종가 기준 등락률 · TradingView(「국가별 심리지도」와 같은 하루치)"
    fxp = (fx or {}).get(ccy) if ccy and latest else None
    # 해외 원화 금액엔 보이는 표식 — 괄호 %(주가 등락)로 눈으로 나눠 보면 안 맞기 때문이다(#33·#228)
    won = "원화 " if fxp is not None else ""
    amt = f" · {won}{_signed_krw(value * krw_pct(v, fxp) / 100)}" if value else ""
    js = ""
    if amt:
        tip += " · 금액 = " + ("기준 날 " if latest else "") + "보유 평가금액 × 등락률(추정)"
        if ccy and not latest:
            tip += " · 그날 기준 하루 등락이라 현지통화 등락만(환율 변동 미반영)"
        elif latest:
            # 화면이 환율(해외)·실시간 등락(장 중·종가 수집 전 세션 — 10분마다)을 받으면 금액·툴팁을 다시 그린다 —
            # 그 재료(종가 누적 등락·금액·통화·기준 환율·종목 키·가격 표기·다시 그리기 전 툴팁)
            cu = ccy or "KRW"
            try:
                from bot.dashboard import _currency_sym
                sym = _currency_sym(cu)
            except Exception:                                  # noqa: BLE001
                sym = ""
            js = (f" js-hn-amt' data-p='{round(v, 6)}' data-v='{round(value)}"
                  + (f"' data-c='{_h.escape(ccy)}" if ccy else "")
                  + (f"' data-fb='{fb}" if fb and ccy else "") + (f"' data-px='{_h.escape(pt)}" if pt else "")
                  + (f"' data-k='{_h.escape(key)}" if key else "")
                  + f"' data-cu='{_h.escape(cu)}' data-sym='{_h.escape(sym)}"
                  + f"' data-tip='{_h.escape(ptip + ' · ' + tip if pt else tip)}")
            if ccy:
                tip += ((f" · {ccy} 환율은 화면이 곧 받습니다 — 지금은 현지통화 등락만" if fx is None else
                         f" · {ccy} 환율 미확인 — 현지통화 등락만") if fxp is None else
                        f" · 원화 기준(주가 {sign}{v:.2f}% · 원/{ccy} {'+' if fxp > 0 else ''}{fxp:.2f}%)")
    if pt:
        tip = f"{ptip} · {tip}"
    return (f"<span class='hn-chg {cls}{js}' title='{_h.escape(tip)}'>"
            f"({_h.escape(pt + ' · ') if pt else ''}{sign}{v:.2f}%{amt})</span>")


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
    if r == REQUEUE_REASON:
        return f"{r} — 새 기준으로 다시 정리"
    if r == TOPUP_REASON:
        return f"{r} — 아침 뉴스와 함께 다시 정리"
    return f"{r} — 다음 주기에 다시" if s.get("retry") else r


def _ind_html(ind: str) -> str:
    import html as _h
    return f" · <span class='hn-ind' title='업종 · TradingView(「국가별 섹터혼합」과 같은 분류)'>{_h.escape(ind)}</span>" \
        if ind else ""


def _added_html(s: dict) -> str:
    """낮에 자산 기록이 바뀌어 붙은 종목 — 아침 수집분과 창은 같지만 모은 시각이 다르다고 밝힌다(#43)."""
    import html as _h
    at = str(s.get("added_at") or "")
    return f" · 자산 기록 변경 뒤 {_h.escape(at[11:16])} 추가" if len(at) >= 16 else ""


def _amt_base(pnl: dict | None, s: dict, val: float) -> float:
    """최신일 카드 금액의 기준 — 요약과 같은 기준 날 평가금액(없으면 지금 금액)."""
    b = ((pnl or {}).get("base") or {}).get(s.get("key"))
    return b if b is not None else val


def _cum_prev(pnl: dict, s: dict):
    """최신일 카드의 괄호 값 — 전일대비 요약과 **같은** 기준 대비 장종료가 누적 등락(#38·#33). (값, 재료 dict, 사유)."""
    if s.get("key") not in (pnl.get("cum") or {}):
        return None
    c = pnl["cum"][s["key"]]
    if c is None:                        # 거래소 달력을 모르는 시장 — 사유를 단 '(—)'
        return None, {}, no_close_why(s.get("market"))
    ka = c.get("ka")
    return ((ka - 1) * 100 if ka is not None else None), {"at": pnl.get("at", ""), **c}, c.get("why") or ""


def _card_px(prev, look, s: dict):
    """카드 괄호의 종가 — 등락을 낸 **같은** 경로에서(#33): 최신일 누적(`_cum_prev` 재료 dict)이면 그 끝 종가, 지난 날
    (또는 누적을 못 쓴 최신일)이면 그 하루치의 종가. 실패는 이 칸만 비운다(#315)."""
    try:
        m = str(s.get("market") or "").upper()
        if m != "KR" and not currency_of(m):      # 통화를 모르면 싣지 않는다 — 위안 값에 원 기호를 붙이지 않게(리뷰 M1)
            return None
        d = prev[1] if prev else None
        if isinstance(d, dict):
            return (d["px"], d["px_d"]) if d.get("px") is not None else None
        return look.px(s) if hasattr(look, "px") else None
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: 카드 종가 조회 실패(%s): %s", s.get("key"), exc)
        return None


def _card(s: dict, *, final: bool = False, prev=None, latest: bool = False, value: float | None = None,
          fx: dict | None = None, ind: str = "", pnl: dict | None = None, px=None) -> str:
    """종목 카드. `px` = 괄호 맨 앞 종가 (값, 세션 날짜) — 등락과 **같은** 경로가 준 것(`_card_px`). `final` = 주기가 더는 이어서 정리하지 않는 날(`_live_dates` 밖) — 그날의 '대기' 를 '다음
    주기에' 라고 적으면 지킬 수 없는 약속이다(리뷰 2026-10-12)."""
    import html as _h
    items = s.get("items") or []
    st = s.get("status")
    # 새 기준으로 다시 정리하려다 실패했거나 그날 안에 못 끝낸 종목 — 옛 정리를 라벨을 달고 보여 준다
    # (지우기만 하면 멀쩡하던 정리가 흔적 없이 사라진다, 델타 리뷰 2026-10-13). 대기 중엔 안 보인다(곧 바뀐다).
    old = s.get("prev_pc") if isinstance(s.get("prev_pc"), dict) and (st == "failed" or (final and st != "ok")) \
        else None
    shown = old or s
    rel = shown.get("relevant")
    relset = set(rel) if isinstance(rel, list) else {it["n"] for it in items}
    # 보관 정리를 보여 줄 때 그 정리 뒤에 붙은 항목(저녁 보강 · `max_n` 보다 큰 번호)은 AI 가 본 적이 없다 — '무관하다고
    # 본' 것으로 흐리거나 세지 않고 '아직 정리 안 됨' 이라 적는다(독립 리뷰 M2 · #165)
    mx = old.get("max_n") if old else None
    unseen = {it["n"] for it in items if isinstance(mx, int) and it["n"] > mx}
    n_tg = sum(1 for it in items if it["src"] == "telegram")
    # 평가금액 — 최신일은 요약(`pnl_positions`)과 같은 지금의 자산 기록 값, 지난 날은 그날 기록의 값(#33)
    val = value if value is not None else float(s.get("value") or 0)
    drop = int(s.get("dropped_lines") or 0)
    offl = int(s.get("offtopic_lines") or 0)
    cwhy = s.get("check_why") or ""
    # 확인 단계의 결과 — 뺀 줄은 수를, 확인하지 못한 줄은 사유를 보이는 줄로(#43·#228)
    notes = "".join(
        f"<div class='hn-why'>{_h.escape(x)}</div>" for x in (
            f"근거 번호가 없거나 맞지 않아 버린 줄 {drop}개" if drop else "",
            f"투자와 무관한 소식(스포츠·연예 등)이라고 다시 확인해 뺀 줄 {offl}개" if offl else "",
            f"⚠️ 스포츠·연예 문맥으로 보이는 줄을 다시 확인하지 못해 그대로 둠 — {cwhy}" if cwhy else "") if x)
    if old:
        r = s.get("reason") or "사유 미기록"
        why = ("이어서 정리하는 기간 안에 차례가 오지 않음" if r in (REQUEUE_REASON, TOPUP_REASON) else r)
        # 보관한 까닭이 둘이다 — 정리 기준이 바뀜(`requeue_stale`) · 저녁 보강으로 새 뉴스가 붙음(`top_up`, #34)
        am = old.get("kind") == "topup"
        tag = ("직전 정리" if old.get("evening") else "아침 정리") if am else "옛 기준"
        lead = (f"저녁에 붙은 새 뉴스까지 넣어 다시 정리하지 못해 {tag}를 보여 줍니다(새 뉴스는 반영 안 됨)" if am
                else "새 기준으로 다시 정리하지 못해 옛 기준 정리를 보여 줍니다")
        pc = (f"<div class='hn-why'>⚠️ {lead} — {_h.escape(why)}</div>"
              + ("<div class='hn-pc'>" + _side(f"👍 Pros · 호재 관점({tag})", "hn-pro", old.get("pros") or [])
                 + _side(f"👎 Cons · 악재·리스크 관점({tag})", "hn-con", old.get("cons") or []) + "</div>"
                 if old.get("pros") or old.get("cons") else
                 f"<div class='hn-why'>{tag} 정리에도 호재·악재로 가른 줄이 없었음</div>"))
    elif st == "ok":
        if not s.get("pros") and not s.get("cons"):
            gone = [x for x in (f"{drop}줄은 근거 번호가 없거나 맞지 않아 버림" if drop else "",
                                f"{offl}줄은 투자와 무관한 소식(스포츠·연예 등)이라고 다시 확인해 뺌" if offl else "") if x]
            why = ("AI 가 쓴 줄 중 " + " · ".join(gone) + " — 아래 근거 목록에서 확인하세요" if gone else
                   "투자와 관련 있다고 본 항목이 없음 — 아래 근거 목록에서 확인하세요" if not relset else
                   "뉴스는 있지만 호재·악재로 가를 내용이 없음")
            pc = f"<div class='hn-why'>{_h.escape(why)}</div>"
        else:
            pc = ("<div class='hn-pc'>" + _side("👍 Pros · 호재 관점", "hn-pro", s.get("pros") or [])
                  + _side("👎 Cons · 악재·리스크 관점", "hn-con", s.get("cons") or []) + "</div>"
                  + notes)
    elif st == "failed":
        pc = (f"<div class='hn-why'>Pros/Cons 못 만듦({MAX_ANALYZE_TRIES}회 실패) — "
              f"{_h.escape(s.get('reason') or '사유 미기록')} · 뉴스만 표시</div>")
    elif final:
        pc = (f"<div class='hn-why'>Pros/Cons 정리를 마치지 못함 — "
              f"{_h.escape(s.get('reason') or '사유 미기록')} · 뉴스만 표시</div>")
    else:
        pc = f"<div class='hn-why'>Pros/Cons 대기 — {_h.escape(_pending_reason(s))}</div>"
    lis = "".join(_item_li(it, it["n"] not in relset and it["n"] not in unseen, unseen=it["n"] in unseen)
                  for it in items)
    off = len([it for it in items if it["n"] not in relset and it["n"] not in unseen])
    offnote = f" · 종목·투자와 무관하다고 본 {off}건은 흐리게" if off and (st == "ok" or old) else ""
    q = f"{s['name']} {s['ticker']}" + (f" {s['ko']}" if s.get("ko") else "")
    return (f"<div class='hn-card' data-hncard='1' data-mk='{_h.escape(s['market'])}' "
            f"data-pro='{1 if shown.get('pros') else 0}' data-con='{1 if shown.get('cons') else 0}' "
            f"data-tg='{1 if n_tg else 0}' data-q='{_h.escape(q)}'>"
            f"<div class='hn-h'><a class='hn-nm' href='{_h.escape(_lookup(s))}' title='종목분석 화면으로'>"
            f"{_h.escape(s['name'])}</a>{_prev_html(prev, _amt_base(pnl, s, val) if latest else val, ccy=currency_of(s.get('market') or ''), fx=fx, fb=((pnl or {}).get('fb') or {}).get(s.get('key')) if latest else None, px=px, key=s.get('key'))}"
            f"<span class='hn-tk'>{_h.escape(s['ticker'])}{_ind_html(ind or s.get('ind') or '')} · 보유 {_krw(val)}"
            f" · 뉴스 {len(items) - n_tg}건{f' · 텔레그램 {n_tg}건' if n_tg else ''}"
            f"{_added_html(s)}</span></div>"
            f"{pc}<details class='hn-src'><summary>근거 {len(items)}건{offnote}</summary>"
            f"<ol>{lis}</ol></details></div>")


def _day_meta(rec: dict) -> str:
    w = rec.get("window") or {}
    a, b = _parse(w.get("since")), _parse(w.get("until"))
    parts = []
    if a and b:
        parts.append(f"뉴스 창 {a.strftime('%m-%d %H:%M')} ~ {b.strftime('%m-%d %H:%M')}")
    for t in rec.get("topups") or []:
        if isinstance(t, dict):
            parts.append(f"{_hm(t.get('at'))} 저녁 보강 새 항목 {t.get('items', 0)}건/{t.get('stocks', 0)}종목"
                         + (f"(원천 실패 {t['fail']}종목)" if t.get("fail") else ""))
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
        ck = int(llm.get("checks") or 0)
        parts.append(f"AI {llm['calls']}회" + (f"(그중 무관 소식 확인 {ck}회)" if ck else "")
                     + f" · {_cost_krw(llm.get('cost_usd', 0))}")
    return " · ".join(parts)


def _cost_krw(usd) -> str:
    """일자 칸의 AI 비용 — 원화(사용자 2026-10-14 "일별 금액도 달러말고 원화로"). 비용 카드와 같은 환율
    (`usage_tracker.KRW_PER_USD`, #38)·같은 꼴(₩ 정수)."""
    try:
        from bot.usage_tracker import KRW_PER_USD
        return f"₩{int(round(float(usd or 0) * KRW_PER_USD)):,}"
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: 비용 원화 환산 실패: %s", exc)
        return f"${float(usd or 0):.4f}"


def _day_summary(rec: dict) -> str:
    import html as _h
    d = datetime.fromisoformat(rec["date"])
    ss = rec.get("stocks") or []
    n_news = len([s for s in ss if s.get("items")])
    return (f"<summary>📅 {rec['date']} ({_WD[d.weekday()]}) · 뉴스 {n_news}/{len(ss)}종목"
            f"<span class='hn-meta'>{_h.escape(_day_meta(rec))}</span></summary>")


def _day_body(rec: dict, *, final: bool = False, now: datetime | None = None, latest: bool = False,
              values: dict | None = None, fx: dict | None = None, pnl: dict | None = None) -> str:
    """하루 칸의 본문(카드 · 새 뉴스 없음 · 뺀 상품) — 최신일은 페이지에, 지난 날은 조각 파일에 같은
    함수로 싣는다(#38). `final` → `_card`."""
    import html as _h
    ss = rec.get("stocks") or []
    with_news = [s for s in ss if s.get("items")]
    quiet = [s for s in ss if not s.get("items")]
    look = prev_changes(rec["date"], now)
    # 업종은 렌더 때 다시 읽는다 — 적어 두지 않은 옛 기록도 채우고(#270), 그날 라벨이 없어 영문으로 적힌 값도
    # 한국어 라벨이 생기면 따라온다(적어 둔 값은 하루치가 없을 때의 폴백, 리뷰 2026-10-14)
    indl = industry_lookup(rec["date"])
    vals = values or {}
    # 최신일 카드 = 요약과 같은 장종료가 누적 · 지금 자산 기록에 없는 종목(판 종목)은 그날 기준 전날 하루 등락
    cards = "".join(_card(s, final=final, prev=prev, latest=latest, value=vals.get(s.get("key")), fx=fx, ind=indl(s),
                          pnl=pnl, px=_card_px(prev, look, s))
                    for s in with_news
                    for prev in [(_cum_prev(pnl, s) if pnl and latest else None) or look(s)])
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
              latest: bool = False, values: dict | None = None, fx: dict | None = None,
              pnl: dict | None = None) -> str:
    return (f"<details class='hn-day' data-hnday='1' data-open='{1 if open_ else 0}'"
            f"{' open' if open_ else ''}>{_day_summary(rec)}"
            f"{_day_body(rec, final=final, now=now, latest=latest, values=values, fx=fx, pnl=pnl)}</details>")


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


def _prev_date_html(recs: list[dict]) -> str:
    """뉴스 기준일 옆에 **그 전에 기록된 날**(사용자 2026-10-14 · 칸 이름은 2026-10-15 '최신 기준일' → '뉴스 기준일') —
    날짜 내림차순 기록의 두 번째.
    기록이 하루뿐이면 '없음' 이라고 말한다(빈칸은 '못 셌다' 로 읽힌다, #43)."""
    ds = sorted({r["date"] for r in recs if r.get("date")}, reverse=True)
    return f"<span class='prev'>· 전 {ds[1] if len(ds) > 1 else '없음'}</span>"


def _asset_upload_html(ctx: dict, portfolio: dict | None, now: datetime) -> str:
    """맨 위 '자산 기록' 칸의 값 — 보유 종목과 평가금액을 가져온 **마지막 업로드**와 오늘 올렸는지(사용자 2026-10-15
    "오늘 난 자산을 업데이트 안 했지만 10-15 기준으로 주식 변동에 따라 변동되잖아"). 오늘 안 올렸으면 화면의 금액은 그
    업로드의 평가금액에 그 뒤 종가 등락을 곱한 추정치다. 나이는 **날 단위**만 — '몇 시간 전' 을 적으면 15분 재생성마다
    페이지가 바뀌어 '새 업데이트' 배너가 상시 켜진다(#442). 업로드 시각은 요약 재료(`pnl_context` cur_ts)에서, 요약이
    없으면(보유 0 · 실패) 자산 기록을 한 번 읽는다. 업로드 시각이 없는 기록은 자산 기록이 적은 기준(`as_of`)을 그대로."""
    import html as _h
    ts = (ctx or {}).get("cur_ts")
    model = None
    if ts is None:
        model = portfolio if portfolio is not None else _load_model_safe()
        sk = _saved_kst(model) if isinstance(model, dict) else None
        ts = sk[1] if sk else None
    if ts is None:
        raw = str((model if isinstance(model, dict) else {}).get("as_of") or "").strip()
        return (f"<div class='v'>{_h.escape(raw) if raw else '—'}<span class='prev'>· "
                f"{'업로드 시각 기록 없음' if raw else '자산 기록 없음'}</span></div>")
    try:
        at = datetime.fromtimestamp(float(ts), _KST)
    except (TypeError, ValueError, OverflowError, OSError):    # 깨진 값이 렌더를 멈추지 않게(#424)
        return "<div class='v'>—<span class='prev'>· 업로드 시각을 못 읽음</span></div>"
    days = (now.date() - at.date()).days
    tail = ("오늘 업로드" if days == 0 else
            f"{days}일 전 · 오늘 업로드 없음" if days > 0 else "업로드 시각이 지금보다 뒤")
    return f"<div class='v'>{at.strftime('%m-%d %H:%M')}<span class='prev'>· {tail}</span></div>"


def _lag_txt(m) -> str:
    """마감 뒤 몇 분에 그 세션 종가를 받나 — 시장마다 다르다(하루치 `READY_MIN` · Yahoo 만 받는 시장 `YCLOSE_MARGIN_MIN`).
    한 문구로 적으면 홍콩 줄이 '3시간 뒤' 라고 거짓말한다(독립 리뷰 2026-10-14). 모르면 하루치 시각."""
    m = int(m or 0) or _ws_ready_min()
    return f"{m // 60}시간" if m >= 60 and m % 60 == 0 else f"{m}분"


def _yahoo_reason(d: dict) -> str:
    """Yahoo 일봉 종가로 센 까닭 — 세 갈래의 처방이 다르다(#82): 하루치를 안 받는 시장 · 하루치를 놓친 세션 · 하루치에
    없는 종목(ETF·우선주 등)."""
    if d.get("nosnap"):
        return "TradingView 하루치를 받지 않는 시장이라"
    if d.get("broken"):
        b = d["broken"]
        return f"TradingView 하루치를 놓친 세션({', '.join(b[:2])}{f' 외 {len(b) - 2}' if len(b) > 2 else ''})이 있어"
    return "TradingView 하루치에 없는 종목이라"


def _ws_ready_min() -> int:
    """하루치를 받는 시각(장 마감 뒤 몇 분) — 가이드 문구가 리터럴로 적으면 `READY_MIN` 을 바꿀 때 갈린다(#38·#55)."""
    try:
        from bot.world_sectors import READY_MIN
        return int(READY_MIN)
    except Exception:                                          # noqa: BLE001
        return 180


def _ws_ready_h() -> int:
    return _ws_ready_min() // 60


def render_page(records: list[dict], state: dict | None = None,
                now: datetime | None = None, *, gen: str | None = None, total: int | None = None,
                portfolio: dict | None = None, fx: dict | None = None) -> str:
    """`records` = 화면에 실을 날들(최근 `RENDER_DAYS` 일이면 충분 — 그 앞은 안 그린다) · `total` = 보관 중인
    날 수(`count_records`, 없으면 `records` 수)."""
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
    # 전일대비 요약 — 실패는 이 칸만 비운다(곁들이가 본체를 지우지 않게, #315)
    pnl = ""
    pos_vals: dict = {}
    # 원화 환율은 서버가 굽지 않는다 — 환율 전일대비는 장 중 계속 바뀌어, 구우면 15분 재생성마다 페이지 내용이
    # 달라져 '내용이 같으면 안 쓴다'(#442)가 무너지고 '새 업데이트' 배너가 상시 켜진다(독립 리뷰 2026-10-14). 화면이
    # 열자마자 `api/live_pct?fx=1` 로 받는다(`_PNL_JS`). `fx` 인자는 테스트·진단용.
    pnl_ctx: dict = {}
    try:
        pos, skip = pnl_positions(portfolio)
        pos_vals = {p["key"]: p["value"] for p in pos}
        if pos:
            pnl_ctx = pnl_context(pos, now, portfolio)
            pnl = _pnl_html(pnl_ctx["pos"], skip, pnl_ctx["look"], fx=fx, ctx=pnl_ctx)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("holdings_news: 전일대비 요약 실패: %s", exc)
        pnl_ctx = {}
    # 맨 위 날짜 칸 셋 — 뉴스를 모은 날 · 보유와 평가금액을 가져온 자산 업로드 · 그 금액을 굴린 종가(사용자 2026-10-15
    # "최신 기준일은 기준을 나눠야 — 자산을 업데이트 안 했어도 주식 변동에 따라 변동"). 한 칸이 세 날짜를 대표하면 오늘
    # 업로드를 안 한 날에도 금액이 '오늘 자산' 으로 읽힌다(#34·#43).
    asof = pnl_ctx.get("asof") or {}
    stats = (
        "<div class='stat-grid'>"
        f"<div class='stat'><div class='k'>뉴스 기준일 · 전 기록일</div><div class='v'>"
        f"{latest['date'] if latest else '—'}{_prev_date_html(allrecs)}</div></div>"
        f"<div class='stat' id='hn-st-asset'><div class='k'>자산 기록(마지막 업로드)</div>"
        f"{_asset_upload_html(pnl_ctx, portfolio, now)}</div>"
        f"<div class='stat' id='hn-st-asof'><div class='k'>시세 반영(마지막 종가)</div><div class='v hn-v-sm'>"
        f"{_h.escape(asof_line(asof)) if asof else '—'}</div></div>"
        f"<div class='stat'><div class='k'>뉴스 있는 종목 / 보유 종목</div><div class='v'>"
        f"{n_news} / {n_all}</div></div>"
        f"<div class='stat'><div class='k'>텔레그램 언급(최신일)</div><div class='v'>"
        f"{tg.get('matched', 0)}건</div></div>"
        f"<div class='stat'><div class='k'>AI 비용 (오늘 / 이번 달 / 누적)</div><div class='v'>"
        f"{_h.escape(_cost_line(now))}</div></div>"
        "</div>")
    days = "".join(_day_html(r, open_=True, final=r["date"] not in live, now=now, latest=True, values=pos_vals, fx=fx, pnl=pnl_ctx)
                   if i == 0
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
                       ("con", "👎 Cons 있음"), ("tg", "📨 텔레그램 언급"))) + (
        "<span class='pill hn-reset' id='hn-reset' title='고른 칩과 검색어를 모두 지웁니다'>↺ 초기화</span>")
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>보유종목 News</title>{_BOARD_CSS}{_HN_CSS}
{_theme_head()}
</head><body><div class="wrap">
<div class="nav"><a href="market.html">🌍 홈</a> · <a href="portfolio.html">💼 자산</a> · <a href="index.html">🦉 종목분석</a> · <a href="dart_feed.html">📋 DART</a></div>
<h1>🗞️ <em>보유종목 News</em> — 보유 종목 뉴스 · Pros/Cons</h1>
<div class="sub">자산 대시보드 보유 종목 × 네이버 뉴스(국내) · Yahoo Finance(해외) · 텔레그램 'Noah의 RAG'
채널 · 매일 {RUN_HOUR:02d}:{RUN_MIN:02d} · {TOPUP_HOUR:02d}:{TOPUP_MIN:02d} KST 이후 직전 집계 이후 뉴스를 모아 AI(Gemini flash-lite)가 그
문장만 근거로 Pros/Cons 를 정리 · 내용이 바뀐 시각 {_h.escape(gen_s)}</div>
<div class="hn-status" data-kind="{kind}">{_h.escape(line)}</div>
{stats}
{pnl}
<details class="guide"><summary>ℹ️ 이 보드 읽는 법</summary>
<b>맨 위 날짜 칸 셋</b> — 날짜가 세 가지라 따로 적습니다. <b>뉴스 기준일</b>은 뉴스를 모은 날(옆은 그 전에 기록한
날)입니다. <b>자산 기록</b>은 보유 종목과 평가금액을 가져온 마지막 자산 업로드이고 오늘 올렸는지를 적습니다 — 오늘
올리지 않았으면 화면의 금액은 '오늘 자산' 이 아니라 그 업로드의 평가금액에 그 뒤 종가 등락을 곱한 <b>추정치</b>입니다.
<b>시세 반영</b>은 그 등락을 시장마다 어느 날 종가까지 반영했는지입니다(장이 닫히는 시각이 시장마다 달라 날짜가 갈립니다).<br>
<b>종목</b> — 💼 자산 대시보드와 같은 보유 기록입니다. 여러 증권사에 나눠 가진 종목은 하나로 합치고,
평가금액이 큰 종목부터 보여 줍니다. ETF(국내는 자산 기록이 ETF 로 알아본 상품, 미국은 거래소 목록의 ETF
표시)와 티커를 못 찾은 상품은 '회사' 뉴스로 가를 수 없어 빼고, 그 이름을 날짜 칸 맨 아래에 적습니다(일본·홍콩·
대만 ETF 는 가를 목록이 없어 회사로 셉니다).<br>
<b>카드 머리</b> — 종목명을 누르면 종목분석 화면으로 갑니다. 티커 옆은 그 종목의 <b>업종</b>입니다(「국가별
섹터혼합」과 같은 TradingView 분류 · 그 시장 하루치가 없으면 비어 있습니다). AI 는 이 업종을 보고 이름만 같은
다른 사업 기사(예: 반도체 장비 회사 '제우스' 에 섞인 같은 이름의 게임 기사)를 근거에서 뺍니다.<br>
<b>전일대비 — 기준은 KST 날짜 · 값은 장종료가</b> — 자산 기록은 하루에도 여러 번 올라오므로 기준은 '직전 업로드' 가
아니라 <b>오늘보다 앞선 가장 최근 날짜의 마지막 업로드</b>입니다(오늘이 10/9 이고 그 앞 기록이 10/5 뿐이면 10/5 의 마지막
업로드 · 오늘 몇 번을 올려도 기준은 그대로). 카드 괄호는 <b>(종가 · 등락률 · 내 보유의 금액 변동)</b>입니다 — 종가는
그 등락이 끝나는 세션의 실제 종가(현지통화 · 마우스를 올리면 어느 날 종가인지)이고, 등락을 못 셌으면 종가도 싣지 않고
'(—)' 입니다. 맨 위(최신) 날짜 카드의 괄호와 맨 위 요약 칸은 둘 다 그 기준 대비
<b>장종료가 누적 등락</b>입니다 — 기준 업로드 뒤에 마감한 세션마다의 종가 등락(「국가별 심리지도」가 받아 둔 TradingView
하루치)을 곱해 '기준 무렵 마지막 종가 → 받아 둔 마지막 종가' 를 셉니다. 하루치는 장 마감 {_ws_ready_h()}시간 뒤에 받으므로,
<b>장 중인 세션</b>과 <b>마감했지만 종가를 아직 안 받은 세션</b>은 화면이 {LIVE_POLL_SEC // 60}분마다 그 종목의 <b>실시간 등락</b>(정규장 ·
직전 종가 대비 — 국내·미·일·홍·중은 네이버, 대만은 TWSE)을 받아 종가 누적에 곱해 이어 붙입니다. 그 세션만 남았을 때만
붙이고(안 센 세션이 둘이면 하나를 빼먹은 값이 되어 종가 누적 그대로), 원천이 준 세션 날짜가 그 세션이 아니면 붙이지
않습니다. 이때 카드 괄호의 가격은 그 현재가이고(마우스를 올리면 받은 시각), 요약 칸 아래에 '실시간 n종목' 이 붙습니다. 어느
세션을 이을지는 페이지가 {TICK_MIN}분마다 다시 만들어질 때 정해지므로, 장이 열리기 전에 연 화면은 개장 뒤 첫 갱신이 실리면
아래 '새 업데이트' 알림으로 다시 읽어야 실시간이 붙고, 열어 둔 화면은 그 세션 종가를 받을 무렵 묻기를 멈춥니다. 서버는 한
종목을 {LIVE_POLL_SEC // 60}분에 한 번만 받으므로 화면을 여러 개 열어도 요청이 늘지 않고, 아무도 안 열면 묻지 않습니다. 맨 위
<b>시세 반영</b> 칸이 시장마다 <b>어느 날 종가까지</b> 반영했는지(실시간을 붙인 시장은 그 날짜와 '실시간' · 받은 시각) 적고(시장마다
장이 닫히는 시각이 달라 한 칸에 서로 다른 날의 종가가 섞입니다), 마감했지만 아직 안 받은 종가는 요약 아래 줄이 적습니다. 금액은
<b>기준 날 평가금액 × 누적 등락</b>으로 추정합니다(자산 기록에 수량이 없어 평가금액을 씁니다 · 기준 업로드가 장중이었으면
그 세션 종가까지를 통째로 세므로 어긋날 수 있습니다). 기준 날 이후 새로 생긴 종목은 지금 기록 금액을 쓰고 그 기록의
업로드 시각부터 센 등락을 곱합니다(기준 날부터 세면 들고 있지 않던 날의 손익이 됩니다 · 요약 아래 줄이 그 수를
적습니다). 원천이 그대로였던 세션(휴장 추정)은 등락 0 으로 셉니다. TradingView 하루치는 보통주·DR 만 실어, ETF·
우선주처럼 거기에 없는 종목과 하루치를 받지 않는 시장(홍콩)은 <b>Yahoo 일봉 종가</b>로 같은 날 종가까지를 셉니다(봇이
{TICK_MIN}분 주기에 받아 둡니다 — 화면은 묻지 않습니다). 그 사이 세션의 하루치를 놓친 경우나 종가를 아직 못 받은 경우는
지어내지 않고 '(—)' 이며 마우스를 올리면 사유를 적습니다(요약 칸에선 '종가 기준 n/N종목' 의 나머지). 해외 종목 금액은 <b>원화 기준</b>입니다 — 주가 등락에 그 통화의 원화 환율 변동(네이버
원화 환율 · 해외 보유가 있으면 화면이 1시간마다 환율 하나만 받습니다)을 곱해 (1+주가 등락)×(1+환율 변동)−1 로 셉니다 —
금액 앞에 '원화' 가 붙습니다. 환율 변동은 기준 업로드 무렵의 환율 수준이 남아 있으면 '지금 ÷ 기준' 이고, 없으면(그
업로드를 한 시간 안에 못 봤을 때) 오늘 하루 변동입니다. 괄호 안 %는 주가 등락 그대로라 금액 ÷ 평가금액과 다를 수
있습니다(마우스를 올리면 두 등락을 적습니다). 종가와 환율의 시각이 달라(예: 미국 종가는 간밤 · 환율은 서울 외환시장
지금) 추정치이고, 환율을 못 받은 통화는 '환율 미확인' 으로 세고 주가 등락만으로 셉니다. 환율은 화면을 연 뒤 받으므로
처음 잠깐은 '환율 대기' 입니다. 요약 칸 이름은 그 칸이 <b>실제로 담은 세션 수</b>를 말합니다 — 기준 업로드 뒤 반영한
세션이 하나면 '전일대비'(그 뒤 마감한 세션의 종가를 아직 받는 중이면 '(1세션)'), 둘 이상이면 'MM-DD 기록 이후(N세션)'(며칠 자산을 올리지 않았거나, 기준 업로드가 장 마감 전이라
그 세션까지 같이 셀 때), 아직 하나도 반영하지 못했으면 '(반영한 세션 없음)' 입니다(값은 같은 식이고 이름만 사실대로
바뀝니다 · 기준 날 뒤 새로 생긴 종목은 기준이 달라 세지 않습니다). 요약 <b>전체/국내/해외</b> 칸은 자산 기록의 값을 매길 수 있는 보유
전부(ETF 포함 · 뉴스가 없는 종목 포함)를 같은 식으로 더한 값이고, %는 그 합 ÷ 등락을 아는 종목의 기준 평가금액
합입니다(등락을 모르는 종목은 금액에도 분모에도 넣지 않습니다 — 칸 아래 'n/N종목' 이 그 수를 말합니다) · 티커를 못 찾아 뺀 상품은 요약 아래 줄이 이름과 금액을 적습니다). 지난 날 카드의 괄호와 지금 자산 기록에
없는 종목(판 종목)의 괄호는 그날 기준 전날 하루 등락 그대로이고 현지통화 등락만입니다(마우스를 올리면 무엇의 값인지와
기준 시각).<br>
<b>낮에 산 종목</b> — 종목 목록은 매일 아침 수집 때 정해지지만, 그 뒤 자산 기록이 바뀌어 새로 생긴 보유는
{TICK_MIN}분 주기마다 그 종목만 같은 창·같은 규칙으로 모아 오늘 칸에 붙입니다(카드에 '자산 기록 변경 뒤 HH:MM 추가'
· 한 주기 {MAX_ADD_PER_TICK}종목까지). 판 종목의 오늘 카드는 그대로 남습니다(그날 아침 들고 있던 종목의 소식이라).<br>
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
{MAX_PER_SIDE}줄씩 씁니다. 줄 끝 [번호]는 근거 항목이고, 근거 번호가 없거나 틀린 줄은 버립니다.
{INVEST_TOPICS}처럼 투자 판단에 닿는 내용만 씁니다 — 이름이 같은 다른 회사 얘기나, 회사 이름은 나오지만
투자와 무관한 소식(그 이름을 단 스포츠 구단의 경기 결과·선수 기록, 연예·문화 행사 등)은 근거로 쓰지 않고 근거
목록에서 흐리게 보입니다. 스포츠·연예·콘텐츠·게임이 그 회사의 본업이거나 회사의 수입·지출에 닿는 일(후원·마케팅
계약, 흥행 수입, 구단 매각·운영비)은 씁니다. 정리가 끝난 뒤 스포츠·연예 문맥으로 보이는 줄은 AI 에 <b>한 번 더</b>
물어 투자와 무관하다고 답하면 빼고 카드에 뺀 줄 수를 적습니다(AI 일시 오류면 그 종목을 '대기' 로 되돌려 다음 주기에
다시 정리하고 — 같은 종목 {MAX_CHECK_REVERTS}번째면 그만두고 — 응답을 못 읽었으면 줄은 남기고 그렇게 적습니다).
AI 판단이라 놓치거나 지나치게 빼는 항목이 있을 수 있습니다. 이
기준이 바뀌면 가장 최근 기록은 새 기준으로 다시 정리합니다(AI 키가 있을 때 · 그동안 그 종목은 '대기' · 그 앞의
기록은 옛 정리 그대로). 이것은 뉴스 요약이지 투자 판단이 아닙니다.<br>
<b>언제 갱신되나</b> — 매일 {RUN_HOUR:02d}:{RUN_MIN:02d} KST 이후 하루치를 모읍니다(장 시작 전에 읽도록 · 봇이 {TICK_MIN}분마다
확인하므로 보통 {_hm_after(TICK_MIN)} 사이에 시작합니다 — 앞선 정리 작업이 길면 그보다 늦을 수 있습니다).
창은 직전 집계 {WINDOW_OVERLAP_H}시간 전부터 지금까지입니다(직전 집계 뒤에 늦게 들어온 글을 놓치지 않으려고
겹치며, 이미 실린 항목은 빼고 싣습니다). <b>저녁 보강</b> — 같은 날 {TOPUP_HOUR:02d}:{TOPUP_MIN:02d} KST 이후 한 번 더 모아
아침 창 이후의 <b>새 항목만</b> 그날 칸에 붙입니다(근거 목록에 '저녁 보강(HH:MM)' · 아침 항목 번호는 그대로). 새 항목이 붙은
종목만 아침 뉴스와 함께 다시 정리하고(그동안 '대기'), 다시 정리를 마치지 못하면 아침 정리를 '아침 정리' 라벨로 보여
줍니다(그 카드의 저녁 항목은 '아직 정리 안 됨'). 다음 날 아침 창은 저녁 보강 시각에서 이어집니다. 원천 장애로 저녁 보강을
못 받으면 창을 옮기지 않고 {TOPUP_RETRY_MIN}분 뒤 다시 하며(그날 {TOPUP_MAX_TRIES}번까지 — 그래도 안 되면 다음 날 아침
수집이 그 시간대를 덮습니다), 몇 종목만 못 받았으면 그 종목은 다음 수집이 놓친 구간부터 다시 엽니다(다만 종목당
{MAX_NEWS_PER_STOCK}건 상한은 같아서 그 사이 기사가 많으면 최근 것만 담깁니다). 봇이 며칠 멈췄어도 {MAX_WINDOW_H}시간보다 오래된 뉴스는 싣지 않습니다.
AI 정리는 한 주기에 {MAX_LLM_CALLS}번까지 부르고 남으면 15분 뒤 이어서 합니다('대기' — 바로 전 날의 대기분도
이어서 정리하고, 그보다 앞선 날은 '마치지 못함' 으로 적습니다). AI 의 일시 오류(요청 한도·서버 장애·시간 초과)는
실패로 세지 않고 다음 주기에 다시 합니다. AI 가 답했는데 쓸 수 없는 경우가 같은 종목에서
{MAX_ANALYZE_TRIES}번이면 그날은 뉴스만 보여 줍니다.<br>
<b>표시 범위</b> — 최근 {RENDER_DAYS}일만 보여 주고 그 앞은 보관만 합니다. 가장 최근 날만 바로 보이고
지난 날은 펼칠 때 불러옵니다(보유 종목이 많으면 하루 칸이 무거워서). 검색·필터는 불러온 날에 걸리고, 아직
안 연 날은 펼치면 같이 걸러집니다(상태 줄이 몇 날이 남았는지 적습니다).<br>
<b>필터 칩</b> — 여러 개를 함께 고를 수 있습니다(다시 누르면 풀립니다). 국내·해외는 '또는'(둘 다 고르면 전부),
Pros·Cons·텔레그램은 '그리고'(고른 표식을 모두 가진 카드만)로 걸립니다. 상태 줄 앞 대괄호가 지금 걸린 칩입니다.
<b>전체</b>는 칩만 풀고, <b>↺ 초기화</b>는 칩과 검색어를 함께 지웁니다.
</details>
<div class="hn-tools"><input id="hn-q" type="text" placeholder="종목명·티커 검색" autocomplete="off" spellcheck="false"></div>
<div class="pills">{pills}</div>
<div id="hn-st" class="sub">{shown}</div>
{days}
<div class="footer">보유종목 News — 뉴스 정리(교육·정보), 투자 권유 아님 · NOAH</div>
</div>
<script>{_HN_JS}</script><script>{_PNL_JS}</script>
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
        ck = int(llm.get("checks") or 0)
        out(f"   AI {llm.get('calls', 0)}회" + (f"(그중 무관 소식 확인 {ck}회)" if ck else "")
            + f" · 입력 {llm.get('pt', 0):,} · 출력 {llm.get('ot', 0):,} 토큰 · ${llm.get('cost_usd', 0):.4f}")
        out("   " + requeue_note(r, now))
        try:                             # 곁들이 줄 — 실패해도 판정 줄은 남긴다(#315)
            out("   " + holdings_diff_line(r, stocks, now, st))
        except Exception as exc:                                # noqa: BLE001
            out(f"   보유 대조: ❓ 계산 실패({type(exc).__name__}: {_exc_msg(exc, 80)})")
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
        print("⚠️ AI(Gemini) 호출 비용이 듭니다 — 오늘 기록이 이미 있으면 대기 종목만 정리합니다(정리 기준이 "
              "바뀌었고 AI 키가 있으면 가장 최근 기록을 새 기준으로 다시 정리 — 그 종목의 본문도 다시 읽습니다).")
        print(json.dumps(tick_and_render(), ensure_ascii=False))
        return 0
    return why()


if __name__ == "__main__":
    sys.exit(main())
