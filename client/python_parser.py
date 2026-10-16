"""Extracción AST nativa para archivos Python (.py) sin dependencias externas."""
import ast
from typing import Any, Dict, List, Optional, Set, Tuple

PY_BUILTINS = {
    "abs", "all", "any", "ascii", "bin", "bool", "breakpoint", "bytearray", "bytes",
    "callable", "chr", "classmethod", "compile", "complex", "delattr", "dict", "dir",
    "divmod", "enumerate", "eval", "exec", "filter", "float", "format", "frozenset",
    "getattr", "globals", "hasattr", "hash", "help", "hex", "id", "input", "int",
    "isinstance", "issubclass", "iter", "len", "list", "locals", "map", "max",
    "memoryview", "min", "next", "object", "oct", "open", "ord", "pow", "print",
    "property", "range", "repr", "reversed", "round", "set", "setattr", "slice",
    "sorted", "staticmethod", "str", "sum", "super", "tuple", "type", "vars", "zip",
    "Exception", "ValueError", "TypeError", "RuntimeError", "KeyError", "IndexError",
    "AttributeError", "OSError", "PermissionError", "FileNotFoundError",
}

PY_TYPE_BUILTINS = PY_BUILTINS | {
    "Any", "Optional", "Union", "Literal", "Annotated", "List", "Dict", "Set",
    "Tuple", "Sequence", "Iterable", "Iterator", "Callable", "Mapping", "Awaitable",
    "Coroutine", "AsyncIterator", "AsyncGenerator", "ClassVar", "Final", "Self", "None",
}

MODEL_BASES = {
    "BaseModel", "BaseSettings", "TypedDict", "Enum", "IntEnum", "StrEnum", "Protocol",
}


def _compact(text: str, limit: int = 300) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _unparse(node: Optional[ast.AST]) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _doc_summary(node: ast.AST) -> str:
    doc = ast.get_docstring(node) or ""
    if not doc:
        return ""
    first_lines = [ln.strip() for ln in doc.splitlines() if ln.strip()]
    return _compact(" ".join(first_lines), 160)


def _format_arguments(args: ast.arguments) -> str:
    parts: List[str] = []
    posonly = getattr(args, "posonlyargs", [])
    regular = list(posonly) + list(args.args)
    defaults = [None] * (len(regular) - len(args.defaults)) + list(args.defaults)

    for idx, (arg, default) in enumerate(zip(regular, defaults)):
        piece = arg.arg
        if arg.annotation is not None:
            piece += f": {_unparse(arg.annotation)}"
        if default is not None:
            piece += f" = {_unparse(default)}"
        parts.append(piece)
        if posonly and idx == len(posonly) - 1:
            parts.append("/")

    if args.vararg is not None:
        var_piece = f"*{args.vararg.arg}"
        if args.vararg.annotation is not None:
            var_piece += f": {_unparse(args.vararg.annotation)}"
        parts.append(var_piece)
    elif args.kwonlyargs:
        parts.append("*")

    for kwarg, kwdefault in zip(args.kwonlyargs, args.kw_defaults):
        piece = kwarg.arg
        if kwarg.annotation is not None:
            piece += f": {_unparse(kwarg.annotation)}"
        if kwdefault is not None:
            piece += f" = {_unparse(kwdefault)}"
        parts.append(piece)

    if args.kwarg is not None:
        kw_piece = f"**{args.kwarg.arg}"
        if args.kwarg.annotation is not None:
            kw_piece += f": {_unparse(args.kwarg.annotation)}"
        parts.append(kw_piece)

    return "(" + ", ".join(parts) + ")"


def _fn_signature(prefix_name: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    kw = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    ret = f" -> {_unparse(node.returns)}" if node.returns is not None else ""
    return _compact(f"{kw}{prefix_name}{_format_arguments(node.args)}{ret}", 300)


def _extract_type_refs(nodes: List[Optional[ast.AST]]) -> Set[str]:
    refs: Set[str] = set()
    for root in nodes:
        if root is None:
            continue
        for sub in ast.walk(root):
            if isinstance(sub, ast.Name) and sub.id not in PY_TYPE_BUILTINS and not sub.id.isupper():
                refs.add(sub.id)
    return refs


def _extract_calls(node: ast.AST, self_class: Optional[str] = None) -> Set[str]:
    calls: Set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        if isinstance(fn, ast.Name):
            if fn.id not in PY_BUILTINS and not fn.id.startswith("_"):
                calls.add(fn.id)
        elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            owner = fn.value.id
            attr = fn.attr
            if attr.startswith("_"):
                continue
            if owner in ("self", "cls") and self_class:
                calls.add(f"{self_class}.{attr}")
            elif owner[:1].isupper():
                calls.add(f"{owner}.{attr}")
    return calls


def extract_python(content: str, rel_path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Extrae utilidades, clases, modelos Pydantic, endpoints y relaciones de un archivo .py."""
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        return [], [{"reason": "Sintaxis no reconocida por AST Python",
                     "line": exc.lineno or 1, "column": exc.offset or 1}], {}

    local_types: Dict[str, Dict[str, Any]] = {}
    imports_map: Dict[str, Tuple[str, str]] = {}
    utilities: List[Dict[str, Any]] = []

    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            mod_path = stmt.module.replace(".", "/")
            for alias in stmt.names:
                bound = alias.asname or alias.name
                imports_map[bound] = (mod_path, alias.name)
        elif isinstance(stmt, ast.ClassDef):
            bases = [_unparse(b) for b in stmt.bases]
            is_model = any(b.split(".")[-1] in MODEL_BASES for b in bases)
            fields: List[str] = []
            methods: List[str] = []
            type_nodes: List[Optional[ast.AST]] = list(stmt.bases)

            for item in stmt.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fname = item.target.id
                    if fname.startswith("_"):
                        continue
                    fann = _unparse(item.annotation)
                    type_nodes.append(item.annotation)
                    fdef = f"{fname}: {fann}"
                    if item.value is not None and isinstance(item.value, ast.Constant):
                        fdef += f" = {repr(item.value.value)}"
                    fields.append(fdef)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("_"):
                        continue
                    msig = _fn_signature(f"{stmt.name}.{item.name}", item)
                    methods.append(msig)
                    m_type_nodes = [item.returns] + [a.annotation for a in item.args.args] + [a.annotation for a in item.args.kwonlyargs]
                    type_nodes.extend(m_type_nodes)
                    m_refs = _extract_type_refs(m_type_nodes)
                    m_calls = _extract_calls(item, self_class=stmt.name)
                    m_decos = [f"@{_unparse(d)}" for d in item.decorator_list if _unparse(d) not in ("staticmethod", "classmethod")]
                    m_contract = "\n".join(m_decos + [msig])
                    relations = ([{"target_name": c, "kind": "calls"} for c in sorted(m_calls)] +
                                 [{"target_name": r, "kind": "uses_type"} for r in sorted(m_refs)])
                    utilities.append({
                        "name": f"{stmt.name}.{item.name}",
                        "file_path": rel_path,
                        "type": "method",
                        "signature": msig,
                        "contract": m_contract,
                        "description": _doc_summary(item) or f"Método público de {stmt.name}",
                        "source_line": item.lineno,
                        "source_end_line": item.end_lineno or item.lineno,
                        "exported": True,
                        "referenced_types": sorted(m_refs),
                        "relations": relations,
                    })

            base_str = f"({', '.join(bases)})" if bases else ""
            body_members = "; ".join(fields + methods) if (fields or methods) else "..."
            contract = f"class {stmt.name}{base_str}: {{ {body_members} }}"
            c_refs = _extract_type_refs(type_nodes) - {stmt.name}
            local_types[stmt.name] = {
                "name": stmt.name,
                "contract": contract,
                "file_path": rel_path,
                "source_line": stmt.lineno,
                "source_end_line": stmt.end_lineno or stmt.lineno,
                "refs": sorted(c_refs),
                "exported": True,
                "kind": "type",
            }
            utilities.append({
                "name": stmt.name,
                "file_path": rel_path,
                "type": "type" if is_model else "class",
                "signature": _compact(f"class {stmt.name}{base_str}", 200),
                "contract": contract,
                "description": _doc_summary(stmt) or (f"Modelo de datos en {rel_path}" if is_model else f"Clase en {rel_path}"),
                "source_line": stmt.lineno,
                "source_end_line": stmt.end_lineno or stmt.lineno,
                "exported": True,
                "referenced_types": sorted(c_refs),
                "relations": [{"target_name": r, "kind": "uses_type"} for r in sorted(c_refs)],
            })

        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = [_unparse(d) for d in stmt.decorator_list]
            if stmt.name.startswith("_") and not decos:
                continue
            fsig = _fn_signature(stmt.name, stmt)
            f_type_nodes = [stmt.returns] + [a.annotation for a in stmt.args.args] + [a.annotation for a in stmt.args.kwonlyargs]
            f_refs = _extract_type_refs(f_type_nodes)
            f_calls = _extract_calls(stmt)
            deco_lines = [f"@{d}" for d in decos]
            kind = "util"
            if any("tool" in d for d in decos):
                kind = "tool"
            elif any(d.startswith(("api_router.", "app.", "router.")) for d in decos):
                kind = "endpoint"
            elif "service" in rel_path.lower():
                kind = "service"

            relations = ([{"target_name": c, "kind": "calls"} for c in sorted(f_calls)] +
                         [{"target_name": r, "kind": "uses_type"} for r in sorted(f_refs)])
            utilities.append({
                "name": stmt.name,
                "file_path": rel_path,
                "type": kind,
                "signature": fsig,
                "contract": "\n".join(deco_lines + [fsig]),
                "description": _doc_summary(stmt) or f"Función ({kind}) en {rel_path}",
                "source_line": stmt.lineno,
                "source_end_line": stmt.end_lineno or stmt.lineno,
                "exported": not stmt.name.startswith("_"),
                "referenced_types": sorted(f_refs),
                "relations": relations,
            })

    module_meta = {
        "imports": imports_map,
        "types": local_types,
        "exports": {name: (None, name) for name in local_types},
        "stars": [],
    }
    return utilities, [], module_meta
