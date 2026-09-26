"""Citat -> turn_id: timestamp + potrivire fuzzy pe replicile ferestrei.

Fără potrivire sigură: turn_id = None (validarea îl marchează / elimină).
"""
import re
import unicodedata

from rapidfuzz import fuzz

from LLM.loader import ts_seconds

CEDILLA = str.maketrans({"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț"})


def norm(text):
    t = unicodedata.normalize("NFC", text or "").translate(CEDILLA).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def score(q, text):
    # citatul e de regulă o parte din replică => partial_ratio; dacă e mult mai lung decât
    # replica (model care a lipit două replici), partial_ratio ar da 100 pe bucata comună
    if len(q) > 1.3 * len(text):
        return fuzz.ratio(q, text)
    return fuzz.partial_ratio(q, text)


def resolve(quote, timestamp, turns, qcfg):
    """turns: replicile ferestrei (Turn). Returnează dict cu turn_id (sau None) și detaliile potrivirii."""
    q = norm(quote)
    want = ts_seconds(timestamp)
    res = {"quote": quote, "timestamp": timestamp, "turn_id": None, "score": 0, "ts_match": False,
           "exact": False, "method": None}
    if not q:
        res["method"] = "empty_quote"
        return res

    scored = []
    for t in turns:
        sc = score(q, norm(t.text))
        ts_ok = want is not None and ts_seconds(t.ts) == want
        scored.append((sc + (10 if ts_ok else 0), sc, ts_ok, t))
    scored.sort(key=lambda x: -x[0])

    short = len(q) < qcfg["short_quote_chars"]
    for _, sc, ts_ok, t in scored:
        if short:
            ok = ts_ok and sc >= 90
        else:
            ok = sc >= qcfg["min_score"] or (ts_ok and sc >= qcfg["min_score_same_ts"])
        if ok:
            res.update(turn_id=t.id, score=round(sc, 1), ts_match=ts_ok, exact=q in norm(t.text),
                       method="ts+text" if ts_ok else "text", turn_ts=t.ts)
            return res
    if scored:
        _, sc, ts_ok, t = scored[0]
        res.update(score=round(sc, 1), method="no_match", best_turn=t.id)
    return res
