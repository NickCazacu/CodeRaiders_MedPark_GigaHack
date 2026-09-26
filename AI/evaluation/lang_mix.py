"""Test pe amestec de limbi: o „ședință” sintetică din fragmente reale cu transcriere cunoscută
(română moldovenească din Kaggle, rusă și engleză din FLEURS), apoi scor pe fiecare limbă.

    python -m evaluation.lang_mix make  [--name langmix] [--ro 24 --ru 12 --en 6] [--seed 7]
    python run_pipeline.py ..\\datasets\\eval\\langmix\\mix.wav --job-id langmix_base
    python -m evaluation.lang_mix score langmix_base [--name langmix]

Măsoară exact riscul de la o înregistrare necunoscută: limba aleasă pe fiecare fragment (acuratețe) și
WER pe fiecare limbă. Rusa ar trebui să rămână rusă (bonusul pentru română nu trebuie s-o traducă).
Fragmentele românești vin DOAR din videoclipurile Kaggle păstrate pentru test (KAGGLE_TEST), niciodată
din cele folosite la fine-tuning. Datele (audio + referință) stau în datasets/eval/<name>/ (în afara git).
"""
import argparse
import csv
import json
import random
import subprocess
from collections import defaultdict
from pathlib import Path

import jiwer
import numpy as np
import soundfile as sf

from pipeline.common import ROOT, jobs_dir, load_config
from tests.evaluate import norm

REPO = ROOT.parent
KAGGLE = REPO / "archive"
FLEURS = REPO / "datasets" / "fleurs" / "data"
EVAL = REPO / "datasets" / "eval"
KAGGLE_TEST = ["TOFItbQPJvc", "zGREIEtFMa0"]  # vorbitori nevăzuți la fine-tuning
SR = 16000


def load_audio(path):
    """Orice fișier -> mono 16 kHz float32 (ffmpeg, ca în pipeline)."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32)


def kaggle_clips(rng, n):
    rows = [r for r in csv.DictReader(open(KAGGLE / "filtered_dataset.csv", encoding="utf-8"))
            if r["video_id"] in KAGGLE_TEST and 3 <= float(r["duration"]) <= 15
            and 6 <= len(r["transcript"]) / float(r["duration"]) <= 22]
    return [{"lang": "ro", "text": r["transcript"], "source": f"kaggle:{r['audio_path']}",
             "path": KAGGLE / "moldovadataset_release" / r["audio_path"].replace("\\", "/")}
            for r in rng.sample(rows, min(n, len(rows)))]


def fleurs_clips(rng, lang, code, n):
    rows, seen = [], set()
    for line in open(FLEURS / code / "dev.tsv", encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        if f[0] in seen:  # același text citit de mai mulți vorbitori: o singură dată
            continue
        seen.add(f[0])
        rows.append({"lang": lang, "text": f[2], "source": f"fleurs:{code}/{f[1]}",
                     "path": FLEURS / code / "audio" / "dev" / f[1]})
    return rng.sample(rows, min(n, len(rows)))


def make(args):
    rng = random.Random(args.seed)
    pools = {"ro": kaggle_clips(rng, args.ro), "ru": fleurs_clips(rng, "ru", "ru_ru", args.ru),
             "en": fleurs_clips(rng, "en", "en_us", args.en)}
    # ordine de ședință: blocuri de 1–4 fragmente în aceeași limbă (comutări realiste, nu la fiecare frază)
    order = []
    while any(pools.values()):
        lang = rng.choices([l for l in pools if pools[l]], weights=[len(pools[l]) for l in pools if pools[l]])[0]
        for _ in range(rng.randint(1, 4)):
            if pools[lang]:
                order.append(pools[lang].pop())
    out_dir = EVAL / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    parts, ref, t = [], [], 0.0
    for c in order:
        gap = np.zeros(int(rng.uniform(0.6, 1.4) * SR), dtype=np.float32)
        x = load_audio(c["path"])
        x = x / max(np.abs(x).max(), 1e-6) * 0.5  # niveluri comparabile între surse
        parts += [gap, x]
        t += len(gap) / SR
        ref.append({"start": round(t, 3), "end": round(t + len(x) / SR, 3), "lang": c["lang"],
                    "text": c["text"], "source": c["source"]})
        t += len(x) / SR
    sf.write(str(out_dir / "mix.wav"), np.concatenate(parts + [np.zeros(SR, dtype=np.float32)]), SR)
    (out_dir / "reference.json").write_text(json.dumps(ref, ensure_ascii=False, indent=1), encoding="utf-8")
    n = defaultdict(int)
    for r in ref:
        n[r["lang"]] += 1
    print(f"{out_dir / 'mix.wav'}: {t / 60:.1f} min, {dict(n)}")


def score(args):
    ref = json.loads((EVAL / args.name / "reference.json").read_text(encoding="utf-8"))
    tr = json.loads((jobs_dir(load_config()) / args.job / "transcript.json").read_text(encoding="utf-8"))
    kept = [s for s in tr if not s["dropped"]]
    per = defaultdict(lambda: {"n": 0, "lang_ok": 0, "ref": [], "hyp": [], "confusions": defaultdict(int)})
    for r in ref:
        a, b = r["start"] - 0.3, r["end"] + 0.3
        words = sorted((w for s in kept for w in s["words"] if a <= w["start"] < b), key=lambda w: w["start"])
        overlap = defaultdict(float)
        for s in kept:
            o = min(s["end"], b) - max(s["start"], a)
            if o > 0:
                overlap[s["lang"]] += o
        got = max(overlap, key=overlap.get) if overlap else "—"
        p = per[r["lang"]]
        p["n"] += 1
        p["lang_ok"] += got == r["lang"]
        if got != r["lang"]:
            p["confusions"][got] += 1
        p["ref"].append(norm(r["text"]))
        p["hyp"].append(norm("".join(w["w"] for w in words)) or "-")
    print(f"job {args.job} vs {args.name}")
    print(f"{'limba':6s} {'fragmente':>9s} {'limbă corectă':>14s} {'WER':>7s} {'CER':>7s}  confuzii")
    for lang, p in sorted(per.items()):
        print(f"{lang:6s} {p['n']:9d} {p['lang_ok'] / p['n']:14.0%} {jiwer.wer(p['ref'], p['hyp']):7.1%} "
              f"{jiwer.cer(p['ref'], p['hyp']):7.1%}  {dict(p['confusions']) or '-'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--name", default="langmix")
    m.add_argument("--ro", type=int, default=24)
    m.add_argument("--ru", type=int, default=12)
    m.add_argument("--en", type=int, default=6)
    m.add_argument("--seed", type=int, default=7)
    s = sub.add_parser("score")
    s.add_argument("job")
    s.add_argument("--name", default="langmix")
    args = ap.parse_args()
    make(args) if args.cmd == "make" else score(args)


if __name__ == "__main__":
    main()
