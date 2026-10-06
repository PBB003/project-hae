"""Cuenta recuperación de contexto con código real, sin generar ni publicar código.

No mide tokens facturados, calidad de código, tiempo de implementación ni bugs.
"""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path

import tiktoken

from client import ast_parser
from client.repository import git_metadata
from client.scanner import scan_codebase
from server.config import settings


CASES = [
    ('Checkbox', 'frontend', 'component', 'Checkbox', 'src/components/form/input/Checkbox.tsx', []),
    ('Crear proyecto: frontend', 'frontend', 'utility', 'createProject', 'src/infrastructure/services/OperationsService.ts', ['src/application/dtos/OperationsDTO.ts']),
    ('Crear proyecto: backend', 'backend', 'utility', 'ProjectService.createProject', 'src/operations/domain/services/project.service.ts', ['src/operations/ports/inbound/manage-projects.port.ts']),
    ('useModal', 'frontend', 'utility', 'useModal', 'src/hooks/useModal.ts', []),
    ('DateInput', 'frontend', 'component', 'DateInput', 'src/components/form/DateInput.tsx', []),
    ('ProjectFormModal', 'frontend', 'component', 'ProjectFormModal', 'src/components/projects/ProjectFormModal.tsx', ['src/domain/projects/types.ts']),
    ('ProjectsListTable', 'frontend', 'component', 'ProjectsListTable', 'src/components/projects/ProjectsListTable.tsx', ['src/domain/projects/types.ts', 'src/domain/finance/types.ts']),
]


def count(encoding, value):
    return len(encoding.encode(value, disallowed_special=()))


def serialized(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def legacy_search_response(kind, project_id, rows):
    """Formato congelado anterior a response_level; solo para comparar dos llamadas."""
    formatted = [f"Componentes encontrados en '{project_id}':" if kind == 'component'
                 else f"Utilidades/Hooks encontrados en '{project_id}':"]
    for r in rows:
        origin = f"en `{r['file_path']}`:{r.get('source_line') or '?'} | repo {r['repo_id']} | rama {r['branch']} | commit {r.get('commit_sha') or 'desconocido'}"
        if kind == 'component':
            props = f" | Props: {r['props_summary']}" if r.get('props_summary') else ''
            desc = f" ({r['description']})" if r.get('description') else ''
            formatted.append(f"• <{r['name']}/> {origin}{desc}{props}")
        else:
            sig = f" | Firma: {r['signature']}" if r.get('signature') else ''
            desc = f" - {r['description']}" if r.get('description') else ''
            formatted.append(f"• `{r['name']}` ({r.get('type', 'util')}) {origin}{sig}{desc}")
    return '\n'.join(formatted)


def legacy_row(row):
    return {key:value for key,value in row.items() if key not in
            ('type_dependencies','unresolved_types','type_resolution','exported','stale')}


def phase_two_response(row):
    fields = ('name','file_path','source_line','repo_id','branch','commit_sha','scanned_at','dirty','revision','complete')
    result = {key:row.get(key) for key in fields}
    result['contract'] = row.get('contract') or row.get('props_summary') or row.get('signature')
    result['contract_source'] = 'ast' if row.get('contract') else 'summary' if result['contract'] else 'missing'
    return serialized({'level':'contract','match':'exact','results':[result]})


def verify_dependencies(root, dependencies):
    """Contrastar declaraciones contra otra lectura de fuente, no omitir su coste."""
    fragments, hashes = [], {}
    for dep in dependencies:
        path = root/dep['file_path']
        text = path.read_text(encoding='utf-8-sig')
        hashes[dep['file_path']] = hashlib.sha256(text.encode()).hexdigest()
        if dep['contract'].rstrip(';') not in text:
            _, symbols = ast_parser.extract(text,path.suffix,dep['file_path'],False,True,include_contracts=True)
            if not any(x['name']==dep['name'] and x.get('contract')==dep['contract'] for x in symbols):
                raise ValueError(f"Contrato importado no corresponde a fuente: {dep['name']}")
        fragments.append(dep['file_path']+'\n'+dep['contract'])
    return '\n'.join(fragments),hashes


def directed_source(source, extension, symbol):
    """Declaración sin implementación y declaraciones locales referenciadas.

    Es un baseline favorable al lector que ya localizó el símbolo: no intenta
    medir cómo ese lector eligió el archivo o entendió los requisitos.
    """
    tree = ast_parser._parser_for(extension).parse(source.encode())
    root = tree.root_node
    simple_name = symbol.rsplit('.', 1)[-1]
    stack, found, declarations = [root], None, {}
    while stack:
        node = stack.pop()
        name = node.child_by_field_name('name')
        if name is not None:
            text = ast_parser._txt(name)
            if node.type in ('interface_declaration', 'type_alias_declaration', 'class_declaration'):
                declarations[text] = node
            if text == simple_name and node.type in ('function_declaration', 'method_definition', 'variable_declarator'):
                found = node
        stack.extend(reversed(node.named_children))
    if found is None:
        raise ValueError(f'No se encontró declaración: {symbol}')
    fn = found
    if found.type == 'variable_declarator':
        fn, _ = ast_parser._unwrap(found.child_by_field_name('value'))
    body = fn.child_by_field_name('body') if fn is not None else None
    end = body.start_byte if body is not None else found.end_byte
    header = source.encode()[found.start_byte:end].decode()
    fragments = [header]
    reference_stack, references = list(found.named_children), set()
    while reference_stack:
        current = reference_stack.pop()
        if body is not None and current == body:
            continue
        if current.type == 'type_identifier':
            references.add(ast_parser._txt(current))
        reference_stack.extend(current.named_children)
    for name in sorted(references):
        if name in declarations:
            fragments.append(ast_parser._txt(declarations[name]))
    return '\n'.join(fragments)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--mode', choices=('legacy', 'optimized', 'advanced'), default='advanced')
    parser.add_argument('--output')
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    os.environ.setdefault('TIKTOKEN_CACHE_DIR',str(Path(__file__).resolve().parents[1]/'.hae-cache'/'tiktoken'))
    roots = {'frontend': workspace/'tekniek-frontend', 'backend': workspace/'tekniek-backend'}
    encodings = {name: tiktoken.get_encoding(name) for name in ('o200k_base', 'cl100k_base')}
    scans = {}
    for repo_id, root in roots.items():
        print(f'Escaneando {repo_id}...',flush=True)
        diagnostics = {}
        components, utilities = scan_codebase(root, diagnostics)
        scans[repo_id] = (components, utilities, diagnostics)
    result = {'measured_at_utc': datetime.now(timezone.utc).isoformat(), 'tiktoken_version': importlib.metadata.version('tiktoken'),
        'mode': args.mode, 'hae_lookup_calls': len(CASES)*(2 if args.mode == 'legacy' else 1),
        'scope': 'Recuperación de interfaces de 7 símbolos preseleccionados de archivos reales de Tekniek',
        'method': 'Textos de argumentos/respuestas MCP, contexto inicial una vez, fuente completa o declaración dirigida; sin generación de código ni facturación',
        'scan': {repo: {'components': len(c), 'utilities': len(u), **d} for repo, (c,u,d) in scans.items()}, 'cases': [], 'totals': {}}
    original = settings.API_KEY, settings.DB_PATH
    with tempfile.TemporaryDirectory(prefix='hae-benchmark-') as directory:
        settings.API_KEY, settings.DB_PATH = 'benchmark-local-private-key-123456789', str(Path(directory)/'benchmark.db')
        try:
            from fastapi.testclient import TestClient
            from server.main import app
            from server import mcp_server as mcp
            with TestClient(app) as client:
                headers = {'X-HAE-Key': settings.API_KEY}
                for repo_id, root in roots.items():
                    components, utilities, diagnostics = scans[repo_id]
                    payload = {'project': {'id': 'benchmark-tekniek', 'name': 'Tekniek benchmark', 'tech_stack': 'React, TypeScript, NestJS'},
                        'repository': {'repo_id': repo_id, **git_metadata(root), 'scanned_at': datetime.now(timezone.utc).isoformat(),
                            'complete': not diagnostics['errors']}, 'mode': 'snapshot' if not diagnostics['errors'] else 'merge',
                        'components': components, 'utilities': utilities}
                    client.post('/api/sync', headers=headers, json=payload).raise_for_status()
                context = asyncio.run(mcp.hae_get_project_context('benchmark-tekniek'))
                context_payload = serialized({'name': 'hae_get_project_context', 'arguments': {'project_id': 'benchmark-tekniek'}})+context
                if args.mode == 'advanced':
                    initial_context_args = {'project_id':'benchmark-tekniek','if_none_match':''}
                    initial_context = json.loads(asyncio.run(mcp.hae_get_project_context(**initial_context_args)))
                    context_payload = serialized({'name':'hae_get_project_context','arguments':initial_context_args})+serialized(initial_context)
                    warm_context_args = {**initial_context_args,'if_none_match':initial_context['etag']}
                    warm_context = asyncio.run(mcp.hae_get_project_context(**warm_context_args))
                    if not json.loads(warm_context).get('not_modified'):
                        raise ValueError('El contexto sin cambios no se reutiliza')
                    warm_context_payload = serialized({'name':'hae_get_project_context','arguments':warm_context_args})+warm_context
                tools = asyncio.run(mcp.mcp_server.list_tools())
                schema_payload = serialized([tool.model_dump(mode='json', exclude_none=True) for tool in tools])
                for label, repo_id, kind, name, file, extra_files in CASES:
                    path = roots[repo_id]/file
                    source = path.read_text(encoding='utf-8-sig')
                    extras = '\n'.join((roots[repo_id]/extra).read_text(encoding='utf-8-sig') for extra in extra_files)
                    query_args = {'project_id': 'benchmark-tekniek', 'query': name, 'limit': 1, 'repo_id': repo_id}
                    from server.services.registry_service import RegistryService
                    if args.mode in ('legacy','optimized'):
                        service_search = RegistryService.search_components if kind == 'component' else RegistryService.search_utilities
                        if args.mode == 'legacy':
                            lookup = legacy_search_response(kind, 'benchmark-tekniek', asyncio.run(service_search(**query_args)))
                            detail_args = {'project_id': 'benchmark-tekniek', 'name': name, 'repo_id': repo_id}
                            rows=[]
                            for lookup_search in (RegistryService.search_components,RegistryService.search_utilities):
                                rows.extend(legacy_row(x) for x in asyncio.run(lookup_search('benchmark-tekniek',name,limit=50,repo_id=repo_id)) if x['name']==name)
                            detail=json.dumps(rows,ensure_ascii=False)
                        else:
                            hits=asyncio.run(service_search('benchmark-tekniek',name,limit=50,repo_id=repo_id))
                            exact=[x for x in hits if x['name']==name]
                            if len(exact)!=1:
                                raise ValueError('Baseline fase 2 no es único')
                            lookup=phase_two_response(exact[0])
                            rows=json.loads(lookup)['results']
                    else:
                        search = mcp.hae_search_components if kind == 'component' else mcp.hae_search_utilities
                        query_args['max_tokens'] = 16384
                        lookup = asyncio.run(search(**query_args))
                        response = json.loads(lookup)
                        if response['match'] != 'exact' or response['level'] != 'contract':
                            raise ValueError(f'La consulta no devuelve contrato único: {name}')
                        rows = response['results']
                        if response.get('partial'):
                            raise ValueError(f'No comparar un contrato omitido: {name}')
                    selected = [row for row in rows if row['file_path'] == file and row['name'] == name]
                    if len(selected) != 1:
                        raise ValueError(f'Catálogo no corresponde a fuente: {name}')
                    expected = [item for item in scans[repo_id][0 if kind == 'component' else 1]
                                if item['name'] == name and item['file_path'] == file]
                    if len(expected) != 1 or selected[0]['contract'] != expected[0]['contract']:
                        raise ValueError(f'El contrato entregado no es íntegro: {name}')
                    directed = file+'\n'+directed_source(source,path.suffix,name)+'\n'+extras
                    previous_directed = directed
                    full = file+'\n'+source+'\n'+extras
                    lookup_payload = serialized({'name': 'hae_search_'+('components' if kind == 'component' else 'utilities'), 'arguments': query_args})+lookup
                    if args.mode == 'legacy':
                        lookup_payload += serialized({'name': 'hae_get_symbol_detail', 'arguments': detail_args})+detail
                    if args.mode == 'advanced':
                        dependencies = rows[0].get('types', [])
                        if {(x['file_path'],x['name'],x['contract']) for x in dependencies} != {(x['file_path'],x['name'],x['contract']) for x in expected[0]['type_dependencies']}:
                            raise ValueError(f'No se entregó el cierre de tipos: {name}')
                        selective,dependency_hashes=verify_dependencies(roots[repo_id],dependencies)
                        directed=file+'\n'+directed_source(source,path.suffix,name)+'\n'+selective
                        expanded_files=sorted(set(extra_files)|{x['file_path'] for x in dependencies})
                        expanded_full=file+'\n'+source+'\n'+'\n'.join((roots[repo_id]/extra).read_text(encoding='utf-8-sig') for extra in expanded_files)
                        warm_args={**query_args,'if_none_match':response['etag']}
                        warm=asyncio.run(search(**warm_args))
                        if not json.loads(warm).get('not_modified'):
                            raise ValueError(f'El símbolo sin cambios no se reutiliza: {name}')
                        warm_payload=serialized({'name':'hae_search_'+('components' if kind=='component' else 'utilities'),'arguments':warm_args})+warm
                        direct_args={'project_id':'benchmark-tekniek','name':name,'file_path':file,'repo_id':repo_id,'max_tokens':16384}
                        direct=asyncio.run(mcp.hae_get_symbol(**direct_args))
                        if json.loads(direct)['results'][0]['contract']!=expected[0]['contract']:
                            raise ValueError(f'Consulta directa incorrecta: {name}')
                        direct_payload=serialized({'name':'hae_get_symbol','arguments':direct_args})+direct
                        default_budget=json.loads(asyncio.run(search(**{k:v for k,v in query_args.items() if k!='max_tokens'})))
                        compact_args={**query_args,'include_types':False}
                        compact=asyncio.run(search(**compact_args))
                        if 'types' in json.loads(compact)['results'][0]:
                            raise ValueError('Se repitieron tipos omitidos explícitamente')
                        compact_payload=serialized({'name':'hae_search_'+('components' if kind=='component' else 'utilities'),'arguments':compact_args})+compact
                    row = {'case': label, 'symbol': name, 'repo_id': repo_id, 'file': file, 'sha256': hashlib.sha256(source.encode()).hexdigest(),
                           'extra_source_files': extra_files,
                           'extra_source_sha256': {extra: hashlib.sha256((roots[repo_id]/extra).read_text(encoding='utf-8-sig').encode()).hexdigest() for extra in extra_files},
                           'tokens': {}}
                    if args.mode == 'advanced':
                        row.update(dependency_source_sha256=dependency_hashes,
                            resolved_types=[{'name':x['name'],'file_path':x['file_path']} for x in dependencies],
                            unresolved_types=rows[0].get('unresolved_types',[]),
                            default_budget_partial=bool(default_budget.get('partial')),default_budget_omissions=default_budget.get('budget'))
                    for encoding_name, encoding in encodings.items():
                        baseline = count(encoding,full)
                        aimed = count(encoding,directed)
                        index_only = count(encoding,lookup_payload)+(count(encoding,extras) if args.mode!='advanced' else 0)
                        verified = count(encoding,lookup_payload)+aimed
                        row['tokens'][encoding_name] = {'full_files': baseline, 'directed_source': aimed,
                            'hae_lookup_and_imported_contracts': index_only, 'hae_lookup_and_source_verification': verified,
                            'lookup_saving_vs_full_percent': round((1-index_only/baseline)*100,2),
                            'verified_saving_vs_full_percent': round((1-verified/baseline)*100,2)}
                        if args.mode=='advanced':
                            row['tokens'][encoding_name].update(full_files_with_dependency_graph=count(encoding,expanded_full),
                                hae_lookup_and_previous_source_verification=count(encoding,lookup_payload)+count(encoding,previous_directed),
                                hae_revalidation_only=count(encoding,warm_payload),
                                hae_direct_lookup_and_source_verification=count(encoding,direct_payload)+aimed,
                                hae_known_types_and_previous_source_verification=count(encoding,compact_payload)+count(encoding,previous_directed))
                    result['cases'].append(row)
                for name, encoding in encodings.items():
                    totals = {key: sum(row['tokens'][name][key] for row in result['cases']) for key in
                              ('full_files','directed_source','hae_lookup_and_imported_contracts','hae_lookup_and_source_verification')}
                    totals['context_initial'] = count(encoding,context_payload)
                    totals['all_hae_tool_schemas_optional'] = count(encoding,schema_payload)
                    totals['hae_session_verified'] = totals['hae_lookup_and_source_verification']+totals['context_initial']
                    totals['hae_session_verified_plus_all_schemas'] = totals['hae_session_verified']+totals['all_hae_tool_schemas_optional']
                    totals['saving_vs_full_percent'] = round((1-totals['hae_session_verified']/totals['full_files'])*100,2)
                    totals['saving_vs_directed_percent'] = round((1-totals['hae_session_verified']/totals['directed_source'])*100,2)
                    if args.mode=='advanced':
                        for key in ('full_files_with_dependency_graph','hae_lookup_and_previous_source_verification',
                                    'hae_revalidation_only','hae_direct_lookup_and_source_verification','hae_known_types_and_previous_source_verification'):
                            totals[key]=sum(row['tokens'][name][key] for row in result['cases'])
                        totals['context_revalidation_only']=count(encoding,warm_context_payload)
                        totals['hae_session_revalidation_only']=totals['hae_revalidation_only']+totals['context_revalidation_only']
                        totals['saving_vs_expanded_full_percent']=round((1-totals['hae_session_verified']/totals['full_files_with_dependency_graph'])*100,2)
                    result['totals'][name] = totals
        finally:
            settings.API_KEY, settings.DB_PATH = original
    output = Path(args.output or f'benchmarks/results/token_efficiency_{args.mode}.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output': str(output.resolve()), 'cases': len(result['cases']), 'totals': result['totals'],
                      'scan_errors': {repo: len(data['errors']) for repo,data in result['scan'].items()}},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
