import asyncio
import re
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from google.genai import types

from .auth import install_auth
from .config import APP_TITLE, FAST_GENAI_CONFIG, FIRMWARE_AUTO_SYNC, MAX_HISTORY_MESSAGES
from .device import router as device_router
from .integrations import (
    collection,
    generate_speech_bytes,
    gemini,
    groq,
    stream_speech_bytes,
)
from .models import MultiProjectResponse, NotesPayload, PersonalityPayload
from .ota_sync import background_sync_loop, router as ota_sync_router
from .pages import router as pages_router
from .services import extract_and_save_data, process_meeting_storage
from .state import sessions, state_memory
from .streaming import aiter_to_thread, split_ready_sentences


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Mientras el backend viva, va a buscar firmware nuevo a GitHub."""
    sync_task = asyncio.create_task(background_sync_loop()) if FIRMWARE_AUTO_SYNC else None
    yield
    if sync_task:
        sync_task.cancel()


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
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    session_id: str = Form("esp32_session"),
):
    audio_bytes = await file.read()
    sessions.setdefault(session_id, [])

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
            # El SDK de Groq es sincrónico. Dentro de un endpoint async frena el
            # event loop, y con el streaming eso ya no es gratis: mientras el
            # loop está tomado no sale el audio que ya tenemos sintetizado.
            transcription = await asyncio.to_thread(
                groq.audio.transcriptions.create,
                file=(file.filename or "audio.webm", audio_bytes),
                model="whisper-large-v3-turbo",
                language="es",
            )
            user_text = transcription.text.strip()
        except Exception as error:
            print(f"[Groq STT Error]: {error}")

    context_text = await asyncio.to_thread(_retrieve_context, user_text)
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
    Si incluís alguno, poné TODOS los [CMD:...] juntos al principio de la respuesta,
    antes de cualquier texto hablado. El dispositivo los recibe en la cabecera del
    audio, que sale antes de que termines de escribir: los que aparezcan más tarde
    llegan tarde y se pierden.

    [INFORMACIÓN RECUPERADA DE NOTAS/REUNIONES]
    Usa esta información para responder sus dudas sobre proyectos:
    {context_text}
    """
    current_user_message = f"Usuario: {user_text or '[Audio inaudible]'}"
    gemini_contents = [system_instruction, *sessions[session_id], current_user_message]

    text_stream = aiter_to_thread(
        lambda: gemini.models.generate_content_stream(
            model="gemini-3.1-flash-lite",
            contents=gemini_contents,
            config=FAST_GENAI_CONFIG,
        )
    )

    # Primera etapa: consumir hasta tener algo que decir. Hace falta cortar acá
    # porque X-Action viaja en la cabecera, y la cabecera sale antes que el
    # cuerpo: para cuando empiece a salir audio, los comandos ya tienen que estar
    # decididos. Por eso el prompt le pide al modelo que los ponga primero.
    buffer = ""
    full_text = ""
    pending_chunks: list[str] = []
    exhausted = False
    try:
        async for piece in text_stream:
            fragment = getattr(piece, "text", None) or ""
            if not fragment:
                continue
            buffer += fragment
            full_text += fragment
            pending_chunks, buffer = split_ready_sentences(buffer)
            if pending_chunks:
                break
        else:
            exhausted = True
    except Exception as error:
        print(f"[Gemini Stream Error]: {error}")
        return Response(
            content=await generate_speech_bytes("Perdón, tuve un problema procesando eso."),
            media_type="audio/mpeg",
            headers={"X-Action": "NONE"},
        )

    if exhausted:
        pending_chunks, buffer = split_ready_sentences(buffer, flush=True)

    commands = re.findall(r"\[CMD:(.*?)\]", full_text, re.IGNORECASE)
    action_command = "|".join(commands).upper() if commands else "NONE"

    if user_text and background_tasks:
        background_tasks.add_task(
            extract_and_save_data,
            user_text,
            state_memory["last_project_name"],
        )

    async def speak(chunk: str):
        spoken = _spoken_text(chunk)
        if not spoken:
            return
        async for audio in stream_speech_bytes(spoken):
            yield audio

    async def audio_body():
        nonlocal buffer, full_text
        said_something = False

        for chunk in pending_chunks:
            async for audio in speak(chunk):
                said_something = True
                yield audio

        if not exhausted:
            try:
                # El generador se reanuda donde lo dejó la primera etapa.
                async for piece in text_stream:
                    fragment = getattr(piece, "text", None) or ""
                    if not fragment:
                        continue
                    buffer += fragment
                    full_text += fragment
                    ready, buffer = split_ready_sentences(buffer)
                    for chunk in ready:
                        async for audio in speak(chunk):
                            said_something = True
                            yield audio
            except Exception as error:
                # La cabecera ya salió, así que no hay forma de avisar del error
                # más que cortando el audio donde quedó.
                print(f"[Gemini Stream Error]: {error}")

            ready, buffer = split_ready_sentences(buffer, flush=True)
            for chunk in ready:
                async for audio in speak(chunk):
                    said_something = True
                    yield audio

        if not said_something:
            async for audio in stream_speech_bytes("Perdón, tuve un problema procesando eso."):
                yield audio

        late = re.findall(r"\[CMD:(.*?)\]", full_text, re.IGNORECASE)
        if len(late) > len(commands):
            print(f"[CMD tardío]: el modelo pidió {late[len(commands):]} después de la cabecera.")

        response_text = _spoken_text(full_text)
        sessions[session_id].extend([current_user_message, f"Asistente: {response_text}"])
        sessions[session_id] = sessions[session_id][-(MAX_HISTORY_MESSAGES * 2):]

    return StreamingResponse(
        audio_body(),
        media_type="audio/mpeg",
        headers={"X-Action": action_command},
    )


def _spoken_text(text: str) -> str:
    """Deja sólo lo que hay que pronunciar: sin markdown y sin los [CMD:]."""
    clean = text.replace("**", "").replace("*", "").replace("#", "")
    return re.sub(r"\[CMD:.*?\]\s*", "", clean, flags=re.IGNORECASE).strip()


def _retrieve_context(user_text: str) -> str:
    if gemini is None or not user_text or collection.count() == 0:
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
        print(f"[Vector Store Query Error]: {error}")
        return "No hay datos relevantes en la base."


@app.post("/update-personality")
def update_personality(payload: PersonalityPayload):
    state_memory["assistant_personality"] = payload.personality_text
    return {"status": "success", "current_personality": state_memory["assistant_personality"]}
