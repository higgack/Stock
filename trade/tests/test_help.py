"""trade.bot._HELP_TEXT — 4096 UTF-16 cap 회귀 가드 + 핵심 내용 보존.

2026-06-18: help 가 4296 > 4096 으로 새어 /help 가 텔레그램에서 'message too long'
으로 깨져 있던 것을 압축 + 자동화 플로우 반영하며 발견. cmd_help 가 단일 메시지로
보내므로(분할 없음) 이 cap 을 다시 넘으면 /help 가 조용히 죽는다 → 영구 가드.

bot.py 는 telegram 의존이라 import 불가(샌드박스) → 소스에서 regex 추출(import-free)."""

import re
import unittest
from pathlib import Path

_SRC = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
_HELP = re.search(r'_HELP_TEXT\s*=\s*"""(.*?)"""', _SRC, re.DOTALL).group(1)


class HelpTextTests(unittest.TestCase):
    def test_within_telegram_4096_cap(self):
        n = len(_HELP.encode("utf-16-le")) // 2
        self.assertLess(n, 4096, f"_HELP_TEXT {n} UTF-16 > 4096 — /help 가 깨짐")

    def test_all_commands_present(self):
        for cmd in ("/help", "/start", "/watch", "/unwatch", "/ignore",
                    "/unignore", "/ignored", "/hs", "/unhs", "/hslist",
                    "/map", "/unmap", "/customs", "/cost", "/export"):
            self.assertIn(cmd, _HELP, f"명령어 누락: {cmd}")

    def test_automation_flow_documented(self):
        # 사용자 2026-06-18: 데이터→파싱→갱신 자동화 플로우가 help 에 반영돼야.
        for must in ("자동화 플로우", "dart-revenue", "dart-reparse",
                     "18일", "04:30", "customs-probe"):
            self.assertIn(must, _HELP, f"플로우 내용 누락: {must}")


if __name__ == "__main__":
    unittest.main()
