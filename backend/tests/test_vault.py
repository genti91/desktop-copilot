"""Vault de Obsidian: escritura de notas, resolución de proyecto, tareas e índice.

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


def _meeting(name, *tasks):
    return ProjectSummary(
        project_name=name,
        summary="",
        action_items=[ActionItem(task=t, due_date="") for t in tasks],
        design_feedback=[],
    )


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
    fields, body = vault.split_frontmatter(path.read_text(encoding="utf-8"))
    assert fields["proyecto"] == "Acme"
    assert fields["tipo"] == "reunion"
    assert "- [ ] Rehacer el logo (entrega: 2026-09-05)" in body
    assert "El azul quedó muy saturado" in body


def test_meeting_filename_does_not_double_the_date(vault_dir):
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    path = vault.write_meeting_note(f"Reunión con el estudio — {today}", _meeting("Acme"))
    assert path.name == f"{today} Reunión con el estudio.md"


def test_project_name_with_slashes_stays_one_folder(vault_dir):
    path = vault.add_capture("cli/ente raro", "hola")
    assert path.parent.parent == vault_dir  # una sola carpeta, no anidada


# --------------------------------------------------------------------------- #
# Capturas: se acumulan en un solo archivo por proyecto
# --------------------------------------------------------------------------- #


def test_add_capture_creates_one_file_and_appends(vault_dir):
    first = vault.add_capture("Nike", "", action_items=[ActionItem(task="Mandar propuesta", due_date="")])
    second = vault.add_capture(
        "Nike", "", action_items=[ActionItem(task="Cambiar el tamaño del logo", due_date="")]
    )

    assert first == second == vault_dir / "Nike" / "Capturas.md"
    text = first.read_text(encoding="utf-8")
    assert "- [ ] Mandar propuesta" in text
    assert "- [ ] Cambiar el tamaño del logo" in text
    # las dos tareas viven bajo la misma sección
    assert text.count("## Tareas") == 1


def test_add_capture_files_notes_and_feedback_in_their_sections(vault_dir):
    path = vault.add_capture(
        "Nike", "", general_notes=["revisar contrato"], design_feedback=["el gris está muy frío"]
    )
    text = path.read_text(encoding="utf-8")
    notas = text.split("## Notas")[1].split("##")[0]
    feedback = text.split("## Feedback")[1]
    assert "revisar contrato" in notas
    assert "el gris está muy frío" in feedback


def test_add_capture_uses_raw_text_only_when_nothing_was_extracted(vault_dir):
    path = vault.add_capture("Nike", "acordate de llamar al proveedor")
    assert "acordate de llamar al proveedor" in path.read_text(encoding="utf-8").split("## Notas")[1]


# --------------------------------------------------------------------------- #
# Resolución de proyecto (fuzzy)
# --------------------------------------------------------------------------- #


def test_resolve_project_reuses_a_similar_existing_folder(vault_dir):
    vault.write_meeting_note("Kickoff", _meeting("Panadería La Espiga"))

    assert vault.resolve_project("La Espiga") == "Panadería La Espiga"
    assert vault.resolve_project("la espiga") == "Panadería La Espiga"
    assert vault.resolve_project("espiga") == "Panadería La Espiga"

    path = vault.add_capture("La Espiga", "", action_items=[ActionItem(task="X", due_date="")])
    assert path == vault_dir / "Panadería La Espiga" / "Capturas.md"


def test_resolve_project_matches_a_distinctive_token(vault_dir):
    vault.write_meeting_note("Kickoff", _meeting("Estudio Contable Ferrari & Asociados"))
    assert vault.resolve_project("Ferrari") == "Estudio Contable Ferrari & Asociados"


def test_resolve_project_tolerates_typos(vault_dir):
    vault.write_meeting_note("Kickoff", _meeting("Panadería La Espiga"))
    assert vault.resolve_project("Panaderia la Spiga") == "Panadería La Espiga"
    assert vault.resolve_project("la spiga") == "Panadería La Espiga"  # forma corta con typo


def test_resolve_project_keeps_different_projects_apart(vault_dir):
    vault.write_meeting_note("Kickoff", _meeting("Nike"))
    assert vault.resolve_project("Adidas") == "Adidas"
    assert vault.resolve_project("Coca Cola") == "Coca Cola"


def test_resolve_project_on_empty_vault_returns_the_name(vault_dir):
    assert vault.resolve_project("Proyecto Nuevo") == "Proyecto Nuevo"


# --------------------------------------------------------------------------- #
# Marcado de tareas completas
# --------------------------------------------------------------------------- #


def test_mark_completed_flips_only_matching_open_boxes(vault_dir):
    path = vault.write_meeting_note("Kickoff", _meeting("Acme", "Rehacer el logo", "Elegir tipografía"))

    changed = vault.mark_completed("Acme", ["rehacer el logo"])

    assert changed == [path]
    text = path.read_text(encoding="utf-8")
    assert "- [x] Rehacer el logo" in text
    assert "- [ ] Elegir tipografía" in text


def test_mark_completed_finds_the_project_by_a_close_name(vault_dir):
    path = vault.write_meeting_note("Kickoff", _meeting("Panadería La Espiga", "Cambiar el tamaño del logo"))

    changed = vault.mark_completed("la espiga", ["cambiar el tamaño del logo"])

    assert changed == [path]
    assert "- [x] Cambiar el tamaño del logo" in path.read_text(encoding="utf-8")


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
    path = vault.add_capture("Acme", "primera versión")
    vault.index_file(path)
    vault.index_file(path)  # segunda pasada: reemplaza, no duplica

    assert indexed.count() == 1
    stored = indexed.get()
    assert stored["ids"][0] == "Acme/Capturas.md"
    assert stored["metadatas"][0]["project"] == "Acme"
    assert stored["metadatas"][0]["source"] == "vault"


def test_reconcile_indexes_new_and_drops_deleted(vault_dir, indexed):
    a = vault.write_meeting_note("Kickoff", _meeting("Acme"))
    b = vault.write_meeting_note("Segunda", _meeting("Acme"))
    vault.reconcile()
    assert indexed.count() == 2

    b.unlink()
    vault.reconcile()

    stored = indexed.get()
    assert stored["ids"] == ["Acme/" + a.name]


def test_reconcile_ignores_hidden_dirs_and_prunes_them(vault_dir, indexed):
    real = vault.add_capture("Acme", "nota real")
    stv = vault_dir / ".stversions" / "Acme"
    stv.mkdir(parents=True)
    (stv / "vieja~20260101.md").write_text("---\nproyecto: Acme\n---\n\nversion vieja", encoding="utf-8")

    vault.reconcile()
    assert indexed.get()["ids"] == ["Acme/Capturas.md"]

    indexed.add(ids=[".stversions/Acme/vieja~20260101.md"], embeddings=[[1.0, 0.0, 0.0]],
                documents=["x"], metadatas=[{"source": "vault", "mtime": 0.0}])
    vault.reconcile()
    assert indexed.get()["ids"] == ["Acme/Capturas.md"]


def test_reconcile_reindexes_edited_file(vault_dir, indexed):
    path = vault.add_capture("Acme", "texto viejo")
    vault.reconcile()

    import os
    import time

    path.write_text("---\nproyecto: Acme\n---\n\ntexto nuevo", encoding="utf-8")
    future = time.time() + 10
    os.utime(path, (future, future))
    vault.reconcile()

    assert indexed.count() == 1
    assert "texto nuevo" in indexed.get()["documents"][0]
