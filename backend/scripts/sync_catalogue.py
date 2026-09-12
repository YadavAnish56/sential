"""
CLI utility for synchronizing authoritative government camera catalogue into PostgreSQL.
Usage:
    python backend/scripts/sync_catalogue.py [--url URL]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure backend and root are in sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
for p in [str(ROOT_DIR), str(BACKEND_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from app.database.connection import SessionLocal
from app.services.catalog_sync import CameraCatalogSyncService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentinel.scripts.sync_catalogue")


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize CCTV cameras from catalogue into PostgreSQL.")
    parser.add_argument("--url", help="Custom catalogue URL override")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        service = CameraCatalogSyncService(db=db)
        logger.info("Initiating catalogue synchronization...")
        result = service.sync(custom_url=args.url)

        print("\n" + "=" * 60)
        print("  SENTINEL CCTV CATALOGUE SYNCHRONIZATION REPORT")
        print("=" * 60)
        print(f"  Success:          {result.success}")
        print(f"  Status Code:      {result.status_code}")
        print(f"  Requires Auth:    {result.requires_auth}")
        if result.redirect_url:
            print(f"  Redirect URL:     {result.redirect_url}")
        print(f"  Message:          {result.message}")
        print(f"  Source URL:       {result.source_url}")
        print("-" * 60)
        print(f"  Total Received:   {result.stats.total_received}")
        print(f"  Created:          {result.stats.created}")
        print(f"  Updated:          {result.stats.updated}")
        print(f"  Unchanged:        {result.stats.unchanged}")
        print(f"  Skipped:          {result.stats.skipped}")
        print(f"  With Coordinates: {result.stats.with_coordinates}")
        print(f"  With RTSP:        {result.stats.with_rtsp}")
        print(f"  With WHEP:        {result.stats.with_whep}")
        print("=" * 60 + "\n")

        return 0 if result.success else 1
    except Exception as e:
        logger.error("Failed to run catalogue sync: %s", e, exc_info=True)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
