"""Pipeline complet: ingest -> normalize -> segment -> asr -> postprocess -> pack.

    python run_pipeline.py sedinta.m4a --model medium --device cuda [--no-diarization]

Fiecare etapă e reluabilă: la o nouă rulare pe același fișier, etapele terminate
sunt sărite, iar ASR-ul continuă de la ultimul segment scris.
"""
import argparse
import json
import sys
import time

from pipeline.common import jobs_dir, load_config, load_status, save_status, ROOT
from pipeline.ingest import ingest
from pipeline.normalize import normalize
from pipeline.segment import segment
from pipeline.asr import asr
from pipeline.postprocess import postprocess
from pipeline.pack_for_llm import pack

# etapa din run_pipeline -> etapele din status.json pe care le cuprinde
STATUS_KEYS = {"ingest": ["ingest", "probe"], "normalize": ["normalize", "denoise"],
               "segment": ["diarize", "segment"], "asr": ["asr"], "postprocess": ["postprocess"],
               "pack": ["pack"]}


def fmt_t(s):
    s = float(s or 0)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:04.1f}" if s >= 3600 else f"{int(s // 60)}:{s % 60:04.1f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--model", help="ex.: medium, large-v3, large-v3-turbo")
    ap.add_argument("--device", choices=["cuda", "cpu"])
    ap.add_argument("--compute-type", help="implicit: din config (cpu => int8)")
    ap.add_argument("--batch-size", type=int)
    ap.add_argument("--beam-size", type=int, help="implicit: din config (5)")
    ap.add_argument("--language", help="forțează limba (ro/ru/en); implicit: detecție per segment")
    ap.add_argument("--language-bias", help="bonus la alegerea limbii, ex.: ro=0.2 sau ro=0.2,en=0.1")
    ap.add_argument("--no-prompt", action="store_true", help="fără initial_prompt și hotwords (pentru comparații)")
    ap.add_argument("--denoise", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--diarization", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--job-id")
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = load_config(args.config)
    w = cfg["whisper"]
    if args.model:
        w["model"] = args.model
    if args.device:
        w["device"] = args.device
        cfg["diarization"]["device"] = args.device
        if args.device == "cpu" and not args.compute_type:
            w["compute_type"] = "int8"
    if args.compute_type:
        w["compute_type"] = args.compute_type
    if args.batch_size:
        w["batch_size"] = args.batch_size
    if args.beam_size:
        w["beam_size"] = args.beam_size
    if args.language:
        w["language"] = args.language
    if args.language_bias:
        w["language_bias"] = {k.strip(): float(v) for k, v in (x.split("=") for x in args.language_bias.split(","))}
    if args.no_prompt:
        w["use_prompt"] = False
    diar = cfg["diarization"]["enabled"] if args.diarization is None else args.diarization
    if diar and not (ROOT / cfg["diarization"]["pipeline_dir"] / "config.yaml").exists():
        sys.exit(f"Lipsește modelul de diarizare ({cfg['diarization']['pipeline_dir']}, vezi SETUP.md §5b). "
                 "Rulează cu --no-diarization pentru speaker 'UNK'.")

    t_start = time.perf_counter()
    timings = {}
    job_id = None

    def step(i, name, fn):
        print(f"\n━━ [{i}/6] {name} ━━")
        t0 = time.perf_counter()
        r = fn()
        timings[name] = round(time.perf_counter() - t0, 2)
        return r

    job_id, job_dir = step(1, "ingest", lambda: ingest(args.audio, args.job_id, cfg))
    print(f"job_id={job_id}")
    step(2, "normalize", lambda: normalize(job_id, args.denoise, cfg))
    step(3, "segment", lambda: segment(job_id, diar, False, cfg))
    step(4, "asr", lambda: asr(job_id, cfg))
    step(5, "postprocess", lambda: postprocess(job_id, cfg))
    out = step(6, "pack", lambda: pack(job_id, cfg))
    total = time.perf_counter() - t_start

    # ---------- raport ----------
    status = load_status(job_dir)
    audio_s = status.get("duration_s") or 0
    st = status["stages"]
    transcript = json.loads((job_dir / "transcript.json").read_text(encoding="utf-8"))
    kept = [s for s in transcript if not s["dropped"]]
    llm = json.loads(out.read_text(encoding="utf-8"))

    print("\n══════════════ RAPORT ══════════════")
    print(f"job:            {job_id}")
    print(f"model:          {w['model']} / {w['device']} / {w['compute_type']} (batch {w['batch_size']})")
    print(f"durată audio:   {fmt_t(audio_s)}")
    print("etape (acum | ultima rulare efectivă, din status.json):")
    for name, keys in STATUS_KEYS.items():
        run_s = sum(st.get(k, {}).get("seconds", 0) for k in keys)
        print(f"  {name:12s} {timings[name]:8.1f} s | {run_s:8.1f} s")
    print(f"timp total:     {fmt_t(total)}")
    if audio_s:
        print(f"real-time:      RTF {total / audio_s:.3f}  (≈ {audio_s / total:.1f}× mai rapid decât durata audio)")
    print(f"segmente:       {len(transcript)} (eliminate: {len(transcript) - len(kept)} "
          f"{st.get('postprocess', {}).get('dropped', {})})")
    print(f"vorbitori:      {len(llm['speakers'])} {llm['speakers']}")
    print(f"limbi:          {llm['languages']}")
    low = sum(s["low_confidence"] for s in kept)
    print(f"încredere mică: {low} segmente ({100 * low / max(len(kept), 1):.1f}%), "
          f"{sum(t['low_confidence'] for t in llm['turns'])} replici")
    print(f"LLM:            {llm['n_turns']} replici în {llm['n_windows']} ferestre "
          f"(max {max((x['tokens_est'] for x in llm['windows']), default=0)} tokeni est.)")
    print(f"ieșire:         {out}")

    status["report"] = {"total_s": round(total, 2), "rtf": round(total / audio_s, 4) if audio_s else None,
                        "stage_wall_s": timings, "model": w["model"], "device": w["device"]}
    save_status(job_dir, status)


if __name__ == "__main__":
    main()
