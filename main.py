import asyncio
import io
import ipaddress
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import requests
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, HttpUrl, model_validator

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
    load_presets,
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
    tour_image_urls,
)

app = FastAPI(title="Goldmoon Cinematic Video API", version="2.0")

API_KEY_SECRET = os.getenv("VIDEO_API_KEY", "GoldmoonSecret2026")
FONT_PATH = os.getenv("FONT_PATH", str(MONTSERRAT_FONT))

render_semaphore = asyncio.Semaphore(1)


class VideoRequest(BaseModel):
    image_urls: list[HttpUrl] | None = Field(default=None, min_length=2, max_length=4)
    tour_slug: str | None = Field(default=None, max_length=96)
    video_title: str = Field("goldmoon_promo", max_length=50)
    text_scene_1: str | None = Field(default=None, max_length=60)
    text_scene_2: str | None = Field(default=None, max_length=60)
    bg_music: Literal["desert_ambient", "luxury_chill", "cinematic_epic"] = "luxury_chill"
    style: str | None = Field(
        default=None,
        max_length=32,
        description="Visual preset override. Omit or use unknown name for a random style.",
    )
    debug_mode: bool = Field(
        False,
        description="When true, renders a ~3 second preview instead of the full video.",
    )
    logo_url: HttpUrl | None = Field(
        default=None,
        description="Optional logo image URL for the outro. Falls back to the bundled logo.",
    )
    website_url: str = Field(
        default=DEFAULT_WEBSITE_URL,
        max_length=200,
        description="Website URL displayed at the bottom of the outro.",
    )

    @model_validator(mode="after")
    def validate_image_source(self) -> "VideoRequest":
        if not self.tour_slug and not self.image_urls:
            raise ValueError("Provide either tour_slug or image_urls (2-4 items).")
        return self


def resolve_render_request(payload: VideoRequest) -> dict:
    image_urls = [str(url) for url in payload.image_urls] if payload.image_urls else []
    text_scene_1 = payload.text_scene_1
    text_scene_2 = payload.text_scene_2
    video_title = payload.video_title
    bg_music = payload.bg_music
    style = payload.style

    if payload.tour_slug:
        try:
            tour = fetch_tour_by_slug(payload.tour_slug)
        except SanityError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if not tour:
            raise HTTPException(
                status_code=404,
                detail=f"Tour not found in Sanity: {payload.tour_slug}",
            )

        image_urls = tour_image_urls(tour)
        text_scene_1 = text_scene_1 or tour.get("text_scene_1")
        text_scene_2 = text_scene_2 or tour.get("text_scene_2")
        if video_title == "goldmoon_promo" and tour.get("title"):
            video_title = tour["title"]
        if tour.get("bg_music") in {"desert_ambient", "luxury_chill", "cinematic_epic"}:
            bg_music = tour["bg_music"]
        if style is None and tour.get("style"):
            style = tour["style"]

    if len(image_urls) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least 2 image URLs are required for rendering.",
        )
    if len(image_urls) > 4:
        image_urls = image_urls[:4]

    if not text_scene_1 or not text_scene_2:
        raise HTTPException(
            status_code=400,
            detail="text_scene_1 and text_scene_2 are required.",
        )

    return {
        "image_urls": image_urls,
        "text_scene_1": text_scene_1,
        "text_scene_2": text_scene_2,
        "video_title": video_title,
        "bg_music": bg_music,
        "style": style or "",
        "debug_mode": payload.debug_mode,
        "logo_url": str(payload.logo_url) if payload.logo_url else None,
        "website_url": payload.website_url.strip() or DEFAULT_WEBSITE_URL,
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
    CLI entry for n8n Execute Command:
    python main.py <img1> <img2> [img3] [img4] <text1> <text2> <video_title> [bg_music]
    """
    if len(sys.argv) < 6:
        print("Error: Missing arguments from n8n!")
        print(
            "Expected: python main.py <img1> <img2> <text1> <text2> <video_title> [bg_music]"
        )
        print(
            "Or: python main.py <img1> <img2> <img3> <text1> <text2> <video_title> [bg_music]"
        )
        sys.exit(1)

    optional_music = sys.argv[-1]
    known_music = {"desert_ambient", "luxury_chill", "cinematic_epic"}
    if optional_music in known_music:
        bg_music = optional_music
        argv = sys.argv[1:-1]
    else:
        bg_music = "luxury_chill"
        argv = sys.argv[1:]

    if len(argv) < 5:
        print("Error: Missing text or video title arguments.")
        sys.exit(1)

    video_title = argv[-1]
    text_scene_2 = argv[-2]
    text_scene_1 = argv[-3]
    image_args = argv[:-3]

    if len(image_args) < 2 or len(image_args) > 4:
        print("Error: Provide 2 to 4 image paths.")
        sys.exit(1)

    image_paths = [Path(arg).resolve() for arg in image_args]
    output_name = safe_output_filename(video_title)
    output_path = Path.cwd() / f"output_{Path(output_name).stem}.mp4"
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

    try:
        result = render_video(
            {
                "image_paths": image_paths,
                "text_scene_1": text_scene_1,
                "text_scene_2": text_scene_2,
                "video_title": video_title,
                "bg_music": bg_music,
                "output_path": output_path,
                "style": style,
                "debug_mode": debug_mode,
                "logo_path": downloaded_logo,
                "website_url": website_url,
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
                        detail=f"Unsafe image URL at index {idx}",
                    )
                img_path = APP_DIR / f"img_{job_id}_{idx}.jpg"
                try:
                    download_image(url, img_path)
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid image at index {idx}",
                    ) from exc
                downloaded_images.append(img_path)

            try:
                require_english_text(render_data["text_scene_1"], "text_scene_1")
                require_english_text(render_data["text_scene_2"], "text_scene_2")
            except RenderError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            try:
                await asyncio.to_thread(
                    render_video,
                    {
                        "image_paths": downloaded_images,
                        "text_scene_1": render_data["text_scene_1"],
                        "text_scene_2": render_data["text_scene_2"],
                        "video_title": render_data["video_title"],
                        "bg_music": render_data["bg_music"],
                        "output_path": output_video,
                        "style": render_data["style"],
                        "debug_mode": render_data["debug_mode"],
                        "logo_path": downloaded_logo,
                        "website_url": render_data["website_url"],
                    },
                )
            except RenderError as exc:
                if "timeout" in str(exc).lower():
                    raise HTTPException(status_code=504, detail=str(exc)) from exc
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            download_name = safe_output_filename(render_data["video_title"])
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
