"""Postprocesare: NFC, ş/ţ (sedilă) -> ș/ț (virgulă), scriptul fiecărui cuvânt
(chirilic/latin/mixt), filtrarea halucinațiilor (repetiții, compression_ratio mare,
fraze tipice Whisper, liniște transcrisă), post-corecție opțională după glosar
(pipeline.correct, postprocess.glossary_correction.enabled).

    python -m pipeline.postprocess JOB_ID

Citește asr.jsonl, scrie transcript.json. Segmentele eliminate rămân în fișier
cu "dropped": "<motiv>", ca să poată fi auditate.
"""
import argparse
import json
import re
import unicodedata
import zlib
from collections import Counter

from pipeline.common import jobs_dir, load_config, run_stage
from pipeline.glossary import glossary_hash

CEDILLA = str.maketrans({"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț"})

# fraze pe care Whisper le „aude” în liniște/zgomot (din subtitrările de antrenare)
HALLUCINATIONS = re.compile("|".join([
    r"субтитр", r"корректор\s+[а-я]\.",  # „субтитры” nu apare real într-o ședință
    r"продолжение следует", r"спасибо за просмотр", r"подписывайтесь на( наш)? канал",
    r"thanks? (you )?for watching", r"please subscribe", r"like and subscribe",
    r"mul[țţt]umesc pentru vizionare", r"abona[țţt]i-v[ăa]", r"v[ăa] mul[țţt]umim pentru vizionare",
    r"v[ăa] abona[țţt]i", r"abona[țţt]i-v[ăa] la canal",
    # finaluri de clipuri YouTube, frecvente în datele de antrenare Whisper
    r"da[țţt]i (un )?like", r"l[ăa]sa[țţt]i un comentariu", r"distribui[țţt]i acest (material|video|clip)",
    r"material(ul)? video", r"re[țţt]ele(le)? sociale", r"ставьте лайк", r"подписывайтесь",
    r"don'?t forget to (like|subscribe)",
]), re.IGNORECASE)


def compression_ratio(text):
    b = text.encode("utf-8")  # aceeași formulă ca faster-whisper
    return len(b) / len(zlib.compress(b)) if b else 0.0


def clean(text):
    return unicodedata.normalize("NFC", text).translate(CEDILLA)


def script(word):
    lat = cyr = 0
    for ch in word:
        if ch.isalpha():
            name = unicodedata.name(ch, "")
            cyr += name.startswith("CYRILLIC")
            lat += name.startswith("LATIN")
    if lat and cyr:
        return "mixed"  # ex.: omoglife „пaциент” cu „a” latin
    return "cyrl" if cyr else "latn" if lat else "other"


def norm_token(w):
    return re.sub(r"[^\w]", "", w.lower())


def collapse_repeats(words, word_min, phrase_min):
    """Aceeași unitate (1-8 cuvinte) repetată consecutiv de prea multe ori -> o păstrăm o dată."""
    toks = [norm_token(w["w"]) for w in words]
    removed = 0
    for n in range(1, 9):
        need = word_min if n == 1 else phrase_min
        i = 0
        while i + n * need <= len(words):
            unit = toks[i:i + n]
            k = 1
            while all(unit) and toks[i + k * n:i + (k + 1) * n] == unit:
                k += 1
            if k >= need:
                del words[i + n:i + k * n]
                del toks[i + n:i + k * n]
                removed += (k - 1) * n
            i += 1
    return removed


def process(r, p, corrector=None):
    r = dict(r)
    flags = []
    raw = r["text"]
    r["text"] = clean(raw)
    for w in r["words"]:
        w["w"] = clean(w["w"])
        w["script"] = script(w["w"])

    if collapse_repeats(r["words"], p["repeat_word_min"], p["repeat_phrase_min"]):
        flags.append("repetition")
        r["text"] = "".join(w["w"] for w in r["words"]).strip()

    if corrector and corrector.apply(r):
        flags.append("glossary_corrected")

    if r["text"] != raw:
        r["text_raw"] = raw
    r["scripts"] = dict(Counter(w["script"] for w in r["words"] if w["script"] != "other"))
    if r["scripts"].get("mixed"):
        flags.append("mixed_script_word")
    # cu limba greșită Whisper tinde să traducă, nu să transcrie
    scores = sorted((r.get("lang_scores") or {}).values(), reverse=True)
    method = r.get("lang_method", "detect")
    if len(scores) >= 2:
        if scores[0] - scores[1] < p["min_lang_margin"]:
            flags.append("uncertain_language")
    elif method != "forced" and r["lang_prob"] < p["min_lang_prob"]:
        flags.append("uncertain_language")

    dropped = None
    cr = compression_ratio(r["text"])
    if (r["compression_ratio"] or 0) > p["max_compression_ratio"]:
        flags.append("high_compression")
    if not r["text"]:
        dropped = "empty"
    elif r.get("prompt_leak"):
        dropped = "prompt_leak"  # textul e o copie a initial_prompt, nu ce s-a vorbit
    elif cr > p["max_compression_ratio"]:
        dropped = "compression_ratio"  # tot repetitiv și după colapsarea repetițiilor
    elif (r["no_speech_prob"] or 0) > p["max_no_speech_prob"] and (r["avg_logprob"] or 0) < -1.0:
        dropped = "no_speech"
    elif HALLUCINATIONS.search(r["text"]):
        # scurtă, sau cu >= 2 tipare diferite (ex. „dați like... lăsați un comentariu... distribuiți”): halucinație sigură
        n_patterns = len({m.group(0).lower() for m in HALLUCINATIONS.finditer(r["text"])})
        if len(r["text"].split()) <= 12 or n_patterns >= 2:
            dropped = "hallucination_phrase"
        else:
            flags.append("hallucination_phrase")

    r["flags"] = flags
    r["dropped"] = dropped
    return r


def postprocess(job_id, cfg=None):
    cfg = cfg or load_config()
    job_dir = jobs_dir(cfg) / job_id
    out = job_dir / "transcript.json"

    def work():
        p = cfg["postprocess"]
        gc = p.get("glossary_correction") or {}
        corrector = None
        if gc.get("enabled"):
            from pipeline.correct import Corrector
            corrector = Corrector(gc)
        with open(job_dir / "asr.jsonl", encoding="utf-8") as f:
            recs = sorted((json.loads(l) for l in f if l.strip()), key=lambda r: r["id"])
        res = [process(r, p, corrector) for r in recs]
        tmp = out.with_name(out.name + ".part")
        tmp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out)
        return {
            "n_segments": len(res),
            "dropped": dict(Counter(r["dropped"] for r in res if r["dropped"])),
            "flags": dict(Counter(f for r in res for f in r["flags"])),
            "low_confidence": sum(r["low_confidence"] for r in res if not r["dropped"]),
            "glossary_correction": bool(corrector),
            "glossary_hash": glossary_hash() if corrector else None,
            "corrections": sum(len(r.get("corrections", [])) for r in res),
        }

    run_stage(job_dir, "postprocess", out, work)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id")
    ap.add_argument("--config")
    args = ap.parse_args()
    print(postprocess(args.job_id, load_config(args.config)))


if __name__ == "__main__":
    main()
