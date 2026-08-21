"""systemd 유닛 ↔ `deploy/` 정합성 — 같은 사고가 두 번 났다.

  · 2026-08-02 `trade-bot-beon-listener`·`beon-sync` 가 VM 에만 있었다.
  · 2026-08-21 `dashboard-refresh`·`unstored-check`·`health`·
    `customs-fetch`·`backup` **5쌍**이 VM 에만 있었다.

두 번째가 더 나빴다: `dashboard-refresh` 는 inbox→DB→화면의 유일한
경로이고 `unstored-check` 는 그게 멈춘 걸 알려줄 안전망이라, 둘 다
없으면 데이터가 조용히 안 오르고 그 사실조차 안 알려진다.

여기 검사는 **레포 안에서 볼 수 있는 것**만 본다(테스트는 VM 을 못 본다).
VM 대조는 `trade.scripts.unit_drift_check` 가 매일 한다.
"""

import re
import unittest
from pathlib import Path

from trade.scripts import unit_drift_check as udc

_DEPLOY = Path("deploy")


class RepoUnitTests(unittest.TestCase):
    def test_every_timer_has_a_service(self):
        """타이머만 있으면 systemd 가 `Unit=` 대상을 못 찾아 조용히 안 돈다."""
        for t in _DEPLOY.glob("trade-bot*.timer"):
            self.assertTrue((_DEPLOY / f"{t.stem}.service").exists(),
                            f"{t.name} 의 짝 service 가 없다")

    def test_timer_unit_field_points_at_a_real_service(self):
        for t in _DEPLOY.glob("trade-bot*.timer"):
            m = re.search(r"^Unit=(\S+)$", t.read_text(encoding="utf-8"),
                          re.M)
            self.assertIsNotNone(m, f"{t.name} 에 Unit= 이 없다")
            self.assertTrue((_DEPLOY / m.group(1)).exists(),
                            f"{t.name} → {m.group(1)} 가 없다")

    def test_every_execstart_target_exists_in_the_repo(self):
        """유닛이 지워진 스크립트를 가리키면 매 틱 조용히 실패한다."""
        root = Path(".")
        checked = 0
        for s in _DEPLOY.glob("trade-bot*.service"):
            for line in s.read_text(encoding="utf-8").splitlines():
                if not line.startswith("ExecStart="):
                    continue
                mod = re.search(r"-m\s+([\w.]+)", line)
                sh = re.search(r"stock-trade/([\w/.\-]+\.sh)", line)
                if mod:
                    p = root / (mod.group(1).replace(".", "/") + ".py")
                    self.assertTrue(p.exists(), f"{s.name}: {p} 없음")
                    checked += 1
                elif sh:
                    p = root / sh.group(1)
                    if p.parts[0] == "deploy" or p.parts[0] == "trade":
                        self.assertTrue(p.exists(), f"{s.name}: {p} 없음")
                        checked += 1
        self.assertGreater(checked, 10, "대조 대상이 0건이면 통과가 아니다")

    def test_inventory_doc_lists_every_timer(self):
        """표 갱신을 사람이 기억하는 방식은 두 번 실패했다 — 테스트가 강제."""
        doc = Path("docs/automation.md").read_text(encoding="utf-8")
        for t in _DEPLOY.glob("trade-bot*.timer"):
            self.assertIn(t.name, doc,
                          f"{t.name} 이 docs/automation.md 인벤토리에 없다")

    def test_the_five_recovered_units_are_present(self):
        """2026-08-21 복구분 — 다시 사라지면 데이터가 조용히 안 오른다."""
        for u in ("dashboard-refresh", "unstored-check", "health",
                  "customs-fetch", "backup"):
            for ext in ("service", "timer"):
                p = _DEPLOY / f"trade-bot-{u}.{ext}"
                self.assertTrue(p.exists(), f"{p} 가 사라졌다")

    def test_dashboard_refresh_still_ingests(self):
        """이 유닛이 inbox→DB→화면의 **유일한** 경로다. ingest 단계가
        빠지면 나쁜양파 데이터가 영영 화면에 안 오른다."""
        txt = (_DEPLOY / "trade-bot-dashboard-refresh.service").read_text(
            encoding="utf-8")
        execs = [l for l in txt.splitlines() if l.startswith("ExecStart=")]
        self.assertTrue(any("trade.scripts.ingest_inbox" in l for l in execs))
        self.assertTrue(any(l.rstrip().endswith("trade.dashboard")
                            for l in execs), "대시보드 재생성이 없다")


class DriftFunctionTests(unittest.TestCase):
    """⚠️ 픽스처로 **틀린 상태를 실제로 재현**해 ❌ 가 뜨는 걸 본 뒤 믿는다
    (실수 #47 — 감사 도구의 계수 패턴 자체가 틀릴 수 있다)."""

    LISTING = (
        "  trade-bot.service                loaded active running Trade bot\n"
        "  trade-bot-health.service         loaded active exited  Health\n"
        "● trade-bot-backup.service         loaded failed failed  Backup\n"
        "  unrelated-thing.service          loaded active running Nope\n")

    def test_parses_unit_names_from_systemctl_output(self):
        got = udc.vm_units(self.LISTING)
        self.assertEqual(got, {"trade-bot", "trade-bot-health",
                               "trade-bot-backup"})
        self.assertNotIn("unrelated-thing", got)

    def test_reports_units_present_only_on_the_vm(self):
        only_vm, only_repo = udc.drift({"a", "b"}, {"a"})
        self.assertEqual(only_vm, ["b"])
        self.assertEqual(only_repo, [])

    def test_reports_units_present_only_in_the_repo(self):
        """deploy 에만 있으면 **한 번도 안 돈다** — 있다고 믿는 게 더 위험."""
        only_vm, only_repo = udc.drift({"a"}, {"a", "z"})
        self.assertEqual(only_vm, [])
        self.assertEqual(only_repo, ["z"])

    def test_allowlist_is_explicit_and_documented(self):
        """비어 있거나 사유 없는 allowlist 는 검사를 조용히 무력화한다."""
        self.assertTrue(udc._ALLOW, "allowlist 가 비었다")
        for k, why in udc._ALLOW.items():
            self.assertTrue(why.strip(), f"{k}: 사유가 없다")

    def test_clean_state_reports_no_drift(self):
        self.assertEqual(udc.drift({"a", "b"}, {"a", "b"}), ([], []))

    def test_probe_prints_its_version(self):
        """진단 스크립트 버전 배너(실수 #21). 숫자가 아니라 **찍는 행위**를."""
        src = Path(udc.__file__).read_text(encoding="utf-8")
        self.assertIn("_PROBE_VER", src)
        self.assertIn('v{_PROBE_VER}', src)


if __name__ == "__main__":
    unittest.main()
