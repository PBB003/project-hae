"""Chequeos declarativos: las reglas nunca se evalúan como código ejecutable."""
import fnmatch
import re
import os
from pathlib import Path


KINDS = {"forbidden_pattern", "forbidden_import", "required_file", "max_file_lines"}
IGNORED = {"node_modules", ".git", ".venv", ".venv-test", "dist", "build", "__pycache__", ".hae-backups", "tmp_pytest"}


def validate_check_spec(spec):
    if not isinstance(spec, dict) or spec.get("kind") not in KINDS:
        raise ValueError("check_spec.kind inválido")
    allowed = {"kind", "include", "exclude", "pattern", "module", "path", "limit", "severity"}
    if set(spec) - allowed:
        raise ValueError("Campos desconocidos en check_spec")
    if spec.get("severity", "error") not in ("error", "warning"):
        raise ValueError("severity debe ser error o warning")
    for key in ("include", "exclude"):
        if key in spec and (not isinstance(spec[key], list) or any(not isinstance(x, str) for x in spec[key])):
            raise ValueError(f"{key} debe ser una lista de globs")
    kind = spec["kind"]
    if kind == "forbidden_pattern":
        if not isinstance(spec.get("pattern"), str) or not 0 < len(spec["pattern"]) <= 2000:
            raise ValueError("pattern obligatorio (máximo 2000 caracteres)")
        try:
            re.compile(spec["pattern"])
        except re.error as exc:
            raise ValueError("Expresión regular inválida") from exc
    if kind == "forbidden_import" and not isinstance(spec.get("module"), str):
        raise ValueError("module obligatorio")
    if kind == "required_file":
        value = spec.get("path")
        if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts or ":" in value:
            raise ValueError("path debe ser relativa sin ..")
    if kind == "max_file_lines" and (type(spec.get("limit")) is not int or spec["limit"] < 1):
        raise ValueError("limit debe ser un entero positivo")


def check_rules(root_dir, rules):
    root = Path(root_dir).resolve()
    if not root.is_dir():
        raise ValueError("Directorio de chequeo inexistente")
    findings, unchecked = [], []
    files = []
    def walk_error(exc):
        raise ValueError("No se pudo recorrer un directorio del chequeo") from exc
    for directory, dirs, names in os.walk(root,onerror=walk_error):
        dirs[:] = [d for d in dirs if d not in IGNORED and (not d.startswith(".") or d == ".github") and not (Path(directory)/d).is_symlink()]
        files.extend(Path(directory)/name for name in names if not name.startswith(".") and name != "hae.json"
                     and (Path(directory)/name).suffix in (".ts", ".tsx", ".js", ".jsx", ".py", ".md", ".json", ".yml", ".yaml"))
    for rule in rules:
        if rule.get("status", "active") != "active":
            continue
        spec = rule.get("check_spec")
        if not spec:
            unchecked.append(rule["title"])
            continue
        validate_check_spec(spec)
        selected = [p for p in files if any(fnmatch.fnmatch(p.relative_to(root).as_posix(), glob) for glob in spec.get("include", ["*"]))
                    and not any(fnmatch.fnmatch(p.relative_to(root).as_posix(), glob) for glob in spec.get("exclude", []))]
        kind = spec["kind"]
        violations = []
        if kind == "required_file":
            target = (root / spec["path"]).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                violations.append((spec["path"], 1))
        for path in selected if kind != "required_file" else []:
            if path.is_symlink() or not path.resolve().is_relative_to(root) or path.stat().st_size > 1_000_000:
                raise ValueError(f"Chequeo incompleto: archivo fuera de alcance o demasiado grande: {path.relative_to(root)}")
            content = path.read_text(encoding="utf-8-sig")
            if kind == "max_file_lines":
                if len(content.splitlines()) > spec["limit"]:
                    violations.append((path.relative_to(root).as_posix(), spec["limit"] + 1))
            elif kind == "forbidden_pattern":
                for match in re.finditer(spec["pattern"], content):
                    violations.append((path.relative_to(root).as_posix(), content.count("\n", 0, match.start()) + 1))
            elif kind == "forbidden_import":
                # tree-sitter evita falsos positivos de comentarios y strings.
                from client import ast_parser
                if path.suffix not in (".ts", ".tsx", ".js", ".jsx"):
                    continue
                if not ast_parser.AVAILABLE:
                    raise ValueError("forbidden_import requiere tree-sitter")
                tree = ast_parser._parser_for(path.suffix).parse(content.encode())
                for node in tree.root_node.named_children:
                    if node.type not in ("import_statement", "export_statement"):
                        continue
                    source = node.child_by_field_name("source")
                    if source is not None and ast_parser._txt(source).strip("\"'") == spec["module"]:
                        violations.append((path.relative_to(root).as_posix(), node.start_point.row + 1))
        for path, line in violations:
            findings.append({"rule": rule["title"], "path": path, "line": line, "severity": spec.get("severity", "error")})
    return {"findings": findings, "unchecked_rules": unchecked,
            "passed": not any(f["severity"] == "error" for f in findings)}
