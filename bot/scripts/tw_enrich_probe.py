"""대만 급등·급락의 **업종·시총**이 왜 비는가 — 단계별 실측.

사용자 2026-09-10: "업종이랑 시총 안나오는건 너무 작아서 그런거야?"
크기는 두 값 **어느 경로에도 들어가지 않는다** — 업종은 上市·上櫃 전종목
일괄 맵이고 시총은 티커마다 만든다. 그러니 '작아서'는 가설이 아니라 아직
안 잰 것이다(#12). 같은 질문이 세 번째라(2026-08-04 업종 · 08-19 업종 ·
오늘 업종+시총) 확인 명령을 건네는 대신 단계를 갈라 센다(#252).

  ① 대상        화면과 같은 입력(오늘 무버 TOP30) 또는 인자로 준 코드(#35).
                인자 모드도 종가를 붙인다 — FinMind 시총 폴백이 종가를 쓰므로
                None 으로 넘기면 화면과 다른 결과가 난다(#145).
  ② 캐시·회로    화면이 읽는 그 자리 — **③이 갱신하기 전에** 먼저 찍는다
  ③ 렌더-세이프  cache-only = 화면이 **지금** 받는 값(#35 화면이 쓰는 그 경로)
  ④ 소스        업종 上市/上櫃 OpenAPI 를 지금 직접 받아 코드별로 어디에 있나
                (--slow 면 ④-b: 백그라운드와 같은 yfinance/FinMind 경로)
  ⑤ 판정        갈래로(#82) — 판정 불가는 ✅ 가 아니다(#54). ④-b 를 돌렸으면
                그 결과가 판정에 **들어간다**(같은 실행이 반증한 원인을 적지 않는다)
  ⑥ 한글명      **종목별로** 왜 아직 한자인가(사용자 2026-09-17 "최대한 한글화 한것
                맞지? 5번은 넘게 이거 돌리는듯하네"). 거부된 번역은
                `translate_miss.json` 에 프롬프트 지문과 함께 남아 **같은
                프롬프트로는 다시 묻지 않는다** — 그런데 화면·진단은 "다음 빌드가
                채운다" 고 말해 왔다(#55). 그래서 갈래를 이름으로 적는다:
                거부(영구) / 재시도 예정 / 미시도 / 영문폴백. LLM 0(cache-only)

    cd ~/stock && .venv/bin/python -m bot.scripts.tw_enrich_probe
    cd ~/stock && .venv/bin/python -m bot.scripts.tw_enrich_probe --slow
    cd ~/stock && .venv/bin/python -m bot.scripts.tw_enrich_probe 8227 6949

부수효과를 사실대로(#284): 기본 실행은 **화면 렌더와 같은 경로**라 렌더가
원래 하는 갱신(업종맵 24h 캐시 · finviz 업종 보조맵)은 한다. LLM 번역은
0(want_name 을 열지 않는다 — #312·#321), 시총 디스크 캐시는 쓰지 않는다.
`--slow` 만 백그라운드와 같은 경로(yfinance·FinMind)를 태워 시총 캐시를
**채운다**(= 화면을 고치는 쓰기). fast_info 레이트리밋 쿨다운은 봇
프로세스 메모리에만 있어 여기서는 **보이지 않는다** — 그래서 말하지 않는다.
"""
from __future__ import annotations

import re
import sys
import time

from bot.chart_translate import _MAX_BATCH
from bot.finviz_client import MCAP_PERSIST_TTL
from bot.twse_client import _TW_IND_CACHE_TTL

_PROBE_VER = 6
# 문턱은 **제품에서 가져온다** — 복제하면 진단이 화면과 다른 말을 한다(#38).
_MCAP_TTL_H = MCAP_PERSIST_TTL / 3600
_IND_TTL_H = _TW_IND_CACHE_TTL / 3600


def _p(*a):
    print(*a, flush=True)


def _age_label(sec: float | None) -> str:
    if sec is None:
        return "파일 없음"
    h = sec / 3600.0
    return f"{h:.1f}시간 전" if h >= 1 else f"{sec / 60:.0f}분 전"


def enrich_verdict(*, n: int, render_ok: bool, mcap_filled: int, ind_filled: int,
                   mcap_age_sec: float | None, map_n: int, in_map: int,
                   src_in: int, yf_pause: bool,
                   slow: tuple[int, int] | None = None) -> list[str]:
    """②③④ 실측 → 사람이 읽는 판정 줄(순수 함수 — 값으로 검증, #41·#176).

    모집단을 섞지 않는다: `map_n`·`in_map` 은 **화면이 읽는 캐시 맵** 하나에서
    세고, `src_in` 은 ④가 지금 직접 받은 원천에서 센다(둘이 갈리면 그 자체가
    '캐시가 낡았다'는 판정이다). `slow` 는 ④-b 를 돌렸을 때 (시총, 업종) 채움
    수 — 같은 실행이 채웠는데 '캐시 파일이 없다'를 원인으로 적으면 거짓이다.
    갈래마다 처방이 다르므로 뭉뚱그리지 않는다(#82). 잴 게 없거나 재는
    경로가 죽었으면 ✅ 도 ❌ 도 아니라 판정 불가다(#54)."""
    if n <= 0:
        return ["❓ 대조할 종목이 0개 — 무버 목록을 못 받았다(원천/네트워크). "
                "업종·시총 판정 불가."]
    if not render_ok:
        return ["❓ 렌더 경로(③)가 예외로 끝나 화면 값을 못 쟀다 — 판정 불가. "
                "위 ③의 예외가 먼저다(캐시·원천 판정은 그 뒤에)."]
    out: list[str] = []

    # ── 시총
    if mcap_filled >= n:
        out.append(f"✅ 시총 {mcap_filled}/{n} — 화면이 채운다")
    elif slow is not None and slow[0] > mcap_filled:
        out.append(f"❌ 시총 {mcap_filled}/{n} (지금 화면) — 캐시 콜드였다: "
                   f"--slow 가 {slow[0]}/{n} 을 채워 디스크에 썼으니 다음 렌더부터 뜬다")
    elif slow is not None:
        out.append(f"❌ 시총 {mcap_filled}/{n} — --slow 로도 {slow[0]}/{n}: "
                   "원천(yfinance fast_info · FinMind 발행주식수)이 이 종목들을 못 준다")
    else:
        if mcap_age_sec is None:
            why = "디스크 캐시 파일이 없다(백그라운드 enrich 가 한 번도 못 썼다)"
        elif mcap_age_sec >= MCAP_PERSIST_TTL:
            why = (f"디스크 캐시가 {_age_label(mcap_age_sec)}이라 만료"
                   f"(TTL {_MCAP_TTL_H:.0f}시간) — 통째로 버려진다")
        else:
            why = (f"캐시는 살아 있는데({_age_label(mcap_age_sec)}) 이 티커들이 "
                   "그 안에 없다 — 백그라운드가 아직 못 채웠다")
        out.append(f"❌ 시총 {mcap_filled}/{n} — {why}")
    if mcap_filled < n and yf_pause:
        out.append("   ↪ yfinance 정지 마커가 켜져 있다 — 백그라운드가 "
                   "시총을 채울 수 없다(마커를 끄기 전엔 안 채워진다)")

    # ── 업종
    if ind_filled >= n:
        out.append(f"✅ 업종 {ind_filled}/{n} — 화면이 채운다")
    elif map_n <= 0 and src_in <= 0:
        out.append(f"❌ 업종 {ind_filled}/{n} — 캐시 맵도 원천(上市·上櫃 OpenAPI)도 "
                   "0종목 = 소스 장애. 이 맵은 전종목 일괄이라 종목 크기와 무관하다")
    elif map_n <= 0:
        out.append(f"❌ 업종 {ind_filled}/{n} — 캐시 맵이 없는데 원천엔 {src_in}/{n} 있다: "
                   "③이 방금 받아 썼거나 다음 렌더가 받는다 — 곧 채워진다")
    elif in_map >= n:
        out.append(f"❗ 업종 {ind_filled}/{n} — 캐시 맵({map_n}종목)엔 {in_map}/{n} 이 "
                   "다 있는데 화면 값이 비었다 = 배선 문제")
    elif src_in > in_map:
        out.append(f"⚠️ 업종 {ind_filled}/{n} — 캐시 맵({map_n}종목)엔 {in_map}/{n}, "
                   f"지금 원천엔 {src_in}/{n}: 맵이 낡았다(TTL {_IND_TTL_H:.0f}시간 "
                   "안이라 화면이 옛 맵을 읽는다 — 만료 뒤 첫 렌더가 갱신)")
    else:
        gap = n - max(in_map, src_in)
        out.append(f"⚠️ 업종 {ind_filled}/{n} — 上市·上櫃 파싱 맵 어디에도 없는 코드 "
                   f"{gap}개: 興櫃·ETF·신규상장이거나 원천이 그 코드를 안 실은 경우다 "
                   "— 어느 쪽인지는 ④ 원문으로(단정 안 함). yfinance `.TWO` 폴백은 "
                   "백그라운드에서만 돈다")
    if slow is not None and slow[1] > ind_filled:
        out.append(f"   ↪ --slow 가 업종을 {slow[1]}/{n} 까지 채웠다 — 다음 렌더부터 뜬다")

    # 사용자 질문에 대한 답은 **재서** 말한다(#165 안 잰 것을 단정하지 말 것).
    if map_n > 0 or src_in > 0:
        out.append(f"↪ '너무 작아서?' 에 대한 답: 일괄 맵(캐시 {map_n}종목)에 이 표의 "
                   f"{in_map}/{n}, 지금 받은 원천엔 {src_in}/{n} 이 실제로 들어 있다 — "
                   "크기가 이유라면 맵에도 없어야 한다")
    return out


def name_rows_diag(items: list, *, titles: dict, names: dict, miss: dict,
                   longnames: dict | None = None) -> list:
    """종목별 한글명 갈래(순수 · LLM 0). 화면이 쓰는 캐시를 그대로 조회한 결과를
    받아 **왜 그 상태인지**만 정한다 — 판정을 여기 두면 값으로 잴 수 있다(#176).

    ⚠️ 화면의 해소 순서를 그대로 따른다(독립 리뷰 2026-09-17 H1 — 첫 판은
    longName 을 아예 안 봐서, longName 으로 풀린 행을 '한자 잔존' 으로 과대
    보고했다, #35): yfinance longName 번역 → native 번역 → 영문 longName 폴백 →
    티커 캐시(`stock_panel` 이 마지막에 덮는다).

    갈래(#82): 'korean' · 'by_longname'/'by_title'/'by_ticker'(캐시가 풀어 준다) ·
    'latin'(원천 이름이 한자가 아님) · 'en_fallback'(longName 이 영문이라 화면이
    그걸 쓴다) · 'rejected'(물었는데 거부 — **영구**) · 'will_retry' ·
    'never_asked' · 'no_name'(원천 이름 없음 = 인자 모드).
    """
    from bot.chart_translate import has_han
    out = []
    for it in items:
        tk, nm = it.get("ticker"), it.get("name") or ""
        en = (longnames or {}).get(tk) or ""
        # ⚠️ 인자 모드(`… probe 8227 6949`)는 원천 이름이 없어 `name` 이 **코드**다
        # — 그걸 이름으로 세면 한자가 아니라서 '영문 폴백' 이라는 **거짓 갈래**가
        # 나온다(#35 진단은 화면이 보는 입력을 재야 한다).
        if not nm or nm in (tk, str(tk or "").split(".")[0]):
            out.append({"ticker": tk, "native": nm, "branch": "no_name",
                        "label": "원천 이름 없음(인자 모드) — 화면 입력이 아니다"})
            continue
        kr = ((names or {}).get(tk) or (titles or {}).get(en)
              or (titles or {}).get(nm))
        if kr:
            branch = ("by_ticker" if (names or {}).get(tk)
                      else "by_longname" if (titles or {}).get(en) else "by_title")
            # 어느 캐시가 풀었는지 적는다 — 값이 이상할 때 **어디를 고칠지**가
            # 그걸로 정해진다(VM 실측 2026-09-17: 캐시 값에 되읊기 접두가
            # 구워져 있었다). '캐시가 풂' 만 적으면 그다음을 사람이 짐작한다(#82).
            gate = {"by_ticker": "티커 캐시", "by_longname": "longName→제목 캐시",
                    "by_title": "제목 캐시"}[branch]
            label = f"{gate}가 풂 → {kr}"
        elif not has_han(nm):
            branch, label = (("korean", "") if _has_hangul(nm)
                             else ("latin", "원천 이름이 한자가 아님(영문/숫자)"))
        elif en and not has_han(en):
            branch = "en_fallback"
            label = f"번역은 없지만 longName 이 영문이라 화면은 그걸 쓴다 → {en}"
        else:
            # 관문이 둘이라 **둘 다** 본다(리뷰 B1) — 하나라도 다시 물으면
            # 화면은 바뀔 수 있다(#82 처방이 다르다: 어느 프롬프트를 고칠 것인가).
            recs = [r for r in ((miss or {}).get(nm), (miss or {}).get(tk))
                    if isinstance(r, dict)]
            if not recs:
                branch, label = "never_asked", "아직 한 번도 안 물음 — 다음 빌드가 채운다"
            elif any(r.get("retry") for r in recs):
                branch = "will_retry"
                gates = "·".join(sorted({r.get("gate") or "?" for r in recs
                                         if r.get("retry")}))
                label = f"프롬프트가 바뀜({gates}) — 다음 빌드가 다시 묻는다"
            else:
                branch = "rejected"
                g = "·".join(sorted({r.get("gate") or "?" for r in recs}))
                why = next((r.get("why") for r in recs if r.get("why")), "사유 미기록")
                # 지문을 같이 적는다 — "어느 프롬프트 판에서 거부됐나" 를
                # 답해야 고친 뒤 다시 볼 때 갈린다(#364, 리뷰 L3: 안 찍으면
                # 죽은 출력이다).
                ver = next((r.get("ver") for r in recs if r.get("ver")), "")
                label = (f"물었는데 거부됨({g} 관문) — 같은 프롬프트로는 다시 안 묻는다"
                         f" · {why}" + (f" · 지문 {ver}" if ver else ""))
        out.append({"ticker": tk, "native": nm, "branch": branch, "label": label})
    return out


def _has_hangul(s: str) -> bool:
    return any("\uac00" <= c <= "\ud7a3" for c in s or "")


def suspicious_values(rows: list) -> list:
    """캐시가 풀어 준 값 중 **이름 같지 않은** 것 — [(티커, 값)](순수).

    셋을 본다: (a) 티커가 값 안에 박혀 있다(되읊기) (b) 숫자·구두점으로 시작
    한다(번호 접두) (c) 원문 그대로다. 정화는 읽는 경계가 하지만, **새 모양의
    되읊기**는 여기서 먼저 보인다(#381 "한 모양만 막으면 다른 모양으로 온다").
    """
    out = []
    for r in rows:
        if not str(r.get("branch") or "").startswith("by_"):
            continue
        v = str(r.get("label") or "").split("→", 1)[-1].strip()
        tk, nat = str(r.get("ticker") or ""), str(r.get("native") or "")
        code = tk.split(".")[0]
        if (code and code in v) or re.match(r"^\W*\d", v) or (nat and v == nat):
            out.append((tk, v))
    return out


def name_verdict(rows: list) -> list:
    """⑥ 판정 — 대조 0건은 ✅ 가 아니다(#54). 고칠 수 있는 것만 ❌ 로(#260)."""
    if not rows:
        return ["❓ 대조 0행 — 판정 불가"]
    c: dict = {}
    for r in rows:
        c[r["branch"]] = c.get(r["branch"], 0) + 1
    kor = (c.get("korean", 0) + c.get("by_title", 0) + c.get("by_ticker", 0)
           + c.get("by_longname", 0))
    han = c.get("rejected", 0) + c.get("will_retry", 0) + c.get("never_asked", 0)
    latin = c.get("latin", 0) + c.get("en_fallback", 0)
    lines = [f"총 {len(rows)}종목 · 한글 {kor} · 영문 {latin} · 한자 잔존 {han}"
             # 소계 합이 총계와 같아야 한다(#45) — 이름을 못 받은 행은 따로 센다.
             + (f" · 이름 미확인 {c['no_name']}" if c.get("no_name") else "")]
    if c.get("rejected"):
        lines.append(f"❌ {c['rejected']}종목은 기다려도 안 바뀐다 — 물었는데 거부됐고 "
                     "같은 프롬프트로는 다시 묻지 않는다. 위 줄의 관문 프롬프트를 "
                     "고치면 지문이 바뀌어 자동 재시도된다.")
    if c.get("will_retry") or c.get("never_asked"):
        lines.append(f"⚠️ {c.get('will_retry', 0) + c.get('never_asked', 0)}종목은 다음 "
                     "빌드가 (다시) 묻는다 — 기다리면 된다(한 번에 최대 "
                     f"{_MAX_BATCH}종목씩).")
    if latin:
        lines.append(f"ℹ️ 영문 {latin}종목 — 통용 한글명이 없으면 한자보다 영문이 "
                     "낫다는 규약대로다(설계대로, 사용자 2026-09-17).")
    if c.get("no_name"):
        lines.append(f"❓ {c['no_name']}종목은 원천 이름을 못 받아 판정 불가 — "
                     "인자 없이 돌리면 화면과 같은 입력을 잰다(#35·#54).")
    # ⚠️ '한자가 없나' 만 재면 **값이 값다운가** 는 안 잰 것이다 — 2026-09-17
    # 실측에서 캐시가 `1709.TW | 호팍스`·`9. 레트로닉스` 인데 ⑥ 은 ✅ 를 찍었다.
    # 한 모양을 막아도 다른 모양으로 오므로(#381) 규율이 아니라 가드로 둔다(#119).
    odd = suspicious_values(rows)
    if odd:
        lines.append(f"⚠️ 값이 수상한 {len(odd)}종목 — 되읊기·번호 접두가 남았을 수 "
                     "있다(정화는 읽는 경계가 하지만, 새 모양이면 여기서 먼저 보인다): "
                     + " · ".join(f"{t} {v}" for t, v in odd[:5])
                     + (f" (외 {len(odd) - 5}종목)" if len(odd) > 5 else ""))
    if not han and not c.get("no_name"):
        lines.append("✅ 한자로 남은 종목 없음")
    return lines


def _closes_by_code(tw) -> dict[str, float]:
    """인자 모드용 종가 — 화면 경로(fetch_tw_movers)가 싣는 그 값(#145)."""
    try:
        rows, _ = tw._tw_all_common()
        return {str(r.get("code")): r.get("close") for r in rows
                if r.get("code") and r.get("close")}
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   ⚠️ 종가 조회 실패 {type(exc).__name__}: {exc} — 종가 없이 진행"
           "(FinMind 시총 폴백은 종가가 없으면 안 돈다)")
        return {}


def main() -> int:
    argv = list(sys.argv[1:])
    slow = "--slow" in argv
    codes = [a.split(".")[0] for a in argv if not a.startswith("--")]

    from bot import twse_client as tw
    from bot import finviz_client as fv

    _p(f"🇹🇼 tw_enrich_probe v{_PROBE_VER} · 업종·시총이 왜 비는가 · "
       + ("--slow(yfinance·FinMind 호출 + 시총 캐시 기록)" if slow
          else "렌더와 같은 경로(업종맵 갱신 가능) · LLM 0 · 시총 캐시 기록 없음"))
    _p(f"① 인터프리터: {sys.executable}")

    items: list[dict] = []
    if codes:
        px = _closes_by_code(tw)
        items = [{"ticker": f"{c}.TW", "name": c, "price": px.get(c)} for c in codes]
        nopx = [c for c in codes if px.get(c) is None]
        _p(f"   대상: 인자 {len(codes)}종목"
           + (f" · 종가 미확인 {len(nopx)}개({', '.join(nopx[:5])})" if nopx else " · 종가 붙임"))
    else:
        try:
            mv = tw.fetch_tw_movers()
            for it in (mv.get("up") or []) + (mv.get("down") or []):
                items.append({"ticker": f"{it.get('code','')}.TW",
                              "name": it.get("name") or it.get("code"),
                              "price": it.get("close")})
            codes = [it["ticker"].split(".")[0] for it in items]
            _p(f"   대상: 화면과 같은 입력 — 오늘 무버 {len(items)}종목"
               f"{(' · ' + mv.get('date', '')) if mv.get('date') else ''}")
        except Exception as exc:                               # noqa: BLE001
            _p(f"   ❌ 무버 목록 실패 {type(exc).__name__}: {exc}")
    tickers = [it["ticker"] for it in items]
    n = len(tickers)

    _p("")
    _p("② 캐시·회로 상태 (③ 이 갱신하기 전의 값)")
    mcap_age = fv.cache_age_sec("enrich_mcap_TW.json")
    expired = mcap_age is not None and mcap_age >= MCAP_PERSIST_TTL
    _p(f"   시총 캐시  enrich_mcap_TW.json  {_age_label(mcap_age)}"
       f" · TTL {_MCAP_TTL_H:.0f}시간{'  ❌ 만료 — 통째로 버려진다' if expired else ''}")
    persist = fv._cached("enrich_mcap_TW.json", ttl=MCAP_PERSIST_TTL) or {}
    have_mc = sum(1 for t in tickers if persist.get(t) is not None)
    _p(f"              항목 {len(persist)}종목 · 이 표의 {have_mc}/{n}")
    try:
        fp = tw._CACHE_DIR / "tw_industry_map.json"
        ind_age = (time.time() - fp.stat().st_mtime) if fp.exists() else None
    except OSError:
        ind_age = None
    ind_cached = tw._cached_stale("tw_industry_map", max_age_sec=_TW_IND_CACHE_TTL) or {}
    map_n = len(ind_cached)
    in_map = sum(1 for c in codes if ind_cached.get(c))
    _p(f"   업종 캐시  tw_industry_map.json  {_age_label(ind_age)}"
       f" · TTL {_IND_TTL_H:.0f}시간 · 항목 {map_n}종목 · 이 표의 {in_map}/{n}")
    yf_pause = fv.yf_paused()
    _p(f"   yfinance   정지마커 {'🚫 켜짐' if yf_pause else '꺼짐'}"
       " · fast_info 쿨다운은 봇 프로세스 안에서만 보여 여기서 판정 불가")

    _p("")
    _p("③ 렌더-세이프(cache-only) = 화면이 지금 받는 값")
    mcap_filled = ind_filled = 0
    render_ok = False
    if n:
        try:
            from bot.highlow_render import _enrich_compute
            meta = _enrich_compute(tickers, items, "TW", True, False, False)
            render_ok = True
            for it in items:
                m = meta.get(it["ticker"], {}) or {}
                mcap_filled += 1 if m.get("mcap") is not None else 0
                ind_filled += 1 if m.get("ind") else 0
            _p(f"   시총 {mcap_filled}/{n} · 업종 {ind_filled}/{n}")
            for it in items[:10]:
                m = meta.get(it["ticker"], {}) or {}
                mc = m.get("mcap")
                _p(f"   {it['ticker']:10} 시총 {(mc if mc is not None else '— (빈칸)'):>12}"
                   f"   업종 {m.get('ind') or '— (빈칸)'}")
            if n > 10:
                _p(f"   … 앞 10종목만 표시(전체 {n}종목은 위 집계)")
        except Exception as exc:                               # noqa: BLE001
            _p(f"   ❌ 실패 {type(exc).__name__}: {exc}")

    _p("")
    _p("④ 소스 갈라 세기 — 지금 직접 받는다")
    per_src: dict[str, dict] = {}
    for url, label in ((tw._OPENAPI_LISTED_INFO, "上市(TWSE)"),
                       (tw._OPENAPI_OTC_INFO, "上櫃(TPEx)")):
        try:
            m = tw._fetch_one_industry_source(url, label)
        except Exception as exc:                               # noqa: BLE001
            _p(f"   {label:12} 예외 {type(exc).__name__}: {exc}")
            m = {}
        per_src[label] = m
        _p(f"   {label:12} {len(m):>5}종목"
           f"{'  ❌ 비었다 — 이 대역이 통째로 빈다' if not m else ''}")
    where = {c: next((lb for lb, m in per_src.items() if m.get(c)), None) for c in codes}
    src_in = sum(1 for c in codes if where[c])
    _p(f"   이 표의 {src_in}/{n} 이 원천에 있음"
       + (f" · 上市 {sum(1 for v in where.values() if v and v.startswith('上市'))}"
          f" · 上櫃 {sum(1 for v in where.values() if v and v.startswith('上櫃'))}" if n else ""))
    missing = [c for c in codes if not where[c]]
    if missing:
        _p(f"   어디에도 없는 코드 {len(missing)}개: {' '.join(missing[:20])}"
           f"{' …' if len(missing) > 20 else ''}")
    if not per_src.get("上櫃(TPEx)"):
        _p("   ↪ 上櫃 원문 2행 — 어느 필드가 코드/업종인가(추측 금지, #155)")
        try:
            import requests
            r = requests.get(tw._OPENAPI_OTC_INFO, headers=tw._HDRS, timeout=15)
            rows = r.json() if r.status_code == 200 else None
            if not isinstance(rows, list) or not rows:
                _p(f"      HTTP {r.status_code} · 리스트 아님 — 응답 앞 200자:")
                _p(f"      {str(r.text)[:200]}")
            else:
                _p(f"      {len(rows)}행 · 키 {list(rows[0])}")
                for row in rows[:2]:
                    _p("      " + " · ".join(f"{k}={row[k]!r}" for k in list(row)[:8]))
        except Exception as exc:                               # noqa: BLE001
            _p(f"      실패 {type(exc).__name__}: {exc}")

    slow_res: tuple[int, int] | None = None
    if slow and n:
        _p("")
        _p("④-b 백그라운드와 같은 경로(--slow) — yfinance·FinMind, 시총 캐시 기록")
        try:
            from bot.highlow_render import _enrich_compute
            meta2 = _enrich_compute(tickers, items, "TW", True, False, True)
            s_mc = sum(1 for t in tickers
                       if (meta2.get(t, {}) or {}).get("mcap") is not None)
            s_ind = sum(1 for t in tickers if (meta2.get(t, {}) or {}).get("ind"))
            slow_res = (s_mc, s_ind)
            _p(f"   시총 {s_mc}/{n} · 업종 {s_ind}/{n}")
        except Exception as exc:                               # noqa: BLE001
            _p(f"   ❌ 실패 {type(exc).__name__}: {exc}")

    _p("")
    _p("⑤ 판정")
    for line in enrich_verdict(n=n, render_ok=render_ok, mcap_filled=mcap_filled,
                               ind_filled=ind_filled, mcap_age_sec=mcap_age,
                               map_n=map_n, in_map=in_map, src_in=src_in,
                               yf_pause=yf_pause, slow=slow_res):
        _p(f"   {line}")
    if not slow and render_ok and (mcap_filled < n or ind_filled < n):
        _p("   ↪ 캐시 콜드인지 원천 부재인지 가르려면(시총 캐시를 채우는 쓰기):")
        _p("      cd ~/stock && .venv/bin/python -m bot.scripts.tw_enrich_probe --slow")

    _p("")
    _p("⑥ 한글명 — 왜 아직 한자인가(종목별 · cache-only · LLM 0)")
    if not items:
        _p("   ❓ 대상이 없어 판정 불가(대조 0행)")
        return 0
    try:
        from bot.chart_translate import (miss_diag, translate_names_kr,
                                         translate_titles_kr)
        tks = [it["ticker"] for it in items]
        natives = [it["name"] for it in items if it.get("name")]
        # 화면은 longName 을 **먼저** 번역한다 — 안 보면 그걸로 풀린 행을 '한자
        # 잔존' 으로 과대보고한다(리뷰 H1·#35). `allow_slow=False` = 영구 캐시만
        # (yfinance .info 호출 0 · 쓰기 0).
        en = fv._fetch_display_names(tks, allow_slow=False) or {}
        ktl = translate_titles_kr(natives + [v for v in en.values() if v],
                                  cache_only=True) or {}
        knm = translate_names_kr([(it["ticker"], it.get("name")) for it in items],
                                 cache_only=True) or {}
        miss = miss_diag(titles=natives, tickers=tks)   # 관문 둘 다(리뷰 B1)
        rows = name_rows_diag(items, titles=ktl, names=knm, miss=miss,
                              longnames=en)
    except Exception as exc:                                   # noqa: BLE001
        _p(f"   ❌ 캐시 조회 실패 {type(exc).__name__}: {exc} — 판정 불가")
        return 0
    for r in rows:
        if r["branch"] == "korean":
            continue
        _p(f"   · {r['ticker']} {r['native']} → {r['label']}")
    for line in name_verdict(rows):
        _p(f"   {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
