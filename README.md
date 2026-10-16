# Project HAE — Harness AI Efficiency

HAE aporta contexto verificable, catálogo de código, grafo relacional de símbolos y memoria de trabajo a agentes mediante REST y MCP/SSE. Permite localizar código reutilizable, analizar dependencias e impacto de cambios, revisar reglas aprobadas y continuar tareas con sus tickets, reuniones y evidencia.

## Qué está implementado

- **Índice AST de TS/TSX/JS/JSX y Python:**
  - TypeScript/React (`tree-sitter`): componentes, props, hooks, clases, métodos públicos y contratos; resolución selectiva de tipos locales con aliases (`tsconfig.json`), reexportaciones y pendientes explícitos.
  - Python (`ast` nativo en `client/python_parser.py`): clases, modelos Pydantic, métodos públicos, endpoints FastAPI, herramientas MCP y funciones de servicio, con resolución de tipos locales importados.
  - Rangos exactos de línea (`source_line` y `source_end_line`) en cada símbolo y dependencia de tipo para que el agente lea únicamente el fragmento necesario en disco.
- **Grafo relacional de dependencias e impacto (`symbol_relations` y `hae_get_symbol_graph`):**
  - Extrae relaciones estáticas `renders` (componentes JSX), `calls` (llamadas a funciones/métodos) y `uses_type` (tipos referenciados) durante el escaneo AST.
  - Permite consultar dependencias salientes (`direction="outgoing"`), radio de impacto / dependientes (`direction="incoming"`) o ambos (`direction="both"`) de 1 a 5 saltos vía MCP (`hae_get_symbol_graph`) y REST (`GET /api/projects/{id}/symbols/{name}/graph`).
- **Contexto dinámico guiado por ticket (`hae_get_project_context`):**
  - Detecta automáticamente el `ticket_id` a partir del nombre de la rama Git (ej. `TSO-164/feature`), inyecta la especificación del ticket activo, sugiere los símbolos candidatos del catálogo con sus rutas y rangos `Lstart-Lend`, y aplica fallback progresivo de bitácoras de sesión.
- **Eficiencia de tokens, niveles de respuesta y validadores (`respond`):**
  - Niveles `location`, `contract` y `detail` en `hae_search_components`, `hae_search_utilities`, `hae_get_symbol` y `hae_query_knowledge`.
  - Deduplicación de tipos ya conocidos en la conversación mediante `include_types=False` o `exclude_types=[...]`, manteniendo validadores `etag` (`if_none_match`) aislados y presupuesto conservador por bytes UTF-8 (`max_tokens`) sin truncar contratos a mitad de cadena.
- **Búsqueda léxica y conceptual bilingüe:**
  - Búsqueda por nombre, ruta, firma y conceptos español/inglés con puntuación explicable. Es una heurística local determinista; no usa embeddings ni garantiza encontrar todo el código.
- **Catálogo con manifiestos por repositorio y rama:**
  - Aislado por `project_id`, `repo_id` y `branch`, con `commit_sha`, estado `dirty`, fecha de escaneo y control optimista de concurrencia (`revision`).
- **Gobernanza de reglas y verificación declarativa (`hae check`):**
  - Reglas versionadas con flujo `proposed` $\rightarrow$ revisión $\rightarrow$ `active` (aprobación administradora). `hae check` evalúa comprobaciones declarativas (`forbidden_import`, `forbidden_pattern`, `required_file`, `max_file_lines`) e informa explícitamente las reglas textuales no verificables automáticamente.
- **Memoria operativa e integraciones externas:**
  - Bitácoras de sesión con ticket, repositorio, rama, commit, agente, actor y evidencia de pruebas.
  - YouTrack con paginación, reconciliación de tickets resueltos, asignaciones por usuario y webhooks con inbox persistente y reintentos.
  - Google Docs con identidad del documento, paginación, notas completas y extracción de acuerdos.
- **Seguridad y aislamiento:**
  - Claves con alcance por usuario, proyecto y rol (`reader`, `writer`, `admin`); autorización en REST y MCP/SSE.

## Herramientas MCP principales

| Herramienta | Propósito |
| :--- | :--- |
| `hae_get_project_context` | Resumen inicial del proyecto, vigencias de repositorios, foco de ticket activo, candidatos del catálogo, última sesión, daily y reglas activas. |
| `hae_search_components` / `hae_search_utilities` | Búsqueda de componentes UI o utilidades/hooks/servicios con niveles `location`/`contract`/`detail`, `exclude_types`, `max_tokens` e `if_none_match`. |
| `hae_get_symbol` | Recuperación directa de un símbolo conocido por `name` y `file_path` sin coste de ranking. |
| `hae_get_symbol_graph` | Grafo relacional de 1 a 5 saltos (`outgoing`, `incoming`, `both`) para explorar llamadas, renders, tipos usados y radio de impacto. |
| `hae_query_knowledge` | Búsqueda conceptual bilingüe combinada con `response_level="location"` por defecto y presupuesto de tokens. |
| `hae_get_youtrack_tasks` / `hae_get_youtrack_task_detail` | Listado de tickets activos y lectura íntegra de criterios de aceptación. |
| `hae_get_rules` / `hae_record_decision` | Consulta de reglas versionadas y registro de nuevas propuestas arquitectónicas (`status="proposed"`). |
| `hae_save_session_log` / `hae_get_session_history` | Registro y consulta de bitácoras de sesión con commit y evidencia. |

## Inicio local

Se verifica con Python 3.12+. Desde la raíz:

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
- [Medición de tokens en un proyecto fullstack](benchmarks/REPORT.md)
- [Optimización de búsquedas y benchmark antes/después](benchmarks/OPTIMIZATION_REPORT.md)
- [Acceso directo, tipos selectivos, validadores y presupuestos](benchmarks/ADVANCED_REPORT.md)
- [Niveles de respuesta MCP](docs/SEARCH_RESPONSES.md)
- [Despliegue](docs/ORACLE_DEPLOYMENT.md)
- [Conexión MCP y protocolo del agente](docs/CURSOR_AND_ANTIGRAVITY.md)
- [Integración Google Meet / Docs](docs/GOOGLE_MEET_INTEGRATION.md)

## Límites conocidos

El scanner AST indexa TS/TSX/JS/JSX y Python estáticamente, pero no sustituye al compilador de TypeScript ni a un analizador de tipos completo: no resuelve declaraciones de paquetes externos (`node_modules` / `site-packages`), llamadas dinámicas por reflexión ni inferencia profunda de tipos sin anotación. Verifica siempre el archivo fuente antes de editar.
