import json

from server.database import get_db_connection
from server.models import ProjectTaskCreate
from server.policy import authorize


class TasksService:
    @staticmethod
    async def _store(db, project_id, task, viewer_id):
        old = await (await db.execute("SELECT id,source_updated_at FROM project_tasks WHERE project_id=? AND external_id=?",
            (project_id, task.external_id))).fetchone()
        if old and old["source_updated_at"] is not None and (task.source_updated_at is None or task.source_updated_at < old["source_updated_at"]):
            return old["id"]
        await db.execute("""INSERT INTO project_tasks(project_id,external_id,title,status,description,assignee,url,is_mine,
            resolved_at,source_updated_at,source_id,assignee_ids,archived,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP) ON CONFLICT(project_id,external_id) DO UPDATE SET
            title=excluded.title,status=excluded.status,description=excluded.description,assignee=excluded.assignee,
            url=excluded.url,is_mine=CASE WHEN ?='legacy' THEN excluded.is_mine ELSE project_tasks.is_mine END,
            resolved_at=excluded.resolved_at,source_updated_at=excluded.source_updated_at,source_id=excluded.source_id,
            assignee_ids=excluded.assignee_ids,archived=0,updated_at=CURRENT_TIMESTAMP""",
            (project_id, task.external_id, task.title, task.status or "Open", task.description, task.assignee, task.url,
             int(bool(task.is_mine)) if viewer_id == "legacy" else 0, task.resolved_at, task.source_updated_at, task.source_id,
             json.dumps(task.assignee_ids), viewer_id))
        if task.source_updated_at is not None:
            # Los IDs de usuario HAE configurados para YouTrack deben coincidir
            # con sus IDs en YouTrack. Recalcular todos evita flags obsoletos
            # cuando un webhook reasigna un issue a otra persona.
            memberships = await (await db.execute("SELECT user_id FROM task_memberships WHERE project_id=? AND external_id=?",
                (project_id,task.external_id))).fetchall()
            for member in memberships:
                await db.execute("UPDATE task_memberships SET is_mine=? WHERE project_id=? AND external_id=? AND user_id=?",
                    (int(member["user_id"] in task.assignee_ids),project_id,task.external_id,member["user_id"]))
            for user_id in task.assignee_ids:
                await db.execute("INSERT INTO task_memberships(project_id,external_id,user_id,is_mine) VALUES(?,?,?,1) ON CONFLICT(project_id,external_id,user_id) DO UPDATE SET is_mine=1",
                    (project_id,task.external_id,user_id))
        await db.execute("""INSERT INTO task_memberships(project_id,external_id,user_id,is_mine) VALUES(?,?,?,?)
            ON CONFLICT(project_id,external_id,user_id) DO UPDATE SET is_mine=excluded.is_mine""",
            (project_id, task.external_id, viewer_id, int(viewer_id in task.assignee_ids) if task.source_updated_at is not None else int(bool(task.is_mine))))
        return (await (await db.execute("SELECT id FROM project_tasks WHERE project_id=? AND external_id=?", (project_id, task.external_id))).fetchone())["id"]

    @staticmethod
    async def upsert_task(project_id, external_id, title, status="Open", description=None, assignee=None, url=None,
                          is_mine=False, resolved_at=None, source_updated_at=None, source_id=None, assignee_ids=None, viewer_id=None):
        identity = authorize(project_id, write=True)
        viewer_id = viewer_id or identity.user_id
        if identity.role != "admin" and viewer_id != identity.user_id:
            from fastapi import HTTPException
            raise HTTPException(403, "No puedes modificar la asignación de otro usuario")
        task = ProjectTaskCreate(external_id=external_id, title=title, status=status, description=description,
            assignee=assignee, url=url, is_mine=is_mine, resolved_at=resolved_at, source_updated_at=source_updated_at,
            source_id=source_id, assignee_ids=assignee_ids or [])
        async with get_db_connection() as db:
            if not await (await db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,))).fetchone():
                raise ValueError(f"Proyecto '{project_id}' no existe en HAE.")
            task_id = await TasksService._store(db, project_id, task, viewer_id)
            await db.commit()
            return task_id

    @staticmethod
    async def sync_tasks(project_id, snapshot):
        identity = authorize(project_id, write=True)
        viewer_id = snapshot.viewer_id if identity.role == "admin" else identity.user_id
        if identity.role != "admin" and snapshot.viewer_id not in ("legacy", identity.user_id):
            from fastapi import HTTPException
            raise HTTPException(403, "No puedes sincronizar como otro usuario")
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                if not await (await db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,))).fetchone():
                    raise ValueError(f"Proyecto '{project_id}' no existe en HAE.")
                ids = set()
                for task in snapshot.tasks:
                    await TasksService._store(db, project_id, task, viewer_id)
                    ids.add(task.external_id)
                if snapshot.complete:
                    cutoff = int(snapshot.observed_at.timestamp() * 1000)
                    rows = await (await db.execute("SELECT external_id FROM project_tasks WHERE project_id=? AND (source_updated_at IS NULL OR source_updated_at<=?)",
                        (project_id,cutoff))).fetchall()
                    for row in rows:
                        if row["external_id"] not in ids:
                            await db.execute("UPDATE project_tasks SET archived=1,updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND external_id=?", (project_id,row["external_id"]))
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return len(ids)

    @staticmethod
    async def get_active_tasks(project_id, limit=10, only_mine=False, viewer_id=None):
        identity = authorize(project_id)
        viewer_id = viewer_id or identity.user_id
        if identity.role != "admin" and viewer_id != identity.user_id:
            from fastapi import HTTPException
            raise HTTPException(403, "No puedes consultar asignaciones de otro usuario")
        sql = """SELECT t.*, COALESCE(m.is_mine, CASE WHEN ?='legacy' THEN t.is_mine ELSE 0 END) personal_mine
            FROM project_tasks t LEFT JOIN task_memberships m ON m.project_id=t.project_id AND m.external_id=t.external_id AND m.user_id=?
            WHERE t.project_id=? AND t.archived=0 AND t.resolved_at IS NULL AND t.status NOT IN ('Resolved','Done','Closed')"""
        if only_mine:
            sql += " AND COALESCE(m.is_mine, CASE WHEN ?='legacy' THEN t.is_mine ELSE 0 END)=1"
        params = [viewer_id, viewer_id, project_id] + ([viewer_id] if only_mine else [])
        sql += " ORDER BY personal_mine DESC,t.updated_at DESC,t.external_id LIMIT ?"
        params.append(max(1,min(int(limit),100)))
        async with get_db_connection() as db:
            rows = await (await db.execute(sql, params)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["is_mine"] = item.pop("personal_mine")
                item["assignee_ids"] = json.loads(item["assignee_ids"] or "[]")
                result.append(item)
            return result

    @staticmethod
    async def get_task_by_id(project_id, external_id):
        identity = authorize(project_id)
        async with get_db_connection() as db:
            row = await (await db.execute("SELECT * FROM project_tasks WHERE project_id=? AND UPPER(external_id)=UPPER(?)",
                (project_id,external_id.strip()))).fetchone()
            if not row:
                return None
            item = dict(row)
            member = await (await db.execute("SELECT is_mine FROM task_memberships WHERE project_id=? AND external_id=? AND user_id=?",
                (project_id,item["external_id"],identity.user_id))).fetchone()
            item["is_mine"] = member["is_mine"] if member else (item["is_mine"] if identity.user_id == "legacy" else 0)
            item["assignee_ids"] = json.loads(item["assignee_ids"] or "[]")
            return item
