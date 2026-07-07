import asyncio
import io
import ipaddress
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, HttpUrl

from render_pipeline import (
    APP_DIR,
    ASSETS_DIR,
    CUSTOM_FONT,
    DEFAULT_STYLE,
    DEFAULT_WEBSITE_URL,
    FALLBACK_FONT,
    FALLBACK_FONT_ALT,
    LOGO_PATH,
    MAX_IMAGE_BYTES,
    MONTSERRAT_FONT,
    OSWALD_FONT,
    OUTRO_DURATION,
    RenderError,
    TEXT_ANIMATIONS,
    TRANSITION_POOL,
    assign_scene_texts,
    load_presets,
    pick_text_animation,
    pick_transition,
    render_video,
    require_english_text,
    safe_output_filename,
)
from sanity_client import (
    SANITY_DATASET,
    SANITY_PROJECT_ID,
    SanityError,
    fetch_all_tours,
    fetch_tour_by_slug,
)

app = FastAPI(title="Goldmoon Cinematic Video API", version="2.0")

API_KEY_SECRET = os.getenv("VIDEO_API_KEY", "GoldmoonSecret2026")
FONT_PATH = os.getenv("FONT_PATH", str(MONTSERRAT_FONT))

render_semaphore = asyncio.Semaphore(1)


class Scene(BaseModel):
    image_url: HttpUrl


class VideoRequest(BaseModel):
    style: str = Field(
        default="",
        max_length=32,
        description="Visual preset name. Omit or use unknown name for a random style.",
    )
    logo_url: HttpUrl | None = Field(
        default=None,
        description="Optional logo image URL for the outro. Falls back to the bundled logo.",
    )
    website_url: str = Field(
        default=DEFAULT_WEBSITE_URL,
        max_length=100,
        description="Website URL displayed at the bottom of the outro.",
    )
    scenes: list[Scene] = Field(
        ...,
        min_length=2,
        max_length=4,
        description="2-4 scenes, each with an image URL.",
    )
    scene_texts: list[str] = Field(
        ...,
        min_length=2,
        max_length=4,
        description=(
            "2-4 overlay text strings mapped to scenes by index. "
            "If fewer texts than scenes, the last text repeats for remaining scenes."
        ),
    )
    transition: str | None = Field(
        default=None,
        max_length=20,
        description=(
            "Optional scene-to-scene transition (see /presets for the full list). "
            "Omit for a randomly chosen transition so each render looks distinct."
        ),
    )
    text_animation: str | None = Field(
        default=None,
        max_length=20,
        description=(
            "Optional text entrance animation: fade, slide_up, slide_down, or "
            "rise_fade. Omit for a randomly chosen animation each render."
        ),
    )
    bg_music: str | None = Field(
        default=None,
        max_length=40,
        description=(
            "Optional background music key: desert_ambient, luxury_chill, "
            "cinematic_epic, arab_trailer, vlog_energetic, cairo_nights, "
            "egyptian_desert, desert_travels, ancient_mystique, or "
            "ancient_empire. Omit to use the default (luxury_chill)."
        ),
    )


def resolve_render_request(payload: VideoRequest) -> dict[str, Any]:
    return {
        "image_urls": [str(scene.image_url) for scene in payload.scenes],
        "scene_texts": list(payload.scene_texts),
        "style": payload.style or "",
        "logo_url": str(payload.logo_url) if payload.logo_url else None,
        "website_url": payload.website_url.strip() or DEFAULT_WEBSITE_URL,
        "transition": (payload.transition or "").strip().lower() or None,
        "text_animation": (payload.text_animation or "").strip().lower() or None,
        "bg_music": (payload.bg_music or "").strip().lower() or None,
    }


def verify_api_key(x_api_key: str | None = Header(default=None)) -> str:
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")
    return x_api_key


def is_url_safe(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass
    return True


def download_image(url: str, dest: Path) -> None:
    response = requests.get(url, timeout=10, stream=True)
    if not response.ok:
        raise HTTPException(status_code=400, detail=f"Failed to fetch image: {url}")

    content_length = response.headers.get("content-length")
    if content_length and int(content_length) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image exceeds 10MB limit")

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="Image exceeds 10MB limit")
        chunks.append(chunk)

    img_bytes = b"".join(chunks)
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            img.verify()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image file") from exc

    dest.write_bytes(img_bytes)


def download_logo(url: str, dest: Path) -> None:
    try:
        download_image(url, dest)
    except HTTPException as exc:
        raise RenderError(str(exc.detail)) from exc


def run_n8n_cli() -> None:
    """
    CLI entry for n8n Execute Command nodes.

    Usage:
        python main.py --images <img1> <img2> [img3] [img4] \\
                       --texts  <text1> <text2> [text3] [text4] \\
                       --title  <video_title> \\
                       [--music desert_ambient|luxury_chill|cinematic_epic|
                                 arab_trailer|vlog_energetic|cairo_nights|
                                 egyptian_desert|desert_travels|
                                 ancient_mystique|ancient_empire]

    Environment overrides (optional):
        STYLE, DEBUG_MODE, WEBSITE_URL, LOGO_URL
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="goldmoon-render",
        description="Goldmoon CLI video renderer for n8n Execute Command nodes",
    )
    parser.add_argument(
        "--images",
        nargs="+",
        required=True,
        metavar="PATH",
        help="2-4 local image file paths",
    )
    parser.add_argument(
        "--texts",
        nargs="+",
        required=True,
        metavar="TEXT",
        help="2-4 scene overlay texts (English only, max 60 chars each)",
    )
    parser.add_argument(
        "--title",
        required=True,
        help="Output video title (used to name the output file)",
    )
    parser.add_argument(
        "--music",
        default="luxury_chill",
        choices=[
            "desert_ambient",
            "luxury_chill",
            "cinematic_epic",
            "arab_trailer",
            "vlog_energetic",
            "cairo_nights",
            "egyptian_desert",
            "desert_travels",
            "ancient_mystique",
            "ancient_empire",
        ],
        help="Background music track key (default: luxury_chill)",
    )
    parser.add_argument(
        "--transition",
        default=None,
        choices=TRANSITION_POOL,
        help="Scene transition style. Omit for a random one each run.",
    )
    parser.add_argument(
        "--text-animation",
        default=None,
        choices=TEXT_ANIMATIONS,
        help="Text entrance animation. Omit for a random one each run.",
    )

    args = parser.parse_args(sys.argv[1:])

    if len(args.images) < 2 or len(args.images) > 4:
        print("Error: Provide 2 to 4 image paths via --images.")
        sys.exit(1)
    if len(args.texts) < 2 or len(args.texts) > 4:
        print("Error: Provide 2 to 4 scene texts via --texts.")
        sys.exit(1)

    for idx, text in enumerate(args.texts):
        try:
            require_english_text(text, f"--texts[{idx}]")
        except RenderError as exc:
            print(f"Error: {exc}")
            sys.exit(1)

    style = os.getenv("STYLE", "").strip()
    debug_mode = os.getenv("DEBUG_MODE", "").strip().lower() in {"1", "true", "yes"}
    website_url = os.getenv("WEBSITE_URL", DEFAULT_WEBSITE_URL).strip() or DEFAULT_WEBSITE_URL
    logo_url = os.getenv("LOGO_URL", "").strip()
    downloaded_logo: Path | None = None

    if logo_url:
        if not is_url_safe(logo_url):
            print("Error: Unsafe LOGO_URL.")
            sys.exit(1)
        downloaded_logo = Path.cwd() / f"logo_{uuid.uuid4().hex}.png"
        try:
            download_logo(logo_url, downloaded_logo)
        except RenderError as exc:
            print(f"Error: {exc}")
            sys.exit(1)

    image_paths = [Path(arg).resolve() for arg in args.images]
    output_name = safe_output_filename(args.title)
    output_path = Path.cwd() / f"output_{Path(output_name).stem}.mp4"

    try:
        result = render_video(
            {
                "image_paths": image_paths,
                "scene_texts": args.texts,
                "output_path": output_path,
                "style": style,
                "logo_path": downloaded_logo,
                "website_url": website_url,
                "bg_music": args.music,
                "debug_mode": debug_mode,
                "transition": args.transition,
                "text_animation": args.text_animation,
            }
        )
    except RenderError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    finally:
        if downloaded_logo:
            downloaded_logo.unlink(missing_ok=True)

    print(f"Success: {result}")


@app.get("/health")
def health_check() -> dict:
    try:
        presets = load_presets()
        preset_names = sorted(presets)
        presets_loaded = True
    except RenderError:
        preset_names = []
        presets_loaded = False

    return {
        "status": "healthy",
        "ffmpeg_installed": shutil.which("ffmpeg") is not None,
        "font_installed": any(
            Path(path).exists()
            for path in (
                FONT_PATH,
                MONTSERRAT_FONT,
                OSWALD_FONT,
                CUSTOM_FONT,
                FALLBACK_FONT,
                FALLBACK_FONT_ALT,
            )
        ),
        "logo_installed": LOGO_PATH.exists(),
        "presets_loaded": presets_loaded,
        "preset_count": len(preset_names),
        "presets": preset_names,
        "default_style": DEFAULT_STYLE,
        "default_website_url": DEFAULT_WEBSITE_URL,
        "outro_duration_seconds": OUTRO_DURATION,
        "sanity_project_id": SANITY_PROJECT_ID,
        "sanity_dataset": SANITY_DATASET,
    }


@app.get("/presets")
def list_presets(_auth: str = Depends(verify_api_key)) -> dict:
    presets = load_presets()
    return {
        "default_style": DEFAULT_STYLE,
        "styles": {
            key: {
                "display_name": value.get("display_name", key),
                "filter": value.get("filter", ""),
                "zoom": value.get("zoom", {}),
            }
            for key, value in presets.items()
        },
        "transitions": TRANSITION_POOL,
        "text_animations": TEXT_ANIMATIONS,
    }


@app.get("/sanity/tours")
def list_sanity_tours(_auth: str = Depends(verify_api_key)) -> list[dict]:
    try:
        return fetch_all_tours()
    except SanityError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/sanity/tours/{slug}")
def get_sanity_tour(slug: str, _auth: str = Depends(verify_api_key)) -> dict:
    try:
        tour = fetch_tour_by_slug(slug)
    except SanityError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not tour:
        raise HTTPException(status_code=404, detail=f"Tour not found: {slug}")
    return tour


@app.post("/render", response_class=FileResponse)
async def render_video_endpoint(
    payload: VideoRequest,
    background_tasks: BackgroundTasks,
    _auth: str = Depends(verify_api_key),
) -> FileResponse:
    async with render_semaphore:
        job_id = uuid.uuid4().hex
        downloaded_images: list[Path] = []
        downloaded_logo: Path | None = None
        output_video = APP_DIR / f"video_{job_id}.mp4"

        try:
            render_data = resolve_render_request(payload)

            # Validate all texts early, before any expensive I/O
            for idx, text in enumerate(render_data["scene_texts"]):
                try:
                    require_english_text(text, f"scene_texts[{idx}]")
                except RenderError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc

            # Validate optional transition/animation overrides early too.
            # (Only when explicitly provided; None means "pick randomly later".)
            if render_data.get("transition"):
                try:
                    pick_transition(render_data["transition"])
                except RenderError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            if render_data.get("text_animation"):
                try:
                    pick_text_animation(render_data["text_animation"])
                except RenderError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc

            logo_url = render_data.get("logo_url")
            if logo_url:
                if not is_url_safe(logo_url):
                    raise HTTPException(status_code=400, detail="Unsafe logo_url")
                downloaded_logo = APP_DIR / f"logo_{job_id}.png"
                try:
                    download_image(logo_url, downloaded_logo)
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="Failed to download logo_url",
                    ) from exc

            for idx, url in enumerate(render_data["image_urls"]):
                if not is_url_safe(url):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unsafe image URL at scene index {idx}",
                    )
                img_path = APP_DIR / f"img_{job_id}_{idx}.jpg"
                try:
                    download_image(url, img_path)
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid image at scene index {idx}",
                    ) from exc
                downloaded_images.append(img_path)

            try:
                await asyncio.to_thread(
                    render_video,
                    {
                        "image_paths": downloaded_images,
                        "scene_texts": render_data["scene_texts"],
                        "output_path": output_video,
                        "style": render_data["style"],
                        "logo_path": downloaded_logo,
                        "website_url": render_data["website_url"],
                        "transition": render_data.get("transition"),
                        "text_animation": render_data.get("text_animation"),
                        "bg_music": render_data.get("bg_music") or "luxury_chill",
                    },
                )
            except RenderError as exc:
                if "timeout" in str(exc).lower():
                    raise HTTPException(status_code=504, detail=str(exc)) from exc
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            style_slug = render_data["style"] or "promo"
            download_name = safe_output_filename(f"goldmoon_{style_slug}")
            background_tasks.add_task(output_video.unlink, missing_ok=True)
            return FileResponse(
                path=str(output_video),
                media_type="video/mp4",
                filename=download_name,
            )
        finally:
            for img_path in downloaded_images:
                img_path.unlink(missing_ok=True)
            if downloaded_logo:
                downloaded_logo.unlink(missing_ok=True)


if __name__ == "__main__":
    run_n8n_cli()
