"""거래소 **전용 체결 창**에서 모으는 '그 거래소에서 거래되는 종목' 하한 — 자동 누적.

사용자 2026-09-17 "이 NXT 랑 KRX 애프터랑 안겹치는것도 많을텐데. NXT 에
등록안된 기업들도 많기 때문에" → "이것도 해줘".

무엇을 재나
  네이버 폴링 응답에는 시간외 체결이 **어느 거래소 것인지 이름으로 가르는
  필드가 없다**(2026-09-17 VM 실측, `prepost_client.venue_attribution_note`).
  그래서 **필드로는** 못 가른다. 그런데 **창으로는** 갈린다 — KRX 는 체결
  창이 애프터마켓(16:00–20:00)뿐이라, 그 밖의 NXT 체결 창(프리 08:00–09:00 ·
  애프터 15:40–16:00)에 붙는 시간외 체결은 **정의상 NXT** 다. 그 구간에서
  시간외 체결이 관측된 종목이 'NXT 에서 거래되는 종목' 의 **하한**이다.

왜 모듈인가(왜 프로브 한 방이 아닌가)
  이 측정은 **그 창이 열려 있을 때만** 할 수 있다. 운영자가 15:40~16:00 에
  맞춰 프로브를 치게 만드는 fix 는 잘못된 fix 다(§Automation-first — 반복
  확인은 제품에 심는다, #252). 시간외 보드 스캔은 **이미 그 창에서 돌고
  있으므로**(`prepost_client._compute_kr_prepost`, 2분 주기 · ~200종목) 거기서
  관측을 주워 담으면 추가 네트워크 콜이 **0** 이다.

⚠️ 하한이지 목록이 아니다 — 그 창에 체결이 없었을 뿐인 NXT 종목이 있고,
유니버스도 '정규장 무버 200종목' 이라 전 상장이 아니다(#54·#165 잰 범위를
빼고 말하지 말 것). 그래서 화면·CLI 가 그렇게 적는다.

⚠️ 이 연역은 **원천이 밝힌 창**(네이버증권 공지 153) 위에 선다 — 원천이 창을
또 바꾸면 판정도 같이 틀린다(#165). 그래서 창을 여기 리터럴로 적지 않고
`bot.kr_session` 단일 출처에서만 읽으며, 거래소 이름도 열거하지 않는다(#24 —
거래소가 늘면 저절로 따라온다).

⚠️ **낡은 블록 가드**: 네이버 시간외 블록이 직전 세션 값을 들고 있으면, 그
창에서 읽었다는 이유만으로 귀속하면 어제 16:00~20:00(KRX 일 수 있는) 체결을
NXT 라 부르게 된다. 그래서 '지금이 전용 창인가' 만 보지 않고 **그 체결의
시각(`localTradedAt`) 자체가 같은 거래소의 전용 창 안인가**를 같이 본다.
시각을 못 읽으면 세지 않고 `undated` 로 남긴다 — 세는 쪽이 아니라 **못 센
쪽을 말하는 것**이 다음 라운드의 재료다(#54·#82). 그 판단이 옳았는지 보게
**못 센 건의 원문 표본도** 적는다(#109) — 표본이 필요한 세계는 바로 하나도
못 센 세계인데, 첫 판은 `if hit:` 안에서만 표본을 적어 그 세계에서 한 줄도
안 남겼다(독립 리뷰 2026-09-17 H2).

⚠️ **쓰는 프로세스가 둘이다** — 봇(워머 180초)과 대시보드 서버(방문 SWR)가
같은 파일을 read-modify-write 한다. `prepost_client._KR_REFRESHING` 는 프로세스
로컬이라 그걸 못 막는다(리뷰 H3 실측: lost update 재현). 그래서 이 파일은
**파일 락 + 원자적 교체**(tmp→`os.replace`)로 쓴다 — `finviz_client._cache_write`
는 `write_text`(truncate 후 쓰기)라 길이 0 인 창이 실재하고(#280), 그 창을 읽은
쪽이 '빈 상태' 로 시작하면 누적을 통째로 덮는다(리뷰 B1 실측).

읽기 전용 확인:
    cd ~/stock && .venv/bin/python -m bot.venue_universe
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime

from bot.kr_session import KST, exclusive_venue, exclusive_window_label
# ⚠️ `exclusive_venue` 는 **값 바인딩**이다 — 테스트가 창 판정을 갈아끼우려면
# `bot.kr_session` 이 아니라 **이 모듈의 이름**을 패치해야 한다(리뷰 L5).

log = logging.getLogger("bot.venue_universe")

_STORE = "venue_universe_v1.json"
_MAX_TICKERS = 4000      # 파일 크기 상한 — 넘치면 **버린 수를 적는다**(#45)
_MAX_DATES = 90          # 관측일 목록 상한(같은 이유)


def parse_ts(ts) -> datetime | None:
    """원천의 체결시각 문자열 → KST datetime. 못 읽으면 None(추측 금지).

    ⚠️ 형식을 **재지 않았다** — KR 폴링 응답의 `localTradedAt` 이 ISO 인지
    압축형인지, 오프셋을 달고 오는지 확인한 적이 없다(#165). 형제 파서
    `chart_data._quote_date` 가 같은 원천 필드를 "ISO '..+09:00' 또는
    'YYYYMMDD'" 로 적어 두었지만 그것도 독스트링이지 실측 기록이 아니다.
    그래서 관용 파서를 두되 **naive 면 KST 로 본다**(국내 엔드포인트라는
    가정이고, 그게 틀리면 이 측정이 통째로 틀린다 — 그래서 못 센 건의 원문
    표본을 남겨 첫 실측이 이 가정을 스스로 반증하게 한다).
    """
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        v = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        digits = "".join(c for c in s if c.isdigit())
        if len(digits) not in (12, 14):
            return None
        try:
            v = datetime.strptime(digits[:14].ljust(14, "0"), "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return v.astimezone(KST) if v.tzinfo is not None else v.replace(tzinfo=KST)


def classify(over_ts, venue: str) -> str:
    """이 한 건을 하한에 셀 수 있나 — 'counted' · 'undated' · 'outside'(순수).

    `venue` 는 **지금** 전용 창인 거래소다. 체결 시각이 **같은 거래소의** 전용
    창 안일 때만 센다 — 낡은 블록 가드(위 모듈 주석). 거래소까지 보는 이유:
    거래소가 늘어 전용 창이 둘이 되면, 어제 저쪽 전용 창 체결을 들고 있는
    낡은 블록이 이쪽으로 귀속된다(리뷰 H1 — 오늘 표로는 발화하지 않아 합성
    거래소로 회귀를 걸었다).
    """
    t = parse_ts(over_ts)
    if t is None:
        return "undated"
    return "counted" if exclusive_venue(t) == (venue, "exclusive") else "outside"


def _store_path():
    """저장소 경로 — **호출 시점**에 해석한다(테스트가 캐시 디렉터리를 갈아끼운다)."""
    from bot.finviz_client import _CACHE_DIR
    return _CACHE_DIR / _STORE


def read_store() -> tuple:
    """(store, 갈래) — 'ok' · 'empty'(파일 없음) · 'unreadable: 사유'.

    ⚠️ TTL 로 읽지 않는다 — `_cached` 는 만료와 파손을 **같은 None** 으로
    돌려주므로, TTL 이 짧아지는 변경 하나로 정상 저장소가 '파손' 으로 오보되고
    누적이 영구 중단된다(리뷰 M4). 갈래는 이름으로 부른다(#82).
    """
    p = _store_path()
    try:
        if not p.exists():
            return {"v": 1, "venues": {}}, "empty"
        raw = p.read_bytes()
    except OSError as exc:
        return {}, f"unreadable: OSError {exc}"
    if not raw.strip():
        # ⚠️ 길이 0 은 '빈 상태' 가 아니라 **쓰다 만 것**이다(#280 truncate 창 ·
        # 크래시 · 디스크풀). 빈 상태로 보면 다음 쓰기가 누적을 통째로 덮는다.
        return {}, "unreadable: 빈 파일(쓰다 만 것일 수 있음)"
    try:
        d = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:                                  # noqa: BLE001
        return {}, f"unreadable: {type(exc).__name__}"
    if not isinstance(d, dict):
        return {}, "unreadable: dict 가 아님"
    return d, "ok"


def _write_store(store: dict) -> None:
    """원자적 교체 — reader 가 **찢어진 JSON 이나 길이 0** 을 보지 않게(#280)."""
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


@contextmanager
def _locked():
    """프로세스 간 배타 락 — 봇·대시보드 두 writer 의 lost update 차단(리뷰 H3).

    락을 못 걸면(플랫폼·권한) **그냥 진행한다** — 잠금 실패로 측정을 멈추는
    것보다 드물게 한 스캔을 잃는 편이 낫다(다음 스캔이 다시 줍는다). 대신
    그 사실을 로그로 남긴다(#42a 폴백은 조용하면 안 된다).
    """
    p = _store_path()
    f = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        import fcntl
        f = open(p.with_name(p.name + ".lock"), "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    except Exception as exc:                                  # noqa: BLE001
        log.info("venue_universe: 파일 락 없이 진행합니다: %s", exc)
        try:
            yield
        finally:
            if f is not None:
                f.close()
        return
    try:
        yield
    finally:
        try:
            import fcntl as _fc
            _fc.flock(f.fileno(), _fc.LOCK_UN)
        finally:
            f.close()


def observe(items, now: datetime | None = None) -> dict:
    """스캔이 본 (티커, 체결시각) 들을 하한에 누적한다. **예외를 올리지 않는다.**

    반환(어느 갈래든 **사실을 말한다**, #82): branch/venue · seen(넘겨받은 수) ·
    counted · new · undated · outside · total(누적 종목수 — 셀 수 있었을 때만).
    """
    venue, branch = exclusive_venue(now)
    res = {"venue": venue, "branch": branch, "counted": 0, "new": 0,
           "undated": 0, "outside": 0, "seen": 0}
    # ⚠️ `total`(누적 종목수)은 **셀 수 있었을 때만** 싣는다 — 안 센 실행에
    # 0 을 적으면 "아무것도 안 쌓였다"로 읽힌다(#54·#34).
    try:
        pairs = [(str(tk), ts) for tk, ts in (items or []) if tk]
        res["seen"] = len(pairs)
        if branch != "exclusive":
            return res                # 겹치는 창·창 밖 — 귀속할 수 없다
        hit: dict = {}
        miss: dict = {}
        for tk, ts in pairs:
            if tk in hit:
                continue
            verdict = classify(ts, venue)
            if verdict == "counted":
                hit[tk] = ts
            else:
                res[verdict] += 1
                # 빈 문자열도 **보이게** 남긴다 — falsy 라 화면에서 통째로
                # 사라지면 "원문이 비어 있었다" 는 사실을 못 말한다(#43).
                miss.setdefault(verdict, str(ts) if str(ts or "") else "(빈 문자열)")
        res["counted"] = len(hit)
        with _locked():
            store, state = read_store()
            if state.startswith("unreadable"):
                # 못 읽었으면 **쓰지 않는다** — 새 dict 로 시작하면 다음 쓰기가
                # 몇 달치 누적을 덮는다(리뷰 B1 실측: 4종목 → 1종목).
                raise RuntimeError(f"누적 파일을 못 읽었습니다 — {state}")
            v = store.setdefault("venues", {}).setdefault(venue, {})
            tks = v.setdefault("tickers", {})
            days = set()
            for tk, ts in hit.items():
                t = parse_ts(ts)
                # 날짜는 **체결 시각**에서 — 스캔 시각으로 적으면 낡은 블록이
                # 정당하게 세어진 날(휴장일 포함) 관측일이 어긋난다(리뷰 M2).
                day = (t or datetime.now(KST)).strftime("%Y-%m-%d")
                days.add(day)
                rec = tks.get(tk)
                if rec is None:
                    if len(tks) >= _MAX_TICKERS:
                        v["capped"] = int(v.get("capped") or 0) + 1
                        continue
                    tks[tk] = {"first": day, "last": day, "n": 1}
                    res["new"] += 1
                else:
                    rec["last"] = max(str(rec.get("last") or ""), day)
                    rec["n"] = int(rec.get("n") or 0) + 1
            if days:
                dates = [d for d in (v.get("dates") or []) if d not in days]
                # 최신이 뒤 — 자르는 쪽도 **오래된 앞쪽**이어야 화면의 범위 끝이
                # 옛 날짜로 굳지 않는다(리뷰 M3).
                v["dates"] = sorted(dates + sorted(days))[-_MAX_DATES:]
            # ⚠️ 아래 셋은 `if hit:` **밖**이다 — 그래야 "스캔이 안 돌았다" 와
            # "돌았는데 0건" 이 구별된다(리뷰 H2).
            v["scans"] = int(v.get("scans") or 0) + 1
            v["last_scan"] = (now or datetime.now(KST)).astimezone(
                KST).strftime("%Y-%m-%d %H:%M")
            if hit:
                v["ts_sample"] = str(next(iter(hit.values())) or "")
            for kind, sample in miss.items():
                v[f"{kind}_sample"] = sample
            v["undated"] = int(v.get("undated") or 0) + res["undated"]
            v["outside"] = int(v.get("outside") or 0) + res["outside"]
            _write_store(store)
            res["total"] = len(tks)      # 쓰기가 실제로 끝난 뒤에만 말한다
    except Exception as exc:                                  # noqa: BLE001
        log.warning("venue_universe: 누적 실패: %s", exc)
    return res


def summary() -> dict:
    """누적 상태(읽기 전용) — {거래소: {total, dates, scans, ...}}.

    못 읽으면 **빈 dict 가 아니라** `{"_error": 사유}` — 빈 dict 는 '아직 안
    쌓였다' 로 읽혀 장애를 정상으로 위장한다(#54·#82).
    """
    out: dict = {}
    store, state = read_store()
    if state.startswith("unreadable"):
        return {"_error": state}
    for venue, v in (store.get("venues") or {}).items():
        tks = v.get("tickers") or {}
        out[venue] = {"total": len(tks),
                      "dates": list(v.get("dates") or []),
                      "scans": int(v.get("scans") or 0),
                      "last_scan": v.get("last_scan") or "",
                      "undated": int(v.get("undated") or 0),
                      "outside": int(v.get("outside") or 0),
                      "capped": int(v.get("capped") or 0),
                      "ts_sample": v.get("ts_sample") or "",
                      "undated_sample": v.get("undated_sample") or "",
                      "outside_sample": v.get("outside_sample") or "",
                      "sample": sorted(tks)[:10]}
    return out


def format_lines(summ: dict | None = None) -> list:
    """사람이 읽는 줄 — 프로브 ⑤ 와 CLI 가 **같은 문장**을 쓴다(#38)."""
    s = summary() if summ is None else summ
    if s.get("_error"):
        return [f"❌ 누적 기록을 못 읽었습니다 — {s['_error']}. 그 사이 수집은 "
                "덮어쓰지 않고 건너뜁니다(기록 유실 방지)."]
    if not s:
        _, branch = exclusive_venue()
        why = {"exclusive": "전용 창인데 아직 스캔이 안 돌았습니다",
               "overlap": "지금은 두 거래소 창이 겹치는 구간입니다",
               "closed": "지금은 체결 창 밖입니다"}.get(branch, branch)
        return [f"❓ 누적된 관측이 없습니다 — {why}. 전용 창"
                f"({exclusive_window_label()})에 시간외 보드 스캔이 돌면 "
                "자동으로 쌓입니다(추가 네트워크 콜 0)."]
    lines = []
    for venue, v in sorted(s.items()):
        # ⚠️ 0종목에 ✅ 를 찍으면 "쟀는데 없다" 로 읽힌다 — 스캔은 돌았는데 한
        # 건도 못 셌다면 그건 **판정 불가**다(#54, 리뷰 H2).
        mark = "✅" if v["total"] else "❓"
        lines.append(
            f"{mark} {venue} 전용 창 관측 하한 {v['total']}종목 "
            f"— 관측일 {len(v['dates'])}일"
            + (f"({v['dates'][0]}"
               + (f"~{v['dates'][-1]})" if v["dates"][0] != v["dates"][-1]
                  else ")") if v["dates"] else "")
            + f" · 스캔 {v['scans']}회"
            + (f" · 마지막 {v['last_scan']}" if v["last_scan"] else ""))
        if v["sample"]:
            lines.append("   표본: " + ", ".join(v["sample"])
                         + (" …" if v["total"] > len(v["sample"]) else ""))
        skipped = []
        if v["undated"]:
            skipped.append(f"체결시각 못 읽음 {v['undated']}건")
        if v["outside"]:
            skipped.append(f"체결시각이 전용 창 밖 {v['outside']}건")
        if v["capped"]:
            skipped.append(f"상한({_MAX_TICKERS}) 초과로 버림 {v['capped']}건")
        if skipped:
            lines.append("   ⚠️ 안 센 것: " + " · ".join(skipped))
        # 못 센 건의 원문 표본 — 형식 가정(naive=KST)이 틀린 세계에서 이 줄이
        # 유일한 단서다(#109·#165).
        for lbl, key in (("센 건", "ts_sample"), ("못 읽은 건", "undated_sample"),
                         ("창 밖으로 본 건", "outside_sample")):
            if v.get(key):
                lines.append(f"   체결시각 원문 표본({lbl}): {v[key]}")
    lines.append("   ⚠️ 하한이지 목록이 아닙니다 — 그 창에 체결이 없었을 "
                 "뿐인 종목이 빠지고, 스캔 유니버스도 정규장 무버 상위라 전 "
                 "상장이 아닙니다. 거래소 필드가 아니라 창으로 가른 것이라 "
                 "원천이 창을 바꾸면 이 판정도 바뀝니다(공지 153).")
    return lines


def main() -> int:
    print(f"venue_universe — 거래소 전용 창 관측 하한(읽기 전용) · "
          f"저장소 {_STORE}")
    print("실행: cd ~/stock && .venv/bin/python -m bot.venue_universe")
    for line in format_lines():
        print(line)
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
