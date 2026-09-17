"""차트 공시 제목 CN/JP/TW → 한국어 번역 (Gemini Flash, 영구 캐시).

CN/JP/TW 공시 제목은 자유 텍스트라 사전 완역 불가 → Flash 로 번역. 비용은 **영구
디스크 캐시**로 최소화(제목당 1회만 번역 → 이후 ₩0) + **배치**(여러 제목 한 콜).
graceful: GOOGLE_API_KEY 부재/실패 시 {} 반환(원문 유지). KR(원래 한국어)·US(고정
사전 완역)는 호출 안 함 — chart_events 가 CN/JP/TW 에서만 호출.

비용: Flash, 제목당 토큰 소액 + 영구 캐시라 단일 채널 기준 무시 가능. usage.jsonl 에
subsystem='chart_translate' 로 기록(메인 대시보드 총합에 포함, 분석 버킷으로 폴딩).
"""
from __future__ import annotations

import json
import logging
import os
from bot.genai_factory import effective_key as _effective_key
import re
import tempfile
import threading
import hashlib as _hashlib
import time
from pathlib import Path

log = logging.getLogger("bot.chart_translate")

_HOME = Path.home() / ".tradingagents"
_CACHE = _HOME / "chart_title_kr.json"
_USAGE = _HOME / "usage.jsonl"
_USD_TO_KRW = 1330.0
_FLASH_IN, _FLASH_OUT = 0.30, 2.50   # gemini-2.5-flash $/M
_MAX_BATCH = 40                       # 한 콜당 최대 제목 수(토큰 bound)


_SALVAGE_LOCK = threading.Lock()

# 한자/한글 판정 — "이 답이 정말 번역인가" 를 재는 단일 출처(#38).
# `bollinger_board.has_han` 이 같은 판정을 따로 들고 있었는데, 정작 **번역을
# 캐시하는 쪽**은 그 판정을 안 써서 한자 그대로인 답이 영구 캐시에 들어갔다.
_HAN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_HANGUL_RE = re.compile(r"[가-힣]")
# 모델이 입력 형식(`티커 | 현지명`)을 그대로 되읊는 경우를 벗긴다 — 실측:
# 대만 급등락 19행이 `3296.TW` 인데 이름줄이 `3296.TWO | 승덕` 이었다
# (사용자 2026-09-17 캡처). `_strip_dup_ticker` 는 **그 행의 티커와 정확히
# 같을 때만** 벗기므로 접미사가 어긋난 되읊기(.TW vs .TWO)는 화면까지 샌다.
_ECHO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,14}\s*\|\s*(.+)$")


_MISS_CACHE = _HOME / "translate_miss.json"

# 프롬프트는 **상수**다 — 지문(`_prompt_fp`)을 뜨려면 데이터 줄과 분리돼야 한다.
# ⚠️ 마지막 줄 "한자를 그대로 두지 마세요" 가 이번 라운드의 핵심이다(사용자
# 2026-09-17 "한글화 안된것들"): 옛 프롬프트는 한자 음독만 금지하고 **대안을
# 안 줘서**, 통용 한글명이 없는 소형주(百達-KY·昶瑞機電·三商電)에서 모델이
# 원문을 그대로 돌려줬다. 공식 영문명이라는 퇴로를 열어 준다.
_TITLE_PROMPT = (
    "다음은 해외 증시 공시 제목입니다. 각 줄을 자연스럽고 간결한 한국어로 번역하세요.\n"
    "- 회사명은 한국에서 통용되는 명칭(예: 贵州茅台→귀주모태주, トヨタ→도요타)\n"
    "- 통용 한글명이 없는 회사는 **공식 영문명**(로마자)을 쓰고 한자를 그대로 두지 마세요.\n"
    "- 번역문만, 입력과 동일한 '번호. 번역' 형식으로 같은 번호 유지\n"
    "- 군더더기 설명 금지, 한 줄당 한 번역\n\n")

_NAME_PROMPT = (
    "다음은 대만/중국/홍콩 상장사입니다('티커 | 현지명'). 각 줄을 한국 투자자에게 "
    "통용되는 **간결한 한글 회사명**으로 바꾸세요.\n"
    "- 영문 통용명 기준 음역: 華邦電 → 윈본드, 鴻海 → 폭스콘, 聯發科 → 미디어텍, "
    "比亞迪 → 비야디, 騰訊 → 텐센트, 阿里巴巴 → 알리바바\n"
    "- 한국에서 영문 약자로 더 통용되면 영문 유지: 台積電 → TSMC, 聯電 → UMC, "
    "中芯國際 → SMIC, 日月光 → ASE\n"
    "- 한자 음독(화봉전 등) 금지. 법인격(Corporation/股份有限公司/控股) 생략, "
    "핵심 브랜드만. 한 줄당 결과 하나, 입력과 동일한 '번호. 결과' 형식, 같은 "
    "번호 유지. 설명 금지.\n"
    "- 통용 한글명이 없으면 그 회사의 **공식 영문명**(로마자)을 쓰세요. "
    "한자를 그대로 두지 마세요. 티커를 되읊지 말고 이름만 쓰세요.\n\n")


def _prompt_fp(template: str) -> str:
    """프롬프트 **내용**의 지문 — 손으로 올리는 버전은 이 레포에서 여섯 번
    졌다(#119). 프롬프트를 고치면 지문이 바뀌어 실패 기록이 저절로 무효가
    되고, 그 종목이 다음 배치에서 자동으로 다시 시도된다(#348)."""
    return _hashlib.sha1(template.encode("utf-8")).hexdigest()[:10]


def _miss_load() -> dict:
    return _read_json_cache(_MISS_CACHE)


def _miss_skip(cache_miss: dict, key: str, fp: str) -> bool:
    """이 프롬프트로 이미 실패한 항목인가 — 같은 지문이면 다시 안 묻는다.

    ⚠️ 기록이 없으면 **묻는다**(#54 대조 0건은 통과가 아니다). 그리고 지문이
    다르면 프롬프트가 바뀐 것이므로 다시 묻는다 — 실패를 영구 기록하면
    프롬프트를 고쳐도 영영 재시도가 없다(#171 가드가 '못 만든다' 로 끝나면
    그 자리가 영원히 빈다)."""
    rec = (cache_miss or {}).get(key)
    return isinstance(rec, dict) and rec.get("ver") == fp


def _miss_record(cache_miss: dict, key: str, fp: str, why: str) -> None:
    cache_miss[key] = {"ver": fp, "why": why}


def miss_diag(titles: list | None = None, tickers: list | None = None) -> dict:
    """{키: {gate, why, ver, retry}} — **읽기 전용 조회**(LLM·네트워크 0).

    사용자 2026-09-17 "대만 급등/급락 종목들 최대한 한글화 한것 맞지? 5번은
    넘게 이거 돌리는듯하네": 화면·진단이 아직 한자인 종목을 두고 "다음 빌드의
    LLM 번역이 채운다"고 말해 왔는데, **거부된 번역은 여기 기록이 남아 같은
    프롬프트로는 다시 묻지 않는다**(`_miss_skip`). 그래서 기다려도 안 바뀌고
    사용자가 같은 화면을 다시 돌린다 — 설명이 코드와 어긋나면 버그다(#55).

    ⚠️ **관문이 둘이다**(독립 리뷰 2026-09-17 B1 — 첫 판은 하나만 봤다):
    `translate_titles_kr` 는 **원문 문자열** 키 + `_TITLE_PROMPT` 지문,
    `translate_names_kr` 는 **티커** 키 + `_NAME_PROMPT` 지문으로 같은 파일에
    쓴다. 한 관문만 보면 (a) 다른 관문에 막힌 종목을 '아직 안 물었다' 로 말하고
    (b) 운영자가 그 관문의 프롬프트를 고쳐도 진단이 계속 "기다려도 안 바뀐다"
    고 한다(#364 고친 것이 안 먹은 것처럼 보였다). 그래서 지문 상수를 호출부가
    고르지 않고 **관문 이름에서 파생**시킨다(#38).

    `retry` = 그 관문이 지금 프롬프트로 **다시 물어보나**. False 면 프롬프트가
    바뀌기 전엔 영구다(#171 가드가 '못 만든다' 로 끝나면 그 자리가 영원히 빈다).

    ⚠️ '읽기 전용' 의 범위: LLM·네트워크를 안 쓰고 miss 기록을 **의미상** 바꾸지
    않는다. 파일이 깨진 UTF-8 이면 공용 캐시 독자(`_read_json_cache`)가 정리본을
    쓴다 — 그건 제품이 어차피 하는 수리다(#331·#284 '안 쓴다'는 무엇을 안 쓰는지까지).
    """
    cache_miss = _miss_load()
    out: dict = {}
    for keys, gate, tpl in ((titles, "titles", _TITLE_PROMPT),
                            (tickers, "names", _NAME_PROMPT)):
        fp = _prompt_fp(tpl)
        for k in keys or []:
            rec = (cache_miss or {}).get(k)
            if not isinstance(rec, dict):
                continue
            out[k] = {"gate": gate, "why": str(rec.get("why") or ""),
                      "ver": str(rec.get("ver") or ""),
                      "retry": rec.get("ver") != fp}
    return out


def has_han(name: str | None) -> bool:
    """한자가 남아 있으면 아직 한글화 안 된 이름이다(한국어엔 한자가 없다)."""
    return bool(_HAN_RE.search(name or ""))


def clean_answer(answer: str) -> str:
    """모델 답에서 되읊은 `티커 | ` 접두를 벗긴다(순수). 없으면 원본."""
    m = _ECHO_RE.match((answer or "").strip())
    return m.group(1).strip() if m else (answer or "").strip()


def looks_translated(src: str, answer: str) -> bool:
    """그 답을 **번역으로 받아들일지**(순수).

    ⚠️ 옛 판은 `if kr:` 만 봤다 — 비어 있지만 않으면 영구 캐시에 넣었다.
    그래서 모델이 한자를 **그대로 되돌려주면** 그게 '한국어 회사명' 으로
    굳어 다시는 안 고쳐진다(사용자 2026-09-17 `百達-KY`·`昶瑞機電`·`三商電`
    이 그 상태). '있다' 만 묻는 검사는 눈이 먼다(#25) — 무엇이 있는지 본다.

    거부 둘: (a) 답이 원문과 같다(에코) (b) 한자가 남았는데 한글이 하나도
    없다. (b) 는 `百達-KY → 바이다-KY` 처럼 **한글이 섞인** 답은 통과시킨다.
    """
    a, s_ = (answer or "").strip(), (src or "").strip()
    if not a or a == s_:
        return False
    return not (has_han(a) and not _HANGUL_RE.search(a))


def _atomic_write_json(path: Path, d: dict) -> None:
    """고유 임시파일(mkstemp) + os.replace — reader 가 찢어진 JSON 을 보지 않고
    (#280), 렌더 스레드 여럿과 워밍 스레드가 **같은 tmp 이름**을 나눠 쓰다 서로의
    내용을 섞거나 두 번째 replace 가 FileNotFoundError 로 죽는 일이 없다(독립
    리뷰 2026-09-10 #4 — 읽기 경로가 정리본을 쓰게 되면서 생긴 경쟁)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(d, ensure_ascii=False))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _drop_replacement_entries(d: dict) -> tuple[dict, int]:
    """U+FFFD(대체문자)가 키·값에 든 항목을 버린다. 남기면 (a) 깨진 이름이 화면에
    그대로 실리고 (b) 캐시 히트라 **영원히 다시 번역되지 않으며** (c) 다음
    `_save` 가 그걸 정상 항목으로 굽는다(독립 리뷰 2026-09-10 #1). 버린 항목은
    캐시 미스가 되어 다음 워밍이 다시 채운다(항목 수만큼만 재과금)."""
    bad = [k for k, v in d.items()
           if "\ufffd" in str(k) or (isinstance(v, str) and "\ufffd" in v)]
    for k in bad:
        d.pop(k, None)
    return d, len(bad)


def _read_json_cache(path: Path) -> dict:
    """번역 캐시 파일을 **어떤 경우에도 던지지 않고** 읽는다.

    2026-09-10 VM 실측: `chart_title_kr.json` 의 432,419번째 바이트가 0x8d 라
    `read_text(encoding="utf-8")` 이 UnicodeDecodeError 를 던졌다 — 옛 except 는
    JSONDecodeError·OSError 만 잡아 그 예외가 **호출부 전부로** 나갔다. 대만
    급등락·52주 보드는 `enrich_for_panel` 의 넓은 except 가 삼켜 **업종·시총·
    한글명이 통째로 빈칸**이 됐고(사용자 캡처 그대로), 볼린저 2차 캐시는 조용히
    0건이었다(#12 silent-fail · #315 곁들이 하나가 본체를 지운다).
    깨진 바이트는 `errors="replace"` 로 걷어내고 파싱을 다시 시도한다 — 대개
    문자열 값 하나만 다치고 나머지 수천 항목은 산다. 다친 항목(U+FFFD 포함)은
    **버리고 정리본을 한 번 다시 써서** 다음 읽기부터는 경고가 안 뜬다.
    그래도 못 읽으면 {} 를 돌려주되 **경고를 남긴다**(침묵 금지 #43) — 그 경우
    번역이 다시 과금되므로 운영자가 알아야 한다. 파일 없음만 조용하고, 그 밖의
    OSError(권한·I/O)와 dict 가 아닌 본문도 경고한다."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.warning("translate cache %s: 읽기 실패(%s) — 빈 캐시로 시작(번역이 다시 과금된다)",
                    path.name, exc)
        return {}
    salvaged = False
    try:
        d = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        log.warning("translate cache %s: UTF-8 깨진 바이트(위치 %d) — 대체문자로 "
                    "걷어내고 다시 읽음(다친 항목만 손실)", path.name, exc.start)
        try:
            d = json.loads(raw.decode("utf-8", errors="replace"))
            salvaged = True
        except json.JSONDecodeError as exc2:
            log.warning("translate cache %s: 깨진 바이트를 걷어내도 JSON 이 아님(%s) — "
                        "빈 캐시로 시작(번역이 다시 과금된다)", path.name, exc2)
            return {}
    except json.JSONDecodeError as exc:
        log.warning("translate cache %s: JSON 파싱 실패(%s) — 빈 캐시로 시작", path.name, exc)
        return {}
    if not isinstance(d, dict):
        log.warning("translate cache %s: 본문이 dict 가 아님(%s) — 빈 캐시로 시작",
                    path.name, type(d).__name__)
        return {}
    if salvaged:
        d, n_bad = _drop_replacement_entries(d)
        log.warning("translate cache %s: 깨진 항목 %d개 버리고 정리본을 다시 씀(%d개 유지)",
                    path.name, n_bad, len(d))
        # 정리본 쓰기는 한 번이면 된다 — 동시에 읽은 스레드 여럿이 각자 쓰지 않게
        # 잠그고, 잠금을 얻은 뒤 파일이 이미 정상(다른 스레드가 먼저 정리)이면 건너뛴다.
        with _SALVAGE_LOCK:
            try:
                json.loads(path.read_bytes().decode("utf-8"))
                return d                                    # 누군가 먼저 정리했다
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
            try:
                _atomic_write_json(path, d)
            except OSError as exc:
                log.warning("translate cache %s: 정리본 쓰기 실패(%s) — 다음 읽기에 다시 걷어낸다",
                            path.name, exc)
    return d


def _load() -> dict:
    return _read_json_cache(_CACHE)


def _save(d: dict) -> None:
    try:
        _atomic_write_json(_CACHE, d)
    except OSError as exc:
        log.warning("translate cache %s: 쓰기 실패(%s)", _CACHE.name, exc)


def _log_usage(pt: int, ot: int) -> None:
    try:
        cost_usd = (pt * _FLASH_IN + ot * _FLASH_OUT) / 1e6
        with open(_USAGE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "type": "llm_call",
                                "model": "gemini-2.5-flash",
                                "prompt_tokens": pt, "completion_tokens": ot,
                                "cost_usd": round(cost_usd, 6),
                                "subsystem": "chart_translate"}) + "\n")
    except OSError:
        pass


_IND_CACHE = _HOME / "industry_en.json"


def _load_ind() -> dict:
    return _read_json_cache(_IND_CACHE)


def _save_ind(d: dict) -> None:
    try:
        _atomic_write_json(_IND_CACHE, d)
    except OSError as exc:
        log.warning("translate cache %s: 쓰기 실패(%s)", _IND_CACHE.name, exc)


def translate_industries_en(names: list[str]) -> dict:
    """{한글 업종명 → 영문} — 네이버 업종(reutersIndustryName/industryGroupKor)을
    표준 영문 산업명으로(사용자 2026-06-14 '모두 영문'). Flash 배치·영구 캐시
    (industry_en.json, ~150개 1회 → 이후 ₩0). graceful(키부재/실패 시 빠짐 →
    호출부 원문 유지)."""
    uniq = [n for n in dict.fromkeys(names) if n and n.strip()]
    if not uniq:
        return {}
    cache = _load_ind()
    out = {n: cache[n] for n in uniq if cache.get(n)}
    todo = [n for n in uniq if n not in cache][:_MAX_BATCH]
    if not todo:
        return out
    api_key = _effective_key()
    if not api_key:
        return out
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(todo))
    prompt = (
        "다음은 주식시장 '업종(산업) 분류명'입니다. 각 줄을 표준 영문 산업명으로 "
        "번역하세요.\n- 예: 반도체와반도체장비 → Semiconductors & Equipment, "
        "건설및엔지니어링 → Construction & Engineering, 다각화된통신서비스 → "
        "Diversified Telecommunication Services\n- 번역문만, 입력과 동일한 "
        "'번호. 번역' 형식, 같은 번호 유지. 설명 금지.\n\n" + lines)
    try:
        from bot.screener import _call_pro
        text, pt, ot = _call_pro(api_key, prompt, model="gemini-2.5-flash",
                                 enable_grounding=False)
        _log_usage(pt, ot)
        for line in (text or "").splitlines():
            m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            en = m.group(2).strip()
            if 0 <= idx < len(todo) and en:
                out[todo[idx]] = en
                cache[todo[idx]] = en
        _save_ind(cache)
    except Exception:
        pass
    return out


_NAME_CACHE = _HOME / "names_en.json"


def _load_name() -> dict:
    return _read_json_cache(_NAME_CACHE)


def _save_name(d: dict) -> None:
    try:
        _atomic_write_json(_NAME_CACHE, d)
    except OSError as exc:
        log.warning("translate cache %s: 쓰기 실패(%s)", _NAME_CACHE.name, exc)


def translate_names_en(names: list[str]) -> dict:
    """{中文/native 회사명 → 영문} — 대만 소형주 등 yfinance longName 부재 종목을
    통용 영문 회사명으로(사용자 2026-06-14 '대만 소형주 中文→영문 번역으로 reliable
    하게'). translate_industries_en 인프라 재사용 패턴. Flash 배치·영구 캐시
    (names_en.json → 종목당 1회·이후 ₩0). graceful(키부재/실패 시 빠짐 → 호출부
    원문 유지)."""
    uniq = [n for n in dict.fromkeys(names) if n and n.strip()]
    if not uniq:
        return {}
    cache = _load_name()
    out = {n: cache[n] for n in uniq if cache.get(n)}
    todo = [n for n in uniq if n not in cache][:_MAX_BATCH]
    if not todo:
        return out
    api_key = _effective_key()
    if not api_key:
        return out
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(todo))
    prompt = (
        "다음은 대만/중화권 상장사의 中文 회사명입니다. 각 줄을 통용되는 영문 "
        "회사명으로 번역하세요.\n- 예: 台積電 → TSMC, 鴻海 → Hon Hai (Foxconn), "
        "聯發科 → MediaTek, 長榮 → Evergreen Marine\n- 공식 영문명이 있으면 그것을, "
        "없으면 음역. 번역문만, 입력과 동일한 '번호. 번역' 형식, 같은 번호 유지. "
        "설명 금지.\n\n" + lines)
    try:
        from bot.screener import _call_pro
        text, pt, ot = _call_pro(api_key, prompt, model="gemini-2.5-flash",
                                 enable_grounding=False)
        _log_usage(pt, ot)
        for line in (text or "").splitlines():
            m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            en = m.group(2).strip()
            if 0 <= idx < len(todo) and en:
                out[todo[idx]] = en
                cache[todo[idx]] = en
        _save_name(cache)
    except Exception:
        pass
    return out


_NAME_KR_CACHE = _HOME / "names_kr.json"


def _load_name_kr() -> dict:
    return _read_json_cache(_NAME_KR_CACHE)


def _save_name_kr(d: dict) -> None:
    try:
        _atomic_write_json(_NAME_KR_CACHE, d)
    except OSError as exc:
        log.warning("translate cache %s: 쓰기 실패(%s)", _NAME_KR_CACHE.name, exc)


def translate_names_kr(pairs: list, cache_only: bool = False) -> dict:
    """[(ticker, 현지명)…] → {ticker: 한글 회사명} — CN/TW/HK 종목명을 **영문 통용명
    기준 한글 음역**으로(사용자 2026-06-15 '화봉전 말고 윈본드'). 한자음독(화봉전)
    대신 한국 금융권 통용 표기(윈본드·폭스콘·미디어텍), 영문 약자가 더 통용되면 영문
    유지(TSMC·UMC·SMIC). Flash 배치·영구 캐시(names_kr.json, **티커당 1회** → 이후
    ₩0). graceful(키부재/실패 시 빠짐 → 호출부 원문 유지). 티커가 disambiguation
    힌트라 현지명이 한자음독이어도 정확.

    cache_only=True (렌더-세이프, 2026-06-16): 캐시된 번역만 반환하고 Flash 호출은
    안 함 — stock_panel 등 렌더 경로가 매 30초/시간 재호출돼도 네트워크·LLM 블로킹
    0. 미캐시 종목은 빠지고(호출부가 원문 유지), 호출부가 백그라운드로 워밍."""
    seen: set = set()
    uniq: list = []
    for tk, nm in pairs:
        tk = str(tk or "").strip()
        if tk and tk not in seen:
            seen.add(tk)
            uniq.append((tk, str(nm or "").strip()))
    if not uniq:
        return {}
    cache = _load_name_kr()
    out = {tk: cache[tk] for tk, _ in uniq if cache.get(tk)}
    fp = _prompt_fp(_NAME_PROMPT)
    miss_cache = {} if cache_only else _miss_load()
    todo = [(tk, nm) for tk, nm in uniq
            if tk not in cache and not _miss_skip(miss_cache, tk, fp)][:_MAX_BATCH]
    if cache_only or not todo:        # 렌더-세이프(캐시만) 또는 전부 캐시됨
        return out
    api_key = _effective_key()
    if not api_key:
        return out
    lines = "\n".join(f"{i + 1}. {tk} | {nm}" for i, (tk, nm) in enumerate(todo))
    prompt = _NAME_PROMPT + lines
    try:
        from bot.screener import _call_pro
        text, pt, ot = _call_pro(api_key, prompt, model="gemini-2.5-flash",
                                 enable_grounding=False)
        _log_usage(pt, ot)
        for line in (text or "").splitlines():
            m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if not (0 <= idx < len(todo)):
                continue
            tk, src = todo[idx]
            # 되읊은 `티커 | ` 접두를 벗기고(실측 `3296.TWO | 승덕`) **번역인지**
            # 판정한다 — 한자 그대로면 캐시에 넣지 않는다(#25·#43).
            kr = clean_answer(m.group(2))
            if looks_translated(src, kr):
                out[tk] = kr
                cache[tk] = kr
                miss_cache.pop(tk, None)
            else:
                _miss_record(miss_cache, tk, fp,
                             f"모델이 번역을 못 냈습니다(답={kr[:24]!r})")
        # 답이 아예 안 온 줄도 실패다 — 기록이 없으면 매 수집마다 다시 묻는다
        # (#348 예산 밖은 언젠가 채워지나).
        for tk, _src in todo:
            if tk not in out:
                _miss_record(miss_cache, tk, fp, "모델 응답에 그 줄이 없습니다")
        _save_name_kr(cache)
        _atomic_write_json(_MISS_CACHE, miss_cache)
    except Exception:
        pass
    return out


def translate_titles_kr(titles: list[str], cache_only: bool = False) -> dict:
    """[제목…] → {원문제목: 한국어}. 캐시 우선, 미캐시만 Flash 배치 번역. graceful.

    cache_only=True 면 캐시된 번역만 반환(Flash 호출 0 — 렌더-세이프 경로용,
    사용자 2026-06-16 TW 렌더 8.2s 블록 제거). 실패/키부재 시 번역 못 한 제목은
    dict 에서 빠짐(호출부가 원문 유지)."""
    uniq = [t for t in dict.fromkeys(titles) if t and t.strip()]
    if not uniq:
        return {}
    cache = _load()
    out = {t: cache[t] for t in uniq if t in cache and cache[t]}
    fp = _prompt_fp(_TITLE_PROMPT)
    miss_cache = {} if cache_only else _miss_load()
    todo = [t for t in uniq
            if t not in cache and not _miss_skip(miss_cache, t, fp)][:_MAX_BATCH]
    if cache_only or not todo:        # 렌더-세이프(캐시만) 또는 전부 캐시됨
        return out
    api_key = _effective_key()
    if not api_key:
        return out  # graceful — 원문 유지
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(todo))
    prompt = _TITLE_PROMPT + lines
    try:
        from bot.screener import _call_pro
        text, pt, ot = _call_pro(api_key, prompt, model="gemini-2.5-flash",
                                 enable_grounding=False)
        _log_usage(pt, ot)
        for line in (text or "").splitlines():
            m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if not (0 <= idx < len(todo)):
                continue
            src = todo[idx]
            kr = clean_answer(m.group(2))
            # 한자 그대로인 답을 캐시에 넣으면 그게 '한국어' 로 굳는다(#25) —
            # 이번 라운드에 사용자가 본 `百達-KY`·`三商電` 이 그 상태다.
            if looks_translated(src, kr):
                out[src] = kr
                cache[src] = kr
                miss_cache.pop(src, None)
            else:
                _miss_record(miss_cache, src, fp,
                             f"모델이 번역을 못 냈습니다(답={kr[:24]!r})")
        for t in todo:
            if t not in out:
                _miss_record(miss_cache, t, fp, "모델 응답에 그 줄이 없습니다")
        _save(cache)
        _atomic_write_json(_MISS_CACHE, miss_cache)
    except Exception:
        pass
    return out
