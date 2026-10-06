import json

from client.scanner import scan_codebase


def workspace(tmp_path, sources, config=None):
    for path, text in sources.items():
        target = tmp_path/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    if config:
        (tmp_path/'tsconfig.json').write_text(config,encoding='utf-8')
    diagnostics = {}
    _, items = scan_codebase(tmp_path,diagnostics)
    assert not diagnostics['errors']
    return {x['name']:x for x in items}


def test_selective_transitive_types_aliases_and_no_unused_declarations(tmp_path):
    items = workspace(tmp_path,{
        'src/types.ts': 'interface Address { zip: string; } export interface Input { address: Address; } export interface Unused { secret: string; }',
        'src/service.ts': 'import type { Input as CreateInput } from "@/types"; export function create(input: CreateInput): string { return "ok"; }'},
        '{/* comentario */ "compilerOptions": { "paths": { "@/*": ["./src/*"], }, }, }')
    deps = {x['name']:x for x in items['create']['type_dependencies']}
    assert set(deps) == {'Input','Address'}
    assert deps['Input']['requested_as'] == 'CreateInput'
    assert deps['Address']['exported'] is False
    assert items['Address']['exported'] is False
    assert items['create']['type_resolution'] == 'complete'
    assert 'Unused' not in '\n'.join(x['contract'] for x in deps.values())


def test_barrel_reexports_default_and_namespace_imports(tmp_path):
    items = workspace(tmp_path,{
        'types.ts': 'export interface Input { name: string; } export default interface DefaultInput { value: number; }',
        'barrel.ts': 'export { Input as PublicInput } from "./types";',
        'service.ts': 'import { PublicInput } from "./barrel"; import Def from "./types"; import * as ns from "./types"; export function run(a: PublicInput, b: Def, c: ns.Input): void {}'})
    assert {x['name'] for x in items['run']['type_dependencies']} == {'Input','DefaultInput'}
    assert items['run']['unresolved_types'] == []


def test_cycles_terminate_and_ambiguous_star_exports_stay_unresolved(tmp_path):
    items = workspace(tmp_path,{
        'a.ts': 'import { B } from "./b"; export interface A { b: B; }',
        'b.ts': 'import { A } from "./a"; export interface B { a: A; }',
        'other.ts': 'export interface A { another: string; }',
        'barrel.ts': 'export * from "./a"; export * from "./other";',
        'good.ts': 'import { A } from "./a"; export function good(input: A): void {}',
        'bad.ts': 'import { A } from "./barrel"; export function bad(input: A): void {}'})
    assert len(items['good']['type_dependencies']) == 2
    assert items['good']['type_resolution'] == 'complete'
    assert items['bad']['unresolved_types'] == ['A']


def test_external_missing_types_and_generics_are_explicit(tmp_path):
    items = workspace(tmp_path,{'service.ts': 'import { External } from "package-not-installed"; export function run<T>(a: T, b: External, c: Missing): Promise<T> { return null as any; }'})
    assert items['run']['unresolved_types'] == ['External','Missing']
    assert items['run']['type_resolution'] == 'partial'


def test_relative_imports_cannot_escape_repository(tmp_path):
    items = workspace(tmp_path,{'service.ts': 'import { Secret } from "../outside"; export function run(a: Secret): void {}'})
    assert items['run']['type_dependencies'] == []
    assert items['run']['unresolved_types'] == ['Secret']


def test_inherited_paths_and_js_extension_resolution(tmp_path):
    items = workspace(tmp_path,{
        'tsconfig.base.json': json.dumps({'compilerOptions':{'baseUrl':'.','paths':{'shared/*':['src/*']}}}),
        'src/types.ts': 'export type Input = { id: string };',
        'service.ts': 'import { Input } from "shared/types.js"; export function run(a: Input): void {}'},
        '{"extends":"./tsconfig.base.json"}')
    assert items['run']['type_resolution'] == 'complete'
    assert items['run']['type_dependencies'][0]['file_path'] == 'src/types.ts'


def test_const_tuple_type_queries_are_selective_and_dynamic_values_stay_unresolved(tmp_path):
    items = workspace(tmp_path,{
        'types.ts':'export const VALUES = ["one", "two"] as const; export type Choice = (typeof VALUES)[number]; export const DYNAMIC = compute(); export type Unsupported = typeof DYNAMIC;',
        'service.ts':'import { Choice, Unsupported } from "./types"; export function run(a: Choice, b: Unsupported): void {}'})
    assert {x['name'] for x in items['run']['type_dependencies']} == {'Choice','VALUES','Unsupported'}
    assert items['run']['unresolved_types'] == ['DYNAMIC']
    assert items['VALUES']['type']=='constant'


def test_component_namespace_types_are_reported_as_external(tmp_path):
    target=tmp_path/'Card.tsx'
    target.write_text('import React from "react"; interface Props { title: string; } export const Card: React.FC<Props> = ({title}) => <div>{title}</div>;')
    components,_=scan_codebase(tmp_path)
    assert components[0]['unresolved_types'] == ['React.FC']


def test_local_nested_types_not_already_in_contract_are_delivered(tmp_path):
    items=workspace(tmp_path,{'run.ts':'interface Address { zip: string } interface Input { address: Address } export function run(a: Input): void {}'})
    assert {x['name'] for x in items['run']['type_dependencies']}=={'Address'}
    assert 'Input = {' in items['run']['contract']
    assert items['Address']['exported'] is False


def test_class_public_contract_resolves_heritage_and_method_types(tmp_path):
    items=workspace(tmp_path,{
        'types.ts':'export interface Input { name: string } export interface Output { result: string } export class Parent { value: string; } export interface Rule { enabled: boolean }',
        'child.ts':'import { Input, Output, Parent, Rule } from "./types"; export class Child extends Parent implements Rule { enabled: boolean; map(a: Input): Output { return null as any; } private secret(): Hidden { return null as any; } }',
        'run.ts':'import { Child } from "./child"; export function run(a: Child): void {}'})
    assert {x['name'] for x in items['run']['type_dependencies']}=={'Child','Input','Output','Parent','Rule'}
    assert items['run']['unresolved_types']==[]


def test_type_query_return_and_forwardref_imported_props_are_not_missed(tmp_path):
    items=workspace(tmp_path,{
        'types.ts':'export interface Props { value: string } export const VALUE = "literal" as const;',
        'get.ts':'import { VALUE } from "./types"; export function get(): typeof VALUE { return VALUE; }'})
    assert {x['name'] for x in items['get']['type_dependencies']}=={'VALUE'}
    (tmp_path/'Panel.tsx').write_text('import { forwardRef } from "react"; import { Props } from "./types"; export const Panel = forwardRef<HTMLDivElement, Props>((p, ref) => <div/>);')
    components,_=scan_codebase(tmp_path)
    panel=next(x for x in components if x['name']=='Panel')
    assert {x['name'] for x in panel['type_dependencies']}=={'Props'}
    assert 'HTMLDivElement' in panel['unresolved_types']
