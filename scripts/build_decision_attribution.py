#!/usr/bin/env python3
import json
from collections import defaultdict
from pathlib import Path
from statistics import median

LATEST=Path('data/latest.json')
MEMORY=Path('data/memory.json')
OUT=Path('data/decision-attribution.json')
EXTRA_FRICTION_BPS=(5,10,20,35)


def num(v):
    try:return float(v)
    except (TypeError,ValueError):return None


def clean_forward(memory):
    out=[]
    for r in list((memory or {}).get('predictions') or []):
        if str(r.get('outcome') or '').upper() not in {'HIT','MISS'}: continue
        if not r.get('resolved') or not r.get('model_version'): continue
        rs=str(r.get('resolution_state') or '').upper()
        if any(x in rs for x in ('EXCLUDED','SUPERSEDED','DUPLICATE','AMBIGUOUS')): continue
        out.append(r)
    return out


def vote_value(v):
    if isinstance(v,dict):
        for k in ('direction','vote','signal','value','score'):
            if k in v:return vote_value(v.get(k))
        return None
    x=num(v)
    if x is not None:
        if x>0:return 1
        if x<0:return -1
        return 0
    s=str(v or '').upper().strip()
    if s in {'LONG','BUY','BULL','BULLISH','UP','POSITIVE'}:return 1
    if s in {'SHORT','SELL','BEAR','BEARISH','DOWN','NEGATIVE'}:return -1
    if s in {'NEUTRAL','HOLD','FLAT','0','NONE','NULL',''}:return 0
    return None


def relation(v,direction):
    vv=vote_value(v); final=1 if str(direction).upper()=='LONG' else -1 if str(direction).upper()=='SHORT' else None
    if vv is None or final is None:return 'UNKNOWN'
    if vv==0:return 'NEUTRAL'
    return 'ALIGNED' if vv==final else 'OPPOSED'


def row_metrics(rows):
    n=len(rows); hits=sum(str(r.get('outcome') or '').upper()=='HIT' for r in rows)
    rets=[num(r.get('return_pct')) for r in rows]; rets=[x for x in rets if x is not None]
    return {'n':n,'hit_rate':round(hits/n,4) if n else None,'avg_net_return_pct':round(sum(rets)/len(rets),4) if rets else None}


def attribution(rows):
    names=set()
    for r in rows:
        mv=r.get('model_votes') or {}
        if isinstance(mv,dict): names.update(mv.keys())
    result=[]
    for name in sorted(names):
        buckets=defaultdict(list)
        for r in rows:
            mv=r.get('model_votes') or {}
            if isinstance(mv,dict) and name in mv:buckets[relation(mv.get(name),r.get('direction'))].append(r)
        aligned=row_metrics(buckets['ALIGNED']); opposed=row_metrics(buckets['OPPOSED']); neutral=row_metrics(buckets['NEUTRAL'])
        observed=aligned['n']+opposed['n']+neutral['n']
        if observed<5: evidence='INSUFFICIENT'
        elif observed<20: evidence='OBSERVE'
        else:
            ah=aligned['hit_rate']; oh=opposed['hit_rate']
            ar=aligned['avg_net_return_pct']; orr=opposed['avg_net_return_pct']
            if ah is not None and oh is not None and ah-oh>=0.10 and (ar is None or orr is None or ar>=orr): evidence='SUPPORTIVE'
            elif ah is not None and oh is not None and oh-ah>=0.10: evidence='ADVERSE'
            else:evidence='NEUTRAL'
        result.append({'submodel':name,'observed_n':observed,'aligned':aligned,'opposed':opposed,'neutral':neutral,'evidence':evidence})
    result.sort(key=lambda x:(x['evidence']=='SUPPORTIVE',x['observed_n']),reverse=True)
    return result


def stress(rows):
    base=[num(r.get('return_pct')) for r in rows]; base=[x for x in base if x is not None]
    baseline_positive=sum(x>0 for x in base)
    tiers=[]
    for bps in EXTRA_FRICTION_BPS:
        vals=[x-bps/100 for x in base]
        positive=sum(x>0 for x in vals)
        tiers.append({'extra_friction_bps':bps,'n':len(vals),'positive_rate':round(positive/len(vals),4) if vals else None,
                      'avg_return_pct':round(sum(vals)/len(vals),4) if vals else None,
                      'median_return_pct':round(median(vals),4) if vals else None,
                      'positive_survival_ratio':round(positive/baseline_positive,4) if baseline_positive else None})
    avg0=sum(base)/len(base) if base else None
    by={x['extra_friction_bps']:x for x in tiers}
    if len(base)<20: state='INSUFFICIENT_FORWARD_SAMPLE'
    elif avg0 is not None and avg0>0 and (by[10]['avg_return_pct'] or 0)<=0: state='FRAGILE'
    elif (by[10]['avg_return_pct'] or -1)>0 and (by[20]['avg_return_pct'] or -1)<=0: state='SENSITIVE'
    elif (by[35]['avg_return_pct'] or -1)>0: state='ROBUST_TO_35BPS'
    else: state='MIXED'
    return {'state':state,'baseline_n':len(base),'baseline_avg_net_return_pct':round(avg0,4) if avg0 is not None else None,'tiers':tiers,
            'method':'Incremental friction is subtracted from already cost-adjusted forward return_pct; no spread/slippage observations are fabricated.'}


def build(latest,memory):
    rows=clean_forward(memory)
    return {'schema_version':'1.0','model_version':latest.get('model_version'),'eligible_forward_resolved':len(rows),
            'submodel_attribution':attribution(rows),'friction_stress':stress(rows),
            'policy':{'minimum_n_for_directional_attribution':20,'auto_retune':False,'capital_mode':'PAPER_RESEARCH_ONLY'},
            'note':'Attribution is diagnostic association, not causal proof. Forward evidence only; legacy rows do not drive promotion or retuning.'}


def main():
    latest=json.loads(LATEST.read_text(encoding='utf-8')); memory=json.loads(MEMORY.read_text(encoding='utf-8')) if MEMORY.exists() else {}
    out=build(latest,memory); OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(f"decision-attribution: forward={out['eligible_forward_resolved']} friction={out['friction_stress']['state']} submodels={len(out['submodel_attribution'])}")

if __name__=='__main__':main()
