# Notas de Google Meet / Gemini

El cliente de HAE recupera notas de Google Docs, conserva texto íntegro y las identifica por `source_id` de Drive. Actualizar o renombrar un documento actualiza la misma reunión; documentos diferentes pueden tener el mismo título. Las consultas de reuniones ordenan por fecha de la reunión.

## Preparar el cliente

```powershell
python -m pip install -r client/requirements-google.txt
```

Habilita Drive y Docs en el proyecto Google correspondiente y prepara un cliente OAuth de aplicación de escritorio. Guarda sus credenciales privadas en `~/.hae_google_credentials.json`. El primer uso solicita acceso de lectura a Drive y Docs en el navegador; el token se guarda en `~/.hae_google_token.json`. No subir estos archivos a un repositorio.

La configuración global `~/.hae.json` contiene `server_url` y una `api_key` con permiso de escritura para el proyecto. Ejecuta desde un workspace con identidad correcta:

```powershell
hae google
```

O indica explícitamente el proyecto al ejecutar el módulo:

```powershell
python -m client.gdrive_sync --project tekniek
```

El cliente pagina documentos cuyo título contiene `Daily` y filtra el ID del proyecto en el título. Se recomienda `Daily - tekniek: YYYY-MM-DD - Notas de Gemini`. Los encabezados `Resumen`, `Resumen ejecutivo`, `Próximos pasos`, `Elementos de acción` y sus variantes inglesas separan resumen y acciones. La estructura inesperada requiere revisar el original; la extracción no interpreta obligaciones ni responsables.

No se truncan notas, resumen ni acciones al guardar. El contexto inicial presenta una vista compacta; `hae_get_meeting_detail(project_id, note_id)` o `GET /api/projects/{id}/meetings/{note_id}` recupera el registro completo.

## Importación manual o automatización externa

Enviar `POST /api/projects/{id}/meetings` con `title`, `summary`, `meeting_date`, `action_items`, `raw_notes`, `source_id` y `source_url`. Si se utiliza Apps Script, guardar URL, proyecto y credencial en Script Properties y añadir `X-HAE-Key` desde esas propiedades; no incrustar claves en el script.

La guía anterior incluía una clave administradora real. Se retiró del documento local; debe rotarse en el servidor y actualizarse en los clientes antes de publicar. El cliente actual es manual: no se creó un trigger de Google ni una tarea recurrente durante este refuerzo.
