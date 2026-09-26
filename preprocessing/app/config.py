"""Configuration for the pipeline.

Only the preprocess stage is defined here so far. The global ``Settings`` object
(pydantic-settings, ``PROFILE`` env var, model paths, Ollama/n8n URLs) belongs in this
module too and should compose ``PreprocessConfig`` when the other stages land.

Nothing here performs I/O or network access at import time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Profile = Literal["gpu16", "cpu32"]
DenoiseLevel = Literal["off", "light", "aggressive"]
LevelMode = Literal["none", "global", "dynamic"]
VadBackend = Literal["auto", "silero-onnx", "silero-torch", "energy"]

DEFAULT_WORK_DIR = Path(os.environ.get("MOM_WORK_DIR", "data/work"))


class PreprocessConfig(BaseModel):
    """Stage 1 settings: decode -> clean -> VAD -> chunk.

    Defaults are tuned for meeting-room recordings (far-field mic, several speakers,
    long silent stretches) and are deliberately conservative on denoising: Whisper is
    trained on noisy audio and over-filtering costs more WER than the noise itself.
    """

    model_config = {"extra": "forbid"}

    # --- output format (fixed by Whisper: 16 kHz mono) -------------------------------
    sample_rate: int = 16_000
    """Whisper front-end rate. Do not change unless the ASR model changes."""

    # --- ffmpeg cleanup chain ---------------------------------------------------------
    denoise: DenoiseLevel = "light"
    """off = decode only; light = afftdn noise tracking; aggressive = stronger + lowpass."""
    highpass_hz: int = 70
    """Removes mains hum, HVAC rumble and desk thumps. 0 disables."""
    lowpass_hz: int | None = None
    """Usually harmful (kills fricatives). Set ~7500 only for very hissy recordings."""
    rnnoise_model: Path | None = None
    """Optional local .rnnn model for ffmpeg ``arnndn`` (best denoiser, needs the file)."""
    ffmpeg_threads: int = 0
    """0 = let ffmpeg pick (one thread per core)."""

    # --- level normalisation ----------------------------------------------------------
    level_mode: LevelMode = "global"
    """global = single gain computed from speech only (no pumping, keeps SNR);
    dynamic = ffmpeg ``speechnorm`` for recordings where speaker loudness varies a lot;
    none = leave levels untouched."""
    target_rms_dbfs: float = -20.0
    max_gain_db: float = 20.0

    # --- voice activity detection ------------------------------------------------------
    vad_backend: VadBackend = "auto"
    """auto = silero-onnx -> silero-torch -> energy (first one that loads)."""
    silero_model_path: Path | None = None
    """Local silero_vad.onnx / .jit. None = look in the installed ``silero_vad`` package
    and in ``models/vad/``. Never downloaded at runtime."""
    vad_threshold: float = 0.5
    """Silero speech probability to enter a speech run."""
    min_speech_ms: int = 250
    """Shorter blips (door, keyboard, cough) are dropped."""
    min_silence_ms: int = 400
    """Gaps shorter than this do not split a speech run — avoids cutting inside a word."""
    speech_pad_ms: int = 200
    """Padding added on both sides of every speech run; protects onsets/codas."""
    vad_workers: int = 0
    """Threads used for the neural VAD. 0 = auto (min(4, cpu_count))."""

    # --- chunking ------------------------------------------------------------------------
    chunk_target_s: float = 15.0
    """Preferred chunk length fed to ASR. 28-30 s is faster with a batched Whisper
    pipeline; 15 s follows the architecture note in CLAUDE.md and is safer for
    code-switching (language is re-checked per chunk)."""
    chunk_max_s: float = 30.0
    """Hard cap — Whisper's receptive field is 30 s, longer chunks get truncated."""
    chunk_min_s: float = 5.0
    """Short chunks give Whisper too little context and hallucinate; merge below this."""
    merge_gap_s: float = 0.8
    """Silence up to this length is kept inside a chunk instead of splitting it."""

    # --- I/O and performance --------------------------------------------------------------
    work_dir: Path = DEFAULT_WORK_DIR
    write_processed_wav: bool = True
    """16-bit mono WAV of the cleaned audio, read back by ASR/diarization."""
    write_chunk_wavs: bool = False
    """One WAV per chunk. Only for debugging or a multi-process ASR worker pool."""
    cache: bool = True
    """Skip work when the same file+config was already preprocessed (dev/eval reruns)."""
    max_in_memory_s: float = 4 * 3600
    """Longer inputs are decoded to a raw file and memory-mapped instead of held in RAM."""

    def filter_chain(self) -> str:
        """Build the ffmpeg -af chain.

        Order matters for speed: resample to 16 kHz mono *first* so every following
        filter runs on 1/6 of the samples of a 48 kHz stereo source.
        """
        parts = [f"aresample={self.sample_rate}:out_chlayout=mono:resampler=soxr:precision=28"]
        if self.highpass_hz:
            parts.append(f"highpass=f={self.highpass_hz}:poles=2")
        if self.rnnoise_model is not None:
            # rnnoise runs at 48 kHz; ffmpeg inserts the conversion automatically.
            parts.append(f"arnndn=m={self.rnnoise_model.as_posix()}")
        elif self.denoise == "light":
            parts.append("afftdn=nr=10:nf=-30:tn=1")
        elif self.denoise == "aggressive":
            parts.append("afftdn=nr=20:nf=-25:tn=1")
        if self.lowpass_hz:
            parts.append(f"lowpass=f={self.lowpass_hz}:poles=2")
        if self.level_mode == "dynamic":
            parts.append("speechnorm=e=12.5:r=0.0001:l=1")
        return ",".join(parts)

    def fallback_filter_chain(self) -> str:
        """Same chain without soxr, for ffmpeg builds compiled without libsoxr."""
        return self.filter_chain().replace(":resampler=soxr:precision=28", "")


def preprocess_config(profile: Profile | None = None, **overrides: object) -> PreprocessConfig:
    """Preprocess settings for a hardware profile (``PROFILE`` env var by default)."""
    profile = profile or os.environ.get("PROFILE", "cpu32")  # type: ignore[assignment]
    defaults: dict[str, object] = {}
    if profile == "cpu32":
        # CPU-only box: ASR is the bottleneck, so spend a little more on VAD threads to
        # cut as much silence as possible before Whisper sees the audio.
        defaults["vad_workers"] = 4
    return PreprocessConfig(**(defaults | overrides))  # type: ignore[arg-type]
