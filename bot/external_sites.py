"""메인 nav 에 거는 **외부 사이트** 단일 레지스트리.

사용자 2026-08-21: "쩜상리서치 대시보드를 우리 대시보드에 연결해줘 …
부동산 다음에 똑같이 | 줄 그은다음에. 자주 봐야겠어."

⚠️ **여기 등재는 사용자가 그 건을 명시했을 때만** 한다(사용자 2026-08-21
정정: "Sites 로 요청하는건 여전히 텔레그램만 올리고 내가 따로 명시해서
올려달라고 하면 그때만 메인 Nav 에 올려"). "Sites 에 추가" 요청은 `/sites`
(`_SITES_TEXT`)에만 넣는다 — 그래서 `_SITES_TEXT` 30여 개 중 여기 있는 건
다섯뿐이고, 그게 정상이다. 임의로 옮기지 말 것.

⚠️ 왜 레지스트리인가. 같은 사이트가 `/sites`(텔레그램)와 대시보드 nav 두
표면에 실린다. 두 곳에 따로 적으면 한쪽만 갱신돼 갈라진다(CLAUDE.md 규칙
10c '명령 = 텔레그램·대시보드 단일 레지스트리' 의 사이트판). nav 는 여기서
**생성**하고, `_SITES_TEXT` 는 회귀가 이 목록을 전부 담고 있는지 강제한다.

`nav_label` 과 `sites_title` 이 다른 건 의도다 — nav 는 좁아서 짧아야 하고
(`쩜상리서치`), `/sites` 는 설명형이어도 된다(`쩜상리서치 대시보드`).
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    nav_label: str      # 대시보드 nav 표시(짧게)
    sites_title: str    # /sites 목록 표시(설명형 허용)
    url: str


# 사용자 2026-08-21 지정 순서·제목 그대로.
SITES: tuple[Site, ...] = (
    Site("쩜상리서치", "쩜상리서치 대시보드",
         "https://upperlimitprice.github.io/dashboards/"),
    Site("Jusikbot", "Jusikbot — Real-time Stock Dashboard",
         "https://jusikbot.com/"),
    Site("국민연금", "국민연금 현황", "https://whale-insight.com/"),
    Site("Funeasy", "funesay board", "https://easyconomics.com/"),
    Site("밸류업", "activeholders", "https://activeholders.com/"),
    # 사용자 2026-08-21: "이것도 대시보드에 넣어주고, Nav 제목은 Stockeasy".
    Site("Stockeasy", "Stockeasy", "https://stockeasy.intellio.kr/"),
    # 사용자 2026-08-22: nav 제목 지정 — "Stockhub" · "싹다분석".
    Site("Stockhub", "Stockhub", "https://stockhub.kr/"),
    Site("싹다분석", "싹다분석", "https://stocks.allreview.kr/"),
    # 사용자 2026-08-26: "Sites 에 추가해주고 대시보드 Nav 도 추가, 둘다
    # 제목은 'Nvidia screener'".
    Site("Nvidia screener", "Nvidia screener",
         "https://nvidiascreener.streamlit.app/"),
)


def nav_html() -> str:
    """부동산 뒤에 붙는 nav 조각(`|` 구분자 포함). 없으면 빈 문자열.

    ⚠️ 외부로 나가는 링크라 `target="_blank"` + `rel="noopener"` — 새 탭이
    우리 대시보드를 `window.opener` 로 건드리지 못하게 한다."""
    if not SITES:
        return ""
    links = " &middot; ".join(
        f'<a href="{_html.escape(s.url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{_html.escape(s.nav_label)}</a>'
        for s in SITES)
    return ('<span style="white-space:nowrap">&nbsp;|&nbsp;</span>' + links)
