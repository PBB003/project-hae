FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias del sistema mínimas
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar requerimientos e instalar
COPY server/requirements.txt /app/server/requirements.txt
COPY client/requirements.txt /app/client/requirements.txt
COPY requirements-dev.lock /app/requirements-dev.lock
RUN pip install --no-cache-dir -c /app/requirements-dev.lock -r /app/server/requirements.txt -r /app/client/requirements.txt

# Copiar código del servidor
COPY server /app/server
COPY client /app/client

# Directorio de datos para persistencia de SQLite
VOLUME ["/app/data"]
ENV HAE_DB_PATH="/app/data/hae_data.db"
ENV HAE_HOST="0.0.0.0"
ENV HAE_PORT="8000"

EXPOSE 8000

CMD ["python", "-m", "server.main"]
