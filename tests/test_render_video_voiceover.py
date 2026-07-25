"""Unit tests for render_video's voiceover soft-fail and voice-selection
behavior. FFmpeg itself and real images are stubbed out here - the rest of
the pipeline (filtergraph construction, style resolution, etc.) is already
covered directly in test_render_pipeline.py.
"""

import render_pipeline as rp
from tts import TTSError


def _fake_render_env(monkeypatch, output_path, images):
    """Stub the parts of render_video that need real ffmpeg/image files."""
    monkeypatch.setattr(rp, "validate_local_image", lambda path: None)

    def fake_run_ffmpeg(command):
        output_path.write_bytes(b"fake-mp4-bytes")

    monkeypatch.setattr(rp, "run_ffmpeg", fake_run_ffmpeg)
    return images


def test_render_video_succeeds_when_voiceover_synthesis_fails(monkeypatch, tmp_path, capsys):
    output_path = tmp_path / "out.mp4"
    images = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    _fake_render_env(monkeypatch, output_path, images)

    def failing_synthesize(text, path, voice_name=None):
        raise TTSError("quota exceeded")

    monkeypatch.setattr(rp, "synthesize_voiceover", failing_synthesize)

    result = rp.render_video(
        {
            "image_paths": images,
            "scene_texts": ["Discover Egypt", "Book Now"],
            "output_path": output_path,
            "voiceover_text": "Come explore the wonders of the Nile.",
        }
    )

    assert result == output_path
    assert output_path.exists()
    # No leftover temp voiceover file after a failed synthesis.
    assert list(tmp_path.glob("voiceover_*.wav")) == []
    assert "voiceover=failed" in capsys.readouterr().out


def test_render_video_skips_voiceover_when_text_absent(monkeypatch, tmp_path, capsys):
    output_path = tmp_path / "out.mp4"
    images = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    _fake_render_env(monkeypatch, output_path, images)

    called = False

    def unexpected_synthesize(text, path, voice_name=None):
        nonlocal called
        called = True

    monkeypatch.setattr(rp, "synthesize_voiceover", unexpected_synthesize)

    rp.render_video(
        {
            "image_paths": images,
            "scene_texts": ["Discover Egypt", "Book Now"],
            "output_path": output_path,
        }
    )

    assert called is False
    assert "voiceover=off" in capsys.readouterr().out


def test_render_video_passes_requested_voice_through(monkeypatch, tmp_path):
    output_path = tmp_path / "out.mp4"
    images = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    _fake_render_env(monkeypatch, output_path, images)

    captured = {}

    def recording_synthesize(text, path, voice_name=None):
        captured["voice_name"] = voice_name
        path.write_bytes(b"fake-wav")

    monkeypatch.setattr(rp, "synthesize_voiceover", recording_synthesize)

    rp.render_video(
        {
            "image_paths": images,
            "scene_texts": ["Discover Egypt", "Book Now"],
            "output_path": output_path,
            "voiceover_text": "Come explore the wonders of the Nile.",
            "voiceover_voice": "Puck",
        }
    )

    assert captured["voice_name"] == "Puck"


def test_render_video_defaults_voice_to_none_when_unset(monkeypatch, tmp_path):
    output_path = tmp_path / "out.mp4"
    images = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    _fake_render_env(monkeypatch, output_path, images)

    captured = {}

    def recording_synthesize(text, path, voice_name=None):
        captured["voice_name"] = voice_name
        path.write_bytes(b"fake-wav")

    monkeypatch.setattr(rp, "synthesize_voiceover", recording_synthesize)

    rp.render_video(
        {
            "image_paths": images,
            "scene_texts": ["Discover Egypt", "Book Now"],
            "output_path": output_path,
            "voiceover_text": "Come explore the wonders of the Nile.",
        }
    )

    assert captured["voice_name"] is None


def test_render_video_hides_captions_when_overlay_disabled(monkeypatch, tmp_path):
    output_path = tmp_path / "out.mp4"
    images = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    _fake_render_env(monkeypatch, output_path, images)

    captured = {}
    real_build_filter_complex = rp.build_filter_complex

    def recording_build_filter_complex(num_images, font_path, scene_texts, *args, **kwargs):
        captured["scene_texts"] = scene_texts
        return real_build_filter_complex(num_images, font_path, scene_texts, *args, **kwargs)

    monkeypatch.setattr(rp, "build_filter_complex", recording_build_filter_complex)

    result = rp.render_video(
        {
            "image_paths": images,
            "scene_texts": [],
            "enable_text_overlay": False,
            "output_path": output_path,
        }
    )

    assert result == output_path
    assert captured["scene_texts"] == [[], []]


def test_render_video_requires_scene_texts_when_overlay_enabled(tmp_path):
    images = [tmp_path / "a.jpg", tmp_path / "b.jpg"]

    try:
        rp.render_video(
            {
                "image_paths": images,
                "scene_texts": [],
                "output_path": tmp_path / "out.mp4",
            }
        )
        assert False, "expected RenderError"
    except rp.RenderError as exc:
        assert "scene texts" in str(exc).lower()
