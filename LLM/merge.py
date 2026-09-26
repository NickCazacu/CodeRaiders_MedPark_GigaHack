"""Reduce: cazurile din ferestre -> cazurile ședinței.

- elimină deciziile din replicile de suprapunere deja extrase în fereastra anterioară;
- grupează cazurile după case_key (rapidfuzz; pentru perechi ambigue întreabă LLM-ul);
- ultima decizie și ultimul ETA câștigă; deciziile schimbate rămân cu superseded: true
  (supersede: llm = un apel scurt per caz cu >= 2 decizii; flag = doar replaces_previous; all).

Deciziile primite au deja turn_id (sau None), `_t` (secunde, pentru ordine) și `_window`.
"""
import re
import unicodedata

from rapidfuzz import fuzz

from LLM.loader import ts_seconds
from LLM.quotes import norm

TS_REF = re.compile(r"\[(\d+:\d{2}(?::\d{2})?)\]")


def norm_key(key):
    t = unicodedata.normalize("NFKD", key or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def key_score(a, b):
    na, nb = norm_key(a), norm_key(b)
    if na == nb:
        return 100.0
    # „Pacient 48” vs „Pacient 84”: text aproape identic, pacienți diferiți
    da, db = set(re.findall(r"\d+", na)), set(re.findall(r"\d+", nb))
    if da and db and da != db:
        return 0.0
    if da and da == db:
        # același număr: „Pacient 48” ~ „Pacient 48, cardiologie” (submulțime de cuvinte e suficientă)
        return float(fuzz.token_set_ratio(na, nb))
    # fără număr comun, doar textul contează: strict, ca perechile incerte să ajungă la LLM
    return float(fuzz.token_sort_ratio(na, nb))


def ts_refs(text):
    return [ts_seconds(x) for x in TS_REF.findall(text or "")]


class Merger:
    def __init__(self, cfg, same_case=None):
        """same_case(a, b) -> True / False / None (None = apel eșuat => cazuri diferite)."""
        self.m = cfg["merge"]
        self.same_case = same_case
        self.cases = []          # acumulatoare, în ordinea primei apariții
        self.seen_turns = set()  # turn_id-urile deciziilor deja păstrate
        self.events = []         # tot ce s-a hotărât aici, pentru llm_debug/merge.json
        self._asked = {}

    # ---------- fereastra ----------
    def add_window(self, window_index, overlap_ids, overlap_end_s, cases):
        """overlap_ids: replicile de context ale ferestrei; overlap_end_s: timpul ultimei replici de context."""
        overlap_ids = set(overlap_ids)
        kept_turns = set()
        for case in cases:
            before = len(case["decisions"])
            case["decisions"] = [d for d in case["decisions"] if self._keep(d, window_index, overlap_ids, case)]
            if not case["decisions"] and overlap_ids and self._only_context(case, overlap_end_s):
                self.events.append({"event": "drop_context_case", "window": window_index,
                                    "case_key": case["case_key"], "dropped_decisions": before})
                continue
            acc = self._find(case, window_index)
            if acc is None:
                acc = {"case_key": case["case_key"], "topics": [], "summaries": [], "decisions": [],
                       "etas": [], "open_questions": [], "windows": []}
                self.cases.append(acc)
                self.events.append({"event": "new_case", "window": window_index, "case_key": case["case_key"]})
            self._absorb(acc, case, window_index)
            kept_turns |= {d["turn_id"] for d in case["decisions"] if d["turn_id"] is not None}
        self.seen_turns |= kept_turns

    def _keep(self, d, w, overlap_ids, case):
        tid = d["turn_id"]
        if tid is not None and tid in overlap_ids and tid in self.seen_turns:
            self.events.append({"event": "drop_overlap_duplicate", "window": w, "case_key": case["case_key"],
                                "turn_id": tid, "quote": d["quote"]})
            return False
        if tid is None and overlap_ids:
            # fără turn_id nu știm unde e; dacă același citat există deja, e tot un duplicat
            q = norm(d["quote"])
            for acc in self.cases:
                if any(q and fuzz.ratio(q, norm(x["quote"])) >= 90 for x in acc["decisions"]):
                    self.events.append({"event": "drop_duplicate_quote", "window": w,
                                        "case_key": case["case_key"], "quote": d["quote"]})
                    return False
        return True

    @staticmethod
    def _only_context(case, overlap_end_s):
        # cazul trimite doar la liniile de context => l-am văzut deja în fereastra anterioară
        refs = [r for r in ts_refs(case["discussion_summary"]) if r is not None]
        return bool(refs) and all(r <= overlap_end_s for r in refs)

    # ---------- potrivirea cazurilor ----------
    def _find(self, case, w):
        scored = sorted(((key_score(case["case_key"], acc["case_key"]), i) for i, acc in enumerate(self.cases)),
                        reverse=True)
        asked = 0
        for sc, i in scored:
            acc = self.cases[i]
            if sc >= self.m["same_score"]:
                if sc < 100:
                    self.events.append({"event": "match_fuzzy", "window": w, "case_key": case["case_key"],
                                        "into": acc["case_key"], "score": sc})
                return acc
            if sc <= self.m["diff_score"] or asked >= 2 or not self.same_case:
                break
            asked += 1
            pair = (norm_key(case["case_key"]), norm_key(acc["case_key"]))
            if pair not in self._asked:
                self._asked[pair] = self.same_case(case, self._view(acc))
            ans = self._asked[pair]
            self.events.append({"event": "ask_same_case", "window": w, "case_key": case["case_key"],
                                "candidate": acc["case_key"], "score": sc, "answer": ans})
            if ans:
                return acc
        return None

    @staticmethod
    def _view(acc):
        return {"case_key": acc["case_key"], "topic": next((t for t in reversed(acc["topics"]) if t), ""),
                "discussion_summary": " ".join(acc["summaries"])}

    def _absorb(self, acc, case, w):
        if case["topic"]:
            acc["topics"].append(case["topic"])
        if case["discussion_summary"] and case["discussion_summary"] not in acc["summaries"]:
            acc["summaries"].append(case["discussion_summary"])
        have = {d["turn_id"] for d in acc["decisions"] if d["turn_id"] is not None}
        # întâi deciziile cu replică găsită, apoi cele fără: o decizie fără replică identică cu una
        # ancorată (modelul a reformulat citatul) e un duplicat
        for d in sorted(case["decisions"], key=lambda x: x["turn_id"] is None):
            if d["turn_id"] is not None and d["turn_id"] in have:
                self.events.append({"event": "drop_duplicate_turn", "window": w, "case_key": acc["case_key"],
                                    "turn_id": d["turn_id"]})
                continue
            if d["turn_id"] is None:
                twin = next((x for x in acc["decisions"] if x["turn_id"] is not None and
                             fuzz.token_set_ratio(norm(d["decision"]), norm(x["decision"])) >= 85), None)
                if twin:
                    self.events.append({"event": "drop_unresolved_duplicate", "window": w,
                                        "case_key": acc["case_key"], "decision": d["decision"],
                                        "same_as_turn": twin["turn_id"]})
                    continue
            have.add(d["turn_id"])
            acc["decisions"].append(d)
        acc["etas"].append((w, case["eta"]))
        for q in case["open_questions"]:
            if not any(fuzz.ratio(norm(q), norm(x)) >= 90 for x in acc["open_questions"]):
                acc["open_questions"].append(q)
        acc["windows"].append(w)

    # ---------- antet pentru fereastra următoare ----------
    def known_cases(self, limit):
        out = []
        for acc in self.cases[-limit:]:
            decs = self._ordered(acc)
            last = decs[-1] if decs else None
            out.append(f"{acc['case_key']} — {last['status']}: {last['decision']}" if last
                       else f"{acc['case_key']} — discutat, fără decizie")
        return out

    # ---------- rezultat ----------
    @staticmethod
    def _ordered(acc):
        return sorted(acc["decisions"], key=lambda d: (d["_t"], d["_window"], d["_order"]))

    def _superseded(self, acc, decs, supersede):
        n = len(decs)
        mode = self.m["supersede"]
        if mode == "all":
            return [i < n - 1 for i in range(n)]
        if mode == "llm" and supersede and n >= 2:
            # verificare separată, pe toate deciziile cazului: modelul de extracție vede o singură
            # fereastră și marchează rar (sau greșit) replaces_previous
            idx = supersede(acc["case_key"], decs)
            self.events.append({"event": "supersede_check", "case_key": acc["case_key"],
                                "answer": None if idx is None else sorted(idx)})
            if idx is not None:
                return [i in idx and i < n - 1 for i in range(n)]
        sup = [False] * n  # "flag", sau "llm" fără răspuns
        for i, d in enumerate(decs):
            if d["replaces_previous"]:
                for j in range(i):
                    sup[j] = True
        return sup

    def _collapse(self, acc, decs):
        """Propunere + confirmare imediată („programăm operația pe 2 octombrie” / „Da, aprobat, pe 2
        octombrie”) extrase ca două decizii cu același text -> una singură: confirmarea (cea mai recentă)."""
        out = []
        for d in decs:
            prev = out[-1] if out else None
            if (prev and prev["turn_id"] is not None and d["turn_id"] is not None
                    and 0 < d["turn_id"] - prev["turn_id"] <= 5
                    and fuzz.token_set_ratio(norm(prev["decision"]), norm(d["decision"])) >= 90):
                self.events.append({"event": "collapse_confirmed_decision", "case_key": acc["case_key"],
                                    "dropped_turn": prev["turn_id"], "kept_turn": d["turn_id"]})
                out[-1] = d
            else:
                out.append(d)
        return out

    def result(self, supersede=None):
        """supersede(case_key, decizii) -> set de indici înlocuiți sau None (apel eșuat)."""
        out = []
        for acc in self.cases:
            decs = self._collapse(acc, self._ordered(acc))
            sup = self._superseded(acc, decs, supersede)
            etas = [e for _, e in sorted(acc["etas"], key=lambda x: x[0]) if e["type"] != "none"]
            eta = dict(etas[-1]) if etas else {"type": "none", "raw": None, "date": None,
                                               "condition": None, "needs_review": False}
            out.append({
                "case_key": acc["case_key"],
                "topic": next((t for t in reversed(acc["topics"]) if t), ""),
                "discussion_summary": " ".join(acc["summaries"]),
                "decisions": [{"decision": d["decision"], "status": d["status"], "quote": d["quote"],
                               "timestamp": d["timestamp"], "turn_id": d["turn_id"],
                               "superseded": s, "needs_review": False} for d, s in zip(decs, sup)],
                "eta": eta,
                "open_questions": acc["open_questions"],
            })
        return out
