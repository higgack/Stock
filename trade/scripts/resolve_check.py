"""미매칭 신규 관련종목 일일 점검 — 시세가 안 붙는(이름→코드 해석 실패) 종목을
운영자에게 하루 1회 DM으로 알린다.

설계(운영자 요청: "상장사는 자동으로 붙이고, 안 되는 신규만 알려줘"):
  - 관련종목은 워머가 이미 자동 해석·시세 부착(KRX 마스터 + data.go.kr). 이 스크립트는
    그 *나머지* — 끝내 안 붙는 종목 — 의 가시성만 담당.
  - 매일 1회(--if-due, 날짜 마커). dashboard-refresh의 마지막 패스로 매 틱 호출돼도
    하루 한 번만 실제 수행, 그 외 즉시 skip(build_krx_codes --if-stale와 동일 패턴).
  - durable seen-set과 비교해 **새로 안 붙기 시작한 것만** 운영자 DM(빈도순, cap).
  - **첫 실행 baseline-silent**: 현재 미매칭 전부를 seen으로 기록만 하고 알림 0
    (배포 직후 수십 건이 한꺼번에 쏟아지지 않게 — customs_scan 패턴).
  - 해석이 다시 되면 seen에서 빠짐 → 나중에 또 안 되면 재알림.
  - 신규 0이면 무음. 토큰/대상 없으면 silent skip(배포·워밍 안 깨짐).

상장사면 운영자가 코드(_DIRECT_CODES/_NAME_ALIASES) 추가를 요청, 비상장/외국/제품명
이면 그냥 무시하면 된다.

Run by hand:
    .venv/bin/python -m trade.scripts.resolve_check            # 강제 수행
    .venv/bin/python -m trade.scripts.resolve_check --if-due   # 오늘 안 했으면만
    .venv/bin/python -m trade.scripts.resolve_check --audit    # 렌더 기준 빈칸 전수
"""

import json
import logging
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from trade import price_provider as pp
from trade.store import list_all_alerts, open_db

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("resolve-check")

_KST = timezone(timedelta(hours=9))
_CAP = int(os.environ.get("TRADE_RESOLVE_ALERT_CAP") or "20")


def _data_dir() -> Path:
    return Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")


def _state_path() -> Path:
    return _data_dir() / "resolve_check.json"


def _store_db() -> Path:
    return _data_dir() / "store.db"


def _today() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d")


def _load_state() -> dict:
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        log.warning("state write failed: %s", e)


def _notify(text: str) -> None:
    """운영자 DM(블랙아웃 알림과 동일 경로). 토큰/대상 없으면 silent skip."""
    token = os.environ.get("TRADE_BOT_TOKEN")
    try:
        from trade import operator
        chat_id = operator.get()
    except Exception:
        chat_id = None
    if not token or not chat_id:
        log.info("notify skipped: no TRADE_BOT_TOKEN / operator chat_id")
        return
    try:
        subprocess.run(
            ["curl", "-s", "-m", "10", "-X", "POST",
             f"https://api.telegram.org/bot{token}/sendMessage",
             "--data-urlencode", f"chat_id={chat_id}",
             "--data-urlencode", f"text={text}",
             "--data-urlencode", "parse_mode=HTML"],
            timeout=15, check=False, capture_output=True,
        )
    except Exception as e:
        log.warning("notify failed: %s", e)


def run(if_due: bool = False) -> int:
    # 이름→코드 해석 수단이 전혀 없으면(마스터도 키도 없음) 전수 미매칭으로 오인
    # 알림하지 않게 skip — 설정 문제지 미상장이 아님.
    if not pp._KRX_MASTER.exists() and not os.environ.get("TRADE_DATA_GO_KR_KEY"):
        log.info("no KRX master and no data.go.kr key — skip")
        return 0
    db = _store_db()
    if not db.exists():
        log.info("no store.db at %s — skip", db)
        return 0

    state = _load_state()
    if if_due and state.get("day") == _today():
        log.info("already ran today (%s) — skip", state["day"])
        return 0

    conn = open_db(db)
    try:
        alerts = list_all_alerts(conn)
    finally:
        conn.close()

    freq: Counter = Counter()
    for a in alerts:
        for n in pp.split_names(a.get("stocks") or []):
            freq[n] += 1
    names = list(freq)
    resolved = pp.resolve_codes(names, fetch=True)
    unresolved = [n for n in names if n not in resolved]

    seen = set(state.get("seen", []))
    first_run = "seen" not in state
    new = [n for n in unresolved if n not in seen]
    # 빈도 높은 순(실제 회사일수록 여러 알림에 반복) — 운영자 트리아지 도움
    new.sort(key=lambda n: (-freq[n], n))

    if first_run:
        log.info("baseline: recorded %d unresolved names (silent, no alert)",
                 len(unresolved))
    elif new:
        shown = new[:_CAP]
        body = ", ".join(f"{n}({freq[n]}회)" for n in shown)
        more = f" 외 {len(new) - len(shown)}개" if len(new) > len(shown) else ""
        _notify(
            f"🔎 <b>시세 미매칭 신규 관련종목 {len(new)}개</b>\n{body}{more}\n"
            f"<i>상장사면 코드 추가 요청, 비상장/외국/제품명이면 무시</i>")
        log.info("alerted %d new unresolved names", len(new))
    else:
        log.info("no new unresolved names — silent")

    _save_state({"day": _today(), "seen": sorted(unresolved)})
    return 0


def audit() -> int:
    """운영자 수동 전수 점검 — '대시보드에 시세가 안 붙는 회사'를 분류 출력.

    일일 DM(run)이 이름→코드 해석 실패(①)만 보는 것과 달리, **대시보드 렌더가
    HTML에 박는 바로 그 함수**(dashboard._stock_quotes_for)를 직접 호출해 실제
    표시 여부로 판정한다. 손으로 짠 일회성 진단이 ttl/캡 기본값을 잘못 먹여
    오도하던 문제를 없애려는 것 — 이 명령이 '정답본'이다. 두 부류로 나눔:
      ■ 코드 자체가 없음 — 비상장·외국·상폐·표기불일치(별칭/프록시 후보 or 무시)
      ● 코드는 있는데 KIS 시세 없음 — 상폐/미커버/일시(주목 대상)
    """
    from trade import dashboard
    db = _store_db()
    if not db.exists():
        log.info("no store.db at %s — skip", db)
        return 0
    conn = open_db(db)
    try:
        alerts = list_all_alerts(conn)
    finally:
        conn.close()
    freq: Counter = Counter()
    for a in alerts:
        for n in pp.split_names(a.get("stocks") or []):
            freq[n] += 1
    names = list(freq)
    if not pp.provider_active():
        # 키 미설정이면 qmap이 전부 비어 '전 종목 빈칸'으로 오독됨 — 명시 경고.
        print("⚠️ 시세 공급자 OFF(키 없음) — 시세박힘이 0으로 나옴. "
              "● 분류는 무의미, ■(코드 없음)만 참고.")
    qmap = dashboard._stock_quotes_for(alerts)          # 렌더가 박는 {이름: {p,c}}
    resolved = pp.resolve_codes(names, fetch=False)     # 이름→코드(외부 호출 0)
    blank = [n for n in names if n not in qmap]
    no_code = sorted((n for n in blank if n not in resolved), key=lambda n: (-freq[n], n))
    no_quote = sorted((n for n in blank if n in resolved), key=lambda n: (-freq[n], n))
    print(f"시세박힘 {len(qmap)} / 전체 {len(names)} / 빈칸 {len(blank)}")
    print(f"\n■ 코드 없음 ({len(no_code)}) — 비상장·외국·상폐·표기불일치:")
    for n in no_code:
        print(f"   {n} ({freq[n]}회)")
    print(f"\n● 코드는 있는데 KIS 시세 없음 ({len(no_quote)}) — 주목:")
    for n in no_quote:
        print(f"   {n} [{resolved[n]}] ({freq[n]}회)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--audit" in argv:
        return audit()
    return run(if_due="--if-due" in argv)


if __name__ == "__main__":
    sys.exit(main())
