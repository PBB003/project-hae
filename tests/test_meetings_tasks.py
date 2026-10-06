"""Tests para las funcionalidades de reuniones (Google Meet) y tareas (YouTrack)"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.mcp_server import (
    hae_get_meeting_notes,
    hae_save_meeting_note,
    hae_get_youtrack_tasks,
    hae_save_youtrack_task,
    hae_get_project_context
)
import asyncio
import pytest


@pytest.fixture
def project_client(api_client, auth_headers):
    response = api_client.post("/api/sync", json={
        "project": {"id": "test-integ", "name": "Test Integration"},
        "components": [], "utilities": [],
    }, headers=auth_headers)
    assert response.status_code == 200
    return api_client


@pytest.mark.parametrize("path,payload", [
    ("meetings", {"title": "Daily", "summary": "Acuerdos"}),
    ("tasks", {"external_id": "TSO-1", "title": "Implementar filtro"}),
])
@pytest.mark.parametrize("bad_headers", [{}, {"X-HAE-Key": "incorrecta"},
                                         {"Authorization": "Bearer incorrecta"}])
def test_unauthorized_writes_do_not_change_database(project_client, auth_headers, path, payload, bad_headers):
    response = project_client.post(f"/api/projects/test-integ/{path}", json=payload, headers=bad_headers)
    assert response.status_code == 401
    response = project_client.get(f"/api/projects/test-integ/{path}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["meetings" if path == "meetings" else "active_tasks"] == []


@pytest.mark.parametrize("payload", [
    {}, {"title": "Daily"}, {"summary": "Acuerdos"},
    {"title": None, "summary": "Acuerdos"},
    {"title": 123, "summary": "Acuerdos"},
    {"title": "", "summary": "Acuerdos"},
    {"title": " \n\t ", "summary": "Acuerdos"},
    {"title": "Daily", "summary": " \n "},
    {"title": "Daily", "summary": ["Acuerdos"]},
    {"title": "Daily", "summary": "Acuerdos", "meeting_date": "2026-02-30"},
    {"title": "Daily", "summary": "Acuerdos", "meeting_date": "20261005"},
    [],
])
def test_malformed_meetings_are_rejected(project_client, auth_headers, payload):
    response = project_client.post("/api/projects/test-integ/meetings", json=payload, headers=auth_headers)
    assert response.status_code == 422
    assert project_client.get("/api/projects/test-integ/meetings", headers=auth_headers).json()["meetings"] == []


def test_invalid_json_meeting_is_rejected(project_client, auth_headers):
    response = project_client.post("/api/projects/test-integ/meetings", content='{"title":',
                                   headers={**auth_headers, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert project_client.get("/api/projects/test-integ/meetings/latest", headers=auth_headers).json()["latest_meeting"] is None


@pytest.mark.parametrize("payload", [
    {}, {"external_id": "TSO-1"}, {"external_id": "", "title": "Tarea"},
    {"external_id": " \n ", "title": "Tarea"}, {"external_id": "TSO-1", "title": "  "},
    {"external_id": 123, "title": "Tarea"},
])
def test_malformed_tasks_are_rejected(project_client, auth_headers, payload):
    response = project_client.post("/api/projects/test-integ/tasks", json=payload, headers=auth_headers)
    assert response.status_code == 422
    assert project_client.get("/api/projects/test-integ/tasks", headers=auth_headers).json()["active_tasks"] == []


def test_duplicate_ticket_updates_in_place_and_preserves_full_detail(project_client, auth_headers):
    payload = {"external_id": "TSO-164", "title": "Nómina certificada", "is_mine": True}
    first = project_client.post("/api/projects/test-integ/tasks", json=payload, headers=auth_headers)
    assert first.status_code == 200
    description = "Criterios de aceptación completos.\n" * 100 + "FIN DE LOS REQUISITOS"
    payload.update(external_id=" TSO-164 ", title="Nómina certificada actualizada",
                   status="In Progress", description=description)
    for _ in range(3):
        response = project_client.post("/api/projects/test-integ/tasks", json=payload, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["task_id"] == first.json()["task_id"]
    tasks = project_client.get("/api/projects/test-integ/tasks", headers=auth_headers).json()["active_tasks"]
    assert len(tasks) == 1
    assert tasks[0]["title"] == payload["title"]
    detail = project_client.get("/api/projects/test-integ/tasks/tso-164", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["description"] == description
    from server.mcp_server import hae_get_youtrack_task_detail
    assert description in asyncio.run(hae_get_youtrack_task_detail("test-integ", "TSO-164"))


def test_repeated_meeting_updates_without_duplicates(project_client, auth_headers):
    payload = {"title": "Daily", "summary": "Resumen inicial", "meeting_date": "2026-10-05"}
    first = project_client.post("/api/projects/test-integ/meetings", json=payload, headers=auth_headers)
    assert first.status_code == 200
    payload.update(title=" Daily ", summary=" Nuevos acuerdos ", action_items="Coordinar endpoint")
    response = project_client.post("/api/projects/test-integ/meetings", json=payload, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["meeting_note_id"] == first.json()["meeting_note_id"]
    notes = project_client.get("/api/projects/test-integ/meetings", headers=auth_headers).json()["meetings"]
    assert len(notes) == 1
    assert notes[0]["summary"] == "Nuevos acuerdos"
    assert notes[0]["action_items"] == payload["action_items"]


@pytest.mark.parametrize("status", ["Resolved", "Done", "Closed"])
def test_closed_tasks_are_hidden_but_detail_remains(project_client, auth_headers, status):
    for external_id, is_mine in [("TSO-1", False), ("TSO-2", True), ("TSO-3", False)]:
        response = project_client.post("/api/projects/test-integ/tasks", json={
            "external_id": external_id, "title": external_id, "is_mine": is_mine,
            "status": status if external_id == "TSO-3" else "Open",
        }, headers=auth_headers)
        assert response.status_code == 200
    tasks = project_client.get("/api/projects/test-integ/tasks", headers=auth_headers).json()["active_tasks"]
    assert [task["external_id"] for task in tasks] == ["TSO-2", "TSO-1"]
    mine = project_client.get("/api/projects/test-integ/tasks?only_mine=true", headers=auth_headers).json()["active_tasks"]
    assert [task["external_id"] for task in mine] == ["TSO-2"]
    assert project_client.get("/api/projects/test-integ/tasks/TSO-3", headers=auth_headers).json()["status"] == status


@pytest.mark.parametrize("method,path,payload", [
    ("post", "meetings", {"title": "Daily", "summary": "Acuerdos"}),
    ("post", "tasks", {"external_id": "TSO-1", "title": "Tarea"}),
    ("get", "meetings", None), ("get", "meetings/latest", None),
    ("get", "tasks", None), ("get", "tasks/TSO-1", None),
])
def test_missing_project_returns_404(api_client, auth_headers, method, path, payload):
    kwargs = {"headers": auth_headers}
    if payload is not None:
        kwargs["json"] = payload
    assert api_client.request(method, f"/api/projects/missing/{path}", **kwargs).status_code == 404


def test_mcp_task_retains_assignment(project_client, auth_headers):
    result = asyncio.run(hae_save_youtrack_task("test-integ", "TSO-5", "Tarea asignada", is_mine=True))
    assert "actualizada" in result
    task = project_client.get("/api/projects/test-integ/tasks/TSO-5", headers=auth_headers).json()
    assert task["is_mine"] == 1


def test_meetings_and_tasks(api_client, auth_headers):
    c, H = api_client, auth_headers
    # Registrar proyecto
    c.post("/api/sync", json={
        "project": {"id": "test-integ", "name": "Test Integration", "tech_stack": "Next.js, NestJS"},
        "components": [],
        "utilities": [],
        "rules": []
    }, headers=H)

    # 1. Test POST meeting note
    m_payload = {
        "title": "Daily - test-integ: 2026/10/05 09:30 AST - Notas de Gemini",
        "meeting_date": "2026-10-05",
        "summary": "Actualizaciones financieras y revisión de métricas para cuentas por cobrar con coordinación técnica",
        "action_items": "Coordinar endpoint de cuentas por cobrar en el backend",
        "source_url": "https://docs.google.com/document/d/123456"
    }
    res = c.post("/api/projects/test-integ/meetings", json=m_payload, headers=H)
    assert res.status_code == 200
    assert "meeting_note_id" in res.json()

    # 2. Test GET meeting note
    res = c.get("/api/projects/test-integ/meetings/latest", headers=H)
    assert res.status_code == 200
    assert "cuentas por cobrar" in res.json()["latest_meeting"]["summary"]

    # 3. Test POST task (YouTrack)
    t_payload = {
        "external_id": "TEK-104",
        "title": "Implementar filtro de métricas de cuentas por cobrar",
        "status": "In Progress",
        "description": "El usuario debe poder filtrar por fecha y estado de pago",
        "assignee": "Pedro Breynik"
    }
    res = c.post("/api/projects/test-integ/tasks", json=t_payload, headers=H)
    assert res.status_code == 200

    # 4. Test GET tasks
    res = c.get("/api/projects/test-integ/tasks", headers=H)
    assert res.status_code == 200
    assert len(res.json()["active_tasks"]) == 1
    assert res.json()["active_tasks"][0]["external_id"] == "TEK-104"

    # 5. Test summary context includes meeting and task
    res = c.get("/api/projects/test-integ/summary", headers=H)
    assert res.status_code == 200
    summary_text = res.json()["summary"]
    assert "Última Daily / Reunión" in summary_text
    assert "cuentas por cobrar" in summary_text
    assert "TEK-104" in summary_text

    # 6. Test MCP Tools
    async def run_mcp():
        msg_m = await hae_get_meeting_notes("test-integ")
        assert "cuentas por cobrar" in msg_m

        msg_t = await hae_get_youtrack_tasks("test-integ")
        assert "TEK-104" in msg_t

        ctx = await hae_get_project_context("test-integ")
        assert "TEK-104" in ctx

    asyncio.run(run_mcp())
    print("[OK] Tests de reuniones y tareas pasaron exitosamente!")

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__]))
