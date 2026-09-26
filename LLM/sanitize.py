"""Aduce răspunsul modelului la forma internă: tipuri, vocabulare fixe, citate curate.

Schema din `format` constrânge deja structura; aici prindem restul (valori în afara
vocabularului, prefixe [mm:ss] SPEAKER: rămase în citate, timpi în alt format).
Fiecare corecție e notată în `fixes`, pentru debug.
"""
import re

from rapidfuzz import fuzz, process

from LLM.loader import LABEL_RE, MARK_RE, TS_RE, fmt_ts, ts_seconds
from LLM.schemas import ETA_TYPES, FACT_CATEGORIES, STATUSES

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


# „patul nou” fără număr = „patul nouă” (9): transcrierea pierde „ă” final; un pat nu e „nou”
BED_NINE = re.compile(r"\b(pat(?:ul)?|patului)\s+nou\b(?!ă)", re.IGNORECASE)


def fix_bed_numbers(text, fixes, where):
    new = BED_NINE.sub(lambda m: f"{m.group(1)} 9", text)
    if new != text:
        fixes.append(f"{where}: {text!r} -> {new!r} („patul nou” = patul nouă)")
    return new


def fact(f, fixes, where):
    if not isinstance(f, dict):
        return None
    text = fix_ranges(s(f.get("fact")))
    if not text:
        fixes.append(f"{where}: constatare goală eliminată")
        return None
    cat = fix_enum(f.get("category"), FACT_CATEGORIES, "evoluție")
    if cat != s(f.get("category")):
        fixes.append(f"{where}: categorie {f.get('category')!r} -> {cat!r}")
    return {"category": cat, "fact": text, "timestamp": norm_ts(f.get("timestamp"))}


# unitate scrisă după un număr -> cum ar apărea în vorbire (în linia transcrisă)
# unitatea + eventualele „/kg/min”, „/zi”, „/L”: toată expresia dispare dacă unitatea nu a fost spusă
UNIT = re.compile(r"(?<=\d)(\s*)(mg|µg|μg|mcg|ml|mL|mmol|µmol|μmol|umol|mmHg|g|%|UI)((?:/[^\W\d_]+)*)(?!\w)")
UNIT_SPOKEN = {"mg": ("mg", "miligram"), "µg": ("µg", "mcg", "microgram", "gamma"), "μg": ("μg", "mcg", "microgram"),
               "mcg": ("mcg", "µg", "microgram"), "ml": ("ml", "mililit"), "g": ("g", "gram"),
               "mmol": ("mmol", "milimol"), "µmol": ("mol", "micromol"), "μmol": ("mol", "micromol"),
               "umol": ("mol", "micromol"), "mmhg": ("mmhg", "milimetri", "mm"), "%": ("%", "la sută", "procent"),
               "ui": ("ui", "unități")}


def strip_unspoken_units(text, source, fixes, where):
    """Scoate unitățile de măsură pe care modelul le-a adăugat, dar care nu apar în linia transcrisă
    („amicacină 1500” -> nu „1500 mg”). Fără linie sursă, textul rămâne cum e."""
    if not source:
        return text
    src = source.lower()

    def spoken(x):
        if x == "%":
            return "%" in src
        # abrevierile scurte ca cuvânt întreg („g” nu se potrivește în „gradul”), rădăcinile ca început de cuvânt
        tail = r"(?![^\W\d_])" if len(x) <= 4 else ""
        return re.search(r"(?<![^\W\d_])" + re.escape(x) + tail, src) is not None

    def repl(m):
        unit = m.group(2) + m.group(3)
        key = m.group(2) if m.group(2) in ("µmol", "μmol", "μg") else m.group(2).lower()
        if any(spoken(x) for x in UNIT_SPOKEN.get(key, (key,))):
            return m.group(0)
        fixes.append(f"{where}: unitate nespusă eliminată: {unit!r}")
        return ""

    return UNIT.sub(repl, text)


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
        for x in c.get("facts", []):
            add(x, "fact")
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
        for j, x in enumerate(c.get("facts", []), 1):
            check(f"C{i}.facts[{j}]", x.get("fact"))
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
        key = fix_bed_numbers(s(c.get("case_key")) or s(c.get("topic")), fixes, f"{where}.case_key")
        if not key:
            fixes.append(f"{where}: caz fără case_key și topic, eliminat")
            continue
        decs = [x for j, d in enumerate(c.get("decisions") or [])
                if (x := decision(d, fixes, f"{where}.decisions[{j}]"))]
        facts = [x for j, f in enumerate(c.get("facts") or [])
                 if (x := fact(f, fixes, f"{where}.facts[{j}]"))]
        cases.append({
            "case_key": key,
            "topic": s(c.get("topic")),
            "discussion_summary": fix_ranges(s(c.get("discussion_summary"))),
            "facts": facts,
            "decisions": decs,
            "eta": eta(c.get("eta"), fixes, where),
            "open_questions": [fix_ranges(q) for q in (s(x) for x in c.get("open_questions") or []) if q],
        })
    return cases, fix_ranges(s(data.get("summary"))), fixes
