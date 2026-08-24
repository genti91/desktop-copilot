from datetime import datetime
import uuid

from google.genai import types

from .integrations import (
    collection,
    gemini,
    mark_rag_tasks_as_completed,
    mark_tasks_as_completed,
    save_project_to_rag,
    send_to_notion,
)
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
            mark_tasks_as_completed(parsed.project_name, parsed.completed_tasks)
            mark_rag_tasks_as_completed(parsed.project_name, parsed.completed_tasks)

        if parsed.action_items or parsed.design_feedback or parsed.general_notes:
            document = f"Dictado/Nota [{parsed.project_name}]: {user_text}"
            embedding = gemini.models.embed_content(model="gemini-embedding-2", contents=document)
            collection.add(
                ids=[str(uuid.uuid4())],
                embeddings=[embedding.embeddings[0].values],
                documents=[document],
                metadatas=[
                    {
                        "project": parsed.project_name,
                        "is_general": parsed.is_general_reminder,
                        "status": "pending",
                        "created_at": datetime.now().isoformat(),
                    }
                ],
            )
            send_to_notion(
                project_name=parsed.project_name,
                action_items=parsed.action_items,
                design_feedback=parsed.design_feedback,
                general_notes=parsed.general_notes,
            )
            if not parsed.is_general_reminder:
                state_memory["last_project_name"] = parsed.project_name
    except Exception as error:
        print(f"[Background Extraction Error]: {error}")


def process_meeting_storage(meeting_title: str, projects: list[ProjectSummary]):
    try:
        for project in projects:
            save_project_to_rag(meeting_title, project)
            page_id = send_to_notion(
                project_name=project.project_name,
                action_items=project.action_items,
                design_feedback=project.design_feedback,
                summary=project.summary,
                meeting_title=meeting_title,
            )
            if page_id:
                state_memory["last_notion_page_ids"][project.project_name] = page_id
            state_memory["last_project_name"] = project.project_name
    except Exception as error:
        print(f"[Background Meeting Save Error]: {error}")
