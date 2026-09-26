"""Metrici comune: numărarea erorilor (S/D/I) pe cuvinte și caractere, agregare,
tabelul de rezultate (evaluation/results.csv -> evaluation/RESULTS.md)."""
import csv
import itertools
from dataclasses import dataclass, field
from datetime import datetime

from rapidfuzz.distance import Levenshtein

from pipeline.common import ROOT

RESULTS_CSV = ROOT / "evaluation" / "results.csv"
RESULTS_MD = ROOT / "evaluation" / "RESULTS.md"

COLUMNS = [
    "date", "gold", "job", "setup", "prompt", "hotwords", "correction", "note",
    "n_ref_words", "wer", "wer_no_digits", "wer_clean", "wer_overlap", "cer", "cer_clean", "cer_overlap",
    "wer_ro", "wer_ru", "term_recall", "term_recall_clean", "term_recall_overlap", "term_precision",
    "lang_acc", "overlap_s", "overlap_words", "decisions", "decisions_in_overlap",
    "etas", "etas_in_overlap", "wer_events",
]
# coloanele afișate în RESULTS.md (restul rămân în CSV)
MD_COLUMNS = [
    ("date", "data"), ("gold", "gold"), ("job", "job"), ("setup", "setup"), ("hotwords", "hw"),
    ("correction", "corr"), ("wer", "WER"), ("wer_clean", "WER clean"), ("wer_overlap", "WER overlap"), ("wer_no_digits", "WER fără cifre"),
    ("cer", "CER"), ("wer_ro", "WER ro"), ("wer_ru", "WER ru"), ("term_recall", "termeni R"),
    ("term_recall_overlap", "termeni R overlap"), ("term_precision", "termeni P"), ("lang_acc", "limbă ok"),
    ("wer_events", "WER decizii/ETA"), ("note", "notă"),
]


@dataclass
class Errors:
    """Erori de editare acumulate: S(ubstituții), D(eleții), I(nserții), N (lungimea referinței)."""
    S: int = 0
    D: int = 0
    I: int = 0
    N: int = 0
    units: int = 0
    extra: dict = field(default_factory=dict)

    def add(self, o):
        self.S += o.S
        self.D += o.D
        self.I += o.I
        self.N += o.N
        self.units += o.units
        return self

    @property
    def rate(self):
        return (self.S + self.D + self.I) / self.N if self.N else None

    def as_dict(self):
        return {"rate": rnd(self.rate), "S": self.S, "D": self.D, "I": self.I, "N": self.N}


def edit_counts(ref, hyp):
    """ref, hyp: secvențe (liste de cuvinte sau șiruri de caractere) -> Errors."""
    e = Errors(N=len(ref), units=1)
    for op in Levenshtein.editops(ref, hyp):
        if op.tag == "replace":
            e.S += 1
        elif op.tag == "delete":
            e.D += 1
        else:
            e.I += 1
    return e


def align(ref, hyp):
    """Alinierea Levenshtein completă: [(tag, i, j)], tag în eq/sub/del/ins; i/j = index sau None."""
    ops = []
    for o in Levenshtein.opcodes(ref, hyp):
        if o.tag == "equal" or o.tag == "replace":
            tag = "eq" if o.tag == "equal" else "sub"
            ops += [(tag, i, j) for i, j in zip(range(o.src_start, o.src_end), range(o.dest_start, o.dest_end))]
        elif o.tag == "delete":
            ops += [("del", i, None) for i in range(o.src_start, o.src_end)]
        else:
            ops += [("ins", None, j) for j in range(o.dest_start, o.dest_end)]
    return ops


def best_order(groups, hyp, max_groups=4, return_order=False):
    """Referința dintr-o regiune cu suprapunere: replicile (grupuri de cuvinte) se pot
    concatena în orice ordine, fiindcă ASR-ul pe un singur canal nu are o ordine „corectă”.
    Alegem ordinea cu cele mai puține erori. -> (ref, Errors) sau, cu return_order, indicii grupurilor"""
    idx = range(len(groups))
    orders = itertools.permutations(idx) if 1 < len(groups) <= max_groups else [tuple(idx)]
    best = None
    for o in orders:
        ref = [w for k in o for w in groups[k]]
        e = edit_counts(ref, hyp)
        if best is None or e.S + e.D + e.I < best[1].S + best[1].D + best[1].I:
            best = (ref, e, o)
    return list(best[2]) if return_order else best[:2]


def rnd(x, k=4):
    return None if x is None else round(x, k)


def append_result(row):
    """Adaugă un rând în results.csv și regenerează RESULTS.md."""
    row = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), **row}
    new = not RESULTS_CSV.exists()
    with open(RESULTS_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in COLUMNS})
    write_md()


def fmt(v, col):
    if v in ("", None):
        return "–"
    if col in ("wer", "wer_no_digits", "wer_clean", "wer_overlap", "cer", "wer_ro", "wer_ru", "term_recall",
               "term_recall_overlap", "term_precision", "lang_acc", "wer_events"):
        return f"{100 * float(v):.1f}%"
    return str(v).replace("|", "/")


def write_md():
    with open(RESULTS_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    lines = [
        "# Rezultate ASR",
        "",
        "Generat automat de `python -m evaluation.evaluate` / `python -m evaluation.moldovan score` "
        "din `evaluation/results.csv` (toate coloanele sunt acolo). Nu editați manual.",
        "",
        "- **WER/CER**: pe textul normalizat (litere mici, fără punctuație, ș/ț cu virgulă, ё→е). "
        "**clean** = fără vorbire suprapusă, **overlap** = regiunile unde vorbesc ≥ 2 persoane.",
        "- **termeni R/P**: recall/precizie pentru termenii din `glossary/terms.tsv` "
        "(același concept, același alfabet, la ±2 s).",
        "- **limbă ok**: segmentele ASR a căror limbă aleasă coincide cu limba din gold.",
        "- **WER decizii/ETA**: WER doar pe intervalele marcate ca decizie sau termen (ETA) în gold.",
        "- `moldovan_sample`: eșantionul din corpusul de română moldovenească (fără termeni medicali, fără suprapuneri). "
        "Referința are numerele în litere, Whisper scrie cifre: **WER fără cifre** exclude segmentele cu cifre în ipoteză.",
        "",
        "| " + " | ".join(h for _, h in MD_COLUMNS) + " |",
        "|" + "|".join("---" for _ in MD_COLUMNS) + "|",
    ]
    for r in rows:
        lines.append("| " + " | ".join(fmt(r.get(c), c) for c, _ in MD_COLUMNS) + " |")
    RESULTS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
