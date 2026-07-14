"""Modular FFmpeg rendering pipeline for Goldmoon Video API."""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image

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
IMG_DURATION = 4.0
XFADE_DURATION = 0.5
FRAMERATE = 30
DURATION_FRAMES = int(IMG_DURATION * FRAMERATE)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
FFMPEG_TIMEOUT = 600
WRAP_CHARS = 28
SCENE_FONT_SIZE = 46
SCENE_TEXT_START_Y = 1100
SCENE_LINE_SPACING = 85
TEXT_FADE_DELAY = 0.3
TEXT_FADE_DURATION = 0.5

OUTRO_DURATION = 2.0
OUTRO_FRAMES = int(OUTRO_DURATION * FRAMERATE)
DEFAULT_WEBSITE_URL = "https://www.goldmoontours.com/en"
OUTRO_URL_FADE_DELAY = 0.4
OUTRO_URL_FADE_DURATION = 0.5
OUTRO_URL_FONT_SIZE = 26
OUTRO_URL_Y = 1180

# Subscribe-button watermark: bottom-anchored, shown briefly at the very
# start of the video and again over the outro. Only applied when
# SUBSCRIBE_ICON_PATH exists (same "static asset present = always used"
# convention as the logo).
SUBSCRIBE_ICON_WIDTH = 480
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
) -> tuple[float, float, float, int]:
    if debug_mode:
        xfade_duration = 0.25
        outro_duration = 0.75
        img_duration = (
            DEBUG_TOTAL_DURATION - outro_duration + xfade_duration + (num_images - 1) * xfade_duration
        ) / num_images
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


def ease_in_out_ratio_expr(progress_expr: str) -> str:
    """Cosine ease-in-out over an already-computed 0..1 progress expr."""
    return f"(0.5-0.5*cos(PI*{progress_expr}))"


def build_eased_zoom_expr(zoom_cfg: dict[str, Any], duration_frames: int) -> str:
    """Build an absolute (not incremental) zoompan `z` expression that eases
    from `start` to `end` across the clip using a cosine in/out curve, based
    on the output frame number `on` and the known clip length in frames.

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
    eased = ease_in_out_ratio_expr(progress)
    return f"{start}+({end}-{start})*{eased}"


def build_eased_pan_expr(expr: str, duration_frames: int) -> str:
    """Replace linear `on`-based pan drift (e.g. `on*0.7`) in a zoompan x/y
    expression with an eased frame count, so panning accelerates/decelerates
    instead of moving at a constant speed. No-op if `on` isn't referenced.
    """
    if not expr or not re.search(r"\bon\b", expr):
        return expr
    if duration_frames <= 1:
        eased_on = "0"
    else:
        progress = f"min(on/{duration_frames - 1}\\,1)"
        eased_on = f"({duration_frames - 1}*{ease_in_out_ratio_expr(progress)})"
    return re.sub(r"\bon\b", eased_on, expr)


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
        "fontcolor=white",
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


def build_drawtext_filters(
    font_path: str,
    text_lines: list[str],
    text_preset: dict[str, Any],
    animation: str = "fade",
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
            f"alpha='{eased_progress_expr(fade_delay, fade_duration)}'",
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


def build_scene_vf_filter(
    font_path: str,
    text_lines: list[str],
    preset: dict[str, Any],
    duration_frames: int,
    animation: str = "fade",
) -> str:
    """Build per-scene FFmpeg -vf chain from preset filter + movement."""
    zoom = preset.get("zoom", {})
    z_expr = build_eased_zoom_expr(zoom, duration_frames)
    x_expr = build_eased_pan_expr(zoom.get("x", "iw/2-(iw/zoom/2)"), duration_frames)
    y_expr = build_eased_pan_expr(zoom.get("y", "ih/2-(ih/zoom/2)"), duration_frames)

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
    text_filters = build_drawtext_filters(font_path, text_lines, text_preset, animation)
    return base_filter + "," + ",".join(text_filters) + f",fps={FRAMERATE}"


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
    subscribe_icon_idx: int | None = None,
) -> tuple[str, float]:
    filter_parts: list[str] = []

    for i in range(num_images):
        scene_filter = build_scene_vf_filter(
            font_path, scene_texts[i], preset, duration_frames, animation
        )
        filter_parts.append(f"[{i}:v]{scene_filter}[v_scene_{i}];")

    if subscribe_icon_idx is not None:
        filter_parts.append(
            build_watermark_overlay_filter(
                subscribe_icon_idx, "[v_scene_0]", "[v_scene_0_sub]", img_duration
            )
        )
        last_output = "[v_scene_0_sub]"
    else:
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
    if CINEMATIC_LUT_PATH.exists():
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
) -> str:
    escaped_font = font_path.replace(":", "\\:")
    url_drawtext = build_outro_url_drawtext(escaped_font, website_url)
    outro_duration = duration_frames / FRAMERATE

    return (
        f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FRAMERATE}:d={outro_duration}[bg];"
        f"[{logo_input_idx}:v]scale=850:-1[logo_scaled];"
        f"[bg][logo_scaled]overlay=(W-w)/2:(H-h)/2-120[with_logo];"
        f"[with_logo]{url_drawtext},"
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
) -> tuple[str, list[str], list[str], list[str], float]:
    if not scene_texts or not any(scene_texts):
        raise ValueError("Scene text is empty after sanitization")

    img_duration, xfade_duration, outro_duration, duration_frames = resolve_render_timing(
        debug_mode, num_images
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
        subscribe_icon_idx,
    )

    outro_offset = images_duration - xfade_duration
    total_duration = images_duration + outro_duration - xfade_duration

    if logo_path:
        outro_build_filters = build_outro_with_logo_filter(
            font_path, num_images, website_url, outro_frames
        )
        outro_input = ["-i", str(logo_path)]
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
      bg_music, style, debug_mode, logo_path, website_url, output_path
    """
    raw_image_paths = data["image_paths"]
    raw_scene_texts = data["scene_texts"]

    if len(raw_image_paths) < 2 or len(raw_image_paths) > 4:
        raise RenderError("Please provide 2 to 4 image paths.")
    if len(raw_scene_texts) < 2 or len(raw_scene_texts) > 4:
        raise RenderError("Please provide 2 to 4 scene texts.")

    image_paths = [Path(p) for p in raw_image_paths]

    validated_texts = [
        require_english_text(text, f"scene_texts[{idx}]")
        for idx, text in enumerate(raw_scene_texts)
    ]
    scene_text_lines = assign_scene_texts(len(image_paths), validated_texts)

    bg_music = data.get("bg_music", "luxury_chill")
    debug_mode = bool(data.get("debug_mode", False))
    logo_path = Path(data["logo_path"]) if data.get("logo_path") else None
    website_url = data.get("website_url", DEFAULT_WEBSITE_URL)
    output_path = Path(data["output_path"]) if data.get("output_path") else None
    style_name = data.get("style", "")
    text_animation = pick_text_animation(data.get("text_animation"))

    for image_path in image_paths:
        validate_local_image(image_path)

    resolved_style, preset = resolve_preset(style_name)
    text_style = resolve_text_style(preset.get("text", {}), data.get("text_style"))
    preset = {**preset, "text": text_style}
    transition = pick_transition(data.get("transition"), preset.get("transition"))
    font_path = resolve_font_for_preset(preset)
    music_path = resolve_bg_music(bg_music)
    effective_logo = logo_path if logo_path and logo_path.exists() else resolve_logo_path()
    subscribe_icon_path = resolve_subscribe_icon_path()
    num_images = len(image_paths)

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

    run_ffmpeg(command)

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RenderError("Video rendering failed.")

    print(
        f"Render complete with style={resolved_style}, "
        f"transition={transition}, text_animation={text_animation}"
    )
    return output_path
