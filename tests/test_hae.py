"""Tests de regresión de HAE: python -m pytest tests/test_hae.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from client.scanner import scan_codebase  # noqa: E402
from server.config import Settings  # noqa: E402

PAYLOAD = {
    "project": {"id": "p1", "name": "P1", "tech_stack": "React"},
    "components": [
        {"name": "Button", "file_path": "src/Button.tsx", "description": "boton primario"},
        {"name": "Button", "file_path": "src/Button.tsx", "description": "duplicado en payload"},
        {"name": "Modal", "file_path": "src/Modal.tsx", "description": "100% modal_dialog"},
    ],
    "utilities": [],
    "rules": [{"title": "Iconos", "rule_content": "lucide", "category": "styling"}],
}


def test_server(api_client, auth_headers):
    c, H = api_client, auth_headers
    assert c.get("/api/health").status_code == 200
    # Auth obligatoria en API y en el endpoint MCP montado
    assert c.get("/api/projects").status_code == 401
    assert c.get("/api/projects", headers={"X-HAE-Key": "mala"}).status_code == 401
    assert c.get("/sse").status_code == 401
    assert c.get("/api/projects", headers={"Authorization": f"Bearer {H['X-HAE-Key']}"}).status_code == 200

    # Sync duplicado en payload no debe dar 500; reglas no se duplican al re-sincronizar
    for _ in range(3):
        r = c.post("/api/sync", json=PAYLOAD, headers=H)
        assert r.status_code == 200, r.text
    summary = c.get("/api/projects/p1/summary", headers=H).json()["summary"]
    assert summary.count("[Iconos]") == 1, summary
    assert "1 componentes" not in summary and "2 componentes" in summary, summary

    # 404 en vez de 500 para proyecto inexistente
    assert c.get("/api/projects/nope/summary", headers=H).status_code == 404
    r = c.post("/api/rules", params={"project_id": "nope"}, json={"title": "x", "rule_content": "y"}, headers=H)
    assert r.status_code == 404


def test_search(api_client, auth_headers):
    assert api_client.post("/api/sync", json=PAYLOAD, headers=auth_headers).status_code == 200
    import asyncio
    from server.services.registry_service import RegistryService

    async def run():
        assert [r["name"] for r in await RegistryService.search_components("p1", "modal")] == ["Modal"]
        # % y _ se tratan literalmente (antes '%' devolvía todo)
        assert [r["name"] for r in await RegistryService.search_components("p1", "100%")] == ["Modal"]
        assert await RegistryService.search_components("p1", "%%%") == []
        # búsqueda multi-palabra
        assert [r["name"] for r in await RegistryService.search_components("p1", "duplicado payload")] == ["Button"]

    asyncio.run(run())


def test_placeholder_key_rejected():
    for bad in ("", "hae-secret-token-change-me", "tu-clave-secreta-hae-2026", "corta"):
        try:
            Settings(API_KEY=bad).validate_api_key()
        except RuntimeError:
            continue
        raise AssertionError(f"clave insegura aceptada: {bad!r}")


def test_scanner(tmp_path):
    # El proyecto vive bajo una carpeta llamada 'build': antes se ignoraba todo
    base = tmp_path / "build" / "app"
    (base / "src" / "components").mkdir(parents=True)
    (base / "src" / "components" / "Card.tsx").write_text("export function Card() { return null }", encoding="utf-8")
    (base / "src" / "components" / "consts.ts").write_text("export const API_URL = 'x'", encoding="utf-8")
    (base / "src" / "components" / "Card.test.tsx").write_text("export function CardTest() {}", encoding="utf-8")
    (base / "node_modules" / "x").mkdir(parents=True)
    (base / "node_modules" / "x" / "Y.tsx").write_text("export function Y() {}", encoding="utf-8")
    comps, _ = scan_codebase(str(base))
    assert [c["name"] for c in comps] == ["Card"], comps


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__]))
