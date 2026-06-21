"""
Upload local shorts images to Sanity and create/update shorts documents.

Prerequisites:
  - Place images in images/shorts/ (jpg, jpeg, png, webp)
  - Set SANITY_API_TOKEN, SANITY_PROJECT_ID, SANITY_DATASET in .env

Usage:
  python scripts/sync_shorts_to_sanity.py
  python scripts/sync_shorts_to_sanity.py --images-dir images/shorts
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sanity_client import (  # noqa: E402
    SanityError,
    sanity_mutate,
    sanity_query,
    upload_image_asset,
)

SHORTS_DIR = ROOT / "images" / "shorts"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def slug_from_filename(filename: str) -> str:
    stem = Path(filename).stem.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return slug or "short"


def location_name_from_filename(filename: str) -> str:
    return Path(filename).stem.replace("-", " ").replace("_", " ").title()


def existing_short_slugs() -> set[str]:
    rows = sanity_query(
        """
        *[_type == "shorts"]{
          "slug": string::split(_id, ".")[-1]
        }
        """
    )
    return {row["slug"] for row in rows if row.get("slug")}


def sync_short(image_path: Path, *, skip_existing: bool, existing: set[str]) -> bool:
    slug = slug_from_filename(image_path.name)
    if skip_existing and slug in existing:
        print(f"Skipping existing: shorts.{slug}")
        return False

    location_name = location_name_from_filename(image_path.name)
    asset_id = upload_image_asset(str(image_path), label=image_path.name)

    sanity_mutate(
        [
            {
                "createOrReplace": {
                    "_id": f"shorts.{slug}",
                    "_type": "shorts",
                    "locationName": location_name,
                    "mainImage": {
                        "_type": "image",
                        "asset": {"_type": "reference", "_ref": asset_id},
                    },
                    "processed": False,
                }
            }
        ]
    )
    print(f"Created record for: {location_name} (shorts.{slug})")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync shorts images to Sanity CMS.")
    parser.add_argument("--images-dir", type=Path, default=SHORTS_DIR)
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip shorts documents that already exist in Sanity.",
    )
    args = parser.parse_args()

    if not args.images_dir.exists():
        raise SystemExit(
            f"{args.images_dir} not found. Create it and add shorts images first."
        )

    image_paths = sorted(
        path
        for path in args.images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not image_paths:
        raise SystemExit(f"No images found in {args.images_dir}")

    existing = existing_short_slugs() if args.skip_existing else set()
    synced = 0

    for image_path in image_paths:
        try:
            if sync_short(image_path, skip_existing=args.skip_existing, existing=existing):
                synced += 1
        except SanityError as exc:
            print(f"Failed syncing {image_path.name}: {exc}")
            raise SystemExit(1) from exc

    print(f"Sanity shorts sync completed ({synced} new/updated records).")


if __name__ == "__main__":
    main()
