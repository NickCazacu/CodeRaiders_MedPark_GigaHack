"""Alegerea limbii per segment (pipeline.asr.pick_language).
    python -m tests.test_language_choice
"""
from pipeline.asr import choose_candidates, pick_language

PREF = {"ro": {"ru": 0.5}}
W = {"compare_languages": ["ro", "ru"], "language_preference": PREF, "compare_skip_prob": 0.8, "short_segment_s": 2.0}
SEG = {"start": 0.0, "end": 5.0}


def test_confident_detection_skips_compare():
    assert choose_candidates([("en", 0.95), ("ro", 0.03)], SEG, None, W) == [("en", 0.95)]


def test_confident_russian_is_still_compared():
    # româna moldovenească e detectată des ca rusă, sigur: nu ne încredem în detector
    cands = choose_candidates([("ru", 0.97), ("ro", 0.02)], SEG, None, W)
    assert [l for l, _ in cands] == ["ro", "ru"]


def test_unsure_detection_is_compared():
    cands = choose_candidates([("ro", 0.6), ("ru", 0.3), ("en", 0.1)], SEG, None, W)
    assert [l for l, _ in cands] == ["ro", "ru"]


def test_no_skip_threshold_always_compares():
    w = {**W, "compare_skip_prob": None}
    assert [l for l, _ in choose_candidates([("en", 0.99)], SEG, None, w)] == ["ro", "ru", "en"]


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
