#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path
import subprocess

import engine

ROOT=Path(__file__).resolve().parents[1]


def signal(date='2026-08-28',ticker='TEST',direction='LONG',horizon=10,setup='BREAKOUT'):
    return {'date':date,'ticker':ticker,'direction':direction,'horizon':horizon,'horizon_setup_family':setup,'horizon_state':'RULE_BASED_FALLBACK_NO_STABLE_EDGE','horizon_policy_version':engine.HORIZON_POLICY_VERSION,'sector':'TEST','asset_class':'EQUITY','cluster':'TEST','role':'CORE','confidence_pct':74.0,'forecast_probability':None,'probability_state':{'state':'INSUFFICIENT_CALIBRATION_SAMPLE','n':0},'entry_price':100.0,'risk_pct':2.0,'stop_price':98.0 if direction=='LONG' else 102.0,'target1_price':102.8 if direction=='LONG' else 97.2,'target2_price':104.4 if direction=='LONG' else 95.6,'data_quality_score':100,'model_completeness_score':60,'model_votes':[],'external_model_votes':[],'learning_adjustment':0,'risk_regime':'NEUTRAL','rates_regime':'RATES_STABLE','provenance':{'entry_price':'TEST_OBSERVED'}}


def test_episode_refresh():
    mem={'predictions':[]}
    a=engine.add_prediction(mem,signal())
    b=engine.add_prediction(mem,signal(date='2026-08-29'))
    assert a['action']=='CREATED_NEW_EPISODE'
    assert b['action']=='UPDATED_ACTIVE_EPISODE'
    assert len(mem['predictions'])==1
    assert mem['predictions'][0]['observations']==2


def test_thesis_change_supersedes():
    mem={'predictions':[]}
    engine.add_prediction(mem,signal())
    engine.add_prediction(mem,signal(date='2026-08-29',horizon=5))
    assert len(mem['predictions'])==2
    assert sum(p['outcome']=='PENDING' for p in mem['predictions'])==1
    assert sum(p['outcome']=='SUPERSEDED' for p in mem['predictions'])==1
    engine.add_prediction(mem,signal(date='2026-08-30',direction='SHORT',horizon=5))
    assert sum(p['outcome']=='PENDING' for p in mem['predictions'])==1
    assert [p for p in mem['predictions'] if p['outcome']=='PENDING'][0]['direction']=='SHORT'


def test_legacy_quarantine():
    base={'learning_lineage':'ALPHA_V85_COST_AWARE_1','ticker':'LEGACY','direction':'LONG','horizon':10,'horizon_setup_family':'BREAKOUT','outcome':'PENDING','entry':100.0}
    mem={'predictions':[deepcopy({**base,'id':'a','date':'2026-08-25'}),deepcopy({**base,'id':'b','date':'2026-08-26'}),deepcopy({**base,'id':'c','date':'2026-08-27','horizon':5})]}
    engine.migrate_prediction_episodes(mem)
    outcomes=[p['outcome'] for p in mem['predictions']]
    assert outcomes.count('PENDING')==1
    assert 'EXCLUDED_DUPLICATE' in outcomes
    assert 'SUPERSEDED' in outcomes


def test_contracts():
    src=(ROOT/'engine.py').read_text(encoding='utf-8')
    html=(ROOT/'index.html').read_text(encoding='utf-8')
    js=(ROOT/'finance-cockpit.js').read_text(encoding='utf-8')
    assert engine.MODEL_VERSION=='8.6.0-episode-ledger-cost-aware'
    assert engine.LEARNING_LINEAGE=='ALPHA_V86_EPISODE_LEDGER_1'
    for m in ['ONE_ACTIVE_EPISODE_PER_TICKER_WITH_STABLE_SETUP_KEY','legacy_resolved_excluded','model_completeness_score','EXCLUDED_DUPLICATE']:
        assert m in src,m
    for m in ['Decision ledger','ledgerKpis','importLedgerFile']:
        assert m in html,m
    for m in ['compactLedgerEpisodes','syncLedger','alpha_decision_journal_v86',"version:'8.6'",'z.observed_at===obsAt','Model completeness','Episode guard']:
        assert m in js,m
    proc=subprocess.run(['node','--check',str(ROOT/'finance-cockpit.js')],capture_output=True,text=True)
    assert proc.returncode==0,proc.stderr


def main():
    tests=[test_episode_refresh,test_thesis_change_supersedes,test_legacy_quarantine,test_contracts]
    for fn in tests:
        fn();print('PASS',fn.__name__)
    print(f'ALL PASS: {len(tests)} external-cockpit regression tests')

if __name__=='__main__':main()
