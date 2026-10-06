import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from server import mcp_server as mcp
from server.config import settings
from server.policy import Identity,current_identity


@pytest.fixture
def catalog(api_client, auth_headers):
    payload = {'project': {'id':'p','name':'P'}, 'mode':'snapshot',
        'repository':{'repo_id':'front','branch':'main','commit_sha':'abc','scanned_at':datetime.now(timezone.utc).isoformat()},
        'utilities':[{'name':'run','file_path':'run.ts','contract':'function run(input: Input): void',
            'type_resolution':'complete','type_dependencies':[{'name':'Input','file_path':'types.ts','contract':'interface Input { name: string; }'}]}]}
    api_client.post('/api/sync',headers=auth_headers,json=payload).raise_for_status()
    return api_client,auth_headers,payload


def direct(**kwargs):
    return json.loads(asyncio.run(mcp.hae_get_symbol('p','run','run.ts',**kwargs)))


def test_direct_lookup_does_not_search_and_carries_types(catalog,monkeypatch):
    from server.services.registry_service import RegistryService
    async def forbidden(*args,**kwargs):
        raise AssertionError('No hacer ranking para símbolo conocido')
    monkeypatch.setattr(RegistryService,'_search',forbidden)
    response=direct(repo_id='front',branch='main')
    assert response['match']=='exact' and response['results'][0]['types'][0]['name']=='Input'
    assert direct(repo_id='wrong')['match']=='none'


def test_known_types_can_be_omitted_explicitly_and_do_not_share_validator(catalog):
    full=direct()
    compact=direct(include_types=False,if_none_match=full['etag'])
    assert not compact.get('not_modified')
    assert 'types' not in compact['results'][0]
    assert compact['results'][0]['types_available']==1
    assert compact['results'][0]['types_included'] is False


def test_conditional_response_is_invalidated_by_merge_and_snapshot(catalog):
    client,headers,payload=catalog
    initial=direct()
    assert direct(if_none_match=initial['etag']) == {'not_modified':True,'etag':initial['etag']}
    payload['mode']='merge'; payload['repository']['expected_revision']=1
    payload['utilities'][0]['type_dependencies'][0]['contract']='interface Input { name: string; added: boolean; }'
    client.post('/api/sync',headers=headers,json=payload).raise_for_status()
    changed=direct(if_none_match=initial['etag'])
    assert not changed.get('not_modified') and changed['etag']!=initial['etag']
    payload['mode']='snapshot'; payload['repository']['expected_revision']=2
    payload['utilities']=[]; payload['repository']['allow_empty']=True
    client.post('/api/sync',headers=headers,json=payload).raise_for_status()
    assert direct(if_none_match=changed['etag'])['match']=='none'


def test_cached_content_never_bypasses_permissions_or_user_scope(catalog):
    initial=direct()
    token=current_identity.set(Identity(user_id='reader',role='reader',projects=('p',)))
    try:
        response=direct(if_none_match=initial['etag'])
        assert not response.get('not_modified')
    finally:
        current_identity.reset(token)
    token=current_identity.set(Identity(user_id='reader',role='reader',projects=('other',)))
    try:
        with pytest.raises(HTTPException) as exc:
            direct(if_none_match=initial['etag'])
        assert exc.value.status_code==403
    finally:
        current_identity.reset(token)


def test_staleness_invalidates_validator(catalog,monkeypatch):
    client,headers,_=catalog
    import server.services.registry_service as registry
    before=direct()
    class Later(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime.now(timezone.utc)+timedelta(hours=settings.CATALOG_MAX_AGE_HOURS+1)
    monkeypatch.setattr(registry,'datetime',Later)
    after=direct(if_none_match=before['etag'])
    assert not after.get('not_modified') and after['results'][0]['stale'] is True


def test_budget_omits_whole_contracts_with_metadata_and_larger_budget_recovers(catalog):
    client,headers,payload=catalog
    payload['mode']='merge'; payload['repository']['expected_revision']=1
    large='interface Input { '+('field: string; '*150)+'}'
    payload['utilities'][0]['type_dependencies'][0]['contract']=large
    client.post('/api/sync',headers=headers,json=payload).raise_for_status()
    small=direct(max_tokens=1024)
    assert len(json.dumps(small,ensure_ascii=False,separators=(',',':')).encode())<=1024
    assert small['partial'] is True and small['budget']['omitted_types']==1
    assert small['results'][0]['contract']=='function run(input: Input): void'
    large_response=direct(max_tokens=8192,if_none_match=small['etag'])
    assert not large_response.get('not_modified')
    assert large_response['results'][0]['types'][0]['contract']==large


@pytest.mark.parametrize('budget',[0,511,65537])
def test_invalid_budgets_are_rejected(catalog,budget):
    with pytest.raises(ValueError,match='max_tokens'):
        direct(max_tokens=budget)


def test_rules_and_context_support_conditional_reuse_and_changed_rules_invalidate(catalog):
    client,headers,_=catalog
    call=lambda tool,**args:json.loads(asyncio.run(tool('p',**args)))
    initial=call(mcp.hae_get_rules,if_none_match='')
    assert call(mcp.hae_get_rules,if_none_match=initial['etag'])['not_modified']
    context=call(mcp.hae_get_project_context,if_none_match='')
    assert call(mcp.hae_get_project_context,if_none_match=context['etag'])['not_modified']
    client.post('/api/rules?project_id=p',headers=headers,json={'title':'Rule','rule_content':'Approved rule'}).raise_for_status()
    assert not call(mcp.hae_get_rules,if_none_match=initial['etag']).get('not_modified')
    assert not call(mcp.hae_get_project_context,if_none_match=context['etag']).get('not_modified')


def test_scanner_dependencies_round_trip_and_private_types_can_be_recovered(catalog,tmp_path):
    from client.scanner import scan_codebase
    (tmp_path/'types.ts').write_text('interface Private { id: string } export interface Input { data: Private }')
    (tmp_path/'run.ts').write_text('import { Input } from "./types"; export function run(input: Input): void {}')
    components,utilities=scan_codebase(tmp_path)
    client,headers,_=catalog
    client.post('/api/sync',headers=headers,json={'project':{'id':'p','name':'P'},'components':components,'utilities':utilities}).raise_for_status()
    known=json.loads(asyncio.run(mcp.hae_get_symbol('p','run','run.ts',repo_id='legacy')))
    assert {x['name'] for x in known['results'][0]['types']}=={'Input','Private'}
    private=json.loads(asyncio.run(mcp.hae_get_symbol('p','Private','types.ts',repo_id='legacy')))
    assert private['results'][0]['exported'] is False
    assert private['results'][0]['contract']=='interface Private { id: string }'


def test_unicode_budget_never_slices_contract_strings(catalog):
    from server.services.response_service import respond,upper_bound
    contract='interface Input { value: "'+('á🙂'*200)+'" }'
    response=respond({'level':'contract','results':[{'name':'Input','file_path':'types.ts','contract':contract}]},['p'],512)
    assert upper_bound(response)<=512 and response['partial'] is True
    assert 'contract' not in response['results'][0]


def test_budgeted_context_and_rules_mark_omissions(catalog):
    client,headers,_=catalog
    client.post('/api/rules?project_id=p',headers=headers,json={'title':'Large rule','rule_content':'Regla '*1000}).raise_for_status()
    rules=json.loads(asyncio.run(mcp.hae_get_rules('p',max_tokens=512)))
    assert rules['partial'] and rules['budget']['omitted_results']==1
    context=json.loads(asyncio.run(mcp.hae_get_project_context('p',max_tokens=512)))
    assert context['partial'] and context['budget']['omitted_contracts']==1
