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


class TTSError(Exception):
    pass


def synthesize_voiceover(
    text: str, output_path: Path, voice_name: str = DEFAULT_VOICE
) -> Path:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise TTSError("GEMINI_API_KEY is not configured.")

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
