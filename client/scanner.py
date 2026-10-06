import os
import re
import json
from pathlib import Path
from typing import List, Dict, Any, Tuple

try:
    from . import ast_parser
except ImportError:  # ejecutado como script (hae_sync.py añade client/ al sys.path)
    import ast_parser

ENGINE = "tree-sitter" if ast_parser.AVAILABLE else "regex"

# Patrones para componentes React
COMPONENT_FUNC_RE = re.compile(
    r'(?:export\s+(?:default\s+)?(?:function|const)\s+([A-Z][a-zA-Z0-9_]*))'
)
PROPS_INTERFACE_RE = re.compile(
    r'(?:interface|type)\s+([A-Z][a-zA-Z0-9_]*Props)\s*(?:=\s*)?\{([^}]+)\}',
    re.MULTILINE | re.DOTALL
)
JSDOC_RE = re.compile(r'/\*\*\s*([\s\S]*?)\s*\*/')

# Patrones para hooks y utilidades
HOOK_RE = re.compile(r'export\s+(?:function|const)\s+(use[A-Z][a-zA-Z0-9_]*)')
UTIL_RE = re.compile(r'export\s+(?:function|const)\s+([a-z][a-zA-Z0-9_]*)')

def extract_jsdoc(content_before: str) -> str:
    matches = list(JSDOC_RE.finditer(content_before))
    if matches:
        last_match = matches[-1].group(1)
        # Limpiar asteriscos y espacios
        lines = [re.sub(r'^\s*\*\s?', '', line).strip() for line in last_match.splitlines()]
        clean_doc = " ".join([l for l in lines if l and not l.startswith('@')])
        return clean_doc[:150]
    return ""

def scan_codebase(root_dir: str, diagnostics=None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Escanea archivos TSX, JSX, TS, JS buscando componentes, hooks y utilidades.
    """
    components = []
    utilities = []
    
    ignore_dirs = {
        'node_modules', '.next', '.git', 'dist', 'build', 'out',
        '.cache', '.vscode', '.cursor', '__pycache__', '.venv', 'env', 'venv',
        'coverage', '.turbo', '.nuxt', '.svelte-kit'
    }
    skip_markers = ('.test.', '.spec.', '.stories.', '.d.ts')
    max_file_bytes = 1_000_000

    root_path = Path(root_dir).resolve()
    if not root_path.is_dir():
        raise ValueError(f"Directorio inexistente: {root_dir}")
    if diagnostics is None:
        diagnostics = {}
    diagnostics.update(engine=ENGINE, errors=[], scanned_files=0)

    def walk_error(exc):
        diagnostics["errors"].append({"path": str(Path(exc.filename).relative_to(root_path)), "reason": type(exc).__name__})
    for root, dirs, files in os.walk(root_path, onerror=walk_error):
        # Poda en memoria: NUNCA entra a node_modules, .next, .git, etc.
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith('.') and not (Path(root) / d).is_symlink()]
        
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in ('.tsx', '.jsx', '.ts', '.js'):
                continue
            if any(m in fname.lower() for m in skip_markers):
                continue

            full_path = os.path.join(root, fname)
            try:
                if os.path.getsize(full_path) > max_file_bytes:
                    diagnostics["errors"].append({"path": str(Path(full_path).relative_to(root_path)), "reason": "Archivo supera 1 MB"})
                    continue
            except OSError as exc:
                diagnostics["errors"].append({"path": str(Path(full_path).relative_to(root_path)), "reason": type(exc).__name__})
                continue

            path = Path(full_path)
            if path.is_symlink() or not path.resolve().is_relative_to(root_path):
                diagnostics["errors"].append({"path": str(path.relative_to(root_path)), "reason": "Ruta fuera del repositorio"})
                continue
            rel_path = str(path.relative_to(root_path)).replace('\\', '/')
            try:
                content = path.read_text(encoding='utf-8-sig')
            except Exception as exc:
                diagnostics["errors"].append({"path": rel_path, "reason": type(exc).__name__})
                continue
            diagnostics["scanned_files"] += 1

            if ast_parser.AVAILABLE:
                try:
                    if ast_parser._parser_for(ext).parse(content.encode('utf-8')).root_node.has_error:
                        diagnostics['errors'].append({'path':rel_path,'reason':'Sintaxis no reconocida por AST'})
                        continue
                    is_ui = ext in ('.tsx', '.jsx') or 'component' in rel_path.lower() or 'ui/' in rel_path.lower()
                    is_util_dir = any(k in rel_path.lower() for k in ('util', 'lib', 'helper', 'service', 'tools'))
                    found_c, found_u = ast_parser.extract(content, ext, rel_path, is_ui, is_util_dir, include_contracts=True)
                    components.extend(found_c)
                    utilities.extend(found_u)
                    continue
                except Exception as exc:
                    diagnostics["errors"].append({"path": rel_path, "reason": type(exc).__name__})
                    continue

            # 1. Extraer interfaces de Props en el archivo si existen
            props_map = {}
            for m in PROPS_INTERFACE_RE.finditer(content):
                p_name = m.group(1)
                raw_props = m.group(2)
                # Compactar props a una sola línea
                clean_props = " ".join(raw_props.split())
                clean_props = re.sub(r';\s*', ', ', clean_props).strip(', ')
                props_map[p_name] = f"{{ {clean_props} }}"

            # 2. Detectar componentes React (nombres que empiezan con Mayúscula)
            # Se analizan preferentemente en archivos .tsx, .jsx o dentro de carpetas components/ o ui/
            is_ui_file = ext in ('.tsx', '.jsx') or 'component' in rel_path.lower() or 'ui/' in rel_path.lower()
            if is_ui_file:
                for m in COMPONENT_FUNC_RE.finditer(content):
                    comp_name = m.group(1)
                    if comp_name.isupper():
                        continue  # constante (API_URL, MAX_ITEMS), no un componente
                    start_pos = m.start()
                    doc = extract_jsdoc(content[:start_pos])

                    # Asociar props si coincide el nombre
                    props_summary = props_map.get(f"{comp_name}Props") or props_map.get("Props")
                    
                    # Categoría estimada
                    category = "ui"
                    if "form" in rel_path.lower():
                        category = "form"
                    elif "layout" in rel_path.lower():
                        category = "layout"
                    elif "page" in rel_path.lower() or "view" in rel_path.lower():
                        category = "view"

                    components.append({
                        "name": comp_name,
                        "file_path": rel_path,
                        "props_summary": props_summary,
                        "description": doc or f"Componente en {rel_path}",
                        "category": category
                    })

            # 3. Detectar Hooks (use...)
            for m in HOOK_RE.finditer(content):
                hook_name = m.group(1)
                start_pos = m.start()
                doc = extract_jsdoc(content[:start_pos])
                utilities.append({
                    "name": hook_name,
                    "file_path": rel_path,
                    "signature": f"{hook_name}(...)",
                    "description": doc or f"Custom hook en {rel_path}",
                    "type": "hook"
                })

            # 4. Detectar Utilidades y Helpers en carpetas util, lib, helper, services
            is_util_folder = any(k in rel_path.lower() for k in ('util', 'lib', 'helper', 'service', 'tools'))
            if is_util_folder and not is_ui_file:
                for m in UTIL_RE.finditer(content):
                    fn_name = m.group(1)
                    if fn_name.startswith('use'):
                        continue  # ya capturado como hook
                    start_pos = m.start()
                    doc = extract_jsdoc(content[:start_pos])
                    utilities.append({
                        "name": fn_name,
                        "file_path": rel_path,
                        "signature": f"{fn_name}(...)",
                        "description": doc or f"Utilidad en {rel_path}",
                        "type": "service" if "service" in rel_path.lower() else "util"
                    })

    # Deduplicar por (name, file_path)
    dedup_components = {f"{c['name']}@{c['file_path']}": c for c in components}.values()
    dedup_utilities = {f"{u['name']}@{u['file_path']}": u for u in utilities}.values()

    return list(dedup_components), list(dedup_utilities)

def _parse_dependencies_stack(deps: dict) -> List[str]:
    stack = []
    # Frontend
    if "next" in deps:
        stack.append(f"Next.js {deps['next']}")
    elif "react" in deps:
        stack.append(f"React {deps['react']}")
    if "vue" in deps:
        stack.append(f"Vue {deps['vue']}")
    if "tailwindcss" in deps or "@tailwindcss/vite" in deps:
        stack.append("Tailwind CSS")
    if "typescript" in deps:
        stack.append("TypeScript")
    if "zustand" in deps:
        stack.append("Zustand")
    if "@apollo/client" in deps or "apollo-client" in deps:
        stack.append("Apollo GraphQL")
    if "lucide-react" in deps:
        stack.append("Lucide Icons")
    if "@tanstack/react-query" in deps:
        stack.append("React Query")
    if "zod" in deps:
        stack.append("Zod")
    # Backend Node / Nest / Express
    if "@nestjs/core" in deps:
        stack.append(f"NestJS {deps['@nestjs/core']}")
    elif "express" in deps:
        stack.append("Express.js")
    elif "fastify" in deps:
        stack.append("Fastify")
    if "@prisma/client" in deps or "prisma" in deps:
        stack.append("Prisma ORM")
    if "typeorm" in deps:
        stack.append("TypeORM")
    if "drizzle-orm" in deps:
        stack.append("Drizzle ORM")
    return stack

def detect_project_info(root_dir: str) -> Dict[str, Any]:
    """
    Detecta automáticamente nombre y stack a partir de package.json,
    soportando tanto proyectos individuales como multi-repos (ej: carpeta base con frontend y backend).
    """
    root_path = Path(root_dir).resolve()
    info = {
        "id": root_path.name.lower().replace(" ", "-"),
        "name": root_path.name,
        "description": "Proyecto detectado automáticamente",
        "tech_stack": ""
    }

    pkg_file = root_path / 'package.json'
    
    # 1. Caso estándar: package.json en la raíz
    if pkg_file.exists():
        try:
            data = json.loads(pkg_file.read_text(encoding='utf-8-sig'))
            if "name" in data:
                info["id"] = data["name"].replace("@", "").replace("/", "-")
                info["name"] = data["name"]
            if "description" in data:
                info["description"] = data["description"]
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            stack = _parse_dependencies_stack(deps)
            if stack:
                info["tech_stack"] = ", ".join(stack)
            return info
        except Exception:
            pass

    # 2. Caso Multi-repo / Monorepo: buscar subcarpetas (ej: tekniek-frontend, tekniek-backend, apps/*)
    subproject_stacks = []
    subfolder_names = []
    ignore_subdirs = {'node_modules', '.git', 'dist', 'build', '.next', '.venv'}

    for sub in root_path.iterdir():
        if not sub.is_dir() or sub.name in ignore_subdirs:
            continue
        sub_pkg = sub / 'package.json'
        sub_req = sub / 'requirements.txt'
        
        # Sub-repo Node/TypeScript
        if sub_pkg.exists():
            try:
                sub_data = json.loads(sub_pkg.read_text(encoding='utf-8-sig'))
                deps = {**sub_data.get("dependencies", {}), **sub_data.get("devDependencies", {})}
                stack = _parse_dependencies_stack(deps)
                if stack:
                    label = "Frontend" if any(k in sub.name.lower() for k in ("front", "client", "ui", "web")) else ("Backend" if any(k in sub.name.lower() for k in ("back", "server", "api")) else sub.name)
                    subproject_stacks.append(f"{label} ({sub.name}): {', '.join(stack)}")
                    subfolder_names.append(sub.name)
            except Exception:
                pass
        # Sub-repo Python
        elif sub_req.exists():
            try:
                txt = sub_req.read_text(encoding='utf-8-sig').lower()
                pystack = ["Python"]
                if "fastapi" in txt: pystack.append("FastAPI")
                if "django" in txt: pystack.append("Django")
                if "flask" in txt: pystack.append("Flask")
                if "sqlalchemy" in txt: pystack.append("SQLAlchemy")
                subproject_stacks.append(f"Backend ({sub.name}): {', '.join(pystack)}")
                subfolder_names.append(sub.name)
            except Exception:
                pass

    if subproject_stacks:
        info["tech_stack"] = " | ".join(subproject_stacks)
        info["description"] = f"Proyecto Fullstack Multi-repo ({', '.join(subfolder_names)})"

    return info
