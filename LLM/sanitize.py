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
            "discussion_summary": s(c.get("discussion_summary")),
            "decisions": decs,
            "eta": eta(c.get("eta"), fixes, where),
            "open_questions": [q for q in (s(x) for x in c.get("open_questions") or []) if q],
        })
    return cases, s(data.get("summary")), fixes
