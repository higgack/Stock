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

from dataclasses import dataclass
from typing import Callable

from trade import cn_exports as _cn
from trade import cn_stock_exports as _cns
from trade import cn_stock_imports as _cni
from trade import jp2_exports as _jp2
from trade import jp_stock_exports as _jps
from trade import kr_stock_exports as _krs
from trade import mx_exports as _mx
from trade import my_exports as _my
from trade import my_stock_exports as _mys
from trade import ph_exports as _ph
from trade import th_exports as _th
from trade import tw_exports as _tw
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
           country="대만", basis="item", flow="export"),
    Source("cn", "중국", _cn.parse_cn_export, _cn.open_cn_db, _cn.ingest,
           _cn.regenerate, "cn.db", "cn.html", "🐼 중국 수출 데이터(나쁜양파)",
           country="중국", basis="item", flow="export"),
    Source("jp2", "일본", _jp2.parse_jp2_export, _jp2.open_jp2_db, _jp2.ingest,
           _jp2.regenerate, "jp2.db", "jp2.html",
           "🎌 일본 수출 데이터(나쁜양파)",
           country="일본", basis="item", flow="export"),
    Source("th", "태국", _th.parse_th_export, _th.open_th_db, _th.ingest,
           _th.regenerate, "th.db", "th.html", "🐘 태국 수출 데이터(나쁜양파)",
           country="태국", basis="item", flow="export"),
    Source("my", "말레이시아", _my.parse_my_export, _my.open_my_db, _my.ingest,
           _my.regenerate, "my.db", "my.html",
           "🐯 말레이시아 수출 데이터(나쁜양파)",
           country="말레이시아", basis="item", flow="export"),
    Source("ph", "필리핀", _ph.parse_ph_export, _ph.open_ph_db, _ph.ingest,
           _ph.regenerate, "ph.db", "ph.html",
           "🥭 필리핀 수출 데이터(나쁜양파)",
           country="필리핀", basis="item", flow="export"),
    Source("mx", "멕시코", _mx.parse_mx_export, _mx.open_mx_db, _mx.ingest,
           _mx.regenerate, "mx.db", "mx.html", "🌮 멕시코 수출 데이터(나쁜양파)",
           country="멕시코", basis="item", flow="export"),
    Source("us", "미국 수입", _us.parse_us_import, _us.open_us_db, _us.ingest,
           _us.regenerate, "us.db", "us.html", "🗽 미국 수입 데이터(나쁜양파)",
           country="미국", basis="item", flow="import"),
    # 미국 PPI(사용자 2026-08-19) — 유일하게 **금액이 아니라 지수** 소스.
    # 마커('미국 PPI')는 us_imports 의 'N월 수입 미국' 과 겹치지 않지만,
    # 품목(HS) 기준이므로 **종목 기준(krs/jps) 앞**에 둔다(아래 계약).
    Source("uppi", "미국 PPI", _uppi.parse_us_ppi, _uppi.open_us_ppi_db,
           _uppi.ingest, _uppi.regenerate, "us_ppi.db", "us_ppi.html",
           "📈 미국 PPI 데이터(나쁜양파)",
           country="미국", basis="item", flow="index"),
    Source("krs", "한국 수출(종목별)", _krs.parse_kr_stock_export,
           _krs.open_kr_stock_db, _krs.ingest, _krs.regenerate,
           "kr_stock.db", "kr_stock.html",
           # 한국만 품목(HS)이 아니라 **종목(회사)** 기준이라 라벨에 명시.
           "🏢 한국 수출 데이터(종목별·나쁜양파)",
           country="한국", basis="company", flow="export"),
    # 일본도 품목(jp2)과 **종목** 두 갈래다. 종목판은 jp2 파서가 회사 헤더를
    # 못 읽어 관련성 필터에서 통째로 드랍되고 있었다(2026-08-16 실측 8건).
    Source("jps", "일본 수출(종목별)", _jps.parse_jp_stock_export,
           _jps.open_jp_stock_db, _jps.ingest, _jps.regenerate,
           "jp_stock.db", "jp_stock.html",
           "🗼 일본 수출 데이터(종목별·나쁜양파)",
           country="일본", basis="company", flow="export"),
    # 말레이시아도 품목(my)과 **종목** 두 갈래다. 26년 7월 종목판이 채널에
    # 떴는데 품목 파서의 마커가 `N월 수출 말레이시아`(어순 반대)라 관련성
    # 필터를 통과 못 하고 통째로 드랍됐다(사용자 2026-08-20 — 일본이
    # 2026-08-16 에 겪은 것과 같은 사고).
    Source("mys", "말레이시아 수출(종목별)", _mys.parse_my_stock_export,
           _mys.open_my_stock_db, _mys.ingest, _mys.regenerate,
           "my_stock.db", "my_stock.html",
           "🐆 말레이시아 수출 데이터(종목별·나쁜양파)",
           country="말레이시아", basis="company", flow="export"),
    # 중국도 품목(cn)과 **종목** 두 갈래다. 26년 7월 종목판이 채널에 떴는데
    # 품목 파서의 마커가 `N월 수출 중국`(어순 반대)이라 관련성 필터를
    # 통과 못 하고 통째로 드랍됐다(사용자 2026-08-21 — 일본 2026-08-16,
    # 말레이시아 2026-08-20 과 **같은 사고**. 어순 반대 형제 포맷은 네
    # 번째다: 새 나라의 종목판이 뜨면 또 난다고 보는 게 맞다).
    Source("cns", "중국 수출(종목별)", _cns.parse_cn_stock_export,
           _cns.open_cn_stock_db, _cns.ingest, _cns.regenerate,
           "cn_stock.db", "cn_stock.html",
           "🏮 중국 수출 데이터(종목별·나쁜양파)",
           country="중국", basis="company", flow="export"),
    # 중국은 **수입** 종목판도 온다(사용자 2026-08-21 "중국수입 7월 기업도
    # 있어"). 수출과 마커 한 단어만 다르므로 문법 엔진(`cn_stock_flow`)을
    # 공유하고 여기선 독립 소스로 등록한다 — DB·페이지·라우팅은 별개다.
    Source("cni", "중국 수입(종목별)", _cni.parse_cn_stock_import,
           _cni.open_cn_stock_import_db, _cni.ingest, _cni.regenerate,
           "cn_stock_import.db", "cn_stock_import.html",
           "🧧 중국 수입 데이터(종목별·나쁜양파)",
           country="중국", basis="company", flow="import"),
)

# nav 표시 순서 — **ingest 순서와 다르다.** SOURCES 를 재정렬하면 라우팅이
# 바뀌므로 표시 순서는 여기서 따로 만든다.
#
# 규약(사용자 2026-08-20 "품목 레퍼런스북 다음은 우선순위대로 나라별, 품목별,
# 회사별, 수출다음 수입순. 나라의 순서는 대시보드의 개수가 많은것 우선"):
#   ① 나라로 묶고, 나라는 **대시보드 개수 내림차순**
#   ② 한 나라 안에서는 품목별 → 회사별
#   ③ 그 다음 수출 → 수입 → 지수
#
# ⚠️ 손으로 나열하지 않고 **계산한다.** 옛 코드는 12개를 직접 적어 뒀는데,
# 그러면 새 소스를 추가할 때마다 규약과 어긋난 자리에 조용히 놓인다(실수 #24
# — 목록형은 목록 밖을 못 본다). 실제로 mys 를 추가했을 때 규약상 my 바로
# 옆이어야 하는데 손으로 끼워 넣어야 했다. 이제 축(country/basis/flow)만
# 밝히면 자리는 자동이고, 축을 안 밝히면 dataclass 가 즉시 터진다.
_BASIS_RANK = {"item": 0, "company": 1}
_FLOW_RANK = {"export": 0, "import": 1, "index": 2}

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
