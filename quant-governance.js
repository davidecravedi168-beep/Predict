(()=>{
  const fmtPct=v=>Number.isFinite(Number(v))?`${(Number(v)*100).toLocaleString('it-IT',{maximumFractionDigits:1})}%`:'—';
  const fmtNum=(v,d=2)=>Number.isFinite(Number(v))?Number(v).toLocaleString('it-IT',{maximumFractionDigits:d}):'—';
  const text=(el,s)=>{el.textContent=s;return el};
  const node=(tag,cls,s='')=>text(Object.assign(document.createElement(tag),{className:cls}),s);

  function card(k,v,n){
    const c=node('div','qgCard');c.append(node('div','qgK',k),node('div','qgV',v),node('div','qgN',n));return c;
  }

  function renderDrift(section,fs){
    const dm=fs?.drift_monitor;
    if(!dm)return;
    const wrap=node('div','qgDrift');
    const counts=dm.state_counts||{};
    const state=counts.DRIFT?'DRIFT':counts.WATCH?'WATCH':counts.STABLE?'STABLE':'WAIT';
    const head=node('div','qgDriftHead');
    head.append(node('b','',`Temporal stability · ${state}`),node('span','',`${dm.segments_evaluated??0} segmenti valutati`));
    wrap.append(head);
    const alerts=(dm.alerts||[]).slice(0,3);
    if(!alerts.length){
      wrap.append(node('div','qgSegEmpty',dm.segments_evaluated?'Nessun deterioramento rolling rilevato.':'Servono almeno due finestre da 5 esiti per misurare drift.'));
    }else{
      const list=node('div','qgSegList');
      for(const a of alerts){
        const row=node('div',`qgSegRow qgDrift-${String(a.state||'WATCH').toLowerCase()}`);
        const left=node('div','qgSegName');
        left.append(node('b','',`${a.dimension} · ${a.segment}`),node('span','',`${a.state} · n=${a.n} · finestre ${a.prior_n}/${a.recent_n}`));
        const right=node('div','qgSegMetrics');
        right.append(
          node('b','',`Δ hit ${fmtPct(a.hit_rate_delta)}`),
          node('span','',`Δ return ${fmtNum(a.avg_return_delta_pct,2)} pp · Δ Brier ${fmtNum(a.brier_delta,3)}`)
        );
        row.append(left,right);list.append(row);
      }
      wrap.append(list);
    }
    wrap.append(node('div','qgSegPolicy','Drift = revisione obbligatoria, non auto-retuning: Alpha non modifica pesi o soglie da solo.'));
    section.append(wrap);
  }

  function renderSegments(box,g){
    const fs=g.forward_segments;
    if(!fs)return;
    const section=node('div','qgSegments');
    const head=node('div','qgSegHead');
    head.append(node('b','','Forward segment lab'),node('span','',`${fs.eligible_forward_resolved??0} esiti eleggibili · min n=${fs.minimum_n_to_display??5}`));
    section.append(head);
    const ranked=(fs.ranked_evidence||[]).slice(0,4);
    if(!ranked.length){
      section.append(node('div','qgSegEmpty','Campione segmentato ancora insufficiente: nessun ranking viene forzato.'));
    }else{
      const list=node('div','qgSegList');
      for(const s of ranked){
        const row=node('div','qgSegRow');
        const left=node('div','qgSegName');
        left.append(node('b','',`${s.dimension} · ${s.segment}`),node('span','',`n=${s.n} · ${s.evidence} · drift ${s.drift?.state||'—'}`));
        const right=node('div','qgSegMetrics');
        right.append(
          node('b','',fmtPct(s.hit_rate)),
          node('span','',`LB95 ${fmtPct(s.wilson_lower_95)} · avg ${fmtNum(s.avg_return_pct_after_costs,2)}% · DD ${fmtNum(s.max_drawdown_pct_flat_sequence,2)}%`)
        );
        row.append(left,right);list.append(row);
      }
      section.append(list);
    }
    renderDrift(section,fs);
    section.append(node('div','qgSegPolicy','Diagnostica soltanto: i segmenti non modificano automaticamente pesi, soglie o capitale.'));
    box.append(section);
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
    renderSegments(box,g);

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
