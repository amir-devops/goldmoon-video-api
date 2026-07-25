"""Gemini text-to-speech voiceover synthesis."""

from __future__ import annotations

import os
import wave
from pathlib import Path

from google import genai
from google.genai import types

GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_VOICE = "Kore"
TTS_SAMPLE_RATE = 24000

# Curated subset of Gemini's prebuilt voices (it offers ~30; these cover a
# useful spread of tone for marketing narration). Any other valid Gemini
# voice name still works - this list is for validation/UI, not an allowlist
# enforced against the API itself.
AVAILABLE_VOICES = {
    "Kore": "Firm, confident (default)",
    "Puck": "Upbeat, energetic",
    "Charon": "Deep, informative",
    "Zephyr": "Bright, breezy",
    "Fenrir": "Excitable, punchy",
    "Leda": "Youthful, warm",
    "Orus": "Authoritative, steady",
    "Aoede": "Airy, gentle",
}


class TTSError(Exception):
    pass


def get_audio_duration(path: Path) -> float:
    """Return the duration in seconds of a WAV file written by synthesize_voiceover."""
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def synthesize_voiceover(
    text: str, output_path: Path, voice_name: str = DEFAULT_VOICE
) -> Path:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise TTSError("GEMINI_API_KEY is not configured.")

    voice_name = (voice_name or DEFAULT_VOICE).strip() or DEFAULT_VOICE

    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=GEMINI_TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice_name
                        )
                    )
                ),
            ),
        )
    except Exception as exc:
        raise TTSError(f"Gemini TTS request failed: {exc}") from exc

    if not response.candidates:
        reason = getattr(response.prompt_feedback, "block_reason", None)
        raise TTSError(f"Gemini TTS returned no candidates (block_reason={reason}).")

    parts = response.candidates[0].content.parts
    if not parts or parts[0].inline_data is None:
        raise TTSError("Gemini TTS response contained no audio data.")

    audio_data = parts[0].inline_data.data

    if not audio_data:
        raise TTSError("Gemini TTS returned no audio data.")

    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TTS_SAMPLE_RATE)
        wf.writeframes(audio_data)

    return output_path
