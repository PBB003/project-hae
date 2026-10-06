"""Test del parser AST: python tests/test_ast.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client import ast_parser  # noqa: E402

import pytest

pytestmark = pytest.mark.skipif(not ast_parser.AVAILABLE, reason="Instala client/requirements.txt para probar el AST")

TSX = '''
import React, { memo, forwardRef } from "react";

interface ButtonProps {
  // variante visual
  variant?: "primary" | "secondary";
  onClick?: () => void;
}

/** Botón principal accesible */
export const Button = memo(forwardRef<HTMLButtonElement, ButtonProps>(function Button(props, ref) {
  return <button ref={ref} {...props} />;
}));

type CardProps = { title: string; children?: React.ReactNode };
export const Card: React.FC<CardProps> = ({ title }) => <div>{title}</div>;

export function Modal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  return isOpen ? <div onClick={onClose} /> : null;
}

function Tooltip({ text }: { text: string }) { return <span>{text}</span>; }
const Badge = () => <i />;
export { Tooltip, Badge as StatusBadge };

export const API_URL = "http://x";            // constante: NO es componente
export const note = "export function Fake() {}"; // texto: NO es componente
// export function Commented() {}
export default function Dashboard() { return <main />; }
'''

TS_HOOKS = '''
/** Obtiene el usuario actual */
export const useUser = (id: string): { name: string } => ({ name: id });
export function formatPrice(amount: number, currency = "USD"): string { return `${amount}`; }
export const slugify = async (s: string) => s.toLowerCase();
export * from "./other";
'''


def names(items):
    return sorted(i["name"] for i in items)


def test_react_exports_wrappers_and_props():
    comps, utils = ast_parser.extract(TSX, ".tsx", "src/components/ui/Mix.tsx", True, False)
    assert names(comps) == ["Button", "Card", "Dashboard", "Modal", "StatusBadge", "Tooltip"], names(comps)
    by = {c["name"]: c for c in comps}
    assert by["Button"]["description"] == "Botón principal accesible"
    assert "variant?" in by["Button"]["props_summary"] and "//" not in by["Button"]["props_summary"], by["Button"]
    assert "title: string" in by["Card"]["props_summary"], by["Card"]
    assert "isOpen: boolean" in by["Modal"]["props_summary"], by["Modal"]
    assert utils == [], utils

def test_hook_and_utility_signatures():
    comps, utils = ast_parser.extract(TS_HOOKS, ".ts", "src/lib/utils.ts", False, True)
    assert comps == [], comps
    assert names(utils) == ["formatPrice", "slugify", "useUser"], names(utils)
    u = {x["name"]: x for x in utils}
    assert u["useUser"]["type"] == "hook" and u["useUser"]["signature"] == "useUser(id: string): { name: string }", u["useUser"]
    assert u["formatPrice"]["signature"] == 'formatPrice(amount: number, currency = "USD"): string', u["formatPrice"]
    assert u["useUser"]["description"] == "Obtiene el usuario actual"

def test_broken_syntax_does_not_raise():
    ast_parser.extract("export function ( {{{ <div", ".tsx", "src/Broken.tsx", True, False)


@pytest.mark.parametrize("declaration", [
    'export function FilterTable({ filters: { range: [start, end] = [] } = {}, rows = [], ...rest }: Props) { return <table />; }',
    'export const FilterTable = ({ filters: { range: [start, end] = [] } = {}, rows = [], ...rest }: Props = {}) => <table />;',
    'export const FilterTable: React.FC<Props> = ({ rows = [], ...rest }) => <table />;',
    'export const FilterTable = memo(forwardRef<HTMLTableElement, Props>(({ rows = [], ...rest }, ref) => <table ref={ref}/>));',
])
def test_complex_destructuring_resolves_props(declaration):
    source = '''
    interface Props {
      /** Filtros opcionales */
      filters?: { range?: [Date, Date] };
      rows?: Array<{ id: string; label: string }>;
      onSelect?: (row: { id: string }) => void;
    }
    ''' + declaration
    components, utilities = ast_parser.extract(source, ".tsx", "src/components/FilterTable.tsx", True, False)
    assert names(components) == ["FilterTable"]
    props = components[0]["props_summary"]
    for expected in ("filters?", "range?: [Date, Date]", "rows?", "onSelect?"):
        assert expected in props
    assert "Filtros opcionales" not in props
    assert not utilities


def test_literal_urls_and_comment_markers_survive_type_cleanup():
    source = '''
    interface Props {
      // comentario real a omitir
      url: "https://example.com/a//b";
      marker: "/* literal */";
      label: "a; b";
      onChange?: () => void;
    }
    export const LinkCard = ({url, ...rest}: Props) => <a href={url}/>;
    '''
    components, _ = ast_parser.extract(source, ".tsx", "src/LinkCard.tsx", True, False)
    props = components[0]["props_summary"]
    for literal in ('"https://example.com/a//b"', '"/* literal */"', '"a; b"', "onChange?"):
        assert literal in props
    assert "comentario real" not in props


@pytest.mark.parametrize("ext", [".ts", ".tsx"])
def test_generic_hooks_preserve_type_parameters(ext):
    source = '''
    /** Memoria de un valor tipado */
    export function useValue<T extends object>(initial: T): T { return initial; }
    export const useList = <T,>(initial: T[]): T[] => initial;
    '''
    components, utilities = ast_parser.extract(source, ext, "src/hooks/useValue" + ext, False, True)
    assert not components
    by_name = {utility["name"]: utility for utility in utilities}
    assert by_name["useValue"]["signature"] == "useValue<T extends object>(initial: T): T"
    assert by_name["useList"]["signature"] == "useList<T,>(initial: T[]): T[]"
    assert all(utility["type"] == "hook" for utility in utilities)


def test_hooks_inside_component_file_are_catalogued():
    source = '''
    export function useFilters({range: [start, end] = []} = {}) { return {start, end}; }
    export const FilterPanel = () => <aside/>;
    const privateHelper = () => 42;
    export const API_URL = "https://example.com";
    '''
    components, utilities = ast_parser.extract(source, ".tsx", "src/components/FilterPanel.tsx", True, False)
    assert names(components) == ["FilterPanel"]
    assert names(utilities) == ["useFilters"]
    assert utilities[0]["type"] == "hook"


@pytest.mark.parametrize("source,path,expected", [
    ('export default function ({label}: {label: string}) { return <div/>; }', "src/cards/Item.tsx", "Item"),
    ('export default ({label}: {label: string}) => <div/>;', "src/overview/index.tsx", "Overview"),
])
def test_anonymous_default_exports(source, path, expected):
    components, _ = ast_parser.extract(source, ".tsx", path, True, False)
    assert names(components) == [expected]
    assert "label: string" in components[0]["props_summary"]


def test_reexports_comments_and_strings_do_not_create_phantom_symbols():
    source = '''
    export { Button } from "./Button";
    export * from "./hooks";
    // export function Commented() { return <div/>; }
    export const note = "export function Fake() {}";
    const Hidden = () => <div/>;
    '''
    components, utilities = ast_parser.extract(source, ".tsx", "src/index.tsx", True, False)
    assert components == utilities == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
