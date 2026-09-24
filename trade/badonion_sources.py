"""나쁜양파(Badonions) 소스 단일 레지스트리.

배경 — 이 목록이 **5개 파일에 중복**돼 있었다: `scripts/listen_badonion.py`
(관련성 필터 + 드랍 로그 문구), `scripts/backfill_badonion.py`(같은 필터의
미러 + 필터 로그 문구), `scripts/ingest_inbox.py`(폴백 라우팅 체인 + DB
오픈 + 카운터), `scripts/unstored_check.py`(억제 분기), `dashboard.py`
(미매칭 집계 스킵). 국가명 나열 문자열은 10군데 하드코딩돼 있었다.

2026-08-16 한국 수출(종목별)을 추가하며 **로그 문구가 실제로 어긋났다** —
필터는 한국을 통과시키는데 로그는 여전히 "대만·중국·일본·태국·말레이시아·
필리핀·멕시코 수출/미국 수입" 만 나열했다. 드리프트가 이론이 아니라 관측
사실이 됐으므로 한 곳으로 모은다. 이제 소스 추가는 이 파일 1줄이다.

9개 모듈이 전부 같은 계약을 지키기 때문에 가능하다:
    parse_*(caption) -> dict | None
    open_*_db(path)  -> sqlite3.Connection
    ingest(conn, caption, *, source_message_id, posted_at, media_paths) -> bool

⚠️ **SOURCES 의 순서가 곧 ingest 폴백 순서다.** `_ingest_group` 은 순차
fallback 이라 앞선 파서가 먼저 캡션을 가져간다. 순서를 바꾸면 조용한
오저장이 된다(테스트가 순서를 pin 한다).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from trade import cn_exports as _cn
from trade import cn_stock_exports as _cns
from trade import cn_stock_imports as _cni
from trade import jp2_exports as _jp2
from trade import jp_stock_exports as _jps
from trade import kr_stock_exports as _krs
from trade import kr_stock_imports as _kri
from trade import mx_exports as _mx
from trade import my_exports as _my
from trade import my_stock_exports as _mys
from trade import ph_exports as _ph
from trade import th_exports as _th
from trade import tw_exports as _tw
from trade import tw_monthly_revenue as _twr
from trade import tw_stock_exports as _tws
from trade import us_imports as _us
from trade import us_ppi as _uppi


@dataclass(frozen=True)
class Source:
    key: str                    # 카운터/코드 식별자 (예: "tw")
    label: str                  # 사람이 읽는 이름 — 로그 문구가 이걸 조립
    parse: Callable             # parse_*(caption) -> dict | None
    open_db: Callable           # open_*_db(path) -> Connection
    ingest: Callable            # ingest(conn, caption, *, ...) -> bool
    regenerate: Callable        # regenerate(db, out, *, media_url_prefix)
    db_file: str                # DATA_DIR 하위 파일명
    html_file: str              # 대시보드 형제 페이지 (없으면 "")
    nav_label: str              # index.html nav 링크 텍스트 (이모지 포함)
    # ↓ nav 표시 순서 계산용 축(_nav_order 규약). 기본값을 안 두는 건 의도 —
    # 새 소스가 축을 안 밝히면 조용히 엉뚱한 자리에 놓이는 대신 즉시 터진다.
    country: str                # 나라 묶음 키 (예: "일본")
    basis: str                  # "item"(품목/HS) | "company"(종목/회사)
    flow: str                   # "export" | "import" | "index"
    # ↓ 이 소스의 파서가 받는 **캡션 문법**. 형제 전수 계약(#262 상관 4지표·
    # #261 헤더 2레이아웃 등)이 이 축으로 대상을 고른다 — 옛 판은
    # `basis=="company" and flow in ("export","import")` 로 골랐는데,
    # 2026-09-16 한국 **금액판**(회사·수출인데 상관 지표가 없다)이 붙자
    # 그 선택이 멀쩡한 소스를 계약 위반으로 찍었다(#34 한 축이 두 뜻을
    # 대표하면 한쪽은 반드시 거짓말). 축을 나눠 **문법으로** 고른다.
    #   "hs"      품목(HS) 판          "corr"    종목 지표판(Update 헤더·상관)
    #   "amount"  회사 금액판(▶️·$M)   "revenue" 월매출   "ppi" 미국 PPI
    # ⚠️ 기본값을 두지 않는다 — 새 소스가 안 밝히면 조용히 남의 계약에
    # 걸리거나(오탐) 어느 계약에도 안 걸린다(눈멂). 회귀가 전수로 잰다.
    grammars: tuple[str, ...]


# 순서 = ingest 폴백 순서. 뒤로 갈수록 나중에 시도된다.
# **회사(종목) 기준** 소스(krs·jps·mys)는 마커가 가장 좁아 전부 맨 뒤 —
# 품목(HS) 파서가 먼저 캡션을 가져가야 한다. (2026-08-16 krs 추가 시점엔
# 한국이 유일한 회사 기준이었으나 jps·mys 가 붙어 이제 셋이다.)
#
# nav_label 이모지 규칙(사용자 2026-07-11 스크린샷 교훈): 🇹🇼/🇨🇳 같은
# **flag-sequence 는 일부 폰트에서 'tw' 문자로 렌더**되므로 논-플래그
# 이모지(국가 상징 동물/음식)를 쓴다.
# 2026-08-17: 예외로 남겨뒀던 🇺🇸 도 결국 같은 증상이 났다(사용자 스크린샷 —
# 미국만 'us' 글자로 렌더). 예외 없이 전 소스 논-플래그로 통일하고, 테스트가
# regional-indicator(U+1F1E6~U+1F1FF)를 nav_label·페이지 h1 에서 금지한다.
# ⚠️ 이 금지는 **표시 문자열만** — 텔레그램 캡션(파서 입력)의 🇺🇸/🇹🇼 는 원문
# 마커라 손대면 ingest 가 통째로 깨진다.
SOURCES: tuple[Source, ...] = (
    Source("tw", "대만", _tw.parse_tw_export, _tw.open_tw_db, _tw.ingest,
           _tw.regenerate, "tw.db", "tw.html", "🧋 대만 수출 데이터(나쁜양파)",
           country="대만", basis="item", flow="export", grammars=("hs",)),
    Source("cn", "중국", _cn.parse_cn_export, _cn.open_cn_db, _cn.ingest,
           _cn.regenerate, "cn.db", "cn.html", "🐼 중국 수출 데이터(나쁜양파)",
           country="중국", basis="item", flow="export", grammars=("hs",)),
    Source("jp2", "일본", _jp2.parse_jp2_export, _jp2.open_jp2_db, _jp2.ingest,
           _jp2.regenerate, "jp2.db", "jp2.html",
           "🎌 일본 수출 데이터(나쁜양파)",
           country="일본", basis="item", flow="export", grammars=("hs",)),
    Source("th", "태국", _th.parse_th_export, _th.open_th_db, _th.ingest,
           _th.regenerate, "th.db", "th.html", "🐘 태국 수출 데이터(나쁜양파)",
           country="태국", basis="item", flow="export", grammars=("hs",)),
    Source("my", "말레이시아", _my.parse_my_export, _my.open_my_db, _my.ingest,
           _my.regenerate, "my.db", "my.html",
           "🐯 말레이시아 수출 데이터(나쁜양파)",
           country="말레이시아", basis="item", flow="export", grammars=("hs",)),
    Source("ph", "필리핀", _ph.parse_ph_export, _ph.open_ph_db, _ph.ingest,
           _ph.regenerate, "ph.db", "ph.html",
           "🥭 필리핀 수출 데이터(나쁜양파)",
           country="필리핀", basis="item", flow="export", grammars=("hs",)),
    Source("mx", "멕시코", _mx.parse_mx_export, _mx.open_mx_db, _mx.ingest,
           _mx.regenerate, "mx.db", "mx.html", "🌮 멕시코 수출 데이터(나쁜양파)",
           country="멕시코", basis="item", flow="export", grammars=("hs",)),
    Source("us", "미국 수입", _us.parse_us_import, _us.open_us_db, _us.ingest,
           _us.regenerate, "us.db", "us.html", "🗽 미국 수입 데이터(나쁜양파)",
           country="미국", basis="item", flow="import", grammars=("hs",)),
    # 미국 PPI(사용자 2026-08-19) — 유일하게 **금액이 아니라 지수** 소스.
    # 마커('미국 PPI')는 us_imports 의 'N월 수입 미국' 과 겹치지 않지만,
    # 품목(HS) 기준이므로 **종목 기준(krs/jps) 앞**에 둔다(아래 계약).
    Source("uppi", "미국 PPI", _uppi.parse_us_ppi, _uppi.open_us_ppi_db,
           _uppi.ingest, _uppi.regenerate, "us_ppi.db", "us_ppi.html",
           "📈 미국 PPI 데이터(나쁜양파)",
           country="미국", basis="item", flow="index", grammars=("ppi",)),
    # ⚠️ 한국 수출은 **문법이 둘**이다(2026-09-16 사용자 실측): 옛 지표판
    # (`HPSP (403870)` / `한국 수출` / `26년 7월 Update`)과 새 금액판
    # (`8월 수출 한국` / `▶️ 회사 — 품목` / `$158.5M (+105.3% YoY) (+11.8% MoM)`).
    # 새 판을 받는 소스가 없어 LS ELECTRIC 이 조용히 드랍되고 있었다 —
    # 관련성 필터가 곧 파서라 저장도 미매칭 알림도 없었다(#83·#261·#330·#332).
    # 사용자 결정으로 **한 페이지에 합치므로** 필터는 `parse_any` 다.
    Source("krs", "한국 수출(종목별)", _krs.parse_any,
           _krs.open_kr_stock_db, _krs.ingest, _krs.regenerate,
           "kr_stock.db", "kr_stock.html",
           # 한국만 품목(HS)이 아니라 **종목(회사)** 기준이라 라벨에 명시.
           "🏢 한국 수출 데이터(종목별·나쁜양파)",
           country="한국", basis="company", flow="export", grammars=("corr", "amount",)),
    # 한국 **수입** 회사별(사용자 2026-09-16 "대시보드에 한국수입 회사별은
    # 없는것 같은데 만들어줘. 현재는 8월부터 텔레칩스 하나야"). 수출 금액판과
    # 마커 한 낱말만 다르므로 문법 엔진(`kr_company_flow`)을 공유하고 여기선
    # 독립 소스로 등록한다 — DB·페이지·라우팅은 별개다(cn 수출/수입과 같은 규율).
    Source("kri", "한국 수입(회사별)", _kri.parse_kr_stock_import,
           _kri.open_kr_stock_import_db, _kri.ingest, _kri.regenerate,
           "kr_stock_import.db", "kr_stock_import.html",
           "🏢 한국 수입 데이터(회사별·나쁜양파)",
           country="한국", basis="company", flow="import", grammars=("amount",)),
    # 일본도 품목(jp2)과 **종목** 두 갈래다. 종목판은 jp2 파서가 회사 헤더를
    # 못 읽어 관련성 필터에서 통째로 드랍되고 있었다(2026-08-16 실측 8건).
    Source("jps", "일본 수출(종목별)", _jps.parse_jp_stock_export,
           _jps.open_jp_stock_db, _jps.ingest, _jps.regenerate,
           "jp_stock.db", "jp_stock.html",
           "🗼 일본 수출 데이터(종목별·나쁜양파)",
           country="일본", basis="company", flow="export", grammars=("corr",)),
    # 말레이시아도 품목(my)과 **종목** 두 갈래다. 26년 7월 종목판이 채널에
    # 떴는데 품목 파서의 마커가 `N월 수출 말레이시아`(어순 반대)라 관련성
    # 필터를 통과 못 하고 통째로 드랍됐다(사용자 2026-08-20 — 일본이
    # 2026-08-16 에 겪은 것과 같은 사고).
    Source("mys", "말레이시아 수출(종목별)", _mys.parse_my_stock_export,
           _mys.open_my_stock_db, _mys.ingest, _mys.regenerate,
           "my_stock.db", "my_stock.html",
           "🐆 말레이시아 수출 데이터(종목별·나쁜양파)",
           country="말레이시아", basis="company", flow="export", grammars=("corr",)),
    # 중국도 품목(cn)과 **종목** 두 갈래다. 26년 7월 종목판이 채널에 떴는데
    # 품목 파서의 마커가 `N월 수출 중국`(어순 반대)이라 관련성 필터를
    # 통과 못 하고 통째로 드랍됐다(사용자 2026-08-21 — 일본 2026-08-16,
    # 말레이시아 2026-08-20 과 **같은 사고**. 어순 반대 형제 포맷은 네
    # 번째다: 새 나라의 종목판이 뜨면 또 난다고 보는 게 맞다).
    Source("cns", "중국 수출(종목별)", _cns.parse_cn_stock_export,
           _cns.open_cn_stock_db, _cns.ingest, _cns.regenerate,
           "cn_stock.db", "cn_stock.html",
           "🏮 중국 수출 데이터(종목별·나쁜양파)",
           country="중국", basis="company", flow="export", grammars=("corr",)),
    # 중국은 **수입** 종목판도 온다(사용자 2026-08-21 "중국수입 7월 기업도
    # 있어"). 수출과 마커 한 단어만 다르므로 문법 엔진(`cn_stock_flow`)을
    # 공유하고 여기선 독립 소스로 등록한다 — DB·페이지·라우팅은 별개다.
    Source("cni", "중국 수입(종목별)", _cni.parse_cn_stock_import,
           _cni.open_cn_stock_import_db, _cni.ingest, _cni.regenerate,
           "cn_stock_import.db", "cn_stock_import.html",
           "🧧 중국 수입 데이터(종목별·나쁜양파)",
           country="중국", basis="company", flow="import", grammars=("corr",)),
    # 대만도 품목(tw)과 **종목** 두 갈래다. 26년 8월 종목판이 채널에 떴는데
    # 파서가 없어 관련성 필터에서 통째로 드랍됐다(사용자 2026-09-10 —
    # 일본 08-16·말레이시아 08-20·중국 08-21 과 같은 사고의 **다섯 번째**,
    # #83 이 예측한 그대로). 문법 엔진(`cn_stock_flow`)을 나라만 바꿔 쓴다.
    Source("tws", "대만 수출(종목별)", _tws.parse_tw_stock_export,
           _tws.open_tw_stock_db, _tws.ingest, _tws.regenerate,
           "tw_stock.db", "tw_stock.html",
           "🧋 대만 수출 데이터(종목별·나쁜양파)",
           country="대만", basis="company", flow="export", grammars=("corr",)),
    # 대만 월매출(종목별) — `badonion.co.kr/twse-revenue` 카드. 2026-09-10 대만
    # 수출 6건은 회수됐는데 TSMC 월매출만 어느 파서도 안 받아 드랍됐다(여섯 번째).
    # 수출 문법이 아니라 엔진을 재사용하지 않는다(월매출·REV·MoM·YoY·누적).
    Source("twr", "대만 월매출(종목별)", _twr.parse_tw_monthly_revenue,
           _twr.open_tw_revenue_db, _twr.ingest, _twr.regenerate,
           "tw_revenue.db", "tw_revenue.html",
           "🧋 대만 월매출 데이터(종목별·나쁜양파)",
           country="대만", basis="company", flow="revenue", grammars=("revenue",)),
)


def sources_with_grammar(grammar: str) -> tuple[Source, ...]:
    """그 캡션 문법을 받는 소스들. **형제 전수 계약의 유일한 선택기**다 —
    테스트마다 조건을 복제하면 새 문법이 붙을 때 한쪽만 고쳐진다(#38·#24)."""
    return tuple(s for s in SOURCES if grammar in s.grammars)

# nav 표시 순서 — **ingest 순서와 다르다.** SOURCES 를 재정렬하면 라우팅이
# 바뀌므로 표시 순서는 여기서 따로 만든다.
#
# 규약(사용자 2026-08-20 "품목 레퍼런스북 다음은 우선순위대로 나라별, 품목별,
# 회사별, 수출다음 수입순. 나라의 순서는 대시보드의 개수가 많은것 우선"):
#   ① 나라로 묶고, 나라는 **대시보드 개수 내림차순**
#   ② 한 나라 안에서는 품목별 → 회사별
#   ③ 그 다음 수출 → 수입 → 지수 → 매출(2026-09-10 대만 월매출)
#
# ⚠️ 손으로 나열하지 않고 **계산한다.** 옛 코드는 12개를 직접 적어 뒀는데,
# 그러면 새 소스를 추가할 때마다 규약과 어긋난 자리에 조용히 놓인다(실수 #24
# — 목록형은 목록 밖을 못 본다). 실제로 mys 를 추가했을 때 규약상 my 바로
# 옆이어야 하는데 손으로 끼워 넣어야 했다. 이제 축(country/basis/flow)만
# 밝히면 자리는 자동이고, 축을 안 밝히면 dataclass 가 즉시 터진다.
_BASIS_RANK = {"item": 0, "company": 1}
_FLOW_RANK = {"export": 0, "import": 1, "index": 2, "revenue": 3}

# 레지스트리 **밖**에서 nav 에 실리는 대시보드 수. dashboard.py 가 jp.html
# (일본/비온)을 직접 하드코딩하는데, 나라별 개수 판정에 이게 빠지면 일본이
# 2개로 세어져 순위가 틀어진다. 테스트가 dashboard.py 의 jp.html 링크와
# 이 상수를 함께 고정한다(한쪽만 사라지면 계수가 조용히 어긋난다).
_EXTRA_COUNTRY_PAGES: dict[str, int] = {"일본": 1}


def _source_rank(s: Source) -> tuple[int, int]:
    """나라 안에서의 정렬 키 — 품목별 우선, 그 다음 수출 우선."""
    return (_BASIS_RANK[s.basis], _FLOW_RANK[s.flow])


def _nav_order(sources: tuple[Source, ...] | None = None) -> tuple[str, ...]:
    """위 규약대로 표시 순서를 계산한다. 키 누락은 구조적으로 불가능해졌다
    (옛 하드코딩은 누락 시 페이지가 생성돼도 **도달 불가**였고, is_relevant
    가 미매칭 알림까지 눌러 조용한 유실이 됐다).

    `sources` 인자는 테스트용 — 합성 소스로 규약 자체를 검증한다(현행 12개만
    보면 "지금 순서를 그대로 적은" 테스트가 되어 규약을 되돌리는 변경을 못
    잡는다, 실수 #19)."""
    src = SOURCES if sources is None else sources
    idx = {s.key: i for i, s in enumerate(src)}
    groups: dict[str, list[Source]] = {}
    for s in src:
        if s.html_file:
            groups.setdefault(s.country, []).append(s)

    def country_key(item: tuple[str, list[Source]]):
        name, group = item
        pages = len(group) + _EXTRA_COUNTRY_PAGES.get(name, 0)
        # 개수 동률이면 그 나라의 대표(가장 앞) 소스 성격으로 가른다 —
        # 수출 나라가 수입 나라보다 앞(③의 나라 단위 적용).
        return (-pages, min(_source_rank(g) for g in group),
                min(idx[g.key] for g in group))

    out: list[str] = []
    for _name, group in sorted(groups.items(), key=country_key):
        for s in sorted(group, key=lambda g: (_source_rank(g), idx[g.key])):
            out.append(s.key)
    return tuple(out)


NAV_ORDER: tuple[str, ...] = _nav_order()


def is_relevant(text: str) -> bool:
    """나쁜양파 채널의 무관 콘텐츠(애널리스트 레이팅표 등) 필터.

    리스너·백필이 forward 전에 쓰고, unstored_check·dashboard 가 '정상
    처리 경로라 미매칭 집계에서 제외' 판정에 쓴다. 네 곳이 같은 함수를
    보게 해 판정이 갈리지 않게 한다."""
    return any(s.parse(text) is not None for s in SOURCES)


def matching_keys(text: str) -> tuple[str, ...]:
    """이 캡션을 받는 소스 키 **전부**(순수) — `is_relevant` 의 상세판.

    ⚠️ 왜 필요한가(2026-09-17): 백필 로그가 `9/402 units are [대만 · 중국
    · … · 한국 수입(회사별) · …] 데이터` 라고 **레지스트리 전체**를 나열해,
    그 9건이 **어느 소스**였는지 알 수 없었다. 그래서 "한국 수입 회사별이
    안 들어온다" 를 물어도 '채널에 그런 글이 없었다' 와 '우리 파서가
    떨어뜨렸다' 가 갈리지 않는다 — 처방이 정반대다(#82·#143 대조군).
    숫자만 세는 원장은 다음 라운드를 추측으로 만든다(#290·#93).

    한 캡션이 둘 이상에 걸릴 수 있으므로 **전부** 돌려준다(먼저 걸린
    하나만 세면 소계 합이 총계와 어긋난다, #45).
    """
    return tuple(s.key for s in SOURCES if s.parse(text) is not None)


def _unit_keys(unit) -> set:
    """유닛(= 앨범이면 멤버 묶음) 하나를 받는 소스 키 전부 — 멤버 합집합."""
    keys: set = set()
    for m in unit:
        keys |= set(matching_keys(getattr(m, "text", "") or ""))
    return keys


def unit_labels(unit) -> str:
    """유닛을 받는 소스 이름(레지스트리 순서) — 백필의 **포워드할 유닛** 줄용.

    ⚠️ 왜(2026-09-24, 실수 #403): 09-22 dry-run 은 드랍된 유닛과 이미 받은
    유닛만 찍고, **파서는 받는데 아직 inbox 에 없는 유닛**(= 파서가 생기기
    전에 리스너가 버린 캡션)은 소스별 계수로만 셌다. kri 가 빈 이유가 바로
    그 갈래였는데 출력에 한 줄도 없어 '원천 미게시' 로 오판했다. 줄 하나가
    어느 소스인지까지 말해야 그 줄만 보고 판정된다(#356).
    """
    keys = _unit_keys(unit)
    return ", ".join(s.label for s in SOURCES if s.key in keys) or "(없음)"


def relevance_breakdown(units) -> list[str]:
    """유닛(= `.text` 를 가진 메시지들의 묶음)을 **소스별로** 센 사람용 줄.

    ⚠️ 왜 레지스트리에 있나: 호출부(`backfill_badonion`)는 telethon 이
    없으면 import 조차 안 되므로 거기 두면 **회귀가 통째로 스킵**된다 —
    판정은 의존성 없는 모듈로 뺀다(#176).

    한 캡션이 둘 이상에 걸릴 수 있어 소계 합은 총계보다 클 수 있다 — 그
    사실을 줄에 적는다(#45). 0건 소스도 **이름을 대서** 말한다(#54·#82):
    '이 창의 채널에 그런 글이 없었다' 와 '우리 파서가 떨어뜨렸다' 는
    처방이 정반대이고, 후자는 `--show-irrelevant` 원문이 답한다(#109).
    """
    from collections import Counter
    brk: Counter = Counter()
    n_units = 0
    for u in units:
        n_units += 1
        for k in _unit_keys(u):
            brk[k] += 1
    by = {s.key: s.label for s in SOURCES}
    got = [f"{by.get(k, k)} {n}"
           for k, n in sorted(brk.items(), key=lambda kv: (-kv[1], kv[0]))]
    out = [f"relevance breakdown: {', '.join(got) if got else '(없음)'}"
           + (" · 유닛 하나가 여러 소스에 걸리면 중복 계수"
              if sum(brk.values()) > n_units else "")]
    zero = [s.label for s in SOURCES if s.key not in brk]
    if zero:
        # ⚠️ 0건은 갈래가 **둘**이고 하나가 이 도구의 존재 이유다 —
        # (a) 채널에 그런 글이 없었다 (b) 새 형식을 우리 파서가 떨어뜨렸다
        # (#83·#261·#330·#332·#370 — 일곱 번 반복된 조용한 유실). 주절로
        # (a)를 사실처럼 적으면 (b)를 안 보게 만든다(#165·#82, 리뷰 M4).
        out.append(f"  0건 소스({len(zero)}): {', '.join(zero)}"
                   " — 채널에 그 글이 없었거나, 새 형식을 파서가 못 받은 것"
                   "(가르려면 --show-irrelevant 원문을 볼 것)")
    return out


# ─────────────────────────────────────────────────────────────────────────
# 동기화 창 — 관련성 필터(= 파서 집합)가 바뀐 배포 뒤엔 한 번 넓게 훑는다
# ─────────────────────────────────────────────────────────────────────────
# ⚠️ 왜(2026-09-24, 실수 #403): 관련성 필터가 곧 파서라, 파서가 생기기 **전**에
# 올라온 캡션은 리스너가 버린다. 되찾는 길은 백필뿐인데 6시간 동기화는 최근
# 3일만 본다 — 캡션과 파서 배포 사이가 3일을 넘으면 **영영** 못 줍는다.
# kri(한국 수입 회사별)는 사용자가 2026-09-16 에 채널에서 텔레칩스 캡션을 봤는데
# (`kr_stock_imports` 독스트링) 09-24 까지 보드가 비어 있었다. #261·#330 이
# "파서 배포는 백필 수동 회수까지가 한 세트" 라고 적어 두고 **사람 손**에
# 맡겼고, 그 수동 단계가 #370·#371 에서 두 번(telethon 미설치 · 지어낸 플래그)
# 실패했다 → 규율이 아니라 구조로(#119·#267): 필터 코드 지문이 기록과 다르면
# 다음 동기화가 **한 번** 넓은 창을 쓰고, 성공하면 지문을 기록한다.
DEFAULT_LOOKBACK_DAYS = 3
# 나쁜양파 보드는 전부 **월간** 발행이다 — 31일 + 여유면 파서가 바뀌기 직전에
# 나온 각 소스의 가장 최근 발행 한 회가 창 안에 든다. 더 깊은 이력은 사람이
# `--since` 로 연다(무한히 넓히면 후보 상한 TRADE_MAX_CANDIDATES 에 먼저 닿는다).
RECOVERY_LOOKBACK_DAYS = 40
SYNC_STATE_NAME = "badonion_sync_state.json"

_TRADE_DIR = Path(__file__).resolve().parent


def _module_file(name: str, base_dir: Path) -> Path | None:
    """`trade.x.y` → 파일(`trade/x/y.py` 또는 패키지 `__init__.py`). 모듈이
    아니면 None — `from trade.stock_link import _JP_CODE` 의 `_JP_CODE` 는 이름이다.

    ⚠️ `importlib.util.find_spec` 을 안 쓰는 이유: 점 이름은 **부모를 import**
    해 버린다. 백필은 requests 가 없는 `.backfill-venv` 에서 돌므로(프로브 ②
    실측) 지문을 재다가 부작용·ImportError 를 부르면 안 된다 — 경로로만 푼다."""
    parts = name.split(".")
    if not parts or parts[0] != base_dir.name:
        return None
    stem = base_dir.parent.joinpath(*parts)
    for cand in (stem.with_suffix(".py"), stem / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _strip_docstrings(tree) -> None:
    """독스트링을 비운다 — 설명만 고친 배포가 40일 회수를 부르지 않게(#266).
    주석은 애초에 AST 에 없다."""
    import ast
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""


def filter_roots() -> tuple[str, ...]:
    """관련성 필터의 뿌리 모듈 — 이 레지스트리 + 각 소스 파서가 사는 모듈.

    레지스트리에서 **파생**한다(이름 열거 금지, #24) — 소스를 한 줄 더하면
    그 파서 모듈이 저절로 지문에 들어간다."""
    import inspect
    out = {__name__}
    for s in SOURCES:
        mod = inspect.getmodule(s.parse)
        if mod is not None:
            out.add(mod.__name__)
    return tuple(sorted(out))


def filter_closure(roots, base_dir: Path | None = None) -> dict[str, Path]:
    """뿌리에서 패키지 안 import 를 따라간 **전이 폐포**(모듈명 → 파일).

    ⚠️ 한 단계로는 부족하다 — kri 의 파서 함수는 어댑터(`kr_stock_imports`)에
    있지만 문법은 엔진(`kr_company_flow`)에 산다. 엔진만 고친 배포를 못 보면
    이 장치가 막으려던 유실이 그대로 재발한다(#365 한 단계만 훑다 63개를
    놓쳤다). 렌더 모듈까지 딸려 오지만(넓게 잡는 쪽을 택했다) 더 덮은 비용은
    40일 스캔 한 번이고, 덜 덮은 비용은 영영 비는 보드다."""
    import ast
    base_dir = base_dir or _TRADE_DIR
    pkg0 = base_dir.name
    seen: dict[str, Path | None] = {}
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        f = _module_file(name, base_dir)
        seen[name] = f
        if f is None:
            continue
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom):
                if n.level:            # 상대 import — 지금은 없지만 생기면 샌다
                    pkg = name if f.name == "__init__.py" else name.rpartition(".")[0]
                    for _ in range(n.level - 1):
                        pkg = pkg.rpartition(".")[0]
                    mod = f"{pkg}.{n.module}" if n.module else pkg
                elif (n.module or "").split(".")[0] == pkg0:
                    mod = n.module
                else:
                    continue
                todo.append(mod)
                # `from trade import kr_company_flow` 는 모듈이 `trade` 라
                # 서브모듈을 이름에서 후보로 만든다(#365 와 같은 함정).
                todo += [f"{mod}.{a.name}" for a in n.names]
            elif isinstance(n, ast.Import):
                todo += [a.name for a in n.names
                         if a.name.split(".")[0] == pkg0]
    return {k: v for k, v in seen.items() if v is not None}


def relevance_fingerprint(*, roots=None, base_dir: Path | None = None) -> str:
    """관련성 필터를 만든 **코드의 지문**(sha1 앞 10자). 못 재면 "".

    ⚠️ 뿌리 모듈 하나라도 못 찾거나 못 읽으면 **지문을 주장하지 않는다** —
    덜 덮은 지문을 전부 덮은 것처럼 내면 과대 주장이다(#364·#365·#54)."""
    import ast
    import hashlib
    roots = tuple(roots) if roots is not None else filter_roots()
    try:
        files = filter_closure(roots, base_dir)
        if not roots or any(r not in files for r in roots):
            return ""
        h = hashlib.sha1()
        for name in sorted(files):     # 순서를 고정해야 지문이 안정적이다
            tree = ast.parse(files[name].read_text(encoding="utf-8"))
            _strip_docstrings(tree)
            h.update(name.encode())
            h.update(ast.dump(tree).encode())
        return h.hexdigest()[:10]
    except Exception:                                  # noqa: BLE001
        return ""


def _read_state(state_path: Path) -> tuple[dict | None, str]:
    """(상태 dict | None, 못 읽은 사유). 갈래를 이름으로 말한다(#82)."""
    try:
        rec = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "기록 없음(첫 실행)"
    except Exception as exc:                           # noqa: BLE001
        return None, f"기록 못 읽음({type(exc).__name__})"
    return (rec, "") if isinstance(rec, dict) else (None, "기록 형식이 다르다")


def _recorded_fp(state_path: Path) -> tuple[str | None, str]:
    """(기록된 지문 | None, 없을 때의 사유)."""
    rec, why = _read_state(state_path)
    if rec is None:
        return None, why
    fp = rec.get("relevance_fp")
    if isinstance(fp, str) and fp:
        return fp, ""
    retry = rec.get("retry")
    if isinstance(retry, dict) and retry.get("count"):
        return None, f"기록 없음(회수 재시도 {retry.get('count')}회째)"
    return None, "기록 형식이 다르다"


def _covers_recovery(since, lookback_days, to, now: datetime) -> bool:
    """명시 창이 **지금까지** 회수 창(40일)을 덮나. `--to` 가 있으면 지금까지가
    아니므로 덮지 않는다. `--since` 를 못 읽으면 덮지 않는다(백필이 어차피 거부).

    ⚠️ `--since` 가 있으면 **그것만** 본다 — 백필은 둘을 같이 받으면 `--since`
    로 훑는다. `--lookback-days` 를 먼저 보면 `--since <4일 전> --lookback-days
    45` 가 4일만 훑고도 '덮었다' 로 기록해 회수가 영영 사라진다(독립 리뷰 M2)."""
    if to is not None:
        return False
    if since is None:
        return lookback_days is not None and lookback_days >= RECOVERY_LOOKBACK_DAYS
    try:
        start = datetime.strptime(str(since), "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now - start).days >= RECOVERY_LOOKBACK_DAYS


def sync_plan(state_path: Path, *, since=None, lookback_days=None, to=None,
              fp: str | None = None, now: datetime | None = None) -> dict:
    """이번 동기화의 창 → `{"days", "record", "fp", "reason"}`.

    - `--since`·`--lookback-days`·`--to` 를 **명시**하면 그 창을 쓴다. 기록은
      그 창이 지금까지 회수 창을 **덮을 때만** 한다 — 좁은 창을 회수로 치면
      다 된 줄 알고 다시 안 훑고, 넓은 창까지 무시하면 자동 회수가 후보
      상한에 걸려 중단될 때 알림이 시키는 대로 넓게 돌려도 6시간마다 같은
      중단이 영원히 반복된다(수렴 지점이 없다, #171). `days` 는 `--since` 면 None.
    - 아니면 필터 지문을 기록과 대조한다: 같으면 기본 창, 다르거나 기록이
      없으면 회수 창 — `record=True` 는 "성공하면 이 지문을 기록하라" 다.
    - 지문을 못 재면 기본 창이고 **판정 불가라고 말한다**(#54): 못 잰 것으로
      넓히면 매번 40일이 되고, 조용히 좁히면 회수가 사라진다.
    """
    explicit = [flag for flag, v in (("--since", since),
                                     ("--lookback-days", lookback_days),
                                     ("--to", to)) if v is not None]
    if explicit:
        # 백필과 같은 우선순위 — `--since` 가 있으면 창은 그것이다(M2).
        days = (None if since is not None
                else lookback_days if lookback_days is not None
                else DEFAULT_LOOKBACK_DAYS)
        what = "·".join(explicit)
        if not _covers_recovery(since, lookback_days, to,
                                now or datetime.now(timezone.utc)):
            return {"days": days, "record": False, "fp": "", "recovery": False,
                    "reason": f"{what} 명시 — 회수 창({RECOVERY_LOOKBACK_DAYS}일)"
                              "을 덮지 않아 필터 지문 기록은 건드리지 않는다"}
        fp = relevance_fingerprint() if fp is None else fp
        return {"days": days, "record": bool(fp), "fp": fp, "recovery": False,
                "reason": f"{what} 명시 — 회수 창({RECOVERY_LOOKBACK_DAYS}일)을 "
                          "덮으므로 성공하면 필터 지문을 기록한다"
                          + ("" if fp else "(지문을 못 재 기록 불가)")}
    fp = relevance_fingerprint() if fp is None else fp
    if not fp:
        return {"days": DEFAULT_LOOKBACK_DAYS, "record": False, "fp": "",
                "recovery": False,
                "reason": "관련성 필터 지문을 못 쟀다 — 회수 필요 여부 판정 "
                          "불가, 기본 창"}
    old, why = _recorded_fp(state_path)
    if old == fp:
        return {"days": DEFAULT_LOOKBACK_DAYS, "record": False, "fp": fp,
                "recovery": False,
                "reason": f"관련성 필터 지문 {fp} 그대로 — 기본 창"}
    # ⚠️ '파서가 바뀌었다' 고 적지 않는다 — 지문은 렌더 모듈까지 덮으므로 바뀐
    # 게 파서인지 모르고, 기록이 없을 때는 바뀌었는지조차 모른다(#165).
    what = (f"관련성 필터 코드 지문 {old} → {fp}(필터가 쓰는 모듈이 바뀌었다)"
            if old else f"관련성 필터 코드 지문 {fp} — {why}")
    return {"days": RECOVERY_LOOKBACK_DAYS, "record": True, "fp": fp,
            "recovery": True,
            "reason": (f"{what}: 그 전에 버려졌을 캡션을 회수하려 최근 "
                       f"{RECOVERY_LOOKBACK_DAYS}일을 한 번 훑는다")}


# 자동 회수의 안전장치 둘(독립 리뷰 2026-09-24).
# ① 포워드가 일부 실패한 회수는 기록하지 않는다 — 일시 장애(연결·5xx)도 같은
#    '실패' 경로로 오므로, 기록하면 다음 틱이 3일로 돌아가 그 캡션을 영영 잃는다.
#    다만 **정말 지워진** 메시지는 매번 실패하므로 3회째에는 영구 실패로 보고
#    기록한다(수렴 지점, #171 — 무한히 40일을 훑으며 6시간마다 알리지 않게).
RECOVERY_MAX_ATTEMPTS = 3
# ② 자동 회수가 한 번에 포워드할 유닛 상한 — 평소 회수는 파서 배포 전에
#    버려진 캡션 몇 건이다(월간 소스 한 달치가 수십 장: kr_stock 21·jp_stock 13
#    실측). 이보다 많으면 새 파서가 너무 넓게 잡았을 가능성이 커서, 40일치를
#    비공개 채널에 한 번에 쏟지 않고 멈춰 사람에게 묻는다(명시 창은 이 상한 밖).
RECOVERY_MAX_UNITS = 100


def _write_state(state_path: Path, obj: dict) -> None:
    """tmp + replace 원자 쓰기. 형제 `price_provider._atomic_write_json` 과 같은
    방식이지만 import 하지 않는다 — 지문 폐포는 함수 본문의 import 도 따라가고,
    그 모듈의 ImportError 가 `except OSError` 밖으로 새면 성공한 동기화가
    트레이스백으로 끝난다(독립 리뷰 L3)."""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def record_sync(state_path: Path, fp: str, *,
                now: datetime | None = None) -> bool:
    """성공한 회수 뒤 지문을 기록한다(재시도 표식은 지운다). 못 잰 지문(`""`)은
    기록하지 않는다."""
    if not fp:
        return False
    now = now or datetime.now(timezone.utc)
    _write_state(state_path, {"relevance_fp": fp, "recorded_at": now.isoformat()})
    return True


def finish_recovery(state_path: Path, fp: str, *, failed_units: int,
                    now: datetime | None = None) -> tuple[bool, str]:
    """성공한(rc 0) 회수 실행 뒤 → (기록했나, 사람이 읽는 사유).

    포워드가 전부 됐으면 기록한다. 일부 실패면 기록하지 않고 **재시도 횟수만**
    남긴다(옛 지문은 그대로 둬야 다음 틱이 다시 넓게 훑는다). 같은 지문으로
    `RECOVERY_MAX_ATTEMPTS` 회째에도 실패가 남으면 영구 실패로 보고 기록한다."""
    if not fp:
        return False, "관련성 필터 지문을 못 재 기록하지 않는다"
    if failed_units <= 0:
        record_sync(state_path, fp, now=now)
        return True, (f"관련성 필터 지문 {fp} 기록 — 다음 동기화부터 기본 "
                      f"{DEFAULT_LOOKBACK_DAYS}일")
    rec, _why = _read_state(state_path)
    rec = rec or {}
    retry = rec.get("retry") if isinstance(rec.get("retry"), dict) else {}
    n = (int(retry.get("count") or 0) if retry.get("fp") == fp else 0) + 1
    if n >= RECOVERY_MAX_ATTEMPTS:
        record_sync(state_path, fp, now=now)
        return True, (f"포워드 실패 {failed_units}건이 회수 {n}회째에도 남았다 — "
                      f"영구 실패(삭제·포워드 불가)로 보고 지문 {fp} 기록")
    now = now or datetime.now(timezone.utc)
    keep = {k: v for k, v in rec.items() if k != "retry"}
    _write_state(state_path, {**keep, "retry": {"fp": fp, "count": n,
                                                "at": now.isoformat()}})
    return False, (f"포워드 실패 {failed_units}건 — 지문을 기록하지 않아 다음 "
                   f"동기화가 회수를 다시 시도한다({n}/{RECOVERY_MAX_ATTEMPTS})")


def labels() -> str:
    """로그·문서용 소스 나열. 하드코딩 문자열을 대체해 드리프트를 막는다."""
    return " · ".join(s.label for s in SOURCES)


def by_key(key: str) -> Source | None:
    return next((s for s in SOURCES if s.key == key), None)


def nav_sources() -> list[Source]:
    """nav 표시 순서대로 — html_file 이 있는 소스만."""
    out = [by_key(k) for k in NAV_ORDER]
    return [s for s in out if s is not None and s.html_file]


def nav_html() -> str:
    """index.html 상단 형제 대시보드 링크. 하드코딩 9줄을 대체해, 소스를
    추가하면 nav 도 자동으로 따라오게 한다(옛 코드는 nav 만 빠뜨리면 페이지가
    생성돼도 도달 불가였다)."""
    return "".join(
        f' &nbsp;·&nbsp; <a href="{s.html_file}">{s.nav_label} →</a>'
        for s in nav_sources()
    )
