import html
import re
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, Response
from google.genai import types

from .config import APP_TITLE, FAST_GENAI_CONFIG, MAX_HISTORY_MESSAGES
from .integrations import collection, generate_speech_bytes, gemini, groq
from .models import MultiProjectResponse, NotesPayload, PersonalityPayload
from .services import extract_and_save_data, process_meeting_storage
from .state import sessions, state_memory

app = FastAPI(title=APP_TITLE)
DASHBOARD_TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"


@app.post("/process-notes", response_model=MultiProjectResponse)
def process_notes(payload: NotesPayload, background_tasks: BackgroundTasks):
    today = datetime.now().strftime("%d/%m/%Y")
    prompt = f"""
    Eres un asistente para una diseñadora gráfica de imagen corporativa.
    Analiza el texto de notas de reunión y separa la información por PROYECTO o CLIENTE.

    DATO IMPORTANTE: La fecha actual es {today}. 
    Si el texto no menciona fecha, usa esta para el 'meeting_title'.

    Para cada proyecto extrae:
    - project_name: Nombre del proyecto/cliente.
    - summary: Breve síntesis del feedback.
    - action_items: Tareas exclusivas para la diseñadora. El 'due_date' DEBE estar obligatoriamente en formato ISO 8601 (YYYY-MM-DD). Calcula la fecha matemática exacta usando la fecha actual como referencia (ej: si hoy es 18/08/2026 y pide para "mañana", pon 2026-08-19. Si no hay fecha clara, no pongas nada).
    - design_feedback: Lista de observaciones visuales y cambios solicitados.
    """
    response = gemini.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=[payload.notes_text, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=MultiProjectResponse,
        ),
    )
    parsed_response: MultiProjectResponse = response.parsed
    state_memory["last_meeting_title"] = parsed_response.meeting_title
    background_tasks.add_task(
        process_meeting_storage,
        parsed_response.meeting_title,
        parsed_response.projects,
    )
    return parsed_response


@app.post("/voice-assistant")
async def voice_assistant(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    session_id: str = Form("esp32_session"),
):
    audio_bytes = await file.read()
    sessions.setdefault(session_id, [])

    user_text = ""
    if groq:
        try:
            transcription = groq.audio.transcriptions.create(
                file=(file.filename or "audio.webm", audio_bytes),
                model="whisper-large-v3-turbo",
                language="es",
            )
            user_text = transcription.text.strip()
        except Exception as error:
            print(f"[Groq STT Error]: {error}")

    context_text = _retrieve_context(user_text)
    system_instruction = f"""
    {state_memory["assistant_personality"]}
    Sos capaz de recordar notas de reuniones y controlar las luces de su escritorio.

    [CONTROL DE LUCES]
    - Color NeoPixel: usa [CMD:LED_RGB:R,G,B] (valores de 0 a 255).
    - Brillo NeoPixel: usa [CMD:LED_BRIGHTNESS:V] (V de 0 a 255).
    - Encender Filamento: usa [CMD:FILAMENT_ON].
    - Apagar Filamento: usa [CMD:FILAMENT_OFF].
    - Apagar todos los LEDs y el display: usa [CMD:ALL_OFF]. La próxima vez que se toque el botón de voz, todo volverá a encenderse.
    Si no te pide interactuar con las luces, no incluyas ningún [CMD:].

    [INFORMACIÓN RECUPERADA DE NOTAS/REUNIONES]
    Usa esta información para responder sus dudas sobre proyectos:
    {context_text}
    """
    current_user_message = f"Usuario: {user_text or '[Audio inaudible]'}"
    gemini_contents = [system_instruction, *sessions[session_id], current_user_message]

    try:
        result = gemini.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=gemini_contents,
            config=FAST_GENAI_CONFIG,
        )
        response_text = (result.text or "").replace("**", "").replace("*", "").replace("#", "").strip()
    except Exception as error:
        print(f"[Gemini Error]: {error}")
        response_text = "Perdón, tuve un problema procesando eso."

    sessions[session_id].extend([current_user_message, f"Asistente: {response_text}"])
    sessions[session_id] = sessions[session_id][-(MAX_HISTORY_MESSAGES * 2):]

    commands = re.findall(r"\[CMD:(.*?)\]", response_text, re.IGNORECASE)
    action_command = "|".join(commands).upper() if commands else "NONE"
    spoken_text = re.sub(r"\[CMD:.*?\]\s*", "", response_text, flags=re.IGNORECASE).strip()

    if user_text and background_tasks:
        background_tasks.add_task(
            extract_and_save_data,
            user_text,
            state_memory["last_project_name"],
        )

    audio_response = await generate_speech_bytes(spoken_text)
    return Response(
        content=audio_response,
        media_type="audio/mpeg",
        headers={"X-Action": action_command},
    )


def _retrieve_context(user_text: str) -> str:
    if not user_text or collection.count() == 0:
        return "No hay datos relevantes en la base."
    try:
        embedding = gemini.models.embed_content(model="gemini-embedding-2", contents=user_text)
        results = collection.query(
            query_embeddings=[embedding.embeddings[0].values],
            where={"status": {"$ne": "completed"}},
            n_results=2,
        )
        documents = results.get("documents", [[]])[0]
        return "\n---\n".join(documents) if documents else "No hay datos relevantes en la base."
    except Exception as error:
        print(f"[ChromaDB Query Error]: {error}")
        return "No hay datos relevantes en la base."


@app.post("/update-personality")
def update_personality(payload: PersonalityPayload):
    state_memory["assistant_personality"] = payload.personality_text
    return {"status": "success", "current_personality": state_memory["assistant_personality"]}


@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard():
    template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    content = template.replace(
        "__PERSONALITY__",
        html.escape(state_memory["assistant_personality"]),
    )
    return HTMLResponse(content=content)
