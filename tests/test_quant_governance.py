import unittest
from datetime import datetime, timedelta, timezone

from scripts.build_quant_governance import build_governance


NOW = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)


def base_snapshot():
    return {
        'model_version': '8.6.1-test',
        'engine_updated_at': '2026-09-03T09:50:00+00:00',
        'memory': {
            'independent_episodes': 40,
            'active': 10,
            'resolved': 30,
            'hit_rate': 0.56,
            'brier': 0.23,
            'brier_n': 30,
        },
        'data_quality': {'availability_ratio': 0.98, 'unavailable_tickers': []},
        'backtest': {'state': 'AVAILABLE', 'signals': 100, 'hit_rate': 0.55, 'avg_return_pct': 0.20},
        'signals': [
            {'ticker': 'A', 'cluster': 'X', 'asset_class': 'EQUITY', 'direction': 'LONG', 'model_completeness_score': 0.9},
            {'ticker': 'B', 'cluster': 'Y', 'asset_class': 'ETF_EQUITY', 'direction': 'SHORT', 'model_completeness_score': 0.85},
        ],
        'watchlist': [],
    }


def forward_memory(n=30, hits=18):
    rows = []
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(n):
        hit = i < hits
        rows.append({
            'id': f'EP-{i}',
            'model_version': '8.6.1-test',
            'learning_lineage': 'ALPHA_V86_EPISODE_LEDGER_1',
            'date': (start + timedelta(days=i)).date().isoformat(),
            'resolved': (start + timedelta(days=i + 5)).date().isoformat(),
            'ticker': f'T{i}',
            'asset_class': 'EQUITY' if i % 2 == 0 else 'ETF_EQUITY',
            'cluster': 'TEST',
            'direction': 'LONG' if i % 3 else 'SHORT',
            'horizon': 10 if i % 2 == 0 else 20,
            'horizon_setup_family': 'TREND' if i % 2 == 0 else 'BREAKOUT',
            'confidence_pct': 75 if i % 2 == 0 else 65,
            'risk_regime': 'RISK_ON',
            'rates_regime': 'RATES_STABLE',
            'outcome': 'HIT' if hit else 'MISS',
            'return_pct': 1.0 if hit else -0.7,
            'forecast_probability': 0.60,
        })
    return {'predictions': rows}


def single_segment_memory(prior_hits, recent_hits, window=10):
    rows = []
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    outcomes = ([True] * prior_hits + [False] * (window - prior_hits) +
                [True] * recent_hits + [False] * (window - recent_hits))
    for i, hit in enumerate(outcomes):
        rows.append({
            'id': f'D-{i}',
            'model_version': '8.6.1-test',
            'date': (start + timedelta(days=i)).date().isoformat(),
            'resolved': (start + timedelta(days=i + 3)).date().isoformat(),
            'ticker': f'D{i}',
            'asset_class': 'EQUITY',
            'cluster': 'ONE',
            'direction': 'LONG',
            'horizon': 10,
            'horizon_setup_family': 'TREND',
            'confidence_pct': 75,
            'risk_regime': 'RISK_ON',
            'rates_regime': 'RATES_STABLE',
            'outcome': 'HIT' if hit else 'MISS',
            'return_pct': 1.1 if hit else -1.1,
            'forecast_probability': 0.65,
        })
    return {'predictions': rows}


class QuantGovernanceTests(unittest.TestCase):
    def test_calibrating_sample_is_paper_only(self):
        out = build_governance(base_snapshot(), now=NOW, memory_data=forward_memory())
        self.assertEqual(out['promotion_state'], 'PAPER_ONLY')
        self.assertTrue(out['calibration']['allowed'])
        self.assertFalse(out['blockers'])
        self.assertEqual(out['schema_version'], '2.2')

    def test_probability_before_30_resolved_blocks(self):
        d = base_snapshot()
        d['memory']['resolved'] = 5
        d['memory']['brier_n'] = 5
        d['signals'][0]['forecast_probability'] = 0.67
        out = build_governance(d, now=NOW, memory_data=forward_memory(5, 3))
        self.assertEqual(out['status'], 'BLOCKED')
        self.assertIn('EMPIRICAL_PROBABILITY_PUBLISHED_BEFORE_MINIMUM_FORWARD_SAMPLE', out['blockers'])

    def test_future_timestamp_blocks(self):
        d = base_snapshot()
        d['engine_updated_at'] = '2026-09-03T10:30:00+00:00'
        out = build_governance(d, now=NOW, memory_data=forward_memory())
        self.assertIn('SOURCE_TIMESTAMP_IN_FUTURE', out['blockers'])

    def test_cold_start_never_promotes_live(self):
        d = base_snapshot()
        d['memory']['resolved'] = 0
        d['memory']['brier_n'] = 0
        out = build_governance(d, now=NOW, memory_data={'predictions': []})
        self.assertEqual(out['promotion_state'], 'RESEARCH_ONLY')
        self.assertFalse(out['policy']['live_capital_auto_promotion'])

    def test_signal_concentration_is_flagged(self):
        d = base_snapshot()
        d['signals'] = [
            {'ticker': str(i), 'cluster': 'SAME', 'asset_class': 'EQUITY', 'direction': 'LONG', 'model_completeness_score': 0.9}
            for i in range(6)
        ]
        out = build_governance(d, now=NOW, memory_data=forward_memory())
        self.assertIn('SIGNAL_CLUSTER_CONCENTRATION_ABOVE_50_PERCENT', out['flags'])
        self.assertIn('SIGNAL_DIRECTION_CONCENTRATION_ABOVE_80_PERCENT', out['flags'])

    def test_forward_segments_include_asset_regime_horizon_and_confidence(self):
        out = build_governance(base_snapshot(), now=NOW, memory_data=forward_memory())
        seg = out['forward_segments']
        self.assertEqual(seg['eligible_forward_resolved'], 30)
        self.assertTrue(seg['dimensions']['asset_class'])
        self.assertTrue(seg['dimensions']['regime'])
        self.assertTrue(seg['dimensions']['horizon'])
        self.assertTrue(seg['dimensions']['confidence_band'])
        self.assertTrue(seg['dimensions']['setup_family'])
        self.assertTrue(all('wilson_lower_95' in x for x in seg['ranked_evidence']))
        self.assertIn('drift_monitor', seg)

    def test_excluded_and_legacy_rows_do_not_enter_forward_segments(self):
        m = forward_memory(4, 3)
        m['predictions'].append({
            'model_version': '8.6.1-test', 'resolved': '2026-09-01', 'outcome': 'HIT',
            'resolution_state': 'LEGACY_DUPLICATE_EXCLUDED_FROM_STATS', 'asset_class': 'CRYPTO',
        })
        m['predictions'].append({
            'resolved': '2026-09-01', 'outcome': 'HIT', 'asset_class': 'CRYPTO',
        })
        d = base_snapshot()
        d['memory']['resolved'] = 4
        d['memory']['brier_n'] = 4
        out = build_governance(d, now=NOW, memory_data=m)
        self.assertEqual(out['forward_segments']['eligible_forward_resolved'], 4)

    def test_segment_diagnostics_never_auto_promote_live(self):
        d = base_snapshot()
        d['memory']['resolved'] = 120
        d['memory']['brier_n'] = 120
        out = build_governance(d, now=NOW, memory_data=forward_memory(120, 80))
        self.assertEqual(out['promotion_state'], 'PAPER_ONLY')
        self.assertFalse(out['policy']['live_capital_auto_promotion'])
        self.assertIn('never auto-retune', out['forward_segments']['policy'])

    def test_clear_recent_deterioration_triggers_segment_drift(self):
        d = base_snapshot()
        d['memory']['resolved'] = 20
        d['memory']['brier_n'] = 20
        out = build_governance(d, now=NOW, memory_data=single_segment_memory(9, 2, 10))
        dm = out['forward_segments']['drift_monitor']
        self.assertGreater(dm['state_counts'].get('DRIFT', 0), 0)
        self.assertIn('FORWARD_SEGMENT_DRIFT_DETECTED', out['flags'])
        self.assertTrue(any(x['state'] == 'DRIFT' for x in dm['alerts']))

    def test_similar_windows_remain_stable(self):
        d = base_snapshot()
        d['memory']['resolved'] = 20
        d['memory']['brier_n'] = 20
        out = build_governance(d, now=NOW, memory_data=single_segment_memory(7, 7, 10))
        states = out['forward_segments']['drift_monitor']['state_counts']
        self.assertGreater(states.get('STABLE', 0), 0)
        self.assertNotIn('FORWARD_SEGMENT_DRIFT_DETECTED', out['flags'])

    def test_small_history_never_claims_drift(self):
        d = base_snapshot()
        d['memory']['resolved'] = 8
        d['memory']['brier_n'] = 8
        out = build_governance(d, now=NOW, memory_data=forward_memory(8, 4))
        dm = out['forward_segments']['drift_monitor']
        self.assertEqual(dm['segments_evaluated'], 0)
        self.assertNotIn('FORWARD_SEGMENT_DRIFT_DETECTED', out['flags'])


if __name__ == '__main__':
    unittest.main()
