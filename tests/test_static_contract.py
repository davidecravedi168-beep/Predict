import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]

class StaticContractTest(unittest.TestCase):
    def test_secrets_not_in_browser(self):
        html=(ROOT/'index.html').read_text(encoding='utf-8')
        js=(ROOT/'finance-cockpit.js').read_text(encoding='utf-8')
        self.assertNotRegex(html+js,r'(?i)(api[_-]?key|bearer\s+[A-Za-z0-9._-]{12,})')

    def test_ui_is_finance_specific_and_has_ledger(self):
        html=(ROOT/'index.html').read_text(encoding='utf-8')
        js=(ROOT/'finance-cockpit.js').read_text(encoding='utf-8')
        for label in ('QUANT BROKER COCKPIT','Signal desk','Macro cockpit','Quant lab','Decision ledger'):
            self.assertIn(label,html)
        self.assertIn('Content-Security-Policy',html)
        self.assertIn("serviceWorker.register('./sw.js'",js)
        self.assertIn('alpha_decision_journal_v86',js)
        self.assertIn('compactLedgerEpisodes',js)
        self.assertIn('SUPERSEDED',js)

    def test_workflow_runs_double_qa_and_v86_validator(self):
        y=(ROOT/'.github/workflows/update-data.yml').read_text(encoding='utf-8')
        self.assertIn('QA pass 1',y)
        self.assertIn('QA pass 2',y)
        self.assertIn('write_automation_health.py',y)
        self.assertIn('validate_v86_snapshot.py',y)
        self.assertIn('apply_alpha_v86_external_cockpit.py',y)

    def test_pages_deploys_a_public_allowlist(self):
        y=(ROOT/'.github/workflows/deploy-pages-V6.1-AUTO.yml').read_text(encoding='utf-8')
        self.assertIn('Build public allowlist',y)
        self.assertIn('path: "_site"',y)
        self.assertNotIn('path: "."',y)
        self.assertIn('Private implementation file leaked',y)

    def test_service_worker_uses_v9_shell_and_live_health(self):
        sw=(ROOT/'sw.js').read_text(encoding='utf-8')
        self.assertIn('alpha-engine-v9-finance-cockpit',sw)
        self.assertIn('./finance-cockpit.js',sw)
        self.assertIn('./finance-cockpit.css',sw)
        self.assertIn('./icon-192.png',sw)
        self.assertNotIn('./assets/icon-192.png',sw)
        self.assertIn('automation-health',sw)
        self.assertIn('market-series',sw)

if __name__=='__main__': unittest.main()
