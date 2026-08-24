import io
import uuid
from datetime import datetime
from typing import Optional

import edge_tts
from google import genai
from groq import Groq
from notion_client import Client as NotionClient

from .config import (
    GEMINI_API_KEY,
    GROQ_API_KEY,
    NOTION_API_KEY,
    NOTION_DATABASE_ID,
    VECTOR_STORE_PATH,
)
from .models import ActionItem, ProjectSummary
from .vectorstore import Collection


gemini = genai.Client(api_key=GEMINI_API_KEY)
groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
notion = NotionClient(auth=NOTION_API_KEY) if NOTION_API_KEY else None
collection = Collection(VECTOR_STORE_PATH)


def clean_notion_id(raw_id: str) -> str:
    if not raw_id:
        return ""
    return raw_id.split("?")[0].split("/")[-1].replace("-", "")


def send_to_notion(
    project_name: str,
    action_items: list[ActionItem],
    design_feedback: list[str],
    general_notes: Optional[list[str]] = None,
    summary: Optional[str] = None,
    meeting_title: Optional[str] = None,
) -> bool:
    if not notion or not NOTION_DATABASE_ID:
        return False

    database_id = clean_notion_id(NOTION_DATABASE_ID)

    def insert_record(name: str, category: str, due_date: Optional[str] = None):
        properties = {
            "Nombre": {"title": [{"text": {"content": name}}]},
            "Proyecto": {"select": {"name": project_name}},
            "Categoría": {"select": {"name": category}},
            "Estado": {"status": {"name": "Pendiente"}},
        }
        if due_date and due_date != "None":
            properties["Fecha de Entrega"] = {"date": {"start": due_date}}
        try:
            notion.pages.create(parent={"database_id": database_id}, properties=properties)
        except Exception as error:
            print(f"-> [Notion Insert Error] {category}: {error}")

    if summary and meeting_title:
        insert_record(f"Resumen ({meeting_title}): {summary}", "Nota")
    for item in action_items:
        insert_record(item.task, "Tarea", item.due_date)
    for feedback in design_feedback:
        insert_record(feedback, "Feedback")
    for note in general_notes or []:
        insert_record(note, "Nota")
    return True


def mark_tasks_as_completed(project_name: str, completed_tasks: list[str]):
    if not notion or not completed_tasks or not NOTION_DATABASE_ID:
        return

    try:
        for task_text in completed_tasks:
            search_result = notion.databases.query(
                database_id=clean_notion_id(NOTION_DATABASE_ID),
                filter={
                    "and": [
                        {"property": "Proyecto", "select": {"equals": project_name}},
                        {"property": "Estado", "status": {"equals": "Pendiente"}},
                        {"property": "Nombre", "rich_text": {"contains": task_text}},
                    ]
                },
            )
            for page in search_result.get("results", []):
                notion.pages.update(
                    page_id=page["id"],
                    properties={"Estado": {"status": {"name": "Completado"}}},
                )
    except Exception as error:
        print(f"-> [Notion Mark Completed Error]: {error}")


def mark_rag_tasks_as_completed(project_name: str, completed_tasks: list[str]):
    try:
        results = collection.get(where={"project": project_name})
        if not results or not results.get("ids"):
            return
        for task_text in completed_tasks:
            for doc_id, doc_text, metadata in zip(
                results["ids"], results["documents"], results["metadatas"]
            ):
                if task_text.lower() in doc_text.lower() and metadata.get("status") == "pending":
                    collection.update(
                        ids=[doc_id],
                        metadatas=[{**metadata, "status": "completed"}],
                    )
    except Exception as error:
        print(f"-> [RAG Mark Completed Error]: {error}")


def save_project_to_rag(meeting_title: str, project: ProjectSummary) -> str:
    action_items = "\n".join(
        f"- {item.task} (Entrega: {item.due_date})" for item in project.action_items
    )
    feedback = "\n".join(f"- {item}" for item in project.design_feedback)
    document = f"""
    Reunión: {meeting_title}
    Proyecto: {project.project_name}
    Resumen: {project.summary}
    Tareas de diseño:
    {action_items if action_items else 'Ninguna'}
    Feedback visual:
    {feedback if feedback else 'Ninguno'}
    """
    embedding = gemini.models.embed_content(model="gemini-embedding-2", contents=document)
    document_id = str(uuid.uuid4())
    collection.add(
        ids=[document_id],
        embeddings=[embedding.embeddings[0].values],
        documents=[document],
        metadatas=[
            {
                "project": project.project_name,
                "meeting_title": meeting_title,
                "status": "pending",
                "created_at": datetime.now().isoformat(),
            }
        ],
    )
    return document_id


async def generate_speech_bytes(text: str) -> bytes:
    communicate = edge_tts.Communicate(text, "es-AR-TomasNeural")
    buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    return buffer.getvalue()
