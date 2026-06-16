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
  // 신고저(52주)=1h, 그 외 라이브 페이지=30s
  var SLOW={'/kr52':1,'/jp52':1,'/hk52':1,'/tw52':1,'/ushighlow':1};
  var MKT={'/nxt':'KR','/theme':'KR','/highlow':'KR','/kr52':'KR','/krprepost':'KR',
    '/usmovers':'US','/ushighlow':'US','/usindustry':'US','/usprepost':'US',
    '/jpmovers':'JP','/jp52':'JP','/hkmovers':'HK','/hk52':'HK',
    '/cnmovers':'CN','/twhighlow':'TW','/tw52':'TW'};
  var market=MKT[page];
  if(!market) return;                          // 라이브 페이지 아님 → 폴링 0
  var interval=SLOW[page]?3600000:30000;       // 신고저 1h · 그 외 30s
  function kstNow(){var n=new Date();return new Date(n.getTime()+(n.getTimezoneOffset()+540)*60000);}
  function isOpen(){
    var k=kstNow(), d=k.getDay(), hm=k.getHours()*60+k.getMinutes();
    if(market==='US'){   // 美 정규장 ≈ KST 22:30~익일 05:10 (서머타임 버퍼)
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
  function tick(){
    if(document.hidden) return;                  // 백그라운드 탭 skip
    if(!isOpen()) return;                         // 장후·주말 → 폴링 0(부하 방지)
    var s=document.querySelector('#q,#mkt-search,input[type=search]');
    if(s&&(s===document.activeElement||s.value)) return;   // 검색 중 skip
    fetch(location.href,{cache:'no-store'})
      .then(function(r){if(!r.ok)throw 0;return r.text();})
      .then(function(html){
        var doc=new DOMParser().parseFromString(html,'text/html');
        var fresh=doc.getElementById('live-root'), cur=document.getElementById('live-root');
        if(fresh&&cur&&fresh.innerHTML.length>50){
          cur.innerHTML=fresh.innerHTML;
          if(window.hlBindSort) window.hlBindSort();   // 표 정렬 재바인드(swap 후)
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
