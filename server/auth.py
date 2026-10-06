import secrets
import json
import hashlib
from typing import Iterable, Optional, Tuple

from starlette.responses import JSONResponse
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from server.config import settings
from server.policy import Identity, current_identity

# Únicas rutas sin autenticación (para health checks)
PUBLIC_PATHS = {"/api/health"}


def is_valid_key(provided: Optional[str]) -> bool:
    return resolve_identity(provided) is not None


def resolve_identity(provided):
    if not provided:
        return None
    if settings.API_KEY and secrets.compare_digest(provided.encode(), settings.API_KEY.encode()):
        return Identity()
    for client in settings.CLIENT_KEYS:
        if secrets.compare_digest(provided.encode(), client.key.encode()):
            return Identity(client.user_id, client.role, tuple(client.projects))
    return None


def extract_key(headers: Iterable[Tuple[bytes, bytes]]) -> Optional[str]:
    """Acepta `X-HAE-Key: <key>` o `Authorization: Bearer <key>`."""
    bearer = None
    for name, value in headers:
        name = name.lower()
        if name == b"x-hae-key":
            return value.decode("latin-1")
        if name == b"authorization" and value[:7].lower() == b"bearer ":
            bearer = value[7:].decode("latin-1").strip()
    return bearer


class ApiKeyMiddleware:
    """Middleware ASGI puro (compatible con streaming SSE) que protege TODA la app,
    incluido el endpoint MCP montado en la raíz."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return
        provided = extract_key(scope["headers"])
        identity = resolve_identity(provided)
        if scope["path"].startswith("/api/webhooks/youtrack/"):
            token = dict(scope["headers"]).get(b"x-youtrack-token", b"").decode("latin-1")
            if settings.WEBHOOK_TOKEN and secrets.compare_digest(token.encode(), settings.WEBHOOK_TOKEN.encode()):
                identity = Identity("youtrack-webhook", "writer", (scope["path"].rsplit("/", 1)[-1],))
            else:
                identity = None
        if identity is None:
            await JSONResponse({"detail": "API key inválida o no provista"}, status_code=401)(scope, receive, send)
            return
        # MCP 2.3 vincula la sesión SSE a este principal. El fingerprint impide
        # reutilizar el session_id de una conexión con otra credencial.
        fingerprint = hashlib.sha256((provided or "webhook").encode()).hexdigest()
        scope["user"] = AuthenticatedUser(AccessToken(token=fingerprint, client_id=fingerprint, scopes=[identity.role]))
        # El transporte SSE entrega mensajes desde un task propio. Validar
        # también aquí el proyecto y el permiso de cada tools/call evita depender
        # únicamente de la propagación de contextvars del SDK.
        if identity.role != "admin" and scope["method"] == "POST" and not scope["path"].startswith("/api/"):
            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if len(body) > 2_000_000:
                    await JSONResponse({"detail": "Mensaje demasiado grande"}, status_code=413)(scope, receive, send)
                    return
                if not message.get("more_body"):
                    break
            try:
                request = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                request = None
            if not isinstance(request, dict) or not isinstance(request.get("params", {}), dict):
                await JSONResponse({"detail": "Mensaje MCP inválido; no se admiten lotes"}, status_code=400)(scope, receive, send)
                return
            if request.get("method") == "tools/call":
                params = request.get("params", {})
                args = params.get("arguments", {})
                name = params.get("name", "")
                if not isinstance(args, dict) or not isinstance(name, str):
                    await JSONResponse({"detail": "Argumentos MCP inválidos"}, status_code=400)(scope, receive, send)
                    return
                reads = {"hae_get_project_context", "hae_search_components", "hae_search_utilities",
                         "hae_get_session_history", "hae_get_meeting_notes", "hae_get_meeting_detail",
                         "hae_get_youtrack_tasks", "hae_get_youtrack_task_detail", "hae_list_projects",
                         "hae_query_knowledge", "hae_get_symbol_detail", "hae_get_symbol", "hae_get_rules", "hae_get_rule_history"}
                denied = (name != "hae_list_projects" and args.get("project_id") not in identity.projects) or (
                    identity.role == "reader" and name not in reads)
                if denied:
                    await JSONResponse({"detail": "Herramienta o proyecto no permitido"}, status_code=403)(scope, receive, send)
                    return
            original_receive = receive
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await original_receive()

            receive = replay
        token = current_identity.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            current_identity.reset(token)
