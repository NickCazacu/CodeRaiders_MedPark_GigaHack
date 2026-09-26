# llm: extracția procesului-verbal (llm_input.json → minutes.json)

Citește `jobs/<job_id>/llm_input.json` (scris de `pipeline/pack_for_llm.py`) și produce `minutes.json`:
cazurile discutate, deciziile cu citat verbatim și timp, ETA-ul și întrebările deschise.
Rulează **doar** pe Ollama local (`qwen3:8b`), fără alte apeluri de rețea: URL-urile non-locale sunt
refuzate, iar proxy-urile din mediu sunt ignorate.

## Rulare

Pregătire unică: Ollama instalat și `ollama pull qwen3:8b`. Dependențele Python sunt deja în
`requirements.txt` (`pyyaml`, `rapidfuzz`).

```powershell
.\.venv\Scripts\python.exe -m LLM.extract jobs\<job_id>\llm_input.json --date 2026-09-21
.\.venv\Scripts\python.exe -m LLM.extract <job_id> --date 2026-09-21 --out minute.json
```

Opțiuni: `--out` (implicit `minutes.json` lângă intrare), `--debug-dir` (implicit `llm_debug/` lângă
intrare), `--config` (implicit `LLM/config.yaml`), `--think-final` (`think: true` pentru apelul final).

Din pipeline, după ce s-a scris `llm_input.json`:

```python
from LLM.extract import extract, extract_job
extract_job(job_id, "2026-09-21")          # jobs/<id>/minutes.json + etapa „llm” în status.json (sare dacă există)
report = extract(path, "2026-09-21")       # direct pe un fișier; întoarce raportul rulării
```

## Cum lucrează

1. **Încărcare** (`loader.py`): adaptorul transformă `llm_input.json` în `Meeting` / `Window` / `Turn`.
   Textul curat al unei replici e `line` fără `[mm:ss] SPEAKER:` și fără ` [?]`. `turn_ids` e
   `[prima, ultima]` (acceptă și lista completă). O replică e nesigură dacă are `low_confidence`, `[?]`,
   `low_confidence_ratio` peste prag sau (când ASR-ul îl va adăuga) `overlap: true`.
2. **Verificarea contextului**: pentru fiecare fereastră, `(prompt fix + tokens_est) × token_factor +
   antet + num_predict` față de `num_ctx`. Depășirile sunt raportate (`context_check.json`, `run.json`),
   niciodată trunchiate: `on_overflow: expand` mărește `num_ctx` până la `max_num_ctx`, `skip` sare fereastra.
3. **O fereastră** (`n_windows == 1`): un apel scoate rezumatul și toate cazurile.
4. **Mai multe ferestre**: un apel per fereastră, în ordine. Antetul conține data, cazurile cunoscute
   (`case_key — stare`) și rezumatul ferestrei anterioare. Primele `overlap_turns` linii sunt marcate
   CONTEXT (nu se extrage din ele).
5. **Verificări în cod după fiecare fereastră** (`quotes.py`, `attribution.py`):
   - fiecare citat → `turn_id` (timestamp + rapidfuzz pe replicile ferestrei; fără potrivire: `null`);
   - decizie pusă la alt pacient: pacientul „curent” al replicii citate e ultimul pacient/salon numit
     la sau înaintea ei. Dacă diferă de numărul din `case_key`, decizia (și ETA-ul din aceeași replică)
     se mută la cazul potrivit, inclusiv la un caz cunoscut din ferestrele anterioare;
   - aceeași replică revendicată de mai multe cazuri rămâne doar la cazul potrivit;
   - `eta.raw` trebuie să fie în transcriere: o parafrază apropiată e înlocuită cu fragmentul exact
     din replică, altfel (ex. o traducere) devine `null`. Confuziile de tip fără echivoc se corectează:
     zi a săptămânii / „mâine” marcate `absolute` → `relative`; „каждые 6 часов” / „zilnic” → `recurring`;
   - un caz repetat de ≥ 3 ori în același răspuns e semnalat (buclă). Schema are `maxItems` și
     reîncercarea folosește alt `seed`.
6. **Reduce** (`merge.py`):
   - se elimină deciziile din replicile de context deja extrase în fereastra anterioară;
   - cazurile se grupează după `case_key` (rapidfuzz; numere diferite = pacienți diferiți; perechile
     ambigue primesc un apel scurt „același caz?”);
   - deciziile se ordonează în timp; ultimul ETA cu tip ≠ `none` câștigă;
   - `superseded`: pentru fiecare caz cu ≥ 2 decizii, un apel scurt („care decizii au fost schimbate
     sau anulate de una ulterioară?”) pe toate deciziile cazului. Dacă apelul eșuează, se folosește
     `replaces_previous` din extracție;
   - un apel final scrie `meeting_summary` și ordonează cazurile pe teme.

Un apel eșuat (timeout, JSON invalid, răspuns tăiat) se reîncearcă o dată. Apoi e notat în
`run.json` și rularea continuă cu restul ferestrelor.

## Ieșire (`minutes.json`)

```json
{
  "meeting_summary": "3–5 propoziții în română, cu [mm:ss]",
  "cases": [{
    "case_key": "Pacient 48, cardiologie",
    "topic": "…",
    "discussion_summary": "… [00:32]",
    "decisions": [{"decision": "…", "status": "aprobat", "quote": "verbatim, limba originală",
                   "timestamp": "00:32", "turn_id": 3, "superseded": false, "needs_review": false}],
    "eta": {"type": "relative", "raw": "вечером", "date": null, "condition": null, "needs_review": false},
    "open_questions": ["… [00:41]"]
  }]
}
```

- `status`: `aprobat` | `respins` | `amânat` | `necesită investigații suplimentare` | `în discuție`
- `eta.type`: `absolute` | `relative` | `duration` | `conditional` | `vague` | `recurring` | `none`.
  `raw` = expresia exactă (`null` la `none`), `condition` doar la `conditional`, `date` mereu `null`
  (o calculează validarea).
- `timestamp` = `mm:ss` al replicii citate, ca în transcriere. `turn_id` e pus de cod; `null` = citatul
  nu a fost găsit în fereastră.
- `needs_review` rămâne `false`: îl setează validarea (ex. `turn_id: null` sau replică nesigură; vezi
  `turn_uncertain` în `llm_debug/quotes.json`).
- Listele goale sunt valide.

## Debug: `jobs/<job_id>/llm_debug/` (rescris la fiecare rulare)

| Fișier | Conținut |
|---|---|
| `context_check.json` | estimarea de tokeni per fereastră față de `num_ctx` |
| `window_NNN.prompt.txt` | mesajele trimise (system, exemplul few-shot, fereastra) |
| `window_NNN.response.json` | răspunsul brut, JSON-ul parsat, corecțiile, cazurile după rezolvarea citatelor |
| `quotes.json` | citat → replică: scor, potrivire de timp, `exact`, `in_context`, `turn_uncertain` |
| `checks.json` | corecțiile din cod: decizii mutate la alt pacient, revendicări duble, `eta.raw` negăsit |
| `same_case.json` | întrebările „același caz?” și răspunsurile |
| `supersede.json` | întrebările „ce decizii au fost înlocuite?” și răspunsurile |
| `merge.json` | evenimentele de unificare (duplicate eliminate, potriviri fuzzy) și cazurile rezultate |
| `final.prompt.txt`, `final.response.json` | apelul final |
| `run.json` | timpi, tokeni per apel, erori, avertismente, `token_factor_observed`, configurația |

## Configurare (`LLM/config.yaml`)

| Cheie | Implicit | Variabilă de mediu |
|---|---|---|
| `ollama.url` | `http://localhost:11434` (doar local) | `LLM_OLLAMA_URL` |
| `ollama.model` | `qwen3:8b` | `LLM_MODEL` |
| `ollama.timeout_s` / `retries` | 300 / 1 | `LLM_TIMEOUT_S` / `LLM_RETRIES` |
| `ollama.num_ctx` / `temperature` | 8192 / 0.15 | `LLM_NUM_CTX` / `LLM_TEMPERATURE` |
| `ollama.keep_alive` | `5m` | `LLM_KEEP_ALIVE` |
| `ollama.repeat_penalty` | 1.1 | |
| `stages.<extract,same_case,supersede,final>.think` | `false` | `LLM_THINK_EXTRACT`, `LLM_THINK_SAME_CASE`, `LLM_THINK_SUPERSEDE`, `LLM_THINK_FINAL` (0/1) |
| `stages.*.num_predict` | 2048 / 32 / 128 / 1024 | |
| `extract.max_cases` / `max_decisions` | 12 / 8 (`maxItems` în schemă) | |
| `context.token_factor` | 1.55 (măsurat pe qwen3:8b) | `LLM_TOKEN_FACTOR` |
| `context.on_overflow` / `max_num_ctx` | `expand` / 12288 | `LLM_ON_OVERFLOW` / `LLM_MAX_NUM_CTX` |
| `context.header_max_cases` / `header_summaries` | 40 / 1 | |
| `input.uncertain_ratio` | 0.5 | `LLM_UNCERTAIN_RATIO` |
| `quotes.min_score` / `min_score_same_ts` / `short_quote_chars` | 80 / 65 / 12 | |
| `merge.same_score` / `diff_score` | 90 / 60 | |
| `merge.supersede` | `llm` (apel per caz) / `flag` (doar `replaces_previous`) / `all` | `LLM_SUPERSEDE` |

Promptul se editează în `LLM/prompts/`: `system.md` (reguli), `window.md` / `single.md` (mesajul cu
transcrierea), `same_case.md`, `supersede.md`, `final.md`, `fewshot.json` (exemplul RO/RU/EN, trecut
prin același șablon ca apelul real).

## Performanță și memorie (qwen3:8b, RTX 4070 Laptop 8 GB)

- Pe GPU, o fereastră durează 10–25 s; pe CPU, 70–130 s. `ollama ps` trebuie să arate `100% GPU`.
  Pe laptopurile cu comutator de GPU (Lenovo Legion: GPU Working Mode), placa NVIDIA poate fi
  deconectată pe baterie. Atunci Ollama rulează pe CPU.
- La `num_ctx` 8192, modelul ocupă ~6.2 GB VRAM.
- Transcrierea costă ~1.56 tokeni Qwen per `tokens_est`, iar promptul fix (reguli + exemplu) ~2 900
  tokeni. Ferestrele implicite ale ASR (`pack.window_tokens: 3500`) cer ~12k context, deci
  `on_overflow: expand`, strâns pe 8 GB. Recomandare pentru echipa ASR: `window_tokens` ≈ 2000–2500.

## Teste

```powershell
.\.venv\Scripts\python.exe -m LLM.tests                       # offline, fără Ollama: loader, context, citate, verificări, merge, flux complet cu client fals
.\.venv\Scripts\python.exe -m LLM.tests.test_scenarios        # cu qwen3:8b real; sărit dacă Ollama nu răspunde
                                                              # strict: pacientul corect per decizie, tipul exact de ETA, eta.raw verbatim
.\.venv\Scripts\python.exe -m LLM.tests.fixtures.build        # regenerează fixture-urile
```

Fixture-urile din `LLM/tests/fixtures/` sunt generate cu `make_turns` / `make_windows` din
`pipeline/pack_for_llm.py`, deci au exact formatul real. `llm_input.example.json` reproduce exemplul
din documentația ASR.
