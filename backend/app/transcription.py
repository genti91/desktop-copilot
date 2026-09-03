"""Decide si una transcripción es alguien hablándole al equipo, o ruido.

El ESP32 ya filtra por nivel antes de mandar (ver el margen sobre el piso de
ruido en `wakeword.cpp`), pero eso sólo distingue "cerca" de "lejos". Lo que
pasa ese filtro y sigue sin ser un pedido llega hasta acá, y son dos cosas:

  - Silencio o ruido sordo. Whisper NO devuelve vacío cuando le llega eso: se
    entrenó con subtítulos de videos, así que inventa la muletilla con la que
    esos videos terminan. "Gracias por ver el video", "Subtítulos realizados
    por la comunidad de Amara.org" y "¡Suscríbete!" son las tres que más
    aparecen, y son alucinaciones, no algo que alguien haya dicho.
  - Una transcripción de una o dos letras, que es lo que sale de un golpe seco.

Contestar cualquiera de las dos es lo que se percibe como "le hablo una vez y
después responde solo". Devolverlas como ruido deja al dispositivo callado, que
en la duda es lo que corresponde.
"""

import re
import unicodedata

# Comparadas ya normalizadas: sin tildes, sin puntuación y en minúsculas.
#
# "gracias" está en la lista a sabiendas de que alguien puede agradecerle al
# asistente de verdad. Es la alucinación más frecuente de todas, y el costo de
# equivocarse es asimétrico: perder un "de nada" no se nota, contestar solo en
# medio de una charla ajena sí.
ALUCINACIONES_DE_WHISPER = frozenset(
    {
        "gracias",
        "muchas gracias",
        "muchisimas gracias",
        "gracias por ver el video",
        "gracias por ver este video",
        "gracias por su atencion",
        "suscribete",
        "suscribete al canal",
        "no olvides suscribirte",
        "subtitulos realizados por la comunidad de amara org",
        "subtitulos por la comunidad de amara org",
        "subtitulado por la comunidad de amara org",
        "subtitulos creados por la comunidad de amara org",
        "amara org",
        "mas informacion en www consumer es",
        "hasta la proxima",
        "nos vemos en el proximo video",
        "you",
        "the",
    }
)

# Menos que esto no alcanza para un pedido: es un chasquido transcrito.
MINIMO_DE_CARACTERES = 2


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y sin puntuación, para comparar contra la lista."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    sin_tildes = "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", sin_tildes)).strip()


def es_ruido(texto: str) -> bool:
    """True si no hay que contestar: no se dijo nada, o Whisper lo inventó."""
    limpio = normalizar(texto or "")
    if len(limpio) < MINIMO_DE_CARACTERES:
        return True
    return limpio in ALUCINACIONES_DE_WHISPER
