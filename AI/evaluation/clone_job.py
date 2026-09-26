"""Copie a unui job până după ASR, pentru variante care schimbă doar postprocesarea
(ex. post-corecția după glosar), fără să refacem ASR-ul:

    python -m evaluation.clone_job Medpark_base Medpark_base_corr
    python -m pipeline.postprocess Medpark_base_corr     # cu noul config.yaml
    python -m pipeline.pack_for_llm Medpark_base_corr
    python -m evaluation.evaluate Medpark_base_corr --gold Medpark --note "+ corecție glosar"

Nu copiază transcript.json, llm_input.json, eval_*.json și nici sursa (source.*).
"""
import argparse
import shutil

from pipeline.common import jobs_dir, load_config, load_status, save_status

SKIP = {"transcript.json", "llm_input.json"}
RESET_STAGES = ("postprocess", "pack")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    args = ap.parse_args()

    root = jobs_dir(load_config())
    src, dst = root / args.src, root / args.dst
    if not (src / "asr.jsonl").exists():
        raise SystemExit(f"{src} nu are asr.jsonl")
    if dst.exists():
        raise SystemExit(f"{dst} există deja")
    dst.mkdir()
    for p in src.iterdir():
        if p.is_file() and p.name not in SKIP and not p.name.startswith(("eval_", "source.")):
            shutil.copy2(p, dst / p.name)
    status = load_status(dst)
    status["job_id"] = args.dst
    status["cloned_from"] = args.src
    for k in RESET_STAGES:
        status["stages"].pop(k, None)
    save_status(dst, status)
    print(f"{dst}: copiat din {args.src} (fără {', '.join(sorted(SKIP))})")


if __name__ == "__main__":
    main()
