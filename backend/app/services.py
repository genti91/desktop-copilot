from datetime import datetime

from google.genai import types

from . import vault
from .integrations import gemini
from .models import ProjectSummary, VoiceProcessingResult
from .state import state_memory


def extract_and_save_data(user_text: str, default_project: str):
    if gemini is None:
        return
    try:
        today = datetime.now().strftime("%d/%m/%Y")
        prompt = f"""
        Analiza la frase dictada por el usuario y extrae cualquier tarea, feedback o nota relevante para el proyecto '{default_project}'.

        DATO IMPORTANTE: Hoy es {today}.
        Para las tareas extraídas, el campo 'due_date' DEBE estar obligatoriamente en formato ISO 8601 (YYYY-MM-DD). Calcula la fecha matemáticamente usando la fecha de hoy como referencia.
        Si el usuario NO especifica una fecha de entrega, deja el 'due_date' completamente vacío (string vacío ""). NUNCA escribas "No especificado" ni ninguna otra palabra.
        """
        result = gemini.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[user_text, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VoiceProcessingResult,
            ),
        )
        parsed: VoiceProcessingResult = result.parsed

        if parsed.completed_tasks:
            for path in vault.mark_completed(parsed.project_name, parsed.completed_tasks):
                vault.index_file(path)

        if parsed.action_items or parsed.design_feedback or parsed.general_notes:
            vault.index_file(
                vault.write_capture_note(
                    parsed.project_name,
                    user_text,
                    parsed.action_items,
                    parsed.design_feedback,
                    parsed.general_notes,
                )
            )
            if not parsed.is_general_reminder:
                state_memory["last_project_name"] = parsed.project_name
    except Exception as error:
        print(f"[Background Extraction Error]: {error}")


def process_meeting_storage(meeting_title: str, projects: list[ProjectSummary]):
    try:
        for project in projects:
            vault.index_file(vault.write_meeting_note(meeting_title, project))
            state_memory["last_project_name"] = project.project_name
    except Exception as error:
        print(f"[Background Meeting Save Error]: {error}")
