import io
import json
import sys
import types
import urllib.error
from unittest.mock import Mock

import pytest

from client import ast_parser, gdrive_sync, hae_sync


def test_controller_dto_and_full_component_contracts():
    source = '''export class CreateDto {
        @IsString() name!: string;
        certifiedPayroll?: boolean = false;
        private token: string;
    }
    @Controller("projects") export class ProjectsController {
        @Post() createProject(@Body() dto: CreateDto): Promise<Project> { return IMPLEMENTATION; }
    }'''
    _, utilities = ast_parser.extract(source, '.ts', 'src/projects.controller.ts', False, False, include_contracts=True)
    contract = next(item['contract'] for item in utilities if item['name'] == 'ProjectsController.createProject')
    assert '@Controller("projects")' in contract and '@Post()' in contract
    assert '@IsString() name!: string' in contract and 'certifiedPayroll?: boolean = false' in contract
    assert 'token' not in contract and 'IMPLEMENTATION' not in contract
    props = '\n'.join(f'property{n}: string;' for n in range(50))
    components, _ = ast_parser.extract(f'interface Props {{{props}}}\nexport const Card: React.FC<Props> = props => <div/>;',
                                     '.tsx', 'Card.tsx', True, False, include_contracts=True)
    assert 'property49: string' in components[0]['contract']
    assert len(components[0]['props_summary']) <= 300
    assert not any(key.startswith('_') for key in components[0])


def test_exported_function_in_domain_folder_is_indexed():
    _, utilities = ast_parser.extract('export function mapProject(input: string) { return input; }',
        '.ts', 'src/domain/project.ts', False, False, include_contracts=True)
    assert utilities[0]['name'] == 'mapProject'
    assert 'return input' not in utilities[0]['contract']


def test_global_key_is_not_sent_to_a_different_local_server(tmp_path, monkeypatch):
    home = tmp_path/'home'; home.mkdir()
    project = tmp_path/'project'; project.mkdir()
    (home/'.hae.json').write_text(json.dumps({'server_url': 'https://trusted.invalid', 'api_key': 'private-key-123456789'}))
    (project/'hae.json').write_text(json.dumps({'server_url': 'https://different.invalid', 'project': {'id': 'p'}}))
    monkeypatch.setattr(hae_sync.Path, 'home', lambda: home)
    monkeypatch.delenv('HAE_API_KEY', raising=False)
    assert hae_sync.load_or_create_config(project)['api_key'] == ''
    monkeypatch.setenv('HAE_API_KEY', 'explicit-key')
    assert hae_sync.load_or_create_config(project)['api_key'] == 'explicit-key'


@pytest.mark.parametrize('version,errors', [(1, []), (2, ['bad syntax'])])
def test_sync_refuses_old_server_or_incomplete_scanner(tmp_path, monkeypatch, version, errors):
    monkeypatch.setattr(sys, 'argv', ['hae', 'sync', '--path', str(tmp_path)])
    monkeypatch.setattr(hae_sync, 'load_or_create_config', lambda _: {'project': {'id': 'p'}})
    monkeypatch.setattr(hae_sync, 'request_json', lambda _, path: {'api_version': version} if path == '/api/health' else {'repositories': []})
    def scan(path, diagnostics):
        diagnostics.update(engine='tree-sitter', errors=errors)
        return [], []
    monkeypatch.setattr(hae_sync, 'scan_codebase', scan)
    send = Mock()
    monkeypatch.setattr(hae_sync, 'sync_to_server', send)
    with pytest.raises(SystemExit) as error:
        hae_sync.main()
    assert error.value.code == 1
    send.assert_not_called()


@pytest.mark.parametrize('engine,errors,parser_error,expected', [
    ('regex', [], 'Se requiere tree-sitter==0.25.2', 'Se requiere tree-sitter==0.25.2'),
    ('tree-sitter', [{'path': 'src/broken.ts', 'reason': 'Sintaxis no reconocida por AST'}], '',
     'src/broken.ts: Sintaxis no reconocida por AST'),
])
def test_sync_reports_cause_without_uploading_incomplete_catalog(
        tmp_path, monkeypatch, capsys, engine, errors, parser_error, expected):
    monkeypatch.setattr(sys, 'argv', ['hae', 'sync', '--path', str(tmp_path)])
    monkeypatch.setattr(hae_sync, 'load_or_create_config', lambda _: {'project': {'id': 'p'}})
    monkeypatch.setattr(hae_sync, 'request_json', lambda _, path: {'api_version': 2} if path == '/api/health' else {'repositories': []})
    def scan(path, diagnostics):
        diagnostics.update(engine=engine, errors=errors, parser_error=parser_error, scanned_files=12)
        return [], []
    monkeypatch.setattr(hae_sync, 'scan_codebase', scan)
    send = Mock()
    monkeypatch.setattr(hae_sync, 'sync_to_server', send)
    with pytest.raises(SystemExit) as error:
        hae_sync.main()
    output = capsys.readouterr().out
    assert error.value.code == 1 and expected in output and 'archivos=12' in output
    assert sys.executable in output and 'no se reemplazará el catálogo' in output
    if engine == 'regex':
        assert '-m pip install "tree-sitter==0.25.2"' in output
    send.assert_not_called()


def test_scanner_reports_unavailable_parser_reason(tmp_path, monkeypatch):
    from client import scanner
    (tmp_path / 'service.ts').write_text('export function run() { return 1; }', encoding='utf-8')
    monkeypatch.setattr(scanner.ast_parser, 'AVAILABLE', False)
    monkeypatch.setattr(scanner.ast_parser, 'INIT_ERROR', 'Se requiere tree-sitter==0.25.2')
    diagnostics = {}
    scanner.scan_codebase(tmp_path, diagnostics)
    assert diagnostics['engine'] == 'regex'
    assert diagnostics['parser_error'] == 'Se requiere tree-sitter==0.25.2'
    assert diagnostics['scanned_files'] == 1


def test_jsx_url_ampersand_does_not_block_scan_or_change_contract(tmp_path):
    from client.scanner import scan_codebase
    source = 'export function FontLink(icon = <link href="https://fonts.example/css?family=Inter&display=swap" />) { return <div>{icon}</div>; }'
    path = tmp_path / 'FontLink.tsx'
    path.write_text(source, encoding='utf-8')
    tree = ast_parser._parser_for('.tsx').parse(source.encode())
    assert tree.root_node.has_error  # Reproduce the grammar's false positive.
    diagnostics = {}
    components, _ = scan_codebase(tmp_path, diagnostics)
    assert not diagnostics['errors']
    assert components[0]['name'] == 'FontLink'
    assert 'family=Inter&display=swap' in components[0]['contract']
    assert '&amp;' not in components[0]['contract']
    assert path.read_text(encoding='utf-8') == source


@pytest.mark.parametrize('source', [
    'export function Broken() { return <div href={broken(&display=swap)} />; }',
    'export function Broken() { return <div href="url&display=swap />; }',
    'export function Broken() { return <div href="url&display=swap"; }',
    'export function Broken() { return <div href="url&display=swap" />; const x = ; }',
])
def test_ampersand_compatibility_does_not_hide_real_syntax_errors(tmp_path, source):
    from client.scanner import scan_codebase
    (tmp_path / 'Broken.tsx').write_text(source, encoding='utf-8')
    diagnostics = {}
    assert scan_codebase(tmp_path, diagnostics) == ([], [])
    assert diagnostics['errors']
    assert all(error['path'] == 'Broken.tsx' and error['line'] >= 1 and error['column'] >= 1
               for error in diagnostics['errors'])


def test_offline_check_does_not_depend_on_folder_project_identity(tmp_path, monkeypatch):
    (tmp_path/'AGENTS.md').write_text('hae_get_project_context(project_id="correct")')
    rules = tmp_path/'rules.json'; rules.write_text('[{"title":"Instructions","check_spec":{"kind":"required_file","path":"AGENTS.md"}}]')
    monkeypatch.setattr(sys, 'argv', ['hae', 'check', '--path', str(tmp_path), '--rules-file', str(rules), '--strict'])
    monkeypatch.setattr(hae_sync, 'load_or_create_config', lambda _: {'project': {'id': 'different-folder-name'}})
    network = Mock(side_effect=AssertionError('No network'))
    monkeypatch.setattr(hae_sync, 'request_json', network)
    hae_sync.main()
    network.assert_not_called()


@pytest.fixture
def google_client(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive_sync.Path, 'home', lambda: tmp_path)
    (tmp_path/'.hae.json').write_text(json.dumps({'server_url': 'https://hae.invalid', 'api_key': 'test-key'}))
    monkeypatch.setattr(gdrive_sync, 'get_google_creds', lambda: object())
    drive, docs = Mock(), Mock()
    module = types.ModuleType('googleapiclient.discovery')
    module.build = lambda name, *args, **kwargs: drive if name == 'drive' else docs
    monkeypatch.setitem(sys.modules, 'googleapiclient', types.ModuleType('googleapiclient'))
    monkeypatch.setitem(sys.modules, 'googleapiclient.discovery', module)
    network = Mock(return_value=io.BytesIO(b'{}'))
    monkeypatch.setattr(gdrive_sync.urllib.request, 'urlopen', network)
    return drive, docs, network


def test_google_paginates_filters_and_saves_full_original(google_client):
    drive, docs, network = google_client
    drive.files().list().execute.side_effect = [
        {'nextPageToken': 'page-2', 'files': [{'id': 'unrelated', 'name': 'Daily - other'}]},
        {'files': [{'id': 'doc-1', 'name': 'Daily - tekniek 2026-10-05', 'webViewLink': 'https://docs.invalid/doc-1'}]}]
    original = 'Resumen ejecutivo\n' + 'Resumen completo\n'*1000 + 'Próximos pasos\nAcción uno\nAcción dos'
    docs.documents().get().execute.return_value = {'body': {'content': [{'paragraph': {'elements': [{'textRun': {'content': original}}]}}]}}
    assert gdrive_sync.sync_google_meetings_to_hae('tekniek') is True
    assert network.call_count == 1
    payload = json.loads(network.call_args.args[0].data)
    assert payload['source_id'] == 'doc-1' and payload['raw_notes'] == original
    assert len(payload['summary']) > 10000 and payload['action_items'] == 'Acción uno\nAcción dos'
    assert drive.files().list.call_args.kwargs['pageToken'] == 'page-2'


def test_google_returns_failure_for_unrelated_documents_or_fetch_error(google_client):
    drive, docs, network = google_client
    drive.files().list().execute.return_value = {'files': [{'id': 'other', 'name': 'Daily - other'}]}
    assert gdrive_sync.sync_google_meetings_to_hae('tekniek') is False
    network.assert_not_called()
    drive.files().list().execute.side_effect = TimeoutError('Unavailable')
    assert gdrive_sync.sync_google_meetings_to_hae('tekniek') is False
