# HAE: acceso directo, tipos selectivos, reutilización y presupuestos

Fecha: 6 de octubre de 2026. Implementación local de las mejoras 3 a 6. [JSON reproducible](results/token_efficiency_advanced.json), [benchmark](token_efficiency.py), [protocolo MCP](../docs/SEARCH_RESPONSES.md).

## Cambios

- Acceso directo `hae_get_symbol`: filtros SQL por nombre/archivo/repositorio/rama, sin ranking. La herramienta de detalle anterior también usa consulta exacta; ya no depende de los primeros 50 candidatos de una búsqueda.
- Resolución selectiva de declaraciones de tipos locales, incluidas dependencias transitivas, aliases, reexportaciones, herencia pública y constantes literales para typeof. Declara pendientes externos, limita recorrido y conserva condición de exportación de tipos privados. El scanner sincroniza las declaraciones necesarias, sin mandar todo el archivo DTO. `include_types=False` permite omitir tipos ya conocidos.
- Validadores `etag`/`if_none_match` por identidad, alcance, contenido y opciones. Revalidar contratos, reglas y contexto desde el estado actual; no repetir cuerpos iguales. Sin caché global ni autorización omitida. El cliente debe conservar el contenido asociado.
- Presupuestos `max_tokens` conservadores de 512 a 65.536 mediante bytes UTF-8 del JSON. Omisiones de bloques completos señaladas con partial/budget; no corta firmas ni tipos. No equivale a facturación ni a conteo exacto del modelo.

## Resultado medido

Siete consultas de interfaces, mismo código principal y mismos archivos auxiliares originales que la fase anterior. Se comprobó la coincidencia de sus hashes. La versión nueva incorpora archivos y tipos adicionales necesarios para cerrar contratos de retorno y referencias transitivas. **La cobertura aumenta; el consumo inicial también.**

| Tokenizador | Primera recuperación y verificación | Revalidación de contenido conservado | Reducción en esta repetición | Leer archivos completos con grafo de tipos | Ahorro inicial frente a ese alcance |
|---|---:|---:|---:|---:|---:|
| o200k_base | 7649 | 863 | 88.72 % | 21750 | 64.83 % |
| cl100k_base | 7634 | 902 | 88.18 % | 21262 | 64.1 % |

La primera recuperación contabiliza argumentos/respuestas, contratos, lectura selectiva de la fuente y un contexto inicial. La revalidación contabiliza siete consultas con sus etags más contexto sin cambios. **No vuelve a leer/verificar contratos:** presupone que el cliente conserva los datos y la evidencia anterior y que la revisión y fuente siguen vigentes. No representa ahorro en una nueva tarea de programación ni se debe sumar como si repitiera toda la verificación inicial.

| Consulta (o200k_base) | Fase 1-2 anterior | Primera lectura actual | Revalidación actual | Declaraciones de tipos incluidas | Presupuesto predeterminado parcial |
|---|---:|---:|---:|---:|---|
| Checkbox | 302 | 363 | 119 | 0 | no |
| Crear proyecto: frontend | 1423 | 1911 | 103 | 14 | sí |
| Crear proyecto: backend | 530 | 2012 | 115 | 12 | sí |
| useModal | 147 | 193 | 107 | 0 | no |
| DateInput | 398 | 448 | 114 | 0 | no |
| ProjectFormModal | 785 | 938 | 111 | 3 | no |
| ProjectsListTable | 1432 | 1501 | 109 | 5 | no |

Las filas excluyen contexto inicial y esquemas. Con el contexto inicial, la fase anterior usó 5256 tokens y la actual 7649: **45.53 % más**. Parte del incremento corresponde a validadores/vigencia; la mayor parte a contratos más amplios y sus declaraciones, particularmente el retorno ProjectModel del backend. No presentar esta fase como una reducción universal de consumo.

La referencia de archivos completos ahora incluye los archivos del cierre de tipos: 21750 tokens, frente a 16161 en el alcance anterior. Una lectura dirigida que conoce de antemano las declaraciones necesarias usa 2576 tokens y sigue siendo más económica que HAE en la primera consulta. Los costes nuevos no acreditan mejora de calidad o velocidad al programar.

## Escenarios adicionales

- Consulta directa por nombre/archivo: 7426 tokens para las siete interfaces con la misma verificación, sin contexto inicial. La búsqueda actual usa 7366. La consulta directa evita ranking, pero sus argumentos de archivo **no garantizan menos tokens**. Para funciones pequeñas conocidas, leer la fuente directamente sigue siendo una opción más barata.
- Si los tipos ya se conocen y se envía include_types=False, con la misma verificación de los archivos auxiliares originales el total es 5728 tokens. Esa opción omite contenido explícitamente; no acredita que se haya entregado el cierre de tipos.
- Cargando todos los esquemas actuales, la primera recuperación suma 11442 tokens. Los esquemas tienen 3793 tokens. Exposición y caché dependen del cliente y se reportan separadamente; el protocolo no configura caché del proveedor de modelos.
- El presupuesto predeterminado de 4.096 usa una cota en bytes y señala omisiones en los dos casos createProject. La medición completa usa max_tokens=16.384 y exige que no haya omisiones. No atribuye ahorro a respuestas incompletas. Para recuperar bloques faltantes, aumentar el presupuesto o pedir declaraciones por nombre y archivo.

## Método y límites

El modo advanced verifica que cada contrato y declaración devuelta corresponda al catálogo recién construido y a otra lectura local del archivo fuente. Conserva los hashes de archivos principales, auxiliares originales y nuevos archivos de dependencias. Los JSON no contienen código ni credenciales. La base es temporal; no se modifica ni publica la fuente del consumidor.

La verificación dirigida actual usa el contrato principal y las declaraciones importadas necesarias, en lugar de archivos DTO enteros. Los baselines legacy y optimized conservan sus formateadores anteriores para comparar el mismo alcance; sus esquemas son los actuales si se repiten con este código. Los artefactos anteriores permanecen como resultados históricos.

El benchmark cuenta texto de argumentos y respuestas de funciones MCP locales, no facturación, prompts comunes, envolturas, razonamiento o generación. Las siete consultas tienen nombres conocidos, límite uno y repositorio conocido; el contexto temporal no tiene tickets, reglas o reuniones. Los etags incluyen datos que cambian al escanear, por lo que su tokenización puede variar ligeramente entre repeticiones aunque el código sea idéntico. Son resultados de esta ejecución, no constantes del producto.

La resolución no sustituye al compilador ni infiere tipos. Dependencias de paquetes externos React/DOM, enums y casos complejos pueden quedar pendientes. Checkbox señala React.FC externo; el benchmark anterior tampoco aportaba su biblioteca. La verificación de implementación o comportamiento requiere leer código adicional. El scanner terminó 602 archivos frontend y 722 backend sin cierre nativo; layout.tsx mantiene un fallo AST y el frontend permanece incompleto.

El cliente solo debe enviar if_none_match mientras conserve el contenido anterior en contexto/caché. Si lo pierde tras compactar o cambiar de chat, recuperarlo sin validador. Una revalidación de catálogo no detecta cambios de fuente sin sincronizar. Los metadatos de vigencia, incluido stale, deben revisarse junto con la fuente.

## Reproducir

```powershell
.\.venv-test\Scripts\python.exe -m benchmarks.token_efficiency --workspace 'C:\Users\pedro\Desktop\Tekniek' --mode advanced
```

Instalar previamente benchmarks/requirements.txt si falta tiktoken. Se conservan los modos legacy y optimized para repetir los formatos anteriores; usar --output con un nombre nuevo para no reemplazar artefactos históricos.

Verificación final: **168 pruebas aprobadas**. Cubren consultas directas sin ranking, permisos antes de revalidar, identidades/alcances, cambios por merge/snapshot/reglas/caducidad, Unicode y presupuestos sin cortar contratos, propagación de tipos y recuperación de privados, imports/aliases/barriles/ciclos/herencia/literales, forwardRef y typeof, más llamadas directas y revalidación por MCP/SSE real en localhost. Las migraciones de catálogo son aditivas y se siguen verificando con la suite.

Estos cambios no están desplegados en el servidor remoto. Al desplegar, reiniciar servidor y sincronizar con el scanner nuevo para disponer de los tipos. El registro de decisión/sesión en HAE conserva continuidad y no despliega código.
