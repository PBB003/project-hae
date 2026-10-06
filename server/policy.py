"""Identidad de la petición y permisos compartidos por REST y MCP."""
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class Identity:
    user_id: str = "legacy"
    role: str = "admin"
    projects: tuple[str, ...] = ()


current_identity = ContextVar("hae_identity", default=Identity())


def authorize(project_id: str, write: bool = False, admin: bool = False):
    identity = current_identity.get()
    if identity.role != "admin" and (admin or project_id not in identity.projects or (write and identity.role != "writer")):
        raise HTTPException(403, "La clave no tiene permisos para esta operación/proyecto")
    return identity
