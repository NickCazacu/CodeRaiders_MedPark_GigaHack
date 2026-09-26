"""Numerele rostite -> cifre (pipeline.itn).
    python -m tests.test_itn
"""
from pipeline.itn import normalize_numbers as n

CASES = [
    ("fracție de treizeci și opt", "fracție de 38"),
    ("ureea nouă spre zece", "ureea 19"),
    ("acum doi spre zice", "acum 12"),
    ("nouăsprezece ani", "19 ani"),
    ("creatinina două sute patruzeci", "creatinina 240"),
    ("o sută douăzeci pe optzeci", "120 pe 80"),
    ("o mie cinci sute de mililitri", "1500 de mililitri"),
    ("douăzeci de mii", "20000"),
    ("zero virgulă zero opt", "0,08"),
    ("doi virgulă cinci miligrame", "2,5 miligrame"),
    ("saturația nouăzeci și patru la sută", "saturația 94%"),
    ("80 la sută", "80%"),
    # nu se ating: unități singure, articolul „o”, textul fără numere
    ("unul mobilizează, doi, dacă", "unul mobilizează, doi, dacă"),
    ("o pungă de sânge", "o pungă de sânge"),
    ("și dobu e cu patru.", "și dobu e cu patru."),
    ("douăzeci și ceva", "20 și ceva"),
    ("ureea noul spre zece", "ureea noul spre zece"),  # număr deformat: nu ghicim
    ("zece zile", "10 zile"),
    # nu trece peste punctuație
    ("treizeci. Și opt", "30. Și opt"),
    ("Treizeci și opt, patruzeci", "38, 40"),
]


def test_cases():
    bad = [(src, n(src), exp) for src, exp in CASES if n(src) != exp]
    assert not bad, "\n".join(f"{s!r} -> {g!r}, așteptat {e!r}" for s, g, e in bad)


if __name__ == "__main__":
    test_cases()
    print(f"OK  {len(CASES)} cazuri")
