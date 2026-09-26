"""Post-corecție după glosar: cuvintele apropiate (fuzzy) de rădăcina unui termen medical
sunt rescrise cu rădăcina corectă, păstrând terminația:
„trombospirație” -> „tromboaspirație”, „креатенина” -> „креатинина”.

Apelat din pipeline.postprocess dacă postprocess.glossary_correction.enabled e true.
Fiecare înlocuire e păstrată în segment ("corrections"), iar textul original în "text_raw".

Reguli conservatoare (o corecție greșită e mai rea decât o eroare ASR vizibilă):
- doar termeni dintr-un singur cuvânt, fără abrevieri, cu rădăcina de >= min_len caractere;
- același alfabet (latin/chirilic) și limba segmentului;
- cuvântul nu e deja o formă a vreunui termen;
- scor rapidfuzz >= min_score și un singur candidat clar (diferență >= 3 puncte față de al doilea).
"""
import unicodedata

from rapidfuzz import fuzz

from pipeline.glossary import ENDINGS, MAX_ENDING, load_terms, norm_text, script_of

LANG_SCRIPT = {"ro": "latn", "en": "latn", "ru": "cyrl"}
# terminațiile acceptate după rădăcina corectată (altfel tăietura e greșită: „operaț” + „torul”)
VALID_ENDINGS = {
    "ro": set(ENDINGS["ro"]) | {"ei", "ile", "uri", "urile", "ul", "ului"},
    "ru": set(ENDINGS["ru"]) | {"ов", "ев", "ом", "ам", "ами"},
    "en": set(ENDINGS["en"]),
}


def strip_marks(s):
    """Fără diacritice: „saturație” -> „saturatie” (ASR-ul le omite uneori)."""
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if not unicodedata.combining(ch))


def similarity(a, b):
    """rapidfuzz.ratio, dar o diferență doar de diacritice contează ca potrivire aproape exactă."""
    if strip_marks(a) == strip_marks(b):
        return 99.0
    return fuzz.ratio(a, b)


class Corrector:
    def __init__(self, c, terms=None):
        self.c = c
        terms = terms or load_terms()
        self.known = {}   # lang -> rădăcinile tuturor termenilor dintr-un cuvânt (pentru „deja corect”)
        self.cands = {}   # lang -> {rădăcină: rădăcina e și cuvânt întreg?} eligibile pentru corecție
        for t in terms:
            if t.abbr or len(t.stems) != 1:
                continue
            st = t.stems[0]
            self.known.setdefault(t.lang, set()).add(st)
            if len(st) >= c["min_len"]:
                # „infarct”, „furosemid”: rădăcina e un cuvânt. „intubaț” (din intubație) nu e, deci
                # nu corectăm „intubat” (participiu valid) în „intubaț”.
                bare = norm_text(t.text)[0] == st
                d = self.cands.setdefault(t.lang, {})
                d[st] = d.get(st, False) or bare
        self.cands = {k: list(v.items()) for k, v in self.cands.items()}

    def is_known(self, tok, lang):
        return any(tok.startswith(st) and len(tok) - len(st) <= MAX_ENDING for st in self.known.get(lang, ()))

    def best(self, tok, lang):
        """(rădăcina corectă, terminația păstrată, scor) sau None."""
        scored = []
        ok_endings = VALID_ENDINGS.get(lang, set())
        for st, bare in self.cands.get(lang, ()):
            if abs(len(tok) - len(st)) > MAX_ENDING + 4:
                continue
            # rădăcina se compară cu prefixul cuvântului (±4 caractere: ASR-ul inserează/omite
            # litere); restul trebuie să fie o terminație reală
            cuts = [L for L in range(max(len(st) - 4, 1), min(len(st) + 4, len(tok)) + 1)
                    if (tok[L:] in ok_endings) or (bare and L == len(tok))]
            if not cuts:
                continue
            s, cut = max((similarity(tok[:L], st), L) for L in cuts)
            if s >= self.c["min_score"]:
                scored.append((s, st, tok[cut:]))
        if not scored:
            return None
        scored.sort(reverse=True)
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 3 and scored[0][1] != scored[1][1]:
            return None  # ambiguu
        s, st, ending = scored[0]
        return st, ending, round(s, 1)

    def correct_word(self, word, lang):
        """word: tokenul Whisper, cu spațiul și punctuația lui (ex. " trombospirație,").
        Returnează (cuvântul nou, corecție) sau (word, None)."""
        norm = norm_text(word)
        if len(norm) != 1 or LANG_SCRIPT.get(lang) != script_of(norm[0]):
            return word, None
        tok = norm[0]
        if len(tok) < self.c["min_len"] or self.is_known(tok, lang):
            return word, None
        b = self.best(tok, lang)
        if not b:
            return word, None
        st, ending, score = b
        new_tok = st + ending
        # păstrăm tot ce e în jurul cuvântului (spațiu, punctuație) și majuscula inițială
        lo = word.lower()
        i = lo.find(tok[:1])
        j = i + len(tok)
        if i < 0 or lo[i:j] != tok:
            return word, None  # forma de suprafață diferă de cea normalizată (ș/ş, ё): nu riscăm
        if word[i].isupper():
            new_tok = new_tok[:1].upper() + new_tok[1:]
        return word[:i] + new_tok + word[j:], {"from": word[i:j], "to": new_tok, "score": score}

    def apply(self, r):
        """Corectează un segment din transcript (în loc). Returnează nr. de corecții."""
        fixes = []
        for w in r["words"]:
            new, fix = self.correct_word(w["w"], r["lang"])
            if fix:
                w["w"] = new
                fixes.append({**fix, "start": w["start"]})
        if fixes:
            r["corrections"] = fixes
            r["text"] = "".join(w["w"] for w in r["words"]).strip()
        return len(fixes)
