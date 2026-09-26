"""Adaptor: jobs/<id>/llm_input.json (scris de pipeline/pack_for_llm.py) -> structuri interne.

Restul modulului folosește doar Meeting / Window / Turn, deci o schimbare de format
la ASR se rezolvă aici.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

TS_RE = re.compile(r"^\[(\d+):(\d{2})(?::(\d{2}))?\]\s*")  # [mm:ss] (minute > 59 permise) sau [h:mm:ss]
MARK_RE = re.compile(r"\s*\[\?\]\s*$")
LABEL_RE = re.compile(r"^(UNK|SPEAKER_\d+):\s*")


def est_tokens(text):
    # aceeași estimare ca pipeline/pack_for_llm.py: octeți UTF-8 / 4
    return (len(text.encode("utf-8")) + 3) // 4


def ts_seconds(ts):
    """'00:32' / '65:12' / '1:05:12' / '[00:32]' -> secunde; None dacă nu se poate citi."""
    m = re.fullmatch(r"\[?\s*(\d+):(\d{1,2})(?::(\d{1,2}))?\s*\]?", str(ts or "").strip())
    if not m:
        return None
    a, b, c = m.groups()
    return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)


def fmt_ts(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"  # ca în pack_for_llm: minutele pot trece de 59


@dataclass
class Turn:
    id: int
    start: float
    end: float
    ts: str                  # timpul exact cum apare în linie, fără paranteze ("00:32")
    text: str                # textul vorbit: fără [mm:ss], fără eticheta vorbitorului, fără [?]
    line: str                # linia originală, cum apare în fereastră
    speaker: str             # doar context, nu ajunge în minută
    low_confidence: bool
    low_confidence_ratio: float
    marker: bool             # linia are [?]
    overlap: bool | None     # vorbire suprapusă (câmp viitor de la ASR; None = necunoscut)
    uncertain: bool          # low_confidence, [?], ratio peste prag sau overlap
    langs: list
    segment_ids: list
    tokens: int


@dataclass
class Window:
    index: int
    turn_ids: list           # toate id-urile, în ordine
    overlap_turns: int       # primele N replici repetă finalul ferestrei anterioare (doar context)
    start: float
    end: float
    tokens: int
    text: str
    lines: list              # liniile din text, câte una per replică

    @property
    def context_ids(self):
        return self.turn_ids[:self.overlap_turns]

    @property
    def context_lines(self):
        return self.lines[:self.overlap_turns]

    @property
    def new_lines(self):
        return self.lines[self.overlap_turns:]


@dataclass
class Meeting:
    job_id: str
    path: Path
    audio_duration_s: float | None
    languages: dict
    speakers: list
    turns: dict              # id -> Turn
    windows: list
    warnings: list = field(default_factory=list)

    @property
    def n_windows(self):
        return len(self.windows)


def parse_line(line, speaker=None):
    """'[00:41] SPEAKER_01: De acord. [?]' -> ('00:41', 'De acord.', True)."""
    m = TS_RE.match(line)
    ts, rest = None, line
    if m:
        ts, rest = m.group(0).strip()[1:-1], line[m.end():]
    if speaker and rest.startswith(f"{speaker}:"):
        rest = rest[len(speaker) + 1:].lstrip()
    else:
        rest = LABEL_RE.sub("", rest, count=1)
    marker = bool(MARK_RE.search(rest))
    return ts, MARK_RE.sub("", rest).strip(), marker


def window_ids(raw, turns):
    """turn_ids e [prima, ultima] în pack_for_llm; acceptăm și lista completă."""
    ids = list(raw)
    if len(ids) == 2 and ids[1] >= ids[0] and all(i in turns for i in range(ids[0], ids[1] + 1)):
        return list(range(ids[0], ids[1] + 1))
    return ids


def load(path, uncertain_ratio=0.5):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    warnings = []

    turns = {}
    for t in data.get("turns", []):
        ts, text, marker = parse_line(t["line"], t.get("speaker"))
        ratio = float(t.get("low_confidence_ratio") or 0.0)
        overlap = t.get("overlap")
        low = bool(t.get("low_confidence"))
        turns[t["id"]] = Turn(
            id=t["id"], start=float(t["start"]), end=float(t["end"]),
            ts=ts or fmt_ts(t["start"]), text=text, line=t["line"], speaker=t.get("speaker", ""),
            low_confidence=low, low_confidence_ratio=ratio, marker=marker,
            overlap=None if overlap is None else bool(overlap),
            uncertain=low or marker or ratio > uncertain_ratio or bool(overlap),
            langs=list(t.get("langs") or []), segment_ids=list(t.get("segment_ids") or []),
            tokens=int(t.get("tokens_est") or est_tokens(t["line"])),
        )

    windows = []
    raw_windows = data.get("windows")
    if raw_windows is None:
        # format mai vechi / incomplet: o singură fereastră cu toate replicile
        warnings.append("llm_input.json nu are 'windows': folosesc o singură fereastră cu toate replicile")
        ids = sorted(turns)
        raw_windows = [{"index": 0, "turn_ids": ids, "overlap_turns": 0,
                        "text": "\n".join(turns[i].line for i in ids)}] if ids else []

    for w in raw_windows:
        ids = window_ids(w["turn_ids"], turns)
        missing = [i for i in ids if i not in turns]
        if missing:
            warnings.append(f"fereastra {w['index']}: replici inexistente în 'turns': {missing}")
            ids = [i for i in ids if i in turns]
        lines = w["text"].split("\n") if w.get("text") else []
        if len(lines) != len(ids):
            warnings.append(f"fereastra {w['index']}: {len(lines)} linii în text vs {len(ids)} replici; "
                            "folosesc liniile din 'turns'")
            lines = [turns[i].line for i in ids]
        overlap = max(0, min(int(w.get("overlap_turns") or 0), len(ids)))
        windows.append(Window(
            index=w["index"], turn_ids=ids, overlap_turns=overlap,
            start=float(w.get("start", turns[ids[0]].start if ids else 0.0)),
            end=float(w.get("end", turns[ids[-1]].end if ids else 0.0)),
            tokens=int(w.get("tokens_est") or est_tokens(w.get("text", ""))),
            text=w.get("text", ""), lines=lines,
        ))
    windows.sort(key=lambda w: w.index)

    return Meeting(
        job_id=data.get("job_id") or path.parent.name, path=path,
        audio_duration_s=data.get("audio_duration_s"), languages=data.get("languages") or {},
        speakers=data.get("speakers") or [], turns=turns, windows=windows, warnings=warnings,
    )
