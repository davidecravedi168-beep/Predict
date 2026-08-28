import pathlib
import unittest


ROOT=pathlib.Path(__file__).resolve().parents[1]


class StaticContractTest(unittest.TestCase):
    def test_secrets_not_in_browser(self):
        html=(ROOT/'index.html').read_text(encoding='utf-8')
        self.assertNotRegex(html,r'(?i)(api[_-]?key|bearer\s+[A-Za-z0-9._-]{12,})')

    def test_ui_is_finance_specific(self):
        html=(ROOT/'index.html').read_text(encoding='utf-8')
        for label in ('Cross-Asset','Rischio €','Journal','Adaptive Horizon'):
            self.assertIn(label,html)
        self.assertIn('Content-Security-Policy',html)
        self.assertIn("serviceWorker.register('./sw.js'",html)

    def test_workflow_runs_double_qa(self):
        y=(ROOT/'.github/workflows/update-data.yml').read_text(encoding='utf-8')
        self.assertIn('QA pass 1',y)
        self.assertIn('QA pass 2',y)
        self.assertIn('write_automation_health.py',y)

    def test_pages_deploys_a_public_allowlist(self):
        y=(ROOT/'.github/workflows/deploy-pages-V6.1-AUTO.yml').read_text(encoding='utf-8')
        self.assertIn('Build public allowlist',y)
        self.assertIn('path: "_site"',y)
        self.assertNotIn('path: "."',y)
        self.assertIn('Private implementation file leaked',y)

    def test_service_worker_uses_real_icon_paths_and_live_health(self):
        sw=(ROOT/'sw.js').read_text(encoding='utf-8')
        self.assertIn('alpha-engine-v8-5-edge-core',sw)
        self.assertIn('./icon-192.png',sw)
        self.assertNotIn('./assets/icon-192.png',sw)
        self.assertIn('automation-health',sw)


if __name__=='__main__': unittest.main()
