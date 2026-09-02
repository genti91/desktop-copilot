"""Notas como archivos Markdown en un vault de Obsidian.

El vault es la única fuente de verdad: cada nota o reunión es un `.md` con un
frontmatter mínimo. El vector store (RAG) pasa a ser un índice derivado y
reconstruible —si se borra, se rearma leyendo el vault—.

Dos caminos lo mantienen al día:

- **Escritura del backend** (`write_meeting_note`, `write_capture_note`,
  `mark_completed`): indexan el archivo en el acto, sin esperar al watcher.
- **`watch_loop`**: un poll cada pocos segundos que reindexa lo que cambió por
  fuera del backend (lo que editás en Obsidian) y saca del índice lo borrado.
  Es un poll y no `watchdog` a propósito: sin dependencias nuevas y alcanza de
  sobra para un vault personal.

El frontmatter se parsea y se emite a mano (pares `clave: valor` planos) para no
sumar PyYAML, igual que el resto del backend evita dependencias pesadas.
"""

from __future__ import annotations

import asyncio
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


def _slug(text: str, *, fallback: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:60] or fallback


def _project_dir(project_name: str) -> Path:
    directory = vault_root() / _clean_component(project_name, fallback="General")
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
    # El modelo a veces mete la fecha en el título; no la repetimos en el nombre.
    clean_title = re.sub(r"\s*[—–-]?\s*\d{4}-\d{2}-\d{2}\s*$", "", meeting_title).strip()
    stem = _clean_component(clean_title, fallback="reunion")
    prefix = "" if stem.startswith(today) else f"{today} "
    path = _project_dir(project.project_name) / f"{prefix}{stem}.md"

    sections = [
        _dump_frontmatter(
            {
                "proyecto": project.project_name,
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


def write_capture_note(
    project_name: str,
    user_text: str,
    action_items: Optional[list[ActionItem]] = None,
    design_feedback: Optional[list[str]] = None,
    general_notes: Optional[list[str]] = None,
) -> Path:
    now = datetime.now()
    stem = f"{now.strftime('%Y-%m-%dT%H-%M-%S')} nota"
    directory = _project_dir(project_name)
    path = directory / f"{stem}.md"
    bump = 2
    while path.exists():  # varias capturas en el mismo segundo
        path = directory / f"{stem} ({bump}).md"
        bump += 1

    sections = [
        _dump_frontmatter(
            {"proyecto": project_name, "tipo": "nota", "fecha": now.strftime("%Y-%m-%d")}
        ),
        f"{user_text.strip()}\n" if user_text.strip() else "",
    ]
    for note in general_notes or []:
        sections.append(f"- {note}")
    if action_items:
        sections.append("\n## Tareas\n\n" + "\n".join(_task_line(item) for item in action_items) + "\n")
    if design_feedback:
        sections.append("\n## Feedback\n\n" + "\n".join(f"- {line}" for line in design_feedback) + "\n")

    path.write_text("\n".join(part for part in sections if part), encoding="utf-8")
    return path


def mark_completed(project_name: str, completed_tasks: list[str]) -> list[Path]:
    """Marca `- [ ]` como `- [x]` en los .md del proyecto. Devuelve los cambiados."""
    directory = vault_root() / _clean_component(project_name, fallback="General")
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
