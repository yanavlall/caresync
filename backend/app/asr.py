"""
Audio transcription backends.

Supports three modes, selected by the ASR_BACKEND env var:

  mock             Returns a fixed transcript. Used in tests and when no
                   API keys are available.
  openai_whisper   Calls OpenAI's Whisper-1 model via their API.
  faster_whisper   Runs faster-whisper locally on CPU or GPU.

The interface is a single async function transcribe(audio_bytes) -> str.
"""

import logging
from typing import Callable

from .config import settings

logger = logging.getLogger(__name__)


MOCK_TRANSCRIPT = (
    "This is unit 42 calling in a patient en route. "
    "Patient name is John Doe, 54 year old male. "
    "Chief complaint is chest pain that started about 20 minutes ago while he was shoveling snow. "
    "Blood pressure is 148 over 92, heart rate 112, respiratory rate 22, SpO2 94 on room air. "
    "Patient is alert and oriented, GCS 15. "
    "We have him on 4 liters of oxygen via nasal cannula and gave him 324 milligrams of aspirin chewed. "
    "Assessment is possible acute coronary syndrome. "
    "ETA to the ED is 8 minutes."
)


async def _mock_transcribe(audio_bytes: bytes) -> str:
    logger.info("ASR mock: returning fixed transcript (%d bytes of audio ignored)", len(audio_bytes))
    return MOCK_TRANSCRIPT


async def _openai_whisper_transcribe(audio_bytes: bytes) -> str:
    """Call OpenAI's Whisper API. Requires OPENAI_API_KEY."""
    if not settings.openai_api_key:
        logger.warning("ASR openai_whisper selected but OPENAI_API_KEY missing; falling back to mock")
        return await _mock_transcribe(audio_bytes)

    # Import lazily so the dep is optional.
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    import io
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "recording.wav"
    resp = await client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
    )
    return resp.text


async def _faster_whisper_transcribe(audio_bytes: bytes) -> str:
    """Run faster-whisper locally. Heavier dep; used only if explicitly selected."""
    import asyncio
    import tempfile

    from faster_whisper import WhisperModel  # type: ignore

    def _run() -> str:
        model = WhisperModel("small", device="auto", compute_type="auto")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
            f.write(audio_bytes)
            f.flush()
            segments, _info = model.transcribe(f.name)
            return " ".join(seg.text.strip() for seg in segments)

    # Run the blocking model in a thread so we don't stall the event loop.
    return await asyncio.to_thread(_run)


_BACKENDS: dict[str, Callable] = {
    "mock": _mock_transcribe,
    "openai_whisper": _openai_whisper_transcribe,
    "faster_whisper": _faster_whisper_transcribe,
}


async def transcribe(audio_bytes: bytes) -> str:
    """Dispatch to the configured ASR backend."""
    backend = _BACKENDS.get(settings.asr_backend, _mock_transcribe)
    logger.info("Transcribing with backend=%s bytes=%d", settings.asr_backend, len(audio_bytes))
    return await backend(audio_bytes)
