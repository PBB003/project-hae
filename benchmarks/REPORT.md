# Medición de tokens de HAE con Tekniek

Fecha: 6 de octubre de 2026. Resultado reproducible: [JSON de conteos y hashes](results/token_efficiency.json). Script: [token_efficiency.py](token_efficiency.py).

Este informe conserva la medición original de dos llamadas por símbolo. Después se implementaron búsqueda con contrato y tres niveles de respuesta: [comparación actual antes/después](OPTIMIZATION_REPORT.md).

## Resultado

En siete consultas preseleccionadas, HAE más verificación de la declaración y un contexto inicial utilizó **6,933 tokens**, frente a **16,161** leyendo los archivos completos: **57.1 % menos** con o200k_base. Con cl100k_base, la reducción fue **55.98 %**.

Sin embargo, un lector que ya conoce exactamente el archivo y símbolo utilizó **3,599 tokens** leyendo declaraciones y los mismos archivos auxiliares. HAE añadió **92.64 %** a ese escenario. No hay un porcentaje universal de ahorro.

| Escenario, siete consultas | o200k_base | cl100k_base |
|---|---:|---:|
| Archivos completos y tipos auxiliares | 16161 | 15774 |
| Declaraciones conocidas y tipos auxiliares | 3599 | 3548 |
| HAE y verificación, incluido contexto inicial | 6933 | 6943 |
| Anterior más todos los esquemas HAE | 10036 | 10042 |

Si se cargaran todos los esquemas HAE sin caché en esta sesión, el ahorro frente a archivos completos sería **37.90 %** con o200k_base y **36.34 %** con cl100k_base. Su exposición real depende del agente y del transporte; se presenta como coste separado.

## Consultas

Estas filas excluyen el contexto inicial y los esquemas. Un ahorro negativo representa más tokens.

| Consulta | Archivo completo | Declaración dirigida | HAE y verificación | Ahorro frente a completo |
|---|---:|---:|---:|---:|
| Checkbox | 462 | 88 | 541 | -17.1 % |
| Crear proyecto: frontend | 4706 | 1281 | 1630 | 65.36 % |
| Crear proyecto: backend | 2074 | 386 | 727 | 64.95 % |
| useModal | 118 | 19 | 332 | -181.36 % |
| DateInput | 1255 | 130 | 681 | 45.74 % |
| ProjectFormModal | 4622 | 569 | 1023 | 77.87 % |
| ProjectsListTable | 2924 | 1126 | 1760 | 39.81 % |

## Método y límites

Se escanearon 602 archivos frontend y 722 backend del workspace local Tekniek, sin modificar sus fuentes ni enviarlas a un servicio externo. Se poblaron una SQLite temporal y el servidor HAE local mediante TestClient. Se contaron cadenas de argumentos y respuestas reales de las funciones MCP, no una ejecución de un agente programando. El tokenizador es [tiktoken](https://github.com/openai/tiktoken), versión 0.14.0, con o200k_base y cl100k_base; no se presupone un modelo concreto ni equivalencia exacta con facturación.

Los siete nombres se eligieron antes de la medición. Las búsquedas usan límite uno, repositorio y nombre conocidos, y verifican que el detalle corresponda al archivo esperado. Esta selección favorece la recuperación conocida y no mide precisión de consultas ambiguas. Se contabilizan búsqueda y detalle por cada símbolo; el cliente podría ahorrar omitiendo llamadas redundantes.

Para comparar referencias de interfaces, la lectura dirigida recorta la implementación y conserva declaraciones locales referenciadas. Los principales tipos importados necesarios se aportan como archivos auxiliares idénticos en los tres escenarios: OperationsDTO, el puerto backend y los tipos de proyectos/finanzas. HAE aún no los resuelve entre archivos. No es un cierre exhaustivo de dependencias, ni una medida de información suficiente para modificar toda la implementación. Consultar comportamiento o bugs exige leer también el código pertinente y añade tokens.

El contexto inicial se obtiene una vez: 239 tokens o200k_base. El proyecto temporal no tiene tickets, reuniones, reglas ni sesiones previas; un contexto de producción puede ser mayor. Los archivos auxiliares repetidos se cuentan por consulta; no se simula reutilización de contexto ni caché. No se cuentan prompts compartidos, envolturas del protocolo, razonamiento, generación de código, costes monetarios ni tiempo de implementación.

El frontend dejó un error AST en src/app/layout.tsx y se sincronizó como merge incompleto. El backend no presentó errores. Los casos elegidos sí fueron recuperados y verificados. Esto no acredita cobertura completa del frontend ni el índice del servidor remoto.

## Qué demuestra para programar

HAE puede reducir lecturas grandes para descubrir interfaces reutilizables y ofrece memoria, reglas y trazabilidad. Para funciones pequeñas ya localizadas, las dos llamadas y sus metadatos pueden costar más que leer la función. Conviene usar el índice para descubrimiento y consultar la fuente necesaria para verificar el cambio.

No se midió mejora de calidad, reducción de bugs ni velocidad de escritura. Para cuantificarlas, ejecutar tareas equivalentes con y sin HAE, mismo modelo y presupuesto, sesiones nuevas, orden aleatorio y varias repeticiones. Registrar tokens totales, tiempo hasta pruebas aprobadas, fallos de aceptación, regresiones, duplicación y violaciones de reglas, con revisión sin conocer la condición. Publicar mediana, dispersión y número de tareas, incluyendo el coste de preparación y mantenimiento del índice.

## Reproducir

Desde la raíz de Project HAE, con el entorno de pruebas instalado:

```powershell
.\.venv-test\Scripts\python.exe -m pip install -r benchmarks/requirements.txt
.\.venv-test\Scripts\python.exe -m benchmarks.token_efficiency --workspace 'C:\Users\pedro\Desktop\Tekniek' --mode legacy --output benchmarks/results/token_efficiency_legacy_rerun.json
```

El tokenizador puede descargar vocabularios en la primera ejecución. Su caché vive en .hae-cache/tiktoken y está excluida del repositorio y Docker. El JSON conserva hashes de las fuentes y conteos, sin código crudo ni credenciales.

## Corrección del cierre de Python

El escaneo con tree-sitter 0.26.0 reprodujo una violación de acceso durante la recolección de memoria en Windows. Con 0.25.2 terminó el mismo escaneo de 1.324 archivos. Se fijó 0.25.2 en las dependencias, se evita cargar versiones nativas distintas y se añadió una regresión en subproceso de 1.200 extracciones con GC forzado. Verificación local: **136 pruebas aprobadas** y pip check sin incompatibilidades. El error sintáctico de layout.tsx es independiente y sigue pendiente.
