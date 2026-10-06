# Bitácora local registrada en HAE

Fecha: 2026-10-06. Proyecto: project-hae. Agente: Codex.

Se corrigió el cierre nativo del parser durante GC fijando tree-sitter 0.25.2, reteniendo árboles explícitamente y evitando cargar versiones distintas. Doctor expone el diagnóstico del parser. Se agregó una regresión aislada con 1.200 extracciones y GC forzado. Verificación final: 136 pruebas aprobadas y pip check sin conflictos.

Se añadió un benchmark reproducible de recuperación de interfaces y su informe en benchmarks/REPORT.md. Los conteos, alcance y limitaciones están en benchmarks/results/token_efficiency.json. No se midieron calidad ni tiempo de implementación.

Pendientes: corregir el fallo AST de layout.tsx en el repositorio consumidor antes de un snapshot frontend completo; validar futuras actualizaciones del parser; ejecutar una comparación controlada de tareas para productividad. No se modificó el despliegue remoto.

Decisión propuesta: mantener la dependencia nativa verificada y probar cambios con GC en subproceso; publicar métricas solo con baseline y alcance, separando recuperación de contexto de productividad.

El envío inicial fue bloqueado por la revisión automática porque no pudo verificar la confianza/propiedad del destino. El usuario autorizó explícitamente la conexión a su servidor HAE y el envío posterior terminó correctamente: propuesta #21 (pendiente de revisión antes de activarse) y sesión #9 en project-hae. Se verificaron inicialización MCP/SSE, catálogo de 17 herramientas y lectura de contexto. El servidor sigue indicando catálogo sin manifiesto verificable. No se guardó la credencial en esta bitácora ni en archivos del proyecto.
