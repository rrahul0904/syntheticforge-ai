from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_home_and_health():
    assert client.get('/api/health').json()['status'] == 'ok'
    html = client.get('/').text
    assert 'SyntheticForge AI' in html
    assert 'Generate data' in html


def test_generate_and_all_exports():
    payload = {
        'database_type': 'snowflake', 'database_name': 'analytics', 'schema_name': 'raw',
        'table_name': 'payments', 'row_count': 4, 'seed': 12, 'columns': [],
        'foreign_keys': [], 'ai_inference': False,
    }
    generated = client.post('/api/generate', json=payload)
    assert generated.status_code == 200
    body = generated.json()
    assert len(body['rows']) == 4
    assert body['generation_mode'] == 'smart-local'
    for fmt in ('csv', 'json', 'ndjson', 'sql'):
        res = client.post(f'/api/export/{fmt}', json=payload)
        assert res.status_code == 200
        assert res.text.strip()
    sql = client.post('/api/export/sql', json=payload).text
    assert 'INSERT INTO "raw"."payments"' in sql


def test_provider_status_never_exposes_key(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER', 'openai-compatible')
    monkeypatch.setenv('AI_MODEL', 'example-model')
    monkeypatch.setenv('AI_API_KEY', 'top-secret')
    body = client.get('/api/provider-status').json()
    assert body['configured'] is True
    assert 'top-secret' not in str(body)
