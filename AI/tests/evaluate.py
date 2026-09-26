"""Evaluare ASR față de o transcriere de referință (WER/CER), pe ferestre de timp.

    python -m tests.evaluate tests/reference/medpark.txt JOB1 JOB2 ... [--show]

Pentru fiecare fragment din referință ia cuvintele transcrise (din transcript.json, segmentele
păstrate) al căror start cade în fereastra fragmentului. Folosind timpii per cuvânt, joburi cu
segmentări diferite (cu/fără diarizare) se compară corect.
Normalizare: litere mici, fără diacritice (ş/ș/s, ă/a...), fără punctuație.
"""
import argparse
import json
import re
import unicodedata

import jiwer

from pipeline.common import jobs_dir, load_config

FRAG = re.compile(r"^#\s*fragment\s+(\d+):(\d+)\s*-\s*(\d+):(\d+)")
LINE = re.compile(r"^\[[^\]]+\]\s*\d+:\d+\s*-\s*\d+:\d+\s*:\s*(.*)$")


def norm(text):
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")  # fără diacritice
    t = re.sub(r"[^\w\s]", " ", t)  # punctuația (inclusiv în 0.22, 38-40) -> spațiu, identic în ref și hyp
    return " ".join(t.split())


def load_reference(path):
    frags = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if m := FRAG.match(line):
            a = int(m[1]) * 60 + int(m[2])
            b = int(m[3]) * 60 + int(m[4])
            frags.append({"start": a, "end": b, "text": []})
        elif (m := LINE.match(line)) and frags:
            frags[-1]["text"].append(re.sub(r"\[[^\]]*\]", " ", m[1]))  # fără [adnotări]
    for f in frags:
        f["text"] = " ".join(f["text"])
    return frags


def hypothesis(job, a, b):
    t = json.loads((jobs_dir(load_config()) / job / "transcript.json").read_text(encoding="utf-8"))
    words = [w for s in t if not s["dropped"] for w in s["words"] if a <= w["start"] < b]
    words.sort(key=lambda w: w["start"])
    return "".join(w["w"] for w in words)


def cyrillic_share(text):
    letters = [c for c in text if c.isalpha()]
    return sum("CYRILLIC" in unicodedata.name(c, "") for c in letters) / max(len(letters), 1)


def setup_of(job):
    try:
        with open(jobs_dir(load_config()) / job / "asr.jsonl", encoding="utf-8") as f:
            return json.loads(f.readline()).get("setup", "?")
    except (OSError, ValueError):
        return "?"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reference")
    ap.add_argument("jobs", nargs="+")
    ap.add_argument("--show", action="store_true", help="afișează textul transcris per fragment")
    args = ap.parse_args()

    frags = load_reference(args.reference)
    ref_all = norm(" ".join(f["text"] for f in frags))
    print(f"referință: {len(frags)} fragmente, {len(ref_all.split())} cuvinte\n")
    head = " | ".join(f"WER {f['start'] // 60}:{f['start'] % 60:02d}" for f in frags)
    print(f"{'job':30s} | {head} | WER total | CER total | chirilic | setup")

    for job in args.jobs:
        hyps = [hypothesis(job, f["start"], f["end"]) for f in frags]
        wers = [jiwer.wer(norm(f["text"]), norm(h) or "-") for f, h in zip(frags, hyps)]
        hyp_all = norm(" ".join(hyps)) or "-"
        print(f"{job:30s} | " + " | ".join(f"{x:9.1%}" for x in wers)
              + f" | {jiwer.wer(ref_all, hyp_all):9.1%} | {jiwer.cer(ref_all, hyp_all):9.1%}"
              + f" | {cyrillic_share(' '.join(hyps)):8.1%} | {setup_of(job)}")
        if args.show:
            for f, h in zip(frags, hyps):
                print(f"    [{f['start'] // 60}:{f['start'] % 60:02d}] {h.strip()}")


if __name__ == "__main__":
    main()
