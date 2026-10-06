"""Regresiones de permisos, migración y reconciliación con fuentes externas."""
import asyncio
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import httpx
import pytest

from client.scanner import scan_codebase
from client.rule_checker import check_rules
from client import youtrack_sync
from server.config import ClientKey, Settings, settings
from server.database import CREATE_TABLES_SQL, init_db


@pytest.fixture
def project(api_client, auth_headers):
    api_client.post('/api/sync', headers=auth_headers, json={'project': {'id': 'p', 'name': 'Original'}}).raise_for_status()
    return api_client, auth_headers


def test_sync_failure_rolls_back_catalog_project_and_rules(project, monkeypatch):
    client, headers = project
    from server.services.rules_service import RulesService
    async def fail(*args, **kwargs):
        raise ValueError('Fallo controlado')
    monkeypatch.setattr(RulesService, 'add_rule', fail)
    response = client.post('/api/sync', headers=headers, json={'project': {'id': 'p', 'name': 'Changed'},
        'components': [{'name': 'Wrong', 'file_path': 'Wrong.tsx'}], 'rules': [{'title': 'Rule', 'rule_content': 'Text'}]})
    assert response.status_code == 422
    assert client.get('/api/projects', headers=headers).json()[0]['name'] == 'Original'
    assert client.get('/api/projects/p/components', headers=headers).json() == []
    assert client.get('/api/projects/p/rules', headers=headers).json()['rules'] == []


def test_writer_cannot_partially_sync_active_rules(project, monkeypatch):
    client, admin = project
    key = ClientKey(key='writer-test-123456789', user_id='A', projects=['p'], role='writer')
    monkeypatch.setattr(settings, 'CLIENT_KEYS', [key])
    response = client.post('/api/sync', headers={'X-HAE-Key': key.key}, json={'project': {'id': 'p', 'name': 'Changed'},
        'components': [{'name': 'Wrong', 'file_path': 'Wrong.tsx'}], 'rules': [{'title': 'Rule', 'rule_content': 'Text'}]})
    assert response.status_code == 403
    assert client.get('/api/projects', headers=admin).json()[0]['name'] == 'Original'
    assert client.get('/api/projects/p/components', headers=admin).json() == []


@pytest.mark.parametrize('body', [[], None, 42, {'method': 'tools/call', 'params': None},
    {'method': 'tools/call', 'params': {'arguments': []}}, {'method': 'tools/call', 'params': {'name': []}}])
def test_malformed_scoped_mcp_messages_do_not_crash(project, monkeypatch, body):
    client, _ = project
    key = ClientKey(key='reader-test-123456789', user_id='A', projects=['p'])
    monkeypatch.setattr(settings, 'CLIENT_KEYS', [key])
    assert client.post('/messages/', headers={'X-HAE-Key': key.key}, content=json.dumps(body)).status_code == 400


def test_partial_catalog_warns_and_has_provenance(project):
    client, headers = project
    payload = {'project': {'id': 'p', 'name': 'Original'}, 'mode': 'merge', 'repository': {'repo_id': 'front',
        'scanned_at': datetime.now(timezone.utc).isoformat(), 'complete': False}}
    assert client.post('/api/sync', headers=headers, json=payload).status_code == 200
    assert 'INCOMPLETO' in client.get('/api/projects/p/summary', headers=headers).json()['summary']
    assert client.get('/api/projects/p/repositories', headers=headers).json()['repositories'][0]['complete'] == 0


def test_full_task_snapshot_requires_start_time_and_preserves_new_events(project):
    client, headers = project
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
    for external_id, updated in [('TSO-1', int(cutoff.timestamp()*1000)-1), ('TSO-2', int(cutoff.timestamp()*1000)+1)]:
        client.post('/api/projects/p/tasks', headers=headers, json={'external_id': external_id, 'title': external_id,
            'source_updated_at': updated}).raise_for_status()
    assert client.post('/api/projects/p/tasks/sync', headers=headers, json={'tasks': [], 'complete': True}).status_code == 422
    assert client.post('/api/projects/p/tasks/sync', headers=headers, json={'tasks': [], 'complete': True,
        'observed_at': cutoff.isoformat()}).status_code == 200
    assert [t['external_id'] for t in client.get('/api/projects/p/tasks', headers=headers).json()['active_tasks']] == ['TSO-2']


def test_authoritative_reassignment_recomputes_all_memberships(project, monkeypatch):
    client, admin = project
    keys = [ClientKey(key=f'reader-{user}-123456789', user_id=user, projects=['p']) for user in ['A', 'B']]
    monkeypatch.setattr(settings, 'CLIENT_KEYS', keys)
    for updated, assignees in [(100, ['A']), (200, ['B'])]:
        client.post('/api/projects/p/tasks', headers=admin, json={'external_id': 'TSO-1', 'title': 'Ticket',
            'source_updated_at': updated, 'assignee_ids': assignees}).raise_for_status()
    for key, expected in zip(keys, [0, 1]):
        assert client.get('/api/projects/p/tasks/TSO-1', headers={'X-HAE-Key': key.key}).json()['is_mine'] == expected
    # Un envío legado sin fecha no debe sobrescribir una fuente con versión.
    client.post('/api/projects/p/tasks', headers=admin, json={'external_id': 'TSO-1', 'title': 'Old'}).raise_for_status()
    assert client.get('/api/projects/p/tasks/TSO-1', headers=admin).json()['title'] == 'Ticket'


def test_legacy_database_migrates_twice_without_losing_ids(auth_headers):
    from pathlib import Path
    Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.DB_PATH) as db:
        db.executescript(CREATE_TABLES_SQL)
        db.execute("INSERT INTO projects(id,name) VALUES('old','Old')")
        db.execute("INSERT INTO components(id,project_id,name,file_path) VALUES(77,'old','Card','Card.tsx')")
        db.execute("INSERT INTO architectural_rules(id,project_id,title,rule_content) VALUES(66,'old','Rule','Text')")
        db.execute("INSERT INTO session_logs(id,project_id,summary) VALUES(55,'old','Session')")
        db.execute("INSERT INTO project_tasks(id,project_id,external_id,title) VALUES(44,'old','TSO-1','Task')")
    asyncio.run(init_db())
    asyncio.run(init_db())
    with sqlite3.connect(settings.DB_PATH) as db:
        assert db.execute('SELECT id,repo_id,branch FROM components').fetchone() == (77, 'legacy', '')
        assert db.execute('SELECT id,summary FROM session_logs').fetchone() == (55, 'Session')
        assert db.execute('SELECT id,title,archived FROM project_tasks').fetchone() == (44, 'Task', 0)
        assert db.execute('SELECT rule_id,revision,rule_content FROM rule_revisions').fetchall() == [(66, 1, 'Text')]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_incomplete_files_cannot_pass_as_complete(tmp_path):
    (tmp_path/'huge.ts').write_text('x'*1_000_001)
    diagnostics = {}
    assert scan_codebase(tmp_path, diagnostics) == ([], [])
    assert diagnostics['errors'][0]['reason'] == 'Archivo supera 1 MB'
    with pytest.raises(ValueError, match='incompleto'):
        check_rules(tmp_path, [{'title': 'Lines', 'check_spec': {'kind': 'max_file_lines', 'limit': 10}}])
    (tmp_path/'huge.ts').unlink()
    (tmp_path/'bad.ts').write_bytes(b'export const x = "\xff";')
    scan_codebase(tmp_path, diagnostics)
    assert diagnostics['errors'][0]['reason'] == 'UnicodeDecodeError'


def test_youtrack_paginates_and_retains_resolved_records(monkeypatch):
    responses = [io.BytesIO(json.dumps([{'idReadable': f'TSO-{i}', 'resolved': 10, 'updated': 20} for i in range(2)]).encode()),
                 io.BytesIO(b'[{"idReadable":"TSO-2","resolved":null,"updated":20}]')]
    network = Mock(side_effect=responses)
    monkeypatch.setattr(youtrack_sync.urllib.request, 'urlopen', network)
    tasks = youtrack_sync.fetch_youtrack_issues('https://youtrack.invalid', 'token', top=2)
    assert len(tasks) == 3 and tasks[0]['resolved'] == 10
    assert '$skip=0' in network.call_args_list[0].args[0].full_url
    assert '$skip=2' in network.call_args_list[1].args[0].full_url


@pytest.mark.parametrize('values', [
    {'CLIENT_KEYS': [{'key': 'admin-test-123456789', 'user_id': 'A', 'projects': ['p']}]},
    {'WEBHOOK_TOKEN': 'short'},
    {'CLIENT_KEYS': [{'key': 'reader-test-123456789', 'user_id': 'A', 'projects': [' ']}]},
])
def test_invalid_credential_configuration_fails_at_startup(values):
    config = Settings(_env_file=None, API_KEY='admin-test-123456789', **values)
    with pytest.raises(RuntimeError):
        config.validate_api_key()


@pytest.fixture
def webhook(project, monkeypatch):
    client, headers = project
    monkeypatch.setattr(settings, 'WEBHOOK_TOKEN', 'webhook-test-token-with-32-characters')
    monkeypatch.setattr(settings, 'YOUTRACK_URL', 'https://youtrack.invalid')
    monkeypatch.setattr(settings, 'YOUTRACK_TOKEN', 'secret-test-token')
    monkeypatch.setattr(settings, 'YOUTRACK_PROJECTS', {'p': 'TSO'})
    event = {'event': 'issueUpdated', 'id': '2-1', 'project': {'shortName': 'TSO'},
        'timestamp': datetime.now(timezone.utc).isoformat(), 'changedFields': [{'name': 'summary', 'value': 'Partial'}]}
    return client, headers, {'X-YouTrack-Token': settings.WEBHOOK_TOKEN}, event


def test_webhook_auth_idempotency_and_authoritative_fetch(webhook, monkeypatch):
    client, headers, hook_headers, event = webhook
    calls = []
    async def get(self, url, **kwargs):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request('GET', url), json={'id': '2-1', 'idReadable': 'TSO-1',
            'summary': 'Authoritative', 'description': 'Full requirements', 'updated': 100, 'resolved': None,
            'customFields': [{'name': 'Assignee', 'value': [{'id': 'A', 'name': 'Ana'}]}]})
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    assert client.post('/api/webhooks/youtrack/p', headers=headers, json=event).status_code == 401
    assert client.post('/api/webhooks/youtrack/p', headers=hook_headers, json={**event, 'project': {'shortName': 'BAD'}}).status_code == 403
    first = client.post('/api/webhooks/youtrack/p', headers=hook_headers, json=event).json()
    second = client.post('/api/webhooks/youtrack/p', headers=hook_headers, json=event).json()
    assert first == second and first['status'] == 'done' and len(calls) == 1
    reordered = {**event, 'changedFields': [{'value': 'Partial', 'name': 'summary'}]}
    assert client.post('/api/webhooks/youtrack/p', headers=hook_headers, json=reordered).json() == first
    assert len(calls) == 1
    task = client.get('/api/projects/p/tasks/TSO-1', headers=headers).json()
    assert task['title'] == 'Authoritative' and task['description'] == 'Full requirements'
    assert task['assignee_ids'] == ['A']


def test_failed_webhook_is_durable_and_can_retry(webhook, monkeypatch):
    client, headers, hook_headers, event = webhook
    async def failing(self, url, **kwargs):
        raise httpx.ConnectError('Must not expose secret-test-token')
    monkeypatch.setattr(httpx.AsyncClient, 'get', failing)
    result = client.post('/api/webhooks/youtrack/p', headers=hook_headers, json=event).json()
    assert result['status'] == 'failed'
    events = client.get('/api/projects/p/webhook-events', headers=headers).json()['events']
    assert events[0]['attempts'] == 1 and events[0]['last_error'] == 'ConnectError'
    async def get(self, url, **kwargs):
        return httpx.Response(200, request=httpx.Request('GET', url), json={'id': '2-1', 'idReadable': 'TSO-1', 'summary': 'Recovered', 'updated': 200})
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    retry = client.post(f"/api/projects/p/webhook-events/{result['event_id']}/retry", headers=headers)
    assert retry.status_code == 200 and retry.json()['status'] == 'done'
    assert client.get('/api/projects/p/webhook-events', headers=headers).json()['events'][0]['status'] == 'done'
    assert client.get('/api/projects/p/tasks/TSO-1', headers=headers).json()['title'] == 'Recovered'


def test_out_of_order_delete_cannot_archive_newer_task(webhook):
    client, headers, hook_headers, event = webhook
    stamp = int(datetime.fromisoformat(event['timestamp']).timestamp()*1000)
    client.post('/api/projects/p/tasks', headers=headers, json={'external_id': 'TSO-1', 'title': 'Newer',
        'source_id': '2-1', 'source_updated_at': stamp+100}).raise_for_status()
    result = client.post('/api/webhooks/youtrack/p', headers=hook_headers, json={**event, 'event': 'issueDeleted'}).json()
    assert result['status'] == 'done'
    assert client.get('/api/projects/p/tasks/TSO-1', headers=headers).json()['archived'] == 0
    newer = {**event, 'event': 'issueDeleted', 'timestamp': (datetime.fromisoformat(event['timestamp'])+timedelta(seconds=1)).isoformat()}
    assert client.post('/api/webhooks/youtrack/p', headers=hook_headers, json=newer).json()['status'] == 'done'
    assert client.get('/api/projects/p/tasks/TSO-1', headers=headers).json()['archived'] == 1
    client.post('/api/projects/p/tasks', headers=headers, json={'external_id': 'TSO-1', 'title': 'Stale',
        'source_id': '2-1', 'source_updated_at': stamp+200}).raise_for_status()
    assert client.get('/api/projects/p/tasks/TSO-1', headers=headers).json()['archived'] == 1
