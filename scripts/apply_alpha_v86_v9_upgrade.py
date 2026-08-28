#!/usr/bin/env python3
"""Upgrade the v8.5 engine and current V9 cockpit without reverting the V9 UI."""
from pathlib import Path

from apply_alpha_v86_upgrade_v2 import patch_engine, replace_unique

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'engine.py'
INDEX = ROOT / 'index.html'


def patch_v9_index(text):
    changed = False
    if 'Alpha Engine V9 · Finance Cockpit' not in text:
        raise RuntimeError('V9 cockpit marker missing; refusing to patch unknown frontend')

    quant_old = '<div id="governance" class="heatmap"></div></div></div></section>'
    quant_new = '''<div id="governance" class="heatmap"></div></div><div class="card"><div class="head"><div><h2>Decision Ledger</h2><small>Un episodio attivo per asset · storico deduplicato · audit locale</small></div><div><button id="importLedgerBtn" class="btn">Import</button> <button id="exportLedgerBtn" class="btn">Export</button></div></div><div id="ledgerKpis" class="kpis"></div><div id="ledgerNote" class="warning" style="margin-top:10px"></div><div id="ledgerList" class="list" style="margin-top:10px"></div><input id="importLedgerFile" type="file" accept="application/json,.json" hidden></div></div></section>'''
    text, c = replace_unique(text, quant_old, quant_new, 'V9 Decision Ledger card')
    changed |= c

    state_old = "const S={data:null,signals:[],selected:null,filter:'ALL',watch:new Set(JSON.parse(localStorage.getItem('alpha_watch_v9')||'[]')),portfolio:JSON.parse(localStorage.getItem('alpha_pf_v9')||'[]'),chart:'performance'};"
    ledger_js = '''const S={data:null,signals:[],selected:null,filter:'ALL',watch:new Set(JSON.parse(localStorage.getItem('alpha_watch_v9')||'[]')),portfolio:JSON.parse(localStorage.getItem('alpha_pf_v9')||'[]'),chart:'performance'};
const LEDGER_KEY='alpha_decision_journal_v86',LEDGER_LEGACY_KEYS=['alpha_decision_journal_v70','alpha_decision_journal_v44'],LEDGER_TERMINAL=new Set(['TARGET1','TARGET2','STOP','AMBIGUO','SUPERSEDED','EXPIRED']);
const num=(v,d=null)=>Number.isFinite(Number(v))?Number(v):d;
function ledgerKey(z){return [z?.ticker||'',z?.direction||'',num(z?.horizon,null)??'NA',z?.horizon_setup_family||'MIXED'].join('|')}
function compactLedgerEpisodes(rows){
 const sorted=(Array.isArray(rows)?rows:[]).filter(z=>z&&z.ticker&&z.direction).map(z=>({...z})).sort((a,b)=>num(a.ts,0)-num(b.ts,0)),out=[],activeByKey=new Map();
 for(const z of sorted){
  z.direction=String(z.direction).toUpperCase()==='SHORT'?'SHORT':'LONG';
  if(LEDGER_TERMINAL.has(z.status)){out.push(z);continue}
  const key=ledgerKey(z),prev=activeByKey.get(key);
  if(prev){
   const pe=num(prev.price,null),ze=num(z.price,null);prev.episode_id=prev.episode_id||prev.id;prev.episode_key=key;prev.last_seen_at=z.observed_at||z.market_data_at||z.updated_at||prev.last_seen_at;prev.last_model_version=z.model_version||prev.last_model_version||prev.model_version;prev.latest_confidence=num(z.confidence,prev.latest_confidence);prev.observed_high=Math.max(num(prev.observed_high,pe??-Infinity),num(z.observed_high,ze??-Infinity));prev.observed_low=Math.min(num(prev.observed_low,pe??Infinity),num(z.observed_low,ze??Infinity));if(num(z.last_price,null)!=null)prev.last_price=z.last_price;if(z.observed_at)prev.observed_at=z.observed_at;prev.observations=Math.max(num(prev.observations,0),num(z.observations,0));prev.duplicate_snapshots=num(prev.duplicate_snapshots,0)+1;continue;
  }
  z.episode_id=z.episode_id||z.id;z.episode_key=key;z.status=z.status||((num(z.stop,null)!=null&&num(z.target1,null)!=null)?'OPEN':'OBSERVE_ONLY');z.first_seen_at=z.first_seen_at||z.updated_at||new Date(num(z.ts,Date.now())).toISOString();z.last_seen_at=z.last_seen_at||z.observed_at||z.market_data_at||z.updated_at||z.first_seen_at;activeByKey.set(key,z);out.push(z);
 }
 const byTicker=new Map();out.forEach(z=>{if(!LEDGER_TERMINAL.has(z.status)){const a=byTicker.get(z.ticker)||[];a.push(z);byTicker.set(z.ticker,a)}});
 for(const group of byTicker.values()){if(group.length<2)continue;group.sort((a,b)=>num(a.ts,0)-num(b.ts,0));const newest=group[group.length-1];group.slice(0,-1).forEach(old=>{old.status='SUPERSEDED';old.superseded_by=newest.episode_id||newest.id;old.resolved_at=newest.first_seen_at||newest.updated_at||new Date().toISOString()})}
 return out.sort((a,b)=>num(a.ts,0)-num(b.ts,0));
}
function readLedger(){
 try{let raw=localStorage.getItem(LEDGER_KEY);if(!raw){for(const k of LEDGER_LEGACY_KEYS){const v=localStorage.getItem(k);if(v){raw=v;break}}}const parsed=JSON.parse(raw||'[]');return compactLedgerEpisodes(Array.isArray(parsed)?parsed:(Array.isArray(parsed?.journal)?parsed.journal:[]))}catch{return[]}
}
function saveLedger(rows){try{localStorage.setItem(LEDGER_KEY,JSON.stringify(compactLedgerEpisodes(rows).slice(-750)))}catch{}}
function feedRows(){return [...(S.data?.signals||[]),...(S.data?.watchlist||[])]}
function feedPrice(x){return num(x?.quote_price,num(x?.current_price,num(x?.last_price,num(x?.price,num(x?.entry_price,null)))))}
function directionalPct(dir,entry,px){if(!(entry>0)||px==null)return null;return (dir==='SHORT'?(entry-px)/entry:(px-entry)/entry)*100}
function snapshotLedger(){
 if(!S.data)return;let j=readLedger(),changed=false,now=Date.now();
 for(const x of (S.data.signals||[])){if(!x?.ticker||!x?.direction)continue;const key=[x.ticker,x.direction,num(x.horizon,null)??'NA',x.horizon_setup_family||'MIXED'].join('|'),same=j.find(z=>!LEDGER_TERMINAL.has(z.status)&&ledgerKey(z)===key);
  if(same){same.last_seen_at=S.data.market_data_at||S.data.updated_at||new Date().toISOString();same.last_model_version=S.data.model_version||x.model_version||same.model_version;same.latest_confidence=num(x.confidence_pct,same.latest_confidence);same.model_completeness=num(x.model_completeness_score,same.model_completeness);changed=true;continue}
  const episodeId='EP_'+x.ticker+'_'+x.direction+'_'+now,prior=j.filter(z=>!LEDGER_TERMINAL.has(z.status)&&z.ticker===x.ticker);for(const old of prior){old.status='SUPERSEDED';old.superseded_by=episodeId;old.resolved_at=S.data.market_data_at||S.data.updated_at||new Date().toISOString();changed=true}
  const px=feedPrice(x),levels=num(x.stop_price,null)!=null&&num(x.target1_price,null)!=null;j.push({id:episodeId,episode_id:episodeId,episode_key:key,ts:now,first_seen_at:S.data.updated_at||new Date().toISOString(),last_seen_at:S.data.market_data_at||S.data.updated_at||new Date().toISOString(),updated_at:S.data.updated_at||null,market_data_at:S.data.market_data_at||null,model_version:S.data.model_version||x.model_version||'unknown',learning_lineage:S.data.model_learning?.learning_lineage||null,ticker:x.ticker,direction:x.direction,asset_class:x.asset_class||null,currency:x.currency||'USD',price:px,score:num(x.score,null),confidence:num(x.confidence_pct,null),learning_adjustment:num(x.learning_adjustment,0),data_quality:num(x.data_quality_score,null),model_completeness:num(x.model_completeness_score,null),horizon:num(x.horizon,null),horizon_state:x.horizon_state||null,horizon_setup_family:x.horizon_setup_family||null,stop:num(x.stop_price,null),target1:num(x.target1_price,null),target2:num(x.target2_price,null),status:levels?'OPEN':'OBSERVE_ONLY',observations:0,observed_high:px,observed_low:px,last_price:px,current_return_pct:0,mfe_pct:0,mae_pct:0,r_multiple:0});changed=true;
 }
 if(changed)saveLedger(j)
}
function observeLedger(){
 if(!S.data)return;const rows=feedRows(),byTicker=new Map();rows.forEach(x=>{if(x?.ticker&&feedPrice(x)!=null&&!byTicker.has(x.ticker))byTicker.set(x.ticker,x)});let j=readLedger(),changed=false;
 for(const z of j){if(LEDGER_TERMINAL.has(z.status))continue;const x=byTicker.get(z.ticker),entry=num(z.price,null),cur=feedPrice(x);if(!x||entry==null||cur==null)continue;z.last_price=cur;z.observed_high=Math.max(num(z.observed_high,entry),cur);z.observed_low=Math.min(num(z.observed_low,entry),cur);z.observed_at=S.data.market_data_at||S.data.updated_at||new Date().toISOString();z.observations=num(z.observations,0)+1;z.current_return_pct=directionalPct(z.direction,entry,cur);z.mfe_pct=z.direction==='SHORT'?directionalPct('SHORT',entry,z.observed_low):directionalPct('LONG',entry,z.observed_high);z.mae_pct=Math.min(0,z.direction==='SHORT'?directionalPct('SHORT',entry,z.observed_high):directionalPct('LONG',entry,z.observed_low));const stop=num(z.stop,null),t1=num(z.target1,null),t2=num(z.target2,null),stopHit=stop!=null&&(z.direction==='SHORT'?z.observed_high>=stop:z.observed_low<=stop),t1Hit=t1!=null&&(z.direction==='SHORT'?z.observed_low<=t1:z.observed_high>=t1),t2Hit=t2!=null&&(z.direction==='SHORT'?z.observed_low<=t2:z.observed_high>=t2);if(t2Hit)z.status='TARGET2';else if(t1Hit)z.status='TARGET1';else if(stopHit)z.status='STOP';else z.status=(stop==null||t1==null)?'OBSERVE_ONLY':'OPEN';const risk=stop==null?null:Math.abs(entry-stop);if(risk>0)z.r_multiple=z.status==='STOP'?-1:z.status==='TARGET1'?Math.abs(t1-entry)/risk:z.status==='TARGET2'?Math.abs(t2-entry)/risk:(z.direction==='SHORT'?entry-cur:cur-entry)/risk;if(['TARGET1','TARGET2','STOP'].includes(z.status))z.resolved_at=z.observed_at;changed=true}
 if(changed)saveLedger(j)
}
function ledgerStats(){const j=readLedger(),resolved=j.filter(z=>['TARGET1','TARGET2','STOP'].includes(z.status)),wins=resolved.filter(z=>z.status!=='STOP'),active=j.filter(z=>!LEDGER_TERMINAL.has(z.status)),sup=j.filter(z=>z.status==='SUPERSEDED'),dups=j.reduce((a,z)=>a+num(z.duplicate_snapshots,0),0),avgR=resolved.length?resolved.map(z=>num(z.r_multiple,null)).filter(v=>v!=null).reduce((a,b)=>a+b,0)/resolved.map(z=>num(z.r_multiple,null)).filter(v=>v!=null).length:null;return{j,resolved,wins,active,sup,dups,avgR}}
function renderLedger(){if(!$('ledgerKpis'))return;const s=ledgerStats(),backend=S.data?.memory||{};$('ledgerKpis').innerHTML=[['ACTIVE',s.active.length],['RESOLVED',s.resolved.length],['HIT RATE',s.resolved.length?`${fmt(s.wins.length/s.resolved.length*100,1)}%`:'—'],['AVG R',s.avgR==null?'—':`${fmt(s.avgR,2)}R`],['SUPERSEDED',s.sup.length],['DUP REMOVED',s.dups],['BACKEND N',backend.independent_episodes??backend.total??'—'],['LEARNING N',backend.learning_resolved??backend.resolved??'—']].map(([a,b])=>`<div class="miniCard"><span>${a}</span><b>${b}</b></div>`).join('');$('ledgerNote').textContent='Episode guard: un solo episodio attivo per ticker. Le tesi sostituite restano in audit come SUPERSEDED e non contano come esiti. Learning separato dalla vecchia lineage.';const recent=[...s.j].sort((a,b)=>num(b.ts,0)-num(a.ts,0)).slice(0,8);$('ledgerList').innerHTML=recent.map(z=>`<div class="asset"><div class="assetName"><b>${esc(z.ticker)}</b><span>${esc(z.direction)} · ${esc(z.horizon??'—')}D</span></div><div class="assetPrice">${money(z.last_price??z.price,z.currency)}</div><div class="assetScore ${z.status==='STOP'?'neg':z.status?.startsWith('TARGET')?'pos':''}">${esc(z.status||'OPEN')}<span class="sub">${num(z.r_multiple,null)==null?'—':fmt(z.r_multiple,2)+'R'}</span></div><div class="hideM"><b style="font-size:9px">${num(z.confidence,null)==null?'—':fmt(z.confidence,0)+'%'}</b><span class="sub">CONF</span></div><span></span></div>`).join('')||'<div class="empty">Il ledger inizierà dal prossimo segnale valido.</div>'}
function exportLedger(){const payload={format:'ALPHA-DECISION-JOURNAL',version:'8.6',exported_at:new Date().toISOString(),journal:readLedger()},blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='alpha-v86-decision-journal-'+new Date().toISOString().slice(0,10)+'.json';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
async function importLedger(ev){const f=ev.target.files?.[0];ev.target.value='';if(!f)return;try{if(f.size>2*1024*1024)throw new Error('file troppo grande');const o=JSON.parse(await f.text()),rows=Array.isArray(o)?o:o?.journal;if(!Array.isArray(rows))throw new Error('formato non riconosciuto');saveLedger([...readLedger(),...rows]);observeLedger();renderLedger();$('ledgerNote').textContent='Import completato e deduplicato: '+rows.length+' record ricevuti.'}catch(e){$('ledgerNote').textContent='Import non riuscito: '+String(e.message||e)}}
function bindLedgerControls(){if($('exportLedgerBtn')&&!$('exportLedgerBtn').dataset.bound){$('exportLedgerBtn').dataset.bound='1';$('exportLedgerBtn').addEventListener('click',exportLedger);$('importLedgerBtn').addEventListener('click',()=>$('importLedgerFile').click());$('importLedgerFile').addEventListener('change',importLedger)}}'''
    text, c = replace_unique(text, state_old, ledger_js, 'V9 ledger runtime')
    changed |= c

    gov_old = "['Strict no-fabrication',S.data?.data_quality?.strict_no_fabrication?'ON':'OFF']"
    gov_new = "['Strict no-fabrication',S.data?.data_quality?.strict_no_fabrication?'ON':'OFF'],['Model completeness',Number.isFinite(Number(s.model_completeness_score))?`${fmt(s.model_completeness_score,0)}/100`:'—'],['Episode guard',S.data?.edge_core?.signal_lock==='ONE_ACTIVE_EPISODE_PER_TICKER_WITH_STABLE_SETUP_KEY'?'ON':'CHECK']"
    text, c = replace_unique(text, gov_old, gov_new, 'V9 model completeness/governance')
    changed |= c

    boot_old = 'renderHeat();renderHealth();renderQuant();brokerCalc();renderPortfolio();requestAnimationFrame(renderChart)'
    boot_new = 'renderHeat();renderHealth();renderQuant();snapshotLedger();observeLedger();renderLedger();bindLedgerControls();brokerCalc();renderPortfolio();requestAnimationFrame(renderChart)'
    text, c = replace_unique(text, boot_old, boot_new, 'V9 boot ledger hooks')
    changed |= c

    health_old = "`Backtest ${bt.state||'—'} · resolved ${mem.resolved||0}.`"
    health_new = "`Backtest ${bt.state||'—'} · resolved ${mem.resolved||0} · episodi indipendenti ${mem.independent_episodes??mem.total??'—'} · active ${mem.active??'—'}.`"
    text, c = replace_unique(text, health_old, health_new, 'V9 health episode metrics')
    changed |= c
    return text, changed


def main():
    engine = ENGINE.read_text(encoding='utf-8')
    index = INDEX.read_text(encoding='utf-8')
    engine2, ce = patch_engine(engine)
    index2, ci = patch_v9_index(index)
    required_engine = ['8.6.0-episode-ledger-cost-aware', 'def migrate_prediction_episodes(mem):', 'model_completeness_score', 'ONE_ACTIVE_EPISODE_PER_TICKER_WITH_STABLE_SETUP_KEY']
    required_index = ['Decision Ledger', 'compactLedgerEpisodes', 'DUP REMOVED', "version:'8.6'", 'Model completeness', 'Episode guard']
    for m in required_engine:
        if m not in engine2: raise RuntimeError('engine verification failed: '+m)
    for m in required_index:
        if m not in index2: raise RuntimeError('V9 verification failed: '+m)
    if '<12*3600*1000' in index2: raise RuntimeError('legacy 12-hour gate unexpectedly present')
    if ce: ENGINE.write_text(engine2, encoding='utf-8')
    if ci: INDEX.write_text(index2, encoding='utf-8')
    print(f'Alpha v8.6 + V9 cockpit migration complete: engine_changed={ce} index_changed={ci}')


if __name__ == '__main__':
    main()
