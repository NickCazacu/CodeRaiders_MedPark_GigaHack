"""Stage 1 — audio preprocessing.

    audio file -> 16 kHz mono, cleaned, level-normalised waveform
               -> speech regions (VAD)
               -> ASR-sized chunks on the original timeline

Why it matters for the pipeline: everything downstream is priced per second of audio.
A hospital meeting is typically 40-60 % silence, so dropping it is the cheapest speedup
in the whole system — and it removes the material Whisper hallucinates on.

Design rules:
* chunk boundaries stay on the *original* timeline, so ASR timestamps never need a
  remapping table and stay aligned with diarization and the MoM evidence quotes;
* a chunk is never cut inside a word — cuts land in detected silence, and when a
  speech run is longer than ``chunk_max_s`` the cut goes to the quietest point near
  the target;
* pure function of (path, config): no globals, no network, safe to call per job.

CLI::

    python -m app.pipeline.preprocess data/audio/meeting.m4a --out data/work -v
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from app.config import PreprocessConfig, preprocess_config
from app.models import AudioChunk, PreprocessResult, SpeechRegion
from app.pipeline import audio_io, vad

log = logging.getLogger(__name__)

_CACHE_VERSION = 1


def preprocess(
    source: Path, cfg: PreprocessConfig | None = None, *, job_id: str | None = None
) -> PreprocessResult:
    """Decode, clean and segment one audio file. Never logs audio content."""
    cfg = cfg or preprocess_config()
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    job_id = job_id or uuid.uuid4().hex[:12]
    cfg.work_dir.mkdir(parents=True, exist_ok=True)

    key = _cache_key(source, cfg)
    if cfg.cache and (cached := _load_cache(cfg, key, job_id)) is not None:
        log.info("job=%s stage=preprocess cache=hit chunks=%d", job_id, len(cached.chunks))
        return cached

    timings: dict[str, float] = {}
    warnings: list[str] = []

    with _timed(timings, "probe"):
        info = audio_io.probe(source)
    if info.duration_s <= 0:
        warnings.append("container reports no duration; decoding blind")

    with _timed(timings, "decode"):
        audio = audio_io.decode(source, cfg, info)
    duration_s = len(audio) / cfg.sample_rate
    if duration_s < 0.1:
        raise ValueError(f"{source.name}: decoded to {duration_s:.2f}s of audio")

    with _timed(timings, "envelope"):
        envelope = vad.rms_envelope(audio, cfg.sample_rate)

    with _timed(timings, "vad"):
        regions, backend = vad.detect_speech(audio, cfg, envelope)
    speech_s = vad.speech_seconds(regions)
    if not regions:
        warnings.append("no speech detected — check the microphone track or lower vad_threshold")
    elif speech_s / duration_s > 0.98:
        warnings.append("VAD found speech nearly everywhere — noisy room or VAD too permissive")

    with _timed(timings, "normalize"):
        gain_db = _normalize(audio, regions, cfg)

    with _timed(timings, "chunk"):
        chunks = pack_chunks(regions, cfg, envelope=envelope, duration_s=duration_s)

    wav_path: Path | None = None
    with _timed(timings, "write"):
        if cfg.write_processed_wav:
            wav_path = audio_io.write_wav(cfg.work_dir / f"{key}.wav", audio, cfg.sample_rate)
        if cfg.write_chunk_wavs:
            chunks = _write_chunk_wavs(chunks, audio, cfg, key)

    result = PreprocessResult(
        job_id=job_id,
        source_path=source,
        wav_path=wav_path,
        sample_rate=cfg.sample_rate,
        source_duration_s=info.duration_s or duration_s,
        audio_duration_s=duration_s,
        speech_duration_s=speech_s,
        chunks=chunks,
        regions=regions,
        vad_backend=backend,
        gain_db=gain_db,
        timings_s={k: round(v, 3) for k, v in timings.items()},
        warnings=warnings,
    )
    total = sum(timings.values())
    log.info(
        "job=%s stage=preprocess backend=%s audio=%.1fs speech=%.1fs (%.0f%%) chunks=%d "
        "asr_input=%.1fs took=%.2fs rtf=%.3f",
        job_id, backend, duration_s, speech_s, 100 * result.speech_ratio,
        len(chunks), result.asr_input_s, total, total / max(duration_s, 1e-6),
    )  # fmt: skip
    for warning in warnings:
        log.warning("job=%s stage=preprocess %s", job_id, warning)

    if cfg.cache:
        _store_cache(cfg, key, result)
    return result


# ----------------------------------------------------------------------------------
# chunking
# ----------------------------------------------------------------------------------


def pack_chunks(
    regions: list[SpeechRegion],
    cfg: PreprocessConfig,
    *,
    envelope: tuple[np.ndarray, float] | None = None,
    duration_s: float | None = None,
) -> list[AudioChunk]:
    """Group speech regions into ASR-sized chunks.

    Greedy packing: keep adding regions while the chunk stays under ``chunk_target_s``
    and the gap to the next region is small enough to be worth carrying (silence inside
    a chunk costs ASR time, but cutting too eagerly loses conversational context).
    Regions longer than ``chunk_max_s`` are split first, at their quietest interior
    point near the target length.
    """
    if not regions:
        return []

    units: list[SpeechRegion] = []
    for region in regions:
        units.extend(_split_long(region, cfg, envelope))

    chunks: list[AudioChunk] = []
    current: list[SpeechRegion] = []
    for unit in units:
        if current:
            gap = unit.start - current[-1].end
            span = unit.end - current[0].start
            too_long = span > cfg.chunk_max_s
            packed = current[-1].end - current[0].start
            complete = span > cfg.chunk_target_s and packed >= cfg.chunk_min_s
            if too_long or gap > cfg.merge_gap_s or complete:
                chunks.append(_make_chunk(len(chunks), current))
                current = []
        current.append(unit)
    if current:
        chunks.append(_make_chunk(len(chunks), current))

    return _merge_tiny(chunks, cfg)


def _split_long(
    region: SpeechRegion, cfg: PreprocessConfig, envelope: tuple[np.ndarray, float] | None
) -> list[SpeechRegion]:
    """Cut a too-long speech run into <= chunk_max_s pieces at low-energy points."""
    if region.duration <= cfg.chunk_max_s:
        return [region]

    pieces: list[SpeechRegion] = []
    start = region.start
    while region.end - start > cfg.chunk_max_s:
        target = start + cfg.chunk_target_s
        cut = _quietest_point(envelope, target, window_s=1.0)
        # Keep the cut inside the legal band even if the search drifted, and always
        # make progress so the loop terminates.
        floor = start + max(cfg.chunk_min_s, 1.0)
        cut = min(max(cut, floor), start + cfg.chunk_max_s, region.end)
        pieces.append(SpeechRegion(start=start, end=cut))
        start = cut
    pieces.append(SpeechRegion(start=start, end=region.end))
    return pieces


def _quietest_point(
    envelope: tuple[np.ndarray, float] | None, target_s: float, window_s: float
) -> float:
    """Local RMS minimum within +/- ``window_s`` of ``target_s`` (target if unknown)."""
    if envelope is None:
        return target_s
    levels, hop_s = envelope
    if levels.size == 0:
        return target_s
    low = max(0, int((target_s - window_s) / hop_s))
    high = min(levels.size, int((target_s + window_s) / hop_s) + 1)
    if high <= low:
        return target_s
    return float((low + int(np.argmin(levels[low:high]))) * hop_s)


def _make_chunk(index: int, regions: list[SpeechRegion]) -> AudioChunk:
    return AudioChunk(
        index=index,
        start=regions[0].start,
        end=regions[-1].end,
        speech_s=sum(r.duration for r in regions),
    )


def _merge_tiny(chunks: list[AudioChunk], cfg: PreprocessConfig) -> list[AudioChunk]:
    """Fold chunks below ``chunk_min_s`` into a neighbour (Whisper hallucinates on
    very short clips), as long as the merge does not break ``chunk_max_s``."""
    merged: list[AudioChunk] = []
    for chunk in chunks:
        if merged and chunk.duration < cfg.chunk_min_s:
            previous = merged[-1]
            if chunk.end - previous.start <= cfg.chunk_max_s:
                merged[-1] = AudioChunk(
                    index=previous.index,
                    start=previous.start,
                    end=chunk.end,
                    speech_s=previous.speech_s + chunk.speech_s,
                )
                continue
        merged.append(chunk)
    return [c.model_copy(update={"index": i}) for i, c in enumerate(merged)]


# ----------------------------------------------------------------------------------
# level normalisation
# ----------------------------------------------------------------------------------


def _normalize(audio: np.ndarray, regions: list[SpeechRegion], cfg: PreprocessConfig) -> float:
    """Apply one global gain computed from speech only. Returns the gain in dB.

    A single gain (rather than a compressor) keeps the speech/noise ratio intact, so
    the VAD decisions above stay valid and Whisper sees the same signal it was trained
    on. ``level_mode="dynamic"`` instead does per-speaker levelling inside ffmpeg.
    """
    if cfg.level_mode != "global" or not regions or not isinstance(audio, np.ndarray):
        return 0.0
    if not audio.flags.writeable:  # memmapped decode of a very long file
        return 0.0

    speech = np.concatenate(
        [
            audio[int(r.start * cfg.sample_rate) : int(r.end * cfg.sample_rate)]
            for r in regions[:200]  # a few minutes of speech is plenty for an RMS estimate
        ]
    )
    rms = float(np.sqrt(np.mean(np.square(speech, dtype=np.float64))))
    if rms <= 1e-6:
        return 0.0

    gain_db = cfg.target_rms_dbfs - 20.0 * np.log10(rms)
    gain_db = float(np.clip(gain_db, -cfg.max_gain_db, cfg.max_gain_db))
    gain = 10.0 ** (gain_db / 20.0)

    peak = float(np.max(np.abs(audio)))
    if peak * gain > 0.99:  # never clip: back off instead
        gain = 0.99 / max(peak, 1e-9)
        gain_db = 20.0 * float(np.log10(gain))
    np.multiply(audio, np.float32(gain), out=audio)
    return round(gain_db, 2)


# ----------------------------------------------------------------------------------
# chunk wavs, cache, timing
# ----------------------------------------------------------------------------------


def _write_chunk_wavs(
    chunks: list[AudioChunk], audio: np.ndarray, cfg: PreprocessConfig, key: str
) -> list[AudioChunk]:
    out_dir = cfg.work_dir / f"{key}_chunks"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[AudioChunk] = []
    for chunk in chunks:
        path = out_dir / f"chunk_{chunk.index:04d}.wav"
        audio_io.write_wav(path, chunk.samples(audio, cfg.sample_rate), cfg.sample_rate)
        written.append(chunk.model_copy(update={"path": path}))
    return written


def cleanup(result: PreprocessResult, *, keep_wav: bool = False) -> int:
    """Delete the artefacts of one job. Call it once the MoM has been sent.

    The retention policy is the caller's decision; this only removes what stage 1
    wrote (cleaned WAV, chunk WAVs, cache entry). Returns the number of files removed.
    """
    targets: list[Path] = []
    if result.wav_path is not None:
        if not keep_wav:
            targets.append(result.wav_path)
        targets.append(result.wav_path.with_suffix(".preprocess.json"))
    targets.extend(c.path for c in result.chunks if c.path is not None)

    removed = 0
    for target in targets:
        try:
            target.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("job=%s could not delete %s: %s", result.job_id, target.name, exc)
    for chunk_dir in {c.path.parent for c in result.chunks if c.path is not None}:
        try:
            chunk_dir.rmdir()
        except OSError:
            pass
    log.info("job=%s stage=preprocess cleanup removed=%d", result.job_id, removed)
    return removed


def _cache_key(source: Path, cfg: PreprocessConfig) -> str:
    stat = source.stat()
    payload = json.dumps(
        {
            "v": _CACHE_VERSION,
            "path": str(source.resolve()),
            "size": stat.st_size,
            "mtime": stat.st_mtime_ns,
            "cfg": cfg.model_dump(mode="json", exclude={"work_dir", "cache"}),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{source.stem[:40]}_{digest}"


def _cache_path(cfg: PreprocessConfig, key: str) -> Path:
    return cfg.work_dir / f"{key}.preprocess.json"


def _load_cache(cfg: PreprocessConfig, key: str, job_id: str) -> PreprocessResult | None:
    path = _cache_path(cfg, key)
    if not path.is_file():
        return None
    try:
        result = PreprocessResult.model_validate_json(path.read_text())
    except (ValueError, OSError):
        log.warning("unreadable preprocess cache %s, recomputing", path.name)
        return None
    if result.wav_path is not None and not result.wav_path.is_file():
        return None
    return result.model_copy(update={"job_id": job_id})


def _store_cache(cfg: PreprocessConfig, key: str, result: PreprocessResult) -> None:
    try:
        _cache_path(cfg, key).write_text(result.model_dump_json(indent=2))
    except OSError as exc:  # cache is an optimisation, never fatal
        log.warning("could not write preprocess cache: %s", exc)


@contextmanager
def _timed(store: dict[str, float], name: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        store[name] = store.get(name, 0.0) + time.perf_counter() - start


# ----------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.pipeline.preprocess",
        description="Stage 1: decode, denoise, VAD and chunk one meeting recording.",
    )
    parser.add_argument("audio", type=Path, help="input audio/video file")
    parser.add_argument("--out", type=Path, default=None, help="work dir (default data/work)")
    parser.add_argument("--profile", choices=["gpu16", "cpu32"], default=None)
    parser.add_argument("--denoise", choices=["off", "light", "aggressive"], default=None)
    parser.add_argument("--level", choices=["none", "global", "dynamic"], default=None)
    parser.add_argument(
        "--vad", choices=["auto", "silero-onnx", "silero-torch", "energy"], default=None
    )
    parser.add_argument("--chunk-target", type=float, default=None, metavar="S")
    parser.add_argument("--chunk-max", type=float, default=None, metavar="S")
    parser.add_argument("--write-chunks", action="store_true", help="also write one WAV per chunk")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    overrides = {
        "work_dir": args.out,
        "denoise": args.denoise,
        "level_mode": args.level,
        "vad_backend": args.vad,
        "chunk_target_s": args.chunk_target,
        "chunk_max_s": args.chunk_max,
        "write_chunk_wavs": args.write_chunks or None,
        "cache": False if args.no_cache else None,
    }
    cfg = preprocess_config(args.profile, **{k: v for k, v in overrides.items() if v is not None})

    started = time.perf_counter()
    result = preprocess(args.audio, cfg)
    wall = time.perf_counter() - started

    if args.json:
        print(result.model_dump_json(indent=2))
        return 0

    saved = result.audio_duration_s - result.asr_input_s
    print(f"\n  source        {result.source_path.name}")
    print(f"  duration      {result.audio_duration_s:8.1f} s")
    print(f"  speech        {result.speech_duration_s:8.1f} s  ({100 * result.speech_ratio:.0f} %)")
    print(f"  ASR input     {result.asr_input_s:8.1f} s  "
          f"(-{saved:.0f} s, {100 * saved / max(result.audio_duration_s, 1e-9):.0f} % less audio)")
    print(f"  chunks        {len(result.chunks):8d}  "
          f"(vad={result.vad_backend}, gain={result.gain_db:+.1f} dB)")
    if result.wav_path:
        print(f"  wav           {result.wav_path}")
    print(f"  stages        {', '.join(f'{k}={v:.2f}s' for k, v in result.timings_s.items())}")
    rtf = wall / max(result.audio_duration_s, 1e-9)
    print(f"  total         {wall:8.2f} s  (realtime factor {rtf:.3f})")
    for warning in result.warnings:
        print(f"  ! {warning}")
    print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
