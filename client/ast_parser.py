"""Extracción de componentes React, hooks y utilidades con tree-sitter (TS/TSX/JS/JSX).

Sustituye a las expresiones regulares: entiende `React.memo`, `forwardRef`, `React.FC<Props>`,
`export default`, `export { A, B as C }`, props inline y JSDoc, y no se confunde con
cadenas o comentarios. Si tree-sitter no está instalado, `AVAILABLE` es False y
scanner.py usa el método de regex como respaldo.
"""
import re
import importlib.metadata
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

try:
    # 0.26.0 produjo access violations durante GC en escaneos reales Windows.
    # No cargar esa extensión nativa en un entorno que todavía no se actualizó.
    if importlib.metadata.version("tree-sitter") != "0.25.2":
        raise RuntimeError("Se requiere tree-sitter==0.25.2; actualiza client/requirements.txt")
    import tree_sitter_typescript as _tst
    from tree_sitter import Language, Parser

    _LANGS = {
        "tsx": Language(_tst.language_tsx()),
        "ts": Language(_tst.language_typescript()),
    }
    AVAILABLE = True
except Exception as exc:  # pragma: no cover - depende del entorno
    _LANGS = {}
    AVAILABLE = False
    INIT_ERROR = str(exc)
else:
    INIT_ERROR = ""

_parsers: Dict[str, Any] = {}
_JSX_TYPES = {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}
_FN_TYPES = {"arrow_function", "function_expression", "function"}
_HOOK_RE = re.compile(r"^use[A-Z0-9]")
_IDENT_RE = re.compile(r"^[A-Za-z_$][\w$]*$")


def _parser_for(ext: str):
    key = "ts" if ext == ".ts" else "tsx"  # la gramática TSX también cubre JS/JSX
    if key not in _parsers:
        _parsers[key] = Parser(_LANGS[key])
    return _parsers[key]


def syntax_errors(root) -> List[Dict[str, Any]]:
    """Errores reales de sintaxis, conservando el texto y offsets originales.

    La gramática TSX marca ampersands sin entidad HTML en atributos JSX como
    ERROR (p. ej. una URL con &display=swap). Dentro de una cadena de atributo
    correctamente cerrada son texto válido; no suprimir errores de expresiones,
    etiquetas, comillas faltantes ni otros contextos.
    """
    errors = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == 'ERROR' or node.is_missing:
            parent = node.parent
            quoted_attribute = (parent is not None and parent.type == 'string'
                                and parent.parent is not None and parent.parent.type == 'jsx_attribute')
            value = parent.text if quoted_attribute else b''
            ampersand_text = (not node.is_missing and node.text.startswith(b'&')
                              and len(value) >= 2 and value[:1] in (b'"', b"'")
                              and value[-1:] == value[:1] and value[:1] not in node.text)
            if not (quoted_attribute and ampersand_text):
                errors.append({'reason': 'Sintaxis no reconocida por AST',
                               'line': node.start_point.row + 1, 'column': node.start_point.column + 1})
        if node.has_error:
            stack.extend(reversed(node.children))
    return errors


def _txt(node) -> str:
    return node.text.decode("utf-8", "ignore") if node is not None else ""


def _compact(s: str, limit: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _has_jsx(node) -> bool:
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in _JSX_TYPES:
            return True
        stack.extend(n.children)
    return False


def _doc_for(node) -> str:
    prev = node.prev_sibling
    if prev is None or prev.type != "comment":
        return ""
    raw = _txt(prev)
    if not raw.startswith("/**"):
        return ""
    body = raw[3:-2] if raw.endswith("*/") else raw[3:]
    lines = [re.sub(r"^\s*\*\s?", "", ln).strip() for ln in body.splitlines()]
    return " ".join(ln for ln in lines if ln and not ln.startswith("@"))[:150]


def _unwrap(value) -> Tuple[Optional[Any], List[str]]:
    """Desenvuelve memo(forwardRef<El, Props>((p, ref) => ...)) y similares.
    Devuelve (nodo_función, [argumentos_genéricos])."""
    generics: List[str] = []
    v = value
    while v is not None and v.type in (
        "call_expression", "parenthesized_expression", "as_expression", "satisfies_expression"
    ):
        if v.type == "call_expression":
            targs = v.child_by_field_name("type_arguments")
            if targs is not None:
                generics.extend(_txt(c) for c in targs.named_children)
            args = v.child_by_field_name("arguments")
            v = args.named_children[0] if args is not None and args.named_children else None
        else:
            v = v.named_children[0] if v.named_children else None
    if v is not None and v.type in _FN_TYPES:
        return v, generics
    return None, generics


def _clean_type_text(raw: str) -> str:
    # Los comentarios y separadores se identifican por AST: los mismos
    # caracteres dentro de literales (URLs, "/*...*/", "a; b") son datos.
    prefix = b"type __HAEProps = "
    source = raw.encode("utf-8")
    tree = _parser_for(".ts").parse(prefix + source)
    root = tree.root_node
    edits = []
    stack = [root]
    while stack:
        node = stack.pop()
        start, end = node.start_byte - len(prefix), node.end_byte - len(prefix)
        if 0 <= start < end <= len(source) and node.type in ("comment", ";"):
            edits.append((start, end, b" " if node.type == "comment" else b", "))
        else:
            stack.extend(node.children)
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return " ".join(source.decode("utf-8").split())


def _props_summary(fn, generics: List[str], type_ann, types: Dict[str, str]) -> Optional[str]:
    candidate: Optional[str] = None

    params = fn.child_by_field_name("parameters")
    if params is not None and params.named_children:
        first = params.named_children[0]
        tnode = first.child_by_field_name("type")  # type_annotation
        if tnode is not None and tnode.named_children:
            candidate = _txt(tnode.named_children[0])

    if candidate is None and type_ann is not None:  # const X: React.FC<Props> = ...
        stack = [type_ann]
        while stack:
            n = stack.pop(0)
            if n.type == "type_arguments" and n.named_children:
                candidate = _txt(n.named_children[0])
                break
            stack.extend(n.children)

    if candidate is None and generics:  # forwardRef<El, Props>(...)
        candidate = generics[-1]

    if not candidate:
        return None
    resolved = types.get(candidate) if _IDENT_RE.match(candidate) else None
    return _compact(_clean_type_text(resolved or candidate), 300)


def _signature(name: str, fn) -> str:
    params = fn.child_by_field_name("parameters")
    if params is not None:
        ptxt = _txt(params)
    else:
        single = fn.child_by_field_name("parameter")
        ptxt = f"({_txt(single)})" if single is not None else "()"
    ret = fn.child_by_field_name("return_type")
    type_params = fn.child_by_field_name("type_parameters")
    return _compact(f"{name}{_txt(type_params)}{ptxt}{_txt(ret)}", 200)


def extract(content: str, ext: str, rel_path: str, is_ui_file: bool,
            is_util_folder: bool, include_contracts: bool = False) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    tree = _parser_for(ext).parse(content.encode("utf-8"))
    root = tree.root_node

    types: Dict[str, str] = {}
    decls: Dict[str, Dict[str, Any]] = {}
    exported: List[Tuple[str, str]] = []  # (nombre_local, nombre_exportado)
    symbols: List[Dict[str, Any]] = []

    stem = PurePosixPath(rel_path).stem
    if stem == "index":
        stem = PurePosixPath(rel_path).parent.name or stem
    default_name = stem[:1].upper() + stem[1:]

    def register_type(node):
        name = node.child_by_field_name("name")
        body = node.child_by_field_name("body") or node.child_by_field_name("value")
        if name is not None and body is not None:
            types[_txt(name)] = _txt(body)

    def register(node, doc_node, is_exp, local_override=None):
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            name = _txt(name_node) if name_node is not None else local_override
            if name:
                decls[name] = {"fn": node, "generics": [], "type_ann": None, "doc": _doc_for(doc_node)}
                if is_exp:
                    exported.append((name, name))
        elif node.type == "lexical_declaration":
            for d in node.named_children:
                if d.type != "variable_declarator":
                    continue
                name_node = d.child_by_field_name("name")
                if name_node is None or name_node.type != "identifier":
                    continue
                fn, generics = _unwrap(d.child_by_field_name("value"))
                if fn is None:
                    continue
                name = _txt(name_node)
                decls[name] = {"fn": fn, "generics": generics,
                               "type_ann": d.child_by_field_name("type"), "doc": _doc_for(doc_node)}
                if is_exp:
                    exported.append((name, name))
        elif node.type in ("interface_declaration", "type_alias_declaration"):
            register_type(node)
            if is_exp and include_contracts:
                name = _txt(node.child_by_field_name("name"))
                symbols.append({"name": name, "file_path": rel_path, "type": "type",
                                "signature": _compact(_txt(node), 4000), "contract": _txt(node),
                                "description": _doc_for(doc_node) or f"Contrato de tipos en {rel_path}",
                                "source_line": node.start_point.row + 1, "source_end_line": node.end_point.row + 1})
        elif node.type == "class_declaration" and is_exp:
            name = _txt(node.child_by_field_name("name")) or default_name
            body = node.child_by_field_name("body")
            if body is None:
                return
            methods, properties, decorators = [], [], []
            class_decorators = [_txt(c) for c in doc_node.named_children if c.type == "decorator"]
            for member in body.named_children:
                if member.type == "decorator":
                    decorators.append(_txt(member))
                    continue
                private = any(_txt(c) in ("private", "protected") for c in member.children if c.type == "accessibility_modifier")
                if member.type == "public_field_definition":
                    if not private and not _txt(member.child_by_field_name("name")).startswith("#"):
                        value = member.child_by_field_name("value")
                        header = member.text[:value.start_byte-member.start_byte].decode("utf-8").rstrip(' =') if value else _txt(member)
                        if value and value.type in ("true", "false", "number", "string", "null"):
                            header += " = " + _txt(value)
                        properties.append(" ".join(decorators + [header]))
                    decorators = []
                    continue
                if member.type != "method_definition":
                    decorators = []
                    continue
                method_name = _txt(member.child_by_field_name("name"))
                if private or method_name.startswith("#") or method_name == "constructor":
                    decorators = []
                    continue
                signature = _signature(f"{name}.{method_name}", member)
                methods.append(signature)
                symbols.append({"name": f"{name}.{method_name}", "file_path": rel_path, "type": "method",
                                "signature": signature, "description": _doc_for(member) or f"Método público de {name}",
                                "source_line": member.start_point.row + 1, "source_end_line": member.end_point.row + 1,
                                "_fn": member, "_decorators": class_decorators + decorators})
                decorators = []
            if include_contracts:
                header = content.encode("utf-8")[node.start_byte:body.start_byte].decode("utf-8")
                contract = "\n".join(class_decorators + [header + " { " + "; ".join(properties + methods) + " }"])
                types[name] = contract
                symbols.append({"name": name, "file_path": rel_path, "type": "class",
                                "signature": _compact(header, 1000), "contract": contract,
                                "description": _doc_for(doc_node) or f"Clase en {rel_path}",
                                "source_line": node.start_point.row + 1, "source_end_line": node.end_point.row + 1})

    for child in root.children:
        if child.type == "export_statement":
            if child.child_by_field_name("source") is not None:
                continue  # re-export `export ... from '...'`: el símbolo vive en otro archivo
            decl = child.child_by_field_name("declaration")
            value = child.child_by_field_name("value")
            is_default = any(c.type == "default" for c in child.children)
            if decl is not None:
                register(decl, child, True, local_override=default_name if is_default else None)
            elif value is not None:
                if value.type == "identifier":
                    exported.append((_txt(value), _txt(value)))
                else:
                    fn, generics = _unwrap(value)
                    if fn is not None:
                        decls[default_name] = {"fn": fn, "generics": generics, "type_ann": None,
                                               "doc": _doc_for(child)}
                        exported.append((default_name, default_name))
            else:
                for clause in child.named_children:
                    if clause.type != "export_clause":
                        continue
                    for spec in clause.named_children:
                        if spec.type != "export_specifier":
                            continue
                        local = spec.child_by_field_name("name")
                        alias = spec.child_by_field_name("alias")
                        if local is not None:
                            exported.append((_txt(local), _txt(alias or local)))
        else:
            register(child, child, False)

    low = rel_path.lower()
    category = "ui"
    if "form" in low:
        category = "form"
    elif "layout" in low:
        category = "layout"
    elif "page" in low or "view" in low:
        category = "view"

    components: List[Dict[str, Any]] = []
    utilities: List[Dict[str, Any]] = symbols
    seen = set()

    for local, public in exported:
        info = decls.get(local)
        if info is None or public in seen:
            continue
        seen.add(public)
        fn = info["fn"]
        is_hook = bool(_HOOK_RE.match(public))
        is_capital = public[:1].isupper() and not public.isupper()

        if not is_hook and is_capital and (_has_jsx(fn) or ext in (".tsx", ".jsx")):
            components.append({
                "name": public,
                "file_path": rel_path,
                "props_summary": _props_summary(fn, info["generics"], info["type_ann"], types),
                "description": info["doc"] or f"Componente en {rel_path}",
                "category": category,
                "source_line": fn.start_point.row + 1,
                "source_end_line": fn.end_point.row + 1,
                "_fn": fn, "_type_ann": info["type_ann"], "_generics": info["generics"],
            })
        elif is_hook or include_contracts or (public[:1].islower() and is_util_folder and not is_ui_file):
            utilities.append({
                "name": public,
                "file_path": rel_path,
                "signature": _signature(public, fn),
                "description": info["doc"] or (f"Custom hook en {rel_path}" if is_hook else f"Utilidad en {rel_path}"),
                "type": "hook" if is_hook else ("service" if "service" in low else "util"),
                "source_line": fn.start_point.row + 1,
                "source_end_line": fn.end_point.row + 1,
                "_fn": fn,
            })

    for item in components + utilities:
        fn = item.pop("_fn", None)
        decorators = item.pop("_decorators", [])
        type_ann = item.pop("_type_ann", None)
        generics = item.pop("_generics", [])
        if fn is not None:
            body = fn.child_by_field_name("body")
            signature = fn.text[:body.start_byte-fn.start_byte].decode("utf-8").strip() if body else _txt(fn)
            referenced = {name for name in generics if name in types}
            required_types, bound_types = set(), set()
            stack = [type_ann] + [fn.child_by_field_name(field) for field in ("parameters", "return_type", "type_parameters")]
            generic_trees = [_parser_for('.ts').parse(('type __HAE_GENERIC__ = '+generic+';').encode()) for generic in generics]
            stack.extend(tree.root_node for tree in generic_trees)
            while stack:
                current = stack.pop()
                if current is None:
                    continue
                if current.type == 'type_parameter':
                    parameter_name = current.child_by_field_name('name')
                    if parameter_name is not None:
                        bound_types.add(_txt(parameter_name))
                if current.type == 'nested_type_identifier':
                    required_types.add(_txt(current))
                    continue
                if current.type == 'type_query':
                    required_types.add(_txt(current))
                    continue
                if current.type == 'type_identifier':
                    required_types.add(_txt(current))
                if current.type == "type_identifier" and _txt(current) in types:
                    referenced.add(_txt(current))
                stack.extend(current.children)
            item["contract"] = "\n".join(decorators + [signature] + ([_txt(type_ann)] if type_ann else []) + [f"{name} = {types[name]}" for name in sorted(referenced)])
            item['referenced_types'] = sorted(required_types - bound_types - {'__HAE_GENERIC__'})

    return components, utilities
