#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path
import re
import subprocess
import tempfile

import engine

ROOT = Path(__file__).resolve().parents[1]


def signal(date='2026-08-28', ticker='TEST', direction='LONG', horizon=10, setup='BREAKOUT'):
    return {
        'date': date, 'ticker': ticker, 'direction': direction, 'horizon': horizon,
        'horizon_setup_family': setup, 'horizon_state': 'RULE_BASED_FALLBACK_NO_STABLE_EDGE',
        'horizon_policy_version': engine.HORIZON_POLICY_VERSION, 'sector': 'TEST_GROUP',
        'asset_class': 'EQUITY', 'cluster': 'TEST_CLUSTER', 'role': 'CORE',
        'confidence_pct': 74.0, 'forecast_probability': None,
        'probability_state': {'state': 'INSUFFICIENT_CALIBRATION_SAMPLE', 'n': 0},
        'entry_price': 100.0, 'risk_pct': 2.0,
        'stop_price': 98.0 if direction == 'LONG' else 102.0,
        'target1_price': 102.8 if direction == 'LONG' else 97.2,
        'target2_price': 104.4 if direction == 'LONG' else 95.6,
        'data_quality_score': 100, 'model_completeness_score': 60,
        'model_votes': [], 'external_model_votes': [], 'learning_adjustment': 0,
        'risk_regime': 'NEUTRAL', 'rates_regime': 'RATES_STABLE',
        'provenance': {'entry_price': 'TEST_OBSERVED'},
    }


def test_same_setup_is_one_episode():
    mem = {'predictions': []}
    a = engine.add_prediction(mem, signal())
    b = engine.add_prediction(mem, signal(date='2026-08-29'))
    assert a['action'] == 'CREATED_NEW_EPISODE'
    assert b['action'] == 'UPDATED_ACTIVE_EPISODE'
    assert len(mem['predictions']) == 1
    assert mem['predictions'][0]['observations'] == 2
    assert mem['predictions'][0]['outcome'] == 'PENDING'


def test_new_thesis_supersedes_old():
    mem = {'predictions': []}
    engine.add_prediction(mem, signal())
    engine.add_prediction(mem, signal(date='2026-08-29', horizon=5))
    assert len(mem['predictions']) == 2
    assert sum(p['outcome'] == 'PENDING' for p in mem['predictions']) == 1
    assert sum(p['outcome'] == 'SUPERSEDED' for p in mem['predictions']) == 1
    engine.add_prediction(mem, signal(date='2026-08-30', direction='SHORT', horizon=5))
    assert len(mem['predictions']) == 3
    assert sum(p['outcome'] == 'PENDING' for p in mem['predictions']) == 1
    assert [p for p in mem['predictions'] if p['outcome'] == 'PENDING'][0]['direction'] == 'SHORT'


def test_legacy_duplicates_quarantined():
    base = {'learning_lineage': 'ALPHA_V85_COST_AWARE_1', 'ticker': 'LEGACY', 'direction': 'LONG', 'horizon': 10, 'horizon_setup_family': 'BREAKOUT', 'outcome': 'PENDING', 'entry': 100.0}
    rows = [
        {**base, 'id': 'old-1', 'date': '2026-08-25'},
        {**base, 'id': 'old-2', 'date': '2026-08-26'},
        {**base, 'id': 'old-3', 'date': '2026-08-27', 'horizon': 5},
    ]
    mem = {'predictions': deepcopy(rows)}
    engine.migrate_prediction_episodes(mem)
    outcomes = [p['outcome'] for p in mem['predictions']]
    assert outcomes.count('PENDING') == 1
    assert 'EXCLUDED_DUPLICATE' in outcomes
    assert 'SUPERSEDED' in outcomes
    assert [p for p in mem['predictions'] if p['outcome'] == 'PENDING'][0]['id'] == 'old-3'


def test_engine_contract():
    src = (ROOT / 'engine.py').read_text(encoding='utf-8')
    assert engine.MODEL_VERSION == '8.6.0-episode-ledger-cost-aware'
    assert engine.LEARNING_LINEAGE == 'ALPHA_V86_EPISODE_LEDGER_1'
    assert 'ONE_ACTIVE_EPISODE_PER_TICKER_WITH_STABLE_SETUP_KEY' in src
    assert 'legacy_resolved_excluded' in src
    assert 'model_completeness_score' in src
    assert 'EXCLUDED_DUPLICATE' in src


def test_v9_frontend_contract():
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    for marker in ['Alpha Engine V9 · Finance Cockpit', 'Decision Ledger', 'compactLedgerEpisodes', 'DUP REMOVED', "version:'8.6'", 'Model completeness', 'Episode guard', 'alpha_decision_journal_v86']:
        assert marker in html, marker
    assert '<12*3600*1000' not in html
    assert 'SUPERSEDED' in html
    assert 'snapshotLedger();observeLedger();renderLedger();bindLedgerControls();' in html


def test_v9_javascript_syntax():
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    matches = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', html, flags=re.S | re.I)
    assert matches, 'no inline script found'
    js = '\n'.join(matches)
    with tempfile.NamedTemporaryFile('w', suffix='.js', encoding='utf-8', delete=False) as f:
        f.write(js)
        path = f.name
    proc = subprocess.run(['node', '--check', path], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def main():
    tests = [test_same_setup_is_one_episode, test_new_thesis_supersedes_old, test_legacy_duplicates_quarantined, test_engine_contract, test_v9_frontend_contract, test_v9_javascript_syntax]
    for fn in tests:
        fn(); print('PASS', fn.__name__)
    print(f'ALL PASS: {len(tests)} Alpha v8.6/V9 guard tests')


if __name__ == '__main__':
    main()
