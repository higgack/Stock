"""한글 렌더 가능한 폰트 찾기 — **이름 목록이 아니라 글리프 실측**으로.

⚠️ 왜 바꿨나(사용자 2026-08-18 LPK.DE 분기실적 "이미지 렌더 불가(서버 한글
폰트 미설치)"). 옛 판정은 `NanumGothic.ttf` 등 **경로 3개**와 이름에 "Nanum"
이 들어가는지만 봤다 — Noto Sans CJK KR 처럼 한글이 완벽히 되는 폰트가 깔려
있어도 "미설치"로 단정하고 이미지를 통째로 포기했다. 목록형 판정이 목록 밖
현실을 못 보는 실수 #24 와 같은 모양이다.

여기서는 후보 폰트를 열어 **U+AC00('가')·U+D55C('한') 글리프가 실제로 있는지**
확인한다. 이름이 무엇이든 한글이 그려지면 쓰고, Nanum 이어도 글리프가 없으면
안 쓴다. 판정 근거는 `diagnose()` 가 문장으로 돌려주므로 화면이 "왜 안 되는지"
를 말할 수 있다(실수 #12 silent-fail 금지).

matplotlib 전용 모듈이 아니다 — 경로만 돌려주므로 다른 렌더러도 쓸 수 있다.
"""
from __future__ import annotations

import glob
import logging
import os

log = logging.getLogger("bot.korean_font")

# 같은 값이면 앞쪽 우선. 한글 본문용으로 검증된 순서(가독성·굵기 밸런스).
_PREFERRED = (
    "nanumgothic", "nanumbarungothic", "nanumsquare",
    "notosanscjkkr", "notosanskr", "sourcehansanskr", "notosanscjk",
    "malgungothic", "applesdgothicneo", "applegothic",
    "wenquanyizenhei", "d2coding", "gulim", "batang",
)

# 배포 환경별 폰트 디렉터리. 여기 없는 곳에 깔려 있어도 matplotlib 캐시에
# 잡히면 찾는다(두 경로를 **둘 다** 훑는다).
_FONT_DIRS = (
    "/usr/share/fonts", "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts"),
    "/Library/Fonts", os.path.expanduser("~/Library/Fonts"),
    "C:/Windows/Fonts",
)

_HANGUL_PROBE = (0xAC00, 0xD55C)      # '가', '한'
# ⚠️ **만능 폴백 폰트를 걸러내는 반대 증거.** matplotlib 이 동봉한
# `LastResortHE-Regular.ttf` 는 모든 코드포인트에 글리프를 준다 — 한글 검사만
# 하면 통과하는데 실제로는 네모(두부)만 그린다. 미할당 코드포인트에도 글리프가
# 있으면 그건 폴백 폰트다(실측 2026-08-18: LastResort 는 U+0E5C·U+FFFFE 에도
# 인덱스를 준다, 정상 한글 폰트 wqy-zenhei 는 0).
_UNASSIGNED_PROBE = (0x0E5C, 0xFFFFE)

_CACHE: dict[str, str | None] = {}


def _has_hangul(path: str) -> bool:
    """폰트 파일이 한글 글리프를 **실제로** 갖고 있는지. 못 열면 False."""
    try:
        from matplotlib.ft2font import FT2Font
        f = FT2Font(path)
        if not all(f.get_char_index(cp) for cp in _HANGUL_PROBE):
            return False
        # 만능 폴백(두부 폰트) 제외 — 위 주석 참조.
        return not any(f.get_char_index(cp) for cp in _UNASSIGNED_PROBE)
    except Exception:
        return False


def _rank(path: str) -> int:
    stem = os.path.basename(path).rsplit(".", 1)[0].replace(" ", "").replace("-", "").lower()
    for i, name in enumerate(_PREFERRED):
        if name in stem:
            return i
    return len(_PREFERRED)


def _candidates() -> list[str]:
    out: list[str] = []
    try:
        import matplotlib.font_manager as fm
        out.extend(f.fname for f in fm.fontManager.ttflist)
    except Exception:
        pass
    for d in _FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for ext in ("ttf", "otf", "ttc"):
            out.extend(glob.glob(os.path.join(d, "**", f"*.{ext}"), recursive=True))
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def find_font() -> str | None:
    """한글이 그려지는 폰트 파일 경로. 없으면 None. 프로세스 1회 탐색."""
    if "path" in _CACHE:
        return _CACHE["path"]
    best: str | None = None
    best_rank = len(_PREFERRED) + 1
    for p in sorted(_candidates(), key=_rank):
        r = _rank(p)
        if r >= best_rank:
            continue                       # 이미 더 좋은 걸 찾았다
        if _has_hangul(p):
            best, best_rank = p, r
            if r == 0:
                break                      # 최우선 후보 — 더 볼 필요 없다
    _CACHE["path"] = best
    if best:
        log.info("korean_font: %s", best)
    else:
        log.warning("korean_font: 한글 글리프를 가진 폰트를 못 찾았다")
    return best


def setup_matplotlib() -> bool:
    """matplotlib rcParams 에 한글 폰트를 걸어준다. 성공 시 True."""
    p = find_font()
    if not p:
        return False
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.font_manager as fm
        from matplotlib import rcParams
        fm.fontManager.addfont(p)
        rcParams["font.family"] = fm.FontProperties(fname=p).get_name()
        rcParams["axes.unicode_minus"] = False      # U+2212 결손 폰트 대비
        return True
    except Exception as exc:                        # noqa: BLE001
        log.warning("korean_font: matplotlib 설정 실패: %s", exc)
        return False


def diagnose() -> str:
    """왜 되는지/안 되는지 한 문장 — 화면과 로그가 같은 말을 하도록."""
    p = find_font()
    if p:
        return f"한글 폰트 사용: {os.path.basename(p)}"
    n = len(_candidates())
    return (f"한글 글리프를 가진 폰트 없음(후보 {n}개 검사). "
            "설치: sudo apt-get install -y fonts-nanum "
            "(또는 fonts-noto-cjk) 후 봇 재시작")
