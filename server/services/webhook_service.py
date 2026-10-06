"""Inbox durable e idempotente; los eventos parciales se reconcilian con YouTrack."""
import hashlib
import json
from datetime import datetime

import httpx
from fastapi import HTTPException

from server.config import settings
from server.database import get_db_connection
from server.models import ProjectTaskCreate
from server.policy import authorize, current_identity, Identity
from server.services.tasks_service import TasksService
from client.youtrack_sync import issue_to_task


class WebhookService:
    @staticmethod
    async def receive(project_id, event):
        authorize(project_id,write=True)
        if not settings.YOUTRACK_URL or not settings.YOUTRACK_TOKEN:
            raise HTTPException(503,"Configura HAE_YOUTRACK_URL y HAE_YOUTRACK_TOKEN")
        expected = settings.YOUTRACK_PROJECTS.get(project_id)
        if not expected or event.project.get("shortName") != expected:
            raise HTTPException(403,"El proyecto YouTrack no corresponde al proyecto HAE")
        serialized = json.dumps(event.model_dump(mode="json"),sort_keys=True,separators=(',',':'))
        event_id = hashlib.sha256(serialized.encode()).hexdigest()
        async with get_db_connection() as db:
            if not await (await db.execute("SELECT 1 FROM projects WHERE id=?",(project_id,))).fetchone():
                raise HTTPException(404,"Proyecto HAE no encontrado")
            await db.execute("INSERT OR IGNORE INTO webhook_events(event_id,project_id,payload) VALUES(?,?,?)",(event_id,project_id,serialized))
            await db.commit()
        await WebhookService.process(event_id)
        async with get_db_connection() as db:
            row = await (await db.execute("SELECT status FROM webhook_events WHERE event_id=?",(event_id,))).fetchone()
        return {"event_id":event_id,"status":row["status"]}

    @staticmethod
    async def process(event_id):
        async with get_db_connection() as db:
            cursor = await db.execute("UPDATE webhook_events SET status='processing',attempts=attempts+1 WHERE event_id=? AND status IN ('pending','failed') AND attempts<5",(event_id,))
            await db.commit()
            if not cursor.rowcount:
                return
            event_row = dict(await (await db.execute("SELECT * FROM webhook_events WHERE event_id=?",(event_id,))).fetchone())
        project_id, event = event_row["project_id"], json.loads(event_row["payload"])
        token = current_identity.set(Identity("youtrack-webhook","writer",(project_id,)))
        try:
            if event["event"] == "issueDeleted":
                deleted_at = int(datetime.fromisoformat(event["timestamp"]).timestamp() * 1000)
                async with get_db_connection() as db:
                    await db.execute("UPDATE project_tasks SET archived=1,source_updated_at=?,updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND source_id=? AND (source_updated_at IS NULL OR source_updated_at<=?)",
                        (deleted_at,project_id,event["id"],deleted_at))
                    await db.commit()
            else:
                # Recuperar el issue completo: no borrar campos que el evento no incluye.
                from urllib.parse import quote
                url = settings.YOUTRACK_URL.rstrip('/') + '/api/issues/' + quote(event["id"],safe='')
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.get(url,headers={"Authorization":f"Bearer {settings.YOUTRACK_TOKEN}"},
                        params={"fields":"id,idReadable,summary,description,resolved,updated,customFields(name,value(id,name,login,isResolved))"})
                    response.raise_for_status()
                    issue = response.json()
                if not isinstance(issue,dict) or type(issue.get("updated")) is not int:
                    raise ValueError("Issue sin fecha updated verificable")
                if issue.get("idReadable","").rsplit('-',1)[0] != settings.YOUTRACK_PROJECTS[project_id]:
                    raise ValueError("Proyecto del issue inesperado")
                task = ProjectTaskCreate(**issue_to_task(issue,"youtrack-webhook",settings.YOUTRACK_URL))
                await TasksService.upsert_task(project_id,**task.model_dump())
            async with get_db_connection() as db:
                await db.execute("UPDATE webhook_events SET status='done',last_error=NULL WHERE event_id=?",(event_id,))
                await db.commit()
        except Exception as exc:
            async with get_db_connection() as db:
                await db.execute("UPDATE webhook_events SET status='failed',last_error=? WHERE event_id=?",(type(exc).__name__,event_id))
                await db.commit()
        finally:
            current_identity.reset(token)

    @staticmethod
    async def retry_pending():
        async with get_db_connection() as db:
            # Recuperación de trabajo interrumpido por un reinicio.
            rows = await (await db.execute("SELECT event_id FROM webhook_events WHERE status IN ('pending','failed') AND attempts<5 ORDER BY created_at LIMIT 20")).fetchall()
        for row in rows:
            await WebhookService.process(row["event_id"])

    @staticmethod
    async def list_events(project_id):
        authorize(project_id,admin=True)
        async with get_db_connection() as db:
            rows = await (await db.execute("SELECT event_id,status,attempts,last_error,created_at FROM webhook_events WHERE project_id=? ORDER BY created_at DESC LIMIT 100",(project_id,))).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    async def retry_event(project_id,event_id):
        authorize(project_id,admin=True)
        async with get_db_connection() as db:
            row = await (await db.execute("SELECT status FROM webhook_events WHERE project_id=? AND event_id=?",(project_id,event_id))).fetchone()
            if not row:
                raise HTTPException(404,"Evento no encontrado")
            if row["status"] == "failed":
                await db.execute("UPDATE webhook_events SET attempts=0 WHERE event_id=?",(event_id,))
                await db.commit()
        await WebhookService.process(event_id)
        async with get_db_connection() as db:
            row = await (await db.execute("SELECT status FROM webhook_events WHERE event_id=?",(event_id,))).fetchone()
            return row["status"]
