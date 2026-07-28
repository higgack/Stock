"""geo — 국가/한국지역 검증 (대시보드 축 노이즈 차단, 사용자 2026-06-22)."""
from trade import geo


def test_real_countries_pass():
    for c in ("미국", "중국", "대만", "러시아 연방", "베트남"):
        assert geo.is_country(c)


def test_country_buckets_pass():
    assert geo.is_country("전국") and geo.is_country("글로벌")


def test_non_countries_rejected():
    for x in ("24년 1분기내 가동 시작", "10,000kVA 초과", "AccuFab CEL", ""):
        assert not geo.is_country(x)
        assert geo.norm_country(x) == "전국"


def test_kr_regions_pass():
    for r in ("경기 성남시", "충북 청주시", "강원 춘천시", "세종",
              "경기도 수원시", "전국", ".경기 성남시"):
        assert geo.is_kr_region(r), r


def test_non_regions_fold_to_jeonguk():
    # 외국·제품명·스펙·화학명은 한국지역 아님 → 전국
    for x in ("미국", "캐나다", "Tera Harz Spinner", "높이 300미리 미만",
              "전압 1000V 이하", "코발트산 리튬 주석산염", ""):
        assert not geo.is_kr_region(x)
        assert geo.norm_region(x) == "전국"
