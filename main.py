import io
import os
import subprocess
import tempfile
from pathlib import Path

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="Goldmoon Egypt Tours Video Renderer")

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
OVERLAY_TOP = 760
OVERLAY_BOTTOM = 1160
OVERLAY_ALPHA = 150
MAX_TEXT_WIDTH = 800
FONT_PATH = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
FONT_SIZE = 52
LINE_SPACING = 12
COLOR_HOOK = "white"
COLOR_CTA = "#FFD700"
SCENE_DURATION = 3
FRAMERATE = 24


class RenderRequest(BaseModel):
    image_url: HttpUrl
    text_scene_1: str
    text_scene_2: str


def load_font(size: int = FONT_SIZE) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = Path(FONT_PATH)
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size=size)
    return ImageFont.load_default()


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current_line = words[0]

    for word in words[1:]:
        candidate = f"{current_line} {word}"
        bbox = font.getbbox(candidate)
        line_width = bbox[2] - bbox[0]
        if line_width <= max_width:
            current_line = candidate
        else:
            lines.append(current_line)
            current_line = word

    lines.append(current_line)
    return lines


def measure_text_block(
    lines: list[str], font: ImageFont.FreeTypeFont
) -> tuple[int, int]:
    if not lines:
        return 0, 0

    max_width = 0
    total_height = 0

    for index, line in enumerate(lines):
        bbox = font.getbbox(line)
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        max_width = max(max_width, line_width)
        total_height += line_height
        if index < len(lines) - 1:
            total_height += LINE_SPACING

    return max_width, total_height


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    color: str,
    box_top: int,
    box_bottom: int,
) -> None:
    block_width, block_height = measure_text_block(lines, font)
    box_height = box_bottom - box_top
    start_y = box_top + (box_height - block_height) // 2

    current_y = start_y
    for line in lines:
        bbox = font.getbbox(line)
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        x = (CANVAS_WIDTH - line_width) // 2
        draw.text((x, current_y), line, font=font, fill=color)
        current_y += line_height + LINE_SPACING


def fetch_background_image(image_url: str) -> Image.Image:
    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
        return image
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch image from URL: {exc}",
        ) from exc
    except (IOError, OSError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unreadable image data: {exc}",
        ) from exc


def prepare_base_frame(background: Image.Image) -> Image.Image:
    resized = background.resize(
        (CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.LANCZOS
    )
    frame = resized.convert("RGBA")

    overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(
        [(0, OVERLAY_TOP), (CANVAS_WIDTH, OVERLAY_BOTTOM)],
        fill=(0, 0, 0, OVERLAY_ALPHA),
    )

    return Image.alpha_composite(frame, overlay)


def render_scene_frame(base_frame: Image.Image, text: str, color: str) -> Image.Image:
    frame = base_frame.copy()
    draw = ImageDraw.Draw(frame)
    font = load_font()
    lines = wrap_text(text, font, MAX_TEXT_WIDTH)
    draw_centered_text(
        draw, lines, font, color, OVERLAY_TOP, OVERLAY_BOTTOM
    )
    return frame.convert("RGB")


def compile_video(scene1_path: Path, scene2_path: Path, output_path: Path) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(FRAMERATE),
        "-t",
        str(SCENE_DURATION),
        "-i",
        str(scene1_path),
        "-loop",
        "1",
        "-framerate",
        str(FRAMERATE),
        "-t",
        str(SCENE_DURATION),
        "-i",
        str(scene2_path),
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FRAMERATE),
        str(output_path),
    ]

    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail="FFmpeg is not installed or not available in PATH.",
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"FFmpeg rendering failed with exit code {exc.returncode}.",
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
            scene1_path = work_dir / "scene1.jpg"
            scene2_path = work_dir / "scene2.jpg"

            background = fetch_background_image(str(payload.image_url))
            base_frame = prepare_base_frame(background)

            scene1 = render_scene_frame(base_frame, payload.text_scene_1, COLOR_HOOK)
            scene2 = render_scene_frame(base_frame, payload.text_scene_2, COLOR_CTA)

            scene1.save(scene1_path, format="JPEG", quality=95)
            scene2.save(scene2_path, format="JPEG", quality=95)

            compile_video(scene1_path, scene2_path, output_path)

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
            filename="goldmoon_short.mp4",
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
