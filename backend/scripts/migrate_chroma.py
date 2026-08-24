"""Migra la memoria de ChromaDB al almacén SQLite.

ChromaDB guarda los documentos y su metadata en `chroma.sqlite3`, pero los
vectores viven en los índices binarios de hnswlib. En vez de intentar leer ese
formato, los documentos se vuelven a embeber con Gemini: son pocos y así el
resultado queda consistente con el modelo que usa el backend hoy.

Uso (desde `backend/`, con el venv activado y GEMINI_API_KEY en el .env):

    python scripts/migrate_chroma.py            # migra
    python scripts/migrate_chroma.py --dry-run  # sólo muestra qué haría
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import CHROMA_PATH, VECTOR_STORE_PATH  # noqa: E402

DOCUMENT_KEY = "chroma:document"


def read_chroma(database: Path) -> list[tuple[str, str, dict]]:
    """Devuelve (id, documento, metadata) de cada registro de la base vieja."""
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT em.id AS row_id, e.embedding_id AS embedding_id,"
            "       em.key, em.string_value, em.int_value, em.float_value, em.bool_value"
            " FROM embedding_metadata em"
            " JOIN embeddings e ON e.id = em.id"
        ).fetchall()
    except sqlite3.OperationalError as error:
        raise SystemExit(f"No pude leer {database}: {error}")
    finally:
        connection.close()

    grouped: dict[str, dict] = {}
    for row in rows:
        entry = grouped.setdefault(row["embedding_id"], {})
        value = next(
            (
                row[column]
                for column in ("string_value", "int_value", "float_value", "bool_value")
                if row[column] is not None
            ),
            None,
        )
        entry[row["key"]] = value

    records = []
    for embedding_id, fields in grouped.items():
        document = fields.pop(DOCUMENT_KEY, None)
        if not document:
            continue
        metadata = {key: value for key, value in fields.items() if not key.startswith("chroma:")}
        records.append((embedding_id, document, metadata))
    return records


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    database = Path(CHROMA_PATH) / "chroma.sqlite3"
    if not database.is_file():
        print(f"No hay base de ChromaDB en {database}; nada que migrar.")
        return 0

    records = read_chroma(database)
    if not records:
        print("La base de ChromaDB no tiene documentos.")
        return 0

    print(f"Encontre {len(records)} documentos en {database}")
    for _, document, metadata in records[:3]:
        preview = " ".join(document.split())[:70]
        print(f"  - [{metadata.get('project', 'sin proyecto')}] {preview}...")
    if len(records) > 3:
        print(f"  ... y {len(records) - 3} mas")

    if dry_run:
        print(f"\n--dry-run: no escribi nada. El destino seria {VECTOR_STORE_PATH}")
        return 0

    # Se importa tarde para no pedir GEMINI_API_KEY en el --dry-run.
    from app.integrations import collection, gemini

    if collection.count() > 0:
        print(f"\nOjo: {VECTOR_STORE_PATH} ya tiene {collection.count()} documentos.")
        if input("Los ids que coincidan se van a sobrescribir. Sigo? [s/N] ").strip().lower() != "s":
            print("Cancelado.")
            return 1

    print("\nRe-embebiendo con Gemini...")
    migrated = 0
    for embedding_id, document, metadata in records:
        try:
            embedding = gemini.models.embed_content(
                model="gemini-embedding-2", contents=document
            )
            collection.add(
                ids=[embedding_id],
                embeddings=[embedding.embeddings[0].values],
                documents=[document],
                metadatas=[metadata],
            )
            migrated += 1
            print(f"  {migrated}/{len(records)}", end="\r")
        except Exception as error:
            print(f"\n  ! Falle con {embedding_id}: {error}")

    print(f"\nListo: {migrated}/{len(records)} documentos en {VECTOR_STORE_PATH}")
    print("La carpeta chroma_db/ se puede borrar cuando lo verifiques.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
