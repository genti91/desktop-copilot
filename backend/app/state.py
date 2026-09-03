sessions: dict[str, list[str]] = {}

DEFAULT_PERSONALITY = "Sos el asistente de escritorio de una diseñadora gráfica. Respondés de forma conversacional, empática y concisa."

# La personalidad del asistente ya no vive acá: pasó al perfil de cada
# dispositivo (models.DeviceConfig.personality) para configurarse por separado.
state_memory = {
    "last_meeting_title": "Reunión de Feedback",
    "last_project_name": "General",
}
