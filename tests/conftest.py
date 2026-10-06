"""Configuración de pruebas: cada caso usa su propia SQLite y clave temporal."""
import secrets

import pytest


@pytest.fixture
def auth_headers(monkeypatch, tmp_path):
    from server.config import settings

    key = secrets.token_urlsafe(24)
    monkeypatch.setattr(settings, "API_KEY", key)
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "database" / "test.db"))
    return {"X-HAE-Key": key}


@pytest.fixture
def api_client(auth_headers):
    # Importar después de configurar settings: main valida la clave al cargarse.
    from fastapi.testclient import TestClient
    from server.main import app

    with TestClient(app) as client:
        yield client
