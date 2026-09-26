"""ffmpeg-backed audio I/O for the preprocess stage.

Everything is local: ffmpeg reads a file on disk and writes raw PCM to a pipe. No
network, no temporary uploads, no external codecs service.

Performance notes:
* one single ffmpeg pass does demux + downmix + resample + cleanup;
* the resample to 16 kHz mono is the *first* filter, so denoising runs on ~6x fewer
  samples than at 48 kHz stereo;
* PCM arrives as float32 over a pipe straight into a preallocated numpy buffer — no
  intermediate WAV file, no int16 round-trip, one allocation for the whole recording;
* files longer than ``max_in_memory_s`` are decoded to a raw file and memory-mapped.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.config import PreprocessConfig

log = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

_READ_BLOCK = 1 << 20  # 1 MiB
_WRITE_BLOCK = 1 << 22  # 4 Mi samples


class FfmpegError(RuntimeError):
    """ffmpeg/ffprobe failed. Message carries the tail of stderr."""


@dataclass(frozen=True)
class AudioInfo:
    path: Path
    duration_s: float
    sample_rate: int
    channels: int
    codec: str
    size_bytes: int


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe(path: Path) -> AudioInfo:
    """Read container/stream metadata with ffprobe."""
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels,codec_name,duration",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise FfmpegError(f"ffprobe failed for {path.name}: {proc.stderr.strip()[-400:]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise FfmpegError(f"no audio stream in {path.name}")
    stream = streams[0]
    duration = _first_float(stream.get("duration"), data.get("format", {}).get("duration"))
    return AudioInfo(
        path=path,
        duration_s=duration,
        sample_rate=int(stream.get("sample_rate") or 0),
        channels=int(stream.get("channels") or 0),
        codec=str(stream.get("codec_name") or "unknown"),
        size_bytes=path.stat().st_size,
    )


def _first_float(*values: object) -> float:
    for value in values:
        try:
            parsed = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0.0


def _decode_cmd(src: Path, chain: str, cfg: PreprocessConfig, dst: str) -> list[str]:
    return [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-threads", str(cfg.ffmpeg_threads),
        "-i", str(src),
        "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-af", chain,
        "-f", "f32le", "-acodec", "pcm_f32le", dst,
    ]  # fmt: skip


def decode(src: Path, cfg: PreprocessConfig, info: AudioInfo | None = None) -> np.ndarray:
    """Decode + clean ``src`` into a mono float32 array at ``cfg.sample_rate``.

    Returns samples in [-1, 1] (ffmpeg may overshoot slightly; callers clip on write).
    """
    info = info or probe(src)
    if info.duration_s > cfg.max_in_memory_s:
        return _decode_to_memmap(src, cfg, info)
    return _decode_to_pipe(src, cfg, info)


def _run_with_fallback(cfg: PreprocessConfig, runner: Callable[[str], np.ndarray]) -> np.ndarray:
    """Try the soxr chain, retry once without soxr for builds lacking libsoxr."""
    chains = [cfg.filter_chain()]
    fallback = cfg.fallback_filter_chain()
    if fallback != chains[0]:
        chains.append(fallback)
    last: FfmpegError | None = None
    for attempt, chain in enumerate(chains):
        try:
            return runner(chain)
        except FfmpegError as exc:
            last = exc
            if attempt + 1 < len(chains):
                log.warning("ffmpeg chain failed, retrying without soxr: %s", exc)
    raise last  # type: ignore[misc]


def _decode_to_pipe(src: Path, cfg: PreprocessConfig, info: AudioInfo) -> np.ndarray:
    def run(chain: str) -> np.ndarray:
        # Preallocate from the probed duration (+2% slack) so the common case is a
        # single allocation; anything beyond that is appended and concatenated.
        expected = int(info.duration_s * cfg.sample_rate * 1.02) + cfg.sample_rate
        buffer = np.empty(max(expected, cfg.sample_rate), dtype=np.float32)
        raw = memoryview(buffer).cast("B")
        filled = 0
        overflow: list[bytes] = []

        with tempfile.TemporaryFile() as errfile:
            proc = subprocess.Popen(
                _decode_cmd(src, chain, cfg, "-"),
                stdout=subprocess.PIPE,
                stderr=errfile,
                stdin=subprocess.DEVNULL,
            )
            assert proc.stdout is not None
            try:
                while filled < len(raw):
                    read = proc.stdout.readinto(raw[filled : filled + _READ_BLOCK])
                    if not read:
                        break
                    filled += read
                else:
                    while block := proc.stdout.read(_READ_BLOCK):
                        overflow.append(block)
            finally:
                proc.stdout.close()
                proc.wait()
            if proc.returncode != 0:
                errfile.seek(0)
                detail = errfile.read().decode(errors="replace")[-400:]
                raise FfmpegError(f"ffmpeg failed for {src.name}: {detail}")

        audio = buffer[: filled // 4]
        if overflow:
            extra = np.frombuffer(b"".join(overflow), dtype=np.float32)
            audio = np.concatenate([audio, extra])
        return audio

    return _run_with_fallback(cfg, run)


def _decode_to_memmap(src: Path, cfg: PreprocessConfig, info: AudioInfo) -> np.ndarray:
    """Very long recording: let ffmpeg write raw f32 to disk, then memory-map it."""
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    raw_path = cfg.work_dir / f"{src.stem}.{cfg.sample_rate}.f32"

    def run(chain: str) -> np.ndarray:
        proc = subprocess.run(
            [*_decode_cmd(src, chain, cfg, "-y"), str(raw_path)],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise FfmpegError(
                f"ffmpeg failed for {src.name}: {proc.stderr.decode(errors='replace')[-400:]}"
            )
        return np.memmap(raw_path, dtype=np.float32, mode="r")

    log.info("decoding %.0f s to memmap (%s)", info.duration_s, raw_path.name)
    return _run_with_fallback(cfg, run)


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> Path:
    """Write 16-bit mono PCM. Converts in blocks so peak memory stays flat."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        for start in range(0, len(audio), _WRITE_BLOCK):
            block = np.clip(audio[start : start + _WRITE_BLOCK], -1.0, 1.0)
            out.writeframes((block * 32767.0).astype("<i2").tobytes())
    return path


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a 16-bit mono PCM WAV written by :func:`write_wav`."""
    with wave.open(str(path), "rb") as src:
        if src.getsampwidth() != 2 or src.getnchannels() != 1:
            raise ValueError(f"{path.name}: expected 16-bit mono PCM")
        frames = src.readframes(src.getnframes())
        rate = src.getframerate()
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    return audio, rate
