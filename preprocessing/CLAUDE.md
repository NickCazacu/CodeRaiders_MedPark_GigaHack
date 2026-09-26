# Medpark MoM Pipeline — Project Context for Claude Code

Hackathon prototype for **Medpark** (private hospital, Chișinău, Moldova): a **100% on-premise**
pipeline that turns meeting audio (upload or live "Rec") into a structured **Minutes of Meeting
(MoM)** document and emails it to a predefined distribution list. Team project, Python.

Reply in the language the user writes in (team speaks Russian / Romanian / English).
Code, comments, commit messages and docstrings: **English**.

## HARD RULE — offline security gate (disqualification if broken)

Any external network call at runtime = the whole submission is disqualified. The jury may unplug
the network during the demo and run the pipeline end-to-end.

- NEVER add runtime calls to cloud ASR/LLM APIs, external SMTP (Gmail, Outlook, SendGrid…),
  CDNs, Google Fonts, analytics, telemetry, or HuggingFace Hub.
- Model downloads happen ONLY in `scripts/download_models.py` (dev time). Runtime code loads
  models from local paths; set `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`.
- Frontend: vanilla HTML/JS/CSS, every asset served locally from `app/static/`. No CDN links.
- Email goes ONLY through Mailpit (`mailpit:1025`) via n8n.
- n8n must run with telemetry off (see `.env.example`).
- Allowed hosts in code: `localhost`, `127.0.0.1`, docker service names (`ollama`, `n8n`, `mailpit`, `api`).
- Before adding any dependency, check that it does not phone home at runtime (first-run
  downloads: nltk, tiktoken, whisper cache, pyannote). If it does, pre-download in the script.
- A hook (`.claude/hooks/check_external_calls.py`) flags external URLs after every edit. If it
  fires, fix the code — do not work around it. `# offline-ok` on a line is only for
  XML namespaces and similar non-network strings.

## Data privacy

- Never read, print or copy files from `data/audio/` (real anonymized hospital recordings).
- Logs contain only job id, stage, timings, errors — never transcript text or names.
- After the email is sent, delete audio and intermediate transcripts (configurable retention).
- The email contains the MoM only, never the full transcript.

## Judging criteria (optimize in this order)

| Criterion | Weight | What it means for code |
|---|---|---|
| Security & Architecture | Pass/Fail + 20% | offline gate above; runs on 1×16 GB GPU **or** CPU-only 32 GB RAM |
| Linguistic Accuracy | 30% | RO/RU/EN code-switching, medical terms, no hallucinations, measured WER/CER |
| Output Quality | 30% | real decisions & action items (not retelling), correct owners & deadlines, MoM usable without edits |
| UX | 10% | fast "meeting end → email", 1-click upload/Rec, minimal user input |
| Pitch & Demo | 10% | offline live demo, metrics slides |

Speed target: < 15 min end-to-end for a 60-min recording on reference hardware. Measure, never guess.

## Architecture

```
Web UI (upload | Rec + meeting type)
  → FastAPI (job queue, stage status, chunked upload for Rec)
    1. preprocess  ffmpeg → 16 kHz mono WAV, Silero VAD segments (5–15 s)
    2. asr         faster-whisper + initial_prompt (mixed RO/RU/EN) + hotwords (glossary)
                   + per-segment language re-check (ro vs ru, keep best avg_logprob)
    3. diarize     pyannote (optional, strong bonus) → speaker labels
    4. correct     glossary-constrained correction only (term → term), no free rewriting
    5. extract     Ollama LLM, JSON-schema output → verification pass on evidence quotes
    6. dates       relative deadlines → absolute dates (dateparser, meeting date as base)
    7. render      DOCX/PDF from per-meeting-type template
  → POST webhook to n8n → Switch(meeting_type) → Send Email (SMTP mailpit:1025)
```

Rec mode: browser `MediaRecorder` sends 30 s chunks during the meeting; ASR runs incrementally,
so after "Stop" only the tail + LLM remain. This is the main UX speed trick — keep it working.

GPU memory: stages run sequentially. Unload Whisper (`del model; gc.collect();
torch.cuda.empty_cache()`) before LLM; call Ollama with `keep_alive: 0` at the end of a job.

## Default model choices (change only with eval numbers)

| Profile | ASR | LLM (Ollama) |
|---|---|---|
| `gpu16` | faster-whisper `large-v3` / `large-v3-turbo`, `float16` | Qwen 7–8B Instruct, Q4_K_M, `num_ctx` 32768 |
| `cpu32` | faster-whisper `large-v3-turbo` or `medium`, `int8` | Qwen 3–4B Instruct, Q4_K_M |

Profile is selected by `PROFILE` env var in `app/config.py` (pydantic-settings).

## Repository layout

```
app/
  main.py            FastAPI: /jobs (upload), /jobs/{id}/chunk (Rec), /jobs/{id} (status), static UI
  config.py          settings + profiles
  jobs.py            in-process queue, stage status
  models.py          pydantic: Segment, Transcript, ActionItem, MoM, Job
  pipeline/          preprocess.py asr.py diarize.py correct.py extract.py dates.py render.py
  static/            index.html app.js style.css  (NO external assets)
prompts/             system prompts per stage & meeting type (versioned, never inline in code)
templates/           DOCX templates: medical.docx executive.docx administrative.docx
n8n/workflows/       exported n8n workflow JSON (keep in git)
data/glossary/       medical_terms.txt (one term per line), participants.yaml (per meeting type)
data/audio/          test recordings — gitignored, do not read
data/gold/           transcripts/*.txt (reference), mom/*.json (gold MoMs) for evaluation
eval/                eval_asr.py eval_mom.py results.md
scripts/             download_models.py (ONLY online script), bench.py
tests/
docker-compose.yml   services: api, ollama, n8n, mailpit — network `internal: true` for demo
```

## Core data contracts (app/models.py)

- `Segment`: start, end, text, lang (`ro|ru|en`), speaker, avg_logprob, no_speech_prob, uncertain: bool
- `ActionItem`: task, owner (must be from participants list or `null` → rendered "Nealocat"),
  deadline_raw, deadline (date | null), evidence (verbatim quote), timestamp
- `MoM`: meeting_type, date, participants, summary, decisions[], action_items[], open_issues[]

LLM rules: `temperature` ≤ 0.2, output validated with pydantic; on validation error retry once,
then fail the job visibly. Owners are chosen ONLY from `participants.yaml`. Never invent deadlines.

## Language conventions for output

- Transcript: keep each language as spoken. Russian stays **Cyrillic**, English terms as-is.
- Romanian diacritics: normalize to comma-below `ș ț` (not cedilla `ş ţ`).
- MoM document language: **Romanian** (TODO: confirm with organizers) — one language per document.
- Uncertain ASR segments are marked `[?]`; numbers (doses, sums, dates) in uncertain segments
  are highlighted in the MoM for human check.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/download_models.py                      # dev only, needs internet
docker compose up -d ollama n8n mailpit                # infra (Mailpit UI: http://localhost:8025)
uvicorn app.main:app --reload --port 8000              # API + UI: http://localhost:8000
pytest -q
ruff check . && ruff format .
python eval/eval_asr.py                                # WER / CER / term recall
python eval/eval_mom.py                                # action item precision / recall
python scripts/bench.py --audio data/audio/<file>      # end-to-end timing per stage
```

Ports: API 8000, Ollama 11434, n8n 5678, Mailpit SMTP 1025 / UI 8025.

## How to work in this repo

- For changes touching more than one pipeline stage: propose a short plan first.
- After changing `asr.py`, `correct.py` or ASR prompts → run `/eval-asr` and report the delta.
- After changing `extract.py` or `prompts/` → run `/eval-mom` and report the delta.
- Before commits touching dependencies, docker-compose, frontend or n8n → run `/offline-audit`.
- Never run `pip install`, `ollama pull` or model downloads without asking (large, slow).
- Prefer small, typed functions; pydantic v2 models; `pathlib`; `logging` (no print).
- Every pipeline stage is a pure-ish function `(input model, config) -> output model` so it can
  be tested and benchmarked separately.
- Tests must not require GPU or network: mock Whisper/Ollama, use tiny fixtures in `tests/fixtures/`.
- Keep README "Performance" and "Accuracy" tables up to date — the jury reads them.

## Open decisions (ask the user before assuming)

- MoM output language (Romanian assumed).
- Final ASR model (large-v3 vs turbo) and LLM (by eval results).
- Diarization in or out of the final demo (depends on speed on reference hardware).
