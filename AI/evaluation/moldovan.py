"""Evaluare pe eșantionul de română moldovenească (evaluation/sample_moldovan.py): WER/CER
și cât de des alegerea limbii dă altceva decât `ro` (confuzia ro -> ru din config.yaml).

    python -m evaluation.moldovan make-job [--job-id md_sample]   # job fără ingest/normalize/segment
    python -m pipeline.asr md_sample [--model large-v3]             # pe mașina cu GPU
    python -m pipeline.postprocess md_sample
    python -m evaluation.moldovan score md_sample [--note "..."]

make-job concatenează clipurile (cu 0.5 s de liniște între ele) într-un audio_16k.wav și scrie
segments.json cu un segment per clip, deci ASR-ul și postprocesarea rulează cu ACELAȘI cod ca
pentru ședințe. Pentru alt model/setări: alt --job-id (asr.jsonl nu amestecă setări).

Textul corpusului e normalizat (litere mici, numerele scrise în litere), iar Whisper scrie cifre.
De aceea raportăm și WER-ul pe segmentele fără cifre în ipoteză (wer_no_digits).
"""
import argparse
import json
import re
from collections import Counter

import numpy as np
import soundfile as sf

from evaluation.metrics import Errors, append_result, edit_counts, rnd
from pipeline.common import ROOT, jobs_dir, load_config, now, save_status
from pipeline.glossary import norm_text, script_of

SAMPLE = ROOT / "data" / "external" / "moldovan_dialect" / "sample"
GAP_S = 0.5
SR = 16000


def make_job(job_id, cfg):
    manifest = [json.loads(l) for l in open(SAMPLE / "manifest.jsonl", encoding="utf-8")]
    job = jobs_dir(cfg) / job_id
    if (job / "segments.json").exists():
        raise SystemExit(f"{job} există deja")
    job.mkdir(parents=True, exist_ok=True)
    gap = np.zeros(int(GAP_S * SR), dtype="int16")
    segs, refs, t = [], {}, 0.0
    with sf.SoundFile(str(job / "audio_16k.wav"), "w", SR, 1, "PCM_16") as out:
        for i, m in enumerate(manifest):
            x, sr = sf.read(str(SAMPLE / m["wav"]), dtype="int16")
            assert sr == SR, m["wav"]
            out.write(x)
            out.write(gap)
            d = len(x) / SR
            segs.append({"id": i, "speaker": m["teacher"], "start": round(t, 3), "end": round(t + d, 3)})
            refs[i] = {"clip": m["id"], "text": m["text"], "teacher": m["teacher"], "discipline": m["discipline"]}
            t += d + GAP_S
    (job / "segments.json").write_text(json.dumps(segs, indent=1), encoding="utf-8")
    (job / "refs.json").write_text(json.dumps(refs, ensure_ascii=False, indent=1), encoding="utf-8")
    save_status(job, {"job_id": job_id, "created_at": now(), "source": "moldovan_sample",
                      "audio": "audio_16k.wav", "duration_s": round(t, 2),
                      "stages": {"segment": {"state": "done", "n_segments": len(segs), "from": "moldovan_sample"}}})
    return job, len(segs), t


def score(job_id, cfg):
    job = jobs_dir(cfg) / job_id
    refs = {int(k): v for k, v in json.loads((job / "refs.json").read_text(encoding="utf-8")).items()}
    tr = {s["id"]: s for s in json.loads((job / "transcript.json").read_text(encoding="utf-8"))}

    w_all, c_all, w_nod, w_ro, w_other = Errors(), Errors(), Errors(), Errors(), Errors()
    langs, dropped, by_disc, rows = Counter(), Counter(), {}, []
    cyr = lat = 0
    for i, ref in refs.items():
        s = tr.get(i)
        hyp_text = s["text"] if s and not s["dropped"] else ""
        if s and s["dropped"]:
            dropped[s["dropped"]] += 1
        r, h = norm_text(ref["text"]), norm_text(hyp_text)
        we, ce = edit_counts(r, h), edit_counts(" ".join(r), " ".join(h))
        w_all.add(we)
        c_all.add(ce)
        if not re.search(r"\d", hyp_text):
            w_nod.add(we)
        lang = s["lang"] if s else None
        langs[lang] += 1
        (w_ro if lang == "ro" else w_other).add(we)
        by_disc.setdefault(ref["discipline"], Errors()).add(we)
        for tok in h:
            if script_of(tok) == "cyrl":
                cyr += 1
            else:
                lat += 1
        rows.append({"id": i, "clip": ref["clip"], "lang": lang, "lang_scores": s and s.get("lang_scores"),
                     "errors": we.S + we.D + we.I, "wer": rnd(we.rate), "ref": ref["text"], "hyp": hyp_text})

    n = len(refs)
    setups = sorted({s.get("setup") for s in tr.values() if s.get("setup")})
    report = {
        "job": job_id, "gold": "moldovan_sample", "setup": "; ".join(setups), "n_segments": n,
        "wer": w_all.as_dict(), "wer_no_digits": w_nod.as_dict(), "cer": c_all.as_dict(),
        "wer_when_lang_ro": w_ro.as_dict(), "wer_when_lang_not_ro": w_other.as_dict(),
        "languages": dict(langs), "lang_acc": rnd(langs["ro"] / n) if n else None,
        "cyrillic_word_share": rnd(cyr / (cyr + lat)) if cyr + lat else None,
        "dropped": dict(dropped),
        "wer_by_discipline": {d: e.as_dict() for d, e in sorted(by_disc.items())},
        "not_ro": [r for r in rows if r["lang"] != "ro"],
        "worst": sorted(rows, key=lambda r: -r["errors"])[:30],
    }
    (job / "eval_moldovan_sample.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def pct(x):
    return "–" if x is None else f"{100 * x:.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("make-job")
    a.add_argument("--job-id", default="md_sample")
    b = sub.add_parser("score")
    b.add_argument("job_id")
    b.add_argument("--note")
    b.add_argument("--no-save", action="store_true")
    ap.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config)

    if args.cmd == "make-job":
        job, n, t = make_job(args.job_id, cfg)
        print(f"{job}: {n} segmente, {t / 60:.1f} min\n"
              f"următorul pas: python -m pipeline.asr {args.job_id} && python -m pipeline.postprocess {args.job_id}")
        return

    r = score(args.job_id, cfg)
    print(f"job {r['job']}  setup {r['setup']}  {r['n_segments']} segmente")
    print(f"WER {pct(r['wer']['rate'])} (fără cifre: {pct(r['wer_no_digits']['rate'])}), CER {pct(r['cer']['rate'])}")
    print(f"limba aleasă: {r['languages']}  => ro corect {pct(r['lang_acc'])}; "
          f"WER când limba=ro {pct(r['wer_when_lang_ro']['rate'])}, altfel {pct(r['wer_when_lang_not_ro']['rate'])}")
    print(f"cuvinte chirilice în ipoteză: {pct(r['cyrillic_word_share'])}; eliminate: {r['dropped'] or 0}")
    for d, e in r["wer_by_discipline"].items():
        print(f"  {d:36s} WER {pct(e['rate'])} ({e['N']} cuvinte)")
    if not args.no_save:
        append_result({"gold": "moldovan_sample", "job": r["job"], "setup": r["setup"], "note": args.note,
                       "n_ref_words": r["wer"]["N"], "wer": r["wer"]["rate"], "wer_no_digits": r["wer_no_digits"]["rate"],
                       "wer_clean": r["wer"]["rate"], "cer": r["cer"]["rate"], "cer_clean": r["cer"]["rate"],
                       "wer_ro": r["wer"]["rate"], "lang_acc": r["lang_acc"]})
        print("-> evaluation/results.csv, evaluation/RESULTS.md")


if __name__ == "__main__":
    main()
