import os
import re
import subprocess
import textwrap
import uuid
from pathlib import Path

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Goldmoon Egypt Tours Video Renderer")

APP_DIR = Path(os.getenv("APP_DIR", "/app"))
ASSETS_DIR = APP_DIR / "assets"
SOUNDS_DIR = APP_DIR / "sounds"

TYPING_SFX = ASSETS_DIR / "typing.mp3"
FALLBACK_MUSIC = ASSETS_DIR / "music_epic.mp3"

MUSIC_SEARCH_DIRS = (ASSETS_DIR, SOUNDS_DIR, APP_DIR)

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FALLBACK_FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

VIDEO_DURATION = 6
FRAMERATE = 30
TOTAL_FRAMES = VIDEO_DURATION * FRAMERATE

FONT_SIZE = 54
WRAP_CHARS = 28

MAX_VIDEO_TITLE_CHARS = 50
MAX_SCENE_TEXT_CHARS = 40

BG_MUSIC_ALIASES = {
    "desert_ambient": "samuelfjohanns-egypt-expedition-a-mysterious-discovery-119128.mp3",
    "luxury_chill": "tunetank-vlog-beat-background-349853.mp3",
    "cinematic_epic": "samuelfjohanns-cinematic-duduk-192901.mp3",
}


class VideoRequest(BaseModel):
    image_url: str
    video_title: str = ""
    text_scene_1: str
    text_scene_2: str
    bg_music: str


def resolve_font_path() -> str:
    if Path(FONT_PATH).exists():
        return FONT_PATH
    if Path(FALLBACK_FONT).exists():
        return FALLBACK_FONT
    raise HTTPException(
        status_code=500,
        detail="No suitable bold system font found for text rendering.",
    )


def resolve_bg_music(music_filename: str) -> Path:
    alias_key = (music_filename or "").strip().lower()
    resolved_name = BG_MUSIC_ALIASES.get(alias_key, music_filename)

    raw_name = Path(resolved_name).name
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "", raw_name).strip()

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

    raise HTTPException(
        status_code=500,
        detail="Background music not found and fallback missing too.",
    )


def sanitize_plain_text(text: str, max_chars: int | None = None) -> str:
    """Strip unsafe characters before FFmpeg drawtext or filename use."""

    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = cleaned.replace('"', "").replace("\\", "")
    cleaned = re.sub(r"[^\w\s.,!?\-]", "", cleaned, flags=re.UNICODE)
    cleaned = cleaned.strip()
    if max_chars is not None:
        return cleaned[:max_chars].strip()
    return cleaned


def sanitize_scene_text(text: str) -> str:
    return sanitize_plain_text(text, max_chars=MAX_SCENE_TEXT_CHARS)


def escape_drawtext(text: str) -> str:
    """Escape remaining FFmpeg drawtext metacharacters."""

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
    plain_text = sanitize_scene_text(text)
    if not plain_text:
        return ""

    lines = textwrap.wrap(plain_text, width=width)
    if not lines:
        return ""

    # FFmpeg drawtext supports \n inside text option.
    return "\\n".join(escape_drawtext(line) for line in lines)


def build_video_filters(font_path: str, hook_text: str, cta_text: str) -> str:
    escaped_font = font_path.replace(":", "\\:")
    text_y = "(h-520)"

    # Critical rendering rules:
    # - eq only: eq=contrast=1.1:saturation=1.2
    # - vignette only: vignette=angle=0.5
    # - keep filters separated by commas
    # - drawtext/drawbox: use w/h (not iw/ih) for FFmpeg compatibility
    return (
        f"[0:v]scale=8000:-1,"
        f"zoompan=z='min(zoom+0.0015\\,1.3)':d={TOTAL_FRAMES}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920,"
        f"eq=contrast=1.1:saturation=1.2,"
        f"vignette=angle=0.5,"
        f"drawbox=y=(h-600):w=w:h=400:color=black@0.6:t=fill,"
        f"drawtext=fontfile={escaped_font}:text='{hook_text}':fontcolor=white:fontsize={FONT_SIZE}:"
        f"x=(w-text_w)/2:y={text_y}:line_spacing=8:"
        f"enable='between(t\\,0\\,3)':alpha='if(lt(t\\,0.5)\\,t*2\\,if(gt(t\\,2.5)\\,(3-t)*2\\,1))',"
        f"drawtext=fontfile={escaped_font}:text='{cta_text}':fontcolor=0x00D7FF:fontsize={FONT_SIZE}:"
        f"x=(w-text_w)/2:y={text_y}:line_spacing=8:"
        f"enable='between(t\\,3\\,6)':alpha='if(lt(t\\,3.5)\\,(t-3)*2\\,if(gt(t\\,5.5)\\,(6-t)*2\\,1))'[v]"
    )


def build_audio_filters(include_typing_sfx: bool) -> str:
    if include_typing_sfx:
        # Inputs:
        # [1:a] background music
        # [2:a] typing sfx
        return (
            "[1:a]volume=0.20,afade=t=out:st=5.5:d=0.5[bg];"
            "[2:a]asplit=2[sfx1_raw][sfx2_raw];"
            "[sfx1_raw]atrim=0:2,volume=0.8[sfx1];"
            "[sfx2_raw]atrim=0:2,adelay=3000|3000,volume=0.8[sfx2];"
            "[bg][sfx1][sfx2]amix=inputs=3:duration=first[a]"
        )

    # Inputs: [1:a] background music only
    return "[1:a]volume=0.20,afade=t=out:st=5.5:d=0.5[a]"


@app.post("/render")
def render_video(
    data: VideoRequest, background_tasks: BackgroundTasks
) -> FileResponse:
    unique_id = uuid.uuid4().hex
    input_image = APP_DIR / f"input_{unique_id}.jpg"
    output_video = APP_DIR / f"output_{unique_id}.mp4"

    typing_sfx = TYPING_SFX if TYPING_SFX.exists() else None
    bg_music = resolve_bg_music(data.bg_music)

    try:
        try:
            response = requests.get(data.image_url, timeout=15)
            if response.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail="Failed to download image from URL.",
                )
            input_image.write_bytes(response.content)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Image download error: {exc}",
            ) from exc

        font_path = resolve_font_path()
        hook_text = prepare_scene_text(data.text_scene_1)
        cta_text = prepare_scene_text(data.text_scene_2)

        if not hook_text or not cta_text:
            raise HTTPException(
                status_code=400,
                detail="text_scene_1 and text_scene_2 must contain valid plain text.",
            )

        video_filters = build_video_filters(font_path, hook_text, cta_text)
        audio_filters = build_audio_filters(include_typing_sfx=typing_sfx is not None)
        filter_complex = f"{video_filters};{audio_filters}"

        command = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(input_image),
            "-i",
            str(bg_music),
        ]
        if typing_sfx is not None:
            command.extend(["-i", str(typing_sfx)])

        command.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-t",
                str(VIDEO_DURATION),
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(FRAMERATE),
                "-movflags",
                "+faststart",
                str(output_video),
            ]
        )

        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:
            error_msg = exc.stderr.decode(errors="replace").strip()
            raise HTTPException(
                status_code=500,
                detail=f"FFmpeg Render Error: {error_msg or exc.returncode}",
            ) from exc

        if not output_video.exists() or output_video.stat().st_size == 0:
            raise HTTPException(
                status_code=500,
                detail="Video file was not created successfully.",
            )

        safe_title = sanitize_plain_text(
            data.video_title, max_chars=MAX_VIDEO_TITLE_CHARS
        )
        download_name = (
            f"{re.sub(r'[^A-Za-z0-9._-]+', '_', safe_title).strip('._')}.mp4"
            if safe_title
            else "goldmoon_viral_shorts.mp4"
        )

        background_tasks.add_task(output_video.unlink, missing_ok=True)
        return FileResponse(
            path=str(output_video),
            media_type="video/mp4",
            filename=download_name,
        )
    finally:
        input_image.unlink(missing_ok=True)
