from typing import Literal, Optional

from pydantic import BaseModel, Field

from .state import DEFAULT_PERSONALITY


class ActionItem(BaseModel):
    task: str
    due_date: str


class ProjectSummary(BaseModel):
    project_name: str
    summary: str
    action_items: list[ActionItem]
    design_feedback: list[str]


class MultiProjectResponse(BaseModel):
    meeting_title: str
    projects: list[ProjectSummary]


class VoiceProcessingResult(BaseModel):
    intent_type: Literal["INTERNAL_QUESTION", "WEB_QUESTION", "NOTE_OR_TASK"]
    spoken_response: str
    project_name: str
    is_general_reminder: bool = False
    action_items: list[ActionItem] = Field(default_factory=list)
    completed_tasks: list[str] = Field(default_factory=list)
    design_feedback: list[str] = Field(default_factory=list)
    general_notes: list[str] = Field(default_factory=list)


class NotesPayload(BaseModel):
    notes_text: str
    default_title: str = "Reunión de Feedback"


class PersonalityPayload(BaseModel):
    personality_text: str
    device: str = "default"
    rag_enabled: Optional[bool] = None


class DeviceImage(BaseModel):
    id: str
    label: str
    source: Literal["default", "upload"]
    preview_url: str
    raw_url: str
    checksum: str


class DeviceConfig(BaseModel):
    revision: int = 1
    rgb_enabled: bool = True
    rgb_color: str = "#FF2A00"
    rgb_brightness: int = Field(default=70, ge=0, le=255)
    filament_enabled: bool = True
    display_enabled: bool = True
    image_id: Optional[str] = None
    # Personalidad del asistente de voz y si este equipo usa notas/RAG. Viven en
    # el perfil del dispositivo para que cada ESP se configure por separado.
    personality: str = DEFAULT_PERSONALITY
    rag_enabled: bool = True
    updated_at: str = ""


class DeviceConfigUpdate(BaseModel):
    """Actualización parcial: sólo se aplican los campos presentes."""

    rgb_enabled: Optional[bool] = None
    rgb_color: Optional[str] = None
    rgb_brightness: Optional[int] = Field(default=None, ge=0, le=255)
    filament_enabled: Optional[bool] = None
    display_enabled: Optional[bool] = None
    image_id: Optional[str] = None
    clear_image: bool = False
    personality: Optional[str] = None
    rag_enabled: Optional[bool] = None


class FirmwareManifest(BaseModel):
    available: bool = False
    version: str = "0.0.0"
    build: int = 0
    url: Optional[str] = None
    sha256: Optional[str] = None
    size: int = 0
    notes: str = ""
    uploaded_at: str = ""
