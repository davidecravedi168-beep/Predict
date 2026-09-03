import json
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
        # Assert the current finance-specific product contract. This must not
        # pin the data pipeline to retired marketing copy or formatting.
        for label in ('PREDICTIVE QUANT COCKPIT','Signal desk','Macro cockpit','Quant lab','Decision ledger'):
            self.assertIn(label,html)
        self.assertIn('Content-Security-Policy',html)
        self.assertIn("serviceWorker.register('./sw.js'",js)
        self.assertIn('alpha_decision_journal_v86',js)
        self.assertIn('compactLedgerEpisodes',js)
        self.assertIn('SUPERSEDED',js)

    def test_workflow_runs_observable_qa_and_v86_validator(self):
        y=(ROOT/'.github/workflows/update-data.yml').read_text(encoding='utf-8')
        # Keep QA fail-closed, but require independently visible modules so a
        # single failing contract can be diagnosed without disabling QA.
        for marker in (
            'QA decision attribution',
            'QA edge core',
            'QA portfolio shadow',
            'QA quant governance',
            'QA static contract',
            'QA pass 2',
        ):
            self.assertIn(marker,y)
        self.assertIn('write_automation_health.py',y)
        self.assertIn('validate_v86_snapshot.py',y)
        self.assertIn('apply_alpha_v86_external_cockpit.py',y)

    def test_pages_deploys_a_public_allowlist(self):
        y=(ROOT/'.github/workflows/deploy-pages-V6.1-AUTO.yml').read_text(encoding='utf-8')
        self.assertIn('Build public allowlist',y)
        self.assertIn('path: "_site"',y)
        self.assertNotIn('path: "."',y)
        # Validate the actual leak-prevention behavior rather than requiring a
        # particular human-readable error message.
        self.assertIn('find _site -type f',y)
        self.assertIn("-name '*.py'",y)
        self.assertIn("-name '*.yml'",y)
        self.assertIn("-name '*memory*'",y)
        self.assertIn('exit 1',y)

    def test_service_worker_manifest_and_deploy_share_current_icon_contract(self):
        """Regression: icon family renames must not leave QA pinned to a retired filename."""
        sw=(ROOT/'sw.js').read_text(encoding='utf-8')
        manifest=json.loads((ROOT/'manifest.webmanifest').read_text(encoding='utf-8'))
        deploy=(ROOT/'.github/workflows/deploy-pages-V6.1-AUTO.yml').read_text(encoding='utf-8')

        self.assertIn('alpha-engine-v9-finance-cockpit',sw)
        self.assertIn('./finance-cockpit.js',sw)
        self.assertIn('./finance-cockpit.css',sw)
        self.assertIn('./freeze-ui.css',sw)
        self.assertIn('automation-health',sw)
        self.assertIn('market-series',sw)

        icons=manifest.get('icons') or []
        self.assertGreaterEqual(len(icons),2)
        icon_sources=[]
        for icon in icons:
            src=str(icon.get('src') or '').lstrip('./')
            self.assertTrue(src,icon)
            icon_sources.append(src)
            self.assertIn(f'./{src}',sw,f'{src} is in manifest but not cached by service worker')
            self.assertIn(src,deploy,f'{src} is in manifest but not generated/copied by Pages deploy')

        self.assertIn('alpha-home-192.png',icon_sources)
        self.assertIn('alpha-home-512.png',icon_sources)
        self.assertNotIn('./icon-192.png',sw)
        # Formatting around '=' is irrelevant; the source icon contract is not.
        self.assertIn("Image.open('icon-180.png')",deploy)

if __name__=='__main__': unittest.main()
