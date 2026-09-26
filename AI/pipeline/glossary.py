"""Glosarul medical (glossary/terms.tsv): încărcare, normalizarea textului, potrivirea
termenilor (cu flexiune) și generarea hotwords.

    python -m pipeline.glossary hotwords      # scrie glossary/hotwords.<lang>.txt
    python -m pipeline.glossary find "text"   # ce termeni găsește într-un text

Folosit de pipeline.correct (post-corecția) și de evaluation (acuratețea termenilor).
"""
import argparse
import hashlib
import re
import unicodedata
from dataclasses import dataclass

from pipeline.common import ROOT

TERMS_PATH = ROOT / "glossary" / "terms.tsv"
LANGS = ("ro", "ru", "en")
MIN_STEM = 4       # rădăcina rămasă după tăierea terminației
MAX_ENDING = 5     # cât poate avea în plus un cuvânt față de rădăcină ca să fie o formă flexionată

# terminații tăiate de stemmer-ul minimal (cea mai lungă întâi)
ENDINGS = {
    "ro": sorted("urilor ilor elor ului iei ele ii ia ie ea ul le a e i ă u".split(), key=len, reverse=True),
    "ru": sorted(("ями ами ого его ому ему ыми ими ией ия ию ии ей ой ом ем ам ям ах ях ых их "
                  "ое ая ые ий ый ую ы и а я у ю е о ь й").split(), key=len, reverse=True),
    "en": sorted("ies es s".split(), key=len, reverse=True),
}

CEDILLA = str.maketrans({"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț", "ё": "е", "Ё": "Е"})


def norm_text(s):
    """Forma comparabilă: NFC, ș/ț cu virgulă, ё->е, litere mici, fără punctuație.
    Cratima și apostroful devin spațiu („s-a” -> „s a”), identic pentru referință și ipoteză."""
    s = unicodedata.normalize("NFC", s).translate(CEDILLA).lower()
    return re.sub(r"[^\w\s]|_", " ", s).split()


def raw_tokens(s):
    """Ca norm_text, dar păstrează majusculele (pentru abrevieri)."""
    s = unicodedata.normalize("NFC", s).translate(CEDILLA)
    return re.sub(r"[^\w\s]|_", " ", s).split()


def stem(word, lang):
    for e in ENDINGS.get(lang, ()):
        if word.endswith(e) and len(word) - len(e) >= MIN_STEM:
            return word[:-len(e)]
    return word


def script_of(word):
    """cyrl | latn: alfabetul unui cuvânt (după prima literă)."""
    for ch in word:
        if ch.isalpha():
            return "cyrl" if unicodedata.name(ch, "").startswith("CYRILLIC") else "latn"
    return "latn"


@dataclass(frozen=True)
class Term:
    concept: str        # id-ul conceptului (rândul din terms.tsv)
    category: str
    lang: str
    text: str           # forma scrisă, ex. „infarct miocardic”
    abbr: bool          # abreviere: potrivire exactă, cu majuscule
    stems: tuple        # rădăcinile per cuvânt (abreviere: tokenii exacți)
    canonical: bool     # prima variantă a celulei
    hotword: bool

    def match_at(self, norm, raw, i):
        """Lungimea potrivirii la poziția i în lista de tokeni (0 = nu se potrivește)."""
        n = len(self.stems)
        if i + n > len(norm):
            return 0
        if self.abbr:
            return n if tuple(raw[i:i + n]) == self.stems else 0
        for st, tok in zip(self.stems, norm[i:i + n]):
            if not (tok.startswith(st) and len(tok) - len(st) <= MAX_ENDING):
                return 0
        return n


def load_terms(path=TERMS_PATH):
    terms = []
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    header = lines[0].split("\t")
    for line in lines[1:]:
        row = dict(zip(header, (c.strip() for c in line.split("\t"))))
        # hotword: 1 = toate limbile; sau lista limbilor, ex. „ro,en” (rusa e deja aproape de limita de tokeni)
        hw = (row.get("hotword") or "").replace(" ", "")
        hot_langs = set(LANGS) if hw == "1" else set(hw.split(",")) - {"", "0"}
        custom = row.get("stem", "")
        for lang in LANGS:
            hot = lang in hot_langs
            for col, abbr in ((lang, False), (f"abbr_{lang}", True)):
                for k, text in enumerate(v for v in (row.get(col) or "").split("|") if v.strip()):
                    text = text.strip()
                    if abbr:
                        stems = tuple(raw_tokens(text))
                    elif custom and k == 0:
                        stems = tuple(custom.split())
                    else:
                        stems = tuple(stem(t, lang) for t in norm_text(text))
                    terms.append(Term(row["id"], row["category"], lang, text, abbr, stems,
                                      canonical=k == 0, hotword=hot and k == 0 and not abbr))
    # termenii mai lungi întâi: „tomografie computerizată” înaintea lui „tomografie”
    return sorted(set(terms), key=lambda t: (-len(t.stems), t.concept, t.lang, t.text))


def glossary_hash(path=TERMS_PATH):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8] if path.exists() else None


def find_terms(text, terms, langs=None):
    """Aparițiile termenilor într-un text: [(Term, i_token_start, i_token_end)], fără suprapuneri.
    Pozițiile se referă la norm_text(text)."""
    norm, raw = norm_text(text), raw_tokens(text)
    return find_in_tokens(norm, raw, terms, langs)


def find_in_tokens(norm, raw, terms, langs=None):
    out, i = [], 0
    cand = [t for t in terms if not langs or t.lang in langs]
    while i < len(norm):
        for t in cand:
            n = t.match_at(norm, raw, i)
            if n:
                out.append((t, i, i + n))
                i += n
                break
        else:
            i += 1
    return out


def write_hotwords(terms):
    """glossary/hotwords.<lang>.txt din termenii cu hotword=1 (forma canonică)."""
    paths = []
    for lang in LANGS:
        words = sorted({t.text for t in terms if t.hotword and t.lang == lang})
        if not words:
            continue
        p = ROOT / "glossary" / f"hotwords.{lang}.txt"
        p.write_text(f"# generat din terms.tsv (python -m pipeline.glossary hotwords); nu editați manual\n"
                     + "\n".join(words) + "\n", encoding="utf-8")
        est = len(", ".join(words).encode("utf-8")) // 3  # ~tokeni Whisper, grosier
        paths.append((p, len(words), est))
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hotwords")
    f = sub.add_parser("find")
    f.add_argument("text")
    args = ap.parse_args()

    terms = load_terms()
    if args.cmd == "hotwords":
        for p, n, est in write_hotwords(terms):
            print(f"{p.relative_to(ROOT)}: {n} termeni, ~{est} tokeni (limita comună cu initial_prompt: ~224)")
    else:
        norm = norm_text(args.text)
        for t, i, j in find_terms(args.text, terms):
            print(f"{' '.join(norm[i:j])!r:30} -> {t.concept} ({t.lang}, {t.category}{', abrev.' if t.abbr else ''})")


if __name__ == "__main__":
    main()
