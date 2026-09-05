"""Selected-model readiness and real HTTP response contracts, without inference."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import ollafim as o


class TestHealthStatus(unittest.TestCase):
    def check(self, data, model='qwen2.5-coder:1.5b-base'):
        with patch.object(o, 'http_get_json', return_value=data) as getter, patch.object(o, 'stream_ollama') as generate:
            result = o.health_status('http://backend.test:11434/', model)
            getter.assert_called_once_with('http://backend.test:11434/api/tags')
            generate.assert_not_called()
            return result

    def test_selected_model_present(self):
        result = self.check({'models': [{'name': 'other:latest'}, {'name': 'qwen2.5-coder:1.5b-base'}]})
        self.assertTrue(result['ok']); self.assertTrue(result['model_available']); self.assertTrue(result['backend_reachable']); self.assertTrue(result['catalog_valid']); self.assertIsNone(result['reason'])

    def test_reachable_backend_with_missing_model_is_not_ready(self):
        for catalog in [[], [{'name': 'qwen3.8:27b'}, {'name': 'nomic-embed-text:latest'}]]:
            with self.subTest(catalog=catalog):
                result = self.check({'models': catalog})
                self.assertFalse(result['ok']); self.assertFalse(result['model_available']); self.assertTrue(result['backend_reachable']); self.assertTrue(result['catalog_valid']); self.assertEqual(result['reason'], 'model_missing')

    def test_backend_failure_is_distinct_from_missing_model(self):
        result = self.check(None)
        self.assertFalse(result['backend_reachable']); self.assertFalse(result['catalog_valid']); self.assertIsNone(result['model_available']); self.assertEqual(result['reason'], 'backend_unavailable')

    def test_malformed_catalog_cannot_claim_model_absence_or_readiness(self):
        for data in [{}, [], True, 'models', {'models': {}}, {'models': [None]}, {'models': ['model']}, {'models': [{}]}, {'models': [{'name': 123}]}, {'models': [{'name': '  '}]}, {'models': [{'name': 'qwen2.5-coder:1.5b-base'}, {}]}]:
            with self.subTest(data=data):
                result = self.check(data)
                self.assertFalse(result['ok']); self.assertTrue(result['backend_reachable']); self.assertIsNone(result['model_available']); self.assertEqual(result['reason'], 'invalid_catalog')

    def test_only_omitted_latest_tag_is_normalized(self):
        for selected, stored in [('qwen2.5-coder', 'qwen2.5-coder:latest'), ('team/coder', 'team/coder:latest'), ('registry.test:5000/team/coder', 'registry.test:5000/team/coder:latest')]:
            self.assertTrue(self.check({'models': [{'name': stored}]}, selected)['ok'])
        for stored in ['qwen2.5-coder:7b', 'team/qwen2.5-coder:latest', 'alias:latest', 'QWEN2.5-CODER:latest']:
            self.assertFalse(self.check({'models': [{'name': stored}]}, 'qwen2.5-coder')['ok'])


class TestHealthHTTP(unittest.TestCase):
    def setUp(self):
        handler = type('FixtureHandler', (o.Handler,), {'config': {'ollama': 'http://backend.test:11434', 'model': 'fixture-coder:latest'}})
        self.server = o.ThreadingServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True); self.thread.start()
        self.addCleanup(self.stop)
    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
    def request(self, data):
        with patch.object(o, 'http_get_json', return_value=data) as getter, patch.object(o, 'stream_ollama') as generate:
            try:
                response = urllib.request.urlopen('http://127.0.0.1:%d/health' % self.server.server_port, timeout=2)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = json.load(response); code = response.status
            getter.assert_called_once(); generate.assert_not_called()
            return code, body
    def test_http_success_preserves_existing_fields(self):
        code, body = self.request({'models': [{'name': 'fixture-coder:latest'}]})
        self.assertEqual(code, 200); self.assertEqual({k: body[k] for k in ['ok','ollama','model','version']}, {'ok': True, 'ollama':'http://backend.test:11434','model':'fixture-coder:latest','version':o.__version__})
    def test_http_503_for_all_nonready_conditions(self):
        for data, reason in [(None,'backend_unavailable'), ({},'invalid_catalog'), ({'models':[]},'model_missing')]:
            code, body = self.request(data); self.assertEqual(code,503); self.assertFalse(body['ok']); self.assertEqual(body['reason'],reason)
