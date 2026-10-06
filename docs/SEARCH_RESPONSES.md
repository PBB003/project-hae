# Búsqueda MCP con niveles de respuesta

`hae_search_components` y `hae_search_utilities` aceptan `response_level` con tres valores. La respuesta sigue siendo texto MCP, ahora con JSON compacto en lugar de una lista de viñetas. Los clientes que interpreten el formato anterior deben adaptarse; los nombres, filtros y permisos de las herramientas se conservan. Los endpoints REST de catálogo mantienen su formato anterior.

| Nivel | Uso | Información |
|---|---|---|
| `location` | Descubrir candidatos | Nombre, archivo, línea, repositorio, rama y manifiesto de vigencia. Sin contratos ni descripciones. |
| `contract` (predeterminado) | Recuperar una interfaz conocida | Para un nombre exacto único en el alcance, devuelve ubicación y contrato íntegro en la misma llamada. Si no es único, devuelve ubicaciones. |
| `detail` | Inspeccionar todos los metadatos | Registros completos, contratos, resúmenes, descripción y puntuación de búsqueda, hasta el límite solicitado. |

Ejemplo de argumentos MCP:

```json
{
  "project_id": "tekniek",
  "query": "Checkbox",
  "repo_id": "frontend",
  "branch": "main",
  "response_level": "contract"
}
```

La respuesta contiene `level`, `match` y `results`. `match` puede ser `exact`, `ambiguous`, `candidates` o `none`. `level` indica el nivel realmente entregado. La unicidad se comprueba antes de aplicar `limit`: usar `limit=1` no convierte una búsqueda ambigua en una coincidencia clara. Para desambiguar, precisar nombre, repositorio o rama. Las búsquedas semánticas conservan candidatos sin elegir automáticamente un contrato.

En nivel contrato, `contract_source=ast` indica el contrato AST almacenado sin truncar. `summary` indica que solo había una firma o resumen legado, y `missing` que no había ninguno; en esos casos leer la fuente para completar la interfaz. No se duplican el contrato, su firma y su resumen en la misma respuesta compacta.

Los metadatos de vigencia se conservan incluso cuando son desconocidos: `complete`, `revision`, `dirty`, `commit_sha` y `scanned_at`, más `stale`. El catálogo sigue siendo una instantánea. Verificar fuente y manifiesto antes de implementar. Una respuesta vacía no demuestra que el código no exista.

`hae_get_symbol_detail` continúa disponible para clientes existentes y para recuperar detalle exacto. Tras una búsqueda que ya entrega contrato, no hace falta pedirlo otra vez para obtener la misma interfaz. Leer la implementación sigue siendo necesario si la tarea trata de comportamiento o errores.

## Símbolos conocidos y tipos selectivos

`hae_get_symbol(project_id, name, file_path, repo_id, branch)` recupera un símbolo conocido mediante filtros SQL exactos, sin ranking ni búsqueda previa. Admite los mismos niveles, presupuesto y validador de respuesta. Precisar repositorio/rama si hay coincidencias en varios alcances. El endpoint y herramienta anteriores de detalle siguen disponibles. Para funciones pequeñas cuya ubicación ya se conoce, leer el fragmento de fuente puede ser más económico.

En nivel contrato, `types` incluye solo las declaraciones locales alcanzadas desde las referencias del símbolo, con su nombre, archivo, contrato, línea y condición de exportación cuando se conoce. Se sigue el cierre transitivo, incluidas referencias locales no presentes en el contrato principal, sin devolver todo el archivo DTO. Los tipos privados necesarios pueden recuperarse individualmente; `exported=false` indica que no se pueden importar directamente como símbolos públicos.

Se admiten imports relativos, aliases de paths/baseUrl de tsconfig/jsconfig JSONC, extends local dentro del repositorio, archivos índice, reexportaciones, aliases de nombre, namespaces y default. Se resuelven interfaces, aliases, clases públicas y typeof de constantes literales seguras. Hay límites de profundidad/cantidad y detección de ciclos. No se ejecuta TypeScript ni se leen paquetes externos, rutas excluidas o archivos fuera del repositorio; no se infieren tipos y no se implementa toda la semántica del compilador. Enums y construcciones complejas pueden quedar pendientes.

`type_resolution` indica `not_scanned`, `partial` o `complete` dentro de este alcance; `unresolved_types` enumera pendientes, incluidos tipos externos React/DOM. No confundir resolución del grafo almacenado con integridad de una respuesta limitada. `include_types=False` omite las declaraciones en nivel contrato y muestra `types_available`/`types_included=false`; usarlo si los tipos ya se conocen. El nivel detalle conserva registros completos.

## Reutilizar contenido por validador

Las nuevas respuestas incluyen `etag`. Tras guardar ese contenido íntegro, enviar el mismo alcance/nivel/opciones con `if_none_match=etag`: si sigue igual, responde `not_modified=true` y el validador. El servidor vuelve a autorizar y leer el catálogo; un hash incluye identidad, alcance, parámetros, contenido y manifiesto. Cambios en revisión, contrato, tipos y caducidad invalidan el contenido correspondiente. No existe una caché global de datos de usuarios.

`hae_get_rules` y `hae_get_project_context` mantienen su formato anterior sin opciones. Para empezar a reutilizarlos, enviar `if_none_match=""`: devuelve un objeto JSON con reglas/contexto y etag; enviar ese etag en consultas posteriores. También admiten presupuesto opcional. Un contenido limitado continúa siendo limitado aunque su validador siga vigente.

Enviar un validador solo si el contenido correspondiente permanece disponible en la conversación o caché local. Si se pierde por compactación o cambio de chat, recuperarlo sin validador. Esto requiere que el agente/cliente use el protocolo; no se configura automáticamente la caché de un proveedor de modelos. Cambios de código local sin sincronizar no pueden detectarse en HAE remoto.

## Presupuesto de respuesta

`max_tokens` permite de 512 a 65.536, con 4.096 por defecto para búsqueda/símbolo. Usa el número de bytes UTF-8 del JSON como cota superior conservadora para tokenizadores BPE de bytes. No es un contador exacto de tokens, ni incluye argumentos/esquemas del transporte; no requiere descargar vocabularios. Puede omitir bloques aunque un tokenizador concreto admitiera más texto.

Cuando no caben, se omiten declaraciones de tipos completas, después contratos o resultados completos; no se cortan firmas ni tipos a mitad de texto. La respuesta contiene `partial=true`, `budget` con cantidades omitidas y una indicación para ampliar presupuesto o recuperar un símbolo concreto. Los manifiestos e identidades de los resultados conservados se mantienen. Para reglas/contexto, se omiten bloques completos con el mismo aviso. La opción nunca acredita una interfaz completa si faltan bloques.

La mejora inicial se verifica con [el benchmark comparativo](../benchmarks/OPTIMIZATION_REPORT.md); la continuación con [lectura inicial y revalidación](../benchmarks/ADVANCED_REPORT.md). Estas herramientas se modificaron y probaron localmente; registrar una decisión en el servidor remoto no despliega el código. Es necesario actualizar servidor y sincronizar con el scanner nuevo para disponer de tipos importados.
