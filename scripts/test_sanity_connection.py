"""Verify Sanity API credentials with a read-only query."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sanity_client import fetch_all_tours, sanity_query  # noqa: E402


def main() -> int:
    tours = fetch_all_tours()
    print(f"OK: fetched {len(tours)} tour(s)")
    if tours:
        print(f"Sample: {tours[0].get('title')} ({tours[0].get('slug')})")

    shorts = sanity_query('count(*[_type == "shorts"])')
    print(f"OK: shorts count query returned {shorts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
