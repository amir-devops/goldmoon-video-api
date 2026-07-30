import pytest

import render_pipeline as rp


# ---------------------------------------------------------------------------
# Text sanitization / validation
# ---------------------------------------------------------------------------

class TestSanitizePlainText:
    def test_collapses_whitespace(self):
        assert rp.sanitize_plain_text("hello    world\n\tfoo") == "hello world foo"

    def test_strips_quotes_and_backslashes(self):
        assert rp.sanitize_plain_text('say "hi" \\now') == "say hi now"

    def test_strips_disallowed_punctuation(self):
        assert rp.sanitize_plain_text("50% off!! @tour #egypt") == "50 off!! tour egypt"

    def test_truncates_to_max_chars(self):
        assert rp.sanitize_plain_text("abcdefghij", max_chars=5) == "abcde"

    def test_empty_input(self):
        assert rp.sanitize_plain_text("") == ""
        assert rp.sanitize_plain_text(None) == ""


class TestRequireEnglishText:
    def test_valid_text_passes_through_cleaned(self):
        assert rp.require_english_text("  Discover Egypt!  ", "field") == "Discover Egypt!"

    def test_rejects_non_english_script(self):
        with pytest.raises(rp.RenderError):
            rp.require_english_text("اكتشف مصر", "field")

    def test_strips_emoji_but_keeps_valid_text(self):
        # sanitize_plain_text strips non-ASCII characters like emoji rather
        # than rejecting the whole string, as long as English text remains.
        assert rp.require_english_text("Book now! 🐫", "field") == "Book now!"

    def test_rejects_emoji_only_text(self):
        with pytest.raises(rp.RenderError):
            rp.require_english_text("🐫🐫🐫", "field")

    def test_rejects_empty(self):
        with pytest.raises(rp.RenderError):
            rp.require_english_text("   ", "field")

    def test_error_message_includes_field_name(self):
        with pytest.raises(rp.RenderError, match="scene_texts.0."):
            rp.require_english_text("مرحبا", "scene_texts[0]")


class TestSafeOutputFilename:
    def test_slugifies_title(self):
        assert rp.safe_output_filename("Nile Cruise: 5 Days!") == "Nile_Cruise_5_Days.mp4"

    def test_empty_title_falls_back(self):
        assert rp.safe_output_filename("") == "goldmoon_promo.mp4"

    def test_non_english_falls_back(self):
        # sanitize_plain_text strips non-ASCII, so an all-Arabic title
        # collapses to nothing and the fallback name kicks in.
        assert rp.safe_output_filename("رحلة النيل") == "goldmoon_promo.mp4"


class TestSplitSceneLines:
    def test_wraps_long_text_into_at_most_two_lines(self):
        lines = rp.split_scene_lines("Discover the wonders of ancient Egypt today")
        assert 1 <= len(lines) <= 2
        assert all(len(line) <= rp.WRAP_CHARS for line in lines)

    def test_empty_text_returns_no_lines(self):
        assert rp.split_scene_lines("") == []

    def test_short_text_single_line(self):
        assert rp.split_scene_lines("Book Now") == ["Book Now"]


class TestAssignSceneTexts:
    def test_maps_one_text_per_image(self):
        result = rp.assign_scene_texts(2, ["Hook line", "Call to action"])
        assert len(result) == 2
        assert result[0] == ["Hook line"]
        assert result[1] == ["Call to action"]

    def test_reuses_last_text_when_fewer_than_images(self):
        result = rp.assign_scene_texts(4, ["Only text"])
        assert len(result) == 4
        assert all(lines == ["Only text"] for lines in result)


# ---------------------------------------------------------------------------
# Style / preset resolution
# ---------------------------------------------------------------------------

class TestNormalizeStyleName:
    def test_lowercases_and_replaces_separators(self):
        assert rp.normalize_style_name("Luxury Gold") == "luxury_gold"
        assert rp.normalize_style_name("cinematic-dark") == "cinematic_dark"


class TestResolvePreset:
    def test_unknown_style_raises(self):
        with pytest.raises(rp.RenderError):
            rp.resolve_preset("not_a_real_style")

    def test_known_style_returned_by_key(self):
        key, preset = rp.resolve_preset("luxury_gold")
        assert key == "luxury_gold"
        assert "zoom" in preset and "text" in preset

    def test_style_name_is_normalized(self):
        key, _ = rp.resolve_preset("Luxury Gold")
        assert key == "luxury_gold"

    def test_empty_style_picks_a_valid_random_preset(self):
        presets = rp.load_presets()
        key, preset = rp.resolve_preset("")
        assert key in presets
        assert preset is presets[key]


class TestPickTransition:
    def test_explicit_valid_transition_is_normalized_and_returned(self):
        assert rp.pick_transition("Fade") == "fade"

    def test_unknown_transition_raises(self):
        with pytest.raises(rp.RenderError):
            rp.pick_transition("not_a_transition")

    def test_style_default_used_when_no_explicit_request(self):
        assert rp.pick_transition(None, style_default="wipeleft") == "wipeleft"

    def test_random_fallback_is_from_pool(self):
        assert rp.pick_transition(None, None) in rp.TRANSITION_POOL


class TestPickTextAnimation:
    def test_explicit_valid_animation(self):
        assert rp.pick_text_animation("slide_up") == "slide_up"

    def test_unknown_animation_raises(self):
        with pytest.raises(rp.RenderError):
            rp.pick_text_animation("not_an_animation")

    def test_random_fallback_is_from_pool(self):
        assert rp.pick_text_animation(None) in rp.TEXT_ANIMATIONS


class TestResolveTextStyle:
    def test_no_override_returns_preset_unchanged(self):
        preset = {"fontcolor": "white", "box": 0}
        assert rp.resolve_text_style(preset, None) is preset

    def test_named_mode_applies_overrides(self):
        preset = {"fontcolor": "white", "box": 0}
        merged = rp.resolve_text_style(preset, {"mode": "bold"})
        assert merged["box"] == 0
        assert merged["shadowcolor"] == "black@0.9"
        assert merged["uppercase"] is True

    def test_unknown_mode_raises(self):
        with pytest.raises(rp.RenderError):
            rp.resolve_text_style({}, {"mode": "not_a_mode"})

    def test_explicit_fields_override_mode(self):
        merged = rp.resolve_text_style({}, {"color": "#FF0000", "box_opacity": 0.5})
        assert merged["fontcolor"] == "#FF0000"
        assert merged["box"] == 1
        assert merged["boxcolor"] == "black@0.5"

    def test_shadow_false_removes_shadow(self):
        preset = {"shadowcolor": "black@0.9"}
        merged = rp.resolve_text_style(preset, {"shadow": False})
        assert "shadowcolor" not in merged


class TestApplyZoomOverride:
    def test_no_override_returns_preset_unchanged(self):
        preset = {"zoom": {"start": 1.0, "end": 1.5}}
        assert rp.apply_zoom_override(preset, None) is preset

    def test_override_merges_onto_existing_zoom(self):
        preset = {"zoom": {"start": 1.0, "end": 1.5, "x": "iw/2"}}
        result = rp.apply_zoom_override(preset, {"start": 1.2, "end": 2.0})
        assert result["zoom"]["start"] == 1.2
        assert result["zoom"]["end"] == 2.0
        assert result["zoom"]["x"] == "iw/2"  # untouched


class TestResolveBgMusic:
    def test_known_alias_resolves_to_existing_file(self):
        path = rp.resolve_bg_music("luxury_chill")
        assert path is not None
        assert path.exists()
        assert path.name == rp.BG_MUSIC_ALIASES["luxury_chill"]

    def test_unknown_key_falls_back_to_some_track(self):
        # No matching file for a nonsense key; resolve_bg_music should still
        # return *some* playable track rather than raising, since a missing
        # bg_music value shouldn't fail a whole render.
        path = rp.resolve_bg_music("definitely_not_a_real_track_key")
        assert path is not None
        assert path.exists()


# ---------------------------------------------------------------------------
# Filtergraph construction
# ---------------------------------------------------------------------------

@pytest.fixture
def preset():
    return rp.get_preset("luxury_gold")


@pytest.fixture
def scene_texts():
    return [["Discover Egypt"], ["Book Now"]]


class TestBuildFilterComplex:
    def test_rejects_empty_scene_texts(self, preset):
        with pytest.raises(ValueError):
            rp.build_filter_complex(2, "font.ttf", [], None, None, preset)

    def test_no_music_uses_silent_source(self, preset, scene_texts):
        filter_complex, outro_input, subscribe_input, audio_input, duration = rp.build_filter_complex(
            2, "font.ttf", scene_texts, None, None, preset
        )
        assert audio_input == ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        assert "a_final" in filter_complex
        assert "a_voice" not in filter_complex
        assert duration > 0

    def test_with_music_no_voiceover(self, preset, scene_texts):
        music_path = rp.resolve_bg_music("luxury_chill")
        filter_complex, _, _, audio_input, _ = rp.build_filter_complex(
            2, "font.ttf", scene_texts, music_path, None, preset
        )
        assert audio_input == ["-i", str(music_path)]
        assert "volume=0.25" in filter_complex
        assert "sidechaincompress" not in filter_complex

    def test_with_voiceover_adds_ducking_chain(self, preset, scene_texts, tmp_path):
        music_path = rp.resolve_bg_music("luxury_chill")
        voiceover_path = tmp_path / "voice.wav"
        voiceover_path.write_bytes(b"fake-wav-bytes")

        filter_complex, _, _, audio_input, _ = rp.build_filter_complex(
            2, "font.ttf", scene_texts, music_path, None, preset,
            voiceover_path=voiceover_path,
        )
        assert audio_input == ["-i", str(music_path), "-i", str(voiceover_path)]
        assert "sidechaincompress" in filter_complex
        assert "a_voice" in filter_complex
        assert "amix=inputs=2" in filter_complex

    def test_missing_voiceover_file_is_ignored(self, preset, scene_texts, tmp_path):
        # build_filter_complex itself only checks .exists(); render_video is
        # responsible for not passing a path when synthesis failed, but the
        # builder should degrade gracefully either way.
        missing_path = tmp_path / "does_not_exist.wav"
        filter_complex, _, _, audio_input, _ = rp.build_filter_complex(
            2, "font.ttf", scene_texts, None, None, preset,
            voiceover_path=missing_path,
        )
        assert "a_voice" not in filter_complex
        assert audio_input == ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]

    def test_scales_with_number_of_images(self, preset):
        texts = [["A"], ["B"], ["C"], ["D"]]
        filter_complex, _, _, _, _ = rp.build_filter_complex(
            4, "font.ttf", texts, None, None, preset
        )
        for i in range(4):
            assert f"[{i}:v]" in filter_complex
        assert "v_final" in filter_complex

    def test_debug_mode_shrinks_total_duration(self, preset, scene_texts):
        _, _, _, _, normal_duration = rp.build_filter_complex(
            2, "font.ttf", scene_texts, None, None, preset, debug_mode=False
        )
        _, _, _, _, debug_duration = rp.build_filter_complex(
            2, "font.ttf", scene_texts, None, None, preset, debug_mode=True
        )
        assert debug_duration < normal_duration

    def test_focus_points_shift_the_crop_window(self, preset, scene_texts):
        # Focus-point cropping only applies in the legacy "fill" frame mode.
        filter_complex, *_ = rp.build_filter_complex(
            2, "font.ttf", scene_texts, None, None, preset,
            focus_points=[(0.8, 0.2), (0.5, 0.5)],
            frame_mode="fill",
        )
        assert "clip(0.8*iw-810.0" in filter_complex
        assert "clip(0.2*ih-1440.0" in filter_complex
        # Default (no focus point given) still centers, matching old behavior.
        assert "clip(0.5*iw-810.0" in filter_complex

    def test_no_focus_points_defaults_to_center_crop(self, preset, scene_texts):
        filter_complex, *_ = rp.build_filter_complex(
            2, "font.ttf", scene_texts, None, None, preset, frame_mode="fill"
        )
        assert filter_complex.count("clip(0.5*iw-810.0") == 2

    def test_reveal_is_default_and_pans_full_screen(self, preset, scene_texts):
        # Default (reveal) mode fills the frame (cover scale) and pans instead of
        # cropping to a fixed window - so no focus-crop clip() and no blur bars.
        filter_complex, *_ = rp.build_filter_complex(
            2, "font.ttf", scene_texts, None, None, preset
        )
        assert "clip(" not in filter_complex
        assert "gblur" not in filter_complex
        assert filter_complex.count("force_original_aspect_ratio=increase") == 2
        assert "flags=lanczos" in filter_complex
        # A moving crop window (references the frame counter n) = the reveal pan.
        assert "crop=1080:1920:x=" in filter_complex

    def test_fit_mode_shows_whole_image_with_blur(self, preset, scene_texts):
        filter_complex, *_ = rp.build_filter_complex(
            2, "font.ttf", scene_texts, None, None, preset, frame_mode="fit"
        )
        assert "clip(" not in filter_complex
        assert filter_complex.count("force_original_aspect_ratio=decrease") == 2
        assert "gblur" in filter_complex
        assert "[fit_comp_0]" in filter_complex
        assert "[fit_comp_1]" in filter_complex


# ---------------------------------------------------------------------------
# Smart image cropping (focus-point detection)
# ---------------------------------------------------------------------------

class TestBuildFocusCropExpr:
    def test_center_focus_matches_old_static_crop(self):
        x_expr, y_expr = rp.build_focus_crop_expr((0.5, 0.5))
        assert x_expr == "clip(0.5*iw-810.0\\,0\\,iw-1620)"
        assert y_expr == "clip(0.5*ih-1440.0\\,0\\,ih-2880)"

    def test_off_center_focus_shifts_window(self):
        x_expr, y_expr = rp.build_focus_crop_expr((0.9, 0.1))
        assert "0.9*iw" in x_expr
        assert "0.1*ih" in y_expr


class TestDetectFocusPoint:
    def test_unreadable_image_defaults_to_center(self, tmp_path):
        bogus = tmp_path / "not_an_image.jpg"
        bogus.write_bytes(b"not actually an image")
        assert rp.detect_focus_point(bogus) == (0.5, 0.5)

    def test_blank_image_defaults_to_center(self, tmp_path):
        from PIL import Image as PILImage

        blank = tmp_path / "blank.jpg"
        PILImage.new("RGB", (400, 600), color="gray").save(blank)
        fx, fy = rp.detect_focus_point(blank)
        assert fx == pytest.approx(0.5, abs=0.01)
        assert fy == pytest.approx(0.5, abs=0.01)

    def test_off_center_detail_pulls_focus_toward_it(self, tmp_path):
        from PIL import Image as PILImage, ImageDraw

        # Flat gray field with a single high-contrast square near the top
        # right corner - edge-density fallback should pull focus that way.
        img = PILImage.new("RGB", (400, 600), color="gray")
        draw = ImageDraw.Draw(img)
        draw.rectangle([320, 20, 380, 80], fill="black")
        path = tmp_path / "detail.jpg"
        img.save(path)

        fx, fy = rp.detect_focus_point(path)
        assert fx > 0.6
        assert fy < 0.4
