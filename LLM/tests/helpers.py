"""Utilitare pentru teste: client Ollama fals, verificarea formatului minutei, runner simplu."""
import copy
import json
import re
import tempfile
import traceback
from pathlib import Path

from LLM.config import load_config
from LLM.schemas import ETA_TYPES, STATUSES

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PREFIX = re.compile(r"^\[\d+:\d{2}(?::\d{2})?\]|\b(UNK|SPEAKER_\d+)\b|\[\?\]")


def fixture(name):
    return FIXTURES / name


def cfg(**over):
    c = load_config(env={})
    for dotted, v in over.items():
        sec, key = dotted.split(".")
        c[sec][key] = v
    return c


class FakeClient:
    """Răspunsuri scriptate per etapă. `script[stage]` e o listă (consumată în ordine) de dict-uri
    (răspunsul JSON), None (apel eșuat) sau funcții msgs -> dict/None."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def chat(self, stage, messages, schema, num_ctx=None):
        self.calls.append({"stage": stage, "messages": copy.deepcopy(messages), "num_ctx": num_ctx})
        queue = self.script.get(stage, [])
        item = queue.pop(0) if queue else None
        if callable(item):
            item = item(messages)
        if item is None:
            return {"data": None, "raw": None, "error": "fake: eșuat", "errors": ["fake: eșuat"],
                    "attempts": 2, "meta": {}}
        raw = json.dumps(item, ensure_ascii=False)
        return {"data": item, "raw": raw, "error": None, "errors": [], "attempts": 1,
                "meta": {"prompt_eval_count": None}}


def case(key, decisions=(), eta=None, topic="t", summary="", questions=()):
    return {"case_key": key, "topic": topic, "discussion_summary": summary, "decisions": list(decisions),
            "eta": eta or {"type": "none", "raw": "", "condition": ""}, "open_questions": list(questions)}


def dec(quote, ts, text="d", status="aprobat", replaces=False):
    return {"quote": quote, "timestamp": ts, "decision": text, "status": status, "replaces_previous": replaces}


def tmpdir():
    return Path(tempfile.mkdtemp(prefix="llm_test_"))


def check_minutes(m):
    """Formatul cerut: chei, tipuri, vocabulare, fără prefixe/vorbitori în citate. Returnează lista de probleme."""
    errs = []
    if set(m) != {"meeting_summary", "cases"}:
        errs.append(f"chei top-level: {sorted(m)}")
    if not isinstance(m.get("meeting_summary"), str):
        errs.append("meeting_summary nu e string")
    for c in m.get("cases", []):
        k = c.get("case_key")
        if list(c) != ["case_key", "topic", "discussion_summary", "decisions", "eta", "open_questions"]:
            errs.append(f"{k}: chei {list(c)}")
        for d in c["decisions"]:
            if list(d) != ["decision", "status", "quote", "timestamp", "turn_id", "superseded", "needs_review"]:
                errs.append(f"{k}: chei decizie {list(d)}")
            if d["status"] not in STATUSES:
                errs.append(f"{k}: status {d['status']!r}")
            if PREFIX.search(d["quote"]):
                errs.append(f"{k}: citat cu prefix/vorbitor/[?]: {d['quote']!r}")
            if not re.fullmatch(r"\d{2,}:\d{2}|", d["timestamp"]):
                errs.append(f"{k}: timestamp {d['timestamp']!r}")
            if d["needs_review"] is not False:
                errs.append(f"{k}: needs_review setat")
        active = [d for d in c["decisions"] if not d["superseded"]]
        if c["decisions"] and c["decisions"][-1]["superseded"]:
            errs.append(f"{k}: ultima decizie e superseded")
        if c["decisions"] and not active:
            errs.append(f"{k}: nicio decizie activă")
        e = c["eta"]
        if list(e) != ["type", "raw", "date", "condition", "needs_review"]:
            errs.append(f"{k}: chei eta {list(e)}")
        if e["type"] not in ETA_TYPES:
            errs.append(f"{k}: eta.type {e['type']!r}")
        if e["date"] is not None or e["needs_review"] is not False:
            errs.append(f"{k}: eta.date/needs_review setate")
        if e["type"] != "conditional" and e["condition"] is not None:
            errs.append(f"{k}: condition pe eta {e['type']}")
        for field in ("case_key", "topic", "discussion_summary"):
            if re.search(r"\b(UNK|SPEAKER_\d+)\b", c[field]):
                errs.append(f"{k}: vorbitor în {field}")
    if re.search(r"\b(UNK|SPEAKER_\d+)\b", m.get("meeting_summary", "")):
        errs.append("vorbitor în meeting_summary")
    return errs


def run(ns):
    tests = [(n, f) for n, f in ns.items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"OK    {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} teste trecute")
    return failed
