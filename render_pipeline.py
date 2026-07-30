"""Modular FFmpeg rendering pipeline for Goldmoon Video API."""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import textwrap
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from tts import TTSError, get_audio_duration, synthesize_voiceover

APP_DIR = Path(os.getenv("APP_DIR", "/app"))
ASSETS_DIR = APP_DIR / "assets"
SOUNDS_DIR = APP_DIR / "sounds"

FALLBACK_MUSIC = ASSETS_DIR / "music_epic.mp3"
MUSIC_SEARCH_DIRS = (ASSETS_DIR, SOUNDS_DIR, APP_DIR)

CUSTOM_FONT = APP_DIR / "PlayfairDisplay-Regular.ttf"
MONTSERRAT_FONT = ASSETS_DIR / "Montserrat-Bold.ttf"
OSWALD_FONT = ASSETS_DIR / "Oswald-Bold.ttf"
LOGO_PATH = Path(os.getenv("LOGO_PATH", str(ASSETS_DIR / "logo.png")))
SUBSCRIBE_ICON_PATH = Path(
    os.getenv("SUBSCRIBE_ICON_PATH", str(ASSETS_DIR / "subscribe_icon.png"))
)

# Shared cinematic-grade LUT (see scripts/generate_luts.py) applied on top of
# every preset's own color filter so all styles read as if shot on the same
# film stock, instead of each preset's differing eq/colorchannelmixer chain
# producing a visibly different color response.
CINEMATIC_LUT_PATH = ASSETS_DIR / "luts" / "cinematic_film.cube"
FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FALLBACK_FONT_ALT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

# Internal supersample resolution: scenes are composited/zoomed at 1.5x the
# final size and downscaled on output, so a slow zoom never softens the image.
SUPERSAMPLE_W = 1620
SUPERSAMPLE_H = 2880

# Scene framing modes:
#   "reveal" (default) - fill the ENTIRE frame (no letterbox bars) with the
#            source scaled to cover, then slowly pan across it so the parts a
#            static 9:16 crop would hide are revealed over the scene. A wide
#            landscape photo therefore fills the screen AND is seen in full.
#   "fit"  - show the WHOLE source image at once fit to the frame width, with a
#            blurred, darkened copy of the same image filling the letterbox
#            area (bars top/bottom), plus a gentle cinematic zoom-OUT.
#   "fill" - the legacy behavior: scale to cover the 9:16 frame and crop to a
#            detected focus point, with the preset's own (usually zoom-in) move.
DEFAULT_FRAME_MODE = "reveal"
FRAME_MODES = ("reveal", "fit", "fill")

# Sharpening applied after the upscale so full-screen framing of lower-res
# source photos still reads crisp (luma-only mild unsharp mask).
UNSHARP_FILTER = "unsharp=5:5:0.5:5:5:0.0"

# "reveal" pan: how far across the covered image to travel, as a fraction of the
# full overshoot. Half a traversal reads as a calm, deliberate camera move; a
# full traversal feels like the image is racing past.
REVEAL_PAN_FRACTION = 0.5

# Trim a few pixels off every source edge before framing. Some source photos
# carry a thin baked-in border (e.g. a red/black edge from re-encoding) that the
# old center-crop hid but the full-frame reveal would otherwise expose.
EDGE_TRIM_PX = 14

# Blurred-fill ("fit") background is blurred from a small copy then upscaled -
# visually identical to blurring the full-res image but far cheaper.
FIT_BG_W = 270
FIT_BG_H = 480
FIT_BG_BLUR_SIGMA = 12
FIT_BG_BRIGHTNESS = -0.14
FIT_BG_SATURATION = 1.15
# Gentle pull-back so the full frame is revealed by the end of each scene.
FIT_ZOOM_START = 1.12
FIT_ZOOM_END = 1.0

IMG_DURATION = 4.0
XFADE_DURATION = 0.5
FRAMERATE = 30
DURATION_FRAMES = int(IMG_DURATION * FRAMERATE)
# When a voiceover is present, scenes are stretched/shrunk to match its
# actual length instead of the fixed IMG_DURATION, so on-screen captions
# stay in sync with the narration. Clamped so a very short or very long
# script doesn't produce scenes too fast for the Ken Burns pan/zoom and
# text fades, or so long they look static.
MIN_VOICEOVER_IMG_DURATION = 2.5
MAX_VOICEOVER_IMG_DURATION = 8.0
MAX_IMAGE_BYTES = 10 * 1024 * 1024
FFMPEG_TIMEOUT = 600
WRAP_CHARS = 28
SCENE_FONT_SIZE = 46
SCENE_TEXT_START_Y = 1470
SCENE_LINE_SPACING = 85
TEXT_FADE_DELAY = 0.3
TEXT_FADE_DURATION = 0.5
# Caption legibility: every caption gets a drop-shadow, and any background box a
# style defines is bumped to at least a readable opacity with generous padding,
# so text stays clear over bright skies/pale stone regardless of the font.
DEFAULT_CAPTION_SHADOW = "black@0.75"
CAPTION_MIN_BOX_OPACITY = 0.55
CAPTION_MIN_BOX_PADDING = 26

OUTRO_DURATION = 2.0
OUTRO_FRAMES = int(OUTRO_DURATION * FRAMERATE)
DEFAULT_WEBSITE_URL = "https://www.goldmoontours.com/en"
OUTRO_URL_FADE_DELAY = 0.55
OUTRO_URL_FADE_DURATION = 0.5
OUTRO_URL_FONT_SIZE = 38
OUTRO_URL_Y = 1180
OUTRO_URL_COLOR = "#F7941D"
LOGO_FADE_DURATION = 0.6
LOGO_RISE_DISTANCE = 40

# Outro card styling: centered logo, a brand-orange divider beneath it, a
# call-to-action line, then the website URL. The divider "wipes" open in step
# with the logo fade so the card assembles rather than popping in.
OUTRO_LOGO_WIDTH = 780
OUTRO_DIVIDER_Y = 1020
OUTRO_DIVIDER_WIDTH = 320
OUTRO_DIVIDER_THICKNESS = 4

# Call-to-action shown on the outro (visual only - it never touches the
# voiceover or the scene captions). Set to "" (or pass outro_cta="") to hide.
DEFAULT_OUTRO_CTA = "Book Your Journey"
OUTRO_CTA_Y = 1085
OUTRO_CTA_FONT_SIZE = 52
OUTRO_CTA_COLOR = "white"
OUTRO_CTA_FADE_DELAY = 0.35
OUTRO_CTA_FADE_DURATION = 0.5

# Loudness target for the final audio master (EBU R128). Applied once to the
# fully mixed track so every video lands at the same professional level.
LOUDNORM_FILTER = "loudnorm=I=-14:TP=-1.5:LRA=11"

# Subscribe-button watermark: bottom-anchored, shown briefly at the very
# start of the video and again over the outro. Only applied when
# SUBSCRIBE_ICON_PATH exists (same "static asset present = always used"
# convention as the logo).
SUBSCRIBE_ICON_WIDTH = 300
SUBSCRIBE_ICON_BOTTOM_MARGIN = 70
SUBSCRIBE_ICON_FADE_DURATION = 0.35
SUBSCRIBE_ICON_HOLD_DURATION = 2.0

DEFAULT_STYLE = "desert_safari"
DEBUG_TOTAL_DURATION = 3.0

PRESETS_PATH = ASSETS_DIR / "presets.json"
LOCAL_PRESETS_PATH = Path(__file__).resolve().parent / "assets" / "presets.json"

FONT_PRESET_MAP = {
    "classic": CUSTOM_FONT,
    "script": CUSTOM_FONT,
    "thin": MONTSERRAT_FONT,
    "simple": MONTSERRAT_FONT,
    "bold": OSWALD_FONT,
}

# Named text-overlay style modes. Each supplies drawtext-preset overrides
# applied on top of the active style preset's own text config. `glassmorphism`
# is approximated as a translucent box behind the text (FFmpeg's drawtext has
# no true backdrop-blur) rather than a literal frosted-glass blur.
TEXT_STYLE_MODES = {
    "minimalist": {
        "box": 0,
        "shadowcolor": None,
        "uppercase": False,
    },
    "glassmorphism": {
        "box": 1,
        "boxcolor": "black@0.45",
        "boxborderw": 30,
        "shadowcolor": "black@0.4",
        "shadowx": 2,
        "shadowy": 2,
        "uppercase": False,
    },
    "bold": {
        "box": 0,
        "shadowcolor": "black@0.9",
        "shadowx": 4,
        "shadowy": 4,
        "uppercase": True,
    },
}

BG_MUSIC_ALIASES = {
    "desert_ambient": "samuelfjohanns-egypt-expedition-a-mysterious-discovery-119128.mp3",
    "luxury_chill": "tunetank-vlog-beat-background-349853.mp3",
    "cinematic_epic": "samuelfjohanns-cinematic-duduk-192901.mp3",
    "arab_trailer": "alex-morgan-arab-trailer-545516.mp3",
    "vlog_energetic": "bombinsound-vlog-youtube-499475.mp3",
    "cairo_nights": "elijah_k-cairo-500585.mp3",
    "egyptian_desert": "gr0za-egyptian-egypt-desert-music-557539.mp3",
    "desert_travels": "grand_project-desert-travels-391123.mp3",
    "ancient_mystique": "onetent-ancient-181070.mp3",
    "ancient_empire": "the_mountain-ancient-empire-142301.mp3",
    "motivation_energy": "jonasblakewood-motivation-music-557632.mp3",
    "fashion_house": "kulakovka-fashion-house-275628.mp3",
    "summer_breeze": "the_mountain-summer-513165.mp3",
    "tropical_vibes": "the_mountain-tropical-tropical-music-508038.mp3",
    "summer_dance": "white_records-short-background-music-for-video-vlog-summer-dance-tropical-house-158706.mp3",
}

# Curated FFmpeg xfade transitions. One is chosen per render (not per cut) so a
# single video keeps a consistent, professional transition language while
# successive renders still look distinct from one another.
TRANSITION_POOL = [
    "fade",
    "fadeblack",
    "fadewhite",
    "dissolve",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "circleopen",
    "circleclose",
    "radial",
    "diagtl",
    "diagbr",
]

# Text overlay entrance animations. Chosen once per render (all scene texts in
# a video share it) for a coherent look; timing still comes from each preset's
# fade_delay/fade_duration. scale_fade/slide_scale additionally animate
# fontsize (a true "kinetic type" pop-in), the others only move/fade.
TEXT_ANIMATIONS = ["fade", "slide_up", "slide_down", "rise_fade", "scale_fade", "slide_scale"]
TEXT_SLIDE_DISTANCE = 46.0
TEXT_SLIDE_DISTANCE_SUBTLE = 20.0
TEXT_SCALE_START_FACTOR = 0.72

_preset_cache: dict[str, Any] | None = None


class RenderError(Exception):
    """Raised when video rendering fails."""


def normalize_style_name(style_name: str) -> str:
    return style_name.strip().lower().replace("-", "_").replace(" ", "_")


def load_presets() -> dict[str, Any]:
    global _preset_cache
    if _preset_cache is None:
        for candidate in (PRESETS_PATH, LOCAL_PRESETS_PATH, APP_DIR / "presets.json"):
            if candidate.exists():
                with candidate.open(encoding="utf-8") as handle:
                    _preset_cache = json.load(handle)
                break
        if _preset_cache is None:
            raise RenderError(
                f"Presets file not found. Expected at {PRESETS_PATH} or {LOCAL_PRESETS_PATH}"
            )
    return _preset_cache


def get_preset(style_name: str = "") -> dict[str, Any]:
    """Load a preset by style name; pick randomly when empty or unknown."""
    _, preset = resolve_preset(style_name)
    return preset


def resolve_preset(style_name: str = "") -> tuple[str, dict[str, Any]]:
    presets = load_presets()
    key = normalize_style_name(style_name)

    if style_name and key not in presets:
        raise RenderError(
            f"Style '{style_name}' (normalized as '{key}') not found. "
            f"Available styles are: {list(presets.keys())}"
        )

    if not key or key not in presets:
        key = random.choice(sorted(presets))
        print(f"No style specified. Randomly selected: {key}")

    return key, presets[key]


def list_preset_names() -> list[str]:
    return sorted(load_presets())


def pick_transition(requested: str | None = None, style_default: str | None = None) -> str:
    """Resolve the scene-to-scene transition for a render.

    Priority: an explicit, valid `requested` value > the active style
    preset's own signature transition (`style_default`, from presets.json)
    > a random pick from TRANSITION_POOL when neither is available.
    """
    if requested:
        normalized = normalize_style_name(requested)
        if normalized not in TRANSITION_POOL:
            raise RenderError(
                f"Unknown transition '{requested}'. Choose one of: "
                f"{', '.join(TRANSITION_POOL)}"
            )
        return normalized
    if style_default and style_default in TRANSITION_POOL:
        return style_default
    return random.choice(TRANSITION_POOL)


def pick_text_animation(requested: str | None = None) -> str:
    """Resolve the text entrance animation for a render.

    An explicit, valid value is honored; otherwise one is chosen at random
    from TEXT_ANIMATIONS so every render feels distinct by default.
    """
    if requested:
        normalized = normalize_style_name(requested)
        if normalized not in TEXT_ANIMATIONS:
            raise RenderError(
                f"Unknown text animation '{requested}'. Choose one of: "
                f"{', '.join(TEXT_ANIMATIONS)}"
            )
        return normalized
    return random.choice(TEXT_ANIMATIONS)


def resolve_text_style(
    text_preset: dict[str, Any], text_style: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge an optional `text_style` override onto a style preset's text config.

    Priority: named `mode` (minimalist/glassmorphism/bold) applies first, then
    explicit `font`/`color`/`shadow`/`box_opacity` fields override individual
    fields on top of it. Returns the preset's own text config unchanged when
    no override is given.
    """
    if not text_style:
        return text_preset

    merged = dict(text_preset)

    mode = normalize_style_name(text_style.get("mode") or "")
    if mode:
        if mode not in TEXT_STYLE_MODES:
            raise RenderError(
                f"Unknown text_style mode '{text_style['mode']}'. Choose one of: "
                f"{', '.join(TEXT_STYLE_MODES)}"
            )
        for key, value in TEXT_STYLE_MODES[mode].items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value

    if text_style.get("font"):
        merged["font"] = text_style["font"]

    if text_style.get("color"):
        merged["fontcolor"] = text_style["color"]

    if text_style.get("box_opacity") is not None:
        opacity = float(text_style["box_opacity"])
        merged["box"] = 1
        merged["boxcolor"] = f"black@{opacity}"

    shadow = text_style.get("shadow")
    if shadow is True and not merged.get("shadowcolor"):
        merged["shadowcolor"] = "black@0.6"
        merged.setdefault("shadowx", 2)
        merged.setdefault("shadowy", 2)
    elif shadow is False:
        merged.pop("shadowcolor", None)

    return merged


def apply_zoom_override(
    preset: dict[str, Any], zoom_override: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge optional start/end zoom-level overrides onto a style preset's
    zoom config, e.g. from a per-tour Sanity override. Leaves pan (x/y)
    untouched. Returns the preset unchanged when no override is given.
    """
    if not zoom_override:
        return preset

    zoom = dict(preset.get("zoom", {}))
    if zoom_override.get("start") is not None:
        zoom["start"] = float(zoom_override["start"])
    if zoom_override.get("end") is not None:
        zoom["end"] = float(zoom_override["end"])
    return {**preset, "zoom": zoom}


def sanitize_plain_text(text: str, max_chars: int | None = None) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = cleaned.replace('"', "").replace("\\", "")
    cleaned = re.sub(r"[^\w\s.,!?\-]", "", cleaned, flags=re.UNICODE).strip()
    if max_chars is not None:
        return cleaned[:max_chars].strip()
    return cleaned


def require_english_text(text: str, field_name: str, max_chars: int = 60) -> str:
    cleaned = sanitize_plain_text(text, max_chars=max_chars)
    if not cleaned or not re.fullmatch(r"[A-Za-z0-9\s.,!?\-]+", cleaned):
        raise RenderError(f"{field_name} must contain English plain text only.")
    return cleaned


def safe_output_filename(video_title: str) -> str:
    safe_title = sanitize_plain_text(video_title, max_chars=50)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_title).strip("._")
    return f"{slug or 'goldmoon_promo'}.mp4"


def resolve_render_timing(
    debug_mode: bool,
    num_images: int,
    voiceover_duration: float | None = None,
) -> tuple[float, float, float, int]:
    if debug_mode:
        xfade_duration = 0.25
        outro_duration = 0.75
        img_duration = (
            DEBUG_TOTAL_DURATION - outro_duration + xfade_duration + (num_images - 1) * xfade_duration
        ) / num_images
        return img_duration, xfade_duration, outro_duration, int(img_duration * FRAMERATE)
    if voiceover_duration:
        xfade_duration = XFADE_DURATION
        outro_duration = OUTRO_DURATION
        # Solve for the per-scene duration that makes the crossfaded scene
        # sequence (see build_scene_pipeline's images_duration formula) add
        # up to the voiceover's length, so captions and narration line up.
        raw_img_duration = (
            voiceover_duration + (num_images - 1) * xfade_duration
        ) / num_images
        img_duration = max(
            MIN_VOICEOVER_IMG_DURATION, min(MAX_VOICEOVER_IMG_DURATION, raw_img_duration)
        )
        return img_duration, xfade_duration, outro_duration, int(img_duration * FRAMERATE)
    return IMG_DURATION, XFADE_DURATION, OUTRO_DURATION, DURATION_FRAMES


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
    raise RenderError("No suitable bold system font found.")


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


def resolve_logo_path() -> Path | None:
    if LOGO_PATH.exists():
        return LOGO_PATH
    return None


def resolve_subscribe_icon_path() -> Path | None:
    if SUBSCRIBE_ICON_PATH.exists():
        return SUBSCRIBE_ICON_PATH
    return None


def ffmpeg_escape_filter_expr(expr: str) -> str:
    return expr.replace(",", "\\,")


def escape_lut_path(path: Path) -> str:
    """Format a filesystem path for use as lut3d=file='...'.

    Forward slashes work on every platform FFmpeg runs on (including
    Windows), which sidesteps the need to escape backslashes or the drive
    letter's colon. Wrapping in single quotes lets the raw colon/slashes
    pass through the filtergraph parser without further escaping.
    """
    posix_path = str(path).replace("\\", "/").replace("'", "")
    return f"'{posix_path}'"


def eased_progress_expr(delay: float, duration: float) -> str:
    """FFmpeg expr: 0->1 progress starting at `delay`, eased in/out via a
    cosine curve over `duration` seconds, using the filter's time var `t`.

    Commas are pre-escaped (\\,) for embedding directly inside a filtergraph
    option value, matching this module's existing expression style.
    """
    if duration <= 0:
        return f"if(lt(t\\,{delay})\\,0\\,1)"
    return (
        f"if(lt(t\\,{delay})\\,0\\,"
        f"if(lt(t\\,{delay + duration})\\,"
        f"(0.5-0.5*cos(PI*(t-{delay})/{duration}))\\,1))"
    )


def build_text_alpha_expr(
    fade_delay: float, fade_duration: float, scene_duration: float, xfade_duration: float
) -> str:
    """Ease text in at `fade_delay`, hold, then ease back out so it's fully
    transparent before this scene starts crossfading into the next clip.

    Without a fade-out, the caption stays at full opacity right up to the
    scene's last frame, so it visibly ghosts through the xfade dissolve into
    the next scene (or the outro card). fade-in and fade-out progress are
    each 0->1 ramps computed independently, then combined with min() so the
    result is whichever ramp is currently "more hidden".
    """
    fade_out_duration = fade_duration
    fade_out_start = max(
        fade_delay + fade_duration, scene_duration - xfade_duration - fade_out_duration
    )
    fade_in = eased_progress_expr(fade_delay, fade_duration)
    fade_out_ramp = eased_progress_expr(fade_out_start, fade_out_duration)
    return f"min({fade_in}\\,(1-({fade_out_ramp})))"


def ease_in_out_ratio_expr(progress_expr: str) -> str:
    """Cosine ease-in-out over an already-computed 0..1 progress expr."""
    return f"(0.5-0.5*cos(PI*{progress_expr}))"


def build_eased_zoom_expr(zoom_cfg: dict[str, Any], duration_frames: int) -> str:
    """Build an absolute (not incremental) zoompan `z` expression that moves
    from `start` to `end` across the clip at a constant rate, based on the
    output frame number `on` and the known clip length in frames.

    Uses linear (not eased) progress on purpose: a real dolly/zoom move
    holds a steady speed rather than accelerating through the middle of the
    shot, which is what reads as "cinematic" rather than a UI-style
    ease-in-out bounce.

    Falls back to a raw `z` string from the preset for backward
    compatibility if `start`/`end` aren't present.
    """
    end = zoom_cfg.get("end")
    if end is None:
        return ffmpeg_escape_filter_expr(zoom_cfg.get("z", "1"))

    start = float(zoom_cfg.get("start", 1.0))
    end = float(end)
    if duration_frames <= 1 or start == end:
        return str(end)

    progress = f"min(on/{duration_frames - 1}\\,1)"
    return f"{start}+({end}-{start})*{progress}"


def build_eased_pan_expr(expr: str, duration_frames: int) -> str:
    """Replace `on`-based pan drift (e.g. `on*0.7`) in a zoompan x/y
    expression with a duration-clamped frame count, so panning holds the
    same constant rate as the zoom (see build_eased_zoom_expr) instead of
    running past the clip's last frame. No-op if `on` isn't referenced.
    """
    if not expr or not re.search(r"\bon\b", expr):
        return expr
    clamped_on = "0" if duration_frames <= 1 else f"min(on\\,{duration_frames - 1})"
    return re.sub(r"\bon\b", clamped_on, expr)


def format_outro_website_text(url: str) -> str:
    return (url or "").strip() or DEFAULT_WEBSITE_URL


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


def build_outro_url_drawtext(escaped_font: str, website_url: str, fade: bool = True) -> str:
    clean_url = escape_drawtext(format_outro_website_text(website_url))
    parts = [
        f"drawtext=fontfile={escaped_font}",
        f"text='{clean_url}'",
        f"fontcolor={OUTRO_URL_COLOR}",
        f"fontsize={OUTRO_URL_FONT_SIZE}",
        "x=(w-text_w)/2",
        f"y={OUTRO_URL_Y}",
        "shadowcolor=black@0.9",
        "shadowx=2",
        "shadowy=2",
    ]
    if fade:
        parts.append(
            "alpha='if(lt(t\\,"
            f"{OUTRO_URL_FADE_DELAY})\\,0\\,"
            f"min((t-{OUTRO_URL_FADE_DELAY})/{OUTRO_URL_FADE_DURATION}\\,1))'"
        )
    return ":".join(parts)


def build_outro_cta_drawtext(escaped_font: str, cta_text: str, fade: bool = True) -> str:
    """Bold, uppercase call-to-action line for the outro card. Visual only."""
    clean_cta = escape_drawtext(sanitize_plain_text(cta_text, max_chars=40).upper())
    parts = [
        f"drawtext=fontfile={escaped_font}",
        f"text='{clean_cta}'",
        f"fontcolor={OUTRO_CTA_COLOR}",
        f"fontsize={OUTRO_CTA_FONT_SIZE}",
        "x=(w-text_w)/2",
        f"y={OUTRO_CTA_Y}",
        "shadowcolor=black@0.9",
        "shadowx=2",
        "shadowy=2",
    ]
    if fade:
        parts.append(
            "alpha='if(lt(t\\,"
            f"{OUTRO_CTA_FADE_DELAY})\\,0\\,"
            f"min((t-{OUTRO_CTA_FADE_DELAY})/{OUTRO_CTA_FADE_DURATION}\\,1))'"
        )
    return ":".join(parts)


def split_scene_lines(text: str, max_lines: int = 2) -> list[str]:
    plain_text = sanitize_plain_text(text, max_chars=60)
    if not plain_text:
        return []
    return textwrap.wrap(plain_text, width=WRAP_CHARS)[:max_lines]


def assign_scene_texts(num_images: int, scene_texts: list[str]) -> list[list[str]]:
    """Map each image index to its scene text and split into drawtext lines.

    If scene_texts has fewer entries than num_images, the last text is reused
    for all remaining images rather than raising an error.
    """
    result: list[list[str]] = []
    for i in range(num_images):
        raw = scene_texts[i] if i < len(scene_texts) else scene_texts[-1]
        result.append(split_scene_lines(raw))
    return result


def build_text_offset_expr(
    animation: str, fade_delay: float, fade_duration: float
) -> str | None:
    """Return a time-based y-offset expression for the given text animation.

    The offset starts at `dist` and eases to 0 in step with the alpha fade
    (cosine ease-in-out over [fade_delay, fade_delay + fade_duration]), so
    the text visually settles into its resting position at the same moment
    it becomes fully opaque. Returns None for plain "fade" (no movement).
    """
    if animation in ("slide_up", "slide_scale"):
        dist = TEXT_SLIDE_DISTANCE
    elif animation == "rise_fade":
        dist = TEXT_SLIDE_DISTANCE_SUBTLE
    elif animation == "slide_down":
        dist = -TEXT_SLIDE_DISTANCE
    else:
        return None

    progress = eased_progress_expr(fade_delay, fade_duration)
    return f"({dist}*(1-{progress}))"


def build_text_fontsize_expr(
    animation: str, base_fontsize: int, fade_delay: float, fade_duration: float
) -> str:
    """Return a fontsize expression that eases in from a smaller size for
    "kinetic" scale-in animations, or a plain constant otherwise.
    """
    if animation not in ("scale_fade", "slide_scale"):
        return str(base_fontsize)

    start_size = max(1, round(base_fontsize * TEXT_SCALE_START_FACTOR))
    progress = eased_progress_expr(fade_delay, fade_duration)
    return f"({start_size}+({base_fontsize}-{start_size})*{progress})"


def ensure_box_opacity(boxcolor: str, minimum: float = CAPTION_MIN_BOX_OPACITY) -> str:
    """Raise a caption box's alpha to at least `minimum` for legibility.

    Accepts FFmpeg color forms like "black@0.35" (bumped to "black@0.55") and
    leaves a fully-opaque color (no "@alpha") untouched.
    """
    match = re.match(r"^(.*?)@([0-9]*\.?[0-9]+)$", boxcolor.strip())
    if not match:
        return boxcolor
    base, alpha = match.group(1), float(match.group(2))
    return f"{base}@{max(alpha, minimum):g}"


def build_drawtext_filters(
    font_path: str,
    text_lines: list[str],
    text_preset: dict[str, Any],
    animation: str = "fade",
    scene_duration: float | None = None,
    xfade_duration: float = 0.0,
) -> list[str]:
    escaped_font = font_path.replace(":", "\\:")
    text_filters: list[str] = []
    fontsize = int(text_preset.get("fontsize", SCENE_FONT_SIZE))
    line_spacing = int(text_preset.get("line_spacing", SCENE_LINE_SPACING))
    fade_delay = float(text_preset.get("fade_delay", TEXT_FADE_DELAY))
    fade_duration = float(text_preset.get("fade_duration", TEXT_FADE_DURATION))
    uppercase = bool(text_preset.get("uppercase", True))
    text_y = text_preset.get("text_y", SCENE_TEXT_START_Y)
    offset_expr = build_text_offset_expr(animation, fade_delay, fade_duration)
    fontsize_expr = build_text_fontsize_expr(animation, fontsize, fade_delay, fade_duration)
    if scene_duration:
        alpha_expr = build_text_alpha_expr(fade_delay, fade_duration, scene_duration, xfade_duration)
    else:
        alpha_expr = eased_progress_expr(fade_delay, fade_duration)

    for index, line in enumerate(text_lines):
        display_line = line.strip()
        if uppercase:
            display_line = display_line.upper()
        premium_line = escape_drawtext(display_line)

        if text_y == "center":
            y_position = f"(h-text_h)/2+({index}*{line_spacing})"
        else:
            y_position = f"{text_y}+({index}*{line_spacing})"

        if offset_expr:
            y_position = f"({y_position})+({offset_expr})"

        parts = [
            f"drawtext=fontfile={escaped_font}",
            f"text='{premium_line}'",
            f"fontcolor={text_preset.get('fontcolor', 'white')}",
            f"fontsize={fontsize_expr}",
            "x=(w-text_w)/2",
            f"y={y_position}",
            f"alpha='{alpha_expr}'",
        ]

        if text_preset.get("box"):
            parts.extend(
                [
                    "box=1",
                    f"boxcolor={ensure_box_opacity(text_preset.get('boxcolor', 'black@0.4'))}",
                    f"boxborderw={max(int(text_preset.get('boxborderw', 20)), CAPTION_MIN_BOX_PADDING)}",
                ]
            )
        else:
            parts.append("box=0")

        # Always give the caption a drop-shadow (use the style's own if defined,
        # otherwise a strong default) so text separates from the background even
        # when the font is thin or the box is translucent.
        if text_preset.get("shadowcolor"):
            parts.extend(
                [
                    f"shadowcolor={text_preset['shadowcolor']}",
                    f"shadowx={int(text_preset.get('shadowx', 2))}",
                    f"shadowy={int(text_preset.get('shadowy', 2))}",
                ]
            )
        else:
            parts.extend(
                [f"shadowcolor={DEFAULT_CAPTION_SHADOW}", "shadowx=2", "shadowy=2"]
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


def build_focus_crop_expr(
    focus_point: tuple[float, float], crop_w: int = 1620, crop_h: int = 2880
) -> tuple[str, str]:
    """Build crop filter x/y expressions that keep `focus_point` (normalized
    fx, fy over the scaled image) centered in the crop window, clamped so
    the window never runs off the edge of the image.
    """
    fx, fy = focus_point
    x_expr = f"clip({fx}*iw-{crop_w / 2}\\,0\\,iw-{crop_w})"
    y_expr = f"clip({fy}*ih-{crop_h / 2}\\,0\\,ih-{crop_h})"
    return x_expr, y_expr


def build_reveal_pan_exprs(duration_frames: int, scene_index: int) -> tuple[str, str]:
    """Crop x/y expressions that pan a full-frame window across the covered
    image over the scene, cosine-eased, so the whole photo is revealed.

    Cover-scaling to the frame with force_original_aspect_ratio=increase makes
    exactly one axis overflow (the other equals the frame), so only that axis
    actually moves - `iw-VIDEO_WIDTH` is 0 for a portrait source and positive
    for a landscape one, and vice-versa for `ih-VIDEO_HEIGHT`. The pan
    direction alternates per scene so successive shots don't all drift the same
    way, which reads as more deliberate camera work.
    """
    last = max(duration_frames - 1, 1)
    progress = f"min(n/{last}\\,1)"
    eased = f"(0.5-0.5*cos(PI*{progress}))"
    if scene_index % 2 == 1:
        eased = f"(1-{eased})"
    travel = REVEAL_PAN_FRACTION
    x_expr = f"(iw-{VIDEO_WIDTH})*{travel}*{eased}"
    y_expr = f"(ih-{VIDEO_HEIGHT})*{travel}*{eased}"
    return x_expr, y_expr


def build_reveal_vf_filter(
    font_path: str,
    text_lines: list[str],
    preset: dict[str, Any],
    duration_frames: int,
    animation: str = "fade",
    xfade_duration: float = 0.0,
    scene_index: int = 0,
) -> str:
    """Full-screen reveal-pan scene chain (the default framing).

    The image is cover-scaled (lanczos) so it fills the whole 9:16 frame with
    no bars, sharpened to counter the upscale, looped into a frame stream, then
    a full-frame crop window is panned across it so nothing is permanently
    cropped away. Scaling before the loop keeps the expensive resize a one-shot.
    """
    crop_x_expr, crop_y_expr = build_reveal_pan_exprs(duration_frames, scene_index)
    base_filter = (
        # Trim any baked-in edge border off the source first (see EDGE_TRIM_PX).
        f"crop=iw-{2 * EDGE_TRIM_PX}:ih-{2 * EDGE_TRIM_PX},"
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
        "force_original_aspect_ratio=increase:flags=lanczos,"
        f"setsar=1,{UNSHARP_FILTER},format=yuv420p,"
        f"loop={duration_frames}:1:0,setpts=N/({FRAMERATE}*TB),"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:x='{crop_x_expr}':y='{crop_y_expr}'"
    )

    if not text_lines:
        return f"{base_filter},fps={FRAMERATE}"

    text_preset = preset.get("text", {})
    scene_duration = duration_frames / FRAMERATE
    text_filters = build_drawtext_filters(
        font_path, text_lines, text_preset, animation, scene_duration, xfade_duration
    )
    return base_filter + "," + ",".join(text_filters) + f",fps={FRAMERATE}"


def build_scene_vf_filter(
    font_path: str,
    text_lines: list[str],
    preset: dict[str, Any],
    duration_frames: int,
    animation: str = "fade",
    xfade_duration: float = 0.0,
    focus_point: tuple[float, float] = (0.5, 0.5),
) -> str:
    """Build per-scene FFmpeg -vf chain from preset filter + movement."""
    zoom = preset.get("zoom", {})
    z_expr = build_eased_zoom_expr(zoom, duration_frames)
    x_expr = build_eased_pan_expr(zoom.get("x", "iw/2-(iw/zoom/2)"), duration_frames)
    y_expr = build_eased_pan_expr(zoom.get("y", "ih/2-(ih/zoom/2)"), duration_frames)
    crop_x_expr, crop_y_expr = build_focus_crop_expr(focus_point)

    base_filter = (
        f"loop={duration_frames}:1:0,"
        "format=yuv420p,"
        "scale=w=1620:h=2880:force_original_aspect_ratio=increase,"
        f"crop=1620:2880:x='{crop_x_expr}':y='{crop_y_expr}',"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={duration_frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={FRAMERATE}"
    )

    if not text_lines:
        return f"{base_filter},fps={FRAMERATE}"

    text_preset = preset.get("text", {})
    scene_duration = duration_frames / FRAMERATE
    text_filters = build_drawtext_filters(
        font_path, text_lines, text_preset, animation, scene_duration, xfade_duration
    )
    return base_filter + "," + ",".join(text_filters) + f",fps={FRAMERATE}"


def build_fit_scene_subgraph(
    idx: int,
    font_path: str,
    text_lines: list[str],
    preset: dict[str, Any],
    duration_frames: int,
    animation: str = "fade",
    xfade_duration: float = 0.0,
    fit_zoom: dict[str, Any] | None = None,
) -> str:
    """Build a full labeled subgraph (from `[{idx}:v]` to `[v_scene_{idx}]`) that
    shows the entire source image with a blurred-fill backdrop and a cinematic
    zoom-out.

    The image is split: one copy is scaled to *cover* the frame, heavily
    blurred and darkened to form a soft backdrop; the other is scaled to *fit*
    (so nothing is cropped) and overlaid centered on top. The composite is then
    pulled back with zoompan so the full frame is revealed by the scene's end.
    """
    zoom_cfg = fit_zoom or {"start": FIT_ZOOM_START, "end": FIT_ZOOM_END}
    z_expr = build_eased_zoom_expr(zoom_cfg, duration_frames)
    x_expr = "iw/2-(iw/zoom/2)"
    y_expr = "ih/2-(ih/zoom/2)"

    compose = (
        f"[{idx}:v]crop=iw-{2 * EDGE_TRIM_PX}:ih-{2 * EDGE_TRIM_PX},"
        f"split=2[fit_bg_{idx}][fit_fg_{idx}];"
        f"[fit_bg_{idx}]scale={FIT_BG_W}:{FIT_BG_H}:force_original_aspect_ratio=increase,"
        f"crop={FIT_BG_W}:{FIT_BG_H},gblur=sigma={FIT_BG_BLUR_SIGMA},"
        f"eq=brightness={FIT_BG_BRIGHTNESS}:saturation={FIT_BG_SATURATION},"
        f"scale={SUPERSAMPLE_W}:{SUPERSAMPLE_H},setsar=1[fit_bgs_{idx}];"
        f"[fit_fg_{idx}]scale={SUPERSAMPLE_W}:{SUPERSAMPLE_H}:"
        f"force_original_aspect_ratio=decrease,setsar=1[fit_fgs_{idx}];"
        f"[fit_bgs_{idx}][fit_fgs_{idx}]overlay=(W-w)/2:(H-h)/2,"
        f"format=yuv420p[fit_comp_{idx}];"
    )
    zoompan = (
        f"[fit_comp_{idx}]zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={duration_frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={FRAMERATE}"
    )

    if not text_lines:
        return f"{compose}{zoompan},fps={FRAMERATE}[v_scene_{idx}];"

    text_preset = preset.get("text", {})
    scene_duration = duration_frames / FRAMERATE
    text_filters = build_drawtext_filters(
        font_path, text_lines, text_preset, animation, scene_duration, xfade_duration
    )
    return (
        f"{compose}{zoompan},"
        + ",".join(text_filters)
        + f",fps={FRAMERATE}[v_scene_{idx}];"
    )


def build_per_scene_texts(scene_texts: list[str]) -> list[list[str]]:
    """Map each scene's text to drawtext lines."""
    result: list[list[str]] = []
    for index, text in enumerate(scene_texts):
        cleaned = require_english_text(text, f"scenes[{index}].text")
        lines = split_scene_lines(cleaned)
        if not lines:
            raise RenderError(f"scenes[{index}].text must contain valid plain text.")
        result.append(lines)
    return result


def build_watermark_overlay_filter(
    icon_input_idx: int,
    source_label: str,
    output_label: str,
    clip_duration: float,
    width: int = SUBSCRIBE_ICON_WIDTH,
    bottom_margin: int = SUBSCRIBE_ICON_BOTTOM_MARGIN,
    fade_duration: float = SUBSCRIBE_ICON_FADE_DURATION,
    hold_duration: float = SUBSCRIBE_ICON_HOLD_DURATION,
) -> str:
    """Fade a bottom-anchored watermark icon in, hold, then fade it out
    within `clip_duration` seconds of the target clip's own local timeline
    (each zoompan/color source's `t` starts at 0), and overlay it centered
    near the bottom edge. `source_label`/`output_label` are bracketed
    filtergraph labels, e.g. "[v_scene_0]" / "[v_scene_0_sub]".
    """
    fade_duration = min(fade_duration, max(clip_duration / 4, 0.05))
    fade_out_start = max(fade_duration, min(fade_duration + hold_duration, clip_duration - fade_duration))
    icon_label = f"icon_{output_label.strip('[]')}"
    return (
        f"[{icon_input_idx}:v]scale={width}:-1,format=rgba,"
        f"fade=t=in:st=0:d={fade_duration}:alpha=1,"
        f"fade=t=out:st={fade_out_start}:d={fade_duration}:alpha=1[{icon_label}];"
        f"{source_label}[{icon_label}]overlay=(W-w)/2:H-h-{bottom_margin}:format=auto{output_label};"
    )


def build_scene_pipeline(
    num_images: int,
    font_path: str,
    scene_texts: list[list[str]],
    preset: dict[str, Any],
    img_duration: float,
    xfade_duration: float,
    duration_frames: int,
    transition: str = "fade",
    animation: str = "fade",
    lut_enabled: bool = True,
    focus_points: list[tuple[float, float]] | None = None,
    frame_mode: str = DEFAULT_FRAME_MODE,
    fit_zoom: dict[str, Any] | None = None,
) -> tuple[str, float]:
    # Subscribe-icon watermark only shows over the outro now, not at the
    # start of the video - see build_filter_complex's outro_watermark.
    filter_parts: list[str] = []

    for i in range(num_images):
        if frame_mode == "reveal":
            # Full-screen cover + reveal pan (default): fills the frame with no
            # bars and pans across so the whole image is seen.
            scene_filter = build_reveal_vf_filter(
                font_path,
                scene_texts[i],
                preset,
                duration_frames,
                animation,
                xfade_duration,
                scene_index=i,
            )
            filter_parts.append(f"[{i}:v]{scene_filter}[v_scene_{i}];")
            continue
        if frame_mode == "fit":
            # Whole-image blurred-fill composition + zoom-out.
            filter_parts.append(
                build_fit_scene_subgraph(
                    i,
                    font_path,
                    scene_texts[i],
                    preset,
                    duration_frames,
                    animation,
                    xfade_duration,
                    fit_zoom,
                )
            )
            continue
        # Legacy fill: cover the frame and crop to the detected focus point.
        focus_point = focus_points[i] if focus_points else (0.5, 0.5)
        scene_filter = build_scene_vf_filter(
            font_path,
            scene_texts[i],
            preset,
            duration_frames,
            animation,
            xfade_duration,
            focus_point,
        )
        filter_parts.append(f"[{i}:v]{scene_filter}[v_scene_{i}];")

    last_output = "[v_scene_0]"
    current_offset = img_duration - xfade_duration
    for i in range(1, num_images):
        next_label = f"[v_mix_{i}]" if i < num_images - 1 else "[v_images_merged]"
        filter_parts.append(
            f"{last_output}[v_scene_{i}]xfade=transition={transition}:duration={xfade_duration}:"
            f"offset={current_offset}{next_label};"
        )
        last_output = next_label
        current_offset += img_duration - xfade_duration

    merge_filter = preset.get("filter", "").strip()
    grade_chain = f"{merge_filter}," if merge_filter else ""
    if lut_enabled and CINEMATIC_LUT_PATH.exists():
        grade_chain += f"lut3d=file={escape_lut_path(CINEMATIC_LUT_PATH)},format=yuv420p"
    else:
        grade_chain += "format=yuv420p"
    filter_parts.append(f"[v_images_merged]{grade_chain}[v_graded];")

    images_duration = (img_duration * num_images) - (xfade_duration * (num_images - 1))
    return "".join(filter_parts), images_duration


def build_outro_with_logo_filter(
    font_path: str,
    logo_input_idx: int,
    website_url: str = DEFAULT_WEBSITE_URL,
    duration_frames: int = OUTRO_FRAMES,
    cta_text: str = DEFAULT_OUTRO_CTA,
) -> str:
    escaped_font = font_path.replace(":", "\\:")
    url_drawtext = build_outro_url_drawtext(escaped_font, website_url)
    outro_duration = duration_frames / FRAMERATE
    # Optional call-to-action line; skipped entirely when blank so callers can
    # turn it off without changing anything else on the card.
    cta_chain = ""
    if cta_text and cta_text.strip():
        cta_chain = build_outro_cta_drawtext(escaped_font, cta_text) + ","

    # Fade + rise the logo in instead of having it pop in at full opacity on
    # the outro card's first frame; clamp the duration so a very short outro
    # (e.g. debug_mode) still leaves time for the rest of the card.
    logo_fade_duration = min(LOGO_FADE_DURATION, max(outro_duration / 3, 0.05))
    logo_progress = ease_in_out_ratio_expr(f"min(t/{logo_fade_duration}\\,1)")
    logo_rise = f"({LOGO_RISE_DISTANCE}*(1-{logo_progress}))"

    # Brand-orange divider drawn just beneath the centered logo, wiped open from
    # the middle in step with the logo fade so the card assembles itself instead
    # of appearing all at once. Its half-width grows 0 -> DIVIDER_HALF_WIDTH.
    # NB: inside drawbox's x/w expressions, `w`/`h` mean the BOX's own size, so
    # the frame width must be referenced as `iw` to center it correctly.
    divider_half = f"({OUTRO_DIVIDER_WIDTH // 2}*{logo_progress})"
    divider = (
        f"drawbox=x='(iw/2)-{divider_half}':y={OUTRO_DIVIDER_Y}:"
        f"w='2*{divider_half}':h={OUTRO_DIVIDER_THICKNESS}:"
        f"color={OUTRO_URL_COLOR}@0.95:t=fill"
    )

    return (
        # Warm near-black backdrop with a soft radial vignette so the eye is
        # pulled to the centered logo instead of a flat black rectangle.
        f"color=c=0x0B0B0D:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FRAMERATE}:d={outro_duration},"
        f"vignette=angle=PI/5[bg];"
        f"[{logo_input_idx}:v]scale={OUTRO_LOGO_WIDTH}:-1,format=rgba,"
        f"fade=t=in:st=0:d={logo_fade_duration}:alpha=1[logo_scaled];"
        f"[bg][logo_scaled]overlay=(W-w)/2:(H-h)/2-160+{logo_rise}[with_logo];"
        f"[with_logo]{divider},{cta_chain}{url_drawtext},"
        f"fps={FRAMERATE}[v_outro]"
    )


def build_outro_filter(
    font_path: str,
    website_url: str = DEFAULT_WEBSITE_URL,
    duration_frames: int = OUTRO_FRAMES,
) -> str:
    escaped_font = font_path.replace(":", "\\:")
    company_name = escape_drawtext("GOLDMOON")
    url_drawtext = build_outro_url_drawtext(escaped_font, website_url, fade=False)

    return (
        f"drawtext=fontfile={escaped_font}:text='{company_name}':"
        f"fontcolor=gold:fontsize=72:box=0:"
        f"x=(w-text_w)/2:y=(h-text_h)/2-60:"
        f"borderw=2:bordercolor=black,"
        f"{url_drawtext},"
        f"setsar=1,fps={FRAMERATE}"
    )


def build_filter_complex(
    num_images: int,
    font_path: str,
    scene_texts: list[list[str]],
    music_path: Path | None,
    logo_path: Path | None,
    preset: dict[str, Any],
    website_url: str = DEFAULT_WEBSITE_URL,
    debug_mode: bool = False,
    transition: str = "fade",
    animation: str = "fade",
    subscribe_icon_path: Path | None = None,
    lut_enabled: bool = True,
    voiceover_path: Path | None = None,
    voiceover_duration: float | None = None,
    focus_points: list[tuple[float, float]] | None = None,
    frame_mode: str = DEFAULT_FRAME_MODE,
    fit_zoom: dict[str, Any] | None = None,
    outro_cta: str = DEFAULT_OUTRO_CTA,
) -> tuple[str, list[str], list[str], list[str], float]:
    if len(scene_texts) != num_images:
        raise ValueError(
            f"scene_texts must have exactly {num_images} entries (one per image, "
            "empty list allowed for no caption on that scene)."
        )

    img_duration, xfade_duration, outro_duration, duration_frames = resolve_render_timing(
        debug_mode, num_images, voiceover_duration
    )
    outro_frames = int(outro_duration * FRAMERATE)

    outro_bg_idx = num_images
    subscribe_icon_idx = num_images + 1 if subscribe_icon_path else None
    music_idx = num_images + (2 if subscribe_icon_path else 1)

    image_filters, images_duration = build_scene_pipeline(
        num_images,
        font_path,
        scene_texts,
        preset,
        img_duration,
        xfade_duration,
        duration_frames,
        transition,
        animation,
        lut_enabled,
        focus_points,
        frame_mode,
        fit_zoom,
    )

    outro_offset = images_duration - xfade_duration
    total_duration = images_duration + outro_duration - xfade_duration

    if logo_path:
        outro_build_filters = build_outro_with_logo_filter(
            font_path, num_images, website_url, outro_frames, cta_text=outro_cta
        )
        # -loop 1: see the subscribe-icon comment below - the logo now
        # animates (fade-in + rise), which needs an advancing timestamp;
        # without this it's a single frame at pts=0 and the fade filter
        # evaluates it as permanently transparent.
        outro_input = ["-loop", "1", "-i", str(logo_path)]
    else:
        outro_build_filters = (
            f"[{outro_bg_idx}:v]{build_outro_filter(font_path, website_url, outro_frames)}[v_outro]"
        )
        outro_input = [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:d={outro_duration}:r={FRAMERATE}",
        ]

    if subscribe_icon_idx is not None:
        outro_watermark = build_watermark_overlay_filter(
            subscribe_icon_idx, "[v_outro]", "[v_outro_sub]", outro_duration
        )
        outro_final_label = "[v_outro_sub]"
    else:
        outro_watermark = ""
        outro_final_label = "[v_outro]"

    outro_filters = (
        f"{outro_build_filters};{outro_watermark}"
        f"[v_graded]{outro_final_label}xfade=transition=fade:duration={xfade_duration}:"
        f"offset={outro_offset}[v_final];"
    )

    # -loop 1 makes ffmpeg emit the still image as a continuous stream of
    # frames with advancing timestamps, which the fade-in/out filters below
    # need to animate; without it the icon is a single frame at pts=0 and
    # the fade filter evaluates it as permanently transparent.
    subscribe_input = ["-loop", "1", "-i", str(subscribe_icon_path)] if subscribe_icon_path else []

    has_voiceover = bool(voiceover_path and voiceover_path.exists())
    # Master the final mix to a consistent broadcast loudness, but only when
    # there's actually audio - running loudnorm on a pure-silence source (no
    # music and no voiceover) would just amplify nothing / risk artifacts.
    has_real_audio = (music_path and music_path.exists()) or has_voiceover
    master_suffix = f",{LOUDNORM_FILTER}" if has_real_audio else ""
    if music_path and music_path.exists():
        audio_input = ["-i", str(music_path)]
        # With a voiceover, ducking pulls the bed down during speech, so it
        # can run louder at rest (in the gaps) without ever competing with
        # the narration; music-only renders keep the lower fixed level.
        music_volume = 0.42 if has_voiceover else 0.25
        music_filters = (
            f"[{music_idx}:a]aloop=loop=-1:size=2e+09,atrim=0:{total_duration},"
            f"volume={music_volume}[a_music]"
        )
    else:
        audio_input = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        music_filters = f"[{music_idx}:a]atrim=0:{total_duration}[a_music]"

    if has_voiceover:
        voice_idx = music_idx + 1
        audio_input = audio_input + ["-i", str(voiceover_path)]
        # Duck the music bed under the voiceover (sidechaincompress uses the
        # voice track as the control signal), then mix the ducked music
        # back in with the dry voice so speech stays intelligible. The voice
        # track feeds two filters (sidechaincompress + amix), so it must be
        # split first - ffmpeg rejects an output pad consumed twice.
        audio_filters = (
            f"{music_filters};"
            f"[{voice_idx}:a]apad,atrim=0:{total_duration},"
            f"asplit=2[a_voice_side][a_voice_mix];"
            f"[a_music][a_voice_side]sidechaincompress=threshold=0.025:ratio=10:"
            f"attack=10:release=110[a_music_duck];"
            f"[a_music_duck][a_voice_mix]amix=inputs=2:duration=first:"
            f"dropout_transition=0,afade=t=out:st={total_duration - 0.5}:"
            f"d=0.5{master_suffix}[a_final]"
        )
    else:
        audio_filters = (
            f"{music_filters};"
            f"[a_music]afade=t=out:st={total_duration - 0.5}:d=0.5{master_suffix}[a_final]"
        )

    return (
        image_filters + outro_filters + audio_filters,
        outro_input,
        subscribe_input,
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


_FACE_CASCADE: cv2.CascadeClassifier | None = None


def _get_face_cascade() -> cv2.CascadeClassifier:
    global _FACE_CASCADE
    if _FACE_CASCADE is None:
        _FACE_CASCADE = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
    return _FACE_CASCADE


def detect_focus_point(image_path: Path) -> tuple[float, float]:
    """Return the normalized (fx, fy) coordinate - each in [0, 1] - of the
    most visually important region of the image, so scene cropping can keep
    it in frame instead of always taking a blind center crop.

    Tries face detection first (best for photos with people), then falls
    back to an edge-density centroid (Canny edges) as a generic saliency
    proxy - detailed regions like a temple facade or a boat's silhouette
    stand out from flat sky/sand/water, so most landmark and scenery shots
    still get a sensible subject-following crop. Defaults to dead-center
    (0.5, 0.5) - the previous fixed behavior - if the image can't be read
    or has no discernible edges.
    """
    img = cv2.imread(str(image_path))
    if img is None:
        return 0.5, 0.5

    height, width = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    faces = _get_face_cascade().detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(int(width * 0.05), int(height * 0.05)),
    )
    if len(faces) > 0:
        largest = max(faces, key=lambda f: f[2] * f[3])
        fx, fy, fw, fh = largest
        return float(fx + fw / 2) / width, float(fy + fh / 2) / height

    edges = cv2.Canny(gray, 50, 150)
    ys, xs = np.nonzero(edges)
    if len(xs) == 0:
        return 0.5, 0.5
    return float(np.mean(xs)) / width, float(np.mean(ys)) / height


def run_ffmpeg(command: list[str]) -> None:
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


def render_video(data: dict[str, Any]) -> Path:
    """
    Modular render entry point.

    Expected keys:
      image_paths: list[Path|str]  (2-4 items)
      scene_texts: list[str]       (2-4 items; if fewer than images, last text repeats)
    Optional:
      bg_music, style, debug_mode, logo_path, website_url, output_path,
      frame_mode ("reveal" default | "fit" | "fill"): "reveal" fills the whole
      frame (no bars) with the source scaled to cover, then pans across it so
      the entire wide photo is seen over the scene; "fit" shows the whole image
      at once with a blurred backdrop and zoom-out (letterbox bars); "fill"
      covers and crops to a detected focus point (legacy behavior).
      outro_cta (default "Book Your Journey"): call-to-action line shown on the
      outro card; pass "" to hide it. Visual only - never affects the voiceover.
      transition, text_animation, text_style, lut_enabled (default True),
      subscribe_icon_enabled (default True), zoom_override ({start, end}),
      voiceover_text (synthesized via Gemini TTS and ducked under bg_music),
      voiceover_voice (Gemini voice name, default "Kore"; see tts.AVAILABLE_VOICES).
      enable_text_overlay (default True): set False to hide the on-screen
      scene captions entirely and render on images + music + voiceover only.
      When voiceover_text synthesizes successfully, scene durations are
      derived from the narration's actual length (clamped to
      MIN/MAX_VOICEOVER_IMG_DURATION per scene) so on-screen captions stay
      in sync with what's being spoken, instead of the fixed IMG_DURATION.
      If voiceover synthesis fails, the render still succeeds without narration
      (music/captions only, fixed IMG_DURATION) rather than failing the whole request.
    """
    raw_image_paths = data["image_paths"]
    raw_scene_texts = data.get("scene_texts") or []
    enable_text_overlay = bool(data.get("enable_text_overlay", True))

    if len(raw_image_paths) < 2 or len(raw_image_paths) > 4:
        raise RenderError("Please provide 2 to 4 image paths.")

    image_paths = [Path(p) for p in raw_image_paths]

    if enable_text_overlay:
        if len(raw_scene_texts) < 2 or len(raw_scene_texts) > 4:
            raise RenderError("Please provide 2 to 4 scene texts.")
        validated_texts = [
            require_english_text(text, f"scene_texts[{idx}]")
            for idx, text in enumerate(raw_scene_texts)
        ]
        scene_text_lines = assign_scene_texts(len(image_paths), validated_texts)
    else:
        scene_text_lines = [[] for _ in image_paths]

    bg_music = data.get("bg_music", "luxury_chill")
    debug_mode = bool(data.get("debug_mode", False))
    logo_path = Path(data["logo_path"]) if data.get("logo_path") else None
    website_url = data.get("website_url", DEFAULT_WEBSITE_URL)
    output_path = Path(data["output_path"]) if data.get("output_path") else None
    style_name = data.get("style", "")
    text_animation = pick_text_animation(data.get("text_animation"))

    for image_path in image_paths:
        validate_local_image(image_path)

    # Outro call-to-action text; "" (explicitly) hides it. Missing key = default.
    outro_cta = data.get("outro_cta")
    if outro_cta is None:
        outro_cta = DEFAULT_OUTRO_CTA

    frame_mode = normalize_style_name(data.get("frame_mode") or DEFAULT_FRAME_MODE)
    if frame_mode not in FRAME_MODES:
        raise RenderError(
            f"Unknown frame_mode '{data.get('frame_mode')}'. Choose one of: "
            f"{', '.join(FRAME_MODES)}"
        )

    zoom_override = data.get("zoom_override")
    # In fit mode the source is never cropped, so the preset's own (zoom-in)
    # move doesn't apply; use the gentle pull-back defaults, letting a
    # zoom_override tweak the start/end if the caller wants.
    fit_zoom = {"start": FIT_ZOOM_START, "end": FIT_ZOOM_END}
    if zoom_override:
        if zoom_override.get("start") is not None:
            fit_zoom["start"] = float(zoom_override["start"])
        if zoom_override.get("end") is not None:
            fit_zoom["end"] = float(zoom_override["end"])

    # Focus-point detection only matters for the "fill" crop; "fit" shows the
    # whole image, so skip the OpenCV pass entirely in that (default) case.
    if frame_mode == "fill":
        focus_points = [detect_focus_point(image_path) for image_path in image_paths]
    else:
        focus_points = None

    resolved_style, preset = resolve_preset(style_name)
    text_style = resolve_text_style(preset.get("text", {}), data.get("text_style"))
    preset = {**preset, "text": text_style}
    preset = apply_zoom_override(preset, zoom_override)
    transition = pick_transition(data.get("transition"), preset.get("transition"))
    font_path = resolve_font_for_preset(preset)
    music_path = resolve_bg_music(bg_music)
    effective_logo = logo_path if logo_path and logo_path.exists() else resolve_logo_path()
    subscribe_icon_enabled = bool(data.get("subscribe_icon_enabled", True))
    subscribe_icon_path = resolve_subscribe_icon_path() if subscribe_icon_enabled else None
    lut_enabled = bool(data.get("lut_enabled", True))
    num_images = len(image_paths)

    voiceover_text = (data.get("voiceover_text") or "").strip()
    voiceover_voice = (data.get("voiceover_voice") or "").strip() or None
    voiceover_path: Path | None = None
    voiceover_duration: float | None = None
    voiceover_failed = False
    if voiceover_text:
        candidate_path = APP_DIR / f"voiceover_{uuid.uuid4().hex}.wav"
        try:
            synthesize_voiceover(voiceover_text, candidate_path, voice_name=voiceover_voice)
            voiceover_path = candidate_path
            voiceover_duration = get_audio_duration(candidate_path)
        except TTSError as exc:
            # Soft-fail: narration is an enhancement, not a hard requirement.
            # An unattended n8n cron run shouldn't lose the whole video (and
            # the music/captions that already work) over a TTS hiccup.
            voiceover_failed = True
            candidate_path.unlink(missing_ok=True)
            print(f"Warning: voiceover synthesis failed, rendering without it: {exc}")

    try:
        filter_complex, outro_input, subscribe_input, audio_input, total_duration = build_filter_complex(
            num_images,
            font_path,
            scene_text_lines,
            music_path,
            effective_logo,
            preset,
            website_url=website_url,
            debug_mode=debug_mode,
            transition=transition,
            animation=text_animation,
            subscribe_icon_path=subscribe_icon_path,
            lut_enabled=lut_enabled,
            voiceover_path=voiceover_path,
            voiceover_duration=voiceover_duration,
            focus_points=focus_points,
            frame_mode=frame_mode,
            fit_zoom=fit_zoom,
            outro_cta=outro_cta,
        )

        if output_path is None:
            output_path = APP_DIR / f"output_goldmoon_{resolved_style}.mp4"

        command = ["ffmpeg", "-y"]
        for img in image_paths:
            command.extend(["-i", str(img)])
        command.extend(outro_input)
        command.extend(subscribe_input)
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
            "medium",
            "-crf",
            "19",
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
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

        run_ffmpeg(command)

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RenderError("Video rendering failed.")

        print(
            f"Render complete with style={resolved_style}, "
            f"transition={transition}, text_animation={text_animation}, "
            f"voiceover={'failed' if voiceover_failed else ('on' if voiceover_path else 'off')}"
        )
        return output_path
    finally:
        if voiceover_path:
            voiceover_path.unlink(missing_ok=True)
