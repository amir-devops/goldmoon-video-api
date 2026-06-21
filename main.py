import asyncio
import io
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import requests
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, HttpUrl, model_validator

from sanity_client import (
    SANITY_DATASET,
    SANITY_PROJECT_ID,
    SanityError,
    fetch_all_tours,
    fetch_tour_by_slug,
    tour_image_urls,
)

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

PRESETS_PATH = Path(__file__).resolve().parent / "presets.json"
DEFAULT_STYLE = "desert_safari"
DEBUG_TOTAL_DURATION = 3.0

FONT_PRESET_MAP = {
    "classic": CUSTOM_FONT,
    "script": CUSTOM_FONT,
    "thin": MONTSERRAT_FONT,
    "simple": MONTSERRAT_FONT,
    "bold": OSWALD_FONT,
}

_preset_cache: dict[str, Any] | None = None
render_semaphore = asyncio.Semaphore(1)


class RenderError(Exception):
    """Raised when video rendering fails outside HTTP request context."""


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
        description="Visual preset override. Omit to use the tour's Sanity style.",
    )
    debug_mode: bool = Field(
        False,
        description="When true, renders a ~3 second preview instead of the full video.",
    )

    @model_validator(mode="after")
    def validate_image_source(self) -> "VideoRequest":
        if not self.tour_slug and not self.image_urls:
            raise ValueError("Provide either tour_slug or image_urls (2-4 items).")
        return self


def resolve_render_request(payload: VideoRequest) -> dict[str, str | list[str]]:
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

    style = (style or DEFAULT_STYLE).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        get_preset(style)
    except RenderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "image_urls": image_urls,
        "text_scene_1": text_scene_1,
        "text_scene_2": text_scene_2,
        "video_title": video_title,
        "bg_music": bg_music,
        "style": style,
        "debug_mode": payload.debug_mode,
    }


def verify_api_key(x_api_key: str | None = Header(default=None)) -> str:
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")
    return x_api_key


def load_presets() -> dict[str, Any]:
    global _preset_cache
    if _preset_cache is None:
        preset_path = PRESETS_PATH if PRESETS_PATH.exists() else APP_DIR / "presets.json"
        if not preset_path.exists():
            raise RenderError(f"Presets file not found: {preset_path}")
        with preset_path.open(encoding="utf-8") as handle:
            _preset_cache = json.load(handle)
    return _preset_cache


def get_preset(style: str) -> dict[str, Any]:
    key = style.strip().lower().replace("-", "_").replace(" ", "_")
    presets = load_presets()
    if key not in presets:
        available = ", ".join(sorted(presets))
        raise RenderError(f"Unknown style '{style}'. Available: {available}")
    return presets[key]


def list_preset_names() -> list[str]:
    return sorted(load_presets())


def resolve_render_timing(
    debug_mode: bool,
    num_images: int,
) -> tuple[float, float, float, int]:
    """Return img_duration, xfade_duration, outro_duration, duration_frames."""
    if debug_mode:
        xfade_duration = 0.25
        outro_duration = 0.75
        img_duration = (
            DEBUG_TOTAL_DURATION - outro_duration + xfade_duration + (num_images - 1) * xfade_duration
        ) / num_images
        return img_duration, xfade_duration, outro_duration, int(img_duration * FRAMERATE)
    return IMG_DURATION, XFADE_DURATION, OUTRO_DURATION, DURATION_FRAMES


def resolve_font_for_preset(preset: dict[str, Any]) -> str:
    font_key = preset.get("text", {}).get("font", "bold")
    preferred = FONT_PRESET_MAP.get(font_key, MONTSERRAT_FONT)
    candidates = [
        str(preferred),
        str(MONTSERRAT_FONT),
        str(OSWALD_FONT),
        str(CUSTOM_FONT),
        FALLBACK_FONT,
        FALLBACK_FONT_ALT,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return resolve_font_path()


def ffmpeg_escape_filter_expr(expr: str) -> str:
    return expr.replace(",", "\\,")


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


def require_english_text(text: str, field_name: str, max_chars: int = 60) -> str:
    """Ensure scene text is English-only for Shorts branding consistency."""
    cleaned = sanitize_plain_text(text, max_chars=max_chars)
    if not cleaned or not re.fullmatch(r"[A-Za-z0-9\s.,!?\-]+", cleaned):
        raise RenderError(f"{field_name} must contain English plain text only.")
    return cleaned


def safe_output_filename(video_title: str) -> str:
    safe_title = sanitize_plain_text(video_title, max_chars=50)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_title).strip("._")
    return f"{slug or 'goldmoon_promo'}.mp4"


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


def build_drawtext_filters(
    font_path: str,
    text_lines: list[str],
    text_preset: dict[str, Any],
) -> list[str]:
    escaped_font = font_path.replace(":", "\\:")
    text_filters: list[str] = []
    fontsize = int(text_preset.get("fontsize", SCENE_FONT_SIZE))
    line_spacing = int(text_preset.get("line_spacing", SCENE_LINE_SPACING))
    fade_delay = float(text_preset.get("fade_delay", TEXT_FADE_DELAY))
    fade_duration = float(text_preset.get("fade_duration", TEXT_FADE_DURATION))
    uppercase = bool(text_preset.get("uppercase", True))
    text_y = text_preset.get("text_y", SCENE_TEXT_START_Y)

    for index, line in enumerate(text_lines):
        display_line = line.strip()
        if uppercase:
            display_line = display_line.upper()
        premium_line = escape_drawtext(display_line)

        if text_y == "center":
            y_position = f"(h-text_h)/2+({index}*{line_spacing})"
        else:
            y_position = f"{text_y}+({index}*{line_spacing})"

        parts = [
            f"drawtext=fontfile={escaped_font}",
            f"text='{premium_line}'",
            f"fontcolor={text_preset.get('fontcolor', 'white')}",
            f"fontsize={fontsize}",
            "x=(w-text_w)/2",
            f"y={y_position}",
            (
                "alpha='if(lt(t\\,"
                f"{fade_delay})\\,0\\,"
                f"min((t-{fade_delay})/{fade_duration}\\,1))'"
            ),
        ]

        if text_preset.get("box"):
            parts.extend(
                [
                    "box=1",
                    f"boxcolor={text_preset.get('boxcolor', 'black@0.4')}",
                    f"boxborderw={int(text_preset.get('boxborderw', 20))}",
                ]
            )
        else:
            parts.append("box=0")

        if text_preset.get("shadowcolor"):
            parts.extend(
                [
                    f"shadowcolor={text_preset['shadowcolor']}",
                    f"shadowx={int(text_preset.get('shadowx', 2))}",
                    f"shadowy={int(text_preset.get('shadowy', 2))}",
                ]
            )

        if text_preset.get("borderw"):
            parts.extend(
                [
                    f"borderw={int(text_preset['borderw'])}",
                    f"bordercolor={text_preset.get('bordercolor', 'white@0.35')}",
                ]
            )

        text_filters.append(":".join(parts))

    return text_filters


def build_scene_filter(
    font_path: str,
    text_lines: list[str],
    preset: dict[str, Any],
    duration_frames: int,
) -> str:
    zoom = preset.get("zoom", {})
    z_expr = ffmpeg_escape_filter_expr(zoom.get("z", "min(zoom+0.001,1.15)"))
    x_expr = zoom.get("x", "iw/2-(iw/zoom/2)")
    y_expr = zoom.get("y", "ih/2-(ih/zoom/2)")

    base_filter = (
        f"loop={duration_frames}:1:0,"
        "format=yuv420p,"
        "scale=w=1620:h=2880:force_original_aspect_ratio=increase,"
        "crop=1620:2880,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={duration_frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={FRAMERATE}"
    )

    if not text_lines:
        return f"{base_filter},fps={FRAMERATE}"

    text_preset = preset.get("text", {})
    text_filters = build_drawtext_filters(font_path, text_lines, text_preset)
    return base_filter + "," + ",".join(text_filters) + f",fps={FRAMERATE}"


def build_scene_pipeline(
    num_images: int,
    font_path: str,
    scene_texts: list[list[str]],
    preset: dict[str, Any],
    img_duration: float,
    xfade_duration: float,
    duration_frames: int,
) -> tuple[str, float]:
    """Build preset-driven scene filters + xfade chain. Returns (filter_str, total_duration)."""
    filter_parts: list[str] = []

    for i in range(num_images):
        scene_filter = build_scene_filter(font_path, scene_texts[i], preset, duration_frames)
        filter_parts.append(f"[{i}:v]{scene_filter}[v_scene_{i}];")

    last_output = "[v_scene_0]"
    current_offset = img_duration - xfade_duration
    for i in range(1, num_images):
        next_label = f"[v_mix_{i}]" if i < num_images - 1 else "[v_images_merged]"
        filter_parts.append(
            f"{last_output}[v_scene_{i}]xfade=transition=fade:duration={xfade_duration}:"
            f"offset={current_offset}{next_label};"
        )
        last_output = next_label
        current_offset += img_duration - xfade_duration

    merge_filter = preset.get("filter", "").strip()
    if merge_filter:
        filter_parts.append(f"[v_images_merged]{merge_filter}[v_graded];")
    else:
        filter_parts.append("[v_images_merged]format=yuv420p[v_graded];")

    images_duration = (img_duration * num_images) - (xfade_duration * (num_images - 1))
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
    logo_input_idx: int,
    website_url: str = WEBSITE_URL,
    duration_frames: int = OUTRO_FRAMES,
) -> str:
    """
    Build outro on a pure black canvas so the logo blends cleanly without a harsh box.
    """
    escaped_font = font_path.replace(":", "\\:")
    clean_url = escape_drawtext(website_url.strip().upper())
    outro_duration = duration_frames / FRAMERATE

    return (
        f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FRAMERATE}:d={outro_duration}[bg];"
        f"[{logo_input_idx}:v]scale=380:-1[logo_scaled];"
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
    preset: dict[str, Any],
    debug_mode: bool = False,
) -> tuple[str, list[str], list[str], float]:
    scene_texts = assign_scene_texts(num_images, text_scene_1, text_scene_2)
    if not any(scene_texts):
        raise ValueError("Scene text is empty after sanitization")

    img_duration, xfade_duration, outro_duration, duration_frames = resolve_render_timing(
        debug_mode, num_images
    )
    outro_frames = int(outro_duration * FRAMERATE)

    image_filters, images_duration = build_scene_pipeline(
        num_images,
        font_path,
        scene_texts,
        preset,
        img_duration,
        xfade_duration,
        duration_frames,
    )

    outro_bg_idx = num_images
    music_idx = num_images + 1
    outro_offset = images_duration - xfade_duration
    total_duration = images_duration + outro_duration - xfade_duration

    if logo_path:
        outro_filters = (
            build_outro_with_logo_filter(
                font_path, num_images, WEBSITE_URL, outro_frames
            )
            + ";"
            + f"[v_graded][v_outro]xfade=transition=fade:duration={xfade_duration}:"
            f"offset={outro_offset}[v_final];"
        )
        outro_input = ["-i", str(logo_path)]
    else:
        outro_filters = (
            f"[{outro_bg_idx}:v]{build_outro_filter(font_path, outro_frames)}[v_outro];"
            f"[v_graded][v_outro]xfade=transition=fade:duration={xfade_duration}:"
            f"offset={outro_offset}[v_final];"
        )
        outro_input = [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:d={outro_duration}:r={FRAMERATE}",
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


def validate_local_image(path: Path) -> None:
    if not path.exists():
        raise RenderError(f"Image not found: {path}")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise RenderError(f"Image exceeds 10MB limit: {path}")
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as exc:
        raise RenderError(f"Invalid image file: {path}") from exc


def execute_render(
    image_paths: list[Path],
    text_scene_1: str,
    text_scene_2: str,
    video_title: str,
    bg_music: str = "luxury_chill",
    output_path: Path | None = None,
    style: str = DEFAULT_STYLE,
    debug_mode: bool = False,
) -> Path:
    if len(image_paths) < 2 or len(image_paths) > 4:
        raise RenderError("Please provide 2 to 4 images.")

    scene_1 = require_english_text(text_scene_1, "text_scene_1")
    scene_2 = require_english_text(text_scene_2, "text_scene_2")
    if not split_scene_lines(scene_1) or not split_scene_lines(scene_2):
        raise RenderError("text_scene_1 and text_scene_2 must contain valid plain text.")

    for image_path in image_paths:
        validate_local_image(image_path)

    preset = get_preset(style)
    try:
        font_path = resolve_font_for_preset(preset)
    except HTTPException as exc:
        raise RenderError(str(exc.detail)) from exc

    music_path = resolve_bg_music(bg_music)
    logo_path = resolve_logo_path()
    num_images = len(image_paths)

    filter_complex, outro_input, audio_input, total_duration = build_filter_complex(
        num_images,
        font_path,
        scene_1,
        scene_2,
        music_path,
        logo_path,
        preset,
        debug_mode=debug_mode,
    )

    if output_path is None:
        safe_title = sanitize_plain_text(video_title, max_chars=50)
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_title).strip("._") or "goldmoon_promo"
        output_path = APP_DIR / f"output_{slug}.mp4"
    else:
        output_path = Path(output_path)

    command = ["ffmpeg", "-y"]
    for img in image_paths:
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
            str(output_path),
        ]
    )

    try:
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderError("Rendering timeout.") from exc

    if process.returncode != 0:
        error_msg = process.stderr.decode(errors="replace").strip()
        raise RenderError(f"FFmpeg Error: {error_msg or process.returncode}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RenderError("Video rendering failed.")

    return output_path


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
    style = os.getenv("STYLE", DEFAULT_STYLE).strip().lower().replace("-", "_")
    debug_mode = os.getenv("DEBUG_MODE", "").strip().lower() in {"1", "true", "yes"}

    try:
        result = execute_render(
            image_paths=image_paths,
            text_scene_1=text_scene_1,
            text_scene_2=text_scene_2,
            video_title=video_title,
            bg_music=bg_music,
            output_path=output_path,
            style=style,
            debug_mode=debug_mode,
        )
    except RenderError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    print(f"Success: {result}")


@app.get("/health")
def health_check() -> dict:
    try:
        preset_names = list_preset_names()
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
            render_data = resolve_render_request(payload)

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
                    execute_render,
                    downloaded_images,
                    render_data["text_scene_1"],
                    render_data["text_scene_2"],
                    render_data["video_title"],
                    render_data["bg_music"],
                    output_video,
                    render_data["style"],
                    render_data["debug_mode"],
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


if __name__ == "__main__":
    run_n8n_cli()
