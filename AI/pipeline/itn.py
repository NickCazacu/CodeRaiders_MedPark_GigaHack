"""Numerele rostite în română -> cifre (inverse text normalization), conservator.

    „treizeci și opt” -> 38, „nouă spre zece” -> 19, „două sute cincizeci” -> 250,
    „zero virgulă zero opt” -> 0,08, „80 la sută” -> 80%

Se convertesc doar formele fără ambiguitate: zecile, numerele 11-19, sutele, miile și
zecimalele cu „virgulă”. Unitățile singure („doi”, „patru”, „o”, „un”) rămân cuvinte:
„unul mobilizează, doi...” nu e un număr. Nu se atinge nimic peste semne de punctuație.
"""
import re

UNITS = {"zero": 0, "unu": 1, "una": 1, "doi": 2, "două": 2, "doua": 2, "trei": 3, "patru": 4, "cinci": 5,
         "șase": 6, "sase": 6, "șapte": 7, "sapte": 7, "opt": 8, "nouă": 9, "noua": 9}
TEENS = {"zece": 10, "unsprezece": 11, "doisprezece": 12, "douăsprezece": 12, "treisprezece": 13,
         "paisprezece": 14, "cincisprezece": 15, "șaisprezece": 16, "șaptesprezece": 17, "optsprezece": 18,
         "nouăsprezece": 19,
         # vorbire curentă
         "unșpe": 11, "doișpe": 12, "douășpe": 12, "treișpe": 13, "paișpe": 14, "cinșpe": 15, "cincișpe": 15,
         "șaișpe": 16, "șapteșpe": 17, "optșpe": 18, "nouășpe": 19}
TENS = {"douăzeci": 20, "doăzeci": 20, "treizeci": 30, "patruzeci": 40, "cincizeci": 50, "șaizeci": 60,
        "șaptezeci": 70, "optzeci": 80, "nouăzeci": 90}
# „nouă spre zece”: Whisper desparte adesea numerele 11-19 (și „zece” iese uneori „zice”)
SPLIT_TEEN = {"un": 1, "unu": 1, **{k: v for k, v in UNITS.items() if v >= 2}, "pai": 4, "cin": 5, "șai": 6}
ONE = {"o", "un", "una"}

TOKEN = re.compile(r"\w+|\s+|[^\w\s]+")


class _P:
    def __init__(self, words):
        self.w = words  # [(lower, index_in_tokens)]

    def at(self, i):
        return self.w[i][0] if i < len(self.w) else None

    def below100(self, i):
        """-> (valoare, j, sigur) sau None. sigur = conține un cuvânt-număr neambiguu."""
        t = self.at(i)
        if t in TENS:
            v, j = TENS[t], i + 1
            if self.at(j) == "și" and self.at(j + 1) in UNITS and UNITS[self.at(j + 1)] > 0:
                v, j = v + UNITS[self.at(j + 1)], j + 2
            return v, j, True
        if t in TEENS:
            if t == "zece" and i and self.w[i - 1][0] == "spre":
                return None  # „X spre zece” cu X neînțeles: nu știm numărul, rămâne textul
            return TEENS[t], i + 1, True
        if t in SPLIT_TEEN and self.at(i + 1) == "spre" and self.at(i + 2) in ("zece", "zice"):
            return 10 + SPLIT_TEEN[t], i + 3, True
        if t in UNITS:
            return UNITS[t], i + 1, False
        return None

    def below1000(self, i):
        t, n = self.at(i), self.at(i + 1)
        if t in ONE and n == "sută":
            h, j = 100, i + 2
        elif t in UNITS and UNITS[t] >= 2 and n == "sute":
            h, j = UNITS[t] * 100, i + 2
        else:
            return self.below100(i)
        r = self.below100(j)
        if r and (r[2] or r[0] > 0):
            return h + r[0], r[1], True
        return h, j, True

    def integer(self, i):
        t, n = self.at(i), self.at(i + 1)
        if t in ONE and n == "mie":
            th, j = 1000, i + 2
        else:
            r = self.below1000(i)
            if not r:
                return None
            v, j, sure = r
            k = j + 1 if self.at(j) == "de" else j
            if self.at(k) == "mii" and v >= 2:
                th, j = v * 1000, k + 1
            else:
                return r
        r = self.below1000(j)
        if r and (r[2] or r[0] > 0):
            return th + r[0], r[1], True
        return th, j, True

    def number(self, i):
        """-> (text, j) sau None."""
        r = self.integer(i)
        if not r:
            return None
        v, j, sure = r
        if self.at(j) == "virgulă":
            k, zeros = j + 1, ""
            while self.at(k) == "zero" and self.integer(k + 1):  # „zero virgulă zero opt” = 0,08
                zeros, k = zeros + "0", k + 1
            f = self.integer(k)
            if f:
                return f"{v},{zeros}{f[0]}", f[1]
        return (str(v), j) if sure else None


def is_word(t):
    return bool(re.match(r"\w", t))


def normalize_numbers(text):
    toks = TOKEN.findall(text)
    out, i = [], 0
    while i < len(toks):
        if not is_word(toks[i]):
            out.append(toks[i])
            i += 1
            continue
        # grup: cuvinte consecutive separate doar prin spații (numerele nu trec peste punctuație)
        idx = [i]
        while idx[-1] + 2 < len(toks) and toks[idx[-1] + 1].isspace() and is_word(toks[idx[-1] + 2]):
            idx.append(idx[-1] + 2)
        p, w = _P([(toks[k].lower(), k) for k in idx]), 0
        while w < len(idx):
            r = p.number(w)
            text_w, w2 = r if r else (toks[idx[w]], w + 1)
            out.append(text_w)
            if w2 < len(idx):
                out.append(toks[idx[w2 - 1] + 1])  # spațiul original dintre cuvinte
            w = w2
        i = idx[-1] + 1
    return re.sub(r"(\d)\s+la\s+sută\b", r"\1%", "".join(out))
