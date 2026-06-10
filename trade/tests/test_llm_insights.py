"""trade.llm_insights — 파싱/가드레일/렌더/다이제스트 + 호출 모킹 테스트.

실제 Gemini는 절대 호출하지 않는다(전 케이스 킬스위치·키없음·모듈 모킹).
"""

import os
import sys
import types
import unittest
from unittest import mock

from trade import llm_insights


class ParseTests(unittest.TestCase):
    def test_plain_array(self):
        self.assertEqual(llm_insights._parse('[{"title":"a","body":"b"}]'),
                         [{"title": "a", "body": "b"}])

    def test_fenced_json(self):
        c = llm_insights._parse('```json\n[{"title":"a","body":"b"}]\n```')
        self.assertEqual(len(c), 1)

    def test_embedded_array(self):
        c = llm_insights._parse('설명...\n[{"title":"a","body":"b"}]\n끝')
        self.assertEqual(len(c), 1)

    def test_junk_returns_empty(self):
        self.assertEqual(llm_insights._parse("no json here"), [])

    def test_caps_cards(self):
        big = "[" + ",".join('{"title":"t%d","body":"b"}' % i
                             for i in range(20)) + "]"
        self.assertLessEqual(len(llm_insights._parse(big)), 6)


class GuardTests(unittest.TestCase):
    def test_killswitch_no_key_needed(self):
        with mock.patch.dict(os.environ, {"TRADE_LLM_INSIGHTS": "0"}):
            self.assertEqual(llm_insights.generate("digest"), [])

    def test_no_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ["TRADE_LLM_INSIGHTS"] = "1"
            self.assertEqual(llm_insights.generate("digest"), [])

    def test_empty_digest(self):
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "k"}):
            self.assertEqual(llm_insights.generate("  "), [])

    def test_daily_cap_blocks(self):
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "k",
                                          "TRADE_LLM_INSIGHTS": "1"}), \
             mock.patch.object(llm_insights.llm_usage, "calls_today",
                               return_value=999):
            self.assertEqual(llm_insights.generate("digest"), [])


class GenerateMockedTests(unittest.TestCase):
    def test_generate_parses_and_records_usage(self):
        fake_mod = types.ModuleType("langchain_google_genai")

        class FakeLLM:
            def __init__(self, **kw):
                pass

            def invoke(self, msgs):
                m = mock.Mock()
                m.content = '[{"title":"AI 인프라","body":"수요 확대."}]'
                m.usage_metadata = {"input_tokens": 120, "output_tokens": 60}
                return m

        fake_mod.ChatGoogleGenerativeAI = FakeLLM
        with mock.patch.dict(sys.modules, {"langchain_google_genai": fake_mod}), \
             mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "k",
                                          "TRADE_LLM_INSIGHTS": "1"}), \
             mock.patch.object(llm_insights.llm_usage, "calls_today",
                               return_value=0), \
             mock.patch.object(llm_insights.llm_usage, "record") as rec:
            cards = llm_insights.generate("digest text")
        self.assertEqual(cards, [{"title": "AI 인프라", "body": "수요 확대."}])
        rec.assert_called_once()
        self.assertEqual(rec.call_args.args[0], "gemini-2.5-pro")

    def test_generate_swallows_exceptions(self):
        fake_mod = types.ModuleType("langchain_google_genai")

        class BoomLLM:
            def __init__(self, **kw):
                pass

            def invoke(self, msgs):
                raise RuntimeError("quota")

        fake_mod.ChatGoogleGenerativeAI = BoomLLM
        with mock.patch.dict(sys.modules, {"langchain_google_genai": fake_mod}), \
             mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "k"}), \
             mock.patch.object(llm_insights.llm_usage, "calls_today",
                               return_value=0):
            self.assertEqual(llm_insights.generate("digest"), [])


class RenderTests(unittest.TestCase):
    def test_empty_no_box(self):
        self.assertEqual(llm_insights.render_html([]), "")

    def test_box_markers(self):
        h = llm_insights.render_html([{"title": "AI", "body": "수요 확대"}])
        self.assertIn("ins-box", h)
        self.assertIn("데이터가 말하는 추가 신호", h)
        self.assertIn("AI", h)


class DigestTests(unittest.TestCase):
    def test_digest_has_industry_block(self):
        months = {}
        for i in range(24):
            y = 2024 + (i // 12); mo = (i % 12) + 1
            months[f"{y}-{mo:02d}"] = 11_000_000_000
        months["2026-03"] = 20_000_000_000
        months["2026-04"] = 31_000_000_000
        d = llm_insights.build_digest({"반도체": months}, {}, {}, {})
        self.assertIn("[산업]", d)
        self.assertIn("반도체", d)


if __name__ == "__main__":
    unittest.main()
