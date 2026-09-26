"""Shared pydantic contracts.

Only the stage-1 (preprocess) models live here so far; ``Segment``/``Transcript``/
``ActionItem``/``MoM`` are added by the ASR and extraction stages.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class SpeechRegion(BaseModel):
    """A continuous stretch of speech on the original audio timeline (seconds)."""

    model_config = {"frozen": True}

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


class AudioChunk(BaseModel):
    """A slice of the cleaned audio handed to ASR.

    ``start``/``end`` are on the *original* timeline, so ASR timestamps only need the
    chunk offset added — silence removal never shifts the clock.
    """

    model_config = {"frozen": True}

    index: int
    start: float
    end: float
    speech_s: float = Field(description="Seconds of detected speech inside the chunk.")
    path: Path | None = Field(default=None, description="Set only if chunk WAVs were written.")

    @property
    def duration(self) -> float:
        return self.end - self.start

    def samples(self, audio, sample_rate: int):  # noqa: ANN001, ANN201 - numpy array in/out
        """View of this chunk inside the full cleaned waveform (no copy)."""
        return audio[int(self.start * sample_rate) : int(self.end * sample_rate)]


class PreprocessResult(BaseModel):
    """Output of stage 1. Serialisable: the waveform itself is read from ``wav_path``."""

    job_id: str
    source_path: Path
    wav_path: Path | None
    sample_rate: int
    source_duration_s: float
    audio_duration_s: float = Field(description="Duration after decoding/cleanup.")
    speech_duration_s: float
    chunks: list[AudioChunk]
    regions: list[SpeechRegion]
    vad_backend: str
    gain_db: float = 0.0
    timings_s: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def speech_ratio(self) -> float:
        return self.speech_duration_s / self.audio_duration_s if self.audio_duration_s else 0.0

    @property
    def silence_removed_s(self) -> float:
        return max(0.0, self.audio_duration_s - self.speech_duration_s)

    @property
    def asr_input_s(self) -> float:
        """Audio actually sent to ASR — the number that drives transcription time."""
        return sum(c.duration for c in self.chunks)
