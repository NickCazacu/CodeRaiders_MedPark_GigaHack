"""Teste pentru regulile de segmentare, pe regiuni sintetice.
    python -m tests.test_segment_rules
"""
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from pipeline.segment import build_segments

C = {"merge_gap_s": 0.3, "min_segment_s": 1.0, "attach_max_gap_s": 1.0,
     "split_above_s": 15, "max_segment_s": 25, "pad_s": 0.2}
SR = 16000


def run(regions, duration=120.0, audio=None):
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "a.wav"
        x = audio if audio is not None else np.random.default_rng(0).normal(0, 0.1, int(duration * SR))
        sf.write(str(wav), x.astype("float32"), SR)
        return build_segments(regions, wav, duration, C)


def spans(segs):
    return [(s["speaker"], s["start"], s["end"]) for s in segs]


def test_merge_same_speaker_short_gap():
    out = run([[10, 12, "A"], [12.2, 14, "A"], [14.25, 16, "B"]])
    # A: pauza 0.2 < 0.3 => unit; B: alt vorbitor => separat
    assert [s[0] for s in spans(out)] == ["A", "B"], out
    assert out[0]["start"] == 9.8 and out[0]["end"] == 14.125  # padding limitat la mijlocul pauzei


def test_no_merge_gap_over_threshold():
    out = run([[10, 12, "A"], [12.5, 14, "A"]])
    assert len(out) == 2, out


def test_attach_short_prefers_same_speaker():
    out = run([[10, 13, "A"], [13.5, 14.0, "B"], [14.4, 17, "B"]])
    # segmentul B de 0.5 s se alipește de B (același vorbitor), nu de A
    assert spans(out) == [("A", 9.8, 13.2), ("B", 13.3, 17.2)], out


def test_short_isolated_stays():
    out = run([[10, 13, "A"], [20, 20.5, "A"], [30, 33, "A"]])
    assert len(out) == 3 and out[1]["end"] - out[1]["start"] < 1.0, out


def test_split_long_at_longest_pause():
    # 20 s de vorbire, pauze interne 0.1 s și una de 0.25 s la 12 s (după unire => un segment)
    regions = [[t, t + 1.9, "A"] for t in np.arange(0, 10, 2.0)]
    regions += [[10, 11.75, "A"], [12, 20, "A"]]
    out = run(regions, duration=30)
    assert len(out) == 2, out
    assert abs(out[0]["end"] - 11.875) < 1e-6 and abs(out[1]["start"] - 11.875) < 1e-6, out


def test_split_continuous_speech_over_max():
    # 40 s fără nicio pauză => tăiere la punctul cel mai liniștit; toate <= 25 s
    x = np.random.default_rng(1).normal(0, 0.2, 60 * SR)
    x[int(21.0 * SR):int(21.1 * SR)] *= 0.01  # un moment liniștit la 21 s
    out = run([[5, 45, "A"]], duration=60, audio=x)
    assert len(out) == 2 and all(s["end"] - s["start"] <= 25 for s in out), out
    assert 20.9 < out[0]["end"] < 21.2, out


def test_everything_within_limits():
    rng = np.random.default_rng(2)
    t, regions = 0.0, []
    while t < 3000:
        d = float(rng.choice([0.3, 0.8, 2, 5, 12, 30, 45]))
        regions.append([t, t + d, str(rng.choice(["A", "B", "C"]))])
        t += d + float(rng.choice([0.05, 0.2, 0.4, 1.5]))
    out = run(regions, duration=t + 1)
    d = [s["end"] - s["start"] for s in out]
    assert max(d) <= 25 + 1e-6, max(d)
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(out, out[1:])), "suprapuneri"


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print(f"OK  {name}")
    print(f"{len(tests)} teste trecute")
