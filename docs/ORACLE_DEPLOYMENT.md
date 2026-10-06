# Desplegar HAE 2

El código local y sus pruebas no modifican automáticamente la VM. Antes de actualizar, seguir la [migración y recuperación](HARDENING.md#Migración-y-recuperación) y respaldar la SQLite con el servicio detenido.

## Docker

Copiar `.env.example` a `.env`, generar `HAE_API_KEY` y configurar las claves de usuarios y las integraciones opcionales. No guardar claves en `docker-compose.yml`. La imagen incluye `server` y `client`, porque el servidor necesita el validador de reglas y el adaptador YouTrack.

```bash
docker compose up -d --build
curl http://127.0.0.1:8000/api/health
```

Comprobar `api_version: 2`. Compose publica en `127.0.0.1:8000`; usar un proxy HTTPS para acceso remoto. El proxy debe transmitir SSE sin buffering y conservar las cabeceras de autenticación. Configurar firewall y reglas OCI para el puerto HTTPS del proxy; ajustar a la infraestructura existente.

SQLite persiste en `./data`. Mantener una sola instancia de servicio para esta implementación. El worker de webhooks no está diseñado como cola distribuida entre varias réplicas.

## Servicio Python

Usar Python 3.12 y un entorno dedicado:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r server/requirements.txt -r client/requirements.txt
.venv/bin/python -m server.main
```

Para systemd, adaptar `server/hae.service` al usuario, directorio y entorno de la VM. Las credenciales deben residir en un archivo privado de entorno o configuración del servicio. Instalar el servicio solo después de validar las rutas reales.

## Comprobaciones de la actualización

1. Confirmar `/api/health` con versión 2 y acceso REST con clave válida.
2. Confirmar `401` con la clave anterior revocada y sin clave.
3. Ejecutar `hae doctor`, sincronizar cada repositorio/rama y revisar contexto y commit.
4. Conectar MCP/SSE, comprobar una búsqueda y registrar una sesión de prueba en el proyecto destinado a pruebas.
5. Si se activan webhooks, enviar un evento de prueba y revisar el inbox; verificar la recuperación de un fallo temporal.

La documentación anterior expuso la clave administradora en la guía Google. Rotarla en la VM y actualizar sus consumidores forma parte de esta actualización. La imagen Docker, el proxy y el servicio remoto requieren verificación en el entorno de despliegue; la suite local no los acredita.
