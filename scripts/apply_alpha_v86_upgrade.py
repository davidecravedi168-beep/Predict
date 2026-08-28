#!/usr/bin/env python3
# One-shot/idempotent Alpha Engine v8.6 episode-ledger migration.
# Patches only when expected v8.5 anchors are present; fails closed otherwise.
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine.py"
INDEX = ROOT / "index.html"

def replace_exact(text, old, new, label):
    n = text.count(old)
    if n == 0:
        if new in text:
            return text, False
        raise RuntimeError(f"{label}: anchor not found")
    if n != 1:
        raise RuntimeError(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1), True

def replace_regex(text, pattern, repl, label):
    new_text, n = re.subn(pattern, repl, text, count=1, flags=re.S)
    if n == 0:
        marker = repl.splitlines()[0].strip()
        if marker and marker in text:
            return text, False
        raise RuntimeError(f"{label}: regex anchor not found")
    return new_text, True

def patch_engine(text):
    changed = False
    pairs = [
        ('MODEL_VERSION = "8.5.0-cost-aware-edge-core"', 'MODEL_VERSION = "8.6.0-episode-ledger-cost-aware"', "engine version"),
        ('LEARNING_LINEAGE = "ALPHA_V85_COST_AWARE_1"', 'LEARNING_LINEAGE = "ALPHA_V86_EPISODE_LEDGER_1"', "learning lineage"),
        ('"schema_version": "8.5"', '"schema_version": "8.6"', "schema version"),
        ('"signal_lock": "IMMUTABLE_MODEL_VERSION_DATE_TICKER_DIRECTION_HORIZON"', '"signal_lock": "ONE_ACTIVE_EPISODE_PER_TICKER_WITH_STABLE_SETUP_KEY"', "signal lock"),
        ('mem = resolve_memory(load_memory(), data)', 'mem = resolve_memory(migrate_prediction_episodes(load_memory()), data)', "memory migration call"),
    ]
    for old, new, label in pairs:
        text, c = replace_exact(text, old, new, label)
        changed |= c

    old_skip = 'if p.get("outcome") in ("HIT", "MISS"):\n            continue'
    new_skip = 'if p.get("outcome") in ("HIT", "MISS", "SUPERSEDED", "EXPIRED", "CANCELLED", "EXCLUDED_DUPLICATE"):\n            continue'
    text, c = replace_exact(text, old_skip, new_skip, "terminal outcomes")
    changed |= c

    old_resolved = 'resolved = [p for p in mem.get("predictions", []) if p.get("outcome") in ("HIT", "MISS")]'
    new_resolved = '''resolved = [
        p for p in mem.get("predictions", [])
        if p.get("outcome") in ("HIT", "MISS")
        and p.get("learning_lineage") == LEARNING_LINEAGE
    ]
    legacy_resolved = [
        p for p in mem.get("predictions", [])
        if p.get("outcome") in ("HIT", "MISS")
        and p.get("learning_lineage") != LEARNING_LINEAGE
    ]'''
    text, c = replace_exact(text, old_resolved, new_resolved, "clean-lineage resolved stats")
    changed |= c

    new_add = r'''def _episode_key_from_parts(ticker, direction, horizon, setup_family):
    return f"{ticker}|{direction}|{int(horizon)}|{setup_family or 'MIXED'}"


def _episode_key_from_prediction(p):
    h = safe_num(p.get("horizon"))
    if h is None or int(h) != h or int(h) < 1:
        return None
    ticker = str(p.get("ticker") or "")
    direction = str(p.get("direction") or "")
    if not ticker or direction not in ("LONG", "SHORT"):
        return None
    return _episode_key_from_parts(
        ticker, direction, int(h), p.get("horizon_setup_family") or "MIXED"
    )


def migrate_prediction_episodes(mem):
    # Fail-closed migration for legacy overlapping PENDING predictions.
    rows = mem.setdefault("predictions", [])
    pending = []
    for p in rows:
        if p.get("outcome") != "PENDING":
            continue
        key = _episode_key_from_prediction(p)
        if key:
            p.setdefault("episode_key", key)
            p.setdefault("episode_id", f"EP|{p.get('date')}|{key}")
        p.setdefault("observations", 1)
        p.setdefault("state", "ACTIVE")
        pending.append(p)

    by_key = defaultdict(list)
    for p in pending:
        key = p.get("episode_key")
        if key:
            by_key[key].append(p)
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda x: (str(x.get("date") or ""), str(x.get("id") or "")))
        canonical = ordered[0]
        for dup in ordered[1:]:
            if dup.get("outcome") != "PENDING":
                continue
            canonical["observations"] = max(
                int(canonical.get("observations", 1) or 1),
                int(dup.get("observations", 1) or 1),
            )
            canonical["last_seen"] = max(str(canonical.get("last_seen") or canonical.get("date") or ""), str(dup.get("date") or ""))
            dup["outcome"] = "EXCLUDED_DUPLICATE"
            dup["state"] = "SUPERSEDED"
            dup["superseded_by"] = canonical.get("id")
            dup["resolution_state"] = "LEGACY_DUPLICATE_EXCLUDED_FROM_STATS"

    by_ticker = defaultdict(list)
    for p in rows:
        if p.get("outcome") == "PENDING" and p.get("ticker"):
            by_ticker[p["ticker"]].append(p)
    for ticker, group in by_ticker.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda x: (str(x.get("date") or ""), str(x.get("id") or "")))
        newest = ordered[-1]
        for old in ordered[:-1]:
            old["outcome"] = "SUPERSEDED"
            old["state"] = "SUPERSEDED"
            old["superseded_by"] = newest.get("id")
            old["resolution_state"] = "LEGACY_OVERLAP_SUPERSEDED"
        newest["state"] = "ACTIVE"
    return mem


def add_prediction(mem, sig):
    # Create or refresh one independent active forecast episode per ticker.
    ledger = mem.setdefault("predictions", [])
    h = int(sig["horizon"])
    setup = sig.get("horizon_setup_family") or "MIXED"
    episode_key = _episode_key_from_parts(sig["ticker"], sig["direction"], h, setup)

    active = [
        p for p in ledger
        if p.get("outcome") == "PENDING" and p.get("ticker") == sig["ticker"]
    ]
    for p in active:
        existing_key = p.get("episode_key") or _episode_key_from_prediction(p)
        if existing_key == episode_key:
            p["episode_key"] = episode_key
            p.setdefault("episode_id", f"EP|{p.get('date')}|{episode_key}")
            p["state"] = "ACTIVE"
            p["last_seen"] = sig["date"]
            p["observations"] = int(p.get("observations", 1) or 1) + 1
            p["latest_confidence_pct"] = sig.get("confidence_pct")
            p["latest_learning_adjustment"] = sig.get("learning_adjustment", 0)
            p["latest_model_version"] = MODEL_VERSION
            return {"action": "UPDATED_ACTIVE_EPISODE", "episode_id": p["episode_id"]}

    for p in active:
        p["outcome"] = "SUPERSEDED"
        p["state"] = "SUPERSEDED"
        p["superseded_on"] = sig["date"]
        p["resolution_state"] = "SUPERSEDED_BY_NEW_ACTIVE_THESIS"

    pid = f'EP|{sig["date"]}|{episode_key}|{MODEL_VERSION}'
    row = {
        "id": pid,
        "episode_id": pid,
        "episode_key": episode_key,
        "state": "ACTIVE",
        "observations": 1,
        "first_seen": sig["date"],
        "last_seen": sig["date"],
        "model_version": MODEL_VERSION,
        "learning_lineage": LEARNING_LINEAGE,
        "date": sig["date"],
        "ticker": sig["ticker"],
        "sector": sig.get("sector"),
        "asset_class": sig.get("asset_class"),
        "cluster": sig.get("cluster"),
        "role": sig.get("role"),
        "direction": sig["direction"],
        "horizon": sig["horizon"],
        "horizon_state": sig.get("horizon_state"),
        "horizon_setup_family": sig.get("horizon_setup_family"),
        "horizon_policy_version": sig.get("horizon_policy_version"),
        "confidence_pct": sig.get("confidence_pct"),
        "forecast_probability": sig.get("forecast_probability"),
        "probability_state": sig.get("probability_state"),
        "entry": sig.get("entry_price", sig.get("price")),
        "entry_source": (sig.get("provenance") or {}).get("entry_price"),
        "risk_pct": sig.get("risk_pct"),
        "stop": sig.get("stop_price"),
        "target1": sig.get("target1_price"),
        "target2": sig.get("target2_price"),
        "data_quality_score": sig.get("data_quality_score"),
        "model_completeness_score": sig.get("model_completeness_score"),
        "model_votes": sig.get("model_votes", []),
        "external_model_votes": sig.get("external_model_votes", []),
        "learning_adjustment": sig.get("learning_adjustment", 0),
        "risk_regime": sig.get("risk_regime"),
        "rates_regime": sig.get("rates_regime"),
        "outcome": "PENDING",
    }
    ledger.append(row)
    return {"action": "CREATED_NEW_EPISODE", "episode_id": pid}
'''
    pattern = r'def add_prediction\(mem, sig\):.*?\n\ndef _segment'
    repl = new_add + "\n\ndef _segment"
    text, c = replace_regex(text, pattern, repl, "episode-aware add_prediction")
    changed |= c

    old_stats = '''    mem["stats"] = {
        "total": len(mem.get("predictions", [])),
        "resolved": len(resolved),'''
    new_stats = '''    lineage_rows = [p for p in mem.get("predictions", []) if p.get("learning_lineage") == LEARNING_LINEAGE]
    active_rows = [p for p in lineage_rows if p.get("outcome") == "PENDING"]
    superseded_rows = [p for p in mem.get("predictions", []) if p.get("outcome") in ("SUPERSEDED", "EXCLUDED_DUPLICATE")]
    mem["stats"] = {
        "total": len(lineage_rows),
        "independent_episodes": len(lineage_rows),
        "active": len(active_rows),
        "superseded_or_excluded": len(superseded_rows),
        "legacy_resolved_excluded": len(legacy_resolved),
        "resolved": len(resolved),'''
    text, c = replace_exact(text, old_stats, new_stats, "clean stats counters")
    changed |= c

    anchor = '''    edge, edge_reasons = historical_edge(mem, asset_class, direction, horizon)
    reasons = []'''
    completeness = '''    model_completeness_fields = {
        "market_feature_set": data_quality >= 80,
        "risk_envelope": stop is not None and target1 is not None and target2 is not None,
        "empirical_probability": forecast_p is not None,
        "learning_segment_mature": learn_meta.get("state") not in (
            "INSUFFICIENT_SAMPLE", "INSUFFICIENT_CALIBRATION_SAMPLE", "NO_CONFIDENCE_INDEX"
        ),
        "external_model_evidence": bool(ext_votes),
    }
    model_completeness_weights = {
        "market_feature_set": 40,
        "risk_envelope": 20,
        "empirical_probability": 15,
        "learning_segment_mature": 15,
        "external_model_evidence": 10,
    }
    model_completeness_score = sum(
        model_completeness_weights[k]
        for k, ok in model_completeness_fields.items() if ok
    )
    decision_reliability_state = (
        "CALIBRATION_LIMITED" if forecast_p is None
        else "MODEL_COMPLETE" if model_completeness_score >= 80
        else "PARTIAL_MODEL_EVIDENCE"
    )

    edge, edge_reasons = historical_edge(mem, asset_class, direction, horizon)
    reasons = []'''
    text, c = replace_exact(text, anchor, completeness, "model completeness")
    changed |= c

    return_anchor = '''        "data_quality_score": data_quality,
        "data_quality_fields": expected,
        "model_consensus": round(consensus, 3),'''
    return_new = '''        "data_quality_score": data_quality,
        "data_quality_fields": expected,
        "model_completeness_score": model_completeness_score,
        "model_completeness_fields": model_completeness_fields,
        "decision_reliability_state": decision_reliability_state,
        "model_consensus": round(consensus, 3),'''
    text, c = replace_exact(text, return_anchor, return_new, "completeness output")
    changed |= c

    return text, changed


def patch_index(text):
    changed = False

    anchor = "const LEGACY_JOURNAL_KEYS=['alpha_decision_journal_v44'];"
    insert = '''const LEGACY_JOURNAL_KEYS=['alpha_decision_journal_v44'];
const JOURNAL_TERMINAL=new Set(['TARGET1','TARGET2','STOP','AMBIGUO','SUPERSEDED','EXPIRED']);
function journalEpisodeKey(z){return [z?.ticker||'',z?.direction||'',safeNum(z?.horizon,null)??'NA',z?.horizon_setup_family||'MIXED'].join('|')}
function compactJournalEpisodes(rows){
  const sorted=(Array.isArray(rows)?rows:[]).filter(Boolean).map(z=>({...z})).sort((a,b)=>safeNum(a.ts,0)-safeNum(b.ts,0));
  const out=[],activeByKey=new Map();
  for(const z of sorted){
    if(JOURNAL_TERMINAL.has(z.status)){out.push(z);continue}
    const key=journalEpisodeKey(z),prev=activeByKey.get(key);
    if(prev){
      const pe=safeNum(prev.price,null),ze=safeNum(z.price,null);
      prev.episode_id=prev.episode_id||prev.id;
      prev.episode_key=key;
      prev.last_seen_at=z.observed_at||z.market_data_at||z.updated_at||prev.last_seen_at;
      prev.last_model_version=z.model_version||prev.last_model_version||prev.model_version;
      prev.latest_confidence=safeNum(z.confidence,prev.latest_confidence);
      prev.observed_high=Math.max(safeNum(prev.observed_high,pe??-Infinity),safeNum(z.observed_high,ze??-Infinity));
      prev.observed_low=Math.min(safeNum(prev.observed_low,pe??Infinity),safeNum(z.observed_low,ze??Infinity));
      if(Number.isFinite(safeNum(z.last_price,null)))prev.last_price=z.last_price;
      if(z.observed_at)prev.observed_at=z.observed_at;
      prev.observations=Math.max(safeNum(prev.observations,0),safeNum(z.observations,0));
      prev.duplicate_snapshots=safeNum(prev.duplicate_snapshots,0)+1;
      continue;
    }
    z.episode_id=z.episode_id||z.id;
    z.episode_key=key;
    z.first_seen_at=z.first_seen_at||z.updated_at||new Date(safeNum(z.ts,Date.now())).toISOString();
    z.last_seen_at=z.last_seen_at||z.observed_at||z.market_data_at||z.updated_at||z.first_seen_at;
    activeByKey.set(key,z);out.push(z);
  }
  const byTicker=new Map();
  out.forEach(z=>{if(!JOURNAL_TERMINAL.has(z.status)&&z.ticker){const a=byTicker.get(z.ticker)||[];a.push(z);byTicker.set(z.ticker,a)}});
  for(const group of byTicker.values()){
    if(group.length<2)continue;
    group.sort((a,b)=>safeNum(a.ts,0)-safeNum(b.ts,0));
    const newest=group[group.length-1];
    group.slice(0,-1).forEach(old=>{old.status='SUPERSEDED';old.superseded_by=newest.episode_id||newest.id;old.resolved_at=newest.first_seen_at||newest.updated_at||new Date().toISOString()});
  }
  return out.sort((a,b)=>safeNum(a.ts,0)-safeNum(b.ts,0));
}'''
    text, c = replace_exact(text, anchor, insert, "journal compactor")
    changed |= c

    text, c = replace_exact(text, "return Array.isArray(x)?x:[];", "return Array.isArray(x)?compactJournalEpisodes(x):[];", "journal read compaction")
    changed |= c
    text, c = replace_exact(text, "if(['TARGET2','TARGET1','STOP','AMBIGUO'].includes(z.status)) continue;", "if(JOURNAL_TERMINAL.has(z.status)) continue;", "journal terminal skip")
    changed |= c
    text, c = replace_exact(text, "if(z.status!=='OPEN') z.resolved_at=z.observed_at;", "if(['TARGET1','TARGET2','STOP','AMBIGUO'].includes(z.status)) z.resolved_at=z.observed_at;", "journal resolved timestamp")
    changed |= c

    text, c = replace_exact(text, "const payload={format:'ALPHA-DECISION-JOURNAL',version:'8.5',exported_at:new Date().toISOString(),journal:j};", "const payload={format:'ALPHA-DECISION-JOURNAL',version:'8.6',exported_at:new Date().toISOString(),journal:compactJournalEpisodes(j)};", "journal export schema")
    changed |= c
    text, c = replace_exact(text, "a.download='alpha-v8-decision-journal-'+", "a.download='alpha-v86-decision-journal-'+", "journal export filename")
    changed |= c

    text, c = replace_exact(text, "status:['OPEN','OBSERVE_ONLY','TARGET1','TARGET2','STOP','AMBIGUO'].includes(z.status)?z.status:'OPEN'", "status:['OPEN','OBSERVE_ONLY','TARGET1','TARGET2','STOP','AMBIGUO','SUPERSEDED','EXPIRED'].includes(z.status)?z.status:'OPEN'", "import statuses")
    changed |= c
    text, c = replace_exact(text, "saveJournal([...map.values()].sort((a,b)=>safeNum(a.ts,0)-safeNum(b.ts,0))); updateJournalObservations(); renderJournal();", "saveJournal(compactJournalEpisodes([...map.values()])); updateJournalObservations(); renderJournal();", "import compaction")
    changed |= c

    snapshot_pattern = r'function snapshotSignals\(\)\{.*?\}\nfunction calcNow\(\)\{'
    snapshot_new = r'''function snapshotSignals(){
  if(!D)return;
  let original=getJournal(),j=compactJournalEpisodes(original),changed=JSON.stringify(j)!==JSON.stringify(original);
  const now=Date.now(),pool=[...(D.signals||[])];
  for(const x of pool){
    const de=x.decision_engine||{};if(!x.ticker||!x.direction)continue;
    const key=[x.ticker,x.direction,safeNum(x.horizon,null)??'NA',x.horizon_setup_family||'MIXED'].join('|');
    const activeSame=j.find(z=>!JOURNAL_TERMINAL.has(z.status)&&journalEpisodeKey(z)===key);
    if(activeSame){
      activeSame.last_seen_at=D.market_data_at||D.updated_at||new Date().toISOString();
      activeSame.last_model_version=D.model_version||x.model_version||activeSame.model_version;
      activeSame.latest_confidence=safeNum(de.final_confidence,activeSame.latest_confidence);
      changed=true;continue;
    }
    const priorActive=j.filter(z=>!JOURNAL_TERMINAL.has(z.status)&&z.ticker===x.ticker);
    const episodeId='EP_'+x.ticker+'_'+x.direction+'_'+now;
    for(const old of priorActive){old.status='SUPERSEDED';old.superseded_by=episodeId;old.resolved_at=D.market_data_at||D.updated_at||new Date().toISOString();changed=true}
    const px=safeNum(x.entry_price,safeNum(x.price,null)),levels=safeNum(x.stop_price,null)!=null&&safeNum(x.target1_price,null)!=null;
    j.push({id:episodeId,episode_id:episodeId,episode_key:key,ts:now,first_seen_at:D.updated_at||new Date().toISOString(),last_seen_at:D.market_data_at||D.updated_at||new Date().toISOString(),updated_at:D.updated_at||null,market_data_at:D.market_data_at||null,model_version:D.model_version||x.model_version||'unknown',learning_lineage:D.model_learning?.learning_lineage||null,ticker:x.ticker,direction:x.direction,asset_class:inferAssetClass(x),currency:quoteCurrency(x),price:px,score:safeNum(x.score,null),thesis:safeNum(de.thesis_score,null),contra_risk:safeNum(de.contra_risk,null),confidence:safeNum(de.final_confidence,null),concentration_penalty:safeNum(de.concentration_penalty,0),learning_adjustment:safeNum(de.learning_adjustment,0),data_quality:safeNum(de.data_quality_score,null),model_completeness:safeNum(x.model_completeness_score,null),model_votes:Array.isArray(x.model_votes)?x.model_votes:[],provenance:x.provenance||{},verdict:de.arbiter||null,priority:signalPriority(x),regime:regimeForAsset(x),cluster:x.cluster||inferCluster(x),risk:x.risk_level||null,risk_pct:safeNum(x.risk_pct,null),horizon:safeNum(x.horizon,null),horizon_state:x.horizon_state||null,horizon_setup_family:x.horizon_setup_family||null,stop:safeNum(x.stop_price,null),target1:safeNum(x.target1_price,null),target2:safeNum(x.target2_price,null),status:levels?'OPEN':'OBSERVE_ONLY',observations:0,observed_high:px,observed_low:px,last_price:px,current_return_pct:0,mfe_pct:0,mae_pct:0,r_multiple:0});
    changed=true;
  }
  if(changed)saveJournal(compactJournalEpisodes(j));
}
function calcNow(){'''
    text, c = replace_regex(text, snapshot_pattern, snapshot_new, "stable frontend episodes")
    changed |= c

    text, c = replace_exact(text, "data_quality_score:dq,data_quality_adjustment:dqAdj", "data_quality_score:dq,model_completeness_score:safeNum(x.model_completeness_score,null),decision_reliability_state:x.decision_reliability_state||null,data_quality_adjustment:dqAdj", "frontend completeness model")
    changed |= c

    old_box = '<div class="duelbox"><div class="lbl">Data quality</div><b>${de.data_quality_score??\'—\'}/100</b></div>'
    new_box = old_box + '<div class="duelbox"><div class="lbl">Completezza modello</div><b>${de.model_completeness_score??\'—\'}/100</b></div>'
    text, c = replace_exact(text, old_box, new_box, "completeness UI")
    changed |= c

    old_test = '''    const j=getJournal();
    add('Journal / memoria locale',ok?'PASS':'FAIL',ok?(j.length+' snapshot salvati'):'localStorage non scrivibile');'''
    new_test = '''    const j=getJournal();
    add('Journal / memoria locale',ok?'PASS':'FAIL',ok?(j.length+' episodi salvati'):'localStorage non scrivibile');
    const activeJ=j.filter(z=>!JOURNAL_TERMINAL.has(z.status)), activeTickerKeys=activeJ.map(z=>z.ticker);
    add('Journal Episode Guard',new Set(activeTickerKeys).size===activeTickerKeys.length?'PASS':'FAIL',new Set(activeTickerKeys).size===activeTickerKeys.length?'un solo episodio attivo per asset':'episodi attivi sovrapposti');'''
    text, c = replace_exact(text, old_test, new_test, "system test episode guard")
    changed |= c

    for old, new in [
        ("j.length+' snapshot'", "j.length+' episodi'"),
        ("'alpha_last_data_v85'", "'alpha_last_data_v86'"),
        ("./data/latest.json?build=v85edgecore", "./data/latest.json?build=v86episodeledger"),
    ]:
        if old in text:
            text = text.replace(old, new)
            changed = True

    return text, changed


def main():
    engine = ENGINE.read_text(encoding="utf-8")
    index = INDEX.read_text(encoding="utf-8")
    engine2, ce = patch_engine(engine)
    index2, ci = patch_index(index)

    required_engine = [
        'MODEL_VERSION = "8.6.0-episode-ledger-cost-aware"',
        "def migrate_prediction_episodes(mem):",
        "UPDATED_ACTIVE_EPISODE",
        '"model_completeness_score": model_completeness_score',
        '"schema_version": "8.6"',
    ]
    required_index = [
        "function compactJournalEpisodes(rows)",
        "Journal Episode Guard",
        "version:'8.6'",
        "v86episodeledger",
        "Completezza modello",
    ]
    for marker in required_engine:
        if marker not in engine2:
            raise RuntimeError(f"engine verification failed: {marker}")
    for marker in required_index:
        if marker not in index2:
            raise RuntimeError(f"index verification failed: {marker}")
    if "<12*3600*1000" in index2:
        raise RuntimeError("legacy 12-hour snapshot duplication gate still present")

    if ce:
        ENGINE.write_text(engine2, encoding="utf-8")
    if ci:
        INDEX.write_text(index2, encoding="utf-8")
    print(f"Alpha v8.6 migration complete: engine_changed={ce} index_changed={ci}")

if __name__ == "__main__":
    main()
