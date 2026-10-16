from datetime import datetime, timedelta, timezone
from typing import Literal
import json
from server.config import settings

from server.database import get_db_connection
from server.policy import authorize, current_identity
from server.services.search_service import rank
from server.services.response_service import respond

MAX_LIMIT = 50
SearchLevel = Literal["location", "contract", "detail"]
GraphDirection = Literal["outgoing", "incoming", "both"]


def _clamp(limit):
    return max(1, min(int(limit), MAX_LIMIT))


def _normalize_exclude_types(exclude_types):
    if not exclude_types:
        return []
    return sorted({str(t).strip() for t in exclude_types if t and str(t).strip()})


class SyncConflict(ValueError):
    pass


class RegistryService:
    @staticmethod
    async def upsert_project(project):
        authorize(project.id, write=True)
        async with get_db_connection() as db:
            await db.execute("""INSERT INTO projects(id,name,description,tech_stack,updated_at)
                VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                description=excluded.description,tech_stack=excluded.tech_stack,updated_at=CURRENT_TIMESTAMP""",
                (project.id, project.name, project.description, project.tech_stack))
            await db.commit()

    @staticmethod
    async def get_project(project_id):
        authorize(project_id)
        async with get_db_connection() as db:
            row = await (await db.execute("SELECT * FROM projects WHERE id=?", (project_id,))).fetchone()
            return dict(row) if row else None

    @staticmethod
    async def list_projects():
        identity = current_identity.get()
        async with get_db_connection() as db:
            rows = await (await db.execute("SELECT * FROM projects ORDER BY updated_at DESC")).fetchall()
            return [dict(row) for row in rows if identity.role == "admin" or row["id"] in identity.projects]

    @staticmethod
    async def delete_project(project_id):
        authorize(project_id, admin=True)
        async with get_db_connection() as db:
            cursor = await db.execute("DELETE FROM projects WHERE id=?", (project_id,))
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def sync_catalog(project_id, components, utilities, repository=None, mode="merge", project=None, rules=None):
        authorize(project_id, write=True)
        for rule in rules or []:
            authorize(project_id, write=True, admin=rule.status != "proposed")
        if mode == "snapshot" and (repository is None or not repository.complete):
            raise ValueError("snapshot requiere manifiesto de repositorio completo")
        scope_repo = repository.repo_id if repository else "legacy"
        branch = repository.branch if repository else ""
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                if project is not None:
                    await db.execute("""INSERT INTO projects(id,name,description,tech_stack,updated_at)
                        VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                        description=excluded.description,tech_stack=excluded.tech_stack,updated_at=CURRENT_TIMESTAMP""",
                        (project.id, project.name, project.description, project.tech_stack))
                current = await (await db.execute("SELECT * FROM repositories WHERE project_id=? AND repo_id=? AND branch=?",
                    (project_id, scope_repo, branch))).fetchone()
                if repository:
                    revision = current["revision"] if current else 0
                    if repository.expected_revision != revision:
                        raise SyncConflict(f"Revisión obsoleta: esperado {revision}; refresca el manifiesto")
                    if current and current["scanned_at"] and repository.scanned_at < datetime.fromisoformat(current["scanned_at"]):
                        raise SyncConflict("El escaneo es anterior al catálogo actual")
                    if repository.scanned_at > datetime.now(timezone.utc) + timedelta(minutes=5):
                        raise ValueError("scanned_at está en el futuro")
                if mode == "snapshot":
                    if not components and not utilities and not repository.allow_empty:
                        count = 0
                        for table in ("components", "utilities"):
                            count += (await (await db.execute(f"SELECT count(*) n FROM {table} WHERE project_id=? AND repo_id=? AND branch=?",
                                (project_id, scope_repo, branch))).fetchone())["n"]
                        if count:
                            raise SyncConflict("Catálogo vacío rechazado: usa allow_empty solo para una eliminación intencional")
                    for table in ("components", "utilities", "symbol_relations"):
                        await db.execute(f"DELETE FROM {table} WHERE project_id=? AND repo_id=? AND branch=?", (project_id, scope_repo, branch))
                for table, source_kind, items, extra in [
                    ("components", "component", components, ["props_summary", "category"]),
                    ("utilities", "utility", utilities, ["signature", "type"]),
                ]:
                    columns = ["project_id", "repo_id", "branch", "name", "file_path", "description", "source_line", "source_end_line", "contract",
                               "type_dependencies", "unresolved_types", "type_resolution", "relations", "exported"] + extra
                    updates = ",".join(f"{column}=excluded.{column}" for column in columns[5:])
                    for item in items:
                        dumped = item.model_dump()
                        raw_relations = list(dumped.get("relations") or [])
                        raw_type_deps = list(dumped.get("type_dependencies") or [])
                        resolved_type_files = {}
                        for dep in raw_type_deps:
                            if dep.get("name") and dep.get("file_path"):
                                resolved_type_files[dep["name"]] = dep["file_path"]
                            if dep.get("requested_as") and dep.get("file_path"):
                                resolved_type_files[dep["requested_as"]] = dep["file_path"]

                        normalized_relations = []
                        seen_edges = set()
                        for rel in raw_relations:
                            target_name = rel["target_name"]
                            rel_kind = rel["kind"]
                            target_file = rel.get("target_file") or resolved_type_files.get(target_name)
                            line = rel.get("line")
                            edge_key = (rel_kind, target_name, target_file or "", line or 0)
                            if edge_key not in seen_edges:
                                seen_edges.add(edge_key)
                                normalized_relations.append({
                                    "target_name": target_name,
                                    "kind": rel_kind,
                                    "target_file": target_file,
                                    "line": line,
                                })
                        existing_type_targets = {r["target_name"] for r in normalized_relations if r["kind"] == "uses_type"}
                        for dep in raw_type_deps:
                            dep_name = dep["name"]
                            if dep_name not in existing_type_targets:
                                existing_type_targets.add(dep_name)
                                edge_key = ("uses_type", dep_name, dep["file_path"], dumped.get("source_line") or 0)
                                if edge_key not in seen_edges:
                                    seen_edges.add(edge_key)
                                    normalized_relations.append({
                                        "target_name": dep_name,
                                        "kind": "uses_type",
                                        "target_file": dep["file_path"],
                                        "line": dumped.get("source_line"),
                                    })

                        dumped["relations"] = normalized_relations
                        values = {**dumped, "project_id": project_id, "repo_id": scope_repo, "branch": branch}
                        for field in ('type_dependencies', 'unresolved_types', 'relations'):
                            values[field] = json.dumps(values[field], ensure_ascii=False, separators=(',', ':'))
                        await db.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                            f"ON CONFLICT(project_id,repo_id,branch,file_path,name) DO UPDATE SET {updates},updated_at=CURRENT_TIMESTAMP",
                            tuple(values[column] for column in columns))

                        if mode != "snapshot":
                            await db.execute(
                                "DELETE FROM symbol_relations WHERE project_id=? AND repo_id=? AND branch=? AND source_file=? AND source_name=?",
                                (project_id, scope_repo, branch, item.file_path, item.name),
                            )
                        for rel in normalized_relations:
                            await db.execute(
                                """INSERT OR IGNORE INTO symbol_relations(
                                    project_id, repo_id, branch, source_name, source_file, source_kind,
                                    target_name, target_file, relation_kind, line
                                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                (
                                    project_id,
                                    scope_repo,
                                    branch,
                                    item.name,
                                    item.file_path,
                                    source_kind,
                                    rel["target_name"],
                                    rel.get("target_file") or "",
                                    rel["kind"],
                                    int(rel.get("line") or 0),
                                ),
                            )
                if repository:
                    await db.execute("""INSERT INTO repositories(project_id,repo_id,branch,commit_sha,dirty,scanned_at,revision,complete)
                        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id,repo_id,branch) DO UPDATE SET
                        commit_sha=excluded.commit_sha,dirty=excluded.dirty,scanned_at=excluded.scanned_at,
                        revision=excluded.revision,complete=excluded.complete,synced_at=CURRENT_TIMESTAMP""",
                        (project_id, scope_repo, branch, repository.commit_sha, int(repository.dirty), repository.scanned_at.isoformat(), revision + 1, int(mode == "snapshot")))
                if rules:
                    from server.services.rules_service import RulesService
                    for rule in rules:
                        await RulesService.add_rule(project_id, rule.title, rule.rule_content, rule.category or "general", rule.status, rule.author, rule.check_spec, _db=db)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    @staticmethod
    async def get_repositories(project_id):
        authorize(project_id)
        async with get_db_connection() as db:
            rows = await (await db.execute("SELECT * FROM repositories WHERE project_id=? ORDER BY repo_id,branch", (project_id,))).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    async def _search(project_id, table, query, filter_column, filter_value, limit, repo_id, branch):
        authorize(project_id)
        sql = f"""SELECT c.*,r.commit_sha,r.scanned_at,r.dirty,r.revision,r.complete FROM {table} c
            LEFT JOIN repositories r ON r.project_id=c.project_id AND r.repo_id=c.repo_id AND r.branch=c.branch WHERE c.project_id=?"""
        params = [project_id]
        for column, value in [(filter_column, filter_value), ("repo_id", repo_id), ("branch", branch)]:
            if value is not None and (value or column == "branch"):
                sql += f" AND c.{column}=?"
                params.append(value)
        async with get_db_connection() as db:
            rows = [RegistryService._decode(row) for row in await (await db.execute(sql, params)).fetchall()]
        fields = ["name", "description", "file_path", "props_summary" if table == "components" else "signature", "contract"]
        return rank(rows, query, fields, _clamp(limit) if limit is not None else len(rows))

    @staticmethod
    async def search_components(project_id, query=None, category=None, limit=15, repo_id=None, branch=None):
        return await RegistryService._search(project_id, "components", query, "category", category, limit, repo_id, branch)

    @staticmethod
    async def search_utilities(project_id, query=None, type_filter=None, limit=15, repo_id=None, branch=None):
        return await RegistryService._search(project_id, "utilities", query, "type", type_filter, limit, repo_id, branch)

    @staticmethod
    async def search_response(project_id, kind, query="", filter_value=None, limit=10,
                              repo_id=None, branch=None, response_level: SearchLevel = "contract",
                              max_tokens=4096, if_none_match=None, include_types=True, exclude_types=None):
        """Contrato automático solo para nombre exacto único, antes de limitar candidatos."""
        if response_level not in ("location", "contract", "detail"):
            raise ValueError("response_level debe ser location, contract o detail")
        if kind not in ("component", "utility"):
            raise ValueError("Tipo de símbolo desconocido")
        # Comprobar todo el alcance antes de limitar evita ocultar duplicados.
        rows = await RegistryService._search(project_id,
            "components" if kind == "component" else "utilities", query,
            "category" if kind == "component" else "type", filter_value, None, repo_id, branch)
        exact = [row for row in rows if row["name"].casefold() == query.strip().casefold()]
        match = "exact" if len(exact) == 1 else "ambiguous" if len(exact) > 1 else "candidates" if rows else "none"
        level = response_level
        if level == "contract":
            if match == "exact":
                selected = exact
            else:
                selected, level = rows[:_clamp(limit)], "location"
        else:
            selected = rows[:_clamp(limit)]
        excluded = _normalize_exclude_types(exclude_types)
        payload = RegistryService._project_response(selected, level, match, include_types, excluded)
        if (level == "location" and response_level == "contract") or not rows:
            payload["hint"] = "Precisa nombre/repositorio/rama; verifica cobertura y fuente."
        scope = [project_id, kind, query, filter_value, limit, repo_id, branch, response_level, include_types]
        if excluded:
            scope.append(excluded)
        return respond(payload, scope, max_tokens, if_none_match)

    @staticmethod
    async def query_knowledge_response(project_id, query="", limit=10, repo_id=None, branch=None,
                                       response_level: SearchLevel = "location", max_tokens=4096,
                                       if_none_match=None, include_types=True, exclude_types=None):
        if response_level not in ("location", "contract", "detail"):
            raise ValueError("response_level debe ser location, contract o detail")
        clamped_limit = max(1, min(int(limit), 25))
        components = await RegistryService._search(project_id, "components", query, "category", None, None, repo_id, branch)
        for row in components:
            row["kind"] = "component"
        utilities = await RegistryService._search(project_id, "utilities", query, "type", None, None, repo_id, branch)
        for row in utilities:
            row["kind"] = "utility"
        combined = sorted(components + utilities, key=lambda row: (-row.get("score", 0), row["name"], row["file_path"]))
        exact = [row for row in combined if row["name"].casefold() == query.strip().casefold()]
        match = "exact" if len(exact) == 1 else "ambiguous" if len(exact) > 1 else "candidates" if combined else "none"
        level = response_level
        if level == "contract":
            if match == "exact":
                selected = exact
            else:
                selected, level = combined[:clamped_limit], "location"
        else:
            selected = combined[:clamped_limit]
        excluded = _normalize_exclude_types(exclude_types)
        payload = RegistryService._project_response(selected, level, match, include_types, excluded, include_kind_and_score=True)
        if (level == "location" and response_level == "contract") or not combined:
            payload["hint"] = "Sin resultados exactos; comprueba la cobertura del índice y busca en el código."
        scope = [project_id, "knowledge", query, clamped_limit, repo_id, branch, response_level, include_types]
        if excluded:
            scope.append(excluded)
        return respond(payload, scope, max_tokens, if_none_match)

    @staticmethod
    def _decode(row):
        item = dict(row)
        for field in ('type_dependencies', 'unresolved_types', 'relations'):
            if field in item:
                item[field] = json.loads(item[field] or '[]')
        return item

    @staticmethod
    def _project_response(selected, level, match, include_types=True, exclude_types=None, include_kind_and_score=False):
        excluded_set = set(exclude_types or [])
        if level == "detail":
            results = selected
        else:
            fields = ("name", "file_path", "source_line", "source_end_line", "repo_id", "branch", "commit_sha",
                      "scanned_at", "dirty", "revision", "complete")
            results = [{field: row.get(field) for field in fields} for row in selected]
            if include_kind_and_score:
                for result, row in zip(results, selected):
                    if row.get("kind"):
                        result["kind"] = row["kind"]
                    if row.get("score") is not None:
                        result["score"] = row["score"]
            if level == "contract":
                for result, row in zip(results, selected):
                    # No truncar el contrato ni duplicarlo con su resumen.
                    result["contract"] = row.get("contract") or row.get("props_summary") or row.get("signature")
                    result["contract_source"] = "ast" if row.get("contract") else "summary" if result["contract"] else "missing"
                    result['type_resolution'] = row.get('type_resolution', 'not_scanned')
                    if row.get('exported') is not None:
                        result['exported'] = bool(row['exported'])
                    raw_deps = row.get('type_dependencies') or []
                    if raw_deps and include_types:
                        filtered_deps = [
                            dep for dep in raw_deps
                            if dep.get('name') not in excluded_set and dep.get('requested_as') not in excluded_set
                        ]
                        if filtered_deps:
                            result['types'] = [{key: value for key, value in dep.items()
                                if value is not None and not (key == 'kind' and value == 'type')}
                                for dep in filtered_deps]
                        if len(filtered_deps) < len(raw_deps):
                            result['types_excluded'] = len(raw_deps) - len(filtered_deps)
                    elif raw_deps:
                        result['types_available'] = len(raw_deps)
                        result['types_included'] = False
                    if row.get('unresolved_types'):
                        result['unresolved_types'] = row['unresolved_types']
        for row in results:
            stamp = datetime.fromisoformat(row['scanned_at']) if row.get('scanned_at') else None
            row['stale'] = stamp is None or (datetime.now(timezone.utc) - stamp).total_seconds() > settings.CATALOG_MAX_AGE_HOURS * 3600
        return {"level": level, "match": match, "results": results}

    @staticmethod
    async def get_symbol_detail(project_id, name, repo_id=None, branch=None, file_path=None):
        authorize(project_id)
        results = []
        async with get_db_connection() as db:
            await db.execute('BEGIN')
            for table in ('components', 'utilities'):
                sql = f"""SELECT c.*,r.commit_sha,r.scanned_at,r.dirty,r.revision,r.complete FROM {table} c
                    LEFT JOIN repositories r ON r.project_id=c.project_id AND r.repo_id=c.repo_id AND r.branch=c.branch
                    WHERE c.project_id=? AND c.name=?"""
                params = [project_id, name]
                for column, value in [('repo_id', repo_id), ('branch', branch), ('file_path', file_path)]:
                    if value is not None:
                        sql += f' AND c.{column}=?'
                        params.append(value)
                results.extend(RegistryService._decode(row) for row in await (await db.execute(sql + ' ORDER BY c.repo_id,c.branch,c.file_path', params)).fetchall())
        return results

    @staticmethod
    async def symbol_response(project_id, name, file_path, repo_id=None, branch=None, response_level: SearchLevel='contract',
                              max_tokens=4096, if_none_match=None, include_types=True, exclude_types=None):
        if response_level not in ('location', 'contract', 'detail'):
            raise ValueError('response_level debe ser location, contract o detail')
        rows = await RegistryService.get_symbol_detail(project_id, name, repo_id, branch, file_path)
        match = 'exact' if len(rows) == 1 else 'ambiguous' if rows else 'none'
        level = 'location' if match != 'exact' and response_level == 'contract' else response_level
        excluded = _normalize_exclude_types(exclude_types)
        payload = RegistryService._project_response(rows[:MAX_LIMIT], level, match, include_types, excluded)
        if match != 'exact':
            payload['hint'] = 'Precisa repositorio/rama; verifica cobertura y fuente.'
        scope = [project_id, 'symbol', name, file_path, repo_id, branch, response_level, include_types]
        if excluded:
            scope.append(excluded)
        return respond(payload, scope, max_tokens, if_none_match)

    @staticmethod
    async def get_symbol_graph(project_id, name, file_path=None, repo_id=None, branch=None,
                               direction: GraphDirection = "both", depth: int = 2,
                               max_tokens: int = 4096, if_none_match=None):
        if direction not in ("outgoing", "incoming", "both"):
            raise ValueError("direction debe ser outgoing, incoming o both")
        depth = max(1, min(int(depth), 5))
        authorize(project_id)

        roots = await RegistryService.get_symbol_detail(project_id, name, repo_id, branch, file_path)
        async with get_db_connection() as db:
            catalog_index = {}
            for table, kind_label in (("components", "component"), ("utilities", "utility")):
                sql = f"""SELECT c.name, c.file_path, c.source_line, c.source_end_line, c.repo_id, c.branch,
                          r.commit_sha, r.scanned_at, r.dirty, r.revision, r.complete
                          FROM {table} c
                          LEFT JOIN repositories r ON r.project_id=c.project_id AND r.repo_id=c.repo_id AND r.branch=c.branch
                          WHERE c.project_id=?"""
                params = [project_id]
                for col, val in (("repo_id", repo_id), ("branch", branch)):
                    if val is not None:
                        sql += f" AND c.{col}=?"
                        params.append(val)
                for row in await (await db.execute(sql, params)).fetchall():
                    item = dict(row)
                    item["kind"] = kind_label
                    catalog_index[(item["repo_id"], item["branch"], item["file_path"], item["name"])] = item
                    catalog_index.setdefault((item["repo_id"], item["branch"], "", item["name"]), item)

            def _make_node(sym_name, sym_file, sym_repo, sym_branch, dist):
                info = (
                    catalog_index.get((sym_repo, sym_branch, sym_file or "", sym_name))
                    or catalog_index.get((sym_repo, sym_branch, "", sym_name))
                )
                scanned_at = info.get("scanned_at") if info else None
                stamp = datetime.fromisoformat(scanned_at) if scanned_at else None
                stale = stamp is None or (datetime.now(timezone.utc) - stamp).total_seconds() > settings.CATALOG_MAX_AGE_HOURS * 3600
                return {
                    "name": sym_name,
                    "file_path": (info["file_path"] if info else sym_file) or None,
                    "kind": info["kind"] if info else "external",
                    "source_line": info.get("source_line") if info else None,
                    "source_end_line": info.get("source_end_line") if info else None,
                    "repo_id": sym_repo,
                    "branch": sym_branch,
                    "commit_sha": info.get("commit_sha") if info else None,
                    "stale": stale,
                    "distance": dist,
                }

            nodes_by_key = {}
            edges = []
            seen_edge_keys = set()

            if roots:
                seed_tuples = [(r["name"], r["file_path"], r["repo_id"], r["branch"]) for r in roots]
            else:
                # Even if root is not a catalog row (e.g. external utility), check symbol_relations
                seed_sql = "SELECT DISTINCT repo_id, branch, target_file FROM symbol_relations WHERE project_id=? AND (source_name=? OR target_name=?)"
                seed_params = [project_id, name, name]
                for col, val in (("repo_id", repo_id), ("branch", branch)):
                    if val is not None:
                        seed_sql += f" AND {col}=?"
                        seed_params.append(val)
                seed_rows = await (await db.execute(seed_sql, seed_params)).fetchall()
                seed_tuples = [(name, file_path or "", r["repo_id"], r["branch"]) for r in seed_rows]

            for s_name, s_file, s_repo, s_branch in seed_tuples:
                n_key = (s_repo, s_branch, s_file or "", s_name)
                nodes_by_key[n_key] = _make_node(s_name, s_file, s_repo, s_branch, 0)

            async def _traverse(dir_mode: str):
                frontier = list(seed_tuples)
                visited = {(s_repo, s_branch, s_file or "", s_name) for s_name, s_file, s_repo, s_branch in frontier}
                for hop in range(1, depth + 1):
                    if not frontier:
                        break
                    next_frontier = []
                    for cur_name, cur_file, cur_repo, cur_branch in frontier:
                        if dir_mode == "outgoing":
                            sql = """SELECT * FROM symbol_relations
                                     WHERE project_id=? AND repo_id=? AND branch=? AND source_name=?"""
                            params = [project_id, cur_repo, cur_branch, cur_name]
                            if cur_file:
                                sql += " AND source_file=?"
                                params.append(cur_file)
                            sql += " ORDER BY line, target_name"
                            rows = await (await db.execute(sql, params)).fetchall()
                            for r in rows:
                                target_file = r["target_file"] or (
                                    catalog_index.get((cur_repo, cur_branch, "", r["target_name"]), {}).get("file_path") or ""
                                )
                                e_key = (
                                    cur_repo, cur_branch, r["source_file"], r["source_name"],
                                    r["relation_kind"], r["target_name"], target_file, r["line"], "outgoing"
                                )
                                if e_key not in seen_edge_keys:
                                    seen_edge_keys.add(e_key)
                                    edges.append({
                                        "source": r["source_name"],
                                        "source_file": r["source_file"],
                                        "target": r["target_name"],
                                        "target_file": target_file or None,
                                        "kind": r["relation_kind"],
                                        "line": r["line"] or None,
                                        "distance": hop,
                                        "direction": "outgoing",
                                        "repo_id": cur_repo,
                                        "branch": cur_branch,
                                    })
                                n_key = (cur_repo, cur_branch, target_file, r["target_name"])
                                if n_key not in nodes_by_key:
                                    nodes_by_key[n_key] = _make_node(r["target_name"], target_file, cur_repo, cur_branch, hop)
                                if n_key not in visited:
                                    visited.add(n_key)
                                    next_frontier.append((r["target_name"], target_file, cur_repo, cur_branch))
                        else:
                            sql = """SELECT * FROM symbol_relations
                                     WHERE project_id=? AND repo_id=? AND branch=? AND target_name=?"""
                            params = [project_id, cur_repo, cur_branch, cur_name]
                            if cur_file:
                                sql += " AND (target_file=? OR target_file='')"
                                params.append(cur_file)
                            sql += " ORDER BY source_file, line, source_name"
                            rows = await (await db.execute(sql, params)).fetchall()
                            for r in rows:
                                target_file = r["target_file"] or cur_file or ""
                                e_key = (
                                    cur_repo, cur_branch, r["source_file"], r["source_name"],
                                    r["relation_kind"], r["target_name"], target_file, r["line"], "incoming"
                                )
                                if e_key not in seen_edge_keys:
                                    seen_edge_keys.add(e_key)
                                    edges.append({
                                        "source": r["source_name"],
                                        "source_file": r["source_file"],
                                        "target": r["target_name"],
                                        "target_file": target_file or None,
                                        "kind": r["relation_kind"],
                                        "line": r["line"] or None,
                                        "distance": hop,
                                        "direction": "incoming",
                                        "repo_id": cur_repo,
                                        "branch": cur_branch,
                                    })
                                n_key = (cur_repo, cur_branch, r["source_file"], r["source_name"])
                                if n_key not in nodes_by_key:
                                    nodes_by_key[n_key] = _make_node(r["source_name"], r["source_file"], cur_repo, cur_branch, hop)
                                if n_key not in visited:
                                    visited.add(n_key)
                                    next_frontier.append((r["source_name"], r["source_file"], cur_repo, cur_branch))
                    frontier = next_frontier

            if direction in ("outgoing", "both"):
                await _traverse("outgoing")
            if direction in ("incoming", "both"):
                await _traverse("incoming")

        match = "exact" if len(roots) == 1 else "ambiguous" if len(roots) > 1 else "external" if edges else "none"
        nodes_list = sorted(nodes_by_key.values(), key=lambda n: (n["distance"], n["name"], n["file_path"] or ""))
        summary = {
            "outgoing_count": sum(1 for e in edges if e["direction"] == "outgoing"),
            "incoming_count": sum(1 for e in edges if e["direction"] == "incoming"),
            "renders": sum(1 for e in edges if e["kind"] == "renders"),
            "calls": sum(1 for e in edges if e["kind"] == "calls"),
            "uses_type": sum(1 for e in edges if e["kind"] == "uses_type"),
        }
        payload = {
            "symbol": name,
            "match": match,
            "direction": direction,
            "depth": depth,
            "summary": summary,
            "nodes": nodes_list,
            "edges": edges,
        }
        if match == "none":
            payload["hint"] = "Símbolo o relaciones no encontradas; verifica cobertura y sincronización."
        elif match == "ambiguous":
            payload["hint"] = "Múltiples símbolos coinciden; precisa file_path, repo_id o branch."
        return respond(
            payload,
            [project_id, "graph", name, file_path, repo_id, branch, direction, depth],
            max_tokens,
            if_none_match,
        )
