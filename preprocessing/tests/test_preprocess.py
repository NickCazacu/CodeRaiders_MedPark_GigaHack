"""Stage 1 tests. No GPU, no network, no real recordings: fixtures are synthetic."""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
import pytest

from app.config import PreprocessConfig
from app.models import SpeechRegion
from app.pipeline import audio_io, vad
from app.pipeline.preprocess import cleanup, pack_chunks, preprocess

SR = 16_000
needs_ffmpeg = pytest.mark.skipif(not audio_io.ffmpeg_available(), reason="ffmpeg not installed")


def _tone(seconds: float, freq: float = 180.0, amp: float = 0.3) -> np.ndarray:
    """Voiced-ish burst: a low fundamental plus harmonics, which both VADs accept."""
    t = np.arange(int(seconds * SR)) / SR
    wave_ = sum(amp / (k + 1) * np.sin(2 * math.pi * freq * (k + 1) * t) for k in range(4))
    envelope = 0.5 * (1 - np.cos(2 * math.pi * np.clip(t / 0.05, 0, 1) / 2))  # soft onset
    return (wave_ * envelope).astype(np.float32)


def _silence(seconds: float, noise: float = 0.001) -> np.ndarray:
    rng = np.random.default_rng(7)
    return (rng.standard_normal(int(seconds * SR)) * noise).astype(np.float32)


def _layout(*parts: tuple[str, float]) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Build audio from ("speech"|"silence", seconds) parts; returns audio + true spans."""
    blocks, spans, cursor = [], [], 0.0
    for kind, seconds in parts:
        blocks.append(_tone(seconds) if kind == "speech" else _silence(seconds))
        if kind == "speech":
            spans.append((cursor, cursor + seconds))
        cursor += seconds
    return np.concatenate(blocks), spans


@pytest.fixture
def cfg(tmp_path: Path) -> PreprocessConfig:
    return PreprocessConfig(work_dir=tmp_path, vad_backend="energy", cache=False)


@pytest.fixture
def meeting_wav(tmp_path: Path) -> tuple[Path, list[tuple[float, float]]]:
    """A miniature 'meeting': talk, pause, talk, long pause, talk."""
    audio, spans = _layout(
        ("silence", 1.0), ("speech", 6.0), ("silence", 3.0),
        ("speech", 4.0), ("silence", 5.0), ("speech", 8.0), ("silence", 1.0),
    )  # fmt: skip
    path = tmp_path / "meeting.wav"
    audio_io.write_wav(path, audio, SR)
    return path, spans


# ----------------------------------------------------------------------------------
# audio_io
# ----------------------------------------------------------------------------------


def test_write_and_read_wav_roundtrip(tmp_path: Path) -> None:
    audio = _tone(0.5)
    path = audio_io.write_wav(tmp_path / "a.wav", audio, SR)
    restored, rate = audio_io.read_wav(path)
    assert rate == SR
    assert len(restored) == len(audio)
    assert np.max(np.abs(restored - audio)) < 1e-3  # 16-bit quantisation only


def test_write_wav_clips_instead_of_wrapping(tmp_path: Path) -> None:
    loud = np.array([2.0, -2.0, 0.0], dtype=np.float32)
    restored, _ = audio_io.read_wav(audio_io.write_wav(tmp_path / "loud.wav", loud, SR))
    assert restored.max() > 0.99 and restored.min() < -0.99


@needs_ffmpeg
def test_decode_resamples_and_downmixes(tmp_path: Path, cfg: PreprocessConfig) -> None:
    stereo = tmp_path / "stereo48.wav"
    samples = (np.random.default_rng(1).standard_normal((48_000, 2)) * 0.1).astype(np.float32)
    with wave.open(str(stereo), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(48_000)
        out.writeframes((samples * 32767).astype("<i2").tobytes())

    info = audio_io.probe(stereo)
    assert (info.sample_rate, info.channels) == (48_000, 2)

    audio = audio_io.decode(stereo, cfg, info)
    assert audio.dtype == np.float32
    assert abs(len(audio) - SR) < SR * 0.05  # 1 s at 16 kHz mono


@needs_ffmpeg
def test_probe_rejects_non_audio(tmp_path: Path) -> None:
    bogus = tmp_path / "notes.txt"
    bogus.write_text("no audio here")
    with pytest.raises(audio_io.FfmpegError):
        audio_io.probe(bogus)


# ----------------------------------------------------------------------------------
# VAD
# ----------------------------------------------------------------------------------


def test_energy_vad_finds_speech_and_skips_silence(cfg: PreprocessConfig) -> None:
    audio, spans = _layout(("silence", 2.0), ("speech", 3.0), ("silence", 4.0), ("speech", 2.0))
    regions, backend = vad.detect_speech(audio, cfg)

    assert backend == "energy"
    assert len(regions) == len(spans)
    for region, (start, end) in zip(regions, spans, strict=True):
        assert region.start == pytest.approx(start, abs=0.35)  # speech_pad_ms widens it
        assert region.end == pytest.approx(end, abs=0.35)


def test_vad_drops_short_blips(cfg: PreprocessConfig) -> None:
    audio, _ = _layout(("silence", 1.0), ("speech", 0.08), ("silence", 1.0), ("speech", 2.0))
    regions, _ = vad.detect_speech(audio, cfg)
    assert len(regions) == 1
    assert regions[0].duration == pytest.approx(2.4, abs=0.4)


def test_vad_bridges_short_gaps(cfg: PreprocessConfig) -> None:
    """A 150 ms pause inside a sentence must not split it (min_silence_ms=400)."""
    audio, _ = _layout(("speech", 1.5), ("silence", 0.15), ("speech", 1.5))
    regions, _ = vad.detect_speech(audio, cfg)
    assert len(regions) == 1


def test_vad_on_pure_silence(cfg: PreprocessConfig) -> None:
    regions, _ = vad.detect_speech(_silence(5.0), cfg)
    assert regions == []


def test_rms_envelope_tracks_level() -> None:
    audio, _ = _layout(("silence", 1.0), ("speech", 1.0))
    levels, hop = vad.rms_envelope(audio, SR)
    assert hop == pytest.approx(0.01)
    assert levels.size == pytest.approx(200, abs=2)
    assert levels[:80].max() < levels[120:].min()  # silence quieter than speech


def test_auto_backend_falls_back_when_no_model_is_installed(tmp_path: Path) -> None:
    """Silero is optional: 'auto' must still produce regions on a bare machine."""
    cfg = PreprocessConfig(work_dir=tmp_path, vad_backend="auto", silero_model_path=None)
    audio, spans = _layout(("silence", 1.0), ("speech", 2.0), ("silence", 1.0))
    regions, backend = vad.detect_speech(audio, cfg)

    assert backend in {"silero-onnx", "silero-torch", "energy"}
    assert len(regions) == 1


def test_missing_silero_model_is_reported_not_downloaded(tmp_path: Path) -> None:
    cfg = PreprocessConfig(work_dir=tmp_path, silero_model_path=tmp_path / "absent.onnx")
    with pytest.raises(vad.VadUnavailable):
        vad._silero_model_path(cfg, ".onnx")


def test_block_parallel_vad_stitches_frames_in_order(cfg: PreprocessConfig) -> None:
    """Blocks run in parallel threads; the warm-up prefix must not leak into output."""
    audio = np.zeros(int(700 * SR), dtype=np.float32)  # 700 s -> 3 blocks
    seen: list[tuple[int, int]] = []

    def fake_block(bounds: tuple[int, int]) -> np.ndarray:
        seen.append(bounds)
        start, stop = bounds
        n = (stop - start) // vad.SILERO_WINDOW
        first = start // vad.SILERO_WINDOW
        return np.arange(first, first + n, dtype=np.float32)

    probs = vad._run_blocks(audio, cfg, fake_block, workers=3)

    expected = len(audio) // vad.SILERO_WINDOW
    assert len(seen) == 3
    assert probs.size == expected
    assert np.array_equal(probs, np.arange(expected, dtype=np.float32))  # order + no overlap


def test_hysteresis_keeps_runs_that_peak_above_threshold() -> None:
    probs = np.array([0.0, 0.4, 0.9, 0.4, 0.0, 0.4, 0.42, 0.0], dtype=np.float32)
    regions = vad._regions_from_probs(probs, hop_s=1.0, threshold=0.5, frame_s=1.0)

    # The 0.4 shoulders stay attached to the 0.9 peak; the 0.4/0.42 run alone is dropped.
    assert len(regions) == 1
    assert (regions[0].start, regions[0].end) == (1.0, 4.0)


def test_regions_are_disjoint_and_sorted(cfg: PreprocessConfig) -> None:
    audio, _ = _layout(*[("speech", 0.6), ("silence", 0.5)] * 6)
    regions, _ = vad.detect_speech(audio, cfg)
    assert all(a.end <= b.start for a, b in zip(regions, regions[1:], strict=False))


# ----------------------------------------------------------------------------------
# chunking
# ----------------------------------------------------------------------------------


def _regions(*spans: tuple[float, float]) -> list[SpeechRegion]:
    return [SpeechRegion(start=s, end=e) for s, e in spans]


def test_chunks_respect_min_max(cfg: PreprocessConfig) -> None:
    regions = _regions(*[(i * 4.0, i * 4.0 + 3.0) for i in range(30)])
    chunks = pack_chunks(regions, cfg)

    assert chunks
    assert all(c.duration <= cfg.chunk_max_s + 1e-6 for c in chunks)
    assert all(c.duration >= cfg.chunk_min_s for c in chunks[:-1])
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunks_cover_all_speech_and_stay_ordered(cfg: PreprocessConfig) -> None:
    regions = _regions((0.0, 5.0), (7.0, 12.0), (30.0, 42.0))
    chunks = pack_chunks(regions, cfg)

    assert sum(c.speech_s for c in chunks) == pytest.approx(sum(r.duration for r in regions))
    assert all(a.end <= b.start for a, b in zip(chunks, chunks[1:], strict=False))
    assert chunks[0].start == 0.0 and chunks[-1].end == 42.0


def test_long_silence_splits_chunks(cfg: PreprocessConfig) -> None:
    """A 10 min gap must never end up inside a chunk."""
    chunks = pack_chunks(_regions((0.0, 4.0), (600.0, 604.0)), cfg)
    assert len(chunks) == 2
    assert chunks[0].end == 4.0 and chunks[1].start == 600.0


def test_monologue_is_split_at_the_quiet_point(cfg: PreprocessConfig) -> None:
    """A 70 s uninterrupted speech run is cut into legal pieces."""
    chunks = pack_chunks(_regions((0.0, 70.0)), cfg)
    assert len(chunks) >= 3
    assert all(c.duration <= cfg.chunk_max_s + 1e-6 for c in chunks)
    assert chunks[0].start == 0.0 and chunks[-1].end == 70.0
    assert all(a.end == b.start for a, b in zip(chunks, chunks[1:], strict=False))  # no gaps


def test_empty_input_produces_no_chunks(cfg: PreprocessConfig) -> None:
    assert pack_chunks([], cfg) == []


# ----------------------------------------------------------------------------------
# end to end
# ----------------------------------------------------------------------------------


@needs_ffmpeg
def test_preprocess_end_to_end(meeting_wav, cfg: PreprocessConfig) -> None:
    path, spans = meeting_wav
    result = preprocess(path, cfg, job_id="test-job")

    assert result.job_id == "test-job"
    assert result.sample_rate == SR
    assert result.audio_duration_s == pytest.approx(28.0, abs=0.2)
    # 18 s of speech in a 28 s file: the silence must actually be dropped.
    assert result.speech_duration_s == pytest.approx(18.0, abs=1.5)
    assert result.asr_input_s < result.audio_duration_s * 0.85
    assert result.chunks and result.wav_path is not None and result.wav_path.is_file()
    assert set(result.timings_s) >= {"probe", "decode", "vad", "chunk"}

    audio, rate = audio_io.read_wav(result.wav_path)
    assert rate == SR
    for chunk in result.chunks:
        assert chunk.samples(audio, rate).size == pytest.approx(chunk.duration * rate, abs=2)


@needs_ffmpeg
def test_preprocess_keeps_original_timeline(meeting_wav, cfg: PreprocessConfig) -> None:
    """Chunk boundaries must match the original recording, not the silence-free one."""
    path, spans = meeting_wav
    result = preprocess(path, cfg)

    assert result.chunks[0].start == pytest.approx(spans[0][0], abs=0.4)
    assert result.chunks[-1].end == pytest.approx(spans[-1][1], abs=0.4)


@needs_ffmpeg
def test_preprocess_normalizes_level(tmp_path: Path, cfg: PreprocessConfig) -> None:
    quiet, _ = _layout(("silence", 0.5), ("speech", 3.0), ("silence", 0.5))
    path = audio_io.write_wav(tmp_path / "quiet.wav", quiet * 0.05, SR)

    result = preprocess(path, cfg)
    audio, _ = audio_io.read_wav(result.wav_path)
    speech = audio[int(0.8 * SR) : int(3.2 * SR)]
    rms_dbfs = 20 * math.log10(float(np.sqrt(np.mean(speech**2))) + 1e-12)

    assert result.gain_db > 6.0
    assert rms_dbfs == pytest.approx(cfg.target_rms_dbfs, abs=3.0)
    assert np.max(np.abs(audio)) <= 1.0


@needs_ffmpeg
def test_preprocess_cache_hits_second_run(meeting_wav, tmp_path: Path) -> None:
    path, _ = meeting_wav
    cached_cfg = PreprocessConfig(work_dir=tmp_path, vad_backend="energy", cache=True)

    first = preprocess(path, cached_cfg, job_id="a")
    second = preprocess(path, cached_cfg, job_id="b")

    assert second.job_id == "b"  # job id is per call, the rest comes from cache
    assert [(c.start, c.end) for c in second.chunks] == [(c.start, c.end) for c in first.chunks]
    assert second.timings_s == first.timings_s


@needs_ffmpeg
def test_preprocess_writes_chunk_wavs(meeting_wav, tmp_path: Path) -> None:
    path, _ = meeting_wav
    cfg = PreprocessConfig(
        work_dir=tmp_path, vad_backend="energy", cache=False, write_chunk_wavs=True
    )
    result = preprocess(path, cfg)

    assert all(c.path is not None and c.path.is_file() for c in result.chunks)
    for chunk in result.chunks:
        audio, rate = audio_io.read_wav(chunk.path)
        assert len(audio) / rate == pytest.approx(chunk.duration, abs=0.05)


@needs_ffmpeg
def test_cleanup_removes_every_artefact(meeting_wav, tmp_path: Path) -> None:
    """Retention policy: nothing derived from the recording may survive the job."""
    path, _ = meeting_wav
    cfg = PreprocessConfig(work_dir=tmp_path / "work", vad_backend="energy", write_chunk_wavs=True)
    result = preprocess(path, cfg)

    assert cleanup(result) >= 1 + len(result.chunks)
    assert not result.wav_path.exists()
    assert all(not c.path.exists() for c in result.chunks)
    assert list((tmp_path / "work").glob("*.preprocess.json")) == []


def test_missing_file_raises(cfg: PreprocessConfig) -> None:
    with pytest.raises(FileNotFoundError):
        preprocess(Path("/nonexistent/meeting.wav"), cfg)


@needs_ffmpeg
def test_silent_recording_is_reported_not_crashed(tmp_path: Path, cfg: PreprocessConfig) -> None:
    path = audio_io.write_wav(tmp_path / "empty.wav", _silence(4.0), SR)
    result = preprocess(path, cfg)

    assert result.chunks == []
    assert any("no speech" in w for w in result.warnings)


def test_filter_chain_order_puts_resample_first() -> None:
    """Resampling first means every later filter runs on 16 kHz mono — the main win."""
    chain = PreprocessConfig(denoise="light").filter_chain().split(",")
    assert chain[0].startswith("aresample=16000")
    assert any(part.startswith("afftdn") for part in chain)
    assert "afftdn" not in PreprocessConfig(denoise="off").filter_chain()
