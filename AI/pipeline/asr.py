"""ASR: faster-whisper pe segmentele din segments.json.

    python -m pipeline.asr JOB_ID [--model medium] [--device cuda] [--compute-type float16]

Scrie incremental jobs/{id}/asr.jsonl (un segment pe linie, fsync după fiecare
grup). La reluare continuă de la segmentele care lipsesc din fișier.
"""
import argparse
import hashlib
import json
import os
import re
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


def load_glossary(w, lang):
    """(initial_prompt, hotwords) pentru limba `lang`. Ordinea de căutare:
    glossary/prompt.<lang>.txt, glossary/prompt.txt, whisper.initial_prompt[lang] din config.
    La fel pentru hotwords.<lang>.txt / hotwords.txt (câte un termen pe linie), doar cu whisper.use_hotwords
    (fișierele generate din glossary/terms.tsv stau aici, dar nu schimbă baseline-ul)."""
    if w.get("use_prompt") is False:
        return None, None
    g = ROOT / "glossary"

    def lines(name):
        p = g / name
        if not p.exists():
            return []
        return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]

    cfg_prompt = w.get("initial_prompt")
    if isinstance(cfg_prompt, dict):
        cfg_prompt = cfg_prompt.get(lang)
    prompt = " ".join(lines(f"prompt.{lang}.txt") or lines("prompt.txt")) or cfg_prompt or None
    hotwords = ", ".join(lines(f"hotwords.{lang}.txt") or lines("hotwords.txt")) or None
    return prompt, (hotwords if w.get("use_hotwords") else None)


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


def detect_languages(model, clips, allowed, batch_size, keep=False):
    """Per clip: [(limbă, prob), ...] descrescător, dintr-o trecere de encoder; restrâns la `allowed`.
    keep=True: întoarce și ieșirile encoderului [(StorageView, primul index)], refolosite la score_languages."""
    from faster_whisper.audio import pad_or_trim

    out, encs = [], []
    for i in range(0, len(clips), batch_size):
        feats = np.stack([pad_or_trim(model.feature_extractor(c)[..., :-1]) for c in clips[i:i + batch_size]])
        enc = model.encode(feats)
        if keep:
            encs.append((enc, i))
        for res in model.model.detect_language(enc):
            probs = {tok[2:-2]: p for tok, p in res}  # "<|ro|>" -> "ro"
            cand = {k: v for k, v in probs.items() if not allowed or k in allowed} or probs
            out.append(sorted(((k, round(float(v), 3)) for k, v in cand.items()), key=lambda kv: -kv[1]))
    return (out, encs) if keep else out


def score_languages(model, encs, cands, w):
    """Scorul fiecărei limbi candidate, decodând direct din ieșirea encoderului de la detecție (fără încă o
    trecere de encoder): beam w["beam_size"], fără prompt, fără timestamps, ca decodarea de comparație clasică.
    Scorul = log-probabilitatea medie per token. -> {(k, lang): scor}"""
    tok = model.hf_tokenizer
    sot, tr, nots = (tok.token_to_id(t) for t in ("<|startoftranscript|>", "<|transcribe|>", "<|notimestamps|>"))
    scores = {}
    for enc, i0 in encs:
        n = enc.shape[0]
        for r in range(max(len(cands[i0 + j]) for j in range(n))):
            langs = [cands[i0 + j][min(r, len(cands[i0 + j]) - 1)][0] for j in range(n)]
            res = model.model.generate(enc, [[sot, tok.token_to_id(f"<|{l}|>"), tr, nots] for l in langs],
                                       beam_size=w["beam_size"], return_scores=True, max_length=224,
                                       suppress_blank=True)
            for j, (l, x) in enumerate(zip(langs, res)):
                if r < len(cands[i0 + j]) and x.scores:
                    scores[(i0 + j, l)] = round(float(x.scores[0]), 4)
    return scores


def choose_candidates(det, seg, prev_lang, w):
    """Limbile în care decodăm segmentul: compare_languages + limba detectată (dacă e alta).
    Fără compare_languages: doar limba detectată; segmentele scurte cu detecție nesigură
    moștenesc limba anterioară."""
    cmp = w.get("compare_languages") or []
    if not cmp:
        top = det[0]
        if seg["end"] - seg["start"] < w["short_segment_s"] and top[1] < 0.5 and prev_lang:
            top = (prev_lang, top[1])
        return [top]
    # detector sigur pe o limbă în care nu greșește sistematic => o singură decodare (viteză).
    # Limbile „pierzătoare” ale unei preferințe (ex. ru, în care e luată româna moldovenească) se compară mereu.
    losers = {b for overs in (w.get("language_preference") or {}).values() for b in overs}
    skip = w.get("compare_skip_prob")
    if skip is not None and det[0][1] >= skip and det[0][0] not in losers:
        return [det[0]]
    probs = dict(det)
    return [(l, probs.get(l, 0.0)) for l in dict.fromkeys(cmp + [det[0][0]])]


def transcribe_batched(pipe, clips, lang, prompt, hotwords, w):
    """Clipurile concatenate + clip_timestamps => un apel batched. Returnează,
    per clip, segmentele whisper și offset-ul clipului în audio-ul concatenat."""
    offsets = np.cumsum([0] + [len(c) for c in clips[:-1]]) / SR
    clip_ts = [{"start": float(o), "end": float(o + len(c) / SR)} for o, c in zip(offsets, clips)]
    segments, _ = pipe.transcribe(
        np.concatenate(clips), language=lang, clip_timestamps=clip_ts,
        batch_size=w["batch_size"], beam_size=w["beam_size"],
        initial_prompt=prompt, hotwords=hotwords,
        word_timestamps=w.get("word_timestamps", True), vad_filter=False,  # condition_on_previous_text e mereu False aici
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
        condition_on_previous_text=False, word_timestamps=w.get("word_timestamps", True), vad_filter=False,
    )
    return list(segments), 0.0


def setup(w, glossary):
    """Ce determină rezultatul ASR: model/compute_type/limbă/amprenta prompturilor și hotwords."""
    lang = w["language"] or "auto"
    if not w["language"] and w.get("compare_languages"):
        lang += "-cmp-" + "+".join(w["compare_languages"])
        pref = w.get("language_preference") or {}
        if pref:
            lang += "-pref-" + "+".join(f"{a}>{b}{m:g}" for a, o in sorted(pref.items()) for b, m in sorted(o.items()))
        if w.get("compare_skip_prob") is not None:
            lang += f"-skip{w['compare_skip_prob']:g}"
        if w.get("compare_from_encoder"):
            lang += "-encscore"
    fp = hashlib.sha1(json.dumps(sorted(glossary.items()), ensure_ascii=False).encode("utf-8")).hexdigest()[:6]
    beam = f"/beam{w['beam_size']}" if w["beam_size"] != 5 else ""
    return f"{w['model']}/{w['compute_type']}/{lang}/p{fp}{beam}"


def pick_language(scores, preference):
    """Limba cu avg_logprob-ul cel mai bun. O preferință {a: {b: m}} schimbă DOAR duelul a–b: dacă b câștigă,
    dar a e la cel mult m în urmă, câștigă a. Față de alte limbi (ex. engleza) decide scorul real.
    (Un bonus aplicat față de orice limbă făcea engleza să fie „tradusă” în română.)"""
    best = max(scores, key=scores.get)
    for a, overs in (preference or {}).items():
        m = overs.get(best)
        # a câștigă doar duelul cu `best`; o a treia limbă mai bună decât a rămâne mai bună
        if a in scores and m is not None and scores[a] + m >= scores[best] \
                and all(scores[a] >= v for l, v in scores.items() if l not in (a, best)):
            best = a
    return best


def prompt_leak(text, prompt, min_cover=0.5):
    """Whisper copiază uneori initial_prompt în transcriere (mai ales cu limba greșită
    sau pe audio neclar), iar copia are un avg_logprob foarte bun. Considerăm copie
    textul acoperit în proporție de >= min_cover de trigrame de cuvinte din prompt."""
    tw = re.findall(r"\w+", (text or "").lower())
    pw = re.findall(r"\w+", (prompt or "").lower())
    if len(tw) < 3 or len(pw) < 3:
        return False
    pg = {tuple(pw[i:i + 3]) for i in range(len(pw) - 2)}
    covered = set()
    for i in range(len(tw) - 2):
        if tuple(tw[i:i + 3]) in pg:
            covered.update((i, i + 1, i + 2))
    return len(covered) / len(tw) >= min_cover


def avg_logprob(subsegs):
    if not subsegs:
        return None
    n = [max(len(s.tokens), 1) for s in subsegs]
    return round(float(np.average([s.avg_logprob for s in subsegs], weights=n)), 4)


def compression_ratio(text):
    from faster_whisper.transcribe import get_compression_ratio
    return round(get_compression_ratio(text), 3) if text else 0.0


def record(seg, lang, lang_prob, lang_scores, method, subsegs, offset, w, su):
    text = "".join(s.text for s in subsegs).strip()
    words = [
        {"w": x.word, "start": round(seg["start"] + x.start - offset, 3),
         "end": round(seg["start"] + x.end - offset, 3), "p": round(x.probability, 3)}
        for s in subsegs for x in (s.words or [])
    ]
    lp = avg_logprob(subsegs)
    no_speech = round(float(np.mean([s.no_speech_prob for s in subsegs])), 4) if subsegs else None
    return {
        "id": seg["id"], "setup": su,
        "speaker": seg["speaker"], "start": seg["start"], "end": seg["end"],
        "lang": lang,
        "lang_prob": lang_prob,        # probabilitatea dată de detector limbii alese
        "lang_scores": lang_scores,    # avg_logprob per limbă decodată ({} dacă s-a decodat una singură)
        "lang_method": method,         # forced | detect | logprob
        "text": text,
        "avg_logprob": lp, "no_speech_prob": no_speech,
        "compression_ratio": compression_ratio(text),
        "low_confidence": lp is None or lp < w["low_confidence_logprob"],
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

    forced = w["language"]
    glossary = {l: load_glossary(w, l) for l in ([forced] if forced else w["languages"] or [])}
    su = setup(w, glossary)

    # nu amestecăm modele/setări/prompturi în același asr.jsonl (și nu sărim etapa cu rezultatele altora)
    used = {r.get("setup") for r in read_done(out).values()} - {None}
    if used and used != {su}:
        raise SystemExit(f"{out} e făcut cu {sorted(used)}, nu cu {su}. "
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

        pool = max(w["batch_size"], 1) * POOL_BATCHES
        prev_lang = done[max(done)]["lang"] if done else None
        langs_count, decodes = {}, 0

        with sf.SoundFile(str(wav)) as f, open(out, "a", encoding="utf-8") as fo, \
                tqdm(total=round(sum(s["end"] - s["start"] for s in todo)), unit="s", desc="asr") as bar:
            assert f.samplerate == SR, f"{wav} nu e {SR} Hz"
            for i in range(0, len(todo), pool):
                chunk = todo[i:i + pool]
                clips = [read_clip(f, s) for s in chunk]

                # limbile candidate per segment
                enc_cmp = bool(w.get("compare_from_encoder")) and not forced
                if forced:
                    cands = [[(forced, 1.0)] for _ in chunk]
                else:
                    det = detect_languages(model, clips, w["languages"], max(w["batch_size"], 1), keep=enc_cmp)
                    det, encs = det if enc_cmp else (det, None)
                    cands = []
                    for s, d in zip(chunk, det):
                        cands.append(choose_candidates(d, s, prev_lang, w))
                        prev_lang = cands[-1][0][0]

                def decode(idx_langs, use_prompt, wd=w):
                    """idx_langs: [(k, lang)]. Un apel (batched) per limbă. -> {(k, lang): (subsegs, offset)}"""
                    nonlocal decodes
                    out = {}
                    for lang in sorted({l for _, l in idx_langs}):
                        idx = [k for k, l in idx_langs if l == lang]
                        if lang not in glossary:
                            glossary[lang] = load_glossary(w, lang)
                        prompt, hotwords = glossary[lang] if use_prompt else (None, None)
                        if pipe:
                            rs = transcribe_batched(pipe, [clips[k] for k in idx], lang, prompt, hotwords, wd)
                        else:
                            rs = [transcribe_one(model, clips[k], lang, prompt, hotwords, wd) for k in idx]
                        out.update({(k, lang): r for k, r in zip(idx, rs)})
                        decodes += len(idx)
                    return out

                # 1) decizia de limbă: decodare FĂRĂ prompt în fiecare limbă candidată, ca scorurile
                #    să fie comparabile (promptul umflă scorul și, pe limba greșită, e copiat în text).
                #    Cu compare_from_encoder, comparația decodează direct din encoderul de la detecție (fără încă
                #    o trecere de encoder per limbă), iar rezultatul ei nu mai e folosit ca text final.
                multi = [k for k, c in enumerate(cands) if len(c) > 1]
                if enc_cmp:
                    plain, fast = {}, score_languages(model, encs, cands, w)
                    del encs
                else:
                    plain = decode([(k, l) for k in multi for l, _ in cands[k]], use_prompt=False)
                chosen, lang_scores = [], []
                for k, cand in enumerate(cands):
                    if len(cand) == 1:
                        chosen.append(cand[0][0])
                        lang_scores.append({})
                        continue
                    if enc_cmp:
                        sc = {l: fast.get((k, l)) for l, _ in cand}
                    else:
                        sc = {l: avg_logprob(plain[(k, l)][0]) for l, _ in cand}
                    sc = {l: v for l, v in sc.items() if v is not None}
                    chosen.append(pick_language(sc, w.get("language_preference")) if sc else cand[0][0])
                    lang_scores.append(sc)

                # 2) transcrierea finală în limba aleasă, CU promptul de domeniu; dacă rezultatul
                #    e o copie a promptului, păstrăm varianta fără prompt (dacă există).
                #    Fără prompt/hotwords pentru limba aleasă, decodarea de la pasul 1 e deja finală.
                pairs = list(enumerate(chosen))
                for l in set(chosen):
                    glossary.setdefault(l, load_glossary(w, l))
                # refacem doar ce lipsește: segmentele cu o singură limbă, sau prompt / hotwords nefolosite la pasul 1
                redo = [(k, l) for k, l in pairs if (k, l) not in plain or any(glossary[l])]
                final = {kl: plain[kl] for kl in pairs if kl not in redo}
                final.update(decode(redo, use_prompt=True))
                for k, (s, cand) in enumerate(zip(chunk, cands)):
                    lang = chosen[k]
                    res = final[(k, lang)]
                    # hotwords intră și ele în prompt, deci pot fi copiate la fel
                    leak = prompt_leak("".join(x.text for x in res[0]), " ".join(filter(None, glossary[lang])))
                    if leak and (k, lang) in plain:
                        res, leak = plain[(k, lang)], False
                    method = "forced" if forced else ("logprob" if len(cand) > 1 else "detect")
                    rec = record(s, lang, dict(cand)[lang], lang_scores[k], method, *res, w, su)
                    rec["prompt_leak"] = leak
                    fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    langs_count[lang] = langs_count.get(lang, 0) + 1
                fo.flush()
                os.fsync(fo.fileno())
                bar.update(round(sum(s["end"] - s["start"] for s in chunk)))

        return {"model": w["model"], "device": w["device"], "compute_type": w["compute_type"],
                "setup": su, "batched": pipe is not None, "resumed_from": len(done),
                "transcribed": len(todo), "decodes": decodes, "languages": langs_count,
                "initial_prompt": {l: bool(p) for l, (p, _) in glossary.items()},
                "hotwords": {l: bool(h) for l, (_, h) in glossary.items()}}

    run_stage(job_dir, "asr", out, work, done=lambda: len(read_done(out)) >= len(segs))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id")
    ap.add_argument("--model")
    ap.add_argument("--device")
    ap.add_argument("--compute-type")
    ap.add_argument("--language", help="forțează limba (ro/ru/en); implicit: detecție + decodare top-N")
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = load_config(args.config)
    for k in ("model", "device", "compute_type", "language"):
        if getattr(args, k):
            cfg["whisper"][k] = getattr(args, k)
    print(asr(args.job_id, cfg))


if __name__ == "__main__":
    main()
