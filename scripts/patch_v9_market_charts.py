from pathlib import Path

p = Path('index.html')
s = p.read_text(encoding='utf-8')

s = s.replace(
    '<small>Momentum osservato ai checkpoint disponibili</small>',
    '<small>Prezzi reali osservati · intraday 15m / daily</small>'
)

old_tabs = '<div class="tabs" id="perfTabs"><button class="active" data-chart="performance">PERFORMANCE</button><button data-chart="risk">RISK</button><button data-chart="horizon">HORIZON</button></div>'
new_tabs = '<div class="tabs" id="perfTabs"><button data-chart="1d">1D</button><button class="active" data-chart="3m">3M</button><button data-chart="1y">1Y</button><button data-chart="risk">RISK</button><button data-chart="horizon">HORIZON</button></div>'
if old_tabs not in s:
    raise SystemExit('V9 performance tabs marker not found')
s = s.replace(old_tabs, new_tabs, 1)

old_state = "const S={data:null,signals:[],selected:null,filter:'ALL',watch:new Set(JSON.parse(localStorage.getItem('alpha_watch_v9')||'[]')),portfolio:JSON.parse(localStorage.getItem('alpha_pf_v9')||'[]'),chart:'performance'};"
new_state = "const S={data:null,series:null,signals:[],selected:null,filter:'ALL',watch:new Set(JSON.parse(localStorage.getItem('alpha_watch_v9')||'[]')),portfolio:JSON.parse(localStorage.getItem('alpha_pf_v9')||'[]'),chart:'3m'};"
if old_state not in s:
    raise SystemExit('V9 state marker not found')
s = s.replace(old_state, new_state, 1)

start = s.find('function renderChart(){')
end = s.find("$('perfTabs').querySelectorAll", start)
if start < 0 or end < 0:
    raise SystemExit('renderChart markers not found')
new_chart = r'''function renderChart(){
  const s=S.selected;if(!s)return;const c=$('mainChart'),feed=S.series?.symbols?.[s.ticker];
  if(['1d','3m','1y'].includes(S.chart)){
    let rows=[];
    if(S.chart==='1d'){
      rows=Array.isArray(feed?.intraday)?feed.intraday:[];
      if(rows.length){const lastDay=String(rows[rows.length-1][0]).slice(0,10);rows=rows.filter(r=>String(r[0]).slice(0,10)===lastDay)}
    }else{
      rows=Array.isArray(feed?.daily)?feed.daily:[];
      if(S.chart==='3m')rows=rows.slice(-66);
    }
    if(rows.length>=2){
      const base=Number(rows[0][1]);
      const points=rows.map(r=>base?((Number(r[1])/base)-1)*100:0);
      const stride=Math.max(1,Math.ceil(rows.length/4));
      const labels=rows.map((r,i)=>(i===0||i===rows.length-1||i%stride===0)?new Date(r[0]).toLocaleDateString('it-IT',{day:'2-digit',month:'short'}):'');
      drawLine(c,points,labels,'%');
      $('chartLegend').innerHTML=`<span>${S.chart==='1d'?'15 minuti':'Daily adjusted close'}</span><span>${esc(feed?.display||s.ticker)}</span><span>Fonte: ${esc(S.series?.source||'Yahoo Finance via yfinance')}</span>`;
      return;
    }
    drawLine(c,[Number(s.ret5_pct),Number(s.ret20_pct),Number(s.ret60_pct)],['5D','20D','60D']);
    $('chartLegend').innerHTML='<span>Feed storico non disponibile · fallback checkpoint osservati</span>';
    return;
  }
  if(S.chart==='risk'){
    drawBars(c,[{l:'RSI',v:Number(s.rsi||0)},{l:'VOL',v:Math.min(100,Number(s.volatility_pct||0)*10)},{l:'RISK',v:Number(s.risk_pct||0)*100},{l:'CONF',v:Number(s.confidence_pct||0)}]);
    $('chartLegend').innerHTML='<span>Metriche normalizzate per lettura rapida</span>';
  }else{
    const ev=s.horizon_profile?.evidence||[];
    drawBars(c,ev.map(e=>({l:`${e.horizon}D`,v:Number(e.hit_rate||0)*100})));
    $('chartLegend').innerHTML='<span>Hit-rate pre-forecast per horizon</span><span>Campioni piccoli ≠ certezza</span>';
  }
}
'''
s = s[:start] + new_chart + s[end:]

start = s.find('async function boot(){')
end = s.find("window.addEventListener('resize'", start)
if start < 0 or end < 0:
    raise SystemExit('boot markers not found')
new_boot = r'''async function boot(){try{
  const [latestRes,seriesRes]=await Promise.all([
    fetch(`data/latest.json?v=${Date.now()}`,{cache:'no-store'}),
    fetch(`data/market-series.json?v=${Date.now()}`,{cache:'no-store'}).catch(()=>null)
  ]);
  if(!latestRes||!latestRes.ok)throw new Error(`latest HTTP ${latestRes?.status||'offline'}`);
  S.data=await latestRes.json();
  if(seriesRes&&seriesRes.ok){try{S.series=await seriesRes.json()}catch{S.series=null}}
  S.signals=Array.isArray(S.data.signals)?S.data.signals:[];S.selected=pickBest();
  renderStatus();renderTape();renderHero();renderOrder();renderFilters();renderLists();renderHeat();renderHealth();renderQuant();brokerCalc();renderPortfolio();requestAnimationFrame(renderChart)
}catch(e){$('syncText').textContent='DATA ERROR';$('healthDot').className='dot bad';$('heroName').textContent='Dati non disponibili';$('heroTicker').textContent='FAIL CLOSED';$('heroReason').textContent='Alpha non mostra segnali finché data/latest.json non è leggibile.';console.error(e)}}
'''
s = s[:start] + new_boot + s[end:]

p.write_text(s, encoding='utf-8')
print('Alpha V9 market-series chart patch applied')
