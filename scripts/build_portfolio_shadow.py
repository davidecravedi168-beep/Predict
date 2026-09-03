#!/usr/bin/env python3
import json
from collections import Counter, defaultdict
from pathlib import Path

LATEST=Path('data/latest.json')
MEMORY=Path('data/memory.json')
OUT=Path('data/portfolio-shadow.json')


def f(v):
    try:return float(v)
    except (TypeError,ValueError):return None

def share(counter,total):
    return {k:round(v/total,4) for k,v in counter.items()} if total else {}

def portfolio_risk(d):
    sig=list(d.get('signals') or [])
    n=len(sig)
    dims={k:Counter(str(x.get(k) or 'UNKNOWN') for x in sig) for k in ('asset_class','cluster','direction','currency')}
    flags=[]
    limits={'cluster':0.40,'asset_class':0.45,'direction':0.75,'currency':0.75}
    exposures={}
    for k,c in dims.items():
        s=share(c,n); top=max(s.items(),key=lambda x:x[1]) if s else (None,0)
        exposures[k]={'counts':dict(c),'shares':s,'top':top[0],'top_share':top[1]}
        if top[1]>limits[k]: flags.append(f'{k.upper()}_CONCENTRATION')
    # proxy factor buckets: deliberately transparent, no fabricated covariance matrix
    factors=Counter()
    for x in sig:
        ac=str(x.get('asset_class') or '')
        direction=str(x.get('direction') or 'UNKNOWN')
        cluster=str(x.get('cluster') or '')
        if ac in {'EQUITY','ETF_EQUITY','INDEX_FUTURE'}: factors['EQUITY_BETA']+=1
        if ac in {'BTP','ETF_BOND_GOV','ETF_BOND_CREDIT'}: factors['DURATION_RATES']+=1
        if ac in {'ETF_COMMODITY'} or 'COMMOD' in cluster: factors['COMMODITY']+=1
        if str(x.get('currency') or '')=='USD': factors['USD_DENOMINATED']+=1
        factors[f'DIRECTION_{direction}']+=1
    factor_shares=share(factors,n)
    if factor_shares.get('DURATION_RATES',0)>0.50: flags.append('DURATION_FACTOR_CONCENTRATION')
    if factor_shares.get('EQUITY_BETA',0)>0.60: flags.append('EQUITY_BETA_CONCENTRATION')
    status='ATTENTION' if flags else 'HEALTHY'
    return {'status':status,'signal_count':n,'exposures':exposures,'factor_proxy_shares':factor_shares,'flags':flags,
            'policy':'Exposure diagnostics use transparent proxy buckets. No covariance/correlation is fabricated without adequate synchronized return history.'}

def clean(rows):
    out=[]
    for r in rows:
        if str(r.get('outcome') or '').upper() not in {'HIT','MISS'} or not r.get('resolved'):continue
        if any(x in str(r.get('resolution_state') or '').upper() for x in ('EXCLUDED','SUPERSEDED','DUPLICATE','AMBIGUOUS')):continue
        if not r.get('model_version'):continue
        out.append(r)
    return out

def metrics(rows,prefix=''):
    n=len(rows); hits=sum(str(r.get('outcome') or '').upper()=='HIT' for r in rows)
    rets=[f(r.get(prefix+'return_pct')) for r in rows]; rets=[x for x in rets if x is not None]
    probs=[(f(r.get(prefix+'forecast_probability')),1 if str(r.get('outcome') or '').upper()=='HIT' else 0) for r in rows]
    probs=[x for x in probs if x[0] is not None]
    return {'n':n,'hit_rate':round(hits/n,4) if n else None,'avg_return_pct':round(sum(rets)/len(rets),4) if rets else None,
            'brier':round(sum((p-y)**2 for p,y in probs)/len(probs),6) if probs else None,'brier_n':len(probs)}

def champion_shadow(memory):
    rows=clean(list((memory or {}).get('predictions') or []))
    champion=metrics(rows)
    # Shadow fields are opt-in immutable forecast-time fields. Absence means no comparison, never synthesized.
    shadow_rows=[r for r in rows if r.get('shadow_model_version') and (r.get('shadow_outcome') or r.get('shadow_return_pct') is not None or r.get('shadow_forecast_probability') is not None)]
    shadow_n=len(shadow_rows)
    shadow_hits=sum(str(r.get('shadow_outcome') or '').upper()=='HIT' for r in shadow_rows)
    srets=[f(r.get('shadow_return_pct')) for r in shadow_rows]; srets=[x for x in srets if x is not None]
    sprobs=[]
    for r in shadow_rows:
        p=f(r.get('shadow_forecast_probability')); o=str(r.get('shadow_outcome') or '').upper()
        if p is not None and o in {'HIT','MISS'}:sprobs.append((p,1 if o=='HIT' else 0))
    shadow={'n':shadow_n,'hit_rate':round(shadow_hits/shadow_n,4) if shadow_n else None,'avg_return_pct':round(sum(srets)/len(srets),4) if srets else None,
            'brier':round(sum((p-y)**2 for p,y in sprobs)/len(sprobs),6) if sprobs else None,'brier_n':len(sprobs)}
    comparable=min(champion['n'],shadow_n)
    state='NO_SHADOW_SAMPLE' if shadow_n==0 else ('OBSERVE' if comparable<30 else 'EVALUATE')
    promotion_eligible=False
    reasons=[]
    if comparable<30: reasons.append('MIN_30_COMPARABLE_FORWARD_OUTCOMES_NOT_MET')
    if comparable>=30:
        if shadow['avg_return_pct'] is None or champion['avg_return_pct'] is None or shadow['avg_return_pct']<=champion['avg_return_pct']: reasons.append('NO_NET_RETURN_ADVANTAGE')
        if shadow['brier'] is not None and champion['brier'] is not None and shadow['brier']>=champion['brier']: reasons.append('NO_BRIER_ADVANTAGE')
        promotion_eligible=not reasons
    return {'state':state,'champion':champion,'shadow':shadow,'comparable_n':comparable,'promotion_eligible_for_review':promotion_eligible,'reasons':reasons,
            'policy':'Shadow never changes production automatically. Promotion requires a separate review, sufficient comparable forward evidence, persistence and risk checks.'}

def build(d,memory):
    return {'schema_version':'1.0','model_version':d.get('model_version'),'portfolio_risk':portfolio_risk(d),'champion_shadow':champion_shadow(memory),
            'capital_policy':'PAPER/RESEARCH ONLY; this diagnostic cannot authorize real-money deployment.'}

def main():
    d=json.loads(LATEST.read_text()); m=json.loads(MEMORY.read_text()) if MEMORY.exists() else {}
    out=build(d,m); OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'portfolio':out['portfolio_risk']['status'],'shadow':out['champion_shadow']['state']},indent=2))
if __name__=='__main__':main()
