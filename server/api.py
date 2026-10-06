from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from server.models import RuleItem, SyncPayload, SessionLogCreate, MeetingNoteCreate, ProjectTaskCreate, TaskSnapshot, YouTrackEvent
from server.services.registry_service import RegistryService, SyncConflict
from server.services.rules_service import RulesService
from server.services.session_service import SessionService
from server.services.meetings_service import MeetingsService
from server.services.tasks_service import TasksService


# La autenticación (X-HAE-Key / Bearer) la aplica ApiKeyMiddleware a toda la app;
# solo /api/health es público.
api_router = APIRouter(prefix="/api")


@api_router.get("/health")
async def health_check():
    return {"status": "ok", "service": "HAE Context Server", "api_version": 2,
            "capabilities": ["repository_snapshots", "ranked_concept_search", "symbol_contracts",
                             "task_snapshots", "versioned_rules", "scoped_keys", "meeting_detail"]}


@api_router.get("/projects")
async def list_projects():
    return await RegistryService.list_projects()


@api_router.get("/projects/{project_id}/summary")
async def get_project_summary(project_id: str, repo_id: str | None = None, branch: str | None = None,
                              ticket_id: str | None = None, expected_commit: str | None = None,
                              max_chars: int | None = Query(None, ge=1500, le=20000)):
    if not await RegistryService.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado")
    summary = await RulesService.get_project_context_summary(project_id,repo_id,branch,ticket_id,expected_commit,max_chars)
    return {"project_id": project_id, "summary": summary}


@api_router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    if not await RegistryService.delete_project(project_id):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado")
    return {"status": "deleted", "project_id": project_id}


@api_router.post("/sync")
async def sync_project(payload: SyncPayload):
    try:
        await RegistryService.sync_catalog(payload.project.id,payload.components,payload.utilities,payload.repository,payload.mode,
                                           project=payload.project,rules=payload.rules)
    except SyncConflict as exc:
        raise HTTPException(409,str(exc))
    except ValueError as exc:
        raise HTTPException(422,str(exc))

    return {
        "status": "success",
        "message": f"Proyecto '{payload.project.id}' sincronizado exitosamente",
        "synced_components": len(payload.components),
        "synced_utilities": len(payload.utilities),
    }


@api_router.post("/rules")
async def add_rule(project_id: str, rule: RuleItem):
    try:
        rule_id = await RulesService.add_rule(
            project_id=project_id,
            title=rule.title,
            rule_content=rule.rule_content,
            category=rule.category or "general",
            status=rule.status, author=rule.author, check_spec=rule.check_spec,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "success", "rule_id": rule_id}


@api_router.post("/projects/{project_id}/sessions")
async def add_session_log(project_id: str, payload: SessionLogCreate):
    try:
        log_id = await SessionService.add_session_log(
            project_id=project_id,
            summary=payload.summary,
            completed_tasks=payload.completed_tasks,
            pending_tasks=payload.pending_tasks,
            repo_id=payload.repo_id, branch=payload.branch, commit_sha=payload.commit_sha,
            ticket_id=payload.ticket_id, agent=payload.agent, evidence=payload.evidence,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "success", "session_log_id": log_id}


@api_router.get("/projects/{project_id}/sessions")
async def get_session_logs(project_id: str, limit: int = Query(5,ge=1,le=50), ticket_id: str | None = None, branch: str | None = None):
    if not await RegistryService.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado")
    logs = await SessionService.get_session_logs(project_id, limit=limit,ticket_id=ticket_id,branch=branch)
    return {"project_id": project_id, "sessions": logs}


@api_router.get("/projects/{project_id}/sessions/latest")
async def get_latest_session_log(project_id: str):
    if not await RegistryService.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado")
    log = await SessionService.get_latest_session_log(project_id)
    return {"project_id": project_id, "latest_session": log}


@api_router.post("/projects/{project_id}/meetings")
async def add_meeting_note(project_id: str, payload: MeetingNoteCreate):
    try:
        note_id = await MeetingsService.add_meeting_note(
            project_id=project_id,
            title=payload.title,
            summary=payload.summary,
            action_items=payload.action_items,
            meeting_date=payload.meeting_date,
            raw_notes=payload.raw_notes,
            source_url=payload.source_url,
            source_id=payload.source_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "success", "meeting_note_id": note_id}


@api_router.get("/projects/{project_id}/meetings")
async def list_meeting_notes(project_id: str, limit: int = 5):
    if not await RegistryService.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado")
    notes = await MeetingsService.list_meeting_notes(project_id, limit=limit)
    return {"project_id": project_id, "meetings": notes}


@api_router.get("/projects/{project_id}/meetings/latest")
async def get_latest_meeting_note(project_id: str):
    if not await RegistryService.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado")
    note = await MeetingsService.get_latest_meeting_note(project_id)
    return {"project_id": project_id, "latest_meeting": note}


@api_router.post("/projects/{project_id}/tasks")
async def upsert_task(project_id: str, payload: ProjectTaskCreate):
    try:
        task_id = await TasksService.upsert_task(
            project_id=project_id,
            external_id=payload.external_id,
            title=payload.title,
            status=payload.status,
            description=payload.description,
            assignee=payload.assignee,
            url=payload.url,
            is_mine=bool(payload.is_mine),
            resolved_at=payload.resolved_at, source_updated_at=payload.source_updated_at,
            source_id=payload.source_id, assignee_ids=payload.assignee_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "success", "task_id": task_id}


@api_router.get("/projects/{project_id}/tasks")
async def get_active_tasks(project_id: str, limit: int = Query(10,ge=1,le=100), only_mine: bool = False):
    if not await RegistryService.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado")
    tasks = await TasksService.get_active_tasks(project_id, limit=limit, only_mine=only_mine)
    return {"project_id": project_id, "active_tasks": tasks}


@api_router.get("/projects/{project_id}/tasks/{external_id}")
async def get_task_detail(project_id: str, external_id: str):
    if not await RegistryService.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado")
    task = await TasksService.get_task_by_id(project_id, external_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Ticket '{external_id}' no encontrado")
    return task


@api_router.get("/projects/{project_id}/repositories")
async def get_repositories(project_id: str):
    if not await RegistryService.get_project(project_id):
        raise HTTPException(404,"Proyecto no encontrado")
    return {"repositories": await RegistryService.get_repositories(project_id)}


@api_router.get("/projects/{project_id}/components")
async def search_components(project_id: str, query: str = "", category: str | None = None,
                             repo_id: str | None = None, branch: str | None = None, limit: int = Query(10,ge=1,le=50)):
    return await RegistryService.search_components(project_id,query,category,limit,repo_id,branch)


@api_router.get("/projects/{project_id}/utilities")
async def search_utilities(project_id: str, query: str = "", type_filter: str | None = None,
                           repo_id: str | None = None, branch: str | None = None, limit: int = Query(10,ge=1,le=50)):
    return await RegistryService.search_utilities(project_id,query,type_filter,limit,repo_id,branch)


@api_router.get("/projects/{project_id}/symbols/{name}")
async def symbol_detail(project_id: str, name: str, repo_id: str | None = None, branch: str | None = None):
    symbols = await RegistryService.get_symbol_detail(project_id,name,repo_id,branch)
    if not symbols:
        raise HTTPException(404,"Símbolo no encontrado; una búsqueda vacía no prueba ausencia en el repositorio")
    return symbols


@api_router.get("/projects/{project_id}/rules")
async def get_rules(project_id: str, status: str = "active"):
    return {"rules": await RulesService.get_rules(project_id,status=status)}


@api_router.get("/projects/{project_id}/rules/{rule_id}/history")
async def rule_history(project_id: str, rule_id: int):
    return {"revisions": await RulesService.get_history(project_id,rule_id)}


@api_router.post("/projects/{project_id}/rules/{rule_id}/approve/{revision}")
async def approve_rule(project_id: str, rule_id: int, revision: int):
    try:
        await RulesService.approve_revision(project_id,rule_id,revision)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return {"status":"approved"}


@api_router.get("/projects/{project_id}/meetings/{note_id}")
async def meeting_detail(project_id: str, note_id: int):
    note = await MeetingsService.get_meeting_detail(project_id,note_id)
    if not note:
        raise HTTPException(404,"Reunión no encontrada")
    return note


@api_router.post("/projects/{project_id}/tasks/sync")
async def sync_tasks(project_id: str, payload: TaskSnapshot):
    try:
        count = await TasksService.sync_tasks(project_id,payload)
    except ValueError as exc:
        raise HTTPException(404,str(exc))
    return {"status":"success","synced_tasks":count}


@api_router.post("/webhooks/youtrack/{project_id}")
async def youtrack_webhook(project_id: str, event: YouTrackEvent):
    from server.services.webhook_service import WebhookService
    return await WebhookService.receive(project_id,event)


@api_router.get("/projects/{project_id}/webhook-events")
async def webhook_events(project_id: str):
    from server.services.webhook_service import WebhookService
    return {"events":await WebhookService.list_events(project_id)}


@api_router.post("/projects/{project_id}/webhook-events/{event_id}/retry")
async def retry_webhook(project_id: str, event_id: str):
    from server.services.webhook_service import WebhookService
    status = await WebhookService.retry_event(project_id,event_id)
    return {"status":status}
