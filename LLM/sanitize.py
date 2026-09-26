"""Aduce răspunsul modelului la forma internă: tipuri, vocabulare fixe, citate curate.

Schema din `format` constrânge deja structura; aici prindem restul (valori în afara
vocabularului, prefixe [mm:ss] SPEAKER: rămase în citate, timpi în alt format).
Fiecare corecție e notată în `fixes`, pentru debug.
"""
import re

from rapidfuzz import fuzz, process

from LLM.loader import LABEL_RE, MARK_RE, TS_RE, fmt_ts, ts_seconds
from LLM.schemas import ETA_TYPES, STATUSES

QUOTE_CHARS = "\"'„“”«»‘’ "


def s(v):
    return v.strip() if isinstance(v, str) else ""


def clean_quote(q):
    q = s(q)
    q = TS_RE.sub("", q, count=1)
    q = LABEL_RE.sub("", q, count=1)
    q = MARK_RE.sub("", q)
    return q.strip(QUOTE_CHARS)


TS_RANGE = re.compile(r"\[(\d+:\d{2}(?::\d{2})?)\s*[–—-]\s*(\d+:\d{2}(?::\d{2})?)\]")


def fix_ranges(text):
    """„[07:05–21:17]” -> „[07:05] [21:17]”: fiecare afirmație trimite la un moment din înregistrare."""
    return TS_RANGE.sub(r"[\1] [\2]", text or "")


def norm_ts(ts):
    sec = ts_seconds(ts)
    return fmt_ts(sec) if sec is not None else ""


def fix_enum(value, allowed, default):
    v = s(value)
    if v in allowed:
        return v
    by_fold = {a.casefold(): a for a in allowed}
    if v.casefold() in by_fold:
        return by_fold[v.casefold()]
    best = process.extractOne(v, allowed, scorer=fuzz.ratio) if v else None
    return best[0] if best and best[1] >= 85 else default


def eta(raw_eta, fixes, where):
    e = raw_eta if isinstance(raw_eta, dict) else {}
    typ = fix_enum(e.get("type"), ETA_TYPES, None)
    raw = s(e.get("raw")) or None
    cond = s(e.get("condition")) or None
    if typ is None:
        typ = "vague" if raw else "none"
        fixes.append(f"{where}: eta.type {e.get('type')!r} -> {typ}")
    if typ == "none":
        if raw:
            fixes.append(f"{where}: eta none cu raw {raw!r} -> raw ignorat")
        raw, cond = None, None
    elif typ != "conditional":
        cond = None
    return {"type": typ, "raw": raw, "date": None, "condition": cond, "needs_review": False}


def decision(d, fixes, where):
    if not isinstance(d, dict):
        return None
    text, quote = s(d.get("decision")), clean_quote(d.get("quote"))
    if not text and not quote:
        fixes.append(f"{where}: decizie goală eliminată")
        return None
    if quote != s(d.get("quote")):
        fixes.append(f"{where}: citat curățat {s(d.get('quote'))!r} -> {quote!r}")
    status = fix_enum(d.get("status"), STATUSES, "în discuție")
    if status != s(d.get("status")):
        fixes.append(f"{where}: status {d.get('status')!r} -> {status!r}")
    return {"decision": text, "status": status, "quote": quote, "timestamp": norm_ts(d.get("timestamp")),
            "replaces_previous": bool(d.get("replaces_previous"))}


CYRILLIC = re.compile(r"[Ѐ-ӿ]+(?:[\s-]+[Ѐ-ӿ]+)*")
# câmpurile scrise de model, care trebuie să fie doar în română (quote și eta.raw sunt verbatim)
RO_FIELDS = ("case_key", "topic", "discussion_summary")


def non_romanian_fields(minutes):
    """Referințe (container, cheie) la câmpurile care trebuie să fie în română dar conțin chirilică."""
    refs = []

    def add(obj, key):
        if isinstance(obj[key], str) and CYRILLIC.search(obj[key]):
            refs.append((obj, key))

    add(minutes, "meeting_summary")
    for c in minutes.get("cases", []):
        for f in RO_FIELDS:
            add(c, f)
        for d in c.get("decisions", []):
            add(d, "decision")
        for i in range(len(c.get("open_questions", []))):
            add(c["open_questions"], i)
        if (c.get("eta") or {}).get("condition"):
            add(c["eta"], "condition")
    return refs


def romanian_problems(minutes):
    """Text rusesc (chirilic) în câmpurile care trebuie să fie în română -> listă de probleme.
    Engleza nu se poate detecta sigur fără dicționar; doar chirilica e verificată."""
    out = []

    def check(where, text):
        hits = CYRILLIC.findall(text or "")
        if hits:
            out.append(f"{where}: text neromânesc {hits[:3]}")

    check("meeting_summary", minutes.get("meeting_summary"))
    for i, c in enumerate(minutes.get("cases", []), 1):
        for f in RO_FIELDS:
            check(f"C{i}.{f}", c.get(f))
        for j, d in enumerate(c.get("decisions", []), 1):
            check(f"C{i}.decisions[{j}].decision", d.get("decision"))
        for j, q in enumerate(c.get("open_questions", []), 1):
            check(f"C{i}.open_questions[{j}]", q)
        check(f"C{i}.eta.condition", (c.get("eta") or {}).get("condition"))
    return out


def extract(data):
    """Răspunsul apelului de extracție -> (cases, summary, fixes)."""
    fixes = []
    data = data if isinstance(data, dict) else {}
    cases = []
    for i, c in enumerate(data.get("cases") or []):
        if not isinstance(c, dict):
            continue
        where = f"case[{i}]"
        key = s(c.get("case_key")) or s(c.get("topic"))
        if not key:
            fixes.append(f"{where}: caz fără case_key și topic, eliminat")
            continue
        decs = [x for j, d in enumerate(c.get("decisions") or [])
                if (x := decision(d, fixes, f"{where}.decisions[{j}]"))]
        cases.append({
            "case_key": key,
            "topic": s(c.get("topic")),
            "discussion_summary": fix_ranges(s(c.get("discussion_summary"))),
            "decisions": decs,
            "eta": eta(c.get("eta"), fixes, where),
            "open_questions": [fix_ranges(q) for q in (s(x) for x in c.get("open_questions") or []) if q],
        })
    return cases, fix_ranges(s(data.get("summary"))), fixes
