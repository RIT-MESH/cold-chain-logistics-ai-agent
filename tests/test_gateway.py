"""Contract tests that do not need live databases or LLM credentials."""
import os
from fastapi.testclient import TestClient
from api import main

client = TestClient(main.app)


def test_health():
    assert client.get('/health').status_code == 200


def test_requires_key(monkeypatch):
    monkeypatch.setenv('FDE_API_KEY', 'testing-secret')
    assert client.get('/v1/models').status_code == 401
    assert client.get('/v1/models', headers={'Authorization': 'Bearer testing-secret'}).status_code == 200


def test_chat_contract(monkeypatch):
    monkeypatch.setenv('FDE_API_KEY', 'testing-secret')
    # 2026-11-21 contract: get_answer returns (answer, meta) — meta carries
    # the token usage plus the prefetch markers (had_prefetch / sop_available).
    monkeypatch.setattr(main, 'get_answer', lambda messages: (
        'Test response', {'prompt_tokens': 3, 'completion_tokens': 5,
                          'reasoning_tokens': 2, 'total_tokens': 8,
                          'had_prefetch': False}))
    req = {'model': 'cold-chain-agent', 'messages': [{'role': 'user', 'content': 'Hello'}]}
    headers = {'Authorization': 'Bearer testing-secret'}
    resp = client.post('/v1/chat/completions', headers=headers, json=req)
    assert resp.status_code == 200
    assert resp.json()['choices'][0]['message']['content'] == 'Test response'
    assert resp.json()['usage']['total_tokens'] == 8  # C12-style counter wired (R6-1)
    resp = client.post('/v1/chat/completions', headers=headers, json={**req, 'stream': True})
    assert resp.status_code == 200
    assert 'data: [DONE]' in resp.text


def test_invalid_model(monkeypatch):
    monkeypatch.setenv('FDE_API_KEY', 'testing-secret')
    r = client.post('/v1/chat/completions', headers={'Authorization': 'Bearer testing-secret'}, json={
        'model': 'incorrect', 'messages': [{'role':'user', 'content':'hi'}]})
    assert r.status_code == 400
