"""VM systemd 유닛 ↔ `deploy/` 드리프트 점검 — 읽기 전용·LLM 0·₩0.

⚠️ 왜 필요한가. 이 사고가 **두 번** 났다:
  · 2026-08-02 — `trade-bot-beon-listener`·`beon-sync` 가 VM 에만 있었다.
  · 2026-08-21 — `dashboard-refresh`·`unstored-check`·`health`·
    `customs-fetch`·`backup` 5쌍이 VM 에만 있었다.
두 번째가 더 나빴다: `dashboard-refresh` 는 inbox→DB→화면의 **유일한
경로**이고, `unstored-check` 는 그게 멈춘 걸 알려줄 안전망이다. 둘 다
`deploy/` 에 없으면 `install-trade-units.sh` 로 VM 을 재구축하는 순간
데이터가 조용히 안 오르고 **그 사실조차 안 알려진다**.

규율로는 못 막는다(두 번 다 사람이 표 갱신을 잊었다) → 매일 도는
`dashboard-audit` 틱에 붙여 **기계가 대조**한다.

⚠️ 반대 방향도 본다. `deploy/` 에만 있고 VM 에 없으면 그건 "설치가 안
됐다"는 뜻이라 그 자동화는 **한 번도 안 돈다** — 있다고 믿는 게 더 위험하다.

읽기 전용: `systemctl` 조회만 하고 아무것도 설치·시작하지 않는다.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_PROBE_VER = 1
# 레포에 소스를 두지 않는 게 **정상**인 유닛. 늘릴 땐 사유를 같이 적을 것 —
# 비워 두면 이 검사가 조용히 무력해진다(실수 #24: allowlist 는 명시적으로).
_ALLOW: dict[str, str] = {
    # trade-watchdog 은 NOAH 체크아웃(~/stock)의 스크립트를 실행한다.
    "trade-bot-watchdog": "ExecStart 가 ~/stock/deploy 에 있음(별 체크아웃)",
}


def repo_units(deploy_dir: Path) -> set[str]:
    """`deploy/` 가 정의하는 유닛 이름(확장자 제외)."""
    return {p.stem for p in deploy_dir.glob("trade-bot*.service")}


def vm_units(listing: str) -> set[str]:
    """`systemctl list-units --type=service` 출력 → 유닛 이름 집합.

    출력 파싱을 순수 함수로 뺀 건 의도다 — 샌드박스엔 systemd 가 없어
    문자열로만 검증할 수 있고, 인라인으로 두면 회귀가 소스만 보게 된다
    (실수 #41 의 처방)."""
    out: set[str] = set()
    for line in (listing or "").splitlines():
        m = re.search(r"(trade-bot[\w.-]*?)\.service\b", line)
        if m:
            out.add(m.group(1))
    return out


def drift(vm: set[str], repo: set[str]) -> tuple[list[str], list[str]]:
    """(VM 에만 있음, deploy 에만 있음). allowlist 는 양쪽에서 뺀다."""
    only_vm = sorted(u for u in vm - repo if u not in _ALLOW)
    only_repo = sorted(u for u in repo - vm if u not in _ALLOW)
    return only_vm, only_repo


def _systemctl() -> str:
    try:
        r = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--all",
             "--no-legend", "--no-pager", "--plain"],
            capture_output=True, text=True, timeout=30)
        if not (r.stdout or "").strip():
            # ⚠️ rc=0 인데 빈 출력일 수도 있다(컨테이너·systemd 없음).
            # 사유 없이 빈 값을 돌려주면 '드리프트 없음'으로 읽힌다.
            print(f"❌ systemctl 출력이 비었다 — 판정 불가 "
                  f"(rc={r.returncode}, stderr="
                  f"{(r.stderr or '').strip()[:120]!r})")
        return r.stdout or ""
    except Exception as exc:                                   # noqa: BLE001
        # ⚠️ 조용히 빈 문자열을 돌려주면 "드리프트 없음"으로 읽힌다
        # (대조 0건은 통과가 아니다, 실수 #54).
        print(f"❌ systemctl 조회 실패 — 판정 불가: "
              f"{type(exc).__name__}: {exc}")
        return ""


def main(argv: list[str] | None = None) -> int:
    print(f"=== systemd 유닛 ↔ deploy/ 드리프트 점검 v{_PROBE_VER} ===")
    deploy = Path(__file__).resolve().parents[2] / "deploy"
    repo = repo_units(deploy)
    listing = _systemctl()
    if not listing:
        return 2                       # 판정 불가는 통과가 아니다
    vm = vm_units(listing)
    if not vm:
        print("❌ VM 유닛이 0개로 파싱됨 — 파싱 패턴이 틀렸을 수 있다")
        return 2
    only_vm, only_repo = drift(vm, repo)
    print(f"VM {len(vm)}개 · deploy {len(repo)}개 "
          f"· allowlist {len(_ALLOW)}개")
    for u in only_vm:
        print(f"  ⚠️ VM 에만 있음(레포 미반영): {u}  ← systemctl cat {u}")
    for u in only_repo:
        print(f"  ⚠️ deploy 에만 있음(설치 안 됨 = 한 번도 안 돎): {u}")
    if not only_vm and not only_repo:
        print("  ✅ 드리프트 없음")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
