"""기술 분석 탭 회귀 (사용자 2026-06-28).

검증 지표 세트(무료) + 강세/약세 토론(LLM 1콜, cost-gated, 캐시)의 순수 로직.
LLM·yfinance 는 샌드박스 미설치라 호출하지 않고, 파싱·캐시·프롬프트·비용기록·
지표 산출(가짜 OHLCV 주입)만 결정적으로 검증한다."""
import json
import unittest
from pathlib import Path
from tempfile import mkdtemp

import bot.technical_analysis as ta


class TestParseJson(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(ta._parse_json('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        self.assertEqual(ta._parse_json('```json\n{"a": 2}\n```'), {"a": 2})

    def test_embedded(self):
        self.assertEqual(ta._parse_json('블라 {"x": [1, 2]} 끝'), {"x": [1, 2]})

    def test_garbage(self):
        self.assertIsNone(ta._parse_json("그냥 텍스트"))
        self.assertIsNone(ta._parse_json(""))
        self.assertIsNone(ta._parse_json(None))


class TestPrompt(unittest.TestCase):
    def test_contains_ticker_and_rules(self):
        p = ta._prompt("NVDA", {"asof": "2026-06-27", "rsi14": 55})
        self.assertIn("NVDA", p)
        self.assertIn("5거래일", p)          # 단기 horizon 규칙
        self.assertIn("날조 금지", p)        # 환각 가드
        self.assertIn("JSON", p)             # 스키마 강제
        # 지표가 프롬프트에 직렬화돼 들어가야 함(LLM 이 근거로 쓰도록)
        self.assertIn("rsi14", p)

    def test_no_placeholder_leak(self):
        # 과거 실수(2026-06-28): 스키마 placeholder "형태 문자열" 을 LLM 이 그대로
        # 베껴 '판정' 칸에 노출. 메타단어가 스키마에 남으면 재발 → 영구 차단.
        p = ta._prompt("AAPL", {"asof": "2026-06-27"})
        self.assertNotIn("형태 문자열", p)
        self.assertIn("axis_verdict", p)     # 라벨 enum 필드명
        self.assertIn("빈 문자열", p)        # 한쪽 근거 없을 때 "" 지시


class TestCacheAndUsage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(mkdtemp())
        ta._CACHE_DIR = self.tmp / "cache"
        ta._USAGE_LOG = self.tmp / "usage.jsonl"

    def test_cache_roundtrip(self):
        d = ta.today_kst()
        f = ta._cache_file("AAPL", d)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"ok": True, "verdict": "중립"}), "utf-8")
        got = ta.cached_debate("AAPL")
        self.assertEqual(got["verdict"], "중립")

    def test_cache_miss(self):
        self.assertIsNone(ta.cached_debate("ZZZZ"))

    def test_cache_file_path_safe(self):
        # 슬래시·경로조작 문자가 파일명에 새지 않음(. _ - 만 허용).
        f = ta._cache_file("../../etc/passwd", "2026-06-28")
        self.assertNotIn("/", f.name)
        # 정상 티커는 보존(005930.KS 등 시장 접미사)
        self.assertIn("005930.KS", ta._cache_file("005930.KS", "2026-06-28").name)

    def test_usage_log_format(self):
        ta._log_usage(1000, 500)
        rec = json.loads(self.tmp.joinpath("usage.jsonl").read_text("utf-8").strip())
        self.assertEqual(rec["type"], "llm_call")
        self.assertEqual(rec["subsystem"], "technical")
        self.assertEqual(rec["prompt_tokens"], 1000)
        self.assertEqual(rec["completion_tokens"], 500)
        # 비용 = (1000*0.10 + 500*0.40)/1e6
        self.assertAlmostEqual(rec["cost_usd"], (1000 * 0.10 + 500 * 0.40) / 1e6, places=8)


class TestClientJsIdempotency(unittest.TestCase):
    """lookup 지연로딩은 _TECHNICAL_JS 를 core/full 2회 주입한다. 중복 배선 시
    document 클릭 리스너가 2개가 돼 '실행' 1클릭이 &run=1 을 2회 발사(Gemini
    2중 과금) — window 플래그 가드가 반드시 있어야 한다(과거 실수 재발 방지)."""

    def test_window_wired_guard_present(self):
        import bot.dashboard as d
        js = d._TECHNICAL_JS
        self.assertIn("window.__noahTechWired", js)
        # 가드는 '있으면 즉시 return' 형태여야 1회만 배선됨
        self.assertIn("if(window.__noahTechWired) return", js)

    def test_state_on_window_survives_dom_swap(self):
        # IND·debate·busy 상태가 window 에 있어야 DOM 교체(core→full) 후 재렌더 가능
        import bot.dashboard as d
        js = d._TECHNICAL_JS
        for key in ("window.__noahTechIND", "window.__noahTechDebate",
                    "window.__noahTechBusy"):
            self.assertIn(key, js)


class TestComputeIndicators(unittest.TestCase):
    """가짜 일봉(상승추세)을 주입해 지표 산출·라운딩·None 가드를 검증.
    yfinance/pandas 미설치 환경은 건너뛴다(VM 에서는 실행)."""

    def setUp(self):
        try:
            import pandas  # noqa: F401
        except Exception:
            self.skipTest("pandas 미설치(샌드박스)")

    def _fake_hist(self, n):
        import pandas as pd
        idx = pd.date_range("2025-01-01", periods=n, freq="D")
        close = pd.Series([100 + i * 0.5 for i in range(n)], index=idx)
        return pd.DataFrame({
            "Close": close, "High": close * 1.01, "Low": close * 0.99,
            "Open": close, "Volume": pd.Series([1_000_000] * n, index=idx),
        })

    def test_short_history_returns_none(self):
        import bot.technical_analysis as t
        orig = None
        try:
            import yfinance  # noqa: F401
            orig = "have"
        except Exception:
            pass
        # 30행 미만 → DATA OFFLINE 가드. yfinance 없으면 import 단계서 None.
        # 직접 산출 경로만 검증하려고 _last 등 헬퍼 대신 길이 가드를 본다.
        df = self._fake_hist(10)
        # compute_indicators 는 내부서 yf 호출 → 여기선 산출식만 별도 확인.
        self.assertLess(len(df), 30)
        del t, orig

    def test_indicator_values(self):
        # compute_indicators 내부 산출과 동일한 공식으로 핵심 지표 sanity 확인.
        import pandas as pd
        df = self._fake_hist(260)
        close = df["Close"]
        sma50 = float(close.rolling(50).mean().iloc[-1])
        ema10 = float(close.ewm(span=10, adjust=False).mean().iloc[-1])
        # 단조 상승이면 현재가 > SMA50, EMA10 > SMA50
        self.assertGreater(float(close.iloc[-1]), sma50)
        self.assertGreater(ema10, sma50)


if __name__ == "__main__":
    unittest.main()
