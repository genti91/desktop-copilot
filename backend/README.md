# Desktop Co-Pilot Backend

API FastAPI para procesar notas de reuniones, consultar memoria RAG, guardar tareas en Notion y responder por voz.

## Estructura

```text
backend/
├── app/
│   ├── config.py          # Variables de entorno y configuración
│   ├── integrations.py    # Gemini, Groq, Notion, ChromaDB y TTS
│   ├── main.py            # Aplicación y endpoints HTTP
│   ├── models.py          # Schemas Pydantic
│   ├── services.py        # Procesamiento en segundo plano
│   └── templates/         # Dashboard HTML
├── chroma_db/             # Datos persistidos de ChromaDB
├── .env                  # Secretos locales, no versionar
├── .env.example          # Plantilla de configuración
├── main.py               # Entry point compatible con Uvicorn
└── requirements.txt
```

## Ejecutar

Desde `backend/`, con el entorno virtual activado:

```powershell
pip install -r requirements.txt
uvicorn main:app --reload
```

El dashboard queda disponible en `http://127.0.0.1:8000/dashboard`.
