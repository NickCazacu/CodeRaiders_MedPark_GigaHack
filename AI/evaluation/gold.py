"""Transcrierea de referință (gold), adnotată în Audacity ca piste de etichete (labels).

    python -m evaluation.gold draft JOB_ID NAME   # schiță din ASR -> evaluation/gold/NAME/utterances.txt
    python -m evaluation.gold build NAME          # etichetele -> evaluation/gold/NAME/gold.json + statistici
    python -m evaluation.gold from-reference tests/reference/medpark.txt NAME
                                                  # referința existentă (formatul tests/evaluate.py) -> etichete + gold.json

Trei fișiere de etichete (Audacity: File > Export > Export Labels; format „start<TAB>end<TAB>text”):
  utterances.txt  o replică per etichetă:  S1|ro|text exact cum s-a spus
  overlap.txt     regiunile unde vorbesc >= 2 persoane (textul etichetei nu contează)
  events.txt      DECISION: ...  /  ETA: ...  (opțional etichete REGION = intervalele evaluate; implicit tot gold-ul)
Regulile de adnotare: evaluation/README.md.
"""
import argparse
import json
import re
import sys
import unicodedata

from pipeline.common import ROOT, jobs_dir, load_config
from pipeline.glossary import find_terms, load_terms, norm_text

GOLD_DIR = ROOT / "evaluation" / "gold"
LANGS = {"ro", "ru", "en", "mix"}
EVENT_TYPES = {"decision": "decision", "decizie": "decision", "решение": "decision",
               "eta": "eta", "termen": "eta", "срок": "eta"}
MIN_OVERLAP_S = 0.1


def read_labels(path):
    """Etichete Audacity -> [(start, end, text)]. Liniile de frecvență („\\t...”) sunt ignorate."""
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.startswith("\\") or line.startswith("#"):
            continue
        parts = line.split("\t", 2)
        try:
            s, e = float(parts[0].replace(",", ".")), float(parts[1].replace(",", "."))
        except (ValueError, IndexError):
            raise SystemExit(f"{path.name}:{n}: nu e o etichetă Audacity (start<TAB>end<TAB>text): {line!r}")
        out.append((s, e, parts[2].strip() if len(parts) > 2 else ""))
    return out


def write_labels(path, labels):
    path.write_text("".join(f"{s:.3f}\t{e:.3f}\t{t}\n" for s, e, t in labels), encoding="utf-8")


def ref_text(text):
    """Textul folosit ca referință: fără marcaje între paranteze drepte ([neclar], [râs], ...)."""
    return re.sub(r"\[[^\]]*\]", " ", text).strip()


def merge(intervals, min_len=0.0):
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [[round(s, 3), round(e, 3)] for s, e in out if e - s >= min_len]


def computed_overlaps(utts):
    """Intersecțiile replicilor unor vorbitori diferiți."""
    res = []
    for i, a in enumerate(utts):
        for b in utts[i + 1:]:
            if b["start"] >= a["end"]:
                break
            if a["speaker"] != b["speaker"]:
                s, e = max(a["start"], b["start"]), min(a["end"], b["end"])
                if e > s:
                    res.append((s, e))
    return res


def build(name):
    d = GOLD_DIR / name
    errors = []
    utts = []
    for s, e, label in read_labels(d / "utterances.txt"):
        parts = label.split("|", 2)
        if len(parts) != 3:
            errors.append(f"[{s:.2f}] eticheta trebuie să fie VORBITOR|limbă|text: {label!r}")
            continue
        spk, lang, text = (p.strip() for p in parts)
        if lang not in LANGS:
            errors.append(f"[{s:.2f}] limba {lang!r} nu e una din {sorted(LANGS)}")
        if e <= s:
            errors.append(f"[{s:.2f}] eticheta are durata {e - s:.2f} s")
        if not ref_text(text):
            errors.append(f"[{s:.2f}] replică fără text (pentru vorbire neinteligibilă scrieți [neclar])")
        utts.append({"start": round(s, 3), "end": round(e, 3), "speaker": spk, "lang": lang, "text": text})
    if errors:
        raise SystemExit("Erori în utterances.txt:\n  " + "\n  ".join(errors))
    if not utts:
        raise SystemExit(f"{d / 'utterances.txt'} lipsește sau e gol")
    utts.sort(key=lambda u: (u["start"], u["end"]))
    for i, u in enumerate(utts):
        u["id"] = i

    explicit = [(s, e) for s, e, _ in read_labels(d / "overlap.txt")]
    overlaps = merge(explicit + computed_overlaps(utts), MIN_OVERLAP_S)

    events, regions = [], []
    for s, e, label in read_labels(d / "events.txt"):
        if label.strip().upper() == "REGION":
            regions.append([round(s, 3), round(e, 3)])
            continue
        m = re.match(r"\s*([^\s:]+)\s*:\s*(.*)", label)
        typ = EVENT_TYPES.get(m.group(1).lower()) if m else None
        if not typ:
            raise SystemExit(f"events.txt [{s:.2f}]: eticheta trebuie să înceapă cu DECISION: sau ETA: ({label!r})")
        events.append({"type": typ, "start": round(s, 3), "end": round(max(e, s + 0.5), 3), "text": m.group(2)})

    regions = merge(regions) or [[utts[0]["start"], max(u["end"] for u in utts)]]
    gold = {"name": name, "regions": regions, "utterances": utts, "overlaps": overlaps,
            "overlaps_explicit": len(explicit), "events": events}
    (d / "gold.json").write_text(json.dumps(gold, ensure_ascii=False, indent=1), encoding="utf-8")
    return gold


def in_any(s, e, intervals):
    return any(s < b and e > a for a, b in intervals)


def stats(gold):
    utts, ov = gold["utterances"], gold["overlaps"]
    words = sum(len(norm_text(ref_text(u["text"]))) for u in utts)
    ov_s = sum(e - s for s, e in ov)
    span = sum(e - s for s, e in gold["regions"])
    terms = load_terms()
    n_terms = sum(len(find_terms(ref_text(u["text"]), terms)) for u in utts)
    by_lang = {}
    for u in utts:
        by_lang[u["lang"]] = by_lang.get(u["lang"], 0) + 1
    ev = gold["events"]
    return {
        "intervale": ", ".join(f"{s:.1f}–{e:.1f} s" for s, e in gold["regions"]) + f" ({span / 60:.1f} min)",
        "replici": len(utts), "vorbitori": sorted({u["speaker"] for u in utts}), "limbi": by_lang,
        "cuvinte": words, "termeni din glosar": n_terms,
        "suprapuneri": f"{len(ov)} regiuni, {ov_s:.1f} s ({100 * ov_s / span:.1f}%), "
                       f"{gold['overlaps_explicit']} marcate manual",
        "decizii": f"{sum(e['type'] == 'decision' for e in ev)} "
                   f"(în suprapuneri: {sum(e['type'] == 'decision' and in_any(e['start'], e['end'], ov) for e in ev)})",
        "ETA": f"{sum(e['type'] == 'eta' for e in ev)} "
               f"(în suprapuneri: {sum(e['type'] == 'eta' and in_any(e['start'], e['end'], ov) for e in ev)})",
    }


def parse_time(s):
    """„1:32”, „0:01:32”, „92.5” -> secunde"""
    t = 0.0
    for part in s.strip().split(":"):
        t = t * 60 + float(part.replace(",", "."))
    return t


def from_reference(path, name, force=False):
    """Formatul tests/reference/*.txt (tests/evaluate.py):
        # fragment 0:00-1:32
        [Speaker1] 0:00 - 0:15: text
    -> etichete Audacity (limba ghicită după alfabet: majoritar chirilic = ru, altfel ro; de verificat)
    + REGION pentru fiecare fragment, apoi build()."""
    frag = re.compile(r"^#\s*fragment\s+([\d:.,]+)\s*-\s*([\d:.,]+)")
    line_re = re.compile(r"^\[([^\]]+)\]\s*([\d:.,]+)\s*-\s*([\d:.,]+)\s*:\s*(.*)$")
    utts, regions = [], []
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if m := frag.match(line):
            regions.append((parse_time(m[1]), parse_time(m[2]), "REGION"))
        elif m := line_re.match(line):
            text = m[4].strip()
            letters = [c for c in text if c.isalpha()]
            cyr = sum("CYRILLIC" in unicodedata.name(c, "") for c in letters)
            lang = "ru" if letters and cyr / len(letters) > 0.5 else "ro"
            spk = re.sub(r"\s+", "", m[1])
            utts.append((parse_time(m[2]), parse_time(m[3]), f"{spk}|{lang}|{text}"))
    if not utts:
        raise SystemExit(f"{path}: nicio replică în formatul „[Vorbitor] m:ss - m:ss: text”")
    d = GOLD_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    if (d / "utterances.txt").exists() and not force:
        raise SystemExit(f"{d / 'utterances.txt'} există deja. --force ca să-l suprascrii.")
    write_labels(d / "utterances.txt", utts)
    write_labels(d / "events.txt", regions)
    if not (d / "overlap.txt").exists():
        (d / "overlap.txt").write_text("", encoding="utf-8")
    return build(name)


def draft(job_id, name, force=False):
    """Schiță de utterances.txt din transcript.json (sau segments.json), de corectat ascultând."""
    job = jobs_dir(load_config()) / job_id
    d = GOLD_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    out = d / "utterances.txt"
    if out.exists() and not force:
        raise SystemExit(f"{out} există deja (munca adnotatorului). --force ca să-l suprascrii.")
    tr = job / "transcript.json"
    if tr.exists():
        segs = [s for s in json.loads(tr.read_text(encoding="utf-8")) if not s["dropped"]]
        labels = [(s["start"], s["end"], f"{s['speaker']}|{s['lang']}|{s['text']}") for s in segs]
    else:
        segs = json.loads((job / "segments.json").read_text(encoding="utf-8"))
        labels = [(s["start"], s["end"], f"{s['speaker']}||") for s in segs]
    write_labels(out, labels)
    for extra in ("overlap.txt", "events.txt"):
        if not (d / extra).exists():
            (d / extra).write_text("", encoding="utf-8")
    return out, len(labels)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("draft")
    a.add_argument("job_id")
    a.add_argument("name")
    a.add_argument("--force", action="store_true")
    b = sub.add_parser("build")
    b.add_argument("name")
    c = sub.add_parser("from-reference")
    c.add_argument("path")
    c.add_argument("name")
    c.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.cmd == "draft":
        out, n = draft(args.job_id, args.name, args.force)
        print(f"{out}: {n} etichete. Import în Audacity: File > Import > Labels (audio: jobs/{args.job_id}/audio_16k.wav)")
    else:
        gold = build(args.name) if args.cmd == "build" else from_reference(args.path, args.name, args.force)
        for k, v in stats(gold).items():
            print(f"{k:20s} {v}")
        print(f"-> {GOLD_DIR / args.name / 'gold.json'}")


if __name__ == "__main__":
    sys.exit(main())
