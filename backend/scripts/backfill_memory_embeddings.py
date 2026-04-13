"""
One-shot backfill: embed all henry_memory rows that don't have an embedding yet.

USAGE
-----
From the backend/ directory on a machine with DATABASE_URL + VOYAGE_API_KEY
configured (Railway shell, or locally with a tunnel):

    python -m scripts.backfill_memory_embeddings
    python -m scripts.backfill_memory_embeddings --batch-size 32 --dry-run
    python -m scripts.backfill_memory_embeddings --limit 100

FLAGS
-----
  --batch-size N   Voyage batch size (default 32). Voyage allows up to 128.
  --limit N        Stop after N memories (default: all). Useful for canary runs.
  --dry-run        Fetch + embed but don't write. Verifies API works.
  --model NAME     Override embedding model (default: config.EMBEDDING_MODEL).
  --force          Re-embed rows that already have an embedding. Use when
                   changing models. Without this flag, existing embeddings
                   are preserved.

Idempotent: re-running skips already-embedded rows (unless --force).
Safe to interrupt: commits after each batch, so a killed process resumes
from the next unembedded row on retry.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("backfill_embeddings")


async def run(
    batch_size: int = 32,
    limit: Optional[int] = None,
    dry_run: bool = False,
    force: bool = False,
    model_override: Optional[str] = None,
) -> int:
    from sqlalchemy import select, update, or_
    from app.database import async_session
    from app.models import HenryMemory
    from app.services.embeddings import get_embedding_provider

    provider = get_embedding_provider()
    if provider is None:
        logger.error(
            "Embedding provider unavailable — set VOYAGE_API_KEY and "
            "EMBEDDING_ENABLED=true. Aborting."
        )
        return 1

    if model_override:
        provider.model_name = model_override
        logger.info(f"Model override active: {model_override}")

    model_name = provider.model_name
    logger.info(f"Using provider model={model_name} dims={provider.dimensions}")
    if dry_run:
        logger.info("DRY RUN — no database writes will be made")

    processed = 0
    skipped_empty = 0
    failed = 0

    while True:
        async with async_session() as db:
            # Fetch next unembedded (or all, if --force) batch.
            stmt = select(HenryMemory)
            if not force:
                stmt = stmt.where(
                    or_(
                        HenryMemory.embedding.is_(None),
                        HenryMemory.embedding_model != model_name,
                    )
                )
            stmt = stmt.order_by(HenryMemory.created_at.asc()).limit(batch_size)

            remaining = None
            if limit is not None:
                remaining = limit - processed
                if remaining <= 0:
                    break
                stmt = stmt.limit(min(batch_size, remaining))

            result = await db.execute(stmt)
            rows = list(result.scalars().all())

        if not rows:
            logger.info("No more memories to embed — done.")
            break

        texts = [r.content or "" for r in rows]
        # Skip empties cleanly — they shouldn't exist but guard anyway.
        embeddable_ids = [r.id for r, t in zip(rows, texts) if t.strip()]
        embeddable_texts = [t for t in texts if t.strip()]
        skipped_this_batch = len(rows) - len(embeddable_texts)
        skipped_empty += skipped_this_batch
        if skipped_this_batch:
            logger.warning(f"Skipping {skipped_this_batch} empty-content rows in batch")

        if not embeddable_texts:
            processed += len(rows)
            continue

        try:
            vectors = await provider.embed_batch(embeddable_texts)
        except Exception as e:
            logger.error(f"Batch embed failed: {e}")
            failed += len(embeddable_texts)
            # Don't loop forever on total provider failure
            if failed > 3 * batch_size:
                logger.error("Too many consecutive failures — aborting")
                return 2
            continue

        # Write back one-by-one so a single bad row doesn't poison the batch.
        updates_written = 0
        async with async_session() as db:
            for mid, vec in zip(embeddable_ids, vectors):
                if vec is None:
                    failed += 1
                    continue
                if dry_run:
                    updates_written += 1
                    continue
                await db.execute(
                    update(HenryMemory)
                    .where(HenryMemory.id == mid)
                    .values(embedding=vec, embedding_model=model_name)
                )
                updates_written += 1
            if not dry_run:
                await db.commit()

        processed += len(rows)
        logger.info(
            f"Batch done: processed={processed} "
            f"written={updates_written} empty={skipped_empty} failed={failed}"
        )

    logger.info(
        f"Finished. processed={processed} empty={skipped_empty} failed={failed}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--model", type=str, default=None, dest="model")
    args = ap.parse_args()

    return asyncio.run(
        run(
            batch_size=args.batch_size,
            limit=args.limit,
            dry_run=args.dry_run,
            force=args.force,
            model_override=args.model,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
