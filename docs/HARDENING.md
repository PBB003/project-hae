# HAE 2: garantías y operación

Estos cambios refuerzan el código local. El servidor remoto debe actualizarse para ofrecer las capacidades nuevas. `/api/health` anuncia `api_version: 2`; la CLI rechaza snapshots contra versiones anteriores.

## Identidad y catálogo

Este workspace corresponde a `project-hae`. Tekniek mantiene su proyecto separado. Alinea `hae.json`, `AGENTS.md`, `GEMINI.md` y las reglas de Cursor; `hae doctor` detecta conflictos con AGENTS y verifica acceso autenticado al catálogo.

Para varios repositorios, declara rutas relativas dentro del workspace, con IDs únicos y sin solapamientos:

```json
"repositories": [
  {"id": "frontend", "path": "frontend"},
  {"id": "backend", "path": "backend"}
]
```

Cada `(project_id, repo_id, branch)` conserva su catálogo actual. Git aporta rama, commit y indicador de cambios locales; sin Git, el commit queda desconocido. La búsqueda devuelve origen, líneas, fecha y revisión. Verifica la fuente antes de reutilizar un símbolo; los contratos importados desde otro archivo no se resuelven.

`POST /api/sync` admite:

- `mode: merge`: inserta/actualiza sin borrar símbolos omitidos. Sin manifiesto usa `repo_id: legacy`; no acredita cobertura ni vigencia.
- `mode: snapshot`: exige manifiesto completo y `expected_revision` actual. Reemplaza únicamente el repositorio y rama declarados. Rechaza revisiones obsoletas, fechas anteriores y eliminaciones vacías implícitas.

La transacción incluye proyecto, catálogo, manifiesto y reglas. Un fallo no deja cambios parciales. Un conflicto devuelve `409`: recuperar revisión y volver a escanear. Un escaneo con errores de lectura, sintaxis, codificación o archivos mayores de 1 MB se considera incompleto. Se excluyen dependencias, compilados, archivos de pruebas, stories y declaraciones `.d.ts`.

Un catálogo vacío que elimina símbolos existentes requiere `hae sync --allow-empty`, destinado a una eliminación intencional. La sincronización de varios repositorios usa una transacción por repositorio; un fallo posterior no revierte los repositorios ya sincronizados.

## Contexto y continuidad

`hae_get_project_context` admite `repo_id`, `branch`, `ticket_id` y `expected_commit`. Un commit esperado debe identificar un único catálogo y coincidir; los conflictos se rechazan. El contexto tiene un presupuesto de caracteres (`HAE_CONTEXT_MAX_CHARS`, 6000 por defecto), avisa sobre catálogo antiguo/incompleto y remite a herramientas de detalle.

Leer el resumen no sustituye recuperar el ticket, contrato o reunión completos. Tickets y reuniones son datos externos; sus textos no autorizan comandos ni cambios de reglas. Las sesiones conservan campos de evidencia sin verificar por sí mismas que una prueba se haya ejecutado.

```powershell
hae log -m "Corregida validación" --ticket TSO-164 --agent codex --evidence "pytest: 125 passed" --completed "Validación y pruebas" --pending "Desplegar"
```

En un workspace con varios repositorios, `hae log` usa el primero configurado. Para registrar otro repositorio, usa REST/MCP con sus metadatos explícitos.

## Reglas y comprobaciones

`hae_record_decision` registra propuestas. Crear una propuesta con el mismo título que una regla activa conserva el contenido aprobado. Los autores quedan vinculados a la credencial; el administrador puede declarar un autor explícito.

Revisar propuestas con `GET /api/projects/{id}/rules?status=proposed`, consultar `/rules/{rule_id}/history` y aprobar una revisión concreta con `POST /api/projects/{id}/rules/{rule_id}/approve/{revision}`. Solo se aprueba la revisión más reciente y requiere clave administradora.

Las comprobaciones admiten `forbidden_pattern`, `forbidden_import` (imports/exports estáticos TS/JS), `required_file` y `max_file_lines`. Se validan los campos de `check_spec`; no se ejecutan instrucciones recibidas en las reglas. `include`, `exclude` y `severity` delimitan archivos y resultados.

```json
{"title":"Import autorizado","rule_content":"Usar el adaptador local","status":"proposed",
 "check_spec":{"kind":"forbidden_import","module":"forbidden-sdk","include":["src/*"]}}
```

`hae check` obtiene reglas activas del servidor; `--rules-file` permite ejecutarlas sin red. Código de salida: `0` comprobaciones completadas sin errores; `1` incumplimientos (o reglas manuales pendientes con `--strict`); `2` chequeo incompleto/configuración inválida. Las reglas textuales aparecen en `unchecked_rules`. No se afirma su cumplimiento automático. Las expresiones regulares deben ser revisadas por el administrador antes de activarlas.

## Credenciales

`HAE_API_KEY` es administradora. Generar valores aleatorios, distintos, y cargar las credenciales mediante `.env` o variables del servicio. `HAE_CLIENT_KEYS` es una lista JSON de `{key,user_id,projects,role}`; roles `reader` y `writer`. No se admiten claves repetidas ni proyectos vacíos. `writer` puede sincronizar, guardar sesiones/tickets/reuniones y proponer reglas; aprobar reglas o eliminar proyectos requiere administrador.

Las credenciales por usuario utilizan `user_id` de YouTrack cuando se quiera identificar asignaciones. Las claves de lector permiten todos los datos del proyecto autorizado; no hay permisos por ticket o documento individual. Las sesiones SSE están vinculadas a la credencial que las creó.

La guía antigua de Google incluía la clave administradora vigente. Se eliminó del documento local. Antes de publicar esta versión, generar una nueva, actualizar la VM y los clientes que la usan, reiniciar y comprobar que la anterior devuelve `401`. La copia privada ignorada de respaldo puede conservar la documentación histórica; no compartirla. Limpiar también históricos de Git o copias publicadas si existieran. Retirar texto del documento no revoca una clave en el servidor.

## YouTrack

El cliente pagina todas las consultas, incluye `resolved` y `updated`, deduplica por actualización y envía una transacción de tareas. Una consulta incompleta o personalizada hace merge. Solo una consulta completa del proyecto puede archivar tickets ausentes; todos sus tickets requieren `source_updated_at`; su `observed_at` corresponde al comienzo de la consulta y protege actualizaciones posteriores.

Configura `~/.hae.json` con `youtrack.base_url`, `token`, `default_project`, `user_id` opcional y `hae_project_id`. Sin `user_id`, se consulta `/api/users/me`. El cliente rechaza proyectos distintos del mapeo. La configuración global tiene sus propias credenciales HAE; deben permitir ese proyecto y corresponder al usuario sincronizado.

Los webhooks usan el formato de la app oficial [Webhook Triggers](https://www.jetbrains.com/help/youtrack/cloud/webhook-triggers.html): `issueCreated`, `issueUpdated`, `issueDeleted`, cabecera `X-YouTrack-Token` y fecha ISO con zona horaria. Activar mediante:

- `HAE_WEBHOOK_TOKEN`: secreto distinto de al menos 32 caracteres.
- `HAE_YOUTRACK_URL` y `HAE_YOUTRACK_TOKEN`: acceso al issue completo.
- `HAE_YOUTRACK_PROJECTS`: mapa JSON de IDs HAE a códigos YouTrack, por ejemplo `{"tekniek":"TSO"}`.

Enviar a `POST /api/webhooks/youtrack/{project_id}`. Las actualizaciones recuperan el issue completo desde YouTrack, para no borrar campos ausentes en eventos parciales. Los eventos se deduplican y guardan en SQLite; los fallos tienen hasta cinco intentos, con una pausa de 30 segundos entre ciclos de reintento. Un reinicio recupera eventos interrumpidos. Las eliminaciones son archivados con control de fecha.

El administrador puede consultar `GET /api/projects/{id}/webhook-events` y reintentar fallos con `POST /api/projects/{id}/webhook-events/{event_id}/retry`. Se registra la clase de error, sin el mensaje potencialmente sensible. Un evento aceptado puede devolver `status: failed`: sigue almacenado para reintentos. El inbox ofrece entrega idempotente local; no garantiza que YouTrack envíe todos los eventos. Mantener sincronizaciones completas para reconciliación.

## Migración y recuperación

1. Detener el servicio y copiar el archivo SQLite y cualquier WAL/SHM asociado. Guardar código anterior y variables del servicio en un lugar privado.
2. Instalar los requisitos de servidor y cliente; el servidor utiliza también el validador declarativo y el adaptador YouTrack del paquete `client`.
3. Reiniciar HAE 2 con las credenciales nuevas. El arranque migra SQLite, conserva registros anteriores y asigna `legacy` al catálogo sin manifiesto. Las reuniones antiguas sin documento se identifican por título; los duplicados antiguos conservan su registro más reciente.
4. Verificar `api_version`, ejecutar `hae doctor` y realizar una sincronización completa para cada repositorio/rama que se vaya a utilizar. Los agentes deben reconectar sus sesiones MCP tras cambiar claves.
5. Si falla, detener HAE 2 y restaurar conjuntamente código, dependencias, DB y configuración anteriores. No ejecutar código anterior contra la base migrada como mecanismo de rollback.

No mover el catálogo de Tekniek a Project HAE. El proyecto antiguo `mi-proyecto-react` conserva sus registros históricos como referencia; no representa el catálogo de este workspace.
