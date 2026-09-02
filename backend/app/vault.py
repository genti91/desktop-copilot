"""Notas como archivos Markdown en un vault de Obsidian.

El vault es la única fuente de verdad: cada nota o reunión es un `.md` con un
frontmatter mínimo. El vector store (RAG) pasa a ser un índice derivado y
reconstruible —si se borra, se rearma leyendo el vault—.

Dos caminos lo mantienen al día:

- **Escritura del backend** (`write_meeting_note`, `add_capture`,
  `mark_completed`): indexan el archivo en el acto, sin esperar al watcher.
  El proyecto se resuelve con `resolve_project`: si ya hay una carpeta parecida
  se reutiliza, en vez de crear "La Espiga" al lado de "Panadería La Espiga".
- **`watch_loop`**: un poll cada pocos segundos que reindexa lo que cambió por
  fuera del backend (lo que editás en Obsidian) y saca del índice lo borrado.
  Es un poll y no `watchdog` a propósito: sin dependencias nuevas y alcanza de
  sobra para un vault personal.

El frontmatter se parsea y se emite a mano (pares `clave: valor` planos) para no
sumar PyYAML, igual que el resto del backend evita dependencias pesadas.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import OBSIDIAN_VAULT_PATH, VAULT_WATCH_INTERVAL_SECONDS
from .models import ActionItem, ProjectSummary

EMBED_MODEL = "gemini-embedding-2"
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_CHECKBOX = re.compile(r"^(\s*[-*]\s+)\[([ xX])\](\s+.*)$")

# Archivo donde se acumulan las tareas/notas sueltas de un proyecto (voz o
# dictado corto). Las reuniones de `/process-notes` siguen yendo a un .md propio.
CAPTURE_FILE = "Capturas.md"

# Umbral de parecido para decidir que dos nombres son el mismo proyecto.
PROJECT_MATCH_THRESHOLD = 0.8
_STOPWORDS = {"la", "el", "los", "las", "de", "del", "y", "e", "the", "un", "una", "&"}


def vault_root() -> Path:
    return Path(OBSIDIAN_VAULT_PATH)


# --------------------------------------------------------------------------- #
# Nombres de archivo y frontmatter
# --------------------------------------------------------------------------- #


def _clean_component(text: str, *, fallback: str) -> str:
    """Deja un texto usable como nombre de carpeta o archivo en cualquier SO."""
    text = _FORBIDDEN.sub("", (text or "").strip()).strip(" .")
    text = re.sub(r"\s+", " ", text)
    return text[:80] or fallback


def _canon(text: str) -> tuple[str, set[str]]:
    """Normaliza para comparar: sin acentos, minúsculas, sólo letras y números."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    tokens = re.sub(r"[^a-z0-9]+", " ", plain).split()
    return " ".join(tokens), set(tokens)


def _significant(tokens: set[str]) -> set[str]:
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def _all_tokens_have_a_match(small: set[str], big: set[str]) -> bool:
    """True si cada token de `small` aparece en `big`, exacto o con un typo chico."""
    if not small or not big:
        return False
    return all(
        any(a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85 for b in big)
        for a in small
    )


def existing_projects() -> list[str]:
    """Nombres de las carpetas de proyecto que ya existen en el vault."""
    root = vault_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def resolve_project(name: str) -> str:
    """Devuelve la carpeta de proyecto que corresponde a `name`.

    Si ya hay una carpeta con un nombre igual o parecido —mismo nombre con más
    o menos palabras ("La Espiga" / "Panadería La Espiga"), o escrito un poco
    distinto ("Ferrari" / "Ferrari & Asociados", un typo)— se reutiliza esa. Si
    no se parece a ninguna, es un proyecto nuevo y se usa el nombre tal cual.
    """
    proposed = _clean_component(name, fallback="General")
    candidates = existing_projects()
    if not candidates:
        return proposed

    target_str, target_tokens = _canon(proposed)
    target_sig = _significant(target_tokens)

    scored: list[tuple[float, float, str]] = []
    for candidate in candidates:
        cand_str, cand_tokens = _canon(candidate)
        cand_sig = _significant(cand_tokens)

        contained = _all_tokens_have_a_match(target_sig, cand_sig) or _all_tokens_have_a_match(
            cand_sig, target_sig
        )
        union = target_tokens | cand_tokens
        jaccard = len(target_tokens & cand_tokens) / len(union) if union else 0.0
        ratio = difflib.SequenceMatcher(None, target_str, cand_str).ratio()
        score = max(ratio, jaccard, 0.9 if contained else 0.0)
        scored.append((score, jaccard, candidate))

    scored.sort(reverse=True)
    best_score, _, best_name = scored[0]
    if best_score >= PROJECT_MATCH_THRESHOLD and best_name != proposed:
        print(f"[Vault] '{name}' -> proyecto existente '{best_name}'")
    return best_name if best_score >= PROJECT_MATCH_THRESHOLD else proposed


def _project_dir(project_name: str) -> Path:
    directory = vault_root() / resolve_project(project_name)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _dump_frontmatter(fields: dict[str, str]) -> str:
    lines = "\n".join(f"{key}: {value}" for key, value in fields.items() if value not in (None, ""))
    return f"---\n{lines}\n---\n"


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Devuelve (frontmatter, cuerpo). Sin frontmatter, el dict va vacío."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("\n")
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("'\"")
    return fields, body


def relative_id(path: Path) -> str:
    """Id estable para el vector store: la ruta relativa al vault, en POSIX."""
    try:
        return path.resolve().relative_to(vault_root().resolve()).as_posix()
    except ValueError:
        return path.name


def _project_from_path(path: Path) -> str:
    try:
        parts = path.resolve().relative_to(vault_root().resolve()).parts
        return parts[0] if len(parts) > 1 else "General"
    except ValueError:
        return "General"


def _is_hidden(path: Path, base: Path) -> bool:
    """True si `path` cuelga de una carpeta oculta (.stversions, .obsidian, .trash…)."""
    try:
        rel = path.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return any(part.startswith(".") for part in rel.parts[:-1])


def note_files(base: Path) -> list[Path]:
    """Los .md reales bajo `base`, sin lo que vive en carpetas ocultas."""
    return [path for path in base.rglob("*.md") if not _is_hidden(path, vault_root())]


# --------------------------------------------------------------------------- #
# Escritura
# --------------------------------------------------------------------------- #


def _task_line(item: ActionItem) -> str:
    due = (item.due_date or "").strip()
    return f"- [ ] {item.task}" + (f" (entrega: {due})" if due and due != "None" else "")


def write_meeting_note(meeting_title: str, project: ProjectSummary) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    directory = _project_dir(project.project_name)
    project_name = directory.name
    # El modelo a veces mete la fecha en el título; no la repetimos en el nombre.
    clean_title = re.sub(r"\s*[—–-]?\s*\d{4}-\d{2}-\d{2}\s*$", "", meeting_title).strip()
    stem = _clean_component(clean_title, fallback="reunion")
    prefix = "" if stem.startswith(today) else f"{today} "
    path = directory / f"{prefix}{stem}.md"

    sections = [
        _dump_frontmatter(
            {
                "proyecto": project_name,
                "tipo": "reunion",
                "fecha": today,
                "titulo": meeting_title,
            }
        ),
        f"# {meeting_title}\n",
    ]
    if project.summary:
        sections.append(f"## Resumen\n\n{project.summary}\n")
    if project.action_items:
        sections.append("## Tareas\n\n" + "\n".join(_task_line(item) for item in project.action_items) + "\n")
    if project.design_feedback:
        sections.append("## Feedback\n\n" + "\n".join(f"- {line}" for line in project.design_feedback) + "\n")

    path.write_text("\n".join(sections), encoding="utf-8")
    return path


def _insert_into_section(text: str, header: str, new_lines: list[str]) -> str:
    """Agrega `new_lines` al final de la sección `header` (crea la sección si falta)."""
    if not new_lines:
        return text
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == header)
    except StopIteration:
        return text.rstrip("\n") + f"\n\n{header}\n\n" + "\n".join(new_lines) + "\n"

    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines[insert_at:insert_at] = new_lines
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def add_capture(
    project_name: str,
    user_text: str,
    action_items: Optional[list[ActionItem]] = None,
    design_feedback: Optional[list[str]] = None,
    general_notes: Optional[list[str]] = None,
) -> Path:
    """Suma tareas/notas/feedback al archivo de capturas del proyecto (una sola
    por proyecto). Si ya existe, agrega a las secciones; si no, lo crea."""
    directory = _project_dir(project_name)
    project = directory.name
    path = directory / CAPTURE_FILE
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = (
            _dump_frontmatter({"proyecto": project, "tipo": "capturas"})
            + f"\n# {project} — capturas\n\n## Tareas\n\n## Notas\n\n## Feedback\n"
        )

    stamp = datetime.now().strftime("%Y-%m-%d")
    notes = list(general_notes or [])
    if user_text.strip() and not action_items and not design_feedback and not notes:
        notes.append(user_text.strip())

    text = _insert_into_section(text, "## Tareas", [_task_line(item) for item in action_items or []])
    text = _insert_into_section(text, "## Notas", [f"- {stamp}: {note}" for note in notes])
    text = _insert_into_section(
        text, "## Feedback", [f"- {stamp}: {line}" for line in design_feedback or []]
    )

    path.write_text(text, encoding="utf-8")
    return path


def mark_completed(project_name: str, completed_tasks: list[str]) -> list[Path]:
    """Marca `- [ ]` como `- [x]` en los .md del proyecto. Devuelve los cambiados."""
    directory = vault_root() / resolve_project(project_name)
    if not completed_tasks or not directory.is_dir():
        return []

    needles = [task.strip().lower() for task in completed_tasks if task.strip()]
    changed: list[Path] = []
    for path in note_files(directory):
        original = path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        touched = False
        for index, line in enumerate(lines):
            match = _CHECKBOX.match(line.rstrip("\n"))
            if not match or match.group(2) != " ":
                continue
            if any(needle in line.lower() for needle in needles):
                newline = "\n" if line.endswith("\n") else ""
                lines[index] = f"{match.group(1)}[x]{match.group(3)}{newline}"
                touched = True
        if touched:
            path.write_text("".join(lines), encoding="utf-8")
            changed.append(path)
    return changed


# --------------------------------------------------------------------------- #
# Indexado en el vector store
# --------------------------------------------------------------------------- #


def _derive_status(text: str) -> str:
    boxes = [match.group(2).lower() for line in text.splitlines() if (match := _CHECKBOX.match(line))]
    return "completed" if boxes and all(box == "x" for box in boxes) else "pending"


def index_file(path: Path) -> None:
    """(Re)indexa un .md del vault. Sin GEMINI_API_KEY no hace nada."""
    from .integrations import collection, gemini

    if gemini is None:
        return
    path = Path(path)
    if not path.is_file() or _is_hidden(path, vault_root()):
        return

    text = path.read_text(encoding="utf-8")
    fields, body = split_frontmatter(text)
    project = fields.get("proyecto") or _project_from_path(path)
    title = fields.get("titulo") or path.stem
    embed_input = f"{title}\n{project}\n\n{body}".strip() or text

    embedding = gemini.models.embed_content(model=EMBED_MODEL, contents=embed_input)
    collection.add(
        ids=[relative_id(path)],
        embeddings=[embedding.embeddings[0].values],
        documents=[body.strip() or text],
        metadatas=[
            {
                "project": project,
                "status": _derive_status(text),
                "source": "vault",
                "path": str(path),
                "mtime": path.stat().st_mtime,
                "created_at": fields.get("fecha") or datetime.now().isoformat(),
            }
        ],
    )


def reconcile() -> None:
    """Sincroniza el índice con el vault: reindexa lo cambiado, borra lo que ya no está."""
    from .integrations import collection, gemini

    if gemini is None:
        return
    root = vault_root()
    if not root.is_dir():
        return

    on_disk = {relative_id(path): path for path in note_files(root)}
    stored = collection.get(where={"source": "vault"})
    stored_mtime = {
        doc_id: metadata.get("mtime", 0.0)
        for doc_id, metadata in zip(stored["ids"], stored["metadatas"])
    }

    for doc_id, path in on_disk.items():
        try:
            if doc_id not in stored_mtime or path.stat().st_mtime > stored_mtime[doc_id] + 1e-3:
                index_file(path)
        except Exception as error:
            print(f"[Vault] no pude indexar {path}: {error}")

    missing = [doc_id for doc_id in stored_mtime if doc_id not in on_disk]
    if missing:
        collection.delete(missing)
        print(f"[Vault] saqué del índice {len(missing)} nota(s) borrada(s)")


async def watch_loop() -> None:
    """Tarea de fondo: reconcilia el vault con el índice cada pocos segundos."""
    vault_root().mkdir(parents=True, exist_ok=True)
    print(f"[Vault] observando {vault_root()} cada {VAULT_WATCH_INTERVAL_SECONDS}s")
    while True:
        try:
            await asyncio.to_thread(reconcile)
        except Exception as error:
            print(f"[Vault] watcher: {error}")
        await asyncio.sleep(VAULT_WATCH_INTERVAL_SECONDS)
