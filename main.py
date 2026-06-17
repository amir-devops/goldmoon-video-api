import asyncio
import io
import ipaddress
import os
import re
import shutil
import subprocess
import textwrap
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import requests
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, HttpUrl

app = FastAPI(title="Goldmoon Cinematic Video API", version="2.0")

APP_DIR = Path(os.getenv("APP_DIR", "/app"))
ASSETS_DIR = APP_DIR / "assets"
SOUNDS_DIR = APP_DIR / "sounds"

FALLBACK_MUSIC = ASSETS_DIR / "music_epic.mp3"
MUSIC_SEARCH_DIRS = (ASSETS_DIR, SOUNDS_DIR, APP_DIR)

API_KEY_SECRET = os.getenv("VIDEO_API_KEY", "GoldmoonSecret2026")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FALLBACK_FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
IMG_DURATION = 4.0
XFADE_DURATION = 0.5
FRAMERATE = 30
MAX_IMAGE_BYTES = 10 * 1024 * 1024
FFMPEG_TIMEOUT = 120
WRAP_CHARS = 28
FONT_SIZE = 46
TEXT_Y = "(h-420)"

BG_MUSIC_ALIASES = {
    "desert_ambient": "samuelfjohanns-egypt-expedition-a-mysterious-discovery-119128.mp3",
    "luxury_chill": "tunetank-vlog-beat-background-349853.mp3",
    "cinematic_epic": "samuelfjohanns-cinematic-duduk-192901.mp3",
}

render_semaphore = asyncio.Semaphore(1)


class VideoRequest(BaseModel):
    image_urls: list[HttpUrl] = Field(..., min_length=2, max_length=4)
    video_title: str = Field("goldmoon_promo", max_length=50)
    text_scene_1: str = Field(..., max_length=60)
    text_scene_2: str = Field(..., max_length=60)
    bg_music: Literal["desert_ambient", "luxury_chill", "cinematic_epic"] = "luxury_chill"


def verify_api_key(x_api_key: str | None = Header(default=None)) -> str:
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")
    return x_api_key


def resolve_font_path() -> str:
    if Path(FONT_PATH).exists():
        return FONT_PATH
    if Path(FALLBACK_FONT).exists():
        return FALLBACK_FONT
    raise HTTPException(status_code=500, detail="No suitable bold system font found.")


def resolve_bg_music(music_key: str) -> Path | None:
    resolved_name = BG_MUSIC_ALIASES.get(music_key, music_key)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "", Path(resolved_name).name).strip()
    if safe_name:
        for folder in MUSIC_SEARCH_DIRS:
            candidate = folder / safe_name
            if candidate.exists():
                return candidate
    if FALLBACK_MUSIC.exists():
        return FALLBACK_MUSIC
    for folder in (SOUNDS_DIR, ASSETS_DIR):
        mp3_files = sorted(folder.glob("*.mp3"))
        if mp3_files:
            return mp3_files[0]
    return None


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


def sanitize_plain_text(text: str, max_chars: int | None = None) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = cleaned.replace('"', "").replace("\\", "")
    cleaned = re.sub(r"[^\w\s.,!?\-]", "", cleaned, flags=re.UNICODE).strip()
    if max_chars is not None:
        return cleaned[:max_chars].strip()
    return cleaned


def escape_drawtext(text: str) -> str:
    escaped = (text or "").strip()
    for source, target in {
        "\\": "\\\\",
        ":": "\\:",
        "'": "\\'",
        "%": "\\%",
        "[": "\\[",
        "]": "\\]",
    }.items():
        escaped = escaped.replace(source, target)
    return escaped


def prepare_scene_text(text: str, width: int = WRAP_CHARS) -> str:
    plain_text = sanitize_plain_text(text, max_chars=60)
    if not plain_text:
        return ""
    lines = textwrap.wrap(plain_text, width=width)
    return "\\n".join(escape_drawtext(line) for line in lines)


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


def build_xfade_chain(num_images: int) -> tuple[str, float]:
    """Build letterbox + xfade filter chain. Returns (filter_str, total_duration)."""
    filter_parts: list[str] = []
    for i in range(num_images):
        filter_parts.append(
            f"[{i}:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=0x0F0F0F,"
            f"setsar=1[v_scaled_{i}];"
        )

    last_output = "[v_scaled_0]"
    current_offset = IMG_DURATION - XFADE_DURATION
    for i in range(1, num_images):
        next_label = f"[v_mix_{i}]" if i < num_images - 1 else "[v_images_merged]"
        filter_parts.append(
            f"{last_output}[v_scaled_{i}]xfade=transition=fade:duration={XFADE_DURATION}:"
            f"offset={current_offset}{next_label};"
        )
        last_output = next_label
        current_offset += IMG_DURATION - XFADE_DURATION

    total_duration = (IMG_DURATION * num_images) - (XFADE_DURATION * (num_images - 1))
    return "".join(filter_parts), total_duration


def build_filter_complex(
    num_images: int,
    font_path: str,
    hook_text: str,
    cta_text: str,
    music_path: Path | None,
) -> tuple[str, list[str], float]:
    image_filters, total_duration = build_xfade_chain(num_images)
    escaped_font = font_path.replace(":", "\\:")
    scene_split = (num_images // 2) * IMG_DURATION
    if scene_split <= 0 or scene_split >= total_duration:
        scene_split = total_duration / 2

    text_filters = (
        f"[v_images_merged]"
        f"drawtext=fontfile={escaped_font}:text='{hook_text}':fontcolor=white:fontsize={FONT_SIZE}:"
        f"borderw=3:bordercolor=black:x=(w-text_w)/2:y={TEXT_Y}:line_spacing=10:"
        f"enable='between(t\\,0\\,{scene_split})',"
        f"drawtext=fontfile={escaped_font}:text='{cta_text}':fontcolor=0x00D7FF:fontsize={FONT_SIZE}:"
        f"borderw=3:bordercolor=black:x=(w-text_w)/2:y={TEXT_Y}:line_spacing=10:"
        f"enable='between(t\\,{scene_split}\\,{total_duration})'[v_final];"
    )

    audio_input: list[str]
    if music_path and music_path.exists():
        audio_input = ["-i", str(music_path)]
        audio_filters = (
            f"[{num_images}:a]aloop=loop=-1:size=2e+09,atrim=0:{total_duration},"
            f"volume=0.25,afade=t=out:st={total_duration - 0.5}:d=0.5[a_final]"
        )
    else:
        audio_input = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        audio_filters = f"[{num_images}:a]atrim=0:{total_duration}[a_final]"

    return image_filters + text_filters + audio_filters, audio_input, total_duration


@app.get("/health")
def health_check() -> dict:
    return {
        "status": "healthy",
        "ffmpeg_installed": shutil.which("ffmpeg") is not None,
        "font_installed": Path(FONT_PATH).exists() or Path(FALLBACK_FONT).exists(),
    }


@app.post("/render", response_class=FileResponse)
async def render_video(
    payload: VideoRequest,
    background_tasks: BackgroundTasks,
    _auth: str = Depends(verify_api_key),
) -> FileResponse:
    async with render_semaphore:
        job_id = uuid.uuid4().hex
        downloaded_images: list[Path] = []
        output_video = APP_DIR / f"video_{job_id}.mp4"

        try:
            for idx, url_obj in enumerate(payload.image_urls):
                url = str(url_obj)
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

            font_path = resolve_font_path()
            hook_text = prepare_scene_text(payload.text_scene_1)
            cta_text = prepare_scene_text(payload.text_scene_2)
            if not hook_text or not cta_text:
                raise HTTPException(
                    status_code=400,
                    detail="text_scene_1 and text_scene_2 must contain valid plain text.",
                )

            music_path = resolve_bg_music(payload.bg_music)
            num_images = len(downloaded_images)
            filter_complex, audio_input, total_duration = build_filter_complex(
                num_images, font_path, hook_text, cta_text, music_path
            )

            command = ["ffmpeg", "-y"]
            for img in downloaded_images:
                command.extend(["-loop", "1", "-t", str(IMG_DURATION), "-i", str(img)])
            command.extend(audio_input)
            command.extend(
                [
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[v_final]",
                    "-map",
                    "[a_final]",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "faster",
                    "-crf",
                    "22",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    str(FRAMERATE),
                    "-movflags",
                    "+faststart",
                    "-t",
                    str(total_duration),
                    str(output_video),
                ]
            )

            try:
                process = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=FFMPEG_TIMEOUT,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise HTTPException(status_code=504, detail="Rendering timeout.") from exc

            if process.returncode != 0:
                error_msg = process.stderr.decode(errors="replace").strip()
                raise HTTPException(
                    status_code=500,
                    detail=f"FFmpeg Error: {error_msg or process.returncode}",
                )

            if not output_video.exists() or output_video.stat().st_size == 0:
                raise HTTPException(status_code=500, detail="Video rendering failed.")

            safe_title = sanitize_plain_text(payload.video_title, max_chars=50)
            download_name = (
                f"{re.sub(r'[^A-Za-z0-9._-]+', '_', safe_title).strip('._')}.mp4"
                if safe_title
                else "goldmoon_promo.mp4"
            )

            background_tasks.add_task(output_video.unlink, missing_ok=True)
            return FileResponse(
                path=str(output_video),
                media_type="video/mp4",
                filename=download_name,
            )
        finally:
            for img_path in downloaded_images:
                img_path.unlink(missing_ok=True)
