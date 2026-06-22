"""item_industry — BeOn 품목→산업 정확매칭 + '미분류' fallback (사용자 2026-06-22)."""
from trade import item_industry as ii


def test_norm_strips_symbols_and_space():
    assert ii._norm("광섬유 케이블") == "광섬유케이블"
    assert ii._norm("초고압 변압기(10,000kVA 초과)") == "초고압변압기10000kva초과"


def test_known_items_resolve():
    # 구조화 매핑으로 잡히는 대표 품목(HS→MTI 산업).
    assert ii.industry_for("라면") == "농수산식품"
    assert ii.industry_for("광섬유 케이블") == "전기기기"


def test_unknown_item_returns_empty():
    # 매핑 불가는 '' → 뷰에서 '미분류'. (fuzzy 자동추정 안 함)
    assert ii.industry_for("존재하지않는품목명xyz") == ""
    assert ii.industry_for("") == ""


def test_manual_override_takes_priority(monkeypatch):
    # 운영자 확인 override 가 최우선(인덱스보다 먼저).
    monkeypatch.setitem(ii._MANUAL, "라면", "테스트산업")
    try:
        assert ii.industry_for("라면") == "테스트산업"
    finally:
        ii._MANUAL.pop("라면", None)


def test_index_is_dict():
    idx = ii._index()
    assert isinstance(idx, dict) and len(idx) > 100   # 구조화 사전 비어있지 않음
