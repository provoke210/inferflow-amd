import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from fastapi.testclient import TestClient
from inferflow.app import create_app
from inferflow.config import Settings
from inferflow.returns import ReturnFacts, check_return


def test_browser_policy_matches_python_for_every_boundary_combination():
    node = os.environ.get('NODE_EXECUTABLE') or shutil.which('node')
    if not node:
        pytest.skip('Node is needed to verify the public browser policy against Python')
    cases = [dict(zip(['days_since_delivery', 'unused', 'packaging_intact'], values))
             for values in itertools.product([None, 0, 7, 8, 10], [None, False, True], [None, False, True])]
    module = (Path(__file__).resolve().parents[1]/'inferflow/static/assets/return-policy.mjs').as_uri()
    script = "import {checkReturn} from " + json.dumps(module) + ";let s='';for await(const chunk of process.stdin)s+=chunk;console.log(JSON.stringify(JSON.parse(s).map(checkReturn)));"
    result = subprocess.run([node, '--input-type=module', '-e', script], input=json.dumps(cases), text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == [check_return(ReturnFacts(**case)).model_dump() for case in cases]


def test_english_local_assets_are_served_without_provider_calls(tmp_path, monkeypatch):
    app = create_app(Settings(db_path=str(tmp_path/'demo.db')))
    with TestClient(app) as client:
        async def forbidden(*args, **kwargs):
            pytest.fail('Reading the English app assets must not query the provider')
        monkeypatch.setattr(app.state.engine, 'submit', forbidden)
        for url in ['/', '/assets/app.mjs', '/assets/return-policy.mjs', '/assets/style.css']:
            response = client.get(url)
            assert response.status_code == 200
            assert not any('\u4e00' <= char <= '\u9fff' for char in response.text)
        assert client.post('/api/returns/check', json={'days_since_delivery': 10, 'unused': True, 'packaging_intact': True}).json()['status'] == 'conditions_not_met'
