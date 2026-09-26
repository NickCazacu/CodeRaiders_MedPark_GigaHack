"""Adaptorul llm_input.json -> Meeting/Window/Turn.
    python -m LLM.tests.test_loader
"""
import json
import sys

from LLM.loader import load, parse_line, ts_seconds
from LLM.tests.helpers import fixture, run, tmpdir


def write(data):
    p = tmpdir() / "llm_input.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def turn(i, line, start, **kw):
    return {"id": i, "speaker": kw.pop("speaker", "UNK"), "start": start, "end": start + 2, "line": line,
            "low_confidence": False, "low_confidence_ratio": 0.0, "langs": ["ro"], "segment_ids": [i],
            "tokens_est": 10, **kw}


def test_example_fixture():
    m = load(fixture("llm_input.example.json"))
    assert m.n_windows == 2 and len(m.turns) == 7, (m.n_windows, len(m.turns))
    w = m.windows[1]
    assert w.turn_ids == [1, 2, 3, 4, 5, 6] and w.context_ids == [1, 2], w.turn_ids
    assert w.context_lines[0].startswith("[00:21]") and w.new_lines[0].startswith("[00:32]"), w.lines
    t3, t4 = m.turns[3], m.turns[4]
    assert t3.text == "Давайте повторим креатинин вечером и решим по гемодиализу." and t3.ts == "00:32", t3
    assert t4.text == "De acord. Și ecografia?" and t4.marker and t4.uncertain, t4
    assert not m.warnings, m.warnings


def test_parse_line_forms():
    assert parse_line("[00:41] SPEAKER_01: De acord. Și ecografia? [?]", "SPEAKER_01") == \
        ("00:41", "De acord. Și ecografia?", True)
    assert parse_line("[65:12] UNK: Pacientul 48, da.") == ("65:12", "Pacientul 48, da.", False)
    assert parse_line("[1:05:12] SPEAKER_03: Да, давай. [?]") == ("1:05:12", "Да, давай.", True)
    # textul care începe cu „Pacient:” nu e confundat cu o etichetă de vorbitor
    assert parse_line("[00:05] UNK: Pacient: stabil", "UNK") == ("00:05", "Pacient: stabil", False)


def test_ts_seconds():
    assert ts_seconds("00:32") == 32 and ts_seconds("[00:32]") == 32
    assert ts_seconds("65:12") == 3912 and ts_seconds("1:05:12") == 3912
    assert ts_seconds("") is None and ts_seconds("ieri") is None and ts_seconds(None) is None


def test_turn_ids_range_and_full_list():
    turns = [turn(i, f"[00:0{i}] UNK: replica {i}", float(i)) for i in range(5)]
    text = lambda ids: "\n".join(turns[i]["line"] for i in ids)
    data = {"turns": turns, "windows": [
        {"index": 0, "turn_ids": [0, 3], "overlap_turns": 0, "text": text(range(4))},        # [prima, ultima]
        {"index": 1, "turn_ids": [2, 3, 4], "overlap_turns": 2, "text": text([2, 3, 4])},   # listă completă
    ]}
    m = load(write(data))
    assert m.windows[0].turn_ids == [0, 1, 2, 3] and m.windows[1].turn_ids == [2, 3, 4]
    assert m.windows[1].context_ids == [2, 3] and m.windows[1].new_lines == [turns[4]["line"]]


def test_uncertain_rules_and_future_overlap_field():
    data = {"turns": [
        turn(0, "[00:00] UNK: a", 0.0),
        turn(1, "[00:02] UNK: b", 2.0, low_confidence_ratio=0.6),
        turn(2, "[00:04] UNK: c", 4.0, overlap=True),
        turn(3, "[00:06] UNK: d [?]", 6.0, low_confidence=True, low_confidence_ratio=1.0),
    ]}
    m = load(write(data), uncertain_ratio=0.5)
    assert [m.turns[i].uncertain for i in range(4)] == [False, True, True, True]
    assert m.turns[0].overlap is None and m.turns[2].overlap is True
    assert load(write(data), uncertain_ratio=0.7).turns[1].uncertain is False


def test_text_line_mismatch_falls_back_to_turns():
    turns = [turn(i, f"[00:0{i}] UNK: r{i}", float(i)) for i in range(3)]
    m = load(write({"turns": turns, "windows": [{"index": 0, "turn_ids": [0, 2], "overlap_turns": 0,
                                                  "text": "[00:00] UNK: r0 r1 r2"}]}))
    assert m.windows[0].lines == [t["line"] for t in turns] and m.warnings, m.warnings


def test_missing_windows_single_window():
    turns = [turn(i, f"[00:0{i}] UNK: r{i}", float(i)) for i in range(3)]
    m = load(write({"turns": turns}))
    assert m.n_windows == 1 and m.windows[0].turn_ids == [0, 1, 2] and m.warnings


def test_unk_speakers_long_fixture():
    m = load(fixture("long_meeting.json"))
    assert m.speakers == ["UNK"] and m.n_windows >= 6, (m.speakers, m.n_windows)
    assert all(not t.text.startswith("UNK") and not t.text.startswith("[") for t in m.turns.values())
    assert any(t.marker and t.uncertain for t in m.turns.values())


if __name__ == "__main__":
    sys.exit(run(globals()))
