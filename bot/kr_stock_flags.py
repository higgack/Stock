"""KR 종목 상태 플래그(정리매매·거래정지·관리종목) — KIS 마스터 단일 출처.

왜 이게 있나(사용자 2026-09-19 "코스나인이나, 원풍물산 코다코같은건 맞는거야?
이거 정리매매 종목이야? 30% 가 상하한룰로 알고 있는데"): 랭킹 보드가
`1원 · -50.00%` 만 적고 **왜 ±30% 밖인지**는 한 마디도 안 했다. 값은 KRX 가
준 그대로라 산수도 맞고(#33) 어떤 감사도 안 걸렸는데(#96), 화면이 답하지
못해 사용자가 물어야 알 수 있었다 — 그게 결함이다(#43).
정리매매는 가격제한폭이 **적용되지 않는** 문서화된 예외다.

원천: KIS 마스터(`bollinger_board._kis_master_rows`). 이 레포가 이미 볼린저
유니버스용으로 받는 공개 zip 이고 인증이 필요 없다 — 새 원천을 찾은 게 아니라
**이미 받는 파일이 무엇을 더 주는지** 먼저 물은 것이다(#150·§작업 원칙 선행
사례 먼저). `_KIS_RISK_COLS` 가 책(KOSPI/KOSDAQ)마다 다른 컬럼명을 들고,
이 모듈이 그 **원문을 해석**한다(#38 파싱과 해석의 단일 출처).

시장 게이트가 아니라 **데이터소스 경계**다 — KIS 마스터는 한국 증권사 파일이라
KR 밖의 종목이 아예 없다(§UNIVERSAL 문서화된 데이터소스 사유). 다른 시장에
같은 플래그를 주는 원천이 생기면 `highlow_render._RISK_SOURCES` 에 한 줄을
더하는 것으로 끝나고 렌더러는 안 바뀐다.

⚠️ **렌더 경로는 캐시만 본다**(`cache_only=True`). 콜드면 zip 2개를 각 30초
상한으로 받는데 그걸 렌더가 기다리면 그게 곧 화면 블록이다(#116·#312).
미스는 백그라운드가 데우고 다음 렌더에 반영된다(형제 `highlow_render.
_kick_name_fill` 과 같은 규약, #38).

⚠️ **값의 인코딩은 재지 않았다.** 'Y/N' 인지 '0/1' 인지 샌드박스에서 KIS
마스터에 도달할 수 없어 확인하지 못했다(실측: `new.real.download.dws.co.kr`
도달 불가). 그래서 둘 다 받아들이고 판정을 **3-상태**(참/거짓/모름)로 둔다 —
모르는 원문은 버리지 않고 **세어서 표본과 함께** `--why` 가 말한다(#54 대조
0건은 통과가 아니다 · #82 갈래는 이름으로 · #109 원문 표본을 같이 찍을 것 ·
#165 재지 않은 것을 단정하지 말 것). 첫 실측이 이 가정을 스스로 반증한다.
"""
from __future__ import annotations

import logging

log = logging.getLogger("bot.kr_stock_flags")

_CACHE = "kr_stock_flags_v1.json"
_TTL = 12 * 3600          # 상장 종목의 상태는 하루 단위로 바뀐다(#61)
_SCHEMA = 2               # v2 — 데이터 나이·실패 백오프를 봉투에 싣는다

# 원천이 죽었을 때의 재시도 간격. "실패는 짧게만 믿는다"(#303)와 "재시도도
# 유계여야 한다"(#116)를 **같이** 지키는 것이 지수 백오프다(#384 그대로) —
# 고정 15분으로 두면 `/highlow` 가 30초마다 폴링하는 12시간 동안 KIS zip 을
# 끊임없이 받는다(독립 리뷰 2026-09-19 실측: 5회 렌더 → 10회 다운로드).
_BACKOFF = (15 * 60, 30 * 60, 3600, 2 * 3600, 4 * 3600, 6 * 3600)

_RUN_HINT = "cd ~/stock && .venv/bin/python -m bot.kr_stock_flags --why"

#: 이 모듈이 아는 상태 키 — `bollinger_board._KIS_RISK_COLS` 의 정규 키와 같다.
RISK_KEYS = ("정리매매", "거래정지", "관리종목")

#: 화면에 뱃지로 나가는 키. 오늘은 정리매매 하나다 — **가격제한폭이 적용되지
#: 않는 것은 정리매매뿐**이라, 사용자가 물은 "왜 ±30% 밖인가"에 답하는 키가
#: 정확히 이것이다. 나머지 둘은 같은 파싱에서 공짜로 오므로 맵에는 싣되
#: 그리지 않는다(#25·#260 늘 뜨는 뱃지는 아무것도 안 재는 것과 같다).
BADGE_KEYS = ("정리매매",)

_TRUE = ("Y", "y", "1")
_FALSE = ("N", "n", "0", "")


def parse_flag(raw) -> bool | None:
    """마스터 원문 한 칸 → True/False/**None(모름)**.

    인코딩을 재지 않았으므로(모듈 독스트링) Y/1 과 N/0 을 둘 다 받고, 그 밖은
    **모름**이다. 모름을 False 로 접으면 '정상'과 구별되지 않아 뱃지가 영영
    안 붙어도 아무도 모른다(#54·#82).
    """
    if raw is None:
        return None
    v = str(raw).strip()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def _collect() -> tuple[dict, int, dict, list, dict]:
    """KIS 마스터를 실제로 받아 (맵, 총행수, 모름계수, 메모, **이름**) 반환.

    맵은 **False 가 아닌 종목만** 담는다(True 또는 모름) — 2,800행을 통째로
    구우면 캐시가 쓸데없이 커진다. 그래서 '맵에 없음' 은 총행수가 0 보다 클
    때만 '정상'이고, 0 이면 **판정 불가**다(#45 두 모집단을 한 수로 세지 말 것).

    ⚠️ 이름은 **플래그가 붙은 종목만** · **진단 전용**이다. `_kis_master_rows`
    가 이름을 이미 주는데 첫 판이 그걸 버려서 `--why ③` 이 `008290` 같은
    코드만 찍었고, 사용자가 "이게 원풍물산 맞나"를 따로 찾아봐야 했다
    (VM 실측 2026-09-19 · #123·#129·#189·#228·#292 계열 — 계산해 둔 것을
    표시까지 배선 안 하면 없는 것과 같다 · #356 판정 줄은 자족해야 한다).
    화면은 보드 행에서 이름을 이미 받으므로 캐시에는 **안 굽는다**(봉투
    스키마 불변 — #18 캐시 무효화를 유발하지 않는다).
    """
    from bot.bollinger_board import _kis_master_rows
    out: dict = {}
    total = 0
    unknown: dict = {}
    notes: list = []
    names: dict = {}
    books = 0
    for book in ("kospi", "kosdaq"):
        try:
            rows, note = _kis_master_rows(book)
        except Exception as exc:                               # noqa: BLE001
            notes.append(f"{book} 실패({type(exc).__name__}: {exc})")
            continue
        if not rows:
            notes.append(f"{book} 0행({note})")
            continue
        books += 1
        # ⚠️ 원천 note 를 **버리지 말 것** — 거기에 '상태 컬럼 미발견' 이
        # 실린다(독립 리뷰 2026-09-19 실측: 성공 경로에서 통째로 사라져
        # `--why` 도 못 봤다). 그러면 KIS 가 컬럼명을 바꾼 날 빈 맵이 12시간
        # 구워지고 화면은 '오늘 해당 종목 없음' 이라고 **거짓**을 말한다(#43·#54).
        notes.append(f"{book} {note}" if note else f"{book} {len(rows):,}행")
        for r in rows:
            code = str(r.get("code") or "").zfill(6)
            if len(code) != 6:
                continue
            total += 1
            raw = r.get("risk_raw") or {}
            hit: dict = {}
            for k in RISK_KEYS:
                if k not in raw:
                    continue
                v = parse_flag(raw.get(k))
                if v is None:
                    # 모르는 원문은 **표본과 함께** 남긴다 — 숫자만 세면 어느
                    # 인코딩으로 온 건지 다음 라운드가 또 추측한다(#109).
                    slot = unknown.setdefault(k, {"n": 0, "samples": []})
                    slot["n"] += 1
                    s = str(raw.get(k))
                    if s not in slot["samples"] and len(slot["samples"]) < 5:
                        slot["samples"].append(s)
                    hit[k] = None
                elif v:
                    hit[k] = True
            if hit:
                out[code] = hit
                # ⚠️ `_kis_master_rows` 는 이름이 비면 **코드**를 넣는다
                # (`head[21:].strip() or code`). 그걸 이름이라 싣으면
                # `008290  008290` 이 찍혀 **코드가 회사명인 척**한다
                # (독립 리뷰 2026-09-19 실측 · #34·#165). 코드와 같으면
                # 이름을 못 받은 것이므로 **안 싣는다** — 그래야 아래
                # `(이름 미확보)` 갈래가 실제로 발화한다(#291).
                nm = str(r.get("name") or "").strip()
                if nm and nm != code:
                    names[code] = nm
    if books < 2:
        # ⚠️ 반쪽 맵은 굽지 않는다 — 코스닥 zip 만 실패한 맵을 완전본으로
        # 구우면 코스닥 정리매매 종목이 12시간 동안 뱃지 없이 뜬다(#280·#384).
        notes.append("책 2권을 다 못 받아 **부분**입니다(캐시에 굽지 않습니다)")
    return out, total, unknown, notes, names


def _read_envelope() -> dict:
    """디스크 봉투 — **TTL 과 무관하게** 읽는다.

    ⚠️ 데이터 나이는 파일 mtime 이 아니라 봉투의 `fetched` 다 — 실패한
    재시도만으로도 파일은 새로 쓰이기 때문이다(#384·#304).
    ⚠️ 어떤 바이트가 와도 안 던진다(#331 한 바이트가 보드 셋을 비웠다).
    """
    from bot.finviz_client import _cached
    try:
        c = _cached(_CACHE, ttl=10 * 365 * 24 * 3600)
    except Exception:                                          # noqa: BLE001
        return {}
    return c if (isinstance(c, dict) and c.get("v") == _SCHEMA) else {}


def _write_envelope(env: dict) -> None:
    from bot.finviz_client import _cache_write
    _cache_write(_CACHE, env)


def snapshot(*, cache_only: bool = True, write: bool = True,
             force: bool = False) -> dict:
    """한 번의 디스크 읽기로 화면·진단이 필요한 것을 **전부** 돌려준다.

    반환: `{flags, note, state, n, names, fetched, fails, next_try}`.
    `names` 는 **진단 전용**이고 네 경로 모두 키를 싣는다(캐시 경로는 `{}`
    — 캐시에 굽지 않기 때문이다). 키 유무로 갈리면 호출부가 KeyError 를
    내므로 **항상 있다**(#54 없는 것과 모르는 것은 다른 말이다).
    `state` 는 넷이고 **처방이 다르다**(#82):
      * `ok`      — 쓸 수 있는 맵(수집 뒤 `_TTL` 안)
      * `stale`   — 맵은 있는데 낡음(그래도 쓴다 — 상장 상태는 하루 단위)
      * `cold`    — 한 번도 못 받음(백그라운드가 데우는 중)
      * `backoff` — 받지 못하고 있음(재시도 대기 중, `next_try`)
    '맵이 비었다' 와 '원천이 죽었다' 를 같은 빈 dict 로 접으면 화면이
    "오늘 해당 종목 없음" 이라고 **거짓**을 말한다(독립 리뷰 2026-09-19 · #45·#54).

    `cache_only=True` = **렌더 경로** — 디스크만 보고 원천은 안 친다
    (콜드면 zip 2개 × 30초 상한이 곧 화면 블록, #116·#312).
    `force=True` = 캐시를 건너뛰고 **원천을 친다**(진단 ② 가 ① 과 같은 것을
    읽어 대조군이 성립하지 않던 것, 독립 리뷰 2026-09-19 실측).
    `write=False` = 진단 — 운영 캐시를 채우지 않는다(#30·#264·#283).
    """
    import time as _t
    env = {} if force else _read_envelope()
    now = _t.time()
    flags = {str(k): {str(kk): (True if vv is True else None)
                      for kk, vv in v.items()}
             for k, v in (env.get("flags") or {}).items()
             if isinstance(v, dict)}
    n = int(env.get("n") or 0)
    fetched = float(env.get("fetched") or 0)
    fails = int(env.get("fails") or 0)
    next_try = float(env.get("next_try") or 0)
    note = str(env.get("note") or "")
    if n > 0:
        state = "ok" if (now - fetched) < _TTL else "stale"
        if cache_only or state == "ok":
            # ⚠️ `names` 는 **신선 수집 때만** 있다(캐시엔 안 굽는다) —
            # 빈 dict 로 명시해 호출부가 키 유무로 갈리지 않게 한다(#54).
            return {"flags": flags, "note": note, "state": state, "n": n,
                    "names": {}, "fetched": fetched, "fails": fails,
                    "next_try": next_try}
    elif cache_only:
        state = "backoff" if now < next_try else "cold"
        if not note:
            note = ("재시도 대기 중입니다" if state == "backoff"
                    else "아직 안 받았습니다(백그라운드가 데웁니다)")
        return {"flags": {}, "note": note, "state": state, "n": 0,
                "names": {}, "fetched": fetched, "fails": fails,
                "next_try": next_try}

    out, total, unknown, notes, names = _collect()
    partial = any("부분" in x for x in notes)
    if total <= 0 or partial:
        # ⚠️ 실패도 **도장을 남긴다** — 안 남기면 다음 렌더가 곧바로 또
        # 받는다(`/highlow` 는 30초 폴링이다, 독립 리뷰 실측 5렌더=10다운로드).
        # 간격은 지수 백오프 — "실패는 짧게만"(#303)과 "재시도도 유계"(#116)를
        # 같이 지키는 유일한 방법이다(#384).
        fails += 1
        why = (" · ".join(notes)) or "원천 도달 실패"
        note = ("KIS 마스터가 0행 — " if total <= 0 else "부분 수집 — ") + why
        if write:
            wait = _BACKOFF[min(fails, len(_BACKOFF)) - 1]
            _write_envelope(dict(env, v=_SCHEMA, note=note, fails=fails,
                                 next_try=now + wait))
        # 부분이어도 **값은 준다**(#148 우리가 버린 건 아닌가) — 굽지만 않는다.
        return {"flags": out if partial else {}, "note": note,
                "state": "backoff", "n": 0, "fetched": fetched,
                "names": names if partial else {},
                "fails": fails, "next_try": now + _BACKOFF[
                    min(fails, len(_BACKOFF)) - 1]}

    note = f"{total:,}종목 · 플래그 있는 종목 {len(out):,}"
    if unknown:
        note += " · 모름 " + ", ".join(
            f"{k} {v['n']:,}({'/'.join(v['samples'])})"
            for k, v in sorted(unknown.items()))
    if notes:
        note += " · " + " · ".join(notes)
    if write:
        _write_envelope({"v": _SCHEMA, "flags": out, "n": total,
                         "unknown": unknown, "note": note,
                         "fetched": now, "fails": 0, "next_try": 0})
    return {"flags": out, "note": note, "state": "ok", "n": total,
            "names": names, "fetched": now, "fails": 0, "next_try": 0}


def flags_map(*, cache_only: bool = False, write: bool = True,
              force: bool = False) -> tuple[dict, str]:
    """({code6: {키: True|None}}, 사유) — `snapshot` 의 얇은 래퍼.

    ⚠️ 이 2-튜플은 **상태를 버린다** — '맵이 비었다' 와 '원천이 죽었다' 를
    가르려면 `snapshot` 을 쓸 것(#129 래퍼를 만들 땐 버리는 정보가 화면에
    필요한지 먼저 물을 것).
    """
    s = snapshot(cache_only=cache_only, write=write, force=force)
    return s["flags"], s["note"]


def badges(code_or_ticker: str, fmap: dict) -> tuple:
    """맵에서 이 종목의 **그릴** 키를 고른다 → ('정리매매', …).

    티커(`082660.KQ`)든 코드(`082660`)든 받는다. 모름(None)은 **그리지
    않는다** — 모르는 것을 '정리매매' 라고 적으면 화면이 거짓말한다(#165).
    모름이 몇 건인지는 `--why` 가 말한다(#82).
    """
    code = str(code_or_ticker or "").split(".")[0].strip()
    hit = fmap.get(code) if isinstance(fmap, dict) else None
    if not isinstance(hit, dict):
        return ()
    return tuple(k for k in BADGE_KEYS if hit.get(k) is True)


def why() -> int:                                              # pragma: no cover
    """진단 — 캐시 상태·원천 도달·인코딩 표본을 갈래로 찍는다.

    ⚠️ 운영 캐시를 **쓰지 않는다**(`write=False`) — 진단이 자기가 읽을 신호를
    오염시키면 안 된다(#30·#264·#283). 네트워크는 탄다(그게 이 진단의 요점).
    ⚠️ ② 는 **`force=True`** 로 원천을 친다. 첫 판은 그냥 `flags_map` 을 불러
    12시간 캐시에 걸리면 **캐시를 원천이라 부르고** rc=0 을 냈다 — ① 과 ② 가
    같은 것을 읽으면 대조군이 성립하지 않는다(독립 리뷰 2026-09-19 실측 ·
    #143·#392 대조군이 시험 대상과 같으면 안 된다).
    """
    from bot.finviz_client import cache_age_sec
    try:
        from bot.audit_sweep import audit_fingerprint
        sig = audit_fingerprint(("bot.kr_stock_flags",))
    except Exception as exc:                                   # noqa: BLE001
        sig = f"지문불가({type(exc).__name__})"
    print(f"# kr_stock_flags --why · 코드 지문 {sig} · 사용법: {_RUN_HINT}")
    print(f"# 화면이 쓰는 그 경로(snapshot)를 태웁니다(#35). 뱃지 키={BADGE_KEYS}")

    import time as _t
    c = snapshot(cache_only=True)
    age = cache_age_sec(_CACHE)
    mark = {"ok": "✅", "stale": "⚠️", "cold": "❓", "backoff": "❌"}[c["state"]]
    if age is None:
        print("① 캐시(화면이 읽는 것) — ❓ 파일 없음(배포 직후이거나 아직 한 번도 안 받음)")
    else:
        data_age = (_t.time() - c["fetched"]) if c["fetched"] else None
        age_txt = (f"수집 {data_age / 3600:.1f}시간 전" if data_age
                   else "수집 기록 없음")
        print(f"① 캐시(화면이 읽는 것) — {mark} {c['state']} · {age_txt} · "
              f"파일 {age / 60:.0f}분 전 · 전체 {c['n']:,}종목 · "
              f"플래그 있는 종목 {len(c['flags']):,}")
        if c["state"] == "backoff":
            wait = max(0, c["next_try"] - _t.time())
            print(f"   ↪ 연속 실패 {c['fails']}회 · 다음 시도까지 {wait / 60:.0f}분 "
                  f"(지수 백오프 — 상한 {_BACKOFF[-1] / 3600:.0f}시간)")
        if c["note"]:
            print(f"   ↪ {c['note']}")

    # ② 는 **원천을 친다** — ① 과 다른 것을 읽어야 대조군이다(#143).
    src = snapshot(cache_only=False, write=False, force=True)
    if src["n"] <= 0:
        print(f"② 원천(지금 받아 본 것) — ❌ {src['note']}")
        print("   ↪ 갈래: 네트워크 차단 / KIS 마스터 URL 변경 / zip·인코딩 드리프트")
        return 1
    print(f"② 원천(지금 받아 본 것) — ✅ {src['note']}")
    # '플래그 있는 종목 N' 하나로는 무엇이 몇인지 모른다 — 갈래마다 뜻도
    # 처방도 다르다(#45 두 모집단을 한 수로 세지 말 것 · #82).
    by_key = {k: sum(1 for v in src["flags"].values() if v.get(k) is True)
              for k in RISK_KEYS}
    # ⚠️ 소계 합은 총계와 **다르다** — 한 종목이 여러 갈래를 갖기 때문이다
    # (정리매매 종목은 대개 관리종목이자 거래정지다). VM 실측 2026-09-19:
    # `8 + 119 + 183 = 310` vs `플래그 있는 종목 220`. 첫 판 주석이 "소계 합이
    # 총계와 같아야 한다"고 적었는데 그건 **거짓이었고 실측이 반증했다**
    # (#55 설명이 코드와 어긋나면 버그 · #286). 나란히 놓인 수가 안 맞으면
    # 사용자는 우리 버그로 읽으므로 **중복이라는 사실을 적는다**(#33·#45).
    # 그리고 모름은 어느 소계에도 안 들어가므로 그것도 따로 적는다.
    n_unk = sum(1 for v in src["flags"].values()
                if any(x is None for x in v.values()))
    line = " · ".join(f"{k} {n:,}" for k, n in by_key.items())
    if n_unk:
        line += f" · **모름 {n_unk:,}**(어느 소계에도 안 들어감 — ④ 를 볼 것)"
    print(f"   ↪ 갈래별: {line}"
          + f"  (합 {sum(by_key.values()) + n_unk:,} ≠ 총계 {len(src['flags']):,}"
            " — 한 종목이 여러 갈래를 갖는다)")
    print(f"   ↪ 뱃지로 그리는 것은 {'·'.join(BADGE_KEYS)} 뿐 — 나머지는 "
          "±30% 를 면제하지 않는다")
    if "미발견" in src["note"]:
        # 컬럼명 드리프트는 **0종목과 구별되지 않는다** — 빈 맵을 12시간 굽고
        # 화면은 '오늘 해당 종목 없음' 이라고 거짓을 말한다(독립 리뷰 F2).
        print("   ↪ ❌ 상태 컬럼을 못 찾았습니다 — `_KIS_RISK_COLS` 를 원천 "
              "컬럼명에 맞출 것(그때까지 뱃지는 영영 안 붙습니다)")
        return 1

    fresh = src["flags"]
    liq = sorted(c_ for c_, v in fresh.items() if v.get("정리매매") is True)
    unk = sorted(c_ for c_, v in fresh.items()
                 if any(x is None for x in v.values()))
    nm = src.get("names") or {}
    if liq:
        # ⚠️ **이름을 같이 찍는다** — 첫 판은 코드만 찍어 사용자가 "이게
        # 원풍물산 맞나"를 따로 찾아봐야 했다(#356 판정 줄은 자족해야 한다).
        # 이름을 못 구하면 지어내지 말고 코드만 적는다(#165).
        print(f"③ 정리매매 — ✅ {len(liq)}종목")
        for c_ in liq[:30]:
            print(f"     {c_}  {nm.get(c_) or '(이름 미확보)'}")
        if len(liq) > 30:
            print(f"     … 외 {len(liq) - 30}종목")
    elif unk:
        print(f"③ 정리매매 — ❓ 0종목인데 모름이 {len(unk)}종목 있습니다 "
              "(인코딩을 못 읽었을 수 있습니다 — ② 의 '모름' 표본을 보세요)")
    else:
        # 0종목은 정상일 수 있다(그 날 정리매매 종목이 없을 수 있다).
        # ✅ 로 찍되 **그게 무슨 뜻인지** 적는다(#43·#274). 컬럼 미발견은
        # 위에서 이미 걸러졌으므로 이 문장이 참이다.
        print("③ 정리매매 — ✅ 0종목 · 모름 0건 · 컬럼도 전부 찾았으니 "
              "'오늘 해당 종목 없음'으로 읽습니다(파싱 실패가 아닙니다)")
    if unk:
        print(f"④ 모름 — ⚠️ {len(unk)}종목 · ② 의 표본이 원문입니다. "
              "Y/N·0/1 이 아니면 `parse_flag` 를 그 값에 맞출 것")
    else:
        print("④ 모름 — ✅ 0건(전 행이 Y/N 또는 0/1 로 읽혔습니다)")
    return 0


def main(argv: list | None = None) -> int:
    """CLI 디스패치 — **함수**다. `if __name__` 블록 안에 두면 회귀가 태울 수
    없어 광고한 CLI 가 죽은 채 배포된다(#176·#252·#351)."""
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m bot.kr_stock_flags",
        description=f"KR 종목 상태 플래그 진단. 사용법: {_RUN_HINT}")
    ap.add_argument("--why", action="store_true",
                    help="캐시·원천 도달·인코딩 표본을 갈래로 진단")
    a = ap.parse_args(argv)
    if not a.why:
        ap.print_help()
        return 2
    return why()


if __name__ == "__main__":                       # pragma: no cover
    # ⚠️ 엔트리포인트는 **맨 끝**이다 — 파일 중간에 두면 그 아래 정의가
    # 영영 안 닿는다(#276).
    import sys
    sys.exit(main())
