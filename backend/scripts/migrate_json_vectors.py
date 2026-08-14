from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text

from backend.app.adapters.sql.database import create_engine
from backend.app.adapters.vector.json_store import JsonVectorStore
from backend.app.adapters.vector.pgvector_store import PgVectorStore
from backend.app.core.config import Settings


async def migrate_json_vectors() -> int:
    """Idempotently import the JSON backup into pgvector without modifying it."""
    settings = Settings()
    source_path = Path(settings.agent_vector_store_path)
    if not source_path.exists():
        print("pgvector migration skipped: JSON backup does not exist")
        return 0
    source = JsonVectorStore(settings.agent_vector_store_path)
    records = source.load_all_records()
    if not records:
        print("pgvector migration skipped: JSON backup contains no vectors")
        return 0

    grouped: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for chunk, vector in records:
        grouped[(chunk.document_id, chunk.document_version)].append((chunk, vector))

    engine = create_engine(settings)
    target = PgVectorStore(engine, settings.agent_vector_dimensions)
    try:
        async with engine.connect() as connection:
            completed = (
                await connection.execute(
                    text(
                        "SELECT 1 FROM vector_migration_audits "
                        "WHERE migration_key = 'json-vector-store-v1'"
                    )
                )
            ).scalar_one_or_none()
        if completed is not None:
            print("pgvector migration skipped: JSON backup was already imported")
            return 0
        for (document_id, document_version), items in grouped.items():
            chunks, vectors = zip(*items, strict=True)
            await target.replace_document(document_id, document_version, chunks, vectors)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO vector_migration_audits "
                    "(migration_key, vector_count) VALUES "
                    "('json-vector-store-v1', :vector_count)"
                ),
                {"vector_count": len(records)},
            )
    finally:
        await engine.dispose()
    print(f"pgvector migration complete: {len(records)} vectors from {len(grouped)} documents")
    return len(records)


if __name__ == "__main__":
    asyncio.run(migrate_json_vectors())
