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
from trade import jp2_exports as _jp2
from trade import kr_stock_exports as _krs
from trade import mx_exports as _mx
from trade import my_exports as _my
from trade import ph_exports as _ph
from trade import th_exports as _th
from trade import tw_exports as _tw
from trade import us_imports as _us


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


# 순서 = ingest 폴백 순서. 뒤로 갈수록 나중에 시도된다.
# 한국(종목별)은 유일한 **회사 기준** 소스라 맨 뒤(가장 좁은 마커).
#
# nav_label 이모지 규칙(사용자 2026-07-11 스크린샷 교훈): 🇹🇼/🇨🇳 같은
# **flag-sequence 는 일부 폰트에서 'tw' 문자로 렌더**되므로 논-플래그
# 이모지(국가 상징 동물/음식)를 쓴다. 🇺🇸 만 예외적으로 유지(기존 표기).
SOURCES: tuple[Source, ...] = (
    Source("tw", "대만", _tw.parse_tw_export, _tw.open_tw_db, _tw.ingest,
           _tw.regenerate, "tw.db", "tw.html", "🧋 대만 수출 데이터(나쁜양파)"),
    Source("cn", "중국", _cn.parse_cn_export, _cn.open_cn_db, _cn.ingest,
           _cn.regenerate, "cn.db", "cn.html", "🐼 중국 수출 데이터(나쁜양파)"),
    Source("jp2", "일본", _jp2.parse_jp2_export, _jp2.open_jp2_db, _jp2.ingest,
           _jp2.regenerate, "jp2.db", "jp2.html",
           "🎌 일본 수출 데이터(나쁜양파)"),
    Source("th", "태국", _th.parse_th_export, _th.open_th_db, _th.ingest,
           _th.regenerate, "th.db", "th.html", "🐘 태국 수출 데이터(나쁜양파)"),
    Source("my", "말레이시아", _my.parse_my_export, _my.open_my_db, _my.ingest,
           _my.regenerate, "my.db", "my.html",
           "🐯 말레이시아 수출 데이터(나쁜양파)"),
    Source("ph", "필리핀", _ph.parse_ph_export, _ph.open_ph_db, _ph.ingest,
           _ph.regenerate, "ph.db", "ph.html",
           "🥭 필리핀 수출 데이터(나쁜양파)"),
    Source("mx", "멕시코", _mx.parse_mx_export, _mx.open_mx_db, _mx.ingest,
           _mx.regenerate, "mx.db", "mx.html", "🌮 멕시코 수출 데이터(나쁜양파)"),
    Source("us", "미국 수입", _us.parse_us_import, _us.open_us_db, _us.ingest,
           _us.regenerate, "us.db", "us.html", "🇺🇸 미국 수입 데이터(나쁜양파)"),
    Source("krs", "한국 수출(종목별)", _krs.parse_kr_stock_export,
           _krs.open_kr_stock_db, _krs.ingest, _krs.regenerate,
           "kr_stock.db", "kr_stock.html",
           # 한국만 품목(HS)이 아니라 **종목(회사)** 기준이라 라벨에 명시.
           "🏢 한국 수출 데이터(종목별·나쁜양파)"),
)

# nav 표시 순서 — **ingest 순서와 다르다.** 일본(나쁜양파)은 사용자 요청으로
# 일본(비온) 바로 옆에 붙어야 해서(2026-07-11 "일본은 2개의 서로 다른 채널에서
# 2개의 대쉬보드로 갈거야") 맨 앞이다. SOURCES 를 재정렬하면 라우팅이 바뀌므로
# 표시 순서만 여기서 따로 잡는다. 키 누락은 테스트가 잡는다(누락 시 새 소스가
# 생성은 되는데 nav 에 없어 **도달 불가** — is_relevant 가 미매칭 알림까지
# 눌러버려 조용한 유실이 된다).
NAV_ORDER: tuple[str, ...] = ("jp2", "tw", "cn", "th", "my", "ph", "mx",
                              "us", "krs")


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
