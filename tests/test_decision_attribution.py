import unittest
from scripts.build_decision_attribution import build, relation


def row(i, outcome='HIT', ret=1.0, vote=1, direction='LONG'):
    return {'id':str(i),'model_version':'test','resolved':'2026-09-03','outcome':outcome,'return_pct':ret,'direction':direction,'model_votes':{'TREND_Z':vote}}

class DecisionAttributionTests(unittest.TestCase):
    def test_vote_relation(self):
        self.assertEqual(relation(1,'LONG'),'ALIGNED')
        self.assertEqual(relation(-1,'LONG'),'OPPOSED')
        self.assertEqual(relation('SHORT','SHORT'),'ALIGNED')
    def test_excluded_rows_omitted(self):
        rows=[row(1),{**row(2),'resolution_state':'LEGACY_DUPLICATE_EXCLUDED_FROM_STATS'}]
        out=build({'model_version':'x'},{'predictions':rows})
        self.assertEqual(out['eligible_forward_resolved'],1)
    def test_small_sample_never_strong(self):
        out=build({'model_version':'x'},{'predictions':[row(i) for i in range(4)]})
        self.assertEqual(out['submodel_attribution'][0]['evidence'],'INSUFFICIENT')
        self.assertFalse(out['policy']['auto_retune'])
    def test_friction_arithmetic(self):
        rows=[row(i,ret=1.0) for i in range(20)]
        out=build({'model_version':'x'},{'predictions':rows})
        tiers={x['extra_friction_bps']:x for x in out['friction_stress']['tiers']}
        self.assertAlmostEqual(tiers[10]['avg_return_pct'],0.9)
        self.assertAlmostEqual(tiers[35]['avg_return_pct'],0.65)
        self.assertEqual(out['friction_stress']['state'],'ROBUST_TO_35BPS')
    def test_opposed_can_be_adverse_only_with_sample(self):
        rows=[]
        for i in range(10): rows.append(row(i,'MISS',-1.0,1,'LONG'))
        for i in range(10,20): rows.append(row(i,'HIT',1.0,-1,'LONG'))
        out=build({'model_version':'x'},{'predictions':rows})
        self.assertEqual(out['submodel_attribution'][0]['evidence'],'ADVERSE')

if __name__=='__main__':unittest.main()
