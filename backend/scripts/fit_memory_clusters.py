"""
Fit gaussian mixture over memory embeddings — on demand.

Normally the stats engine handles this periodically. Use this script after:
  - Running the backfill (so clusters reflect the newly-embedded memories)
  - Changing the embedding model (clusters from the old model are invalid)
  - Bulk-importing memories

USAGE
-----
From backend/ with DATABASE_URL set:

    python -m scripts.fit_memory_clusters
    python -m scripts.fit_memory_clusters --min-memories 50
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("fit_clusters")


async def run(min_memories: int) -> int:
    from app.database import async_session
    from app.services.memory_clustering import (
        fit_memory_clusters,
        invalidate_cache,
        MIN_MEMORIES_TO_FIT,
    )
    # Allow override of the module-level min via kwarg-less API. We just
    # compare here — the fit function has its own floor too.
    import app.services.memory_clustering as mc

    if min_memories != MIN_MEMORIES_TO_FIT:
        logger.info(f"Overriding MIN_MEMORIES_TO_FIT: {MIN_MEMORIES_TO_FIT} → {min_memories}")
        mc.MIN_MEMORIES_TO_FIT = min_memories

    async with async_session() as db:
        summary = await fit_memory_clusters(db)
        if summary is None:
            logger.warning("Fit skipped — too few memories or no embeddings.")
            return 1
        await db.commit()
        invalidate_cache()

    logger.info(
        f"Done. k={summary['k']} "
        f"n={summary['n_memories_fit']} "
        f"model={summary['model']} "
        f"log_likelihood={summary['log_likelihood']:.2f}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--min-memories",
        type=int,
        default=20,
        help="Skip fit if fewer than this many embedded memories exist.",
    )
    args = ap.parse_args()
    return asyncio.run(run(min_memories=args.min_memories))


if __name__ == "__main__":
    sys.exit(main())
