"""Segmentare: diarizare pyannote (speaker-diarization-3.1, local, offline) +
Silero-VAD în interiorul turelor de vorbitor, apoi reguli:
unire pauze scurte, alipire segmente scurte, tăiere segmente lungi, padding.

    python -m pipeline.segment JOB_ID [--no-diarization] [--export-segments]

Scrie jobs/{id}/segments.json: [{"id", "speaker", "start", "end"}, ...].
Dacă segments.json există, etapa e sărită (șterge-l ca să refaci segmentarea).
"""
import argparse
import gc
import json
import time

import torch  # înainte de pyannote/silero
import numpy as np
import soundfile as sf
from tqdm import tqdm

from pipeline.common import ROOT, jobs_dir, load_config, load_status, run_stage


# ---------- diarizare ----------

def diarize(wav, cfg):
    """Returnează ture exclusive (fără suprapuneri): [[start, end, speaker], ...]."""
    import yaml
    from pyannote.audio import Pipeline
    from pyannote.audio.pipelines.utils.hook import ProgressHook

    d = cfg["diarization"]
    pdir = ROOT / d["pipeline_dir"]
    if not (pdir / "config.yaml").exists():
        raise RuntimeError(f"Lipsește modelul de diarizare: {pdir} (vezi SETUP.md §5b)")

    # config-ul 3.1 referă sub-modelele prin ID HF; le înlocuim cu copiile locale.
    # pyannote 4.x încarcă obligatoriu un PLDA (implicit community-1 de pe HF), chiar
    # dacă clustering-ul 3.1 (Agglomerative) nu îl folosește, deci îl dăm tot local.
    conf = yaml.safe_load((pdir / "config.yaml").read_text(encoding="utf-8"))
    params = conf["pipeline"]["params"]
    params["segmentation"] = str(ROOT / d["segmentation_dir"])
    params["embedding"] = str(ROOT / d["embedding_dir"])
    params.setdefault("plda", {"checkpoint": str(ROOT / d["plda_dir"]), "subfolder": "plda"})
    pipeline = Pipeline.from_pretrained(conf)

    device = d["device"]
    if device == "cuda" and not torch.cuda.is_available():
        print("[diarize] CUDA indisponibil, rulez pe CPU")
        device = "cpu"
    pipeline.to(torch.device(device))

    # pyannote primește semnalul întreg: segmentarea lui (Inference) îl încarcă oricum
    # complet (~460 MB float32 pentru 2 h). Eliberăm memoria imediat după.
    x, sr = sf.read(str(wav), dtype="float32")
    waveform = torch.from_numpy(x)[None]
    with ProgressHook() as hook:
        out = pipeline({"waveform": waveform, "sample_rate": sr}, hook=hook,
                       min_speakers=d["min_speakers"], max_speakers=d["max_speakers"])
    del x, waveform
    gc.collect()

    ann = getattr(out, "exclusive_speaker_diarization", out)
    turns = [[round(t.start, 3), round(t.end, 3), spk] for t, _, spk in ann.itertracks(yield_label=True)]

    del pipeline, out
    if device == "cuda":
        torch.cuda.empty_cache()
    return turns


# ---------- VAD ----------

def vad_in_turns(wav, turns, cfg):
    """Silero-VAD pe fiecare tură, citind de pe disc doar porțiunea turei."""
    from silero_vad import get_speech_timestamps, load_silero_vad

    v = cfg["vad"]
    model = load_silero_vad()
    regions = []
    with sf.SoundFile(str(wav)) as f:
        sr = f.samplerate
        for start, end, spk in tqdm(turns, desc="vad", unit="tură"):
            f.seek(int(start * sr))
            x = f.read(int((end - start) * sr), dtype="float32")
            if len(x) < 512:  # sub o fereastră silero (32 ms)
                continue
            for t in get_speech_timestamps(
                torch.from_numpy(x), model, sampling_rate=sr,
                threshold=v["threshold"],
                min_speech_duration_ms=v["min_speech_duration_ms"],
                min_silence_duration_ms=v["min_silence_duration_ms"],
                speech_pad_ms=0,  # padding-ul îl aplicăm la final
            ):
                regions.append([start + t["start"] / sr, start + t["end"] / sr, spk])
    return regions


# ---------- reguli ----------
# Un segment ține și `parts`: intervalele de vorbire VAD din el. Pauzele dintre
# parts sunt candidații pentru tăiere.

def dur(s):
    return s["end"] - s["start"]


def join(a, b):
    a["start"], a["end"] = min(a["start"], b["start"]), max(a["end"], b["end"])
    a["parts"] = sorted(a["parts"] + b["parts"])


def merge_gaps(segs, max_gap):
    out = []
    for s in segs:
        if out and out[-1]["speaker"] == s["speaker"] and s["start"] - out[-1]["end"] < max_gap:
            join(out[-1], s)
        else:
            out.append(s)
    return out


def attach_short(segs, min_len, max_gap, max_len):
    i = 0
    while i < len(segs):
        s = segs[i]
        if dur(s) >= min_len:
            i += 1
            continue
        best = None
        for j in (i - 1, i + 1):
            if not 0 <= j < len(segs):
                continue
            n = segs[j]
            gap = max(n["start"], s["start"]) - min(n["end"], s["end"])
            span = max(n["end"], s["end"]) - min(n["start"], s["start"])
            if gap <= max_gap and span <= max_len:
                key = (n["speaker"] != s["speaker"], gap)  # preferă același vorbitor
                if best is None or key < best[0]:
                    best = (key, j)
        if best is None:  # izolat: rămâne așa
            i += 1
            continue
        j = best[1]
        n = segs[j]
        if dur(s) > dur(n):
            n["speaker"] = s["speaker"]
        join(n, s)
        del segs[i]
        i = min(i, j)  # vecinul unit poate fi încă scurt
    return segs


def lowest_energy_point(f, lo, hi):
    """Momentul cu energia minimă (cadre de 20 ms) din [lo, hi]."""
    sr = f.samplerate
    f.seek(int(lo * sr))
    x = f.read(int((hi - lo) * sr), dtype="float32")
    n = int(0.02 * sr)
    frames = x[: len(x) // n * n].reshape(-1, n)
    return lo + (int(np.argmin((frames ** 2).mean(1))) + 0.5) * n / sr


def split_long(s, split_above, max_len, min_len, f):
    if dur(s) <= split_above:
        return [s]

    parts = s["parts"]
    mid = (s["start"] + s["end"]) / 2
    best = None
    for k in range(1, len(parts)):
        g0, g1 = parts[k - 1][1], parts[k][0]
        if g0 - s["start"] < min_len or s["end"] - g1 < min_len:
            continue
        # cea mai lungă pauză; la egalitate (rezoluție 10 ms), cea mai apropiată de mijloc
        key = (round(g1 - g0, 2), -abs((g0 + g1) / 2 - mid))
        if best is None or key > best[0]:
            best = (key, k)

    if best is not None:
        k = best[1]
        a, b = parts[:k], parts[k:]
    elif dur(s) <= max_len:
        return [s]  # vorbire continuă, dar sub limita fermă
    else:
        # nicio pauză detectată: tăiem la punctul cel mai liniștit din mijloc
        t = lowest_energy_point(f, s["start"] + dur(s) / 4, s["end"] - dur(s) / 4)
        a = [[p0, min(p1, t)] for p0, p1 in parts if p0 < t]
        b = [[max(p0, t), p1] for p0, p1 in parts if p1 > t]

    pieces = []
    for ps in (a, b):
        seg = {"speaker": s["speaker"], "start": ps[0][0], "end": max(p[1] for p in ps), "parts": ps}
        pieces += split_long(seg, split_above, max_len, min_len, f)
    return pieces


def pad(segs, p, duration):
    out = []
    for i, s in enumerate(segs):
        lo = (segs[i - 1]["end"] + s["start"]) / 2 if i > 0 else 0.0
        hi = (s["end"] + segs[i + 1]["start"]) / 2 if i + 1 < len(segs) else duration
        out.append({
            "id": i,
            "speaker": s["speaker"],
            "start": round(max(s["start"] - p, lo), 3),
            "end": round(min(s["end"] + p, hi), 3),
        })
    return out


def build_segments(regions, wav, duration, c):
    max_len = c["max_segment_s"] - 2 * c["pad_s"]  # ca după padding să rămână <= max
    segs = [{"speaker": spk, "start": s, "end": e, "parts": [[s, e]]}
            for s, e, spk in sorted(regions)]
    segs = merge_gaps(segs, c["merge_gap_s"])
    segs = attach_short(segs, c["min_segment_s"], c["attach_max_gap_s"], max_len)
    with sf.SoundFile(str(wav)) as f:
        segs = [p for s in segs
                for p in split_long(s, c["split_above_s"], max_len, c["min_segment_s"], f)]
    return pad(segs, c["pad_s"], duration)


# ---------- export (debugging) ----------

def export_segments(wav, segs, out_dir):
    out_dir.mkdir(exist_ok=True)
    with sf.SoundFile(str(wav)) as f:
        sr = f.samplerate
        for s in tqdm(segs, desc="export", unit="seg"):
            p = out_dir / f"{s['id']:05d}_{s['speaker']}_{s['start']:.2f}.wav"
            if p.exists():
                continue
            f.seek(int(s["start"] * sr))
            sf.write(str(p), f.read(int((s["end"] - s["start"]) * sr), dtype="int16"), sr, subtype="PCM_16")
    print(f"[export] {len(segs)} segmente în {out_dir}")


# ---------- orchestrare ----------

def write_json(path, data):
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def segment(job_id, use_diarization=None, export=False, cfg=None):
    cfg = cfg or load_config()
    job_dir = jobs_dir(cfg) / job_id
    status = load_status(job_dir)
    if "audio" not in status:
        raise SystemExit(f"Jobul {job_id} nu a trecut prin normalize")
    if use_diarization is None:
        use_diarization = cfg["diarization"]["enabled"]

    wav = job_dir / status["audio"]
    out = job_dir / "segments.json"
    diar_path = job_dir / "diarization.json"

    if use_diarization and not out.exists():
        run_stage(job_dir, "diarize", diar_path, lambda: diarize_stage(wav, diar_path, cfg))

    def work():
        info = sf.info(str(wav))
        if use_diarization:
            turns = json.loads(diar_path.read_text(encoding="utf-8"))
        else:
            w = cfg["vad"]["window_s"]
            turns = [[t, min(t + w, info.duration), "UNK"] for t in np.arange(0, info.duration, w)]

        t0 = time.perf_counter()
        regions = vad_in_turns(wav, turns, cfg)
        vad_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        segs = build_segments(regions, wav, info.duration, cfg["segment"])
        write_json(out, segs)
        return {
            "diarization": use_diarization,
            "n_turns": len(turns),
            "n_vad_regions": len(regions),
            "n_segments": len(segs),
            "n_speakers": len({s["speaker"] for s in segs}),
            "vad_s": round(vad_s, 2),
            "rules_s": round(time.perf_counter() - t0, 2),
        }

    run_stage(job_dir, "segment", out, work)

    if export:
        segs = json.loads(out.read_text(encoding="utf-8"))
        export_segments(wav, segs, job_dir / "segments")
    return out


def diarize_stage(wav, diar_path, cfg):
    turns = diarize(wav, cfg)
    write_json(diar_path, turns)
    return {"n_turns": len(turns), "n_speakers": len({t[2] for t in turns})}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id")
    ap.add_argument("--diarization", action=argparse.BooleanOptionalAction, default=None,
                    help="--no-diarization: doar Silero-VAD, speaker 'UNK' (implicit: din config)")
    ap.add_argument("--export-segments", action="store_true",
                    help="scrie și câte un WAV per segment în jobs/{id}/segments/ (debugging)")
    ap.add_argument("--config", help="implicit: config.yaml din rădăcina proiectului")
    args = ap.parse_args()

    print(segment(args.job_id, args.diarization, args.export_segments, load_config(args.config)))


if __name__ == "__main__":
    main()
