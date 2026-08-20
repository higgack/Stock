"""DART 공시 · Market cap 두 대시보드 전수 감사 (사용자 2026-08-20 '아주 꼼꼼히 검증').

실행:  cd ~/stock && .venv/bin/python -m bot.scripts.dart_mcap_audit

원칙(실수 #35): **화면이 쓰는 그 경로**를 그대로 태운다 — DART 는
`_load_dart_feed_data()` → `_render_dart_feed_page()`(실제 HTML 을 생성해
화면 숫자를 정규식으로 되읽음), Market cap 은 `fetch_all_axes()`(페이지 regen
과 동일 호출, 캐시 fresh 면 캐시).  실수 #21: 시작 줄에 감사·파서 버전 배너.

읽기 전용 — 아카이브/캐시를 쓰지 않는다(marketcap 은 캐시 만료 시 페이지와
같은 재수집이 일어나며, 그건 화면 경로 그 자체다).
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

AUDIT_VER = 3   # 3 = 파일 날짜 계약을 date 필드로 정정·축간 시차 허용
_KST = timezone(timedelta(hours=9))

_OK, _NG, _WARN = "✅", "❌", "⚠️"

# 카드 여는 태그만 — 'df-card-hd'(카드 내부) 오검출 차단.
_CARD_RE = re.compile(r'<div class="df-card[" ]')


def _p(s: str = "") -> None:
    print(s, flush=True)


def _mark(ok: bool, warn: bool = False) -> str:
    return _WARN if warn else (_OK if ok else _NG)


# ── DART ────────────────────────────────────────────────────────────────────

def audit_dart() -> None:
    _p("\n" + "=" * 72)
    _p("1) DART 공시 대시보드")
    _p("=" * 72)
    import bot.dashboard as d
    from bot import dart_feed as df

    t0 = time.time()
    by_date = d._load_dart_feed_data(days_back=30)
    if not by_date:
        _p(f"{_NG} 아카이브 0건 — ~/.tradingagents/dart_feed_archive 확인")
        return
    raw_total = sum(len(v) for v in by_date.values())
    dates = sorted(by_date)
    _p(f"[데이터] 파일 {len(dates)}일 · {dates[0]} ~ {dates[-1]} · 원본 {raw_total}건 "
       f"({time.time() - t0:.1f}s)")

    # 창(30일) 안에 파일이 없는 날 — 수집 구멍
    today = datetime.now(_KST).date()
    win = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
    missing = [x for x in win if x not in by_date]
    # 주말·휴일은 공시가 없는 게 정상이다 — 거래일만 지적해야 경보가 의미를
    # 갖는다(휴일까지 세면 매번 ⚠️ 라 아무도 안 본다).
    try:
        from bot.market_calendar import last_session_on_or_before
        miss_sess = [x for x in missing if last_session_on_or_before("KR", x) == x]
        _known = last_session_on_or_before("KR", today.isoformat()) is not None
    except Exception:
        miss_sess, _known = [], False
    if _known:
        _p(f"{_mark(not miss_sess)} 창 결측 **거래일** {len(miss_sess)}일 "
           f"{miss_sess} (비거래일 결측 {len(missing) - len(miss_sess)}일은 정상)")
    else:
        _p(f"{_WARN} 창 결측일 {len(missing)}일 {missing} "
           "— 거래일 캘린더 미설치라 주말·휴일 구분 불가(pip install exchange_calendars)")

    # ① rcept_no 무결성: 빈값 · 파일날짜 불일치 · 중복(파일내·교차)
    seen: dict[str, str] = {}
    dup_same = dup_cross = blank = datemis = 0
    mis_ex: list[str] = []
    dup_ex: list[str] = []
    rno_ex: list[str] = []
    rno_dt_diff = 0
    for ds, items in by_date.items():
        inday: set[str] = set()
        for it in items:
            rno = str(it.get("rcept_no") or "").strip()
            if not rno:
                blank += 1
                continue
            if rno in inday:
                dup_same += 1
            inday.add(rno)
            prev = seen.get(rno)
            if prev is not None and prev != ds:
                dup_cross += 1
                if len(dup_ex) < 5:
                    dup_ex.append(f"{rno}: {prev} · {ds}")
            seen[rno] = ds
            _raw = str(it.get("date") or "")
            if _raw[:8] != ds.replace("-", ""):
                datemis += 1
                if len(mis_ex) < 6:
                    mis_ex.append(f"{rno} date={_raw!r} → 파일 {ds}")
            if len(rno) >= 8 and rno[:8] != _raw[:8]:
                rno_dt_diff += 1
                if len(rno_ex) < 4:
                    rno_ex.append(f"{rno} → 공시일자 {_raw} · {it.get('report_nm','')[:24]}")
    _p(f"{_mark(not blank)} rcept_no 결측 {blank}건")
    _p(f"{_mark(not dup_same)} 같은 날 파일 내 rcept_no 중복 {dup_same}건")
    _p(f"{_mark(not dup_cross, warn=bool(dup_cross))} 날짜 간 rcept_no 중복 "
       f"{dup_cross}건 {dup_ex} — 아카이브 잔재. 렌더가 1건만 그리므로 화면 "
       "총계엔 영향 없음(아래 '전체 필 = 실제 카드'로 확인)")
    _p(f"{_mark(not datemis)} 항목의 공시일자(date) ≠ 파일 날짜 {datemis}건"
       + (f" 예: {mis_ex}" if mis_ex else " (카드 날짜라벨 정합)"))
    # 접수번호 앞 8자리와 공시일자가 다른 건 **원천이 그렇게 주는 것**이라
    # 정상이다(2026-08-20 21일치 12,930건 중 70건). 참고로만 센다 — 예전 v1·v2
    # 는 이걸 ❌ 로 찍어 정상 데이터를 결함처럼 보이게 했다.
    _p(f"[참고] 접수번호 날짜 ≠ 공시일자 {rno_dt_diff}건 (원천 특성, 정상) "
       f"{rno_ex[:2]}")

    # ② 미래 날짜
    fut = [x for x in dates if x > today.strftime("%Y-%m-%d")]
    _p(f"{_mark(not fut)} 미래 날짜 파일 {len(fut)}건 {fut if fut else ''}")

    # ③ 카테고리 분포 (필 도달 가능성은 ⑤ 렌더에서 실제 필로 확인)
    cats = Counter(str(it.get("category") or "기타")
                   for items in by_date.values() for it in items)
    _off = {k: v for k, v in cats.items() if k not in set(d._DART_CATEGORIES[1:])}
    _p(f"[카테고리] 카탈로그 밖 {sum(_off.values())}건 "
       f"{dict(sorted(_off.items(), key=lambda kv: -kv[1]))} "
       "— 필이 붙는지는 아래 'Σ(카테고리 필) = 전체'로 판정")
    _p("    분포: " + ", ".join(
        f"{k} {v}" for k, v in sorted(cats.items(), key=lambda kv: -kv[1])))

    # ④ _equity_noise 로 숨는 카드 = 화면 카운트 기준 차이의 근원
    hidden = 0
    hid_by_date: Counter = Counter()
    for ds, items in by_date.items():
        for it in items:
            if d._equity_noise_impl(it):
                hidden += 1
                hid_by_date[ds] += 1
    _p(f"[숨김] _equity_noise 숨김 {hidden}건 / 원본 {raw_total}건 "
       f"→ 표시 대상 {raw_total - hidden}건")

    # ⑤ 렌더 — 화면 숫자를 HTML 에서 되읽어 실제 카드 수와 대조
    t1 = time.time()
    html, frags = d._render_dart_feed_page(by_date)
    _p(f"[렌더] html {len(html):,}자 · 프래그먼트 {len(frags)}개 ({time.time() - t1:.1f}s)")

    def _cards_by_date(text: str) -> Counter:
        out: Counter = Counter()
        ms = list(re.finditer(
            r'<div class="df-date-group(?: collapsed)?" data-date="([^"]+)">', text))
        for i, m in enumerate(ms):
            end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
            # ⚠️ '<div class="df-card' 만 세면 카드 안의 '<div class="df-card-hd"'
            # 까지 잡혀 정확히 2배가 된다(2026-08-20 픽스처로 발각).
            out[m.group(1)] = len(_CARD_RE.findall(text, m.end(), end))
        return out

    cards = _cards_by_date(html)
    for _fc in frags.values():
        cards.update(_cards_by_date(_fc))
    total_cards = sum(cards.values())

    m = re.search(r'data-cat="전체">전체 (\d+)</button>', html)
    pill_all = int(m.group(1)) if m else -1
    _p(f"{_mark(pill_all == total_cards)} 전체 필 {pill_all} vs 실제 카드 {total_cards}")

    pill_cats = {mm.group(1): int(mm.group(2)) for mm in re.finditer(
        r'<button class="df-pill" data-cat="([^"]+)">[^<]*? (\d+)</button>', html)}
    pill_sum = sum(v for k, v in pill_cats.items() if k != "전체")
    _p(f"{_mark(pill_sum == pill_all)} Σ(카테고리 필) {pill_sum} = 전체 {pill_all} "
       f"(차 {pill_all - pill_sum} = 어떤 필로도 도달 못 하는 카드)")

    # 카테고리 필 카운트 vs 실제 data-cat 카드 수
    _CAT_RE = re.compile(r'<div class="df-card[^"]*" data-cat="([^"]+)"')
    dc = Counter(_CAT_RE.findall(html))
    for _fc in frags.values():
        dc.update(_CAT_RE.findall(_fc))
    bad = {k: (v, dc.get(k, 0)) for k, v in pill_cats.items() if dc.get(k, 0) != v}
    _p(f"{_mark(not bad)} 카테고리 필 카운트 ≠ 실제 카드 수 {len(bad)}건 {bad}")

    # 일자 헤더 카운트(서버 렌더 값) vs 실제 카드 수 — 초기 로드 화면 그대로
    hd: dict[str, int] = {}
    for text in [html] + list(frags.values()):
        ms = list(re.finditer(
            r'<div class="df-date-group(?: collapsed)?" data-date="([^"]+)">'
            r'.*?<span class="df-date-cnt">(\d+)</span>', text, re.S))
        for mm in ms:
            hd[mm.group(1)] = int(mm.group(2))
    if not hd:
        _p(f"{_NG} 일자 헤더 라벨 파싱 0건 — 감사 패턴이 렌더와 어긋남(검증 불가)")
    mism = {k: (v, cards.get(k, 0)) for k, v in hd.items() if cards.get(k, 0) != v}
    _p(f"{_mark(bool(hd) and not mism)} 일자 헤더 카운트 ≠ 그 날 실제 카드 수 "
       f"{len(mism)}/{len(hd)}일"
       + (f" 예: {dict(list(sorted(mism.items(), reverse=True))[:5])}" if mism else ""))

    # 월 헤더 카운트 vs 실제
    mh: dict[str, int] = {}
    for mm in re.finditer(r'data-month="([^"]+)"', html):
        seg = html[mm.end():mm.end() + 900]
        c = re.search(r'<span class="df-month-cnt">(\d+)건</span>', seg)
        if c:
            mh[mm.group(1)] = int(c.group(1))
    real_m: Counter = Counter()
    for ds, n in cards.items():
        real_m[ds[:7]] += n
    if not mh:   # 라벨을 못 읽으면 '이상 없음'이 아니라 감사 실패다(실수 #41)
        _p(f"{_NG} 월 헤더 라벨 파싱 0건 — 감사 패턴이 렌더와 어긋남(검증 불가)")
    else:
        mmis = {k: (v, real_m.get(k, 0)) for k, v in mh.items()
                if real_m.get(k, 0) != v}
        _p(f"{_mark(not mmis)} 월 헤더 카운트 ≠ 그 달 실제 카드 수 "
           f"{len(mmis)}/{len(mh)}월 {mmis}")

    # 빈 날짜 그룹(카드 0장인데 헤더만 렌더)
    empty = sorted([k for k, v in cards.items() if v == 0], reverse=True)
    _p(f"{_mark(not empty)} 카드 0장 날짜 그룹 {len(empty)}일 {empty[:6]}")

    # ⑥ 주석(_annotate) 불변식 — 플래그 필 카운트 vs data-flag
    def _n(pat: str) -> int:
        mm = re.search(pat, html)
        return int(mm.group(1)) if mm else 0
    p_sig = _n(r'data-flag="sig">🔥 중요 (\d+)<')
    p_unp = _n(r'data-flag="unparsed">⚠️ 미파싱 (\d+)<')
    p_nop = _n(r'data-flag="noparse">미파싱제외 (\d+)<')
    fl: Counter = Counter()
    for text in [html] + list(frags.values()):
        for f in re.findall(r'<div class="df-card[^"]*" data-cat="[^"]*" data-flag="([^"]*)"', text):
            for tok in f.split():
                fl[tok] += 1
    both = 0
    for text in [html] + list(frags.values()):
        both += len(re.findall(r'data-flag="[^"]*unparsed[^"]*noparse|'
                               r'data-flag="[^"]*noparse[^"]*unparsed', text))
    _p(f"{_mark(p_sig == fl['sig'])} 🔥중요 필 {p_sig} vs 카드 {fl['sig']}")
    _p(f"{_mark(p_unp == fl['unparsed'])} ⚠️미파싱 필 {p_unp} vs 카드 {fl['unparsed']}")
    _p(f"{_mark(p_nop == fl['noparse'])} 미파싱제외 필 {p_nop} vs 카드 {fl['noparse']}")
    _p(f"{_mark(not both)} 미파싱∧미파싱제외 동시 카드 {both}건 (상호배타여야)")
    _tgt = sum(1 for items in by_date.values() for it in items
               if not d._equity_noise_impl(it) and bool(df.is_parse_target(it)))
    _rate = (fl['unparsed'] / _tgt * 100) if _tgt else 0.0
    _p(f"[파싱] 파싱대상 {_tgt}건 중 미파싱 {fl['unparsed']}건 = {_rate:.1f}%")

    # ⑦ 기준시각 — 화면이 '언제 것'인지 말하는가(규칙 10b/43)
    hdr = re.search(r'<p class="sub">출처 DART\(OpenDART\).*?</p>', html, re.S)
    _htxt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", hdr.group(0))).strip() if hdr else ""
    _p(f"{_mark('최신 공시' in _htxt)} 기준(데이터 as-of 표기): {_htxt or '(없음)'}")
    _p(f"{_mark('⚠️ 지연' not in (hdr.group(0) if hdr else ''))} 지연 배지 미표시"
       " (표시되면 마지막 KR 거래일 공시가 아직 없다는 뜻)")
    _p(f"       아카이브 최신 접수일 {dates[-1]} · 최신일 카드 {cards.get(dates[-1], 0)}장")
    fs = _fullscan_age()
    _p(f"       마지막 풀스캔 마커 {fs}")

    # ⑧ 비용카드 3창
    cost = re.search(r'관계후보 발굴 비용[^<]*', html)
    _p(f"{_mark(bool(cost and '오늘' in cost.group(0) and '이번 달' in cost.group(0) and '누적' in cost.group(0)))}"
       f" 비용 3창(오늘/이번달/누적): {cost.group(0)[:110] if cost else '(없음)'}")

    # ⑨ 상세 없는 카드 중 시총줄만 있는 비율 등 렌더 품질
    nocard_link = len(re.findall(r'class="df-ticker-link"', html))
    _p(f"[참고] 인라인(최신월) 종목코드 링크 {nocard_link}개 · "
       f"시총줄 {len(re.findall('df-mcap', html))}개")


def _fullscan_age() -> str:
    from pathlib import Path
    p = Path.home() / ".tradingagents" / ".dart_fullscan_ts"
    try:
        from bot.dart_feed import _FULLSCAN_TS as p2
        p = p2
    except Exception:
        pass
    try:
        ts = p.stat().st_mtime
        return (datetime.fromtimestamp(ts, _KST).strftime("%Y-%m-%d %H:%M")
                + f" ({(time.time() - ts) / 60:.0f}분 전)")
    except OSError:
        return "(없음)"


# ── Market cap ──────────────────────────────────────────────────────────────

_UNIT = {"T": 1e12, "TRILLION": 1e12, "B": 1e9, "BILLION": 1e9,
         "M": 1e6, "MILLION": 1e6}


def _money(s: str):
    """'$4.769 T' / '-$144.36 Billion' → float. 실패 None."""
    t = (s or "").strip().replace(",", "")
    m = re.match(r"^(-?)\$?\s*([\d.]+)\s*([A-Za-z]*)$", t)
    if not m:
        return None
    try:
        v = float(m.group(2))
    except ValueError:
        return None
    v *= _UNIT.get(m.group(3).upper(), 1.0) if m.group(3) else 1.0
    return -v if m.group(1) == "-" else v


def audit_marketcap() -> None:
    _p("\n" + "=" * 72)
    _p("2) Market cap 대시보드")
    _p("=" * 72)
    import bot.dashboard as d
    import bot.marketcap_client as mc

    _p(f"[설정] _PARSER_V={mc._PARSER_V} · _TTL={mc._TTL / 3600:.2f}h · "
       f"축간격 {mc._AXIS_GAP_SEC}s · 축 {len(mc.EMBED_AXES)}개")
    _regen_h = 3.0
    _p(f"{_mark(mc._TTL < _regen_h * 3600)} 캐시 TTL({mc._TTL / 3600:.2f}h) "
       f"< 재생성 주기({_regen_h:.0f}h)  ← 실수 #36(TTL≥주기면 절반이 캐시에 걸려 안 신선)")

    now = time.time()
    for key, lbl, _slug, _mcol in mc.EMBED_AXES:
        c = mc._cache_read(key)
        age = (now - (c.get("ts") or 0)) / 3600 if c.get("ts") else None
        _p(f"    캐시 {key:9s} rows={len(c.get('rows') or []):3d} "
           f"_pv={c.get('_pv')} fetched_at={c.get('fetched_at') or '—'} "
           f"src={c.get('source') or '—'} "
           f"age={'—' if age is None else f'{age:.2f}h'}")

    t0 = time.time()
    data = mc.fetch_all_axes(100)          # 페이지 regen 과 동일 호출
    _p(f"[수집] fetch_all_axes ({time.time() - t0:.1f}s)")

    for key, lbl, _slug, mcol in mc.EMBED_AXES:
        dd = data.get(key) or {}
        rows = dd.get("rows") or []
        head = (f"  ── {lbl} ({key}) · {len(rows)}행 · 기준 {dd.get('fetched_at') or '—'}"
                f" · src {dd.get('source') or '—'}"
                f"{'  ' + _WARN + ' stale(최신 수집 실패)' if dd.get('stale') else ''}")
        _p("")
        _p(head)
        if not rows:
            _p(f"  {_NG} 0행 — 수집 실패(사이트 구조 변경/차단)")
            continue
        ranks = [r.get("rank") for r in rows]
        contig = ranks == list(range(1, len(rows) + 1))
        _p(f"  {_mark(contig)} 순위 1..{len(rows)} 연속 "
           f"{'' if contig else f'— 실제 앞부분 {ranks[:12]}'}")
        vals = [_money(str(r.get("metric") or "")) for r in rows]
        nmiss = sum(1 for v in vals if v is None)
        _p(f"  {_mark(not nmiss, warn=bool(nmiss))} 메트릭 파싱 실패 {nmiss}행"
           + ("" if not nmiss else " 예: " + str([
               (r.get("name"), r.get("metric"), r.get("price"))
               for r, v in zip(rows, vals) if v is None][:4])))
        # ⚠️ 축마다 정렬 방향이 다르다 — P/E 는 낮은 순, MC loss 는 손실이 큰
        # 순(부호로는 오름차순)이다. '내림차순'을 일괄로 기대하면 정상 화면을
        # ❌ 로 찍는다(v1 이 그랬다 — 감사가 거짓 경보를 내는 실수 #47 의 짝).
        seq = [abs(v) if key == "mc_loss" else v for v in vals if v is not None]
        desc = all(a >= b for a, b in zip(seq, seq[1:]))
        asc = all(a <= b for a, b in zip(seq, seq[1:]))
        _dir = "내림차순" if desc else "오름차순" if asc else "정렬 깨짐"
        _base = "절대값 " if key == "mc_loss" else ""
        _p(f"  {_mark(desc or asc)} 메트릭 {_base}단조 정렬 ({_dir}) "
           + ("" if (desc or asc) else "— 역전 " + str([
               (rows[i].get("name"), vals[i], rows[i + 1].get("name"), vals[i + 1])
               for i in range(len(vals) - 1)
               if vals[i] is not None and vals[i + 1] is not None
               and vals[i] < vals[i + 1]][:3])))
        # 컬럼 밀림 탐지 — 시총류 축만: metric/price = 발행주식수 sane band
        if key in ("marketcap", "revenue", "earnings"):
            odd = []
            for r, v in zip(rows, vals):
                p = _money(str(r.get("price") or ""))
                if v is None or not p:
                    continue
                sh = v / p
                if key == "marketcap" and not (1e6 <= sh <= 1e12):
                    odd.append((r.get("name"), r.get("metric"), r.get("price"),
                                f"{sh:,.0f}주"))
            _p(f"  {_mark(not odd)} 시총÷주가 = 발행주식수 이상치 {len(odd)}행 {odd[:4]}"
               if key == "marketcap" else
               f"  (참고) {key} 는 주식수 검산 대상 아님")
        tks = [str(r.get("ticker") or "") for r in rows]
        dups = [k for k, v in Counter(t for t in tks if t).items() if v > 1]
        _p(f"  {_mark(not dups)} 티커 중복 {len(dups)} {dups[:6]} · "
           f"빈 티커 {sum(1 for t in tks if not t)}행")
        _p(f"  {_mark(sum(1 for r in rows if not r.get('name')) == 0)} 빈 회사명 "
           f"{sum(1 for r in rows if not r.get('name'))}행 · "
           f"빈 국가 {sum(1 for r in rows if not r.get('country'))}행 · "
           f"로고없음 {sum(1 for r in rows if not r.get('logo'))}행")
        nmv = sum(1 for r in rows if r.get("rank_move"))
        if key == "marketcap":     # 원본이 moves 속성을 주는 축(실측)
            _p(f"  {_mark(nmv > 0)} 순위변화(moves) 비영 {nmv}행 "
               + ("" if nmv else "— 전 행 0 = moves 속성 파서 회귀 의심"))
        else:
            _p(f"  (참고) 순위변화 비영 {nmv}행 — 이 축은 원본이 moves 를 안 준다")
        pcs = [r.get("chg_pct") for r in rows]
        nnone = sum(1 for x in pcs if x is None)
        wild = [(r.get("name"), r.get("chg_pct"), r.get("metric"), r.get("price"))
                for r in rows
                if r.get("chg_pct") is not None and abs(r["chg_pct"]) > 50]
        _big_ok = key in ("mc_gain", "mc_loss")   # 큰 변동이 이 축의 존재이유
        _p(f"  {_mark(not wild, warn=bool(wild))} Today% 결측 {nnone}행 · "
           f"|변동|>50% {len(wild)}행 {wild[:3]}"
           + ("  (이 축은 큰 변동이 정상 — 눈으로 확인용)" if _big_ok else ""))
        cty = Counter(r.get("country") or "—" for r in rows)
        _p(f"  국가 상위: " + ", ".join(f"{k} {v}" for k, v in cty.most_common(5)))
        _p(f"  1위: {rows[0].get('name')} ({rows[0].get('ticker')}) "
           f"{rows[0].get('metric')} / {rows[0].get('price')} / "
           f"{rows[0].get('chg_pct')}% / {rows[0].get('country')}")

    # Today% 는 어느 축에서 보든 **같은 회사면 같은 값**이어야 한다. 축마다
    # 그 열의 의미가 다르면(기간 등락 등) 여기서 갈린다 — 외부 자료 없이
    # 화면 자체로 판정할 수 있는 유일한 검산이다.
    base = {str(r.get("ticker")): r.get("chg_pct")
            for r in ((data.get("marketcap") or {}).get("rows") or [])
            if r.get("ticker") and r.get("chg_pct") is not None}
    _p("")
    _p("  ── Today% 축간 대조 (기준: Market Cap 축)")
    for key, lbl, *_ in mc.EMBED_AXES:
        if key == "marketcap":
            continue
        rows = (data.get(key) or {}).get("rows") or []
        pairs = [(r.get("name"), base[str(r.get("ticker"))], r.get("chg_pct"))
                 for r in rows
                 if str(r.get("ticker")) in base and r.get("chg_pct") is not None]
        # 6축을 1.5초 간격으로 따로 긁으므로 **장중 시장**(KR·CN 등) 종목은
        # 소수점 아래가 흔들린다 — 그건 시차지 의미 차이가 아니다. 1.0%p 를
        # 넘으면 그 열이 다른 걸 보고 있다는 뜻이라 ❌.
        skew = [x for x in pairs if 0.02 < abs(x[1] - x[2]) <= 1.0]
        bad = [x for x in pairs if abs(x[1] - x[2]) > 1.0]
        _p(f"  {_mark(not bad, warn=bool(skew))} {lbl}: 공통 {len(pairs)}종목 · "
           f"의미 불일치(>1.0%p) {len(bad)} {bad[:3]} · 수집시차(≤1.0%p) "
           f"{len(skew)} {skew[:3]}")

    # 렌더 경로
    page = d._render_marketcap_page(data)
    _ntab = page.count('class="mc-tab"')
    _next = page.count("mc-ext")
    _p("")
    _p(f"[렌더] {len(page):,}자 · 탭 {_ntab}개 · 외부필 {_next}개")
    hdr = re.search(r'글로벌 기업 순위 · 출처.*?</p>', page, re.S)
    _p(f"{_mark(bool(hdr))} 헤더 기준시각: "
       + (re.sub(r'<[^>]+>', ' ', hdr.group(0)) if hdr else '(없음)').strip()[:180])
    fa = [(k, (data.get(k) or {}).get("fetched_at")) for k, *_ in mc.EMBED_AXES]
    uniq = sorted({v for _k, v in fa if v})
    _p(f"{_mark(len(uniq) <= 1, warn=len(uniq) > 1)} 축별 기준시각 {len(uniq)}종 {uniq} "
       f"— 헤더는 첫 축 값만 표기(축마다 다르면 헤더가 대표값)")
    _p(f"{_mark('데이터 수집 실패' not in page)} '데이터 수집 실패' 안내 노출 여부")


def main() -> None:
    _p(f"=== dart_mcap_audit v{AUDIT_VER} · "
       f"{datetime.now(_KST).strftime('%Y-%m-%d %H:%M:%S')} KST · "
       f"python {sys.version.split()[0]} ===")
    try:
        audit_dart()
    except Exception as exc:
        import traceback
        _p(f"{_NG} DART 감사 예외: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    try:
        audit_marketcap()
    except Exception as exc:
        import traceback
        _p(f"{_NG} Market cap 감사 예외: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    _p("\n=== 끝 ===")


if __name__ == "__main__":
    main()
