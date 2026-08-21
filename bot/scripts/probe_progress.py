"""진행 상황을 **보이게** 하는 프로브 공용 유틸.

⚠️ 왜 필요한가(사용자 2026-08-21 "여기서 너무 오래 진행이 안되는데..."):
40종목 스윕은 원래 수십 분이 걸린다. 그런데 두 가지가 겹쳐 **아무것도 안
찍혔다**:
  · 파이썬 stdout 은 파이프에 물리면 **블록 버퍼링**이라 4KB 가 찰 때까지
    한 글자도 안 나간다
  · `| tail -N` 은 정의상 **프로세스가 끝나야** 출력한다
그래서 사용자는 "멈췄나?" 를 판단할 방법이 없었다. 오래 걸리는 건 사실
이므로 **얼마나 남았는지**를 말해 주는 게 옳은 대응이다(#82 '없음'만
말하는 진단은 추측을 부른다 — 침묵은 그보다 나쁘다).

프로브의 계약: 시작 줄에 버전(#21) · 대상 수 · **예상 시간**, 그리고 매
항목마다 진행/ETA. 값은 안 바꾸고 보이기만 바꾼다.
"""

from __future__ import annotations

import sys
import time


def stream_stdout() -> None:
    """줄 단위로 흘려보낸다 — 파이프·리다이렉트에서도 실시간이 되게.

    ⚠️ `| tail` 은 이걸로도 못 고친다(tail 이 EOF 를 기다린다). 그래서
    프로브 사용법 안내는 `tee` 를 쓴다."""
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:                                          # noqa: BLE001
        pass                # 아주 옛 파이썬/이상한 스트림이면 그냥 넘어간다


def fmt_eta(done: int, total: int, t0: float) -> str:
    """`[12/40 30% · 8.2분 경과 · 남은 19.1분]`. 아직 표본이 없으면 진행만.

    ⚠️ 첫 항목이 끝나기 전에는 평균을 못 낸다 — 없는 추정을 지어내지
    않는다(빈칸이 틀린 숫자보다 낫다)."""
    total = max(int(total or 0), 0)
    done = max(int(done or 0), 0)
    el = max(time.time() - t0, 0.0)
    pct = f" {100.0 * done / total:.0f}%" if total else ""
    if done <= 0 or not total:
        return f"[{done}/{total}{pct} · {el / 60:.1f}분 경과]"
    rem = el / done * max(total - done, 0)
    return (f"[{done}/{total}{pct} · {el / 60:.1f}분 경과 "
            f"· 남은 {rem / 60:.1f}분]")
