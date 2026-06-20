"""Finviz식 수출입 히트맵 (트리맵) — 산업트렌드 옆 탭 (사용자 2026-06-12).

데이터 = customs_scan 의 전 chapter 스윕이 저장한 leaf 스냅샷
(customs_heatmap_leaf — 기준월/전월/작년동월 수출·수입, 추가 API 콜 0).
박스 크기 = 금액, 색 = YoY/MoM 등락 (red-green). 계층 = 2자리 chapter
섹션 → HS4 박스 (leaf 합산). 월간 데이터라 갱신 주기 = 익월 1일 잠정
적재 → ~15일 확정 정제 (10일 단위 잠정엔 HS 데이터가 없어 불가 —
사용자 검토 2026-06-12 확인).

렌더 = 순수 HTML/CSS/JS (외부 라이브러리 0): squarified treemap 을
클라이언트에서 계산, 토글(수출/수입 × YoY/MoM), 헤더 ▲▼ 카운트 바,
hover 툴팁. 비용 ₩0 (LLM 0 · 스윕 재사용).
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

# HS 2자리 chapter 한글명 (관세청 표준 류 명칭 축약 — 라벨용).
CHAPTER_KR: dict[str, str] = {
    "01": "산동물", "02": "육류", "03": "어패류", "04": "낙농품",
    "05": "기타 동물성", "06": "산식물", "07": "채소", "08": "과실·견과",
    "09": "커피·차·향신료", "10": "곡물", "11": "제분", "12": "유지종자",
    "13": "식물성 엑스", "14": "기타 식물성", "15": "동식물성 유지",
    "16": "육·어류 조제", "17": "당류", "18": "코코아", "19": "곡물 조제",
    "20": "채소·과실 조제", "21": "기타 조제식료", "22": "음료·주류",
    "23": "사료", "24": "담배", "25": "토석·소금", "26": "광·슬랙",
    "27": "연료/석유", "28": "무기화학", "29": "유기화학", "30": "의약품",
    "31": "비료", "32": "염료·안료", "33": "향료·화장품", "34": "비누·세제",
    "35": "단백질계", "36": "화약", "37": "사진용 재료", "38": "화학공업 기타",
    "39": "플라스틱", "40": "고무", "41": "원피·가죽", "42": "가죽제품",
    "43": "모피", "44": "목재", "45": "코르크", "46": "조물재료",
    "47": "펄프", "48": "종이", "49": "인쇄물", "50": "견",
    "51": "양모", "52": "면", "53": "기타 식물섬유", "54": "인조필라멘트",
    "55": "인조스테이플", "56": "부직포", "57": "양탄자", "58": "특수직물",
    "59": "도포직물", "60": "편물", "61": "의류(편물)", "62": "의류(직물)",
    "63": "기타 섬유제품", "64": "신발", "65": "모자", "66": "우산",
    "67": "조제 우모", "68": "석·시멘트제품", "69": "도자제품", "70": "유리",
    "71": "귀금속/보석", "72": "철강", "73": "철강제품", "74": "구리",
    "75": "니켈", "76": "알루미늄", "78": "납", "79": "아연", "80": "주석",
    "81": "기타 비금속", "82": "공구", "83": "비금속제품", "84": "기계",
    "85": "전기전자", "86": "철도차량", "87": "자동차", "88": "항공기",
    "89": "선박", "90": "정밀기기", "91": "시계", "92": "악기",
    "93": "무기", "94": "가구·조명", "95": "완구·운동구", "96": "잡품",
    "97": "예술품",
}


def build_heatmap_data(rows: list[dict]) -> dict:
    """leaf 스냅샷 → 클라이언트 JSON: chapter → HS4 집계 트리.

    HS4 노드: 값(leaf 합산), 등락(합산 기준 YoY/MoM), 라벨(최대 leaf 명).
    수출·수입 양 방향 모두 포함 — 토글은 클라이언트에서."""
    if not rows:
        return {}
    ref_ym = max(r.get("ref_ym") or "" for r in rows)
    # 산업 그룹 (사용자 2026-06-13 '전기차는 자동차로') — HS 류(2자리)는
    # 관세 분류라 8507 배터리=85류, 8473 부품=84류가 정상이지만 산업
    # 관점과 어긋남. 산업트렌드와 동일 HSK-MTI 연계표(industry_of)로
    # leaf(HS10)→산업을 병행 집계해 [HS류|산업] 토글 제공. 미매핑은
    # '기타(미매핑)'.
    try:
        from trade.mti_map import industry_of as _ind_of
    except Exception:
        _ind_of = lambda _h: None

    def _agg(tree: dict, key: str, gname: str, r: dict, h4: str) -> None:
        gnode = tree.setdefault(key, {"name": gname, "h4": {}})
        node = gnode["h4"].setdefault(h4, {
            "exp": 0, "exp_pm": 0, "exp_py": 0,
            "imp": 0, "imp_pm": 0, "imp_py": 0,
            "n": 0, "top_name": "", "top_val": -1, "top_hs": ""})
        for k in ("exp", "exp_pm", "exp_py", "imp", "imp_pm", "imp_py"):
            node[k] += int(r.get(k) or 0)
        node["n"] += 1                       # HS4 내 leaf(품목) 수 — '외 N' 표기용
        v = int(r.get("exp") or 0) + int(r.get("imp") or 0)
        if v > node["top_val"]:
            node["top_val"] = v
            node["top_name"] = (r.get("name") or h4)[:40]
            # 대표 leaf HS(10) — 셀 클릭 시 기업 보고서 HS 검색용(사용자 2026-06-19
            # '히트맵도 연결'). 보고서가 HS6→MTI6→품목+수출입+관련 상장사 해석.
            node["top_hs"] = str(r.get("hs_code") or "")

    ch: dict[str, dict] = {}
    ind: dict[str, dict] = {}
    for r in rows:
        hs = str(r.get("hs_code") or "")
        if len(hs) < 4:
            continue
        c2, h4 = hs[:2], hs[:4]
        _agg(ch, c2, CHAPTER_KR.get(c2, c2), r, h4)
        try:
            iname = _ind_of(hs) or "기타(미매핑)"
        except Exception:
            iname = "기타(미매핑)"
        _agg(ind, iname, iname, r, h4)

    def _flat(tree: dict, with_c2: bool) -> list:
        out = []
        for key, gnode in tree.items():
            h4s = [{"h4": h4, "nm": n["top_name"], "hs": n["top_hs"], "n": n["n"],
                    "e": n["exp"], "epm": n["exp_pm"], "epy": n["exp_py"],
                    "i": n["imp"], "ipm": n["imp_pm"], "ipy": n["imp_py"]}
                   for h4, n in gnode["h4"].items()]
            out.append({"c2": key if with_c2 else "",
                        "name": gnode["name"], "h4s": h4s})
        return out

    return {"ref_ym": ref_ym, "chapters": _flat(ch, True),
            "industries": _flat(ind, False)}


_HEATMAP_CSS = """
.hm-wrap{position:relative}
.hm-bar{display:flex;align-items:center;gap:10px;margin:6px 0 10px;font-size:12.5px;color:var(--text-sub);flex-wrap:wrap}
.hm-bar b{color:var(--text)}
.hm-meter{flex:1;min-width:160px;height:8px;border-radius:4px;overflow:hidden;display:flex;background:var(--border-soft)}
.hm-meter i{display:block;height:100%}
.hm-toggle{display:flex;gap:4px}
.hm-tbtn{padding:3px 10px;border-radius:999px;border:1px solid var(--border-soft);background:var(--bg);color:var(--text-sub);font-size:11.5px;font-weight:600;cursor:pointer}
.hm-tbtn.is-active{background:var(--tone-export);color:#fff;border-color:var(--tone-export)}
#hm-map{position:relative;width:100%;height:760px;background:var(--bg);border-radius:10px;overflow:hidden}
.hm-ch{position:absolute;overflow:hidden;border:1px solid rgba(0,0,0,.35)}
.hm-ch-label{position:absolute;top:0;left:0;right:0;z-index:3;font-size:10.5px;font-weight:700;color:#fff;background:rgba(0,0,0,.55);padding:1px 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}
.hm-cell{position:absolute;overflow:hidden;border:1px solid rgba(0,0,0,.3);cursor:default}
.hm-cell span{position:absolute;inset:auto 2px 1px 3px;top:1px;font-size:10px;line-height:1.25;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.6);overflow:hidden;pointer-events:none}
.hm-tip{position:fixed;z-index:50;background:rgba(20,22,28,.95);color:#fff;font-size:12px;line-height:1.5;padding:8px 10px;border-radius:8px;pointer-events:none;max-width:300px;display:none}
"""


def render_heatmap_html(rows: list[dict], status_label: str = "") -> str:
    """히트맵 탭 본문 HTML. rows 비면 안내 문구."""
    data = build_heatmap_data(rows)
    if not data or not data.get("chapters"):
        return ("<div style='padding:30px;color:var(--text-sub)'>히트맵 데이터가 "
                "아직 없습니다 — 다음 스캔(10분 변경감지·일 4회 풀스윕) 후 표시됩니다.</div>")
    from html import escape as _esc
    ref = _esc(data["ref_ym"])
    status = f" · 관세청 <b>{_esc(status_label)}</b>" if status_label else ""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # </ defuse — JSON 안 '</script>' 류가 블록을 닫지 않게
    payload = payload.replace("</", "<\\/")
    return f"""
<style>{_HEATMAP_CSS}</style>
<div class="hm-wrap">
  <div class="hm-bar">
    <span>기준 <b>{ref}</b>{status} · 박스=HS4 류 전체 합(크기=금액, 라벨=대표품목 외N) · 셀 클릭→대표품목 상장사·보고서 · 10분 변경감지 · 일 4회 풀스윕</span>
    <span class="hm-toggle" id="hm-dir">
      <button class="hm-tbtn is-active" data-v="exp">수출</button>
      <button class="hm-tbtn" data-v="imp">수입</button>
    </span>
    <span class="hm-toggle" id="hm-mode">
      <button class="hm-tbtn is-active" data-v="yoy">YoY</button>
      <button class="hm-tbtn" data-v="mom">MoM</button>
    </span>
    <span class="hm-toggle" id="hm-grp">
      <button class="hm-tbtn is-active" data-v="ch">HS류</button>
      <button class="hm-tbtn" data-v="ind">산업</button>
    </span>
    <span id="hm-counts"></span>
    <span class="hm-meter" id="hm-meter"></span>
  </div>
  <div id="hm-map"></div>
  <div class="hm-tip" id="hm-tip"></div>
</div>
<script type="application/json" id="hm-data">{payload}</script>
<script>
(function(){{
var DATA=JSON.parse(document.getElementById('hm-data').textContent);
var dir='exp', mode='yoy', grp='ch', HMQ='';
window.hmFilter=function(q){{ HMQ=(q||'').toLowerCase();
  if(document.getElementById('hm-map').offsetParent!==null) render(); }};
document.addEventListener('click',function(e){{
  if(e.target.closest('[data-tab="heatmap"]')) setTimeout(render,0);
}});
function pct(cur,base){{ if(!base) return null; return (cur-base)/base*100; }}
function color(p){{
  if(p===null) return '#5a5f6b';
  var t=Math.max(-1,Math.min(1,p/60));   // ±60% 포화
  if(t>=0){{ var k=Math.round(60+90*t); return 'rgb('+(46-20*t)+','+(125+k*0.45)+','+(80-30*t)+')'; }}
  var u=-t; return 'rgb('+(190+40*u)+','+(70-20*u)+','+(70-20*u)+')';
}}
function fmt(v){{
  if(v>=1e9) return (v/1e9).toFixed(1)+'B$';
  if(v>=1e6) return (v/1e6).toFixed(1)+'M$';
  if(v>=1e3) return (v/1e3).toFixed(0)+'K$';
  return v+'$';
}}
// squarified treemap — items:[{{v:..,..}}] → [{{x,y,w,h}}]
function squarify(items,x,y,w,h){{
  var total=items.reduce(function(s,it){{return s+it.v}},0);
  if(total<=0||w<=0||h<=0) return [];
  var scale=w*h/total, rects=[], i=0;
  items=items.slice().sort(function(a,b){{return b.v-a.v}});
  while(i<items.length){{
    var row=[], rowSum=0, best=Infinity;
    var horiz=w>=h, side=horiz?h:w;
    for(var j=i;j<items.length;j++){{
      var cand=rowSum+items[j].v*scale;
      var worst=0;
      for(var k=i;k<=j;k++){{
        var a=items[k].v*scale, len=cand/side, other=a/len;
        var r=Math.max(len/other,other/len); if(r>worst)worst=r;
      }}
      if(worst<=best){{ best=worst; row.push(items[j]); rowSum=cand; }}
      else break;
    }}
    var len2=rowSum/side, off=0;
    for(var k2=0;k2<row.length;k2++){{
      var a2=row[k2].v*scale, ext=a2/len2;
      if(horiz) rects.push({{it:row[k2],x:x,y:y+off,w:len2,h:ext}});
      else rects.push({{it:row[k2],x:x+off,y:y,w:ext,h:len2}});
      off+=ext;
    }}
    if(horiz){{ x+=len2; w-=len2; }} else {{ y+=len2; h-=len2; }}
    i+=row.length;
  }}
  return rects;
}}
function render(){{
  var map=document.getElementById('hm-map');
  var W=map.clientWidth, H=map.clientHeight;
  map.innerHTML='';
  var chs=[], up=0,down=0,flat=0;
  var GROUPS=(grp==='ind'&&DATA.industries)?DATA.industries:DATA.chapters;
  GROUPS.forEach(function(c){{
    var v=0, cells=[];
    c.h4s.forEach(function(n){{
      var val=dir==='exp'?n.e:n.i;
      if(val<=0) return;
      var base=dir==='exp'?(mode==='yoy'?n.epy:n.epm):(mode==='yoy'?n.ipy:n.ipm);
      var p=pct(val,base);
      if(p===null)flat++; else if(p>0.5)up++; else if(p<-0.5)down++; else flat++;
      v+=val; cells.push({{v:val,h4:n.h4,nm:n.nm,p:p,hs:n.hs}});
    }});
    if(v>0) chs.push({{v:v,name:c.name,c2:c.c2,cells:cells}});
  }});
  document.getElementById('hm-counts').innerHTML=
    '<b style="color:#34c759">▲'+up+'</b> · <b style="color:#ff5b5b">▼'+down+'</b> · —'+flat;
  var m=document.getElementById('hm-meter'); var tot=up+down+flat||1;
  m.innerHTML='<i style="width:'+(up/tot*100)+'%;background:#34c759"></i>'
    +'<i style="width:'+(flat/tot*100)+'%;background:#9aa0ab"></i>'
    +'<i style="width:'+(down/tot*100)+'%;background:#ff5b5b"></i>';
  var tip=document.getElementById('hm-tip');
  squarify(chs.map(function(c){{return {{v:c.v,c:c}}}}),0,0,W,H).forEach(function(r){{
    var c=r.it.c;
    var div=document.createElement('div');
    div.className='hm-ch';
    div.style.cssText='left:'+r.x+'px;top:'+r.y+'px;width:'+r.w+'px;height:'+r.h+'px';
    var lbl=document.createElement('div'); lbl.className='hm-ch-label';
    lbl.textContent=(c.c2?c.c2+' ':'')+c.name;
    div.appendChild(lbl);
    var pad=13;
    squarify(c.cells.map(function(n){{return {{v:n.v,n:n}}}}),0,0,r.w-2,r.h-pad-2)
      .forEach(function(q){{
        var n=q.it.n;
        var cell=document.createElement('div'); cell.className='hm-cell';
        cell.style.cssText='left:'+q.x+'px;top:'+(q.y+pad)+'px;width:'+q.w+'px;height:'+q.h+'px;background:'+color(n.p);
        if(HMQ&&(n.h4+' '+n.nm).toLowerCase().indexOf(HMQ)<0){{ cell.style.opacity='.12'; }}
        if(q.w>54&&q.h>26){{
          var s=document.createElement('span');
          s.textContent=n.h4+' '+n.nm+(n.n>1?' 외'+(n.n-1):'')+' '+(n.p===null?'신규':(n.p>0?'+':'')+n.p.toFixed(1)+'%');
          cell.appendChild(s);
        }}
        cell.addEventListener('mousemove',function(ev){{
          tip.style.display='block';
          tip.style.left=Math.min(ev.clientX+14,window.innerWidth-310)+'px';
          tip.style.top=(ev.clientY+12)+'px';
          tip.innerHTML='<b>'+n.h4+' · 대표 '+n.nm+(n.n>1?' 외'+(n.n-1)+'품목':'')+'</b><br>'
            +'<b>HS4 합계</b> '+(dir==='exp'?'수출':'수입')+' '+fmt(n.v)
            +' · '+(mode==='yoy'?'YoY ':'MoM ')
            +(n.p===null?'신규(전기 0)':(n.p>0?'+':'')+n.p.toFixed(1)+'%')
            +(n.n>1?'<br><span style="color:#9aa0ab">※ 박스=HS4 류 전체 합 · 개별 품목은 상세표 참조</span>':'')
            +(n.hs&&window.rbSearch?'<br><span style="color:#6cb6ff">클릭 → 대표품목 관련 상장사·보고서</span>':'');
        }});
        cell.addEventListener('mouseleave',function(){{tip.style.display='none';}});
        // 셀 클릭 → 기업 보고서(품목 모드)로 그 HS/품목 + 관련 상장사 + 수출입
        // (사용자 2026-06-19 '히트맵도 연결'). rbSearch = 기업 보고서 위젯 전역 훅.
        if(n.hs&&window.rbSearch){{
          cell.style.cursor='pointer';
          cell.addEventListener('click',function(){{tip.style.display='none';window.rbSearch(n.hs);}});
        }}
        div.appendChild(cell);
      }});
    map.appendChild(div);
  }});
}}
['hm-dir','hm-mode','hm-grp'].forEach(function(id){{
  document.getElementById(id).addEventListener('click',function(e){{
    var b=e.target.closest('.hm-tbtn'); if(!b)return;
    this.querySelectorAll('.hm-tbtn').forEach(function(x){{x.classList.remove('is-active')}});
    b.classList.add('is-active');
    if(id==='hm-dir')dir=b.dataset.v; else if(id==='hm-mode')mode=b.dataset.v; else grp=b.dataset.v;
    render();
  }});
}});
var hmObs=new IntersectionObserver(function(es){{
  es.forEach(function(en){{ if(en.isIntersecting){{ render(); hmObs.disconnect(); }} }});
}});
hmObs.observe(document.getElementById('hm-map'));
window.hmCSV=function(){{
  // 활성 그룹 기준 CSV rows — 값은 HS4 류 전체 합(대표품목명+외N), 개별품목 아님
  var G=(grp==='ind'&&DATA.industries)?DATA.industries:DATA.chapters;
  var rows=[['그룹','HS4','대표품목','HS4품목수','수출$(HS4합계)','수출YoY%','수출MoM%','수입$(HS4합계)','수입YoY%','수입MoM%']];
  function p(c,b){{ if(!b)return ''; return ((c-b)/b*100).toFixed(1); }}
  G.forEach(function(c){{
    c.h4s.forEach(function(n){{
      rows.push([(c.c2?c.c2+' ':'')+c.name,n.h4,n.nm+(n.n>1?' 외'+(n.n-1):''),n.n,
        n.e,p(n.e,n.epy),p(n.e,n.epm),n.i,p(n.i,n.ipy),p(n.i,n.ipm)]);
    }});
  }});
  return rows;
}};
window.addEventListener('resize',function(){{
  if(document.getElementById('hm-map').offsetParent!==null) render();
}});
}})();
</script>
"""
