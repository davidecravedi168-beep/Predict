import json
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from edge_core import automation_receipt

latest=json.loads(Path('data/latest.json').read_text(encoding='utf-8'))
receipt=automation_receipt(latest,os.getenv('GITHUB_EVENT_NAME','local'),os.getenv('GITHUB_EVENT_SCHEDULE') or None)
receipt['workflow_version']='ALPHA-8.5-EDGE-CORE'
receipt['run_id']=os.getenv('GITHUB_RUN_ID')
receipt['run_attempt']=int(os.getenv('GITHUB_RUN_ATTEMPT','1'))
Path('data/automation-health.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(receipt,ensure_ascii=False))
