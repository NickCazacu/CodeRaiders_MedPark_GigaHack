"""Promptele din LLM/prompts/ (editabile, versionate) -> mesaje /api/chat.

Șabloanele folosesc {{nume}}; exemplul few-shot trece prin același șablon ca apelul
real, deci rămâne mereu în formatul curent.
"""
import json
from functools import lru_cache

from LLM.config import LLM_DIR

PROMPTS = LLM_DIR / "prompts"
NONE = "(niciunul)"


@lru_cache(maxsize=None)
def read(name):
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def fewshot():
    return json.loads(read("fewshot.json"))


def render(template, **values):
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def extract_user(single, date, window_lines, context_lines=(), window_number=1, n_windows=1,
                 known_cases=(), previous_summary="", patient=None):
    if patient:  # modul pe pacienți: fragmentul unui singur pacient, delimitat de apelul de segmentare
        return render(read("patient.md"), date=date, number=window_number, n=n_windows,
                      label=patient["label"], start=patient["start"],
                      known_cases="\n".join(f"- {c}" for c in known_cases) or NONE,
                      context_lines="\n".join(context_lines) or "(niciuna, începutul ședinței)",
                      window_lines="\n".join(window_lines))
    if single:
        return render(read("single.md"), date=date, window_lines="\n".join([*context_lines, *window_lines]))
    return render(read("window.md"), date=date, window_number=window_number, n_windows=n_windows,
                  known_cases="\n".join(f"- {c}" for c in known_cases) or NONE,
                  previous_summary=previous_summary or "(nu există)",
                  context_lines="\n".join(context_lines) or "(niciuna, începutul ședinței)",
                  window_lines="\n".join(window_lines))


def extract_messages(single, date, window_lines, **kw):
    fs = fewshot()
    example = extract_user(single, fs["date"], fs["window_lines"], fs["context_lines"], fs["window_number"],
                           fs["n_windows"], fs["known_cases"], fs["previous_summary"],
                           patient=fs["patient"] if kw.get("patient") else None)  # exemplul, prin același șablon
    return [
        {"role": "system", "content": read("system.md")},
        {"role": "user", "content": example},
        {"role": "assistant", "content": json.dumps(fs["output"], ensure_ascii=False)},
        {"role": "user", "content": extract_user(single, date, window_lines, **kw)},
    ]


def segment_messages(lines):
    return [{"role": "user", "content": render(read("segment.md"), lines="\n".join(lines))}]


def same_case_messages(a, b):
    return [{"role": "user", "content": render(
        read("same_case.md"), key_a=a["case_key"], topic_a=a["topic"], summary_a=a["discussion_summary"],
        key_b=b["case_key"], topic_b=b["topic"], summary_b=b["discussion_summary"])}]


def translate_messages(texts):
    items = "\n".join(f"{i}. {t}" for i, t in enumerate(texts))
    return [{"role": "user", "content": render(read("translate.md"), texts=items, n=len(texts))}]


def supersede_messages(case_key, decisions):
    lines = [f"{i}. [{d['timestamp']}] {d['decision']} (citat: „{d['quote']}”)" for i, d in enumerate(decisions)]
    return [{"role": "user", "content": render(read("supersede.md"), case_key=case_key,
                                               decisions="\n".join(lines), last_index=len(decisions) - 1)}]


def final_messages(date, window_summaries, case_lines):
    return [{"role": "user", "content": render(
        read("final.md"), date=date, window_summaries="\n".join(window_summaries) or NONE,
        cases="\n".join(case_lines) or NONE, last_index=max(len(case_lines) - 1, 0))}]
