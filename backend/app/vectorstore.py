"""Almacén de vectores mínimo sobre SQLite.

Reemplaza a ChromaDB, que arrastraba `onnxruntime` (sin wheels para ARM de 32
bits, así que no instala en la Raspberry Pi) además de grpcio, kubernetes y
opentelemetry. Los embeddings los genera Gemini, así que de Chroma sólo se usaba
el almacenamiento y la búsqueda por similitud: eso es lo que hay acá.

La interfaz imita la de una colección de Chroma —mismos nombres y mismas formas
de retorno— para que el resto del backend no tenga que cambiar.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id         TEXT PRIMARY KEY,
    document   TEXT NOT NULL,
    metadata   TEXT NOT NULL,
    embedding  BLOB NOT NULL,
    dimensions INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


class UnsupportedFilter(ValueError):
    """El filtro `where` usa un operador que este almacén no implementa."""


def _matches(metadata: dict, where: Optional[dict]) -> bool:
    """Soporta `{campo: valor}` y `{campo: {"$eq"|"$ne": valor}}`, que es lo que usa el backend."""
    if not where:
        return True
    for field, condition in where.items():
        value = metadata.get(field)
        if isinstance(condition, dict):
            for operator, expected in condition.items():
                if operator == "$eq" and value != expected:
                    return False
                if operator == "$ne" and value == expected:
                    return False
                if operator not in ("$eq", "$ne"):
                    raise UnsupportedFilter(f"Operador no soportado: {operator}")
        elif value != condition:
            return False
    return True


class Collection:
    """Colección persistente de documentos con su embedding."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        # Una conexión por operación: FastAPI corre los endpoints sync en un
        # threadpool y SQLite no comparte conexiones entre hilos.
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # ----------------------------------------------------------------- #

    def add(
        self,
        ids: list[str],
        embeddings: Iterable[Iterable[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        rows = []
        now = datetime.now().isoformat(timespec="seconds")
        for document_id, embedding, document, metadata in zip(ids, embeddings, documents, metadatas):
            vector = np.asarray(list(embedding), dtype=np.float32)
            rows.append(
                (
                    document_id,
                    document,
                    json.dumps(metadata, ensure_ascii=False),
                    vector.tobytes(),
                    int(vector.size),
                    now,
                )
            )

        with self._lock, self._connect() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO documents"
                " (id, document, metadata, embedding, dimensions, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def update(self, ids: list[str], metadatas: list[dict]) -> None:
        with self._lock, self._connect() as connection:
            connection.executemany(
                "UPDATE documents SET metadata = ? WHERE id = ?",
                [
                    (json.dumps(metadata, ensure_ascii=False), document_id)
                    for document_id, metadata in zip(ids, metadatas)
                ],
            )

    def count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def get(self, where: Optional[dict] = None) -> dict[str, list[Any]]:
        ids, documents, metadatas = [], [], []
        with self._connect() as connection:
            for row in connection.execute(
                "SELECT id, document, metadata FROM documents ORDER BY created_at"
            ):
                metadata = json.loads(row["metadata"])
                if _matches(metadata, where):
                    ids.append(row["id"])
                    documents.append(row["document"])
                    metadatas.append(metadata)
        return {"ids": ids, "documents": documents, "metadatas": metadatas}

    def query(
        self,
        query_embeddings: Iterable[Iterable[float]],
        where: Optional[dict] = None,
        n_results: int = 2,
    ) -> dict[str, list[list[Any]]]:
        """Devuelve los n documentos más parecidos por coseno, en el formato de Chroma."""
        result: dict[str, list[list[Any]]] = {
            "ids": [],
            "documents": [],
            "metadatas": [],
            "distances": [],
        }

        candidates = []
        with self._connect() as connection:
            for row in connection.execute(
                "SELECT id, document, metadata, embedding, dimensions FROM documents"
            ):
                metadata = json.loads(row["metadata"])
                if _matches(metadata, where):
                    candidates.append((row, metadata))

        for query_embedding in query_embeddings:
            query_vector = np.asarray(list(query_embedding), dtype=np.float32)
            query_norm = np.linalg.norm(query_vector)

            scored = []
            for row, metadata in candidates:
                if row["dimensions"] != query_vector.size:
                    continue  # embedding de otro modelo: no es comparable
                vector = np.frombuffer(row["embedding"], dtype=np.float32)
                denominator = query_norm * np.linalg.norm(vector)
                similarity = 0.0 if denominator == 0 else float(query_vector @ vector / denominator)
                scored.append((1.0 - similarity, row, metadata))

            scored.sort(key=lambda item: item[0])
            top = scored[:n_results]
            result["ids"].append([row["id"] for _, row, _ in top])
            result["documents"].append([row["document"] for _, row, _ in top])
            result["metadatas"].append([metadata for _, _, metadata in top])
            result["distances"].append([distance for distance, _, _ in top])

        return result

    def delete_all(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM documents")
