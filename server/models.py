from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Annotated, List, Literal, Optional
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CatalogItem(BaseModel):
    repo_id: str = "legacy"
    branch: str = ""
    source_line: Optional[int] = Field(None, ge=1)
    source_end_line: Optional[int] = Field(None, ge=1)
    contract: Optional[str] = None
    file_path: NonEmpty

    @model_validator(mode="after")
    def line_order(self):
        if self.source_line is not None and self.source_end_line is not None and self.source_end_line < self.source_line:
            raise ValueError("source_end_line debe ser posterior a source_line")
        return self

    @field_validator("file_path")
    @classmethod
    def relative_path(cls, value):
        value = value.replace("\\", "/")
        if PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts or ":" in value:
            raise ValueError("file_path debe ser una ruta relativa sin ..")
        return value


class RepositorySnapshot(BaseModel):
    repo_id: NonEmpty
    branch: str = ""
    commit_sha: str = ""
    dirty: bool = False
    scanned_at: datetime
    expected_revision: int = Field(0, ge=0)
    complete: bool = True
    allow_empty: bool = False

    @field_validator("scanned_at")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None:
            raise ValueError("scanned_at debe incluir zona horaria")
        return value

class ProjectModel(BaseModel):
    id: NonEmpty = Field(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", description="Identificador único del proyecto (slug)")
    name: NonEmpty = Field(..., description="Nombre legible del proyecto")
    description: Optional[str] = None
    tech_stack: Optional[str] = Field(None, description="Resumen del stack tecnológico (ej: React 19, Tailwind v4, Zustand)")

class ComponentItem(CatalogItem):
    name: NonEmpty = Field(..., description="Nombre del componente (ej: Button, Modal)")
    file_path: NonEmpty = Field(..., description="Ruta relativa del archivo (ej: src/components/ui/Button.tsx)")
    props_summary: Optional[str] = Field(None, description="Firma o tipos de las props aceptadas")
    description: Optional[str] = Field(None, description="Propósito del componente")
    category: Optional[str] = Field("general", description="Categoría (ui, form, layout, feature)")

class UtilityItem(CatalogItem):
    name: NonEmpty = Field(..., description="Nombre de la función o hook (ej: useAuth, formatPrice)")
    file_path: NonEmpty = Field(..., description="Ruta relativa del archivo (ej: src/hooks/useAuth.ts)")
    signature: Optional[str] = Field(None, description="Firma de la función o tipos TypeScript")
    description: Optional[str] = Field(None, description="Propósito de la función o hook")
    type: Optional[str] = Field("util", description="Tipo: hook, util, service, constant")

class RuleItem(BaseModel):
    id: Optional[int] = None
    title: NonEmpty = Field(..., description="Título de la decisión o regla")
    rule_content: NonEmpty = Field(..., description="Detalle de la regla o convención")
    category: Optional[str] = Field("architecture", description="Categoría (architecture, styling, state, rules)")
    status: Literal["active", "proposed", "retired"] = "active"
    author: str = ""
    check_spec: Optional[dict] = None

    @field_validator("check_spec")
    @classmethod
    def declarative_check(cls, value):
        if value is not None:
            from client.rule_checker import validate_check_spec
            validate_check_spec(value)
        return value

class SyncPayload(BaseModel):
    project: ProjectModel
    components: List[ComponentItem] = []
    utilities: List[UtilityItem] = []
    rules: Optional[List[RuleItem]] = None
    repository: Optional[RepositorySnapshot] = None
    mode: Literal["merge", "snapshot"] = "merge"

    @model_validator(mode="after")
    def safe_snapshot(self):
        if self.mode == "snapshot" and (self.repository is None or not self.repository.complete):
            raise ValueError("snapshot requiere un repositorio y un escaneo completo")
        return self

class SessionLogCreate(BaseModel):
    summary: str = Field(..., description="Resumen de las tareas y cambios realizados en la sesión")
    completed_tasks: Optional[str] = Field(None, description="Lista o texto con las tareas finalizadas")
    pending_tasks: Optional[str] = Field(None, description="Tareas pendientes o siguientes pasos para la próxima sesión")
    repo_id: str = ""
    branch: str = ""
    commit_sha: str = ""
    ticket_id: str = ""
    agent: str = ""
    evidence: Optional[str] = None

class SessionLogItem(BaseModel):
    id: Optional[int] = None
    project_id: str
    summary: str
    completed_tasks: Optional[str] = None
    pending_tasks: Optional[str] = None
    created_at: Optional[str] = None

class MeetingNoteCreate(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(..., description="Título de la reunión (ej: Daily - tekniek)")
    meeting_date: Optional[str] = Field(None, description="Fecha de la reunión (YYYY-MM-DD)")
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(..., description="Resumen ejecutivo de los temas y acuerdos")
    action_items: Optional[str] = Field(None, description="Tareas o acuerdos técnicos concretos")
    raw_notes: Optional[str] = Field(None, description="Texto completo de notas de Gemini")
    source_url: Optional[str] = Field(None, description="Enlace al Google Doc")
    source_id: Optional[str] = None

    @field_validator("meeting_date")
    @classmethod
    def validate_meeting_date(cls, value: Optional[str]) -> Optional[str]:
        # Mantener el contrato string/null y las fechas opcionales del cliente.
        if value:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError("meeting_date debe usar YYYY-MM-DD")
        return value

class MeetingNoteItem(BaseModel):
    id: Optional[int] = None
    project_id: str
    title: str
    meeting_date: Optional[str] = None
    summary: str
    action_items: Optional[str] = None
    raw_notes: Optional[str] = None
    source_url: Optional[str] = None
    created_at: Optional[str] = None

class ProjectTaskCreate(BaseModel):
    external_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(..., description="Identificador en YouTrack (ej: TEK-104)")
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(..., description="Título de la tarea")
    status: Optional[str] = "Open"
    description: Optional[str] = None
    assignee: Optional[str] = None
    url: Optional[str] = None
    is_mine: Optional[bool] = False
    resolved_at: Optional[int] = Field(None, ge=0)
    source_updated_at: Optional[int] = Field(None, ge=0)
    source_id: Optional[str] = None
    assignee_ids: List[str] = Field(default_factory=list)


class TaskSnapshot(BaseModel):
    tasks: List[ProjectTaskCreate] = Field(default_factory=list)
    viewer_id: str = "legacy"
    complete: bool = False
    observed_at: Optional[datetime] = None

    @model_validator(mode="after")
    def complete_timestamp(self):
        if self.complete and (self.observed_at is None or self.observed_at.tzinfo is None):
            raise ValueError("Un snapshot completo requiere observed_at con zona horaria al iniciar la consulta")
        if self.complete and any(task.source_updated_at is None for task in self.tasks):
            raise ValueError("Un snapshot completo requiere source_updated_at en todos los tickets")
        if self.observed_at and self.observed_at.tzinfo is None:
            raise ValueError("observed_at requiere zona horaria")
        return self


class YouTrackEvent(BaseModel):
    event: Literal["issueCreated", "issueUpdated", "issueDeleted"]
    id: NonEmpty
    project: dict
    timestamp: datetime

    model_config = {"extra": "allow"}

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None:
            raise ValueError("timestamp debe incluir zona horaria")
        return value

class ProjectTaskItem(BaseModel):
    id: Optional[int] = None
    project_id: str
    external_id: str
    title: str
    status: Optional[str] = "Open"
    description: Optional[str] = None
    assignee: Optional[str] = None
    url: Optional[str] = None
    is_mine: Optional[int] = 0
    updated_at: Optional[str] = None
