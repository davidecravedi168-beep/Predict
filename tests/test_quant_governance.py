import unittest
from datetime import datetime, timezone

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


class QuantGovernanceTests(unittest.TestCase):
    def test_calibrating_sample_is_paper_only(self):
        out = build_governance(base_snapshot(), now=NOW)
        self.assertEqual(out['promotion_state'], 'PAPER_ONLY')
        self.assertTrue(out['calibration']['allowed'])
        self.assertFalse(out['blockers'])

    def test_probability_before_30_resolved_blocks(self):
        d = base_snapshot()
        d['memory']['resolved'] = 5
        d['memory']['brier_n'] = 5
        d['signals'][0]['forecast_probability'] = 0.67
        out = build_governance(d, now=NOW)
        self.assertEqual(out['status'], 'BLOCKED')
        self.assertIn('EMPIRICAL_PROBABILITY_PUBLISHED_BEFORE_MINIMUM_FORWARD_SAMPLE', out['blockers'])

    def test_future_timestamp_blocks(self):
        d = base_snapshot()
        d['engine_updated_at'] = '2026-09-03T10:30:00+00:00'
        out = build_governance(d, now=NOW)
        self.assertIn('SOURCE_TIMESTAMP_IN_FUTURE', out['blockers'])

    def test_cold_start_never_promotes_live(self):
        d = base_snapshot()
        d['memory']['resolved'] = 0
        d['memory']['brier_n'] = 0
        out = build_governance(d, now=NOW)
        self.assertEqual(out['promotion_state'], 'RESEARCH_ONLY')
        self.assertFalse(out['policy']['live_capital_auto_promotion'])

    def test_signal_concentration_is_flagged(self):
        d = base_snapshot()
        d['signals'] = [
            {'ticker': str(i), 'cluster': 'SAME', 'asset_class': 'EQUITY', 'direction': 'LONG', 'model_completeness_score': 0.9}
            for i in range(6)
        ]
        out = build_governance(d, now=NOW)
        self.assertIn('SIGNAL_CLUSTER_CONCENTRATION_ABOVE_50_PERCENT', out['flags'])
        self.assertIn('SIGNAL_DIRECTION_CONCENTRATION_ABOVE_80_PERCENT', out['flags'])


if __name__ == '__main__':
    unittest.main()
