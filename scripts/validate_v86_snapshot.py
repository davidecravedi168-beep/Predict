from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LATEST=ROOT/'data/latest.json'
SERIES=ROOT/'data/market-series.json'


def fail(msg): raise SystemExit(msg)
def finite_or_none(v): return v is None or (isinstance(v,(int,float)) and math.isfinite(v))


def main():
    if not LATEST.exists(): fail('data/latest.json missing')
    d=json.loads(LATEST.read_text(encoding='utf-8'))
    if d.get('schema_version')!='8.6': fail(f"unexpected schema_version: {d.get('schema_version')}")
    if not str(d.get('model_version','')).startswith('8.6.'): fail('V8.6 model_version missing')
    if d.get('data_quality',{}).get('strict_no_fabrication') is not True: fail('strict_no_fabrication gate missing')
    edge=d.get('edge_core',{})
    if edge.get('version')!='1.0.0' or edge.get('domain_profile')!='FINANCE': fail('FINANCE Edge Core metadata missing')
    if edge.get('signal_lock')!='ONE_ACTIVE_EPISODE_PER_TICKER_WITH_STABLE_SETUP_KEY': fail('episode signal lock missing')

    engine_ts=d.get('engine_updated_at') or d.get('updated_at')
    if not engine_ts: fail('engine timestamp missing')
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(engine_ts.replace('Z','+00:00'))).total_seconds()
    if age>600: fail(f'latest.json too old: {age:.0f}s')

    signals=d.get('signals');watch=d.get('watchlist')
    if not isinstance(signals,list) or not isinstance(watch,list): fail('signals/watchlist contract invalid')
    if len({x.get('ticker') for x in signals})!=len(signals): fail('duplicate ticker in signals')

    def row(x,where):
        req=('ticker','asset_class','cluster','direction','score','confidence_pct','data_quality_score','model_completeness_score','decision_reliability_state','provenance')
        miss=[k for k in req if x.get(k) is None]
        if miss: fail(f"{where} {x.get('ticker')} missing {miss}")
        if x.get('direction') not in ('LONG','SHORT'): fail(f"bad direction {x.get('ticker')}")
        for k in ('score','confidence_pct','data_quality_score','model_completeness_score','price','entry_price','stop_price','target1_price','target2_price'):
            if not finite_or_none(x.get(k)): fail(f"non-finite {k} for {x.get('ticker')}")
        dq=x.get('data_quality_score');mc=x.get('model_completeness_score')
        if not 0<=dq<=100 or not 0<=mc<=100: fail(f"quality/completeness outside 0-100 for {x.get('ticker')}")
        p0,st,t1,t2=x.get('entry_price'),x.get('stop_price'),x.get('target1_price'),x.get('target2_price')
        if p0 is not None and p0<=0: fail(f"non-positive price {x.get('ticker')}")
        if None not in (p0,st,t1):
            if x['direction']=='LONG' and not(st<p0<t1): fail(f"LONG geometry invalid {x.get('ticker')}")
            if x['direction']=='SHORT' and not(t1<p0<st): fail(f"SHORT geometry invalid {x.get('ticker')}")
            if t2 is not None and ((x['direction']=='LONG' and t2<t1) or (x['direction']=='SHORT' and t2>t1)): fail(f"T2 invalid {x.get('ticker')}")
        fp=x.get('forecast_probability')
        if fp is not None:
            ps=x.get('probability_state') or {}
            if not isinstance(fp,(int,float)) or not math.isfinite(fp) or not 0<=fp<=1: fail(f"invalid probability {x.get('ticker')}")
            if ps.get('state')!='EMPIRICALLY_CALIBRATED' or int(ps.get('n',0) or 0)<30: fail(f"probability published without calibration {x.get('ticker')}")
        hp=x.get('horizon_profile') or {};h=x.get('horizon')
        if not isinstance(h,int) or h<1 or int(hp.get('selected',-1) or -1)!=h: fail(f"horizon/profile invalid {x.get('ticker')}")
        if x.get('horizon_policy_version')!='AH-1.0': fail(f"horizon policy mismatch {x.get('ticker')}")

    for x in signals: row(x,'signal')
    for x in watch: row(x,'watch')

    cc=Counter(x.get('asset_class') for x in signals);kc=Counter(x.get('cluster') for x in signals)
    if any(v>2 for v in cc.values()) or any(v>2 for v in kc.values()): fail('diversification cap breached')
    if sum(x.get('role')=='SATELLITE' for x in signals)>1: fail('satellite cap breached')

    learning=d.get('model_learning',{})
    if learning.get('no_lookahead') is not True or learning.get('learning_lineage')!='ALPHA_V86_EPISODE_LEDGER_1': fail('V8.6 learning governance mismatch')
    mem=d.get('memory',{})
    for k in ('independent_episodes','active','superseded_or_excluded','legacy_resolved_excluded','resolved','learning_resolved'):
        if k not in mem: fail(f'memory metric missing: {k}')
    if int(mem.get('learning_resolved',0) or 0)>int(mem.get('resolved',0) or 0): fail('learning resolved exceeds resolved')
    if float(learning.get('max_positive_adjustment_points',99))>1.0: fail('positive learning cap too high')
    if float(learning.get('max_negative_adjustment_points',0))>-4.0: fail('negative learning guard too weak')

    if not SERIES.exists(): fail('market-series missing')
    s=json.loads(SERIES.read_text(encoding='utf-8'))
    if s.get('schema_version')!='1.0' or s.get('strict_no_fabrication') is not True or not isinstance(s.get('symbols'),dict): fail('market-series contract invalid')
    for ticker,info in s.get('symbols',{}).items():
        for bucket in ('daily','intraday'):
            pts=info.get(bucket) or []
            if not isinstance(pts,list): fail(f'{ticker} {bucket} not list')
            for point in pts:
                if not isinstance(point,list) or len(point)!=2 or not isinstance(point[1],(int,float)) or not math.isfinite(point[1]): fail(f'invalid chart point {ticker}/{bucket}')

    print('Alpha V8.6 + V9 episode-ledger validation OK')
    print('engine_updated_at:',engine_ts)
    print('signals:',len(signals),'watchlist:',len(watch),'active episodes:',mem.get('active'),'clean resolved:',mem.get('resolved'))

if __name__=='__main__': main()
