"""Sanity CMS client for Goldmoon tour image pipeline."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

SANITY_PROJECT_ID = os.getenv("SANITY_PROJECT_ID", "5aj2yjpb")
SANITY_DATASET = os.getenv("SANITY_DATASET", "production")
SANITY_API_VERSION = os.getenv("SANITY_API_VERSION", "2024-01-01")
SANITY_ORG_ID = os.getenv("SANITY_ORG_ID", "o12AQMZqc")
SANITY_TOKEN = os.getenv("SANITY_API_TOKEN", "")

SANITY_API_BASE = (
    f"https://{SANITY_PROJECT_ID}.api.sanity.io/v{SANITY_API_VERSION}"
)
SANITY_CDN_BASE = f"https://cdn.sanity.io/images/{SANITY_PROJECT_ID}/{SANITY_DATASET}"


class SanityError(Exception):
    pass


def _headers(write: bool = False) -> dict[str, str]:
    if not SANITY_TOKEN:
        raise SanityError(
            "SANITY_API_TOKEN is not set. Add it to your environment or .env file."
        )
    headers = {"Content-Type": "application/json"}
    headers["Authorization"] = f"Bearer {SANITY_TOKEN}"
    return headers


def sanity_query(groq: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    url = f"{SANITY_API_BASE}/data/query/{SANITY_DATASET}"
    payload: dict[str, Any] = {"query": groq}
    if params:
        payload["params"] = params
    response = requests.post(url, json=payload, headers=_headers(), timeout=30)
    if not response.ok:
        raise SanityError(f"Sanity query failed: {response.text}")
    result = response.json().get("result", [])
    return result if isinstance(result, list) else [result]


def sanity_mutate(mutations: list[dict[str, Any]]) -> dict[str, Any]:
    url = f"{SANITY_API_BASE}/data/mutate/{SANITY_DATASET}"
    response = requests.post(
        url,
        json={"mutations": mutations},
        headers=_headers(),
        timeout=60,
    )
    if not response.ok:
        raise SanityError(f"Sanity mutate failed: {response.text}")
    return response.json()


def upload_image_asset(image_path: str, label: str, retries: int = 3) -> str:
    """Upload a local image and return the Sanity asset document ID."""
    path = Path(image_path)
    suffix = path.suffix.lower()
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")

    url = (
        f"{SANITY_API_BASE}/assets/images/{SANITY_DATASET}"
        f"?filename={quote(path.name)}&label={quote(label)}"
    )
    image_bytes = path.read_bytes()
    last_error = "unknown error"

    for attempt in range(1, retries + 1):
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {SANITY_TOKEN}",
                    "Content-Type": content_type,
                },
                data=image_bytes,
                timeout=180,
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt < retries:
                continue
            raise SanityError(
                f"Sanity asset upload failed for {image_path}: {last_error}"
            ) from exc

        if response.ok:
            document = response.json().get("document", {})
            asset_id = document.get("_id")
            if not asset_id:
                raise SanityError(f"Sanity upload returned no asset id for {image_path}")
            return asset_id

        last_error = response.text
        if attempt < retries and response.status_code >= 500:
            continue
        break

    raise SanityError(f"Sanity asset upload failed for {image_path}: {last_error}")


def build_image_url(asset_ref: str, width: int = 1080) -> str:
    """Build a Sanity CDN URL from an image asset reference."""
    asset_id = asset_ref.removeprefix("image-").split("-")[0]
    dimensions = asset_ref.split("-")[-2:]
    if len(dimensions) == 2 and dimensions[0].isdigit():
        size = f"{dimensions[0]}x{dimensions[1]}"
        ext = "jpg"
        return f"{SANITY_CDN_BASE}/{asset_id}-{size}.{ext}?w={width}&auto=format"
    return f"{SANITY_CDN_BASE}/{quote(asset_id, safe='')}?w={width}&auto=format"


def fetch_all_tours() -> list[dict[str, Any]]:
    return sanity_query(
        """
        *[_type == "tour"] | order(title asc) {
          _id,
          title,
          "slug": slug.current,
          text_scene_1,
          text_scene_2,
          bg_music,
          style,
          transition,
          text_animation,
          text_style,
          lut_enabled,
          subscribe_icon_enabled,
          zoom_override,
          "images": images[]{
            "url": asset->url,
            "alt": coalesce(alt, ""),
            "assetId": asset->_id
          }
        }
        """
    )


def fetch_tour_by_slug(slug: str) -> dict[str, Any] | None:
    results = sanity_query(
        """
        *[_type == "tour" && slug.current == $slug][0]{
          _id,
          title,
          "slug": slug.current,
          text_scene_1,
          text_scene_2,
          bg_music,
          style,
          transition,
          text_animation,
          text_style,
          lut_enabled,
          subscribe_icon_enabled,
          zoom_override,
          "images": images[]{
            "url": asset->url,
            "alt": coalesce(alt, ""),
            "assetId": asset->_id
          }
        }
        """,
        {"slug": slug},
    )
    return results[0] if results else None


def tour_image_urls(tour: dict[str, Any], limit: int = 4) -> list[str]:
    urls: list[str] = []
    for image in tour.get("images") or []:
        url = image.get("url")
        if url:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls
