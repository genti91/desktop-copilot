"""Tests del almacén de vectores que reemplazó a ChromaDB.

Se apoyan en la forma de retorno que espera el resto del backend, porque es lo
que hace que `integrations.py`, `services.py` y `main.py` no tuvieran que cambiar.
"""

import pytest

from app.vectorstore import Collection, UnsupportedFilter


@pytest.fixture
def collection(tmp_path):
    return Collection(tmp_path / "memoria.sqlite3")


def add(collection, document_id, vector, text, **metadata):
    collection.add(
        ids=[document_id], embeddings=[vector], documents=[text], metadatas=[metadata]
    )


# --------------------------------------------------------------------------- #
# Básico
# --------------------------------------------------------------------------- #


def test_starts_empty_and_counts(collection):
    assert collection.count() == 0
    add(collection, "a", [1.0, 0.0], "hola", project="X")
    add(collection, "b", [0.0, 1.0], "chau", project="Y")
    assert collection.count() == 2


def test_survives_reopening(tmp_path):
    path = tmp_path / "memoria.sqlite3"
    add(Collection(path), "a", [1.0, 0.0], "persistido", project="X")
    assert Collection(path).count() == 1
    assert Collection(path).get()["documents"] == ["persistido"]


def test_same_id_replaces_instead_of_duplicating(collection):
    add(collection, "a", [1.0, 0.0], "primera", project="X")
    add(collection, "a", [0.0, 1.0], "segunda", project="X")
    assert collection.count() == 1
    assert collection.get()["documents"] == ["segunda"]


# --------------------------------------------------------------------------- #
# Búsqueda por similitud
# --------------------------------------------------------------------------- #


def test_query_ranks_by_cosine_similarity(collection):
    add(collection, "igual", [1.0, 0.0], "identico", project="X")
    add(collection, "cerca", [0.9, 0.1], "parecido", project="X")
    add(collection, "lejos", [0.0, 1.0], "opuesto", project="X")

    results = collection.query(query_embeddings=[[1.0, 0.0]], n_results=3)

    assert results["ids"][0] == ["igual", "cerca", "lejos"]
    assert results["documents"][0][0] == "identico"
    # Distancia = 1 - coseno: 0 para el identico, 1 para el ortogonal.
    assert results["distances"][0][0] == pytest.approx(0.0, abs=1e-6)
    assert results["distances"][0][2] == pytest.approx(1.0, abs=1e-6)


def test_query_respects_n_results(collection):
    for index in range(5):
        add(collection, f"doc-{index}", [1.0, index / 10], f"texto {index}", project="X")
    assert len(collection.query(query_embeddings=[[1.0, 0.0]], n_results=2)["documents"][0]) == 2


def test_query_on_empty_store_returns_empty_lists(collection):
    results = collection.query(query_embeddings=[[1.0, 0.0]], n_results=2)
    assert results["documents"] == [[]]
    assert results["ids"] == [[]]


def test_magnitude_does_not_affect_ranking(collection):
    """El coseno mira dirección: un vector largo no gana por ser largo."""
    add(collection, "corto", [1.0, 0.0], "misma direccion", project="X")
    add(collection, "largo", [0.0, 50.0], "otra direccion", project="X")
    assert collection.query(query_embeddings=[[2.0, 0.0]], n_results=1)["ids"][0] == ["corto"]


def test_embeddings_of_another_size_are_ignored(collection):
    """Si cambia el modelo de embeddings, los viejos no rompen la búsqueda."""
    add(collection, "viejo", [1.0, 0.0], "otro modelo", project="X")
    add(collection, "nuevo", [1.0, 0.0, 0.0], "modelo actual", project="X")

    results = collection.query(query_embeddings=[[1.0, 0.0, 0.0]], n_results=5)
    assert results["ids"][0] == ["nuevo"]


# --------------------------------------------------------------------------- #
# Filtros, tal como los usa el backend
# --------------------------------------------------------------------------- #


def test_get_filters_by_field(collection):
    add(collection, "a", [1.0, 0.0], "de acme", project="Acme")
    add(collection, "b", [0.0, 1.0], "de otro", project="Otro")

    results = collection.get(where={"project": "Acme"})
    assert results["ids"] == ["a"]
    assert results["documents"] == ["de acme"]
    assert results["metadatas"] == [{"project": "Acme"}]


def test_query_can_exclude_by_metadata(collection):
    """El almacén soporta filtros `$ne` sobre metadata (no lo usa el backend hoy)."""
    add(collection, "pendiente", [1.0, 0.0], "sigue abierta", status="pending")
    add(collection, "hecha", [1.0, 0.0], "ya se hizo", status="completed")

    results = collection.query(
        query_embeddings=[[1.0, 0.0]], where={"status": {"$ne": "completed"}}, n_results=5
    )
    assert results["ids"][0] == ["pendiente"]


def test_unsupported_operators_fail_loudly(collection):
    add(collection, "a", [1.0, 0.0], "algo", value=5)
    with pytest.raises(UnsupportedFilter):
        collection.get(where={"value": {"$gt": 3}})


# --------------------------------------------------------------------------- #
# Actualización de metadata
# --------------------------------------------------------------------------- #


def test_update_replaces_metadata_and_keeps_the_document(collection):
    add(collection, "a", [1.0, 0.0], "una tarea", project="X", status="pending")

    metadata = collection.get()["metadatas"][0]
    collection.update(ids=["a"], metadatas=[{**metadata, "status": "completed"}])

    results = collection.get()
    assert results["metadatas"] == [{"project": "X", "status": "completed"}]
    assert results["documents"] == ["una tarea"]


def test_updating_a_missing_id_is_a_noop(collection):
    collection.update(ids=["no-existe"], metadatas=[{"status": "completed"}])
    assert collection.count() == 0


def test_delete_removes_only_the_given_ids(collection):
    add(collection, "a", [1.0, 0.0], "una", project="X")
    add(collection, "b", [0.0, 1.0], "otra", project="X")

    collection.delete(["a", "no-existe"])

    assert collection.get()["ids"] == ["b"]
    collection.delete([])  # lista vacía: no explota
    assert collection.count() == 1


def test_accents_and_unicode_round_trip(collection):
    add(collection, "a", [1.0, 0.0], "Diseño de la campaña ñandú", project="Ñoño")
    results = collection.get()
    assert results["documents"] == ["Diseño de la campaña ñandú"]
    assert results["metadatas"][0]["project"] == "Ñoño"
