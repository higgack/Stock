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
를 사람이 아니라 기록이 답한다. 아직 집계 전인 응답(빈 표·전 종목 0)은 굽지 않고
백오프로 다시 묻는다(#280 부분을 완전본으로 굽지 않는다).

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
    r = _read_json(_raw_path(date))
    if isinstance(r, dict) and isinstance(r.get("nets"), dict) and r.get("date") == date:
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
    if rec.get("recheck"):
        return False
    due = recheck_due_at(rec["date"])
    got = _parse(rec.get("collected_at"))
    return bool(got and got < due <= _now(now))


# ── 원천 ────────────────────────────────────────────────────────────────────
def _pykrx_stock():
    from pykrx import stock
    return stock


def _col(df, *keys):
    for c in df.columns:
        if all(k in str(c) for k in keys):
            return c
    return None


def fetch_nets(date: str) -> tuple[dict | None, str]:
    """{티커(.KS/.KQ): [종목명, 기관 순매수대금(원)]} — KOSPI·KOSDAQ 각 1콜.

    한 시장이라도 비거나 전 종목 0 이면 **통째로** 실패다(#280 — 반쪽을 완전본으로
    굽지 않는다). 사유는 갈래를 이름으로 말한다(#82)."""
    ymd = date.replace("-", "")
    try:
        stock = _pykrx_stock()
    except Exception as exc:                                   # noqa: BLE001
        return None, f"pykrx 없음({type(exc).__name__})"
    out: dict = {}
    for mkt, sfx in MARKETS:
        try:
            df = stock.get_market_net_purchases_of_equities(ymd, ymd, mkt, INVESTOR)
        except Exception as exc:                               # noqa: BLE001
            return None, f"{mkt} 조회 실패({type(exc).__name__}: {str(exc)[:80]})"
        if df is None or len(df) == 0:
            return None, f"{mkt} 응답이 비었다 — 아직 집계 전이거나 KRX 가 답하지 않음"
        net_col = _col(df, "순매수", "대금")
        if net_col is None:
            return None, (f"{mkt} 응답에 순매수거래대금 열이 없다(열: "
                          f"{', '.join(str(c) for c in list(df.columns)[:8])})")
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
            return None, f"{mkt} 전 종목 순매수 0({got}행) — 아직 집계 전으로 보임"
    return out, ""


def fetch_mcap(date: str) -> tuple[dict | None, str]:
    """{6자리 코드: 시가총액(원)} — 전 시장 1콜. 전부 0 이면 실패(휴장·집계 전)."""
    ymd = date.replace("-", "")
    try:
        stock = _pykrx_stock()
    except Exception as exc:                                   # noqa: BLE001
        return None, f"pykrx 없음({type(exc).__name__})"
    try:
        df = stock.get_market_cap_by_ticker(ymd, market="ALL")
    except Exception as exc:                                   # noqa: BLE001
        return None, f"시가총액 조회 실패({type(exc).__name__}: {str(exc)[:80]})"
    if df is None or len(df) == 0:
        return None, "시가총액 응답이 비었다"
    col = _col(df, "시가총액")
    if col is None:
        return None, "시가총액 응답에 시가총액 열이 없다"
    out = {}
    for tkr in df.index:
        try:
            v = float(df.loc[tkr, col])
        except Exception:                                      # noqa: BLE001
            continue
        if v == v and v > 0:
            out[str(tkr).zfill(6)] = v
    if not out:
        return None, "시가총액이 전부 0 — 휴장일이거나 아직 집계 전"
    return out, ""


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


def diff_nets(old: dict, new: dict) -> dict:
    """재확인 대조 — 바뀐(추가·삭제 포함) 종목 수와 가장 큰 차이(원)."""
    keys = set(old) | set(new)
    changed, max_abs = 0, 0.0
    for k in keys:
        a = old.get(k, [None, None])[1]
        b = new.get(k, [None, None])[1]
        if a != b:
            changed += 1
            if a is not None and b is not None:
                max_abs = max(max_abs, abs(float(a) - float(b)))
    return {"changed": changed, "compared": len(keys), "max_abs_won": int(max_abs)}


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
    """이번 주기를 멈춘다 — reason 이 있으면 실패(백오프), 없으면 예산 소진.
    `date` 는 **실패한 원자료의 날짜**다 — 기준일 기록을 만들다 앞 거래일이 실패한
    것과 기준일 자체가 집계 전인 것은 다른 사실이다(#82)."""

    def __init__(self, reason: str = "", date: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.date = date


def _nets_for(date: str, budget: _Budget, now: datetime) -> dict:
    raw = load_raw(date)
    if raw:
        return raw["nets"]
    if not budget.take(2):
        raise _Stop()
    nets, why = fetch_nets(date)
    if nets is None:
        raise _Stop(f"{date} 기관 순매수 — {why}", date)
    _write_json(_raw_path(date), {"date": date, "fetched_at": _iso(now), "nets": nets})
    return nets


def _mcap_for(date: str, budget: _Budget, now: datetime) -> dict:
    raw = load_raw(date) or {}
    if isinstance(raw.get("mcap"), dict) and raw["mcap"]:
        return raw["mcap"]
    if not budget.take(1):
        raise _Stop()
    mcap, why = fetch_mcap(date)
    if mcap is None:
        raise _Stop(f"{date} 시가총액 — {why}", date)
    raw = load_raw(date)
    if raw:
        raw["mcap"] = mcap
        _write_json(_raw_path(date), raw)
    return mcap


def _build_for(date: str, sessions: list[str], budget: _Budget, now: datetime) -> dict:
    idx = sessions.index(date)
    window = sessions[max(0, idx - STREAK_N + 1): idx + 1]
    # 기준일을 먼저 — 아직 집계 전이면 앞 날짜를 받느라 예산을 쓰지 않는다.
    nets_by_date = {date: _nets_for(date, budget, now)}
    mcap = _mcap_for(date, budget, now)
    for d in window[:-1]:
        nets_by_date[d] = _nets_for(d, budget, now)
    rec = build_record(date, sessions, nets_by_date, mcap, collected_at=_iso(now))
    cover = rec["universe"] / max(1, rec["net_rows"])
    if cover < MIN_MCAP_COVER:
        raise _Stop(f"{date} 시가총액을 찾은 종목이 {rec['universe']}/{rec['net_rows']}"
                    f"({cover:.0%}) — 반쪽 표는 굽지 않는다")
    _write_json(record_path(date), rec)
    return rec


def _recheck(rec: dict, sessions: list[str], budget: _Budget, now: datetime) -> dict:
    date = rec["date"]
    old = load_raw(date)
    if not budget.take(2):
        raise _Stop()
    nets, why = fetch_nets(date)
    if nets is None:
        raise _Stop(f"{date} 재확인 — {why}")
    # 저녁 원자료가 없으면 대조할 수 없다 — 전 종목을 '바뀜' 으로 세면 거짓이다(#54).
    d = (diff_nets(old["nets"], nets) if old
         else {"changed": None, "compared": 0, "max_abs_won": 0})
    raw = dict(old or {"date": date})
    raw.update({"date": date, "nets": nets, "rechecked_at": _iso(now)})
    _write_json(_raw_path(date), raw)
    mcap = raw.get("mcap") or _mcap_for(date, budget, now)
    nets_by_date = {date: nets}
    for x in rec.get("window") or []:
        if x != date:
            r = load_raw(x)
            if r:
                nets_by_date[x] = r["nets"]
    new = build_record(date, sessions, nets_by_date, mcap,
                       collected_at=rec.get("collected_at", ""))
    new["recheck"] = {"at": _iso(now), **d}
    _write_json(record_path(date), new)
    return new


def _save_state(st: dict) -> None:
    try:
        _write_json(_STATE, st)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("daily_inst: 상태 저장 실패: %s", exc)


def _fail(st: dict, now: datetime, reason: str) -> None:
    n = int(st.get("fails") or 0) + 1
    wait = _BACKOFF_MIN[min(n, len(_BACKOFF_MIN)) - 1]
    st.update({"fails": n, "reason": reason,
               "next_try": _iso(now + timedelta(minutes=wait))})
    log.warning("daily_inst: %s — %d분 뒤 다시 시도(연속 %d회)", reason, wait, n)


def tick(*, now: datetime | None = None, budget: int = CALL_BUDGET) -> dict:
    """한 주기 — 받아야 할 기록(재확인 → 빠진 날 최신부터)을 예산 안에서 처리.

    반환 {"built": [...], "rechecked": [...], "status": "ok|budget|fail|backoff|idle",
    "reason": "", "calls": n}. 상태는 디스크에 남겨 화면·진단이 읽는다(별도
    프로세스는 이 모듈의 메모리를 못 본다, #343)."""
    now = _now(now)
    st = load_state()
    res = {"built": [], "rechecked": [], "status": "idle", "reason": "", "calls": 0}
    nt = _parse(st.get("next_try"))
    if nt and now < nt:
        res.update(status="backoff", reason=st.get("reason", ""))
        return res
    st["last_try"] = _iso(now)
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
        _save_state(st)
        res.update(status="fail", reason=st["reason"])
        return res
    target = settled_session(now)
    sessions = recent_sessions(target, KEEP_SESSIONS + STREAK_N - 1) if target else None
    if not sessions:
        _fail(st, now, "거래일 달력(exchange_calendars)이 없어 받을 날짜를 정할 수 없다")
        _save_state(st)
        res.update(status="fail", reason=st["reason"])
        return res
    want = sessions[-KEEP_SESSIONS:]
    b = _Budget(budget)
    failed, out_of_budget = "", False
    try:
        for d in reversed(want[-2:]):
            rec = load_record(d)
            if rec and needs_recheck(rec, now):
                _recheck(rec, sessions, b, now)
                res["rechecked"].append(d)
        for d in reversed(want):
            if load_record(d):
                continue
            try:
                _build_for(d, sessions, b, now)
                res["built"].append(d)
            except _Stop as stop:
                # 기준일(가장 최근) **자체**가 아직 집계 전이어도 지난 거래일은 이미
                # 확정이다 — 그 하나 때문에 빈 날 메우기를 막지 않는다(#171). 지난
                # 거래일이 실패했으면(기준일 기록을 만들다 앞날 원자료가 실패한 것
                # 포함) 원천 장애로 보고 멈춘다 — 같은 실패에 호출만 쓴다(#279).
                if not stop.reason or failed or stop.date != want[-1]:
                    raise
                failed = stop.reason
    except _Stop as stop:
        if stop.reason:
            # 앞의 '기준일 집계 전' 과 뒤의 실패를 둘 다 남긴다 — 뒤엣것이 원천
            # 장애라면 그게 더 행동 가능한 사유다(#275·#350).
            failed = (f"{failed} · 이어서 {stop.reason}"
                      if failed and failed != stop.reason else stop.reason)
        else:
            out_of_budget = True
    res["calls"] = b.used
    if res["built"] or res["rechecked"]:
        st["last_ok"] = _iso(now)
    if failed:
        _fail(st, now, failed)
        res.update(status="fail", reason=failed)
    else:
        st.update({"fails": 0, "reason": "", "next_try": None})
        res["status"] = ("budget" if out_of_budget
                         else "ok" if res["built"] or res["rechecked"] else "idle")
        if not out_of_budget:
            _prune_raw(now)
    _save_state(st)
    return res


# ── 판정(순수) ──────────────────────────────────────────────────────────────
def status_verdict(latest: str | None, now: datetime | None = None,
                   state: dict | None = None) -> tuple[str, str]:
    """(갈래, 한 줄) — 갈래 ok·wait·behind·none·unknown. 화면·감사·진단이 같은
    함수를 쓴다(#35·#38). `wait` 는 그날 경계 뒤 `GRACE_MIN` 안(수집 주기가 아직
    한 번도 안 돌았을 수 있다) — 늘 뜨는 경보는 아무것도 안 잰다(#25·#260)."""
    now = _now(now)
    st = state or {}
    exp = settled_session(now)
    bh, bm = boundary_hm()
    tail = ""
    if st.get("reason"):
        nxt = _parse(st.get("next_try"))
        tail = f" — 사유: {st['reason']}" + (
            f" · 다음 시도 {nxt.strftime('%m-%d %H:%M')}" if nxt else "")
    if exp is None:
        return "unknown", "❓ 거래일 달력이 없어 최신 여부를 판정할 수 없습니다"
    if not latest:
        return "none", "아직 기록이 없습니다" + (tail or " — 첫 수집을 기다리는 중")
    from bot import market_calendar as mc
    behind = mc.sessions_behind("KR", latest, exp)
    if behind is None:
        return "unknown", f"❓ {latest} 기록이 최신인지 판정할 수 없습니다(달력 조회 실패)"
    if behind == 0:
        return "ok", f"✅ 최신 — {exp} 장 마감 집계까지 반영"
    ready_at = datetime.fromisoformat(exp).replace(tzinfo=_KST, hour=bh, minute=bm)
    if behind == 1 and now < ready_at + timedelta(minutes=GRACE_MIN):
        return "wait", f"⏳ {exp} 집계를 받는 중 — 매 거래일 {bh:02d}:{bm:02d} 이후 수집"
    return "behind", (f"⚠️ {exp} 기록이 아직 없습니다(마지막 기록 {latest} · "
                      f"{behind}거래일 뒤처짐){tail}")


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
        else:
            parts.append(f"재확인 {when} · 바뀐 종목 {rc['changed']}"
                         + (f"(최대 {rc.get('max_abs_won', 0) / 1e8:,.1f}억)"
                            if rc["changed"] else ""))
    elif got and got < recheck_due_at(rec["date"]):
        parts.append(f"재확인 예정 {recheck_due_at(rec['date']).strftime('%m-%d %H:%M')} 이후")
    parts.append(f"시총 확인 {rec.get('universe', 0):,}종목")
    if rec.get("mcap_missing"):
        parts.append(f"시총 못 찾음 {rec['mcap_missing']}종목 제외")
    return " · ".join(parts)


def _day_html(rec: dict, *, open_: bool) -> str:
    import html as _h
    d = datetime.fromisoformat(rec["date"])
    tiers = []
    for t in rec.get("tiers") or []:
        tables = "".join(_table_html(rec, k, title, (t.get("lists") or {}).get(k))
                         for k, title in LISTS)
        tiers.append(
            f"<div class='di-tier'><div class='di-tier-h'>{_h.escape(t['label'])}"
            f"<span class='di-n'>{t.get('n', 0):,}종목 중 기관 순매수 "
            f"{t.get('n_pos', 0):,}종목</span></div>"
            f"<div class='di-grid'>{tables}</div></div>")
    return (f"<details class='di-day' data-open='{1 if open_ else 0}'"
            f"{' open' if open_ else ''}><summary>📅 {rec['date']} "
            f"({_WD[d.weekday()]}) 기준<span class='di-meta'>{_h.escape(_day_meta(rec))}"
            f"</span></summary><div class='di-body'>{''.join(tiers)}</div></details>")


def render_page(records: list[dict], state: dict | None = None,
                now: datetime | None = None) -> str:
    """daily_inst.html — 월 → 일 접힘(이번 달·최신일만 펼침, Daily Byte 와 같은 형식)."""
    import html as _h

    from bot.fred_boards import _BOARD_CSS, _theme_head
    now = _now(now)
    st = state or {}
    recs = sorted(records, key=lambda r: r["date"], reverse=True)
    latest = recs[0]["date"] if recs else None
    kind, line = status_verdict(latest, now, st)
    bh, bm = boundary_hm()
    last_ok = _parse(st.get("last_ok"))
    stats = (
        "<div class='stat-grid'>"
        f"<div class='stat'><div class='k'>최신 기준일</div><div class='v'>{latest or '—'}</div></div>"
        f"<div class='stat'><div class='k'>기록</div><div class='v'>{len(recs)}거래일</div></div>"
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
    gen = now.strftime("%Y-%m-%d %H:%M KST")
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daily 기관매수</title>{_BOARD_CSS}{_DI_CSS}
{_theme_head()}
</head><body><div class="wrap">
<div class="nav"><a href="market.html">🌍 홈</a> · <a href="index.html">🦉 종목분석</a> · <a href="daily_byte.html">📰 Daily Byte</a> · <a href="dart_feed.html">📋 DART</a></div>
<h1>🏦 <em>Daily 기관매수</em> — 시총 대비 기관 순매수 TOP 10</h1>
<div class="sub">KRX 투자자별(기관합계) 종목별 순매수대금 ÷ 시가총액 · 코스피+코스닥 ·
매 거래일 {bh:02d}:{bm:02d} 이후 자동 집계 · 이튿날 {RECHECK_HOUR:02d}:00 이후 한 번 더 받아
대조 · 페이지 생성 {_h.escape(gen)}</div>
<div class="di-status" data-kind="{kind}">{_h.escape(line)}</div>
{stats}
<details class="guide"><summary>ℹ️ 이 보드 읽는 법</summary>
<b>값</b> — 그날 <b>기관합계 순매수대금 ÷ 그날 시가총액</b>(%)입니다. 출처는 KRX
정보데이터시스템(pykrx)이고 우리가 하는 일은 나눗셈·정렬·구간 나누기뿐입니다.
종목명 위에 마우스를 올리면 순매수 금액·시가총액이 뜨고, 누르면 그 종목의 분석
화면으로 갑니다.<br>
<b>구간</b> — 기준일 종가 시가총액으로 <b>1조 이상 · 5천억~1조 · 5천억 미만</b>을
나눕니다. 구간마다 같은 값을 쓰므로 큰 회사의 0.3% 는 작은 회사의 0.3% 보다 훨씬
큰 금액입니다.<br>
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
적습니다(0 이면 저녁 값이 최종이었다는 뜻). 아직 집계 전이면 굽지 않고 15분 뒤
다시 묻습니다.<br>
<b>휴장일</b> — 주말·공휴일은 기록이 없습니다(거래일만 쌓입니다).
</details>
<div class="di-search"><input id="di-q" type="text" placeholder="종목명 검색 (예: 스피어) — 등장한 날만 펼칩니다" autocomplete="off" spellcheck="false"></div>
<div id="di-st" class="sub">총 {len(recs)}거래일 기록</div>
{body}
<div class="footer">Daily 기관매수 — KRX 수급 관찰(교육·정보), 투자 권유 아님 · NOAH</div>
</div>
<script>{_DI_JS}</script>
</body></html>"""


def regenerate() -> None:
    """daily_inst.html 재생성(저장된 기록만 읽는다 — 네트워크 0). 실패해도 기존 파일 유지."""
    try:
        from bot.dashboard import ARCHIVE_ROOT, _inject_update_banner
        html = _inject_update_banner(render_page(load_records(), load_state()))
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = ARCHIVE_ROOT / "daily_inst.html.tmp"
        tmp.write_text(html, encoding="utf-8")
        os.replace(tmp, ARCHIVE_ROOT / "daily_inst.html")
    except Exception as exc:                                   # noqa: BLE001
        log.warning("daily_inst: 페이지 재생성 실패: %s", exc)


def tick_and_render() -> dict:
    """봇 주기 태스크가 부르는 한 단위 — 수집(예외는 기록) 후 페이지 재생성."""
    try:
        res = tick()
    except Exception as exc:                                   # noqa: BLE001
        log.exception("daily_inst: tick 실패")
        res = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    regenerate()
    if res.get("built") or res.get("rechecked"):
        log.info("daily_inst: 기록 %s · 재확인 %s · KRX %d콜", res.get("built"),
                 res.get("rechecked"), res.get("calls", 0))
    return res


# ── 진단 CLI ────────────────────────────────────────────────────────────────
_WHY_VER = 1
_RUN_HINT = "cd ~/stock && .venv/bin/python -m bot.daily_inst --why"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path.home() / "stock" / ".env")
    except Exception:                                          # noqa: BLE001
        pass


def why() -> int:
    """읽기 전용 — 아무것도 받지도 쓰지도 않는다(#264·#283)."""
    now = _now()
    print(f"[Daily 기관매수 진단 v{_WHY_VER}] {now.strftime('%Y-%m-%d %H:%M KST')}")
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
    latest = recs[-1]["date"] if recs else None
    print(f"④ 저장된 기록: {len(recs)}거래일 · 최신 {latest or '없음'} · 위치 {_REC_DIR}")
    if recs:
        r = recs[-1]
        print(f"   최신 기록: {_day_meta(r)}")
        for t in r.get("tiers") or []:
            top = (t.get("lists") or {}).get("strength") or []
            head = ", ".join(f"{x['n']} {x['r']:.2f}%" for x in top[:3]) or "없음"
            print(f"   {t['label']}: {t.get('n', 0)}종목 · 순매수 {t.get('n_pos', 0)} · 강도 상위 {head}")
    st = load_state()
    print(f"⑤ 수집 상태: 마지막 시도 {st.get('last_try') or '—'} · 마지막 성공 "
          f"{st.get('last_ok') or '—'} · 연속 실패 {st.get('fails') or 0}"
          + (f" · 다음 시도 {st.get('next_try')}" if st.get("next_try") else ""))
    if st.get("reason"):
        print(f"   사유: {st['reason']}")
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
    return 0 if kind in ("ok", "wait") else 1


def probe(date: str | None) -> int:
    """그 날짜를 지금 받아 보기만 한다(쓰지 않는다) — "오늘 집계가 벌써 나왔나" 를 잰다."""
    _load_env()
    now = _now()
    d = date or now.date().isoformat()
    print(f"[Daily 기관매수 probe] {d} — 지금 {now.strftime('%H:%M KST')} (아무것도 안 씁니다)")
    nets, why_n = fetch_nets(d)
    if nets is None:
        print(f"❌ 기관 순매수: {why_n}")
        return 1
    pos = sum(1 for v in nets.values() if v[1] > 0)
    print(f"✅ 기관 순매수 {len(nets)}종목(순매수 {pos}) · 합 {sum(v[1] for v in nets.values()) / 1e8:+,.0f}억")
    mcap, why_m = fetch_mcap(d)
    if mcap is None:
        print(f"❌ 시가총액: {why_m}")
        return 1
    print(f"✅ 시가총액 {len(mcap)}종목")
    return 0


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
        return 0 if res.get("status") in ("ok", "idle", "budget") else 1
    return why()


if __name__ == "__main__":
    raise SystemExit(main())
