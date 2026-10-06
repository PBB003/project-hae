# Project HAE — Harness AI Efficiency

HAE aporta contexto, catálogo de código y memoria de trabajo a agentes mediante REST y MCP/SSE. Permite localizar código reutilizable, revisar reglas aprobadas y continuar tareas con sus tickets, reuniones y evidencia.

## Qué está implementado

- Índice AST de TS/TSX/JS/JSX: componentes, props, hooks, métodos públicos y contratos de tipos locales.
- Búsqueda por nombre, texto y conceptos bilingües con puntuación explicable. Es una heurística local; no usa embeddings ni garantiza encontrar todo el código.
- Catálogo aislado por proyecto, repositorio y rama, con commit, cambios locales, fecha y revisión. Snapshots completos y control de concurrencia; los clientes antiguos hacen merge sin borrar omisiones.
- Contexto compacto configurable, advertencias de vigencia y herramientas para recuperar detalles completos.
- Reglas versionadas: propuestas, revisión y aprobación administradora. `hae check` ejecuta cuatro tipos de comprobación declarativa.
- Sesiones con ticket, repositorio, rama, commit, agente, actor y evidencia.
- YouTrack con paginación, reconciliación de tickets resueltos, asignaciones por usuario y webhooks con inbox persistente y reintentos.
- Google Docs con identidad del documento, paginación, notas completas y extracción de próximos pasos.
- Claves por usuario, proyecto y rol; autorización en REST y MCP y sesiones SSE vinculadas a su credencial.

## Inicio local

Se verifica con Python 3.12. Desde la raíz:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
```

Genera una clave con `python -c "import secrets; print(secrets.token_urlsafe(32))"` y configura `HAE_API_KEY` en `.env`. Arranca con:

```powershell
.\.venv\Scripts\python.exe -m server.main
```

Copia `hae.json.example` a `hae.json` en el workspace que quieras indexar. Ajusta la identidad y los repositorios; proporciona la clave mediante `HAE_API_KEY` o configuración local ignorada por Git. `AGENTS.md` debe usar el mismo `project_id`.

```powershell
.\.venv\Scripts\hae.exe doctor
.\.venv\Scripts\hae.exe sync
.\.venv\Scripts\hae.exe check --rules-file docs/rules.example.json --strict
.\.venv\Scripts\python.exe -m pytest -q
```

En Linux/macOS, usa `.venv/bin/python` y `.venv/bin/hae`. También puedes ejecutar `python -m client.hae_sync`.

## Documentación

- [Garantías, migración y operación de HAE 2](docs/HARDENING.md)
- [Pruebas y sus límites](docs/TESTING.md)
- [Despliegue](docs/ORACLE_DEPLOYMENT.md)
- [Conexión MCP y protocolo del agente](docs/CURSOR_AND_ANTIGRAVITY.md)
- [Integración Google Meet / Docs](docs/GOOGLE_MEET_INTEGRATION.md)

El scanner todavía no indexa Python ni resuelve tipos importados entre archivos. El ahorro de tokens, la precisión de búsqueda y el efecto sobre productividad requieren mediciones; no se publican porcentajes sin evidencia. Las pruebas locales no confirman un despliegue remoto.
