import unittest
from scripts.build_portfolio_shadow import build

class PortfolioShadowTests(unittest.TestCase):
 def test_concentration_flag(self):
  d={'model_version':'x','signals':[{'asset_class':'EQUITY','cluster':'SAME','direction':'LONG','currency':'USD'} for _ in range(5)]}
  o=build(d,{'predictions':[]})
  self.assertEqual(o['portfolio_risk']['status'],'ATTENTION')
  self.assertIn('CLUSTER_CONCENTRATION',o['portfolio_risk']['flags'])
 def test_no_shadow_never_promotes(self):
  d={'model_version':'x','signals':[]}
  o=build(d,{'predictions':[]})
  self.assertEqual(o['champion_shadow']['state'],'NO_SHADOW_SAMPLE')
  self.assertFalse(o['champion_shadow']['promotion_eligible_for_review'])
 def test_small_shadow_is_observe_only(self):
  rows=[]
  for i in range(10):
   rows.append({'model_version':'x','resolved':'2026-09-01','outcome':'HIT','return_pct':1,'shadow_model_version':'s1','shadow_outcome':'HIT','shadow_return_pct':1.1})
  o=build({'model_version':'x','signals':[]},{'predictions':rows})
  self.assertEqual(o['champion_shadow']['state'],'OBSERVE')
  self.assertFalse(o['champion_shadow']['promotion_eligible_for_review'])
if __name__=='__main__':unittest.main()
