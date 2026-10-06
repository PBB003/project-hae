# Pruebas de Project HAE

Desde la raíz, con Python 3.12. `requirements-dev.lock` fija las versiones usadas en la verificación; `requirements-dev.txt` permite resolver versiones dentro de los rangos al preparar una actualización.

```powershell
python -m venv .venv-test
.\.venv-test\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv-test\Scripts\python.exe -m pytest -q
.\.venv-test\Scripts\python.exe -m client.hae_sync check --rules-file docs/rules.example.json --strict
```

En Linux/macOS utiliza `.venv-test/bin/python`. Los casos AST requieren tree-sitter para verificar el scanner completo; el fallback regex no autoriza snapshots desde la CLI.

## Cobertura

- Autenticación REST/SSE, claves por proyecto/rol y rechazo de mensajes MCP malformados.
- Transporte SSE real en localhost: identidad del actor, propuestas y rechazo de una sesión usada con otra credencial.
- Aislamiento por repositorio/rama, conflictos de revisión/commit/fecha, snapshots vacíos, merge legado y transacciones sin escrituras parciales.
- Migración de SQLite antigua y repetición del arranque, conservación de IDs y claves foráneas.
- Props complejas, genéricos, literales, métodos públicos y contratos de tipos locales.
- Estabilidad nativa del parser en un subproceso: 1.200 extracciones con recolección de memoria forzada.
- Ranking bilingüe y búsqueda literal, origen y metadatos del catálogo.
- Niveles MCP location/contract/detail, contrato íntegro sin segunda llamada, ambigüedad independiente de limit y filtros por rama; esquema y validación por SSE real.
- Resolución transitiva selectiva: imports relativos, aliases JSONC/extends local, barriles, namespaces, default, ciclos, tipos privados, herencia pública y literales para typeof; pendientes externos y rutas fuera de alcance.
- Acceso directo sin ranking; validadores por identidad/alcance y cambios de merge/snapshot/reglas/caducidad; presupuestos Unicode sin cortar bloques y opciones de tipos conocidos. Revalidación por SSE real.
- Presupuesto y alcance del contexto; sesiones con evidencia y campos de continuidad.
- Propuestas de reglas, revisión/aprobación e historial; comprobaciones declarativas seguras con líneas y reglas manuales explícitas.
- YouTrack paginado, tickets resueltos, fallos parciales, datos inválidos, asignaciones por usuario y reconciliación que conserva eventos posteriores.
- Webhooks autenticados/idempotentes, recuperación autoritativa, fallos persistentes y reintentos; eventos de borrado fuera de orden.
- Identidad, orden y notas completas de reuniones; próximos pasos y texto de tablas.
- CLI: identidad errónea, fallo externo, versión antigua, configuración y códigos de salida.

`conftest.py` crea una clave aleatoria y una SQLite en `tmp_path` por caso. Las pruebas externas sustituyen la red por respuestas simuladas; `test_mcp_transport.py` abre exclusivamente un servidor temporal en `127.0.0.1` y lo detiene al finalizar. No usan la base ni credenciales de producción.

Para un grupo:

```powershell
.\.venv-test\Scripts\python.exe -m pytest tests/test_reconciliation.py tests/test_mcp_transport.py -q
```

## Límites de la verificación

La suite no verifica Caddy, systemd, Oracle, OAuth real de Google, entrega real de eventos YouTrack ni despliegue Docker. `tests/mcp_smoke.py` permite una consulta de lectura a un servidor explícito; carga la clave de `HAE_API_KEY` y no necesita incluirla en argumentos del proceso. [El benchmark de recuperación de interfaces](../benchmarks/REPORT.md) mide tokens de referencia, sin medir productividad ni calidad al implementar tareas.
