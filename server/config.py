from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, Field
from typing import Literal


class ClientKey(BaseModel):
    key: str = Field(min_length=16)
    user_id: str = Field(min_length=1)
    projects: list[str] = Field(min_length=1)
    role: Literal["reader", "writer"] = "reader"

# Valores de ejemplo/predecibles que nunca deben usarse como clave real
PLACEHOLDER_KEYS = {
    "",
    "change-me",
    "hae-secret-token-change-me",
    "tu-clave-secreta-hae-2026",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HAE_", env_file=".env", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_KEY: str = ""  # Obligatoria: protege la API REST y el endpoint MCP/SSE
    DB_PATH: str = "hae_data.db"
    DEBUG: bool = False
    CLIENT_KEYS: list[ClientKey] = Field(default_factory=list)
    CONTEXT_MAX_CHARS: int = Field(6000, ge=1500, le=20000)
    CATALOG_MAX_AGE_HOURS: int = Field(24, ge=1)
    WEBHOOK_TOKEN: str = ""
    YOUTRACK_URL: str = ""
    YOUTRACK_TOKEN: str = ""
    YOUTRACK_PROJECTS: dict[str, str] = Field(default_factory=dict)

    def validate_api_key(self) -> None:
        if self.API_KEY in PLACEHOLDER_KEYS or len(self.API_KEY) < 16:
            raise RuntimeError(
                "HAE_API_KEY no está configurada o es insegura (mínimo 16 caracteres, "
                "no puede ser un valor de ejemplo). Genera una con: "
                "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        keys = [self.API_KEY] + [client.key for client in self.CLIENT_KEYS]
        if len(set(keys)) != len(keys):
            raise RuntimeError("Cada credencial HAE debe ser única, incluida la clave administradora")
        if any(client.key in PLACEHOLDER_KEYS for client in self.CLIENT_KEYS):
            raise RuntimeError("No se admiten claves de ejemplo para los usuarios")
        if any(not project.strip() for client in self.CLIENT_KEYS for project in client.projects):
            raise RuntimeError("Las credenciales requieren proyectos no vacíos")
        if self.WEBHOOK_TOKEN and len(self.WEBHOOK_TOKEN) < 32:
            raise RuntimeError("HAE_WEBHOOK_TOKEN requiere al menos 32 caracteres")


settings = Settings()
