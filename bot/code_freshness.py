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

⚠️ 배포 중에는 정상적으로 어긋난다 — `auto-update.sh` 는 git reset(소스 mtime 이
바뀐다) 뒤 **stock-bot 정지가 끝날 때까지 기다리고**(TimeoutStopSec 900) 알림을
보낸 다음에야 대시보드를 재시작한다. 유예는 그 창을 덮어야 하므로 **소스가 바뀐 뒤
지난 시간**(``age``)으로 잰다. 2026-10-05 까지는 ``소스 mtime − 프로세스 시작``
(``lag``)으로 쟀는데, 배포 창에서 그 값은 **지난 배포 이후 시간**(몇 시간)이라
유예를 늘 넘었다 — 사용자가 배포 직후 '4시간 전에 갱신됐는데…' 배너를 봤고 대시보드는
몇 분 뒤 스스로 재시작됐다(실수 #435 "타이밍 문제야"). 유예 안이라도 옛 코드인
사실은 ``pending`` 으로 남긴다(#41 — 숨기는 게 아니라 처방을 미룬다).

⚠️ 기준점은 이 프로세스가 **놓친 첫 변경**(시작보다 새 mtime 중 가장 이른 것)이다 — 가장
최근 변경으로 재면 20분 안쪽으로 이어지는 배포마다 유예가 다시 시작돼, 재시작이 계속
실패해도 배너가 침묵한다(독립 리뷰 #435 M1: base 커밋 이력의 연속 쌍 285 중 91 이 20분
미만 — 배포는 도는 동안 들어온 커밋을 한 번에 실으므로 실제 배포 쌍은 그보다 적다).
재는 범위는 자동 배포가 대시보드를 다시 띄우는 조건(`auto-update.sh` CODE_CHANGED 의 .py
갈래)과 같다(배포가 아닌 쓰기가 바꾸는 갈래 둘은 빼고 — `SCAN_EXCLUDED`) — 옛 판은 `bot/`
최상위만 봐 `trade`·`TradingAgents` 만 바뀐 배포의 재시작 실패를 끝내 못 잡았다(같은
리뷰 7a).
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

# 놓친 첫 변경 뒤 이만큼은 '자동 재시작이 오는 중' 으로 본다 — 그 안에서는 수동 재시작을
# 처방하지 않는다(#165 재지 않은 것을 단정하지 말 것). 값은 `auto-update.sh` 의 창에서
# 정한다: reset → stock-bot 정지(`deploy/stock-bot.service` TimeoutStopSec 900) →
# 창 안의 sleep·알림(notify 호출마다 curl 상한) → 대시보드 정지(기본 90초)·시작. 회귀가
# 두 유닛의 정지 상한(TimeoutStopSec·TimeoutSec, systemd 시간 단위 포함)과 reset 뒤 창 안의
# sleep·notify 호출 수 × curl `-m`/`--max-time` 에서 경로 상한(현재 1043초)을 다시 계산해
# 이 값이 그보다 큰지 잰다 — 그 합이 유예를 넘으면(지금 여유 157초) 거기서 빨간불이 된다.
# 하나가 늘어도 여유 안이면 통과한다.
# ⚠️ 못 보는 축(#274): (a) deploy/ 가 바뀐 배포의 install.sh 와 재시작 권한이 없을 때의
# self-heal(install.sh) 실행 시간은 상한이 없다 (b) VM 의 systemd drop-in·
# DefaultTimeoutStopSec 이 90초와 다르면 레포에서 안 보인다 (c) VM 에서 직접 고치고 push 하는
# 경로(LOCAL==REMOTE)는 창이 **첫 편집부터** push·타이머 발화·재시작까지라, 편집이 20분을
# 넘기면 그 사이에 배너가 뜬다 — 기준점을 놓친 첫 변경으로 잡아 연속 배포를 잡는 대가다
# (d) systemd 는 SIGKILL 단계에서도 같은 상한을 다시 기다리는 것으로 알지만 재지 않았다 —
# D 상태 프로세스가 있으면 봇 정지가 최악 두 배가 될 수 있다 (e) mtime 으로 재므로 내용이 같은
# 재작성도 옛 코드로 센다(예: 문서만 바뀐 배포의 git reset 이 VM 의 로컬 편집을 되돌림 —
# 그 배포는 대시보드를 재시작하지 않는다).
GRACE_SEC = 1200
# `/proc/stat` 의 btime 은 **정수 초**라 계산한 시작 시각이 실제보다 최대 1초 이르다 —
# 그 1초 안의 어긋남은 '옛 코드' 로 판정하지 않는다.
_CLOCK_SLACK = 1.0


def process_started() -> float:
    """이 프로세스가 **뜬** 시각(epoch) — `/proc` 을 못 읽으면 import 시각 근사."""
    return _STARTED


# 레포 루트 — 아래 범위는 이 기준의 상대 경로다(테스트가 임시 루트로 갈아 끼운다).
_REPO_ROOT = Path(__file__).resolve().parent.parent
# 재는 범위 = 자동 배포가 대시보드를 다시 띄우는 조건(`deploy/auto-update.sh` CODE_CHANGED)의
# .py 갈래. 그 조건은 `tests/test_restart_closure_20260928.py` 가 대시보드의 import 폐포를
# 덮는다고 재므로, 같은 범위를 재면 '이 프로세스가 올린 코드' 를 덮는다. 열거지만 대조가 있다 —
# 같은 파일의 `test_drift_banner_scans_exactly_the_dashboard_restart_condition` 이 정규식과
# git 목록에서 파생한 집합과 이 스캔이 같은지 잰다(#24 열거는 대조가 있어야 열거다).
# 스캔은 아래 두 목록만으로 정해진다(`bot`·`trade` 는 바로 아래만 — 하위 디렉터리로 내려가지
# 않는다). 조건 안이지만 **일부러 넣지 않은** 갈래 둘이 `SCAN_EXCLUDED` 다 — 배포가 아닌 쓰기가
# 그 자리를 바꾸므로, 넣으면 그 쓰기를 '옛 코드' 라 부르고 재시작을 처방하는 틀린 라벨이 된다
# (#292·#25·#260):
#   · `trade/data/` — 코드가 아니라 데이터이고(지금은 .py 가 없어 스캔에 닿을 일도 없다),
#     운영자가 VM 에서 직접 다시 만드는 경로(`trade.scripts.build_hs_names`)가 있다.
#   · `bot/screener_themes/` — 봇이 **실행 중에** 새 테마 모듈(.py)을 직접 쓴다
#     (`screener_freetext.promote_to_module`, 같은 자유어 5회 사용 시). 운영 코드에서 실행 중에
#     .py 를 쓰는 곳은 그 하나다(2026-10-05 grep 실측 · 독립 리뷰 재확인).
# `SCAN_EXCLUDED` 는 스캔이 읽는 목록이 아니라 대조 회귀가 정규식의 기대 집합에서 그 갈래를 빼는
# 데 쓰는 목록이다 — 사유와 한 자리에 둬 두 목록이 갈라지지 않게 한다(#38). 그 둘만 바뀐
# 배포에서 대시보드 재시작이 실패하면 이 배너는 못 잡는다(못 보는 축, #274).
_SCAN_FLAT = ("bot", "bot/scripts", "trade")       # 그 디렉터리 바로 아래만
_SCAN_TREE = ("TradingAgents/tradingagents",)      # 하위까지
SCAN_EXCLUDED = ("trade/data/", "bot/screener_themes/")


def source_mtimes(root=None) -> list:
    """[(mtime, 레포 기준 경로)] — 위 범위의 .py 전부. 못 읽는 디렉터리는 건너뛴다.

    ⚠️ 이름을 열거하지 않는다 — 목록형 가드는 목록 밖 파일을 못 잡는다(#24). 디렉터리를
    훑으므로 새 파일도 잡힌다. 비용은 .py 약 360개 stat — 중앙값 1.5ms·p90 2ms 라 요청마다
    불러도 된다(2026-10-05 실측).
    여기는 **의존이 없는 최하위 계층**이라 감사·헬스·페이지가 순환 없이 쓴다.
    """
    import os as _os

    base = Path(root) if root else _REPO_ROOT
    out: list = []
    for d in _SCAN_FLAT:
        try:
            with _os.scandir(base / d) as it:
                for e in it:
                    if e.is_file() and e.name.endswith(".py"):
                        try:
                            out.append((e.stat().st_mtime, f"{d}/{e.name}"))
                        except OSError:
                            pass
        except OSError:
            continue
    for d in _SCAN_TREE:
        for dirpath, dirnames, filenames in _os.walk(base / d):
            dirnames[:] = sorted(x for x in dirnames if x != "__pycache__")
            for f in filenames:
                if f.endswith(".py"):
                    full = Path(dirpath) / f
                    try:
                        out.append((full.stat().st_mtime, full.relative_to(base).as_posix()))
                    except (OSError, ValueError):
                        pass
    return out


def newest_source_mtime(root=None) -> float:
    """위 범위 .py 중 가장 최근 수정시각. 못 읽으면 0.0(`root` 는 레포 루트)."""
    return max((m for m, _p in source_mtimes(root)), default=0.0)


def drift(*, started: float | None = None, newest: float | None = None,
          now: float | None = None, grace: float = GRACE_SEC) -> dict:
    """{'stale', 'pending', 'measurable', 'lag_sec', 'age_sec', 'grace_sec', 'started',
    'newest', 'first_new', 'first_path'(+판정 불가면 'why')} (순수 — 인자 주입).

    기준점(anchor)은 이 프로세스가 **놓친 첫 변경** ``first_new``(시작보다 새 mtime 중 가장
    이른 것)이다. ``lag_sec`` = 기준점이 시작보다 얼마나 뒤인가(양수면 옛 코드) ·
    ``age_sec`` = 기준점 뒤 지난 시간. 옛 코드이면서 ``age_sec > grace`` 일 때만 ``stale`` —
    그 전에는 ``pending``(자동 재시작이 오는 중)이다. 두 간격은 **다른 사실**이다: 배포
    창에서 ``lag`` 는 지난 배포 이후 시간(몇 시간)이고 ``age`` 는 방금(몇 초)이다(실수
    #435 — 옛 판은 ``lag`` 로 유예를 재 배포마다 거짓 배너를 냈다). 기준점을 가장 **최근**
    변경으로 잡으면 이어지는 배포마다 유예가 다시 시작돼 진짜 실패가 침묵한다(독립 리뷰
    #435 M1). 놓친 변경이 없으면 기준점은 최신 mtime 이다.

    ``newest`` 를 주입하면 변경이 한 번이었다고 본다(놓친 첫 변경 = ``newest``) — 여러 번의
    변경은 스캔(`source_mtimes`)으로만 들어온다(테스트는 임시 레포 루트로 태운다).

    `measurable=False` 는 판정 불가다 — **'신선하다'가 아니다**(#54 대조 0건은 통과가
    아니다). 소스 mtime 을 못 읽었거나, mtime 이 지금보다 **미래**(시계 역행 등)라 경과를 잴
    수 없는 경우이고, 사유를 ``why`` 에 남긴다(#82). 미래 mtime 을 그대로 쓰면 ``age < 0`` 이라
    유예가 끝날 때까지 '자동 재시작 대기 중' 으로 적혔다(독립 리뷰 #435 L5 · #165).
    """
    st = float(process_started() if started is None else started)
    t = float(time.time() if now is None else now)
    if newest is None:
        mt = source_mtimes()
        nw, nwp = max(mt) if mt else (0.0, "")
        later = sorted((m, p) for m, p in mt if m - st > _CLOCK_SLACK)
        fm, fp = later[0] if later else (None, "")
    else:
        nw, nwp = float(newest), ""
        fm, fp = nw, ""
    base = {"stale": False, "pending": False, "measurable": False, "grace_sec": float(grace),
            "started": st, "newest": nw, "first_new": None, "first_path": ""}
    if nw <= 0:
        return {**base, "lag_sec": 0.0, "age_sec": 0.0, "why": "소스 mtime 을 못 읽었습니다"}
    if nw - t > _CLOCK_SLACK:
        # 어느 파일인지 댄다 — 한 파일만 미래여도 판정 전체가 불가가 되므로(독립 리뷰 델타 L9)
        where = f"`{nwp}` 의 " if nwp else "소스 "
        return {**base, "lag_sec": nw - st, "age_sec": t - nw,
                "why": f"{where}mtime 이 지금보다 {_mins(nw - t)} 뒤입니다(시계 어긋남)"}
    behind = fm is not None and fm - st > _CLOCK_SLACK
    anchor = fm if behind else nw
    age = t - anchor
    stale = behind and age > grace
    return {**base, "stale": bool(stale), "pending": bool(behind and not stale),
            "measurable": True, "lag_sec": anchor - st, "age_sec": age,
            "first_new": fm if behind else None, "first_path": fp if behind else ""}


def _mins(sec: float) -> str:
    # 1분 미만은 초로 — 유예를 ``age`` 로 옮긴 뒤 '옛 코드' 문턱이 ``lag > 1초`` 라 1분 미만의
    # 시차도 문구에 닿는다. 분으로 내리면 '0분 먼저 시작' 이 되어 숫자가 사실을 말하지 않는다
    # (실수 #435 배포 전 셀프리뷰 · #34·#43).
    if abs(sec) < 60:
        return f"{int(abs(sec))}초"
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
    # ⚠️ 우리가 잰 것은 자동 배포의 재시작 조건과 같은 범위의 .py 뿐이다(`source_mtimes`) —
    # 문장이 그보다 넓게 말하면 재지 않은 것을 단정하는 것이다(#165·#274 못 보는 축을
    # 밝힐 것). 그래서 **놓친 첫 변경의 파일**을 대고, 모르면 '코드' 라고만 쓴다.
    # ⚠️ 두 간격을 **따로** 적는다(실수 #435): 옛 판은 `lag`(프로세스 대비 소스 시차)를
    # '소스가 N 전에 갱신됐다' 로 적어, 방금 끝난 배포가 '4시간 전' 으로 읽혔다(#34 한 숫자가
    # 두 뜻을 대표하면 한쪽은 반드시 거짓말).
    age = d.get("age_sec")
    path = d.get("first_path") or ""
    what = f"`{path}` 등 코드가" if path else "코드가"
    when = f" {_mins(age)} 전에" if age is not None else ""
    head = (f"이 프로세스는 옛 코드입니다 — {what}{when} 갱신됐는데 이 프로세스는 "
            f"그보다 {_mins(d['lag_sec'])} 먼저 시작했습니다")
    grace = d.get("grace_sec")
    if grace:
        # 가정으로 적는다 — VM 에서 직접 고친 소스처럼 배포가 아닌 drift 도 있다. '자동 배포가
        # 재시작했어야' 라고 단정하면 재지 않은 원인을 적는 것이다(독립 리뷰 #435 L4 · #165).
        head += f"(자동 배포라면 재시작이 끝났을 {_mins(grace)}이 지났습니다)"
    return f"{head}. `sudo systemctl restart {unit}` 로 재시작하세요." if unit \
        else head + "."


def run_note(d: dict | None = None) -> str:
    """**자동 재시작이 없는** 1회성 실행(CLI)용 한 줄 — 유예를 보지 않는다.

    유예는 '자동 배포가 이 프로세스를 다시 띄우러 오는 중' 이라는 창인데, CLI 는 아무도 다시
    띄우지 않는다. 그래서 옛 코드면(``stale`` 이든 ``pending`` 이든) 바로 말한다 — ``stale``
    에만 기대면 10분 실행 중의 배포가 ``pending`` 으로 침묵하고, 지문은 디스크의 새 코드를·
    결과는 메모리의 옛 코드를 반영한 채 아무 표시가 없다(독립 리뷰 #435 L3 · #45·#274).
    신선하면 빈 문자열(#25·#260), 판정 불가면 그렇다고 사유와 함께 말한다(#54·#82).
    """
    d = drift() if d is None else d
    if not d.get("measurable"):
        why = d.get("why") or ""
        return "프로세스 신선도 판정 불가" + (f"({why})" if why else "")
    if not (d.get("stale") or d.get("pending")):
        return ""
    path = d.get("first_path") or ""
    what = f"`{path}` 등 코드가" if path else "코드가"
    # '일부는 … 수 있습니다' — 감사는 모듈을 실행 중에 하나씩 import 하므로 바뀐 뒤 import 된
    # 감사는 새 코드로 돌고, 바뀐 파일을 아예 안 쓰는 감사도 있다. 전부 옛 코드라고 단정하면 재지
    # 않은 것을 적는 것이다(독립 리뷰 델타 L4 · #165).
    return (f"⚠️ 이 프로세스가 뜬 {_mins(d['lag_sec'])} 뒤 {what} 바뀌었습니다"
            f"({_mins(d['age_sec'])} 전) — 이 결과의 일부는 바뀌기 전 코드로 만들어졌을 수 "
            f"있습니다")


# ── 화면 배너 ────────────────────────────────────────────────────────────
# ⚠️ 2026-09-13: 이 배너가 **메인 대시보드 한 장에만** 있었다. 사용자가 테마
# 페이지에서 옛 부제(`정렬 5종 합산 266개(fallCnt+56, …)`)를 보고 **두 번**
# 물었는데, 그 문구를 만드는 코드는 25시간 전에 base 에서 사라졌다 — 즉
# 화면은 옛 프로세스가 그린 것이고, 페이지는 그 사실을 말할 방법이 없었다
# (#11 '배포완료 ≠ 화면에 보임' · #38 한 화면에서 고쳤으면 형제를 즉시 grep ·
# #12 같은 증상 2회+ 면 다음 패치가 아니라 가시성).
#
# ⚠️ `fetch` 주소는 **상대경로**여야 한다 — 토큰 경로(`/t/<token>/theme`)
# 아래에서도 같은 접두를 따라간다. `/api/build` 로 적으면 토큰이 떨어져 404 다.
#
# ⚠️ 신선하면 **아무것도 그리지 않는다**(#25·#260 늘 뜨는 배너는 아무것도 안
# 재는 것과 같다). 배너 실패가 본 화면을 막아서도 안 된다(#315).
#
# ⚠️⚠️ **이 배너가 못 보는 축**(독립 리뷰 2026-09-13 · #274 검사를 넣을 땐 그게
# 못 보는 축을 같이 답할 것): 서버가 그리는 페이지(`/theme` 등)는 HTML 과
# `/api/build` 가 **같은 프로세스**에서 나온다 — 그 프로세스가 옛 코드면 애초에
# 이 스크립트가 없는 HTML 을 뱉고 `/api/build` 라우트도 없다. 즉 배너는 자기
# 프로세스의 낡음을 **스스로 신고하지 못한다**. 이 배너가 실제로 발화하는 자리는
# `market.html` 처럼 **HTML 은 봇이 굽고 API 는 대시보드가 답하는** 분리 구조다
# (2026-09-12 ★ 사고가 그 조합이었다). 서버렌더 페이지의 '옛 화면' 은 다른 축이
# 잡는다 — 부제를 `#live-sub` 로 두어 `live_refresh` 가 표와 **같이** 갈아끼운다.
BANNER_JS = """<script>
(function(){
  function buildBanner(msg) {
    if (document.getElementById('build-drift')) return;
    var d = document.createElement('div');
    d.id = 'build-drift';
    d.style.cssText = 'background:#3d2b12;border:1px solid #a9741c;color:#f0c674;'
      + 'padding:10px 14px;border-radius:8px;margin:0 0 14px;font-size:13px;line-height:1.5';
    d.textContent = '\u26a0\ufe0f ' + msg;
    document.body.insertBefore(d, document.body.firstChild);
  }
  fetch('api/build')
    .then(function(r) {
      if (r.status === 404) {
        /* 404 는 갈래가 둘이다 — 라우트가 없는 옛 서버, 또는 주소의 토큰이
           바뀐 경우(`_strip_token_or_404`). 처방이 다르므로 단정하지 않는다
           (#82 갈래는 이름으로 · #165 재지 않은 것을 단정하지 말 것). */
        buildBanner('이 페이지의 새 기능(`/api/build`)에 서버가 404 로 답했습니다 — '
                    + '대시보드 프로세스가 옛 코드이거나, 주소의 접근 토큰이 바뀐 것입니다. '
                    + '새로고침해도 같으면 VM 에서 `sudo systemctl restart stock-bot-dashboard`.');
        return null;
      }
      return r.json().catch(function() { return null; });
    })
    .then(function(b) { if (b && b.ok && b.stale && b.note) buildBanner(b.note); })
    .catch(function() {});
})();
</script>"""
