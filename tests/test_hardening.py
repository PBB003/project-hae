import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from client import ast_parser
from client.gdrive_sync import parse_gemini_notes, extract_text_from_doc
from client.rule_checker import check_rules, validate_check_spec
from client.repository import repository_roots, verify_agent_identity
from server.config import ClientKey, settings
from server.database import init_db, get_db_connection, CREATE_TABLES_SQL


@pytest.fixture
def project(api_client,auth_headers):
    assert api_client.post('/api/sync',headers=auth_headers,json={'project':{'id':'p','name':'P'}}).status_code == 200
    return api_client,auth_headers


def snapshot(client,headers,repo='front',branch='main',revision=0,names=('Card',),**extra):
    payload={'project':{'id':'p','name':'P'},'mode':'snapshot',
        'repository':{'repo_id':repo,'branch':branch,'commit_sha':f'commit-{revision}',
                      'scanned_at':datetime.now(timezone.utc).isoformat(),'expected_revision':revision,**extra},
        'components':[{'name':name,'file_path':f'src/{name}.tsx'} for name in names]}
    return client.post('/api/sync',headers=headers,json=payload)


def test_snapshots_isolate_repos_and_branches(project):
    client,headers=project
    assert snapshot(client,headers).status_code == 200
    assert snapshot(client,headers,repo='back').status_code == 200
    assert snapshot(client,headers,branch='feature').status_code == 200
    assert snapshot(client,headers,revision=1,names=('Modal',)).status_code == 200
    rows=client.get('/api/projects/p/components',headers=headers).json()
    assert {(r['repo_id'],r['branch'],r['name']) for r in rows} == {('front','main','Modal'),('front','feature','Card'),('back','main','Card')}
    only=client.get('/api/projects/p/components?repo_id=front&branch=main',headers=headers).json()
    assert [row['name'] for row in only] == ['Modal']
    assert only[0]['revision'] == 2
    assert only[0]['commit_sha'] == 'commit-1'


def test_stale_revision_and_empty_snapshots_are_rejected(project):
    client,headers=project
    assert snapshot(client,headers).status_code == 200
    assert snapshot(client,headers,names=('Wrong',)).status_code == 409
    assert snapshot(client,headers,revision=1,names=()).status_code == 409
    assert snapshot(client,headers,revision=1,complete=False).status_code == 422
    assert [row['name'] for row in client.get('/api/projects/p/components',headers=headers).json()] == ['Card']
    assert snapshot(client,headers,revision=1,names=(),allow_empty=True).status_code == 200
    assert client.get('/api/projects/p/components',headers=headers).json() == []


def test_older_timestamp_and_wrong_commit_are_rejected(project):
    client,headers=project
    assert snapshot(client,headers).status_code == 200
    assert snapshot(client,headers,revision=1,scanned_at=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()).status_code == 409
    assert client.get('/api/projects/p/summary?repo_id=front&branch=main&expected_commit=wrong',headers=headers).status_code == 409
    assert client.get('/api/projects/p/summary?repo_id=unknown',headers=headers).status_code == 409
    assert client.get('/api/projects/p/summary?repo_id=front&expected_commit=commit-0',headers=headers).status_code == 200


def test_legacy_merge_preserves_omitted_symbols(project):
    client,headers=project
    for name in ['Card','Modal']:
        response=client.post('/api/sync',headers=headers,json={'project':{'id':'p','name':'P'},'components':[{'name':name,'file_path':f'{name}.tsx'}]})
        assert response.status_code == 200
    assert len(client.get('/api/projects/p/components',headers=headers).json()) == 2


@pytest.mark.parametrize('path',['../secret.ts','/absolute.ts','C:/secret.ts'])
def test_catalog_paths_are_relative(project,path):
    client,headers=project
    assert client.post('/api/sync',headers=headers,json={'project':{'id':'p','name':'P'},'components':[{'name':'Bad','file_path':path}]}).status_code == 422


def test_bilingual_search_ranks_create_project(project):
    client,headers=project
    response=client.post('/api/sync',headers=headers,json={'project':{'id':'p','name':'P'},'utilities':[
        {'name':'deleteProject','file_path':'OperationsService.ts','signature':'deleteProject(id: string)'},
        {'name':'createProject','file_path':'OperationsService.ts','signature':'createProject(input: CreateInput)'},
        {'name':'formatPrice','file_path':'price.ts'}]})
    assert response.status_code == 200
    rows=client.get('/api/projects/p/utilities',params={'query':'necesito crear un proyecto'},headers=headers).json()
    assert rows[0]['name'] == 'createProject'
    assert rows[0]['score'] > rows[1]['score']
    assert all(row['name'] != 'formatPrice' for row in rows)


def test_backend_methods_and_contracts_are_indexed():
    source='''export interface CreateProjectInput { name: string; certifiedPayroll?: boolean; }
    @Injectable() export class ProjectsService {
        async createProject(input: CreateProjectInput): Promise<Project> { return save(input); }
        private internal(): void {}
    }'''
    components,utilities=ast_parser.extract(source,'.ts','src/projects.service.ts',False,True,include_contracts=True)
    assert not components
    items={item['name']:item for item in utilities}
    assert 'ProjectsService.internal' not in items
    assert 'certifiedPayroll?: boolean' in items['ProjectsService.createProject']['contract']
    assert items['ProjectsService.createProject']['source_line'] == 3
    assert items['CreateProjectInput']['type'] == 'type'
    assert items['ProjectsService']['type'] == 'class'


def test_meetings_preserve_document_identity_and_original(project):
    client,headers=project
    original='Notas completas\n'*1000
    ids=[]
    for source_id,date in [('doc-old','2026-10-01'),('doc-new','2026-10-05')]:
        response=client.post('/api/projects/p/meetings',headers=headers,json={'title':'Daily','summary':'Resumen','meeting_date':date,'source_id':source_id,'raw_notes':original})
        assert response.status_code == 200
        ids.append(response.json()['meeting_note_id'])
    assert ids[0] != ids[1]
    update=client.post('/api/projects/p/meetings',headers=headers,json={'title':'Daily renombrada','summary':'Nuevo resumen','meeting_date':'2026-10-01','source_id':'doc-old','raw_notes':original})
    assert update.json()['meeting_note_id'] == ids[0]
    assert client.get('/api/projects/p/meetings/latest',headers=headers).json()['latest_meeting']['id'] == ids[1]
    assert client.get(f'/api/projects/p/meetings/{ids[0]}',headers=headers).json()['raw_notes'] == original
    asyncio.run(init_db())
    assert len(client.get('/api/projects/p/meetings',headers=headers).json()['meetings']) == 2


def test_gemini_extracts_all_next_steps_and_tables():
    actions=[f'Persona {n}: realizar acción {n}' for n in range(10)]
    summary,parsed=parse_gemini_notes('Resumen\nEste resumen no es un encabezado.\nPróximos pasos\n'+'\n'.join(actions))
    assert 'Este resumen no es un encabezado.' in summary
    assert parsed.splitlines() == actions
    paragraph=lambda text:{'paragraph':{'elements':[{'textRun':{'content':text}}]}}
    doc={'body':{'content':[paragraph('Inicio\n'),{'table':{'tableRows':[{'tableCells':[{'content':[paragraph('Acción\n')]}]}]}},paragraph('Fin')]}}
    assert extract_text_from_doc(doc) == 'Inicio\nAcción\nFin'


def test_task_snapshots_keep_partial_data_and_remove_closed_tasks(project):
    client,headers=project
    for external_id in ['TSO-1','TSO-2']:
        assert client.post('/api/projects/p/tasks',headers=headers,json={'external_id':external_id,'title':external_id}).status_code == 200
    payload={'tasks':[{'external_id':'TSO-1','title':'Completado','status':'Estado personalizado','resolved_at':123,'source_updated_at':200}], 'complete':False}
    assert client.post('/api/projects/p/tasks/sync',headers=headers,json=payload).status_code == 200
    assert [t['external_id'] for t in client.get('/api/projects/p/tasks',headers=headers).json()['active_tasks']] == ['TSO-2']
    payload['complete']=True
    payload['observed_at']=datetime.now(timezone.utc).isoformat()
    assert client.post('/api/projects/p/tasks/sync',headers=headers,json=payload).status_code == 200
    assert client.get('/api/projects/p/tasks',headers=headers).json()['active_tasks'] == []
    assert client.get('/api/projects/p/tasks/TSO-2',headers=headers).json()['archived'] == 1
    # Un evento anterior no puede reabrir un issue ya resuelto.
    assert client.post('/api/projects/p/tasks',headers=headers,json={'external_id':'TSO-1','title':'Viejo','source_updated_at':100}).status_code == 200
    assert client.get('/api/projects/p/tasks/TSO-1',headers=headers).json()['title'] == 'Completado'


def test_scoped_keys_and_personal_assignments(project,monkeypatch):
    client,admin=project
    keys=[ClientKey(key='reader-token-123456789',user_id='A',projects=['p'],role='reader'),
          ClientKey(key='writer-token-123456789',user_id='B',projects=['p'],role='writer')]
    monkeypatch.setattr(settings,'CLIENT_KEYS',keys)
    reader={'X-HAE-Key':keys[0].key}; writer={'X-HAE-Key':keys[1].key}
    assert client.post('/api/sync',headers=admin,json={'project':{'id':'other','name':'Other'}}).status_code == 200
    assert [p['id'] for p in client.get('/api/projects',headers=reader).json()] == ['p']
    assert client.get('/api/projects/other/summary',headers=reader).status_code == 403
    assert client.post('/api/projects/p/tasks',headers=reader,json={'external_id':'TSO-1','title':'X'}).status_code == 403
    for viewer,is_mine in [('A',True),('B',False)]:
        assert client.post('/api/projects/p/tasks/sync',headers=admin,json={'viewer_id':viewer,'tasks':[{'external_id':'TSO-1','title':'Tarea','is_mine':is_mine}]}).status_code == 200
    assert client.get('/api/projects/p/tasks',headers=reader).json()['active_tasks'][0]['is_mine'] == 1
    assert client.get('/api/projects/p/tasks',headers=writer).json()['active_tasks'][0]['is_mine'] == 0
    assert client.post('/api/projects/p/tasks/sync',headers=writer,json={'viewer_id':'A','tasks':[]}).status_code == 403
    assert client.delete('/api/projects/p',headers=writer).status_code == 403
    assert client.post('/messages/',headers=reader,json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'hae_save_session_log','arguments':{'project_id':'p','summary':'No'}}}).status_code == 403
    assert client.post('/messages/',headers=writer,json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'hae_get_project_context','arguments':{'project_id':'other'}}}).status_code == 403


def test_proposals_do_not_replace_active_rules_before_approval(project):
    client,headers=project
    first=client.post('/api/rules?project_id=p',headers=headers,json={'title':'Arquitectura','rule_content':'Regla aprobada'})
    assert first.status_code == 200
    rule_id=first.json()['rule_id']
    from server.mcp_server import hae_record_decision
    assert 'Propuesta' in asyncio.run(hae_record_decision('p','Arquitectura','Cambio propuesto'))
    assert client.get('/api/projects/p/rules',headers=headers).json()['rules'][0]['rule_content'] == 'Regla aprobada'
    history=client.get(f'/api/projects/p/rules/{rule_id}/history',headers=headers).json()['revisions']
    assert len(history) == 2 and history[0]['status'] == 'proposed'
    proposal=client.get('/api/projects/p/rules?status=proposed',headers=headers).json()['rules'][0]
    assert proposal['id'] == rule_id and proposal['rule_content'] == 'Cambio propuesto'
    assert client.post(f'/api/projects/p/rules/{rule_id}/approve/2',headers=headers).status_code == 200
    assert client.get('/api/projects/p/rules',headers=headers).json()['rules'][0]['rule_content'] == 'Cambio propuesto'


def test_context_budget_scope_and_session_evidence(project):
    client,headers=project
    for branch in ['main','feature']:
        assert client.post('/api/projects/p/sessions',headers=headers,json={'summary':'Resumen '+branch,'branch':branch,'ticket_id':'TSO-1','agent':'codex','commit_sha':'abc','evidence':'pytest: passed','pending_tasks':'X'*10000}).status_code == 200
    result=client.get('/api/projects/p/summary?branch=main&ticket_id=TSO-1&max_chars=1500',headers=headers)
    assert result.status_code == 200
    text=result.json()['summary']
    assert len(text)<=1500
    assert 'Resumen main' in text and 'Resumen feature' not in text
    session=client.get('/api/projects/p/sessions?branch=main',headers=headers).json()['sessions'][0]
    assert session['agent']=='codex' and session['evidence']=='pytest: passed' and session['actor']=='legacy'


def test_checks_report_lines_and_avoid_phantom_imports(tmp_path):
    (tmp_path/'code.ts').write_text('const text = "import x from forbidden";\n// import x from "forbidden";\nimport x from "forbidden";\n')
    rule={'title':'Imports','status':'active','check_spec':{'kind':'forbidden_import','module':'forbidden'}}
    result=check_rules(tmp_path,[rule,{'title':'Manual','status':'active'}])
    assert result['findings'] == [{'rule':'Imports','path':'code.ts','line':3,'severity':'error'}]
    assert result['unchecked_rules']==['Manual'] and result['passed'] is False


def test_checks_skip_generated_pytest_fixtures(tmp_path):
    artifacts = tmp_path / 'tmp_pytest'
    artifacts.mkdir()
    (artifacts / 'fixture.py').write_bytes(b'\xffinvalid generated fixture')
    (tmp_path / 'code.py').write_text('value = 1\n', encoding='utf-8')
    rule = {'title': 'Source only', 'check_spec': {'kind': 'forbidden_pattern', 'pattern': 'invalid'}}
    result = check_rules(tmp_path, [rule])
    assert result == {'findings': [], 'unchecked_rules': [], 'passed': True}


@pytest.mark.parametrize('spec',[{'kind':'exec','command':'do anything'},{'kind':'required_file','path':'../outside'},
                                {'kind':'forbidden_pattern','pattern':'['},{'kind':'max_file_lines','limit':0}])
def test_checks_reject_unsafe_or_invalid_specs(spec):
    with pytest.raises(ValueError): validate_check_spec(spec)


def test_identity_mismatch_and_repository_escape_fail(tmp_path):
    (tmp_path/'AGENTS.md').write_text('hae_get_project_context(project_id="wrong")')
    with pytest.raises(ValueError): verify_agent_identity(tmp_path,'right')
    with pytest.raises(ValueError): repository_roots(tmp_path,{'project':{'id':'p'},'repositories':[{'id':'repo','path':'..'}]})
