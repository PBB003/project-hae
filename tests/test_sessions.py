"""Tests para la funcionalidad de memoria de sesión (Session Logs)"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.services.session_service import SessionService
from server.services.rules_service import RulesService
from server.mcp_server import hae_save_session_log, hae_get_session_history, hae_get_project_context
import asyncio


def test_sessions(api_client, auth_headers):
    c, H = api_client, auth_headers
    # Registrar un proyecto base
    sync_payload = {
        "project": {"id": "test-app", "name": "Test App", "tech_stack": "Next.js, TypeScript"},
        "components": [
            {"name": "Header", "file_path": "src/Header.tsx", "description": "Barra de navegación"}
        ],
        "utilities": [],
        "rules": []
    }
    res = c.post("/api/sync", json=sync_payload, headers=H)
    assert res.status_code == 200

    # 1. Probar POST /api/projects/{id}/sessions
    session_data = {
        "summary": "Se implementó el sistema de login y autenticación con NextAuth",
        "completed_tasks": "Login form, sesión con JWT, middleware de rutas protegidas",
        "pending_tasks": "Agregar botón de logout y recuperar contraseña"
    }
    res = c.post("/api/projects/test-app/sessions", json=session_data, headers=H)
    assert res.status_code == 200
    assert "session_log_id" in res.json()

    # 2. Probar GET /api/projects/{id}/sessions/latest
    res = c.get("/api/projects/test-app/sessions/latest", headers=H)
    assert res.status_code == 200
    latest = res.json()["latest_session"]
    assert latest["summary"] == session_data["summary"]
    assert latest["completed_tasks"] == session_data["completed_tasks"]
    assert latest["pending_tasks"] == session_data["pending_tasks"]

    # 3. Probar GET /api/projects/{id}/summary (debe incluir la sesión)
    res = c.get("/api/projects/test-app/summary", headers=H)
    assert res.status_code == 200
    summary_text = res.json()["summary"]
    assert "Última sesión trabajada" in summary_text
    assert "NextAuth" in summary_text
    assert "Login form" in summary_text
    assert "recuperar contraseña" in summary_text

    # 4. Probar 404 en proyecto inexistente
    res = c.post("/api/projects/inexistente/sessions", json=session_data, headers=H)
    assert res.status_code == 404

    # 5. Probar MCP tools
    async def run_mcp_tests():
        msg = await hae_save_session_log(
            project_id="test-app",
            summary="Refactorización de base de datos",
            completed_tasks="Migración a PostgreSQL",
            pending_tasks="Pruebas de carga"
        )
        assert "Bitácora de sesión" in msg

        history = await hae_get_session_history("test-app", limit=5)
        assert "Refactorización de base de datos" in history
        assert "NextAuth" in history

        ctx = await hae_get_project_context("test-app")
        assert "Refactorización de base de datos" in ctx
        assert "Pruebas de carga" in ctx

    asyncio.run(run_mcp_tests())

    print("[OK] Todos los tests de memoria de sesion pasaron exitosamente!")

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__]))
