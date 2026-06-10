"""대시보드 → 봇 작업 요청 스풀 (분석 / 스크리너 실행 버튼).

dashboard_server(별도 프로세스)가 ``submit()`` 으로 JSON 파일을 떨어뜨리면
telegram_bot 의 ``_periodic_dashboard_requests`` 폴러(5초)가 ``take_all()``
로 집어 **텔레그램 채널 명령과 동일 경로**로 실행한다 — 결과는 채널 +
대시보드 아카이브에 똑같이 게시 (분석 파이프라인 코드 중복 0).

파일 기반인 이유: 두 프로세스(systemd stock-bot / stock-bot-dashboard)
사이의 가장 단순하고 재시작-안전한 큐. 봇이 죽어 있어도 요청이 유실되지
않고, 너무 오래(30분+) 묵은 요청은 stale 폐기해 "몇 시간 뒤 갑자기 분석이
돌며 비용 발생" 서프라이즈를 차단한다.

kinds:
  • analyze  — query = 티커 (서버가 종목명→티커 resolve 후 spool)
  • screener — query = Bottleneck 도메인 slug/별칭/자유어 ('' = bottleneck)
  • screen   — query = 조건부 스크리너 조건 ('PER<15 PBR<1' · 'us PER<15' · 'valueup')
  • command  — query = '/명령어 [인자]' (텔레그램 명령을 대시보드에서 실행).
    봇이 캡처 shim 으로 핸들러를 돌려 텍스트 답변을 ``write_result`` 로
    기록 → dashboard_server 의 api/command_result 가 브라우저에 폴링 응답.

결과 스풀(dashboard_results/<id>.json)은 봇(write_result)→서버(read_result)
역방향 채널. command 요청은 submit 가 돌려준 id 로 결과를 매칭한다.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

SPOOL_DIR = Path.home() / ".tradingagents" / "dashboard_requests"
RESULTS_DIR = Path.home() / ".tradingagents" / "dashboard_results"

KINDS = ("analyze", "screener", "screen", "command")
_STALE_SEC = 1800   # 30분 — 봇이 그보다 오래 다운이었다면 폐기 (비용 서프라이즈 방지)
_RESULT_STALE_SEC = 3600   # 1시간 — 묵은 명령 결과 파일 청소
_MAX_PENDING = 8    # 폭주 가드 — 대기 초과 시 submit 거부
_MAX_QUERY_LEN = 200   # command 인자(/watch 다중 조건 등)까지 수용
_SAFE_ID_RE = __import__("re").compile(r"^[a-f0-9]{8,40}$")


def submit(kind: str, query: str) -> dict:
    """요청 스풀에 추가. dashboard_server 가 호출 (browser POST /api/run).

    Returns {ok: bool, note?/error?: str}. 동일 kind+query 가 이미 대기 중이면
    중복 spool 하지 않고 ok=True(dup) — 더블클릭/재시도 비용 중복 차단."""
    kind = (kind or "").strip().lower()
    query = " ".join((query or "").split())[:_MAX_QUERY_LEN]
    if kind not in KINDS:
        return {"ok": False, "error": f"unknown kind: {kind}"}
    if kind in ("analyze", "screen", "command") and not query:
        return {"ok": False, "error": "빈 요청"}
    if kind == "command" and not query.startswith("/"):
        return {"ok": False, "error": "명령은 '/' 로 시작해야 합니다"}
    try:
        SPOOL_DIR.mkdir(parents=True, exist_ok=True)
        pending = list_pending()
        if len(pending) >= _MAX_PENDING:
            return {"ok": False,
                    "error": "대기 중인 요청이 너무 많습니다 — 잠시 후 다시 시도"}
        for p in pending:
            if p.get("kind") == kind and p.get("query") == query:
                return {"ok": True, "dup": True, "id": p.get("id", ""),
                        "note": "이미 같은 요청이 대기 중입니다"}
        req_id = uuid.uuid4().hex
        req = {"kind": kind, "query": query, "ts": time.time(), "id": req_id}
        name = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.json"
        tmp = SPOOL_DIR / (name + ".tmp")
        tmp.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, SPOOL_DIR / name)
        return {"ok": True, "id": req_id}
    except Exception as exc:  # 디스크 오류 등 — 호출자에 깔끔히 보고
        log.warning("dashboard_requests.submit failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def write_result(req_id: str, payload: dict) -> None:
    """봇이 command 실행 결과를 기록 (역방향 채널). dashboard_server 가
    api/command_result 로 읽어 브라우저에 폴링 응답. 묵은 결과는 청소."""
    if not (req_id and _SAFE_ID_RE.match(req_id)):
        return
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        _cleanup_results()
        body = dict(payload or {})
        body.setdefault("ts", time.time())
        tmp = RESULTS_DIR / (req_id + ".json.tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, RESULTS_DIR / (req_id + ".json"))
    except Exception as exc:
        log.warning("dashboard_requests.write_result failed: %s", exc)


def read_result(req_id: str) -> dict | None:
    """dashboard_server 가 호출 — 결과 있으면 dict, 아직이면 None. id 검증."""
    if not (req_id and _SAFE_ID_RE.match(req_id)):
        return None
    f = RESULTS_DIR / (req_id + ".json")
    try:
        if not f.exists():
            return None
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cleanup_results() -> None:
    """1시간 지난 결과 파일 삭제 (디스크 누적 방지)."""
    now = time.time()
    try:
        for f in RESULTS_DIR.glob("*.json"):
            try:
                if now - f.stat().st_mtime > _RESULT_STALE_SEC:
                    f.unlink()
            except Exception:
                continue
    except Exception:
        pass


def list_pending() -> list[dict]:
    """대기 중 요청 목록 (claim 하지 않음 — dedupe/조회용)."""
    out: list[dict] = []
    try:
        for f in sorted(SPOOL_DIR.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
    except Exception:
        pass
    return out


def take_all() -> list[dict]:
    """대기 요청 전부 claim (read 후 unlink). 봇 폴러 전용.

    파싱 불가 파일은 그대로 unlink(영구 루프 방지), stale(>30분) 요청은
    warning 로그 후 폐기. 단일 consumer(systemd 봇 1개) 전제."""
    taken: list[dict] = []
    try:
        files = sorted(SPOOL_DIR.glob("*.json"))
    except Exception:
        return taken
    now = time.time()
    for f in files:
        try:
            req = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            req = None
        try:
            f.unlink()
        except Exception:
            continue  # unlink 실패 → 다음 사이클에 재시도 (중복 실행보다 안전)
        if not isinstance(req, dict):
            continue
        if now - float(req.get("ts") or 0) > _STALE_SEC:
            log.warning("dashboard request stale-dropped: %s", req)
            continue
        if req.get("kind") in KINDS:
            taken.append(req)
    return taken
