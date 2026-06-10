"""trade.price_provider — provider-neutral 시세(KIS) READ-ONLY 레이어 테스트.

라이브 키 없이 transport 주입으로 토큰·현재가 파싱·캐시·기본 OFF를 검증.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from trade import price_provider as pp


def _kis_price_resp(prpr, sdpr, name="삼성전자", ctrt=None):
    out = {"stck_prpr": str(prpr), "stck_sdpr": str(sdpr), "hts_kor_isnm": name}
    if ctrt is not None:
        out["prdy_ctrt"] = str(ctrt)
    return {"output": out, "rt_cd": "0"}


class FakeKIS:
    """transport 스텁 — 토큰 발급 + inquire-price 응답."""
    def __init__(self, prices: dict, *, token="TOK", expires_in=86400):
        self.prices = prices              # {code: (prpr, sdpr, name)}
        self.token = token
        self.expires_in = expires_in
        self.calls = []

    def __call__(self, method, url, *, headers, body=None):
        self.calls.append((method, url))
        if url.endswith("/oauth2/tokenP"):
            return {"access_token": self.token, "expires_in": self.expires_in}
        if "inquire-price" in url:
            code = url.split("fid_input_iscd=")[1][:6]
            if code not in self.prices:
                return {"output": {}}
            prpr, sdpr, name = self.prices[code]
            return _kis_price_resp(prpr, sdpr, name)
        return {}


class _TmpEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._p = [
            mock.patch.object(pp, "_TOKEN_PATH", d / ".kis_token.json"),
            mock.patch.object(pp, "_QUOTE_CACHE", d / "kis_quotes.json"),
            mock.patch.object(pp, "_CODE_CACHE", d / "stock_codes.json"),
            mock.patch.object(pp, "_KRX_MASTER", d / "krx_codes.json"),
            mock.patch.object(pp, "_DATA_DIR", d),
        ]
        for p in self._p:
            p.start()
        # clear=True로 격리 — 앰비언트 TRADE_DATA_GO_KR_KEY가 있으면 auto가
        # dataportal로 가버려 KIS 경로 테스트가 깨지므로(전체 스위트 오염 방지).
        self.env = mock.patch.dict(os.environ, {
            "TRADE_KIS_APPKEY": "AK", "TRADE_KIS_APPSECRET": "AS",
            "TRADE_PRICE_PROVIDER": "auto", "TRADE_KIS_THROTTLE_MS": "0",
        }, clear=True)
        self.env.start()
        pp._KIS_REISSUE_COOLDOWN_UNTIL = 0.0   # self-heal 쿨다운 테스트 격리

    def tearDown(self):
        self.env.stop()
        for p in self._p:
            p.stop()
        self.tmp.cleanup()


class ProviderSelectTests(unittest.TestCase):
    def test_off_when_no_keys(self):
        with mock.patch.dict(os.environ, {"TRADE_PRICE_PROVIDER": "auto"}, clear=True):
            self.assertEqual(pp._provider(), "none")
            self.assertFalse(pp.provider_active())
            # 키 없으면 외부 호출 0 — 빈 결과
            self.assertEqual(pp.get_quotes(["005930"]), {})

    def test_kis_when_keys_present(self):
        with mock.patch.dict(os.environ, {
                "TRADE_KIS_APPKEY": "AK", "TRADE_KIS_APPSECRET": "AS",
                "TRADE_PRICE_PROVIDER": "auto"}, clear=True):
            self.assertEqual(pp._provider(), "kis")
            self.assertTrue(pp.provider_active())

    def test_kis_fallback_env_names_noah_style(self):
        # NOAH 네이밍(KIS_APP_KEY/KIS_APP_SECRET)도 인식 — 한 호스트에서
        # 같은 KIS 앱을 NOAH와 공유할 때 .env 중복 없게.
        with mock.patch.dict(os.environ, {
                "KIS_APP_KEY": "AK", "KIS_APP_SECRET": "AS",
                "TRADE_PRICE_PROVIDER": "auto"}, clear=True):
            self.assertEqual(pp._kis_keys(), ("AK", "AS"))
            self.assertEqual(pp._provider(), "kis")

    def test_trade_prefix_wins_over_noah_fallback(self):
        # 같이 있으면 TRADE_KIS_* 우선(명시 > 폴백).
        with mock.patch.dict(os.environ, {
                "TRADE_KIS_APPKEY": "T", "TRADE_KIS_APPSECRET": "T",
                "KIS_APP_KEY": "N", "KIS_APP_SECRET": "N",
                "TRADE_PRICE_PROVIDER": "auto"}, clear=True):
            self.assertEqual(pp._kis_keys(), ("T", "T"))

    def test_auto_prefers_kis_over_dataportal(self):
        # 둘 다 있으면 KIS(실시간)가 dataportal(EOD T+1)을 이김.
        with mock.patch.dict(os.environ, {
                "KIS_APP_KEY": "AK", "KIS_APP_SECRET": "AS",
                "TRADE_DATA_GO_KR_KEY": "DK",
                "TRADE_PRICE_PROVIDER": "auto"}, clear=True):
            self.assertEqual(pp._provider(), "kis")

    def test_explicit_none_forces_off(self):
        with mock.patch.dict(os.environ, {
                "TRADE_KIS_APPKEY": "AK", "TRADE_KIS_APPSECRET": "AS",
                "TRADE_PRICE_PROVIDER": "none"}, clear=True):
            self.assertEqual(pp._provider(), "none")


class QuoteParseTests(_TmpEnv):
    def test_parse_and_derive_change(self):
        fake = FakeKIS({"005930": (74000, 72000, "삼성전자")})
        q = pp.get_quote("005930", transport=fake)
        self.assertIsNotNone(q)
        self.assertEqual(q.symbol, "005930")
        self.assertEqual(q.name, "삼성전자")
        self.assertEqual(q.price, 74000)
        self.assertEqual(q.prev_close, 72000)
        self.assertEqual(q.change, 2000)                 # 74000-72000
        self.assertAlmostEqual(q.change_pct, 2000/72000*100, places=3)
        self.assertEqual(q.currency, "KRW")

    def test_symbol_normalized_to_6digit(self):
        fake = FakeKIS({"005930": (100, 100, "X")})
        q = pp.get_quote("5930", transport=fake)          # 패딩
        self.assertIsNotNone(q)
        self.assertEqual(q.symbol, "005930")

    def test_missing_symbol_returns_none(self):
        fake = FakeKIS({"005930": (1, 1, "X")})
        self.assertIsNone(pp.get_quote("999999", transport=fake))

    def test_zero_price_dropped(self):
        fake = FakeKIS({"005930": (0, 0, "X")})
        self.assertIsNone(pp.get_quote("005930", transport=fake))

    def test_multi_symbol_batch(self):
        fake = FakeKIS({"005930": (74000, 72000, "삼성전자"),
                        "000660": (180000, 175000, "SK하이닉스")})
        out = pp.get_quotes(["005930", "000660"], transport=fake)
        self.assertEqual(set(out), {"005930", "000660"})
        self.assertEqual(out["000660"].name, "SK하이닉스")


class TokenCacheTests(_TmpEnv):
    def test_token_cached_not_reissued(self):
        # ttl_s=0으로 시세는 매번 재호출시켜 토큰 경로를 강제로 타게 한 뒤,
        # 토큰은 만료 멀어 1회만 발급되는지 확인(시세 캐시와 분리 검증).
        fake = FakeKIS({"005930": (1, 1, "X")}, expires_in=86400)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        token_calls = [c for c in fake.calls if c[1].endswith("/oauth2/tokenP")]
        self.assertEqual(len(token_calls), 1)             # 토큰 1회만 발급

    def test_expired_token_reissued(self):
        # 토큰 만료 임박(expires_in=10 → 만료 60s 전 재사용 규칙상 즉시 stale).
        fake = FakeKIS({"005930": (1, 1, "X")}, expires_in=10)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        token_calls = [c for c in fake.calls if c[1].endswith("/oauth2/tokenP")]
        self.assertEqual(len(token_calls), 2)             # 재발급

    def test_missing_expires_in_defaults_24h_not_instant_expire(self):
        # expires_in 누락 시 24h 기본 → 두 번째 호출도 토큰 재발급 안 함.
        fake = FakeKIS({"005930": (1, 1, "X")})
        fake.expires_in = None
        # FakeKIS는 expires_in 키를 항상 넣으니, 누락 응답을 직접 흉내.
        def tp(method, url, *, headers, body=None):
            if url.endswith("/oauth2/tokenP"):
                return {"access_token": "TOK"}            # expires_in 없음
            return fake(method, url, headers=headers, body=body)
        pp.get_quotes(["005930"], transport=tp, ttl_s=0)
        pp.get_quotes(["005930"], transport=tp, ttl_s=0)
        token_calls = [c for c in fake.calls if c[1].endswith("/oauth2/tokenP")]
        # fake.calls엔 tokenP가 안 잡힘(tp가 가로챔) → 토큰 파일로 검증
        d = json.loads(pp._TOKEN_PATH.read_text(encoding="utf-8"))
        self.assertGreater(d["expires_at"], time.time() + 3600)  # 멀찍이


class QuoteCacheTests(_TmpEnv):
    def test_quote_cache_skips_refetch_within_ttl(self):
        fake = FakeKIS({"005930": (74000, 72000, "삼성전자")})
        pp.get_quotes(["005930"], transport=fake, ttl_s=60)
        price_calls_1 = len([c for c in fake.calls if "inquire-price" in c[1]])
        pp.get_quotes(["005930"], transport=fake, ttl_s=60)   # 캐시 신선
        price_calls_2 = len([c for c in fake.calls if "inquire-price" in c[1]])
        self.assertEqual(price_calls_1, price_calls_2)        # 재호출 없음

    def test_quote_cache_refetches_when_stale(self):
        fake = FakeKIS({"005930": (74000, 72000, "삼성전자")})
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)    # 즉시 만료
        time.sleep(0.01)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        price_calls = len([c for c in fake.calls if "inquire-price" in c[1]])
        self.assertEqual(price_calls, 2)

    def test_default_ttl_uses_recommended_not_60s(self):
        # ttl_s 미지정이면 recommended_ttl() 기본 — 옛 60s 고정 아님(렌더 함정 제거).
        # 100초 지난 캐시는 옛 기본(60s)이면 만료(재호출)지만, 새 기본이면 신선.
        fake = FakeKIS({"005930": (74000, 72000, "삼성전자")})
        pp.get_quotes(["005930"], transport=fake)             # 캐시 적재(기본 ttl)
        n1 = len([c for c in fake.calls if "inquire-price" in c[1]])
        cache = pp._load_cache()
        cache["005930"]["_cached_at"] = time.time() - 100
        pp._save_cache(cache)
        with mock.patch.object(pp, "recommended_ttl", return_value=3600):
            pp.get_quotes(["005930"], transport=fake)         # 기본=recommended=3600
        n2 = len([c for c in fake.calls if "inquire-price" in c[1]])
        self.assertEqual(n1, n2)                              # 100s>60s인데도 재호출 0

    def test_default_ttl_honors_cache_ttl_env_override(self):
        # TRADE_PRICE_CACHE_TTL 명시 설정시 옛 호환 — 그 값이 미지정 기본으로 쓰임.
        fake = FakeKIS({"005930": (74000, 72000, "삼성전자")})
        pp.get_quotes(["005930"], transport=fake)
        cache = pp._load_cache()
        cache["005930"]["_cached_at"] = time.time() - 100
        pp._save_cache(cache)
        with mock.patch.dict(os.environ, {"TRADE_PRICE_CACHE_TTL": "10"}):
            pp.get_quotes(["005930"], transport=fake)         # 기본=10s → 100s 만료
        n = len([c for c in fake.calls if "inquire-price" in c[1]])
        self.assertEqual(n, 2)                                # 재호출됨


class FakeDataPortal:
    """주식시세정보 transport 스텁 — likeItmsNm/likeSrtnCd 부분일치 필터."""
    def __init__(self, items, *, wrap=False, single_as_dict=False):
        self.items = items
        self.wrap = wrap
        self.single_as_dict = single_as_dict
        self.calls = []

    def __call__(self, method, url, *, headers, body=None):
        from urllib.parse import urlparse, parse_qs
        self.calls.append((method, url))
        q = parse_qs(urlparse(url).query)
        matched = list(self.items)
        if "likeItmsNm" in q:
            v = q["likeItmsNm"][0]
            matched = [i for i in self.items if v in i["itmsNm"]]
        elif "likeSrtnCd" in q:
            v = q["likeSrtnCd"][0]
            matched = [i for i in self.items if v in i["srtnCd"]]
        if not matched:
            body = {"items": {}, "totalCount": "0"}
        else:
            item = matched[0] if (self.single_as_dict and len(matched) == 1) else matched
            body = {"items": {"item": item}, "totalCount": str(len(matched))}
        return {"response": {"body": body}} if self.wrap else {"body": body}


_DP_ITEMS = [
    {"srtnCd": "005930", "itmsNm": "삼성전자", "clpr": "74000", "vs": 2000,
     "fltRt": 2.78, "basDt": "20260603", "mrktCtg": "KOSPI"},
    {"srtnCd": "005930", "itmsNm": "삼성전자", "clpr": "72000", "vs": -1000,
     "fltRt": -1.37, "basDt": "20260602", "mrktCtg": "KOSPI"},   # 전 영업일
    {"srtnCd": "005935", "itmsNm": "삼성전자우", "clpr": "60000", "vs": -500,
     "fltRt": -0.83, "basDt": "20260603", "mrktCtg": "KOSPI"},
    {"srtnCd": "000660", "itmsNm": "SK하이닉스", "clpr": "180000", "vs": 5000,
     "fltRt": 2.86, "basDt": "20260603", "mrktCtg": "KOSPI"},
]


class _DPEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._p = [
            mock.patch.object(pp, "_TOKEN_PATH", d / ".kis_token.json"),
            mock.patch.object(pp, "_QUOTE_CACHE", d / "kis_quotes.json"),
            mock.patch.object(pp, "_CODE_CACHE", d / "stock_codes.json"),
            mock.patch.object(pp, "_KRX_MASTER", d / "krx_codes.json"),
            mock.patch.object(pp, "_DATA_DIR", d),
        ]
        for p in self._p:
            p.start()
        self.env = mock.patch.dict(os.environ, {
            "TRADE_DATA_GO_KR_KEY": "DK", "TRADE_PRICE_PROVIDER": "auto",
        }, clear=True)
        self.env.start()
        pp._KIS_REISSUE_COOLDOWN_UNTIL = 0.0   # self-heal 쿨다운 테스트 격리

    def tearDown(self):
        self.env.stop()
        for p in self._p:
            p.stop()
        self.tmp.cleanup()


class DataPortalTests(_DPEnv):
    def test_auto_selects_dataportal_when_datagokr_key(self):
        self.assertEqual(pp._provider(), "dataportal")
        self.assertTrue(pp.provider_active())

    def test_by_name_exact_match_excludes_preferred_share(self):
        # "삼성전자"로 검색하면 삼성전자우도 오지만, 정확일치로 005930만.
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.get_quotes_by_name(["삼성전자"], transport=fake)
        self.assertIn("삼성전자", out)
        q = out["삼성전자"]
        self.assertEqual(q.symbol, "005930")        # 우선주(005935) 아님
        self.assertEqual(q.price, 74000)
        self.assertEqual(q.change, 2000)
        self.assertAlmostEqual(q.change_pct, 2.78)
        self.assertEqual(q.prev_close, 72000)       # 74000 - 2000

    def test_by_name_picks_latest_basdt(self):
        # 005930이 두 영업일(20260602/20260603) → 최신 20260603(74000).
        fake = FakeDataPortal(_DP_ITEMS)
        q = pp.get_quotes_by_name(["삼성전자"], transport=fake)["삼성전자"]
        self.assertEqual(q.price, 74000)

    def test_by_name_unknown_omitted(self):
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.get_quotes_by_name(["없는회사"], transport=fake)
        self.assertEqual(out, {})                    # 틀린 종목 안 붙임

    def test_by_code(self):
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.get_quotes(["000660"], transport=fake)
        self.assertEqual(out["000660"].name, "SK하이닉스")

    def test_handles_single_item_dict_shape(self):
        # 결과 1건이면 data.go.kr이 item을 list 아닌 dict로 줄 수 있음.
        fake = FakeDataPortal(_DP_ITEMS, single_as_dict=True)
        out = pp.get_quotes_by_name(["SK하이닉스"], transport=fake)
        self.assertEqual(out["SK하이닉스"].symbol, "000660")

    def test_handles_response_wrapper(self):
        fake = FakeDataPortal(_DP_ITEMS, wrap=True)
        out = pp.get_quotes_by_name(["SK하이닉스"], transport=fake)
        self.assertEqual(out["SK하이닉스"].symbol, "000660")

    def test_name_cache_skips_refetch(self):
        fake = FakeDataPortal(_DP_ITEMS)
        pp.get_quotes_by_name(["삼성전자"], transport=fake, ttl_s=60)
        n1 = len(fake.calls)
        pp.get_quotes_by_name(["삼성전자"], transport=fake, ttl_s=60)
        self.assertEqual(len(fake.calls), n1)        # 캐시 신선 → 재호출 0

    def test_by_name_empty_when_provider_not_dataportal(self):
        with mock.patch.dict(os.environ, {"TRADE_PRICE_PROVIDER": "none"}):
            self.assertEqual(pp.get_quotes_by_name(["삼성전자"],
                                                   transport=FakeDataPortal(_DP_ITEMS)), {})

    def test_transport_exception_safe(self):
        def boom(*a, **k):
            raise RuntimeError("down")
        self.assertEqual(pp.get_quotes_by_name(["삼성전자"], transport=boom), {})


class FakeHybrid:
    """data.go.kr(이름→코드) + KIS(코드→시세) 둘 다 응답하는 transport."""
    def __init__(self, dp_items, kis_prices, *, token="TOK"):
        self.dp = FakeDataPortal(dp_items)
        self.kis = FakeKIS(kis_prices, token=token)
        self.calls = []

    def __call__(self, method, url, *, headers, body=None):
        self.calls.append(url)
        if "getStockPriceInfo" in url:
            return self.dp(method, url, headers=headers, body=body)
        return self.kis(method, url, headers=headers, body=body)


class RecommendedTtlTests(unittest.TestCase):
    def test_market_hours_short(self):
        from datetime import datetime
        with mock.patch.dict(os.environ, {}, clear=True):
            t = datetime(2026, 6, 5, 11, 0, tzinfo=pp._KST)   # 금 11:00 장중
            self.assertEqual(pp.recommended_ttl(t), 1800)     # 30분(5분 정적 현실 반영)

    def test_after_close_long(self):
        from datetime import datetime
        with mock.patch.dict(os.environ, {}, clear=True):
            t = datetime(2026, 6, 5, 17, 0, tzinfo=pp._KST)   # 금 17:00 마감 후
            self.assertEqual(pp.recommended_ttl(t), 21600)

    def test_weekend_long(self):
        from datetime import datetime
        with mock.patch.dict(os.environ, {}, clear=True):
            t = datetime(2026, 6, 6, 11, 0, tzinfo=pp._KST)   # 토 11:00
            self.assertEqual(pp.recommended_ttl(t), 21600)


class _KisHybridEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._p = [
            mock.patch.object(pp, "_TOKEN_PATH", d / ".kis_token.json"),
            mock.patch.object(pp, "_QUOTE_CACHE", d / "kis_quotes.json"),
            mock.patch.object(pp, "_CODE_CACHE", d / "stock_codes.json"),
            mock.patch.object(pp, "_KRX_MASTER", d / "krx_codes.json"),
            mock.patch.object(pp, "_DATA_DIR", d),
        ]
        for p in self._p:
            p.start()
        self.env = mock.patch.dict(os.environ, {
            "TRADE_KIS_APPKEY": "AK", "TRADE_KIS_APPSECRET": "AS",
            "TRADE_DATA_GO_KR_KEY": "DK", "TRADE_PRICE_PROVIDER": "kis",
            "TRADE_KIS_THROTTLE_MS": "0",
        }, clear=True)
        self.env.start()
        pp._KIS_REISSUE_COOLDOWN_UNTIL = 0.0   # self-heal 쿨다운 테스트 격리

    def tearDown(self):
        self.env.stop()
        for p in self._p:
            p.stop()
        self.tmp.cleanup()


class KisHybridTests(_KisHybridEnv):
    def test_name_resolves_via_datagokr_price_via_kis(self):
        # 이름→코드는 data.go.kr, 시세는 KIS 실시간.
        h = FakeHybrid(_DP_ITEMS, {"005930": (351500, 360500, "삼성전자"),
                                   "000660": (180000, 175000, "SK하이닉스")})
        out = pp.get_quotes_by_name(["삼성전자", "SK하이닉스"], transport=h)
        self.assertEqual(out["삼성전자"].price, 351500)      # KIS 가격
        self.assertEqual(out["SK하이닉스"].symbol, "000660")
        # KIS inquire-price가 실제로 호출됨(실시간 경로)
        self.assertTrue(any("inquire-price" in u for u in h.calls))

    def test_code_cache_durable_skips_resolve(self):
        h = FakeHybrid(_DP_ITEMS, {"005930": (351500, 360500, "삼성전자")})
        pp.get_quotes_by_name(["삼성전자"], transport=h, ttl_s=0)
        dp1 = len([u for u in h.calls if "getStockPriceInfo" in u])
        pp.get_quotes_by_name(["삼성전자"], transport=h, ttl_s=0)
        dp2 = len([u for u in h.calls if "getStockPriceInfo" in u])
        self.assertEqual(dp1, dp2)        # 코드 캐시 → 재-resolve 0 (가격만 재호출)

    def test_unresolved_name_omitted(self):
        h = FakeHybrid(_DP_ITEMS, {"005930": (1, 1, "삼성전자")})
        out = pp.get_quotes_by_name(["없는회사"], transport=h)
        self.assertEqual(out, {})


class ResolveCodesTests(_KisHybridEnv):
    def test_exact_match_and_negative_cache(self):
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.resolve_codes(["삼성전자"], transport=fake)
        self.assertEqual(out["삼성전자"], "005930")    # 우선주 아님
        # 미일치(없는회사)는 음성 캐시 → 재호출 안 함
        pp.resolve_codes(["없는회사"], transport=fake)
        n1 = len(fake.calls)
        pp.resolve_codes(["없는회사"], transport=fake)
        self.assertEqual(len(fake.calls), n1)

    def test_no_key_returns_empty(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pp.resolve_codes(["삼성전자"],
                                              transport=FakeDataPortal(_DP_ITEMS)), {})


class SymbolCapTests(_DPEnv):
    def test_caps_symbols(self):
        with mock.patch.object(pp, "_MAX_SYMBOLS", 2):
            fake = FakeDataPortal(_DP_ITEMS)
            pp.get_quotes_by_name(["삼성전자", "SK하이닉스", "삼성전자우"], transport=fake)
            # 3개 요청했지만 상한 2 → getStockPriceInfo 호출 ≤ 2
            self.assertLessEqual(len(fake.calls), 2)


class SplitNamesTests(unittest.TestCase):
    def test_splits_composites_and_strips_etc(self):
        self.assertEqual(pp.split_names(["GST / 유니셈 등"]), ["GST", "유니셈"])
        self.assertEqual(pp.split_names(["HD현대중공업 / HD현대미포조선"]),
                         ["HD현대중공업", "HD현대미포조선"])

    def test_dedup_and_drop_etc_token(self):
        self.assertEqual(pp.split_names(["삼성전자", "삼성전자"]), ["삼성전자"])
        self.assertEqual(pp.split_names(["등"]), [])
        self.assertEqual(pp.split_names([""]), [])

    def test_comma_separator(self):
        self.assertEqual(pp.split_names(["세아제강, 휴스틸, 넥스틸"]),
                         ["세아제강", "휴스틸", "넥스틸"])
        self.assertEqual(pp.split_names(["에코프로비엠, LG화학"]),
                         ["에코프로비엠", "LG화학"])

    def test_space_separated_multi_tokens(self):
        # 공백으로 묶인 다중 종목 — 원본도 보존하고 각 토큰도 등록(원본은
        # 어차피 미해결 → 토큰별 매칭이 작동).
        out = pp.split_names(["삼아알미늄 DI동일 동원시스템즈 롯데케미칼"])
        for t in ("삼아알미늄", "DI동일", "동원시스템즈", "롯데케미칼"):
            self.assertIn(t, out)

    def test_period_tokens_left_intact(self):
        # JYP Ent. / LS ELECTRIC 같이 마침표/영문 포함은 분리 안 함(KRX 정식
        # 명칭일 수 있음). 원본 그대로만 등록.
        self.assertEqual(pp.split_names(["JYP Ent."]), ["JYP Ent."])

    def test_parser_prefix_stripped(self):
        self.assertEqual(pp.split_names(["관련종목 : LG전자"]), ["LG전자"])
        self.assertEqual(pp.split_names(["관련종목: 삼성전자"]), ["삼성전자"])

    def test_prose_garbage_filtered(self):
        # 제품 설명 프로즈는 종목 후보에서 제거(괄호·%·치수·kVA·따옴표·길이).
        garbage = [
            "보툴리눔 톡신 (강원 횡성_중국) 제네톡스 \"보타원\"은 모두 수출용",
            "000kVA 초과) + 배전반 + 변환기",
            "90%이상", "1000V", "100kVA에서", '"보타원"은', "(강원", "+",
            "소형 변압기(100kVA 이하)",
        ]
        for g in garbage:
            self.assertEqual(pp.split_names([g]), [], g)

    def test_looks_like_stock(self):
        self.assertTrue(pp._looks_like_stock("삼성전자"))
        self.assertTrue(pp._looks_like_stock("HD현대미포조선"))
        self.assertTrue(pp._looks_like_stock("S-Oil"))
        self.assertFalse(pp._looks_like_stock("(강원"))
        self.assertFalse(pp._looks_like_stock("90%이상"))
        self.assertFalse(pp._looks_like_stock("000kVA"))
        self.assertFalse(pp._looks_like_stock("그"))           # 1자
        self.assertFalse(pp._looks_like_stock("보툴리눔 톡신 강원 횡성중국"))  # 길이

    def test_product_blocklist_dropped(self):
        # 품목·제품명이 회사로 새지 않게(회사별 뷰 가짜 회사 방지).
        for prod in ("수산화리튬", "동박", "광섬유", "소주", "젤라틴", "반도체", "웨이퍼"):
            self.assertEqual(pp.split_names([prod]), [], prod)
        # 전 토큰이 제품명인 2어절도 제외
        self.assertEqual(pp.split_names(["반도체 웨이퍼"]), [])
        self.assertEqual(pp.split_names(["검사용 장비"]), [])
        self.assertEqual(pp.split_names(["소자의 측정, 검사용 장비"]), [])
        self.assertEqual(pp.split_names(["인터페이스보드 프로브 카드"]), [])  # 길이15
        # 실제 회사는 정확일치라 안 걸림('한미반도체'≠'반도체')
        self.assertEqual(pp.split_names(["삼성전자"]), ["삼성전자"])
        self.assertEqual(pp.split_names(["한미반도체"]), ["한미반도체"])
        self.assertEqual(pp.split_names(["GST / 유니셈 등"]), ["GST", "유니셈"])


class ResolverAliasTests(_DPEnv):
    def test_typo_alias_esteai_resolves_as_esti(self):
        # 운영자 확인 오타 에스테아이→에스티아이로 질의(별도 종목으로 안 샘).
        seen = {}
        items = [{"srtnCd": "039440", "itmsNm": "에스티아이", "clpr": "20000",
                  "vs": 0, "fltRt": 0, "basDt": "20260604", "mrktCtg": "KOSDAQ"}]

        def fake(method, url, *, headers, body=None):
            from urllib.parse import urlparse, parse_qs
            seen["q"] = parse_qs(urlparse(url).query).get("likeItmsNm", [""])[0]
            return {"response": {"body": {"items": {"item": items}, "totalCount": "1"}}}
        out = pp.resolve_codes(["에스테아이"], transport=fake)
        self.assertEqual(out["에스테아이"], "039440")
        self.assertEqual(seen["q"], "에스티아이")          # alias로 질의

    def test_direct_code_bypasses_datagokr(self):
        # _DIRECT_CODES 항목은 외부 호출 0으로 즉시 코드 반환.
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.resolve_codes(
            ["HD현대건설기계", "HD현대미포조선", "코오롱플라스틱"], transport=fake)
        self.assertEqual(out["HD현대건설기계"], "267270")
        self.assertEqual(out["HD현대미포조선"], "329180")   # → HD현대중공업 프록시
        self.assertEqual(out["코오롱플라스틱"], "120110")   # → 코오롱인더스트리 프록시
        self.assertEqual(len(fake.calls), 0)

    def test_name_alias_queries_aliased_name(self):
        # alias가 적용된 KRX 표기로 likeItmsNm 질의됨(한국타이어→앤테크놀로지).
        seen_query = {}
        items = [{"srtnCd": "161390", "itmsNm": "한국타이어앤테크놀로지",
                  "clpr": "50000", "vs": 0, "fltRt": 0, "basDt": "20260605",
                  "mrktCtg": "KOSPI"}]

        def fake(method, url, *, headers, body=None):
            from urllib.parse import urlparse, parse_qs
            seen_query["q"] = parse_qs(urlparse(url).query).get("likeItmsNm",
                                                                 [""])[0]
            return {"response": {"body": {"items": {"item": items},
                                          "totalCount": "1"}}}
        out = pp.resolve_codes(["한국타이어"], transport=fake)
        self.assertEqual(out["한국타이어"], "161390")
        self.assertEqual(seen_query["q"], "한국타이어앤테크놀로지")   # alias로 질의

    def test_direct_code_spc_group_proxy(self):
        # SPC그룹(비상장 그룹) → SPC삼립(005610) 프록시, 외부 호출 0(운영자 확인).
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.resolve_codes(["SPC그룹"], transport=fake)
        self.assertEqual(out["SPC그룹"], "005610")
        self.assertEqual(len(fake.calls), 0)

    def test_alias_gnbs_queries_eco_name(self):
        # BeOn '지앤비에스' → KRX '지앤비에스에코' 표기로 likeItmsNm 질의(운영자 확인).
        seen_query = {}
        # 코드는 런타임에 data.go.kr가 찾음 — 여기선 alias 질의명/해석 plumbing만 검증
        # (아래 코드는 테스트 픽스처).
        items = [{"srtnCd": "439090", "itmsNm": "지앤비에스에코", "clpr": "10000",
                  "vs": 0, "fltRt": 0, "basDt": "20260605", "mrktCtg": "KOSDAQ"}]

        def fake(method, url, *, headers, body=None):
            from urllib.parse import urlparse, parse_qs
            seen_query["q"] = parse_qs(urlparse(url).query).get("likeItmsNm",
                                                               [""])[0]
            return {"response": {"body": {"items": {"item": items},
                                          "totalCount": "1"}}}
        out = pp.resolve_codes(["지앤비에스"], transport=fake)
        self.assertEqual(out["지앤비에스"], "439090")
        self.assertEqual(seen_query["q"], "지앤비에스에코")            # alias로 질의

    def test_cache_version_invalidates_old_neg_cache(self):
        # 이전 음성 캐시(_v 없음)는 무시되고, alias 덕에 새로 양성 등록됨.
        old = {"HD현대건설기계": {"code": "", "_cached_at": time.time()}}
        pp._CODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        pp._CODE_CACHE.write_text(__import__("json").dumps(old))
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.resolve_codes(["HD현대건설기계"], transport=fake)
        self.assertEqual(out["HD현대건설기계"], "267270")    # direct code 적용


class CacheOnlyTests(_DPEnv):
    def test_fetch_false_empty_cache_no_api(self):
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.get_quotes_by_name(["삼성전자"], transport=fake, fetch=False)
        self.assertEqual(out, {})
        self.assertEqual(len(fake.calls), 0)         # 외부 호출 0(렌더 안전)

    def test_fetch_false_returns_warm_cache(self):
        fake = FakeDataPortal(_DP_ITEMS)
        pp.get_quotes_by_name(["삼성전자"], transport=fake, ttl_s=600)   # 워밍
        n = len(fake.calls)
        out = pp.get_quotes_by_name(["삼성전자"], transport=fake,
                                    ttl_s=600, fetch=False)
        self.assertIn("삼성전자", out)
        self.assertEqual(len(fake.calls), n)         # 캐시만 — 추가 호출 0

    def test_resolve_codes_fetch_false_cache_only(self):
        fake = FakeDataPortal(_DP_ITEMS)
        # 콜드 캐시에서 fetch=False면 빈 결과 + 호출 0
        self.assertEqual(pp.resolve_codes(["삼성전자"], transport=fake,
                                          fetch=False), {})
        self.assertEqual(len(fake.calls), 0)


class SafetyTests(_TmpEnv):
    def test_transport_exception_returns_empty(self):
        def boom(*a, **k):
            raise RuntimeError("network down")
        self.assertEqual(pp.get_quotes(["005930"], transport=boom), {})

    def test_no_orders_or_trading_surface(self):
        # 봇 철학: 읽기 전용. 주문/매매 함수가 모듈에 없어야 함.
        for forbidden in ("place_order", "buy", "sell", "submit_order",
                          "cancel_order", "modify_order"):
            self.assertFalse(hasattr(pp, forbidden), forbidden)


class FakeKISReauth:
    """공유앱 토큰 무효화 시뮬 — 서버측 'valid' 토큰과 다른 bearer는 빈손,
    재발급(tokenP) 후의 토큰으로만 시세를 준다. self-heal 경로 검증용."""
    def __init__(self, prices: dict, *, reissue_works=True):
        self.prices = prices
        self.reissue_works = reissue_works
        self.valid = None              # 서버측 현재 유효 토큰(처음엔 없음)
        self.token_calls = 0
        self.calls = []

    def __call__(self, method, url, *, headers, body=None):
        self.calls.append(url)
        if url.endswith("/oauth2/tokenP"):
            self.token_calls += 1
            if not self.reissue_works:
                return {}              # 발급 실패(엔드포인트 장애)
            self.valid = f"TOK{self.token_calls}"
            return {"access_token": self.valid, "expires_in": 86400}
        if "inquire-price" in url:
            bearer = (headers.get("authorization", "") or "").replace("Bearer ", "")
            if bearer != self.valid:   # 죽은/무효 토큰 → 빈손(서버 무효화 흉내)
                return {"output": {}, "rt_cd": "1", "msg_cd": "EGW00123"}
            code = url.split("fid_input_iscd=")[1][:6]
            if code not in self.prices:
                return {"output": {}}
            prpr, sdpr, name = self.prices[code]
            return _kis_price_resp(prpr, sdpr, name)
        return {}


class TokenSelfHealTests(_TmpEnv):
    def _seed_dead_token(self):
        # 만료는 멀어(평소엔 재사용) 서버에서만 죽은 토큰을 캐시에 심는다.
        pp._TOKEN_PATH.write_text(json.dumps(
            {"access_token": "DEAD", "expires_at": time.time() + 86400}))

    def test_dead_cached_token_reissued_and_recovers(self):
        # 회귀의 핵심: 캐시 토큰이 서버에서 무효화돼 전건 0 → 폐기+재발급 1회로 복구.
        self._seed_dead_token()
        fake = FakeKISReauth({"005930": (74000, 72000, "삼성전자")})
        out = pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        self.assertIn("005930", out)
        self.assertEqual(out["005930"].price, 74000)
        self.assertEqual(fake.token_calls, 1)         # 재발급 정확히 1회로 복구

    def test_cooldown_blocks_repeated_reissue(self):
        # 데이터 없는 코드(시세 0건)에 self-heal이 매 호출 좋은 토큰을 버리지 않게:
        # 첫 호출만 초기발급+self-heal재발급=2, 60s 내 두번째는 쿨다운으로 skip.
        fake = FakeKISReauth({}, reissue_works=True)  # 어떤 코드도 데이터 없음
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        self.assertEqual(fake.token_calls, 2)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        self.assertEqual(fake.token_calls, 2)         # 쿨다운 → 추가 재발급 없음


class KisNoEodFallbackTests(_KisHybridEnv):
    def _seed_eod(self, code, price, basdt="20260604"):
        # 캐시에 EOD 소스 잔존분을 직접 심는다(예: 과거 폴백/다운그레이드 잔재).
        q = pp.Quote(symbol=code, name="x", price=price, change=0, change_pct=0,
                     prev_close=price, currency="KRW", ts="x",
                     source="eod", as_of=basdt)
        pp._QUOTE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        pp._QUOTE_CACHE.write_text(
            json.dumps({code: {**q.as_dict(), "_cached_at": time.time()}},
                       ensure_ascii=False), encoding="utf-8")

    def test_kis_empty_returns_blank_no_eod_fallback(self):
        # KIS가 못 주면 빈칸 — data.go.kr EOD 폴백 안 씀(오정보 방지). likeSrtnCd
        # (EOD 코드조회)는 호출되지 않아야.
        h = FakeHybrid(_DP_ITEMS, {})                 # KIS 전건 실패
        out = pp.get_quotes_by_name(["삼성전자"], transport=h)
        self.assertEqual(out, {})                     # 폴백 없음 → 빈칸
        self.assertFalse(any("likeSrtnCd" in u for u in h.calls))

    def test_kis_healthy_makes_no_eod_calls(self):
        h = FakeHybrid(_DP_ITEMS, {"005930": (351500, 360500, "삼성전자")})
        out = pp.get_quotes_by_name(["삼성전자"], transport=h, ttl_s=0)
        self.assertEqual(out["삼성전자"].price, 351500)   # KIS 실시간
        self.assertFalse(any("likeSrtnCd" in u for u in h.calls))

    def test_eod_entry_hidden_under_kis_on_render(self):
        # prov=kis면 캐시의 EOD 잔존분은 렌더(fetch=False)에서 표시 안 함 → 빈칸.
        self._seed_eod("005930", 74000)
        out = pp.get_quotes(["005930"], transport=FakeHybrid(_DP_ITEMS, {}),
                            ttl_s=21600, fetch=False)
        self.assertEqual(out, {})                     # EOD 소스 → kis에서 숨김

    def test_eod_entry_reclaimed_by_kis_on_warm(self):
        # 워밍(fetch=True)은 EOD 잔존분을 KIS로 탈환(신선해도) → 오래된 EOD 청소.
        self._seed_eod("005930", 74000)
        out = pp.get_quotes(
            ["005930"], transport=FakeHybrid(_DP_ITEMS, {"005930": (351500, 360500, "삼성전자")}),
            ttl_s=21600, fetch=True)
        self.assertEqual(out["005930"].price, 351500)  # KIS로 교체
        self.assertEqual(out["005930"].source, "kis")

    def test_dataportal_item_tagged_eod_with_asof(self):
        # data.go.kr item → Quote는 source='eod'+기준일(dataportal 전용 모드/라벨용).
        q = pp._quote_from_dp_item({"srtnCd": "005930", "itmsNm": "삼성전자",
                                    "clpr": "74000", "vs": 2000, "fltRt": 2.78,
                                    "basDt": "20260604"})
        self.assertEqual(q.source, "eod")
        self.assertEqual(q.as_of, "20260604")

    def test_old_cache_without_source_loads_as_kis(self):
        # source/as_of 없는 구캐시도 Quote(**d) 기본값으로 로드(KIS 취급, 비-soft).
        old = {"005930": {"symbol": "005930", "name": "삼성전자", "price": 74000,
               "change": 1000, "change_pct": 1.3, "prev_close": 73000,
               "currency": "KRW", "ts": "x", "_cached_at": time.time()}}
        pp._QUOTE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        pp._QUOTE_CACHE.write_text(json.dumps(old))
        out = pp.get_quotes(["005930"], transport=FakeKIS({}), ttl_s=600)
        self.assertEqual(out["005930"].source, "kis")  # 기본값 적용, refetch 안 함


class FakeKrxListing:
    """getStockPriceInfo 이름필터 없는 전수 페이지네이션 스텁 — page1에 rows,
    page2부터 빈 응답(빌더가 중단)."""
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, method, url, *, headers, body=None):
        from urllib.parse import urlparse, parse_qs
        self.calls.append(url)
        page = int(parse_qs(urlparse(url).query).get("pageNo", ["1"])[0])
        items = self.rows if page == 1 else []
        return {"response": {"body": {"items": {"item": items},
                                      "totalCount": str(len(items))}}}


class KrxMasterFetchTests(_DPEnv):
    def test_fetch_builds_name_to_code_dedup(self):
        rows = [
            {"srtnCd": "005930", "itmsNm": "삼성전자", "basDt": "20260604"},
            {"srtnCd": "000660", "itmsNm": "SK하이닉스", "basDt": "20260604"},
            {"srtnCd": "005930", "itmsNm": "삼성전자", "basDt": "20260603"},  # 구일자 dup
        ]
        m = pp.fetch_krx_master(transport=FakeKrxListing(rows))
        self.assertEqual(m["삼성전자"], "005930")
        self.assertEqual(m["SK하이닉스"], "000660")
        self.assertEqual(len(m), 2)                       # 코드 기준 dedup

    def test_fetch_no_key_empty(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pp.fetch_krx_master(transport=FakeKrxListing([])), {})


class ResolveMasterTests(_DPEnv):
    def _write_master(self, d):
        pp._KRX_MASTER.parent.mkdir(parents=True, exist_ok=True)
        pp._KRX_MASTER.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def test_master_resolves_without_api(self):
        # 마스터에 있으면 data.go.kr 호출 0으로 즉시 해석(공백 정규화 매칭 포함).
        self._write_master({"삼성전자": "005930", "SK 하이닉스": "000660"})
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.resolve_codes(["삼성전자", "SK하이닉스"], transport=fake)
        self.assertEqual(out["삼성전자"], "005930")
        self.assertEqual(out["SK하이닉스"], "000660")     # 'SK 하이닉스'(공백) 정규화
        self.assertEqual(len(fake.calls), 0)              # 외부 호출 0

    def test_master_resolves_on_render_fetch_false(self):
        # 렌더(fetch=False)도 마스터로 즉시 해석 — 커버리지 즉시 상승.
        self._write_master({"삼성전자": "005930"})
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.resolve_codes(["삼성전자"], transport=fake, fetch=False)
        self.assertEqual(out["삼성전자"], "005930")
        self.assertEqual(len(fake.calls), 0)

    def test_fallback_to_datagokr_when_not_in_master(self):
        self._write_master({"삼성전자": "005930"})         # SK하이닉스는 마스터에 없음
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.resolve_codes(["SK하이닉스"], transport=fake)
        self.assertEqual(out["SK하이닉스"], "000660")      # data.go.kr 폴백
        self.assertGreaterEqual(len(fake.calls), 1)

    def test_master_alone_resolves_without_key(self):
        # 키 없어도 마스터만으로 해석(마스터는 로컬).
        self._write_master({"삼성전자": "005930"})
        with mock.patch.dict(os.environ, {}, clear=True):
            out = pp.resolve_codes(["삼성전자"], transport=FakeDataPortal(_DP_ITEMS))
        self.assertEqual(out["삼성전자"], "005930")


class RenderCapTests(_DPEnv):
    def test_get_quotes_by_name_fetch_false_ignores_cap(self):
        # 렌더(fetch=False)는 _MAX_SYMBOLS 캡을 무시하고 캐시의 전 종목 반환 —
        # 관련종목이 캡보다 많아도 안 잘림(254→376 회귀의 직접 원인 수정).
        fake = FakeDataPortal(_DP_ITEMS)
        names = ["삼성전자", "SK하이닉스"]
        pp.get_quotes_by_name(names, transport=fake, ttl_s=600)        # 워밍
        with mock.patch.object(pp, "_MAX_SYMBOLS", 1):                 # 캡 1로 축소
            got = pp.get_quotes_by_name(names, transport=fake, ttl_s=600, fetch=False)
        self.assertEqual(set(got), {"삼성전자", "SK하이닉스"})         # 캡 1이어도 2개 다

    def test_get_quotes_fetch_false_ignores_cap(self):
        fake = FakeDataPortal(_DP_ITEMS)
        pp.get_quotes(["005930", "000660"], transport=fake, ttl_s=600)  # 워밍
        with mock.patch.object(pp, "_MAX_SYMBOLS", 1):
            got = pp.get_quotes(["005930", "000660"], transport=fake, ttl_s=600,
                                fetch=False)
        self.assertEqual(set(got), {"005930", "000660"})

    def test_fetch_true_still_capped(self):
        # 워머/콜 경로(fetch=True)는 캡 유지(콜 폭주 방지) — 회귀 아님 확인.
        fake = FakeDataPortal(_DP_ITEMS)
        with mock.patch.object(pp, "_MAX_SYMBOLS", 1):
            pp.get_quotes_by_name(["삼성전자", "SK하이닉스", "삼성전자우"],
                                  transport=fake, fetch=True)
        self.assertLessEqual(len([c for c in fake.calls if "likeItmsNm" in c[1]]), 1)


class ProxyDirectCodeTests(_DPEnv):
    def test_operator_verified_proxies_resolve_offline(self):
        # 운영자 도메인 확인 프록시(비상장 자회사→상장 모회사/관계사) — 직접코드라
        # 외부 호출 0으로 해석. 동일 회사 아님(프록시).
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.resolve_codes(
            ["LS전선", "HD현대삼호중공업", "이녹스리튬", "ELECTRIC"], transport=fake)
        self.assertEqual(out["LS전선"], "006260")
        self.assertEqual(out["HD현대삼호중공업"], "267250")
        self.assertEqual(out["이녹스리튬"], "088390")
        self.assertEqual(out["ELECTRIC"], "010120")
        self.assertEqual(len(fake.calls), 0)                          # 직접코드 → API 0


class OverrideTests(unittest.TestCase):
    """운영자 /map 런타임 오버라이드 — _DIRECT_CODES보다 우선, 마스터·키 없어도 동작."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._p = [
            mock.patch.object(pp, "_OVERRIDES_PATH", d / "stock_overrides.json"),
            mock.patch.object(pp, "_CODE_CACHE", d / "stock_codes.json"),
            mock.patch.object(pp, "_KRX_MASTER", d / "krx_codes.json"),
            mock.patch.object(pp, "_DATA_DIR", d),
        ]
        for p in self._p:
            p.start()
        pp._overrides_memo.update(key=None, data={})          # 메모 격리
        self.env = mock.patch.dict(os.environ, {"TRADE_DATA_GO_KR_KEY": "DK"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for p in self._p:
            p.stop()
        pp._overrides_memo.update(key=None, data={})
        self.tmp.cleanup()

    def test_set_list_remove_roundtrip(self):
        pp.set_override("하나코스", "226340")
        self.assertEqual(pp.list_overrides().get("하나코스"), "226340")
        self.assertTrue(pp.remove_override("하나코스"))
        self.assertNotIn("하나코스", pp.list_overrides())
        self.assertFalse(pp.remove_override("없는것"))        # 없으면 False

    def test_set_rejects_non_6digit(self):
        pp.set_override("X", "12345")        # 5자리 → 저장 안 함
        pp.set_override("Y", "abc")          # 숫자 아님 → 저장 안 함
        self.assertEqual(pp.list_overrides(), {})

    def test_override_wins_over_direct_code(self):
        pp.set_override("HD현대건설기계", "999999")           # 직접코드는 267270
        out = pp.resolve_codes(["HD현대건설기계"], transport=FakeDataPortal(_DP_ITEMS))
        self.assertEqual(out["HD현대건설기계"], "999999")     # 오버라이드 우선

    def test_override_resolves_without_master_or_key(self):
        # 마스터·키 다 없어도(early-return 회피) 오버라이드는 해석.
        with mock.patch.dict(os.environ, {}, clear=True):
            pp.set_override("새종목", "123456")
            pp._overrides_memo.update(key=None, data={})      # 파일 변경 강제 재로딩
            out = pp.resolve_codes(["새종목"])
            self.assertEqual(out["새종목"], "123456")

    def test_krx_name_for_reverse_lookup(self):
        # /map 오등록 검증용 — 코드→KRX명 역조회(로컬 마스터, 외부콜 0).
        with mock.patch.object(pp, "_load_krx_master",
                               return_value={"비올": "335890"}):
            self.assertEqual(pp.krx_name_for("335890"), "비올")
            self.assertEqual(pp.krx_name_for("000000"), "")    # 마스터에 없음
            self.assertEqual(pp.krx_name_for(""), "")


if __name__ == "__main__":
    unittest.main()
