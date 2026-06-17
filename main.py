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
CUSTOM_FONT = APP_DIR / "PlayfairDisplay-Regular.ttf"
MONTSERRAT_FONT = ASSETS_DIR / "Montserrat-Bold.ttf"
OSWALD_FONT = ASSETS_DIR / "Oswald-Bold.ttf"
LOGO_PATH = Path(os.getenv("LOGO_PATH", str(ASSETS_DIR / "logo.png")))
FONT_PATH = os.getenv("FONT_PATH", str(MONTSERRAT_FONT))
FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FALLBACK_FONT_ALT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
IMG_DURATION = 4.0
XFADE_DURATION = 0.5
FRAMERATE = 30
DURATION_FRAMES = int(IMG_DURATION * FRAMERATE)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
FFMPEG_TIMEOUT = 120
WRAP_CHARS = 28
SCENE_FONT_SIZE = 46
SCENE_TEXT_START_Y = 1100
SCENE_LINE_SPACING = 85
TEXT_FADE_DELAY = 0.3
TEXT_FADE_DURATION = 0.5

OUTRO_DURATION = 3.0
OUTRO_FRAMES = int(OUTRO_DURATION * FRAMERATE)
WEBSITE_URL = "WWW.GOLDMOONEGYPT.COM"
OUTRO_URL_FADE_DELAY = 0.4
OUTRO_URL_FADE_DURATION = 0.5

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
    env_font = os.getenv("FONT_PATH")
    candidates = [
        env_font,
        str(MONTSERRAT_FONT),
        str(OSWALD_FONT),
        str(CUSTOM_FONT),
        FALLBACK_FONT,
        FALLBACK_FONT_ALT,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
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


def split_scene_lines(text: str, max_lines: int = 2) -> list[str]:
    """Split sanitized text into independent drawtext lines (no literal \\n)."""
    plain_text = sanitize_plain_text(text, max_chars=60)
    if not plain_text:
        return []
    return textwrap.wrap(plain_text, width=WRAP_CHARS)[:max_lines]


def build_scene_filter(
    font_path: str,
    text_lines: list[str],
    duration_frames: int = DURATION_FRAMES,
) -> str:
    """
    Premium scene pipeline:
    1. Loop + scale + center crop + Ken Burns (no stretch)
    2. Shorts safe-zone text placement (y=1100+)
    3. UPPERCASE luxury styling + cinematic transparent box
    4. Smooth text + box fade-in via alpha
    5. Locked 30 FPS timebase
    """
    base_filter = (
        f"loop={duration_frames}:1:0,"
        "format=yuv420p,"
        "scale=w=1620:h=2880:force_original_aspect_ratio=increase,"
        "crop=1620:2880,"
        f"zoompan=z='min(zoom+0.001\\,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={duration_frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={FRAMERATE}"
    )

    if not text_lines:
        return f"{base_filter},fps={FRAMERATE}"

    escaped_font = font_path.replace(":", "\\:")
    text_filters: list[str] = []
    for index, line in enumerate(text_lines):
        premium_line = escape_drawtext(line.strip().upper())
        y_position = f"{SCENE_TEXT_START_Y}+({index}*{SCENE_LINE_SPACING})"
        text_filters.append(
            f"drawtext=fontfile={escaped_font}:text='{premium_line}':"
            f"fontcolor=white:fontsize={SCENE_FONT_SIZE}:"
            f"box=1:boxcolor=black@0.4:boxborderw=20:"
            f"x=(w-text_w)/2:y={y_position}:"
            f"alpha='if(lt(t\\,{TEXT_FADE_DELAY})\\,0\\,"
            f"min((t-{TEXT_FADE_DELAY})/{TEXT_FADE_DURATION}\\,1))'"
        )

    return base_filter + "," + ",".join(text_filters) + f",fps={FRAMERATE}"


def build_scene_pipeline(
    num_images: int,
    font_path: str,
    scene_texts: list[list[str]],
) -> tuple[str, float]:
    """Build Ken Burns scene filters + xfade chain. Returns (filter_str, total_duration)."""
    filter_parts: list[str] = []

    for i in range(num_images):
        scene_filter = build_scene_filter(font_path, scene_texts[i])
        filter_parts.append(f"[{i}:v]{scene_filter}[v_scene_{i}];")

    last_output = "[v_scene_0]"
    current_offset = IMG_DURATION - XFADE_DURATION
    for i in range(1, num_images):
        next_label = f"[v_mix_{i}]" if i < num_images - 1 else "[v_images_merged]"
        filter_parts.append(
            f"{last_output}[v_scene_{i}]xfade=transition=fade:duration={XFADE_DURATION}:"
            f"offset={current_offset}{next_label};"
        )
        last_output = next_label
        current_offset += IMG_DURATION - XFADE_DURATION

    filter_parts.append(
        "[v_images_merged]eq=contrast=1.1:saturation=1.15[v_graded];"
    )

    images_duration = (IMG_DURATION * num_images) - (XFADE_DURATION * (num_images - 1))
    return "".join(filter_parts), images_duration


def assign_scene_texts(
    num_images: int,
    text_scene_1: str,
    text_scene_2: str,
) -> list[list[str]]:
    lines_1 = split_scene_lines(text_scene_1)
    lines_2 = split_scene_lines(text_scene_2)
    split_at = num_images // 2
    return [lines_1 if index < split_at else lines_2 for index in range(num_images)]


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


def resolve_logo_path() -> Path | None:
    if LOGO_PATH.exists():
        return LOGO_PATH
    return None


def build_outro_with_logo_filter(
    font_path: str,
    bg_input_idx: int,
    logo_input_idx: int,
    website_url: str = WEBSITE_URL,
    duration_frames: int = OUTRO_FRAMES,
) -> str:
    """Build outro with branded logo overlay and fading website URL."""
    escaped_font = font_path.replace(":", "\\:")
    clean_url = escape_drawtext(website_url.strip().upper())

    return (
        f"[{logo_input_idx}:v]scale=380:-1[logo_scaled];"
        f"[{bg_input_idx}:v]loop={duration_frames}:1:0,format=yuv420p,"
        f"scale=w=1620:h=2880:force_original_aspect_ratio=increase,"
        f"crop=1620:2880,"
        f"zoompan=z=1:d={duration_frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={FRAMERATE}[bg];"
        f"[bg][logo_scaled]overlay=(W-w)/2:(H-h)/2-120[with_logo];"
        f"[with_logo]drawtext=fontfile={escaped_font}:text='{clean_url}':"
        f"fontcolor=white@0.8:fontsize=30:"
        f"x=(w-text_w)/2:y=1120:"
        f"alpha='if(lt(t\\,{OUTRO_URL_FADE_DELAY})\\,0\\,"
        f"min((t-{OUTRO_URL_FADE_DELAY})/{OUTRO_URL_FADE_DURATION}\\,1))',"
        f"fps={FRAMERATE}[v_outro]"
    )


def build_outro_filter(font_path: str, duration_frames: int = OUTRO_FRAMES) -> str:
    """
    Fallback outro on black background when logo file is unavailable.
    """
    escaped_font = font_path.replace(":", "\\:")
    company_name = escape_drawtext("GOLDMOON")
    website_url = escape_drawtext(WEBSITE_URL)

    return (
        f"drawtext=fontfile={escaped_font}:text='{company_name}':"
        f"fontcolor=gold:fontsize=72:box=0:"
        f"x=(w-text_w)/2:y=(h-text_h)/2-60:"
        f"borderw=2:bordercolor=black,"
        f"drawtext=fontfile={escaped_font}:text='{website_url}':"
        f"fontcolor=white:fontsize=38:box=0:"
        f"x=(w-text_w)/2:y=(h-text_h)/2+40:"
        f"borderw=1:bordercolor=black,"
        f"setsar=1,fps={FRAMERATE}"
    )


def build_filter_complex(
    num_images: int,
    font_path: str,
    text_scene_1: str,
    text_scene_2: str,
    music_path: Path | None,
    logo_path: Path | None,
    outro_bg_path: Path | None,
) -> tuple[str, list[str], list[str], float]:
    scene_texts = assign_scene_texts(num_images, text_scene_1, text_scene_2)
    if not any(scene_texts):
        raise ValueError("Scene text is empty after sanitization")

    image_filters, images_duration = build_scene_pipeline(
        num_images, font_path, scene_texts
    )

    outro_bg_idx = num_images
    logo_idx = num_images + 1 if logo_path else None
    music_idx = num_images + (2 if logo_path else 1)
    outro_offset = images_duration - XFADE_DURATION
    total_duration = images_duration + OUTRO_DURATION - XFADE_DURATION

    if logo_path and outro_bg_path:
        outro_filters = (
            build_outro_with_logo_filter(
                font_path, outro_bg_idx, logo_idx, WEBSITE_URL, OUTRO_FRAMES
            )
            + ";"
            + f"[v_graded][v_outro]xfade=transition=fade:duration={XFADE_DURATION}:"
            f"offset={outro_offset}[v_final];"
        )
        outro_input = [
            "-loop",
            "1",
            "-t",
            str(OUTRO_DURATION),
            "-i",
            str(outro_bg_path),
            "-i",
            str(logo_path),
        ]
    else:
        outro_filters = (
            f"[{outro_bg_idx}:v]{build_outro_filter(font_path)}[v_outro];"
            f"[v_graded][v_outro]xfade=transition=fade:duration={XFADE_DURATION}:"
            f"offset={outro_offset}[v_final];"
        )
        outro_input = [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:d={OUTRO_DURATION}:r={FRAMERATE}",
        ]

    if music_path and music_path.exists():
        audio_input = ["-i", str(music_path)]
        audio_filters = (
            f"[{music_idx}:a]aloop=loop=-1:size=2e+09,atrim=0:{total_duration},"
            f"volume=0.25,afade=t=out:st={total_duration - 0.5}:d=0.5[a_final]"
        )
    else:
        audio_input = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        audio_filters = f"[{music_idx}:a]atrim=0:{total_duration}[a_final]"

    return (
        image_filters + outro_filters + audio_filters,
        outro_input,
        audio_input,
        total_duration,
    )


@app.get("/health")
def health_check() -> dict:
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
            if not split_scene_lines(payload.text_scene_1) or not split_scene_lines(
                payload.text_scene_2
            ):
                raise HTTPException(
                    status_code=400,
                    detail="text_scene_1 and text_scene_2 must contain valid plain text.",
                )

            music_path = resolve_bg_music(payload.bg_music)
            logo_path = resolve_logo_path()
            num_images = len(downloaded_images)
            outro_bg_path = downloaded_images[-1] if downloaded_images else None
            filter_complex, outro_input, audio_input, total_duration = build_filter_complex(
                num_images,
                font_path,
                payload.text_scene_1,
                payload.text_scene_2,
                music_path,
                logo_path,
                outro_bg_path,
            )

            command = ["ffmpeg", "-y"]
            for img in downloaded_images:
                command.extend(["-i", str(img)])
            command.extend(outro_input)
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
