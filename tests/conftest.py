import os
from pathlib import Path

# render_pipeline resolves ASSETS_DIR/SOUNDS_DIR from APP_DIR at import time.
# Point it at the repo root so the real assets/ and sounds/ folders (which
# ship with the repo) are used instead of the container-only /app default.
os.environ.setdefault("APP_DIR", str(Path(__file__).resolve().parent.parent))
