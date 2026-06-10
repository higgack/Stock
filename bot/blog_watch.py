"""블로그 Watcher — 네이버 블로그 '변화하는 기업을 찾아서' (beatthemkt)
새 글 자동 감지 → NOAH 채널 포워드 + 아카이브(ingest).

흐름: RSS poll (rss.blog.naver.com/<id>.xml, 브라우저 UA+Referer) → 새 GUID
감지 → Gemini Flash 3줄 요약(저렴, grounding off) → 텔레그램 채널 push +
JSON 아카이브(대시보드 blog.html 누적·검색·🗑️). state 파일로 중복 차단.

원칙: 발췌(description)에 있는 내용만 요약(환각 0), 투자 권유 표현 금지.
GOOGLE_API_KEY 없거나 RSS 실패 시 graceful skip. 첫 run 은 폭주 방지 위해
기존 글을 seen 처리만 하고 push 안 함(initialized 플래그).

systemd: blog-watch.timer (30분) → oneshot.
수동: cd ~/stock && .venv/bin/python -m bot.blog_watch
"""

from __future__ import annotations

import html as _html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bot.blog_watch")

_KST = timezone(timedelta(hours=9))
_BLOG_ID = "beatthemkt"
_BLOG_TITLE = "변화하는 기업을 찾아서"
_RSS_URLS = (
    f"https://rss.blog.naver.com/{_BLOG_ID}.xml",
    f"https://rss.blog.naver.com/{_BLOG_ID}",
)
_HOME = os.path.expanduser("~")
_STATE = os.path.join(_HOME, ".tradingagents", "blog_watch_state.json")
_ARCHIVE_DIR = os.path.join(_HOME, ".tradingagents", "blog_archive")
_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "blog_usage.jsonl")
_NOAH_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "usage.jsonl")
_USD_TO_KRW = 1330.0
_FLASH_IN, _FLASH_OUT = 0.30, 2.50   # gemini-2.5-flash $/M
_MAX_SEEN = 300
_MAX_NEW_PER_RUN = 5


def _now_kst() -> datetime:
    return datetime.now(_KST)


# ── state (본 GUID) ───────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        with open(_STATE, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("seen", [])
        d.setdefault("initialized", False)
        return d
    except Exception:
        return {"seen": [], "initialized": False}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_STATE), exist_ok=True)
        state["seen"] = list(state.get("seen", []))[-_MAX_SEEN:]
        with open(_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as exc:
        log.warning("blog_watch: state save failed: %s", exc)


# ── RSS fetch + parse ─────────────────────────────────────────────────────
def _fetch_rss() -> str | None:
    import httpx
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Referer": f"https://m.blog.naver.com/{_BLOG_ID}",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    for url in _RSS_URLS:
        try:
            r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
            if r.status_code == 200 and "<item" in r.text:
                return r.text
            log.warning("blog_watch: RSS %s → %d", url, r.status_code)
        except Exception as exc:
            log.warning("blog_watch: RSS fetch %s failed: %s", url, exc)
    return None


def _parse_items(xml: str) -> list[dict]:
    out: list[dict] = []

    def _tag(block: str, t: str) -> str:
        m = re.search(rf"<{t}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>",
                      block, re.DOTALL)
        return m.group(1).strip() if m else ""

    for block in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        title = _html.unescape(_tag(block, "title"))
        link = _tag(block, "link")
        guid = _tag(block, "guid") or link
        pub = _tag(block, "pubDate")
        desc = _html.unescape(re.sub(r"<[^>]+>", " ", _tag(block, "description")))
        desc = re.sub(r"\s+", " ", desc).strip()[:4000]
        if title and link:
            out.append({"title": title, "link": link, "guid": guid,
                        "pubDate": pub, "desc": desc})
    return out


# ── Gemini Flash 요약 + 비용 로그 ─────────────────────────────────────────
def _log_blog_usage(pt: int, ot: int, cost_krw: float) -> None:
    import time as _time
    for path, rec in (
        (_USAGE_LOG, {"ts": _now_kst().isoformat(timespec="seconds"),
                      "date": _now_kst().date().isoformat(),
                      "month": _now_kst().date().isoformat()[:7],
                      "prompt_tok": pt, "output_tok": ot,
                      "cost_krw": round(cost_krw, 4)}),
        (_NOAH_USAGE_LOG, {"ts": _time.time(), "type": "llm_call",
                           "model": "gemini-2.5-flash",
                           "prompt_tokens": pt, "completion_tokens": ot,
                           "cost_usd": round(cost_krw / _USD_TO_KRW, 6),
                           "subsystem": "blog"}),
    ):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warning("blog_watch: usage log failed (%s): %s", path, exc)


def _fetch_post_text(link: str) -> str | None:
    """원문 페이지(m.blog.naver.com)에서 본문 전문 추출 (₩0, 글당 1 fetch).

    RSS description 은 네이버가 자른 발췌(끝 '...')라 글이 중간에 끊김
    (사용자 2026-06-11). 모바일 페이지의 se-main-container(스마트에디터 ONE,
    폴백 postViewArea=구에디터)를 div depth 카운터로 정확히 잘라 태그 strip.
    실패 시 None → RSS 발췌 유지 (graceful)."""
    try:
        import httpx
        m = re.search(r"blog\.naver\.com/([^/?#]+)/(\d+)", link or "")
        if not m:
            return None
        url = f"https://m.blog.naver.com/{m.group(1)}/{m.group(2)}"
        headers = {
            "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
            "Referer": f"https://m.blog.naver.com/{m.group(1)}",
        }
        r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        if r.status_code != 200 or not r.text:
            return None
        html = r.text
        start = None
        for marker in ('class="se-main-container"', 'id="postViewArea"',
                       'class="post_ct"'):
            i = html.find(marker)
            if i >= 0:
                start = html.rfind("<div", 0, i)
                break
        if start is None or start < 0:
            return None
        # div depth 카운터 — 시작 div 의 짝이 닫힐 때까지 (regex 중첩 한계 회피)
        depth = 0
        pos = start
        end = None
        for tag in re.finditer(r"<div\b|</div>", html[start:start + 400_000]):
            if tag.group(0) == "<div":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    end = start + tag.end()
                    break
        if end is None:
            return None
        body = html[start:end]
        body = re.sub(r"<(script|style)\b.*?</\1>", " ", body,
                      flags=re.DOTALL | re.IGNORECASE)
        text = _html.unescape(re.sub(r"<[^>]+>", " ", body))
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\s*\n\s*", "\n", text).strip()
        text = re.sub(r" {2,}", " ", text)
        return text[:15000] if len(text) >= 200 else None
    except Exception as exc:
        log.warning("blog_watch: full text fetch failed (%s): %s", link, exc)
        return None


def _backfill_full(max_n: int = 3) -> int:
    """기존 아카이브의 잘린 발췌(desc<1500자, 전문 미시도)를 원문 전문으로
    교체 — 사이클(30분)당 max_n 건 점진 백필. 실패 3회 캡."""
    done = 0
    try:
        if not os.path.isdir(_ARCHIVE_DIR):
            return 0
        for day in sorted(os.listdir(_ARCHIVE_DIR), reverse=True):
            day_dir = os.path.join(_ARCHIVE_DIR, day)
            if not os.path.isdir(day_dir):
                continue
            for fn in sorted(os.listdir(day_dir), reverse=True):
                if done >= max_n:
                    return done
                if not fn.endswith(".json"):
                    continue
                path = os.path.join(day_dir, fn)
                try:
                    with open(path, encoding="utf-8") as f:
                        rec = json.load(f)
                    if rec.get("full") or int(rec.get("full_try", 0)) >= 3:
                        continue
                    if len(rec.get("desc") or "") >= 1500:
                        continue
                    text = _fetch_post_text(rec.get("link") or "")
                    if text:
                        rec["desc"] = text
                        rec["full"] = True
                    else:
                        rec["full_try"] = int(rec.get("full_try", 0)) + 1
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(rec, f, ensure_ascii=False)
                    done += 1
                except Exception as exc:
                    log.warning("blog_watch: backfill %s failed: %s", fn, exc)
    except Exception as exc:
        log.warning("blog_watch: backfill scan failed: %s", exc)
    return done


def _summarize(item: dict) -> str:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not item.get("desc"):
        return ""
    prompt = (
        "다음은 네이버 블로그 '변화하는 기업을 찾아서'의 새 글이다.\n"
        f"제목: {item['title']}\n본문 발췌: {item['desc'][:2500]}\n\n"
        "이 글의 핵심을 한국어 3줄 이내로 요약하라. 규칙:\n"
        "1. **발췌에 있는 내용만** — 발췌에 없는 수치/사건 창작 절대 금지.\n"
        "2. 종목/기업명이 언급되면 명시.\n"
        "3. 투자 권유·목표가 표현 금지 (정보 요약만).\n"
        "4. 발췌가 너무 짧아 요약 불가하면 '본문 확인 권장' 한 줄."
    )
    try:
        from bot.screener import _call_pro
        text, pt, ot = _call_pro(api_key, prompt, model="gemini-2.5-flash",
                                 enable_grounding=False)
        cost_krw = (pt * _FLASH_IN + ot * _FLASH_OUT) / 1e6 * _USD_TO_KRW
        _log_blog_usage(pt, ot, cost_krw)
        return (text or "").strip()
    except Exception as exc:
        log.warning("blog_watch: summary failed: %s", exc)
        return ""


# ── 아카이브 + 대시보드 ───────────────────────────────────────────────────
def _save_archive(item: dict, summary: str) -> None:
    """Ingest 저장 — 봇이 나중에 참조 가능하도록 새 글 raw 를 JSON 보관.
    대시보드 surface 는 사용자 정책(2026-05-31)으로 없음 (채널 자동 포워드
    처럼만 다룸). 검색/분석 재료용 적층."""
    try:
        now = _now_kst()
        date_iso = now.date().isoformat()
        day_dir = os.path.join(_ARCHIVE_DIR, date_iso)
        os.makedirs(day_dir, exist_ok=True)
        import hashlib
        h = hashlib.sha256((item.get("guid") or "").encode()).hexdigest()[:10]
        path = os.path.join(day_dir, f"{now:%H%M%S}_{h}.json")
        rec = {"ts": now.isoformat(timespec="seconds"), "date": date_iso,
               "title": item["title"], "link": item["link"],
               "guid": item["guid"], "pubDate": item.get("pubDate", ""),
               "summary": summary, "desc": item.get("desc", "")[:15000],
               "full": bool(item.get("full"))}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
    except Exception as exc:
        log.warning("blog_watch: archive(ingest) write failed: %s", exc)


# ── Telegram push ─────────────────────────────────────────────────────────
def _push(item: dict, summary: str) -> bool:
    title = _html.escape(item["title"])
    link = item["link"]
    body = (
        f"📝 <b>변화하는 기업을 찾아서 — 새 글</b>\n"
        f"<b>{title}</b>\n"
    )
    if summary:
        body += f"\n{_html.escape(summary)}\n"
    body += f"\n🔗 {link}"
    try:
        from bot.daily_kr_flow import push_telegram
        return push_telegram(body)
    except Exception as exc:
        log.warning("blog_watch: push failed: %s", exc)
        return False


def run() -> int:
    xml = _fetch_rss()
    if not xml:
        log.warning("blog_watch: RSS 미수신 — skip (VM 네이버 접근 확인)")
        return 1
    items = _parse_items(xml)
    if not items:
        log.warning("blog_watch: RSS 파싱 0건 — skip")
        return 1

    state = _load_state()
    seen = set(state.get("seen", []))

    # 첫 run: 기존 글을 seen 처리만 (폭주 방지), push 안 함.
    if not state.get("initialized"):
        state["seen"] = [it["guid"] for it in items][:_MAX_SEEN]
        state["initialized"] = True
        _save_state(state)
        log.info("blog_watch: 초기화 — 기존 %d개 글 seen 처리 (push 생략)", len(items))
        return 0

    # 새 글 = seen 에 없는 것. RSS 는 최신순이라 뒤집어 오래된 것부터 push.
    new_items = [it for it in items if it["guid"] not in seen]
    new_items = list(reversed(new_items))[:_MAX_NEW_PER_RUN]
    if not new_items:
        log.info("blog_watch: 새 글 없음")
        return 0

    pushed = 0
    for it in new_items:
        # 원문 전문 fetch — RSS 발췌는 잘려 옴(사용자 2026-06-11). 실패 시
        # RSS 발췌 유지.
        full_text = _fetch_post_text(it.get("link") or "")
        if full_text:
            it["desc"] = full_text
            it["full"] = True
        summary = _summarize(it)
        _save_archive(it, summary)
        if _push(it, summary):
            pushed += 1
        seen.add(it["guid"])
        state["seen"].append(it["guid"])
    _save_state(state)
    log.info("blog_watch: 새 글 %d개 처리, %d개 push", len(new_items), pushed)
    # 기존 잘린 글 점진 백필(전문 교체, 사이클당 3건) — 새 글 없어도 진행.
    try:
        bf = _backfill_full(max_n=3)
        if bf:
            log.info("blog_watch: 전문 백필 %d건", bf)
    except Exception as exc:
        log.warning("blog_watch: backfill failed: %s", exc)
    # 대시보드 갱신 — blog.html (사용자 2026-06-11, 레딧 워처 패턴 mirror)
    try:
        from bot.dashboard import regenerate_blog_index
        regenerate_blog_index()
    except Exception as exc:
        log.warning("blog_watch: blog.html regen failed: %s", exc)
    return 0


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
    return run()


if __name__ == "__main__":
    import sys
    sys.exit(main())
