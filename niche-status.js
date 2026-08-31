(()=>{
'use strict';
const fmt=ts=>{if(!ts)return'—';try{return new Intl.DateTimeFormat('it-IT',{dateStyle:'short',timeStyle:'short',timeZone:'Europe/Rome'}).format(new Date(ts))}catch{return String(ts)}};
const ageMin=ts=>{const n=ts?new Date(ts).getTime():NaN;return Number.isFinite(n)?Math.max(0,(Date.now()-n)/60000):Infinity};
function romeClock(){const p=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Rome',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).formatToParts(new Date()),v=Object.fromEntries(p.map(x=>[x.type,x.value]));return{weekday:v.weekday,minutes:Number(v.hour)*60+Number(v.minute)}}
function marketContext(){
 const c=romeClock(),weekend=['Sat','Sun'].includes(c.weekday),m=c.minutes;
 if(weekend)return{cashOpen:false,active:true,label:'CASH CHIUSO · CRYPTO ATTIVO',note:'I mercati azionari cash sono chiusi; le crypto restano attive. I prezzi cash possono restare all’ultima chiusura reale.'};
 if(m<480)return{cashOpen:false,active:true,label:'PRE-OPEN EUROPA · FX/CRYPTO ATTIVI',note:'Le azioni europee cash non sono ancora aperte. FX e crypto sono già attivi; i futures europei iniziano ad attivarsi dalla fascia mattutina.'};
 if(m<540)return{cashOpen:false,active:true,label:'PRE-OPEN EUROPA · FUTURES/FX/CRYPTO ATTIVI',note:'Il cash europeo apre alle 09:00 circa; futures, FX e crypto possono già avere nuovi prezzi.'};
 if(m<930)return{cashOpen:true,active:true,label:'EUROPA APERTA',note:'La sessione cash europea è attiva; FX, crypto e derivati possono essere attivi in parallelo.'};
 if(m<1050)return{cashOpen:true,active:true,label:'EUROPA + USA APERTI',note:'Le sessioni cash europee e statunitensi si sovrappongono.'};
 if(m<1320)return{cashOpen:true,active:true,label:'USA APERTI · EUROPA CHIUSA',note:'Il cash europeo è chiuso, mentre la sessione USA è ancora attiva.'};
 return{cashOpen:false,active:true,label:'CASH CHIUSO · FX/CRYPTO ATTIVI',note:'Le principali sessioni cash sono chiuse; FX e crypto possono continuare a muoversi.'};
}
async function getData(){try{const r=await fetch('data/latest.json?ux='+Date.now(),{cache:'no-store'});return r.ok?await r.json():null}catch{return null}}
function ensureBanner(){let box=document.getElementById('alphaNicheStatus');if(box)return box;const host=document.querySelector('.topbar');if(!host)return null;box=document.createElement('div');box.id='alphaNicheStatus';box.className='notice';box.style.margin='0 0 12px';host.insertAdjacentElement('afterend',box);return box}
function apply(d){const ctx=marketContext(),ts=d?.market_data_at||d?.updated_at,mins=ageMin(ts),box=ensureBanner(),liveText=document.getElementById('liveText'),dot=document.getElementById('liveDot'),updated=document.getElementById('updatedLabel');if(!box)return;
 const pipelineTs=d?.engine_updated_at||d?.updated_at,pipelineAge=ageMin(pipelineTs),pipelineDelayed=!Number.isFinite(pipelineAge)||pipelineAge>90;
 if(pipelineDelayed){box.style.borderColor='rgba(255,107,107,.35)';box.innerHTML=`<b>PIPELINE DA VERIFICARE</b> · ${ctx.label}. ${ctx.note} Ultimo aggiornamento motore reale: ${fmt(pipelineTs)}.`;if(liveText)liveText.textContent='PIPELINE DA VERIFICARE';if(dot)dot.className='dot bad';if(updated)updated.textContent=`Engine ${fmt(pipelineTs)} · market ${fmt(ts)}`;return}
 box.style.borderColor=ctx.cashOpen?'rgba(90,167,255,.35)':'rgba(63,211,148,.35)';box.innerHTML=`<b>${ctx.label}</b> · ${ctx.note} Ultimo dato reale disponibile: ${fmt(ts)}.`;if(liveText)liveText.textContent=ctx.label;if(dot)dot.className='dot';if(updated)updated.textContent=`Ultimo dato reale ${fmt(ts)} · engine ${fmt(pipelineTs)}`;
}
async function run(){apply(await getData())}
function start(){run();setInterval(()=>{if(!document.hidden)run()},60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)run()})}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();