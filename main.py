import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="Goldmoon Egypt Tours Video Renderer")

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
VIDEO_DURATION = 6
FRAMERATE = 30
TOTAL_FRAMES = VIDEO_DURATION * FRAMERATE
FONT_SIZE = 54
WRAP_CHARS = 28
OVERLAY_Y = 760
OVERLAY_H = 400
OVERLAY_CENTER_Y = OVERLAY_Y + OVERLAY_H // 2

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

COLOR_HOOK = "white"
COLOR_CTA = "0xFFD700"
SHADOW_COLOR = "black@0.55"
SHADOW_OFFSET = 3


class RenderRequest(BaseModel):
    image_url: HttpUrl
    text_scene_1: str
    text_scene_2: str


def resolve_font_path() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise HTTPException(
        status_code=500,
        detail="No suitable bold system font found for text rendering.",
    )


def escape_drawtext(text: str) -> str:
    escaped = text.strip()
    replacements = {
        "\\": "\\\\",
        ":": "\\:",
        "'": "\\'",
        "%": "\\%",
        "[": "\\[",
        "]": "\\]",
    }
    for source, target in replacements.items():
        escaped = escaped.replace(source, target)
    return escaped


def wrap_text_block(text: str, width: int = WRAP_CHARS) -> str:
    lines = textwrap.wrap(text.strip(), width=width)
    if not lines:
        return ""
    return "\\n".join(escape_drawtext(line) for line in lines)


def scene_alpha_expression(scene_index: int) -> str:
    if scene_index == 1:
        return "if(lt(t\\,0.5)\\,t*2\\,if(gt(t\\,2.5)\\,(3-t)*2\\,1))"
    return "if(lt(t\\,3.5)\\,(t-3)*2\\,if(gt(t\\,5.5)\\,(6-t)*2\\,1))"


def build_drawtext_layer(
    font_path: str,
    text: str,
    font_color: str,
    start: float,
    end: float,
    scene_index: int,
    shadow: bool = False,
) -> str:
    escaped_font = font_path.replace(":", "\\:")
    y_expr = f"({OVERLAY_CENTER_Y}-text_h/2)"
    if shadow:
        x_expr = f"(w-text_w)/2+{SHADOW_OFFSET}"
        y_expr = f"({OVERLAY_CENTER_Y}-text_h/2)+{SHADOW_OFFSET}"
        font_color = SHADOW_COLOR
    else:
        x_expr = "(w-text_w)/2"

    alpha = scene_alpha_expression(scene_index)
    return (
        f"drawtext=fontfile={escaped_font}:text='{text}':"
        f"fontcolor={font_color}:fontsize={FONT_SIZE}:"
        f"x={x_expr}:y={y_expr}:"
        f"line_spacing=8:"
        f"enable='between(t\\,{start}\\,{end})':"
        f"alpha='{alpha}'"
    )


def build_filter_complex(font_path: str, hook_text: str, cta_text: str) -> str:
    ken_burns = (
        f"scale=8000:-1,"
        f"zoompan=z='min(zoom+0.0015\\,1.35)':d={TOTAL_FRAMES}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:fps={FRAMERATE}"
    )
    color_grade = "eq=contrast=1.08:saturation=1.14:brightness=0.02:gamma=1.02"
    vignette = "vignette=angle=PI/4:mode=forward:a=0.38"
    overlay_box = (
        f"drawbox=x=0:y={OVERLAY_Y}:w=iw:h={OVERLAY_H}:"
        f"color=black@0.58:t=fill"
    )
    film_grain = "noise=c0s=5:c0f=t"

    hook_shadow = build_drawtext_layer(
        font_path, hook_text, COLOR_HOOK, 0, 3, 1, shadow=True
    )
    hook_main = build_drawtext_layer(font_path, hook_text, COLOR_HOOK, 0, 3, 1)
    cta_shadow = build_drawtext_layer(
        font_path, cta_text, COLOR_CTA, 3, 6, 2, shadow=True
    )
    cta_main = build_drawtext_layer(font_path, cta_text, COLOR_CTA, 3, 6, 2)

    return (
        f"[0:v]{ken_burns},{color_grade},{vignette},"
        f"{overlay_box},{film_grain},"
        f"{hook_shadow},{hook_main},"
        f"{cta_shadow},{cta_main}[v]"
    )


def download_image(image_url: str, destination: Path) -> None:
    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        destination.write_bytes(response.content)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch image from URL: {exc}",
        ) from exc


def render_cinematic_video(
    input_path: Path, output_path: Path, hook_text: str, cta_text: str
) -> None:
    font_path = resolve_font_path()
    wrapped_hook = wrap_text_block(hook_text)
    wrapped_cta = wrap_text_block(cta_text)
    filter_complex = build_filter_complex(font_path, wrapped_hook, wrapped_cta)

    command = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(input_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-t",
        str(VIDEO_DURATION),
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FRAMERATE),
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail="FFmpeg is not installed or not available in PATH.",
        ) from exc
    except subprocess.CalledProcessError as exc:
        error_msg = exc.stderr.decode(errors="replace").strip()
        raise HTTPException(
            status_code=500,
            detail=f"FFmpeg rendering failed: {error_msg or exc.returncode}",
        ) from exc


@app.post("/render")
def render_video(
    payload: RenderRequest, background_tasks: BackgroundTasks
) -> FileResponse:
    output_handle = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    output_path = Path(output_handle.name)
    output_handle.close()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            input_image = work_dir / "input.jpg"

            download_image(str(payload.image_url), input_image)
            render_cinematic_video(
                input_image,
                output_path,
                payload.text_scene_1,
                payload.text_scene_2,
            )

        if not output_path.exists() or output_path.stat().st_size == 0:
            output_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500,
                detail="Video file was not created successfully.",
            )

        background_tasks.add_task(os.unlink, output_path)
        return FileResponse(
            path=str(output_path),
            media_type="video/mp4",
            filename="goldmoon_video.mp4",
        )
    except HTTPException:
        output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=f"Video rendering failed: {exc}",
        ) from exc
