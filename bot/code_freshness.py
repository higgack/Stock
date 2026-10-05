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

# 소스가 바뀐 뒤 이만큼은 '자동 재시작이 오는 중' 으로 본다 — 그 안에서는 수동 재시작을
# 처방하지 않는다(#165 재지 않은 것을 단정하지 말 것). 값은 `auto-update.sh` 의 창에서
# 정한다: reset → stock-bot 정지(`deploy/stock-bot.service` TimeoutStopSec 900) →
# sleep·알림(curl -m) → 대시보드 정지(기본 90초)·시작. 회귀가 두 유닛 파일과 스크립트의
# 대기에서 하한을 다시 계산해 이 값이 그보다 큰지 잰다 — 누가 TimeoutStopSec 를 늘리면
# 거기서 빨간불이 된다. ⚠️ 못 보는 축(#274): deploy/ 가 바뀐 배포의 install.sh 실행
# 시간은 상한이 없다(그 경우 install.sh 가 스스로 대시보드를 try-restart 한다).
GRACE_SEC = 1200
# `/proc/stat` 의 btime 은 **정수 초**라 계산한 시작 시각이 실제보다 최대 1초 이르다 —
# 그 1초 안의 어긋남은 '옛 코드' 로 판정하지 않는다.
_CLOCK_SLACK = 1.0


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
          now: float | None = None, grace: float = GRACE_SEC) -> dict:
    """{'stale', 'pending', 'lag_sec', 'age_sec', 'grace_sec', 'started', 'newest',
    'measurable'} (순수 — 인자 주입).

    ``lag_sec`` = 소스가 프로세스보다 얼마나 새것인가(양수면 옛 코드) · ``age_sec`` =
    소스가 바뀐 지 얼마나 됐나. 옛 코드이면서 ``age_sec > grace`` 일 때만 ``stale`` —
    그 전에는 ``pending``(자동 재시작이 오는 중)이다. 두 간격은 **다른 사실**이다:
    배포 창에서 ``lag`` 는 지난 배포 이후 시간(몇 시간)이고 ``age`` 는 방금(몇 초)이다
    (실수 #435 — 옛 판은 ``lag`` 로 유예를 재 배포마다 거짓 배너를 냈다).

    `measurable=False` 는 소스 mtime 을 못 읽은 경우다 — **'신선하다'가 아니라
    판정 불가**다(#54 대조 0건은 통과가 아니다).
    """
    st = float(process_started() if started is None else started)
    nw = float(newest_source_mtime() if newest is None else newest)
    t = float(time.time() if now is None else now)
    if nw <= 0:
        return {"stale": False, "pending": False, "measurable": False,
                "lag_sec": 0.0, "age_sec": 0.0, "grace_sec": float(grace),
                "started": st, "newest": nw}
    lag = nw - st
    age = t - nw
    behind = lag > _CLOCK_SLACK
    stale = behind and age > grace
    return {"stale": bool(stale), "pending": bool(behind and not stale),
            "measurable": True, "lag_sec": lag, "age_sec": age,
            "grace_sec": float(grace), "started": st, "newest": nw}


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
    # ⚠️ 우리가 잰 것은 `bot/` **최상위**의 .py 뿐이다(비재귀 scandir) —
    # `bot/scripts/**`·`trade/**` 만 바뀐 배포는 이 배너를 못 띄운다. 문장이
    # 그보다 넓게 말하면 재지 않은 것을 단정하는 것이다(#165·#274 못 보는 축을
    # 밝힐 것).
    # ⚠️ 두 간격을 **따로** 적는다(실수 #435): 옛 판은 `lag`(프로세스 대비 소스 시차)를
    # '소스가 N 전에 갱신됐다' 로 적어, 방금 끝난 배포가 '4시간 전' 으로 읽혔다(#34 한 숫자가
    # 두 뜻을 대표하면 한쪽은 반드시 거짓말).
    age = d.get("age_sec")
    when = f"{_mins(age)} 전에 " if age is not None else ""
    head = (f"이 프로세스는 옛 코드입니다 — `bot/` 소스가 {when}갱신됐는데 이 프로세스는 "
            f"그보다 {_mins(d['lag_sec'])} 먼저 시작했습니다")
    grace = d.get("grace_sec")
    if grace:
        head += f"(자동 배포가 재시작했어야 할 {_mins(grace)}이 지났습니다)"
    return f"{head}. `sudo systemctl restart {unit}` 로 재시작하세요." if unit \
        else head + "."


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
