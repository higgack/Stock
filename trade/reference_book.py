"""품목 레퍼런스북 — MTI 품목 ↔ HS10 코드 ↔ 산업 ↔ 관련 상장사 통합 참조표
(사용자 2026-06-18 '관련기업까지 총 매치해서 레퍼런스북으로 보게').

데이터는 전부 기보유 — 신규 fetch·LLM·비용 0:
  • 품목(MTI)·HS10·산업 = trade.mti_map (무역협회 HSK-MTI 연계표, 1,295 품목)
  • 관련 상장사 = trade.mti_companies (수동 큐레이션 + 채널 알림 조인)

검색 가능한 단일 자체완결 HTML 페이지. dashboard_server 가 ~/.trade/ 를 정적 서빙하므로
/trade/reference.html 로 노출. dashboard.main 이 매 렌더 regenerate (데이터 정적이라
저렴) + ensure_exists 로 404 방지. 순수 조립 — build_rows/render_page 단위테스트.
"""
from __future__ import annotations

import html as _html
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("trade.reference_book")
_KST = timezone(timedelta(hours=9))
_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
PAGE = _DATA_DIR / "dashboard" / "reference.html"
# DART 보강 후보 캐시 — curation_candidates(타이머)가 계산·기록, 레퍼런스북 렌더가 읽음
# (전 품목×전 상장사 매칭은 무거우므로 렌더에서 매번 돌리지 않고 캐시 read).
REINFORCE_JSON = _DATA_DIR / "dart_reinforce_candidates.json"
# 운영자가 '아니다'고 한 보강 후보 (품목→거절 회사) 영구 거절 목록 — 다시 후보로
# 안 뜨게(사용자 2026-06-20). _build_reinforce 가 승인 시 패널−승인분을 자동 적재.
REJECTED_JSON = _DATA_DIR / "dart_reinforce_rejected.json"
# 승인 CSV 내용 해시 — 변경(=새 승인 배치) 감지해 그 시점 패널−승인분을 1회 거절 적재.
REINFORCE_APPROVED_HASH = _DATA_DIR / ".reinforce_approved_hash"


def load_rejected(path: Path | None = None) -> dict:
    """{품목명: [거절 회사…]} 거절 목록 로드. 부재/실패 → {} (graceful)."""
    import json
    try:
        d = json.loads((path or REJECTED_JSON).read_text(encoding="utf-8"))
        return {k: list(v) for k, v in d.items() if v}
    except Exception:
        return {}


def save_rejected(data: dict, path: Path | None = None) -> None:
    """거절 목록 저장 (best-effort)."""
    import json
    p = path or REJECTED_JSON
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def save_reinforce(data: dict, path: Path | None = None) -> None:
    """{품목명: [추가 후보 회사…]} → JSON. curation_candidates 가 호출. best-effort."""
    import json
    p = path or REINFORCE_JSON
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def load_reinforce(path: Path | None = None) -> list[tuple[str, list[str]]]:
    """캐시 JSON → [(품목명, [추가 후보 회사…])] (후보수 내림차순). 부재/실패 → []."""
    import json
    p = path or REINFORCE_JSON
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = [(k, list(v)) for k, v in d.items() if v]
    items.sort(key=lambda kv: (-len(kv[1]), kv[0]))
    return items


def reinforce_telegram(reinforce: dict, top: int = 12) -> str | None:
    """보강 후보 → 텔레그램 HTML 요약(후보수 상위 top 품목). 0이면 None(무음).
    전체는 레퍼런스북 '🧬 DART 보강 후보' 패널. 18일 dart-revenue 갱신이 호출. 순수."""
    if not reinforce:
        return None
    items = sorted(reinforce.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    total = sum(len(v) for v in reinforce.values())
    lines = [f"🧬 <b>DART 보강 후보 {total}건</b> ({len(reinforce)}품목, 상위 "
             f"{min(top, len(items))})",
             "이미 매핑된 품목에도 DART 매출구성상 <b>추가로 붙일 수 있는</b> 상장사. "
             "검토 후 승인 추가. 전체는 레퍼런스북 🧬 패널.", ""]
    for name, cos in items[:top]:
        more = f" 외 {len(cos) - 6}" if len(cos) > 6 else ""
        lines.append(f"• <b>{_html.escape(name)}</b> — "
                     f"{_html.escape(', '.join(cos[:6]))}{more}")
    return "\n".join(lines)


def build_rows() -> list[dict]:
    """전 품목 [{mti6, name, industry, hs:[HS10...], companies:[상장사...]}]
    (산업·품목명 정렬). 연계표 부재 시 [] (graceful). 순수에 가까움(파일·store.db 읽기)."""
    from trade import mti_companies, mti_map
    try:
        names = mti_map.mti_names()            # {mti6: (품목명, 산업)}
        hsk = mti_map.load_mti()               # {HSK10: (mti6, 산업, 품목명)}
    except Exception:
        return []
    hs_by_mti: dict[str, list[str]] = {}
    for hs, rec in hsk.items():
        m6 = rec[0] if rec else ""
        if m6:
            hs_by_mti.setdefault(m6, []).append(str(hs))
    try:
        pairs = mti_companies.load_channel_pairs()
    except Exception:
        pairs = []
    # 큐레이션 테마 회사·이름을 HS Code → MTI6 로 해석해 **기존 MTI 품목 행에 병합**
    # (사용자 2026-06-19 '별도 집계 말고 기존 것에 붙여'). 별도 테마 행 없음 —
    # 테마 회사는 그 HS 의 관세청 수출입 품목(MTI)에 합류, 테마명은 검색 인덱스로.
    theme_co: dict[str, list[str]] = {}
    theme_kw: dict[str, list[str]] = {}
    try:
        for tr in mti_companies.theme_rows():
            # 핀(_THEME_MTI_PIN) 우선 → catch-all HS6 과잉부착 회피(사용자 2026-06-19).
            for m6 in mti_companies.theme_mti6(tr["name"], tr.get("hs", [])):
                theme_co.setdefault(m6, []).extend(tr["companies"])
                theme_kw.setdefault(m6, []).append(tr["name"])
    except Exception as exc:
        # silent-pass 금지(사용자 2026-06-19 'CNT/평판 왜 안없어져' — 테마 병합이
        # VM 에서 죽으면 테마 회사가 surfaced 안 돼 영구 미매칭). 원인을 로그로 노출.
        log.warning("build_rows 테마 병합 실패(테마 회사 미surfaced): %s", exc)
    rows: list[dict] = []
    for mti6, meta in sorted(names.items(), key=lambda kv: (kv[1][1], kv[1][0])):
        name, industry = meta
        try:
            cos = mti_companies.dedup_companies(
                list(mti_companies.companies_for(name))
                + mti_companies.channel_companies_for(name, pairs)
                + theme_co.get(mti6, [])             # 테마 회사 병합(HS→MTI)
                + mti_companies.reinforce_approved_for(name))  # 운영자 승인 DART 보강
        except Exception:
            cos = []
        row = {"mti6": mti6, "name": name, "industry": industry,
               "hs": sorted(hs_by_mti.get(mti6, [])), "companies": cos}
        if theme_kw.get(mti6):
            row["theme_kw"] = theme_kw[mti6]          # 검색 인덱스(SiC·피부과 등)
        rows.append(row)
    return rows


def unmatched_candidates(rows: list[dict], db_path=None,
                         limit: int = 80) -> list[tuple[str, list[str], int]]:
    """레퍼런스북 어디에도 안 나타난 알림 회사 = '새 언어격차' 자동 발굴
    (사용자 2026-06-19 '새 격차 생겼는지 어떻게 알아?'). 전체 행의 관련상장사
    합집합(static ∪ channel ∪ 별칭)을 기준으로, 알림(store.db)의 종목 중
    **어느 행에도 안 붙은 회사**를 가진 item 을 빈도순으로 반환
    [(item 원문, [누락 회사…≤8], 빈도)]. 회사가 이미 노출된 item(라면 등
    static 커버)은 제외 → 노이즈 최소. 품목어 누수(_NON_COMPANY)는 회사로
    안 셈. 파일 읽기 외 순수 — graceful → []."""
    from trade import mti_companies
    surfaced = {c.replace(" ", "").lower()
                for r in rows for c in (r.get("companies") or [])}
    # + reinforce 승인 전체(품목키 무관, 사용자 2026-07-15 반영 버튼 fix) —
    # 반영 버튼이 적재하는 (원문 캡션, 회사) 쌍은 캡션이 카탈로그 canonical
    # MTI 품목명과 정확히 같을 일이 거의 없어(예: 'ECAC/FGC 압축기' ≠ '압축기')
    # reinforce_approved_for(canonical_name) 경유로는 어떤 행에도 안 붙는다
    # → surfaced 를 rows 만으로 구하면 반영해도 계속 미매칭으로 재등장(독립
    # 리뷰 2026-07-15 — 버튼이 '등재됨'을 표시하지만 실제로 안 사라지던
    # 크리티컬 버그). 반영된 회사는 "이미 검토·승인됨"이 핵심이라, 품목키
    # 정확일치와 무관하게 전역으로 알려진 회사 취급.
    try:
        for cos in mti_companies.load_reinforce_approved().values():
            surfaced.update(c.replace(" ", "").lower() for c in cos)
    except Exception:
        pass
    try:
        alerts = mti_companies._load_alerts(db_path)
    except Exception:
        return []
    # 알림 원문 회사명에 **오타 교정(canon_company) 적용 후** surfaced 와 대조 —
    # surfaced 는 dedup_companies 로 이미 canon 된 표기라, 원문 타이포(에스테아이·
    # SK바이오센서·메티바이오메드)는 canon 없이는 영원히 '미매칭' 으로 남았다
    # (사용자 2026-06-19 '업데이트했는데 숫자가 안 준다'). 비-타이포는 canon 이
    # 원본 그대로라 무영향.
    canon = mti_companies.canon_company
    # 회사 뷰(_company_alert_items)와 동일하게 split_names 로 **공백 결합 토큰 분리** —
    # _clean_stocks 는 쉼표만 쪼개 '나노신소재 제이오'(BeOn 1-space 결합)가 한 토큰으로
    # 남아 canon('나노신소재제이오')이 surfaced 와 영구 불일치 → CNT 등 미매칭 잔존
    # (사용자 2026-06-20 '미매칭 왜 계속 안없어져'). split_names 가 'JYP Ent.'(마침표)
    # 류는 통째 보존. graceful.
    try:
        from trade import price_provider
        _split = price_provider.split_names
    except Exception:
        _split = lambda xs: list(xs or [])  # noqa: E731
    agg: dict[str, list] = {}
    for item, stocks in alerts:
        try:
            stocks = _split(stocks) or stocks
        except Exception:
            pass
        missing = [s for s in stocks
                   if canon(s).replace(" ", "").lower() not in surfaced
                   and s.replace(" ", "").lower() not in mti_companies._NON_COMPANY]
        if not missing:
            continue
        norm = item.replace(" ", "").lower()
        rec = agg.get(norm)
        if rec is None:
            agg[norm] = [item.strip(), dict.fromkeys(missing), 1]
        else:
            rec[1].update(dict.fromkeys(missing))
            rec[2] += 1
    ordered = sorted(agg.values(), key=lambda r: (-r[2], r[0]))
    return [(disp, list(cos)[:8], n) for disp, cos, n in ordered][:limit]


# 테마 = 대시보드와 동일 시간기반(KST 19-07 = body.dark). 라이트 기본 + body.dark 오버라이드
# (사용자 2026-06-18 'light/black 시간에 맞게 안 변해' — 기존 prefers-color-scheme OS기반 폐기).
_CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;
 background:#fff;color:#1f2328;margin:0;padding:0;line-height:1.5}
.wrap{max-width:1280px;margin:0 auto;padding:16px}
.nav{font-size:13px;margin-bottom:10px}.nav a{color:#0969da;text-decoration:none;margin-right:10px}
h1{font-size:20px;margin:6px 0 2px}.sub{font-size:12px;color:#656d76;margin:0 0 12px}
.bar{position:sticky;top:0;background:#fff;padding:8px 0;z-index:5;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
#q{flex:1;min-width:220px;padding:9px 12px;border:1px solid #d0d7de;background:#f6f8fa;color:#1f2328;border-radius:8px;font-size:14px}
.chip{padding:5px 10px;border:1px solid #d0d7de;background:#f6f8fa;color:#1f2328;border-radius:14px;font-size:12px;cursor:pointer}
.chip.on{background:#2f81f7;border-color:#2f81f7;color:#fff}
.cnt{font-size:12px;color:#656d76;margin:6px 2px}
table{border-collapse:collapse;width:100%;font-size:13px}
thead th{position:sticky;top:52px;background:#f6f8fa;color:#656d76;text-align:left;padding:8px;border-bottom:1px solid #d0d7de;z-index:4}
td{padding:7px 8px;border-bottom:1px solid #eaeef2;vertical-align:top}
tr:hover td{background:#f6f8fa}
.nm{font-weight:600}.mti{color:#656d76;font-variant-numeric:tabular-nums;font-size:12px}
.theme-badge{display:inline-block;background:#fff1c2;color:#9a6700;border:1px solid #eaca7a;border-radius:10px;padding:0 7px;font-size:11px;font-weight:600}
body.dark .theme-badge{background:#2a2410;color:#e3b341;border-color:#4a3f17}
.ind{color:#656d76;white-space:nowrap}
.hs{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:#656d76;word-break:break-all}
.co{color:#1f2328}.co .x{display:inline-block;background:#eef1f4;border:1px solid #cdd4dc;border-radius:10px;padding:1px 8px;margin:1px;font-size:12px;color:#1f2328}
.co .none{color:#8b949e}
.dl{padding:8px 12px;border:1px solid #1f883d;background:#238636;color:#fff;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
body.dark{background:#0d1117;color:#e6edf3}
body.dark .nav a{color:#58a6ff}
body.dark .bar{background:#0d1117}
body.dark #q,body.dark .chip{background:#161b22;border-color:#2a2e37;color:#e6edf3}
body.dark .sub,body.dark .cnt,body.dark .mti,body.dark .ind,body.dark .hs,body.dark .co .none{color:#9aa0aa}
body.dark thead th{background:#161b22;color:#9aa0aa;border-color:#2a2e37}
body.dark td{border-color:#1c222b}body.dark tr:hover td{background:#11161d}
body.dark .co{color:#e6edf3}body.dark .co .x{background:#21262d;border-color:#3a414b;color:#e6edf3}
.um{margin:4px 0 12px;border:1px solid #d0d7de;border-radius:8px;background:#fff8e6;padding:0}
.um>summary{cursor:pointer;padding:9px 12px;font-size:13px;font-weight:600;color:#9a6700}
.umnote{font-size:12px;color:#656d76;margin:0 12px 8px}
.umt{font-size:12.5px}.umt thead th{position:static;background:transparent;border-bottom:1px solid #eaca7a}
.umi{color:#1f2328}.umc{color:#1f2328}.umn{text-align:right;color:#656d76;font-variant-numeric:tabular-nums}
body.dark .um{background:#1c1808;border-color:#3a3417}body.dark .um>summary{color:#e3b341}
body.dark .umt thead th{border-color:#4a3f17}body.dark .umi,body.dark .umc{color:#e6edf3}
.umchip{display:inline-flex;align-items:center;gap:6px;background:#eef1f4;border:1px solid #cdd4dc;
 border-radius:10px;padding:1px 4px 1px 8px;margin:1px;font-size:12px;color:#1f2328}
body.dark .umchip{background:#21262d;border-color:#3a414b;color:#e6edf3}
.umbtn{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:2px 8px;font-size:11px;cursor:pointer}
.umbtn:disabled{opacity:.5;cursor:default}
.rf{background:#eef4ff;border-color:#bcd2f7}.rf>summary{color:#1f5fbf}
.rfbar{display:flex;gap:8px;align-items:center;margin:0 12px 8px}
.rfbar #rfq{flex:1;min-width:160px;padding:6px 10px;border:1px solid #bcd2f7;background:#fff;color:#1f2328;border-radius:8px;font-size:13px}
body.dark .rfbar #rfq{background:#0d1117;border-color:#1f3354;color:#e6edf3}
.rf .umt thead th{border-bottom-color:#bcd2f7}
body.dark .rf{background:#0e1726;border-color:#1f3354}body.dark .rf>summary{color:#6cb6ff}
body.dark .rf .umt thead th{border-color:#1f3354}
"""

_JS = """
(function(){
 var q=document.getElementById('q'),rows=[].slice.call(document.querySelectorAll('#tbl tbody tr')),
  cnt=document.getElementById('cnt'),ind='';
 function apply(){
  var t=(q.value||'').trim().toLowerCase().split(/\\s+/).filter(Boolean),n=0;
  rows.forEach(function(r){
   var s=r.getAttribute('data-s');
   var ok=(!ind||r.getAttribute('data-i')===ind)&&t.every(function(w){return s.indexOf(w)>=0;});
   r.style.display=ok?'':'none';if(ok)n++;});
  cnt.textContent=n+' / '+rows.length+' 품목';}
 q.addEventListener('input',apply);
 [].slice.call(document.querySelectorAll('.chip')).forEach(function(c){
  c.addEventListener('click',function(){
   if(c.classList.contains('on')){c.classList.remove('on');ind='';}
   else{document.querySelectorAll('.chip.on').forEach(function(o){o.classList.remove('on');});
    c.classList.add('on');ind=c.getAttribute('data-i');}
   apply();});});
 // 📥 CSV — 현재 보이는(검색·산업 필터 반영) 품목을 CSV 로 (사용자 2026-06-18).
 function cell(s){return '"'+String(s==null?'':s).replace(/"/g,'""')+'"';}
 document.getElementById('csv').addEventListener('click',function(){
  var out=['품목,MTI,산업,구성HS10,관련상장사'];
  rows.forEach(function(r){
   if(r.style.display==='none')return;
   var td=r.querySelectorAll('td');
   var nm=(td[0].querySelector('.nm')||{}).textContent||'';
   var mti=(td[0].querySelector('.mti')||{}).textContent||'';
   var ind2=td[1].textContent||'';
   var hs=td[2].textContent||'';
   var co=[].map.call(td[3].querySelectorAll('.x'),function(x){return x.textContent;}).join('; ');
   out.push([nm,mti,ind2,hs,co].map(cell).join(','));});
  var blob=new Blob(['\\ufeff'+out.join('\\n')],{type:'text/csv;charset=utf-8'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='품목_레퍼런스북.csv';document.body.appendChild(a);a.click();a.remove();});
 // 🧬 보강 후보 패널(rf) — 전수 검토(검색 필터 + CSV 내려받기, 사용자 2026-06-19 '전체 확인').
 var rfq=document.getElementById('rfq'), rftbl=document.getElementById('rftbl');
 var rfrows=rftbl?[].slice.call(document.querySelectorAll('#rftbl tbody tr')):[];
 if(rfq&&rftbl){
  rfq.addEventListener('input',function(){
   var t=(rfq.value||'').toLowerCase().split(/\\s+/).filter(Boolean);
   rfrows.forEach(function(r){var s=r.getAttribute('data-s')||'';
    r.style.display=t.every(function(w){return s.indexOf(w)>=0;})?'':'none';});});
 }
 var rfcsv=document.getElementById('rfcsv');
 if(rfcsv&&rftbl){rfcsv.addEventListener('click',function(){
  var out=['품목,DART추가후보상장사,수'];
  rfrows.forEach(function(r){
   if(r.style.display==='none')return;
   var td=r.querySelectorAll('td');
   out.push([td[0].textContent,td[1].textContent,td[2].textContent].map(cell).join(','));});
  var blob=new Blob(['\\ufeff'+out.join('\\n')],{type:'text/csv;charset=utf-8'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='DART_보강후보.csv';document.body.appendChild(a);a.click();a.remove();});}
 // 테마 = 대시보드와 동일 시간기반(KST 19시~07시 다크). prefers-color-scheme(OS) 아님.
 function applyDark(){var h=(new Date().getUTCHours()+9)%24;
  document.body.classList.toggle('dark',h>=19||h<7);}
 applyDark();setInterval(applyDark,60000);
 apply();
})();
"""


def _render_unmatched(unmatched: list[tuple[str, list[str], int]] | None) -> str:
    """미매칭 후보 패널 — 회사는 있으나 어떤 품목·별칭에도 안 붙은 알림
    (빈도순, 접이식). 새 언어격차(별칭 추가 대상) 자동 노출. 회사 칩마다
    '반영' 버튼(사용자 2026-07-15 '블로그대쉬보드처럼 버튼으로 추가') —
    블로그 페이지 kg_approve 버튼과 동일 백엔드(kg_candidates.approve_
    candidates, regenerate() 가 미리 sync_unmatched_to_queue 로 큐에 적재)
    재사용, GET 라우트만 신설(NOAH 프록시가 POST 미포워드 — dashboard_server
    기존 report_archive_delete 와 동일 GET 컨벤션). 클릭 시 즉시 런타임
    반영(커밋 불요) + 행 흐리게. 비면 ''."""
    if not unmatched:
        return ""
    e = _html.escape

    def _q(s: str) -> str:
        import urllib.parse
        return urllib.parse.quote(s, safe="")

    body = []
    for item, cos, n in unmatched:
        tgt_q = _q(item)
        chips = "".join(
            f'<span class="umchip">{e(co)}'
            f'<button class="umbtn" data-co="{_q(co)}" data-tgt="{tgt_q}">'
            "반영</button></span>"
            for co in cos)
        body.append(
            f'<tr><td class="umi">{e(item)}</td>'
            f'<td class="umc">{chips}</td>'
            f'<td class="umn">{n}</td></tr>')
    script = """
<script>
(function(){
 document.querySelectorAll('.um .umbtn').forEach(function(b){
  b.addEventListener('click', function(){
   b.disabled = true; b.textContent = '반영중…';
   fetch('api/kg_approve?co=' + b.dataset.co + '&rel=' +
    encodeURIComponent('취급품목') + '&tgt=' + b.dataset.tgt,
    {cache: 'no-store', credentials: 'include'})
    .then(function(r){ return r.json(); })
    .then(function(j){
     if(j && j.ok){
      b.textContent = j.ingested ? '등재됨' : '처리됨';
      var row = b.closest('tr'); if(row) row.style.opacity = '.4';
     } else {
      b.disabled = false; b.textContent = '반영';
      alert('실패: ' + ((j && j.error) || '?'));
     }
    }).catch(function(err){
     b.disabled = false; b.textContent = '반영'; alert('오류: ' + err);
    });
  });
 });
})();
</script>"""
    return (
        f"<details class='um'><summary>🔍 미매칭 알림 후보 "
        f"<b>{len(unmatched)}</b>건 — 별칭 추가 대상</summary>"
        "<p class='umnote'>회사는 있으나 어떤 품목·별칭에도 안 붙은 알림"
        "(빈도순). 회사명 옆 <b>반영</b> 버튼으로 즉시 등재(커밋 불요, "
        "런타임 반영) — 오탐이면 그냥 두세요, 다음에도 재확인할 수 있게 "
        "남아있습니다. (자동 갱신 · 일일 결산에 신규분 통지)</p>"
        "<table class='umt'><thead><tr><th>알림 품목 (원문)</th>"
        "<th>회사</th><th>빈도</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></details>"
        f"{script}")


def _render_reinforce(reinforce: list[tuple[str, list[str]]] | None) -> str:
    """DART 매출구성 보강 후보 패널 — 각 품목에 '현재 큐레이션엔 없지만 DART 상
    그 제품을 만드는' 상장사 후보(후보수순, 접이식). 더 많은 상장사 발굴(운영자
    2026-06-19). 승인 전 후보(자동 큐레이션 아님). 비면 ''. 순수."""
    if not reinforce:
        return ""
    e = _html.escape
    total = sum(len(c) for _, c in reinforce)
    body = []
    for item, cos in reinforce:
        joined = ", ".join(cos)
        s = e((item + " " + joined).lower())          # 검색 인덱스(품목+회사)
        body.append(
            f'<tr data-s="{s}"><td class="umi">{e(item)}</td>'
            f'<td class="umc">{e(joined)}</td>'
            f'<td class="umn">{len(cos)}</td></tr>')
    return (
        f"<details class='um rf'><summary>🧬 DART 보강 후보 "
        f"<b>{total}</b>건 ({len(reinforce)}품목) — 추가 상장사 후보 (전체)</summary>"
        "<p class='umnote'>각 품목에 <b>현재 큐레이션엔 없지만 DART 매출구성상 그 "
        "제품을 만드는</b> 상장사 후보 — <b>전수</b>. 검토 후 승인 추가. "
        "(보수적 규칙 매칭 · 자동 큐레이션 아님 · 부수 세그먼트 포함될 수 있음)</p>"
        "<div class='rfbar'><input id='rfq' type='search' placeholder='품목·회사 검색' "
        "autocomplete='off'><button id='rfcsv' class='dl' type='button' "
        "title='전체 보강 후보 CSV 내려받기'>📥 CSV</button></div>"
        "<table class='umt' id='rftbl'><thead><tr><th>품목</th>"
        "<th>DART 추가 후보 상장사</th><th>수</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></details>")


def render_page(rows: list[dict], *, now: datetime | None = None,
                unmatched: list[tuple[str, list[str], int]] | None = None,
                reinforce: list[tuple[str, list[str]]] | None = None) -> str:
    """검색 가능한 자체완결 HTML. 순수."""
    from trade.archive_template import SCROLL_RESTORE_JS  # 뒤로가기 스크롤 복원(공용)
    from trade import mti_companies as _mc                # 검색 동의어(PCB→인쇄회로)
    e = _html.escape
    now = now or datetime.now(_KST)
    inds = sorted({r["industry"] for r in rows if r.get("industry")})
    chips = "".join(f'<span class="chip" data-i="{e(i)}">{e(i)}</span>' for i in inds)
    body = []
    for r in rows:
        hs = ", ".join(r.get("hs") or [])
        cos = r.get("companies") or []
        co_html = ("".join(f'<span class="x">{e(c)}</span>' for c in cos)
                   if cos else '<span class="none">—</span>')
        parts = [r["name"], r["mti6"], r["industry"], hs, " ".join(cos)]
        syn = " ".join(_mc.search_synonyms(r["name"]))
        if syn:                                  # 동의어(PCB→인쇄회로) 검색 인덱스
            parts.append(syn)
        if r.get("theme_kw"):                    # 병합된 테마명(SiC·피부과 등) 검색 인덱스
            parts.append(" ".join(r["theme_kw"]))
        search = " ".join(parts).lower()
        code_html = ('<span class="theme-badge">🏷️ 테마</span>' if r.get("theme")
                     else f'<span class="mti">{e(r["mti6"])}</span>')
        body.append(
            f'<tr data-s="{e(search)}" data-i="{e(r["industry"])}">'
            f'<td><span class="nm">{e(r["name"])}</span> '
            f'{code_html}</td>'
            f'<td class="ind">{e(r["industry"])}</td>'
            f'<td class="hs">{e(hs) or "—"}</td>'
            f'<td class="co">{co_html}</td></tr>')
    n_theme = sum(1 for r in rows if r.get("theme"))
    n_mti = len(rows) - n_theme
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>📖 품목 레퍼런스북</title>"
        f"<style>{_CSS}</style></head><body><div class='wrap'>"
        "<div class='nav'><a href='./'>← 대시보드</a></div>"
        "<h1>📖 품목 레퍼런스북</h1>"
        f"<p class='sub'>MTI 품목 ↔ 구성 HS10 코드 ↔ 산업 ↔ 관련 상장사 · "
        f"무역협회 HSK-MTI 연계표 + 큐레이션·채널 관련기업 · 총 {n_mti:,}품목"
        f"{f' + 🏷️ {n_theme} 테마' if n_theme else ''} · "
        f"기준 {now.strftime('%Y-%m-%d %H:%M')} KST</p>"
        "<div class='bar'><input id='q' type='search' "
        "placeholder='검색: 품목명 / HS코드 / 산업 / 기업명' autocomplete='off'>"
        "<button id='csv' class='dl' type='button' "
        "title='현재 보이는 품목을 CSV로 내려받기'>📥 CSV</button>"
        f"{chips}</div>"
        "<p id='cnt' class='cnt'></p>"
        f"{_render_unmatched(unmatched)}"
        f"{_render_reinforce(reinforce)}"
        "<table id='tbl'><thead><tr><th>품목 (MTI)</th><th>산업</th>"
        "<th>구성 HS10 코드</th><th>관련 상장사</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
        f"<script>{_JS}</script></div>{SCROLL_RESTORE_JS}</body></html>")


def regenerate(out_path: Path | None = None) -> Path:
    """레퍼런스북 HTML 생성. 데이터 없으면 빈 안내 페이지."""
    rows = build_rows()
    try:
        unmatched = unmatched_candidates(rows)
    except Exception:
        unmatched = []
    try:
        # 미매칭 후보 → kg_candidates 큐 적재(반영 버튼이 승인할 대상 생성,
        # 사용자 2026-07-15). regen 마다 호출하지만 write_candidates_csv 가
        # 중복·근사중복 스킵이라 저렴(신규분만 append).
        from trade import kg_candidates
        kg_candidates.sync_unmatched_to_queue(unmatched)
    except Exception as exc:
        log.warning("reference_book: unmatched→queue sync failed: %s", exc)
    try:
        reinforce = load_reinforce()          # curation 타이머가 적재한 캐시(없으면 [])
    except Exception:
        reinforce = []
    html = render_page(rows, unmatched=unmatched, reinforce=reinforce)
    out = out_path or PAGE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def ensure_exists() -> None:
    """대시보드 링크 404 방지 — 없으면 생성. best-effort."""
    try:
        if not PAGE.exists():
            regenerate()
    except Exception:
        pass
