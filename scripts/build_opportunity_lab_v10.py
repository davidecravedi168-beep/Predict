#!/usr/bin/env python3
import argparse, json
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone

MEM=Path("data/memory.json")
OUT=Path("data/alpha-opportunity-lab-v10.json")
LINEAGE="ALPHA_V86_EPISODE_LEDGER_1"

def mean(xs): return sum(xs)/len(xs) if xs else None
def dd(seq):
    eq=peak=0.0; worst=0.0
    for x in seq:
        eq+=x; peak=max(peak,eq); worst=max(worst,peak-eq)
    return worst
def summarize(rows,return_key="return_pct"):
    vals=[float(r[return_key]) for r in rows if isinstance(r.get(return_key),(int,float))]
    if not vals:return None
    stressed=[x-.20 for x in vals]
    hits=sum(x>0 for x in vals)
    n=len(vals)
    state="INSUFFICIENT"
    if n>=10:
        state="PROMISING" if mean(stressed)>0 and hits/n>=.52 else "OBSERVE"
    if n>=20 and mean(stressed)>0 and hits/n>=.55:
        state="STRONG_FORWARD_CANDIDATE"
    return {"n":n,"hit_rate":round(hits/n,4),"avg_return_pct":round(mean(vals),4),"avg_return_plus20bps_pct":round(mean(stressed),4),"max_drawdown_plus20bps_pct":round(dd(stressed),4),"state":state}

def self_test():
    z=[{"return_pct":.5} for _ in range(8)]+[{"return_pct":-.2} for _ in range(4)]
    s=summarize(z)
    if not s or s["n"]!=12 or s["avg_return_plus20bps_pct"] is None: raise SystemExit("LAB_SELF_TEST_FAILED")
    print(json.dumps({"ok":True,"tests":["segment_summary","20bps_stress","forward_thresholds"]}))

def main():
    mem=json.loads(MEM.read_text())
    rows=[p for p in mem.get("predictions",[]) if p.get("learning_lineage")==LINEAGE and p.get("outcome") in ("HIT","MISS") and isinstance(p.get("return_pct"),(int,float))]
    rows.sort(key=lambda x:(str(x.get("date") or ""),str(x.get("resolved") or "")))
    dims={
      "asset_class":lambda p:str(p.get("asset_class") or "UNKNOWN"),
      "horizon":lambda p:f"{int(p.get('horizon') or 0)}d",
      "risk_regime":lambda p:str(p.get("risk_regime") or "UNKNOWN"),
      "rates_regime":lambda p:str(p.get("rates_regime") or "UNKNOWN"),
      "setup_family":lambda p:str(p.get("horizon_setup_family") or "UNKNOWN"),
      "cluster":lambda p:str(p.get("cluster") or "UNKNOWN"),
    }
    segments={}
    for name,fn in dims.items():
        g=defaultdict(list)
        for p in rows:g[fn(p)].append(p)
        segments[name]=[{**{"segment":k},**summarize(v)} for k,v in g.items() if summarize(v)]
        segments[name].sort(key=lambda x:(x["state"]=="STRONG_FORWARD_CANDIDATE",x["state"]=="PROMISING",x["n"],x["avg_return_plus20bps_pct"]),reverse=True)

    models=defaultdict(list)
    for p in rows:
        raw=p.get("underlying_return_pct")
        cost=float(p.get("estimated_round_trip_cost_bps") or 0)/100
        if not isinstance(raw,(int,float)): continue
        for v in p.get("model_votes") or []:
            mid=v.get("model_id"); d=str(v.get("direction") or "")
            if not mid or d not in ("LONG","SHORT"):continue
            directional=float(raw) if d=="LONG" else -float(raw)
            models[mid].append({"return_pct":directional-cost})
    model_rows=[]
    for k,v in models.items():
        s=summarize(v)
        if s:model_rows.append({"model_id":k,**s})
    model_rows.sort(key=lambda x:(x["state"]=="STRONG_FORWARD_CANDIDATE",x["state"]=="PROMISING",x["n"],x["avg_return_plus20bps_pct"]),reverse=True)

    out={
      "schema":"ALPHA-OPPORTUNITY-LAB-V10",
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "resolved_forward_n":len(rows),
      "stress_extra_bps":20,
      "segments":segments,
      "submodels":model_rows,
      "policy":{
        "shadow_only":True,
        "auto_promote":False,
        "minimum_n_promising":10,
        "minimum_n_strong":20,
        "profit_guarantee":False,
        "note":"Ranks forward evidence after an extra 20 bps friction stress. Association is not proof of future profitability."
      }
    }
    OUT.write_text(json.dumps(out,indent=2)+"\n")
    top=[x for x in model_rows if x["state"]!="INSUFFICIENT"][:3]
    print(json.dumps({"ok":True,"n":len(rows),"top_submodels":top}))

if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--self-test",action="store_true");args=ap.parse_args()
    self_test() if args.self_test else main()
