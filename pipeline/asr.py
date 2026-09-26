"""ASR: faster-whisper pe segmentele din segments.json.

    python -m pipeline.asr JOB_ID [--model medium] [--device cuda] [--compute-type float16]

Scrie incremental jobs/{id}/asr.jsonl (un segment pe linie, fsync după fiecare
grup). La reluare continuă de la segmentele care lipsesc din fișier.
"""
import argparse
import json
import os
from bisect import bisect_right

import torch  # noqa: F401  încarcă cuBLAS/cuDNN pentru CTranslate2 (Windows); înainte de faster_whisper
import numpy as np
import soundfile as sf
from tqdm import tqdm

from pipeline.common import ROOT, jobs_dir, load_config, load_status, run_stage

SR = 16000
POOL_BATCHES = 4  # câte batch-uri detectăm/transcriem înainte de a scrie pe disc


def load_model(w, cfg):
    from faster_whisper import WhisperModel

    local = ROOT / cfg["paths"]["models_dir"] / f"faster-whisper-{w['model']}"
    ref = str(local) if local.is_dir() else w["model"]  # altfel: cache HF, fără rețea
    return WhisperModel(ref, device=w["device"], compute_type=w["compute_type"], local_files_only=True)


def load_glossary(w):
    """glossary/prompt.txt -> initial_prompt, glossary/hotwords.txt -> hotwords (câte un termen pe linie)."""
    g = ROOT / "glossary"

    def lines(name):
        p = g / name
        if not p.exists():
            return []
        return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]

    prompt = " ".join(lines("prompt.txt")) or w.get("initial_prompt") or None
    hotwords = ", ".join(lines("hotwords.txt")) or None
    return prompt, hotwords


def read_done(path):
    """Id-urile deja transcrise. O ultimă linie incompletă (crash) e eliminată."""
    if not path.exists():
        return {}
    good, done = [], {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            break
        good.append(line)
        done[r["id"]] = r
    if len(good) != len(path.read_text(encoding="utf-8").splitlines()):
        path.write_text("".join(l + "\n" for l in good), encoding="utf-8")
    return done


def read_clip(f, s):
    f.seek(int(s["start"] * SR))
    return f.read(int((s["end"] - s["start"]) * SR), dtype="float32")


def detect_languages(model, clips, allowed, batch_size):
    """Limba per clip, dintr-o trecere de encoder; restrânsă la `allowed`."""
    from faster_whisper.audio import pad_or_trim

    out = []
    for i in range(0, len(clips), batch_size):
        feats = np.stack([pad_or_trim(model.feature_extractor(c)[..., :-1]) for c in clips[i:i + batch_size]])
        for res in model.model.detect_language(model.encode(feats)):
            probs = {tok[2:-2]: p for tok, p in res}  # "<|ro|>" -> "ro"
            cand = {k: v for k, v in probs.items() if not allowed or k in allowed} or probs
            lang = max(cand, key=cand.get)
            out.append((lang, round(float(cand[lang]), 3)))
    return out


def transcribe_batched(pipe, clips, lang, prompt, hotwords, w):
    """Clipurile concatenate + clip_timestamps => un apel batched. Returnează,
    per clip, segmentele whisper și offset-ul clipului în audio-ul concatenat."""
    offsets = np.cumsum([0] + [len(c) for c in clips[:-1]]) / SR
    clip_ts = [{"start": float(o), "end": float(o + len(c) / SR)} for o, c in zip(offsets, clips)]
    segments, _ = pipe.transcribe(
        np.concatenate(clips), language=lang, clip_timestamps=clip_ts,
        batch_size=w["batch_size"], beam_size=w["beam_size"],
        initial_prompt=prompt, hotwords=hotwords,
        word_timestamps=True, vad_filter=False,  # condition_on_previous_text e mereu False aici
    )
    buckets = [[] for _ in clips]
    for s in segments:
        # s.start e rotunjit la cadre de 20 ms și poate cădea puțin înaintea offset-ului
        # clipului; mijlocul segmentului e mereu în interiorul clipului corect
        buckets[bisect_right(offsets, (s.start + s.end) / 2) - 1].append(s)
    return list(zip(buckets, offsets))


def transcribe_one(model, clip, lang, prompt, hotwords, w):
    segments, _ = model.transcribe(
        clip, language=lang, beam_size=w["beam_size"],
        initial_prompt=prompt, hotwords=hotwords,
        condition_on_previous_text=False, word_timestamps=True, vad_filter=False,
    )
    return list(segments), 0.0


def setup(w):
    """Ce determină rezultatul ASR: model/compute_type/limbă."""
    return f"{w['model']}/{w['compute_type']}/{w['language'] or 'auto'}"


def compression_ratio(text):
    from faster_whisper.transcribe import get_compression_ratio
    return round(get_compression_ratio(text), 3) if text else 0.0


def record(seg, lang, lang_prob, subsegs, offset, w):
    text = "".join(s.text for s in subsegs).strip()
    words = [
        {"w": x.word, "start": round(seg["start"] + x.start - offset, 3),
         "end": round(seg["start"] + x.end - offset, 3), "p": round(x.probability, 3)}
        for s in subsegs for x in (s.words or [])
    ]
    if subsegs:
        n = [max(len(s.tokens), 1) for s in subsegs]
        avg_logprob = round(float(np.average([s.avg_logprob for s in subsegs], weights=n)), 4)
        no_speech = round(float(np.mean([s.no_speech_prob for s in subsegs])), 4)
    else:
        avg_logprob = no_speech = None
    return {
        "id": seg["id"], "setup": setup(w),
        "speaker": seg["speaker"], "start": seg["start"], "end": seg["end"],
        "lang": lang, "lang_prob": lang_prob, "text": text,
        "avg_logprob": avg_logprob, "no_speech_prob": no_speech,
        "compression_ratio": compression_ratio(text),
        "low_confidence": avg_logprob is None or avg_logprob < w["low_confidence_logprob"],
        "words": words,
    }


def asr(job_id, cfg=None):
    cfg = cfg or load_config()
    w = cfg["whisper"]
    job_dir = jobs_dir(cfg) / job_id
    status = load_status(job_dir)
    segs = json.loads((job_dir / "segments.json").read_text(encoding="utf-8"))
    wav = job_dir / status["audio"]
    out = job_dir / "asr.jsonl"

    # nu amestecăm modele/setări în același asr.jsonl (și nu sărim etapa cu rezultatele altui model)
    used = {r.get("setup") for r in read_done(out).values()} - {None}
    if used and used != {setup(w)}:
        raise SystemExit(f"{out} e făcut cu {sorted(used)}, nu cu {setup(w)}. "
                         f"Folosește alt --job-id (ex.: {job_id}_{w['model']}) sau șterge asr.jsonl.")

    def work():
        done = read_done(out)
        todo = [s for s in segs if s["id"] not in done]
        model = load_model(w, cfg)

        pipe = None
        if w["batch_size"] > 1:
            try:
                from faster_whisper import BatchedInferencePipeline
                pipe = BatchedInferencePipeline(model)
            except ImportError:
                print("[asr] BatchedInferencePipeline indisponibil în această versiune: transcriere secvențială")

        prompt, hotwords = load_glossary(w)
        pool = max(w["batch_size"], 1) * POOL_BATCHES
        prev_lang = done[max(done)]["lang"] if done else None
        langs_count = {}

        with sf.SoundFile(str(wav)) as f, open(out, "a", encoding="utf-8") as fo, \
                tqdm(total=round(sum(s["end"] - s["start"] for s in todo)), unit="s", desc="asr") as bar:
            assert f.samplerate == SR, f"{wav} nu e {SR} Hz"
            for i in range(0, len(todo), pool):
                chunk = todo[i:i + pool]
                clips = [read_clip(f, s) for s in chunk]

                if w["language"]:
                    langs = [(w["language"], 1.0)] * len(chunk)
                else:
                    langs = detect_languages(model, clips, w["languages"], max(w["batch_size"], 1))
                for k, s in enumerate(chunk):
                    lang, p = langs[k]
                    if s["end"] - s["start"] < w["short_segment_s"] and p < 0.5 and prev_lang:
                        langs[k] = (prev_lang, p)  # prea scurt ca să ne încredem în detecție
                    prev_lang = langs[k][0]

                results = [None] * len(chunk)
                for lang in sorted({l for l, _ in langs}):
                    idx = [k for k in range(len(chunk)) if langs[k][0] == lang]
                    if pipe:
                        for k, r in zip(idx, transcribe_batched(pipe, [clips[k] for k in idx], lang, prompt, hotwords, w)):
                            results[k] = r
                    else:
                        for k in idx:
                            results[k] = transcribe_one(model, clips[k], lang, prompt, hotwords, w)

                for s, (lang, p), (subsegs, offset) in zip(chunk, langs, results):
                    fo.write(json.dumps(record(s, lang, p, subsegs, offset, w), ensure_ascii=False) + "\n")
                    langs_count[lang] = langs_count.get(lang, 0) + 1
                fo.flush()
                os.fsync(fo.fileno())
                bar.update(round(sum(s["end"] - s["start"] for s in chunk)))

        return {"model": w["model"], "device": w["device"], "compute_type": w["compute_type"],
                "batched": pipe is not None, "resumed_from": len(done), "transcribed": len(todo),
                "languages": langs_count, "initial_prompt": bool(prompt), "hotwords": bool(hotwords)}

    run_stage(job_dir, "asr", out, work, done=lambda: len(read_done(out)) >= len(segs))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id")
    ap.add_argument("--model")
    ap.add_argument("--device")
    ap.add_argument("--compute-type")
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = load_config(args.config)
    for k in ("model", "device", "compute_type"):
        if getattr(args, k):
            cfg["whisper"][k] = getattr(args, k)
    print(asr(args.job_id, cfg))


if __name__ == "__main__":
    main()
