"""이 **프로세스**가 디스크의 코드보다 낡았나 — 배포 drift 를 재는 단일 출처.

`market.html` 은 봇 프로세스(`stock-bot`)가 **정적 파일로 굽고**,
`/api/*` 는 대시보드 프로세스(`stock-bot-dashboard`)가 답한다. 두 유닛은
`deploy/auto-update.sh` 에서 **따로** 재시작되고, 대시보드 재시작은
`sudo -n systemctl restart stock-bot-dashboard` 권한에 달려 있다. 그 권한이
없으면 봇만 갱신돼 — **새 HTML + 옛 API** 라는 조합이 만들어진다.

2026-09-12 실측 증상: 관심종목에 ★ 열은 그려지는데(새 HTML) 누르면
`중요표시 변경 실패` 가 떴다. 화면은 실패 사유를 넷으로 뭉뚱그려
(404·401·네트워크·서버 거절) 어느 쪽인지 말하지 못했고, 처방은 전부 다르다
(#82 갈래는 이름으로 · #11 '배포완료 ≠ 화면에 보임').

판정은 하나다 — **프로세스 시작 시각 < 소스 최신 mtime** 이면 그 프로세스는
옛 코드를 들고 있다. 파일이 아니라 **프로세스**를 재는 것이 핵심이다(같은
체크아웃을 두 프로세스가 공유하므로 파일 mtime 만으론 갈리지 않는다).

⚠️ 배포 직후 몇 초는 정상적으로 어긋난다(봇이 먼저, 대시보드가 몇 초 뒤).
유예를 두지 않으면 **늘 뜨는 배지**가 되어 아무것도 안 재는 것과 같다
(#25·#260). 그리고 유예 안이라도 **사실과 폭은 그대로 말한다**(#41).
"""
from __future__ import annotations

import time
from pathlib import Path


def _proc_start() -> float:
    """이 **프로세스**가 실제로 뜬 시각(epoch). 못 재면 0.0.

    ⚠️ 처음엔 `_STARTED = time.time()`(모듈 import 시각)으로 뒀는데, 호출부가
    이 모듈을 **핸들러 안에서 지연 import** 한다 — 그러면 배포 직후 첫 요청이
    시각을 찍으므로 `시작 > 소스 mtime` 이 되어 **감지하려던 바로 그 상태에서
    'fresh' 라고 답한다**(자기검토 2026-09-12 · #91b 가드가 재는 대상을 틀리면
    그냥 눈이 먼다 · #79 그 경로가 실제로 실행됐나).

    상태는 **아는 쪽에 묻는다**(#86): 리눅스는 `/proc/self/stat` 22번 필드가
    부팅 후 tick 수이고 `/proc/stat` 의 `btime` 이 부팅 시각이다. 못 읽는
    환경(비리눅스)에선 import 시각으로 떨어지되 그건 근사임을 밝힌다.
    """
    try:
        with open("/proc/self/stat", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        # comm(2번 필드)에 공백·괄호가 들어갈 수 있다 — **마지막** ')' 뒤부터
        # 자른다(앞에서 자르면 프로세스 이름에 ')' 가 있을 때 어긋난다).
        rest = raw[raw.rindex(")") + 2:].split()
        ticks = float(rest[19])            # 3번 필드부터 세어 22번 = index 19
        import os as _os
        hz = float(_os.sysconf("SC_CLK_TCK")) or 100.0
        with open("/proc/stat", encoding="utf-8") as f:
            for line in f:
                if line.startswith("btime "):
                    return float(line.split()[1]) + ticks / hz
    except Exception:                                          # noqa: BLE001
        return 0.0
    return 0.0


# 진짜 프로세스 시작 시각 — 못 재면 모듈 import 시각으로 떨어진다.
_IMPORTED = time.time()
_STARTED = _proc_start() or _IMPORTED

# 이 초 안의 어긋남은 배포 순서 때문일 수 있다 — 그 아래는 '옛 코드'라고
# 단정하지 않는다(#165 재지 않은 것을 단정하지 말 것).
GRACE_SEC = 180


def process_started() -> float:
    """이 프로세스가 **뜬** 시각(epoch) — `/proc` 을 못 읽으면 import 시각 근사."""
    return _STARTED


def newest_source_mtime(pkg_dir=None) -> float:
    """`bot/*.py` 중 가장 최근 수정시각. 못 읽으면 0.0.

    ⚠️ 이름을 열거하지 않는다 — 목록형 가드는 목록 밖 파일을 못 잡는다(#24).
    `dashboard_server._max_py_mtime` 과 같은 규약이지만 여기는 **의존이 없는
    최하위 계층**이라 감사·헬스·페이지가 순환 없이 쓸 수 있다.
    """
    import os as _os

    d = Path(pkg_dir) if pkg_dir else Path(__file__).resolve().parent
    newest = 0.0
    try:
        with _os.scandir(d) as it:
            for e in it:
                if e.is_file() and e.name.endswith(".py"):
                    try:
                        newest = max(newest, e.stat().st_mtime)
                    except OSError:
                        pass
    except OSError:
        return 0.0
    return newest


def drift(*, started: float | None = None, newest: float | None = None,
          grace: float = GRACE_SEC) -> dict:
    """{'stale', 'lag_sec', 'started', 'newest', 'measurable'} (순수 — 인자 주입).

    `measurable=False` 는 소스 mtime 을 못 읽은 경우다 — **'신선하다'가 아니라
    판정 불가**다(#54 대조 0건은 통과가 아니다).
    """
    st = float(process_started() if started is None else started)
    nw = float(newest_source_mtime() if newest is None else newest)
    if nw <= 0:
        return {"stale": False, "measurable": False, "lag_sec": 0.0,
                "started": st, "newest": nw}
    lag = nw - st
    return {"stale": bool(lag > grace), "measurable": True,
            "lag_sec": lag, "started": st, "newest": nw}


def _mins(sec: float) -> str:
    m = int(abs(sec) // 60)
    if m < 120:
        return f"{m}분"
    h = m // 60
    return f"{h}시간" if h < 48 else f"{h // 24}일"


def note(d: dict | None = None, *, unit: str = "") -> str:
    """사람이 읽는 한 줄. **신선하면 빈 문자열**(늘 뜨는 배지 금지 #25·#260).

    숫자를 같이 적는다 — '다르다'만 말하면 사용자가 얼마나·왜를 알 수 없다
    (#202 숫자로 나란히 놓아라).

    ⚠️ `unit` 을 아는 호출부만 재시작 명령을 적는다 — 이 모듈은 여러
    프로세스가 import 하므로 유닛 이름을 여기 박으면 **엉뚱한 유닛을 가리키는
    처방**이 된다(#292 틀린 라벨은 라벨이 없는 것보다 나쁘다).
    """
    d = drift() if d is None else d
    if not d.get("measurable") or not d.get("stale"):
        return ""
    # ⚠️ 우리가 잰 것은 `bot/` **최상위**의 .py 뿐이다(비재귀 scandir) —
    # `bot/scripts/**`·`trade/**` 만 바뀐 배포는 이 배너를 못 띄운다. 문장이
    # 그보다 넓게 말하면 재지 않은 것을 단정하는 것이다(#165·#274 못 보는 축을
    # 밝힐 것).
    head = (f"이 프로세스는 옛 코드입니다 — `bot/` 소스가 {_mins(d['lag_sec'])} "
            "전에 갱신됐는데 프로세스는 그보다 먼저 시작했습니다")
    return f"{head}. `sudo systemctl restart {unit}` 로 재시작하세요." if unit \
        else head + "."
