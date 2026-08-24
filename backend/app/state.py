sessions: dict[str, list[str]] = {}

DEFAULT_PERSONALITY = "Sos el asistente de escritorio de una diseñadora gráfica. Respondés de forma conversacional, empática y concisa."

state_memory = {
    "last_meeting_title": "Reunión de Feedback",
    "last_project_name": "General",
    "last_notion_page_ids": {},
    "assistant_personality": DEFAULT_PERSONALITY,
}
