"""Teste manuale: meeting.txt -> llm_input.json și review.md (fără Ollama).
    python -m LLM.tests.test_manual
"""
import json
import sys

from LLM import extract as ex
from LLM.loader import load
from LLM.manual import build_llm_input, normalize_input, overview, parse_meeting, resolve, review
from LLM.tests.helpers import FakeClient, case, cfg, dec, fixture, run, tmpdir

MEETING = """\
# Ședință de test
# date: 2026-09-21
# window_tokens: 3500

Pacientul 12 din cardiologie, fibrilație atrială.
[00:20] SPEAKER_01: Давайте начнём амиодарон сегодня.
SPEAKER_00: De acord, amiodaronă azi.

Pacienta din salonul 4, febrilă.
Facem ecografie azi. [?]
"""


def write_meeting(text=MEETING):
    d = tmpdir()
    (d / "meeting.txt").write_text(text, encoding="utf-8")
    return d / "meeting.txt"


def test_parse_meeting_format():
    settings, utts = parse_meeting(write_meeting())
    assert settings == {"date": "2026-09-21", "window_tokens": "3500"}, settings
    assert [u["speaker"] for u in utts] == ["UNK", "SPEAKER_01", "SPEAKER_00", "UNK", "UNK"]
    assert utts[1]["at"] == 20.0 and utts[1]["text"] == "Давайте начнём амиодарон сегодня."
    assert utts[3]["pause"] == 15.0 and utts[0]["pause"] == 0.0     # linia goală = pauză
    assert utts[4]["low"] and utts[4]["text"] == "Facem ecografie azi."


def test_built_input_matches_asr_format():
    _, utts = parse_meeting(write_meeting())
    data = build_llm_input("t", utts)
    assert data["n_turns"] == 5 and data["n_windows"] == 1
    t = data["turns"]
    assert t[1]["line"] == "[00:20] SPEAKER_01: Давайте начнём амиодарон сегодня." and t[1]["langs"] == ["ru"]
    assert t[4]["line"].endswith("[?]") and t[4]["low_confidence"]
    assert all(a["end"] <= b["start"] for a, b in zip(t, t[1:])), t   # timpii expliciți nu se suprapun
    p = tmpdir() / "llm_input.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert load(p).turns[4].uncertain


def test_review_report():
    _, utts = parse_meeting(write_meeting())
    job = tmpdir()
    (job / "llm_input.json").write_text(json.dumps(build_llm_input("t", utts), ensure_ascii=False), encoding="utf-8")
    client = FakeClient({"extract": [{"summary": "Amiodaronă azi [00:20]. Ecografie azi [00:43].", "cases": [
        case("Pacient 12, cardiologie", [dec("Давайте начнём амиодарон сегодня.", "00:20")],
             {"type": "relative", "raw": "сегодня", "condition": ""}),
        case("Pacientă salon 4", [dec("Facem ecografie azi.", "00:43"), dec("Îl operăm mâine.", "00:50")]),
    ]}]})
    ex.extract(job / "llm_input.json", "2026-09-21", job / "minutes.json", job / "llm_debug", cfg(), client)
    text = review(job, "test").read_text(encoding="utf-8")
    assert "| C1 | Pacient 12, cardiologie |" in text and "relative „сегодня”" in text, text
    assert "## Termene" in text and "| C1 | Pacient 12, cardiologie | d | „сегодня” | relativ |" in text, text
    assert "⚠ replică nesigură" in text                         # decizia pe linia [?]
    assert "⛔ citat negăsit" in text                            # citat inventat => turn_id null
    assert "▶ C1" in text and "▶ C2" in text and "## Listă de verificare" in text

    screen = overview(job)
    assert "PUNCTELE PRINCIPALE\nAmiodaronă azi [00:20]" in screen, screen
    assert "când: „сегодня” (relativ)" in screen and "fără termen: C2 Pacientă salon 4" in screen, screen
    assert "⛔ citat negăsit" in screen and "review.md" in screen


def test_json_inputs_normalized():
    lli = json.loads(fixture("llm_input.example.json").read_text(encoding="utf-8"))
    assert normalize_input(lli, "x") is lli
    # transcript.json (ieșirea postprocess): segmente cu text; cele eliminate sunt ignorate
    segs = [{"id": 0, "speaker": "UNK", "start": 1.0, "end": 4.0, "text": "Pacientul 5, stabil.", "lang": "ro",
             "low_confidence": False, "flags": [], "dropped": None},
            {"id": 1, "speaker": "UNK", "start": 5.0, "end": 6.0, "text": "Субтитры", "lang": "ru",
             "low_confidence": False, "flags": [], "dropped": "hallucination_phrase"},
            {"id": 2, "speaker": "UNK", "start": 7.0, "end": 9.0, "text": "Externare mâine.", "lang": "ro",
             "low_confidence": True, "flags": [], "dropped": None}]
    d = normalize_input(segs, "x")
    assert d["n_turns"] == 2 and d["n_windows"] == 1 and d["turns"][1]["line"] == "[00:07] UNK: Externare mâine. [?]", d
    # listă de replici (doar "turns")
    d = normalize_input(lli["turns"], "x")
    p = tmpdir() / "in.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    assert load(p).n_windows == 1
    try:
        normalize_input({"foo": 1}, "x")
    except SystemExit:
        return
    raise AssertionError("JSON nerecunoscut acceptat")


def test_json_outside_jobs_goes_to_manual_dir():
    p = tmpdir() / "Ședința mea.json"
    p.write_text("{}", encoding="utf-8")
    job_dir, meeting, source = resolve(str(p))
    assert job_dir.name == "manual-_edin_a_mea" and source == p.resolve() and meeting is None, job_dir


if __name__ == "__main__":
    sys.exit(run(globals()))
