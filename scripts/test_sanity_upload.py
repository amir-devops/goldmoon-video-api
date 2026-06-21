"""Quick Sanity asset upload test."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sanity_client import upload_image_asset

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "images-web/pyramids/pyramids-01.jpg"
    aid = upload_image_asset(path, Path(path).name)
    print("OK:", aid)
