"""Vault de Obsidian: escritura de notas, marcado de tareas y sincronización del índice.

No toca Gemini: para `index_file`/`reconcile` se inyecta un `app.integrations`
falso (un `Collection` real sobre tmp y un embebedor de juguete).
"""

import sys
import types

import pytest

from app.models import ActionItem, ProjectSummary
from app.vectorstore import Collection
import app.vault as vault


@pytest.fixture
def vault_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    return tmp_path / "vault"


class _FakeEmbeddings:
    def __init__(self):
        self.embeddings = [types.SimpleNamespace(values=[1.0, 0.0, 0.0])]


class _FakeGemini:
    class models:
        @staticmethod
        def embed_content(model, contents):
            return _FakeEmbeddings()


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    """Inyecta un app.integrations falso y devuelve el Collection para inspeccionar."""
    collection = Collection(tmp_path / "mem.sqlite3")
    fake = types.ModuleType("app.integrations")
    fake.collection = collection
    fake.gemini = _FakeGemini()
    monkeypatch.setitem(sys.modules, "app.integrations", fake)
    return collection


# --------------------------------------------------------------------------- #
# Escritura de notas
# --------------------------------------------------------------------------- #


def test_meeting_note_has_frontmatter_and_checkboxes(vault_dir):
    project = ProjectSummary(
        project_name="Acme",
        summary="Revisamos la marca.",
        action_items=[ActionItem(task="Rehacer el logo", due_date="2026-09-05")],
        design_feedback=["El azul quedó muy saturado"],
    )
    path = vault.write_meeting_note("Reunión de Feedback", project)

    assert path.parent == vault_dir / "Acme"
    text = path.read_text(encoding="utf-8")
    fields, body = vault.split_frontmatter(text)
    assert fields["proyecto"] == "Acme"
    assert fields["tipo"] == "reunion"
    assert "- [ ] Rehacer el logo (entrega: 2026-09-05)" in body
    assert "El azul quedó muy saturado" in body


def test_capture_note_lands_in_project_folder(vault_dir):
    path = vault.write_capture_note(
        "Nike",
        "acordate de mandar la propuesta",
        action_items=[ActionItem(task="Mandar propuesta", due_date="")],
    )
    assert path.parent == vault_dir / "Nike"
    body = vault.split_frontmatter(path.read_text(encoding="utf-8"))[1]
    assert "acordate de mandar la propuesta" in body
    assert "- [ ] Mandar propuesta" in body


def test_project_name_with_slashes_stays_one_folder(vault_dir):
    path = vault.write_capture_note("cli/ente raro", "hola")
    assert path.parent.parent == vault_dir  # una sola carpeta, no anidada


# --------------------------------------------------------------------------- #
# Marcado de tareas completas
# --------------------------------------------------------------------------- #


def test_mark_completed_flips_only_matching_open_boxes(vault_dir):
    project = ProjectSummary(
        project_name="Acme",
        summary="",
        action_items=[
            ActionItem(task="Rehacer el logo", due_date=""),
            ActionItem(task="Elegir tipografía", due_date=""),
        ],
        design_feedback=[],
    )
    path = vault.write_meeting_note("Kickoff", project)

    changed = vault.mark_completed("Acme", ["rehacer el logo"])

    assert changed == [path]
    text = path.read_text(encoding="utf-8")
    assert "- [x] Rehacer el logo" in text
    assert "- [ ] Elegir tipografía" in text


def test_mark_completed_ignores_unknown_project(vault_dir):
    assert vault.mark_completed("NoExiste", ["algo"]) == []


def test_derive_status_completed_only_when_all_boxes_checked():
    assert vault._derive_status("- [ ] a\n- [x] b") == "pending"
    assert vault._derive_status("- [x] a\n- [x] b") == "completed"
    assert vault._derive_status("sin tareas") == "pending"


# --------------------------------------------------------------------------- #
# Índice RAG derivado del vault
# --------------------------------------------------------------------------- #


def test_index_file_upserts_by_relative_path(vault_dir, indexed):
    path = vault.write_capture_note("Acme", "primera versión")
    vault.index_file(path)
    vault.index_file(path)  # segunda pasada: reemplaza, no duplica

    assert indexed.count() == 1
    stored = indexed.get()
    assert stored["ids"][0] == "Acme/" + path.name
    assert stored["metadatas"][0]["project"] == "Acme"
    assert stored["metadatas"][0]["source"] == "vault"


def test_reconcile_indexes_new_and_drops_deleted(vault_dir, indexed):
    a = vault.write_capture_note("Acme", "nota a")
    b = vault.write_capture_note("Acme", "nota b")
    vault.reconcile()
    assert indexed.count() == 2

    b.unlink()
    vault.reconcile()

    stored = indexed.get()
    assert stored["ids"] == ["Acme/" + a.name]


def test_reconcile_reindexes_edited_file(vault_dir, indexed):
    path = vault.write_capture_note("Acme", "texto viejo")
    vault.reconcile()

    # Edición "desde Obsidian": nuevo contenido y mtime más nuevo.
    import os
    import time

    path.write_text("---\nproyecto: Acme\n---\n\ntexto nuevo", encoding="utf-8")
    future = time.time() + 10
    os.utime(path, (future, future))
    vault.reconcile()

    assert indexed.count() == 1
    assert "texto nuevo" in indexed.get()["documents"][0]
