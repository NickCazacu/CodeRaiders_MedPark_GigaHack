"""Alegerea limbii per segment (pipeline.asr.pick_language).
    python -m tests.test_language_choice
"""
from pipeline.asr import pick_language

PREF = {"ro": {"ru": 0.5}}


def test_ro_wins_close_duel_with_ru():
    # româna moldovenească transcrisă cu chirilice „câștigă” ca rusă la mică distanță
    assert pick_language({"ro": -0.9, "ru": -0.5}, PREF) == "ro"


def test_real_russian_still_wins():
    assert pick_language({"ro": -1.2, "ru": -0.4}, PREF) == "ru"


def test_preference_does_not_beat_english():
    # regresia din evaluation/lang_mix: un bonus general făcea engleza să fie tradusă în română
    assert pick_language({"ro": -0.6, "ru": -1.4, "en": -0.3}, PREF) == "en"


def test_no_preference_is_plain_max():
    assert pick_language({"ro": -0.9, "ru": -0.5}, {}) == "ru"


if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        fn()
        print(f"OK  {name}")
