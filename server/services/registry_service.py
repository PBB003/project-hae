from datetime import datetime, timedelta, timezone

from server.database import get_db_connection
from server.policy import authorize, current_identity
from server.services.search_service import rank

MAX_LIMIT = 50


def _clamp(limit):
    return max(1, min(int(limit), MAX_LIMIT))


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
                        (project.id,project.name,project.description,project.tech_stack))
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
                    for table in ("components", "utilities"):
                        await db.execute(f"DELETE FROM {table} WHERE project_id=? AND repo_id=? AND branch=?", (project_id, scope_repo, branch))
                for table, items, extra in [("components", components, ["props_summary", "category"]),
                                            ("utilities", utilities, ["signature", "type"])]:
                    columns = ["project_id", "repo_id", "branch", "name", "file_path", "description", "source_line", "source_end_line", "contract"] + extra
                    updates = ",".join(f"{column}=excluded.{column}" for column in columns[5:])
                    for item in items:
                        values = {**item.model_dump(), "project_id": project_id, "repo_id": scope_repo, "branch": branch}
                        await db.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                            f"ON CONFLICT(project_id,repo_id,branch,file_path,name) DO UPDATE SET {updates},updated_at=CURRENT_TIMESTAMP",
                            tuple(values[column] for column in columns))
                if repository:
                    await db.execute("""INSERT INTO repositories(project_id,repo_id,branch,commit_sha,dirty,scanned_at,revision,complete)
                        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id,repo_id,branch) DO UPDATE SET
                        commit_sha=excluded.commit_sha,dirty=excluded.dirty,scanned_at=excluded.scanned_at,
                        revision=excluded.revision,complete=excluded.complete,synced_at=CURRENT_TIMESTAMP""",
                        (project_id, scope_repo, branch, repository.commit_sha, int(repository.dirty), repository.scanned_at.isoformat(), revision + 1, int(mode == "snapshot")))
                if rules:
                    from server.services.rules_service import RulesService
                    for rule in rules:
                        await RulesService.add_rule(project_id,rule.title,rule.rule_content,rule.category or "general",rule.status,rule.author,rule.check_spec,_db=db)
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
            rows = [dict(row) for row in await (await db.execute(sql, params)).fetchall()]
        fields = ["name", "description", "file_path", "props_summary" if table == "components" else "signature", "contract"]
        return rank(rows, query, fields, _clamp(limit))

    @staticmethod
    async def search_components(project_id, query=None, category=None, limit=15, repo_id=None, branch=None):
        return await RegistryService._search(project_id, "components", query, "category", category, limit, repo_id, branch)

    @staticmethod
    async def search_utilities(project_id, query=None, type_filter=None, limit=15, repo_id=None, branch=None):
        return await RegistryService._search(project_id, "utilities", query, "type", type_filter, limit, repo_id, branch)

    @staticmethod
    async def get_symbol_detail(project_id, name, repo_id=None, branch=None):
        results = []
        for search in (RegistryService.search_components, RegistryService.search_utilities):
            results.extend(item for item in await search(project_id, name, limit=50, repo_id=repo_id, branch=branch) if item["name"] == name)
        return results
