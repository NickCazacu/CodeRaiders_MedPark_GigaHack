"""Voice activity detection for the preprocess stage.

Three interchangeable backends, picked in this order by ``vad_backend="auto"``:

1. ``silero-onnx``  — Silero VAD v4/v5 through onnxruntime (CPU, ~200x realtime,
   no torch import, model file ships inside the ``silero-vad`` wheel);
2. ``silero-torch`` — the same model as a local TorchScript file, when torch is around;
3. ``energy``       — pure numpy adaptive-energy VAD, always available (no model, no
   deps), used on machines without onnxruntime and in tests.

The neural backends are stateful (LSTM), so a single stream cannot be batched over
time. Long recordings are instead split into blocks processed in parallel threads —
onnxruntime releases the GIL, and each block gets a 1 s warm-up prefix so the LSTM
state is settled before its first kept frame.

No backend touches the network: model paths are local and nothing is fetched on miss.
"""

from __future__ import annotations

import logging
import os
from importlib.util import find_spec
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from app.config import PreprocessConfig
from app.models import SpeechRegion

log = logging.getLogger(__name__)

SILERO_WINDOW = 512
"""Silero v5 requires exactly 512 samples per step at 16 kHz (32 ms)."""

_VAD_BLOCK_S = 300.0
"""Audio per VAD thread. Long enough that the warm-up prefix is negligible."""
_WARMUP_S = 1.0

_ENERGY_FRAME_MS = 30
_ENERGY_HOP_MS = 10
_ENERGY_BLOCK_S = 30.0
"""Noise floor is re-estimated per block, so slow level drift does not break the VAD."""


class VadUnavailable(RuntimeError):
    """A neural backend could not be loaded from local files."""


# ----------------------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------------------


def detect_speech(
    audio: np.ndarray,
    cfg: PreprocessConfig,
    envelope: tuple[np.ndarray, float] | None = None,
) -> tuple[list[SpeechRegion], str]:
    """Return padded, merged speech regions plus the backend name that produced them."""
    order: list[str]
    if cfg.vad_backend == "auto":
        order = ["silero-onnx", "silero-torch", "energy"]
    else:
        order = [cfg.vad_backend]

    for backend in order:
        try:
            if backend == "silero-onnx":
                probs, hop = _silero_onnx_probs(audio, cfg)
            elif backend == "silero-torch":
                probs, hop = _silero_torch_probs(audio, cfg)
            elif backend == "energy":
                probs, hop = _energy_probs(audio, cfg, envelope)
            else:  # pragma: no cover - guarded by the Literal type
                raise ValueError(f"unknown vad backend {backend!r}")
        except VadUnavailable as exc:
            log.info("vad backend %s unavailable: %s", backend, exc)
            continue

        raw = _regions_from_probs(probs, hop, cfg.vad_threshold, frame_s=hop)
        duration = len(audio) / cfg.sample_rate
        return postprocess_regions(raw, cfg, duration), backend

    raise VadUnavailable("no VAD backend available")


def postprocess_regions(
    regions: Iterable[SpeechRegion], cfg: PreprocessConfig, duration_s: float
) -> list[SpeechRegion]:
    """Drop blips, bridge short gaps, pad, clamp — in that order."""
    kept = [r for r in regions if r.duration >= cfg.min_speech_ms / 1000]
    bridged = _merge_close(kept, cfg.min_silence_ms / 1000)
    pad = cfg.speech_pad_ms / 1000
    padded = [
        SpeechRegion(start=max(0.0, r.start - pad), end=min(duration_s, r.end + pad))
        for r in bridged
    ]
    # Padding can make neighbours overlap; merging again keeps the list disjoint.
    return _merge_close(padded, 0.0)


def speech_seconds(regions: Iterable[SpeechRegion]) -> float:
    return sum(r.duration for r in regions)


def rms_envelope(
    audio: np.ndarray,
    sample_rate: int,
    frame_ms: int = _ENERGY_FRAME_MS,
    hop_ms: int = _ENERGY_HOP_MS,
) -> tuple[np.ndarray, float]:
    """Frame RMS of the whole recording, in O(n) time and O(n/hop) memory.

    Computed from non-overlapping hop-sized energy blocks summed with a sliding window
    (a strided view would materialise ``frame/hop`` copies of the audio).
    Returns ``(rms_per_frame, hop_seconds)``.
    """
    hop = max(1, sample_rate * hop_ms // 1000)
    per_frame = max(1, frame_ms // hop_ms)
    n_blocks = len(audio) // hop
    if n_blocks == 0:
        return np.zeros(0, dtype=np.float64), hop / sample_rate

    blocks = np.asarray(audio[: n_blocks * hop], dtype=np.float32).reshape(n_blocks, hop)
    block_energy = np.einsum("ij,ij->i", blocks, blocks).astype(np.float64)
    cumulative = np.concatenate(([0.0], np.cumsum(block_energy)))
    stop = np.minimum(np.arange(n_blocks) + per_frame, n_blocks)
    energy = cumulative[stop] - cumulative[:n_blocks]
    rms = np.sqrt(energy / (np.maximum(stop - np.arange(n_blocks), 1) * hop))
    return rms, hop / sample_rate


# ----------------------------------------------------------------------------------
# silero (onnxruntime)
# ----------------------------------------------------------------------------------


def _silero_model_path(cfg: PreprocessConfig, suffix: str) -> Path:
    """Locate a local Silero model. Never downloads — that is dev-time only."""
    candidates: list[Path] = []
    if cfg.silero_model_path is not None:
        candidates.append(cfg.silero_model_path)
    env = os.environ.get("SILERO_VAD_MODEL")
    if env:
        candidates.append(Path(env))
    # Locate package data without importing silero_vad. Its package initializer
    # imports Torch, while the service deliberately uses only the bundled ONNX model.
    spec = find_spec("silero_vad")
    if spec and spec.submodule_search_locations:
        candidates.append(Path(next(iter(spec.submodule_search_locations))) / "data" / f"silero_vad{suffix}")
    candidates.append(Path("models/vad") / f"silero_vad{suffix}")

    for candidate in candidates:
        if candidate.suffix == suffix and candidate.is_file():
            return candidate
    raise VadUnavailable(f"no local silero_vad{suffix} (looked in {len(candidates)} locations)")


def _silero_onnx_probs(audio: np.ndarray, cfg: PreprocessConfig) -> tuple[np.ndarray, float]:
    try:
        import onnxruntime as ort  # noqa: PLC0415
    except ImportError as exc:
        raise VadUnavailable("onnxruntime not installed") from exc

    model = _silero_model_path(cfg, ".onnx")
    workers = cfg.vad_workers or min(4, os.cpu_count() or 1)

    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1  # parallelism comes from our own block threads
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(
        str(model), sess_options=options, providers=["CPUExecutionProvider"]
    )
    input_names = {i.name for i in session.get_inputs()}
    v5 = "state" in input_names

    def run_block(bounds: tuple[int, int]) -> np.ndarray:
        start, stop = bounds
        frames = _frame_view(audio[start:stop], SILERO_WINDOW)
        if len(frames) == 0:
            return np.zeros(0, dtype=np.float32)

        # v5 carries one (2, batch, 128) state tensor; v4 carries separate LSTM h/c,
        # each (2, batch, 64). Both start at zero and warm up within ~100 ms.
        sr = np.array(cfg.sample_rate, dtype=np.int64)
        state = np.zeros((2, 1, 128), dtype=np.float32)
        hidden = np.zeros((2, 1, 64), dtype=np.float32)
        cell = np.zeros((2, 1, 64), dtype=np.float32)
        # Silero v5/v6 ONNX models require the last 64 samples of the previous
        # window before every 512-sample frame. Omitting this context does not
        # raise an ONNX shape error, but it drives speech probabilities far too low.
        context = np.zeros((1, 64), dtype=np.float32)

        out = np.empty(len(frames), dtype=np.float32)
        for i, frame in enumerate(frames):
            window = frame.reshape(1, -1).astype(np.float32, copy=False)
            if v5:
                model_input = np.concatenate((context, window), axis=1)
                prob, state = session.run(
                    None, {"input": model_input, "state": state, "sr": sr}
                )
                context = model_input[:, -64:]
            else:
                prob, hidden, cell = session.run(
                    None, {"input": window, "h": hidden, "c": cell, "sr": sr}
                )
            out[i] = prob.item()
        return out

    return _run_blocks(audio, cfg, run_block, workers), SILERO_WINDOW / cfg.sample_rate


# ----------------------------------------------------------------------------------
# silero (TorchScript)
# ----------------------------------------------------------------------------------


def _silero_torch_probs(audio: np.ndarray, cfg: PreprocessConfig) -> tuple[np.ndarray, float]:
    try:
        import torch  # noqa: PLC0415
    except ImportError as exc:
        raise VadUnavailable("torch not installed") from exc

    model_path = _silero_model_path(cfg, ".jit")
    model = torch.jit.load(str(model_path), map_location="cpu")
    model.eval()
    torch.set_num_threads(cfg.vad_workers or min(4, os.cpu_count() or 1))

    def run_block(bounds: tuple[int, int]) -> np.ndarray:
        start, stop = bounds
        frames = _frame_view(audio[start:stop], SILERO_WINDOW)
        if len(frames) == 0:
            return np.zeros(0, dtype=np.float32)
        model.reset_states()
        out = np.empty(len(frames), dtype=np.float32)
        with torch.no_grad():
            for i, frame in enumerate(frames):
                tensor = torch.from_numpy(np.ascontiguousarray(frame)).unsqueeze(0)
                out[i] = float(model(tensor, cfg.sample_rate).item())
        return out

    # TorchScript keeps its own internal state; run it single-threaded.
    return _run_blocks(audio, cfg, run_block, workers=1), SILERO_WINDOW / cfg.sample_rate


# ----------------------------------------------------------------------------------
# energy fallback
# ----------------------------------------------------------------------------------


def _energy_probs(
    audio: np.ndarray,
    cfg: PreprocessConfig,
    envelope: tuple[np.ndarray, float] | None = None,
) -> tuple[np.ndarray, float]:
    """Adaptive noise-floor energy VAD, fully vectorised.

    The threshold is re-estimated per 30 s block, so slow level drift (a speaker moving
    away from the microphone) does not break detection:

    * ``floor + 9 dB`` — normal case, the block contains both speech and pauses;
    * capped at ``peak - 10 dB`` — a block that is *all* speech has no pauses to
      measure, so its 10th percentile is already speech and the first rule would reject
      everything;
    * never below ``file floor + 9 dB`` — without this, the cap would turn an entirely
      silent block into speech, since there its "peak" is just noise.

    The level is squashed into a pseudo-probability so the thresholding/hysteresis code
    is shared with Silero: 0.5 sits exactly on the threshold.
    """
    enter_db, head_room_db = 9.0, 10.0
    levels, hop_s = envelope if envelope is not None else rms_envelope(audio, cfg.sample_rate)
    if levels.size == 0:
        return np.zeros(0, dtype=np.float32), hop_s

    level_db = 20.0 * np.log10(np.maximum(levels, 1e-10))
    frames_per_block = max(1, int(_ENERGY_BLOCK_S / hop_s))
    n_blocks = max(1, int(np.ceil(level_db.size / frames_per_block)))
    padded = np.pad(
        level_db, (0, n_blocks * frames_per_block - level_db.size), constant_values=np.nan
    ).reshape(n_blocks, frames_per_block)

    block_floor, block_peak = np.nanpercentile(padded, [10, 95], axis=1)
    threshold = np.minimum(block_floor + enter_db, block_peak - head_room_db)
    threshold = np.maximum(threshold, float(np.percentile(level_db, 5)) + enter_db)
    threshold = np.repeat(threshold, frames_per_block)[: level_db.size]

    probs = 1.0 / (1.0 + np.exp(-(level_db - threshold) / 3.0))
    return probs.astype(np.float32), hop_s


# ----------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------


def _frame_view(audio: np.ndarray, window: int) -> np.ndarray:
    """Non-overlapping frames as a view (no copy). Trailing partial frame is dropped."""
    usable = (len(audio) // window) * window
    return np.asarray(audio[:usable], dtype=np.float32).reshape(-1, window)


def _run_blocks(
    audio: np.ndarray,
    cfg: PreprocessConfig,
    run_block: Callable[[tuple[int, int]], np.ndarray],
    workers: int,
) -> np.ndarray:
    """Split into block-aligned pieces, run them (in parallel if allowed), stitch probs."""
    window = SILERO_WINDOW
    block = int(_VAD_BLOCK_S * cfg.sample_rate) // window * window
    warmup = int(_WARMUP_S * cfg.sample_rate) // window * window
    total = (len(audio) // window) * window
    if total == 0:
        return np.zeros(0, dtype=np.float32)

    starts = list(range(0, total, block)) if total > block else [0]
    jobs = [(max(0, s - warmup), min(total, s + block)) for s in starts]
    drop = [(s - j[0]) // window for s, j in zip(starts, jobs, strict=True)]

    if workers > 1 and len(jobs) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outputs = list(pool.map(run_block, jobs))
    else:
        outputs = [run_block(job) for job in jobs]
    return np.concatenate([out[d:] for out, d in zip(outputs, drop, strict=True)])


def _regions_from_probs(
    probs: np.ndarray, hop_s: float, threshold: float, frame_s: float
) -> list[SpeechRegion]:
    """Hysteresis thresholding: a run stays open down to ``threshold - 0.15``.

    Implemented with connected components instead of a per-frame loop: take every run
    above the low threshold, keep the ones that contain at least one high frame.
    """
    if probs.size == 0:
        return []
    high = probs >= threshold
    low = probs >= max(0.01, threshold - 0.15)
    if not high.any():
        return []

    flags = low.astype(np.int8)
    edges = np.diff(flags)
    starts = np.flatnonzero(edges == 1) + 1
    ends = np.flatnonzero(edges == -1) + 1
    if flags[0]:
        starts = np.concatenate(([0], starts))
    if flags[-1]:
        ends = np.concatenate((ends, [flags.size]))

    cumulative_high = np.concatenate(([0], np.cumsum(high)))
    keep = (cumulative_high[ends] - cumulative_high[starts]) > 0
    return [
        SpeechRegion(start=float(s * hop_s), end=float((e - 1) * hop_s + frame_s))
        for s, e in zip(starts[keep], ends[keep], strict=True)
    ]


def _merge_close(regions: Iterable[SpeechRegion], max_gap_s: float) -> list[SpeechRegion]:
    merged: list[SpeechRegion] = []
    for region in sorted(regions, key=lambda r: r.start):
        if merged and region.start - merged[-1].end <= max_gap_s:
            previous = merged.pop()
            merged.append(
                SpeechRegion(start=previous.start, end=max(previous.end, region.end))
            )
        else:
            merged.append(region)
    return merged
