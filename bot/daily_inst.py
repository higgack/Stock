"""Daily 기관매수 — KRX 종목별 기관 순매수대금 ÷ 시가총액 TOP 10 (사용자 2026-10-12).

사용자가 붙인 표(시총 3구간 × 「순매수 강도 · 순매수 전환 · 5일 연속 순매수」
TOP 10)를 매 거래일 같은 모양으로 만든다. 화면은 Daily Byte 처럼 월 → 일로
접히는 일별 기록이다(사용자 "일별 업데이트되는거랑 똑같이").

원천 — pykrx(KRX 정보데이터시스템, 로그인 필수) 두 벌크 호출:
  · `get_market_net_purchases_of_equities(D, D, 시장, "기관합계")` 의
    `순매수거래대금`(원) — KOSPI·KOSDAQ 각 1콜, 전 종목.
  · `get_market_cap_by_ticker(D, "ALL")` 의 `시가총액`(원) — 1콜.
값 = 기관 순매수대금 ÷ 기준일 시가총액 × 100(%). 수치는 전부 원천 그대로이고
우리가 만드는 것은 나눗셈·정렬·구간 나누기뿐이다.

⚠️ **원문 표의 정의는 재지 않았다**(#165) — 원문은 산식을 적지 않았다. 우리가
고른 정의를 화면 ℹ️ 가이드가 그대로 밝힌다:
  · 구간 = 기준일 시가총액 1조 이상 / 5천억~1조 / 5천억 미만.
  · 강도 = 기준일 순매수(>0) 종목을 값 순으로.
  · 전환 = 직전 거래일 **순매도(<0)** → 기준일 순매수(>0).
  · 5일 연속 = 기준일 포함 최근 5거래일 모두 순매수(>0), 기준일 값 순으로.
  원문의 「5일 연속」 칸 값이 같은 종목의 「강도」 칸 값과 같아(스피어 0.69% 등)
  기준일 값으로 정렬한 것으로 읽었다 — 읽은 것이지 원문이 밝힌 것이 아니다.

⚠️ **언제 집계가 끝나는지도 재지 않았다**(사용자 "정확히 언제 집계되는지는 나도
잘 몰라"). 그래서 (1) 그날 마지막 체결 창(KRX 애프터마켓 20:00) 종료 + 30분
뒤부터만 그날을 요구하고(`kr_session.last_settled_session` — 애프터마켓 체결이
집계에 들어가도 틀리지 않는 쪽), (2) **이튿날 07:00 이후 한 번 더 받아** 저녁
값과 대조한다(`recheck`). 바뀐 종목 수를 기록에 남기므로 "저녁 집계가 최종이었나"
를 사람이 아니라 기록이 답한다. 아직 집계 전인 응답(빈 표·전 종목 0)은 굽지 않는다
(#280 부분을 완전본으로 굽지 않는다) — 같은 날 시가총액을 대조군으로 물어(#143) 갈래를
가른다: 시총 전부 0 = KRX 가 휴장으로 답함(거래일에서 뺀다) · 시총은 옴 = 기준일이면
집계 대기(15분 고정 재시도), 지난 날이면 그날만 늦추고 여러 번 비면 "자료 없음" 카드 ·
시총도 빔 = KRX 가 답하지 않음(전체 백오프).

자동 — 봇의 15분 주기 태스크(`tick`)가 "받아야 할 거래일 기록이 있나"를 보고
없으면 받는다(상태 없는 스케줄: 재시작해도 기준점이 안 밀린다). 최근
`KEEP_SESSIONS` 거래일이 비어 있으면 스스로 메운다(봇이 멈췄던 날도, #171).
한 번에 쓰는 KRX 호출은 `CALL_BUDGET` 이 막는다(#116).

진단 — `cd ~/stock && .venv/bin/python -m bot.daily_inst --why` (읽기 전용),
`--probe [YYYY-MM-DD]`(그 날짜를 지금 받아 보기만 — 아무것도 안 쓴다),
`--run`(한 주기 실행 + 페이지 재생성).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.daily_inst")

_KST = timezone(timedelta(hours=9))
_DIR = Path(os.environ.get("HOME") or str(Path.home())) / ".tradingagents" / "daily_inst"
_REC_DIR = _DIR / "records"
_RAW_DIR = _DIR / "raw"
_STATE = _DIR / "state.json"

REC_VER = 1
INVESTOR = "기관합계"
# (pykrx 시장, 티커 접미사) — 접미사는 종목분석 링크(`lookup/005930.KS`)용.
MARKETS = (("KOSPI", ".KS"), ("KOSDAQ", ".KQ"))
# (키, 화면 라벨, 하한 이상, 상한 미만 — None 이면 무한). 단위 원.
TIERS = (("big", "시가총액 1조 이상", 1e12, None),
         ("mid", "시가총액 5천억 ~ 1조", 5e11, 1e12),
         ("small", "시가총액 5천억 미만", 0.0, 5e11))
LISTS = (("strength", "순매수 강도 TOP 10"),
         ("turn", "순매수 전환 TOP 10"),
         ("streak", "5일 연속 순매수 TOP 10"))
TOP_N = 10
STREAK_N = 5
KEEP_SESSIONS = 10        # 자동으로 채워 두는 최근 기록 수
SETTLE_MARGIN_MIN = 30    # 애프터마켓 종료 뒤 여유(잰 값 아님)
GRACE_MIN = 60            # 경계 뒤 이만큼은 '집계 대기'(⚠️ 아님)
RECHECK_HOUR = 7          # 이튿날 이 시각 이후 한 번 더 받아 대조
CALL_BUDGET = 24          # 한 주기 KRX 호출 상한(순매수 1일 = 2콜, 시총 = 1콜)
RAW_KEEP_DAYS = 45
MIN_MCAP_COVER = 0.9      # 순매수 종목 중 시총을 찾은 비율이 이보다 낮으면 굽지 않는다
WAIT_RETRY_MIN = 15       # 기준일 '집계 대기' 는 이 간격 고정으로 다시 묻는다(실패 아님)
WAIT_MAX = 16             # 그 고정 재시도 횟수 상한(약 4시간) — 넘기면 전체 백오프
WAIT_GRACE_H = 12         # 집계 대기가 확인된 동안은 이만큼 ⏳(⚠️ 아님)
MISSING_AFTER = 3         # 시장은 열렸는데 기관 순매수만 이 횟수 비면 그날을 빼고 만든다
RENDER_DAYS = 120         # 화면에 싣는 최근 기록 수(그 앞은 보관만 — 페이지 크기 상한)
STUCK_MIN = 30            # 한 주기가 이만큼 안 끝나면 화면이 말한다(원천이 응답 없이 멈춤)
TICK_TIMEOUT_SEC = 600    # 봇 루프가 한 주기를 기다리는 상한(넘으면 다음 주기로)
MISSING_MIN_AGE_H = 12    # '자료 없음' 확정은 첫 실패 뒤 이만큼 지나야(일시 장애 배제)
REOPEN_H = 24             # 자료 없음·휴장 표식은 창 안에 있는 동안 이 간격으로 다시 묻는다
_KRX_TIMEOUT = (10, 60)   # pykrx 요청 (connect, read) 초 — 라이브러리 기본값은 '무한'
_BACKOFF_MIN = (15, 30, 60, 120, 180)


# ── 저장 ────────────────────────────────────────────────────────────────────
def _read_json(path: Path):
    """어떤 바이트가 와도 던지지 않는다 — 깨진 파일은 None(#331)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return None


def _write_json(path: Path, obj) -> None:
    """원자 교체 — 쓰다 만 파일을 읽는 쪽이 보지 않게(#379·#384)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def record_path(date: str) -> Path:
    return _REC_DIR / f"{date}.json"


def load_record(date: str) -> dict | None:
    r = _read_json(record_path(date))
    if isinstance(r, dict) and r.get("ver") == REC_VER and r.get("date") == date:
        return r
    return None


def load_records() -> list[dict]:
    """저장된 기록 전부, 날짜 오름차순. 판이 다르거나 깨진 파일은 건너뛴다."""
    out = []
    try:
        files = sorted(_REC_DIR.glob("????-??-??.json"))
    except Exception:                                          # noqa: BLE001
        files = []
    for f in files:
        r = load_record(f.stem)
        if r:
            out.append(r)
    return out


def _raw_path(date: str) -> Path:
    return _RAW_DIR / f"{date}.json"


def load_raw(date: str) -> dict | None:
    """그날 원자료 — 순매수 표가 있거나, KRX 가 그날 자료를 주지 않는다는 표식
    (`nodata` = holiday | missing)이 있으면 돌려준다."""
    r = _read_json(_raw_path(date))
    if not isinstance(r, dict) or r.get("date") != date:
        return None
    if isinstance(r.get("nets"), dict) or r.get("nodata") in ("holiday", "missing"):
        return r
    return None


def _prune_raw(now: datetime) -> None:
    cut = (now - timedelta(days=RAW_KEEP_DAYS)).date().isoformat()
    try:
        for f in _RAW_DIR.glob("????-??-??.json"):
            if f.stem < cut:
                f.unlink()
    except Exception as exc:                                   # noqa: BLE001
        log.debug("daily_inst: raw prune 실패: %s", exc)


def load_state() -> dict:
    s = _read_json(_STATE)
    return s if isinstance(s, dict) else {}


# ── 시각 ────────────────────────────────────────────────────────────────────
def _now(now: datetime | None = None) -> datetime:
    t = now or datetime.now(_KST)
    return t.astimezone(_KST) if t.tzinfo else t.replace(tzinfo=_KST)


def _iso(t: datetime) -> str:
    return t.astimezone(_KST).isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(ts))
    except Exception:                                          # noqa: BLE001
        return None
    return t if t.tzinfo else t.replace(tzinfo=_KST)


def settled_session(now: datetime | None = None) -> str | None:
    """지금 '이미 집계가 끝났어야 할' 마지막 거래일(달력이 없으면 None)."""
    from bot import kr_session
    return kr_session.last_settled_session(_now(now), "KRX", SETTLE_MARGIN_MIN)


def boundary_hm() -> tuple[int, int]:
    """그날을 요구하기 시작하는 시각(시, 분) — 화면 문구도 여기서(#38)."""
    from bot import kr_session
    eh, em = kr_session.day_end("KRX")
    t = eh * 60 + em + SETTLE_MARGIN_MIN
    return (t // 60, t % 60)


def recent_sessions(end: str, n: int) -> list[str] | None:
    """`end` 이하 최근 n 거래일(오름차순). 달력이 없으면 None(#54)."""
    from bot import market_calendar as mc
    start = (datetime.fromisoformat(end) - timedelta(days=n * 2 + 20)).date().isoformat()
    ss = mc.sessions_between("KR", start, end)
    if ss is None:
        return None
    return ss[-n:]


def recheck_due_at(date: str) -> datetime:
    """그 거래일 기록을 다시 받아 대조할 수 있는 가장 이른 시각(이튿날 07:00 KST)."""
    d = datetime.fromisoformat(date).replace(tzinfo=_KST)
    return (d + timedelta(days=1)).replace(hour=RECHECK_HOUR, minute=0, second=0)


def needs_recheck(rec: dict, now: datetime | None = None) -> bool:
    """저녁에 받은 기록인데 아직 재확인 전이고, 지금이 재확인 가능 시각 이후인가.
    재확인 시각 뒤에 처음 받은 기록(백필 등)은 이미 그 시각의 값이라 다시 안 묻는다."""
    if rec.get("recheck") or rec.get("nodata"):
        return False
    due = recheck_due_at(rec["date"])
    got = _parse(rec.get("collected_at"))
    return bool(got and got < due <= _now(now))


# ── 원천 ────────────────────────────────────────────────────────────────────
def _ensure_krx_timeout() -> None:
    """pykrx 의 KRX 로그인 세션에 기본 timeout 을 붙인다 — pykrx 1.2.8 의 요청엔
    timeout 이 없어 KRX 가 응답 없이 멈추면 스레드가 영영 안 돌아오고 수집 잠금이
    재시작 전까지 풀리지 않는다(독립 리뷰). 세션이 재로그인으로 바뀌면 다시 붙인다.
    익명 세션(로그인 실패)은 요청마다 새로 만들어 붙일 수 없다 — 그 경우는 로그인
    실패라 응답이 빨리 온다. 실패해도 수집을 막지 않는다."""
    try:
        from requests.adapters import HTTPAdapter

        from pykrx.website.comm.auth import get_auth_session

        class _TimeoutAdapter(HTTPAdapter):
            def send(self, request, **kw):
                if kw.get("timeout") is None:
                    kw["timeout"] = _KRX_TIMEOUT
                return super().send(request, **kw)

        krxs = get_auth_session()
        sess = getattr(krxs, "session", None)
        if sess is not None and not getattr(sess, "_noah_timeout", False):
            sess.mount("https://", _TimeoutAdapter())
            sess.mount("http://", _TimeoutAdapter())
            sess._noah_timeout = True
    except Exception as exc:                                   # noqa: BLE001
        log.debug("daily_inst: KRX timeout 어댑터 실패: %s", exc)


def _pykrx_stock():
    from pykrx import stock
    _ensure_krx_timeout()
    return stock


def _col(df, *keys):
    for c in df.columns:
        if all(k in str(c) for k in keys):
            return c
    return None


def fetch_nets(date: str) -> tuple[dict | None, str, str]:
    """({티커(.KS/.KQ): [종목명, 기관 순매수대금(원)]}, 사유, 갈래) — KOSPI·KOSDAQ 각 1콜.

    갈래 ok · empty(빈 표·전 종목 0 — 집계 전이거나 자료가 없는 날) · error(예외·열
    없음). 한 시장이라도 비거나 전 종목 0 이면 **통째로** 실패다(#280 — 반쪽을
    완전본으로 굽지 않는다). 사유는 갈래를 이름으로 말한다(#82)."""
    ymd = date.replace("-", "")
    try:
        stock = _pykrx_stock()
    except Exception as exc:                                   # noqa: BLE001
        return None, f"pykrx 없음({type(exc).__name__})", "error"
    out: dict = {}
    for mkt, sfx in MARKETS:
        try:
            df = stock.get_market_net_purchases_of_equities(ymd, ymd, mkt, INVESTOR)
        except Exception as exc:                               # noqa: BLE001
            return None, f"{mkt} 조회 실패({type(exc).__name__}: {str(exc)[:80]})", "error"
        if df is None or len(df) == 0:
            return None, f"{mkt} 응답이 비었다", "empty"
        net_col = _col(df, "순매수", "대금")
        if net_col is None:
            return None, (f"{mkt} 응답에 순매수거래대금 열이 없다(열: "
                          f"{', '.join(str(c) for c in list(df.columns)[:8])})"), "error"
        name_col = _col(df, "종목명")
        got, nonzero = 0, 0
        for tkr in df.index:
            try:
                v = float(df.loc[tkr, net_col])
            except Exception:                                  # noqa: BLE001
                continue
            if v != v:                                         # NaN
                continue
            nm = str(df.loc[tkr, name_col]) if name_col is not None else str(tkr)
            out[f"{str(tkr).zfill(6)}{sfx}"] = [nm, v]
            got += 1
            nonzero += bool(v)
        if not nonzero:
            return None, f"{mkt} 전 종목 순매수 0({got}행)", "empty"
    return out, "", "ok"


def fetch_mcap(date: str) -> tuple[dict | None, str, str]:
    """({6자리 코드: 시가총액(원)}, 사유, 갈래) — 전 시장 1콜.

    갈래 ok · holiday(행은 있는데 **전부 0** — KRX 가 그날을 휴장으로 답하는 모양,
    pykrx 자신도 이걸 휴장 신호로 쓴다) · empty(빈 응답) · error(예외·열 없음)."""
    ymd = date.replace("-", "")
    try:
        stock = _pykrx_stock()
    except Exception as exc:                                   # noqa: BLE001
        return None, f"pykrx 없음({type(exc).__name__})", "error"
    try:
        df = stock.get_market_cap_by_ticker(ymd, market="ALL")
    except Exception as exc:                                   # noqa: BLE001
        return None, f"시가총액 조회 실패({type(exc).__name__}: {str(exc)[:80]})", "error"
    if df is None or len(df) == 0:
        return None, "시가총액 응답이 비었다", "empty"
    col = _col(df, "시가총액")
    if col is None:
        return None, "시가총액 응답에 시가총액 열이 없다", "error"
    out = {}
    for tkr in df.index:
        try:
            v = float(df.loc[tkr, col])
        except Exception:                                      # noqa: BLE001
            continue
        if v == v and v > 0:
            out[str(tkr).zfill(6)] = v
    if not out:
        return None, f"시가총액이 전부 0({len(df)}행) — KRX 가 그날을 휴장으로 답함", "holiday"
    return out, "", "ok"


# ── 계산(순수) ──────────────────────────────────────────────────────────────
def _rank(r: dict):
    return (-r["r"], -r["net"], r["t"])


def build_record(date: str, sessions: list[str], nets_by_date: dict, mcap: dict,
                 *, collected_at: str = "") -> dict:
    """기준일 `date` 의 3구간 × 3목록. `sessions` 는 `date` 로 끝나는 최근 거래일
    (오름차순), `nets_by_date` 는 {날짜: {티커: [이름, 순매수원]}}. 순수 함수.

    전환·5일 연속은 앞 거래일 자료가 없으면 **만들지 않고**(빈 표가 아니라 None)
    화면이 왜 없는지 말한다(#43·#54)."""
    cur = nets_by_date[date]
    idx = sessions.index(date)
    prev_d = sessions[idx - 1] if idx >= 1 else None
    prev = nets_by_date.get(prev_d) if prev_d else None
    window = sessions[max(0, idx - STREAK_N + 1): idx + 1]
    missing = [d for d in window if d not in nets_by_date]
    streak_ok = len(window) == STREAK_N and not missing

    rows, miss = [], 0
    for t, (nm, net) in cur.items():
        cap = mcap.get(t.split(".")[0])
        if not cap or cap <= 0:
            miss += 1
            continue
        rows.append({"t": t, "n": nm, "net": float(net), "mcap": float(cap),
                     "r": float(net) / float(cap) * 100.0})

    def _slim(r):
        return {"t": r["t"], "n": r["n"], "r": round(r["r"], 6),
                "net": int(round(r["net"])), "mcap": int(round(r["mcap"]))}

    tiers = []
    for key, label, lo, hi in TIERS:
        sub = [r for r in rows if r["mcap"] >= lo and (hi is None or r["mcap"] < hi)]
        pos = sorted((r for r in sub if r["net"] > 0), key=_rank)
        lists = {"strength": [_slim(r) for r in pos[:TOP_N]]}
        if prev is not None:
            lists["turn"] = [_slim(r) for r in pos
                             if r["t"] in prev and prev[r["t"]][1] < 0][:TOP_N]
        else:
            lists["turn"] = None
        if streak_ok:
            lists["streak"] = [_slim(r) for r in pos if all(
                r["t"] in nets_by_date[d] and nets_by_date[d][r["t"]][1] > 0
                for d in window)][:TOP_N]
        else:
            lists["streak"] = None
        tiers.append({"key": key, "label": label, "n": len(sub),
                      "n_pos": len(pos), "lists": lists})
    return {"ver": REC_VER, "date": date, "prev": prev_d, "window": window,
            "streak_missing": missing, "collected_at": collected_at,
            "universe": len(rows), "net_rows": len(cur), "mcap_missing": miss,
            "tiers": tiers, "recheck": None}


def nodata_record(date: str, reason: str, collected_at: str) -> dict:
    """KRX 가 그날 자료를 주지 않은 날의 기록 — 카드가 비는 대신 사유를 말한다(#43)."""
    return {"ver": REC_VER, "date": date, "nodata": reason, "collected_at": collected_at,
            "tiers": [], "recheck": None}


def diff_nets(old: dict, new: dict) -> dict:
    """재확인 대조 — 바뀐 종목 수(값이 바뀐 것 + 추가·삭제)와 가장 큰 값 차이(원)."""
    keys = set(old) | set(new)
    changed, moved, max_abs = 0, 0, 0.0
    for k in keys:
        a = old.get(k, [None, None])[1]
        b = new.get(k, [None, None])[1]
        if a != b:
            changed += 1
            if a is not None and b is not None:
                moved += 1
                max_abs = max(max_abs, abs(float(a) - float(b)))
    return {"changed": changed, "value_changed": moved, "compared": len(keys),
            "max_abs_won": int(max_abs)}


# ── 수집 ────────────────────────────────────────────────────────────────────
class _Budget:
    def __init__(self, n: int):
        self.left = int(n)
        self.used = 0

    def take(self, n: int) -> bool:
        if self.left < n:
            return False
        self.left -= n
        self.used += n
        return True


class _Stop(Exception):
    """이번 시도를 멈춘다 — reason 이 없으면 예산 소진. `date` 는 **실패한 원자료의
    날짜**(기준일 기록을 만들다 앞 거래일이 실패한 것과 기준일 자체가 집계 전인 것은
    다른 사실이다, #82), `kind` 는 갈래(empty·error·mcap_*·coverage·deferred)."""

    def __init__(self, reason: str = "", date: str = "", kind: str = "error"):
        super().__init__(reason)
        self.reason = reason
        self.date = date
        self.kind = kind


def _mcap_cached(date: str) -> dict | None:
    m = (load_raw(date) or {}).get("mcap")
    return m if isinstance(m, dict) and m else None


def _nets_for(date: str, budget: _Budget, now: datetime, deferred=(),
              ok: list | None = None) -> dict | None:
    """그날 원자료(캐시 우선). KRX 가 그날 자료를 주지 않는 날(표식)은 None.
    원천에서 실제로 받았으면 `ok` 에 날짜를 남긴다 — '자료 없음' 확정의 양성 대조."""
    raw = load_raw(date)
    if raw:
        return None if raw.get("nodata") else raw["nets"]
    if date in deferred:
        raise _Stop(f"{date} 재시도 대기 중", date, "deferred")
    if not budget.take(2):
        raise _Stop()
    nets, why, kind = fetch_nets(date)
    if nets is None:
        raise _Stop(f"{date} 기관 순매수 — {why}", date, kind)
    _write_json(_raw_path(date), {"date": date, "fetched_at": _iso(now), "nets": nets})
    if ok is not None:
        ok.append(date)
    return nets


def _mcap_fetch(date: str, budget: _Budget) -> dict:
    m = _mcap_cached(date)
    if m:
        return m
    if not budget.take(1):
        raise _Stop()
    m, why, kind = fetch_mcap(date)
    if m is None:
        raise _Stop(f"{date} 시가총액 — {why}", date, f"mcap_{kind}")
    return m


def _save_mcap(date: str, mcap: dict) -> None:
    raw = load_raw(date)
    if raw and not raw.get("nodata") and raw.get("mcap") != mcap:
        raw["mcap"] = mcap
        _write_json(_raw_path(date), raw)


def _build_for(date: str, sessions: list[str], budget: _Budget, now: datetime,
               deferred=(), ok: list | None = None) -> dict:
    idx = sessions.index(date)
    window = sessions[max(0, idx - STREAK_N + 1): idx + 1]
    # 기준일을 먼저 — 아직 집계 전이면 앞 날짜를 받느라 예산을 쓰지 않는다.
    cur = _nets_for(date, budget, now, deferred, ok)
    if cur is None:
        raw = load_raw(date) or {}
        rec = nodata_record(date, raw.get("reason") or "KRX 가 그날 자료를 주지 않음",
                            _iso(now))
        _write_json(record_path(date), rec)
        return rec
    # 창 날짜를 시가총액보다 **먼저** 본다 — 창 날짜가 막혀 기록이 안 끝나면 받은
    # 시총은 캐시되지 않아 막힌 기록마다 매 주기 시총을 다시 받는다(독립 리뷰).
    nets_by_date = {date: cur}
    for d in window[:-1]:
        n = _nets_for(d, budget, now, deferred, ok)
        if n is not None:
            nets_by_date[d] = n
    mcap = _mcap_fetch(date, budget)
    rec = build_record(date, sessions, nets_by_date, mcap, collected_at=_iso(now))
    cover = rec["universe"] / max(1, rec["net_rows"])
    if cover < MIN_MCAP_COVER:
        # 시총은 **저장하지 않는다** — 캐시하면 다음 주기가 원천에 다시 묻지 않고 같은
        # 반쪽으로 영구히 실패한다(독립 리뷰 실측).
        raise _Stop(f"{date} 시가총액을 찾은 종목이 {rec['universe']}/{rec['net_rows']}"
                    f"({cover:.0%}) — 반쪽 표는 굽지 않는다", date, "coverage")
    _save_mcap(date, mcap)
    _write_json(record_path(date), rec)
    return rec


def _recheck(rec: dict, sessions: list[str], budget: _Budget, now: datetime) -> dict:
    """이튿날 다시 받아 저녁 값과 대조. **기록을 먼저 쓰고 원자료를 나중에** 덮는다 —
    중간에 실패하면 다음 재시도가 여전히 저녁 값과 대조한다(독립 리뷰: 원자료를 먼저
    덮으면 아침 값끼리 비교해 거짓 '바뀐 종목 0' 이 남았다)."""
    date = rec["date"]
    old = load_raw(date)
    old_nets = (old or {}).get("nets") if old and not old.get("nodata") else None
    if not budget.take(2):
        raise _Stop()
    nets, why, kind = fetch_nets(date)
    if nets is None:
        raise _Stop(f"{date} 재확인 — {why}", date, kind)
    # 저녁 원자료가 없으면 대조할 수 없다 — 전 종목을 '바뀜' 으로 세면 거짓이다(#54).
    d = (diff_nets(old_nets, nets) if old_nets is not None
         else {"changed": None, "value_changed": 0, "compared": 0, "max_abs_won": 0})
    mcap = _mcap_fetch(date, budget)
    nets_by_date = {date: nets}
    for x in rec.get("window") or []:
        if x != date:
            r = load_raw(x)
            if r and not r.get("nodata"):
                nets_by_date[x] = r["nets"]
    new = build_record(date, sessions, nets_by_date, mcap,
                       collected_at=rec.get("collected_at", ""))
    new["recheck"] = {"at": _iso(now), **d}
    _write_json(record_path(date), new)
    raw = dict(old or {})
    raw.pop("nodata", None)
    raw.update({"date": date, "nets": nets, "mcap": mcap, "rechecked_at": _iso(now)})
    _write_json(_raw_path(date), raw)
    return new


def _save_state(st: dict) -> None:
    try:
        _write_json(_STATE, st)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("daily_inst: 상태 저장 실패: %s", exc)


def _ladder(n: int) -> int:
    return _BACKOFF_MIN[min(max(n, 1), len(_BACKOFF_MIN)) - 1]


def _fail(st: dict, now: datetime, reason: str) -> None:
    """원천·환경이 통째로 안 되는 실패 — 전체 백오프(15분→최대 3시간)."""
    n = int(st.get("fails") or 0) + 1
    wait = _ladder(n)
    st.update({"fails": n, "reason": reason, "waiting": False,
               "next_try": _iso(now + timedelta(minutes=wait))})
    log.warning("daily_inst: %s — %d분 뒤 다시 시도(연속 %d회)", reason, wait, n)


def _day_fail(days: dict, key: str, now: datetime, reason: str, kind: str,
              traded: bool = False) -> dict:
    """한 날짜만의 실패 — 그날만 늦추고 다른 날은 계속 메운다(독립 리뷰 H1).
    `traded` = 같은 날 시가총액은 나왔다(시장은 열렸다), `first` = 첫 실패 시각."""
    prev = days.get(key) or {}
    n = int(prev.get("n") or 0) + 1
    days[key] = {"n": n, "reason": reason, "kind": kind, "traded": bool(traded),
                 "first": prev.get("first") or _iso(now),
                 "next_try": _iso(now + timedelta(minutes=_ladder(n)))}
    log.warning("daily_inst: %s — 이 날짜만 %d분 뒤 다시(연속 %d회)", reason, _ladder(n), n)
    return days[key]


def _mark_nodata(date: str, kind: str, reason: str, now: datetime) -> None:
    _write_json(_raw_path(date), {"date": date, "nodata": kind, "reason": reason,
                                  "at": _iso(now)})


def _is_holiday(date: str) -> bool:
    return (load_raw(date) or {}).get("nodata") == "holiday"


def _classify(stop: _Stop, target: str, budget: _Budget, now: datetime) -> tuple[str, str]:
    """실패 하나를 갈래로 — (holiday|waiting|day|outage, 사유).

    기관 순매수가 **비었으면** 같은 날 시가총액을 물어 대조군으로 쓴다(#143): 행이
    있는데 전부 0 이면 KRX 가 그날을 휴장으로 답한 것(달력엔 거래일인 임시공휴일 등),
    시총은 멀쩡하면 시장은 열렸는데 기관 집계만 아직(기준일) 또는 없음(지난 날),
    시총도 비면 KRX 가 답하지 않는 것(로그인·장애 — 처방이 정반대, #82)."""
    bad = stop.date or target
    if stop.kind == "coverage":
        return "day", stop.reason
    if stop.kind != "empty":
        # 지난 날 하나의 예외는 그날만 늦춘다 — 전체 백오프로 보내면 기준일을 받은
        # 주기에도 실패 횟수가 올라 다음 기준일이 최대 3시간 늦어진다(독립 리뷰).
        return ("day" if bad != target else "outage"), stop.reason
    if not budget.take(1):
        raise _Stop()
    m, why, kind = fetch_mcap(bad)
    if kind == "holiday":
        if bad < target or now >= recheck_due_at(bad):
            return "holiday", f"{bad} {why}"
        return "waiting", f"{stop.reason} · {why}(이튿날 아침까지 기다린다)"
    if kind == "ok":
        if bad == target:
            return "waiting", (f"{bad} 기관 순매수가 아직 비었다(시가총액은 나옴) — "
                               "KRX 가 아직 그날 기관 집계를 내지 않은 것으로 보임")
        return "day", f"{stop.reason}(시가총액은 나옴)"
    return "outage", f"{stop.reason} · {why} — KRX 가 답하지 않음(로그인·장애)"


def tick(*, now: datetime | None = None, budget: int = CALL_BUDGET) -> dict:
    """한 주기 — 받아야 할 기록(재확인 → 빠진 날 최신부터)을 예산 안에서 처리.

    반환 {"built", "rechecked", "holidays", "status"(ok|idle|budget|waiting|fail|backoff),
    "reason", "calls"}. 상태는 디스크에 남겨 화면·진단이 읽는다(별도 프로세스는 이
    모듈의 메모리를 못 본다, #343). 어떤 예외도 상태를 남기고 끝난다(독립 리뷰 M5)."""
    now = _now(now)
    st = load_state()
    res = {"built": [], "rechecked": [], "holidays": [], "status": "idle",
           "reason": "", "calls": 0}
    nt = _parse(st.get("next_try"))
    if nt and now < nt:
        res.update(status="backoff", reason=st.get("reason", ""))
        return res
    st["last_try"] = _iso(now)
    st["running_since"] = _iso(now)
    _save_state(st)
    try:
        _tick_body(st, now, budget, res)
    except Exception as exc:                                   # noqa: BLE001
        log.exception("daily_inst: 수집 중 예외")
        _fail(st, now, f"수집 중 예외({type(exc).__name__}: {str(exc)[:120]})")
        res.update(status="fail", reason=st["reason"])
    st.pop("running_since", None)
    _save_state(st)
    return res


def _tick_body(st: dict, now: datetime, budget: int, res: dict) -> None:
    try:
        from bot.pykrx_client import krx_login_ready
        ready = krx_login_ready()
    except Exception as exc:                                   # noqa: BLE001
        ready, why = False, f"pykrx_client 로드 실패({type(exc).__name__})"
    else:
        if not ready:
            # '미설정' 만 적으면 어느 키가 왜 없는지 운영자가 짐작한다 — 갈래는
            # `env_diag` 가 말한다(#82 · 값은 안 찍는다, §Secrets).
            from bot.env_keys import env_diag
            why = (f"KRX_ID/KRX_PW 미설정 — {env_diag('KRX_ID', 'KRX_PW')} · "
                   ".env 에 KRX 로그인 정보를 넣어야 수집된다")
    if not ready:
        _fail(st, now, why)
        res.update(status="fail", reason=st["reason"])
        return
    target = settled_session(now)
    cal = recent_sessions(target, KEEP_SESSIONS + STREAK_N + 5) if target else None
    if not cal:
        _fail(st, now, "거래일 달력(exchange_calendars)이 없어 받을 날짜를 정할 수 없다")
        res.update(status="fail", reason=st["reason"])
        return
    b = _Budget(budget)
    ok: list = []
    outage, waiting, out_of_budget, day_failed = "", "", False, []
    # ① 자료 없음·휴장 표식을 하루 한 번 다시 묻는다 — 일시 장애로 찍힌 표식이 영구히
    #    남지 않게(독립 리뷰). 범위는 보관 기록의 5일 창이 닿는 거래일까지.
    pre = [d for d in cal if not _is_holiday(d)]
    lo = (pre[-(KEEP_SESSIONS + STREAK_N - 1):] or cal)[0]
    try:
        for d in reversed([x for x in cal if x >= lo]):
            raw = load_raw(d)
            if raw and raw.get("nodata") and _reopen_due(raw, now):
                _reopen(d, raw, cal, b, now, ok)
    except _Stop:
        out_of_budget = True
    # KRX 가 휴장으로 답한 날은 거래일에서 뺀다 — 직전 거래일·5일 창이 그날을 건너뛴다.
    # ⚠️ 표식을 다시 물은 **뒤에** 정한다 — 휴장 표식이 풀린 날을 옛 목록으로 다시 만들면
    # 직전 거래일이 그날을 또 건너뛴다(델타 리뷰 반영 중 테스트가 잡음).
    sessions = [d for d in cal if not _is_holiday(d)]
    want = sessions[-KEEP_SESSIONS:]
    target = want[-1]
    # 창이 닿는 범위 — 보관 기록 KEEP_SESSIONS 개의 5일 창이 덮는 거래일 전부.
    span = sessions[-(KEEP_SESSIONS + STREAK_N - 1):]
    days = st.get("days") if isinstance(st.get("days"), dict) else {}
    # 창을 벗어난 날의 실패 기록은 버린다 — 다시 시도할 일이 없는데 남기면 화면이 영원히
    # '⚠️ 실패 중' 을 말한다(#25·#260). ⚠️ 기준은 `want` 가 아니라 **창 범위**다 — want
    # 바로 앞 날짜도 앞 기록들의 5일 창에 들어가므로, want 로 지우면 그날의 백오프가 매
    # 주기 지워져 영원히 다시 묻는다(독립 리뷰: 24시간에 KRX 576콜).
    for k in [k for k in days if k.replace("recheck:", "") not in span]:
        days.pop(k, None)
    st["days"] = days
    deferred = {k for k, v in days.items()
                if (t := _parse((v or {}).get("next_try"))) and now < t}
    try:
        if out_of_budget:
            raise _Stop()
        # ② 저녁에 받은 기록 재확인
        for d in reversed(want):
            rec = load_record(d)
            key = f"recheck:{d}"
            if not rec or key in deferred or not needs_recheck(rec, now):
                continue
            try:
                _recheck(rec, sessions, b, now)
                res["rechecked"].append(d)
                days.pop(key, None)
            except _Stop as stop:
                if not stop.reason:
                    raise
                _day_fail(days, key, now, stop.reason, stop.kind)
        # ③ 빠진 날 — 최신부터
        for d in reversed(want):
            if load_record(d) or d in deferred:
                continue
            try:
                _build_for(d, sessions, b, now, deferred, ok)
                res["built"].append(d)
                days.pop(d, None)
                continue
            except _Stop as stop:
                if not stop.reason:
                    raise
                if stop.kind == "deferred":
                    continue
                # `as` 변수는 except 블록을 나가면 지워진다 — 필요한 건 여기서 꺼낸다.
                bad, kind = stop.date or d, stop.kind
                verdict, why = _classify(stop, target, b, now)
            if verdict == "holiday":
                _mark_nodata(bad, "holiday", why, now)
                days.pop(bad, None)
                res["holidays"].append(bad)
                break               # 거래일 목록이 바뀌었다 — 다음 주기가 새 목록으로 걷는다
            if verdict == "waiting" and bad == target:
                waiting = why
                continue
            if verdict in ("day", "waiting"):
                # 'waiting' 이 기준일이 아닌 날에 나오는 경우는 없지만, 나오면 그날만 늦춘다.
                _day_fail(days, bad, now, why, kind, traded="시가총액은 나옴" in why)
                deferred.add(bad)       # 이 주기 안에서 같은 날을 또 묻지 않는다
                day_failed.append(bad)
                if len(day_failed) >= 2:
                    outage = f"지난 거래일 여러 날이 연달아 실패({', '.join(day_failed)}) — {why}"
                    break
                continue
            outage = why
            break
    except _Stop as stop:
        if stop.reason:
            outage = outage or stop.reason
        else:
            out_of_budget = True
    # ④ '자료 없음' 확정 — **같은 엔드포인트의 양성 대조**가 있을 때만: 이번 주기에 그날보다
    #    **나중** 날짜의 기관 순매수를 원천에서 실제로 받았어야 한다(시총은 다른 엔드포인트라
    #    순매수 엔드포인트만 죽은 것과 구별하지 못한다). 첫 실패 뒤 `MISSING_MIN_AGE_H` 가
    #    지나야 하고, 두 날 이상 실패한 주기(원천 장애)엔 확정하지 않는다(독립 리뷰).
    if not outage and len(day_failed) == 1:
        bad = day_failed[0]
        info = days.get(bad) or {}
        first = _parse(info.get("first"))
        if (info.get("traded") and int(info.get("n") or 0) >= MISSING_AFTER
                and first and now - first >= timedelta(hours=MISSING_MIN_AGE_H)
                and any(x > bad for x in ok)):
            _mark_nodata(bad, "missing", info.get("reason") or "KRX 가 그날 기관 순매수를 주지 않음",
                         now)
            days.pop(bad, None)
    res["calls"] = b.used
    if res["built"] or res["rechecked"]:
        st["last_ok"] = _iso(now)
    if outage:
        _fail(st, now, outage)
        res.update(status="fail", reason=outage)
    elif waiting:
        # 집계 대기는 실패가 아니다 — 15분 고정으로 다시 묻는다(지수 백오프면 KRX 가
        # 21:20 에 내도 22시가 넘어서야 받는다, 독립 리뷰 M4). 그래도 끝없이 묻지 않게
        # `WAIT_MAX` 번을 넘기면 전체 백오프로 넘어간다. 횟수는 **기준일마다** 센다 —
        # 앞 기준일이 끝내 안 나왔어도 새 기준일은 다시 15분부터다(독립 리뷰).
        n = (int(st.get("wait_n") or 0) if st.get("wait_date") == target else 0) + 1
        wait = WAIT_RETRY_MIN if n <= WAIT_MAX else _ladder(n - WAIT_MAX)
        st.update({"wait_n": n, "wait_date": target, "waiting": True, "reason": waiting,
                   "fails": 0, "next_try": _iso(now + timedelta(minutes=wait))})
        res.update(status="waiting", reason=waiting)
    else:
        st.update({"fails": 0, "reason": "", "next_try": None, "wait_n": 0,
                   "waiting": False})
        st.pop("wait_date", None)
        res["status"] = ("budget" if out_of_budget
                         else "ok" if res["built"] or res["rechecked"] or res["holidays"]
                         else "idle")
        if not out_of_budget:
            _prune_raw(now)


def _reopen_due(raw: dict, now: datetime) -> bool:
    at = _parse(raw.get("at"))
    return not at or now - at >= timedelta(hours=REOPEN_H)


def _reopen(d: str, raw: dict, cal: list, budget: _Budget, now: datetime, ok: list) -> bool:
    """자료 없음·휴장 표식을 단 날을 다시 묻는다. 이번엔 받으면 표식을 지우고 그날과
    그날을 창에 둔 기록을 지워 다시 만들게 한다. 못 받으면 다음 확인을 하루 뒤로."""
    if not budget.take(2):
        raise _Stop()
    nets, _why, _kind = fetch_nets(d)
    if nets is None:
        raw = dict(raw)
        raw["at"] = _iso(now)
        _write_json(_raw_path(d), raw)
        return False
    _write_json(_raw_path(d), {"date": d, "fetched_at": _iso(now), "nets": nets})
    ok.append(d)
    for x in (x for x in cal if x >= d):
        rec = load_record(x)
        # 그날이 그 기록의 5일 창 **범위** 안이면 다시 만든다 — 휴장으로 빠졌던 날은
        # 창 목록에 없으니 범위로 본다.
        win = rec.get("window") or [x] if rec else []
        if rec and (x == d or (win and win[0] <= d <= x)):
            try:
                record_path(x).unlink()
            except OSError:
                pass
    log.warning("daily_inst: %s 표식(%s)을 지웠다 — 다시 물으니 자료가 왔다", d, raw.get("nodata"))
    return True


# ── 판정(순수) ──────────────────────────────────────────────────────────────
def _days_note(st: dict) -> str:
    """한 날짜만 실패해 늦추고 있는 날 — 최신이어도 그 사실은 말한다(#41)."""
    items = []
    for k, v in sorted((st.get("days") or {}).items()):
        if not isinstance(v, dict):
            continue
        label = k.replace("recheck:", "") + (" 재확인" if k.startswith("recheck:") else "")
        items.append(f"{label} {v.get('n', 1)}회 실패({str(v.get('reason') or '')[:80]})")
    return " · ".join(items)


def _stuck_note(st: dict, now: datetime) -> str:
    t = _parse(st.get("running_since"))
    if t and now - t > timedelta(minutes=STUCK_MIN):
        return f"수집이 {t.strftime('%m-%d %H:%M')} 부터 끝나지 않음(KRX 가 응답 없이 멈춘 것으로 보임)"
    return ""


def latest_real(records: list[dict]) -> str | None:
    """'자료 없음' 카드를 뺀 마지막 기록 날짜 — 카드는 받은 게 아니라 못 받은 사실이다.
    그걸 최신으로 세면 순매수 엔드포인트가 통째로 죽어도 '1거래일 뒤처짐' 으로 보인다
    (독립 리뷰)."""
    real = [r["date"] for r in records if not r.get("nodata")]
    return max(real) if real else None


def _expected(now: datetime) -> str | None:
    """지금 받아야 할 마지막 거래일 — 달력 기준에서 KRX 가 휴장으로 답한 날을 뺀다.
    빼지 않으면 그날 뒤에 화면·감사가 거짓 ⚠️·❌ 다(독립 리뷰, #260)."""
    from bot import market_calendar as mc
    exp = settled_session(now)
    for _ in range(10):
        if not exp or not _is_holiday(exp):
            return exp
        exp = mc.last_session_on_or_before(
            "KR", (datetime.fromisoformat(exp) - timedelta(days=1)).date().isoformat())
    return exp


def _behind(latest: str, exp: str) -> int | None:
    """`latest` 가 `exp` 보다 몇 거래일 뒤인가 — 휴장 표식 날은 세지 않는다."""
    from bot import market_calendar as mc
    if latest >= exp:
        return 0
    start = (datetime.fromisoformat(latest) + timedelta(days=1)).date().isoformat()
    ss = mc.sessions_between("KR", start, exp)
    if ss is None:
        return None
    return len([d for d in ss if not _is_holiday(d)])


def status_verdict(latest: str | None, now: datetime | None = None,
                   state: dict | None = None) -> tuple[str, str]:
    """(갈래, 한 줄) — ok·degraded·wait·behind·none·unknown. 화면·감사·진단이 같은
    함수를 쓴다(#35·#38). `latest` 는 '자료 없음' 카드를 뺀 마지막 기록(`latest_real`).

    `wait` 는 ① 그날 경계 뒤 `GRACE_MIN` 안(수집 주기가 아직 안 돌았을 수 있다) 또는
    ② 수집기가 '그날은 아직 집계 대기' 로 판정한 동안(`WAIT_GRACE_H` 안 — 사유는 수집기가
    잰 갈래 그대로) — 늘 뜨는 경보는 아무것도 안 잰다(#25·#260). 최신이어도 지난 날
    하나가 실패해 늦추고 있거나, 전체 실패 사유가 남았거나, 수집이 멈췄으면 `degraded`
    로 그 사실을 말한다(✅ 가 지속 실패를 가리지 않게, #41)."""
    now = _now(now)
    st = state or {}
    exp = _expected(now)
    bh, bm = boundary_hm()
    nxt = _parse(st.get("next_try"))
    over = int(st.get("wait_n") or 0) > WAIT_MAX
    tail = ""
    if st.get("reason"):
        # 집계 대기 중엔 다음 시도가 15분마다 바뀐다 — 그대로 실으면 페이지가 15분마다
        # 다시 쓰여 업데이트 배너가 상시 뜬다(#25). 고정 재시도 상한을 넘겼을 때만 싣는다.
        tail = f" — 사유: {st['reason']}" + (
            f" · 다음 시도 {nxt.strftime('%m-%d %H:%M')}"
            if nxt and (not st.get("waiting") or over) else "")
    stuck = _stuck_note(st, now)
    if exp is None:
        return "unknown", "❓ 거래일 달력이 없어 최신 여부를 판정할 수 없습니다"
    if not latest:
        return "none", "아직 기록이 없습니다" + (tail or " — 첫 수집을 기다리는 중") + (
            f" · ⚠️ {stuck}" if stuck else "")
    behind = _behind(latest, exp)
    if behind is None:
        return "unknown", f"❓ {latest} 기록이 최신인지 판정할 수 없습니다(달력 조회 실패)"
    if behind == 0:
        notes = [n for n in (stuck, _days_note(st),
                             (f"수집 실패: {st['reason']}" if st.get("reason")
                              and not st.get("waiting") else "")) if n]
        if notes:
            return "degraded", f"✅ 최신 — {exp} 장 마감 집계까지 반영 · ⚠️ " + " · ".join(notes)
        return "ok", f"✅ 최신 — {exp} 장 마감 집계까지 반영"
    ready_at = datetime.fromisoformat(exp).replace(tzinfo=_KST, hour=bh, minute=bm)
    if behind == 1 and not stuck:
        if st.get("waiting") and now < ready_at + timedelta(hours=WAIT_GRACE_H):
            # 사유는 수집기가 **잰 갈래** 그대로 싣는다 — '시가총액은 나옴' 을 여기서
            # 단정하면 시총이 0 으로 온 날(이튿날 아침까지 기다리는 갈래)에 거짓이다(#165).
            why = str(st.get("reason") or "아직 받지 못함")[:160]
            when = (f"다음 시도 {nxt.strftime('%m-%d %H:%M')}" if over and nxt
                    else f"{WAIT_RETRY_MIN}분마다 다시 묻는 중")
            return "wait", f"⏳ {exp} 집계 대기 — {why} · {when}"
        if now < ready_at + timedelta(minutes=GRACE_MIN):
            return "wait", f"⏳ {exp} 집계를 받는 중 — 매 거래일 {bh:02d}:{bm:02d} 이후 수집"
    if not tail and _days_note(st):
        tail = f" — {_days_note(st)}"
    return "behind", (f"⚠️ {exp} 기록이 아직 없습니다(마지막 기록 {latest} · "
                      f"{behind}거래일 뒤처짐){tail}" + (f" · {stuck}" if stuck else ""))


# ── 화면 ────────────────────────────────────────────────────────────────────
_DI_CSS = """
<style>
.di-status{background:var(--surface2);border-left:3px solid var(--accent);border-radius:8px;padding:10px 12px;font-size:13px;color:var(--fg);margin:10px 0}
.di-search{display:flex;gap:8px;margin:12px 0}
.di-search input{flex:1;max-width:420px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--fg);font-size:13px}
.di-month{margin:12px 0}
.di-month>summary,.di-day>summary{cursor:pointer;font-weight:600;padding:8px 4px;list-style-position:inside}
.di-month>summary{font-size:15px;border-bottom:1px solid var(--border)}
.di-day{background:var(--card);border:1px solid var(--border);border-radius:12px;margin:10px 0;padding:4px 14px}
.di-day>summary{font-size:14px}
.di-meta{font-weight:400;font-size:12px;color:var(--muted);margin-left:8px}
.di-body{padding:4px 0 12px}
.di-tier{margin:14px 0 6px}
.di-tier-h{font-size:16px;font-weight:700;margin:6px 0 8px;color:var(--fg)}
.di-n{font-weight:400;font-size:12px;color:var(--muted);margin-left:6px}
.di-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
@media (max-width:960px){.di-grid{grid-template-columns:1fr}}
.di-tbl{width:100%;border-collapse:collapse;border:1px solid var(--fg);background:var(--card);font-size:13px}
.di-tbl th{text-align:center;color:var(--fg);font-size:13px;font-weight:700;padding:8px;border-bottom:2px solid var(--fg);white-space:nowrap}
.di-tbl td{padding:6px 10px;border-bottom:1px solid var(--border);color:var(--fg)}
.di-tbl td.di-v{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;border-left:1px solid var(--border);width:34%}
.di-nm a{color:var(--fg);text-decoration:none}
.di-nm a:hover{text-decoration:underline}
.di-hit{background:rgba(250,204,21,.32)}
.di-na td{color:var(--muted);text-align:center;font-size:12px;padding:12px 8px}
</style>
"""

# 종목명 검색 — 그 종목이 나온 날만 펼쳐 칸을 강조한다. 상태줄 기본 문구는
# 서버가 찍은 DOM 을 읽는다(JS 가 문구를 만들면 화면마다 갈라진다, #48).
_DI_JS = r"""
(function(){
  var q=document.getElementById('di-q'), st=document.getElementById('di-st');
  if(!q) return;
  var base=st?st.textContent:'';
  var days=[].slice.call(document.querySelectorAll('details.di-day'));
  var months=[].slice.call(document.querySelectorAll('details.di-month'));
  function norm(s){return String(s||'').toLowerCase().replace(/\s+/g,'');}
  function run(){
    var v=norm(q.value), hit=0;
    days.forEach(function(d){
      var any=false;
      [].slice.call(d.querySelectorAll('td.di-nm')).forEach(function(c){
        var m=!!v && norm(c.textContent).indexOf(v)>=0;
        if(m){c.classList.add('di-hit');any=true;}else{c.classList.remove('di-hit');}
      });
      if(!v){d.style.display='';d.open=d.getAttribute('data-open')==='1';return;}
      d.style.display=any?'':'none';
      if(any){d.open=true;hit++;}
    });
    months.forEach(function(m){
      if(!v){m.style.display='';m.open=m.getAttribute('data-open')==='1';return;}
      var vis=[].slice.call(m.querySelectorAll('details.di-day')).some(function(d){return d.style.display!=='none';});
      m.style.display=vis?'':'none';
      if(vis) m.open=true;
    });
    if(st) st.textContent=v?('"'+q.value+'" — '+hit+'일에 등장'):base;
  }
  q.addEventListener('input',run);
})();
"""

_WD = "월화수목금토일"


def _fmt_mcap(v: float) -> str:
    return f"{v / 1e12:.2f}조" if v >= 1e12 else f"{v / 1e8:,.0f}억"


def _row_html(r: dict) -> str:
    import html as _h
    tip = (f"순매수 {r['net'] / 1e8:+,.1f}억 · 시총 {_fmt_mcap(r['mcap'])}"
           f" · {r['t']}")
    nm = _h.escape(str(r.get("n") or r["t"]))
    return (f"<tr title='{_h.escape(tip)}'><td class='di-nm'>"
            f"<a href='lookup/{_h.escape(r['t'])}'>{nm}</a></td>"
            f"<td class='di-v'>{r['r']:.2f}%</td></tr>")


def _na_reason(rec: dict, key: str) -> str:
    if key == "turn":
        return f"직전 거래일({rec.get('prev') or '—'}) 자료가 없어 판정하지 않았습니다"
    miss = rec.get("streak_missing") or []
    if miss:
        return f"최근 5거래일 중 {len(miss)}일({', '.join(miss)}) 자료가 없어 판정하지 않았습니다"
    return "최근 5거래일 자료가 모자라 판정하지 않았습니다"


def _table_html(rec: dict, key: str, title: str, rows) -> str:
    import html as _h
    head = f"<thead><tr><th colspan='2'>{_h.escape(title)}</th></tr></thead>"
    if rows is None:
        body = (f"<tr class='di-na'><td colspan='2'>{_h.escape(_na_reason(rec, key))}"
                "</td></tr>")
    elif not rows:
        body = "<tr class='di-na'><td colspan='2'>해당 종목 없음</td></tr>"
    else:
        body = "".join(_row_html(r) for r in rows)
        if len(rows) < TOP_N:
            body += (f"<tr class='di-na'><td colspan='2'>조건에 맞는 종목이 "
                     f"{len(rows)}개뿐입니다</td></tr>")
    return f"<table class='di-tbl'>{head}<tbody>{body}</tbody></table>"


def _day_meta(rec: dict) -> str:
    got = _parse(rec.get("collected_at"))
    parts = []
    if got:
        parts.append(f"수집 {got.strftime('%m-%d %H:%M')}")
    rc = rec.get("recheck")
    if rc:
        at = _parse(rc.get("at"))
        when = at.strftime('%m-%d %H:%M') if at else '—'
        if rc.get("changed") is None:
            parts.append(f"재확인 {when} · 저녁 원자료가 없어 대조 못 함")
        elif not rc["changed"]:
            parts.append(f"재확인 {when} · 바뀐 종목 0")
        elif rc.get("value_changed", 1) and rc.get("max_abs_won"):
            parts.append(f"재확인 {when} · 바뀐 종목 {rc['changed']}"
                         f"(최대 {rc['max_abs_won'] / 1e8:,.1f}억)")
        else:
            parts.append(f"재확인 {when} · 바뀐 종목 {rc['changed']}(종목 추가·삭제만)")
    elif got and got < recheck_due_at(rec["date"]) and not rec.get("nodata"):
        parts.append(f"재확인 예정 {recheck_due_at(rec['date']).strftime('%m-%d %H:%M')} 이후")
    if rec.get("nodata"):
        return " · ".join(parts)
    parts.append(f"시총 확인 {rec.get('universe', 0):,}종목")
    if rec.get("mcap_missing"):
        parts.append(f"시총 못 찾음 {rec['mcap_missing']}종목 제외")
    return " · ".join(parts)


def _day_html(rec: dict, *, open_: bool) -> str:
    import html as _h
    d = datetime.fromisoformat(rec["date"])
    if rec.get("nodata"):
        body = (f"<div class='di-status'>KRX 자료 없음 — {_h.escape(str(rec['nodata']))}"
                f" · 이 화면이 이 날을 보여 주는 동안 {REOPEN_H}시간마다 다시 묻습니다</div>")
    else:
        tiers = []
        for t in rec.get("tiers") or []:
            tables = "".join(_table_html(rec, k, title, (t.get("lists") or {}).get(k))
                             for k, title in LISTS)
            # 'N종목' 은 그 구간에서 **KRX 가 기관 순매수 행을 준** 종목 수다 — 기관 거래가
            # 없던 종목도 행으로 오는지는 재지 않았다(`--probe` 가 행 수를 잰다, #165).
            tiers.append(
                f"<div class='di-tier'><div class='di-tier-h'>{_h.escape(t['label'])}"
                f"<span class='di-n'>KRX 기관 집계 {t.get('n', 0):,}종목 중 순매수 "
                f"{t.get('n_pos', 0):,}종목</span></div>"
                f"<div class='di-grid'>{tables}</div></div>")
        body = "".join(tiers)
    return (f"<details class='di-day' data-open='{1 if open_ else 0}'"
            f"{' open' if open_ else ''}><summary>📅 {rec['date']} "
            f"({_WD[d.weekday()]}) 기준<span class='di-meta'>{_h.escape(_day_meta(rec))}"
            f"</span></summary><div class='di-body'>{body}</div></details>")


_GEN = "\x00GEN\x00"   # 생성 시각 자리 — 내용 비교에서 빼려고 맨 마지막에 채운다


def render_page(records: list[dict], state: dict | None = None,
                now: datetime | None = None, *, gen: str | None = None) -> str:
    """daily_inst.html — 월 → 일 접힘(이번 달·최신일만 펼침, Daily Byte 와 같은 형식).
    최근 `RENDER_DAYS` 거래일만 싣고 그 앞은 보관만 한다(그 사실을 적는다, #45)."""
    import html as _h

    from bot.fred_boards import _BOARD_CSS, _theme_head
    now = _now(now)
    st = state or {}
    allrecs = sorted(records, key=lambda r: r["date"], reverse=True)
    recs = allrecs[:RENDER_DAYS]
    latest = recs[0]["date"] if recs else None          # 펼쳐 둘 맨 위 카드
    real = latest_real(allrecs)                         # 판정·통계는 받은 기록으로
    n_stub = sum(1 for r in allrecs if r.get("nodata"))
    kind, line = status_verdict(real, now, st)
    bh, bm = boundary_hm()
    last_ok = _parse(st.get("last_ok"))
    stats = (
        "<div class='stat-grid'>"
        f"<div class='stat'><div class='k'>최신 기준일</div><div class='v'>{real or '—'}</div></div>"
        f"<div class='stat'><div class='k'>기록{' (KRX 자료 없음 ' + str(n_stub) + '일 별도)' if n_stub else ''}"
        f"</div><div class='v'>{len(allrecs) - n_stub}거래일</div></div>"
        f"<div class='stat'><div class='k'>마지막 수집 (KST)</div><div class='v'>"
        f"{last_ok.strftime('%m-%d %H:%M') if last_ok else '—'}</div></div>"
        "</div>")
    by_month: dict[str, list[dict]] = {}
    for r in recs:
        by_month.setdefault(r["date"][:7], []).append(r)
    blocks = []
    for i, (ym, rs) in enumerate(by_month.items()):
        y, m = ym.split("-")
        days = "".join(_day_html(r, open_=(r["date"] == latest)) for r in rs)
        blocks.append(
            f"<details class='di-month' data-open='{1 if i == 0 else 0}'"
            f"{' open' if i == 0 else ''}><summary>📆 {int(y)}년 {int(m)}월 "
            f"<span class='di-n'>{len(rs)}거래일</span></summary>{days}</details>")
    body = "".join(blocks) or (
        "<div class='di-status'>아직 기록이 없습니다 — 매 거래일 "
        f"{bh:02d}:{bm:02d} 이후 자동으로 수집합니다.</div>")
    shown = (f"총 {len(allrecs)}거래일 기록" if len(allrecs) <= RENDER_DAYS else
             f"총 {len(allrecs)}거래일 기록 중 최근 {RENDER_DAYS}거래일 표시(그 앞은 보관만)")
    gen_s = now.strftime("%Y-%m-%d %H:%M KST") if gen is None else gen
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daily 기관매수</title>{_BOARD_CSS}{_DI_CSS}
{_theme_head()}
</head><body><div class="wrap">
<div class="nav"><a href="market.html">🌍 홈</a> · <a href="index.html">🦉 종목분석</a> · <a href="daily_byte.html">📰 Daily Byte</a> · <a href="dart_feed.html">📋 DART</a></div>
<h1>🏦 <em>Daily 기관매수</em> — 시총 대비 기관 순매수 TOP 10</h1>
<div class="sub">KRX 투자자별(기관합계) 종목별 순매수대금 ÷ 시가총액 · 코스피+코스닥 ·
매 거래일 {bh:02d}:{bm:02d} 이후 자동 집계 · 이튿날 {RECHECK_HOUR:02d}:00 이후 한 번 더 받아
대조 · 내용이 바뀐 시각 {_h.escape(gen_s)}</div>
<div class="di-status" data-kind="{kind}">{_h.escape(line)}</div>
{stats}
<details class="guide"><summary>ℹ️ 이 보드 읽는 법</summary>
<b>값</b> — 그날 <b>기관합계 순매수대금 ÷ 그날 시가총액</b>(%)입니다. 출처는 KRX
정보데이터시스템(pykrx)이고 우리가 하는 일은 나눗셈·정렬·구간 나누기뿐입니다.
종목명 위에 마우스를 올리면 순매수 금액·시가총액이 뜨고, 누르면 그 종목의 분석
화면으로 갑니다.<br>
<b>구간</b> — 기준일 종가 시가총액으로 <b>1조 이상 · 5천억~1조 · 5천억 미만</b>을
나눕니다. 구간마다 같은 값을 쓰므로 큰 회사의 0.3% 는 작은 회사의 0.3% 보다 훨씬
큰 금액입니다. 구간 제목 옆 "KRX 기관 집계 N종목" 은 그 구간에서 KRX 가 기관 순매수
행을 준 종목 수입니다(기관 거래가 없던 종목도 행으로 오는지는 재지 않았습니다).<br>
<b>세 목록</b> — ① <b>순매수 강도</b>: 기준일에 기관이 순매수한 종목을 값 순으로.
② <b>순매수 전환</b>: 직전 거래일엔 기관이 <b>순매도</b>했는데 기준일엔 순매수한
종목(직전 거래일이 0 이면 넣지 않습니다). ③ <b>5일 연속 순매수</b>: 기준일 포함
최근 5거래일 모두 순매수한 종목. 세 목록 모두 <b>기준일 값</b>으로 정렬하고 값도
기준일 값을 적습니다 — 그래서 같은 종목이 여러 목록에 같은 값으로 나올 수
있습니다.<br>
<b>해석이 들어간 지점</b> — 원래 표가 산식을 밝히지 않아 위 정의(구간 경계·전환의
'순매도 → 순매수'·5일 연속의 정렬 기준)는 <b>우리가 정한 것</b>입니다. 원래 표와
한두 종목 다를 수 있습니다. 거래소 우선주·스팩도 시가총액이 있으면 포함됩니다.<br>
<b>언제 갱신되나</b> — 2026년부터 KRX 에도 애프터마켓(~20:00)이 있어, 그 체결이
기관 집계에 들어가도 틀리지 않게 <b>{bh:02d}:{bm:02d} 이후</b>에만 그날을 받습니다.
KRX 가 정확히 언제 집계를 끝내는지는 재지 않았으므로, <b>이튿날 {RECHECK_HOUR:02d}:00
이후 한 번 더 받아</b> 저녁 값과 대조하고 바뀐 종목 수를 날짜 옆 "재확인" 에
적습니다(0 이면 저녁 값이 최종이었다는 뜻). 그날 시가총액은 나왔는데 기관 집계만
아직이면 굽지 않고 {WAIT_RETRY_MIN}분마다 다시 묻습니다.<br>
<b>휴장일·자료 없는 날</b> — 주말·공휴일은 기록이 없습니다. 달력엔 거래일인데 KRX 가
휴장으로 답한 날(시가총액이 전부 0)은 거래일에서 빼고, 시장은 열렸는데 기관 순매수만
여러 번 물어도 없던 날은 "KRX 자료 없음" 카드로 남깁니다.<br>
<b>표시 범위</b> — 최근 {RENDER_DAYS}거래일만 보여 주고 그 앞은 보관만 합니다.
</details>
<div class="di-search"><input id="di-q" type="text" placeholder="종목명 검색 (예: 스피어) — 등장한 날만 펼칩니다" autocomplete="off" spellcheck="false"></div>
<div id="di-st" class="sub">{shown}</div>
{body}
<div class="footer">Daily 기관매수 — KRX 수급 관찰(교육·정보), 투자 권유 아님 · NOAH</div>
</div>
<script>{_DI_JS}</script>
</body></html>"""


def _page_sig(html: str) -> str:
    import hashlib
    return hashlib.sha1(html.encode("utf-8")).hexdigest()


def regenerate() -> bool:
    """daily_inst.html 재생성(저장된 기록만 읽는다 — 네트워크 0). **내용이 바뀔 때만**
    쓴다 — 15분마다 생성 시각만 바뀐 같은 페이지를 쓰면 열어 둔 화면에 '새 업데이트'
    배너가 상시 뜨고 자동 새로고침이 펼쳐 둔 카드를 접는다(독립 리뷰 H2 · #25·#271).
    썼으면 True. 실패해도 기존 파일 유지."""
    try:
        from bot.dashboard import ARCHIVE_ROOT, _inject_update_banner
        recs, st = load_records(), load_state()
        now = _now()
        body = _inject_update_banner(render_page(recs, st, now, gen=_GEN))
        sig = _page_sig(body)
        out = ARCHIVE_ROOT / "daily_inst.html"
        sig_path = ARCHIVE_ROOT / "daily_inst.html.sig"
        try:
            same = out.exists() and sig_path.read_text(encoding="utf-8").strip() == sig
        except Exception:                                      # noqa: BLE001
            same = False
        if same:
            return False
        html = body.replace(_GEN, now.strftime("%Y-%m-%d %H:%M KST"))
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = ARCHIVE_ROOT / f"daily_inst.html.tmp{os.getpid()}"
        tmp.write_text(html, encoding="utf-8")
        os.replace(tmp, out)
        sig_path.write_text(sig, encoding="utf-8")
        return True
    except Exception as exc:                                   # noqa: BLE001
        log.warning("daily_inst: 페이지 재생성 실패: %s", exc)
        return False


_TICK_LOCK = threading.Lock()


def tick_and_render() -> dict:
    """봇 주기 태스크가 부르는 한 단위 — 수집 후 페이지 재생성.

    앞 주기가 아직 안 끝났으면 수집은 건너뛰고 **재생성만** 한다 — 화면의 판정 줄이 그
    시각에 굳지 않고 '수집이 끝나지 않음' 을 말하게(독립 리뷰 M6). 요청엔
    `_ensure_krx_timeout` 이 기본 timeout 을 달지만 못 다는 경로가 남는다(익명 세션 ·
    요청 도중의 재로그인 — 새 세션엔 다음 조회부터 붙는다). 다른 프로세스(`--run`)와도
    파일 잠금으로 한 번에 하나."""
    if not _TICK_LOCK.acquire(blocking=False):
        log.warning("daily_inst: 앞 주기가 아직 안 끝나 이번 수집은 건너뛴다")
        regenerate()
        return {"status": "busy", "reason": "앞 주기가 아직 안 끝남"}
    fh = None
    try:
        # 프로세스 사이(봇 ↔ `--run`)도 한 번에 하나 — 상태 파일을 서로 덮지 않게.
        try:
            import fcntl
            _DIR.mkdir(parents=True, exist_ok=True)
            fh = open(_DIR / ".tick.lock", "w")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.warning("daily_inst: 다른 프로세스가 수집 중 — 이번 수집은 건너뛴다")
            res = {"status": "busy", "reason": "다른 프로세스가 수집 중"}
        except Exception as exc:                               # noqa: BLE001
            log.debug("daily_inst: 파일 잠금 불가(%s) — 프로세스 내 잠금만 쓴다", exc)
            res = None
        else:
            res = None
        if res is None:
            try:
                res = tick()
            except Exception as exc:                           # noqa: BLE001
                log.exception("daily_inst: tick 실패")
                res = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        if fh is not None:
            fh.close()
        _TICK_LOCK.release()
    regenerate()
    if res.get("built") or res.get("rechecked") or res.get("holidays"):
        log.info("daily_inst: 기록 %s · 재확인 %s · 휴장 판정 %s · KRX %d콜",
                 res.get("built"), res.get("rechecked"), res.get("holidays"),
                 res.get("calls", 0))
    return res


# ── 진단 CLI ────────────────────────────────────────────────────────────────
_WHY_VER = 2   # 2026-10-12 독립 리뷰 반영(자료 없는 날·한 날짜 실패·집계 대기·멈춤)
_RUN_HINT = "cd ~/stock && .venv/bin/python -m bot.daily_inst --why"


def _src_sig() -> str:
    """이 파일의 소스 지문 — 진단 출력이 어느 코드에서 나왔는지(#364)."""
    import hashlib
    try:
        return hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:10]
    except Exception:                                          # noqa: BLE001
        return "지문불가"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception:                                          # noqa: BLE001
        pass


def why() -> int:
    """읽기 전용 — 아무것도 받지도 쓰지도 않는다(#264·#283). 최신(✅)·집계 대기(⏳)면 0."""
    now = _now()
    print(f"[Daily 기관매수 진단 v{_WHY_VER} · 소스 {_src_sig()}] "
          f"{now.strftime('%Y-%m-%d %H:%M KST')}")
    print(f"① 인터프리터: {sys.executable}")
    try:
        from bot.env_keys import env_source
        print("② KRX 자격증명(값은 안 찍습니다): "
              + " · ".join(f"{k}={env_source(k)}" for k in ("KRX_ID", "KRX_PW")))
    except Exception as exc:                                   # noqa: BLE001
        print(f"② 자격증명 판정 실패: {type(exc).__name__}")
    exp = settled_session(now)
    bh, bm = boundary_hm()
    print(f"③ 지금 받아야 할 마지막 거래일: {exp or '판정 불가(달력 없음)'}"
          f" · 그날을 요구하는 시각 {bh:02d}:{bm:02d} KST")
    recs = load_records()
    latest = latest_real(recs)
    n_stub = sum(1 for r in recs if r.get("nodata"))
    print(f"④ 저장된 기록: {len(recs) - n_stub}거래일(KRX 자료 없음 {n_stub}일 별도) · "
          f"최신 {latest or '없음'} · 위치 {_REC_DIR}")
    if recs:
        r = recs[-1]
        print(f"   최신 기록: {_day_meta(r)}")
        if r.get("nodata"):
            print(f"   KRX 자료 없음: {r['nodata']}")
        for t in r.get("tiers") or []:
            top = (t.get("lists") or {}).get("strength") or []
            head = ", ".join(f"{x['n']} {x['r']:.2f}%" for x in top[:3]) or "없음"
            print(f"   {t['label']}: {t.get('n', 0)}종목 · 순매수 {t.get('n_pos', 0)} · 강도 상위 {head}")
    st = load_state()
    print(f"⑤ 수집 상태: 마지막 시도 {st.get('last_try') or '—'} · 마지막 성공 "
          f"{st.get('last_ok') or '—'} · 연속 실패 {st.get('fails') or 0}"
          + (" · 집계 대기 중" if st.get("waiting") else "")
          + (f" · 다음 시도 {st.get('next_try')}" if st.get("next_try") else ""))
    if st.get("reason"):
        print(f"   사유: {st['reason']}")
    if _days_note(st):
        print(f"   한 날짜만 늦추는 중: {_days_note(st)}")
    rc = [r for r in recs if (r.get("recheck") or {}).get("changed") is not None]
    if rc:
        ch = sum(1 for r in rc if r["recheck"].get("changed"))
        print(f"⑥ 재확인: {len(rc)}거래일 중 저녁 값과 달랐던 날 {ch}일"
              + (" — 저녁 집계가 최종이 아니었던 날이 있다" if ch else
                 " — 지금까지는 저녁 집계가 최종이었다"))
    else:
        print("⑥ 재확인: 아직 대조한 날이 없다(이튿날 07:00 이후 생긴다)")
    kind, line = status_verdict(latest, now, st)
    print(f"판정: {line}")
    print(f"다시 보려면: {_RUN_HINT}")
    return 0 if kind in ("ok", "wait") else 1


def probe(date: str | None) -> int:
    """그 날짜를 지금 받아 보기만 한다(쓰지 않는다) — "오늘 집계가 벌써 나왔나" 와
    "KRX 가 기관 거래가 없던 종목도 행으로 주나" 를 잰다."""
    _load_env()
    now = _now()
    d = date or now.date().isoformat()
    print(f"[Daily 기관매수 probe · 소스 {_src_sig()}] {d} — 지금 "
          f"{now.strftime('%H:%M KST')} (아무것도 안 씁니다)")
    nets, why_n, kind_n = fetch_nets(d)
    mcap, why_m, kind_m = fetch_mcap(d)
    if mcap is None:
        print(f"{'⏸' if kind_m == 'holiday' else '❌'} 시가총액[{kind_m}]: {why_m}")
    else:
        print(f"✅ 시가총액 {len(mcap)}종목(코넥스 포함 전 시장)")
    if nets is None:
        print(f"❌ 기관 순매수[{kind_n}]: {why_n}")
        return 1
    for mkt, sfx in MARKETS:
        rows = [v for t, v in nets.items() if t.endswith(sfx)]
        zero = sum(1 for v in rows if not v[1])
        print(f"✅ {mkt} 기관 순매수 {len(rows)}행 · 순매수 {sum(1 for v in rows if v[1] > 0)}"
              f" · 0 인 행 {zero}(0 인 행이 있으면 기관 거래가 없던 종목도 행으로 온다)")
    if mcap:
        codes = {t.split(".")[0] for t in nets}
        print(f"   시가총액이 있는데 기관 순매수 행이 없는 종목: "
              f"{len([c for c in mcap if c not in codes])}(코넥스 포함)")
    return 0 if mcap else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING,
                        format="%(levelname)s:%(name)s:%(message)s")
    if "--probe" in args:
        i = args.index("--probe")
        nxt = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("-") else None
        return probe(nxt)
    if "--run" in args:
        _load_env()
        res = tick_and_render()
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("status") in ("ok", "idle", "budget", "waiting") else 1
    return why()


if __name__ == "__main__":
    raise SystemExit(main())
