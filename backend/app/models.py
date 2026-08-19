from typing import Literal

from pydantic import BaseModel, Field


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
