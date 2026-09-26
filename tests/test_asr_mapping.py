"""Regresie: rezultatele BatchedInferencePipeline trebuie atribuite clipului corect,
chiar dacă start-ul e rotunjit puțin înaintea offset-ului clipului.
    python -m tests.test_asr_mapping
"""
from types import SimpleNamespace

import numpy as np

from pipeline.asr import SR, transcribe_batched


class FakePipe:
    """Imită faster-whisper: un segment per clip, cu start rotunjit la 20 ms în jos."""

    def transcribe(self, audio, clip_timestamps, **kw):
        out = [SimpleNamespace(start=np.floor(c["start"] / 0.02) * 0.02 - 0.01, end=c["end"], text=f"clip{i}")
               for i, c in enumerate(clip_timestamps)]
        return iter(out), None


def test_rounded_start_goes_to_right_clip():
    durs = [15.46, 3.28, 3.81, 12.97]  # offset-uri care nu sunt multipli de 20 ms
    clips = [np.zeros(int(d * SR), dtype="float32") for d in durs]
    res = transcribe_batched(FakePipe(), clips, "ro", None, None, {"batch_size": 8, "beam_size": 1})
    assert [[s.text for s in b] for b, _ in res] == [["clip0"], ["clip1"], ["clip2"], ["clip3"]], res


if __name__ == "__main__":
    test_rounded_start_goes_to_right_clip()
    print("OK  test_rounded_start_goes_to_right_clip")
