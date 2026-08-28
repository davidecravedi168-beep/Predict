#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path

import engine

ROOT = Path(__file__).resolve().parents[1]


def signal(date='2026-08-28', ticker='TEST', direction='LONG', horizon=10, setup='BREAKOUT'):
    return {
        'date': date,
        'ticker': ticker,
        'direction': direction,
        'horizon': horizon,
        'horizon_setup_family': setup,
        'horizon_state': 'RULE_BASED_FALLBACK_NO_STABLE_EDGE',
        'horizon_policy_version': engine.HORIZON_POLICY_VERSION,
        'sector': 'TEST_GROUP',
        'asset_class': 'EQUITY',
        'cluster': 'TEST_CLUSTER',
        'role': 'CORE',
        'confidence_pct': 74.0,
        'forecast_probability': None,
        'probability_state': {'state': 'INSUFFICIENT_CALIBRATION_SAMPLE', 'n': 0},
        'entry_price': 100.0,
        'risk_pct': 2.0,
        'stop_price': 98.0 if direction == 'LONG' else 102.0,
        'target1_price': 102.8 if direction == 'LONG' else 97.2,
        'target2_price': 104.4 if direction == 'LONG' else 95.6,
        'data_quality_score': 100,
        'model_completeness_score': 60,
        'model_votes': [],
        'external_model_votes': [],
        'learning_adjustment': 0,
        'risk_regime': 'NEUTRAL',
        'rates_regime': 'RATES_STABLE',
        'provenance': {'entry_price': 'TEST_OBSERVED'},
    }


def test_same_thesis_updates_not_duplicates():
    mem = {'predictions': []}
    first = engine.add_prediction(mem, signal())
    second_sig = signal(date='2026-08-29')
    second = engine.add_prediction(mem, second_sig)
    assert first['action'] == 'CREATED_NEW_EPISODE'
    assert second['action'] == 'UPDATED_ACTIVE_EPISODE'
    assert len(mem['predictions']) == 1
    row = mem['predictions'][0]
    assert row['outcome'] == 'PENDING'
    assert row['observations'] == 2
    assert row['last_seen'] == '2026-08-29'


def test_changed_thesis_supersedes_previous():
    mem = {'predictions': []}
    engine.add_prediction(mem, signal())
    changed = signal(date='2026-08-29', horizon=5)
    result = engine.add_prediction(mem, changed)
    assert result['action'] == 'CREATED_NEW_EPISODE'
    assert len(mem['predictions']) == 2
    assert sum(p['outcome'] == 'PENDING' for p in mem['predictions']) == 1
    assert sum(p['outcome'] == 'SUPERSEDED' for p in mem['predictions']) == 1

    flipped = signal(date='2026-08-30', direction='SHORT', horizon=5)
    engine.add_prediction(mem, flipped)
    assert len(mem['predictions']) == 3
    assert sum(p['outcome'] == 'PENDING' for p in mem['predictions']) == 1
    active = [p for p in mem['predictions'] if p['outcome'] == 'PENDING'][0]
    assert active['direction'] == 'SHORT'


def test_legacy_overlap_is_quarantined():
    base = {
        'learning_lineage': 'ALPHA_V85_COST_AWARE_1',
        'ticker': 'LEGACY',
        'direction': 'LONG',
        'horizon': 10,
        'horizon_setup_family': 'BREAKOUT',
        'outcome': 'PENDING',
        'entry': 100.0,
    }
    p1 = {**base, 'id': 'old-1', 'date': '2026-08-25'}
    p2 = {**base, 'id': 'old-2', 'date': '2026-08-26'}
    p3 = {**base, 'id': 'old-3', 'date': '2026-08-27', 'horizon': 5}
    mem = {'predictions': [deepcopy(p1), deepcopy(p2), deepcopy(p3)]}
    engine.migrate_prediction_episodes(mem)
    outcomes = [p['outcome'] for p in mem['predictions']]
    assert outcomes.count('PENDING') == 1
    assert 'EXCLUDED_DUPLICATE' in outcomes
    assert 'SUPERSEDED' in outcomes
    active = [p for p in mem['predictions'] if p['outcome'] == 'PENDING'][0]
    assert active['id'] == 'old-3'
    assert active['state'] == 'ACTIVE'


def test_frontend_guards_present():
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    assert 'function compactJournalEpisodes(rows)' in html
    assert 'Journal Episode Guard' in html
    assert '<12*3600*1000' not in html
    assert "version:'8.6'" in html
    assert 'alpha_last_data_v86' in html
    assert 'v86episodeledger' in html
    assert 'Completezza modello' in html
    assert "'SUPERSEDED','EXPIRED'" in html


def test_engine_contract_present():
    src = (ROOT / 'engine.py').read_text(encoding='utf-8')
    assert engine.MODEL_VERSION == '8.6.0-episode-ledger-cost-aware'
    assert engine.LEARNING_LINEAGE == 'ALPHA_V86_EPISODE_LEDGER_1'
    assert 'ONE_ACTIVE_EPISODE_PER_TICKER_WITH_STABLE_SETUP_KEY' in src
    assert 'model_completeness_score' in src
    assert 'legacy_resolved_excluded' in src
    assert 'EXCLUDED_DUPLICATE' in src


def main():
    tests = [
        test_same_thesis_updates_not_duplicates,
        test_changed_thesis_supersedes_previous,
        test_legacy_overlap_is_quarantined,
        test_frontend_guards_present,
        test_engine_contract_present,
    ]
    for fn in tests:
        fn()
        print('PASS', fn.__name__)
    print(f'ALL PASS: {len(tests)} Alpha v8.6 guard tests')


if __name__ == '__main__':
    main()
