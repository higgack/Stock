"""블로그 Watcher — 네이버 블로그(_BLOGS 목록) 새 글 자동 감지 →
NOAH 채널 포워드 + 아카이브(ingest).

흐름: RSS poll → 새 GUID(블로그별 categories 설정이 있으면 그 prefix 만,
None 이면 전체 글) → 원문 전문 fetch(m.blog.naver.com) → 텔레그램 push
(제목 한 줄, 링크 임베드) + JSON 아카이브(대시보드 blog.html — 전문 +
맨 밑 원문 링크 + 🗑️).

**LLM 0 → 비용 ₩0** (요약 제거, 사용자 2026-06-11). RSS 실패 시 graceful
skip.

⛔ **새로 등록되는 블로그는 예외 없이 '새 글부터'가 디폴트다**(사용자
2026-08-24·08-26 확정: "새로운 글만 올라오면 되는거야. 새로 등록되는 것들은
모두. 이건 디폴트야"). 첫 run 은 기존 글을 **seen 처리만** 하고 push 하지
않는다(`state["init"][blog_id]`) — 백필 옵션은 **두지 않는다**. 레지스트리
항목은 `{id, title, categories}` 세 키뿐이라 opt-in 할 자리 자체가 없고,
회귀가 `_BLOGS` **전 항목**에 대해 이 계약을 검사한다(#24 이름 열거는 새
항목을 못 잡는다). 2차 방어로 날짜가드(`_MAX_AGE_DAYS`)가 있어, 초기화를
건너뛰어도 오래된 글은 push 되지 않는다.

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
# 감시 블로그 목록 (멀티 블로그 — 사용자 2026-06-15 teasky0221 추가). 각 블로그:
# id(네이버 blogId) + title(표시명, 비면 런타임 RSS 채널 title 로 보강) +
# categories(수집 카테고리 prefix; None = 전체 글). 새 블로그는 첫 run 에
# **per-blog 초기화**로 기존 글 seen 처리(폭주 방지) → 이후 새 글만 push.
_BLOGS = (
    {"id": "teasky0221", "title": "필승", "categories": None},   # 전체 글 (사용자 2026-06-15)
    {"id": "doctordk", "title": "의교창", "categories": None},   # 전체 글 (사용자 2026-06-15)
    {"id": "jkhan012", "title": "천상천하", "categories": None},  # 전체 글 (사용자 2026-06-18)
    {"id": "ranto28", "title": "메르", "categories": None},      # 전체 글 (사용자 2026-06-18)
    {"id": "richyun0108", "title": "작은 투자자", "categories": None},  # 전체 글 (사용자 2026-06-22)
    {"id": "bboyanaga", "title": "애널리스트 김경민", "categories": None},  # 전체 글 (사용자 2026-06-23)
    {"id": "usforall", "title": "모두의 미국주식", "categories": None},  # 전체 글 (사용자 2026-07-05)
    # 사용자 2026-08-20 (m.blog.naver.com/roe_20, 표시명 사용자 확정).
    # "앞으로 포스팅하는것부터" = per-blog init 기본동작(첫 run 은 기존 글
    # seen 처리만·push 생략) 그대로라 별도 설정 불요 — 백필 없음.
    {"id": "roe_20", "title": "게으른 투자자", "categories": None},  # 전체 글
    # 사용자 2026-08-24 (blog.naver.com/arirangya, 표시명 사용자 확정).
    # 첫 run 은 per-blog init 으로 기존 글 seen 처리만 — 백필 없이 새 글부터.
    {"id": "arirangya", "title": "사색하는 투자자", "categories": None},  # 전체 글
    # 사용자 2026-08-26 (blog.naver.com/intelligent_tiger, 표시명 사용자 확정).
    {"id": "intelligent_tiger", "title": "영리한 타이거", "categories": None},  # 전체 글
)
# 제거: pillion21("알바트로스의 파생 이야기") — 이웃공개 블로그라 RSS 미노출 +
# 본문 자동추출 불가(로그인 벽). 자동수집 효과 없어 제외(사용자 2026-06-21).
# 제거: beatthemkt("변화하는 기업을 찾아서") — 사용자 2026-08-19 요청으로
# 자동수집 중단(앞으로 새 글 push/아카이브 안 함). 기존 아카이브 글은 그대로
# 유지(blog.html 에서 개별 🗑️ 로 삭제 가능) — 수집만 멈추고 과거 기록은
# 보존. 재개하려면 이 항목을 _BLOGS 로 되돌리면 됨(state 의 init 플래그가
# 남아 있어 재개 시 옛 글 폭주 없이 새 글부터 수집).
_HOME = os.path.expanduser("~")
_STATE = os.path.join(_HOME, ".tradingagents", "blog_watch_state.json")
_ARCHIVE_DIR = os.path.join(_HOME, ".tradingagents", "blog_archive")
# ⚠️ seen 은 전 블로그 공용 리스트 — 캡이 작으면 새 블로그 초기화(수십 GUID
# 일괄 유입)가 옛 GUID 를 밀어내 뜸한 블로그의 옛 글이 '신규'로 재푸시됨
# (2026-07-08 의교창 6/18 글 재푸시 — usforall 초기화가 트리거). 600→5000.
_MAX_SEEN = 5000
# 날짜가드(실수 9): pubDate 가 이 일수보다 오래된 글은 push 없이 seen 만
# 처리 — 캡 축출/RSS 재등장에도 옛 글 재푸시 원천 차단.
_MAX_AGE_DAYS = 14
_MAX_NEW_PER_RUN = 5     # 블로그당


def _now_kst() -> datetime:
    return datetime.now(_KST)


# ── state (본 GUID) ───────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        with open(_STATE, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("seen", [])
        d.setdefault("init", {})          # per-blog 초기화 {blog_id: True}
        # 옛 단일 블로그 bool 'initialized' → beatthemkt per-blog init 으로 승계
        # (마이그레이션: 기존 VM state 는 초기화 끝난 beatthemkt 만 가짐).
        if d.get("initialized") and not d["init"].get("beatthemkt"):
            d["init"]["beatthemkt"] = True
        return d
    except Exception:
        return {"seen": [], "init": {}}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_STATE), exist_ok=True)
        state["seen"] = list(state.get("seen", []))[-_MAX_SEEN:]
        with open(_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as exc:
        log.warning("blog_watch: state save failed: %s", exc)


# ── RSS fetch + parse ─────────────────────────────────────────────────────
def _fetch_rss(blog_id: str) -> str | None:
    import httpx
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Referer": f"https://m.blog.naver.com/{blog_id}",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    for url in (f"https://rss.blog.naver.com/{blog_id}.xml",
                f"https://rss.blog.naver.com/{blog_id}"):
        try:
            r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
            if r.status_code == 200 and "<item" in r.text:
                return r.text
            log.warning("blog_watch[%s]: RSS %s → %d", blog_id, url, r.status_code)
        except Exception as exc:
            log.warning("blog_watch[%s]: RSS fetch %s failed: %s", blog_id, url, exc)
    return None


def _parse_channel_title(xml: str) -> str:
    """RSS 채널 수준 <title>(첫 <item> 이전) = 블로그 표시명. 실패 시 ''."""
    head = xml.split("<item", 1)[0]
    m = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                  head, re.DOTALL)
    return _html.unescape(m.group(1).strip()) if m else ""


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
        category = _html.unescape(_tag(block, "category"))
        desc = _html.unescape(re.sub(r"<[^>]+>", " ", _tag(block, "description")))
        desc = re.sub(r"\s+", " ", desc).strip()[:4000]
        if title and link:
            out.append({"title": title, "link": link, "guid": guid,
                        "pubDate": pub, "desc": desc, "category": category})
    return out


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
        # 블록 경계(문단 </p>·줄바꿈 <br>·헤딩·리스트·컴포넌트 </div>)를 줄바꿈으로
        # 보존한 뒤 잔여 태그만 공백 strip — 전부 공백으로 치환하면 문단이 한
        # 덩어리로 뭉쳐 가독성↓ (사용자 2026-06-16 '문맥단위로 띄어쓰기'). 스마트
        # 에디터 ONE 은 문단=<p class=se-text-paragraph>·컴포넌트=<div class=
        # se-component> 라, 그 닫힘을 줄바꿈으로 만들면 렌더가 문단 간격을 준다.
        body = re.sub(r"(?i)</p\s*>|<br\s*/?>|</h[1-6]\s*>|</li\s*>|</div\s*>",
                      "\n", body)
        text = _html.unescape(re.sub(r"<[^>]+>", " ", body))
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)            # 줄 양끝 공백 제거(개행 보존)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()  # 과도한 빈 줄만 정리
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



# ── 아카이브 + 대시보드 ───────────────────────────────────────────────────
def _save_archive(item: dict) -> None:
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
               "blog_id": item.get("blog_id", ""),
               "blog_title": item.get("blog_title", ""),
               "desc": item.get("desc", "")[:15000],
               "full": bool(item.get("full"))}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
    except Exception as exc:
        log.warning("blog_watch: archive(ingest) write failed: %s", exc)


# ── Telegram push ─────────────────────────────────────────────────────────
def _push(item: dict) -> bool:
    """텔레그램 = 글 제목 한 줄만(제목에 원문 링크 임베드) — 사용자 2026-06-11.
    요약 없음 → LLM 0, 비용 ₩0."""
    title = _html.escape(item["title"])
    link = _html.escape(item["link"])
    blog = _html.escape(item.get("blog_title") or item.get("blog_id") or "")
    body = f'📝 <a href="{link}">{title}</a>'
    if blog:
        body += f'\n<i>— {blog}</i>'   # 멀티 블로그 — 출처 표시
    try:
        from bot.daily_kr_flow import push_telegram
        return push_telegram(body)
    except Exception as exc:
        log.warning("blog_watch: push failed: %s", exc)
        return False


def _process_blog(blog: dict, state: dict, seen: set,
                  new_texts: list | None = None) -> int:
    """한 블로그 처리 → push 건수. RSS 미수신 시 -1(실패 신호).
    new_texts 주어지면 새 글 본문을 {text,source} 로 누적(관계후보 자동발굴용)."""
    bid = blog["id"]
    xml = _fetch_rss(bid)
    if not xml:
        log.warning("blog_watch[%s]: RSS 미수신 — skip (VM 네이버 접근 확인)", bid)
        return -1
    items = _parse_items(xml)
    if not items:
        log.warning("blog_watch[%s]: RSS 파싱 0건 — skip", bid)
        return 0
    # 표시명: 설정 title(사용자 지정) 우선 → RSS 채널 title → blog id
    ch_title = blog.get("title") or _parse_channel_title(xml) or bid
    for it in items:
        it["blog_id"] = bid
        it["blog_title"] = ch_title

    # 첫 run(이 블로그): 기존 글 seen 처리만(폭주 방지), push 안 함.
    if not state["init"].get(bid):
        for it in items:
            if it["guid"] not in seen:
                seen.add(it["guid"])
                state["seen"].append(it["guid"])
        state["init"][bid] = True
        log.info("blog_watch[%s/%s]: 초기화 — 기존 %d개 글 seen 처리 (push 생략)",
                 bid, ch_title, len(items))
        return 0

    # 새 글 = seen 에 없는 것. RSS 최신순이라 뒤집어 오래된 것부터 push.
    new_items = list(reversed([it for it in items if it["guid"] not in seen]))
    if not new_items:
        log.info("blog_watch[%s]: 새 글 없음", bid)
        return 0

    # 카테고리 필터(per-blog). categories=None → 전체 글(teasky0221). 비대상
    # 새 글도 seen 처리(재검사 방지). category 가 전혀 없으면 전부 허용(놓침 방지).
    cats = blog.get("categories")
    feed_has_cat = any(it.get("category") for it in items)
    allowed: list[dict] = []
    for it in new_items:
        cat = (it.get("category") or "").strip()
        ok = (cats is None) or (not feed_has_cat) or cat.startswith(cats)
        if ok:
            allowed.append(it)
        else:
            seen.add(it["guid"])
            state["seen"].append(it["guid"])
    if cats and not feed_has_cat:
        log.warning("blog_watch[%s]: RSS 에 category 없음 — 전체 허용(형식 확인)", bid)
    skipped_cat = len(new_items) - len(allowed)
    allowed = allowed[:_MAX_NEW_PER_RUN]

    # 날짜가드 — pubDate 파싱 가능하고 _MAX_AGE_DAYS 초과면 push 제외
    # (seen 처리만). 파싱 불가면 허용(놓침 방지).
    fresh: list[dict] = []
    for it in allowed:
        pub_dt = None
        try:
            from email.utils import parsedate_to_datetime
            pub_dt = parsedate_to_datetime(it.get("pubDate") or "")
        except Exception:
            pub_dt = None
        if pub_dt is not None and (
                _now_kst() - pub_dt.astimezone(_KST)
                > timedelta(days=_MAX_AGE_DAYS)):
            seen.add(it["guid"])
            state["seen"].append(it["guid"])
            log.info("blog_watch[%s]: 날짜가드 — %s (%s) 옛 글 push 생략",
                     bid, it.get("title", "")[:30], it.get("pubDate", ""))
            continue
        fresh.append(it)
    allowed = fresh

    pushed = 0
    for it in allowed:
        # 원문 전문 fetch — RSS 발췌는 잘려 옴. 실패 시 RSS 발췌 유지.
        full_text = _fetch_post_text(it.get("link") or "")
        if full_text:
            it["desc"] = full_text
            it["full"] = True
        _save_archive(it)
        if _push(it):
            pushed += 1
        # 관계후보 자동발굴 재료 누적(본문 = 전문 우선) — run() 후 일괄 추출.
        if new_texts is not None:
            txt = (it.get("desc") or "").strip()
            if txt:
                new_texts.append(
                    {"text": txt, "source": f"blog:{it.get('blog_title') or bid}"})
        seen.add(it["guid"])
        state["seen"].append(it["guid"])
    log.info("blog_watch[%s]: 새 글 %d개 수집, %d push, 카테고리외 %d 제외",
             bid, len(allowed), pushed, skipped_cat)
    return pushed


def run() -> int:
    state = _load_state()
    seen = set(state.get("seen", []))
    fetched_any = False
    new_texts: list[dict] = []          # 새 글 본문 — 관계후보 자동발굴 재료
    for blog in _BLOGS:
        r = _process_blog(blog, state, seen, new_texts)
        if r >= 0:
            fetched_any = True
    _save_state(state)
    if not fetched_any:
        return 1            # 전 블로그 RSS 실패 — 타이머에 실패 신호
    # RSS 를 읽는 데 성공 = 수집기 생존(새 글 0건인 날에도 화면이 말하게).
    try:
        from bot import feed_health as _fh
        _fh.mark("blog")
    except Exception:
        pass

    # 레퍼런스북 관계후보 자동발굴(kg-gen 패턴) — 새 글 본문에서 (회사)-(관계)-
    # (대상) 후보 추출 → 승인 큐 CSV 적재 + 채널 알림. 킬스위치·Gemini 키부재·
    # 실패 전부 graceful(블로그 수집 본류엔 영향 0). 새 글 없으면 no-op.
    if new_texts:
        try:
            from trade import kg_candidates
            got = kg_candidates.run_blog_extraction(new_texts)
            if got:
                log.info("blog_watch: 관계후보 %d건 발굴(승인 큐)", len(got))
        except Exception as exc:
            log.warning("blog_watch: kg_candidates extraction failed: %s", exc)

    # 기존 잘린 글 점진 백필(전 블로그 공통 아카이브, 사이클당 3건) — 새 글 없어도.
    try:
        bf = _backfill_full(max_n=3)
        if bf:
            log.info("blog_watch: 전문 백필 %d건", bf)
    except Exception as exc:
        log.warning("blog_watch: backfill failed: %s", exc)
    # 대시보드 갱신 — blog.html (레딧 워처 패턴 mirror) + valuechain.html(kg 엣지)
    try:
        from bot.dashboard import regenerate_blog_index, regenerate_valuechain_index
        regenerate_blog_index()
        regenerate_valuechain_index()
    except Exception as exc:
        log.warning("blog_watch: dashboard regen failed: %s", exc)
    return 0


def check(blog_id: str) -> int:
    """`python -m bot.blog_watch --check <blogId>` — 한 블로그의 수집 가능
    여부를 **갈래로** 말한다(0=정상, 1=문제).

    ⚠️ 왜 제품에 있나(사용자 2026-08-26): 블로그를 추가할 때마다 "이거
    수집되나?"를 손으로 명령을 조립해 물어봤고, 실제로 **틀린 명령**을
    건넸다 — `_fetch_rss` 는 파싱 결과가 아니라 **원문 XML 문자열**을
    돌려주는데 `r[0]`/`len(r[1])` 로 읽어 `채널: <  · 항목: 1` 이라는
    무의미한 출력이 나왔다(문자열의 첫 글자와 두 번째 글자 길이다).
    반복 진단은 손으로 만들지 말고 제품에 심는다(Automation-first).

    그리고 '안 된다'를 한 단어로 말하지 않는다(#82) — 도달 실패 / 항목 없음
    (이웃공개 등) / 파싱 실패를 **갈라서** 말한다.
    """
    known = {b["id"]: b for b in _BLOGS}
    reg = known.get(blog_id)
    print(f"■ {blog_id} — 레지스트리: "
          + (f"등록됨(표시명 '{reg['title']}', "
             + ("전체 글" if reg["categories"] is None
                else f"카테고리 {reg['categories']}") + ")"
             if reg else "미등록 ⚠️ (_BLOGS 에 없어 자동수집 안 함)"))
    xml = _fetch_rss(blog_id)
    if not xml:
        print("  ❌ RSS 도달 실패 — 404/차단이거나 <item> 이 없다"
              " (이웃공개 블로그면 RSS 자체가 안 나온다)")
        return 1
    print(f"  ✅ RSS 응답 {len(xml):,}자")
    title = _parse_channel_title(xml)
    items = _parse_items(xml)
    print(f"  채널 제목: {title or '(파싱 실패)'}")
    print(f"  항목: {len(items)}개")
    if not items:
        # 대조 0건은 통과가 아니라 실패다(#54) — 원문 표본을 같이 찍는다(#109).
        print("  ❌ 항목 0개 — 원문 앞부분:")
        print("     " + xml[:400].replace("\n", " "))
        return 1
    for it in items[:5]:
        _c = it.get("category") or "(없음)"
        print(f"   · {it['pubDate'][:16]:16} [{_c[:12]:12}] {it['title'][:40]}")
    # ⚠️ **카테고리 필터를 걸려면 원천이 뭐라고 싣는지 봐야 한다** — 이름을
    # 추측하면 한 건도 안 맞거나(접두어 불일치) 전부 새어 나간다(#12·#25).
    # `categories="접두어"` 는 이 값에 startswith 로 걸린다.
    counts: dict[str, int] = {}
    for it in items:
        counts[(it.get("category") or "(없음)")] = counts.get(
            it.get("category") or "(없음)", 0) + 1
    print("  카테고리 분포 (categories= 에 쓸 접두어):")
    for c, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"   · {n:3d}건  {c}")
    cats = (reg or {}).get("categories")
    if cats is not None:
        _hit = sum(n for c, n in counts.items() if c.startswith(cats))
        print(f"  현재 필터 categories={cats!r} → {_hit}/{len(items)}건 통과"
              + ("  ⚠️ 0건 — 접두어가 원천 카테고리와 안 맞는다" if not _hit else ""))
    seen = _load_state()
    inited = bool((seen.get("init") or {}).get(blog_id))
    print(f"  초기화: {'완료 — 새 글부터 push' if inited else '미완료 — 다음 run 이 기존 글을 seen 처리(백필 없음)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys as _sys
    argv = list(_sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--check":
        if len(argv) < 2:
            print("사용법: python -m bot.blog_watch --check <blogId>")
            return 2
        return check(argv[1])
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
