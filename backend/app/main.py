import asyncio
import re
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from google.genai import types

from .auth import install_auth
from .call import CALL_TOOL, aplicar_accion_llamada, call_declaration, call_relay_server
from .config import (
    APP_TITLE,
    CALL_RELAY_ENABLED,
    FIRMWARE_AUTO_SYNC,
    MAX_HISTORY_MESSAGES,
    VAULT_WATCH_ENABLED,
)
from .device import load_config, router as device_router, safe_device, update_profile
from .integrations import collection, generate_speech_bytes, gemini, groq, voice_models
from .lights import (
    aplicar_accion_luz,
    confirmacion_por_defecto,
    light_declarations,
    tools_prompt,
)
from .models import MultiProjectResponse, NotesPayload, PersonalityPayload
from .ota_sync import background_sync_loop, router as ota_sync_router
from .pages import router as pages_router
from .services import extract_and_save_data, process_meeting_storage
from .state import sessions, state_memory


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Tareas de fondo: espejado de firmware y el relay de videollamada."""
    tasks = []
    if FIRMWARE_AUTO_SYNC:
        tasks.append(asyncio.create_task(background_sync_loop()))
    if CALL_RELAY_ENABLED:
        tasks.append(asyncio.create_task(call_relay_server()))
    if VAULT_WATCH_ENABLED:
        from .vault import watch_loop

        tasks.append(asyncio.create_task(watch_loop()))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title=APP_TITLE, lifespan=lifespan)
app.include_router(pages_router)
app.include_router(device_router)
app.include_router(ota_sync_router)
install_auth(app)


MISSING_GEMINI = "Falta GEMINI_API_KEY en el .env del backend."


@app.post("/process-notes", response_model=MultiProjectResponse)
def process_notes(payload: NotesPayload, background_tasks: BackgroundTasks):
    if gemini is None:
        raise HTTPException(status_code=503, detail=MISSING_GEMINI)
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
    request: Request,
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    session_id: str = Form("esp32_session"),
):
    audio_bytes = await file.read()
    caller_name = (request.headers.get("X-Device-Name") or "").strip().lower()
    # Cada equipo tiene su perfil (personalidad, notas/RAG) y su propio historial.
    device = safe_device(caller_name)
    profile = load_config(device)
    session_key = f"{session_id}:{device}"
    sessions.setdefault(session_key, [])

    if gemini is None:
        # El ESP32 reproduce lo que le devuelvan, así que le contestamos hablando
        # en vez de mandarle un JSON de error que sonaría a ruido.
        return Response(
            content=await generate_speech_bytes(
                "Todavía no tengo configurada la clave de Gemini en el servidor."
            ),
            media_type="audio/mpeg",
            headers={"X-Action": "NONE"},
        )

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

    capabilities = "controlar las luces de su escritorio"
    notes_block = ""
    if profile.rag_enabled:
        capabilities = "recordar notas de reuniones y " + capabilities
        notes_block = f"""
    [INFORMACIÓN RECUPERADA DE NOTAS/REUNIONES]
    Usa esta información para responder sus dudas sobre proyectos. En las tareas,
    "- [x]" es una tarea ya hecha y "- []" una pendiente; si están todas en "- [x]"
    ese proyecto no tiene nada pendiente.
    {_retrieve_context(user_text)}
    """

    system_instruction = f"""
    {profile.personality}
    Sos capaz de {capabilities}.

    {tools_prompt(profile.tuya_enabled)}

    [VIDEOLLAMADA]
    Si te piden llamar a alguien ("llamá a Franco", "videollamada con Jose"), usá
    la función {CALL_TOOL} con el nombre en minúsculas. Avisá que estás llamando.
    {notes_block}
    """
    current_user_message = f"Usuario: {user_text or '[Audio inaudible]'}"
    gemini_contents = [system_instruction, *sessions[session_key], current_user_message]

    # Function calling MANUAL: una sola llamada al modelo. Si en la respuesta
    # viene un function_call, lo ejecutamos acá y hablamos una confirmación
    # breve —del modelo si la dio, o una por defecto—. La segunda llamada que
    # haría el modo automático (para que el modelo reaccione al resultado) no
    # vale la pena: el control de luces es fire-and-forget.
    pending_esp: list[str] = []
    tools = [
        types.Tool(
            function_declarations=light_declarations(profile.tuya_enabled) + [call_declaration()]
        )
    ]
    sin_auto_fc = types.AutomaticFunctionCallingConfig(disable=True)

    # Cualquier error pasa al siguiente modelo, sin distinguir el tipo: un 503 y
    # un timeout se ven distinto pero significan lo mismo acá —de este modelo no
    # va a salir la respuesta— y separarlos ya causó que un timeout del primario
    # se saltara el respaldo. Si fallan todos queda la disculpa hablada.
    response_text = "Perdón, tuve un problema procesando eso."
    for modelo, base_config in voice_models():
        configuracion = base_config.model_copy(
            update={"tools": tools, "automatic_function_calling": sin_auto_fc}
        )
        try:
            result = gemini.models.generate_content(
                model=modelo,
                contents=gemini_contents,
                config=configuracion,
            )
        except Exception as error:
            print(f"[Gemini] {modelo} no contestó ({type(error).__name__}); paso al siguiente.")
            continue

        candidate = result.candidates[0] if result.candidates else None
        parts = (
            candidate.content.parts
            if candidate and candidate.content and candidate.content.parts
            else []
        )
        spoken = "".join(part.text for part in parts if getattr(part, "text", None))
        llamadas = [part.function_call for part in parts if getattr(part, "function_call", None)]

        fragmentos: list[str] = []
        for llamada in llamadas:
            args = dict(llamada.args or {})
            try:
                if llamada.name == CALL_TOOL:
                    frase = aplicar_accion_llamada(args, pending_esp, caller_name)
                else:
                    frase = await asyncio.to_thread(
                        aplicar_accion_luz, llamada.name, args, pending_esp, profile.tuya_enabled
                    )
            except Exception as error:
                print(f"[Tool] {llamada.name} falló ({type(error).__name__}: {error}).")
                frase = "tuve un problema con eso"
            if frase:
                fragmentos.append(frase)

        if llamadas and not spoken.strip():
            spoken = confirmacion_por_defecto(fragmentos)

        limpio = spoken.replace("**", "").replace("*", "").replace("#", "").strip()
        if limpio:
            response_text = limpio
        break

    sessions[session_key].extend([current_user_message, f"Asistente: {response_text}"])
    sessions[session_key] = sessions[session_key][-(MAX_HISTORY_MESSAGES * 2):]

    # Red por si un modelo ignora las funciones y escribe [CMD:...] en el texto.
    comandos_texto = re.findall(r"\[CMD:(.*?)\]", response_text, re.IGNORECASE)
    spoken_text = re.sub(r"\[CMD:.*?\]\s*", "", response_text, flags=re.IGNORECASE).strip()

    acciones = pending_esp + [comando.strip().upper() for comando in comandos_texto]
    action_command = "|".join(accion for accion in acciones if accion) or "NONE"

    if user_text and background_tasks and profile.rag_enabled:
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
    if gemini is None or not user_text or collection.count() == 0:
        return "No hay datos relevantes en la base."
    try:
        embedding = gemini.models.embed_content(model="gemini-embedding-2", contents=user_text)
        # Sin filtrar por estado: las notas traen los checkboxes `- [x]` / `- [ ]`,
        # así el modelo distingue lo hecho de lo pendiente y además puede responder
        # sobre resúmenes y feedback de proyectos ya terminados.
        results = collection.query(
            query_embeddings=[embedding.embeddings[0].values],
            n_results=3,
        )
        documents = results.get("documents", [[]])[0]
        return "\n---\n".join(documents) if documents else "No hay datos relevantes en la base."
    except Exception as error:
        print(f"[Vector Store Query Error]: {error}")
        return "No hay datos relevantes en la base."


@app.post("/update-personality")
def update_personality(payload: PersonalityPayload):
    changes: dict = {"personality": payload.personality_text}
    if payload.rag_enabled is not None:
        changes["rag_enabled"] = payload.rag_enabled
    if payload.tuya_enabled is not None:
        changes["tuya_enabled"] = payload.tuya_enabled
    profile = update_profile(payload.device, changes)
    return {
        "status": "success",
        "device": safe_device(payload.device),
        "current_personality": profile.personality,
        "rag_enabled": profile.rag_enabled,
        "tuya_enabled": profile.tuya_enabled,
    }
