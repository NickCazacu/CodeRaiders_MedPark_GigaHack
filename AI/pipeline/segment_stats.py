"""Statistici pentru segments.json: nr. segmente, distribuția duratelor, vorbitori,
verificarea regulilor și timpii etapelor.

    python -m pipeline.segment_stats JOB_ID
"""
import argparse
import json
from collections import defaultdict

import numpy as np

from pipeline.common import jobs_dir, load_config, load_status

BINS = [0, 1, 3, 5, 10, 15, 20, 25, float("inf")]


def fmt_t(s):
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id")
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = load_config(args.config)
    job_dir = jobs_dir(cfg) / args.job_id
    segs = json.loads((job_dir / "segments.json").read_text(encoding="utf-8"))
    status = load_status(job_dir)
    total = status.get("duration_s") or 0
    c = cfg["segment"]

    d = np.array([s["end"] - s["start"] for s in segs])
    print(f"job: {args.job_id}   durată audio: {fmt_t(total)}")
    print(f"segmente: {len(segs)}   vorbire acoperită: {fmt_t(d.sum())} ({100 * d.sum() / max(total, 1e-9):.1f}%)")
    if not len(segs):
        return

    print("\ndurata segmentelor (s):")
    print("  min {:.2f} | p10 {:.2f} | mediană {:.2f} | medie {:.2f} | p90 {:.2f} | max {:.2f}".format(
        d.min(), *np.percentile(d, [10, 50]), d.mean(), np.percentile(d, 90), d.max()))
    counts, _ = np.histogram(d, BINS)
    for lo, hi, n in zip(BINS, BINS[1:], counts):
        label = f"{lo:>2g}-{hi:<3g}s" if hi != float("inf") else f">{lo:g}s   "
        print(f"  {label} {n:6d}  {'█' * int(50 * n / counts.max())}")

    per = defaultdict(lambda: [0, 0.0])
    for s in segs:
        per[s["speaker"]][0] += 1
        per[s["speaker"]][1] += s["end"] - s["start"]
    print(f"\nvorbitori: {len(per)}")
    for spk, (n, t) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        print(f"  {spk:12s} {n:6d} segmente  {fmt_t(t)}  ({100 * t / d.sum():.1f}%)")

    gaps = np.array([b["start"] - a["end"] for a, b in zip(segs, segs[1:])])
    print("\nverificări:")
    print(f"  suprapuneri între segmente: {int((gaps < -1e-6).sum()) if len(gaps) else 0}")
    print(f"  segmente < {c['min_segment_s']}s (izolate, fără vecin apropiat): {int((d < c['min_segment_s']).sum())}")
    print(f"  segmente > {c['split_above_s']}s (fără pauză internă utilizabilă): {int((d > c['split_above_s']).sum())}")
    print(f"  segmente > {c['max_segment_s']}s (încalcă limita fermă): {int((d > c['max_segment_s'] + 1e-6).sum())}")

    print("\netape (status.json):")
    for name, st in status.get("stages", {}).items():
        extra = {k: v for k, v in st.items() if k not in ("state", "started_at", "finished_at", "seconds")}
        print(f"  {name:10s} {st.get('state', '?'):7s} {st.get('seconds', 0):8.2f} s  {extra if extra else ''}")


if __name__ == "__main__":
    main()
