"""Una consulta entrega el contrato sin ocultar ambigüedad o cobertura."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from server import mcp_server as mcp


@pytest.fixture
def catalog(api_client, auth_headers):
    contract = 'interface Props { title: string; optional?: boolean; }\n' + ('// contrato completo\n' * 250)
    payload = {'project': {'id': 'p', 'name': 'P'}, 'mode': 'snapshot',
        'repository': {'repo_id': 'front', 'branch': 'main', 'commit_sha': 'abc', 'dirty': True,
                       'scanned_at': datetime.now(timezone.utc).isoformat()},
        'components': [{'name': 'Card', 'file_path': 'src/Card.tsx', 'source_line': 3,
                        'props_summary': 'Props', 'description': 'Panel de proyectos', 'contract': contract}],
        'utilities': [{'name': 'useCard', 'file_path': 'src/useCard.ts',
                       'signature': 'useCard(): boolean', 'contract': 'function useCard(): boolean'}]}
    api_client.post('/api/sync', headers=auth_headers, json=payload).raise_for_status()
    return api_client, auth_headers, payload


def call(tool, **kwargs):
    return json.loads(asyncio.run(tool('p', **kwargs)))


@pytest.mark.parametrize('tool,name,kind', [(mcp.hae_search_components, 'Card', 'components'),
                                          (mcp.hae_search_utilities, 'useCard', 'utilities')])
def test_unique_exact_match_returns_complete_contract_in_one_call(catalog, tool, name, kind):
    _, _, payload = catalog
    response = call(tool, query=name.lower(), limit=1, max_tokens=16384)
    assert response['level'] == 'contract' and response['match'] == 'exact'
    assert response['results'][0]['contract'] == payload[kind][0]['contract']
    assert response['results'][0]['contract_source'] == 'ast'
    assert response['results'][0]['commit_sha'] == 'abc'
    assert response['results'][0]['complete'] == 1 and response['results'][0]['dirty'] == 1
    assert 'description' not in response['results'][0]


def test_location_omits_contract_and_detail_preserves_full_record(catalog):
    location = call(mcp.hae_search_components, query='Card', response_level='location')
    detail = call(mcp.hae_search_components, query='Card', response_level='detail', max_tokens=16384)
    assert location['results'][0]['file_path'] == 'src/Card.tsx'
    assert 'contract' not in location['results'][0] and 'props_summary' not in location['results'][0]
    assert detail['results'][0]['contract'] == catalog[2]['components'][0]['contract']
    assert detail['results'][0]['props_summary'] == 'Props'
    assert detail['results'][0]['description'] == 'Panel de proyectos'


def test_ambiguous_name_is_not_resolved_by_limit_one_and_scope_disambiguates(catalog):
    client, headers, payload = catalog
    payload['repository'] = {**payload['repository'], 'branch': 'feature'}
    client.post('/api/sync', headers=headers, json=payload).raise_for_status()
    response = call(mcp.hae_search_components, query='Card', limit=1)
    assert response['match'] == 'ambiguous' and response['level'] == 'location'
    assert len(response['results']) == 1 and 'contract' not in response['results'][0]
    scoped = call(mcp.hae_search_components, query='Card', repo_id='front', branch='feature', limit=1)
    assert scoped['match'] == 'exact' and scoped['level'] == 'contract'
    assert scoped['results'][0]['branch'] == 'feature'


def test_semantic_candidates_and_empty_search_do_not_claim_unique_contract(catalog):
    response = call(mcp.hae_search_components, query='panel de proyectos', limit=1)
    assert response['match'] == 'candidates' and response['level'] == 'location'
    empty = call(mcp.hae_search_components, query='xyz-no-match')
    assert empty['match'] == 'none' and empty['results'] == []
    assert 'cobertura' in empty['hint']


def test_summary_fallback_is_explicit_and_missing_manifest_is_retained(catalog):
    client, headers, _ = catalog
    client.post('/api/sync', headers=headers, json={'project': {'id': 'p', 'name': 'P'},
        'utilities': [{'name': 'legacyHook', 'file_path': 'hook.ts', 'signature': 'legacyHook(): void'}]}).raise_for_status()
    response = call(mcp.hae_search_utilities, query='legacyHook')
    assert response['results'][0]['contract_source'] == 'summary'
    assert response['results'][0]['revision'] is None and response['results'][0]['complete'] is None


def test_invalid_response_level_is_rejected(catalog):
    with pytest.raises(ValueError, match='response_level'):
        call(mcp.hae_search_components, query='Card', response_level='unknown')
