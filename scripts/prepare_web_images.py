"""
Convert local tour screenshots to web-safe filenames and JPEG extensions.

Usage:
  python scripts/prepare_web_images.py
  python scripts/prepare_web_images.py --source images --output images-web
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "images"
DEFAULT_OUTPUT = ROOT / "images-web"
WEB_WIDTH = 1920
WEB_QUALITY = 85


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug or "tour-image"


def prepare_images(source_dir: Path, output_dir: Path) -> dict[str, list[Path]]:
    manifest: dict[str, list[Path]] = {}

    for tour_dir in sorted(source_dir.iterdir()):
        if not tour_dir.is_dir():
            continue

        tour_slug = slugify(tour_dir.name)
        tour_output = output_dir / tour_slug
        tour_output.mkdir(parents=True, exist_ok=True)
        manifest[tour_slug] = []

        for index, image_path in enumerate(sorted(tour_dir.glob("*.png")), start=1):
            output_name = f"{tour_slug}-{index:02d}.jpg"
            output_path = tour_output / output_name

            with Image.open(image_path) as img:
                img = img.convert("RGB")
                if img.width > WEB_WIDTH:
                    ratio = WEB_WIDTH / img.width
                    resized = (
                        WEB_WIDTH,
                        max(1, int(img.height * ratio)),
                    )
                    img = img.resize(resized, Image.Resampling.LANCZOS)
                img.save(output_path, format="JPEG", quality=WEB_QUALITY, optimize=True)

            manifest[tour_slug].append(output_path)
            print(f"Prepared {output_path.relative_to(ROOT)}")

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare web-friendly tour images.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source folder not found: {args.source}")

    args.output.mkdir(parents=True, exist_ok=True)
    manifest = prepare_images(args.source, args.output)
    total = sum(len(paths) for paths in manifest.values())
    print(f"Done. {len(manifest)} tours, {total} web images in {args.output}")


if __name__ == "__main__":
    main()
