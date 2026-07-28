"""Regression tests for bot/market.py — detect_market, detect_cn_sub_market,
resolve_english_alias.

All three are pure deterministic functions with no network calls. Seed cases
come from CLAUDE.md per-ticker review history and Phase 3/4 expansion work.
"""

from __future__ import annotations

import pytest

from bot.market import detect_market, detect_cn_sub_market, resolve_english_alias


# ── detect_market ──────────────────────────────────────────────────────────

class TestDetectMarket:
    # ── Korean exchanges ──
    def test_kospi_ks_suffix(self):
        assert detect_market("005930.KS") == "KR"   # 삼성전자

    def test_kosdaq_kq_suffix(self):
        assert detect_market("039030.KQ") == "KR"   # 이오테크닉스; CLAUDE.md Phase 2

    def test_kospi_lowercase_rejected(self):
        # Suffix check is uppercased internally; lowercase input still works
        assert detect_market("005930.ks") == "KR"

    # ── Japanese exchange ──
    def test_jp_t_suffix(self):
        assert detect_market("7203.T") == "JP"      # Toyota

    def test_jp_lowercase(self):
        assert detect_market("7203.t") == "JP"

    # ── Taiwan exchange ──
    def test_tw_tw_suffix(self):
        assert detect_market("2330.TW") == "TW"     # TSMC

    def test_tw_two_suffix(self):
        assert detect_market("8299.TWO") == "TW"    # TPEx OTC board

    # ── China A-share exchanges ──
    def test_cn_a_shanghai_ss(self):
        assert detect_market("600519.SS") == "CN_A"  # 茅台 (상하이 메인)

    def test_cn_a_shenzhen_sz(self):
        assert detect_market("000858.SZ") == "CN_A"  # 五粮液

    def test_cn_a_beijing_bj(self):
        assert detect_market("000001.BJ") == "CN_A"  # 북경증권거래소

    def test_cn_a_star_ss(self):
        assert detect_market("688981.SS") == "CN_A"  # SMIC (STAR 科創板)

    def test_cn_a_chinext_sz(self):
        assert detect_market("300750.SZ") == "CN_A"  # CATL (創業板)

    # ── Hong Kong exchange ──
    def test_hk_hk_suffix(self):
        assert detect_market("0700.HK") == "HK"     # Tencent

    # ── US (default, no suffix) ──
    def test_us_no_suffix(self):
        assert detect_market("NVDA") == "US"

    def test_us_aapl(self):
        assert detect_market("AAPL") == "US"

    def test_us_empty_string(self):
        assert detect_market("") == "US"

    def test_us_none_like(self):
        # None is not a valid input per type hint, but guard against crashes
        assert detect_market(None) == "US"  # type: ignore[arg-type]

    # ── 'CN' string must never appear (Phase 4-CN split: CN → CN_A + HK) ──
    def test_no_legacy_cn_returned_for_ss(self):
        assert detect_market("600519.SS") != "CN"

    def test_no_legacy_cn_returned_for_hk(self):
        assert detect_market("0700.HK") != "CN"


# ── detect_cn_sub_market ────────────────────────────────────────────────────

class TestDetectCnSubMarket:
    # ── Shanghai STAR 科創板 (688xxx.SS) — ±20% limit ──
    def test_star_smic(self):
        assert detect_cn_sub_market("688981.SS") == "CN_A_STAR"

    def test_star_other_688(self):
        assert detect_cn_sub_market("688012.SS") == "CN_A_STAR"

    # ── Shanghai main board (600/601/603/605.SS) — ±10% limit ──
    def test_sh_main_moutai(self):
        assert detect_cn_sub_market("600519.SS") == "CN_A_MAIN"

    def test_sh_main_601(self):
        assert detect_cn_sub_market("601398.SS") == "CN_A_MAIN"   # ICBC

    # ── Shenzhen ChiNext 創業板 (300/301.SZ) — ±20% limit ──
    def test_chinext_catl(self):
        assert detect_cn_sub_market("300750.SZ") == "CN_A_CHINEXT"

    def test_chinext_301_prefix(self):
        assert detect_cn_sub_market("301001.SZ") == "CN_A_CHINEXT"

    # ── Shenzhen main board (000/001.SZ) — ±10% limit ──
    def test_sz_main_wuliangye(self):
        assert detect_cn_sub_market("000858.SZ") == "CN_A_MAIN"

    def test_sz_main_byd(self):
        assert detect_cn_sub_market("002594.SZ") == "CN_A_MAIN"   # BYD A주

    # ── Beijing Stock Exchange (.BJ) — ±30% limit ──
    def test_bjse(self):
        assert detect_cn_sub_market("000001.BJ") == "CN_A_BJSE"

    # ── HK Main board — 0001-3999 / 6000-8999 (not 4-char 8xxx) ──
    def test_hk_main_tencent(self):
        assert detect_cn_sub_market("0700.HK") == "HK_MAIN"

    def test_hk_main_hsbc(self):
        assert detect_cn_sub_market("0005.HK") == "HK_MAIN"

    # ── HK GEM (8xxx 4-char prefix) — low liquidity ──
    def test_hk_gem_4char_8prefix(self):
        assert detect_cn_sub_market("8001.HK") == "HK_GEM"

    def test_hk_main_cnooc_not_gem(self):
        # 0883.HK — starts with 0, not 8 → HK_MAIN
        assert detect_cn_sub_market("0883.HK") == "HK_MAIN"

    # ── Non-CN/HK tickers → None ──
    def test_us_returns_none(self):
        assert detect_cn_sub_market("NVDA") is None

    def test_kr_returns_none(self):
        assert detect_cn_sub_market("005930.KS") is None

    def test_jp_returns_none(self):
        assert detect_cn_sub_market("7203.T") is None

    def test_tw_returns_none(self):
        assert detect_cn_sub_market("2330.TW") is None

    def test_empty_returns_none(self):
        assert detect_cn_sub_market("") is None


# ── resolve_english_alias ───────────────────────────────────────────────────

class TestResolveEnglishAlias:
    # ── KR aliases ──
    def test_naver_kr(self):
        # NAVER alias critical: was the CJ-style bug seed (CLAUDE.md)
        assert resolve_english_alias("NAVER") == "035420.KS"

    def test_samsung_kr(self):
        assert resolve_english_alias("SAMSUNG") == "005930.KS"

    def test_kakao_kr(self):
        assert resolve_english_alias("KAKAO") == "035720.KS"

    def test_cj_kr(self):
        # CJ ticker caused a deterministic failure (CLAUDE.md 2026-05-21)
        assert resolve_english_alias("CJ") == "001040.KS"

    # ── Ambiguous KR aliases → None (must NOT guess) ──
    def test_lg_ambiguous_none(self):
        assert resolve_english_alias("LG") is None

    def test_sk_ambiguous_none(self):
        assert resolve_english_alias("SK") is None

    def test_hyundai_ambiguous_none(self):
        assert resolve_english_alias("HYUNDAI") is None

    # ── JP aliases ──
    def test_toyota_jp(self):
        assert resolve_english_alias("TOYOTA") == "7203.T"

    def test_sony_jp(self):
        assert resolve_english_alias("SONY") == "6758.T"

    def test_mufg_jp(self):
        assert resolve_english_alias("MUFG") == "8306.T"

    # ── TW aliases ──
    def test_tsmc_tw(self):
        assert resolve_english_alias("TSMC") == "2330.TW"

    def test_mediatek_tw(self):
        # Phase 4-TW canonical — MediaTek surfaced nan% bug 2026-05-26
        assert resolve_english_alias("MEDIATEK") == "2454.TW"

    # ── CN aliases — dual-listing defaults (Option α 2026-05-18) ──
    def test_tencent_cn_hk(self):
        # Tencent: no A-share → defaults to HK
        assert resolve_english_alias("TENCENT") == "0700.HK"

    def test_byd_cn_a_default(self):
        # BYD: A주 default (002594.SZ), NOT 1211.HK
        assert resolve_english_alias("BYD") == "002594.SZ"

    def test_catl_cn_a(self):
        assert resolve_english_alias("CATL") == "300750.SZ"

    # ── Case-insensitivity ──
    def test_lowercase_tsmc(self):
        assert resolve_english_alias("tsmc") == "2330.TW"

    def test_mixed_case_toyota(self):
        assert resolve_english_alias("Toyota") == "7203.T"

    # ── Unknown alias → None ──
    def test_unknown_returns_none(self):
        assert resolve_english_alias("FOOBAR") is None

    def test_empty_returns_none(self):
        assert resolve_english_alias("") is None

    def test_none_input_returns_none(self):
        assert resolve_english_alias(None) is None  # type: ignore[arg-type]
