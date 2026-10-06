# Optimización de búsqueda HAE: benchmark antes y después

Fecha: 6 de octubre de 2026. Implementación y verificación locales.

Este informe conserva los resultados de las mejoras 1 y 2. [La continuación de las mejoras 3 a 6](ADVANCED_REPORT.md) añade tipos, acceso directo, validadores y presupuestos, con un alcance de recuperación más amplio.

Se implementaron búsqueda y contrato en una llamada, más niveles location/contract/detail. Para un nombre exacto único, el nivel contrato entrega el contrato almacenado completo, sin descripciones ni resúmenes duplicados. Si hay varios candidatos, devuelve ubicaciones y señala la ambigüedad, aunque limit sea uno. Los metadatos de vigencia permanecen en todos los niveles.

## Resultado medido

Las siete consultas pasaron de **14 a 7 llamadas de búsqueda/detalle**. El contexto inicial permanece como una llamada adicional en ambos casos. Las consultas conservan los mismos contratos, verificación dirigida de fuente y archivos auxiliares de tipos importados.

| Tokenizador | Tokens antes | Tokens después | Reducción frente a HAE anterior | Ahorro anterior frente a archivos completos | Ahorro actual frente a archivos completos |
|---|---:|---:|---:|---:|---:|
| o200k_base | 6933 | 5256 | 24.19 % | 57.1 % | 67.48 % |
| cl100k_base | 6943 | 5226 | 24.73 % | 55.98 % | 66.87 % |

Los totales incluyen búsqueda, contrato, contratos importados, verificación de fuente y contexto inicial. Excluyen esquemas, razonamiento, generación, prompts compartidos y envolturas de transporte. HAE todavía añade aproximadamente **46-47 %** frente a un lector que ya conoce la declaración precisa; antes añadía 93-96 %. No se midieron calidad ni velocidad al escribir código.

| Consulta (o200k_base) | Antes | Después | Reducción frente a HAE anterior | Ahorro actual frente al archivo completo |
|---|---:|---:|---:|---:|
| Checkbox | 541 | 302 | 44.2 % | 34.63 % |
| Crear proyecto: frontend | 1630 | 1423 | 12.7 % | 69.76 % |
| Crear proyecto: backend | 727 | 530 | 27.1 % | 74.45 % |
| useModal | 332 | 147 | 55.7 % | -24.58 % |
| DateInput | 681 | 398 | 41.6 % | 68.29 % |
| ProjectFormModal | 1023 | 785 | 23.3 % | 83.02 % |
| ProjectsListTable | 1760 | 1432 | 18.6 % | 51.03 % |

Las filas excluyen el contexto inicial y los esquemas. Checkbox pasó de costar 17 % más que el archivo completo a ahorrar 34,6 %. useModal todavía cuesta 24,6 % más que leer su archivo completo: 147 frente a 118 tokens. Son conteos de referencia, sin representar una sesión de programación completa.

## Comparación y trazabilidad

[Medición original](results/token_efficiency.json), [baseline repetido](results/token_efficiency_legacy_rerun.json) y [resultado optimizado](results/token_efficiency_optimized.json). Los hashes de todos los archivos principales y auxiliares coinciden en las tres ejecuciones. Los conteos por caso del baseline repetido coinciden exactamente con los originales para ambos tokenizadores.

El modo legacy conserva en el benchmark el formateador anterior y ejecuta búsqueda de catálogo más detalle, para repetir la comparación sobre las mismas fuentes. El modo optimized llama a la nueva búsqueda con su nivel predeterminado y exige un contrato exacto único, correspondiente al archivo esperado e idéntico al almacenado durante el escaneo. No se cuenta una segunda llamada de detalle en ese modo.

Los esquemas de herramientas actuales tienen 3.180 tokens o200k_base, frente a 3.103 originales. Si se cargan todos los esquemas sin caché, el total pasa de **10.036 a 8.436 tokens**, una reducción de **15,94 %** respecto al HAE original. El baseline repetido contabiliza los esquemas actuales y por eso sus totales con esquemas no son idénticos a los originales. Exposición y caché dependen del agente; se reportan aparte.

Se conserva el método y sus límites del [informe original](REPORT.md): selección de siete nombres conocidos, contexto inicial sin tickets/reuniones/reglas, ausencia de caché y navegación, tipos importados aportados como archivos completos idénticos, y lectura de comportamiento fuera del alcance. No se atribuye ahorro a omitir verificación de fuente. No se generaliza este porcentaje a búsquedas ambiguas o proyectos distintos.

Escaneo: 602 archivos frontend y 722 backend sin cierre nativo; sigue un fallo AST en src/app/layout.tsx, por lo que el frontend mantiene merge incompleto. La mejora no resuelve tipos importados ni corrige esa cobertura.

## Reproducir

```powershell
.\.venv-test\Scripts\python.exe -m benchmarks.token_efficiency --workspace 'C:\Users\pedro\Desktop\Tekniek' --mode legacy --output benchmarks/results/token_efficiency_legacy_rerun.json
.\.venv-test\Scripts\python.exe -m benchmarks.token_efficiency --workspace 'C:\Users\pedro\Desktop\Tekniek' --mode optimized
```

Instalar antes benchmarks/requirements.txt en el entorno de pruebas si falta tiktoken. El benchmark usa una base temporal y no modifica ni publica código del consumidor. El JSON guarda hashes y conteos, sin credenciales ni fuente cruda.

## Verificación de implementación

**144 pruebas aprobadas**. Los casos nuevos verifican contratos largos sin truncar, las tres proyecciones, nombres duplicados entre ramas con limit=1, filtros, consultas semánticas, índice vacío, firma legada con procedencia explícita, validación del nivel y llamadas por MCP/SSE real en localhost. La API REST conserva su formato.

La respuesta de búsqueda MCP ahora contiene JSON compacto dentro del bloque de texto. Los clientes que interpreten las viñetas anteriores deben adaptarse: [contrato de respuesta y ejemplos](../docs/SEARCH_RESPONSES.md). No se desplegaron estos cambios en el servidor remoto.
