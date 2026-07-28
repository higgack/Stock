"""YouTube 자막(트랜스크립트) 추출 — anaisbetts/mcp-youtube 아이디어 이식
(2026-07-26, "코드는 이식 안 함" 판정 그대로 — 원 저장소는 Node/Bun MCP
서버였지만 이 파이썬 모노레포에 새 런타임 의존성을 넣을 이유가 없어, 핵심
로직(yt-dlp 로 자막 받아 VTT 타이밍/마크업 제거 → 평문)만 순수 파이썬으로
재구현. 외부 API 키 불요·₩0(yt-dlp 는 무료 CLI, 시스템에 없으면 graceful
None).

현재 어떤 파이프라인에도 배선되지 않은 **범용 유틸리티**다 — 실적콜 웹캐스트나
CEO 인터뷰 영상 같은 구체적 소스가 뉴스/펀더멘털 분석가에 필요해지면 이
모듈을 호출부에서 import 해 쓰면 된다(신규 fetch 로직 불필요, 이미 준비됨).
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.youtube_transcript")

_TIMEOUT_SEC = 60


def _strip_vtt(vtt_text: str) -> str:
    """WebVTT 자막 → 평문 텍스트(순수함수, 네트워크/yt-dlp 무관 — 테스트용).
    헤더(WEBVTT/Kind/Language)·타이밍 큐 라인(-->)·큐 번호·인라인 태그
    (<c>, <00:00:01.000> 등) 제거. 자동생성 자막 특유의 롤링 중복(연속 큐가
    직전 큐 끝부분을 그대로 반복)은 '직전 줄과 동일하면 스킵'으로 완화 —
    완벽한 dedup 은 아니지만 실사용에 충분한 근사."""
    out: list[str] = []
    last: Optional[str] = None
    for raw in vtt_text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s == "WEBVTT" or s.startswith(("Kind:", "Language:", "NOTE", "STYLE")):
            continue
        if "-->" in s:
            continue
        if re.fullmatch(r"\d+", s):
            continue
        s = re.sub(r"<[^>]+>", "", s).strip()
        if not s or s == last:
            continue
        out.append(s)
        last = s
    return " ".join(out)


def fetch_youtube_transcript(url: str, lang: str = "ko") -> Optional[str]:
    """YouTube 영상 자막(수동 우선, 없으면 자동생성)을 평문으로 반환.
    yt-dlp 시스템 미설치/자막 부재/다운로드 실패 → None(graceful, 다른 소스로
    이어가면 됨). lang 자막 없으면 영어(en)로 1회 폴백."""
    if not shutil.which("yt-dlp"):
        log.debug("youtube_transcript: yt-dlp not installed, skipping")
        return None
    for try_lang in (lang, "en") if lang != "en" else (lang,):
        text = _fetch_one_lang(url, try_lang)
        if text:
            return text
    return None


def _fetch_one_lang(url: str, lang: str) -> Optional[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        out_tmpl = str(Path(tmpdir) / "sub")
        cmd = [
            "yt-dlp", "--skip-download", "--write-auto-sub", "--write-sub",
            "--sub-lang", lang, "--sub-format", "vtt",
            "-o", out_tmpl, url,
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=_TIMEOUT_SEC, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("youtube_transcript: yt-dlp failed for %s (%s): %s", url, lang, exc)
            return None
        vtt_files = sorted(Path(tmpdir).glob("*.vtt"))
        if not vtt_files:
            return None
        try:
            vtt_text = vtt_files[0].read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            log.debug("youtube_transcript: vtt read failed: %s", exc)
            return None
        text = _strip_vtt(vtt_text)
        return text or None
