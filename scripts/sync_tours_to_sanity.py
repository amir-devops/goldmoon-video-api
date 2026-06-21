"""
Upload web-ready tour images to Sanity and create/update tour documents.

Prerequisites:
  1. python scripts/prepare_web_images.py
  2. Set SANITY_API_TOKEN, SANITY_PROJECT_ID, SANITY_DATASET in .env

Usage:
  python scripts/sync_tours_to_sanity.py
  python scripts/sync_tours_to_sanity.py --max-images 4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sanity_client import SanityError, sanity_mutate, upload_image_asset  # noqa: E402

WEB_IMAGES_DIR = ROOT / "images-web"
TOUR_TITLES = {
    "abo-simble": "Abu Simbel Temple Tour",
    "al-alamein-military-museum": "El Alamein Military Museum",
    "ali-camp": "Ali Khaled Camp Siwa Oasis",
    "cleopatra-bath": "Cleopatra Bath Siwa",
    "dahshur-memphis-saqqara": "Dahshur Memphis and Saqqara",
    "edfu-kom-ombo": "Edfu and Kom Ombo Temples",
    "egypt-overland-tour-egypt-archaeological-adventure": "Egypt Archaeological Adventure",
    "grand-egyptian-museum": "Grand Egyptian Museum",
    "hot-air-balloon": "Luxor Hot Air Balloon",
    "karnak": "Karnak Temple Luxor",
    "marsa-matruh": "Marsa Matruh Coastline",
    "oracle-tempe": "Temple of the Oracle Siwa",
    "phila-temple": "Philae Temple Aswan",
    "pyramids": "Great Pyramids of Giza",
    "ras-mohammed-tiran-island": "Ras Mohammed and Tiran Island",
    "shali-fortress-and-mountain-of-the-dead": "Shali Fortress Siwa",
    "snorkeling-excursion-at-sharm-el-sheikh": "Sharm El Sheikh Snorkeling",
    "temple-of-amun-um-ubeyda-temple": "Temple of Amun Siwa",
    "valley-of-the-kings-hatshepsut-temple": "Valley of the Kings and Hatshepsut",
}


def default_scene_text(title: str) -> tuple[str, str]:
    return (
        f"Discover {title} in luxury",
        "Book your private Egypt tour now",
    )


def sync_tour(
    tour_slug: str,
    image_paths: list[Path],
    max_images: int,
) -> None:
    title = TOUR_TITLES.get(tour_slug, tour_slug.replace("-", " ").title())
    scene_1, scene_2 = default_scene_text(title)
    selected_images = image_paths[:max_images]

    image_refs: list[dict] = []
    for image_path in selected_images:
        asset_id = upload_image_asset(str(image_path), label=image_path.name)
        image_refs.append(
            {
                "_type": "image",
                "_key": image_path.stem,
                "alt": title,
                "asset": {"_type": "reference", "_ref": asset_id},
            }
        )

    sanity_mutate(
        [
            {
                "createOrReplace": {
                    "_id": f"tour.{tour_slug}",
                    "_type": "tour",
                    "title": title,
                    "slug": {"_type": "slug", "current": tour_slug},
                    "text_scene_1": scene_1,
                    "text_scene_2": scene_2,
                    "bg_music": "cinematic_epic",
                    "style": "desert_safari",
                    "images": image_refs,
                }
            }
        ]
    )
    print(f"Synced tour '{tour_slug}' with {len(image_refs)} images.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync tour images to Sanity CMS.")
    parser.add_argument("--images-dir", type=Path, default=WEB_IMAGES_DIR)
    parser.add_argument("--max-images", type=int, default=4)
    args = parser.parse_args()

    if not args.images_dir.exists():
        raise SystemExit(
            f"{args.images_dir} not found. Run scripts/prepare_web_images.py first."
        )

    tour_dirs = sorted(path for path in args.images_dir.iterdir() if path.is_dir())
    if not tour_dirs:
        raise SystemExit(f"No tour folders found in {args.images_dir}")

    for tour_dir in tour_dirs:
        image_paths = sorted(tour_dir.glob("*.jpg"))
        if len(image_paths) < 2:
            print(f"Skipping {tour_dir.name}: needs at least 2 images.")
            continue
        try:
            sync_tour(tour_dir.name, image_paths, args.max_images)
        except SanityError as exc:
            print(f"Failed syncing {tour_dir.name}: {exc}")
            raise SystemExit(1) from exc

    print("Sanity sync completed.")


if __name__ == "__main__":
    main()
