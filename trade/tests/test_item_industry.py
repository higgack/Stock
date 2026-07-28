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


def test_curated_csv_loaded_and_priority():
    # 운영자 확인 CSV(data/item_industry.csv) 로드 + 자동 인덱스보다 우선.
    cur = ii._curated()
    assert isinstance(cur, dict) and len(cur) > 100    # 일괄 매핑 적재됨
    # 전해액: 자동 인덱스는 미분류('기타')인데 운영자 확인은 이차전지 → CSV 우선(보강).
    assert ii._index().get(ii._norm("전해액")) in (None, "", "기타")
    assert ii.industry_for("전해액") == "이차전지"
    # 표기 정규화(공백) 견고 — 같은 결과.
    assert ii.industry_for("플래시 메모리") == "반도체"
    assert ii.industry_for(" 플래시  메모리 ") == "반도체"


def test_curated_no_conflict_with_mti_map():
    # 정합 게이트: 겹치는 품목명에서 curated 가 mti_map(레퍼런스북·산업트렌드·히트맵
    # 단일 출처)과 어긋나면 안 됨. 단 mti_map 이 '기타'(미분류)면 curated 보강 허용.
    idx = ii._index()
    conflicts = []
    for k, ind in ii._curated().items():
        ref = idx.get(k)
        if ref and ref != "기타" and ref != ind:
            conflicts.append((k, ind, ref))
    assert not conflicts, conflicts


def test_curated_all_within_taxonomy():
    # CSV 의 모든 산업은 표준 MTI 산업 ∪ '기타' (오타·신규라벨 누수 방지).
    from trade import mti_map
    valid = set(mti_map.industries()) | {"기타"}
    bad = sorted({v for v in ii._curated().values() if v not in valid})
    assert not bad, bad
