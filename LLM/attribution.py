"""Verificări în cod după extracția unei ferestre, înainte de merge.

- Decizie pusă la alt pacient: pacientul „curent” al unei replici e ultimul pacient/salon
  menționat la sau înaintea ei. Dacă diferă de numărul din case_key, decizia (și ETA-ul
  citat din aceeași replică) se mută la cazul potrivit.
- Aceeași replică revendicată de mai multe cazuri: rămâne la cazul potrivit, altfel la primul.
- eta.raw care nu apare în textul ferestrei devine null (validarea îl semnalează).
"""
import re

from rapidfuzz import fuzz

from LLM.quotes import norm

# „Pacient 71 de ani” / „Пациентка 45 лет” e vârsta, nu numărul pacientului
AGE = r"(?!\d)(?!\s+(?:de\s+)?(?:ani|an|лет|года|год|years|year)\b)"
PATIENT = re.compile(r"\b(?:pacient\w*|patient\w*|пациент\w*)\s+(?:nr\s+|№\s*)?(\d+)" + AGE)
WARD = re.compile(r"\b(?:salon\w*|палат\w*|room|patul|pat|койк\w*)\s+(?:nr\s+|№\s*)?(\d+)" + AGE)  # salon / pat
LOOKBACK = 25  # câte replici înapoi căutăm ultimul pacient menționat (o discuție pe un caz are ușor 10+ replici)


def ids(text):
    t = norm(text)
    return set(PATIENT.findall(t)), set(WARD.findall(t))


def relation(a, b):
    """a, b = (pacienți, saloane). 'match' / 'mismatch' / 'unknown'. Numărul pacientului are prioritate."""
    for x, y in zip(a, b):
        if x and y:
            return "match" if x & y else "mismatch"
    # tipuri diferite („salon 3” vs „pacientul 21”): numere disjuncte => probabil alt caz;
    # decizia se mută doar dacă există un caz care se potrivește direct (vezi fix_attribution)
    na, nb = a[0] | a[1], b[0] | b[1]
    return "mismatch" if na and nb and not na & nb else "unknown"


def subject_at(turn_id, order, turns):
    """Ultimul pacient/salon menționat la sau înaintea replicii."""
    if turn_id not in order:
        return set(), set()
    i = order.index(turn_id)
    for j in range(i, max(i - LOOKBACK, -1), -1):
        p, w = ids(turns[order[j]].text)
        if p or w:
            return p, w
    return set(), set()


def empty_case(key):
    return {"case_key": key, "topic": "", "discussion_summary": "", "decisions": [],
            "eta": {"type": "none", "raw": None, "date": None, "condition": None, "needs_review": False},
            "open_questions": []}


def fix_attribution(cases, turns, known_keys=()):
    """cases: cazurile unei ferestre, cu turn_id rezolvat. Modifică pe loc; întoarce evenimentele."""
    order = sorted(turns, key=lambda i: turns[i].start)
    events = []

    def target_for(subject, skip):
        for c in cases:
            if c is not skip and relation(ids(c["case_key"]), subject) == "match":
                return c
        for key in known_keys:  # caz din ferestrele anterioare, absent din răspunsul acesta
            if relation(ids(key), subject) == "match":
                stub = empty_case(key)
                cases.append(stub)
                return stub
        return None

    for c in list(cases):
        for d in list(c["decisions"]):
            if d["turn_id"] is None:
                continue
            subject = subject_at(d["turn_id"], order, turns)
            if relation(ids(c["case_key"]), subject) != "mismatch":
                continue
            tgt = target_for(subject, c)
            if tgt is None:
                events.append({"event": "attribution_mismatch_kept", "case_key": c["case_key"],
                               "turn_id": d["turn_id"], "subject": sorted(subject[0] | subject[1])})
                continue
            c["decisions"].remove(d)
            tgt["decisions"].append(d)
            events.append({"event": "decision_moved", "turn_id": d["turn_id"], "from": c["case_key"],
                           "to": tgt["case_key"]})
            raw = c["eta"]["raw"]
            if raw and norm(raw) in norm(turns[d["turn_id"]].text):
                # termenul vine din replica mutată: nu mai aparține cazului sursă
                if tgt["eta"]["type"] == "none":
                    tgt["eta"] = c["eta"]
                    events.append({"event": "eta_moved", "raw": raw, "from": c["case_key"], "to": tgt["case_key"]})
                else:
                    events.append({"event": "eta_dropped", "raw": raw, "from": c["case_key"],
                                   "reason": f"aparține replicii mutate la {tgt['case_key']}"})
                c["eta"] = empty_case("")["eta"]

    # aceeași replică în mai multe cazuri
    claims = {}
    for c in cases:
        for d in c["decisions"]:
            if d["turn_id"] is not None:
                claims.setdefault(d["turn_id"], []).append((c, d))
    for tid, lst in claims.items():
        if len(lst) < 2:
            continue
        subject = subject_at(tid, order, turns)
        keep = next((x for x in lst if relation(ids(x[0]["case_key"]), subject) == "match"), lst[0])
        for c, d in lst:
            if d is not keep[1] and d in c["decisions"]:
                c["decisions"].remove(d)
                events.append({"event": "duplicate_claim_dropped", "turn_id": tid, "case_key": c["case_key"],
                               "kept_in": keep[0]["case_key"]})

    # cazuri goale create doar ca țintă și rămase fără nimic
    cases[:] = [c for c in cases if c["decisions"] or c["topic"] or c["discussion_summary"] or c["open_questions"]]
    return events


# confuziile de tip ETA pe care qwen3:8b le face și cu regula în prompt; textul e fără diacritice (norm_key)
RECURRING = re.compile(r"\b(zilnic\w*|la fiecare|de (doua|trei|patru) ori|ori pe zi|кажд\w*|ежедневн\w*|раз в"
                       r"|every|daily|twice a day)\b")
RELATIVE = re.compile(r"\b(azi|astazi|maine|poimaine|diseara|luni|marti|miercuri|joi|vineri|sambata|duminica"
                      r"|сегодня|завтра|послезавтра|вечером|утром|понедельник\w*|вторник\w*|сред\w*|четверг\w*"
                      r"|пятниц\w*|суббот\w*|воскресень\w*|today|tomorrow|tonight|monday|tuesday|wednesday"
                      r"|thursday|friday|saturday|sunday)\b")
CALENDAR = re.compile(r"\b\d{1,2}[./]\d{1,2}\b|\b(ianuarie|februarie|martie|aprilie|mai|iunie|iulie|august"
                      r"|septembrie|octombrie|noiembrie|decembrie|январ\w*|феврал\w*|март\w*|апрел\w*|ма[яе]"
                      r"|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|january|february|march"
                      r"|april|june|july|august|september|october|november|december)\b")


def eta_type_rule(typ, raw):
    """Tipul corectat, sau None dacă tipul modelului rămâne."""
    from LLM.merge import norm_key
    t = norm_key(raw)
    if typ not in ("recurring", "conditional") and RECURRING.search(t):
        return "recurring"   # „каждые 6 часов” nu e duration
    if typ == "absolute" and RELATIVE.search(t) and not CALENDAR.search(t):
        return "relative"    # o zi a săptămânii / „mâine” nu e o dată calendaristică
    return None


def realign(raw, turns, min_score=85):
    """Parafrază apropiată („dacă crește troponina a doua”) -> fragmentul exact din transcriere
    („Dacă troponina a doua crește”). O traducere nu se potrivește și rămâne negăsită."""
    q = raw.casefold()
    best = None
    for t in turns:
        a = fuzz.partial_ratio_alignment(q, t.text.casefold())
        if a and a.score >= min_score and (best is None or a.score > best[0]):
            best = (a.score, t, a.dest_start, a.dest_end)
    if not best:
        return None
    _, t, s, e = best
    while s > 0 and t.text[s - 1].isalnum():          # extinde la granițe de cuvânt
        s -= 1
    while e < len(t.text) and t.text[e].isalnum():
        e += 1
    span = t.text[s:e].strip(" ,.;:!?")
    return span if 0.6 <= len(span) / max(len(raw), 1) <= 1.6 else None


REPEAT_WORD = r"((?:la\s+)?(?:кажд\w*|fiecare|every|each|ежедневн\w*))\s+"


def extend_recurring(raw, turns):
    """„месяц” copiat din „каждый месяц” -> „каждый месяц”: cuvântul de repetare dinaintea expresiei
    face parte din termen (altfel tipul pare relative în loc de recurring)."""
    for t in turns:
        m = re.search(REPEAT_WORD + re.escape(raw), t.text, re.IGNORECASE)
        if m:
            return t.text[m.start():m.end()]
    return None


def check_eta_raw(cases, window_turns):
    """eta.raw trebuie să fie copiat din transcriere: parafrazele apropiate se înlocuiesc cu
    fragmentul exact, restul devine null (tipul rămâne, validarea decide).
    Apoi corectează tipul pentru tiparele fără echivoc (vezi eta_type_rule)."""
    text = norm(" ".join(t.text for t in window_turns))
    events = []
    for c in cases:
        raw = c["eta"]["raw"]
        if raw and norm(raw) not in text:
            # întâi replicile citate în deciziile cazului, apoi toată fereastra
            own = [t for t in window_turns if t.id in {d["turn_id"] for d in c["decisions"]}]
            span = realign(raw, own) or realign(raw, window_turns)
            events.append({"event": "eta_raw_realigned" if span else "eta_raw_not_in_transcript",
                           "case_key": c["case_key"], "raw": raw, "span": span})
            c["eta"]["raw"] = raw = span
        longer = extend_recurring(raw, window_turns) if raw else None
        if longer:
            events.append({"event": "eta_raw_extended", "case_key": c["case_key"], "raw": raw, "span": longer})
            c["eta"]["raw"] = raw = longer
        fixed = eta_type_rule(c["eta"]["type"], raw) if raw else None
        if fixed:
            events.append({"event": "eta_type_fixed", "case_key": c["case_key"], "raw": raw,
                           "from": c["eta"]["type"], "to": fixed})
            c["eta"]["type"] = fixed
    return events
