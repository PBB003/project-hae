"""Cierre selectivo de tipos locales. Sin ejecutar TS ni leer fuera del repositorio."""
import json
import re
from pathlib import Path, PurePosixPath

from . import ast_parser

BUILTINS = {'Array', 'ReadonlyArray', 'Promise', 'PromiseLike', 'Record', 'Partial', 'Required',
            'Readonly', 'Pick', 'Omit', 'Exclude', 'Extract', 'NonNullable', 'Parameters',
            'ConstructorParameters', 'ReturnType', 'InstanceType', 'Awaited', 'Map', 'Set',
            'WeakMap', 'WeakSet', 'Date', 'Error', 'RegExp', 'Function', 'Object', 'String',
            'Number', 'Boolean', 'Symbol', 'BigInt', 'ThisType', 'Uppercase', 'Lowercase',
            'Capitalize', 'Uncapitalize', 'NoInfer', 'PropertyKey', 'Iterable', 'Iterator'}


def literal(node):
    if node is None:
        return False
    if node.type in ('string','number','true','false','null'):
        return True
    if node.type in ('as_expression','parenthesized_expression'):
        return literal(node.named_children[0]) and (node.type != 'as_expression' or ast_parser._txt(node).rstrip().endswith('as const'))
    if node.type == 'array':
        return all(literal(child) for child in node.named_children)
    if node.type == 'object':
        return all(child.type == 'pair' and literal(child.child_by_field_name('value')) for child in node.named_children)
    return False


def references(text):
    tree = ast_parser._parser_for('.tsx').parse(text.encode())
    return node_references([tree.root_node])


def node_references(nodes):
    stack, refs, bound = list(nodes), set(), set()
    while stack:
        node = stack.pop()
        if node is None:
            continue
        if node.type == 'type_parameter':
            name = node.child_by_field_name('name')
            if name is not None:
                bound.add(ast_parser._txt(name))
        if node.type == 'nested_type_identifier':
            refs.add(ast_parser._txt(node))
            continue
        if node.type == 'type_identifier':
            refs.add(ast_parser._txt(node))
        if node.type == 'type_query':
            refs.add(ast_parser._txt(node))  # typeof requiere un valor, no un tipo local.
            continue
        stack.extend(node.named_children)
    return refs - bound - BUILTINS


def module_info(content, extension, items):
    tree = ast_parser._parser_for(extension).parse(content.encode())
    info = {'imports': {}, 'types': {}, 'exports': {}, 'stars': []}
    indexed = {x['name']: x['contract'] for x in items if x.get('type') == 'class' and x.get('contract')}
    for outer in tree.root_node.named_children:
        source = outer.child_by_field_name('source')
        specifier = ast_parser._txt(source)[1:-1] if source is not None else None
        if outer.type == 'import_statement':
            clause = next((c for c in outer.named_children if c.type == 'import_clause'), None)
            if clause is None:
                continue
            for child in clause.named_children:
                if child.type == 'identifier':
                    info['imports'][ast_parser._txt(child)] = (specifier, 'default')
                elif child.type == 'namespace_import':
                    info['imports'][ast_parser._txt(child.named_children[-1])] = (specifier, '*')
                elif child.type == 'named_imports':
                    for entry in child.named_children:
                        name, alias = entry.child_by_field_name('name'), entry.child_by_field_name('alias')
                        if name is not None:
                            info['imports'][ast_parser._txt(alias or name)] = (specifier, ast_parser._txt(name))
            continue
        exported = outer.type == 'export_statement'
        if exported and specifier is not None:
            clause = next((c for c in outer.named_children if c.type == 'export_clause'), None)
            if clause:
                for entry in clause.named_children:
                    name, alias = entry.child_by_field_name('name'), entry.child_by_field_name('alias')
                    if name is not None:
                        info['exports'][ast_parser._txt(alias or name)] = (specifier, ast_parser._txt(name))
            elif any(c.type == '*' for c in outer.children):
                info['stars'].append(specifier)
            continue
        node = outer.child_by_field_name('declaration') if exported else outer
        if node is None:
            clause = next((c for c in outer.named_children if c.type == 'export_clause'), None)
            if clause:
                for entry in clause.named_children:
                    name, alias = entry.child_by_field_name('name'), entry.child_by_field_name('alias')
                    if name is not None:
                        info['exports'][ast_parser._txt(alias or name)] = (None, ast_parser._txt(name))
            continue
        if node.type == 'lexical_declaration':
            for entry in node.named_children:
                name = entry.child_by_field_name('name')
                if name is None or name.type != 'identifier' or not literal(entry.child_by_field_name('value')):
                    continue
                name = ast_parser._txt(name)
                info['types'][name] = {'name':name,'contract':'const '+ast_parser._txt(entry)+';',
                    'file_path':items[0]['file_path'] if items else '', 'source_line':entry.start_point.row+1,
                    'refs':[], 'kind':'value'}
                if exported:
                    info['exports'][name] = (None,name)
            continue
        if node.type not in ('interface_declaration', 'type_alias_declaration', 'class_declaration'):
            continue
        name = ast_parser._txt(node.child_by_field_name('name'))
        text = indexed.get(name) if node.type == 'class_declaration' else ast_parser._txt(node)
        if not name or not text:
            continue
        refs = references(text)
        if node.type == 'class_declaration':
            nodes = [node.child_by_field_name('type_parameters')]
            parents = set()
            heritage = next((child for child in node.named_children if child.type == 'class_heritage'), None)
            if heritage is not None:
                nodes.append(heritage)
                for clause in heritage.named_children:
                    if clause.type == 'extends_clause':
                        for value in clause.named_children:
                            if value.type in ('identifier','member_expression'):
                                parents.add(ast_parser._txt(value))
            body = node.child_by_field_name('body')
            for member in body.named_children if body is not None else []:
                if any(ast_parser._txt(c) in ('private','protected') for c in member.children if c.type == 'accessibility_modifier'):
                    continue
                if ast_parser._txt(member.child_by_field_name('name')).startswith('#'):
                    continue
                if member.type == 'method_definition' and ast_parser._txt(member.child_by_field_name('name')) != 'constructor':
                    nodes.extend(member.child_by_field_name(field) for field in ('parameters','return_type','type_parameters'))
                elif member.type == 'public_field_definition':
                    nodes.append(member.child_by_field_name('type'))
            refs = node_references(nodes) | parents
        info['types'][name] = {'name': name, 'contract': text, 'file_path': items[0]['file_path'] if items else '',
                               'source_line': node.start_point.row + 1, 'refs': sorted(refs - {name})}
        if exported:
            public = 'default' if any(c.type == 'default' for c in outer.children) else name
            info['exports'][public] = (None, name)
    for name, declaration in info['types'].items():
        declaration['exported'] = any(binding == (None, name) for binding in info['exports'].values())
    return info


def jsonc(text):
    """Comentarios y comas finales sin modificar strings de configuración."""
    out, i, quoted = [], 0, False
    while i < len(text):
        ch = text[i]
        if quoted:
            out.append(ch)
            if ch == '\\' and i + 1 < len(text):
                i += 1
                out.append(text[i])
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
            out.append(ch)
        elif text.startswith('//', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            continue
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise ValueError('Comentario JSONC sin cierre')
            out.append(' ')
            i = end + 2
            continue
        elif ch == ',' and text[i + 1:].lstrip().startswith(('}', ']')):
            pass
        else:
            out.append(ch)
        i += 1
    return json.loads(''.join(out))


class TypeResolver:
    def __init__(self, root, modules):
        self.root, self.modules = Path(root).resolve(), modules
        self.aliases, self.base = {}, self.root
        self.config_warnings = []
        config = self.root / 'tsconfig.json'
        if not config.exists():
            config = self.root / 'jsconfig.json'
        self._config(config, set())

    def _config(self, path, seen):
        path = path.resolve()
        if path in seen or len(seen) >= 8 or not path.is_relative_to(self.root) or path.is_symlink():
            self.config_warnings.append('Configuración fuera de alcance o cíclica')
            return
        if not path.exists():
            return
        seen.add(path)
        try:
            if path.stat().st_size > 1_000_000:
                raise ValueError('Configuración demasiado grande')
            config = jsonc(path.read_text(encoding='utf-8-sig'))
            parent = config.get('extends')
            if isinstance(parent, str) and parent.startswith('.'):
                parent_path = path.parent / parent
                self._config(parent_path if parent_path.suffix else parent_path.with_suffix('.json'), seen)
            elif parent:
                self.config_warnings.append('extends externo no resuelto')
            options = config.get('compilerOptions', {})
            if 'baseUrl' in options:
                self.base = (path.parent / options['baseUrl']).resolve()
            for pattern, targets in options.get('paths', {}).items():
                self.aliases[pattern] = [(self.base if 'baseUrl' in options or self.base != self.root else path.parent) / target for target in targets]
        except (OSError, ValueError, TypeError, AttributeError):
            self.config_warnings.append('Configuración de paths no reconocida')

    def module_path(self, owner, specifier):
        if not specifier:
            return None
        candidates = []
        if specifier.startswith('.'):
            candidates.append(self.root / PurePosixPath(owner).parent / specifier)
        else:
            for pattern in sorted(self.aliases, key=len, reverse=True):
                if '*' in pattern:
                    prefix, suffix = pattern.split('*', 1)
                    if not specifier.startswith(prefix) or (suffix and not specifier.endswith(suffix)):
                        continue
                    wildcard = specifier[len(prefix):len(specifier) - len(suffix) if suffix else None]
                elif pattern == specifier:
                    wildcard = ''
                else:
                    continue
                candidates.extend(Path(str(target).replace('*', wildcard)) for target in self.aliases[pattern])
                break
            candidates.append(self.base / specifier)
        for candidate in candidates:
            candidate = candidate.resolve()
            if not candidate.is_relative_to(self.root):
                continue
            variants = [candidate]
            if candidate.suffix in ('.js', '.jsx'):
                variants.extend(candidate.with_suffix(ext) for ext in ('.ts', '.tsx'))
            variants.extend(Path(str(candidate) + ext) for ext in ('.ts', '.tsx', '.js', '.jsx'))
            variants.extend(candidate / ('index' + ext) for ext in ('.ts', '.tsx', '.js', '.jsx'))
            for variant in variants:
                key = variant.relative_to(self.root).as_posix()
                if key in self.modules:
                    return key
        return None

    def exported(self, path, name, seen=None, work=None):
        seen = set() if seen is None else seen
        work = [256] if work is None else work
        work[0] -= 1
        if (path, name) in seen or len(seen) > 64 or work[0] < 0:
            return None
        seen = seen | {(path, name)}
        module = self.modules[path]
        if name in module['exports']:
            source, local = module['exports'][name]
            if source is None:
                if local in module['types']:
                    return path, local
                if local in module['imports']:
                    source, local = module['imports'][local]
                else:
                    return None
            target = self.module_path(path, source)
            return self.exported(target, local, seen, work) if target else None
        found = set()
        for source in module['stars']:
            target = self.module_path(path, source)
            resolved = self.exported(target, name, seen, work) if target and name != 'default' else None
            if resolved:
                found.add(resolved)
        return next(iter(found)) if len(found) == 1 and work[0] >= 0 else None

    def enrich(self, item):
        owner = item['file_path']
        delivered = set(re.findall(r'^\s*([A-Za-z_$][\w$]*)\s*=',item.get('contract') or '',re.MULTILINE))
        if item.get('type') in ('type','class','constant'):
            delivered.add(item['name'])
        visited, dependencies, unresolved = set(), {}, set()
        def visit(path, ref, depth=0):
            if ref.startswith('typeof '):
                ref = ref[7:].strip()
            if ref in BUILTINS:
                return
            if depth > 12 or len(visited) >= 128:
                unresolved.add(ref + ' (límite de resolución)')
                return
            module = self.modules[path]
            target = (path, ref) if ref in module['types'] else None
            if target is None:
                alias, dot, member = ref.partition('.')
                imported = module['imports'].get(alias)
                if imported:
                    source, public = imported
                    other = self.module_path(path, source)
                    target = self.exported(other, member if public == '*' and dot else public) if other else None
            if target is None:
                unresolved.add(ref)
                return
            if target in visited:
                return
            visited.add(target)
            declaration = self.modules[target[0]]['types'][target[1]]
            if target[0] != owner or target[1] not in delivered:
                dependencies[target] = {key: value for key, value in declaration.items() if key != 'refs'}
                dependencies[target]['file_path'] = target[0]
                if ref != declaration['name']:
                    dependencies[target]['requested_as'] = ref
            for child in declaration['refs']:
                visit(target[0], child, depth + 1)
        explicit = item.pop('referenced_types', None)
        refs = set(explicit) if explicit is not None else references(item.get('contract') or item.get('signature') or item.get('props_summary') or '')
        if item.get('type') in ('type','class','constant') and item['name'] in self.modules[owner]['types']:
            refs = {item['name']}
        for ref in sorted(refs):
            visit(owner, ref)
        item['type_dependencies'] = sorted(dependencies.values(), key=lambda x: (x['file_path'], x['name']))
        item['unresolved_types'] = sorted(unresolved)
        item['type_resolution'] = 'partial' if unresolved else 'complete'
