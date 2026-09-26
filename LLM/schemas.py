"""Scheme JSON trimise la Ollama în `format` (ieșire constrânsă) + vocabularele fixe.

Modelul completează doar ce ține de text; turn_id, superseded, needs_review, eta.date
sunt puse de cod (vezi merge.py).
"""
STATUSES = ["aprobat", "respins", "amânat", "necesită investigații suplimentare", "în discuție"]
ETA_TYPES = ["absolute", "relative", "duration", "conditional", "vague", "recurring", "none"]
# constatările clinice ale unui caz (tot ce nu e decizie, dar contează în procesul-verbal)
FACT_CATEGORIES = ["diagnostic", "istoric", "analize", "imagistică", "microbiologie", "tratament",
                   "procedură", "monitorizare", "evoluție"]

_str = {"type": "string"}

FACT = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": FACT_CATEGORIES},
        "fact": _str,        # în română, cu valorile/dozele exact cum s-au spus
        "timestamp": _str,
    },
    "required": ["category", "fact", "timestamp"],
}

DECISION = {
    "type": "object",
    "properties": {
        "quote": _str,       # întâi citatul: ancorează decizia în text
        "timestamp": _str,
        "decision": _str,
        "status": {"type": "string", "enum": STATUSES},
        "replaces_previous": {"type": "boolean"},
    },
    "required": ["quote", "timestamp", "decision", "status", "replaces_previous"],
}

ETA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ETA_TYPES},
        "raw": _str,         # "" = lipsește
        "condition": _str,
    },
    "required": ["type", "raw", "condition"],
}

CASE = {
    "type": "object",
    "properties": {
        "case_key": _str,
        "topic": _str,
        "discussion_summary": _str,
        "facts": {"type": "array", "items": FACT},   # înaintea deciziilor: întâi ce s-a constatat
        "decisions": {"type": "array", "items": DECISION},
        "eta": ETA,
        "open_questions": {"type": "array", "items": _str},
    },
    "required": ["case_key", "topic", "discussion_summary", "facts", "decisions", "eta", "open_questions"],
}

# o fereastră, sau toată ședința când n_windows == 1 (summary = meeting_summary)
EXTRACT = {
    "type": "object",
    "properties": {"cases": {"type": "array", "items": CASE}, "summary": _str},
    "required": ["cases", "summary"],
}

def extract_schema(max_cases=None, max_decisions=None, max_facts=None):
    """EXTRACT cu maxItems: o buclă a modelului (același caz repetat) se oprește la limită."""
    case = {**CASE, "properties": {**CASE["properties"]}}
    if max_decisions:
        case["properties"]["decisions"] = {**CASE["properties"]["decisions"], "maxItems": max_decisions}
    if max_facts:
        case["properties"]["facts"] = {**CASE["properties"]["facts"], "maxItems": max_facts}
    cases = {"type": "array", "items": case}
    if max_cases:
        cases["maxItems"] = max_cases
    return {**EXTRACT, "properties": {**EXTRACT["properties"], "cases": cases}}


# împărțirea ședinței pe pacienți (primul apel în modul „segment”)
SEGMENTS = {
    "type": "object",
    "properties": {"patients": {"type": "array", "items": {
        "type": "object",
        "properties": {"start": _str, "label": _str, "cue": _str},
        "required": ["start", "label", "cue"],
    }}},
    "required": ["patients"],
}

# a doua trecere pe fragmentul unui pacient: doar constatările care lipsesc
COMPLETE = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": FACT, "maxItems": 15}},
    "required": ["facts"],
}

TRANSLATE = {
    "type": "object",
    "properties": {"texts": {"type": "array", "items": _str}},
    "required": ["texts"],
}

SUPERSEDE = {
    "type": "object",
    "properties": {"superseded": {"type": "array", "items": {"type": "integer"}}},
    "required": ["superseded"],
}

SAME_CASE = {
    "type": "object",
    "properties": {"same": {"type": "boolean"}},
    "required": ["same"],
}

FINAL = {
    "type": "object",
    "properties": {"meeting_summary": _str, "case_order": {"type": "array", "items": {"type": "integer"}}},
    "required": ["meeting_summary", "case_order"],
}
