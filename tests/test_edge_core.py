import unittest

from edge_core import EDGE_CORE_VERSION, assess_freshness, assert_public_snapshot, automation_receipt, cost_adjusted_return_pct, estimated_round_trip_cost_bps


class EdgeCoreTest(unittest.TestCase):
    def setUp(self):
        self.snapshot={"model_version":"8.5.0-test","updated_at":"2026-08-28T00:00:00+00:00","data_quality":{"strict_no_fabrication":True},"signals":[],"watchlist":[]}

    def test_cost_model_is_conservative_and_domain_specific(self):
        self.assertGreater(estimated_round_trip_cost_bps('CRYPTO'),estimated_round_trip_cost_bps('ETF_BOND_GOV'))
        net,cost=cost_adjusted_return_pct(1.0,'EQUITY')
        self.assertEqual(net,0.88)
        self.assertEqual(cost,12.0)

    def test_public_snapshot_contract(self):
        self.assertTrue(assert_public_snapshot(self.snapshot))
        bad={**self.snapshot,"note":"Bearer abcdefghijklmnop"}
        with self.assertRaises(ValueError): assert_public_snapshot(bad)

    def test_missing_timestamp_fails_closed(self):
        self.assertFalse(assess_freshness(None)['operational'])

    def test_receipt(self):
        r=automation_receipt(self.snapshot)
        self.assertTrue(r['ok'])
        self.assertEqual(r['edge_core_version'],EDGE_CORE_VERSION)
        self.assertEqual(len(r['artifact_digest']),16)


if __name__=='__main__': unittest.main()
