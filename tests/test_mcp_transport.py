"""Integración local real: SSE, identidad del actor y propiedad de la sesión."""
import asyncio
import json
import socket
import threading
import time
import subprocess
import sys

import httpx
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

from server.config import ClientKey, settings


@pytest.fixture
def live_server(auth_headers, monkeypatch):
    from server.main import app
    keys = [ClientKey(key='reader-live-123456789', user_id='A', projects=['p']),
            ClientKey(key='writer-live-123456789', user_id='B', projects=['p'], role='writer')]
    monkeypatch.setattr(settings, 'CLIENT_KEYS', keys)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=url) as client:
            for pid in ['p', 'other']:
                client.post('/api/sync', headers=auth_headers, json={'project': {'id': pid, 'name': pid}}).raise_for_status()
        yield url, keys
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive(), 'El servidor local de prueba no se detuvo'


def test_scoped_mcp_context_and_actor_cross_real_sse(live_server, auth_headers):
    url, keys = live_server
    async def run():
        for key in keys:
            async with sse_client(url+'/sse', headers={'X-HAE-Key': key.key}, timeout=5, sse_read_timeout=10) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    projects = await session.call_tool('hae_list_projects', {})
                    text = '\n'.join(item.text for item in projects.content if hasattr(item, 'text'))
                    assert 'other' not in text and 'p' in text
                    context = await session.call_tool('hae_get_project_context', {'project_id': 'p'})
                    assert not context.is_error
                    if key.role == 'writer':
                        result = await session.call_tool('hae_save_session_log', {'project_id': 'p', 'summary': 'Real MCP session', 'evidence': 'SSE test'})
                        assert not result.is_error
                        proposal = await session.call_tool('hae_record_decision', {'project_id': 'p', 'title': 'MCP proposal', 'rule_content': 'Needs approval'})
                        assert not proposal.is_error
        async with httpx.AsyncClient(base_url=url) as client:
            logs = (await client.get('/api/projects/p/sessions', headers=auth_headers)).json()['sessions']
            assert logs[0]['actor'] == 'B'
            rules = (await client.get('/api/projects/p/rules?status=proposed', headers=auth_headers)).json()['rules']
            assert rules[0]['author'] == 'B'
            assert (await client.get('/api/projects/p/rules', headers=auth_headers)).json()['rules'] == []
    asyncio.run(asyncio.wait_for(run(), timeout=25))


def test_mcp_session_cannot_be_reused_with_different_credential(live_server):
    url, keys = live_server
    async def run():
        async with httpx.AsyncClient(base_url=url, timeout=5) as client:
            async with client.stream('GET', '/sse', headers={'X-HAE-Key': keys[0].key}) as stream:
                endpoint = None
                async for line in stream.aiter_lines():
                    if line.startswith('data: '):
                        endpoint = line[6:]
                        break
                assert endpoint and 'session_id=' in endpoint
                payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                    'params': {'name': 'hae_get_project_context', 'arguments': {'project_id': 'p'}}}
                response = await client.post(endpoint, headers={'X-HAE-Key': keys[1].key}, json=payload)
                assert response.status_code == 404
                denied = await client.post(endpoint, headers={'X-HAE-Key': keys[0].key}, json={**payload,
                    'params': {'name': 'hae_save_session_log', 'arguments': {'project_id': 'p', 'summary': 'Denied'}}})
                assert denied.status_code == 403
    asyncio.run(asyncio.wait_for(run(), timeout=15))


def test_search_levels_cross_real_sse(live_server, auth_headers):
    url, keys = live_server
    with httpx.Client(base_url=url) as client:
        client.post('/api/sync', headers=auth_headers, json={'project': {'id': 'p', 'name': 'P'},
            'utilities': [{'name': 'useReady', 'file_path': 'ready.ts', 'signature': 'useReady(): boolean',
                           'contract': 'function useReady(): boolean'}]}).raise_for_status()
    async def run():
        async with sse_client(url+'/sse', headers={'X-HAE-Key': keys[0].key}, timeout=5, sse_read_timeout=10) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                schema = next(t.input_schema for t in tools.tools if t.name == 'hae_search_utilities')
                assert schema['properties']['response_level']['enum'] == ['location', 'contract', 'detail']
                for level in ['location', 'contract', 'detail']:
                    result = await session.call_tool('hae_search_utilities', {'project_id': 'p', 'query': 'useReady', 'response_level': level})
                    assert not result.is_error
                    response = json.loads(result.content[0].text)
                    assert response['level'] == level
                    assert ('contract' in response['results'][0]) == (level != 'location')
                invalid = await session.call_tool('hae_search_utilities', {'project_id': 'p', 'response_level': 'unknown'})
                assert invalid.is_error
                exact_args = {'project_id':'p','name':'useReady','file_path':'ready.ts'}
                first = await session.call_tool('hae_get_symbol',exact_args)
                assert not first.is_error
                etag = json.loads(first.content[0].text)['etag']
                reused = await session.call_tool('hae_get_symbol',{**exact_args,'if_none_match':etag})
                assert not reused.is_error
                assert json.loads(reused.content[0].text)=={'not_modified':True,'etag':etag}
    asyncio.run(asyncio.wait_for(run(), timeout=15))


def test_cli_doctor_and_snapshot_work_against_local_server(live_server, tmp_path, auth_headers):
    url, keys = live_server
    workspace = tmp_path/'workspace'; workspace.mkdir()
    (workspace/'hae.json').write_text(json.dumps({'server_url': url, 'api_key': keys[1].key,
        'project': {'id': 'p', 'name': 'P'}, 'repositories': [{'id': 'frontend', 'path': '.'}], 'rules': []}))
    (workspace/'AGENTS.md').write_text('hae_get_project_context(project_id="p")')
    (workspace/'Card.tsx').write_text('export function Card(props: { name: string }) { return <div>{props.name}</div>; }')
    for command in ['doctor', 'sync']:
        result = subprocess.run([sys.executable, '-m', 'client.hae_sync', command, '--path', str(workspace)],
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        assert result.returncode == 0, result.stdout+result.stderr
        assert keys[1].key not in result.stdout+result.stderr
    with httpx.Client(base_url=url) as client:
        rows = client.get('/api/projects/p/components?repo_id=frontend', headers=auth_headers).json()
        assert len(rows) == 1 and rows[0]['name'] == 'Card' and rows[0]['complete'] == 1
        assert 'name: string' in rows[0]['contract']
