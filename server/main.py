from contextlib import asynccontextmanager
import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.transport_security import TransportSecuritySettings

from server.api import api_router
from server.auth import ApiKeyMiddleware
from server.config import settings
from server.database import init_db
from server.mcp_server import mcp_server

# Falla al arrancar si no hay una API key segura configurada
settings.validate_api_key()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    print("=" * 60)
    print("Servidor HAE (Harness AI Efficiency) iniciado")
    print(f"API REST:  http://{settings.HOST}:{settings.PORT}/api")
    print(f"MCP (SSE): http://{settings.HOST}:{settings.PORT}/sse")
    print("=" * 60)
    worker = None
    if settings.YOUTRACK_URL and settings.YOUTRACK_TOKEN:
        from server.database import get_db_connection
        from server.services.webhook_service import WebhookService
        async with get_db_connection() as db:
            await db.execute("UPDATE webhook_events SET status='pending' WHERE status='processing'")
            await db.commit()
        async def retry_webhooks():
            while True:
                try:
                    await WebhookService.retry_pending()
                except Exception as exc:
                    logging.getLogger("hae.webhooks").error("Falló el ciclo de reintentos: %s",type(exc).__name__)
                await asyncio.sleep(30)
        worker = asyncio.create_task(retry_webhooks())
    try:
        yield
    finally:
        if worker:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="HAE Context Server",
    description="Servidor de contexto persistente y catálogo de código para agentes de IA (Antigravity & Cursor)",
    version="2.0.0",
    lifespan=lifespan,
)

# Orden: el último middleware añadido es el más externo.
# Auth (interno) -> CORS (externo, para que las respuestas 401 también lleven cabeceras CORS)
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # "*" + credentials es inválido y no se necesita (usamos cabecera, no cookies)
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# El SDK de MCP activa por defecto la protección DNS-rebinding solo para localhost,
# lo que devolvía 421 "Misdirected Request" al conectar por IP pública.
# Como el acceso está protegido por API key obligatoria, se desactiva esa comprobación de Host.
app.mount(
    "/",
    mcp_server.sse_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    ),
)


def run():
    uvicorn.run(
        "server.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )


if __name__ == "__main__":
    run()
