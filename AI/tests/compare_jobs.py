"""Compară transcrierile a două joburi pe același audio (aceleași segmente), segment cu segment.

    python -m tests.compare_jobs JOB_A JOB_B [--all]

Fără --all afișează doar segmentele unde limba detectată diferă.
"""
import argparse
import json
from collections import Counter

from pipeline.common import jobs_dir, load_config


def load(job):
    d = jobs_dir(load_config()) / job
    return (json.loads((d / "segments.json").read_text(encoding="utf-8")),
            json.loads((d / "transcript.json").read_text(encoding="utf-8")))


def summary(t):
    kept = [s for s in t if not s["dropped"]]
    lp = [s["avg_logprob"] for s in kept if s["avg_logprob"] is not None]
    sure = [s for s in kept if not s["low_confidence"] and "uncertain_language" not in s["flags"]]
    return {
        "limbi": dict(Counter(s["lang"] for s in kept)),
        "avg_logprob mediu": round(sum(lp) / len(lp), 3),
        "low_confidence": sum(s["low_confidence"] for s in kept),
        "uncertain_language": sum("uncertain_language" in s["flags"] for s in kept),
        "sigure": len(sure),
        "eliminate": sum(bool(s["dropped"]) for s in t),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    (sa, ta), (sb, tb) = load(args.a), load(args.b)
    if sa != sb:
        raise SystemExit("Joburile au segmentări diferite: comparația segment cu segment nu are sens.")

    for name, t in ((args.a, ta), (args.b, tb)):
        print(f"{name:24s} {summary(t)}")

    diff = [(x, y) for x, y in zip(ta, tb) if x["lang"] != y["lang"]]
    print(f"\nlimbă diferită în {len(diff)}/{len(ta)} segmente: "
          f"{dict(Counter(x['lang'] + '->' + y['lang'] for x, y in diff))}\n")
    for x, y in (zip(ta, tb) if args.all else diff):
        print(f"#{x['id']} [{int(x['start'] // 60):02d}:{int(x['start'] % 60):02d}] {x['end'] - x['start']:.1f}s")
        for name, s in ((args.a, x), (args.b, y)):
            print(f"  {name[:14]:14s} {s['lang']}({s['lang_prob']:.2f}) lp={s['avg_logprob']}: {s['text'][:150]}")


if __name__ == "__main__":
    main()
