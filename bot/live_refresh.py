"""세션·cadence 인지 자동 새로고침 JS — 라이브 데이터 대시보드(무버/수급/업종/
신고저)가 진입·체류 중 스스로 갱신(수동 새로고침 불요, 사용자 2026-06-15).

원칙(사용자 '똑똑하게 잘 알아서'):
  • 장중에만 폴링 — 장후·주말은 skip(부하 0, '장후엔 돌릴 필요 없어'). 시장별
    KST 거래시간으로 판정(미국은 KST 밤~익일 새벽 + 주말 경계 처리).
  • cadence: 신고저(52주 전종목 스캔, 무거움)=1시간, 무버/수급/업종(네이버
    경량)=30초 ('신고저는 한시간 기준'). 서버 _session_fresh/_HL_INTRA_TTL 와 동일 철학.
  • fetch-self → #live-root innerHTML 교체(스크롤·다른 위젯 보존) + 정렬
    재바인드(window.hlBindSort). 백그라운드 탭·검색 입력 중 skip. graceful.

#live-root 없는 페이지·미등록 경로는 폴링 0(안전). 토큰 경로(/<token>/nxt)도
마지막 세그먼트로 판정.
"""

LIVE_REFRESH_JS = """<script>
(function(){
  var p=location.pathname; if(p.charAt(p.length-1)==='/') p=p.slice(0,-1);
  var page='/'+p.split('/').pop();
  // 신고저(52주)=1h, 美 장전·장후=5분(서버 30분 TTL), 그 외 라이브 페이지=30s
  // /twhighlow(대만 급등락)은 서버 STOCK_DAY_ALL 캐시가 1h 라 30초 폴링은 ~120배
  // over-poll(동일 바이트 재서빙) → SLOW(1h)로 정렬(사용자 2026-06-16 TW T2).
  var SLOW={'/kr52':1,'/jp52':1,'/hk52':1,'/tw52':1,'/ushighlow':1,'/twhighlow':1};
  // 美 장전·장후: 서버 _PREPOST_TTL=30분이라 30초 폴링은 60배 over-poll(동일
  // 데이터 재서빙·라이브 착시) → 5분으로(사용자 2026-06-16 C 그룹).
  // /krvolume: 서버 캐시 60s · 사용자 지정 2분 주기(2026-09-16) — 30초 폴링이면
  // 3/4 가 같은 바이트 재서빙이다.
  // /krafter(KRX 애프터마켓)는 **폴링 설정을 두지 않는다** — 형제 /krprepost 와
  // 같은 기본 30초다(사용자 '똑같이 KRX 도'). 실제 재집계 간격은 서버
  // _KR_PREPOST_TTL=120초이고 폴링은 그 결과를 30초 안에 화면에 올리는
  // 역할이다 — 폴링을 120초로 두면 TTL 과 같아져 재집계가 2~4분으로 흔들린다(#36).
  var MED={'/usprepost':300000,'/krvolume':120000};
  var MKT={'/nxt':'KR','/theme':'KR','/highlow':'KR','/kr52':'KR','/krprepost':'KR','/krvolume':'KR','/krafter':'KR',
    '/usmovers':'US','/ushighlow':'US','/usindustry':'US','/usprepost':'US',
    '/jpmovers':'JP','/jp52':'JP','/hkmovers':'HK','/hk52':'HK',
    '/cnmovers':'CN','/twhighlow':'TW','/tw52':'TW'};
  var market=MKT[page];
  if(!market) return;                          // 라이브 페이지 아님 → 폴링 0
  var interval=SLOW[page]?3600000:(MED[page]||30000);  // 신고저 1h · 美 장전장후 5분 · 그 외 30s
  function kstNow(){var n=new Date();return new Date(n.getTime()+(n.getTimezoneOffset()+540)*60000);}
  function isOpen(){
    var k=kstNow(), d=k.getDay(), hm=k.getHours()*60+k.getMinutes();
    if(market==='US'){
      if(page==='/usprepost'){   // 美 연장(장전·장후) — 정규장 창보다 넓게(서머타임
        // 버퍼). 장전 KST~17:00–22:30 + 정규 22:30–05:10 + 장후 ~05:00–09:00 →
        // 17:00→익일 10:10. 월저녁~토오전. 서버 30분 SWR 라 30초 폴링=캐시 재조회(부하 무).
        if(d>=1&&d<=5&&hm>=17*60) return true;       // 월~금 저녁(美 장전 onset)
        if(d>=2&&d<=6&&hm<=10*60+10) return true;     // 화~토 오전(정규+장후 tail)
        return false;
      }
      // 정규장 보드(/usmovers·/ushighlow·/usindustry) ≈ KST 22:30~익일 05:10
      if(d>=1&&d<=5&&hm>=22*60+30) return true;   // 월~금 밤(美 당일)
      if(d>=2&&d<=6&&hm<=5*60+10) return true;     // 화~토 새벽(美 전일)
      return false;
    }
    if(d===0||d===6) return false;               // 아시아장 주말 skip
    // KR 08:00–20:00 = NXT(넥스트레이드) 프리마켓(08:00)~애프터마켓(20:00) 포함
    // (정규 KRX 09:00–15:30 + NXT 연장거래, 사용자 2026-06-15 'NXT 도 고려').
    // 그래서 NXT 시간대에도 폴링이 살아 라이브 갱신. JP/HK/CN/TW = 정규장 + 마감 버퍼.
    var W={KR:[8*60,20*60],JP:[9*60,15*60+10],HK:[10*60+30,17*60+10],
           CN:[10*60+30,16*60+10],TW:[10*60,14*60+40]}[market];
    return W?(hm>=W[0]&&hm<=W[1]):true;
  }
  // 컬럼 필터(.hl-flt: 종목 검색·숫자 범위·업종 드롭다운)가 활성이면 swap skip —
  // innerHTML 교체가 사용자가 설정한 필터를 지워버리지 않게(상태 보존, 사용자
  // 2026-06-17). 필터 해제 시 폴링 재개. 텍스트는 type=search 라 아래 검색 가드에도
  // 걸리지만, 숫자(number)·업종(select)은 여기서만 잡힌다.
  function filtering(){
    // hl-table + 범용(테마/업종강도/NXT) 숫자/검색 입력
    var f=document.querySelectorAll('.hl-flt, tr.cflt-row input');
    for(var i=0;i<f.length;i++){ var el=f[i];
      if(el===document.activeElement || el.value) return true; }
    // 다중선택 드롭다운(.ms): 선택 있음 또는 팝업 열림(상호작용 중) → swap skip
    var ms=document.querySelectorAll('.ms');
    for(var k=0;k<ms.length;k++){
      if(ms[k]._sel && ms[k]._sel.length) return true;
      var pop=ms[k].querySelector('.ms-pop');
      if(pop && pop.style.display==='block') return true;
    }
    return false;
  }
  function tick(){
    if(document.hidden) return;                  // 백그라운드 탭 skip
    if(!isOpen()) return;                         // 장후·주말 → 폴링 0(부하 방지)
    var s=document.querySelector('#q,#mkt-search,input[type=search]');
    if(s&&(s===document.activeElement||s.value)) return;   // 검색 중 skip
    if(filtering()) return;                        // 컬럼 필터 활성 중 skip(상태 보존)
    fetch(location.href,{cache:'no-store'})
      .then(function(r){if(!r.ok)throw 0;return r.text();})
      .then(function(html){
        var doc=new DOMParser().parseFromString(html,'text/html');
        /* 부제(`.sub`)는 **신선도를 말하는 줄**이다 — `원천 …` · `저장분(수집
           실패)` · `장중 30초 캐시`. 그런데 `#live-root` 밖이라 탭을 열어 둔
           채 배포·수집이 일어나면 **표만 갱신되고 부제는 영원히 옛 문구**가
           된다(2026-09-13 독립 리뷰 실측 — 사용자가 테마 부제를 두 번 물은
           그 증상의 충분한 설명이다, #43 신선도 라벨이 안 갱신되면 거짓말 ·
           #38 같은 화면의 두 부분이 다른 주기로 돈다). 값과 그 값을 설명하는
           라벨은 **같이** 갈아끼운다. */
        var fsub=doc.getElementById('live-sub'), csub=document.getElementById('live-sub');
        if(fsub&&csub) csub.innerHTML=fsub.innerHTML;
        var fresh=doc.getElementById('live-root'), cur=document.getElementById('live-root');
        if(fresh&&cur&&fresh.innerHTML.length>50){
          cur.innerHTML=fresh.innerHTML;
          if(window.hlBindSort) window.hlBindSort();     // 표 정렬 재바인드(swap 후)
          if(window.hlBindFilter) window.hlBindFilter(); // hl-table 필터행 재생성(swap 후)
          if(window.bindCflt) window.bindCflt();         // 범용 필터(테마/업종강도/NXT) 재생성
        }
      }).catch(function(){});                      // graceful — 무변경
  }
  setInterval(tick, interval);
  // 탭 복귀 즉시 라이브 (사용자 2026-06-15 '대쉬보드 들어가면 그 순간 가장
  // Live'). 진입 자체는 no-cache 서버 렌더라 이미 최신 — 이건 백그라운드에
  // 있다가 돌아왔을 때 다음 interval 까지 기다리지 않고 바로 1회 갱신(장중에만,
  // tick 내부 isOpen·검색중 가드 그대로 적용). 장후·주말이면 tick 이 알아서 skip.
  document.addEventListener('visibilitychange', function(){
    if(!document.hidden) tick();
  });
})();
</script>"""
