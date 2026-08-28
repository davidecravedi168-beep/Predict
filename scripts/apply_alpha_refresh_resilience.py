from pathlib import Path
import re

p=Path('finance-cockpit.js')
s=p.read_text(encoding='utf-8')
orig=s

new_status="function renderStatus(){const ts=state.data?.engine_updated_at||state.data?.updated_at,marketTs=state.data?.market_data_at;const mins=ts?(Date.now()-new Date(ts).getTime())/60000:Infinity;const healthTs=state.health?.checked_at||state.health?.updated_at||null;const healthMins=healthTs?(Date.now()-new Date(healthTs).getTime())/60000:Infinity;const dot=$('liveDot');let label='LIVE',cls='';if(navigator.onLine===false){label='OFFLINE';cls='bad'}else if(!Number.isFinite(mins)||mins>90||healthMins>90){label='STALE';cls='bad'}else if(mins>35||healthMins>35){label='DELAYED';cls='warn'}dot.className=`dot ${cls}`;$('liveText').textContent=`${label} · ${age(ts)}`;$('updatedLabel').textContent=`Engine ${age(ts)} · market ${age(marketTs)} · pipeline ${age(healthTs)}`}`"
if "label='STALE'" not in s:
    pattern=r"function renderStatus\(\)\{.*?\}\nfunction renderAll"
    matches=list(re.finditer(pattern,s,flags=re.S))
    if len(matches)!=1:
        raise SystemExit(f'renderStatus contract changed: expected 1 block, found {len(matches)}')
    s=re.sub(pattern,new_status+'\nfunction renderAll',s,count=1,flags=re.S)

for old,new in [
    ("fetch('data/latest.json',{cache:'no-store'})","fetch('data/latest.json?v='+Date.now(),{cache:'no-store'})"),
    ("fetch('data/market-series.json',{cache:'no-store'})","fetch('data/market-series.json?v='+Date.now(),{cache:'no-store'})"),
    ("fetch('data/automation-health.json',{cache:'no-store'})","fetch('data/automation-health.json?v='+Date.now(),{cache:'no-store'})")
]:
    if old in s:s=s.replace(old,new,1)
    elif new not in s:raise SystemExit('fetch anchor changed: '+old)

marker='/* ALPHA REFRESH RESILIENCE R1 */'
if marker not in s:
    s += """\n/* ALPHA REFRESH RESILIENCE R1 */\nconst ALPHA_REFRESH_MS=5*60*1000;\nlet alphaRefreshBusy=false;\nasync function alphaRefreshNow(){\n  if(alphaRefreshBusy||document.hidden||navigator.onLine===false)return;\n  alphaRefreshBusy=true;\n  try{await load()}finally{alphaRefreshBusy=false}\n}\nsetInterval(alphaRefreshNow,ALPHA_REFRESH_MS);\ndocument.addEventListener('visibilitychange',()=>{if(!document.hidden)alphaRefreshNow()});\nwindow.addEventListener('online',alphaRefreshNow);\nwindow.addEventListener('offline',()=>{try{renderStatus()}catch(_e){}});\n"""

for required in ["label='STALE'","data/latest.json?v=","ALPHA REFRESH RESILIENCE R1","ALPHA_REFRESH_MS=5*60*1000"]:
    if required not in s: raise SystemExit('missing resilience marker '+required)

if s!=orig:
    p.write_text(s,encoding='utf-8')
    print('Alpha refresh resilience applied')
else:
    print('Alpha refresh resilience already applied')
