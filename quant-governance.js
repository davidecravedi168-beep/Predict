(()=>{
  const fmtPct=v=>Number.isFinite(Number(v))?`${(Number(v)*100).toLocaleString('it-IT',{maximumFractionDigits:1})}%`:'—';
  const fmtNum=(v,d=2)=>Number.isFinite(Number(v))?Number(v).toLocaleString('it-IT',{maximumFractionDigits:d}):'—';
  const text=(el,s)=>{el.textContent=s;return el};
  const node=(tag,cls,s='')=>text(Object.assign(document.createElement(tag),{className:cls}),s);

  function card(k,v,n){
    const c=node('div','qgCard');c.append(node('div','qgK',k),node('div','qgV',v),node('div','qgN',n));return c;
  }

  function render(g){
    const home=document.getElementById('page-home');
    if(!home||document.getElementById('quantGovernance'))return;
    const box=node('section',`qgPanel qg-${String(g.status||'CAUTION').toLowerCase()}`);box.id='quantGovernance';
    const head=node('div','qgHead');
    const title=node('div','qgTitle');title.append(node('b','',`Quant governance · ${g.status||'—'}`),node('span','',g.promotion_state||'RESEARCH_ONLY'));
    head.append(title,node('div','qgBadge',g.forward?.maturity||'—'));
    box.append(head);

    const grid=node('div','qgGrid');
    grid.append(
      card('Forward',`${g.forward?.resolved??0} chiusi`,`${g.forward?.active??0} attivi · Brier n=${g.forward?.brier_n??0}`),
      card('Dati',fmtPct(g.data_quality?.availability_ratio),`fresh ${fmtNum(g.data_quality?.freshness_minutes,0)} min`),
      card('Completezza',fmtPct(g.data_quality?.average_model_completeness),`min ${fmtPct(g.data_quality?.minimum_model_completeness)}`),
      card('Paper risk cap',`${fmtNum(g.policy?.paper_risk_unit_cap,2)}R`,'mai auto-promosso a capitale reale')
    );
    box.append(grid);

    const flags=[...(g.blockers||[]),...(g.flags||[])];
    const note=node('div','qgNote',flags.length?`Controlli: ${flags.slice(0,4).join(' · ')}${flags.length>4?` · +${flags.length-4}`:''}`:'Nessun flag quantitativo attivo.');
    box.append(note);
    const anchor=home.querySelector('.summaryGrid');
    if(anchor)home.insertBefore(box,anchor);else home.prepend(box);
  }

  async function load(){
    try{
      const r=await fetch('data/quant-governance.json',{cache:'no-store'});
      if(!r.ok)throw Error(`HTTP ${r.status}`);
      render(await r.json());
    }catch(e){
      console.warn('quant-governance unavailable',e);
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',load,{once:true});else load();
})();
